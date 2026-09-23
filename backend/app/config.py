"""应用配置（Phase 3.1）。

所有配置从环境变量加载，支持 .env 文件。
严禁在代码中硬编码任何凭据（API Key / Database Password / Token 等）。

字段：
- 应用层：APP_NAME / APP_ENV / APP_HOST / APP_PORT
- LLM 层：LLM_PROVIDER / LLM_MODEL / LLM_API_KEY / LLM_BASE_URL / LLM_TIMEOUT_*
- Database 层（Phase 3.1）：DATABASE_URL / DATABASE_ECHO / DATABASE_POOL_SIZE / DATABASE_MAX_OVERFLOW

DATABASE_URL 为空时，DB 相关功能自动禁用：
- get_engine() 返回 None
- /api/health 返回 database="disabled"
- /api/chat 不受影响
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


def _get_float(key: str, default: float) -> float:
    """读取浮点环境变量，失败回退到默认值。"""
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_bool(key: str, default: bool = False) -> bool:
    """读取布尔型环境变量。接受 1/true/yes/on（不区分大小写）。"""
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# ============================================================
# LLM 配置（Phase 2）
# ============================================================

@dataclass(frozen=True)
class LLMSettings:
    """LLM Provider 配置（Phase 2+ 真正使用）。

    所有字段从环境变量读取。LLM_API_KEY 为空时，LLM Client 将自动回退
    到 MockLLMClient（见 backend/app/llm/client.py 的 create_llm_client）。
    """

    provider: str = field(default_factory=lambda: _get_str("LLM_PROVIDER"))
    model: str = field(default_factory=lambda: _get_str("LLM_MODEL"))
    api_key: str = field(default_factory=lambda: _get_str("LLM_API_KEY"))
    base_url: str = field(default_factory=lambda: _get_str("LLM_BASE_URL"))
    timeout_connect: float = field(
        default_factory=lambda: _get_float("LLM_TIMEOUT_CONNECT", 10.0)
    )
    timeout_read: float = field(
        default_factory=lambda: _get_float("LLM_TIMEOUT_READ", 60.0)
    )
    timeout_write: float = field(
        default_factory=lambda: _get_float("LLM_TIMEOUT_WRITE", 10.0)
    )
    timeout_pool: float = field(
        default_factory=lambda: _get_float("LLM_TIMEOUT_POOL", 10.0)
    )


# ============================================================
# Database 配置（Phase 3.1）
# ============================================================

@dataclass(frozen=True)
class DatabaseSettings:
    """PostgreSQL + pgvector 数据库配置（Phase 3.1）。

    字段：
        url:        SQLAlchemy URL，格式 postgresql+psycopg://user:pass@host:port/db
                    为空时所有 DB 功能禁用（health 返回 database="disabled"）。
        echo:       是否打印 SQL（开发用，生产应关闭）。
        pool_size:  SQLAlchemy 连接池基础大小。
        max_overflow: 超出 pool_size 后的最大溢出连接数。
    """

    url: str = field(default_factory=lambda: _get_str("DATABASE_URL"))
    echo: bool = field(default_factory=lambda: _get_bool("DATABASE_ECHO", False))
    pool_size: int = field(default_factory=lambda: _get_int("DATABASE_POOL_SIZE", 5))
    max_overflow: int = field(
        default_factory=lambda: _get_int("DATABASE_MAX_OVERFLOW", 10)
    )


# ============================================================
# Embedding 配置（Phase 3.2）
# ============================================================

@dataclass(frozen=True)
class EmbeddingSettings:
    """Embedding 模型配置（Phase 3.2 引入，Phase 3.5.1 扩展为完整配置）。

    本阶段**仅**提供 `dimension` 字段占位，用于：
        - `KnowledgeChunk.embedding` 列类型（pgvector `vector(N)`）
        - 未来切换 Embedding Provider 时只需修改环境变量

    **重要**：
        - 当前阶段**不创建 vector 索引**（索引要求 dimension 已稳定）。
        - 后续确定 Embedding 模型后，若 dimension 改变：
          1. 更新 `EMBEDDING_DIMENSION` 环境变量
          2. 执行 ALTER TABLE knowledge_chunk ALTER COLUMN embedding TYPE vector(<new_dim>);
          3. 再考虑加索引（ivfflat / hnsw）

    默认值 1024 = SiliconFlow `BAAI/bge-m3`（Phase 3.5.1.5 真实 API 验证）。
    """

    provider: str = field(default_factory=lambda: _get_str("EMBEDDING_PROVIDER"))
    model: str = field(default_factory=lambda: _get_str("EMBEDDING_MODEL"))
    base_url: str = field(default_factory=lambda: _get_str("EMBEDDING_BASE_URL"))
    api_key: str = field(
        default_factory=lambda: _get_str("EMBEDDING_API_KEY") or _get_str("LLM_API_KEY")
    )
    dimension: int = field(default_factory=lambda: _get_int("EMBEDDING_DIMENSION", 1024))
    timeout: float = field(default_factory=lambda: _get_float("EMBEDDING_TIMEOUT", 60.0))


# ============================================================
# RAG 配置（Phase 3.5.4）
# ============================================================

@dataclass(frozen=True)
class RagSettings:
    """RAG Service 配置（Phase 3.5.4 引入）。

    字段：
        max_context_chars: Context Builder 输出的最大字符数；
                          超限后 Context Builder 会从后往前丢弃片段，
                          最后一个被保留的片段允许尾部截断 content。
                          默认 12000（约 2-3k token，对应中长上下文）。
        default_top_k:    RagService 默认 top_k，与 VectorSearchService 一致 = 5。
    """

    max_context_chars: int = field(
        default_factory=lambda: _get_int("RAG_MAX_CONTEXT_CHARS", 12000)
    )
    default_top_k: int = field(default_factory=lambda: _get_int("RAG_TOP_K", 5))


# ============================================================
# Reranker 配置（Phase 3.5.12，离线实验）
# ============================================================

@dataclass(frozen=True)
class RerankerSettings:
    """Cross-Encoder Reranker 配置（Phase 3.5.12 引入）。

    **仅用于离线实验**（tests/test_reranker_real.py +
    RerankerEvaluationService），**未接入生产 RAG**：
    RagService / ChatService / VectorSearchService / API 均不读取本配置。

    字段：
        enabled:    总开关，默认 False（生产默认关闭）。
        model:      HuggingFace 模型名，默认 BAAI/bge-reranker-v2-m3
                    （Cross-Encoder，与 BGE-M3 embedding 同家族）。
        device:     推理设备。空字符串 = 自动（CUDA 可用 → cuda，
                    否则 cpu）。允许显式指定 cpu / cuda，
                    但**不**据此修改任何生产配置。
        max_length:  tokenizer 的 max_length（query+doc 拼接后截断长度）。
    """

    enabled: bool = field(default_factory=lambda: _get_bool("RERANKER_ENABLED", False))
    model: str = field(
        default_factory=lambda: _get_str("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
    )
    device: str = field(default_factory=lambda: _get_str("RERANKER_DEVICE", ""))
    max_length: int = field(default_factory=lambda: _get_int("RERANKER_MAX_LENGTH", 512))


# ============================================================
# 顶层 Settings
# ============================================================

@dataclass(frozen=True)
class Settings:
    """全局应用配置。"""

    app_name: str = field(default_factory=lambda: _get_str("APP_NAME", "WMS AI Assistant"))
    app_env: str = field(default_factory=lambda: _get_str("APP_ENV", "development"))
    app_host: str = field(default_factory=lambda: _get_str("APP_HOST", "0.0.0.0"))
    app_port: int = field(default_factory=lambda: _get_int("APP_PORT", 8000))
    llm: LLMSettings = field(default_factory=LLMSettings)
    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    embedding: EmbeddingSettings = field(default_factory=EmbeddingSettings)
    rag: RagSettings = field(default_factory=RagSettings)
    reranker: RerankerSettings = field(default_factory=RerankerSettings)


settings = Settings()

__all__ = [
    "Settings",
    "LLMSettings",
    "DatabaseSettings",
    "EmbeddingSettings",
    "RagSettings",
    "RerankerSettings",
    "settings",
]