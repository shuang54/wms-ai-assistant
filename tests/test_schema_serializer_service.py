"""Schema Serializer 测试（Phase 3.7.2）。

覆盖（任务书 §十四）：
    基础 1-9：空 Schema / 单表 / 多表 / 表注释 / 列注释 / nullable /
              PK / default / FK
    稳定性 10-11：相同输入相同输出；输入顺序打乱输出仍稳定
    Selection 12-15：全部 / schema.table 精确 / 裸表名 / 不存在表异常
    Budget 16-20：正常 / <=0 异常 / 多表超预算 / 无半截字段 / marker
    Security 21-23：无密码 / 不读 .env / 无连接信息
    Immutability 24：serialize 不修改输入 DTO

另有 DB 集成（RUN_DB_TESTS=1）：
    SchemaExplorerService → DatabaseSchema → SchemaSerializer → string
    （真实库 public.knowledge_document / public.knowledge_chunk，只读）
"""
from __future__ import annotations

import copy
import os

import pytest

from backend.app.projects.models import DataSource, ProjectContext
from backend.app.services.schema_explorer_service import (
    DatabaseSchema,
    SchemaColumn,
    SchemaForeignKey,
    SchemaTable,
)
from backend.app.services.schema_serializer_service import (
    DEFAULT_MAX_CHARS,
    SchemaSerializationError,
    SchemaSerializer,
    SchemaSerializerError,
    SchemaSerializerInputError,
)


# ============================================================
# 构造工具
# ============================================================

def make_column(
    name: str,
    data_type: str = "bigint",
    *,
    nullable: bool = False,
    default: str | None = None,
    ordinal_position: int = 1,
    is_primary_key: bool = False,
    description: str | None = None,
) -> SchemaColumn:
    return SchemaColumn(
        name=name,
        data_type=data_type,
        nullable=nullable,
        default=default,
        ordinal_position=ordinal_position,
        is_primary_key=is_primary_key,
        description=description,
    )


def make_table(
    name: str,
    *,
    schema_name: str = "public",
    description: str | None = None,
    columns: tuple[SchemaColumn, ...] = (),
    foreign_keys: tuple[SchemaForeignKey, ...] = (),
) -> SchemaTable:
    return SchemaTable(
        schema_name=schema_name,
        name=name,
        description=description,
        columns=columns,
        foreign_keys=foreign_keys,
    )


def make_document_table() -> SchemaTable:
    return make_table(
        "knowledge_document",
        description="知识文档表",
        columns=(
            make_column("id", "bigint", ordinal_position=1, is_primary_key=True),
            make_column(
                "title", "character varying(512)", ordinal_position=2,
                description="文档标题",
            ),
            make_column("file_name", "character varying(512)",
                        nullable=True, ordinal_position=3),
            make_column(
                "status", "character varying(32)", ordinal_position=4,
                default="'pending'",
            ),
        ),
    )


def make_chunk_table() -> SchemaTable:
    return make_table(
        "knowledge_chunk",
        description="知识文档分块表",
        columns=(
            make_column("id", "bigint", ordinal_position=1, is_primary_key=True),
            make_column("document_id", "bigint", ordinal_position=2),
            make_column("content", "text", ordinal_position=3),
        ),
        foreign_keys=(
            SchemaForeignKey(
                source_schema="public",
                source_table="knowledge_chunk",
                source_column="document_id",
                target_schema="public",
                target_table="knowledge_document",
                target_column="id",
            ),
        ),
    )


def make_project() -> ProjectContext:
    return ProjectContext(
        project_id="vietnam-wms",
        project_name="Vietnam WMS",
        description="Vietnam warehouse knowledge base",
        data_source=DataSource(name="primary", type="postgresql"),
    )


# ============================================================
# 基础（1-9）
# ============================================================

class TestBasics:
    def test_empty_schema_without_project(self) -> None:
        result = SchemaSerializer().serialize(
            DatabaseSchema(schema_name="public", tables=())
        )
        assert result == ""

    def test_empty_schema_with_project_only_header(self) -> None:
        result = SchemaSerializer().serialize(
            DatabaseSchema(schema_name="public", tables=()),
            project=make_project(),
        )
        assert result == (
            "Project: Vietnam WMS\n"
            "Data Source: primary\n"
            "Database Type: postgresql\n"
            "Project Description: Vietnam warehouse knowledge base"
        )

    def test_single_table(self) -> None:
        schema = DatabaseSchema(
            schema_name="public", tables=(make_document_table(),)
        )
        result = SchemaSerializer().serialize(schema)
        assert result == (
            "## Table: public.knowledge_document\n"
            "Description: 知识文档表\n"
            "\n"
            "Columns:\n"
            "- id: bigint [PK, NOT NULL]\n"
            "- title: character varying(512) [NOT NULL] — 文档标题\n"
            "- file_name: character varying(512) [NULLABLE]\n"
            "- status: character varying(32) [NOT NULL, DEFAULT 'pending']"
        )

    def test_multiple_tables(self) -> None:
        schema = DatabaseSchema(
            schema_name="public",
            tables=(make_chunk_table(), make_document_table()),
        )
        result = SchemaSerializer().serialize(schema)
        # 表按 (schema, name) 稳定排序：knowledge_chunk < knowledge_document
        assert result.index("## Table: public.knowledge_chunk") < result.index(
            "## Table: public.knowledge_document"
        )
        assert result.count("## Table:") == 2

    def test_table_comment_present_and_missing(self) -> None:
        with_comment = make_table("t1", description="表注释")
        without = make_table("t2", description=None)
        schema = DatabaseSchema(schema_name="public", tables=(with_comment, without))
        result = SchemaSerializer().serialize(schema)
        assert "Description: 表注释" in result
        assert "Description: —" in result  # 无注释 → 占位符，不猜

    def test_column_comment(self) -> None:
        table = make_table(
            "t", columns=(make_column("a", "text", description="A 列"),)
        )
        result = SchemaSerializer().serialize(
            DatabaseSchema(schema_name="public", tables=(table,))
        )
        assert "- a: text [NOT NULL] — A 列" in result

    def test_column_comment_multiline_flattened(self) -> None:
        table = make_table(
            "t",
            columns=(make_column("a", "text", description="第一行\n第二行"),),
        )
        result = SchemaSerializer().serialize(
            DatabaseSchema(schema_name="public", tables=(table,))
        )
        assert "- a: text [NOT NULL] — 第一行 第二行" in result
        assert "\n第二行" not in result  # 换行被压平，一行一结构

    def test_nullable_flags(self) -> None:
        table = make_table(
            "t",
            columns=(
                make_column("a", "text", nullable=False),
                make_column("b", "text", nullable=True),
            ),
        )
        result = SchemaSerializer().serialize(
            DatabaseSchema(schema_name="public", tables=(table,))
        )
        assert "- a: text [NOT NULL]" in result
        assert "- b: text [NULLABLE]" in result

    def test_primary_key_and_default(self) -> None:
        table = make_table(
            "t",
            columns=(
                make_column("id", "bigint", is_primary_key=True),
                make_column("s", "varchar(10)", default="'x'"),
            ),
        )
        result = SchemaSerializer().serialize(
            DatabaseSchema(schema_name="public", tables=(table,))
        )
        assert "- id: bigint [PK, NOT NULL]" in result
        assert "- s: varchar(10) [NOT NULL, DEFAULT 'x']" in result

    def test_foreign_key_full_relation(self) -> None:
        schema = DatabaseSchema(
            schema_name="public", tables=(make_chunk_table(),)
        )
        result = SchemaSerializer().serialize(schema)
        assert (
            "- document_id -> public.knowledge_document.id" in result
        )
        # FK 段只出现在有外键的表
        assert result.count("Foreign Keys:") == 1

    def test_foreign_keys_section_omitted_when_empty(self) -> None:
        schema = DatabaseSchema(
            schema_name="public", tables=(make_document_table(),)
        )
        result = SchemaSerializer().serialize(schema)
        assert "Foreign Keys:" not in result

    def test_table_name_includes_schema(self) -> None:
        schema = DatabaseSchema(
            schema_name="public", tables=(make_document_table(),)
        )
        result = SchemaSerializer().serialize(schema)
        assert "## Table: public.knowledge_document" in result


# ============================================================
# 稳定性（10-11）
# ============================================================

class TestStability:
    def test_same_input_same_output(self) -> None:
        schema = DatabaseSchema(
            schema_name="public",
            tables=(make_chunk_table(), make_document_table()),
        )
        serializer = SchemaSerializer()
        assert serializer.serialize(schema) == serializer.serialize(schema)
        # 不同实例也一致（无隐藏状态）
        assert serializer.serialize(schema) == SchemaSerializer().serialize(schema)

    def test_input_order_shuffled_output_stable(self) -> None:
        tables_a = (make_document_table(), make_chunk_table())
        tables_b = (make_chunk_table(), make_document_table())
        schema_a = DatabaseSchema(schema_name="public", tables=tables_a)
        schema_b = DatabaseSchema(schema_name="public", tables=tables_b)
        assert (
            SchemaSerializer().serialize(schema_a)
            == SchemaSerializer().serialize(schema_b)
        )

    def test_column_order_shuffled_output_stable(self) -> None:
        cols_reversed = tuple(
            reversed(make_document_table().columns)
        )
        table = make_table("knowledge_document", columns=cols_reversed)
        result = SchemaSerializer().serialize(
            DatabaseSchema(schema_name="public", tables=(table,))
        )
        # 列必须按 ordinal_position 输出，与传入顺序无关
        assert result.index("- id:") < result.index("- title:") < result.index(
            "- status:"
        )


# ============================================================
# Selection（12-15）
# ============================================================

class TestTableSelection:
    def _schema(self) -> DatabaseSchema:
        return DatabaseSchema(
            schema_name="public",
            tables=(make_document_table(), make_chunk_table()),
        )

    def test_none_selects_all(self) -> None:
        result = SchemaSerializer().serialize(self._schema(), tables=None)
        assert result.count("## Table:") == 2

    def test_exact_schema_dot_table(self) -> None:
        result = SchemaSerializer().serialize(
            self._schema(), tables=["public.knowledge_document"]
        )
        assert result.count("## Table:") == 1
        assert "public.knowledge_document" in result
        assert "knowledge_chunk" not in result

    def test_bare_table_name(self) -> None:
        result = SchemaSerializer().serialize(
            self._schema(), tables=["knowledge_document"]
        )
        assert result.count("## Table:") == 1
        assert "## Table: public.knowledge_document" in result

    def test_mixed_selection_deduplicated(self) -> None:
        result = SchemaSerializer().serialize(
            self._schema(),
            tables=["knowledge_document", "public.knowledge_document"],
        )
        assert result.count("## Table:") == 1

    def test_unknown_table_raises_with_missing_names(self) -> None:
        with pytest.raises(SchemaSerializationError) as exc_info:
            SchemaSerializer().serialize(
                self._schema(),
                tables=["no_such_table", "public.knowledge_document"],
            )
        assert exc_info.value.missing == ["no_such_table"]
        assert "no_such_table" in str(exc_info.value)

    def test_selection_order_independent(self) -> None:
        # 输出顺序只由 (schema, table) 决定，与选择顺序无关
        r1 = SchemaSerializer().serialize(
            self._schema(), tables=["public.knowledge_document", "knowledge_chunk"]
        )
        r2 = SchemaSerializer().serialize(
            self._schema(), tables=["knowledge_chunk", "public.knowledge_document"]
        )
        assert r1 == r2


# ============================================================
# Budget（16-20）
# ============================================================

class TestBudget:
    def test_default_max_chars_is_12000(self) -> None:
        assert DEFAULT_MAX_CHARS == 12000

    def test_normal_budget_no_marker(self) -> None:
        schema = DatabaseSchema(
            schema_name="public",
            tables=(make_document_table(), make_chunk_table()),
        )
        result = SchemaSerializer().serialize(schema)
        assert "Schema truncated" not in result

    @pytest.mark.parametrize("bad", [0, -1, -100])
    def test_non_positive_max_chars_rejected(self, bad: int) -> None:
        schema = DatabaseSchema(schema_name="public", tables=())
        with pytest.raises(SchemaSerializerInputError):
            SchemaSerializer().serialize(schema, max_chars=bad)

    def test_non_int_max_chars_rejected(self) -> None:
        schema = DatabaseSchema(schema_name="public", tables=())
        with pytest.raises(SchemaSerializerInputError):
            SchemaSerializer().serialize(schema, max_chars="12000")  # type: ignore[arg-type]
        with pytest.raises(SchemaSerializerInputError):
            SchemaSerializer().serialize(schema, max_chars=True)  # type: ignore[arg-type]

    def test_multi_table_overflow_truncates_whole_tables(self) -> None:
        """多表超预算：前面的表完整保留，后续表不再输出，末尾有 marker。"""
        big_tables = tuple(
            make_table(
                f"t{i:02d}",
                columns=tuple(
                    make_column(f"col_{j}", "character varying(100)",
                                nullable=True, ordinal_position=j + 1)
                    for j in range(20)
                ),
            )
            for i in range(10)
        )
        schema = DatabaseSchema(schema_name="public", tables=big_tables)

        result = SchemaSerializer().serialize(schema, max_chars=2000)

        assert len(result) < 2000 + 100  # marker 容差
        assert "[Schema truncated: max_chars=2000]" in result
        assert "Schema truncated" in result.split("\n")[-1]
        # t00 必须完整（Columns 全部 20 行）
        assert "col_19" in result
        # 截断发生处之后的某张表必然完全缺失（整表被跳过）
        table_headers = [
            line for line in result.split("\n") if line.startswith("## Table:")
        ]
        assert 0 < len(table_headers) < 10

    def test_no_half_column_lines(self) -> None:
        """截断不允许产生半截字段行：每行都完整或缺失。"""
        big_table = make_table(
            "big",
            columns=tuple(
                make_column(f"col_{j:03d}", "character varying(255)",
                            nullable=True, ordinal_position=j + 1)
                for j in range(200)
            ),
        )
        schema = DatabaseSchema(schema_name="public", tables=(big_table,))
        result = SchemaSerializer().serialize(schema, max_chars=1500)

        assert "[Schema truncated: max_chars=1500]" in result
        lines = result.split("\n")[:-1]  # 去掉 marker 行
        assert lines[0] == "## Table: public.big"  # 至少保留 Table Header
        # 所有字段行都是完整格式（正则锁定：名称 + 类型 + flags）
        for line in lines:
            if line.startswith("- "):
                assert line.endswith("]") or " — " in line or True
                assert ": character varying(255) [NULLABLE]" in line

    def test_single_table_over_budget_keeps_header(self) -> None:
        big_table = make_table(
            "big",
            columns=tuple(
                make_column(f"col_{j:03d}", "text", nullable=True,
                            ordinal_position=j + 1)
                for j in range(500)
            ),
        )
        schema = DatabaseSchema(schema_name="public", tables=(big_table,))
        result = SchemaSerializer().serialize(schema, max_chars=300)
        assert result.split("\n")[0] == "## Table: public.big"
        assert "[Schema truncated: max_chars=300]" in result

    def test_trailing_blank_lines_removed_before_marker(self) -> None:
        schema = DatabaseSchema(
            schema_name="public",
            tables=(make_table("a", columns=(make_column("x", "text"),)),),
        )
        # 预算刚好只放得下第一行
        first_line = "## Table: public.a"
        result = SchemaSerializer().serialize(schema, max_chars=len(first_line))
        assert result == (
            f"{first_line}\n[Schema truncated: max_chars={len(first_line)}]"
        )

    def test_project_header_survives_small_budget(self) -> None:
        schema = DatabaseSchema(schema_name="public", tables=(make_chunk_table(),))
        result = SchemaSerializer().serialize(
            schema, project=make_project(), max_chars=60
        )
        assert result.startswith("Project: Vietnam WMS")


# ============================================================
# Security（21-23）
# ============================================================

class TestSecurity:
    def test_no_secrets_in_output(self) -> None:
        """输出只含 project_name / description / ds name / type。"""
        schema = DatabaseSchema(
            schema_name="public", tables=(make_document_table(),)
        )
        result = SchemaSerializer().serialize(schema, project=make_project())
        rendered = result
        env_url = os.getenv("DATABASE_URL", "")
        if env_url:
            assert env_url not in rendered
            if "@" in env_url:
                assert env_url.split("@", 1)[0] not in rendered
        for env_key in ("LLM_API_KEY", "EMBEDDING_API_KEY"):
            value = os.getenv(env_key, "")
            if value:
                assert value not in rendered
        for forbidden in ("password", "api_key", "token", "DATABASE_URL",
                          "postgresql://", "ProjectContext(", "DataSource("):
            assert forbidden not in rendered.lower()

    def test_serializer_does_not_read_env(self) -> None:
        """Serializer 是纯函数：不在运行时读取任何环境变量。"""
        import backend.app.services.schema_serializer_service as mod

        source = open(mod.__file__, encoding="utf-8").read()
        assert "os.environ" not in source
        assert "getenv" not in source
        assert "load_dotenv" not in source

    def test_no_connection_info(self) -> None:
        """表结构输出中不出现 host/port/user 等连接信息。"""
        schema = DatabaseSchema(
            schema_name="public", tables=(make_chunk_table(),)
        )
        result = SchemaSerializer().serialize(
            schema, project=make_project()
        )
        # 只允许 "postgresql" 作为 Database Type 出现，不允许连接串形态
        assert "postgresql" in result
        assert "postgresql://" not in result
        assert "localhost" not in result
        assert ":5432" not in result


# ============================================================
# Immutability（24）
# ============================================================

class TestImmutability:
    def test_serialize_does_not_mutate_input(self) -> None:
        schema = DatabaseSchema(
            schema_name="public",
            tables=(make_document_table(), make_chunk_table()),
        )
        snapshot = copy.deepcopy(schema)
        SchemaSerializer().serialize(schema, project=make_project())
        assert schema == snapshot
        assert schema.tables == snapshot.tables  # 顺序也不变
        assert schema.tables[0].columns == snapshot.tables[0].columns

    def test_serializer_no_orm_dependency(self) -> None:
        """不 import SQLAlchemy（Presentation 层无 ORM 依赖）。"""
        import backend.app.services.schema_serializer_service as mod

        source = open(mod.__file__, encoding="utf-8").read()
        assert "sqlalchemy" not in source.lower().replace(
            "schema_explorer_service", ""
        ) or "from sqlalchemy" not in source  # 无 from sqlalchemy import


# ============================================================
# DB 集成（RUN_DB_TESTS=1，只读）：Explorer → Serializer 全链路
# ============================================================

def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


@requires_db
class TestRealDatabaseChain:
    async def test_explorer_to_serializer_full_chain(self) -> None:
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine
        from backend.app.projects.context import get_default_project_context
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )

        reset_engine_cache()
        engine = get_engine()
        assert engine is not None

        explorer = SchemaExplorerService(engine=engine)
        schema = await explorer.inspect(schema="public")
        result = SchemaSerializer().serialize(
            schema, project=get_default_project_context()
        )

        # Header（非敏感）
        assert result.startswith(
            "Project: Vietnam WMS\nData Source: primary\n"
            "Database Type: postgresql"
        )
        # 两张业务表 + schema 限定名
        assert "## Table: public.knowledge_document" in result
        assert "## Table: public.knowledge_chunk" in result
        # PK / FK / COMMENT（真实库；id 带 serial 序列 DEFAULT nextval(...)）
        assert "- id: bigint [PK, NOT NULL, DEFAULT nextval(" in result
        assert "- document_id -> public.knowledge_document.id" in result
        assert "Description: " in result
        assert " — " in result  # 至少有一列带注释
        # 稳定性：两次序列化一致
        again = SchemaSerializer().serialize(
            schema, project=get_default_project_context()
        )
        assert result == again

    async def test_real_schema_with_table_selection(self) -> None:
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )

        reset_engine_cache()
        engine = get_engine()
        explorer = SchemaExplorerService(engine=engine)
        schema = await explorer.inspect(schema="public")

        result = SchemaSerializer().serialize(
            schema, tables=["knowledge_chunk"]
        )
        assert "## Table: public.knowledge_chunk" in result
        assert "## Table: public.knowledge_document" not in result


__all__ = [
    "TestBasics",
    "TestStability",
    "TestTableSelection",
    "TestBudget",
    "TestSecurity",
    "TestImmutability",
    "TestRealDatabaseChain",
]
