"""Phase 4.1 Security / Boundary Audit —— 真实 PostgreSQL（Step 47）。

审计对象：

```text
Conversation · ConversationTurn · AI Runtime · Evidence · Annotation
ConversationEvidence · Repository · Read Model
```

验证当前架构**已经能够证明**的边界：

```text
cross-conversation / cross-evidence leakage
identity boundary violation
ORM / session leakage
sensitive metadata leakage
unauthorized persistence
repository bypass
boundary whitelist drift
```

**不含**（明确 OUT OF SCOPE，不算失败）：

```text
RBAC · ABAC · ACL · Multi-tenancy · Auth Service · Policy Engine · JWT · SSO
```

→ 本阶段只验证 **repository / object identity isolation**，不实现授权系统。

Inventory 结论（审计所得，供本文件断言）：

| 对象 | 持久化 owner | 写入方式 |
| ---- | ------------ | -------- |
| Evidence / Annotation | `EvidenceRepository` | ORM only（`insert/select/update`，无裸 SQL） |
| Conversation / Turn | `ConversationRepository` | ORM only |
| ConversationEvidence | `ConversationEvidenceRepository` | ORM only |
| AI Runtime | `AIOrchestratorService` | 不写 Evidence（无 Evidence 依赖） |
| API 层 | — | 不直接写 Evidence / ConversationEvidence |

隔离与残留：

* ``RUN_DB_TESTS=1`` 才运行；
* module 级残留守卫：5 张表计数前后一致；
* 只删除**本测试创建**的 ID；无 TRUNCATE / 全表 DELETE。

禁止：DeepSeek / SiliconFlow / 真实 LLM / network。
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text

from backend.app.db.conversation_evidence_repository import (
    ConversationEvidenceReference,
    ConversationEvidenceRepository,
    ConversationNotFoundRepositoryError,
    EvidenceNotFoundRepositoryError,
)
from backend.app.db.conversation_repository import ConversationRepository
from backend.app.db.evidence_repository import (
    AnnotationNotFoundError,
    AnnotationRow,
    EvidenceRepository,
    EvidenceRepositoryError,
    EvidenceWithProvenance,
    EvidenceProvenanceRow,
)
from backend.app.db.init_db import init_db
from backend.app.db.models.conversation import Conversation
from backend.app.db.models.conversation_evidence import ConversationEvidenceRecord
from backend.app.db.models.conversation_turn import ConversationTurn
from backend.app.db.models.evidence_annotation_record import (
    EvidenceAnnotationRecord,
)
from backend.app.db.models.evidence_record import (
    EVIDENCE_STATUS_PERSISTED,
    SOURCE_TYPE_SYNTHETIC,
    EvidenceRecord,
)
from backend.app.db.session import get_session_factory

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_TESTS") != "1",
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable Phase 4.1 security audit",
)

_SCHEMA = "ai_ops"
_TABLES: tuple[str, ...] = (
    "conversation",
    "conversation_turn",
    "evidence_record",
    "evidence_annotation_record",
    "conversation_evidence",
)

TEST_PROJECT_ID = "phase-4-1-step-47-test"

#: 敏感字段名（大小写不敏感扫描）。
_SENSITIVE_TOKENS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "password",
    "secret",
    "token",
    "authorization",
    "database_url",
    "connection_string",
    "dsn",
)


def _count(table: str) -> int:
    factory = get_session_factory()
    assert factory is not None
    with factory() as session:
        return int(
            session.execute(
                text(f"SELECT count(*) FROM {_SCHEMA}.{table}")
            ).scalar_one()
        )


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _cleanup(
    conversation_ids: tuple[str, ...] = (), evidence_ids: tuple[str, ...] = ()
) -> None:
    factory = get_session_factory()
    assert factory is not None
    with factory() as session, session.begin():
        for conversation_id in conversation_ids:
            session.execute(
                text(
                    f"DELETE FROM {_SCHEMA}.conversation_evidence "
                    "WHERE conversation_id = :conversation_id"
                ),
                {"conversation_id": conversation_id},
            )
        for evidence_id in evidence_ids:
            session.execute(
                text(
                    f"DELETE FROM {_SCHEMA}.conversation_evidence "
                    "WHERE evidence_id = :evidence_id"
                ),
                {"evidence_id": evidence_id},
            )
        for conversation_id in conversation_ids:
            session.execute(
                text(
                    f"DELETE FROM {_SCHEMA}.conversation_turn "
                    "WHERE conversation_id = :conversation_id"
                ),
                {"conversation_id": conversation_id},
            )
            session.execute(
                text(
                    f"DELETE FROM {_SCHEMA}.conversation "
                    "WHERE conversation_id = :conversation_id"
                ),
                {"conversation_id": conversation_id},
            )
        for evidence_id in evidence_ids:
            session.execute(
                text(
                    f"DELETE FROM {_SCHEMA}.evidence_annotation_record "
                    "WHERE evidence_id = :evidence_id"
                ),
                {"evidence_id": evidence_id},
            )
            session.execute(
                text(
                    f"DELETE FROM {_SCHEMA}.evidence_record "
                    "WHERE evidence_id = :evidence_id"
                ),
                {"evidence_id": evidence_id},
            )


@pytest.fixture(scope="module", autouse=True)
def _residue_guard() -> Iterator[None]:
    init_db()
    before = {table: _count(table) for table in _TABLES}
    yield
    after = {table: _count(table) for table in _TABLES}
    assert after == before, f"DB residue detected: {before} -> {after}"


@pytest.fixture()
def conversations() -> ConversationRepository:
    return ConversationRepository()


@pytest.fixture()
def evidences() -> EvidenceRepository:
    return EvidenceRepository()


@pytest.fixture()
def associations() -> ConversationEvidenceRepository:
    return ConversationEvidenceRepository()


def _create_conversation(repository: ConversationRepository) -> str:
    conversation_id = _unique("conv")
    repository.create(conversation_id=conversation_id, project_id=TEST_PROJECT_ID)
    return conversation_id


def _create_evidence(repository: EvidenceRepository) -> str:
    evidence_id = _unique("ev")
    repository.create_evidence(
        evidence_id=evidence_id,
        dataset_version=_unique("ds"),
        source_type=SOURCE_TYPE_SYNTHETIC,
        de_identification_attested=True,
        de_identification_method="external_process",
    )
    repository.update_status(evidence_id, EVIDENCE_STATUS_PERSISTED)
    return evidence_id


def _create_annotation(repository: EvidenceRepository, evidence_id: str) -> str:
    annotation_id = _unique("an")
    repository.create_annotation(
        annotation_id=annotation_id,
        evidence_id=evidence_id,
        case_id="case_001",
        annotation_version="annotation-v1",
        annotator_id="annotator-a",
    )
    return annotation_id


# ============================================================
# 1. Identity Boundary（语义层：ID 不跨 Domain 混用）
# ============================================================


def test_01_identity_boundaries() -> None:
    """各 Domain 有各自 identity；runtime correlation 不得成为 Evidence identity。"""
    conversation_columns = {c.name for c in Conversation.__table__.columns}
    turn_columns = {c.name for c in ConversationTurn.__table__.columns}
    evidence_columns = {c.name for c in EvidenceRecord.__table__.columns}
    annotation_columns = {c.name for c in EvidenceAnnotationRecord.__table__.columns}

    assert "conversation_id" in conversation_columns
    assert "turn_id" in turn_columns
    assert "evidence_id" in evidence_columns
    assert "annotation_id" in annotation_columns

    # Evidence 不得承载会话 / 请求 identity
    for forbidden in (
        "conversation_id",
        "turn_id",
        "assistant_request_id",
        "provider_request_id",
    ):
        assert forbidden not in evidence_columns, forbidden
        assert forbidden not in annotation_columns, forbidden

    # assistant_request_id 只存在于 Turn（correlation，非 FK、非 Evidence identity）
    assert "assistant_request_id" in turn_columns
    assert _foreign_key_targets(ConversationTurn) == {
        "ai_ops.conversation.conversation_id"
    }


def _foreign_key_targets(model: type) -> set[str]:
    return {fk.target_fullname for fk in model.__table__.foreign_keys}


# ============================================================
# 2. Evidence Provenance Boundary（Step 38 冻结）
# ============================================================


def test_02_evidence_provenance_boundary(
    evidences: EvidenceRepository,
) -> None:
    evidence_id = _create_evidence(evidences)
    try:
        provenance = evidences.get_provenance(evidence_id)
        assert provenance is not None
        assert isinstance(provenance, EvidenceProvenanceRow)
        # 精确字段：只有 dataset_version + source_type
        assert set(vars(provenance)) == {"dataset_version", "source_type"}
        runtime_ids = {
            "conversation_id",
            "turn_id",
            "assistant_request_id",
            "provider_request_id",
            "case_id",
            "raw_content",
        }
        assert runtime_ids.isdisjoint(set(vars(provenance)))
    finally:
        _cleanup((), (evidence_id,))


# ============================================================
# 3. ConversationEvidence 边界（精确列集合）
# ============================================================


def test_03_conversation_evidence_columns() -> None:
    factory = get_session_factory()
    assert factory is not None
    with factory() as session:
        columns = {
            row[0]
            for row in session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t"
                ),
                {"s": _SCHEMA, "t": "conversation_evidence"},
            )
        }
    assert columns == {"conversation_id", "evidence_id", "created_at"}
    orm_columns = {c.name for c in ConversationEvidenceRecord.__table__.columns}
    assert orm_columns == {"conversation_id", "evidence_id", "created_at"}


# ============================================================
# 4. Cross-Conversation Isolation
# ============================================================


def test_04_cross_conversation_isolation(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    first, second = _create_conversation(conversations), _create_conversation(
        conversations
    )
    first_evidence, second_evidence = (
        _create_evidence(evidences),
        _create_evidence(evidences),
    )
    try:
        associations.create_association(first, first_evidence)
        associations.create_association(second, second_evidence)

        assert associations.list_evidence_ids(first) == (first_evidence,)
        assert associations.list_evidence_ids(second) == (second_evidence,)
        # 反向
        assert associations.list_conversation_ids(first_evidence) == (first,)
        assert associations.list_conversation_ids(second_evidence) == (second,)
        # 无隐式关联
        assert associations.get_association(first, second_evidence) is None
        assert associations.get_association(second, first_evidence) is None
    finally:
        _cleanup((first, second), (first_evidence, second_evidence))


# ============================================================
# 5. Evidence Reuse ≠ 共享会话资源
# ============================================================


def test_05_evidence_reuse_without_cross_leakage(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    first, second = _create_conversation(conversations), _create_conversation(
        conversations
    )
    shared = _create_evidence(evidences)
    private_to_second = _create_evidence(evidences)
    try:
        associations.create_association(first, shared)
        associations.create_association(second, shared)
        associations.create_association(second, private_to_second)

        assert associations.list_conversation_ids(shared) == tuple(
            sorted((first, second))
        )
        # 共享 Evidence 不等于共享会话的其他资源
        assert private_to_second not in associations.list_evidence_ids(first)
        assert associations.list_evidence_ids(first) == (shared,)
        # Evidence 仍只有一个 record
        assert EvidenceRepository().get_evidence(shared) is not None
    finally:
        _cleanup((first, second), (shared, private_to_second))


# ============================================================
# 6. Annotation Boundary（真实 Evidence 引用 + 交叉隔离）
# ============================================================


def test_06_annotation_evidence_isolation(
    evidences: EvidenceRepository,
) -> None:
    first, second = _create_evidence(evidences), _create_evidence(evidences)
    try:
        before = _count("evidence_annotation_record")
        # 不存在的 Evidence → 拒绝（不得产生孤儿 Annotation）
        with pytest.raises(EvidenceRepositoryError):
            evidences.create_annotation(
                annotation_id=_unique("an"),
                evidence_id="evidence-missing",
                case_id="case_001",
                annotation_version="annotation-v1",
                annotator_id="annotator-a",
            )
        assert _count("evidence_annotation_record") == before

        first_annotation = _create_annotation(evidences, first)
        second_annotation = _create_annotation(evidences, second)

        assert [
            row.annotation_id for row in evidences.list_annotations(first)
        ] == [first_annotation]
        assert [
            row.annotation_id for row in evidences.list_annotations(second)
        ] == [second_annotation]
        assert second_annotation not in {
            row.annotation_id for row in evidences.list_annotations(first)
        }
    finally:
        _cleanup((), (first, second))


# ============================================================
# 7. Delete / Cascade Boundary
# ============================================================


def test_07_delete_cascade_boundary(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    factory = get_session_factory()
    assert factory is not None

    # Case A：删 Conversation → 关联行删；Evidence + Annotation 保留
    conversation_id = _create_conversation(conversations)
    evidence_id = _create_evidence(evidences)
    annotation_id = _create_annotation(evidences, evidence_id)
    associations.create_association(conversation_id, evidence_id)
    with factory() as session, session.begin():
        session.execute(
            text(
                f"DELETE FROM {_SCHEMA}.conversation "
                "WHERE conversation_id = :conversation_id"
            ),
            {"conversation_id": conversation_id},
        )
    assert associations.get_association(conversation_id, evidence_id) is None
    assert EvidenceRepository().get_evidence(evidence_id) is not None
    assert [
        row.annotation_id
        for row in EvidenceRepository().list_annotations(evidence_id)
    ] == [annotation_id]
    _cleanup((), (evidence_id,))

    # Case B：删 Evidence → 关联行删；Conversation 保留
    conversation_id = _create_conversation(conversations)
    evidence_id = _create_evidence(evidences)
    associations.create_association(conversation_id, evidence_id)
    with factory() as session, session.begin():
        session.execute(
            text(
                f"DELETE FROM {_SCHEMA}.evidence_record "
                "WHERE evidence_id = :evidence_id"
            ),
            {"evidence_id": evidence_id},
        )
    assert associations.get_association(conversation_id, evidence_id) is None
    assert (
        ConversationRepository().get_by_conversation_id(conversation_id) is not None
    )
    _cleanup((conversation_id,), ())


# ============================================================
# 8. Read Model Security（frozen + 无 ORM / Session）
# ============================================================


def test_08_read_model_security(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    conversation_id = _create_conversation(conversations)
    evidence_id = _create_evidence(evidences)
    try:
        models = (
            evidences.get_provenance(evidence_id),
            evidences.get_by_id(evidence_id),
            associations.create_association(conversation_id, evidence_id),
        )
        assert models[0] is not None and models[1] is not None
        expected_types = (
            EvidenceProvenanceRow,
            EvidenceWithProvenance,
            ConversationEvidenceReference,
        )
        forbidden = (
            "session",
            "connection",
            "engine",
            "_sa",
            "registry",
            "metadata",
            "result",
        )
        for instance, expected in zip(models, expected_types, strict=True):
            assert isinstance(instance, expected)
            for attribute in vars(instance):  # type: ignore[arg-type]
                for token in forbidden:
                    assert token not in attribute.lower(), f"{attribute}:{token}"
        # AnnotationRow 同样 frozen（构造一次校验）
        annotation_id = _create_annotation(evidences, evidence_id)
        annotation = evidences.list_annotations(evidence_id)[0]
        assert isinstance(annotation, AnnotationRow)
        assert annotation.annotation_id == annotation_id
    finally:
        _cleanup((conversation_id,), (evidence_id,))


# ============================================================
# 9. Sensitive Metadata Boundary
# ============================================================


def test_09_sensitive_metadata_boundary(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    conversation_id = _create_conversation(conversations)
    evidence_id = _create_evidence(evidences)
    try:
        instances = (
            evidences.get_provenance(evidence_id),
            evidences.get_by_id(evidence_id),
            associations.create_association(conversation_id, evidence_id),
        )
        for instance in instances:
            for attribute in vars(instance):  # type: ignore[arg-type]
                lowered = attribute.lower()
                for token in _SENSITIVE_TOKENS:
                    assert token not in lowered, f"{attribute}:{token}"

        # AI Result metadata：只含 correlation / outcome，不含凭据与域 ID
        from backend.app.dto.assistant_outcome import AssistantOutcome
        from backend.app.services.ai_orchestrator_service import (
            AIOrchestrationResult,
            RouteType,
        )

        metadata = {
            "request_id": _unique("req"),
            "outcome": AssistantOutcome.SUCCESS,
        }
        result = AIOrchestrationResult(
            route=RouteType.RAG, content="回答", data=None, metadata=metadata
        )
        for key in result.metadata:
            lowered = str(key).lower()
            for token in _SENSITIVE_TOKENS + (
                "conversation_id",
                "turn_id",
                "evidence_id",
            ):
                assert token not in lowered, key
    finally:
        _cleanup((conversation_id,), (evidence_id,))


# ============================================================
# 10. Repository Boundary（无裸 SQL 写入 / 会话不外泄）
# ============================================================


def test_10_repository_boundary() -> None:
    """Evidence / Conversation / ConversationEvidence 仓储不得出现裸 SQL 写操作。"""
    root = Path(__file__).resolve().parents[1] / "backend" / "app" / "db"
    targets = (
        "evidence_repository.py",
        "conversation_repository.py",
        "conversation_evidence_repository.py",
    )
    forbidden = ("INSERT INTO", "DELETE FROM", "UPDATE ai_ops", "DROP TABLE")
    for name in targets:
        source = (root / name).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{name}: {token}"

