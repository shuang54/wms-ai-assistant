"""Evidence / Annotation 真实 PostgreSQL 持久化测试（Phase 4.1 Step 37）。

真实链路（无 Fake ORM / 无 Fake Repository）：

    EvidenceRepository
            ↓
    ai_ops.evidence_record / ai_ops.evidence_annotation_record
            ↓
    PostgreSQL（真实）

隔离与残留（与 ``test_conversation_api_db_e2e.py`` 同策略）：

* ``RUN_DB_TESTS=1`` 才运行；
* module 级残留校验：两张表计数前后一致；
* 每个用例只删除**本测试创建**的 evidence_id（绝无 TRUNCATE / 全表 DELETE）；
* 建表沿用项目现状：``init_db()`` → ``Base.metadata.create_all()``（**不**引入 Alembic）。

禁止：DeepSeek / SiliconFlow / 真实 LLM / network。
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text

from backend.app.db.evidence_repository import (
    EvidenceNotFoundError,
    EvidenceRepository,
    EvidenceRepositoryError,
    InvalidEvidenceStatusTransitionError,
)
from backend.app.db.init_db import init_db
from backend.app.db.models.evidence_annotation_record import (
    ANNOTATION_REVIEW_DRAFT,
    EvidenceAnnotationRecord,
)
from backend.app.db.models.evidence_record import (
    EVIDENCE_STATUS_ANNOTATED,
    EVIDENCE_STATUS_FINALIZED,
    EVIDENCE_STATUS_IMPORTED,
    EVIDENCE_STATUS_PERSISTED,
    SOURCE_TYPE_REAL,
    EvidenceRecord,
)
from backend.app.db.session import get_session_factory

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_TESTS") != "1",
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)

_SCHEMA = "ai_ops"
_EVIDENCE_TABLE = "evidence_record"
_ANNOTATION_TABLE = "evidence_annotation_record"

_FORBIDDEN_COLUMNS = (
    "api_key",
    "password",
    "authorization",
    "database_url",
    "connection_string",
    "llm_secret",
    "token",
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


def _cleanup(evidence_id: str) -> None:
    """只删除本测试创建的记录（先删 Annotation，再删 Evidence）。"""
    factory = get_session_factory()
    assert factory is not None
    with factory() as session, session.begin():
        session.execute(
            text(
                f"DELETE FROM {_SCHEMA}.{_ANNOTATION_TABLE} "
                "WHERE evidence_id = :evidence_id"
            ),
            {"evidence_id": evidence_id},
        )
        session.execute(
            text(
                f"DELETE FROM {_SCHEMA}.{_EVIDENCE_TABLE} "
                "WHERE evidence_id = :evidence_id"
            ),
            {"evidence_id": evidence_id},
        )


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module", autouse=True)
def _ensure_tables() -> Iterator[None]:
    """建表（幂等）+ module 级残留校验。"""
    init_db()
    before = (_count(_EVIDENCE_TABLE), _count(_ANNOTATION_TABLE))
    yield
    after = (_count(_EVIDENCE_TABLE), _count(_ANNOTATION_TABLE))
    assert before == after, f"DB residue detected: {before} -> {after}"


@pytest.fixture()
def repository() -> EvidenceRepository:
    return EvidenceRepository()


# ============================================================
# Test 1 / 2：Evidence Create + Read
# ============================================================


def test_01_evidence_create_then_read(repository: EvidenceRepository) -> None:
    evidence_id = _unique("ev")
    dataset_version = _unique("ds")
    try:
        created = repository.create_evidence(
            evidence_id=evidence_id,
            dataset_version=dataset_version,
            source_type=SOURCE_TYPE_REAL,
            de_identification_attested=True,
            de_identification_method="external_process",
        )
        assert created.evidence_id == evidence_id
        assert created.status == EVIDENCE_STATUS_IMPORTED

        read_back = repository.get_evidence(evidence_id)
        assert read_back is not None
        assert read_back.evidence_id == created.evidence_id
        assert read_back.dataset_version == dataset_version
        assert read_back.source_type == SOURCE_TYPE_REAL
        assert read_back.de_identification_attested is True
        assert read_back.created_at is not None
    finally:
        _cleanup(evidence_id)


# ============================================================
# Test 3：Status Transition（禁止跳级）
# ============================================================


def test_02_status_transition_and_illegal_skip(repository: EvidenceRepository) -> None:
    evidence_id = _unique("ev")
    try:
        repository.create_evidence(
            evidence_id=evidence_id,
            dataset_version=_unique("ds"),
            source_type=SOURCE_TYPE_REAL,
            de_identification_attested=True,
        )
        persisted = repository.update_status(evidence_id, EVIDENCE_STATUS_PERSISTED)
        assert persisted.status == EVIDENCE_STATUS_PERSISTED

        # 非法跳级：PERSISTED → FINALIZED
        with pytest.raises(InvalidEvidenceStatusTransitionError):
            repository.update_status(evidence_id, EVIDENCE_STATUS_FINALIZED)

        # 状态未被错误推进
        assert repository.get_evidence(evidence_id) is not None
        assert repository.get_evidence(evidence_id).status == (
            EVIDENCE_STATUS_PERSISTED
        )
    finally:
        _cleanup(evidence_id)


# ============================================================
# Test 4 / 5：Annotation Create + Read（稳定关联）
# ============================================================


def test_03_annotation_create_and_read(repository: EvidenceRepository) -> None:
    evidence_id = _unique("ev")
    dataset_version = _unique("ds")
    annotation_id = _unique("an")
    try:
        repository.create_evidence(
            evidence_id=evidence_id,
            dataset_version=dataset_version,
            source_type=SOURCE_TYPE_REAL,
            de_identification_attested=True,
        )
        repository.update_status(evidence_id, EVIDENCE_STATUS_PERSISTED)

        annotation = repository.create_annotation(
            annotation_id=annotation_id,
            evidence_id=evidence_id,
            case_id="case_001",
            annotation_version="annotation-v1",
            annotator_id="annotator-a",
        )
        assert annotation.evidence_id == evidence_id
        assert annotation.case_id == "case_001"
        assert annotation.review_status == ANNOTATION_REVIEW_DRAFT

        rows = repository.list_annotations(evidence_id)
        assert [row.annotation_id for row in rows] == [annotation_id]
        # Evidence 状态被正确推进
        assert repository.get_evidence(evidence_id).status == EVIDENCE_STATUS_ANNOTATED
    finally:
        _cleanup(evidence_id)


# ============================================================
# Test 6：Cross Evidence Isolation
# ============================================================


def test_04_cross_evidence_isolation(repository: EvidenceRepository) -> None:
    first = _unique("ev")
    second = _unique("ev")
    try:
        repository.create_evidence(
            evidence_id=first,
            dataset_version=_unique("ds"),
            source_type=SOURCE_TYPE_REAL,
            de_identification_attested=True,
        )
        repository.create_evidence(
            evidence_id=second,
            dataset_version=_unique("ds"),
            source_type=SOURCE_TYPE_REAL,
            de_identification_attested=True,
        )
        for target in (first, second):
            repository.update_status(target, EVIDENCE_STATUS_PERSISTED)
        repository.create_annotation(
            annotation_id=_unique("an"),
            evidence_id=first,
            case_id="case_001",
            annotation_version="annotation-v1",
            annotator_id="annotator-a",
        )
        repository.create_annotation(
            annotation_id=_unique("an"),
            evidence_id=second,
            case_id="case_002",
            annotation_version="annotation-v1",
            annotator_id="annotator-b",
        )
        assert [row.case_id for row in repository.list_annotations(first)] == [
            "case_001"
        ]
        assert [row.case_id for row in repository.list_annotations(second)] == [
            "case_002"
        ]
    finally:
        _cleanup(first)
        _cleanup(second)


# ============================================================
# Test 7：Idempotency（数据库层阻止重复 Evidence）
# ============================================================


def test_05_idempotency_no_duplicate_evidence(repository: EvidenceRepository) -> None:
    evidence_id = _unique("ev")
    dataset_version = _unique("ds")
    before = _count(_EVIDENCE_TABLE)
    try:
        first = repository.create_evidence(
            evidence_id=evidence_id,
            dataset_version=dataset_version,
            source_type=SOURCE_TYPE_REAL,
            de_identification_attested=True,
        )
        second = repository.create_evidence(
            evidence_id=_unique("ev"),  # 不同 evidence_id
            dataset_version=dataset_version,  # 同一幂等键
            source_type=SOURCE_TYPE_REAL,
            de_identification_attested=True,
        )
        assert second.evidence_id == first.evidence_id  # 返回既有记录
        assert _count(_EVIDENCE_TABLE) == before + 1  # 未产生第二条
        found = repository.find_by_dataset(dataset_version, SOURCE_TYPE_REAL)
        assert found is not None
        assert found.evidence_id == evidence_id
    finally:
        _cleanup(evidence_id)


# ============================================================
# Test 8：Transaction Rollback（无孤儿 Annotation）
# ============================================================


def test_06_transaction_rollback_no_orphan(repository: EvidenceRepository) -> None:
    evidence_id = _unique("ev")
    annotation_before = _count(_ANNOTATION_TABLE)
    try:
        repository.create_evidence(
            evidence_id=evidence_id,
            dataset_version=_unique("ds"),
            source_type=SOURCE_TYPE_REAL,
            de_identification_attested=True,
        )
        repository.update_status(evidence_id, EVIDENCE_STATUS_PERSISTED)

        # 第一次写入成功
        repository.create_annotation(
            annotation_id=_unique("an"),
            evidence_id=evidence_id,
            case_id="case_001",
            annotation_version="annotation-v1",
            annotator_id="annotator-a",
        )
        # 同一 (evidence_id, case_id, annotation_version) → 唯一约束冲突 → 回滚
        with pytest.raises(EvidenceRepositoryError):
            repository.create_annotation(
                annotation_id=_unique("an"),
                evidence_id=evidence_id,
                case_id="case_001",
                annotation_version="annotation-v1",
                annotator_id="annotator-b",
            )
        assert _count(_ANNOTATION_TABLE) == annotation_before + 1
        assert len(repository.list_annotations(evidence_id)) == 1

        # Evidence 不存在的 Annotation → 拒绝（无孤儿）
        with pytest.raises(EvidenceNotFoundError):
            repository.create_annotation(
                annotation_id=_unique("an"),
                evidence_id="ev-does-not-exist",
                case_id="case_001",
                annotation_version="annotation-v1",
                annotator_id="annotator-a",
            )
        assert _count(_ANNOTATION_TABLE) == annotation_before + 1
    finally:
        _cleanup(evidence_id)


# ============================================================
# Test 9：Security（无敏感列）
# ============================================================


def test_07_no_secret_columns() -> None:
    columns = {column.name for column in EvidenceRecord.__table__.columns} | {
        column.name for column in EvidenceAnnotationRecord.__table__.columns
    }
    for forbidden in _FORBIDDEN_COLUMNS:
        assert forbidden not in columns, forbidden
    assert "content" not in columns
    assert "source_ref" not in columns and "content_ref" not in columns
    assert "locator" not in columns and "file_path" not in columns
