"""Phase 4.1 Step 48 — Regression / Traceability Audit（最小、只读）。

目标：确认 Step 37–47 的

```text
Frozen Contract → Implementation → Test
```

三者一致；不新增业务能力、不建 benchmark / HTML report / DB table。

审计方式（**尽量只读**）：

* 静态：ORM 元数据 + 冻结常量 + 生产文件源码扫描；
* 只读 catalog：仅 `information_schema` 查询（DB-gated，需要时才运行）；
* 复用既有测试作为证据（不重新实现测试基础设施）。

Traceability Gap 只允许分类：

```text
FROZEN · IMPLEMENTED · TESTED · DEFERRED · OUT_OF_SCOPE · PRE_EXISTING
```

禁止：Agent / MCP / Memory / Workflow / RBAC / Multi-tenancy / Chat UI / Streaming。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import text

from backend.app.db.conversation_evidence_repository import (
    ConversationEvidenceRepository,
)
from backend.app.db.models.conversation import Conversation
from backend.app.db.models.conversation_evidence import (
    CONVERSATION_EVIDENCE_PK,
    ConversationEvidenceRecord,
)
from backend.app.db.models.evidence_annotation_record import (
    ANNOTATION_REVIEW_DRAFT,
    ANNOTATION_REVIEW_REVIEWED,
    ANNOTATION_REVIEW_TRANSITIONS,
    ANNOTATION_REVIEW_VALUES,
    EvidenceAnnotationRecord,
)
from backend.app.db.models.evidence_record import (
    EVIDENCE_IDEMPOTENCY_CONSTRAINT,
    EVIDENCE_STATUS_ANNOTATED,
    EVIDENCE_STATUS_FINALIZED,
    EVIDENCE_STATUS_IMPORTED,
    EVIDENCE_STATUS_PERSISTED,
    EVIDENCE_STATUS_REVIEWED,
    EVIDENCE_STATUS_TRANSITIONS,
    EVIDENCE_STATUS_VALUES,
    EvidenceRecord,
)
from backend.app.db.session import get_session_factory

_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP_DIR = _REPO_ROOT / "backend" / "app"

#: Step 27 冻结：禁止进入 Evidence identity / provenance 的运行时 ID。
_FORBIDDEN_IDS: tuple[str, ...] = (
    "conversation_id",
    "turn_id",
    "assistant_request_id",
    "provider_request_id",
)


def _columns(model: type) -> set[str]:
    return {column.name for column in model.__table__.columns}


def _foreign_key_targets(model: type) -> set[str]:
    return {fk.target_fullname for fk in model.__table__.foreign_keys}


# ============================================================
# Step 37 / 38：Evidence + Provenance
# ============================================================


def test_37_evidence_model_matches_frozen_contract() -> None:
    columns = _columns(EvidenceRecord)
    assert columns == {
        "evidence_id",
        "dataset_version",
        "source_type",
        "de_identification_attested",
        "de_identification_method",
        "status",
        "created_at",
        "updated_at",
    }
    # Step 38：Provenance = dataset_version + source_type（无独立表）
    assert {"dataset_version", "source_type"} <= columns


def test_38_provenance_has_no_runtime_identity_drift() -> None:
    """Step 27 / 38：Evidence identity 与 provenance 不得被会话 ID 污染。"""
    for model in (EvidenceRecord, EvidenceAnnotationRecord):
        columns = _columns(model)
        for forbidden in _FORBIDDEN_IDS:
            assert forbidden not in columns, f"{model.__name__}: {forbidden}"


def test_38_evidence_repository_source_has_no_conversation_drift() -> None:
    """Evidence / Annotation 仓储源码不得出现会话 ID（关联表仓储除外）。"""
    for name in ("evidence_repository.py", "models/evidence_record.py"):
        source = (_APP_DIR / "db" / name).read_text(encoding="utf-8")
        for forbidden in _FORBIDDEN_IDS:
            assert forbidden not in source, f"{name}: {forbidden}"


# ============================================================
# Step 39：Evidence ↔ Annotation FK
# ============================================================


def test_39_annotation_fk_points_to_evidence() -> None:
    assert _foreign_key_targets(EvidenceAnnotationRecord) == {
        "ai_ops.evidence_record.evidence_id"
    }
    assert "evidence_id" in _columns(EvidenceAnnotationRecord)
    # Annotation 不得直接持有会话 identity
    for forbidden in _FORBIDDEN_IDS:
        assert forbidden not in _columns(EvidenceAnnotationRecord)


# ============================================================
# Step 40：Idempotency Key
# ============================================================


def test_40_idempotency_key_is_composite() -> None:
    """(source_type, dataset_version) 组合唯一 —— 不得退化为 dataset_version 单独唯一。"""
    unique_constraints = {
        constraint.name
        for constraint in EvidenceRecord.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert EVIDENCE_IDEMPOTENCY_CONSTRAINT in unique_constraints
    constraint = next(
        item
        for item in EvidenceRecord.__table__.constraints
        if getattr(item, "name", None) == EVIDENCE_IDEMPOTENCY_CONSTRAINT
    )
    assert {column.name for column in constraint.columns} == {
        "source_type",
        "dataset_version",
    }


# ============================================================
# Step 41 / 42 / 43：状态机
# ============================================================


def test_41_annotation_review_transition_is_one_way() -> None:
    assert ANNOTATION_REVIEW_VALUES == (ANNOTATION_REVIEW_DRAFT, ANNOTATION_REVIEW_REVIEWED)
    assert ANNOTATION_REVIEW_TRANSITIONS == {
        ANNOTATION_REVIEW_DRAFT: (ANNOTATION_REVIEW_REVIEWED,),
        ANNOTATION_REVIEW_REVIEWED: (),
    }


def test_42_evidence_state_machine_is_frozen() -> None:
    assert EVIDENCE_STATUS_VALUES == (
        EVIDENCE_STATUS_IMPORTED,
        EVIDENCE_STATUS_PERSISTED,
        EVIDENCE_STATUS_ANNOTATED,
        EVIDENCE_STATUS_REVIEWED,
        EVIDENCE_STATUS_FINALIZED,
    )
    assert EVIDENCE_STATUS_TRANSITIONS == {
        EVIDENCE_STATUS_IMPORTED: (EVIDENCE_STATUS_PERSISTED,),
        EVIDENCE_STATUS_PERSISTED: (EVIDENCE_STATUS_ANNOTATED,),
        EVIDENCE_STATUS_ANNOTATED: (EVIDENCE_STATUS_REVIEWED,),
        EVIDENCE_STATUS_REVIEWED: (EVIDENCE_STATUS_FINALIZED,),
        EVIDENCE_STATUS_FINALIZED: (),
    }


# ============================================================
# Step 44 / 45：Conversation ↔ Evidence
# ============================================================


def test_44_conversation_and_evidence_are_independent() -> None:
    assert "evidence_id" not in _columns(Conversation)
    assert "conversation_id" not in _columns(EvidenceRecord)


def test_45_association_model_matches_frozen_contract() -> None:
    columns = _columns(ConversationEvidenceRecord)
    assert columns == {"conversation_id", "evidence_id", "created_at"}
    targets = _foreign_key_targets(ConversationEvidenceRecord)
    assert targets == {
        "ai_ops.conversation.conversation_id",
        "ai_ops.evidence_record.evidence_id",
    }
    primary_key = {
        column.name for column in ConversationEvidenceRecord.__table__.primary_key
    }
    assert primary_key == {"conversation_id", "evidence_id"}
    assert CONVERSATION_EVIDENCE_PK == "pk_conversation_evidence"


def test_45_repository_methods_are_contract_backed() -> None:
    """每个 public method 都能对应 Step 45 Repository API 契约。"""
    expected = {
        "create_association",
        "get_association",
        "list_evidence_ids",
        "list_conversation_ids",
    }
    actual = {
        name
        for name in vars(ConversationEvidenceRepository)
        if not name.startswith("_") and callable(getattr(ConversationEvidenceRepository, name))
    }
    assert expected <= actual
    # 无孤儿 public 方法（除 dunder 与内部 helper）
    unexpected = actual - expected - {"_columns", "_factory"}
    unexpected = {name for name in unexpected if not name.startswith("_")}
    assert not unexpected, unexpected


# ============================================================
# Step 46 / 47：E2E 与 Security 测试产物存在
# ============================================================


def test_46_47_evidence_artifacts_exist() -> None:
    tests_dir = _REPO_ROOT / "tests"
    for name in (
        "test_conversation_evidence_e2e_db.py",
        "test_phase_4_1_security_boundary_db.py",
        "test_conversation_evidence_persistence_db.py",
        "test_evidence_annotation_review_db.py",
        "test_evidence_finalization_contract_db.py",
        "test_evidence_lifecycle_consistency_db.py",
        "test_conversation_evidence_contract.py",
    ):
        assert (tests_dir / name).exists(), name
    docs_dir = _REPO_ROOT / "docs" / "evaluation"
    for name in (
        "Phase 4.1 Step 46 — Real Conversation Evidence E2E.md",
        "Phase 4.1 Step 47 — Security Boundary Audit.md",
    ):
        assert (docs_dir / name).exists(), name


# ============================================================
# DB Schema Traceability（只读 catalog；DB-gated）
# ============================================================


@pytest.mark.skipif(
    os.environ.get("RUN_DB_TESTS") != "1",
    reason="set RUN_DB_TESTS=1 to enable read-only catalog inspection",
)
def test_db_schema_traceability() -> None:
    factory = get_session_factory()
    if factory is None:  # pragma: no cover - DB 未配置
        pytest.skip("DB 未配置")
    with factory() as session:
        tables = {
            row[0]
            for row in session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :s"
                ),
                {"s": "ai_ops"},
            )
        }
        assert {
            "evidence_record",
            "evidence_annotation_record",
            "conversation",
            "conversation_turn",
            "conversation_evidence",
        } <= tables
        columns = {
            row[0]
            for row in session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t"
                ),
                {"s": "ai_ops", "t": "conversation_evidence"},
            )
        }
    assert columns == {"conversation_id", "evidence_id", "created_at"}
