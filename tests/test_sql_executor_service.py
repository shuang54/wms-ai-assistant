"""Read-only SQL Executor 测试（Phase 3.7.7）。

单元测试全部使用 Fake Engine / Fake Validator（无数据库）；
真实 PostgreSQL 集成测试由 RUN_DB_TESTS=1 开启（DB writes = 0）。
"""
from __future__ import annotations

import os
import time

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from backend.app.services.schema_explorer_service import (
    DatabaseSchema,
    SchemaColumn,
    SchemaTable,
)
from backend.app.services.sql_validator_service import (
    SQLValidationCode,
    SQLValidationError,
    SQLValidationResult,
)
from backend.app.services.sql_executor_service import (
    DEFAULT_MAX_RESULT_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    SQLExecutionResult,
    SQLExecutor,
    SQLExecutorDatabaseError,
    SQLExecutorInputError,
    SQLExecutorService,
    SQLExecutorTimeoutError,
    SQLExecutorUnavailableError,
    SQLExecutorValidationError,
)


# ============================================================
# Fakes
# ============================================================

class FakeResult:
    def __init__(self, columns: tuple[str, ...], rows: list[tuple]) -> None:
        self._columns = columns
        self._rows = rows

    def keys(self) -> list[str]:
        return list(self._columns)

    def fetchmany(self, size: int) -> list[tuple]:
        return self._rows[:size]


class FakeConnection:
    def __init__(self, engine: "FakeEngine") -> None:
        self._engine = engine

    def execution_options(self, **kwargs) -> "FakeConnection":
        return self

    def execute(self, statement) -> FakeResult:
        sql = getattr(statement, "text", str(statement))
        self._engine.executed.append(sql)
        handler = self._engine.handlers.get(sql)
        if handler is not None:
            raise handler
        columns, rows = self._engine.payload
        return FakeResult(columns, list(rows))

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *exc) -> None:
        return None


class FakeEngine:
    """记录全部执行语句的 Fake Engine。"""

    def __init__(
        self,
        payload: tuple[tuple[str, ...], list[tuple]] = (("value",), [(1,)]),
        handlers: dict[str, Exception] | None = None,
    ) -> None:
        self.payload = payload
        self.handlers = handlers or {}
        self.executed: list[str] = []
        self.connect_count = 0

    @property
    def query_sqls(self) -> list[str]:
        return [s for s in self.executed
                if not s.startswith(("BEGIN", "SET LOCAL", "ROLLBACK"))]

    def connect(self) -> FakeConnection:
        self.connect_count += 1
        return FakeConnection(self)


class FakeValidator:
    """可脚本化的 Fake Validator（记录调用）。"""

    def __init__(self, decisions: list[bool] | None = None) -> None:
        self.calls: list[tuple[str, int]] = []
        self._decisions = list(decisions) if decisions else None

    def validate(self, sql, *, schema=None, allowed_tables=None,
                 max_rows=1000) -> SQLValidationResult:
        self.calls.append((sql, max_rows))
        valid = True
        if self._decisions:
            valid = self._decisions.pop(0)
        if valid:
            return SQLValidationResult(True, sql, (), ())
        return SQLValidationResult(
            False, None,
            (SQLValidationError(
                SQLValidationCode.NON_READ_ONLY, "fake forbidden"
            ),),
            (),
        )


def make_service(
    payload=(("value",), [(1,)]),
    handlers=None,
    decisions=None,
    **kwargs,
) -> tuple[SQLExecutorService, FakeEngine, FakeValidator]:
    engine = FakeEngine(payload=payload, handlers=handlers)
    validator = FakeValidator(decisions=decisions)
    service = SQLExecutorService(
        engine=engine, validator=validator, **kwargs
    )
    return service, engine, validator


# ============================================================
# A. 基础成功
# ============================================================

class TestBasicSuccess:
    async def test_select_one(self) -> None:
        service, _, _ = make_service()
        result = await service.execute("SELECT 1 AS value LIMIT 1")
        assert isinstance(result, SQLExecutionResult)
        assert result.columns == ("value",)
        assert result.rows == ((1,),)
        assert result.row_count == 1
        assert result.truncated is False
        assert result.execution_time_ms >= 0

    async def test_multi_row(self) -> None:
        service, _, _ = make_service(
            payload=(("id",), [(1,), (2,), (3,)])
        )
        result = await service.execute(
            "SELECT id FROM public.t LIMIT 3", max_rows=10
        )
        assert result.row_count == 3
        assert result.rows == ((1,), (2,), (3,))
        assert result.columns == ("id",)

    async def test_default_values(self) -> None:
        assert DEFAULT_TIMEOUT_SECONDS == 10
        assert DEFAULT_MAX_RESULT_BYTES == 1024 * 1024


# ============================================================
# B. Validator 集成（执行前重新验证）
# ============================================================

class TestValidatorIntegration:
    async def test_delete_rejected_no_execution(self) -> None:
        service, engine, validator = make_service(decisions=[False])
        with pytest.raises(SQLExecutorValidationError) as exc_info:
            await service.execute("DELETE FROM public.t WHERE id = 1")
        codes = [e.code for e in exc_info.value.validation_errors]
        assert SQLValidationCode.NON_READ_ONLY in codes
        assert engine.connect_count == 0  # 数据库零接触
        assert validator.calls[0][0] == "DELETE FROM public.t WHERE id = 1"

    async def test_unauthorized_table_rejected(self) -> None:
        service, engine, _ = make_service(decisions=[False])
        with pytest.raises(SQLExecutorValidationError):
            await service.execute(
                "SELECT * FROM public.unauthorized LIMIT 10"
            )
        assert engine.connect_count == 0

    async def test_real_validator_rejects_delete(self) -> None:
        """真实 SQLValidator（非 Fake）：DELETE 无法进入执行。"""
        engine = FakeEngine()
        service = SQLExecutorService(engine=engine)  # 真实 validator
        with pytest.raises(SQLExecutorValidationError) as exc_info:
            await service.execute("DELETE FROM public.t WHERE id = 1")
        codes = [e.code for e in exc_info.value.validation_errors]
        assert SQLValidationCode.NON_READ_ONLY in codes
        assert engine.connect_count == 0

    async def test_real_validator_rejects_no_limit(self) -> None:
        engine = FakeEngine()
        service = SQLExecutorService(engine=engine)
        with pytest.raises(SQLExecutorValidationError):
            await service.execute("SELECT * FROM public.t")
        assert engine.connect_count == 0

    async def test_validated_string_is_executed_string(self) -> None:
        """TOCTOU：验证的 SQL 与执行的 SQL 是同一字符串。"""
        sql = "SELECT id FROM public.t LIMIT 5"
        service, engine, validator = make_service()
        await service.execute(sql)
        assert validator.calls[0][0] == sql
        assert engine.query_sqls == [sql]

    async def test_validator_called_with_max_rows(self) -> None:
        service, _, validator = make_service()
        await service.execute("SELECT 1 LIMIT 5", max_rows=77)
        assert validator.calls[0][1] == 77


# ============================================================
# C. Read-only 事务协议
# ============================================================

class TestReadOnlyTransaction:
    async def test_begin_read_only_and_rollback_executed(self) -> None:
        service, engine, _ = make_service()
        await service.execute("SELECT 1 LIMIT 1")
        assert "BEGIN READ ONLY" in engine.executed
        assert "ROLLBACK" in engine.executed

    async def test_statement_timeout_set_local(self) -> None:
        service, engine, _ = make_service()
        await service.execute("SELECT 1 LIMIT 1", timeout_seconds=5)
        timeouts = [s for s in engine.executed if "statement_timeout" in s]
        assert timeouts == ["SET LOCAL statement_timeout = 5000"]

    async def test_execution_order(self) -> None:
        service, engine, _ = make_service()
        await service.execute("SELECT 1 LIMIT 1")
        idx_begin = engine.executed.index("BEGIN READ ONLY")
        idx_query = engine.executed.index("SELECT 1 LIMIT 1")
        idx_rollback = engine.executed.index("ROLLBACK")
        assert idx_begin < idx_query < idx_rollback

    async def test_rollback_executed_on_query_error(self) -> None:
        service, engine, _ = make_service(
            handlers={"SELECT 1 LIMIT 1": ProgrammingError(
                "stmt", {}, Exception("syntax error")
            )}
        )
        with pytest.raises(SQLExecutorDatabaseError):
            await service.execute("SELECT 1 LIMIT 1")
        assert "ROLLBACK" in engine.executed


# ============================================================
# D. Timeout
# ============================================================

class TestTimeout:
    async def test_timeout_error(self) -> None:
        cancel = OperationalError(
            "stmt", {}, Exception("canceling statement due to statement timeout")
        )
        service, _, _ = make_service(
            handlers={"SELECT 1 LIMIT 1": cancel}
        )
        with pytest.raises(SQLExecutorTimeoutError) as exc_info:
            await service.execute("SELECT 1 LIMIT 1", timeout_seconds=2)
        assert exc_info.value.timeout_seconds == 2

    async def test_query_canceled_class_detected(self) -> None:
        class QueryCanceled(Exception):
            pass

        cancel = OperationalError("stmt", {}, QueryCanceled())
        service, _, _ = make_service(handlers={"SELECT 1 LIMIT 1": cancel})
        with pytest.raises(SQLExecutorTimeoutError):
            await service.execute("SELECT 1 LIMIT 1")


# ============================================================
# E. max_rows / 截断
# ============================================================

class TestRowLimit:
    async def test_rows_capped_at_max_rows(self) -> None:
        rows = [(i,) for i in range(11)]
        service, _, _ = make_service(payload=(("n",), rows))
        result = await service.execute(
            "SELECT n FROM t LIMIT 11", max_rows=10
        )
        assert result.row_count == 10
        assert result.truncated is True
        assert result.rows[-1] == (9,)  # 0..10 的前 10 行

    async def test_within_limit_not_truncated(self) -> None:
        rows = [(i,) for i in range(5)]
        service, _, _ = make_service(payload=(("n",), rows))
        result = await service.execute(
            "SELECT n FROM t LIMIT 5", max_rows=10
        )
        assert result.row_count == 5
        assert result.truncated is False

    async def test_result_size_protection(self) -> None:
        big = "x" * 100_000
        rows = [(big,), (big,), (big,)]
        service, _, _ = make_service(
            payload=(("v",), rows), max_result_bytes=250_000
        )
        result = await service.execute(
            "SELECT v FROM t LIMIT 3", max_rows=10
        )
        assert result.truncated is True
        assert result.row_count == 2  # 第三行会超 250KB → 截断

    async def test_first_row_over_size_limit_kept(self) -> None:
        big = "x" * 500_000
        service, _, _ = make_service(
            payload=(("v",), [(big,), (big,)]), max_result_bytes=64 * 1024
        )
        result = await service.execute(
            "SELECT v FROM t LIMIT 2", max_rows=10
        )
        assert result.row_count == 1
        assert result.truncated is True


# ============================================================
# F. 输入校验
# ============================================================

class TestInputValidation:
    async def test_empty_sql(self) -> None:
        service, engine, validator = make_service()
        with pytest.raises(SQLExecutorInputError):
            await service.execute("")
        with pytest.raises(SQLExecutorInputError):
            await service.execute("   ")
        assert engine.connect_count == 0
        assert validator.calls == []

    async def test_non_string_sql(self) -> None:
        service, _, _ = make_service()
        with pytest.raises(SQLExecutorInputError):
            await service.execute(123)  # type: ignore[arg-type]

    @pytest.mark.parametrize("kwargs", [
        {"max_rows": 0},
        {"max_rows": -1},
        {"max_rows": 100001},
        {"max_rows": "10"},
        {"max_rows": True},
        {"timeout_seconds": 0},
        {"timeout_seconds": 601},
        {"timeout_seconds": 1.5},
        {"allowed_tables": "public.t"},
        {"allowed_tables": [1]},
        {"schema": object()},
    ])
    async def test_invalid_kwargs(self, kwargs) -> None:
        service, engine, _ = make_service()
        with pytest.raises(SQLExecutorInputError):
            await service.execute("SELECT 1 LIMIT 1", **kwargs)
        assert engine.connect_count == 0

    async def test_invalid_constructor_args(self) -> None:
        for kwargs in (
            {"timeout_seconds": 0}, {"timeout_seconds": 9999},
            {"max_rows": 0}, {"max_rows": 10**9},
            {"max_result_bytes": 1}, {"max_result_bytes": 10**10},
            {"max_rows": "5"},
        ):
            with pytest.raises(SQLExecutorInputError):
                SQLExecutorService(**kwargs)

    async def test_engine_unavailable(self, monkeypatch) -> None:
        import backend.app.services.sql_executor_service as mod
        monkeypatch.setattr(mod, "get_engine", lambda: None)
        service = SQLExecutorService()  # 无注入 engine
        with pytest.raises(SQLExecutorUnavailableError):
            await service.execute("SELECT 1 LIMIT 1")


# ============================================================
# G. 数据库异常脱敏
# ============================================================

class TestDatabaseErrorSanitization:
    async def test_error_message_has_no_connection_info(self) -> None:
        err = OperationalError(
            "stmt", {},
            Exception("connection to postgresql://user:secret@h/db failed"),
        )
        service, _, _ = make_service(handlers={"SELECT 1 LIMIT 1": err})
        with pytest.raises(SQLExecutorDatabaseError) as exc_info:
            await service.execute("SELECT 1 LIMIT 1")
        message = str(exc_info.value)
        assert "postgresql://" not in message
        assert "secret" not in message
        assert "password" not in message.lower()
        # 只暴露异常类名
        assert "OperationalError" in exc_info.value.reason

    async def test_generic_db_error(self) -> None:
        service, _, _ = make_service(handlers={
            "SELECT 1 LIMIT 1": ProgrammingError("s", {}, Exception("boom"))
        })
        with pytest.raises(SQLExecutorDatabaseError):
            await service.execute("SELECT 1 LIMIT 1")


# ============================================================
# H. 结果纯净性 / DTO
# ============================================================

class TestResultPurity:
    async def test_rows_are_plain_python(self) -> None:
        payload = (("id", "name"), [(1, "a"), (2, "b")])
        service, _, _ = make_service(payload=payload)
        result = await service.execute("SELECT id, name FROM t LIMIT 2")
        for row in result.rows:
            assert type(row) is tuple
            for value in row:
                assert isinstance(value, (int, str, float, bool, type(None)))
        assert result.columns == ("id", "name")

    async def test_dto_frozen(self) -> None:
        service, _, _ = make_service()
        result = await service.execute("SELECT 1 LIMIT 1")
        with pytest.raises(Exception):
            result.rows = ()  # type: ignore[misc]
        with pytest.raises(Exception):
            result.truncated = True  # type: ignore[misc]

    async def test_protocol_satisfied(self) -> None:
        executor: SQLExecutor = SQLExecutorService(engine=FakeEngine())
        assert callable(executor.execute)


# ============================================================
# I. 静态安全检查
# ============================================================

class TestStaticSecurity:
    def test_no_llm_or_embedding_dependency(self) -> None:
        import inspect
        import backend.app.services.sql_executor_service as mod
        source = inspect.getsource(mod)
        lowered = source.lower()
        assert "deepseek" not in lowered
        assert "embedding" not in lowered
        assert "reranker" not in lowered
        assert "httpx" not in lowered
        assert "requests" not in lowered

    def test_no_eval_exec_subprocess(self) -> None:
        import inspect
        import backend.app.services.sql_executor_service as mod
        source = inspect.getsource(mod)
        assert "eval(" not in source
        assert "exec(" not in source
        assert "subprocess" not in source
        assert "os.system" not in source

    def test_reuses_global_engine_infrastructure(self) -> None:
        """复用现有 db.session.get_engine，不重建 engine。"""
        import inspect
        import backend.app.services.sql_executor_service as mod
        source = inspect.getsource(mod)
        assert "create_engine" not in source
        assert "from backend.app.db.session import get_engine" in source

    def test_does_not_modify_sql(self) -> None:
        """Executor 不修改 SQL：执行的字符串与输入完全一致。"""
        import asyncio

        engine = FakeEngine()
        service = SQLExecutorService(engine=engine)
        asyncio.run(service.execute("SELECT 1 LIMIT 1"))
        assert engine.query_sqls == ["SELECT 1 LIMIT 1"]


# ============================================================
# 真实 PostgreSQL 集成（RUN_DB_TESTS=1，DB writes = 0）
# ============================================================

def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


def make_table(name: str, columns: tuple[str, ...]) -> SchemaTable:
    return SchemaTable(
        schema_name="public",
        name=name,
        description=None,
        columns=tuple(
            SchemaColumn(
                name=c, data_type="bigint", nullable=False, default=None,
                ordinal_position=i + 1, is_primary_key=(c == "id"),
                description=None,
            )
            for i, c in enumerate(columns)
        ),
        foreign_keys=(),
    )


def real_schema() -> DatabaseSchema:
    return DatabaseSchema(
        schema_name="public",
        tables=(
            make_table("knowledge_document", ("id", "title", "file_name")),
            make_table("knowledge_chunk", ("id", "document_id", "content")),
        ),
    )


@requires_db
class TestRealDatabase:
    def _service(self, **kwargs) -> SQLExecutorService:
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine

        reset_engine_cache()
        engine = get_engine()
        assert engine is not None
        return SQLExecutorService(engine=engine, **kwargs)

    async def test_select_one(self) -> None:
        result = await self._service().execute("SELECT 1 AS value LIMIT 1")
        assert result.columns == ("value",)
        assert result.rows == ((1,),)
        assert result.row_count == 1
        assert result.truncated is False
        assert result.execution_time_ms > 0

    async def test_select_from_knowledge_document(self) -> None:
        result = await self._service().execute(
            "SELECT id, title FROM public.knowledge_document LIMIT 10",
            schema=real_schema(),
            allowed_tables=("public.knowledge_document",),
        )
        assert result.columns == ("id", "title")
        assert result.row_count <= 10
        for row in result.rows:
            assert isinstance(row[0], int)
            assert isinstance(row[1], (str, type(None)))

    async def test_delete_rejected_and_data_unchanged(self) -> None:
        from sqlalchemy import text as sa_text
        from backend.app.db.session import get_engine

        engine = get_engine()
        with engine.connect() as conn:
            before = conn.execute(sa_text(
                "SELECT count(*) FROM public.knowledge_document"
            )).scalar()

        service = self._service()
        with pytest.raises(SQLExecutorValidationError):
            await service.execute(
                "DELETE FROM public.knowledge_document WHERE id = 1",
                schema=real_schema(),
            )

        with engine.connect() as conn:
            after = conn.execute(sa_text(
                "SELECT count(*) FROM public.knowledge_document"
            )).scalar()
        assert after == before  # DB writes = 0

    async def test_write_operations_rejected(self) -> None:
        service = self._service()
        for sql in (
            "INSERT INTO public.knowledge_document (title) VALUES ('x')",
            "UPDATE public.knowledge_document SET title = 'x'",
            "DROP TABLE public.knowledge_document",
            "ALTER TABLE public.knowledge_document ADD COLUMN c int",
            "TRUNCATE TABLE public.knowledge_document",
            "CREATE TABLE public.tmp_x (id int)",
            "GRANT SELECT ON public.knowledge_document TO alice",
            "SELECT * INTO public.tmp_x FROM public.knowledge_document LIMIT 1",
        ):
            with pytest.raises(SQLExecutorValidationError):
                await service.execute(sql, schema=real_schema())

    async def test_unauthorized_table_rejected(self) -> None:
        service = self._service()
        with pytest.raises(SQLExecutorValidationError):
            await service.execute(
                "SELECT * FROM public.knowledge_chunk LIMIT 10",
                schema=real_schema(),
                allowed_tables=("public.knowledge_document",),
            )

    async def test_multi_statement_rejected(self) -> None:
        service = self._service()
        sql = (
            "SELECT * FROM public.knowledge_document LIMIT 10; "
            "DELETE FROM public.knowledge_document"
        )
        with pytest.raises(SQLExecutorValidationError):
            await service.execute(sql, schema=real_schema())

    async def test_read_only_transaction_blocks_create(self) -> None:
        """运行时防线：即使 Validator 被绕过（Fake 放行），
        READ ONLY 事务也在数据库端拒绝写操作。"""
        from sqlalchemy import text as sa_text
        from backend.app.db.session import get_engine

        service = self._service()
        service._validator = FakeValidator(decisions=[True])
        with pytest.raises(SQLExecutorDatabaseError):
            await service.execute(
                "CREATE TABLE public._executor_ro_probe (id int)"
            )
        # 确认表未被创建
        engine = get_engine()
        with engine.connect() as conn:
            exists = conn.execute(sa_text(
                "SELECT to_regclass('public._executor_ro_probe')"
            )).scalar()
        assert exists is None  # DB writes = 0

    async def test_statement_timeout_real(self) -> None:
        """真实超时：pg_sleep（仅 Fake Validator 放行）1 秒被取消。"""
        service = self._service()
        service._validator = FakeValidator(decisions=[True])
        start = time.perf_counter()
        with pytest.raises(SQLExecutorTimeoutError):
            await service.execute(
                "SELECT pg_sleep(5) LIMIT 1", timeout_seconds=1
            )
        elapsed = time.perf_counter() - start
        assert elapsed < 4  # 5 秒的 sleep 在 ~1s 被取消

    async def test_executor_row_cap_real(self) -> None:
        """Executor 第二层行数保护：SQL LIMIT 50 > max_rows 10 → 截断。"""
        service = self._service()
        service._validator = FakeValidator(decisions=[True])
        result = await service.execute(
            "SELECT n FROM generate_series(1, 50) AS n LIMIT 50",
            max_rows=10,
        )
        assert result.row_count == 10
        assert result.truncated is True
        assert result.rows == tuple((i,) for i in range(1, 11))

    async def test_connection_pool_not_polluted(self) -> None:
        """SET LOCAL 是事务级：执行结束后新连接不受 timeout 影响。"""
        from sqlalchemy import text as sa_text
        from backend.app.db.session import get_engine

        service = self._service()
        service._validator = FakeValidator(decisions=[True, True])
        await service.execute("SELECT 1 LIMIT 1", timeout_seconds=1)
        # 再执行一次（同一 pool 的另一连接）
        await service.execute("SELECT 1 LIMIT 1", timeout_seconds=1)
        engine = get_engine()
        with engine.connect() as conn:
            value = conn.execute(sa_text("SELECT 1")).scalar()
        assert value == 1


# ============================================================
# 全链路闭环：Text-to-SQL（Fake LLM）→ Validator → Executor → 真实 DB
# ============================================================

@requires_db
class TestFullChain:
    async def test_generator_validator_executor_chain(self) -> None:
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine
        from backend.app.services.text_to_sql_service import TextToSQLService

        reset_engine_cache()
        engine = get_engine()
        assert engine is not None

        class ChainFakeLLM:
            async def chat(self, messages, *, tools=None):
                return ("SELECT id, title FROM public.knowledge_document "
                        "LIMIT 5")

        generated = await TextToSQLService(
            llm_client=ChainFakeLLM()
        ).generate(
            "知识文档有哪些？",
            database_context=(
                "Table public.knowledge_document("
                "id bigint, title varchar, file_name varchar)"
            ),
            allowed_tables=("public.knowledge_document",),
            schema=real_schema(),
        )
        assert generated.validated is True

        result = await SQLExecutorService(engine=engine).execute(
            generated.sql,
            schema=real_schema(),
            allowed_tables=("public.knowledge_document",),
        )
        assert result.row_count <= 5
        assert result.columns == ("id", "title")
