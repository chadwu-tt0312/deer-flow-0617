# LangGraph 節點中的 Thread ID 傳遞與日誌記錄

## 概述

在 LangGraph 多代理系統中，確保每個節點的日誌都能正確記錄到對應的 thread 日誌檔案中是非常重要的。本文檔說明了如何正確設置和維護 thread context。

## 問題背景

在異步執行環境中，Python 的 `threading.local()` 存在嚴重問題：

1. **異步任務共享問題**：不同的異步任務可能運行在同一個 OS 線程上，導致共享同一個 `threading.local()` 實例
2. **Context 覆蓋**：Thread A 的 context 可能被 Thread B 覆蓋
3. **日誌混亂**：日誌無法記錄到正確的 thread 日誌檔案，出現 Thread A 的內容記錄到 Thread B 的檔案中

**解決方案**：使用 `contextvars` 替代 `threading.local()` 以正確支援異步環境。

## 解決方案

### 1. 在節點函數中確保 Thread Context

每個 LangGraph 節點函數都應該在開始時調用 `ensure_thread_context(config)`：

```python
from src.utils.logging_config import ensure_thread_context

def my_node(state: State, config: RunnableConfig):
    # 確保 thread context 正確設置
    ensure_thread_context(config)
    
    # 現在可以安全地使用日誌
    logger.info("節點開始執行")
    
    # 其他業務邏輯...
```

### 2. 在異步函數中的使用

```python
async def my_async_node(state: State, config: RunnableConfig):
    # 確保 thread context 正確設置
    ensure_thread_context(config)
    
    logger.info("異步節點開始執行")
    
    # 異步業務邏輯...
```

## 在 LangGraph 中設置 Thread ID

### 標準 LangGraph 方式（主要方式）

本專案使用標準的 LangGraph 配置方式：

```python
# 標準 LangGraph 方式（src/server/app.py）
config = {
    "configurable": {
        "thread_id": "your-thread-id",
        "resources": resources,
        "max_plan_iterations": max_plan_iterations,
        "max_step_num": max_step_num,
        "max_search_results": max_search_results,
        "mcp_settings": mcp_settings,
        "report_style": report_style.value,
        "enable_deep_thinking": enable_deep_thinking,
    }
}

async for agent, _, event_data in graph.astream(
    input_data, 
    config=config, 
    stream_mode=["messages", "updates"], 
    subgraphs=True
):
    # 確保在每次迭代中都有正確的 thread context
    set_current_thread_context(thread_id, thread_logger)
    # 處理事件...
```

### 備用支援方式

系統同時支援直接在根層級設置的方式：

```python
# 備用方式（向後兼容）
config = {
    "thread_id": "your-thread-id",
    # 其他配置...
}
```

## 核心機制

### 1. `ensure_thread_context()` 函數

```python
def ensure_thread_context(config: RunnableConfig) -> str:
    """
    確保當前線程有正確的 thread context
    
    優先從 config["configurable"]["thread_id"] 獲取（標準 LangGraph 方式），
    備用從 config["thread_id"] 獲取（向後兼容）
    """
    # 標準 LangGraph 方式（優先）
    thread_id = config.get("configurable", {}).get("thread_id")
    
    if not thread_id:
        # 備用方案：直接從根層級獲取
        thread_id = config.get("thread_id")
    
    if thread_id:
        # 設置或恢復 thread context
        thread_logger = get_thread_logger(thread_id)
        if thread_logger:
            set_current_thread_context(thread_id, thread_logger)
    
    return thread_id
```

### 2. 異步環境中的 Context 管理

**重要變更**：系統現在使用 `contextvars` 替代 `threading.local()` 以支援異步環境：

```python
import contextvars

# Context 變數存儲，用於在異步環境中共享當前 thread 的日誌上下文
_current_thread_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'current_thread_id', default=None
)
_current_thread_logger: contextvars.ContextVar[Optional[logging.Logger]] = contextvars.ContextVar(
    'current_thread_logger', default=None
)

def set_current_thread_context(thread_id: str, thread_logger: logging.Logger):
    """設置當前異步上下文的日誌上下文"""
    _current_thread_id.set(thread_id)
    _current_thread_logger.set(thread_logger)

def get_current_thread_id() -> Optional[str]:
    """獲取當前異步上下文的 thread ID"""
    return _current_thread_id.get()

def get_current_thread_logger() -> Optional[logging.Logger]:
    """獲取當前異步上下文的 logger"""
    return _current_thread_logger.get()
```

### 3. Thread Context 自動恢復

系統提供了裝飾器 `ensure_thread_context_decorator`，可以自動從函數參數中恢復 thread context：

```python
from src.utils.logging_config import ensure_thread_context_decorator

@ensure_thread_context_decorator
def my_tool_function(query: str, config: RunnableConfig = None):
    # 裝飾器會自動恢復 thread context
    logger.info(f"工具執行: {query}")
    return result
```

## 日誌檔案結構

Thread 日誌檔案會按照以下格式命名：

```
logs/
├── 250626-BZiftgF-.log    # thread-specific 日誌
├── 250626.log             # 主系統日誌
└── ...
```

- `250626`：日期（年月日）
- `BZiftgF-`：thread_id 的前8個字符
- `.log`：日誌檔案擴展名

## 最佳實踐

### 1. 節點函數模板

```python
def my_node(state: State, config: RunnableConfig):
    # 第一行：確保 thread context
    ensure_thread_context(config)
    
    # 記錄節點開始
    logger.info("節點開始執行")
    
    # 獲取配置
    configurable = Configuration.from_runnable_config(config)
    
    # 業務邏輯...
    
    # 記錄節點完成
    logger.info("節點執行完成")
    
    return result
```

### 2. 工具函數模板

```python
@ensure_thread_context_decorator
def my_tool(query: str, config: RunnableConfig = None):
    logger.info(f"工具開始執行: {query}")
    
    # 工具邏輯...
    
    logger.info("工具執行完成")
    return result
```

### 3. 異步函數模板

```python
async def my_async_function(state: State, config: RunnableConfig):
    ensure_thread_context(config)
    
    logger.info("異步函數開始")
    
    # 異步業務邏輯...
    await some_async_operation()
    
    logger.info("異步函數完成")
    return result
```

## 故障排除

### 1. 日誌沒有記錄到 thread 檔案

檢查：
- 是否在節點函數開始時調用了 `ensure_thread_context(config)`
- config 中是否包含正確的 thread_id
- thread_id 是否在正確的位置（根層級或 configurable 中）

### 2. Thread Context 丟失

可能原因：
- **異步環境問題**：使用了 `threading.local()` 而非 `contextvars`
- **異步迭代中 context 丟失**：在 `async for` 迴圈中沒有重新設置 context
- 沒有正確傳遞 config 參數
- 在子線程中執行但沒有設置 context

解決方案：
- ✅ **已修復**：使用 `contextvars` 替代 `threading.local()`
- ✅ **已修復**：在異步迭代中重新設置 context
- 使用 `ensure_thread_context_decorator` 裝飾器
- 確保所有需要日誌的函數都能訪問到 config

### 3. 日誌重複記錄

檢查：
- 是否多次調用了 `ensure_thread_context()`
- 是否有多個 logger 實例

## 配置檢查

可以使用以下程式碼檢查當前的 thread context：

```python
from src.utils.logging_config import get_current_thread_id, get_current_thread_logger

def debug_thread_context():
    thread_id = get_current_thread_id()
    thread_logger = get_current_thread_logger()
    
    print(f"當前 Thread ID: {thread_id}")
    print(f"Thread Logger: {thread_logger}")
    
    if thread_logger:
        thread_logger.info("Thread context 測試日誌")
```

這個機制確保了在 LangGraph 的複雜執行環境中，每個對話的日誌都能正確地記錄到對應的檔案中，方便調試和追蹤。

## 最新修復總結

### 🚨 解決的關鍵問題

**問題**：不同 thread 的日誌混合記錄
- Thread A (xu4Dg-TK) 的內容出現在 Thread B (lQVXyTCd) 的日誌檔案中
- 根本原因：`threading.local()` 在異步環境中不能正確隔離

**解決方案**：
1. ✅ **使用 contextvars**：正確支援異步環境中的上下文隔離
2. ✅ **異步迭代修復**：在每次 `async for` 迭代中重新設置 context
3. ✅ **配置標準化**：使用標準 LangGraph 配置格式

### 🔧 技術細節

```python
# ❌ 舊方式（有問題）
_thread_local = threading.local()
_thread_local.thread_id = "thread_a"  # 可能被其他異步任務覆蓋

# ✅ 新方式（正確）
_current_thread_id = contextvars.ContextVar('current_thread_id', default=None)
_current_thread_id.set("thread_a")  # 異步上下文隔離
```

### 📊 驗證結果

修復後的效果：
- ✅ 每個 thread 的日誌完全隔離
- ✅ 不再出現日誌交叉記錄
- ✅ 可以並行處理多個對話而不互相干擾
- ✅ 完整的調試追蹤能力

### 🎯 使用建議

對於新的節點或工具函數：
1. **必須**在開始時調用 `ensure_thread_context(config)`
2. **推薦**使用 `@ensure_thread_context_decorator` 裝飾器
3. **確保**所有需要日誌的函數都能訪問到 config 參數 