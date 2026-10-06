"""Annotation Review Transition —— 真实 PostgreSQL E2E（Phase 4.1 Step 41）。

Step 41 冻结：

```text
Annotation.review_status :  DRAFT → REVIEWED
REVIEWED = 终态
禁止：REVIEWED → DRAFT（回退）· REVIEWED → REVIEWED（重复评审）· 未知状态
```

边界（Roadmap v1.1）：

* **Annotation-only mutation** —— 不联动 Evidence 状态
  （``PERSISTED → ANNOTATED`` / ``ANNOTATED → REVIEWED`` 属 **Step 43**）；
* Repository owns transaction（沿用 Step 37/40：``session.begin()``）；
* **先验证、后写入**：非法迁移 → 抛错且数据库状态不变；
* **DB Schema Changes = 0**（``review_status`` / ``updated_at`` 列已存在，
  本 Step 只增加行为，不 ALTER / 不建表 / 不建索引）。

隔离与残留（与既有 DB 测试同策略）：

* ``RUN_DB_TESTS=1`` 才运行；
* module 级残留校验：两张表计数前后一致；
* 只删除**本测试创建**的 evidence_id（无 TRUNCATE / 全表 DELETE）。

禁止：DeepSeek / SiliconFlow / 真实 LLM / network。
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text

from backend.app.db.evidence_repository import (
    AnnotationNotFoundError,
    EvidenceRepository,
    EvidenceRepositoryError,
    InvalidAnnotationReviewTransitionError,
)
from backend.app.db.init_db import init_db
from backend.app.db.models.evidence_annotation_record import (
    ANNOTATION_REVIEW_DRAFT,
    ANNOTATION_REVIEW_REVIEWED,
    ANNOTATION_REVIEW_TRANSITIONS,
)
from backend.app.db.models.evidence_record import (
    EVIDENCE_STATUS_ANNOTATED,
    EVIDENCE_STATUS_PERSISTED,
    SOURCE_TYPE_SYNTHETIC,
)
from backend.app.db.session import get_session_factory

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_TESTS") != "1",
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)

_SCHEMA = "ai_ops"
_EVIDENCE_TABLE = "evidence_record"
_ANNOTATION_TABLE = "evidence_annotation_record"

#: 未知状态（不属于 ANNOTATION_REVIEW_VALUES）。
_UNKNOWN_STATUS = "APPROVED"


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


def _create_annotation(repository: EvidenceRepository, evidence_id: str) -> str:
    annotation_id = _unique("an")
    row = repository.create_annotation(
        annotation_id=annotation_id,
        evidence_id=evidence_id,
        case_id="case_001",
        annotation_version="annotation-v1",
        annotator_id="annotator-a",
    )
    assert row.review_status == ANNOTATION_REVIEW_DRAFT
    return annotation_id


@pytest.fixture(scope="module", autouse=True)
def _ensure_tables() -> Iterator[None]:
    init_db()
    before = (_count(_EVIDENCE_TABLE), _count(_ANNOTATION_TABLE))
    yield
    after = (_count(_EVIDENCE_TABLE), _count(_ANNOTATION_TABLE))
    assert before == after, f"DB residue detected: {before} -> {after}"


@pytest.fixture()
def repository() -> EvidenceRepository:
    return EvidenceRepository()


# ============================================================
# Case 1：DRAFT → REVIEWED（合法迁移）
# ============================================================


def test_01_draft_to_reviewed(repository: EvidenceRepository) -> None:
    evidence_id = _create_evidence(repository)
    try:
        annotation_id = _create_annotation(repository, evidence_id)
        updated = repository.update_annotation_review_status(
            annotation_id, ANNOTATION_REVIEW_REVIEWED
        )
        assert updated.review_status == ANNOTATION_REVIEW_REVIEWED
        assert updated.annotation_id == annotation_id
    finally:
        _cleanup(evidence_id)


# ============================================================
# Case 2：REVIEWED → DRAFT（回退，必须拒绝且数据库不变）
# ============================================================


def test_02_reviewed_to_draft_rejected(repository: EvidenceRepository) -> None:
    evidence_id = _create_evidence(repository)
    try:
        annotation_id = _create_annotation(repository, evidence_id)
        repository.update_annotation_review_status(
            annotation_id, ANNOTATION_REVIEW_REVIEWED
        )
        with pytest.raises(InvalidAnnotationReviewTransitionError):
            repository.update_annotation_review_status(
                annotation_id, ANNOTATION_REVIEW_DRAFT
            )
        stored = repository.get_annotation(annotation_id)
        assert stored is not None
        assert stored.review_status == ANNOTATION_REVIEW_REVIEWED
    finally:
        _cleanup(evidence_id)


# ============================================================
# Case 3：REVIEWED → REVIEWED（重复评审，必须拒绝）
# ============================================================


def test_03_reviewed_to_reviewed_rejected(repository: EvidenceRepository) -> None:
    evidence_id = _create_evidence(repository)
    try:
        annotation_id = _create_annotation(repository, evidence_id)
        repository.update_annotation_review_status(
            annotation_id, ANNOTATION_REVIEW_REVIEWED
        )
        with pytest.raises(InvalidAnnotationReviewTransitionError):
            repository.update_annotation_review_status(
                annotation_id, ANNOTATION_REVIEW_REVIEWED
            )
        stored = repository.get_annotation(annotation_id)
        assert stored is not None
        assert stored.review_status == ANNOTATION_REVIEW_REVIEWED
    finally:
        _cleanup(evidence_id)


# ============================================================
# Case 4：未知状态 → REJECT
# ============================================================


def test_04_unknown_status_rejected(repository: EvidenceRepository) -> None:
    evidence_id = _create_evidence(repository)
    try:
        annotation_id = _create_annotation(repository, evidence_id)
        assert _UNKNOWN_STATUS not in ANNOTATION_REVIEW_TRANSITIONS
        with pytest.raises((ValueError, EvidenceRepositoryError)):
            repository.update_annotation_review_status(annotation_id, _UNKNOWN_STATUS)
        stored = repository.get_annotation(annotation_id)
        assert stored is not None
        assert stored.review_status == ANNOTATION_REVIEW_DRAFT
    finally:
        _cleanup(evidence_id)


# ============================================================
# Case 5：Annotation 不存在 → NotFound（不创建、DB 不变）
# ============================================================


def test_05_annotation_not_found(repository: EvidenceRepository) -> None:
    before = _count(_ANNOTATION_TABLE)
    with pytest.raises(AnnotationNotFoundError):
        repository.update_annotation_review_status(
            "annotation-does-not-exist", ANNOTATION_REVIEW_REVIEWED
        )
    assert _count(_ANNOTATION_TABLE) == before


# ============================================================
# Case 6：Evidence 归属不变
# ============================================================


def test_06_evidence_id_unchanged(repository: EvidenceRepository) -> None:
    evidence_id = _create_evidence(repository)
    try:
        annotation_id = _create_annotation(repository, evidence_id)
        updated = repository.update_annotation_review_status(
            annotation_id, ANNOTATION_REVIEW_REVIEWED
        )
        assert updated.evidence_id == evidence_id
        assert [
            row.annotation_id for row in repository.list_annotations(evidence_id)
        ] == [annotation_id]
    finally:
        _cleanup(evidence_id)


# ============================================================
# Case 7：Evidence 状态不被联动（Step 43 才处理）
# ============================================================


def test_07_evidence_status_not_promoted(repository: EvidenceRepository) -> None:
    evidence_id = _create_evidence(repository)
    try:
        annotation_id = _create_annotation(repository, evidence_id)
        evidence = repository.get_evidence(evidence_id)
        assert evidence is not None
        # create_annotation 推进到 ANNOTATED；之后 review 不得再推进到 REVIEWED
        assert evidence.status == EVIDENCE_STATUS_ANNOTATED
        repository.update_annotation_review_status(
            annotation_id, ANNOTATION_REVIEW_REVIEWED
        )
        after = repository.get_evidence(evidence_id)
        assert after is not None
        assert after.status == EVIDENCE_STATUS_ANNOTATED
    finally:
        _cleanup(evidence_id)


# ============================================================
# Case 8：字段隔离（只有 review_status / updated_at 可变）
# ============================================================


def test_08_other_fields_unchanged(repository: EvidenceRepository) -> None:
    evidence_id = _create_evidence(repository)
    try:
        annotation_id = _create_annotation(repository, evidence_id)
        before = repository.get_annotation(annotation_id)
        assert before is not None
        updated = repository.update_annotation_review_status(
            annotation_id, ANNOTATION_REVIEW_REVIEWED
        )
        assert updated.case_id == before.case_id
        assert updated.annotation_version == before.annotation_version
        assert updated.annotator_id == before.annotator_id
        assert updated.evidence_id == before.evidence_id
        assert updated.created_at == before.created_at
        assert updated.updated_at >= before.updated_at
        assert updated.review_status == ANNOTATION_REVIEW_REVIEWED
    finally:
        _cleanup(evidence_id)


# ============================================================
# Case 9：Cross-Evidence Isolation
# ============================================================


def test_09_cross_evidence_isolation(repository: EvidenceRepository) -> None:
    first = _create_evidence(repository)
    second = _create_evidence(repository)
    try:
        first_annotation = _create_annotation(repository, first)
        second_annotation = _create_annotation(repository, second)
        repository.update_annotation_review_status(
            first_annotation, ANNOTATION_REVIEW_REVIEWED
        )
        reviewed = repository.get_annotation(first_annotation)
        untouched = repository.get_annotation(second_annotation)
        assert reviewed is not None and untouched is not None
        assert reviewed.review_status == ANNOTATION_REVIEW_REVIEWED
        assert untouched.review_status == ANNOTATION_REVIEW_DRAFT
    finally:
        _cleanup(first)
        _cleanup(second)


# ============================================================
# Case 10：Persistence / Read Back（新 Repository 读回）
# ============================================================


def test_10_persisted_and_read_back(repository: EvidenceRepository) -> None:
    evidence_id = _create_evidence(repository)
    try:
        annotation_id = _create_annotation(repository, evidence_id)
        repository.update_annotation_review_status(
            annotation_id, ANNOTATION_REVIEW_REVIEWED
        )
        reloaded = EvidenceRepository().get_annotation(annotation_id)
        assert reloaded is not None
        assert reloaded.review_status == ANNOTATION_REVIEW_REVIEWED
        assert reloaded.annotation_id == annotation_id
    finally:
        _cleanup(evidence_id)


# ============================================================
# Case 11：迁移契约（纯常量校验）
# ============================================================


def test_11_transition_contract() -> None:
    assert ANNOTATION_REVIEW_TRANSITIONS[ANNOTATION_REVIEW_DRAFT] == (
        ANNOTATION_REVIEW_REVIEWED,
    )
    assert ANNOTATION_REVIEW_TRANSITIONS[ANNOTATION_REVIEW_REVIEWED] == ()
