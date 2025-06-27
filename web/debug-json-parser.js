// JSON 解析調試工具
// 在瀏覽器開發者工具的 Console 中執行此腳本來啟用詳細的 JSON 解析日誌

(function () {
    console.log('🔍 JSON 解析調試工具已啟用');

    // 設置 console.debug 為可見（某些瀏覽器默認隱藏 debug 日誌）
    if (typeof console.debug === 'function') {
        console.log('✅ console.debug 可用');
    } else {
        console.warn('⚠️ console.debug 不可用，將使用 console.log 替代');
        console.debug = console.log;
    }

    // 攔截 JSON.parse 調用來記錄所有解析嘗試
    const originalJSONParse = JSON.parse;
    JSON.parse = function (text, reviver) {
        console.debug('🔍 JSON.parse 調用:', {
            input: text,
            inputType: typeof text,
            inputLength: text ? text.length : 0,
            preview: text && text.length > 100 ? text.substring(0, 100) + '...' : text
        });

        try {
            const result = originalJSONParse.call(this, text, reviver);
            console.debug('✅ JSON.parse 成功:', {
                input: text,
                output: result,
                outputType: typeof result
            });
            return result;
        } catch (error) {
            console.error('❌ JSON.parse 失敗:', {
                error: error.message,
                input: text,
                stack: error.stack
            });
            throw error;
        }
    };

    // 監聽 best-effort-json-parser 的 console 輸出
    const originalConsoleLog = console.log;
    const originalConsoleWarn = console.warn;

    console.log = function (...args) {
        if (args.length > 0 && typeof args[0] === 'string') {
            if (args[0].includes('parsed json with extra tokens')) {
                console.error('🚨 best-effort-json-parser 警告:', ...args);
                console.trace('調用堆疊:');
            }
        }
        return originalConsoleLog.apply(this, args);
    };

    console.warn = function (...args) {
        if (args.length > 0 && typeof args[0] === 'string') {
            if (args[0].includes('parsed json with extra tokens')) {
                console.error('🚨 best-effort-json-parser 警告:', ...args);
                console.trace('調用堆疊:');
            }
        }
        return originalConsoleWarn.apply(this, args);
    };

    // 提供手動測試函數
    window.testJSONParsing = function (jsonString) {
        console.log('🧪 手動測試 JSON 解析:', jsonString);
        try {
            const result = JSON.parse(jsonString);
            console.log('✅ 解析成功:', result);
            return result;
        } catch (error) {
            console.error('❌ 解析失敗:', error);
            return null;
        }
    };

    console.log('🎯 調試工具設置完成！');
    console.log('💡 使用 testJSONParsing("your json string") 來手動測試 JSON 解析');
    console.log('📊 現在所有 JSON 解析操作都會被記錄');
})(); 