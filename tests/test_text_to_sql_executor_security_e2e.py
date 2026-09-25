"""SQL Executor Security E2E（Phase 3.9.8）。

Phase 3.9.7 证明了「危险 SQL → 真实 Validator → Reject → Executor 0 次调用」。
本阶段进一步回答：

> **Validator 被绕过后，真实 ``SQLExecutorService`` 自身是否仍有第二道防线？**

因此本阶段**直接调用真实 Executor**，并且**不使用** ``SQLValidatorService``
（§十六）。为了让 SQL 真正到达数据库层，需要绕过 Executor 自带的
re-validation（L294-307，TOCTOU 防护，属生产行为），做法是注入一个
**放行型 Validator**——复用 Executor 已有的 ``validator`` 依赖注入参数，
**不修改任何生产代码、不新增任何参数**（§十七）。

```text
危险 SQL
   ↓
Real SQLExecutorService（validator = 放行桩）
   ↓
BEGIN READ ONLY
   ↓
PostgreSQL 拒绝写操作
   ↓
SQLExecutorDatabaseError + ROLLBACK
   ↓
数据无持久变化
```

真实机制（读源码所得，非假设）：

- AUTOCOMMIT 连接上显式 ``BEGIN READ ONLY``
- ``SET LOCAL statement_timeout``（秒 → ms）
- ``SET LOCAL search_path TO "<schema>"``（标识符已白名单校验）
- ``fetchmany(max_rows + 1)`` 判截断
- ``_materialize`` 结果大小保护（``max_result_bytes``）
- ``finally: ROLLBACK``（无论成败）
- 异常只暴露类名，不透传含连接串 / 密码的消息

数据库范围（§四 / §十九）：

- 仅使用测试 PostgreSQL，**不碰生产库**
- 写操作目标为隔离测试 schema ``t2s_exec_sec``，**不是** ``public.knowledge_document``
- fixture 内建 schema/表，teardown ``DROP SCHEMA ... CASCADE``
- 最终 DB residue = 0
"""
from __future__ import annotations

import os
import time
from typing import Any

import pytest
from sqlalchemy import text as sa_text

from backend.app.db.session import get_engine
from backend.app.services.schema_explorer_service import (
    DatabaseSchema,
    SchemaColumn,
    SchemaTable,
)
from backend.app.services.sql_executor_service import (
    DEFAULT_MAX_ROWS,
    SQLExecutionResult,
    SQLExecutorDatabaseError,
    SQLExecutorService,
    SQLExecutorTimeoutError,
)
from backend.app.services.sql_validator_service import SQLValidationResult

# ============================================================
# 常量 / 环境开关
# ============================================================

_TEST_SCHEMA = "t2s_exec_sec"
_TEST_TABLE = "sample"
_ROW_COUNT = 10
_QUALIFIED = f'"{_TEST_SCHEMA}"."{_TEST_TABLE}"'


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 to enable Executor security E2E (test PG only)",
)


# ============================================================
# 放行型 Validator（仅用于绕过 re-validation，隔离 DB 层防线）
# ============================================================

class PermissiveValidator:
    """放行一切 SQL 的桩 Validator。

    目的：把 Executor 自带的 ``SQLValidatorService`` re-validation 摘除，
    使危险 SQL 能真正到达数据库层，从而验证 **READ ONLY 事务** 这道运行时防线。
    **不修改生产代码**，仅使用 ``SQLExecutorService(validator=...)`` 既有注入点。
    """

    def __init__(self) -> None:
        self.call_count = 0

    def validate(
        self,
        sql: str,
        *,
        schema: Any = None,
        allowed_tables: Any = None,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> SQLValidationResult:
        self.call_count += 1
        return SQLValidationResult(
            valid=True,
            normalized_sql=sql,
            errors=(),
            referenced_tables=(),
        )


# ============================================================
# DB 工具
# ============================================================

def _engine():
    from backend.app.db import reset_engine_cache

    reset_engine_cache()
    engine = get_engine()
    assert engine is not None, "DATABASE_URL not configured"
    return engine


def _autocommit(engine):
    conn = engine.connect()
    return conn.execution_options(isolation_level="AUTOCOMMIT")


def _test_schema_dto() -> DatabaseSchema:
    return DatabaseSchema(
        schema_name=_TEST_SCHEMA,
        tables=(
            SchemaTable(
                schema_name=_TEST_SCHEMA,
                name=_TEST_TABLE,
                description=None,
                columns=(
                    SchemaColumn(
                        name="id", data_type="integer", nullable=True,
                        default=None, ordinal_position=1,
                        is_primary_key=False, description=None,
                    ),
                    SchemaColumn(
                        name="name", data_type="text", nullable=True,
                        default=None, ordinal_position=2,
                        is_primary_key=False, description=None,
                    ),
                ),
                foreign_keys=(),
            ),
        ),
    )


def _read_state() -> tuple[int, str | None, int]:
    """读取隔离表当前状态：(行数, id=1 的 name, name 去重数)。"""
    engine = _engine()
    with _autocommit(engine) as conn:
        count = conn.execute(
            sa_text(f"SELECT count(*) FROM {_QUALIFIED}")
        ).scalar()
        first_name = conn.execute(
            sa_text(f"SELECT name FROM {_QUALIFIED} WHERE id = 1")
        ).scalar()
        distinct_names = conn.execute(
            sa_text(f"SELECT count(DISTINCT name) FROM {_QUALIFIED}")
        ).scalar()
    return int(count or 0), first_name, int(distinct_names or 0)


def _table_exists(qualified_name: str) -> bool:
    engine = _engine()
    with _autocommit(engine) as conn:
        value = conn.execute(
            sa_text(f"SELECT to_regclass('{qualified_name}')")
        ).scalar()
    return value is not None


# ============================================================
# Fixture：隔离 schema + 测试表（setup / teardown 成对）
# ============================================================

@pytest.fixture
def isolated_table():
    """建立隔离 schema + 10 行测试表，测试结束 DROP SCHEMA CASCADE。"""
    engine = _engine()
    with _autocommit(engine) as conn:
        conn.execute(sa_text(f'DROP SCHEMA IF EXISTS "{_TEST_SCHEMA}" CASCADE'))
        conn.execute(sa_text(f'CREATE SCHEMA "{_TEST_SCHEMA}"'))
        conn.execute(
            sa_text(
                f'CREATE TABLE {_QUALIFIED} (id int, name text)'
            )
        )
        conn.execute(
            sa_text(
                f"INSERT INTO {_QUALIFIED} (id, name) "
                f"SELECT i, 'row_' || i FROM generate_series(1, {_ROW_COUNT}) AS i"
            )
        )

    yield _QUALIFIED, _test_schema_dto()

    with _autocommit(engine) as conn:
        conn.execute(sa_text(f'DROP SCHEMA IF EXISTS "{_TEST_SCHEMA}" CASCADE'))


def _service(**kwargs: Any) -> SQLExecutorService:
    """真实 Executor + 放行桩 Validator（不使用 SQLValidatorService）。"""
    return SQLExecutorService(
        engine=_engine(), validator=PermissiveValidator(), **kwargs
    )


# ============================================================
# Tests
# ============================================================

@requires_db
class TestExecutorWriteProtection:
    """§五 ~ §八 / §十五：写操作在 READ ONLY 事务中被数据库拒绝。"""

    async def test_executor_rejects_delete_in_read_only_transaction(
        self, isolated_table: Any
    ) -> None:
        """§五：DELETE 直接交给真实 Executor（Validator 被绕过）→ 执行失败。"""
        _qualified, schema = isolated_table
        before = _read_state()
        service = _service()

        with pytest.raises(SQLExecutorDatabaseError) as exc_info:
            await service.execute(
                f"DELETE FROM {_qualified}", schema=schema
            )

        # 异常脱敏：绝不包含连接串 / 密码
        message = str(exc_info.value).lower()
        assert "postgresql://" not in message
        assert "password" not in message

        # §十五：持久数据未变
        assert _read_state() == before

    async def test_executor_rejects_update_in_read_only_transaction(
        self, isolated_table: Any
    ) -> None:
        """§六：UPDATE 执行失败，且目标行未改变。"""
        _qualified, schema = isolated_table
        before = _read_state()
        service = _service()

        with pytest.raises(SQLExecutorDatabaseError):
            await service.execute(
                f"UPDATE {_qualified} SET name = 'tampered' WHERE id = 1",
                schema=schema,
            )

        after = _read_state()
        assert after == before
        assert after[1] == "row_1"  # id=1 的 name 未被篡改

    async def test_executor_rejects_insert_in_read_only_transaction(
        self, isolated_table: Any
    ) -> None:
        """§七：INSERT 执行失败，且没有新行持久化。"""
        _qualified, schema = isolated_table
        before = _read_state()
        service = _service()

        with pytest.raises(SQLExecutorDatabaseError):
            await service.execute(
                f"INSERT INTO {_qualified} (id, name) VALUES (999, 'intruder')",
                schema=schema,
            )

        after = _read_state()
        assert after[0] == before[0]  # 行数不变
        assert after == before

    async def test_executor_rejects_ddl_in_read_only_transaction(
        self, isolated_table: Any
    ) -> None:
        """§八：DDL（CREATE TABLE）在 READ ONLY 事务中被拒绝，对象未被创建。"""
        _qualified, schema = isolated_table
        probe = f"{_TEST_SCHEMA}.ddl_probe"
        service = _service()

        with pytest.raises(SQLExecutorDatabaseError):
            await service.execute(
                f'CREATE TABLE "{_TEST_SCHEMA}".ddl_probe (id int)',
                schema=schema,
            )

        assert _table_exists(probe) is False
        assert _table_exists(_qualified) is True  # 原表仍在


@requires_db
class TestExecutorReadOnlyTransaction:
    """§九：黑盒验证事务本身处于 READ ONLY 状态。"""

    async def test_executor_transaction_is_read_only(
        self, isolated_table: Any
    ) -> None:
        """通过数据库自身状态证明事务是 READ ONLY（不修改生产 API）。"""
        _qualified, schema = isolated_table
        service = _service()

        result = await service.execute(
            "SELECT current_setting('transaction_read_only') AS mode",
            schema=schema,
        )
        assert result.rows[0][0] == "on"

    async def test_executor_allows_read_only_select(
        self, isolated_table: Any
    ) -> None:
        """§十：只读 SELECT 正常成功 → Executor 不是「拒绝一切」。"""
        _qualified, schema = isolated_table
        service = _service()

        result: SQLExecutionResult = await service.execute(
            f"SELECT id, name FROM {_qualified} ORDER BY id LIMIT 1",
            schema=schema,
            max_rows=5,
        )
        assert result.row_count == 1
        assert result.truncated is False
        assert result.rows == ((1, "row_1"),)


@requires_db
class TestExecutorLimits:
    """§十一 / §十二：行数与结果大小保护（不新增生产逻辑，只测现有行为）。"""

    async def test_executor_enforces_max_rows(
        self, isolated_table: Any
    ) -> None:
        """§十一：10 行表 + max_rows=3 → 只返回 3 行且标记截断。"""
        _qualified, schema = isolated_table
        service = _service()

        result = await service.execute(
            f"SELECT id FROM {_QUALIFIED} ORDER BY id LIMIT 100",
            schema=schema,
            max_rows=3,
        )
        assert result.row_count == 3
        assert result.truncated is True
        assert result.rows == ((1,), (2,), (3,))

    async def test_executor_enforces_result_size_limit(
        self, isolated_table: Any
    ) -> None:
        """§十二：结果超过 max_result_bytes → 截断，不把超大部分当成功。

        按**现有实现**断言（§十八：不改变行为，只测试它）：
        ``_materialize`` 对「第一行即超限」采用 MVP 粗粒度策略——
        保留该行并标记 ``truncated=True``，后续行不再返回。
        """
        _qualified, schema = isolated_table
        # 最小值 64KB（钳制下界），构造约 100KB 单行结果
        service = _service(max_result_bytes=64 * 1024)

        result = await service.execute(
            "SELECT repeat('x', 100000) AS big FROM generate_series(1, 3)",
            schema=schema,
            max_rows=100,
        )
        # 保护生效：被截断，且没有把 3 行完整结果当作成功返回
        assert result.truncated is True
        assert 1 <= result.row_count < 3


@requires_db
class TestExecutorTimeoutAndRecovery:
    """§十三 / §十四：语句超时与异常后连接可恢复。"""

    async def test_executor_enforces_statement_timeout(
        self, isolated_table: Any
    ) -> None:
        """§十三：pg_sleep(2) 在 timeout_seconds=1 下被数据库取消。"""
        _qualified, schema = isolated_table
        service = _service()
        start = time.perf_counter()

        with pytest.raises(SQLExecutorTimeoutError):
            await service.execute(
                "SELECT pg_sleep(2)", schema=schema, timeout_seconds=1
            )

        elapsed = time.perf_counter() - start
        assert elapsed < 5  # 2 秒的 sleep 在 ~1s 被取消
        assert _read_state()[0] == _ROW_COUNT  # 无数据变化

    async def test_executor_connection_recovers_after_failed_query(
        self, isolated_table: Any
    ) -> None:
        """§十四：写操作失败后，同一个 Executor 仍能正常执行只读查询。"""
        _qualified, schema = isolated_table
        service = _service()

        with pytest.raises(SQLExecutorDatabaseError):
            await service.execute(f"DELETE FROM {_qualified}", schema=schema)

        result = await service.execute(
            "SELECT 1 AS value", schema=schema, max_rows=1
        )
        assert result.rows == ((1,),)
        assert result.row_count == 1


@requires_db
class TestNoPersistentMutation:
    """§十五：全部写尝试之后，数据必须没有任何持久变化。"""

    async def test_no_persistent_mutation_after_all_write_attempts(
        self, isolated_table: Any
    ) -> None:
        _qualified, schema = isolated_table
        before = _read_state()
        service = _service()

        for sql in (
            f"DELETE FROM {_qualified}",
            f"UPDATE {_qualified} SET name = 'tampered'",
            f"INSERT INTO {_qualified} (id, name) VALUES (999, 'intruder')",
            f'CREATE TABLE "{_TEST_SCHEMA}".mut_probe (id int)',
        ):
            with pytest.raises(SQLExecutorDatabaseError):
                await service.execute(sql, schema=schema)

        after = _read_state()
        assert after == before
        assert after[0] == _ROW_COUNT
        assert after[1] == "row_1"
        assert _table_exists(f"{_TEST_SCHEMA}.mut_probe") is False
