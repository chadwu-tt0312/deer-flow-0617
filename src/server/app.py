# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# SPDX-License-Identifier: MIT

import base64
import json
import logging
import os
from typing import Annotated, List, cast
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from langchain_core.messages import AIMessageChunk, ToolMessage, BaseMessage
from langgraph.types import Command

from src.config.report_style import ReportStyle
from src.config.tools import SELECTED_RAG_PROVIDER
from src.graph.builder import build_graph_with_memory
from src.podcast.graph.builder import build_graph as build_podcast_graph
from src.ppt.graph.builder import build_graph as build_ppt_graph
from src.prose.graph.builder import build_graph as build_prose_graph
from src.prompt_enhancer.graph.builder import build_graph as build_prompt_enhancer_graph
from src.rag.builder import build_retriever
from src.rag.retriever import Resource
from src.server.chat_request import (
    ChatRequest,
    EnhancePromptRequest,
    GeneratePodcastRequest,
    GeneratePPTRequest,
    GenerateProseRequest,
    TTSRequest,
)
from src.server.mcp_request import MCPServerMetadataRequest, MCPServerMetadataResponse
from src.server.mcp_utils import load_mcp_tools
from src.server.rag_request import (
    RAGConfigResponse,
    RAGResourceRequest,
    RAGResourcesResponse,
)
from src.server.config_request import ConfigResponse
from src.llms.llm import get_configured_llm_models
from src.tools import VolcengineTTS
from src.utils.logging_config import (
    setup_deerflow_logging,
    setup_thread_logging,
    get_thread_logger,
    cleanup_thread_logging,
)

# 設定日誌配置 - 在應用程式啟動時就設定
setup_deerflow_logging(debug=False, log_to_file=True, log_dir="logs")
logger = logging.getLogger(__name__)

INTERNAL_SERVER_ERROR_DETAIL = "Internal Server Error"

app = FastAPI(
    title="DeerFlow API",
    description="API for Deer",
    version="0.1.0",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

graph = build_graph_with_memory()


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
    thread_id = request.thread_id
    if thread_id == "__default__":
        thread_id = str(uuid4())
    return StreamingResponse(
        _astream_workflow_generator(
            request.model_dump()["messages"],
            thread_id,
            request.resources,
            request.max_plan_iterations,
            request.max_step_num,
            request.max_search_results,
            request.auto_accepted_plan,
            request.interrupt_feedback,
            request.mcp_settings,
            request.enable_background_investigation,
            request.report_style,
            request.enable_deep_thinking,
        ),
        media_type="text/event-stream",
    )


async def _astream_workflow_generator(
    messages: List[dict],
    thread_id: str,
    resources: List[Resource],
    max_plan_iterations: int,
    max_step_num: int,
    max_search_results: int,
    auto_accepted_plan: bool,
    interrupt_feedback: str,
    mcp_settings: dict,
    enable_background_investigation: bool,
    report_style: ReportStyle,
    enable_deep_thinking: bool,
):
    # 為這個 thread 設定專用的日誌
    thread_logger = setup_thread_logging(
        thread_id=thread_id,
        level="INFO",
        log_dir="logs",
        console_output=False,  # 不在控制台輸出，避免重複
        file_output=True,
    )

    # 設置當前線程的日誌上下文，讓其他模組也能記錄到 thread 日誌
    from src.utils.logging_config import set_current_thread_context

    set_current_thread_context(thread_id, thread_logger)

    # 追蹤工具調用和結果的映射
    tool_call_mapping = {}

    # 在主日誌中記錄 thread 開始信息（僅基本信息）
    import logging

    main_logger = logging.getLogger("main")
    main_logger.info(f"🆔 Thread [{thread_id[:8]}] 開始處理新對話")

    # 在 thread 日誌中記錄詳細信息
    thread_logger.info(f"🚀 開始處理新的對話")
    if messages:
        thread_logger.info(f"📝 用戶輸入: {messages[-1]['content']}")
    # 根據 Setting 頁面設定，記錄到日誌
    thread_logger.info(
        f"⚙️ 配置: auto_accepted_plan={auto_accepted_plan}, max_plan_iterations={max_plan_iterations}, max_step_num={max_step_num}, max_search_results={max_search_results}"
    )

    input_ = {
        "messages": messages,
        "plan_iterations": 0,
        "final_report": "",
        "current_plan": None,
        "observations": [],
        "auto_accepted_plan": auto_accepted_plan,
        "enable_background_investigation": enable_background_investigation,
        "research_topic": messages[-1]["content"] if messages else "",
    }
    if not auto_accepted_plan and interrupt_feedback:
        resume_msg = f"[{interrupt_feedback}]"
        # add the last message to the resume message
        if messages:
            resume_msg += f" {messages[-1]['content']}"
        input_ = Command(resume=resume_msg)
    async for agent, _, event_data in graph.astream(
        input_,
        config={
            "configurable": {
                "thread_id": thread_id,
                "resources": resources,
                "max_plan_iterations": max_plan_iterations,
                "max_step_num": max_step_num,
                "max_search_results": max_search_results,
                "mcp_settings": mcp_settings,
                "report_style": report_style.value,
                "enable_deep_thinking": enable_deep_thinking,
            }
        },
        stream_mode=["messages", "updates"],
        subgraphs=True,
    ):
        # 確保在每次迭代中都有正確的 thread context
        # 這是關鍵修復：防止異步迭代中 context 丟失
        set_current_thread_context(thread_id, thread_logger)

        # 調試：檢查 context 是否正確設置
        from src.utils.logging_config import get_current_thread_id

        current_context_id = get_current_thread_id()
        if current_context_id != thread_id:
            thread_logger.warning(
                f"🔍 Context 不匹配: 期望 {thread_id[:8]}, 實際 {current_context_id[:8] if current_context_id else 'None'}"
            )

        if isinstance(event_data, dict):
            if "__interrupt__" in event_data:
                yield _make_event(
                    "interrupt",
                    {
                        "thread_id": thread_id,
                        "id": event_data["__interrupt__"][0].ns[0],
                        "role": "assistant",
                        "content": event_data["__interrupt__"][0].value,
                        "finish_reason": "interrupt",
                        "options": [
                            {"text": "Edit plan", "value": "edit_plan"},
                            {"text": "Start research", "value": "accepted"},
                        ],
                    },
                )
            continue
        message_chunk, message_metadata = cast(tuple[BaseMessage, dict[str, any]], event_data)
        agent_name = agent[0].split(":")[0]

        # 記錄到 thread-specific 日誌
        if hasattr(message_chunk, "tool_calls") and message_chunk.tool_calls:
            # 只記錄完整的工具調用（有工具名稱的）
            tool_names = [
                tool_call.get("name", "")
                for tool_call in message_chunk.tool_calls
                if tool_call.get("name")
            ]
            if tool_names:  # 只有當有工具名稱時才記錄
                # 提取執行 ID（取後8位以保持簡潔）
                execution_id = (
                    message_chunk.id.split("-")[-1][:8] if message_chunk.id else "unknown"
                )
                thread_logger.info(f"🛠️ {agent_name} 調用工具: {tool_names}[run_id: {execution_id}]")

                # 記錄每個工具調用的詳細參數
                for tool_call in message_chunk.tool_calls:
                    if tool_call.get("id") and tool_call.get("name"):
                        tool_name = tool_call.get("name")
                        tool_id = tool_call["id"][:8]

                        # 記錄工具參數
                        if tool_call.get("args"):
                            args_str = str(tool_call["args"])
                            thread_logger.info(
                                f"📝 工具[{tool_name}][tool_id: {tool_id}] 參數: {args_str}"
                            )
                        else:
                            thread_logger.info(f"📝 工具[{tool_name}][tool_id: {tool_id}] 無參數")

                        # 儲存工具調用映射以便後續追蹤結果
                        tool_call_mapping[tool_call["id"]] = tool_call.get("name", "unknown_tool")
                        thread_logger.debug(
                            f"🔍 註冊工具映射: {tool_call['id'][:8]} -> {tool_call.get('name')}"
                        )

        elif isinstance(message_chunk, ToolMessage):
            # 工具執行結果 - 這裡記錄內容
            tool_name = "unknown_tool"
            if hasattr(message_chunk, "tool_call_id") and message_chunk.tool_call_id:
                tool_name = tool_call_mapping.get(message_chunk.tool_call_id, "unknown_tool")
                # 記錄工具執行完成和結果內容
                thread_logger.info(
                    f"🔧 工具['{tool_name}']執行完成 (tool_id: {message_chunk.tool_call_id[:8]}): {message_chunk.content}"
                )
                # 調試信息：顯示映射查找情況
                thread_logger.debug(f"🔍 工具映射查找: {message_chunk.tool_call_id} -> {tool_name}")
                thread_logger.debug(f"🔍 當前映射表: {list(tool_call_mapping.keys())}")
            else:
                # 記錄無 tool_id 的情況和內容
                thread_logger.info(f"🔧 工具執行完成 (無 tool_id): {message_chunk.content}")
                thread_logger.debug("🔍 ToolMessage 缺少 tool_call_id")
        elif message_chunk.content and not getattr(message_chunk, "tool_call_chunks", None):
            # 完整的回應內容（避免記錄 streaming chunks）
            # 注意：這裡不記錄內容，因為 _make_event 會處理所有發送到前端的內容
            if hasattr(message_chunk, "response_metadata") and message_chunk.response_metadata.get(
                "finish_reason"
            ):
                # 只記錄完成狀態，內容由 _make_event 記錄
                thread_logger.info(
                    f"✅ {agent_name} 完成回應 (finish_reason: {message_chunk.response_metadata.get('finish_reason')})"
                )

        event_stream_message: dict[str, any] = {
            "thread_id": thread_id,
            "agent": agent_name,
            "id": message_chunk.id,
            "role": "assistant",
            "content": message_chunk.content,
        }
        if message_chunk.additional_kwargs.get("reasoning_content"):
            event_stream_message["reasoning_content"] = message_chunk.additional_kwargs[
                "reasoning_content"
            ]
        if message_chunk.response_metadata.get("finish_reason"):
            event_stream_message["finish_reason"] = message_chunk.response_metadata.get(
                "finish_reason"
            )
        if isinstance(message_chunk, ToolMessage):
            # Tool Message - Return the result of the tool call
            event_stream_message["tool_call_id"] = message_chunk.tool_call_id
            yield _make_event("tool_call_result", event_stream_message)
        elif isinstance(message_chunk, AIMessageChunk):
            # AI Message - Raw message tokens
            if message_chunk.tool_calls:
                # AI Message - Tool Call
                event_stream_message["tool_calls"] = message_chunk.tool_calls
                event_stream_message["tool_call_chunks"] = message_chunk.tool_call_chunks
                yield _make_event("tool_calls", event_stream_message)
            elif message_chunk.tool_call_chunks:
                # AI Message - Tool Call Chunks
                event_stream_message["tool_call_chunks"] = message_chunk.tool_call_chunks
                yield _make_event("tool_call_chunks", event_stream_message)
            else:
                # AI Message - Raw message tokens
                yield _make_event("message_chunk", event_stream_message)

    # 在對話結束時記錄
    main_logger.info(f"✅ Thread [{thread_id[:8]}] 對話處理完成")
    thread_logger.info(f"🏁 對話處理完成")

    # 清理 thread 上下文和日誌資源
    from src.utils.logging_config import clear_current_thread_context

    clear_current_thread_context()
    cleanup_thread_logging(thread_id)


def _make_event(event_type: str, data: dict[str, any]):
    # # 在生成事件前，先記錄內容到 thread 日誌 #TODO: 暫時移除紀錄
    # from src.utils.logging_config import get_current_thread_logger, get_current_thread_id

    # thread_logger = get_current_thread_logger()
    # current_thread_id = get_current_thread_id()

    # # 調試：檢查 _make_event 中的 context 狀態
    # if thread_logger and current_thread_id:
    #     expected_thread_id = data.get("thread_id")
    #     if expected_thread_id and current_thread_id != expected_thread_id:
    #         thread_logger.warning(
    #             f"🔍 _make_event Context 不匹配: 期望 {expected_thread_id[:8]}, 實際 {current_thread_id[:8]}"
    #         )
    # elif not thread_logger:
    #     # 如果沒有 thread_logger，記錄到主日誌
    #     import logging

    #     main_logger = logging.getLogger("main")
    #     main_logger.warning(
    #         f"🔍 _make_event 缺少 thread_logger, current_thread_id: {current_thread_id[:8] if current_thread_id else 'None'}"
    #     )
    # if thread_logger and data.get("content"):
    #     # 根據事件類型決定日誌格式
    #     agent_name = data.get("agent", "unknown")
    #     content = str(data.get("content", ""))

    #     # 檢查是否為完整的回應（避免記錄 streaming 片段）
    #     should_log = False

    #     if event_type == "message_chunk":
    #         # 對於 message_chunk，只記錄完整的回應
    #         # 檢查是否有 finish_reason，表示這是完整的回應
    #         finish_reason = data.get("finish_reason")
    #         if finish_reason:
    #             # 這是完整的回應，可以記錄
    #             thread_logger.info(f"📤 SSE-[{agent_name}] 完整回應: {content}")
    #         # 否則不記錄 streaming 片段

    #     elif event_type == "tool_call_result":
    #         # 工具調用結果通常是完整的，可以記錄（不截斷）
    #         tool_call_id = data.get("tool_call_id", "unknown")
    #         thread_logger.info(f"📋 SSE-工具結果[{tool_call_id[:8]}]: {content}")
    #         # 調試信息：記錄 SSE 事件的 tool_call_id
    #         thread_logger.debug(f"🔍 SSE 事件 tool_call_id: {tool_call_id}")

    #     elif event_type == "tool_calls":
    #         # 工具調用通常是完整的，可以記錄
    #         if content.strip():
    #             thread_logger.info(f"🛠️ SSE-[{agent_name}] 工具調用: {content}")

    #     elif event_type == "tool_call_chunks":
    #         # 工具調用塊可能是片段，只記錄有意義的內容
    #         if content.strip() and len(content.strip()) > 10:  # 只記錄較長的內容
    #             thread_logger.info(f"🔨 SSE-[{agent_name}] 工具調用塊: {content}")

    #     elif event_type == "interrupt":
    #         # 中斷事件通常是完整的，可以記錄
    #         thread_logger.info(f"⏸️ 中斷事件: {content}")

    #     else:
    #         # 其他類型的事件，只記錄較長的內容
    #         if content.strip() and len(content.strip()) > 10:
    #             thread_logger.info(f"📨 SSE-[{event_type}] {agent_name}: {content}")

    # 移除空內容
    if data.get("content") == "":
        data.pop("content")

    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/tts")
async def text_to_speech(request: TTSRequest):
    """Convert text to speech using volcengine TTS API."""
    try:
        app_id = os.getenv("VOLCENGINE_TTS_APPID", "")
        if not app_id:
            raise HTTPException(status_code=400, detail="VOLCENGINE_TTS_APPID is not set")
        access_token = os.getenv("VOLCENGINE_TTS_ACCESS_TOKEN", "")
        if not access_token:
            raise HTTPException(status_code=400, detail="VOLCENGINE_TTS_ACCESS_TOKEN is not set")
        cluster = os.getenv("VOLCENGINE_TTS_CLUSTER", "volcano_tts")
        voice_type = os.getenv("VOLCENGINE_TTS_VOICE_TYPE", "BV700_V2_streaming")

        tts_client = VolcengineTTS(
            appid=app_id,
            access_token=access_token,
            cluster=cluster,
            voice_type=voice_type,
        )
        # Call the TTS API
        result = tts_client.text_to_speech(
            text=request.text[:1024],
            encoding=request.encoding,
            speed_ratio=request.speed_ratio,
            volume_ratio=request.volume_ratio,
            pitch_ratio=request.pitch_ratio,
            text_type=request.text_type,
            with_frontend=request.with_frontend,
            frontend_type=request.frontend_type,
        )

        if not result["success"]:
            raise HTTPException(status_code=500, detail=str(result["error"]))

        # Decode the base64 audio data
        audio_data = base64.b64decode(result["audio_data"])

        # Return the audio file
        return Response(
            content=audio_data,
            media_type=f"audio/{request.encoding}",
            headers={
                "Content-Disposition": (f"attachment; filename=tts_output.{request.encoding}")
            },
        )
    except Exception as e:
        logger.exception(f"Error in TTS endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=INTERNAL_SERVER_ERROR_DETAIL)


@app.post("/api/podcast/generate")
async def generate_podcast(request: GeneratePodcastRequest):
    try:
        report_content = request.content
        print(report_content)
        workflow = build_podcast_graph()
        final_state = workflow.invoke({"input": report_content})
        audio_bytes = final_state["output"]
        return Response(content=audio_bytes, media_type="audio/mp3")
    except Exception as e:
        logger.exception(f"Error occurred during podcast generation: {str(e)}")
        raise HTTPException(status_code=500, detail=INTERNAL_SERVER_ERROR_DETAIL)


@app.post("/api/ppt/generate")
async def generate_ppt(request: GeneratePPTRequest):
    try:
        report_content = request.content
        print(report_content)
        workflow = build_ppt_graph()
        final_state = workflow.invoke({"input": report_content})
        generated_file_path = final_state["generated_file_path"]
        with open(generated_file_path, "rb") as f:
            ppt_bytes = f.read()
        return Response(
            content=ppt_bytes,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
    except Exception as e:
        logger.exception(f"Error occurred during ppt generation: {str(e)}")
        raise HTTPException(status_code=500, detail=INTERNAL_SERVER_ERROR_DETAIL)


@app.post("/api/prose/generate")
async def generate_prose(request: GenerateProseRequest):
    try:
        sanitized_prompt = request.prompt.replace("\r\n", "").replace("\n", "")
        logger.info(f"Generating prose for prompt: {sanitized_prompt}")
        workflow = build_prose_graph()
        events = workflow.astream(
            {
                "content": request.prompt,
                "option": request.option,
                "command": request.command,
            },
            stream_mode="messages",
            subgraphs=True,
        )
        return StreamingResponse(
            (f"data: {event[0].content}\n\n" async for _, event in events),
            media_type="text/event-stream",
        )
    except Exception as e:
        logger.exception(f"Error occurred during prose generation: {str(e)}")
        raise HTTPException(status_code=500, detail=INTERNAL_SERVER_ERROR_DETAIL)


@app.post("/api/prompt/enhance")
async def enhance_prompt(request: EnhancePromptRequest):
    try:
        sanitized_prompt = request.prompt.replace("\r\n", "").replace("\n", "")
        logger.info(f"Enhancing prompt: {sanitized_prompt}")

        # Convert string report_style to ReportStyle enum
        report_style = None
        if request.report_style:
            try:
                # Handle both uppercase and lowercase input
                style_mapping = {
                    "ACADEMIC": ReportStyle.ACADEMIC,
                    "POPULAR_SCIENCE": ReportStyle.POPULAR_SCIENCE,
                    "NEWS": ReportStyle.NEWS,
                    "SOCIAL_MEDIA": ReportStyle.SOCIAL_MEDIA,
                    "academic": ReportStyle.ACADEMIC,
                    "popular_science": ReportStyle.POPULAR_SCIENCE,
                    "news": ReportStyle.NEWS,
                    "social_media": ReportStyle.SOCIAL_MEDIA,
                }
                report_style = style_mapping.get(request.report_style, ReportStyle.ACADEMIC)
            except Exception:
                # If invalid style, default to ACADEMIC
                report_style = ReportStyle.ACADEMIC
        else:
            report_style = ReportStyle.ACADEMIC

        workflow = build_prompt_enhancer_graph()
        final_state = workflow.invoke(
            {
                "prompt": request.prompt,
                "context": request.context,
                "report_style": report_style,
            }
        )
        return {"result": final_state["output"]}
    except Exception as e:
        logger.exception(f"Error occurred during prompt enhancement: {str(e)}")
        raise HTTPException(status_code=500, detail=INTERNAL_SERVER_ERROR_DETAIL)


@app.post("/api/mcp/server/metadata", response_model=MCPServerMetadataResponse)
async def mcp_server_metadata(request: MCPServerMetadataRequest):
    """Get information about an MCP server."""
    try:
        # Set default timeout with a longer value for this endpoint
        timeout = 300  # Default to 300 seconds for this endpoint

        # Use custom timeout from request if provided
        if request.timeout_seconds is not None:
            timeout = request.timeout_seconds

        # Load tools from the MCP server using the utility function
        tools = await load_mcp_tools(
            server_type=request.transport,
            command=request.command,
            args=request.args,
            url=request.url,
            env=request.env,
            timeout_seconds=timeout,
        )

        # Create the response with tools
        response = MCPServerMetadataResponse(
            transport=request.transport,
            command=request.command,
            args=request.args,
            url=request.url,
            env=request.env,
            tools=tools,
        )

        return response
    except Exception as e:
        if not isinstance(e, HTTPException):
            logger.exception(f"Error in MCP server metadata endpoint: {str(e)}")
            raise HTTPException(status_code=500, detail=INTERNAL_SERVER_ERROR_DETAIL)
        raise


@app.get("/api/rag/config", response_model=RAGConfigResponse)
async def rag_config():
    """Get the config of the RAG."""
    return RAGConfigResponse(provider=SELECTED_RAG_PROVIDER)


@app.get("/api/rag/resources", response_model=RAGResourcesResponse)
async def rag_resources(request: Annotated[RAGResourceRequest, Query()]):
    """Get the resources of the RAG."""
    retriever = build_retriever()
    if retriever:
        return RAGResourcesResponse(resources=retriever.list_resources(request.query))
    return RAGResourcesResponse(resources=[])


@app.get("/api/config", response_model=ConfigResponse)
async def config():
    """Get the config of the server."""
    return ConfigResponse(
        rag=RAGConfigResponse(provider=SELECTED_RAG_PROVIDER),
        models=get_configured_llm_models(),
    )
