# JSON 解析異常調試指南

## 問題概述

當您看到 "parsed json with extra tokens: {}" 錯誤時，這通常表示：

1. **best-effort-json-parser** 庫在解析包含額外標記的 JSON 時發出警告
2. 前端接收到的數據格式不符合預期的 JSON 結構
3. 後端返回的數據包含無效的 JSON 格式

## 調試步驟

### 1. 啟用前端調試

在瀏覽器開發者工具的 Console 中執行以下腳本：

```javascript
// 複製 web/debug-json-parser.js 的內容並執行
```

這將會：
- ✅ 記錄所有 JSON.parse 調用
- ✅ 攔截 best-effort-json-parser 的警告
- ✅ 提供詳細的錯誤信息

### 2. 檢查瀏覽器控制台

執行調試腳本後，查看控制台中的以下類型日誌：

#### 成功的解析
```
🔍 JSON.parse 調用: {input: "...", inputType: "string", ...}
✅ JSON.parse 成功: {input: "...", output: {...}, ...}
```

#### 失敗的解析
```
❌ JSON.parse 失敗: {error: "...", input: "...", stack: "..."}
🚨 best-effort-json-parser 警告: parsed json with extra tokens: {}
```

#### 前端組件日誌
```
parseJSON input: {type: "string", length: 123, preview: "...", raw: "..."}
parseJSON processed: {original: "...", processed: "...", changed: true}
parseJSON success: {input: "...", output: {...}, type: "object"}
```

### 3. 檢查後端日誌

查看日誌檔案（如 `logs/250623.log`）中的相關記錄：

#### Tavily 搜尋結果
```
INFO - Tavily search sync results: [{"url": "...", "content": "..."}]
ERROR - Failed to serialize Tavily search results to JSON: ...
```

#### 計劃解析
```
DEBUG - Repaired JSON: {"locale": "zh-TW", ...}
DEBUG - Successfully parsed plan: {...}
ERROR - Planner response is not a valid JSON: Expecting ',' delimiter: line 1 column 123
```

#### 工具調用參數
```
DEBUG - Processing tool call 0 args: {toolName: "web_search", ...}
ERROR - Failed to parse tool call args: {error: ..., argsString: "..."}
```

## 常見問題與解決方案

### 1. "parsed json with extra tokens" 警告

**原因**：best-effort-json-parser 嘗試解析包含額外文本的 JSON
**解決**：檢查數據來源，確保返回純淨的 JSON

### 2. 工具調用參數解析失敗

**原因**：流式傳輸中的 JSON 片段不完整
**解決**：檢查 `merge-message.ts` 中的 argsChunks 合併邏輯

### 3. Web 搜尋結果解析失敗

**原因**：Tavily API 返回的數據結構異常
**解決**：檢查 API 響應格式，增強錯誤處理

### 4. 計劃 JSON 解析失敗

**原因**：LLM 返回的計劃格式不符合預期
**解決**：檢查 prompt 模板，改善 JSON 修復邏輯

## 日誌級別設置

### 前端（瀏覽器控制台）
```javascript
// 顯示 debug 日誌
console.debug = console.log;

// 隱藏 debug 日誌
console.debug = () => {};
```

### 後端（Python）
```bash
# 啟用 DEBUG 級別日誌
python server.py --log-level debug

# 或設置環境變數
export LOG_LEVEL=DEBUG
```

## 實用的調試命令

### 測試 JSON 解析
```javascript
// 在瀏覽器控制台中測試
testJSONParsing('{"valid": "json"}');
testJSONParsing('{"invalid": json}'); // 會失敗
testJSONParsing('{}extra tokens'); // 會觸發警告
```

### 檢查工具調用
```javascript
// 查看當前的工具調用
console.log(window.store?.getState?.()?.toolCalls);
```

### 監控網路請求
在開發者工具的 Network 標籤中：
1. 過濾 `/api/chat` 請求
2. 檢查 Response 內容
3. 查看是否有格式異常的數據

## 預防措施

1. **數據驗證**：在前端接收數據前進行格式驗證
2. **錯誤邊界**：使用 React Error Boundary 捕獲解析錯誤
3. **後備方案**：JSON 解析失敗時提供合理的預設值
4. **類型檢查**：使用 TypeScript 嚴格模式檢查數據類型

## 聯繫支援

如果問題持續存在，請提供：
1. 瀏覽器控制台的完整錯誤日誌
2. 後端日誌檔案的相關片段
3. 觸發問題的具體操作步驟
4. 瀏覽器和作業系統版本信息 