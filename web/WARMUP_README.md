# DeerFlow Web UI 預熱功能

## 概述

為了改善用戶體驗，我們實現了一個預熱系統，在開發服務器啟動後自動預編譯 `/chat` 頁面，避免用戶首次訪問時的編譯等待時間。

## 功能特點

- 🚀 **自動預熱**: 開發服務器啟動後自動訪問關鍵路由
- 🎯 **智能主機檢測**: 自動檢測可用的開發服務器主機
- 🔄 **重試機制**: 內建重試邏輯，確保預熱成功
- 🌐 **多主機支援**: 支援 localhost、127.0.0.1 和區域網路 IP

## 使用方法

### 啟動帶預熱的開發服務器

```bash
# 使用 pnpm 直接啟動
cd web
pnpm run dev:warm

# 或者使用 bootstrap 腳本（推薦）
./bootstrap.sh -d
```

### 配置自定義主機

如果您的開發服務器運行在不同的 IP 地址上，請編輯 `web/scripts/warm-up.js`：

```javascript
const hosts = [
  'localhost:3000',
  '127.0.0.1:3000',
  '192.168.31.180:3000', // 您的開發機器 IP
  'your-custom-ip:3000', // 添加您的自定義 IP
];
```

## 預熱過程

1. **等待服務器啟動** (5秒)
2. **檢測可用主機**: 嘗試連接各個主機地址
3. **預熱路由**: 自動訪問以下頁面
   - `/chat` - 聊天頁面
   - `/` - 主頁
4. **完成通知**: 顯示預熱完成狀態

## 輸出示例

```
🚀 開始預熱 Next.js 路由...
🎯 找到活躍的開發服務器: 192.168.31.180:3000
✅ 預熱成功: http://192.168.31.180:3000/chat (狀態碼: 200)
✅ 預熱成功: http://192.168.31.180:3000/ (狀態碼: 200)
🎉 所有路由預熱完成！
```

## 故障排除

### 預熱失敗

如果預熱失敗，檢查：
1. 開發服務器是否正常啟動
2. 防火牆是否阻擋了連接
3. IP 地址是否正確

### 自定義延遲時間

如果您的開發服務器啟動較慢，可以調整 `WARMUP_DELAY`：

```javascript
const WARMUP_DELAY = 8000; // 增加到 8 秒
```

## 技術細節

- **預熱腳本**: `web/scripts/warm-up.js`
- **npm 腳本**: `dev:warm` 在 `package.json` 中定義
- **主機檢測**: 使用 HTTP 請求測試連接性
- **重試邏輯**: 最多重試 10 次，每次間隔 1 秒

## 效果

- ✅ 消除首次訪問 `/chat` 頁面的編譯等待時間
- ✅ 改善用戶體驗，無需等待 "Compiled /chat in 12.6s"
- ✅ 支援多種開發環境配置
- ✅ 自動化預編譯過程 