"""Evidence × Annotation Lifecycle Consistency —— 真实 PostgreSQL 行为审计（Step 43）。

**本文件不实现任何一致性 enforcement**，只为 Lifecycle Consistency Matrix 提供
**Observed Behavior** 证据（与 Contract 判定严格分离）。

背景（Step 37 决策文档 §Transaction，已存在历史契约）：

```text
create_annotation 为原子单元：读 Evidence → INSERT annotation → 推进 Evidence 状态；
失败整体回滚 → 无孤儿 Annotation、Evidence 状态不错误推进。
```

由此产生两个**组合可达性**事实，需实测确认：

* ``PERSISTED + Annotation`` —— 是否可达？
  （create_annotation 会把 PERSISTED 推进到 ANNOTATED → 预期**不可达**）
* ``IMPORTED + Annotation`` —— 是否可达？
  （TRANSITIONS[IMPORTED] = (PERSISTED,)，不含 ANNOTATED → 不推进 → 预期**可达**）

其余组合（ANNOTATED / REVIEWED / FINALIZED × DRAFT / REVIEWED）的 Observed 证据
已由既有测试覆盖，此处**不重复**：

```text
ANNOTATED + DRAFT      tests/test_evidence_annotation_review_db.py::_create_annotation
ANNOTATED + REVIEWED   tests/test_evidence_annotation_review_db.py::test_07
REVIEWED  + DRAFT      tests/test_evidence_finalization_contract_db.py::_create_reviewed
REVIEWED  + REVIEWED   tests/test_evidence_finalization_contract_db.py::test_02
FINALIZED + DRAFT      tests/test_evidence_finalization_contract_db.py::test_01
FINALIZED + REVIEWED   tests/test_evidence_finalization_contract_db.py::test_02
```

命名约定：`observed` 前缀 = 实现事实；**不等于**业务契约（Contract 见 Lifecycle Contract）。
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text

from backend.app.db.evidence_repository import EvidenceRepository
from backend.app.db.init_db import init_db
from backend.app.db.models.evidence_annotation_record import ANNOTATION_REVIEW_DRAFT
from backend.app.db.models.evidence_record import (
    EVIDENCE_STATUS_ANNOTATED,
    EVIDENCE_STATUS_IMPORTED,
    EVIDENCE_STATUS_PERSISTED,
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


def _create_evidence(repository: EvidenceRepository) -> str:
    evidence_id = _unique("ev")
    repository.create_evidence(
        evidence_id=evidence_id,
        dataset_version=_unique("ds"),
        source_type=SOURCE_TYPE_SYNTHETIC,
        de_identification_attested=True,
    )
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


def test_01_observed_persisted_with_annotation_is_unreachable(
    repository: EvidenceRepository,
) -> None:
    """observed：Evidence=PERSISTED 时创建 Annotation → 状态立即推进为 ANNOTATED。

    → ``PERSISTED + Annotation`` **不可达**（持久状态），
      依据 Step 37 决策文档 §Transaction 的推进契约 + ``EVIDENCE_STATUS_TRANSITIONS``。
    """
    evidence_id = _create_evidence(repository)
    try:
        repository.update_status(evidence_id, EVIDENCE_STATUS_PERSISTED)
        assert repository.get_evidence(evidence_id) is not None
        assert repository.get_evidence(evidence_id).status == EVIDENCE_STATUS_PERSISTED

        annotation = repository.create_annotation(
            annotation_id=_unique("an"),
            evidence_id=evidence_id,
            case_id="case_001",
            annotation_version="annotation-v1",
            annotator_id="annotator-a",
        )
        assert annotation.review_status == ANNOTATION_REVIEW_DRAFT

        after = repository.get_evidence(evidence_id)
        assert after is not None
        assert after.status == EVIDENCE_STATUS_ANNOTATED
        assert repository.list_annotations(evidence_id), "Annotation 应已持久化"
    finally:
        _cleanup(evidence_id)


def test_02_observed_imported_with_annotation_is_reachable(
    repository: EvidenceRepository,
) -> None:
    """observed：Evidence=IMPORTED 时创建 Annotation → 状态**不**推进，保持 IMPORTED。

    → ``IMPORTED + Annotation`` **可达**；
      契约层未显式规定 IMPORTED 场景（Contract = UNDEFINED），此处只记录事实。
    """
    evidence_id = _create_evidence(repository)
    try:
        assert (
            EVIDENCE_STATUS_ANNOTATED
            not in EVIDENCE_STATUS_TRANSITIONS[EVIDENCE_STATUS_IMPORTED]
        )
        before = repository.get_evidence(evidence_id)
        assert before is not None
        assert before.status == EVIDENCE_STATUS_IMPORTED

        repository.create_annotation(
            annotation_id=_unique("an"),
            evidence_id=evidence_id,
            case_id="case_001",
            annotation_version="annotation-v1",
            annotator_id="annotator-a",
        )
        after = repository.get_evidence(evidence_id)
        assert after is not None
        assert after.status == EVIDENCE_STATUS_IMPORTED
        assert len(repository.list_annotations(evidence_id)) == 1
    finally:
        _cleanup(evidence_id)


def test_03_observed_annotation_review_does_not_promote_evidence(
    repository: EvidenceRepository,
) -> None:
    """observed：Annotation DRAFT → REVIEWED **不**自动推进 Evidence（ANNOTATED 保持）。

    跨对象联动「Annotation REVIEWED ⇒ Evidence REVIEWED」当前**不存在**；
    契约是否应当存在 = **UNDEFINED**（OD-9）。
    """
    evidence_id = _create_evidence(repository)
    try:
        repository.update_status(evidence_id, EVIDENCE_STATUS_PERSISTED)
        annotation = repository.create_annotation(
            annotation_id=_unique("an"),
            evidence_id=evidence_id,
            case_id="case_001",
            annotation_version="annotation-v1",
            annotator_id="annotator-a",
        )
        assert repository.get_evidence(evidence_id) is not None
        assert repository.get_evidence(evidence_id).status == EVIDENCE_STATUS_ANNOTATED

        repository.update_annotation_review_status(
            annotation.annotation_id, "REVIEWED"
        )
        after = repository.get_evidence(evidence_id)
        assert after is not None
        assert after.status == EVIDENCE_STATUS_ANNOTATED
        assert repository.get_annotation(annotation.annotation_id) is not None
        assert repository.get_annotation(annotation.annotation_id).review_status == (
            "REVIEWED"
        )
    finally:
        _cleanup(evidence_id)
