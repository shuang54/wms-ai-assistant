"""应用配置（Phase 3.1）。

所有配置从环境变量加载，支持 .env 文件。
严禁在代码中硬编码任何凭据（API Key / Database Password / Token 等）。

字段：
- 应用层：APP_NAME / APP_ENV / APP_HOST / APP_PORT
- LLM 层：LLM_PROVIDER / LLM_MODEL / LLM_API_KEY / LLM_BASE_URL / LLM_TIMEOUT_*
- Database 层（Phase 3.1）：DATABASE_URL / DATABASE_ECHO / DATABASE_POOL_SIZE / DATABASE_MAX_OVERFLOW
- Tool 层（Phase 3.6.3）：TOOL_MAX_ROUNDS（默认 5，钳制 [1, 20]）
- Project 层（Phase 3.7.1.1）：PROJECT_ID / PROJECT_NAME / PROJECT_DESCRIPTION（非敏感 metadata）

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


def _get_int_clamped(
    key: str, default: int, lo: int, hi: int
) -> int:
    """读取整数环境变量并限制在 [lo, hi] 区间。

    解析失败回退默认值；越界时钳制到边界（而非报错），
    用于防御性上限（如 Tool Calling 最大轮数，Phase 3.6.3）。
    """
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(lo, min(hi, value))


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
    batch_size: int = field(
        default_factory=lambda: _get_int("RERANKER_BATCH_SIZE", 8)
    )


# ============================================================
# Project 配置（Phase 3.7.1.1）
# ============================================================

@dataclass(frozen=True)
class ProjectSettings:
    """项目身份配置（Phase 3.7.1.1，仅供 projects 抽象读取）。

    只包含**非敏感**项目 metadata；数据库连接继续由 DatabaseSettings
    （DATABASE_URL）管理，本节**绝不**复制 / 引用任何连接凭据。

    默认值 vietnam-wms / Vietnam WMS 是当前项目的配置层取值，
    不是 Core 层依赖——换项目只需改环境变量，代码零修改。

    字段：
        project_id:          项目唯一标识。
        project_name:        项目显示名。
        project_description: 项目说明（空 → ProjectContext 中为 None）。
    """

    project_id: str = field(
        default_factory=lambda: _get_str("PROJECT_ID", "vietnam-wms")
    )
    project_name: str = field(
        default_factory=lambda: _get_str("PROJECT_NAME", "Vietnam WMS")
    )
    project_description: str = field(
        default_factory=lambda: _get_str("PROJECT_DESCRIPTION", "")
    )


# ============================================================
# Tool Calling 配置（Phase 3.6.3）
# ============================================================

# TOOL_MAX_ROUNDS 允许区间（Phase 3.6.3 任务书 §十七：>=1，上限 20）
TOOL_MAX_ROUNDS_MIN = 1
TOOL_MAX_ROUNDS_MAX = 20


@dataclass(frozen=True)
class ToolSettings:
    """Tool Calling 配置（Phase 3.6.3 引入）。

    字段：
        max_rounds: 单次请求允许执行的**最大 Tool Calling 轮数**
                    （sequential；每轮最多 1 个 Tool Call）。
                    含义：最多 max_rounds 次 Tool 执行 + max_rounds+1 次 LLM 调用，
                    超出后 LLM 仍请求 Tool → ToolCallingBudgetExceededError。
                    环境变量 TOOL_MAX_ROUNDS，默认 5，
                    钳制到 [1, 20]（防止误配导致无限循环 / 天价账单）。
    """

    max_rounds: int = field(
        default_factory=lambda: _get_int_clamped(
            "TOOL_MAX_ROUNDS", 5, TOOL_MAX_ROUNDS_MIN, TOOL_MAX_ROUNDS_MAX
        )
    )


# ============================================================
# Inventory Tool 配置（Phase 3.7.12）
# ============================================================

INVENTORY_TOOL_MATERIAL_CODE_MAX_LEN = 64
INVENTORY_TOOL_QUERY_TIMEOUT_SECONDS_MIN = 1
INVENTORY_TOOL_QUERY_TIMEOUT_SECONDS_MAX = 60


@dataclass(frozen=True)
class InventoryToolSettings:
    """真实 ``get_inventory`` Tool 配置（Phase 3.7.12 引入）。

    字段：
        schema_name:      库存表所在 schema。环境变量 ``WMS_INVENTORY_SCHEMA``，
                          默认 ``public``。必须是 SQL 安全标识符
                          （字母/数字/下划线/点；不接受引号）。
        table_name:       库存表名。环境变量 ``WMS_INVENTORY_TABLE``，
                          默认 ``inventory``。同 schema_name 约束。
        material_code_max_len:
                          ``material_code`` 长度上限。环境变量
                          ``WMS_INVENTORY_MATERIAL_CODE_MAX_LEN``，
                          默认 64，钳制到 [1, 256]。
        query_timeout_seconds:
                          PostgreSQL ``statement_timeout``（秒）。
                          环境变量 ``WMS_INVENTORY_QUERY_TIMEOUT_SECONDS``，
                          默认 10，钳制到 [1, 60]。
    """

    schema_name: str = field(
        default_factory=lambda: _get_str("WMS_INVENTORY_SCHEMA", "public")
    )
    table_name: str = field(
        default_factory=lambda: _get_str("WMS_INVENTORY_TABLE", "inventory")
    )
    material_code_max_len: int = field(
        default_factory=lambda: _get_int_clamped(
            "WMS_INVENTORY_MATERIAL_CODE_MAX_LEN",
            INVENTORY_TOOL_MATERIAL_CODE_MAX_LEN,
            1, 256,
        )
    )
    query_timeout_seconds: int = field(
        default_factory=lambda: _get_int_clamped(
            "WMS_INVENTORY_QUERY_TIMEOUT_SECONDS", 10,
            INVENTORY_TOOL_QUERY_TIMEOUT_SECONDS_MIN,
            INVENTORY_TOOL_QUERY_TIMEOUT_SECONDS_MAX,
        )
    )


# ============================================================
# Text-to-SQL 配置（Phase 3.7.6）
# ============================================================

TEXT_TO_SQL_MAX_ATTEMPTS_MIN = 1
TEXT_TO_SQL_MAX_ATTEMPTS_MAX = 10
TEXT_TO_SQL_MAX_ROWS_MIN = 1
TEXT_TO_SQL_MAX_ROWS_MAX = 100000


@dataclass(frozen=True)
class TextToSQLSettings:
    """Text-to-SQL Generator 配置（Phase 3.7.6 引入）。

    字段：
        max_attempts: 单次 generate() 最多发起的 LLM 生成次数
                      （含首次；Validator 拒绝后携带错误重试）。
                      环境变量 TEXT_TO_SQL_MAX_ATTEMPTS，默认 3，
                      钳制到 [1, 10]（不允许无限制重试）。
        max_rows:     生成 SQL 的 LIMIT 上限（与 SQLValidator
                      的 max_rows 配合使用）。
                      环境变量 TEXT_TO_SQL_MAX_ROWS，默认 1000，
                      钳制到 [1, 100000]。
    """

    max_attempts: int = field(
        default_factory=lambda: _get_int_clamped(
            "TEXT_TO_SQL_MAX_ATTEMPTS", 3,
            TEXT_TO_SQL_MAX_ATTEMPTS_MIN, TEXT_TO_SQL_MAX_ATTEMPTS_MAX,
        )
    )
    max_rows: int = field(
        default_factory=lambda: _get_int_clamped(
            "TEXT_TO_SQL_MAX_ROWS", 1000,
            TEXT_TO_SQL_MAX_ROWS_MIN, TEXT_TO_SQL_MAX_ROWS_MAX,
        )
    )


# ============================================================
# SQL Executor 配置（Phase 3.7.7）
# ============================================================

SQL_EXECUTOR_TIMEOUT_SECONDS_MIN = 1
SQL_EXECUTOR_TIMEOUT_SECONDS_MAX = 600
SQL_EXECUTOR_MAX_ROWS_MIN = 1
SQL_EXECUTOR_MAX_ROWS_MAX = 100000
SQL_EXECUTOR_MAX_RESULT_BYTES_MIN = 64 * 1024
SQL_EXECUTOR_MAX_RESULT_BYTES_MAX = 64 * 1024 * 1024


# ============================================================
# AI Router 配置（Phase 3.7.8）
# ============================================================


@dataclass(frozen=True)
class AIRouterSettings:
    """AI Router 配置（Phase 3.7.8 引入）。

    字段：
        llm_fallback_enabled: 规则无法判断时是否允许调用 LLM。
                              默认 true；false 时直接 RAG 兜底，
                              适合离线 / 高安全场景。
    """

    llm_fallback_enabled: bool = field(
        default_factory=lambda: _get_bool("AI_ROUTER_LLM_FALLBACK_ENABLED", True)
    )


@dataclass(frozen=True)
class SQLExecutorSettings:
    """Read-only SQL Executor 配置（Phase 3.7.7 引入）。

    字段：
        timeout_seconds:  单条 SQL 语句级超时（PostgreSQL
                          statement_timeout，事务内 SET LOCAL，
                          不污染连接池）。默认 10s，钳制 [1, 600]。
        max_rows:         单次执行返回的最大行数（Validator
                          LIMIT 校验之外的运行时第二层保护）。
                          默认 1000，钳制 [1, 100000]。
        max_result_bytes: 结果总大小保护（按字段字符串化估算，
                          MVP 粗粒度）。默认 1MB，
                          钳制 [64KB, 64MB]。
    """

    timeout_seconds: int = field(
        default_factory=lambda: _get_int_clamped(
            "SQL_EXECUTOR_TIMEOUT_SECONDS", 10,
            SQL_EXECUTOR_TIMEOUT_SECONDS_MIN, SQL_EXECUTOR_TIMEOUT_SECONDS_MAX,
        )
    )
    max_rows: int = field(
        default_factory=lambda: _get_int_clamped(
            "SQL_EXECUTOR_MAX_ROWS", 1000,
            SQL_EXECUTOR_MAX_ROWS_MIN, SQL_EXECUTOR_MAX_ROWS_MAX,
        )
    )
    max_result_bytes: int = field(
        default_factory=lambda: _get_int_clamped(
            "SQL_EXECUTOR_MAX_RESULT_BYTES", 1024 * 1024,
            SQL_EXECUTOR_MAX_RESULT_BYTES_MIN,
            SQL_EXECUTOR_MAX_RESULT_BYTES_MAX,
        )
    )


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
    tool: ToolSettings = field(default_factory=ToolSettings)
    inventory_tool: InventoryToolSettings = field(
        default_factory=InventoryToolSettings
    )
    project: ProjectSettings = field(default_factory=ProjectSettings)
    text_to_sql: TextToSQLSettings = field(default_factory=TextToSQLSettings)
    sql_executor: SQLExecutorSettings = field(default_factory=SQLExecutorSettings)
    ai_router: AIRouterSettings = field(default_factory=AIRouterSettings)


settings = Settings()

__all__ = [
    "Settings",
    "LLMSettings",
    "DatabaseSettings",
    "EmbeddingSettings",
    "RagSettings",
    "RerankerSettings",
    "ToolSettings",
    "InventoryToolSettings",
    "ProjectSettings",
    "TextToSQLSettings",
    "SQLExecutorSettings",
    "AIRouterSettings",
    "settings",
]