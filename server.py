# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# SPDX-License-Identifier: MIT

"""
Server script for running the DeerFlow API.
"""

import argparse
import logging
import os
import signal
import sys
import uvicorn
from urllib.parse import urlparse
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Import logging configuration
from src.utils.logging_config import setup_deerflow_logging

logger = logging.getLogger(__name__)


def handle_shutdown(signum, frame):
    """Handle graceful shutdown on SIGTERM/SIGINT"""
    logger.info("Received shutdown signal. Starting graceful shutdown...")
    sys.exit(0)


def get_server_config_from_env():
    """Extract host and port from NEXT_PUBLIC_API_URL environment variable."""
    api_url = os.getenv("NEXT_PUBLIC_API_URL")
    if api_url:
        try:
            parsed = urlparse(api_url)
            host = parsed.hostname or "localhost"
            port = parsed.port or 8000
            return host, port
        except Exception as e:
            logger.warning(f"Failed to parse NEXT_PUBLIC_API_URL: {e}")
    return None, None


# Register signal handlers
signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)

if __name__ == "__main__":
    # Get server config from environment first
    env_host, env_port = get_server_config_from_env()

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Run the DeerFlow API server")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload (default: True except on Windows)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=env_host or "localhost",
        help=f"Host to bind the server to (default: {env_host or 'localhost'}, from NEXT_PUBLIC_API_URL if set)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=env_port or 8000,
        help=f"Port to bind the server to (default: {env_port or 8000}, from NEXT_PUBLIC_API_URL if set)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="info",
        choices=["debug", "info", "warning", "error", "critical"],
        help="Log level (default: info)",
    )

    args = parser.parse_args()

    # 設定日誌配置
    debug_mode = args.log_level.lower() == "debug"
    setup_deerflow_logging(debug=debug_mode)

    # Determine reload setting
    reload = False
    if args.reload:
        reload = True

    try:
        if env_host or env_port:
            logger.info(
                f"Using server configuration from NEXT_PUBLIC_API_URL: {os.getenv('NEXT_PUBLIC_API_URL')}"
            )
        logger.info(f"Starting DeerFlow API server on {args.host}:{args.port}")

        # 創建自定義的 uvicorn 配置來保持我們的日誌設定
        uvicorn_config = uvicorn.Config(
            "src.server:app",
            host=args.host,
            port=args.port,
            reload=reload,
            log_level=args.log_level,
            access_log=True,
            use_colors=True,
        )

        # 創建伺服器實例
        server = uvicorn.Server(uvicorn_config)
        server.run()
    except Exception as e:
        logger.error(f"Failed to start server: {str(e)}")
        sys.exit(1)
