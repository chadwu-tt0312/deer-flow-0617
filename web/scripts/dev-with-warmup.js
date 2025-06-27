#!/usr/bin/env node

/**
 * 開發服務器啟動腳本 - 同時啟動 Next.js 開發服務器和預熱功能
 * 這個腳本會先啟動開發服務器，然後在服務器準備就緒後執行預熱
 */

import { spawn } from 'child_process';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

console.log('🚀 啟動 DeerFlow Web UI 開發服務器（含預熱功能）...\n');

// 啟動 Next.js 開發服務器
const nextProcess = spawn('dotenv', ['-e', '../.env', '--', 'next', 'dev', '--turbo'], {
    stdio: 'pipe',
    shell: true,
    cwd: process.cwd()
});

let serverReady = false;
let warmupCompleted = false;

// 監聽 Next.js 服務器輸出
nextProcess.stdout.on('data', (data) => {
    const output = data.toString();
    console.log(output);

    // 檢測服務器是否準備就緒
    if (output.includes('Ready in') && !serverReady) {
        serverReady = true;
        console.log('\n🎯 開發服務器已準備就緒，開始預熱...\n');

        // 延遲 2 秒後開始預熱，確保服務器完全啟動
        setTimeout(() => {
            startWarmup();
        }, 2000);
    }
});

nextProcess.stderr.on('data', (data) => {
    console.error(data.toString());
});

// 啟動預熱功能
function startWarmup() {
    const warmupProcess = spawn('node', ['scripts/warm-up.js'], {
        stdio: 'inherit',
        shell: true,
        cwd: process.cwd()
    });

    warmupProcess.on('close', (code) => {
        warmupCompleted = true;
        if (code === 0) {
            console.log('\n✅ 預熱完成！您現在可以訪問 http://localhost:3000/chat 或 http://192.168.31.180:3000/chat\n');
        } else {
            console.log('\n⚠️  預熱過程中出現問題，但開發服務器仍在運行\n');
        }
    });
}

// 處理進程終止
process.on('SIGINT', () => {
    console.log('\n🛑 正在關閉開發服務器...');
    nextProcess.kill('SIGINT');
    process.exit(0);
});

process.on('SIGTERM', () => {
    console.log('\n🛑 正在關閉開發服務器...');
    nextProcess.kill('SIGTERM');
    process.exit(0);
});

nextProcess.on('close', (code) => {
    console.log(`\n開發服務器已關閉 (代碼: ${code})`);
    process.exit(code);
}); 