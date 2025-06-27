/**
 * Run `build` or `dev` with `SKIP_ENV_VALIDATION` to skip env validation. This is especially useful
 * for Docker builds.
 */
// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: MIT

import "./src/env.js";

/** @type {import("next").NextConfig} */

// DeerFlow leverages **Turbopack** during development for faster builds and a smoother developer experience.
// However, in production, **Webpack** is used instead.
//
// This decision is based on the current recommendation to avoid using Turbopack for critical projects, as it
// is still evolving and may not yet be fully stable for production environments.

const config = {
  // For development mode
  turbopack: {
    rules: {
      "*.md": {
        loaders: ["raw-loader"],
        as: "*.js",
      },
    },
    // 優化開發模式下的編譯速度
    resolveAlias: {
      // 加速常用庫的解析
      "@": "./src",
      "~": "./src",
    },
  },

  // For production mode
  webpack: (config) => {
    config.module.rules.push({
      test: /\.md$/,
      use: "raw-loader",
    });
    return config;
  },

  // Development server configuration
  async headers() {
    return [
      {
        // Apply these headers to all routes in development
        source: "/(.*)",
        headers: [
          {
            key: "Access-Control-Allow-Origin",
            value: process.env.NODE_ENV === "development" ? "*" : "same-origin",
          },
          {
            key: "Access-Control-Allow-Methods",
            value: "GET, POST, PUT, DELETE, OPTIONS",
          },
          {
            key: "Access-Control-Allow-Headers",
            value: "Content-Type, Authorization, X-Requested-With",
          },
          {
            key: "Access-Control-Allow-Credentials",
            value: "true",
          },
        ],
      },
    ];
  },

  // Experimental features for development
  experimental: {
    // Enable development optimizations
    optimizePackageImports: ["lucide-react", "@radix-ui/react-icons"],
    // Enable faster builds
    optimizeCss: true,
  },

  // Allowed development origins for cross-origin requests
  // 根據 Next.js 文檔，只需要主機名稱，不需要協議和端口
  // 基於安全考量，僅允許本機和已知的區域網路地址
  allowedDevOrigins: [
    "localhost",
    "127.0.0.1",
    "192.168.31.*", // 你的具體開發機器 IP
    "*.local" // 支援 .local 域名（用於本地開發）
  ],

  // ... rest of the configuration.
  output: "standalone",
};

export default config;
