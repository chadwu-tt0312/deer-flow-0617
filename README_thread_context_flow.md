# Thread Context 完整流程梳理

## 概述

本文檔梳理了 DeerFlow 系統中完整的 thread context 傳遞流程，確保所有模組（nodes、decorators、python_repl 等）都能正確使用 thread_id 進行日誌記錄。

**重要更新**：系統已從 `threading.local()` 升級為 `contextvars`，以正確支援異步環境中的 thread 隔離。

## 架構圖

```
用戶請求 → server/app.py → LangGraph → nodes →    agents → tools
    ↓           ↓            ↓        ↓          ↓          ↓
  thread_id  設置config   傳遞config  確保context  傳遞config  記錄日誌
```

## 完整流程

### 1. 請求入口：server/app.py

```python
# 在 chat 端點中
config = {
    "configurable": {
        "thread_id": "generated-thread-id",  # 標準 LangGraph 方式
        "resources": resources,
        "max_plan_iterations": max_plan_iterations,
        "max_step_num": max_step_num,
        "max_search_results": max_search_results,
        "mcp_settings": mcp_settings,
        "report_style": report_style.value,
        "enable_deep_thinking": enable_deep_thinking,
    }
}

# 設置 thread logging
thread_logger = setup_thread_logging(thread_id)
if thread_logger:
    set_current_thread_context(thread_id, thread_logger)

# 啟動 LangGraph
async for agent, _, event_data in graph.astream(input_data, config=config, stream_mode=["messages", "updates"], subgraphs=True):
    # 確保在每次迭代中都有正確的 thread context
    # 這是關鍵修復：防止異步迭代中 context 丟失
    set_current_thread_context(thread_id, thread_logger)
    
    # 處理事件...
```

### 2. LangGraph 節點：src/graph/nodes.py

每個節點函數都應該：

```python
def my_node(state: State, config: RunnableConfig):
    # 第一步：確保 thread context
    ensure_thread_context(config)
    
    # 現在可以安全使用日誌
    logger.info("節點開始執行")
    
    # 業務邏輯...
    return result
```

**已實現的節點**：
- ✅ `coordinator_node`
- ✅ `planner_node`
- ✅ `background_investigation_node`
- ✅ `reporter_node`
- ✅ `researcher_node`
- ✅ `coder_node`
- ✅ `_setup_and_execute_agent_step`
- ✅ `_execute_agent_step`

### 3. Agent 執行：修正 config 傳遞

在 `_execute_agent_step` 中，確保完整的 config（包含 thread_id）傳遞給 agent：

```python
# 構建 agent config，保留原始 config 的內容
agent_config = {}
if config:
    agent_config.update(config)  # 複製原始 config
agent_config["recursion_limit"] = recursion_limit  # 添加特定配置

result = await agent.ainvoke(input=agent_input, config=agent_config)
```

### 4. 工具函數：自動 thread context 支援

#### 4.1 使用 @log_io 裝飾器的工具

```python
@tool
@log_io  # 自動支援 thread context
def my_tool(
    param: str,
    config: dict = None,  # 添加可選的 config 參數
):
    # 工具邏輯...
    return result
```

**已修改的工具**：
- ✅ `python_repl_tool` - 添加 config 參數
- ✅ `crawl_tool` - 添加 config 參數

#### 4.2 使用 create_logged_tool 的工具

```python
# 自動包含 thread context 支援
LoggedTavilySearch = create_logged_tool(TavilySearchResultsWithImages)
```

**已支援的工具**：
- ✅ `LoggedTavilySearch`
- ✅ `LoggedDuckDuckGoSearch`
- ✅ `LoggedBraveSearch`
- ✅ `LoggedArxivSearch`

#### 4.3 BaseTool 類工具

```python
class RetrieverTool(BaseTool):
    # BaseTool 自動處理 config 參數
    def _run(self, keywords: str, run_manager=None):
        # 工具邏輯...
```

### 5. 日誌系統：src/utils/logging_config.py

#### 5.1 異步環境支援

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

#### 5.2 Thread Context 管理

```python
def ensure_thread_context(config: RunnableConfig) -> str:
    """確保當前線程有正確的 thread context"""
    # 標準 LangGraph 方式（優先）
    thread_id = config.get("configurable", {}).get("thread_id")
    if not thread_id:
        # 備用方案：直接從根層級獲取
        thread_id = config.get("thread_id")
    
    if thread_id and get_current_thread_id() != thread_id:
        thread_logger = get_thread_logger(thread_id)
        if thread_logger:
            set_current_thread_context(thread_id, thread_logger)
    
    return thread_id
```

#### 5.3 自動 Thread Context 裝飾器

```python
@ensure_thread_context_decorator
def my_function(*args, **kwargs):
    # 自動從參數中提取 config 並設置 thread context
    pass
```

#### 5.4 日誌過濾和路由

- **ThreadAwareLogHandler**：將 thread-specific 日誌記錄到對應檔案
- **MainLogFilter**：防止 thread-specific 日誌洩漏到主日誌
- **Thread-specific 模組列表**：
  ```python
  thread_relevant_loggers = [
      "src.graph.nodes",
      "src.tools.decorators", 
      "src.tools.python_repl",
      "src.tools.crawl_tool",
      # ... 其他模組
  ]
  ```

## 配置清單

### ✅ 已完成的配置

1. **節點層級**：所有 LangGraph 節點都調用 `ensure_thread_context(config)`
2. **Agent 層級**：`_execute_agent_step` 正確傳遞完整的 config
3. **工具層級**：
   - `@log_io` 裝飾器支援 thread context
   - `create_logged_tool` 支援 thread context
   - 主要工具函數添加 config 參數
4. **日誌層級**：
   - Thread-aware 日誌處理器
   - 主日誌過濾器
   - 自動 thread context 裝飾器

### 🔧 使用方式

#### 新增工具函數

```python
@tool
@log_io
def new_tool(param: str, config: dict = None):
    """新工具函數"""
    logger.info(f"處理參數: {param}")
    return f"結果: {param}"
```

#### 新增節點函數

```python
def new_node(state: State, config: RunnableConfig):
    # 必須：確保 thread context
    ensure_thread_context(config)
    
    logger.info("新節點開始執行")
    # 業務邏輯...
    return result
```

#### 新增 BaseTool 類

```python
class NewTool(BaseTool):
    # BaseTool 自動處理 config
    def _run(self, param: str, run_manager=None):
        logger.info(f"工具執行: {param}")
        return f"結果: {param}"
```

## 測試驗證

### 測試要點

1. **Thread 日誌檔案**：`logs/YYMMDD-THREADID.log` 應包含：
   - 節點執行日誌
   - 工具調用日誌
   - Agent 執行日誌

2. **主日誌檔案**：`logs/YYMMDD.log` 應該：
   - 不包含 thread-specific 模組日誌
   - 只包含系統級別日誌

3. **Thread Context**：
   - 所有工具函數能正確獲取 thread_id
   - 日誌記錄到正確的檔案

### 驗證指令

```bash
# 檢查主日誌是否有洩漏
grep -E "(src\.tools\.decorators|src\.tools\.python_repl)" logs/YYMMDD.log

# 檢查 thread 日誌是否包含工具調用
grep "Tool.*called" logs/YYMMDD-THREADID.log
```

## 異步環境中的 Thread 隔離問題

### 問題背景

在異步環境中，`threading.local()` 存在嚴重問題：
- 不同的異步任務可能運行在同一個 OS 線程上
- 導致不同 thread_id 的任務共享同一個 `threading.local()` 實例
- 結果：Thread A 的日誌被記錄到 Thread B 的文件中

### 解決方案：使用 contextvars

```python
# ❌ 舊方式：threading.local()（有問題）
_thread_local = threading.local()
_thread_local.thread_id = "thread_a"  # 可能被其他異步任務覆蓋

# ✅ 新方式：contextvars（正確）
_current_thread_id = contextvars.ContextVar('current_thread_id', default=None)
_current_thread_id.set("thread_a")  # 異步上下文隔離
```

### 關鍵修復點

1. **異步迭代中的 Context 保持**：
   ```python
   async for agent, _, event_data in graph.astream(...):
       # 確保每次迭代都重新設置 context
       set_current_thread_context(thread_id, thread_logger)
   ```

2. **Context 變數的正確使用**：
   ```python
   # 設置 context
   _current_thread_id.set(thread_id)
   _current_thread_logger.set(thread_logger)
   
   # 獲取 context
   current_id = _current_thread_id.get()
   current_logger = _current_thread_logger.get()
   ```

## 故障排除

### 常見問題

1. **日誌洩漏到主日誌**
   - 檢查是否調用 `ensure_thread_context(config)`
   - 確認 config 中包含 thread_id

2. **Thread 日誌檔案未創建**
   - 檢查 `setup_thread_logging()` 是否被調用
   - 確認 `set_current_thread_context()` 是否被調用

3. **工具函數無法記錄日誌**
   - 確認工具函數有 config 參數
   - 檢查是否使用 `@log_io` 或 `@ensure_thread_context_decorator`

### 調試工具

```python
from src.utils.logging_config import get_current_thread_id, get_current_thread_logger

def debug_thread_context():
    thread_id = get_current_thread_id()
    thread_logger = get_current_thread_logger()
    print(f"Thread ID: {thread_id}")
    print(f"Thread Logger: {thread_logger}")
```

## 結論

通過這個完整的流程，DeerFlow 系統現在能夠：

1. ✅ 正確傳遞 thread_id 從請求入口到所有模組
2. ✅ 確保所有日誌記錄到正確的檔案
3. ✅ 防止 thread-specific 日誌洩漏到主日誌
4. ✅ 提供簡單的 API 供新模組使用

整個系統的日誌記錄現在是完全 thread-aware 的，每個對話的執行過程都能完整追蹤。

## 最新修復總結

### ✅ 已解決的關鍵問題

1. **異步環境中的 Thread 隔離**：
   - ❌ 舊問題：`threading.local()` 在異步環境中導致不同 thread 的日誌混合
   - ✅ 解決方案：使用 `contextvars` 實現正確的異步上下文隔離

2. **異步迭代中的 Context 丟失**：
   - ❌ 舊問題：在 `async for` 迴圈中 context 可能丟失
   - ✅ 解決方案：在每次迭代中重新設置 thread context

3. **配置格式標準化**：
   - ✅ 使用標準 LangGraph 配置格式：`config["configurable"]["thread_id"]`
   - ✅ 保持向後兼容：支援 `config["thread_id"]`

### 🔧 技術實現

- **Context 管理**：`contextvars.ContextVar` 替代 `threading.local()`
- **異步支援**：在異步迭代中主動維護 context
- **自動恢復**：`@ensure_thread_context_decorator` 裝飾器
- **日誌隔離**：ThreadAwareLogHandler 和 MainLogFilter

### 📊 驗證結果

現在每個 thread 的日誌都能正確隔離：
- `logs/250627-xu4Dg-TK.log` - Thread A 的完整日誌
- `logs/250627-lQVXyTCd.log` - Thread B 的完整日誌
- 不再出現日誌交叉記錄的問題

## 日誌分類規則

### 主日誌 (`logs/YYMMDD.log`)

**應該包含**：
- ✅ Thread 生命週期管理日誌
  - `"Thread [ID] 開始處理新對話"`
  - `"Thread [ID] 對話處理完成"`
- ✅ 系統級別日誌
  - `__main__` 模組的日誌
  - `src.server.app` 重要日誌
- ✅ 沒有 thread context 時的一般日誌

**不應該包含**：
- ❌ Thread-specific 模組日誌
  - `src.graph.nodes`
  - `src.tools.decorators`
  - `src.tools.python_repl`
  - `src.tools.crawl_tool`
- ❌ 外部套件日誌（在 thread 上下文中時）
  - `langchain_experimental.utilities.python`
  - `matplotlib`、`PIL` 等

### Thread 日誌 (`logs/YYMMDD-THREADID.log`)

**應該包含**：
- ✅ 所有 thread-specific 模組日誌
- ✅ 外部套件日誌（在 thread 上下文中執行時）
- ✅ 非生命週期的 main 模組日誌

**不應該包含**：
- ❌ Thread 生命週期管理日誌
- ❌ 系統啟動/關閉日誌

### 實際測試結果

根據最新測試，日誌分類已正確實現：

```bash
# 主日誌檔案內容（最新測試）
2025-06-27 14:38:55,651 - main - INFO - 🆔 Thread [test-class] 開始處理新對話
2025-06-27 14:38:55,651 - main - INFO - ✅ Thread [test-class] 對話處理完成  
2025-06-27 14:38:55,651 - __main__ - INFO - 系統啟動完成

# Thread 日誌檔案內容
2025-06-27 14:38:55,651 - WARNING - [langchain_experimental.utilities.python] Python REPL can execute arbitrary code. Use with caution.
2025-06-27 14:38:55,651 - INFO - decorators - Tool python_repl_tool called with parameters: code=test
2025-06-27 14:38:55,651 - INFO - main - 處理用戶請求
``` 