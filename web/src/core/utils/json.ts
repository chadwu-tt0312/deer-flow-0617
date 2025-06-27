import { parse } from "best-effort-json-parser";

function isLikelyCompleteJSON(str: string): boolean {
  const trimmed = str.trim();
  if (!trimmed) return false;

  // 檢查是否以 { 開始並以 } 結束
  if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
    // 簡單檢查括號是否平衡
    let braceCount = 0;
    let inString = false;
    let escaped = false;

    for (let i = 0; i < trimmed.length; i++) {
      const char = trimmed[i];

      if (escaped) {
        escaped = false;
        continue;
      }

      if (char === "\\") {
        escaped = true;
        continue;
      }

      if (char === '"') {
        inString = !inString;
        continue;
      }

      if (!inString) {
        if (char === "{") braceCount++;
        if (char === "}") braceCount--;
      }
    }

    return braceCount === 0;
  }

  return false;
}

export function parseJSON<T>(json: string | null | undefined, fallback: T) {
  if (!json) {
    return fallback;
  }

  const raw = json
    .trim()
    .replace(/^```js\s*/, "")
    .replace(/^```json\s*/, "")
    .replace(/^```ts\s*/, "")
    .replace(/^```plaintext\s*/, "")
    .replace(/^```\s*/, "")
    .replace(/\s*```$/, "");

  // 如果字串為空或只有空白字符，返回 fallback
  if (!raw || raw.trim() === "") {
    return fallback;
  }

  // 如果 JSON 看起來不完整，直接返回 fallback，避免解析錯誤
  if (!isLikelyCompleteJSON(raw)) {
    return fallback;
  }

  // 首先嘗試使用原生 JSON.parse
  try {
    return JSON.parse(raw) as T;
  } catch {
    // 如果原生解析失敗，再使用 best-effort-json-parser
    try {
      return parse(raw) as T;
    } catch (error) {
      // 靜默處理錯誤，避免在控制台顯示錯誤訊息
      // 這在流式傳輸過程中是正常的，因為 JSON 可能還沒有完全接收完成
      return fallback;
    }
  }
}
