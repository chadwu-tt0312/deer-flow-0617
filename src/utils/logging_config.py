#!/usr/bin/env python3
"""
統一的日誌配置模組
支援將日誌同時輸出到控制台和檔案
使用單例模式確保整個應用程式只有一個日誌配置
支援 thread-specific 日誌功能
"""

import logging
import os
import sys
import io
import asyncio
import functools
import threading
import contextvars
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict

# 全域變數追蹤日誌是否已初始化
_logging_initialized = False
_log_file_path = None

# Thread-specific 日誌管理
_thread_loggers: Dict[str, logging.Logger] = {}
_thread_handlers: Dict[str, list] = {}
_thread_lock = threading.Lock()

# Context 變數存儲，用於在異步環境中共享當前 thread 的日誌上下文
# 使用 contextvars 替代 threading.local() 以支援異步環境
_current_thread_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_thread_id", default=None
)
_current_thread_logger: contextvars.ContextVar[Optional[logging.Logger]] = contextvars.ContextVar(
    "current_thread_logger", default=None
)

# 全局 stderr 重定向相關
_original_stderr = None
_stderr_redirected = False


def setup_logging(
    level: str = "INFO",
    log_dir: str = "logs",
    console_output: bool = True,
    file_output: bool = True,
    log_format: Optional[str] = None,
) -> logging.Logger:
    """
    設定統一的日誌配置（單例模式）

    Args:
        level: 日誌級別 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: 日誌檔案目錄
        console_output: 是否輸出到控制台
        file_output: 是否輸出到檔案
        log_format: 自定義日誌格式

    Returns:
        配置好的 logger
    """
    global _logging_initialized, _log_file_path

    # 如果已經初始化過，直接返回現有的 logger
    if _logging_initialized:
        root_logger = logging.getLogger()
        if _log_file_path:
            print(f"📝 日誌已配置，保存到: {_log_file_path}")
        return root_logger

    # 設定日誌級別
    log_level = getattr(logging, level.upper(), logging.INFO)

    # 設定日誌格式
    if log_format is None:
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # 創建根 logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 清除現有的 handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # 創建格式器
    formatter = logging.Formatter(log_format)

    # 控制台輸出
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    # 檔案輸出
    if file_output:
        # 確保日誌目錄存在
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)

        # 使用日期而非精確時間戳，同一天的日誌寫入同一個檔案
        date_str = datetime.now().strftime("%y%m%d")
        log_filename = f"{date_str}.log"
        log_filepath = log_path / log_filename

        # 創建檔案處理器（使用 append 模式）
        file_handler = logging.FileHandler(log_filepath, mode="a", encoding="utf-8")
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        _log_file_path = log_filepath.absolute()
        print(f"📝 日誌將保存到: {_log_file_path}")

    # 標記為已初始化
    _logging_initialized = True

    return root_logger


def setup_thread_logging(
    thread_id: str,
    level: str = "INFO",
    log_dir: str = "logs",
    console_output: bool = True,
    file_output: bool = True,
    log_format: Optional[str] = None,
) -> logging.Logger:
    """
    為特定 thread 設定日誌配置

    Args:
        thread_id: 線程 ID
        level: 日誌級別
        log_dir: 日誌檔案目錄
        console_output: 是否輸出到控制台
        file_output: 是否輸出到檔案
        log_format: 自定義日誌格式

    Returns:
        配置好的 thread-specific logger
    """
    with _thread_lock:
        # 如果已經存在該 thread 的 logger，直接返回
        if thread_id in _thread_loggers:
            return _thread_loggers[thread_id]

        # 創建 thread-specific logger
        logger_name = f"thread_{thread_id}"
        logger = logging.getLogger(logger_name)

        # 設定日誌級別
        log_level = getattr(logging, level.upper(), logging.INFO)
        logger.setLevel(log_level)

        # 防止日誌向上傳播到根 logger（避免重複記錄）
        logger.propagate = False

        # 設定日誌格式（簡潔格式，移除冗餘的 thread_id 和 logger name）
        if log_format is None:
            log_format = "%(asctime)s - %(levelname)s - %(message)s"

        # 創建格式器
        formatter = logging.Formatter(log_format)

        # 儲存 handlers 以便後續清理
        handlers = []

        # 控制台輸出
        if console_output:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(log_level)
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
            handlers.append(console_handler)

        # 檔案輸出
        if file_output:
            # 確保日誌目錄存在
            log_path = Path(log_dir)
            log_path.mkdir(parents=True, exist_ok=True)

            # 使用日期和 thread_id 創建檔案名（取前8個字符避免檔名太長）
            date_str = datetime.now().strftime("%y%m%d")
            thread_short = thread_id[:8] if len(thread_id) > 8 else thread_id
            log_filename = f"{date_str}-{thread_short}.log"
            log_filepath = log_path / log_filename

            # 創建檔案處理器
            file_handler = logging.FileHandler(log_filepath, mode="a", encoding="utf-8")
            file_handler.setLevel(log_level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
            handlers.append(file_handler)

        # 儲存 logger 和 handlers
        _thread_loggers[thread_id] = logger
        _thread_handlers[thread_id] = handlers

        return logger


def get_thread_logger(thread_id: str) -> Optional[logging.Logger]:
    """
    獲取指定 thread 的 logger

    Args:
        thread_id: 線程 ID

    Returns:
        thread-specific logger 或 None
    """
    with _thread_lock:
        return _thread_loggers.get(thread_id)


def set_current_thread_context(thread_id: str, thread_logger: logging.Logger):
    """
    設置當前異步上下文的日誌上下文

    Args:
        thread_id: 線程 ID
        thread_logger: thread-specific logger
    """
    _current_thread_id.set(thread_id)
    _current_thread_logger.set(thread_logger)


def get_current_thread_logger() -> Optional[logging.Logger]:
    """
    獲取當前異步上下文的 logger，如果沒有設置則返回 None

    Returns:
        當前異步上下文的 logger 或 None
    """
    return _current_thread_logger.get()


def get_current_thread_id() -> Optional[str]:
    """
    獲取當前異步上下文的 thread ID，如果沒有設置則返回 None

    Returns:
        當前異步上下文的 thread ID 或 None
    """
    return _current_thread_id.get()


def clear_current_thread_context():
    """
    清除當前異步上下文的日誌上下文
    """
    _current_thread_id.set(None)
    _current_thread_logger.set(None)


class ThreadAwareStderrCapture:
    """捕獲 stderr 輸出並導向到對應的 thread 日誌"""

    def __init__(self, original_stderr):
        self.original_stderr = original_stderr
        self.buffer = io.StringIO()

    def write(self, text):
        # 同時寫入原始 stderr 和緩衝區
        self.original_stderr.write(text)
        self.original_stderr.flush()

        # 如果有內容且不是單純的換行符
        if text.strip():
            # 過濾已知的 tkinter 相關錯誤
            if any(
                filter_text in text
                for filter_text in [
                    "main thread is not in main loop",
                    "tkinter.__init__.py",
                    "Variable.__del__",
                    "Image.__del__",
                    "RuntimeError: main thread is not in main loop",
                ]
            ):
                return  # 忽略這些錯誤

            # 嘗試獲取當前 thread 的 logger
            thread_logger = get_current_thread_logger()
            if thread_logger:
                # 檢查是否是 "Exception ignored" 類型的錯誤
                if "Exception ignored in:" in text or "RuntimeError:" in text:
                    thread_logger.warning(f"🔧 [stderr] {text.strip()}")
                elif "Error" in text or "Exception" in text:
                    thread_logger.error(f"🔧 [stderr] {text.strip()}")
                else:
                    thread_logger.info(f"🔧 [stderr] {text.strip()}")

    def flush(self):
        self.original_stderr.flush()

    def fileno(self):
        return self.original_stderr.fileno()

    def isatty(self):
        return self.original_stderr.isatty()


class ThreadAwareLogHandler(logging.Handler):
    """
    Thread-aware 日誌處理器，將相關日誌記錄到對應的 thread 日誌檔案
    """

    def __init__(self, level=logging.NOTSET):
        super().__init__(level)

    def emit(self, record):
        """
        處理日誌記錄，將相關信息記錄到 thread 日誌
        """
        # 獲取當前線程的 logger
        thread_logger = get_current_thread_logger()
        thread_id = get_current_thread_id()

        # 添加追蹤信息
        debug_info = {
            "record_name": record.name,
            "thread_id": thread_id,
            "has_thread_logger": thread_logger is not None,
            "thread_context_vars": dict(threading.current_thread().__dict__)
            if hasattr(threading.current_thread(), "__dict__")
            else {},
        }

        if thread_logger and thread_id:
            # 檢查是否是需要記錄到 thread 日誌的模組
            thread_relevant_loggers = [
                "src.graph.nodes",
                "src.tools.tavily_search.tavily_search_results_with_images",
                "src.tools.decorators",
                "src.tools.python_repl",
                "src.tools.crawl_tool",
                "src.prompt_enhancer.graph.enhancer_node",
                "src.crawler.jina_client",
            ]

            # 外部套件的日誌（在 thread 上下文中執行時）
            external_loggers = [
                "yfinance",
                "langchain_experimental.utilities.python",
                "matplotlib",
                "matplotlib.font_manager",
                "matplotlib.pyplot",
                "PIL",
                "PIL.PngImagePlugin",
                "httpx",
                "httpcore",
                "urllib3",
                "requests",
                "langchain",
                "openai",
                "anthropic",
                "mcp.client.sse",  # MCP SSE 客戶端日誌
                "mcp.client",  # MCP 客戶端日誌
                "mcp",  # 其他 MCP 相關日誌
            ]

            # 特殊處理 main 模組：只記錄非生命週期日誌到 thread 日誌
            should_record_to_thread = False
            if record.name == "main":
                message = record.getMessage()
                # Thread 生命週期日誌不記錄到 thread 日誌，讓它們保留在主日誌中
                if not any(
                    keyword in message for keyword in ["Thread [", "開始處理新對話", "對話處理完成"]
                ):
                    should_record_to_thread = True
            elif record.name in thread_relevant_loggers or record.name in external_loggers:
                should_record_to_thread = True

            # 如果是相關的日誌，記錄到 thread 日誌
            if should_record_to_thread:
                try:
                    # 格式化消息，添加模組信息
                    if record.name in external_loggers:
                        # 外部套件使用特殊標識，不添加級別信息（由 thread_logger 處理）
                        formatted_msg = f"[{record.name}] {record.getMessage()}"
                    else:
                        # 內部模組使用簡化格式，不重複級別信息
                        module_name = record.name.split(".")[-1] if record.name else record.name
                        formatted_msg = f"{module_name} - {record.getMessage()}"

                    # 使用對應的日誌級別方法記錄
                    level_method = getattr(
                        thread_logger, record.levelname.lower(), thread_logger.info
                    )
                    level_method(formatted_msg)

                    # 記錄成功處理的追蹤信息（只在 DEBUG 模式下）
                    if (
                        record.name in ["src.graph.nodes", "src.tools.decorators"]
                        and thread_logger.level <= logging.DEBUG
                    ):
                        thread_logger.debug(f"🔍 ThreadAwareLogHandler handled: {record.name}")

                    # 關鍵修改：標記這個記錄已被 thread handler 處理，應該被過濾掉
                    # 我們在 record 上添加一個屬性來標記它已被處理
                    setattr(record, "_handled_by_thread_logger", True)

                    # 對於所有 thread-specific 和外部模組，都要確保它們不會洩漏到主日誌
                    # 除非是 main 模組的生命週期日誌
                    if record.name != "main" or not any(
                        keyword in record.getMessage()
                        for keyword in ["Thread [", "開始處理新對話", "對話處理完成"]
                    ):
                        setattr(record, "_should_be_filtered", True)

                except Exception as e:
                    # 如果記錄失敗，記錄錯誤信息
                    if thread_logger:
                        thread_logger.error(
                            f"🚨 ThreadAwareLogHandler failed to process record: {record.name}, error: {e}"
                        )
        else:
            # 記錄缺少 thread context 的情況
            root_logger = logging.getLogger()
            if record.name in ["src.graph.nodes", "src.tools.decorators"]:
                root_logger.debug(
                    f"🚨 MISSING THREAD CONTEXT for {record.name}: thread_id={thread_id}, has_logger={thread_logger is not None}"
                )
                # 即使沒有 thread context，我們也要標記這些關鍵模組的日誌
                setattr(record, "_missing_thread_context", True)


class MainLogFilter(logging.Filter):
    """
    主日誌過濾器，過濾掉不需要在主日誌中顯示的記錄
    """

    def filter(self, record):
        """
        過濾日誌記錄，只允許系統級日誌通過

        注意：這個過濾器只應用於非 ThreadAwareLogHandler 的處理器
        """
        # 首先檢查是否已被 ThreadAwareLogHandler 處理
        if hasattr(record, "_should_be_filtered") and record._should_be_filtered:
            return False

        # 檢查是否已被 ThreadAwareLogHandler 處理
        if hasattr(record, "_handled_by_thread_logger") and record._handled_by_thread_logger:
            return False

        # 獲取當前線程上下文
        thread_id = get_current_thread_id()

        # 定義需要在主日誌中過濾掉的 thread-specific 模組
        # 這些日誌只應該出現在 thread 日誌中
        thread_specific_loggers = [
            "src.graph.nodes",
            "src.tools.tavily_search.tavily_search_results_with_images",
            "src.tools.decorators",
            "src.tools.python_repl",
            "src.tools.crawl_tool",
            "src.prompt_enhancer.graph.enhancer_node",
            "src.crawler.jina_client",
        ]

        # 定義外部套件（當有 thread 上下文時，這些也應該被過濾到 thread 日誌）
        external_loggers = [
            "yfinance",
            "langchain_experimental.utilities.python",
            "matplotlib",
            "matplotlib.font_manager",
            "matplotlib.pyplot",
            "PIL",
            "PIL.PngImagePlugin",
            "httpx",
            "httpcore",
            "urllib3",
            "requests",
            "langchain",
            "openai",
            "anthropic",
            "mcp.client.sse",  # MCP SSE 客戶端日誌
            "mcp.client",  # MCP 客戶端日誌
            "mcp",  # 其他 MCP 相關日誌
        ]

        # 定義應該保留在主日誌中的重要系統日誌
        # 即使在 thread 上下文中，這些日誌也應該出現在主日誌中
        main_log_important = [
            "main",  # Thread 生命週期管理
            "__main__",  # 主程序日誌
            "src.server.app",  # 服務器重要日誌
        ]

        # 特殊處理：Thread 生命週期日誌應該保留在主日誌中
        if record.name in main_log_important:
            # 檢查是否是 Thread 生命週期日誌
            message = record.getMessage()
            if any(
                keyword in message for keyword in ["Thread [", "開始處理新對話", "對話處理完成"]
            ):
                return True  # 保留在主日誌中

        # 如果有 thread 上下文，thread-specific 模組和外部套件的日誌不應該出現在主日誌中
        if thread_id:
            if record.name in thread_specific_loggers or record.name in external_loggers:
                return False  # 過濾掉，不在主日誌中顯示

        # 如果沒有 thread 上下文但是是 thread-specific 的日誌，過濾掉並記錄警告
        if not thread_id and record.name in thread_specific_loggers:
            # 只對重要模組記錄警告，避免過多追蹤信息
            if (
                record.name in ["src.graph.nodes", "src.tools.decorators"]
                and record.levelno >= logging.WARNING
            ):
                # 只記錄 WARNING 級別以上的洩漏
                print(f"🚨 THREAD LEAK: {record.name} - {record.getMessage()[:50]}...")
            return False

        # 沒有 thread 上下文時，或者是其他日誌（如 main），允許通過
        return True


def install_thread_aware_logging():
    """
    安裝 thread-aware 日誌處理器和過濾器
    """
    root_logger = logging.getLogger()

    # 檢查是否已經安裝了 ThreadAwareLogHandler
    thread_handler_exists = False
    for handler in root_logger.handlers:
        if isinstance(handler, ThreadAwareLogHandler):
            thread_handler_exists = True
            break

    if thread_handler_exists:
        return  # 已經安裝，不需要重複安裝

    # 創建並添加 ThreadAwareLogHandler
    thread_handler = ThreadAwareLogHandler()
    thread_handler.setLevel(logging.DEBUG)  # 設置為最低級別，讓它處理所有日誌

    # 重要：將 ThreadAwareLogHandler 插入到列表的開頭，確保它首先處理日誌
    root_logger.handlers.insert(0, thread_handler)

    # 為現有的主日誌 handlers 添加過濾器（跳過 ThreadAwareLogHandler）
    main_filter = MainLogFilter()
    for handler in root_logger.handlers:
        if not isinstance(handler, ThreadAwareLogHandler):
            # 檢查是否已經有 MainLogFilter
            has_main_filter = any(
                isinstance(f, MainLogFilter) for f in getattr(handler, "filters", [])
            )
            if not has_main_filter:
                handler.addFilter(main_filter)


def cleanup_thread_logging(thread_id: str) -> bool:
    """
    清理指定 thread 的日誌資源

    Args:
        thread_id: 線程 ID

    Returns:
        是否成功清理
    """
    with _thread_lock:
        if thread_id not in _thread_loggers:
            return False

        # 關閉並移除所有 handlers
        if thread_id in _thread_handlers:
            for handler in _thread_handlers[thread_id]:
                handler.close()
                if thread_id in _thread_loggers:
                    _thread_loggers[thread_id].removeHandler(handler)
            del _thread_handlers[thread_id]

        # 移除 logger
        if thread_id in _thread_loggers:
            del _thread_loggers[thread_id]

        return True


def cleanup_all_thread_logging():
    """清理所有 thread-specific 日誌資源"""
    with _thread_lock:
        thread_ids = list(_thread_loggers.keys())
        for thread_id in thread_ids:
            cleanup_thread_logging(thread_id)


def reset_logging():
    """重置日誌配置，允許重新初始化"""
    global _logging_initialized, _log_file_path
    _logging_initialized = False
    _log_file_path = None

    # 停用 stderr 捕獲
    disable_stderr_capture()

    # 清除所有現有的 handlers
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        handler.close()
        root_logger.removeHandler(handler)

    # 清理所有 thread-specific 日誌
    cleanup_all_thread_logging()


def get_logger(name: str) -> logging.Logger:
    """
    獲取指定名稱的 logger

    Args:
        name: logger 名稱

    Returns:
        logger 實例
    """
    return logging.getLogger(name)


def enable_debug_logging():
    """啟用 DEBUG 級別的日誌"""
    logging.getLogger("src").setLevel(logging.DEBUG)
    logging.getLogger().setLevel(logging.DEBUG)


def enable_stderr_capture():
    """啟用 stderr 捕獲功能"""
    global _original_stderr, _stderr_redirected

    if not _stderr_redirected:
        _original_stderr = sys.stderr
        sys.stderr = ThreadAwareStderrCapture(_original_stderr)
        _stderr_redirected = True


def disable_stderr_capture():
    """停用 stderr 捕獲功能"""
    global _original_stderr, _stderr_redirected

    if _stderr_redirected and _original_stderr:
        sys.stderr = _original_stderr
        _stderr_redirected = False


def disable_external_loggers():
    """禁用外部套件的詳細日誌"""
    # 設定外部套件的日誌級別為 WARNING 以減少雜訊
    external_loggers = [
        "httpx",
        "httpcore",
        "urllib3",
        "requests",
        "langchain",
        "openai",
        "anthropic",
        "yfinance",
        "matplotlib",
        "matplotlib.font_manager",
        "matplotlib.pyplot",
        "matplotlib.backends",
        "matplotlib.ticker",
        "PIL",
        "PIL.PngImagePlugin",
        "PIL.Image",
        "PIL.ImageFile",
        "mcp.client.sse",  # MCP SSE 客戶端日誌
        "mcp.client",  # MCP 客戶端日誌
        "mcp",  # 其他 MCP 相關日誌
    ]

    for logger_name in external_loggers:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.ERROR)  # 改為 ERROR 級別，更嚴格
        logger.propagate = False  # 禁用向上傳播，確保不會被根 logger 處理

        # 移除現有的 handlers，防止重複輸出
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

    # 特別處理 matplotlib 的根 logger
    matplotlib_root = logging.getLogger("matplotlib")
    matplotlib_root.setLevel(logging.ERROR)
    matplotlib_root.propagate = False

    # 特別處理 PIL 的根 logger
    pil_root = logging.getLogger("PIL")
    pil_root.setLevel(logging.ERROR)
    pil_root.propagate = False


def setup_deerflow_logging(
    debug: bool = False, log_to_file: bool = True, log_dir: str = "logs"
) -> logging.Logger:
    """
    DeerFlow 專用的日誌設定（單例模式）

    注意：使用單例模式，多次調用此函數不會創建新的日誌檔案，
    而是使用第一次調用時的配置。如需重新配置，請先調用 reset_logging()。

    Args:
        debug: 是否啟用 DEBUG 模式
        log_to_file: 是否寫入檔案
        log_dir: 日誌目錄

    Returns:
        配置好的 main logger
    """
    level = "DEBUG" if debug else "INFO"

    # 設置根 logger（關閉 console 輸出，減少雜訊）
    root_logger = setup_logging(
        level=level,
        log_dir=log_dir,
        console_output=False,  # 關閉 console 輸出
        file_output=log_to_file,
    )

    # 創建專門的 main logger，用於記錄系統級信息
    main_logger = logging.getLogger("main")
    main_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # main logger 不需要額外的 handler，會繼承根 logger 的 handlers
    # 但我們可以在這裡添加特殊的格式或過濾邏輯

    # 無論是否為 DEBUG 模式，都要減少外部套件的日誌雜訊
    disable_external_loggers()

    # 安裝 thread-aware 日誌處理器
    install_thread_aware_logging()

    # 啟用 stderr 捕獲功能
    enable_stderr_capture()

    return main_logger


# 環境變數配置
def setup_logging_from_env() -> logging.Logger:
    """從環境變數讀取日誌配置"""
    level = os.getenv("LOG_LEVEL", "INFO")
    log_dir = os.getenv("LOG_DIR", "logs")
    debug = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")
    log_to_file = os.getenv("LOG_TO_FILE", "true").lower() in ("true", "1", "yes")

    return setup_deerflow_logging(debug=debug, log_to_file=log_to_file, log_dir=log_dir)


def ensure_thread_context_decorator(func):
    """
    裝飾器：確保被裝飾的函數在執行時有正確的 thread context

    這個裝飾器會檢查是否有 thread context，如果沒有則嘗試從各種來源恢復
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # 檢查當前是否有 thread context
        current_thread_id = get_current_thread_id()

        if not current_thread_id:
            # 嘗試從函數參數中找到 thread_id 或 config
            thread_id = None

            # 檢查關鍵字參數
            if "thread_id" in kwargs:
                thread_id = kwargs["thread_id"]
            elif "config" in kwargs and hasattr(kwargs["config"], "get"):
                config = kwargs["config"]
                # 標準 LangGraph 方式（優先）
                thread_id = config.get("configurable", {}).get("thread_id")
                if not thread_id:
                    # 備用方案：直接從根層級獲取
                    thread_id = config.get("thread_id")

            # 檢查位置參數中的 config 物件
            if not thread_id:
                for arg in args:
                    if hasattr(arg, "get") and callable(arg.get):
                        # 這可能是一個 config 字典
                        # 標準 LangGraph 方式（優先）
                        potential_thread_id = arg.get("configurable", {}).get("thread_id")
                        if not potential_thread_id:
                            # 備用方案：直接從根層級獲取
                            potential_thread_id = arg.get("thread_id")
                        if potential_thread_id:
                            thread_id = potential_thread_id
                            break

            # 如果找到 thread_id，設置 context
            if thread_id:
                thread_logger = get_thread_logger(thread_id)
                if thread_logger:
                    set_current_thread_context(thread_id, thread_logger)

        return func(*args, **kwargs)

    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        # 檢查當前是否有 thread context
        current_thread_id = get_current_thread_id()

        if not current_thread_id:
            # 嘗試從函數參數中找到 thread_id 或 config
            thread_id = None

            # 檢查關鍵字參數
            if "thread_id" in kwargs:
                thread_id = kwargs["thread_id"]
            elif "config" in kwargs and hasattr(kwargs["config"], "get"):
                config = kwargs["config"]
                # 標準 LangGraph 方式（優先）
                thread_id = config.get("configurable", {}).get("thread_id")
                if not thread_id:
                    # 備用方案：直接從根層級獲取
                    thread_id = config.get("thread_id")

            # 檢查位置參數中的 config 物件
            if not thread_id:
                for arg in args:
                    if hasattr(arg, "get") and callable(arg.get):
                        # 這可能是一個 config 字典
                        # 標準 LangGraph 方式（優先）
                        potential_thread_id = arg.get("configurable", {}).get("thread_id")
                        if not potential_thread_id:
                            # 備用方案：直接從根層級獲取
                            potential_thread_id = arg.get("thread_id")
                        if potential_thread_id:
                            thread_id = potential_thread_id
                            break

            # 如果找到 thread_id，設置 context
            if thread_id:
                thread_logger = get_thread_logger(thread_id)
                if thread_logger:
                    set_current_thread_context(thread_id, thread_logger)

        return await func(*args, **kwargs)

    # 根據函數類型返回適當的包裝器
    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    else:
        return wrapper
