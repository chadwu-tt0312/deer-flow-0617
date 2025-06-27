# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# SPDX-License-Identifier: MIT

import os
import yaml
from typing import Dict, Any


def replace_env_vars(value: str) -> str:
    """Replace environment variables in string values."""
    if not isinstance(value, str):
        return value
    if value.startswith("$"):
        env_var = value[1:]
        return os.getenv(env_var, env_var)
    return value


def process_dict(config: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively process dictionary to replace environment variables."""
    if not config:
        return {}
    result = {}
    for key, value in config.items():
        if isinstance(value, dict):
            result[key] = process_dict(value)
        elif isinstance(value, str):
            result[key] = replace_env_vars(value)
        else:
            result[key] = value
    return result


_config_cache: Dict[str, Dict[str, Any]] = {}


def load_yaml_config(file_path: str) -> Dict[str, Any]:
    """Load and process YAML configuration file."""
    # 如果檔案不存在，返回{}
    if not os.path.exists(file_path):
        return {}

    # 檢查快取中是否已存在配置
    if file_path in _config_cache:
        return _config_cache[file_path]

    # 如果快取中不存在，則載入並處理配置
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except UnicodeDecodeError:
        # 如果 UTF-8 解碼失敗，嘗試使用系統預設編碼
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            config = yaml.safe_load(f)

    processed_config = process_dict(config)

    # 將處理後的配置存入快取
    _config_cache[file_path] = processed_config
    return processed_config
