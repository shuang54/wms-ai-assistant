"""Evidence ↔ Annotation Association —— 真实 PostgreSQL E2E（Phase 4.1 Step 39）。

Audit 结论（决定实现方式的依据，**未修改 ORM**）：

* ORM FK 已存在（Step 37）：
  ``evidence_annotation_record.evidence_id`` →
  ``ForeignKey("ai_ops.evidence_record.evidence_id", ondelete="CASCADE")``；
* **PostgreSQL 真实约束已存在**（实测 pg_constraint）：
  ``evidence_annotation_record_evidence_id_fkey``
  ``FOREIGN KEY (evidence_id) REFERENCES ai_ops.evidence_record(evidence_id)
   ON DELETE CASCADE``；
* ORM ``relationship`` 不存在，且当前 Repository 已用显式
  ``WHERE evidence_id = :evidence_id`` 完成关联 → 按 Step 39 §七「情况 A」：
  **不新增 ORM relationship**（避免为对象级关联引入 cascade / 生命周期行为）；
* 因此本 Step：DB Schema Changes = 0 · ORM = 0 · **只补 Association E2E 测试**。

关联边界：

```text
Evidence 1 ── N Annotation（Annotation.evidence_id 必须指向真实 Evidence）
禁止以 case_id / dataset_version / source_type / 文本匹配作为主关联
```

隔离与残留（与既有 DB 测试同策略）：

* ``RUN_DB_TESTS=1`` 才运行；
* module 级残留校验：两张表计数前后一致；
* 每个用例只删除**本测试创建**的 evidence_id（无 TRUNCATE / 全表 DELETE）。

禁止：DeepSeek / SiliconFlow / 真实 LLM / network。
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app.db.evidence_repository import (
    EvidenceNotFoundError,
    EvidenceRepository,
)
from backend.app.db.init_db import init_db
from backend.app.db.models.evidence_annotation_record import (
    ANNOTATION_REVIEW_DRAFT,
    EvidenceAnnotationRecord,
)
from backend.app.db.models.evidence_record import (
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

#: Step 37 冻结的真实外键名（数据库层约束；非 ORM 声明）。
FK_NAME = "evidence_annotation_record_evidence_id_fkey"


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


def _create_evidence(repository: EvidenceRepository, dataset_version: str) -> str:
    evidence_id = _unique("ev")
    repository.create_evidence(
        evidence_id=evidence_id,
        dataset_version=dataset_version,
        source_type=SOURCE_TYPE_REAL,
        de_identification_attested=True,
        de_identification_method="external_process",
    )
    repository.update_status(evidence_id, EVIDENCE_STATUS_PERSISTED)
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
# Case 0：真实 PostgreSQL 外键存在（§四 Audit 问题 2）
# ============================================================


def test_00_real_foreign_key_exists_in_postgresql() -> None:
    factory = get_session_factory()
    assert factory is not None
    with factory() as session:
        rows = session.execute(
            text(
                "SELECT c.conname, cl.relname, clf.relname, "
                "pg_get_constraintdef(c.oid) "
                "FROM pg_constraint c "
                "JOIN pg_class cl ON cl.oid = c.conrelid "
                "LEFT JOIN pg_class clf ON clf.oid = c.confrelid "
                "JOIN pg_namespace n ON n.oid = cl.relnamespace "
                "WHERE n.nspname = :n AND c.contype = 'f' "
                "AND cl.relname = :t"
            ),
            {"n": _SCHEMA, "t": _ANNOTATION_TABLE},
        ).all()
    assert rows, "evidence_annotation_record 上不存在外键"
    names = {row[0] for row in rows}
    assert FK_NAME in names, names
    definition = next(row[3] for row in rows if row[0] == FK_NAME)
    assert "REFERENCES ai_ops.evidence_record(evidence_id)" in definition


# ============================================================
# Case 1：正常 Association
# ============================================================


def test_01_valid_association(repository: EvidenceRepository) -> None:
    evidence_id = _create_evidence(repository, _unique("ds"))
    try:
        annotation = repository.create_annotation(
            annotation_id=_unique("an"),
            evidence_id=evidence_id,
            case_id="case_001",
            annotation_version="annotation-v1",
            annotator_id="annotator-a",
        )
        assert annotation.evidence_id == evidence_id
        rows = repository.list_annotations(evidence_id)
        assert [row.annotation_id for row in rows] == [annotation.annotation_id]
        assert rows[0].case_id == "case_001"
    finally:
        _cleanup(evidence_id)


# ============================================================
# Case 2：一个 Evidence 多个 Annotation
# ============================================================


def test_02_one_evidence_many_annotations(repository: EvidenceRepository) -> None:
    evidence_id = _create_evidence(repository, _unique("ds"))
    try:
        created = [
            repository.create_annotation(
                annotation_id=_unique("an"),
                evidence_id=evidence_id,
                case_id=f"case_{index:03d}",
                annotation_version="annotation-v1",
                annotator_id="annotator-a",
            )
            for index in (1, 2, 3)
        ]
        rows = repository.list_annotations(evidence_id)
        assert len(rows) == 3
        assert {row.annotation_id for row in rows} == {
            item.annotation_id for item in created
        }
        # 语义未变：annotation_version / review_status / annotator 原样保留
        for row in rows:
            assert row.annotation_version == "annotation-v1"
            assert row.review_status == ANNOTATION_REVIEW_DRAFT
            assert row.annotator_id == "annotator-a"
    finally:
        _cleanup(evidence_id)


# ============================================================
# Case 3：Cross-Evidence Isolation（最重要业务边界）
# ============================================================


def test_03_cross_evidence_isolation(repository: EvidenceRepository) -> None:
    first = _create_evidence(repository, _unique("ds"))
    second = _create_evidence(repository, _unique("ds"))
    try:
        for target, prefix in ((first, "a"), (second, "b")):
            for index in (1, 2):
                repository.create_annotation(
                    annotation_id=f"{prefix}-{index}-{uuid.uuid4().hex[:8]}",
                    evidence_id=target,
                    case_id=f"case_{prefix}_{index}",
                    annotation_version="annotation-v1",
                    annotator_id=f"annotator-{prefix}",
                )
        first_rows = repository.list_annotations(first)
        second_rows = repository.list_annotations(second)
        assert len(first_rows) == 2 and len(second_rows) == 2
        assert {row.evidence_id for row in first_rows} == {first}
        assert {row.evidence_id for row in second_rows} == {second}
        assert {row.case_id for row in first_rows}.isdisjoint(
            {row.case_id for row in second_rows}
        )
    finally:
        _cleanup(first)
        _cleanup(second)


# ============================================================
# Case 4：Invalid Evidence（Repository 拒绝 + 数据库层 FK 拒绝）
# ============================================================


def test_04_invalid_evidence_rejected(repository: EvidenceRepository) -> None:
    annotation_before = _count(_ANNOTATION_TABLE)
    with pytest.raises(EvidenceNotFoundError):
        repository.create_annotation(
            annotation_id=_unique("an"),
            evidence_id="evidence-does-not-exist",
            case_id="case_001",
            annotation_version="annotation-v1",
            annotator_id="annotator-a",
        )
    assert _count(_ANNOTATION_TABLE) == annotation_before

    # 数据库层：绕过 Repository 直接 INSERT → FK 必须拒绝
    factory = get_session_factory()
    assert factory is not None
    with pytest.raises(IntegrityError):
        with factory() as session, session.begin():
            session.execute(
                text(
                    f"INSERT INTO {_SCHEMA}.{_ANNOTATION_TABLE} "
                    "(annotation_id, evidence_id, case_id, annotation_version, "
                    " annotator_id, review_status) "
                    "VALUES (:a, :e, :c, :v, :n, :s)"
                ),
                {
                    "a": _unique("an-raw"),
                    "e": "evidence-does-not-exist",
                    "c": "case_001",
                    "v": "annotation-v1",
                    "n": "annotator-a",
                    "s": ANNOTATION_REVIEW_DRAFT,
                },
            )
    assert _count(_ANNOTATION_TABLE) == annotation_before


# ============================================================
# Case 5：case_id 不是 Association Key
# ============================================================


def test_05_case_id_is_not_association_key(repository: EvidenceRepository) -> None:
    """相同 case_id 可属于不同 Evidence；查询必须按 evidence_id 区分。"""
    first = _create_evidence(repository, _unique("ds"))
    second = _create_evidence(repository, _unique("ds"))
    try:
        shared_case = "case_shared"
        repository.create_annotation(
            annotation_id=_unique("an"),
            evidence_id=first,
            case_id=shared_case,
            annotation_version="annotation-v1",
            annotator_id="annotator-a",
        )
        repository.create_annotation(
            annotation_id=_unique("an"),
            evidence_id=second,
            case_id=shared_case,
            annotation_version="annotation-v1",
            annotator_id="annotator-b",
        )
        first_rows = repository.list_annotations(first)
        second_rows = repository.list_annotations(second)
        assert len(first_rows) == 1 and len(second_rows) == 1
        assert first_rows[0].case_id == second_rows[0].case_id == shared_case
        assert first_rows[0].annotation_id != second_rows[0].annotation_id
        assert first_rows[0].evidence_id == first
        assert second_rows[0].evidence_id == second
    finally:
        _cleanup(first)
        _cleanup(second)


# ============================================================
# Case 6：Read Model 不泄漏 ORM / Session
# ============================================================


def test_06_association_read_model_has_no_orm_or_session(
    repository: EvidenceRepository,
) -> None:
    evidence_id = _create_evidence(repository, _unique("ds"))
    try:
        row = repository.create_annotation(
            annotation_id=_unique("an"),
            evidence_id=evidence_id,
            case_id="case_001",
            annotation_version="annotation-v1",
            annotator_id="annotator-a",
        )
        forbidden = ("session", "connection", "engine", "_sa", "registry", "metadata")
        for attribute in vars(row):
            lowered = attribute.lower()
            for token in forbidden:
                assert token not in lowered, f"{attribute} leaks {token}"
        assert not isinstance(row, EvidenceAnnotationRecord)
        assert not isinstance(row, EvidenceRecord)
        # frozen dataclass：不可原地修改
        try:
            row.case_id = "case_x"  # type: ignore[misc]
        except Exception:  # noqa: BLE001 - frozen dataclass raises
            pass
        else:  # pragma: no cover
            raise AssertionError("AnnotationRow must be frozen")
    finally:
        _cleanup(evidence_id)
