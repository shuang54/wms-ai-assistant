"""Evidence Provenance 真实 PostgreSQL 持久化测试（Phase 4.1 Step 38）。

审计结论（决定实现方式的依据）：

* Step 27 冻结 ``EvidenceProvenance`` **只有** ``dataset_version`` + ``source_type``；
* Step 37 的 ``ai_ops.evidence_record`` **已经**持久化这两个字段（NOT NULL）；
* 因此 Step 38 采用**优先方案**：复用 ``evidence_record`` 现有列作为 Provenance，
  **不新建** provenance 表、不建立第二套 Provenance 模型、不引入
  source_ref / content_ref / locator / file_path / raw_content / url。

本文件验证：

```text
Evidence + Provenance → PostgreSQL → Read Model（get_by_id）
```

隔离与残留（与 ``test_evidence_persistence_db.py`` 同策略）：

* ``RUN_DB_TESTS=1`` 才运行；
* module 级残留校验：``ai_ops.evidence_record`` 计数前后一致；
* 每个用例只删除**本测试创建**的 evidence_id（无 TRUNCATE / 全表 DELETE）。

禁止：DeepSeek / SiliconFlow / 真实 LLM / network。
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text

from backend.app.db.evidence_repository import (
    EvidenceRepository,
    EvidenceRepositoryError,
)
from backend.app.db.init_db import init_db
from backend.app.db.models.evidence_record import (
    SOURCE_TYPE_REAL,
    SOURCE_TYPE_SYNTHETIC,
    EvidenceRecord,
)
from backend.app.db.session import get_session_factory

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_TESTS") != "1",
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)

_SCHEMA = "ai_ops"
_EVIDENCE_TABLE = "evidence_record"

_FORBIDDEN_COLUMNS = (
    "api_key",
    "password",
    "authorization",
    "database_url",
    "connection_string",
    "llm_secret",
    "token",
    "raw_content",
    "source_ref",
    "content_ref",
    "locator",
    "file_path",
    "url",
    "document_path",
)


def _count() -> int:
    factory = get_session_factory()
    assert factory is not None
    with factory() as session:
        return int(
            session.execute(
                text(f"SELECT count(*) FROM {_SCHEMA}.{_EVIDENCE_TABLE}")
            ).scalar_one()
        )


def _cleanup(evidence_id: str) -> None:
    factory = get_session_factory()
    assert factory is not None
    with factory() as session, session.begin():
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
    init_db()
    before = _count()
    yield
    after = _count()
    assert before == after, f"DB residue detected: {before} -> {after}"


@pytest.fixture()
def repository() -> EvidenceRepository:
    return EvidenceRepository()


# ============================================================
# Test 1 / 2：Create + Read Back
# ============================================================


def test_01_create_evidence_with_provenance(repository: EvidenceRepository) -> None:
    evidence_id = _unique("ev")
    dataset_version = _unique("ds")
    try:
        repository.create_evidence(
            evidence_id=evidence_id,
            dataset_version=dataset_version,
            source_type=SOURCE_TYPE_REAL,
            de_identification_attested=True,
            de_identification_method="external_process",
        )
        found = repository.get_by_id(evidence_id)
        assert found is not None
        assert found.evidence_id == evidence_id
        assert found.dataset_version == dataset_version
        assert found.source_type == SOURCE_TYPE_REAL
        assert found.provenance.as_dict() == {
            "dataset_version": dataset_version,
            "source_type": SOURCE_TYPE_REAL,
        }
    finally:
        _cleanup(evidence_id)


def test_02_provenance_read_back_is_consistent(repository: EvidenceRepository) -> None:
    """input → ORM → PostgreSQL → Repository → Read Model 全链路一致。"""
    evidence_id = _unique("ev")
    dataset_version = _unique("ds")
    try:
        repository.create_evidence(
            evidence_id=evidence_id,
            dataset_version=dataset_version,
            source_type=SOURCE_TYPE_REAL,
            de_identification_attested=True,
        )
        provenance = repository.get_provenance(evidence_id)
        assert provenance is not None
        assert provenance.dataset_version == dataset_version
        assert provenance.source_type == SOURCE_TYPE_REAL
        # 禁止占位值
        for placeholder in ("unknown", "default", "fake-source", "generated-source"):
            assert provenance.dataset_version != placeholder
            assert provenance.source_type != placeholder
    finally:
        _cleanup(evidence_id)


# ============================================================
# Test 3：Cross Evidence Isolation
# ============================================================


def test_03_cross_evidence_provenance_isolation(
    repository: EvidenceRepository,
) -> None:
    first = _unique("ev")
    second = _unique("ev")
    try:
        repository.create_evidence(
            evidence_id=first,
            dataset_version="step38-v1",
            source_type=SOURCE_TYPE_REAL,
            de_identification_attested=True,
        )
        repository.create_evidence(
            evidence_id=second,
            dataset_version="step38-v2",
            source_type=SOURCE_TYPE_SYNTHETIC,
            de_identification_attested=False,
            de_identification_method="synthetic_fixture",
        )
        provenance_a = repository.get_provenance(first)
        provenance_b = repository.get_provenance(second)
        assert provenance_a is not None and provenance_b is not None
        assert provenance_a.as_dict() != provenance_b.as_dict()
        assert provenance_a.dataset_version == "step38-v1"
        assert provenance_b.dataset_version == "step38-v2"
        assert provenance_a.source_type == SOURCE_TYPE_REAL
        assert provenance_b.source_type == SOURCE_TYPE_SYNTHETIC
    finally:
        _cleanup(first)
        _cleanup(second)


# ============================================================
# Test 4：Idempotency（不产生第二条 Evidence / 第二份 Provenance）
# ============================================================


def test_04_idempotency_same_provenance(repository: EvidenceRepository) -> None:
    evidence_id = _unique("ev")
    dataset_version = _unique("ds")
    before = _count()
    try:
        first = repository.create_evidence(
            evidence_id=evidence_id,
            dataset_version=dataset_version,
            source_type=SOURCE_TYPE_REAL,
            de_identification_attested=True,
        )
        second = repository.create_evidence(
            evidence_id=_unique("ev"),
            dataset_version=dataset_version,  # 同一幂等键
            source_type=SOURCE_TYPE_REAL,
            de_identification_attested=True,
        )
        assert second.evidence_id == first.evidence_id
        assert _count() == before + 1  # 无重复行
        provenance = repository.get_provenance(evidence_id)
        assert provenance is not None
        assert provenance.dataset_version == dataset_version
    finally:
        _cleanup(evidence_id)


# ============================================================
# Test 5：Rollback（provenance 非法 → 不留下半条 Evidence）
# ============================================================


def test_05_invalid_provenance_rolls_back(repository: EvidenceRepository) -> None:
    before = _count()
    # 非法 source_type → 拒绝（不进入写入；不产生半条 Evidence）
    with pytest.raises(ValueError):
        repository.create_evidence(
            evidence_id=_unique("ev"),
            dataset_version=_unique("ds"),
            source_type="fake-source",
            de_identification_attested=True,
        )
    # 空 dataset_version → 拒绝
    with pytest.raises(ValueError):
        repository.create_evidence(
            evidence_id=_unique("ev"),
            dataset_version="   ",
            source_type=SOURCE_TYPE_REAL,
            de_identification_attested=True,
        )
    # padded dataset_version（not stable）→ 拒绝
    with pytest.raises(ValueError):
        repository.create_evidence(
            evidence_id=_unique("ev"),
            dataset_version=" padded-version ",
            source_type=SOURCE_TYPE_REAL,
            de_identification_attested=True,
        )
    assert _count() == before


# ============================================================
# Test 6：Security（provenance 元数据不得携带敏感 / 原始内容字段）
# ============================================================


def test_06_no_forbidden_provenance_columns() -> None:
    columns = {column.name for column in EvidenceRecord.__table__.columns}
    for forbidden in _FORBIDDEN_COLUMNS:
        assert forbidden not in columns, forbidden
    # Provenance 只由 dataset_version + source_type 表达
    assert "dataset_version" in columns and "source_type" in columns
