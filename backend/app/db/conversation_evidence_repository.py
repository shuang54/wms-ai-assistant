"""``ai_ops.conversation_evidence`` 的最小仓储（Phase 4.1 Step 45）。

**唯一 persistence owner**：本表只由本 Repository 访问；
ConversationRepository / EvidenceRepository **不**拥有该表（避免三方重复持有）。

语义（Step 44 冻结，本文件不得越界）：

```text
Conversation → Evidence = REFERENCE（非 ownership）
Evidence = Reusable（同一 Evidence 可被多个 Conversation 引用）
No cascade lifecycle coupling（本表不参与任何状态联动）
```

OD 落地：

* OD-11：双向 0..N —— `list_evidence_ids(conversation_id)` /
  `list_conversation_ids(evidence_id)`；
* OD-12：DEFERRED TO STEP 46 —— 本表无 `turn_id` / `assistant_request_id`，
  API 亦不接受；
* OD-13：幂等 —— `(conversation_id, evidence_id)` 复合主键；
  重复 `create_association` → **返回既有行**（first-write-wins，沿用 Step 37/40 模式），
  数据库约束兜底。

事务边界（沿用 Step 37–40）：**Repository owns transaction** ——
``with factory() as session, session.begin():``；失败整体回滚，不产生半关联。

禁止：Service / API / Runtime 接线（属 Step 46）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import and_, insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.conversation_repository import validate_conversation_id
from backend.app.db.evidence_repository import validate_evidence_id
from backend.app.db.models.conversation import Conversation
from backend.app.db.models.conversation_evidence import ConversationEvidenceRecord
from backend.app.db.models.evidence_record import EvidenceRecord
from backend.app.db.session import get_session_factory

__all__ = [
    "ConversationEvidenceRepository",
    "ConversationEvidenceRepositoryError",
    "ConversationNotFoundRepositoryError",
    "EvidenceNotFoundRepositoryError",
    "ConversationEvidenceReference",
    "CONVERSATION_EVIDENCE_READ_COLUMNS",
]

#: 只读查询允许返回的字段（显式列，**不用** SELECT *）。
CONVERSATION_EVIDENCE_READ_COLUMNS: Final[tuple[str, ...]] = (
    "conversation_id",
    "evidence_id",
    "created_at",
)


@dataclass(frozen=True)
class ConversationEvidenceReference:
    """Conversation → Evidence 引用行（**不是** ORM 对象）。

    只包含引用本身 —— **不含** dataset_version / source_type / raw_content /
    任何 Evidence 内容与会话内容（Step 44：Reference ≠ Evidence 本体）。
    """

    conversation_id: str
    evidence_id: str
    created_at: datetime


class ConversationEvidenceRepositoryError(Exception):
    """Conversation ↔ Evidence 关联持久化 / 读取失败（事务已回滚）。"""


class ConversationNotFoundRepositoryError(ConversationEvidenceRepositoryError):
    """被引用的 Conversation 不存在（拒绝建立孤儿关联）。"""


class EvidenceNotFoundRepositoryError(ConversationEvidenceRepositoryError):
    """被引用的 Evidence 不存在（拒绝建立孤儿关联）。"""


class ConversationEvidenceRepository:
    """``ai_ops.conversation_evidence`` 的最小仓储（Step 45）。"""

    def __init__(
        self, session_factory: sessionmaker[Session] | None = None
    ) -> None:
        self._session_factory = session_factory

    def _factory(self) -> sessionmaker[Session]:
        factory = self._session_factory or get_session_factory()
        if factory is None:  # pragma: no cover - 配置缺失
            raise ConversationEvidenceRepositoryError("session factory 未配置")
        return factory

    @staticmethod
    def _columns() -> tuple[Any, ...]:
        return tuple(
            getattr(ConversationEvidenceRecord, name)
            for name in CONVERSATION_EVIDENCE_READ_COLUMNS
        )

    # ---------- 写 ----------

    def create_association(
        self, conversation_id: str, evidence_id: str
    ) -> ConversationEvidenceReference:
        """建立引用；**幂等** —— 已存在则原样返回既有行。

        Raises:
            ConversationNotFoundRepositoryError: conversation_id 不存在。
            EvidenceNotFoundRepositoryError: evidence_id 不存在。
            ConversationEvidenceRepositoryError: 数据库失败（事务已回滚）。
        """
        validate_conversation_id(conversation_id)
        validate_evidence_id(evidence_id)
        factory = self._factory()
        try:
            with factory() as session, session.begin():
                if (
                    session.execute(
                        select(Conversation.conversation_id).where(
                            Conversation.conversation_id == conversation_id
                        )
                    ).first()
                    is None
                ):
                    raise ConversationNotFoundRepositoryError(
                        f"Conversation 不存在: {conversation_id}"
                    )
                if (
                    session.execute(
                        select(EvidenceRecord.evidence_id).where(
                            EvidenceRecord.evidence_id == evidence_id
                        )
                    ).first()
                    is None
                ):
                    raise EvidenceNotFoundRepositoryError(
                        f"Evidence 不存在: {evidence_id}"
                    )

                existing = session.execute(
                    select(*self._columns()).where(
                        and_(
                            ConversationEvidenceRecord.conversation_id
                            == conversation_id,
                            ConversationEvidenceRecord.evidence_id == evidence_id,
                        )
                    )
                ).first()
                if existing is not None:
                    return ConversationEvidenceReference(*existing)

                row = session.execute(
                    insert(ConversationEvidenceRecord)
                    .values(
                        conversation_id=conversation_id,
                        evidence_id=evidence_id,
                    )
                    .returning(*self._columns())
                ).first()
                if row is None:  # pragma: no cover
                    raise ConversationEvidenceRepositoryError(
                        "Conversation ↔ Evidence 关联插入未返回结果"
                    )
                return ConversationEvidenceReference(*row)
        except SQLAlchemyError as exc:
            raise ConversationEvidenceRepositoryError(
                f"写入 Conversation ↔ Evidence 关联失败: {exc}"
            ) from exc

    # ---------- 读 ----------

    def get_association(
        self, conversation_id: str, evidence_id: str
    ) -> ConversationEvidenceReference | None:
        """读取单条引用（不存在 → ``None``）。"""
        validate_conversation_id(conversation_id)
        validate_evidence_id(evidence_id)
        factory = self._factory()
        try:
            with factory() as session:
                row = session.execute(
                    select(*self._columns()).where(
                        and_(
                            ConversationEvidenceRecord.conversation_id
                            == conversation_id,
                            ConversationEvidenceRecord.evidence_id == evidence_id,
                        )
                    )
                ).first()
        except SQLAlchemyError as exc:
            raise ConversationEvidenceRepositoryError(
                f"读取 Conversation ↔ Evidence 关联失败: {exc}"
            ) from exc
        return ConversationEvidenceReference(*row) if row is not None else None

    def list_evidence_ids(self, conversation_id: str) -> tuple[str, ...]:
        """Conversation → 0..N Evidence（OD-11）。"""
        validate_conversation_id(conversation_id)
        factory = self._factory()
        try:
            with factory() as session:
                rows = session.execute(
                    select(ConversationEvidenceRecord.evidence_id)
                    .where(
                        ConversationEvidenceRecord.conversation_id == conversation_id
                    )
                    .order_by(ConversationEvidenceRecord.evidence_id)
                ).all()
        except SQLAlchemyError as exc:
            raise ConversationEvidenceRepositoryError(
                f"读取 Conversation 的 Evidence 引用失败: {exc}"
            ) from exc
        return tuple(str(row[0]) for row in rows)

    def list_conversation_ids(self, evidence_id: str) -> tuple[str, ...]:
        """Evidence → 0..N Conversation（OD-11；Evidence = Reusable）。"""
        validate_evidence_id(evidence_id)
        factory = self._factory()
        try:
            with factory() as session:
                rows = session.execute(
                    select(ConversationEvidenceRecord.conversation_id)
                    .where(ConversationEvidenceRecord.evidence_id == evidence_id)
                    .order_by(ConversationEvidenceRecord.conversation_id)
                ).all()
        except SQLAlchemyError as exc:
            raise ConversationEvidenceRepositoryError(
                f"读取 Evidence 的 Conversation 引用失败: {exc}"
            ) from exc
        return tuple(str(row[0]) for row in rows)
