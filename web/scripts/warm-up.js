#!/usr/bin/env node

/**
 * 預熱腳本 - 自動訪問 /chat 路由進行預編譯
 * 這個腳本會在 Next.js 開發服務器啟動後自動訪問 /chat 頁面，
 * 觸發預編譯，避免用戶首次訪問時的編譯等待時間。
 */

import http from 'http';

const WARMUP_DELAY = 3000; // 等待 3 秒讓開發服務器完全啟動
const MAX_RETRIES = 10;
const RETRY_INTERVAL = 1000;

function warmUpRoute(url, retries = 0) {
    return new Promise((resolve, reject) => {
        const req = http.get(url, (res) => {
            console.log(`✅ 預熱成功: ${url} (狀態碼: ${res.statusCode})`);
            // 消費響應數據以避免記憶體洩漏
            res.on('data', () => { });
            res.on('end', () => resolve());
        });

        req.on('error', (err) => {
            if (retries < MAX_RETRIES) {
                console.log(`⏳ 重試預熱 ${url} (第 ${retries + 1} 次)...`);
                setTimeout(() => {
                    warmUpRoute(url, retries + 1).then(resolve).catch(reject);
                }, RETRY_INTERVAL);
            } else {
                console.error(`❌ 預熱失敗: ${url}`, err.message);
                reject(err);
            }
        });

        req.setTimeout(10000, () => {
            req.destroy();
            if (retries < MAX_RETRIES) {
                console.log(`⏳ 超時重試預熱 ${url} (第 ${retries + 1} 次)...`);
                setTimeout(() => {
                    warmUpRoute(url, retries + 1).then(resolve).catch(reject);
                }, RETRY_INTERVAL);
            } else {
                reject(new Error('預熱超時'));
            }
        });
    });
}

async function main() {
    console.log('🚀 開始預熱 Next.js 路由...');

    // 等待開發服務器啟動
    await new Promise(resolve => setTimeout(resolve, WARMUP_DELAY));

    // 支援多種主機名稱，包括您的開發環境
    const hosts = [
        'localhost:3000',
        '127.0.0.1:3000',
        '192.168.31.180:3000', // 您的開發機器 IP
    ];

    // 嘗試找到可用的主機
    let activeHost = null;
    for (const host of hosts) {
        console.log(`🔍 檢測服務器: ${host}`);
        try {
            await new Promise((resolve, reject) => {
                const req = http.get(`http://${host}`, (res) => {
                    console.log(`✅ 服務器響應: ${host} (狀態碼: ${res.statusCode})`);
                    activeHost = host;
                    res.on('data', () => { }); // 消費響應數據
                    res.on('end', () => resolve());
                });
                req.on('error', (err) => {
                    console.log(`❌ 連接失敗: ${host} - ${err.message}`);
                    reject(err);
                });
                req.setTimeout(3000, () => {
                    console.log(`⏰ 連接超時: ${host}`);
                    req.destroy();
                    reject(new Error('timeout'));
                });
            });
            break;
        } catch (err) {
            // 繼續嘗試下一個主機
            console.log(`⚠️  主機 ${host} 不可用，嘗試下一個...`);
        }
    }

    if (!activeHost) {
        console.log('⚠️  無法找到活躍的開發服務器，跳過預熱');
        return;
    }

    console.log(`🎯 找到活躍的開發服務器: ${activeHost}`);

    const routes = [
        `http://${activeHost}/chat`,
        `http://${activeHost}/`, // 也預熱主頁
    ];

    try {
        for (const route of routes) {
            await warmUpRoute(route);
        }
        console.log('🎉 所有路由預熱完成！');
    } catch (error) {
        console.error('❌ 預熱過程中發生錯誤:', error.message);
    }
}

main().catch(console.error);