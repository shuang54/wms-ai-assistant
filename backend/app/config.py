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
# Business Database 配置（WMS/ERP 独立只读业务数据源）
# ============================================================

@dataclass(frozen=True)
class BusinessDatabaseSettings:
    """WMS/ERP 业务库配置（**独立**于应用库；只读账号）。

    为什么独立（与应用库 ``DatabaseSettings`` / ``DATABASE_URL`` 的关系）：

        * 应用库 = 本项目自身的数据（ai_ops 观测表 / 知识库 / 会话）；
        * 业务库 = 客户 WMS/ERP 的真实业务数据（库存等），**只读**；
        * 二者**不复用连接**（独立 Engine / 独立连接池 / 独立 fail-closed）：
          业务库缺失 / 不可达 ⇒ 明确报错，**绝不**回退应用库、**绝不**伪造数据。

    字段（环境变量；**不修改** DATABASE_URL）：

        host:     ``WMS_DATABASE_HOST``     业务库主机
        port:     ``WMS_DATABASE_PORT``     端口（默认 5432）
        name:     ``WMS_DATABASE_NAME``     数据库名
        user:     ``WMS_DATABASE_USER``     只读账号
        password: ``WMS_DATABASE_PASSWORD`` 密码 —— ``repr=False``（**绝不**出现在
                  repr / 日志 / 异常文本中）；真实值只允许放在本机**忽略跟踪**的
                  ``.env``（.gitignore: ``.env`` / ``.env.*``）
        pool_size / max_overflow: 独立小池（只读单查询工具）
        connect_timeout: 建连超时（秒；钳制 [1, 60]）
    """

    host: str = field(default_factory=lambda: _get_str("WMS_DATABASE_HOST"))
    port: int = field(
        default_factory=lambda: _get_int_clamped("WMS_DATABASE_PORT", 5432, 1, 65535)
    )
    name: str = field(default_factory=lambda: _get_str("WMS_DATABASE_NAME"))
    user: str = field(default_factory=lambda: _get_str("WMS_DATABASE_USER"))
    password: str = field(
        default_factory=lambda: _get_str("WMS_DATABASE_PASSWORD"), repr=False
    )
    pool_size: int = field(
        default_factory=lambda: _get_int_clamped("WMS_DATABASE_POOL_SIZE", 2, 1, 20)
    )
    max_overflow: int = field(
        default_factory=lambda: _get_int_clamped(
            "WMS_DATABASE_MAX_OVERFLOW", 1, 0, 20
        )
    )
    connect_timeout: int = field(
        default_factory=lambda: _get_int_clamped(
            "WMS_DATABASE_CONNECT_TIMEOUT", 5, 1, 60
        )
    )

    @property
    def is_configured(self) -> bool:
        """连接参数是否齐备（含密码）—— **唯一**的"已配置"判定。

        任一缺失（含密码为空字符串 / 纯空白）⇒ False ⇒ 业务数据源视为未配置
        （fail-closed：调用方必须显式报错，不得回退应用库）。
        """
        return bool(
            self.host.strip()
            and self.name.strip()
            and self.user.strip()
            and self.password.strip()
        )

    def summary(self) -> dict[str, object]:
        """非敏感摘要（host / port / database / configured；**无** user / password）。

        供日志与人工排查使用；**绝不**包含凭据。
        """
        return {
            "configured": self.is_configured,
            "host": self.host,
            "port": self.port,
            "database": self.name,
        }


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
# Reranker 配置（Phase 3.5.12 离线实验；Phase 3.7.14 正式接入 RAG）
# ============================================================

# Reranker top_k 允许区间（与 VectorSearchService.MAX_TOP_K=50 对齐，
# 防止误配导致无限资源消耗）
RERANKER_TOP_K_MIN = 1
RERANKER_TOP_K_MAX = 50


@dataclass(frozen=True)
class RerankerSettings:
    """Cross-Encoder Reranker 配置（Phase 3.5.12 引入，Phase 3.7.14 接入 RAG）。

    Phase 3.7.14 起，``enabled=true`` 时 RagService 在 Vector Search 之后、
    ContextBuilder 之前对候选片段重排序：

        Vector Search（candidate_top_k 条）
                ↓
        Reranker（重排序）
                ↓
        截断至 top_k 条
                ↓
        ContextBuilder

    字段：
        enabled:         总开关，默认 False（生产默认关闭，保证既有
                         RAG 行为不变，便于灰度）。
        model:           HuggingFace 模型名，默认 BAAI/bge-reranker-v2-m3
                         （Cross-Encoder，与 BGE-M3 embedding 同家族）。
        device:          推理设备。空字符串 = 自动（CUDA 可用 → cuda，
                         否则 cpu）。允许显式指定 cpu / cuda。
        max_length:      tokenizer 的 max_length（query+doc 拼接后截断长度）。
        batch_size:      单次 forward 的 (query, doc) 对数。
        candidate_top_k: 开启 Reranker 后 Vector Search 的召回条数。
                         环境变量 RERANKER_CANDIDATE_TOP_K，默认 10，
                         钳制到 [1, 50]（与 VectorSearchService 上界一致）。
        top_k:           开启 Reranker 后最终送入 ContextBuilder 的
                         片段条数（调用方未显式传 top_k 时的默认值）。
                         环境变量 RERANKER_TOP_K，默认 3，钳制到 [1, 50]。
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
    candidate_top_k: int = field(
        default_factory=lambda: _get_int_clamped(
            "RERANKER_CANDIDATE_TOP_K", 10,
            RERANKER_TOP_K_MIN, RERANKER_TOP_K_MAX,
        )
    )
    top_k: int = field(
        default_factory=lambda: _get_int_clamped(
            "RERANKER_TOP_K", 3,
            RERANKER_TOP_K_MIN, RERANKER_TOP_K_MAX,
        )
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
# Workflow Read API 认证配置（Phase 6 Step 7.2；fail closed）
# ============================================================


@dataclass(frozen=True)
class WorkflowReadSettings:
    """Workflow Read API 最小认证配置（Phase 6 Step 7.2）。

    **不提供任何默认凭据**：两项默认均为空字符串 ⇒ 视为"未配置" ⇒
    ``/api/workflow-runs*`` 与 ``/api/workflow-approvals*`` 一律拒绝访问
    （503，**绝不**退化为匿名可访问——S7.2 §4.1 fail closed）。

    安全边界（与完整身份系统的差别，如实记录）：
        API Key 仅代表**持有该凭据的调用方**（单凭据 / 单项目模型）；
        它不是用户身份系统、不含 RBAC、不能表达"多用户多项目"。

    字段：
        api_key:        服务端共享 API Key（环境变量 ``WORKFLOW_READ_API_KEY``；
                        默认空 = 未配置）。**不得**写入源码 / 测试默认值 /
                        文档 / 日志 / 响应体。
        api_project_id: 该凭据**唯一**授权的项目作用域（环境变量
                        ``WORKFLOW_READ_API_PROJECT_ID``；默认空 = 未配置）。
                        这是授权作用域的**唯一来源**——客户端的 Query /
                        Header / Body 参数**不能**覆盖或扩宽它。
    """

    api_key: str = field(
        default_factory=lambda: _get_str("WORKFLOW_READ_API_KEY", "")
    )
    api_project_id: str = field(
        default_factory=lambda: _get_str("WORKFLOW_READ_API_PROJECT_ID", "")
    )


# ============================================================
# Agent Read API 认证配置（Phase 6 Step 8A；fail closed）
# ============================================================


@dataclass(frozen=True)
class AgentReadSettings:
    """Agent Read API 最小认证配置（Phase 6 Step 8A；与 Workflow 对称）。

    背景：Phase 5 的 ``GET /api/agent/runs/{run_id}(+/timeline)`` 为无认证
    只读面（客户端可任意主张 ``project_id``）；S7.1 审计确认的 T1/T2 缺口
    在 Agent 侧由本配置 + ``agent_read_auth`` 依赖修复（**安全例外解冻**：
    仅访问控制，Agent 其余契约不变）。

    **不提供任何默认凭据**：两项默认均为空字符串 ⇒ 视为"未配置" ⇒
    Agent 读取端点一律拒绝访问（503，**绝不**匿名放行 / 不回退默认项目）。

    凭据独立性：本配置与 ``WorkflowReadSettings`` **独立**（可分别轮换；
    部署者可将两者配置为相同值——由部署层决定，代码不耦合两域）。

    字段：
        api_key:        服务端共享 API Key（环境变量 ``AGENT_READ_API_KEY``；
                        默认空 = 未配置）。不得写入源码 / 测试默认值 / 文档。
        api_project_id: 该凭据**唯一**授权的项目作用域（环境变量
                        ``AGENT_READ_API_PROJECT_ID``；默认空 = 未配置）——
                        授权作用域的**唯一来源**；客户端 Query / Header /
                        Body 不能覆盖或扩宽。
    """

    api_key: str = field(
        default_factory=lambda: _get_str("AGENT_READ_API_KEY", "")
    )
    api_project_id: str = field(
        default_factory=lambda: _get_str("AGENT_READ_API_PROJECT_ID", "")
    )


# ============================================================
# Workflow Command API 认证配置（Phase 7 Step 1；fail closed）
# ============================================================


@dataclass(frozen=True)
class WorkflowCommandSettings:
    """Workflow **命令** API（start / advance / cancel / approve-reject）认证配置。

    与 ``WorkflowReadSettings`` / ``AgentReadSettings`` **同形同机制**
    （X-API-Key + 服务端唯一授权作用域；不引入新的认证机制），但**独立凭据**
    （读写最小权限分离：读取凭据泄露不影响写面；可分别轮换——部署层可
    配置为相同值，代码不耦合）。

    **不提供任何默认凭据**：两项默认均为空字符串 ⇒ 视为"未配置" ⇒
    命令端点一律拒绝访问（503，**绝不**退化为匿名可访问、
    **绝不**回退默认项目）。

    字段：
        api_key:        服务端共享 API Key（环境变量
                        ``WORKFLOW_COMMAND_API_KEY``；默认空 = 未配置）。
                        不得写入源码 / 测试默认值 / 文档 / 日志 / 响应体。
        api_project_id: 该凭据**唯一**授权的项目作用域（环境变量
                        ``WORKFLOW_COMMAND_API_PROJECT_ID``；默认空 = 未配置）——
                        授权作用域的**唯一来源**；客户端 Query / Header /
                        Body 参数不能覆盖或扩宽。
    """

    api_key: str = field(
        default_factory=lambda: _get_str("WORKFLOW_COMMAND_API_KEY", "")
    )
    api_project_id: str = field(
        default_factory=lambda: _get_str("WORKFLOW_COMMAND_API_PROJECT_ID", "")
    )


@dataclass(frozen=True)
class AgentCommandSettings:
    """Agent **命令** API（start / resume / cancel / approve-reject）认证配置。

    Phase 7 Step 4 —— 与 ``WorkflowCommandSettings`` / ``AgentReadSettings``
    **同形同机制**（X-API-Key + 服务端唯一授权作用域；不引入新的认证机制），
    但**独立凭据**（读写最小权限分离：Agent 读取凭据泄露不影响命令面；
    可分别轮换——部署层可配置为相同值，代码不耦合）。

    **不提供任何默认凭据**：两项默认均为空字符串 ⇒ 视为"未配置" ⇒
    命令端点一律拒绝访问（503，**绝不**退化为匿名可访问、
    **绝不**回退默认项目）。

    字段：
        api_key:        服务端共享 API Key（环境变量
                        ``AGENT_COMMAND_API_KEY``；默认空 = 未配置）。
                        不得写入源码 / 测试默认值 / 文档 / 日志 / 响应体。
        api_project_id: 该凭据**唯一**授权的项目作用域（环境变量
                        ``AGENT_COMMAND_API_PROJECT_ID``；默认空 = 未配置）——
                        授权作用域的**唯一来源**；客户端 Query / Header /
                        Body 参数不能覆盖或扩宽。
    """

    api_key: str = field(
        default_factory=lambda: _get_str("AGENT_COMMAND_API_KEY", "")
    )
    api_project_id: str = field(
        default_factory=lambda: _get_str("AGENT_COMMAND_API_PROJECT_ID", "")
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

    字段映射（业务库**表结构可能与通用假设不同** ⇒ 显式配置；仍由
    ``_assert_safe_identifier`` 白名单校验，**绝不**接受用户输入拼接）：

        material_code_column: 物料编码列名。环境变量
                          ``WMS_INVENTORY_MATERIAL_CODE_COLUMN``，默认
                          ``material_code``（如 ERP 使用 ``mat_code`` ⇒ 配置它）。
        qty_column:       库存数量列名。环境变量 ``WMS_INVENTORY_QTY_COLUMN``，
                          默认 ``qty``（如快照表用 ``available_qty``（可用）或
                          ``onhand_qty``（现有量）⇒ 配置它）。
    """

    schema_name: str = field(
        default_factory=lambda: _get_str("WMS_INVENTORY_SCHEMA", "public")
    )
    table_name: str = field(
        default_factory=lambda: _get_str("WMS_INVENTORY_TABLE", "inventory")
    )
    material_code_column: str = field(
        default_factory=lambda: _get_str(
            "WMS_INVENTORY_MATERIAL_CODE_COLUMN", "material_code"
        )
    )
    qty_column: str = field(
        default_factory=lambda: _get_str("WMS_INVENTORY_QTY_COLUMN", "qty")
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
# Work Order Tool 配置（Phase 3.11 Step 5）
# ============================================================

WORK_ORDER_TOOL_NO_MAX_LEN = 64
WORK_ORDER_TOOL_QUERY_TIMEOUT_SECONDS_MIN = 1
WORK_ORDER_TOOL_QUERY_TIMEOUT_SECONDS_MAX = 60


@dataclass(frozen=True)
class WorkOrderToolSettings:
    """真实 ``get_work_order`` Tool 配置（Phase 3.11 Step 5 引入）。

    字段：
        schema_name:      工单表所在 schema。环境变量 ``WMS_WORK_ORDER_SCHEMA``，
                          默认 ``public``。必须是 SQL 安全标识符
                          （字母/数字/下划线/点；不接受引号）。
        table_name:       工单表名。环境变量 ``WMS_WORK_ORDER_TABLE``，
                          默认 ``work_order``。同 schema_name 约束。
                          **当前部署环境未提供该业务表**（Step 5 勘察结论：
                          真实 DB 无 work_order 表）；生产接入时由部署环境
                          提供，Tool 代码不建表、不改任何数据库结构。
        work_order_no_max_len:
                          ``work_order_no`` 长度上限。环境变量
                          ``WMS_WORK_ORDER_NO_MAX_LEN``，默认 64，
                          钳制到 [1, 256]。
        query_timeout_seconds:
                          PostgreSQL ``statement_timeout``（秒）。
                          环境变量 ``WMS_WORK_ORDER_QUERY_TIMEOUT_SECONDS``，
                          默认 10，钳制到 [1, 60]。
    """

    schema_name: str = field(
        default_factory=lambda: _get_str("WMS_WORK_ORDER_SCHEMA", "public")
    )
    table_name: str = field(
        default_factory=lambda: _get_str(
            "WMS_WORK_ORDER_TABLE", "work_order"
        )
    )
    work_order_no_max_len: int = field(
        default_factory=lambda: _get_int_clamped(
            "WMS_WORK_ORDER_NO_MAX_LEN",
            WORK_ORDER_TOOL_NO_MAX_LEN,
            1, 256,
        )
    )
    query_timeout_seconds: int = field(
        default_factory=lambda: _get_int_clamped(
            "WMS_WORK_ORDER_QUERY_TIMEOUT_SECONDS", 10,
            WORK_ORDER_TOOL_QUERY_TIMEOUT_SECONDS_MIN,
            WORK_ORDER_TOOL_QUERY_TIMEOUT_SECONDS_MAX,
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
    business_database: BusinessDatabaseSettings = field(
        default_factory=BusinessDatabaseSettings
    )
    embedding: EmbeddingSettings = field(default_factory=EmbeddingSettings)
    rag: RagSettings = field(default_factory=RagSettings)
    reranker: RerankerSettings = field(default_factory=RerankerSettings)
    tool: ToolSettings = field(default_factory=ToolSettings)
    inventory_tool: InventoryToolSettings = field(
        default_factory=InventoryToolSettings
    )
    work_order_tool: WorkOrderToolSettings = field(
        default_factory=WorkOrderToolSettings
    )
    project: ProjectSettings = field(default_factory=ProjectSettings)
    workflow_read: WorkflowReadSettings = field(
        default_factory=WorkflowReadSettings
    )
    agent_read: AgentReadSettings = field(default_factory=AgentReadSettings)
    agent_command: AgentCommandSettings = field(
        default_factory=AgentCommandSettings
    )
    workflow_command: WorkflowCommandSettings = field(
        default_factory=WorkflowCommandSettings
    )
    text_to_sql: TextToSQLSettings = field(default_factory=TextToSQLSettings)
    sql_executor: SQLExecutorSettings = field(default_factory=SQLExecutorSettings)
    ai_router: AIRouterSettings = field(default_factory=AIRouterSettings)


settings = Settings()

__all__ = [
    "Settings",
    "LLMSettings",
    "DatabaseSettings",
    "BusinessDatabaseSettings",
    "EmbeddingSettings",
    "RagSettings",
    "RerankerSettings",
    "ToolSettings",
    "InventoryToolSettings",
    "WorkOrderToolSettings",
    "ProjectSettings",
    "WorkflowReadSettings",
    "AgentReadSettings",
    "WorkflowCommandSettings",
    "TextToSQLSettings",
    "SQLExecutorSettings",
    "AIRouterSettings",
    "settings",
]