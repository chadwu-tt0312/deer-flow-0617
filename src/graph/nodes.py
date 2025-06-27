# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# SPDX-License-Identifier: MIT

import json
import logging
import os
from typing import Annotated, Literal

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.types import Command, interrupt
from langchain_mcp_adapters.client import MultiServerMCPClient

from src.agents import create_agent
from src.tools.search import LoggedTavilySearch
from src.tools import (
    crawl_tool,
    get_web_search_tool,
    get_retriever_tool,
    python_repl_tool,
)

from src.config.agents import AGENT_LLM_MAP
from src.config.configuration import Configuration
from src.llms.llm import get_llm_by_type
from src.prompts.planner_model import Plan
from src.prompts.template import apply_prompt_template
from src.utils.json_utils import repair_json_output
from src.utils.logging_config import (
    get_current_thread_id,
    get_current_thread_logger,
    get_thread_logger,
    set_current_thread_context,
)

from .types import State
from ..config import SELECTED_SEARCH_ENGINE, SearchEngine

logger = logging.getLogger(__name__)


def ensure_thread_context(config: RunnableConfig) -> str:
    """
    確保當前線程有正確的 thread context，並返回 thread_id

    Args:
        config: LangGraph 的 RunnableConfig，包含 thread_id

    Returns:
        thread_id: 當前線程的 ID
    """
    # 從 config 中獲取 thread_id（標準 LangGraph 方式）
    thread_id = config.get("configurable", {}).get("thread_id")

    if not thread_id:
        # 備用方案：從根層級獲取（向後兼容）
        thread_id = config.get("thread_id")

    if not thread_id:
        logger.warning("No thread_id found in config, using fallback")
        return None

    # 檢查當前線程是否已有正確的 thread context
    current_thread_id = get_current_thread_id()

    if current_thread_id != thread_id:
        # Thread context 不匹配或不存在，重新設置
        thread_logger = get_thread_logger(thread_id)
        if thread_logger:
            set_current_thread_context(thread_id, thread_logger)
            logger.debug(f"Thread context set for thread_id: {thread_id}")
        else:
            logger.warning(f"No thread logger found for thread_id: {thread_id}")

    return thread_id


# 上下文管理常數
def parse_token_limit(value: str) -> int:
    """解析 token 限制值，支援 K/M 後綴"""
    if isinstance(value, int):
        return value

    value = str(value).upper().strip()
    if value.endswith("K"):
        return int(float(value[:-1]) * 1000)
    elif value.endswith("M"):
        return int(float(value[:-1]) * 1000000)
    else:
        return int(value)


MAX_CONTEXT_TOKENS = parse_token_limit(os.getenv("MAX_CONTEXT_TOKENS", "128000"))
TRUNCATION_RATIO = float(os.getenv("TRUNCATION_RATIO", "0.7"))  # 當超出限制時，保留的比例


def estimate_tokens(text: str) -> int:
    """估計文本的 token 數量

    使用更精確的估算方法：
    - 英文：1 token ≈ 4 字符
    - 中文：1 token ≈ 1.5 字符
    - 代碼：1 token ≈ 3 字符
    """
    if not text:
        return 0

    # 檢測中文字符
    chinese_chars = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    english_chars = len(text) - chinese_chars

    # 檢測代碼塊
    code_blocks = text.count("```")
    has_code = code_blocks > 0 or text.count("def ") > 0 or text.count("class ") > 0

    if has_code:
        return len(text) // 3  # 代碼密度較高
    elif chinese_chars > english_chars:
        return int(len(text) / 1.5)  # 中文為主
    else:
        return len(text) // 4  # 英文為主


def truncate_context(messages: list, max_tokens: int = None) -> list:
    """智能截斷上下文以避免超出 token 限制

    策略：
    1. 優先保留系統訊息和當前任務訊息
    2. 保留最近的對話歷史
    3. 如果有重要的研究結果，優先保留
    4. 截斷過長的單個訊息
    """
    if max_tokens is None:
        max_tokens = MAX_CONTEXT_TOKENS

    if not messages:
        return messages

    # 計算總 token 數
    total_tokens = sum(estimate_tokens(str(msg.get("content", ""))) for msg in messages)

    if total_tokens <= max_tokens:
        logger.debug(f"Context length ({total_tokens} tokens) within limit ({max_tokens})")
        return messages

    logger.warning(
        f"Context length ({total_tokens} tokens) exceeds limit ({max_tokens}). Truncating..."
    )

    target_tokens = int(max_tokens * TRUNCATION_RATIO)
    truncated_messages = []
    current_tokens = 0

    # 第一步：分類訊息
    system_messages = []
    task_messages = []
    other_messages = []

    for msg in messages:
        content = str(msg.get("content", ""))
        role = msg.get("role", "")
        name = str(msg.get("name", ""))

        if role == "system" or "system" in name.lower():
            system_messages.append(msg)
        elif any(
            keyword in content.lower()
            for keyword in ["current task", "當前任務", "title", "description"]
        ):
            task_messages.append(msg)
        else:
            other_messages.append(msg)

    # 第二步：按優先級添加訊息
    def add_messages_if_fits(msg_list, max_tokens_remaining):
        nonlocal current_tokens, truncated_messages

        for msg in msg_list:
            content = str(msg.get("content", ""))
            msg_tokens = estimate_tokens(content)

            # 如果單個訊息太長，截斷它
            if msg_tokens > max_tokens_remaining * 0.8:
                truncated_content = content[: int(len(content) * 0.6)] + "\n\n[內容已截斷...]"
                msg_tokens = estimate_tokens(truncated_content)
                msg = {**msg, "content": truncated_content}

            if current_tokens + msg_tokens <= max_tokens_remaining:
                truncated_messages.append(msg)
                current_tokens += msg_tokens
            else:
                break

    # 添加系統訊息（最高優先級）
    add_messages_if_fits(system_messages, target_tokens)

    # 添加任務相關訊息
    remaining_tokens = target_tokens - current_tokens
    add_messages_if_fits(task_messages, remaining_tokens)

    # 添加其他訊息（從最新開始）
    remaining_tokens = target_tokens - current_tokens
    add_messages_if_fits(list(reversed(other_messages)), remaining_tokens)

    # 確保訊息順序正確
    truncated_messages.sort(key=lambda x: messages.index(x) if x in messages else len(messages))

    logger.info(
        f"Context truncated: {len(messages)} -> {len(truncated_messages)} messages, "
        f"{total_tokens} -> ~{current_tokens} tokens"
    )

    return truncated_messages


@tool
def handoff_to_planner(
    research_topic: Annotated[str, "The topic of the research task to be handed off."],
    locale: Annotated[str, "The user's detected language locale (e.g., en-US, zh-CN, zh-TW)."],
):
    """Handoff to planner agent to do plan."""
    # This tool is not returning anything: we're just using it
    # as a way for LLM to signal that it needs to hand off to planner agent
    return


def background_investigation_node(state: State, config: RunnableConfig):
    # 確保 thread context 正確設置
    ensure_thread_context(config)
    logger.info("background investigation node is running.")
    configurable = Configuration.from_runnable_config(config)
    query = state.get("research_topic")
    background_investigation_results = None
    if SELECTED_SEARCH_ENGINE == SearchEngine.TAVILY.value:
        searched_content = LoggedTavilySearch(max_results=configurable.max_search_results).invoke(
            query
        )
        if isinstance(searched_content, list):
            background_investigation_results = [
                f"## {elem['title']}\n\n{elem['content']}" for elem in searched_content
            ]
            return {
                "background_investigation_results": "\n\n".join(background_investigation_results)
            }
        else:
            logger.error(f"Tavily search returned malformed response: {searched_content}")
    else:
        background_investigation_results = get_web_search_tool(
            configurable.max_search_results
        ).invoke(query)
    return {
        "background_investigation_results": json.dumps(
            background_investigation_results, ensure_ascii=False
        )
    }


def planner_node(
    state: State, config: RunnableConfig
) -> Command[Literal["human_feedback", "reporter"]]:
    """Planner node that generate the full plan."""
    # 確保 thread context 正確設置
    ensure_thread_context(config)
    logger.info("Planner generating full plan")
    configurable = Configuration.from_runnable_config(config)
    plan_iterations = state["plan_iterations"] if state.get("plan_iterations", 0) else 0
    messages = apply_prompt_template("planner", state, configurable)

    if state.get("enable_background_investigation") and state.get(
        "background_investigation_results"
    ):
        messages += [
            {
                "role": "user",
                "content": (
                    "background investigation results of user query:\n"
                    + state["background_investigation_results"]
                    + "\n"
                ),
            }
        ]

    if configurable.enable_deep_thinking:
        llm = get_llm_by_type("reasoning")
    elif AGENT_LLM_MAP["planner"] == "basic":
        llm = get_llm_by_type("basic").with_structured_output(
            Plan,
            method="json_mode",
        )
    else:
        llm = get_llm_by_type(AGENT_LLM_MAP["planner"])

    # if the plan iterations is greater than the max plan iterations, return the reporter node
    if plan_iterations >= configurable.max_plan_iterations:
        return Command(goto="reporter")

    full_response = ""
    if AGENT_LLM_MAP["planner"] == "basic" and not configurable.enable_deep_thinking:
        response = llm.invoke(messages)
        full_response = response.model_dump_json(indent=4, exclude_none=True)
    else:
        response = llm.stream(messages)
        for chunk in response:
            full_response += chunk.content
    logger.debug(f"Current state messages: {state['messages']}")
    logger.info(f"Planner response: {full_response}")

    try:
        repaired_response = repair_json_output(full_response)
        logger.debug(f"Repaired JSON: {repaired_response}")
        curr_plan = json.loads(repaired_response)
        logger.debug(f"Successfully parsed plan: {curr_plan}")
    except json.JSONDecodeError as e:
        logger.error(f"Planner response is not a valid JSON: {e}")
        logger.error(f"Original response: {full_response}")
        logger.error(f"Repaired response: {repair_json_output(full_response)}")
        if plan_iterations > 0:
            return Command(goto="reporter")
        else:
            return Command(goto="__end__")
    if curr_plan.get("has_enough_context"):
        logger.info("Planner response has enough context.")
        new_plan = Plan.model_validate(curr_plan)
        return Command(
            update={
                "messages": [AIMessage(content=full_response, name="planner")],
                "current_plan": new_plan,
            },
            goto="reporter",
        )
    return Command(
        update={
            "messages": [AIMessage(content=full_response, name="planner")],
            "current_plan": full_response,
        },
        goto="human_feedback",
    )


def human_feedback_node(
    state,
) -> Command[Literal["planner", "research_team", "reporter", "__end__"]]:
    # 注意：human_feedback_node 沒有 config 參數，因為它是用戶交互節點
    # thread context 應該在調用此節點的上一個節點中已經設置
    current_plan = state.get("current_plan", "")
    # check if the plan is auto accepted
    auto_accepted_plan = state.get("auto_accepted_plan", False)
    if not auto_accepted_plan:
        feedback = interrupt("Please Review the Plan.")

        # if the feedback is not accepted, return the planner node
        if feedback and str(feedback).upper().startswith("[EDIT_PLAN]"):
            return Command(
                update={
                    "messages": [
                        HumanMessage(content=feedback, name="feedback"),
                    ],
                },
                goto="planner",
            )
        elif feedback and str(feedback).upper().startswith("[ACCEPTED]"):
            logger.info("Plan is accepted by user.")
        else:
            raise TypeError(f"Interrupt value of {feedback} is not supported.")

    # if the plan is accepted, run the following node
    plan_iterations = state["plan_iterations"] if state.get("plan_iterations", 0) else 0
    goto = "research_team"
    try:
        current_plan = repair_json_output(current_plan)
        logger.debug(f"Repaired plan JSON: {current_plan}")
        # increment the plan iterations
        plan_iterations += 1
        # parse the plan
        new_plan = json.loads(current_plan)
        logger.debug(f"Successfully parsed plan in human_feedback: {new_plan}")
        if new_plan["has_enough_context"]:
            goto = "reporter"
    except json.JSONDecodeError as e:
        logger.error(f"Human feedback - Planner response is not a valid JSON: {e}")
        logger.error(f"Original plan: {state.get('current_plan', '')}")
        logger.error(f"Repaired plan: {current_plan}")
        if plan_iterations > 0:
            return Command(goto="reporter")
        else:
            return Command(goto="__end__")

    return Command(
        update={
            "current_plan": Plan.model_validate(new_plan),
            "plan_iterations": plan_iterations,
            "locale": new_plan["locale"],
        },
        goto=goto,
    )


def coordinator_node(
    state: State, config: RunnableConfig
) -> Command[Literal["planner", "background_investigator", "__end__"]]:
    """Coordinator node that communicate with customers."""
    # 確保 thread context 正確設置
    ensure_thread_context(config)
    logger.info("Coordinator talking.")
    configurable = Configuration.from_runnable_config(config)
    messages = apply_prompt_template("coordinator", state)
    response = (
        get_llm_by_type(AGENT_LLM_MAP["coordinator"])
        .bind_tools([handoff_to_planner])
        .invoke(messages)
    )
    logger.debug(f"Current state messages: {state['messages']}")

    goto = "__end__"
    locale = state.get("locale", "en-US")  # Default locale if not specified
    research_topic = state.get("research_topic", "")

    if len(response.tool_calls) > 0:
        goto = "planner"
        if state.get("enable_background_investigation"):
            # if the search_before_planning is True, add the web search tool to the planner agent
            goto = "background_investigator"
        try:
            for tool_call in response.tool_calls:
                if tool_call.get("name", "") != "handoff_to_planner":
                    continue
                if tool_call.get("args", {}).get("locale") and tool_call.get("args", {}).get(
                    "research_topic"
                ):
                    locale = tool_call.get("args", {}).get("locale")
                    research_topic = tool_call.get("args", {}).get("research_topic")
                    break
        except Exception as e:
            logger.error(f"Error processing tool calls: {e}")
    else:
        logger.warning(
            "Coordinator response contains no tool calls. Terminating workflow execution."
        )
        logger.debug(f"Coordinator response: {response}")

    return Command(
        update={
            "locale": locale,
            "research_topic": research_topic,
            "resources": configurable.resources,
        },
        goto=goto,
    )


def reporter_node(state: State, config: RunnableConfig):
    """Reporter node that write a final report."""
    # 確保 thread context 正確設置
    ensure_thread_context(config)
    logger.info("Reporter write final report")
    configurable = Configuration.from_runnable_config(config)
    current_plan = state.get("current_plan")
    input_ = {
        "messages": [
            HumanMessage(
                f"# Research Requirements\n\n## Task\n\n{current_plan.title}\n\n## Description\n\n{current_plan.thought}"
            )
        ],
        "locale": state.get("locale", "en-US"),
    }
    invoke_messages = apply_prompt_template("reporter", input_, configurable)
    observations = state.get("observations", [])

    # Add a reminder about the new report format, citation style, and table usage
    invoke_messages.append(
        HumanMessage(
            content="IMPORTANT: Structure your report according to the format in the prompt. Remember to include:\n\n1. Key Points - A bulleted list of the most important findings\n2. Overview - A brief introduction to the topic\n3. Detailed Analysis - Organized into logical sections\n4. Survey Note (optional) - For more comprehensive reports\n5. Key Citations - List all references at the end\n\nFor citations, DO NOT include inline citations in the text. Instead, place all citations in the 'Key Citations' section at the end using the format: `- [Source Title](URL)`. Include an empty line between each citation for better readability.\n\nPRIORITIZE USING MARKDOWN TABLES for data presentation and comparison. Use tables whenever presenting comparative data, statistics, features, or options. Structure tables with clear headers and aligned columns. Example table format:\n\n| Feature | Description | Pros | Cons |\n|---------|-------------|------|------|\n| Feature 1 | Description 1 | Pros 1 | Cons 1 |\n| Feature 2 | Description 2 | Pros 2 | Cons 2 |",
            name="system",
        )
    )

    for observation in observations:
        invoke_messages.append(
            HumanMessage(
                content=f"Below are some observations for the research task:\n\n{observation}",
                name="observation",
            )
        )
    logger.debug(f"Current invoke messages: {invoke_messages}")
    response = get_llm_by_type(AGENT_LLM_MAP["reporter"]).invoke(invoke_messages)
    response_content = response.content
    logger.info(f"reporter response: {response_content}")

    return {"final_report": response_content}


def research_team_node(state: State):
    """Research team node that collaborates on tasks."""
    # 注意：research_team_node 沒有 config 參數，是控制流節點
    # thread context 應該在其他執行節點中已經設置
    logger.info("Research team is collaborating on tasks.")
    pass


async def _execute_agent_step(
    state: State, agent, agent_name: str, config: RunnableConfig = None
) -> Command[Literal["research_team"]]:
    """Helper function to execute a step using the specified agent."""
    # 確保 thread context 正確設置
    if config:
        ensure_thread_context(config)
    current_plan = state.get("current_plan")
    observations = state.get("observations", [])

    # Find the first unexecuted step
    current_step = None
    completed_steps = []
    for step in current_plan.steps:
        if not step.execution_res:
            current_step = step
            break
        else:
            completed_steps.append(step)

    if not current_step:
        logger.warning("No unexecuted step found")
        return Command(goto="research_team")

    logger.info(f"Executing step: {current_step.title}, agent: {agent_name}")

    # Format completed steps information
    completed_steps_info = ""
    if completed_steps:
        completed_steps_info = "# Existing Research Findings\n\n"
        for i, step in enumerate(completed_steps):
            completed_steps_info += f"## Existing Finding {i + 1}: {step.title}\n\n"
            completed_steps_info += f"<finding>\n{step.execution_res}\n</finding>\n\n"

    # Prepare the input for the agent with completed steps info
    agent_input = {
        "messages": [
            HumanMessage(
                content=f"{completed_steps_info}# Current Task\n\n## Title\n\n{current_step.title}\n\n## Description\n\n{current_step.description}\n\n## Locale\n\n{state.get('locale', 'en-US')}"
            )
        ]
    }

    # 智能上下文管理：檢查並截斷過長的上下文
    state_messages = state.get("messages", [])
    observations = state.get("observations", [])

    # 構建完整的上下文信息
    context_parts = []

    # 添加已完成的步驟信息
    if completed_steps_info:
        context_parts.append(
            {"content": completed_steps_info, "role": "system", "type": "completed_steps"}
        )

    # 添加當前任務信息
    current_task_content = f"# Current Task\n\n## Title\n\n{current_step.title}\n\n## Description\n\n{current_step.description}\n\n## Locale\n\n{state.get('locale', 'en-US')}"
    context_parts.append({"content": current_task_content, "role": "user", "type": "current_task"})

    # 添加狀態中的重要訊息
    if state_messages:
        for msg in state_messages[-5:]:  # 只取最近的 5 條訊息
            if hasattr(msg, "content"):
                context_parts.append(
                    {
                        "content": msg.content,
                        "role": getattr(msg, "role", "user"),
                        "type": "state_message",
                    }
                )
            elif isinstance(msg, dict):
                context_parts.append({**msg, "type": "state_message"})

    # 添加觀察結果（最近的幾個）
    if observations:
        recent_observations = observations[-3:]  # 只保留最近的 3 個觀察
        obs_content = "\n\n".join(
            f"觀察 {i + 1}: {obs}" for i, obs in enumerate(recent_observations)
        )
        if obs_content:
            context_parts.append({"content": obs_content, "role": "system", "type": "observations"})

    # 應用上下文截斷
    truncated_context = truncate_context(context_parts)

    # 重建代理輸入
    if truncated_context:
        # 找到當前任務訊息
        task_msg = None
        for msg in truncated_context:
            if msg.get("type") == "current_task":
                task_msg = msg
                break

        if task_msg:
            agent_input["messages"] = [HumanMessage(content=task_msg["content"])]
        else:
            # 如果當前任務被截斷了，使用原始的簡化版本
            agent_input["messages"] = [
                HumanMessage(
                    content=f"任務: {current_step.title}\n描述: {current_step.description}"
                )
            ]

        # 添加其他重要的上下文信息
        additional_context = []
        for msg in truncated_context:
            if msg.get("type") in ["completed_steps", "observations"] and msg.get("content"):
                additional_context.append(msg["content"])

        if additional_context:
            context_summary = "\n\n".join(additional_context)
            if len(context_summary) > 0:
                # 將額外上下文添加到主要訊息中
                current_content = agent_input["messages"][0].content
                agent_input["messages"][0] = HumanMessage(
                    content=f"{context_summary}\n\n{current_content}"
                )

    # Add citation reminder for researcher agent
    if agent_name == "researcher":
        if state.get("resources"):
            resources_info = "**The user mentioned the following resource files:**\n\n"
            for resource in state.get("resources"):
                resources_info += f"- {resource.title} ({resource.description})\n"

            agent_input["messages"].append(
                HumanMessage(
                    content=resources_info
                    + "\n\n"
                    + "You MUST use the **local_search_tool** to retrieve the information from the resource files.",
                )
            )

        agent_input["messages"].append(
            HumanMessage(
                content="IMPORTANT: DO NOT include inline citations in the text. Instead, track all sources and include a References section at the end using numbered format. Include an empty line between each citation for better readability. Use this format for each reference:\n[1](URL) - Source Title\n\n[2](URL) - Another Source Title",
                name="system",
            )
        )

    # Invoke the agent
    default_recursion_limit = 25
    try:
        env_value_str = os.getenv("AGENT_RECURSION_LIMIT", str(default_recursion_limit))
        parsed_limit = int(env_value_str)

        if parsed_limit > 0:
            recursion_limit = parsed_limit
            logger.info(f"Recursion limit set to: {recursion_limit}")
        else:
            logger.warning(
                f"AGENT_RECURSION_LIMIT value '{env_value_str}' (parsed as {parsed_limit}) is not positive. "
                f"Using default value {default_recursion_limit}."
            )
            recursion_limit = default_recursion_limit
    except ValueError:
        raw_env_value = os.getenv("AGENT_RECURSION_LIMIT")
        logger.warning(
            f"Invalid AGENT_RECURSION_LIMIT value: '{raw_env_value}'. "
            f"Using default value {default_recursion_limit}."
        )
        recursion_limit = default_recursion_limit

    logger.info(f"Agent input: {agent_input}")

    # 嘗試執行代理，如果遇到上下文長度錯誤則進一步截斷
    max_retries = 3
    retry_count = 0

    while retry_count < max_retries:
        try:
            # 構建 agent config，保留原始 config 的內容並添加 recursion_limit
            agent_config = {}
            if config:
                # 複製原始 config 的內容
                agent_config.update(config)
            # 設置或覆蓋 recursion_limit
            agent_config["recursion_limit"] = recursion_limit

            result = await agent.ainvoke(input=agent_input, config=agent_config)
            break  # 成功執行，跳出重試循環

        except Exception as e:
            error_msg = str(e).lower()
            if "context_length_exceeded" in error_msg or "maximum context length" in error_msg:
                retry_count += 1
                logger.warning(
                    f"Context length exceeded (attempt {retry_count}/{max_retries}). Further truncating..."
                )

                if retry_count < max_retries:
                    # 更積極地截斷上下文
                    current_content = agent_input["messages"][0].content
                    truncation_factor = 0.8**retry_count  # 每次重試都更積極地截斷

                    max_length = int(len(current_content) * truncation_factor)
                    if max_length < 1000:  # 最小保留 1000 字符
                        max_length = 1000

                    truncated_content = current_content[:max_length]
                    if max_length < len(current_content):
                        truncated_content += (
                            f"\n\n[內容已截斷，原長度: {len(current_content)} 字符]"
                        )

                    agent_input["messages"][0] = HumanMessage(content=truncated_content)
                    logger.info(f"Truncated content to {len(truncated_content)} characters")
                else:
                    # 最後一次嘗試：使用最小化的內容
                    minimal_content = (
                        f"任務: {current_step.title}\n簡要描述: {current_step.description[:200]}..."
                    )
                    agent_input["messages"] = [HumanMessage(content=minimal_content)]
                    logger.warning("Using minimal content for final attempt")

                    try:
                        # 構建 agent config，保留原始 config 的內容並添加 recursion_limit
                        agent_config = {}
                        if config:
                            # 複製原始 config 的內容
                            agent_config.update(config)
                        # 設置或覆蓋 recursion_limit
                        agent_config["recursion_limit"] = recursion_limit

                        result = await agent.ainvoke(input=agent_input, config=agent_config)
                        break
                    except Exception as final_e:
                        logger.error(f"Final attempt failed: {final_e}")

                        # 返回一個基本的錯誤響應
                        class MockMessage:
                            def __init__(self, content):
                                self.content = content

                        result = {
                            "messages": [
                                MockMessage(
                                    f"抱歉，由於上下文長度限制，無法完成任務 '{current_step.title}'。請考慮：\n1. 使用支援更大上下文的模型\n2. 減少研究步驟數量\n3. 調整 MAX_CONTEXT_TOKENS 環境變數"
                                )
                            ]
                        }
                        break
            else:
                # 其他類型的錯誤，直接拋出
                raise e

    # Process the result
    response_content = result["messages"][-1].content
    logger.debug(f"{agent_name.capitalize()} full response: {response_content}")

    # Update the step with the execution result
    current_step.execution_res = response_content
    logger.info(f"Step '{current_step.title}' execution completed by {agent_name}")

    return Command(
        update={
            "messages": [
                HumanMessage(
                    content=response_content,
                    name=agent_name,
                )
            ],
            "observations": observations + [response_content],
        },
        goto="research_team",
    )


async def _setup_and_execute_agent_step(
    state: State,
    config: RunnableConfig,
    agent_type: str,
    default_tools: list,
) -> Command[Literal["research_team"]]:
    """Helper function to set up an agent with appropriate tools and execute a step.

    This function handles the common logic for both researcher_node and coder_node:
    1. Configures MCP servers and tools based on agent type
    2. Creates an agent with the appropriate tools or uses the default agent
    3. Executes the agent on the current step

    Args:
        state: The current state
        config: The runnable config
        agent_type: The type of agent ("researcher" or "coder")
        default_tools: The default tools to add to the agent

    Returns:
        Command to update state and go to research_team
    """
    # 確保 thread context 正確設置
    ensure_thread_context(config)
    configurable = Configuration.from_runnable_config(config)
    mcp_servers = {}
    enabled_tools = {}

    # Extract MCP server configuration for this agent type
    if configurable.mcp_settings:
        for server_name, server_config in configurable.mcp_settings["servers"].items():
            if server_config["enabled_tools"] and agent_type in server_config["add_to_agents"]:
                mcp_servers[server_name] = {
                    k: v
                    for k, v in server_config.items()
                    if k in ("transport", "command", "args", "url", "env")
                }
                for tool_name in server_config["enabled_tools"]:
                    enabled_tools[tool_name] = server_name

    # Create and execute agent with MCP tools if available
    if mcp_servers:
        async with MultiServerMCPClient(mcp_servers) as client:
            loaded_tools = default_tools[:]
            for tool in client.get_tools():
                if tool.name in enabled_tools:
                    tool.description = (
                        f"Powered by '{enabled_tools[tool.name]}'.\n{tool.description}"
                    )
                    loaded_tools.append(tool)
            agent = create_agent(agent_type, agent_type, loaded_tools, agent_type)
            return await _execute_agent_step(state, agent, agent_type, config)
    else:
        # Use default tools if no MCP servers are configured
        agent = create_agent(agent_type, agent_type, default_tools, agent_type)
        return await _execute_agent_step(state, agent, agent_type, config)


async def researcher_node(
    state: State, config: RunnableConfig
) -> Command[Literal["research_team"]]:
    """Researcher node that do research"""
    # 確保 thread context 正確設置
    ensure_thread_context(config)
    logger.info("Researcher node is researching.")
    configurable = Configuration.from_runnable_config(config)
    tools = [get_web_search_tool(configurable.max_search_results), crawl_tool]
    retriever_tool = get_retriever_tool(state.get("resources", []))
    if retriever_tool:
        tools.insert(0, retriever_tool)
    logger.info(f"Researcher tools: {tools}")
    return await _setup_and_execute_agent_step(
        state,
        config,
        "researcher",
        tools,
    )


async def coder_node(state: State, config: RunnableConfig) -> Command[Literal["research_team"]]:
    """Coder node that do code analysis."""
    # 確保 thread context 正確設置
    ensure_thread_context(config)
    logger.info("Coder node is coding.")
    return await _setup_and_execute_agent_step(
        state,
        config,
        "coder",
        [python_repl_tool],
    )
