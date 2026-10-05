"""Evidence Finalization Contract —— 真实 PostgreSQL 行为审计（Phase 4.1 Step 42）。

**本文件不实现任何 Finalization 能力**，只以测试形式**记录并锁定当前真实行为**，
为 Finalization Contract 提供 execution evidence。

已知事实（Step 36/37，不重复实现）：

```text
EVIDENCE_STATUS_TRANSITIONS[REVIEWED]  = (FINALIZED,)
EVIDENCE_STATUS_TRANSITIONS[FINALIZED] = ()            ← terminal
EvidenceRepository.update_status() 已执行迁移校验
```

Step 42 需要证据的部分（本文件提供）：

* ``REVIEWED → FINALIZED`` 当前**是否要求** Annotation 全部 REVIEWED（Q1/Q2）；
* ``FINALIZED`` 之后 Annotation / Provenance 是否真的被阻止修改（Q3/Q6）；
* ``FINALIZED → 任何状态`` 是否被拒绝（Q5）。

重要：Case 4 / Case 5 断言的是 **observed behavior（当前实现事实）**，
**不是**契约主张 —— Finalization 后置不可变性在契约层仍为 **UNDEFINED**
（见 ``docs/decisions/Phase/Phase 4.1 Lifecycle Contract.md``）。
若将来引入 enforcement，本文件需随契约更新。

约束：DB Schema Changes = 0 · 不新增 Service / API / Workflow · 不改 Repository。
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text

from backend.app.db.evidence_repository import (
    EvidenceRepository,
    InvalidEvidenceStatusTransitionError,
)
from backend.app.db.init_db import init_db
from backend.app.db.models.evidence_annotation_record import (
    ANNOTATION_REVIEW_DRAFT,
    ANNOTATION_REVIEW_REVIEWED,
)
from backend.app.db.models.evidence_record import (
    EVIDENCE_STATUS_ANNOTATED,
    EVIDENCE_STATUS_FINALIZED,
    EVIDENCE_STATUS_IMPORTED,
    EVIDENCE_STATUS_PERSISTED,
    EVIDENCE_STATUS_REVIEWED,
    EVIDENCE_STATUS_TRANSITIONS,
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


def _create_reviewed(repository: EvidenceRepository) -> str:
    """创建走到 REVIEWED 的 Evidence（经 PERSISTED → ANNOTATED → REVIEWED）。"""
    evidence_id = _unique("ev")
    repository.create_evidence(
        evidence_id=evidence_id,
        dataset_version=_unique("ds"),
        source_type=SOURCE_TYPE_SYNTHETIC,
        de_identification_attested=True,
    )
    repository.update_status(evidence_id, EVIDENCE_STATUS_PERSISTED)
    repository.create_annotation(
        annotation_id=_unique("an"),
        evidence_id=evidence_id,
        case_id="case_001",
        annotation_version="annotation-v1",
        annotator_id="annotator-a",
    )  # 推进 PERSISTED → ANNOTATED
    repository.update_status(evidence_id, EVIDENCE_STATUS_REVIEWED)
    return evidence_id


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
# Q1 / Q2：REVIEWED → FINALIZED 是否要求 Annotation 全部 REVIEWED
# ============================================================


def test_01_finalize_without_annotation_review_succeeds(
    repository: EvidenceRepository,
) -> None:
    """observed behavior：即使 Annotation 仍是 DRAFT，也能 REVIEWED → FINALIZED。

    → 证明当前 **不存在**「所有 Annotation 必须 REVIEWED」的 precondition。
    """
    evidence_id = _create_reviewed(repository)
    try:
        annotations = repository.list_annotations(evidence_id)
        assert annotations, "预置 Annotation 缺失（审计失效）"
        assert all(row.review_status == ANNOTATION_REVIEW_DRAFT for row in annotations)

        finalized = repository.update_status(evidence_id, EVIDENCE_STATUS_FINALIZED)
        assert finalized.status == EVIDENCE_STATUS_FINALIZED
    finally:
        _cleanup(evidence_id)


def test_02_finalize_with_all_annotations_reviewed_succeeds(
    repository: EvidenceRepository,
) -> None:
    """Annotation 全部 REVIEWED 后同样可以 FINALIZED（两条路径都未被阻止）。"""
    evidence_id = _create_reviewed(repository)
    try:
        for row in repository.list_annotations(evidence_id):
            repository.update_annotation_review_status(
                row.annotation_id, ANNOTATION_REVIEW_REVIEWED
            )
        finalized = repository.update_status(evidence_id, EVIDENCE_STATUS_FINALIZED)
        assert finalized.status == EVIDENCE_STATUS_FINALIZED
    finally:
        _cleanup(evidence_id)


# ============================================================
# Q5：FINALIZED = terminal（出边全部拒绝）
# ============================================================


@pytest.mark.parametrize(
    "target",
    (
        EVIDENCE_STATUS_IMPORTED,
        EVIDENCE_STATUS_PERSISTED,
        EVIDENCE_STATUS_ANNOTATED,
        EVIDENCE_STATUS_REVIEWED,
        EVIDENCE_STATUS_FINALIZED,
    ),
)
def test_03_finalized_has_no_outgoing_transition(
    repository: EvidenceRepository, target: str
) -> None:
    evidence_id = _create_reviewed(repository)
    try:
        repository.update_status(evidence_id, EVIDENCE_STATUS_FINALIZED)
        with pytest.raises(InvalidEvidenceStatusTransitionError):
            repository.update_status(evidence_id, target)
        stored = repository.get_evidence(evidence_id)
        assert stored is not None
        assert stored.status == EVIDENCE_STATUS_FINALIZED
    finally:
        _cleanup(evidence_id)


def test_04_finalized_transition_contract() -> None:
    """纯常量：FINALIZED 无出边；REVIEWED 的唯一出边是 FINALIZED。"""
    assert EVIDENCE_STATUS_TRANSITIONS[EVIDENCE_STATUS_FINALIZED] == ()
    assert EVIDENCE_STATUS_TRANSITIONS[EVIDENCE_STATUS_REVIEWED] == (
        EVIDENCE_STATUS_FINALIZED,
    )


# ============================================================
# Q3：FINALIZED 后 Annotation 是否被阻止（observed behavior）
# ============================================================


def test_05_annotation_write_after_finalize_is_currently_allowed(
    repository: EvidenceRepository,
) -> None:
    """observed behavior：FINALIZED 之后**仍能**新建 Annotation。

    契约层面「FINALIZED 后 Annotation 是否 immutable」仍为 **UNDEFINED**；
    本用例只锁定当前实现事实（无 enforcement），供后续 Step 决策。
    """
    evidence_id = _create_reviewed(repository)
    try:
        repository.update_status(evidence_id, EVIDENCE_STATUS_FINALIZED)
        created = repository.create_annotation(
            annotation_id=_unique("an"),
            evidence_id=evidence_id,
            case_id="case_after_finalize",
            annotation_version="annotation-v1",
            annotator_id="annotator-b",
        )
        assert created.evidence_id == evidence_id
    finally:
        _cleanup(evidence_id)


def test_06_annotation_review_after_finalize_is_currently_allowed(
    repository: EvidenceRepository,
) -> None:
    """observed behavior：FINALIZED 之后**仍能**执行 DRAFT → REVIEWED。"""
    evidence_id = _create_reviewed(repository)
    try:
        annotation_id = repository.list_annotations(evidence_id)[0].annotation_id
        repository.update_status(evidence_id, EVIDENCE_STATUS_FINALIZED)
        reviewed = repository.update_annotation_review_status(
            annotation_id, ANNOTATION_REVIEW_REVIEWED
        )
        assert reviewed.review_status == ANNOTATION_REVIEW_REVIEWED
    finally:
        _cleanup(evidence_id)


# ============================================================
# Q6：Provenance（dataset_version + source_type）是否可修改
# ============================================================


def test_07_no_provenance_mutation_api_exists(
    repository: EvidenceRepository,
) -> None:
    """审计事实：Repository **没有**任何 Provenance / dataset_version /
    source_type / de_identification 修改方法 → FINALIZED 后亦不可改。"""
    forbidden = (
        "update_provenance",
        "update_dataset_version",
        "update_source_type",
        "update_de_identification",
        "set_provenance",
    )
    for name in forbidden:
        assert not hasattr(repository, name), name


def test_08_provenance_unchanged_after_finalize(
    repository: EvidenceRepository,
) -> None:
    evidence_id = _create_reviewed(repository)
    try:
        before = repository.get_provenance(evidence_id)
        assert before is not None
        repository.update_status(evidence_id, EVIDENCE_STATUS_FINALIZED)
        after = repository.get_provenance(evidence_id)
        assert after is not None
        assert after.dataset_version == before.dataset_version
        assert after.source_type == before.source_type
    finally:
        _cleanup(evidence_id)


# ============================================================
# Q4：Trigger ownership —— 当前无任何 Service / API 触发
# ============================================================


def test_09_repository_has_no_finalize_helper(repository: EvidenceRepository) -> None:
    """审计事实：不存在 finalize() / finalize_evidence() 专用入口；
    Finalization 只能经由通用 ``update_status(..., FINALIZED)``。"""
    for name in ("finalize", "finalize_evidence", "mark_finalized"):
        assert not hasattr(repository, name), name
