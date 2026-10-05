"""Evidence ORM Model（Phase 4.1 Step 37 —— 真实持久化实现）。

严格对应 Step 36 冻结的 Evidence Persistence Contract：

    ai_ops.evidence_record
    ├── evidence_id                    String(128) PK（服务端签发）
    ├── dataset_version                String(128) NOT NULL（Step 27 provenance）
    ├── source_type                    String(32) NOT NULL（REAL_DEIDENTIFIED / SYNTHETIC_ONLY）
    ├── de_identification_attested     Boolean NOT NULL（Step 31：attestation）
    ├── de_identification_method       String(64) NULL
    ├── status                         String(32) NOT NULL（状态机；string，非 bool）
    ├── created_at                     DateTime(timezone=True) server_default=now()
    └── updated_at                     DateTime(timezone=True) server_default=now() onupdate=now()

Step 36 冻结的状态机（**不得**用隐式 bool / 散落字符串 / 跳级）：

    IMPORTED → PERSISTED → ANNOTATED → REVIEWED → FINALIZED（终态）

幂等（Step 36 §7）：**数据库层**保证

    uq_evidence_source_dataset (source_type, dataset_version)

即同一 (source_type, dataset_version) 只能存在一条 Evidence —— 不依赖应用层。

沿用现有 DB 风格（不重新发明）：

* Base = ``backend.app.db.base.Base``（SQLAlchemy 2.x DeclarativeBase）；
* 列 = ``Mapped[...]`` + ``mapped_column(...)``，每列带 ``comment``；
* 时间列 = ``DateTime(timezone=True)`` + ``server_default=func.now()``；
* 状态一律 **string**，不使用 PostgreSQL ENUM；
* schema = ``ai_ops``（与 conversation / 观测表同轴；**不得**进入业务 schema public）；
* 不新增 Step 36 明确禁止的字段：source_ref / content_ref / locator /
  file_path / raw_content / conversation_content。

建表方式（本项目当前无 Alembic，遵循现状）：

    db/models/__init__.py 的 _MODELS 注册 → init_db() → Base.metadata.create_all()
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import Boolean, DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base

#: Evidence / Annotation 所在 schema（与 conversation / 观测表同轴；非 public）。
EVIDENCE_SCHEMA: Final[str] = "ai_ops"

#: 表名（``ai_ops.evidence_record``）。
EVIDENCE_TABLE: Final[str] = "evidence_record"

EVIDENCE_ID_MAX_LENGTH: Final[int] = 128
DATASET_VERSION_MAX_LENGTH: Final[int] = 128
SOURCE_TYPE_MAX_LENGTH: Final[int] = 32
ATTESTATION_METHOD_MAX_LENGTH: Final[int] = 64
EVIDENCE_STATUS_MAX_LENGTH: Final[int] = 32

#: 幂等唯一约束名（source_type + dataset_version）。
EVIDENCE_IDEMPOTENCY_CONSTRAINT: Final[str] = "uq_evidence_source_dataset"

#: 状态索引名。
EVIDENCE_STATUS_INDEX: Final[str] = "ix_evidence_record_status"

# ---- 状态机（Step 36 §3 / §5 冻结）----
EVIDENCE_STATUS_IMPORTED: Final[str] = "IMPORTED"
EVIDENCE_STATUS_PERSISTED: Final[str] = "PERSISTED"
EVIDENCE_STATUS_ANNOTATED: Final[str] = "ANNOTATED"
EVIDENCE_STATUS_REVIEWED: Final[str] = "REVIEWED"
EVIDENCE_STATUS_FINALIZED: Final[str] = "FINALIZED"

EVIDENCE_STATUS_VALUES: Final[tuple[str, ...]] = (
    EVIDENCE_STATUS_IMPORTED,
    EVIDENCE_STATUS_PERSISTED,
    EVIDENCE_STATUS_ANNOTATED,
    EVIDENCE_STATUS_REVIEWED,
    EVIDENCE_STATUS_FINALIZED,
)

#: 合法迁移（不得跳级；FINALIZED 为终态）。
EVIDENCE_STATUS_TRANSITIONS: Final[dict[str, tuple[str, ...]]] = {
    EVIDENCE_STATUS_IMPORTED: (EVIDENCE_STATUS_PERSISTED,),
    EVIDENCE_STATUS_PERSISTED: (EVIDENCE_STATUS_ANNOTATED,),
    EVIDENCE_STATUS_ANNOTATED: (EVIDENCE_STATUS_REVIEWED,),
    EVIDENCE_STATUS_REVIEWED: (EVIDENCE_STATUS_FINALIZED,),
    EVIDENCE_STATUS_FINALIZED: (),
}

#: source_type（Step 26 / Step 31 同口径）。
SOURCE_TYPE_REAL: Final[str] = "REAL_DEIDENTIFIED"
SOURCE_TYPE_SYNTHETIC: Final[str] = "SYNTHETIC_ONLY"
SOURCE_TYPE_VALUES: Final[tuple[str, ...]] = (SOURCE_TYPE_REAL, SOURCE_TYPE_SYNTHETIC)


class EvidenceRecord(Base):
    """Evidence 行（一次 Real Evidence Import 的产物身份；**不含**会话正文）。"""

    __tablename__ = EVIDENCE_TABLE

    evidence_id: Mapped[str] = mapped_column(
        String(EVIDENCE_ID_MAX_LENGTH),
        primary_key=True,
        nullable=False,
        comment="Evidence ID（服务端签发；唯一 / 不可变）",
    )

    dataset_version: Mapped[str] = mapped_column(
        String(DATASET_VERSION_MAX_LENGTH),
        nullable=False,
        comment="数据集版本（Step 27 provenance；非空 / 稳定 / 显式）",
    )

    source_type: Mapped[str] = mapped_column(
        String(SOURCE_TYPE_MAX_LENGTH),
        nullable=False,
        comment="来源类型：REAL_DEIDENTIFIED / SYNTHETIC_ONLY",
    )

    de_identification_attested: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="是否已取得脱敏证明（Step 31：false → 禁止 import）",
    )

    de_identification_method: Mapped[str | None] = mapped_column(
        String(ATTESTATION_METHOD_MAX_LENGTH),
        nullable=True,
        comment="脱敏方式（可空；不保存任何凭据 / 路径 / 连接串）",
    )

    status: Mapped[str] = mapped_column(
        String(EVIDENCE_STATUS_MAX_LENGTH),
        nullable=False,
        default=EVIDENCE_STATUS_IMPORTED,
        server_default=EVIDENCE_STATUS_IMPORTED,
        comment="状态：IMPORTED / PERSISTED / ANNOTATED / REVIEWED / FINALIZED",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Evidence 写入时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="最近一次状态变更时间",
    )

    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "dataset_version",
            name=EVIDENCE_IDEMPOTENCY_CONSTRAINT,
        ),
        Index(EVIDENCE_STATUS_INDEX, "status"),
        {"schema": EVIDENCE_SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<EvidenceRecord evidence_id={self.evidence_id} "
            f"dataset_version={self.dataset_version} status={self.status}>"
        )


__all__ = [
    "EvidenceRecord",
    "EVIDENCE_SCHEMA",
    "EVIDENCE_TABLE",
    "EVIDENCE_ID_MAX_LENGTH",
    "DATASET_VERSION_MAX_LENGTH",
    "SOURCE_TYPE_MAX_LENGTH",
    "ATTESTATION_METHOD_MAX_LENGTH",
    "EVIDENCE_STATUS_MAX_LENGTH",
    "EVIDENCE_IDEMPOTENCY_CONSTRAINT",
    "EVIDENCE_STATUS_INDEX",
    "EVIDENCE_STATUS_IMPORTED",
    "EVIDENCE_STATUS_PERSISTED",
    "EVIDENCE_STATUS_ANNOTATED",
    "EVIDENCE_STATUS_REVIEWED",
    "EVIDENCE_STATUS_FINALIZED",
    "EVIDENCE_STATUS_VALUES",
    "EVIDENCE_STATUS_TRANSITIONS",
    "SOURCE_TYPE_REAL",
    "SOURCE_TYPE_SYNTHETIC",
    "SOURCE_TYPE_VALUES",
]
