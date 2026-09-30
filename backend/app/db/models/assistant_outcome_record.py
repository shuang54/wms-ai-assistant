"""AssistantOutcomeRecord ORM Model（Phase 3.12 Step 64）。

**request-level terminal outcome** 持久化表（最小字段）：

    AIOrchestratorService.execute()
        ↓ determine（Step 63：唯一判定点）
    AssistantOutcome（SUCCESS / EMPTY / REFUSED / FAILED）
        ↓ Persistence Service（services/assistant_outcome_persistence_service.py）
        ↓ Repository（db/assistant_outcome_repository.py）
    ai_ops.assistant_outcome_record
        ↓ Assistant Trace Read Model（services/assistant_outcome_query_service.py）
    GET /api/observability/assistant-trace/{assistant_request_id} → outcome

设计约束：

* **Assistant-level，不是 LLM-level**：一次 Assistant 请求最多 1 条 Outcome，
  但它可能对应 0..N 次 LLM 调用 ⇒ 因此**不能**写进 ``llm_usage_record``
  （那是 LLM 请求级事实）；同理不写进 Tool / RAG execution record
  （Tool success ≠ Assistant success）。
* **只存终态事实**：``assistant_request_id`` / ``outcome`` / ``created_at`` /
  ``id``。**不含** question / prompt / content / answer / SQL / RAG chunks /
  tool arguments / tool results / route / error_class / error_message /
  exception / stacktrace / 凭据。
* **first-write-wins**：``assistant_request_id`` UNIQUE；重复写入
  （``ON CONFLICT DO NOTHING``）返回 ``None``，**不覆盖**已有终态
  （SUCCESS → FAILED 视为重复请求 / 生命周期 bug，本表不做状态机）。
* **独立 schema**：位于 ``ai_ops``（与业务 schema ``public`` 物理隔离）；
  本表**不得**出现在 Text-to-SQL 的业务 schema 中。
* **历史兼容**：旧 Trace 没有 Outcome 行 → 读路径返回 ``None``
  （**绝不**根据 LLM usage / Tool / RAG / HTTP status 猜测）。
* **无 migration framework**：全新表随 ``Base.metadata.create_all()`` 创建
  （已在 ``db/models/__init__.py`` 注册）；既有库首次执行 ``init_db()`` 即补建
  —— 新表无需 backfill / 无 ensure_* 步骤。
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import BigInteger, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base

#: AI 内部运维 schema（与业务 schema `public` 物理隔离）。
ASSISTANT_OUTCOME_SCHEMA: Final[str] = "ai_ops"

#: ``assistant_request_id`` 唯一索引（一次 Assistant 请求最多 1 条终态）。
#:
#: * **UNIQUE**：request-level terminal fact —— 幂等以数据库为唯一事实来源；
#: * **非 partial**：该列 NOT NULL，没有"无身份"行（与 llm_usage_record 的
#:   partial unique 不同：usage 允许多行 / 允许 NULL request_id）；
#: * 同时服务 Trace 的 ``WHERE assistant_request_id = ?`` 精确匹配。
ASSISTANT_OUTCOME_REQUEST_ID_INDEX: Final[str] = (
    "uq_assistant_outcome_record_assistant_request_id"
)

#: ``outcome`` 枚举列长度（SUCCESS / EMPTY / REFUSED / FAILED 均 ≤ 8 字符；
#: 留出余量但**不**接受未知值——写入前由 Persistence Service 校验）。
ASSISTANT_OUTCOME_VALUE_MAX_LENGTH: Final[int] = 16


class AssistantOutcomeRecord(Base):
    """Assistant request-level 终态（一行 = 一次 /api/ai/chat 的最终业务结果）。

    位于 ``ai_ops`` schema（**不是** public）：AI 内部运维数据。
    """

    __tablename__ = "assistant_outcome_record"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="Outcome Record 主键",
    )
    assistant_request_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment=(
            "Assistant Trace ID（一次 /api/ai/chat 请求的 request_id；"
            "由 Orchestrator 生成，UNIQUE）"
        ),
    )
    outcome: Mapped[str] = mapped_column(
        String(ASSISTANT_OUTCOME_VALUE_MAX_LENGTH),
        nullable=False,
        comment=(
            "Assistant 终态：SUCCESS / EMPTY / REFUSED / FAILED"
            "（写入前由 Persistence Service 校验；无 error_class）"
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="本条终态记录的创建时间",
    )

    __table_args__ = (
        Index(
            ASSISTANT_OUTCOME_REQUEST_ID_INDEX,
            "assistant_request_id",
            unique=True,
        ),
        {"schema": ASSISTANT_OUTCOME_SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AssistantOutcomeRecord id={self.id} "
            f"assistant_request_id={self.assistant_request_id} "
            f"outcome={self.outcome}>"
        )


__all__ = [
    "AssistantOutcomeRecord",
    "ASSISTANT_OUTCOME_SCHEMA",
    "ASSISTANT_OUTCOME_REQUEST_ID_INDEX",
    "ASSISTANT_OUTCOME_VALUE_MAX_LENGTH",
]
