# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# SPDX-License-Identifier: MIT

from typing import Literal

# Define available LLM types
LLMType = Literal["basic", "reasoning", "vision"]

# Define agent-LLM mapping
AGENT_LLM_MAP: dict[str, LLMType] = {
    "coordinator": "basic",  # 基礎對話即可
    "planner": "basic",  # 🔥 最重要：深度思考制定計劃 (由網頁決定用 basic 或 reasoning <Deep Thinking>)
    "researcher": "basic",  # 成本考量：搜尋和整理用基礎模型
    "coder": "reasoning",  # 🔥 重要：程式邏輯需要推理能力
    "reporter": "reasoning",  # 🔥 重要：報告品質需要深度分析
    "podcast_script_writer": "basic",  # 創意寫作用基礎模型即可
    "ppt_composer": "basic",  # 格式化任務用基礎模型
    "prose_writer": "basic",  # 文字創作用基礎模型
    "prompt_enhancer": "reasoning",  # 提示詞優化需要推理能力
}
