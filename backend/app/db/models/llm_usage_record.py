"""LLMUsageRecord ORM Model（Phase 3.10.14）。

最小 request-level LLM Usage 持久化表：

    LLMObservation
        ↓ Persistence Boundary（services/llm_usage_persistence_service.py）
        ↓ Repository（db/llm_usage_repository.py）
    llm_usage_record

设计约束（任务书 §八 ~ §十九）：

- **只保存 Usage 事实**：不保存 prompt / messages / SQL / RAG chunks /
  tool arguments / tool results / raw response / headers / API key /
  exception message / stack trace（§十六）；
- **不保存 Cost / Pricing**：本阶段无 Pricing Registry，数据库里没有
  input_price / output_price / currency / *_cost 字段，也不建
  llm_pricing 表（§十三 / §十四）；
- **request_id 来自 LLMObservation.request_id**（Provider 真实请求 ID）；
  缺失存 NULL，**绝不生成 UUID 冒充**（§九，方案 A：保持最小）；
- **provider 来自 LLMObservation.provider**；缺失 NULL，
  禁止 "unknown" / "deepseek" / "default" 隐式填充（§十）；
- **model 来自 LLMObservation.model**（Provider 实际返回），
  禁止用 settings.model 兜底（§十一）；
- **token 字段保持 NULL ≠ 0**：partial usage（如 None / 200 / None）
  原样存 NULL / 200 / NULL，不转 0、不重算 total（§十二）；
- **created_at 仅表示本条持久化记录产生的时间**，不是 LLM 开始时间，
  也不新增 started_at / completed_at / first_token_at 等 tracing
  时间戳（§十七）；
- 主键沿用项目既有 BIGINT 自增规范（§十八），不引入 UUID / ULID /
  Snowflake；
- 索引保持最小：只保留 created_at 索引（与 knowledge_chunk 的
  `ix_*_created_at` 命名风格一致）；request_id 允许 NULL，
  不为"看起来完整"建立无意义索引 / 唯一约束（§十九）；

**独立 schema（重要）**：

    本表位于 `ai_ops` schema，不在业务 schema（`public`）。

    SchemaExplorerService / PostgreSQLMetadataProvider 的
    `inspect(schema="public")` 会读取 public 下**全部**基表作为
    Text-to-SQL 的业务 schema。若把 AI 内部运维表放进 public：
      - 业务表枚举断言（既有 DB 测试）会被破坏；
      - LLM 可能把 `llm_usage_record` 当成业务表生成查询——
        内部运维数据不应进入业务语义层。

    因此本表与业务数据物理隔离在 `ai_ops`（`init_db()` 会
    `CREATE SCHEMA IF NOT EXISTS ai_ops`）。

幂等性（§二十八）：
    本阶段**不提供幂等保证**。同一 observation 被 record() 两次会产生
    两条记录（at-least-once / caller-controlled）。request_id 允许 NULL，
    不能依赖唯一约束实现幂等，因此不伪造幂等机制。
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base

#: AI 内部运维 schema（与业务 schema `public` 物理隔离）。
LLM_USAGE_SCHEMA: Final[str] = "ai_ops"


class LLMUsageRecord(Base):
    """LLM Usage 持久化表：一行 = 一次 LLM 请求的 Provider usage 事实。

    位于 `ai_ops` schema（**不是** public）：本表是 AI 内部运维数据，
    不得出现在 Text-to-SQL 的业务 schema 中。
    """

    __tablename__ = "llm_usage_record"

    # ---- 主键 ----
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="Usage Record 主键",
    )

    # ---- Provider 请求标识 ----
    request_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment=(
            "Provider 请求 ID（来自 LLMObservation.request_id）；"
            "缺失为 NULL，绝不生成 UUID 冒充"
        ),
    )
    provider: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment=(
            "Provider 标识（来自 LLMObservation.provider）；"
            "缺失为 NULL，禁止 unknown / default 隐式填充"
        ),
    )
    model: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment=(
            "实际响应的模型（来自 LLMObservation.model）；"
            "禁止使用配置 model 兜底"
        ),
    )

    # ---- Usage 事实（NULL ≠ 0，原样保存，不重算）----
    prompt_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Prompt token 数（Provider 事实；缺失为 NULL，NULL ≠ 0）",
    )
    completion_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Completion token 数（Provider 事实；缺失为 NULL，NULL ≠ 0）",
    )
    total_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="总 token 数（原样保存 Provider 值；不重算 prompt + completion）",
    )

    # ---- 时间戳 ----
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="本条持久化记录的创建时间（不是 LLM 开始时间）",
    )

    # ---- Table-level 索引（最小）+ 独立 schema ----
    __table_args__ = (
        Index("ix_llm_usage_record_created_at", "created_at"),
        # 与业务 schema（public）隔离：避免本表被 Text-to-SQL 的
        # schema 发现当作业务表。
        {"schema": LLM_USAGE_SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<LLMUsageRecord id={self.id} request_id={self.request_id} "
            f"provider={self.provider} model={self.model} "
            f"total_tokens={self.total_tokens}>"
        )


__all__ = ["LLMUsageRecord", "LLM_USAGE_SCHEMA"]
