"""LLM Usage assistant_request_id 索引（Phase 3.12 Step 52）——DB 集成测试。

只在 ``RUN_DB_TESTS=1`` 时运行（默认跳过）。

范围（§九）：
    1. Index exists（真实定义：列 / 非唯一 / btree）
    2. Idempotent initialization（连续两次 ensure_* 不失败）
    3. Exact match（A 只返回 A）
    4. NULL isolation（NULL 行永不匹配）
    5. Stable ordering（created_at ASC, id ASC —— 与 Step 51 前完全一致）
    6. Empty result（unknown → []）
    + 既有索引未消失（pkey / created_at / uq request_id）

数据：只使用 synthetic 行（provider request_id 前缀 ``step52-``），
      **不读取真实 WMS 数据**；teardown 定向 DELETE 本模块自己的行
      （**不使用 TRUNCATE**），并断言 residue = 0 且总行数不变。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import text

from backend.app.db.models.llm_usage_record import (
    LLM_USAGE_ASSISTANT_REQUEST_ID_INDEX,
)
from backend.app.db.session import get_engine
from backend.app.services.llm_usage_query_service import LLMUsageQueryService

_TABLE = "ai_ops.llm_usage_record"
_PREFIX = "step52-"
_BASE_TIME = datetime(2026, 9, 29, 16, 0, 0, tzinfo=timezone.utc)

_INSERT_SQL = text(
    f"INSERT INTO {_TABLE} (request_id, provider, model, "
    "prompt_tokens, completion_tokens, total_tokens, created_at, "
    "assistant_request_id) "
    "VALUES (:request_id, 'step52-provider', 'step52-model', 1, 2, 3, "
    ":created_at, :assistant_request_id)"
)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


def _engine():  # noqa: ANN202
    engine = get_engine()
    if engine is None:
        pytest.skip("DATABASE_URL 未配置")
    return engine


def _assistant_id(suffix: str) -> str:
    return f"{_PREFIX}assistant-{suffix}-{uuid.uuid4().hex[:8]}"


def _provider_id(suffix: str) -> str:
    return f"{_PREFIX}provider-{suffix}-{uuid.uuid4().hex[:8]}"


def _insert_row(
    *,
    assistant_request_id: str | None,
    created_at: datetime,
    suffix: str = "row",
) -> None:
    with _engine().begin() as conn:
        conn.execute(
            _INSERT_SQL,
            {
                "request_id": _provider_id(suffix),
                "created_at": created_at,
                "assistant_request_id": assistant_request_id,
            },
        )


def _index_definitions() -> dict[str, str]:
    with _engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'ai_ops' AND tablename = 'llm_usage_record'"
            )
        ).all()
    return {name: ddl for name, ddl in rows}


def _total_rows() -> int:
    with _engine().connect() as conn:
        return int(
            conn.execute(text(f"SELECT COUNT(*) FROM {_TABLE}")).scalar_one()
        )


def _own_rows() -> int:
    with _engine().connect() as conn:
        return int(
            conn.execute(
                text(
                    f"SELECT COUNT(*) FROM {_TABLE} "
                    "WHERE request_id LIKE :prefix"
                ),
                {"prefix": f"{_PREFIX}%"},
            ).scalar_one()
        )


def _delete_own_rows() -> None:
    with _engine().begin() as conn:
        conn.execute(
            text(f"DELETE FROM {_TABLE} WHERE request_id LIKE :prefix"),
            {"prefix": f"{_PREFIX}%"},
        )


@pytest.fixture(scope="module", autouse=True)
def _schema():
    if not _env_flag("RUN_DB_TESTS"):
        yield
        return
    from backend.app.db import init_db as init_db_module

    init_db_module.init_db()
    yield


@pytest.fixture(autouse=True)
def _cleanup():
    """定向清理 + 行数守恒断言（count_before == count_after）。"""
    if not _env_flag("RUN_DB_TESTS"):
        yield
        return
    _delete_own_rows()
    before = _total_rows()
    yield
    _delete_own_rows()
    assert _own_rows() == 0, "Step 52 测试行未清理（residue != 0）"
    assert _total_rows() == before, "llm_usage_record 行数发生变化"


# ============================================================
# 0. ORM 声明 + init_db 幂等 DDL 接线（**离线**，默认套件即运行）
# ============================================================

class TestOrmDeclaration:
    """无 DB 也运行：保证 Model / init_db 的索引声明不被误删。"""

    def test_model_declares_the_index(self) -> None:
        from backend.app.db.models.llm_usage_record import LLMUsageRecord

        indexes = {index.name: index for index in LLMUsageRecord.__table__.indexes}
        assert LLM_USAGE_ASSISTANT_REQUEST_ID_INDEX in indexes
        index = indexes[LLM_USAGE_ASSISTANT_REQUEST_ID_INDEX]

        assert [column.name for column in index.columns] == [
            "assistant_request_id"
        ]
        assert index.unique is False              # 非唯一
        assert not index.dialect_kwargs           # 非 partial

    def test_init_db_exposes_idempotent_helper(self) -> None:
        from backend.app.db import init_db as init_db_module

        assert callable(init_db_module.ensure_assistant_request_id_index)
        assert (
            "ensure_assistant_request_id_index"
            in init_db_module.__all__
        )
        source = (
            __import__("pathlib").Path(
                init_db_module.__file__
            ).read_text(encoding="utf-8")
        )
        assert "CREATE INDEX IF NOT EXISTS" in source
        assert "ensure_assistant_request_id_index(conn)" in source

    def test_column_definition_unchanged(self) -> None:
        from backend.app.db.models.llm_usage_record import LLMUsageRecord

        column = LLMUsageRecord.__table__.columns["assistant_request_id"]

        assert column.nullable is True            # NULL 合法（不 backfill）
        assert column.unique is not True
        assert column.type.length == 128


# ============================================================
# 1 / 2. Index 存在 + 幂等初始化
# ============================================================

@requires_db
class TestIndexExistence:
    def test_index_exists_with_expected_definition(self) -> None:
        definitions = _index_definitions()
        assert LLM_USAGE_ASSISTANT_REQUEST_ID_INDEX in definitions
        ddl = definitions[LLM_USAGE_ASSISTANT_REQUEST_ID_INDEX]

        assert "USING btree" in ddl
        assert "(assistant_request_id)" in ddl
        assert "UNIQUE" not in ddl.upper()          # 非唯一（一次请求可多条 usage）
        assert "WHERE" not in ddl.upper()           # 非 partial
        assert "created_at" not in ddl              # 非复合

    def test_index_metadata_matches(self) -> None:
        with _engine().connect() as conn:
            row = conn.execute(
                text(
                    "SELECT i.indisunique, i.indisprimary, "
                    "pg_get_indexdef(i.indexrelid) AS ddl "
                    "FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indexrelid "
                    "WHERE c.relname = :name"
                ),
                {"name": LLM_USAGE_ASSISTANT_REQUEST_ID_INDEX},
            ).one()
            column_names = [
                attname
                for attname in conn.execute(
                    text(
                        "SELECT a.attname FROM pg_index i "
                        "JOIN pg_attribute a ON a.attrelid = i.indrelid "
                        "AND a.attnum = ANY(i.indkey) "
                        "WHERE i.indexrelid = ("
                        "  SELECT oid FROM pg_class WHERE relname = :name)"
                    ),
                    {"name": LLM_USAGE_ASSISTANT_REQUEST_ID_INDEX},
                ).scalars()
            ]

        assert row[0] is False        # non-unique
        assert row[1] is False        # not primary
        assert column_names == ["assistant_request_id"]

    def test_existing_indexes_still_present(self) -> None:
        definitions = _index_definitions()

        assert "llm_usage_record_pkey" in definitions
        assert "ix_llm_usage_record_created_at" in definitions
        assert "uq_llm_usage_record_request_id" in definitions
        assert "request_id IS NOT NULL" in definitions[
            "uq_llm_usage_record_request_id"
        ]

    def test_index_is_idempotent(self) -> None:
        from backend.app.db.init_db import ensure_assistant_request_id_index

        with _engine().begin() as conn:
            first = ensure_assistant_request_id_index(conn)
        with _engine().begin() as conn:
            second = ensure_assistant_request_id_index(conn)

        assert first is True and second is True
        matching = [
            name
            for name in _index_definitions()
            if name == LLM_USAGE_ASSISTANT_REQUEST_ID_INDEX
        ]
        assert matching == [LLM_USAGE_ASSISTANT_REQUEST_ID_INDEX]


# ============================================================
# 3 / 4 / 5 / 6. 查询语义（结果 / 排序与 Step 51 前完全一致）
# ============================================================

@requires_db
class TestQuerySemantics:
    def test_exact_match_returns_only_that_request(self) -> None:
        a = _assistant_id("a")
        b = _assistant_id("b")
        _insert_row(
            assistant_request_id=a,
            created_at=_BASE_TIME,
            suffix="a1",
        )
        _insert_row(
            assistant_request_id=b,
            created_at=_BASE_TIME,
            suffix="b1",
        )

        rows = LLMUsageQueryService().list_by_assistant_request_id(a)

        assert len(rows) == 1
        assert rows[0].assistant_request_id == a

    def test_null_assistant_request_id_is_never_matched(self) -> None:
        a = _assistant_id("nulliso")
        _insert_row(
            assistant_request_id=None,
            created_at=_BASE_TIME,
            suffix="null",
        )
        _insert_row(
            assistant_request_id=a,
            created_at=_BASE_TIME,
            suffix="a",
        )

        rows = LLMUsageQueryService().list_by_assistant_request_id(a)

        assert [row.assistant_request_id for row in rows] == [a]
        assert all(row.assistant_request_id is not None for row in rows)

    def test_stable_ordering_created_at_then_id(self) -> None:
        a = _assistant_id("order")
        # 故意乱序插入（先插入较晚的 created_at）
        _insert_row(
            assistant_request_id=a,
            created_at=_BASE_TIME + timedelta(seconds=2),
            suffix="late",
        )
        _insert_row(
            assistant_request_id=a,
            created_at=_BASE_TIME,
            suffix="early",
        )
        _insert_row(
            assistant_request_id=a,
            created_at=_BASE_TIME + timedelta(seconds=1),
            suffix="mid",
        )

        rows = LLMUsageQueryService().list_by_assistant_request_id(a)

        assert len(rows) == 3
        assert [row.created_at for row in rows] == [
            _BASE_TIME,
            _BASE_TIME + timedelta(seconds=1),
            _BASE_TIME + timedelta(seconds=2),
        ]
        pairs = [(row.created_at, row.id) for row in rows]
        assert pairs == sorted(pairs)              # created_at ASC, id ASC

    def test_empty_result_for_unknown_request(self) -> None:
        rows = LLMUsageQueryService().list_by_assistant_request_id(
            f"{_PREFIX}unknown-{uuid.uuid4().hex[:8]}"
        )

        assert rows == []


__all__ = [
    "TestOrmDeclaration",
    "TestIndexExistence",
    "TestQuerySemantics",
]
