"""Evidence Transaction / Idempotency Hardening —— 真实 PostgreSQL E2E（Step 40）。

Audit 结论（**未修改生产代码**）：

* **Idempotency contract 已存在**：``EvidenceRepository.create_evidence()`` 在事务内
  先按 ``(source_type, dataset_version)`` 查询既有记录，命中则原样返回
  （first-write-wins；DB 唯一约束兜底）→ 重复请求**不产生新 evidence_id**；
* **PostgreSQL 真实 UNIQUE 已存在**（Step 39 实测 pg_constraint）：
  ``uq_evidence_source_dataset UNIQUE (source_type, dataset_version)``；
* **Transaction ownership**：Repository owns（每个写方法自备
  ``with factory() as session, session.begin():``）—— 本 Step 继续保持，
  **不引入** TransactionService / UnitOfWork / TransactionManager。

因此本 Step：Production code changes = 0 · DB Schema = 0 · **只补验证测试**。

冻结契约：

```text
Idempotency Key = (source_type, dataset_version)   ← 组合键，不是 dataset_version 单独唯一
重复提交 → count = 1（返回既有 Evidence）
事务内失败 → ROLLBACK，无半成品残留
```

隔离与残留（与既有 DB 测试同策略）：

* ``RUN_DB_TESTS=1`` 才运行；
* module 级残留校验：两张表计数前后一致；
* 只删除**本测试创建**的 Evidence（按 evidence_id 精确删除，无 TRUNCATE / 全表 DELETE）。

禁止：DeepSeek / SiliconFlow / 真实 LLM / network。
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.db.evidence_repository import (
    EvidenceNotFoundError,
    EvidenceRepository,
)
from backend.app.db.init_db import init_db
from backend.app.db.models.evidence_record import (
    EVIDENCE_IDEMPOTENCY_CONSTRAINT,
    EVIDENCE_STATUS_PERSISTED,
    SOURCE_TYPE_REAL,
    SOURCE_TYPE_SYNTHETIC,
    SOURCE_TYPE_VALUES,
)
from backend.app.db.session import get_session_factory

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_TESTS") != "1",
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)

_SCHEMA = "ai_ops"
_EVIDENCE_TABLE = "evidence_record"
_ANNOTATION_TABLE = "evidence_annotation_record"

#: 并发用例的锁等待上限（避免阻塞；**不是** sleep 制造并发）。
_LOCK_TIMEOUT = "2s"


def _count(table: str = _EVIDENCE_TABLE) -> int:
    factory = get_session_factory()
    assert factory is not None
    with factory() as session:
        return int(
            session.execute(
                text(f"SELECT count(*) FROM {_SCHEMA}.{table}")
            ).scalar_one()
        )


def _evidence_count_by_key(source_type: str, dataset_version: str) -> int:
    factory = get_session_factory()
    assert factory is not None
    with factory() as session:
        return int(
            session.execute(
                text(
                    f"SELECT count(*) FROM {_SCHEMA}.{_EVIDENCE_TABLE} "
                    "WHERE source_type = :s AND dataset_version = :d"
                ),
                {"s": source_type, "d": dataset_version},
            ).scalar_one()
        )


def _cleanup(*evidence_ids: str) -> None:
    factory = get_session_factory()
    assert factory is not None
    with factory() as session, session.begin():
        for evidence_id in evidence_ids:
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
    init_db()
    before = (_count(_EVIDENCE_TABLE), _count(_ANNOTATION_TABLE))
    yield
    after = (_count(_EVIDENCE_TABLE), _count(_ANNOTATION_TABLE))
    assert before == after, f"DB residue detected: {before} -> {after}"


@pytest.fixture()
def repository() -> EvidenceRepository:
    return EvidenceRepository()


# ============================================================
# Case 0：PostgreSQL 真实 UNIQUE 约束（§十七）
# ============================================================


def test_00_idempotency_constraint_exists_in_postgresql() -> None:
    factory = get_session_factory()
    assert factory is not None
    with factory() as session:
        rows = session.execute(
            text(
                "SELECT c.conname, pg_get_constraintdef(c.oid) "
                "FROM pg_constraint c "
                "JOIN pg_class cl ON cl.oid = c.conrelid "
                "JOIN pg_namespace n ON n.oid = cl.relnamespace "
                "WHERE n.nspname = :n AND c.contype = 'u' "
                "AND cl.relname = :t"
            ),
            {"n": _SCHEMA, "t": _EVIDENCE_TABLE},
        ).all()
    assert rows, "evidence_record 上不存在 UNIQUE 约束"
    definitions = {row[0]: row[1] for row in rows}
    assert EVIDENCE_IDEMPOTENCY_CONSTRAINT in definitions, definitions
    definition = definitions[EVIDENCE_IDEMPOTENCY_CONSTRAINT]
    assert "source_type" in definition and "dataset_version" in definition
    # 组合键：不得退化成 dataset_version 单独唯一
    assert definition.count(",") >= 1


# ============================================================
# Case 1：Sequential Duplicate（§七）
# ============================================================


def test_01_sequential_duplicate_returns_existing(
    repository: EvidenceRepository,
) -> None:
    dataset_version = _unique("step40")
    source_type = SOURCE_TYPE_REAL
    first = repository.create_evidence(
        evidence_id=_unique("ev"),
        dataset_version=dataset_version,
        source_type=source_type,
        de_identification_attested=True,
        de_identification_method="external_process",
    )
    try:
        second = repository.create_evidence(
            evidence_id=_unique("ev"),  # 不同的 evidence_id → 必须被忽略
            dataset_version=dataset_version,
            source_type=source_type,
            de_identification_attested=True,
            de_identification_method="external_process",
        )
        # contract = 返回既有：同一 evidence_id，绝不产生第二条
        assert second.evidence_id == first.evidence_id
        assert _evidence_count_by_key(source_type, dataset_version) == 1
    finally:
        _cleanup(first.evidence_id)


# ============================================================
# Case 2：Different Dataset Version（§八）
# ============================================================


def test_02_different_dataset_version_allowed(
    repository: EvidenceRepository,
) -> None:
    source_type = SOURCE_TYPE_REAL
    versions = (_unique("step40-v1"), _unique("step40-v2"))
    created = [
        repository.create_evidence(
            evidence_id=_unique("ev"),
            dataset_version=version,
            source_type=source_type,
            de_identification_attested=True,
        )
        for version in versions
    ]
    try:
        assert created[0].evidence_id != created[1].evidence_id
        for version in versions:
            assert _evidence_count_by_key(source_type, version) == 1
    finally:
        _cleanup(*(item.evidence_id for item in created))


# ============================================================
# Case 3：Different Source Type（§九）
# ============================================================


def test_03_different_source_type_allowed(repository: EvidenceRepository) -> None:
    """同一 dataset_version 下不同 source_type 是两个不同 Idempotency Key。"""
    assert {SOURCE_TYPE_REAL, SOURCE_TYPE_SYNTHETIC} <= set(SOURCE_TYPE_VALUES)
    dataset_version = _unique("step40-shared")
    created = [
        repository.create_evidence(
            evidence_id=_unique("ev"),
            dataset_version=dataset_version,
            source_type=source_type,
            de_identification_attested=True,
        )
        for source_type in (SOURCE_TYPE_REAL, SOURCE_TYPE_SYNTHETIC)
    ]
    try:
        assert created[0].evidence_id != created[1].evidence_id
        for source_type in (SOURCE_TYPE_REAL, SOURCE_TYPE_SYNTHETIC):
            assert _evidence_count_by_key(source_type, dataset_version) == 1
    finally:
        _cleanup(*(item.evidence_id for item in created))


# ============================================================
# Case 4：Transaction Rollback（§十 / §十一）
# ============================================================


def test_04_transaction_rollback_leaves_no_residue(
    repository: EvidenceRepository,
) -> None:
    """事务内：Evidence INSERT 成功 → 后续 UNIQUE violation → 整体 ROLLBACK。"""
    dataset_version = _unique("step40-rb")
    source_type = SOURCE_TYPE_REAL
    committed = repository.create_evidence(
        evidence_id=_unique("ev"),
        dataset_version=dataset_version,
        source_type=source_type,
        de_identification_attested=True,
    )
    before = _count()
    factory = get_session_factory()
    assert factory is not None
    doomed_evidence_id = _unique("ev-doomed")
    try:
        with pytest.raises(SQLAlchemyError):
            with factory() as session, session.begin():
                session.execute(
                    text(
                        f"INSERT INTO {_SCHEMA}.{_EVIDENCE_TABLE} "
                        "(evidence_id, dataset_version, source_type, "
                        " de_identification_attested, status) "
                        "VALUES (:e, :d, :s, true, :st)"
                    ),
                    {
                        "e": doomed_evidence_id,
                        "d": dataset_version,  # 与已提交记录同 key → UNIQUE violation
                        "s": source_type,
                        "st": EVIDENCE_STATUS_PERSISTED,
                    },
                )
        # 失败事务中的 Evidence 不得残留
        assert _count() == before
        assert repository.get_by_id(doomed_evidence_id) is None
        assert _evidence_count_by_key(source_type, dataset_version) == 1
    finally:
        _cleanup(committed.evidence_id)


# ============================================================
# Case 5：Concurrent Duplicate（§十四 / §十五）
# ============================================================


def test_05_concurrent_duplicate_prevented(repository: EvidenceRepository) -> None:
    """两个独立 Session 竞争同一 Idempotency Key → 最终只有 1 条 Evidence。

    使用独立 Session + ``lock_timeout``（非 sleep）避免无限阻塞；
    数据库 UNIQUE 约束是最终安全边界。
    """
    dataset_version = _unique("step40-conc")
    source_type = SOURCE_TYPE_REAL
    factory = get_session_factory()
    assert factory is not None

    insert = text(
        f"INSERT INTO {_SCHEMA}.{_EVIDENCE_TABLE} "
        "(evidence_id, dataset_version, source_type, "
        " de_identification_attested, status) "
        "VALUES (:e, :d, :s, true, :st)"
    )
    winner_id = _unique("ev-winner")
    loser_id = _unique("ev-loser")

    # 阶段 A：T1 未提交 → T2 竞争（锁等待 / 唯一约束任一拒绝）
    first_session = factory()
    second_session = factory()
    try:
        with first_session.begin():
            first_session.execute(
                insert,
                {
                    "e": winner_id,
                    "d": dataset_version,
                    "s": source_type,
                    "st": EVIDENCE_STATUS_PERSISTED,
                },
            )
            with pytest.raises(SQLAlchemyError):
                with second_session.begin():
                    second_session.execute(text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
                    second_session.execute(
                        insert,
                        {
                            "e": loser_id,
                            "d": dataset_version,
                            "s": source_type,
                            "st": EVIDENCE_STATUS_PERSISTED,
                        },
                    )
        # 阶段 B：T1 已提交 → T2 再次尝试（UNIQUE 拒绝）
        with pytest.raises(SQLAlchemyError):
            with factory() as session, session.begin():
                session.execute(
                    insert,
                    {
                        "e": loser_id,
                        "d": dataset_version,
                        "s": source_type,
                        "st": EVIDENCE_STATUS_PERSISTED,
                    },
                )
        assert _evidence_count_by_key(source_type, dataset_version) == 1
        stored = repository.get_by_id(winner_id)
        assert stored is not None
        assert stored.evidence_id == winner_id
    finally:
        first_session.close()
        second_session.close()
        _cleanup(winner_id, loser_id)


# ============================================================
# Case 6：Annotation Atomicity（§十二）
# ============================================================


def test_06_annotation_write_is_atomic_with_evidence_reference(
    repository: EvidenceRepository,
) -> None:
    """当前架构：Repository 各自拥有事务（无跨仓储原子 API）。

    因此只验证「Annotation 依赖的 Evidence 不存在 → 整体拒绝、无残留」，
    不新增假的跨仓储原子 API（§十二 明确禁止）。
    """
    annotation_before = _count(_ANNOTATION_TABLE)
    with pytest.raises(EvidenceNotFoundError):
        repository.create_annotation(
            annotation_id=_unique("an"),
            evidence_id="evidence-missing-for-atomicity",
            case_id="case_001",
            annotation_version="annotation-v1",
            annotator_id="annotator-a",
        )
    assert _count(_ANNOTATION_TABLE) == annotation_before

    evidence_id = repository.create_evidence(
        evidence_id=_unique("ev"),
        dataset_version=_unique("step40-at"),
        source_type=SOURCE_TYPE_SYNTHETIC,
        de_identification_attested=True,
    ).evidence_id
    try:
        annotation = repository.create_annotation(
            annotation_id=_unique("an"),
            evidence_id=evidence_id,
            case_id="case_001",
            annotation_version="annotation-v1",
            annotator_id="annotator-a",
        )
        # Evidence 与其 Annotation 同时可见（无 Evidence 存在但 Annotation 缺失）
        assert repository.get_by_id(evidence_id) is not None
        assert [row.annotation_id for row in repository.list_annotations(evidence_id)] == [
            annotation.annotation_id
        ]
    finally:
        _cleanup(evidence_id)
