"""RAG Execution Record ORM Model（Phase 3.12 Step 46；Contract = Step 45）。

一行 = 一条 ``RagExecutionObservation``（RAG 运行时观测的**持久化事实**）。

    RagService → RagExecutionObservation（frozen；13 字段）
        ↓
    ai_ops.rag_execution_record（本 Model）

位于 ``ai_ops`` schema（**不是** public）：AI 内部运维数据，
不参与 Text-to-SQL 的业务 schema。

字段契约（Step 45，**逐字段锁定**）：
    · 13 字段 1:1（列名不变；不新增 / 不改名 / 不丢字段）；
    · 主键 ``id``（BIGINT 自增）—— 沿用 Tool / LLM 既有主键模式；
      观测 DTO **不**持有主键（数据库生成）；
    · ``rerank_elapsed_ms`` 允许 NULL（未使用 Reranker → NULL，**不是 0**）；
    · ``chunk_ids`` / ``document_ids`` 使用项目既有 JSONB 方案
      （与 ``knowledge_*`` 的 ``meta_data`` 同为 ``dialects.postgresql.JSONB``；
      **不**引入新的序列化框架）；只保存 ID，不保存正文 / similarity / embedding；
    · **禁止**字段：query / answer / content / similarity / embedding / prompt /
      messages / raw response / SQL / credentials / project_id（永不进入本表）。

索引（Step 45 §八；最小）：
    · ``ix_rag_execution_record_request_id``  —— Assistant Trace 主查询键
    · ``ix_rag_execution_record_started_at``  —— 时间序 / 未来 retention
    · 未建：chunk_id / document_id / project_id / query / similarity（无查询需求）

表创建：由 ``Base.metadata.create_all()`` 自动创建（在
``backend/app/db/models/__init__.py`` 注册后；无 Alembic / 无手工 DDL）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base

__all__ = [
    "RagExecutionRecordModel",
    "RAG_EXECUTION_SCHEMA",
    "RAG_EXECUTION_TABLE",
    "RAG_EXECUTION_PERSISTED_FIELDS",
]

#: AI 内部运维数据所在 schema（与 public 业务 schema 隔离）。
RAG_EXECUTION_SCHEMA: Final[str] = "ai_ops"

#: 表名（`ai_ops.rag_execution_record`）
RAG_EXECUTION_TABLE: Final[str] = "rag_execution_record"

#: 落库字段白名单（严格 = RagExecutionObservation 的 13 个字段；不含主键）。
#: 顺序 = Step 45 契约字段顺序 = Read DTO 顺序。
RAG_EXECUTION_PERSISTED_FIELDS: Final[tuple[str, ...]] = (
    "request_id",
    "started_at",
    "finished_at",
    "duration_ms",
    "result_count",
    "used_chunks_count",
    "top_k",
    "context_truncated",
    "context_chars",
    "reranker_used",
    "rerank_elapsed_ms",
    "chunk_ids",
    "document_ids",
)


class RagExecutionRecordModel(Base):
    """一次 RAG 执行的持久化事实（一行 = 一条 Observation）。"""

    __tablename__ = RAG_EXECUTION_TABLE

    # ---- 主键（独立；request_id **不是**主键）----
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="RAG Execution Record 主键（BIGINT 自增；项目既有规范）",
    )

    # ---- 关联标识（Assistant Trace ID；唯一来源 = Orchestrator）----
    request_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment=(
            "Assistant Trace ID（一次 /api/ai/chat 请求；Step 35/36 唯一来源）；"
            "与 Tool Execution Record 的 request_id 同名同义；不重新生成"
        ),
    )

    # ---- 时间（timezone-aware）----
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="RAG 开始时间（TIMESTAMP WITH TIME ZONE；UTC）",
    )
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="RAG 结束时间（>= started_at）",
    )
    duration_ms: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="端到端耗时毫秒（perf_counter 差值；>= 0）",
    )

    # ---- 检索 / Context 事实（Trace-safe 数值）----
    result_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Vector Search 候选条数（rerank 前；>= 0）",
    )
    used_chunks_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="实际纳入 Context 的片段数（>= 0）",
    )
    top_k: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="本次生效的 top_k（>= 0；配置相关，非业务数据）",
    )
    context_truncated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        comment="Context 是否发生截断",
    )
    context_chars: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="拼装后的 Context 字符数（>= 0）",
    )
    reranker_used: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        comment="Reranker 是否参与（配置开关事实）",
    )
    rerank_elapsed_ms: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Rerank 耗时毫秒；未使用 Reranker → NULL（**不是 0**）",
    )

    # ---- 命中 identifier（仅 ID；JSONB 数组；去重 + 首次出现顺序）----
    chunk_ids: Mapped[list[int]] = mapped_column(
        JSONB,
        nullable=False,
        comment="纳入 Context 的 chunk id 数组（去重 + 首次出现顺序；仅 ID）",
    )
    document_ids: Mapped[list[int]] = mapped_column(
        JSONB,
        nullable=False,
        comment="对应的 document id 数组（去重 + 首次出现顺序；仅 ID）",
    )

    # ---- 索引（最小）+ 独立 schema ----
    __table_args__ = (
        # 理由：Read Boundary 的 get_by_request_id()（精确查询）主键路径
        Index("ix_rag_execution_record_request_id", "request_id"),
        # 理由：时间序 / 未来时间范围查询与 retention 评估
        Index("ix_rag_execution_record_started_at", "started_at"),
        # 未建索引（本阶段**不**预建）：chunk_id / document_id / project_id
        # （无查询需求；需要时另开阶段评估 —— Step 45 §八）。
        {"schema": RAG_EXECUTION_SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<RagExecutionRecordModel id={self.id} "
            f"request_id={self.request_id} "
            f"result_count={self.result_count} "
            f"used_chunks_count={self.used_chunks_count}>"
        )
