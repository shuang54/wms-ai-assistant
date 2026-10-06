"""Conversation ↔ Evidence Persistence —— 真实 PostgreSQL E2E（Phase 4.1 Step 45）。

Step 45 落地 Step 44 冻结的契约：

```text
Conversation
    │ 0..N reference（NOT ownership）
    ▼
ai_ops.conversation_evidence
    │ N..1
    ▼
Evidence（Independent · Reusable）
```

OD 验证：

* **OD-11**：Conversation → 0..N Evidence；Evidence → 0..N Conversation（Reuse）；
* **OD-12**：DEFERRED TO STEP 46 —— 关联表**不含** turn_id / assistant_request_id；
* **OD-13**：`(conversation_id, evidence_id)` 唯一；重复写入 → 幂等返回既有行。

边界（Step 44 继承）：

* 不在 `evidence_record` 加 conversation_id；不在 `conversation` / `conversation_turn`
  加 evidence_id（本测试断言父表结构未被污染）；
* ON DELETE CASCADE 只删关联行 —— 删除 Conversation **不**删 Evidence，反之亦然；
* 状态互不级联：Conversation ACTIVE→ARCHIVED 不改 Evidence.status；
  Evidence REVIEWED→FINALIZED 不改 Conversation.status。

隔离与残留：

* ``RUN_DB_TESTS=1`` 才运行；
* module 级残留校验（conversation / evidence / conversation_evidence 三表计数前后一致）；
* 只删除**本测试创建**的 conversation_id / evidence_id；无 TRUNCATE / 全表 DELETE。

禁止：DeepSeek / 真实 LLM / network。
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app.db.conversation_evidence_repository import (
    ConversationEvidenceRepository,
    ConversationEvidenceRepositoryError,
    ConversationNotFoundRepositoryError,
    EvidenceNotFoundRepositoryError,
)
from backend.app.db.conversation_repository import ConversationRepository
from backend.app.db.evidence_repository import EvidenceRepository
from backend.app.db.init_db import init_db
from backend.app.db.models.conversation import CONVERSATION_STATUS_ARCHIVED
from backend.app.db.models.conversation_evidence import (
    CONVERSATION_EVIDENCE_EVIDENCE_INDEX,
    CONVERSATION_EVIDENCE_PK,
    CONVERSATION_EVIDENCE_SCHEMA,
    CONVERSATION_EVIDENCE_TABLE,
)
from backend.app.db.models.evidence_record import (
    EVIDENCE_STATUS_ANNOTATED,
    EVIDENCE_STATUS_FINALIZED,
    EVIDENCE_STATUS_PERSISTED,
    EVIDENCE_STATUS_REVIEWED,
    SOURCE_TYPE_SYNTHETIC,
)
from backend.app.db.session import get_session_factory

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_TESTS") != "1",
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)

_SCHEMA = CONVERSATION_EVIDENCE_SCHEMA
_TABLE = CONVERSATION_EVIDENCE_TABLE
_CONVERSATION_TABLE = "conversation"
_EVIDENCE_TABLE = "evidence_record"


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


def _create_conversation(
    repository: ConversationRepository, project_id: str = "project-step45"
) -> str:
    conversation_id = _unique("conv")
    repository.create(conversation_id=conversation_id, project_id=project_id)
    return conversation_id


def _create_evidence(repository: EvidenceRepository) -> str:
    evidence_id = _unique("ev")
    repository.create_evidence(
        evidence_id=evidence_id,
        dataset_version=_unique("ds"),
        source_type=SOURCE_TYPE_SYNTHETIC,
        de_identification_attested=True,
    )
    repository.update_status(evidence_id, EVIDENCE_STATUS_PERSISTED)
    return evidence_id


def _cleanup_associations(
    conversation_ids: tuple[str, ...], evidence_ids: tuple[str, ...]
) -> None:
    """精确删除本测试创建的关联与父记录（无 TRUNCATE / 全表 DELETE）。"""
    factory = get_session_factory()
    assert factory is not None
    with factory() as session, session.begin():
        for conversation_id in conversation_ids:
            session.execute(
                text(
                    f"DELETE FROM {_SCHEMA}.{_TABLE} "
                    "WHERE conversation_id = :conversation_id"
                ),
                {"conversation_id": conversation_id},
            )
        for evidence_id in evidence_ids:
            session.execute(
                text(
                    f"DELETE FROM {_SCHEMA}.{_TABLE} "
                    "WHERE evidence_id = :evidence_id"
                ),
                {"evidence_id": evidence_id},
            )
        for conversation_id in conversation_ids:
            session.execute(
                text(
                    f"DELETE FROM {_SCHEMA}.{_CONVERSATION_TABLE} "
                    "WHERE conversation_id = :conversation_id"
                ),
                {"conversation_id": conversation_id},
            )
        for evidence_id in evidence_ids:
            session.execute(
                text(
                    f"DELETE FROM {_SCHEMA}.{_EVIDENCE_TABLE} "
                    "WHERE evidence_id = :evidence_id"
                ),
                {"evidence_id": evidence_id},
            )


@pytest.fixture(scope="module", autouse=True)
def _ensure_tables() -> Iterator[None]:
    init_db()
    before = (
        _count(_TABLE),
        _count(_CONVERSATION_TABLE),
        _count(_EVIDENCE_TABLE),
    )
    yield
    after = (
        _count(_TABLE),
        _count(_CONVERSATION_TABLE),
        _count(_EVIDENCE_TABLE),
    )
    assert before == after, f"DB residue detected: {before} -> {after}"


@pytest.fixture()
def conversations() -> ConversationRepository:
    return ConversationRepository()


@pytest.fixture()
def evidences() -> EvidenceRepository:
    return EvidenceRepository()


@pytest.fixture()
def associations() -> ConversationEvidenceRepository:
    return ConversationEvidenceRepository()


# ============================================================
# 结构：表 / FK / 唯一约束（真实 PostgreSQL 元数据）
# ============================================================


def test_01_table_exists_in_ai_ops() -> None:
    factory = get_session_factory()
    assert factory is not None
    with factory() as session:
        exists = session.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :s AND table_name = :t"
            ),
            {"s": _SCHEMA, "t": _TABLE},
        ).first()
    assert exists is not None, f"{_SCHEMA}.{_TABLE} 不存在"


def test_02_foreign_keys_point_to_conversation_and_evidence() -> None:
    factory = get_session_factory()
    assert factory is not None
    with factory() as session:
        rows = session.execute(
            text(
                "SELECT c.conname, clf.relname, pg_get_constraintdef(c.oid) "
                "FROM pg_constraint c "
                "JOIN pg_class cl ON cl.oid = c.conrelid "
                "LEFT JOIN pg_class clf ON clf.oid = c.confrelid "
                "JOIN pg_namespace n ON n.oid = cl.relnamespace "
                "WHERE n.nspname = :s AND cl.relname = :t AND c.contype = 'f'"
            ),
            {"s": _SCHEMA, "t": _TABLE},
        ).all()
    targets = {row[1] for row in rows}
    assert {"conversation", "evidence_record"} <= targets, targets
    for row in rows:
        assert "ON DELETE CASCADE" in row[2].upper(), row[2]


def test_03_unique_association_constraint_exists() -> None:
    """OD-13：复合主键（同时承担唯一语义）+ evidence_id 反向索引。"""
    factory = get_session_factory()
    assert factory is not None
    with factory() as session:
        pk = session.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = :name"
            ),
            {"name": CONVERSATION_EVIDENCE_PK},
        ).first()
        index = session.execute(
            text("SELECT 1 FROM pg_indexes WHERE indexname = :name"),
            {"name": CONVERSATION_EVIDENCE_EVIDENCE_INDEX},
        ).first()
    assert pk is not None
    assert "conversation_id" in pk[1] and "evidence_id" in pk[1]
    assert "PRIMARY KEY" in pk[1].upper()
    assert index is not None


def test_04_parent_tables_not_polluted() -> None:
    """Step 44 硬约束：父表不得新增对方 ID 列。"""
    factory = get_session_factory()
    assert factory is not None
    with factory() as session:
        conversation_columns = {
            row[0]
            for row in session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t"
                ),
                {"s": _SCHEMA, "t": _CONVERSATION_TABLE},
            )
        }
        evidence_columns = {
            row[0]
            for row in session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t"
                ),
                {"s": _SCHEMA, "t": _EVIDENCE_TABLE},
            )
        }
        association_columns = {
            row[0]
            for row in session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t"
                ),
                {"s": _SCHEMA, "t": _TABLE},
            )
        }
    assert "evidence_id" not in conversation_columns
    assert "conversation_id" not in evidence_columns
    # OD-12：无 turn-level / provenance 字段
    assert association_columns == {"conversation_id", "evidence_id", "created_at"}


# ============================================================
# 创建 / 读回 / 幂等 / 非法引用
# ============================================================


def test_05_create_association_and_read_back(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    conversation_id = _create_conversation(conversations)
    evidence_id = _create_evidence(evidences)
    try:
        created = associations.create_association(conversation_id, evidence_id)
        assert created.conversation_id == conversation_id
        assert created.evidence_id == evidence_id

        # 新 Repository（新 session）读回 —— 证明真实持久化
        reloaded = ConversationEvidenceRepository().get_association(
            conversation_id, evidence_id
        )
        assert reloaded is not None
        assert reloaded.conversation_id == conversation_id
        assert reloaded.evidence_id == evidence_id
        assert reloaded.created_at == created.created_at
    finally:
        _cleanup_associations((conversation_id,), (evidence_id,))


def test_06_duplicate_association_is_idempotent(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    """OD-13：重复写入 → 仍只有一条（Repository first-write-wins + DB PK）。"""
    conversation_id = _create_conversation(conversations)
    evidence_id = _create_evidence(evidences)
    try:
        first = associations.create_association(conversation_id, evidence_id)
        second = associations.create_association(conversation_id, evidence_id)
        assert first.created_at == second.created_at
        assert associations.list_evidence_ids(conversation_id) == (evidence_id,)

        # DB 层：绕过 Repository 直接 INSERT 重复 → 必须被约束拒绝
        factory = get_session_factory()
        assert factory is not None
        with pytest.raises(IntegrityError):
            with factory() as session, session.begin():
                session.execute(
                    text(
                        f"INSERT INTO {_SCHEMA}.{_TABLE} "
                        "(conversation_id, evidence_id) VALUES (:c, :e)"
                    ),
                    {"c": conversation_id, "e": evidence_id},
                )
    finally:
        _cleanup_associations((conversation_id,), (evidence_id,))


def test_07_invalid_conversation_rejected(
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    evidence_id = _create_evidence(evidences)
    try:
        before = _count(_TABLE)
        with pytest.raises(ConversationNotFoundRepositoryError):
            associations.create_association("conversation-does-not-exist", evidence_id)
        assert _count(_TABLE) == before
    finally:
        _cleanup_associations((), (evidence_id,))


def test_08_invalid_evidence_rejected(
    conversations: ConversationRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    conversation_id = _create_conversation(conversations)
    try:
        before = _count(_TABLE)
        with pytest.raises(EvidenceNotFoundRepositoryError):
            associations.create_association(conversation_id, "evidence-does-not-exist")
        assert _count(_TABLE) == before
    finally:
        _cleanup_associations((conversation_id,), ())


# ============================================================
# OD-11：基数（0..N 双向）+ 隔离
# ============================================================


def test_09_conversation_references_multiple_evidence(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    conversation_id = _create_conversation(conversations)
    evidence_ids = tuple(_create_evidence(evidences) for _ in range(3))
    try:
        for evidence_id in evidence_ids:
            associations.create_association(conversation_id, evidence_id)
        assert associations.list_evidence_ids(conversation_id) == tuple(
            sorted(evidence_ids)
        )
    finally:
        _cleanup_associations((conversation_id,), evidence_ids)


def test_10_evidence_is_reusable_across_conversations(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    """Evidence = Reusable：同一 Evidence 可被多个 Conversation 引用。"""
    conversation_ids = tuple(_create_conversation(conversations) for _ in range(2))
    evidence_id = _create_evidence(evidences)
    try:
        for conversation_id in conversation_ids:
            associations.create_association(conversation_id, evidence_id)
        assert associations.list_conversation_ids(evidence_id) == tuple(
            sorted(conversation_ids)
        )
    finally:
        _cleanup_associations(conversation_ids, (evidence_id,))


def test_11_cross_conversation_isolation(
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
        assert associations.list_conversation_ids(first_evidence) == (first,)
        assert associations.list_conversation_ids(second_evidence) == (second,)
    finally:
        _cleanup_associations(
            (first, second), (first_evidence, second_evidence)
        )


# ============================================================
# 删除语义（ON DELETE CASCADE 只删关联行）
# ============================================================


def test_12_delete_conversation_removes_association_only(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    conversation_id = _create_conversation(conversations)
    evidence_id = _create_evidence(evidences)
    factory = get_session_factory()
    assert factory is not None
    try:
        associations.create_association(conversation_id, evidence_id)
        with factory() as session, session.begin():
            session.execute(
                text(
                    f"DELETE FROM {_SCHEMA}.{_CONVERSATION_TABLE} "
                    "WHERE conversation_id = :conversation_id"
                ),
                {"conversation_id": conversation_id},
            )
        assert associations.get_association(conversation_id, evidence_id) is None
        # Evidence 必须仍然存在（Conversation 删除 ≠ Evidence 删除）
        assert evidences.get_evidence(evidence_id) is not None
    finally:
        _cleanup_associations((conversation_id,), (evidence_id,))


def test_13_delete_evidence_removes_association_only(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    conversation_id = _create_conversation(conversations)
    evidence_id = _create_evidence(evidences)
    factory = get_session_factory()
    assert factory is not None
    try:
        associations.create_association(conversation_id, evidence_id)
        with factory() as session, session.begin():
            session.execute(
                text(
                    f"DELETE FROM {_SCHEMA}.{_EVIDENCE_TABLE} "
                    "WHERE evidence_id = :evidence_id"
                ),
                {"evidence_id": evidence_id},
            )
        assert associations.get_association(conversation_id, evidence_id) is None
        # Conversation 必须仍然存在（Evidence 删除 ≠ Conversation 删除）
        assert conversations.get_by_conversation_id(conversation_id) is not None
    finally:
        _cleanup_associations((conversation_id,), (evidence_id,))


# ============================================================
# 生命周期互不级联
# ============================================================


def test_14_no_cascade_lifecycle_coupling(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    conversation_id = _create_conversation(conversations)
    evidence_id = _create_evidence(evidences)
    try:
        associations.create_association(conversation_id, evidence_id)
        # Evidence 推进到 ANNOTATED / REVIEWED / FINALIZED
        evidences.create_annotation(
            annotation_id=_unique("an"),
            evidence_id=evidence_id,
            case_id="case_001",
            annotation_version="annotation-v1",
            annotator_id="annotator-a",
        )
        assert evidences.get_evidence(evidence_id) is not None
        assert evidences.get_evidence(evidence_id).status == EVIDENCE_STATUS_ANNOTATED
        evidences.update_status(evidence_id, EVIDENCE_STATUS_REVIEWED)
        evidences.update_status(evidence_id, EVIDENCE_STATUS_FINALIZED)
        # Conversation 不得被自动归档
        conversation = conversations.get_by_conversation_id(conversation_id)
        assert conversation is not None
        assert conversation.status != CONVERSATION_STATUS_ARCHIVED

        # 反向：Conversation 归档不得改变 Evidence 状态
        conversations.update_status(conversation_id, CONVERSATION_STATUS_ARCHIVED)
        after = evidences.get_evidence(evidence_id)
        assert after is not None
        assert after.status == EVIDENCE_STATUS_FINALIZED
    finally:
        _cleanup_associations((conversation_id,), (evidence_id,))


# ============================================================
# Read Model / 事务
# ============================================================


def test_15_read_model_is_frozen_and_leaks_nothing(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    conversation_id = _create_conversation(conversations)
    evidence_id = _create_evidence(evidences)
    try:
        reference = associations.create_association(conversation_id, evidence_id)
        forbidden = (
            "session",
            "connection",
            "engine",
            "_sa",
            "registry",
            "metadata",
            "dataset_version",
            "source_type",
            "raw_content",
        )
        for attribute in vars(reference):
            lowered = attribute.lower()
            for token in forbidden:
                assert token not in lowered, f"{attribute} 泄漏 {token}"
        try:
            reference.evidence_id = "tampered"  # type: ignore[misc]
        except Exception:  # noqa: BLE001 - frozen dataclass 应拒绝
            pass
        else:  # pragma: no cover
            raise AssertionError("ConversationEvidenceReference 必须 frozen")
    finally:
        _cleanup_associations((conversation_id,), (evidence_id,))


def test_16_failed_association_write_leaves_no_residue(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
) -> None:
    """绕过 Repository 直接写入无效引用 → FK 拒绝，且无半成品残留。"""
    conversation_id = _create_conversation(conversations)
    factory = get_session_factory()
    assert factory is not None
    try:
        before = _count(_TABLE)
        with pytest.raises(IntegrityError):
            with factory() as session, session.begin():
                session.execute(
                    text(
                        f"INSERT INTO {_SCHEMA}.{_TABLE} "
                        "(conversation_id, evidence_id) VALUES (:c, :e)"
                    ),
                    {"c": conversation_id, "e": "evidence-does-not-exist"},
                )
        assert _count(_TABLE) == before
    finally:
        _cleanup_associations((conversation_id,), ())

