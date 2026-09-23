"""Schema Explorer Service 测试（Phase 3.7.1）。

分两层：

    1. 单元测试（无需数据库，始终运行）：
       - schema 参数校验（非法标识符 / 注入串 / 系统 schema → 拒绝且不发 SQL）
       - DATABASE_URL 未配置 → RuntimeError
       - DTO 不可变 / 类型稳定 / 不暴露 ORM 对象
       - SQL 常量只读性（SELECT-only，无任何 DDL/DML 关键字）

    2. DB 集成测试（RUN_DB_TESTS=1 + DATABASE_URL，默认 skip）：
       - 空数据库（schema 存在但无表）→ tables=()
       - 单表：表名 / 列 / 类型 / nullable / default / ordinal_position
       - 主键：is_primary_key 标记
       - 外键：child.parent_id → parent.id
       - COMMENT ON TABLE / COLUMN 正确读取
       - schema="public"（真实业务表）+ 默认 schema 策略
       - 不存在的 schema → SchemaNotFoundError
       - SQL 注入防护：注入串被拒绝，业务表完好无损

隔离策略：
    DB 测试使用**独立的 schema_explorer_test schema**：
    测试前创建 → 测试执行 → 测试后 DROP SCHEMA ... CASCADE。
    绝不 TRUNCATE / 修改 public 下的 knowledge_document / knowledge_chunk。
"""
from __future__ import annotations

import os
import re

import pytest

from backend.app.services import schema_explorer_service as sx_module
from backend.app.services.schema_explorer_service import (
    DEFAULT_SCHEMA,
    DatabaseSchema,
    SchemaColumn,
    SchemaExplorerError,
    SchemaExplorerInputError,
    SchemaExplorerService,
    SchemaForeignKey,
    SchemaNotFoundError,
    SchemaTable,
)


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


# ============================================================
# 单元测试（无需数据库）
# ============================================================

class TestSchemaValidation:
    """schema 参数校验：全部在发起任何 DB 查询之前拒绝。"""

    async def test_none_uses_default_public(self) -> None:
        assert SchemaExplorerService._validate_schema(None) == "public"

    async def test_valid_identifier_accepted(self) -> None:
        assert SchemaExplorerService._validate_schema("public") == "public"
        assert SchemaExplorerService._validate_schema("  wms  ") == "wms"
        assert SchemaExplorerService._validate_schema("_private") == "_private"
        assert SchemaExplorerService._validate_schema("schema_2026") == "schema_2026"

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "   ",
            "a b",
            "a-b",
            "1abc",
            "public; DROP TABLE x",
            'public"; DROP TABLE knowledge_document',
            "public' OR '1'='1",
            "public--comment",
            "pg_catalog",
            "information_schema",
            "pg_temp",
            "pg_toast",
        ],
    )
    async def test_illegal_or_system_schema_rejected(self, bad: str) -> None:
        with pytest.raises(SchemaExplorerInputError):
            SchemaExplorerService._validate_schema(bad)

    async def test_non_string_rejected(self) -> None:
        for bad in (123, [], object()):
            with pytest.raises(SchemaExplorerInputError):
                SchemaExplorerService._validate_schema(bad)  # type: ignore[arg-type]

    async def test_validation_precedes_engine_resolution(
        self, monkeypatch
    ) -> None:
        """非法 schema 在解析 Engine 之前被拒绝（无 DB 也不会 RuntimeError）。"""
        def _boom():
            raise AssertionError("engine 不应被解析")

        monkeypatch.setattr(sx_module, "get_engine", _boom)
        service = SchemaExplorerService(engine=None)
        with pytest.raises(SchemaExplorerInputError):
            await service.inspect(schema='public"; DROP TABLE x')


class TestEngineMissing:
    async def test_no_engine_raises_runtime_error(self, monkeypatch) -> None:
        """DATABASE_URL 未配置（get_engine → None）→ RuntimeError。"""
        monkeypatch.setattr(sx_module, "get_engine", lambda: None)
        service = SchemaExplorerService(engine=None)
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            await service.inspect(schema="public")


class TestSQLConstantsAreReadOnly:
    """四条 SQL 常量必须是 SELECT-only metadata 查询（安全约束的静态锁）。"""

    @pytest.mark.parametrize(
        "sql_name",
        ["_SCHEMA_EXISTS_SQL", "_TABLES_SQL", "_COLUMNS_SQL", "_FOREIGN_KEYS_SQL"],
    )
    async def test_sql_is_select_only(self, sql_name: str) -> None:
        sql = getattr(sx_module, sql_name)
        stripped = sql.strip()
        assert stripped.upper().startswith("SELECT")
        # 禁止出现任何 DDL / DML 关键字（词边界匹配，避免误伤列名子串）
        forbidden = re.compile(
            r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT)\b",
            re.IGNORECASE,
        )
        assert not forbidden.search(stripped), f"{sql_name} 包含写操作关键字"

    async def test_schema_only_via_bind_parameter(self) -> None:
        """schema 只能通过 :schema 绑定参数传入，不存在 f-string / format 拼接。"""
        for sql_name in (
            "_SCHEMA_EXISTS_SQL",
            "_TABLES_SQL",
            "_COLUMNS_SQL",
            "_FOREIGN_KEYS_SQL",
        ):
            sql = getattr(sx_module, sql_name)
            assert ":schema" in sql
            assert "{" not in sql and "%s" not in sql


class TestDTO:
    async def test_dto_frozen(self) -> None:
        column = SchemaColumn(
            name="id",
            data_type="bigint",
            nullable=False,
            default=None,
            ordinal_position=1,
            is_primary_key=True,
            description=None,
        )
        with pytest.raises(Exception):  # FrozenInstanceError（dataclass 实现细节）
            column.name = "x"  # type: ignore[misc]

        table = SchemaTable(
            schema_name="public",
            name="t",
            description=None,
            columns=(column,),
            foreign_keys=(),
        )
        with pytest.raises(Exception):
            table.name = "x"  # type: ignore[misc]

    async def test_dto_nested_tuples(self) -> None:
        fk = SchemaForeignKey(
            source_schema="public",
            source_table="child",
            source_column="parent_id",
            target_schema="public",
            target_table="parent",
            target_column="id",
        )
        column = SchemaColumn(
            name="id", data_type="bigint", nullable=False, default=None,
            ordinal_position=1, is_primary_key=True, description=None,
        )
        table = SchemaTable(
            schema_name="public", name="t", description=None,
            columns=(column,), foreign_keys=(fk,),
        )
        schema = DatabaseSchema(schema_name="public", tables=(table,))
        assert isinstance(schema.tables, tuple)
        assert isinstance(table.columns, tuple)
        assert isinstance(table.foreign_keys, tuple)

    async def test_empty_schema_dto_convention(self) -> None:
        """空数据库约定：tables 为空 tuple（可复用于 falsy 判断）。"""
        schema = DatabaseSchema(schema_name="public", tables=())
        assert schema.tables == ()
        assert not schema.tables


# ============================================================
# DB 集成测试（RUN_DB_TESTS=1）
# ============================================================

_TEST_SCHEMA = "schema_explorer_test"
_EMPTY_SCHEMA = "schema_explorer_empty_test"

_PARENT_DDL = """
CREATE TABLE {schema}.sx_parent (
    id         BIGINT PRIMARY KEY,
    name       VARCHAR(50) NOT NULL,
    note       TEXT,
    created_at TIMESTAMP DEFAULT now()
)
"""

_CHILD_DDL = """
CREATE TABLE {schema}.sx_child (
    id        BIGINT PRIMARY KEY,
    parent_id BIGINT NOT NULL REFERENCES {schema}.sx_parent(id),
    amount    NUMERIC(10,2)
)
"""


@pytest.fixture(scope="module")
def engine():
    """module-level：真实 Engine（复用项目全局配置）。"""
    from backend.app.db import reset_engine_cache
    from backend.app.db.session import get_engine

    reset_engine_cache()
    eng = get_engine()
    assert eng is not None, "DATABASE_URL 未配置；请配置后启用 RUN_DB_TESTS=1"
    yield eng


@pytest.fixture(scope="module")
def service(engine):
    return SchemaExplorerService(engine=engine)


@pytest.fixture(scope="module")
def test_schema(engine):
    """module-level：独立测试 schema——建（含表 + COMMENT）→ 用 → 删。

    绝不触碰 public 下的 knowledge_document / knowledge_chunk。
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{_TEST_SCHEMA}"'))
        conn.execute(text(_PARENT_DDL.format(schema=_TEST_SCHEMA)))
        conn.execute(text(_CHILD_DDL.format(schema=_TEST_SCHEMA)))
        # 注：PostgreSQL 的 COMMENT DDL 不支持 bind parameter，
        # 这里内联的是测试文件内的固定字面量（非用户输入），无注入面。
        conn.execute(
            text(f"COMMENT ON TABLE \"{_TEST_SCHEMA}\".sx_parent IS '测试父表'")
        )
        conn.execute(
            text(f"COMMENT ON COLUMN \"{_TEST_SCHEMA}\".sx_parent.name IS '父表名称'")
        )
    yield _TEST_SCHEMA
    with engine.begin() as conn:
        conn.execute(
            text(f'DROP SCHEMA IF EXISTS "{_TEST_SCHEMA}" CASCADE')
        )


@requires_db
class TestEmptySchema:
    async def test_schema_without_tables_returns_empty_tuple(
        self, engine, service
    ) -> None:
        """空数据库场景：schema 存在但无任何表 → tables=()。"""
        from sqlalchemy import text

        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{_EMPTY_SCHEMA}"'))
        try:
            result = await service.inspect(schema=_EMPTY_SCHEMA)
            assert result.schema_name == _EMPTY_SCHEMA
            assert result.tables == ()
        finally:
            with engine.begin() as conn:
                conn.execute(
                    text(f'DROP SCHEMA IF EXISTS "{_EMPTY_SCHEMA}" CASCADE')
                )


@requires_db
class TestSingleTable:
    async def test_table_and_columns(self, service, test_schema) -> None:
        result = await service.inspect(schema=test_schema)
        by_name = {t.name: t for t in result.tables}
        assert set(by_name) == {"sx_parent", "sx_child"}

        parent = by_name["sx_parent"]
        assert parent.schema_name == test_schema
        assert [c.name for c in parent.columns] == [
            "id", "name", "note", "created_at",
        ]
        types = {c.name: c.data_type for c in parent.columns}
        assert types["id"] == "bigint"
        assert types["name"] == "character varying(50)"
        assert types["note"] == "text"
        assert types["created_at"] == "timestamp without time zone"

        nullable = {c.name: c.nullable for c in parent.columns}
        assert nullable["id"] is False
        assert nullable["name"] is False
        assert nullable["note"] is True

        defaults = {c.name: c.default for c in parent.columns}
        assert defaults["id"] is None
        assert defaults["created_at"] == "now()"

        positions = [c.ordinal_position for c in parent.columns]
        assert positions == [1, 2, 3, 4]

    async def test_primary_key_flags(self, service, test_schema) -> None:
        result = await service.inspect(schema=test_schema)
        by_name = {t.name: t for t in result.tables}

        parent_pk = {c.name: c.is_primary_key for c in by_name["sx_parent"].columns}
        assert parent_pk["id"] is True
        assert parent_pk["name"] is False

        child_pk = {c.name: c.is_primary_key for c in by_name["sx_child"].columns}
        assert child_pk["id"] is True
        assert child_pk["parent_id"] is False

    async def test_foreign_key(self, service, test_schema) -> None:
        result = await service.inspect(schema=test_schema)
        by_name = {t.name: t for t in result.tables}

        child_fks = by_name["sx_child"].foreign_keys
        assert len(child_fks) == 1
        fk = child_fks[0]
        assert fk.source_schema == test_schema
        assert fk.source_table == "sx_child"
        assert fk.source_column == "parent_id"
        assert fk.target_schema == test_schema
        assert fk.target_table == "sx_parent"
        assert fk.target_column == "id"

        # parent 表自身没有以它为 source 的外键
        assert by_name["sx_parent"].foreign_keys == ()

    async def test_comments(self, service, test_schema) -> None:
        result = await service.inspect(schema=test_schema)
        by_name = {t.name: t for t in result.tables}

        parent = by_name["sx_parent"]
        assert parent.description == "测试父表"
        descriptions = {c.name: c.description for c in parent.columns}
        assert descriptions["name"] == "父表名称"
        assert descriptions["note"] is None  # 无 comment → None，不让 AI 猜

        child = by_name["sx_child"]
        assert child.description is None

    async def test_result_dto_immutable_no_orm(self, service, test_schema) -> None:
        """结果 DTO：不可变、类型稳定、不暴露 ORM / Row 对象。"""
        result = await service.inspect(schema=test_schema)
        assert type(result) is DatabaseSchema
        for table in result.tables:
            assert type(table) is SchemaTable
            for column in table.columns:
                assert type(column) is SchemaColumn
                assert not hasattr(column, "_sa_instance_state")
            for fk in table.foreign_keys:
                assert type(fk) is SchemaForeignKey
        with pytest.raises(Exception):
            result.tables[0].name = "hacked"  # type: ignore[misc]


@requires_db
class TestPublicSchema:
    async def test_public_schema_business_tables(self, service) -> None:
        """真实 public schema：只含业务表，绝不暴露系统表。"""
        result = await service.inspect(schema="public")
        assert result.schema_name == "public"
        names = {t.name for t in result.tables}
        # 系统表绝不出现
        assert not any(n.startswith("pg_") for n in names)
        # 当前库的业务表（Phase 3.7.1 前置调研确认存在）
        assert {"knowledge_document", "knowledge_chunk"} <= names

        doc = next(t for t in result.tables if t.name == "knowledge_document")
        doc_cols = {c.name: c for c in doc.columns}
        assert doc_cols["id"].is_primary_key is True
        assert doc_cols["id"].data_type == "bigint"

    async def test_default_schema_equals_public(self, service) -> None:
        """schema=None → DEFAULT_SCHEMA(public)，与显式传参结果一致。"""
        assert DEFAULT_SCHEMA == "public"
        default_result = await service.inspect()
        public_result = await service.inspect(schema="public")
        assert {t.name for t in default_result.tables} == {
            t.name for t in public_result.tables
        }


@requires_db
class TestNonExistentSchema:
    async def test_unknown_schema_raises(self, service) -> None:
        with pytest.raises(SchemaNotFoundError) as exc_info:
            await service.inspect(schema="no_such_schema_9f3b")
        assert exc_info.value.schema == "no_such_schema_9f3b"
        # 错误信息不泄露 DATABASE_URL / 密码
        assert "postgresql" not in str(exc_info.value).lower()
        assert "@" not in str(exc_info.value)


@requires_db
class TestSQLInjectionGuard:
    async def test_injection_schema_rejected_and_business_tables_intact(
        self, engine, service
    ) -> None:
        """注入串在 SQL 执行前被拒绝；业务表完好无损（零 DDL/DML 执行）。"""
        evil = 'public"; DROP TABLE knowledge_document'
        with pytest.raises(SchemaExplorerInputError):
            await service.inspect(schema=evil)

        # 验证：knowledge_document 仍然存在（未执行任何 DDL）
        from sqlalchemy import text

        with engine.connect() as conn:
            exists = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' "
                    "AND table_name = 'knowledge_document'"
                )
            ).first()
        assert exists is not None


@requires_db
class TestErrorWrapping:
    async def test_db_error_wrapped_without_connection_details(
        self, engine
    ) -> None:
        """查询失败包装为 SchemaExplorerError，只含异常类名，不含连接串。"""

        class _BrokenEngine:
            def connect(self):
                raise RuntimeError("boom")

        service = SchemaExplorerService(engine=_BrokenEngine())  # type: ignore[arg-type]
        # RuntimeError 不是 SQLAlchemyError → 原样上抛（非吞掉）
        with pytest.raises(RuntimeError):
            await service.inspect(schema="public")

        from sqlalchemy.exc import OperationalError

        class _BrokenSaEngine:
            def connect(self):
                raise OperationalError(
                    "stmt", {}, Exception("connect to postgresql://u:secret@h failed")
                )

        service2 = SchemaExplorerService(engine=_BrokenSaEngine())  # type: ignore[arg-type]
        with pytest.raises(SchemaExplorerError) as exc_info:
            await service2.inspect(schema="public")
        message = str(exc_info.value)
        assert "OperationalError" in message  # 只有异常类名
        assert "secret" not in message  # 不泄露原始消息（含密码）
        assert "postgresql://" not in message


__all__ = [
    "TestSchemaValidation",
    "TestEngineMissing",
    "TestSQLConstantsAreReadOnly",
    "TestDTO",
    "TestEmptySchema",
    "TestSingleTable",
    "TestPublicSchema",
    "TestNonExistentSchema",
    "TestSQLInjectionGuard",
    "TestErrorWrapping",
]
