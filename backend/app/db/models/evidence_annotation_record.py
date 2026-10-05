"""Evidence Annotation ORM Model（Phase 4.1 Step 37 —— 真实持久化实现）。

严格对应 Step 36 冻结的 Annotation Persistence Contract：

    ai_ops.evidence_annotation_record
    ├── annotation_id         String(128) PK
    ├── evidence_id           String(128) NOT NULL FK → ai_ops.evidence_record.evidence_id
    │                         （ON DELETE CASCADE；**唯一**外键）
    ├── case_id               String(128) NOT NULL（Step 31/32 case identity）
    ├── annotation_version    String(64) NOT NULL（必须 ≠ dataset_version）
    ├── annotator_id          String(128) NOT NULL
    ├── review_status         String(32) NOT NULL（DRAFT / REVIEWED）
    ├── created_at            DateTime(timezone=True) server_default=now()
    └── updated_at            DateTime(timezone=True) server_default=now() onupdate=now()

关联（Step 36 §6）：

* 通过 **evidence_id** 建立稳定关联；**禁止** content / source-name 文本匹配；
* 不允许以 annotation_version 与 dataset_version 相同（Step 27：两个版本独立）。

约束：

* ``uq_evidence_annotation_evidence_case_version`` (evidence_id, case_id,
  annotation_version)：同一 Evidence 的同一 case 同一标注版本只存在一条；
* ``ix_evidence_annotation_evidence_id``：按 Evidence 检索 Annotation。

沿用现有 DB 风格：Base / Mapped+mapped_column / comment / string 状态 /
``ai_ops`` schema / 无 Alembic（create_all）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models.evidence_record import EVIDENCE_SCHEMA, EVIDENCE_TABLE

#: 表名（``ai_ops.evidence_annotation_record``）。
EVIDENCE_ANNOTATION_TABLE: Final[str] = "evidence_annotation_record"

ANNOTATION_ID_MAX_LENGTH: Final[int] = 128
CASE_ID_MAX_LENGTH: Final[int] = 128
ANNOTATION_VERSION_MAX_LENGTH: Final[int] = 64
ANNOTATOR_ID_MAX_LENGTH: Final[int] = 128
ANNOTATION_REVIEW_STATUS_MAX_LENGTH: Final[int] = 32

#: 唯一约束名（Evidence + case + annotation_version）。
ANNOTATION_UNIQUE_CONSTRAINT: Final[str] = (
    "uq_evidence_annotation_evidence_case_version"
)

#: Evidence 检索索引名。
ANNOTATION_EVIDENCE_INDEX: Final[str] = "ix_evidence_annotation_evidence_id"

# ---- review 状态（沿用 Step 28~30 的 string 状态风格）----
ANNOTATION_REVIEW_DRAFT: Final[str] = "DRAFT"
ANNOTATION_REVIEW_REVIEWED: Final[str] = "REVIEWED"

ANNOTATION_REVIEW_VALUES: Final[tuple[str, ...]] = (
    ANNOTATION_REVIEW_DRAFT,
    ANNOTATION_REVIEW_REVIEWED,
)

#: Step 41 冻结的合法评审迁移（**单向**：DRAFT → REVIEWED；REVIEWED 为终态）。
#:
#: 明确禁止：REVIEWED → DRAFT（回退）· REVIEWED → REVIEWED（重复评审）。
#: 与 Evidence 状态迁移（``EVIDENCE_STATUS_TRANSITIONS``）**互不联动** ——
#: Annotation 评审属 Step 41，Evidence 状态推进属 Step 43。
ANNOTATION_REVIEW_TRANSITIONS: Final[dict[str, tuple[str, ...]]] = {
    ANNOTATION_REVIEW_DRAFT: (ANNOTATION_REVIEW_REVIEWED,),
    ANNOTATION_REVIEW_REVIEWED: (),
}


class EvidenceAnnotationRecord(Base):
    """Annotation 行（一条 = 某个 Evidence 的某个 case 的一次标注版本）。"""

    __tablename__ = EVIDENCE_ANNOTATION_TABLE

    annotation_id: Mapped[str] = mapped_column(
        String(ANNOTATION_ID_MAX_LENGTH),
        primary_key=True,
        nullable=False,
        comment="Annotation ID（服务端签发；唯一 / 不可变）",
    )

    # ---- 唯一外键：稳定关联 Evidence（禁止文本匹配）----
    evidence_id: Mapped[str] = mapped_column(
        String(ANNOTATION_ID_MAX_LENGTH),
        ForeignKey(
            f"{EVIDENCE_SCHEMA}.{EVIDENCE_TABLE}.evidence_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        comment="所属 Evidence（FK → ai_ops.evidence_record.evidence_id；ON DELETE CASCADE）",
    )

    case_id: Mapped[str] = mapped_column(
        String(CASE_ID_MAX_LENGTH),
        nullable=False,
        comment="case identity（Step 31/32；不是 conversation_id）",
    )

    annotation_version: Mapped[str] = mapped_column(
        String(ANNOTATION_VERSION_MAX_LENGTH),
        nullable=False,
        comment="标注版本（必须 ≠ dataset_version；Step 27）",
    )

    annotator_id: Mapped[str] = mapped_column(
        String(ANNOTATOR_ID_MAX_LENGTH),
        nullable=False,
        comment="标注者身份（independence 依据；Step 32）",
    )

    review_status: Mapped[str] = mapped_column(
        String(ANNOTATION_REVIEW_STATUS_MAX_LENGTH),
        nullable=False,
        default=ANNOTATION_REVIEW_DRAFT,
        server_default=ANNOTATION_REVIEW_DRAFT,
        comment="评审状态：DRAFT / REVIEWED",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Annotation 写入时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="最近一次评审状态变更时间",
    )

    __table_args__ = (
        UniqueConstraint(
            "evidence_id",
            "case_id",
            "annotation_version",
            name=ANNOTATION_UNIQUE_CONSTRAINT,
        ),
        Index(ANNOTATION_EVIDENCE_INDEX, "evidence_id"),
        {"schema": EVIDENCE_SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<EvidenceAnnotationRecord annotation_id={self.annotation_id} "
            f"evidence_id={self.evidence_id} case_id={self.case_id}>"
        )


__all__ = [
    "EvidenceAnnotationRecord",
    "EVIDENCE_ANNOTATION_TABLE",
    "ANNOTATION_ID_MAX_LENGTH",
    "CASE_ID_MAX_LENGTH",
    "ANNOTATION_VERSION_MAX_LENGTH",
    "ANNOTATOR_ID_MAX_LENGTH",
    "ANNOTATION_REVIEW_STATUS_MAX_LENGTH",
    "ANNOTATION_UNIQUE_CONSTRAINT",
    "ANNOTATION_EVIDENCE_INDEX",
    "ANNOTATION_REVIEW_DRAFT",
    "ANNOTATION_REVIEW_REVIEWED",
    "ANNOTATION_REVIEW_VALUES",
    "ANNOTATION_REVIEW_TRANSITIONS",
]
