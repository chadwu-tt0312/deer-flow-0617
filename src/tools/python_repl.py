# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# SPDX-License-Identifier: MIT

import logging
import sys
import io
import contextlib
from typing import Annotated
from langchain_core.tools import tool
from langchain_experimental.utilities import PythonREPL
from .decorators import log_io

# Initialize REPL and logger
repl = PythonREPL()
logger = logging.getLogger(__name__)


# 禁用 matplotlib 和 PIL 的詳細日誌
def _suppress_external_loggers():
    """抑制外部套件的詳細日誌"""
    external_loggers = [
        "matplotlib",
        "matplotlib.font_manager",
        "matplotlib.pyplot",
        "matplotlib.backends",
        "matplotlib.ticker",
        "PIL",
        "PIL.PngImagePlugin",
        "PIL.Image",
        "PIL.ImageFile",
    ]
    for logger_name in external_loggers:
        ext_logger = logging.getLogger(logger_name)
        ext_logger.setLevel(logging.ERROR)
        ext_logger.propagate = False


def _configure_matplotlib_backend():
    """配置 matplotlib 使用非 GUI 後端"""
    try:
        import matplotlib

        # 強制使用 Agg 後端（非 GUI）
        matplotlib.use("Agg", force=True)

        # 禁用 tkinter 相關的後端
        import matplotlib.pyplot as plt

        plt.ioff()  # 關閉互動模式

        # 設定環境變數以避免 tkinter
        import os

        os.environ["MPLBACKEND"] = "Agg"

    except ImportError:
        pass  # matplotlib 未安裝


# 在模組載入時就設定
_suppress_external_loggers()
_configure_matplotlib_backend()


@tool
@log_io
def python_repl_tool(
    code: Annotated[str, "The python code to execute to do further analysis or calculation."],
    config: dict = None,  # 添加可選的 config 參數
):
    """Use this to execute python code and do data analysis or calculation. If you want to see the output of a value,
    you should print it out with `print(...)`. This is visible to the user."""
    if not isinstance(code, str):
        error_msg = f"Invalid input: code must be a string, got {type(code)}"
        logger.error(error_msg)
        return f"Error executing code:\n```python\n{code}\n```\nError: {error_msg}"

    logger.info("Executing Python code")

    # 確保外部套件日誌被抑制
    _suppress_external_loggers()

    # 確保 matplotlib 使用非 GUI 後端
    _configure_matplotlib_backend()

    # 捕獲 stderr 輸出，包括 "Exception ignored" 等錯誤
    stderr_capture = io.StringIO()

    try:
        with contextlib.redirect_stderr(stderr_capture):
            result = repl.run(code)

        # 檢查是否有 stderr 輸出
        stderr_output = stderr_capture.getvalue()
        if stderr_output.strip():
            logger.warning(f"Python execution warnings/errors:\n{stderr_output.strip()}")

        # Check if the result is an error message by looking for typical error patterns
        if isinstance(result, str) and ("Error" in result or "Exception" in result):
            logger.error(f"Python execution error: {result}")
            return f"Error executing code:\n```python\n{code}\n```\nError: {result}"

        logger.info("Code execution successful")
    except BaseException as e:
        error_msg = repr(e)
        logger.error(f"Python execution exception: {error_msg}")

        # 也記錄 stderr 輸出
        stderr_output = stderr_capture.getvalue()
        if stderr_output.strip():
            logger.error(f"Additional stderr output:\n{stderr_output.strip()}")

        return f"Error executing code:\n```python\n{code}\n```\nError: {error_msg}"

    result_str = f"Successfully executed:\n```python\n{code}\n```\nStdout: {result}"
    return result_str
