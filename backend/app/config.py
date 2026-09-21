"""应用配置（Phase 1）。

所有配置从环境变量加载，支持 .env 文件。
严禁在代码中硬编码任何凭据（API Key / Password / Token 等）。

字段：
- 应用层：APP_NAME / APP_ENV / APP_HOST / APP_PORT
- LLM 层：LLM_PROVIDER / LLM_MODEL / LLM_API_KEY / LLM_BASE_URL

LLM 字段在 Phase 1 仅用于验证配置链路，Phase 2 才会真正使用。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# 项目根目录下的 .env（如果存在）会被加载；缺失不报错。
load_dotenv()


def _get_str(key: str, default: str = "") -> str:
    """读取字符串环境变量，缺失或为空时返回默认值。"""
    value = os.getenv(key)
    if value is None or value == "":
        return default
    return value


def _get_int(key: str, default: int) -> int:
    """读取整数环境变量，失败回退到默认值。"""
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class LLMSettings:
    """LLM Provider 配置（Phase 2+ 真正使用）。"""

    provider: str = field(default_factory=lambda: _get_str("LLM_PROVIDER"))
    model: str = field(default_factory=lambda: _get_str("LLM_MODEL"))
    api_key: str = field(default_factory=lambda: _get_str("LLM_API_KEY"))
    base_url: str = field(default_factory=lambda: _get_str("LLM_BASE_URL"))


@dataclass(frozen=True)
class Settings:
    """全局应用配置。"""

    app_name: str = field(default_factory=lambda: _get_str("APP_NAME", "WMS AI Assistant"))
    app_env: str = field(default_factory=lambda: _get_str("APP_ENV", "development"))
    app_host: str = field(default_factory=lambda: _get_str("APP_HOST", "0.0.0.0"))
    app_port: int = field(default_factory=lambda: _get_int("APP_PORT", 8000))
    llm: LLMSettings = field(default_factory=LLMSettings)


settings = Settings()

__all__ = ["Settings", "LLMSettings", "settings"]
