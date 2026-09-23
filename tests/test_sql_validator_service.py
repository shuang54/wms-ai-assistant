"""SQL Validator 测试（Phase 3.7.5）。

覆盖任务书 §三十三 A-J 1-51 + §三十四恶意案例 + 真实库集成。

明确验证：Validator 不执行 SQL、不修改 SQL、不连数据库。
"""
from __future__ import annotations

import copy
import os

import pytest

from backend.app.services.schema_explorer_service import (
    DatabaseSchema,
    SchemaColumn,
    SchemaTable,
)
from backend.app.services.sql_validator_service import (
    DEFAULT_MAX_ROWS,
    SQLValidationCode,
    SQLValidationError,
    SQLValidationResult,
    SQLValidator,
    SQLValidatorInputError,
    SQLValidatorService,
)


# ============================================================
# 构造工具
# ============================================================

def make_table(
    name: str,
    columns: tuple[str, ...] = ("id",),
    *,
    schema_name: str = "public",
) -> SchemaTable:
    return SchemaTable(
        schema_name=schema_name,
        name=name,
        description=None,
        columns=tuple(
            SchemaColumn(
                name=c,
                data_type="bigint",
                nullable=False,
                default=None,
                ordinal_position=i + 1,
                is_primary_key=(c == "id"),
                description=None,
            )
            for i, c in enumerate(columns)
        ),
        foreign_keys=(),
    )


def make_schema(*, multi_schema: bool = False) -> DatabaseSchema:
    if multi_schema:
        return DatabaseSchema(
            schema_name="public",
            tables=(
                make_table("inventory", schema_name="a"),
                make_table("inventory", schema_name="b"),
            ),
        )
    return DatabaseSchema(
        schema_name="public",
        tables=(
            make_table("inventory", ("id", "material_code", "qty")),
            make_table("material", ("id", "code", "name")),
        ),
    )


def validate(
    sql: str,
    *,
    schema: DatabaseSchema | None = None,
    allowed_tables=None,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> SQLValidationResult:
    return SQLValidatorService().validate(
        sql, schema=schema, allowed_tables=allowed_tables, max_rows=max_rows
    )


def codes(result: SQLValidationResult) -> set[SQLValidationCode]:
    return {e.code for e in result.errors}


# ============================================================
# A. 基础（1-5）
# ============================================================

class TestBasics:
    def test_valid_select(self) -> None:
        result = validate(
            "SELECT id, material_code FROM public.inventory LIMIT 100",
            schema=make_schema(),
        )
        assert result.valid
        assert result.errors == ()
        assert result.referenced_tables == ("public.inventory",)
        assert result.normalized_sql is not None
        assert "SELECT" in result.normalized_sql.upper()

    def test_empty_sql(self) -> None:
        result = validate("")
        assert not result.valid
        assert codes(result) == {SQLValidationCode.EMPTY_SQL}
        assert result.normalized_sql is None

    def test_whitespace_sql(self) -> None:
        result = validate("   \n\t  ")
        assert not result.valid
        assert codes(result) == {SQLValidationCode.EMPTY_SQL}

    def test_invalid_sql(self) -> None:
        result = validate("SELECT FROM")
        assert not result.valid
        assert codes(result) == {SQLValidationCode.INVALID_SQL}

    def test_parser_error_not_raised(self) -> None:
        """parser 异常被转换为校验结果，不是 500。"""
        result = validate("THIS IS NOT SQL AT ALL ((((")
        assert not result.valid
        assert codes(result) == {SQLValidationCode.INVALID_SQL}


# ============================================================
# B. 写操作（6-15）
# ============================================================

class TestWriteOperations:
    @pytest.mark.parametrize("sql,code", [
        ("INSERT INTO public.inventory VALUES (1)", SQLValidationCode.NON_READ_ONLY),
        ("UPDATE public.inventory SET qty = 0", SQLValidationCode.NON_READ_ONLY),
        ("DELETE FROM public.inventory", SQLValidationCode.NON_READ_ONLY),
        ("MERGE INTO public.inventory t USING public.material s "
         "ON t.id = s.id WHEN MATCHED THEN UPDATE SET qty = 0",
         SQLValidationCode.NON_READ_ONLY),
        ("CREATE TABLE x (id int)", SQLValidationCode.DANGEROUS_OPERATION),
        ("ALTER TABLE public.inventory ADD COLUMN c int",
         SQLValidationCode.DANGEROUS_OPERATION),
        ("DROP TABLE public.inventory", SQLValidationCode.DANGEROUS_OPERATION),
        ("TRUNCATE TABLE public.inventory", SQLValidationCode.DANGEROUS_OPERATION),
    ])
    def test_write_rejected(self, sql: str, code: SQLValidationCode) -> None:
        result = validate(sql, schema=make_schema())
        assert not result.valid
        assert code in codes(result)

    def test_grant_rejected(self) -> None:
        result = validate("GRANT SELECT ON public.inventory TO alice")
        assert not result.valid
        assert codes(result) & {
            SQLValidationCode.DANGEROUS_OPERATION,
            SQLValidationCode.INVALID_SQL,  # parser 不支持时保守拒绝
        }

    def test_revoke_rejected(self) -> None:
        result = validate("REVOKE SELECT ON public.inventory FROM alice")
        assert not result.valid

    @pytest.mark.parametrize("sql", [
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        "START TRANSACTION",
    ])
    def test_transaction_control_rejected(self, sql: str) -> None:
        result = validate(sql)
        assert not result.valid


# ============================================================
# C. 多语句（16-18）
# ============================================================

class TestMultiStatement:
    def test_two_selects_rejected(self) -> None:
        result = validate(
            "SELECT * FROM public.inventory LIMIT 10; "
            "SELECT * FROM public.material LIMIT 10"
        )
        assert not result.valid
        assert SQLValidationCode.MULTI_STATEMENT in codes(result)

    def test_select_plus_delete_rejected(self) -> None:
        result = validate(
            "SELECT * FROM public.inventory LIMIT 10; "
            "DELETE FROM public.inventory"
        )
        assert not result.valid
        assert SQLValidationCode.MULTI_STATEMENT in codes(result)
        assert SQLValidationCode.NON_READ_ONLY in codes(result)

    def test_select_plus_drop_rejected(self) -> None:
        result = validate(
            "SELECT * FROM public.inventory LIMIT 10; "
            "DROP TABLE public.inventory"
        )
        assert not result.valid
        assert SQLValidationCode.DANGEROUS_OPERATION in codes(result)

    def test_trailing_semicolon_single_statement_ok(self) -> None:
        result = validate(
            "SELECT * FROM public.inventory LIMIT 10;", schema=make_schema()
        )
        assert result.valid


# ============================================================
# D. SELECT 特殊情况（19-25）
# ============================================================

class TestSelectSpecialCases:
    def test_select_into_rejected(self) -> None:
        result = validate(
            "SELECT * INTO new_table FROM public.inventory",
            schema=make_schema(),
        )
        assert not result.valid
        assert SQLValidationCode.DANGEROUS_OPERATION in codes(result)

    def test_readonly_cte_allowed(self) -> None:
        sql = (
            "WITH inv AS (SELECT * FROM public.inventory LIMIT 100) "
            "SELECT * FROM inv LIMIT 100"
        )
        result = validate(sql, schema=make_schema())
        assert result.valid
        # CTE 别名 inv 不算物理表
        assert result.referenced_tables == ("public.inventory",)

    def test_write_cte_rejected(self) -> None:
        sql = (
            "WITH x AS (DELETE FROM public.inventory RETURNING *) "
            "SELECT * FROM x LIMIT 100"
        )
        result = validate(sql, schema=make_schema())
        assert not result.valid
        assert SQLValidationCode.NON_READ_ONLY in codes(result)

    def test_readonly_subquery_allowed(self) -> None:
        sql = (
            "SELECT * FROM (SELECT * FROM public.inventory LIMIT 100) t "
            "LIMIT 100"
        )
        result = validate(sql, schema=make_schema())
        assert result.valid
        assert result.referenced_tables == ("public.inventory",)

    def test_aggregate_allowed(self) -> None:
        result = validate(
            "SELECT count(*) FROM public.inventory LIMIT 100",
            schema=make_schema(),
        )
        assert result.valid

    def test_group_by_allowed(self) -> None:
        result = validate(
            "SELECT material_code, sum(qty) FROM public.inventory "
            "GROUP BY material_code LIMIT 100",
            schema=make_schema(),
        )
        assert result.valid

    def test_having_allowed(self) -> None:
        result = validate(
            "SELECT material_code, count(*) FROM public.inventory "
            "GROUP BY material_code HAVING count(*) > 5 LIMIT 100",
            schema=make_schema(),
        )
        assert result.valid

    def test_join_allowed_all_tables_checked(self) -> None:
        result = validate(
            "SELECT i.id FROM public.inventory i "
            "JOIN public.material m ON i.material_code = m.code LIMIT 100",
            schema=make_schema(),
        )
        assert result.valid
        assert result.referenced_tables == (
            "public.inventory", "public.material",
        )

    def test_union_with_limit_allowed(self) -> None:
        result = validate(
            "SELECT id FROM public.inventory UNION "
            "SELECT id FROM public.material LIMIT 100",
            schema=make_schema(),
        )
        assert result.valid

    def test_subquery_outer_limit_required(self) -> None:
        """外层无 LIMIT：子查询的 LIMIT 不算数。"""
        sql = "SELECT * FROM (SELECT * FROM public.inventory LIMIT 100) t"
        result = validate(sql, schema=make_schema())
        assert not result.valid
        assert SQLValidationCode.ROW_LIMIT_REQUIRED in codes(result)


# ============================================================
# E. 表验证（26-34）
# ============================================================

class TestTableValidation:
    def test_existing_table_allowed(self) -> None:
        result = validate(
            "SELECT * FROM public.inventory LIMIT 10", schema=make_schema()
        )
        assert result.valid

    def test_unknown_table_rejected(self) -> None:
        result = validate(
            "SELECT * FROM public.not_exists LIMIT 10", schema=make_schema()
        )
        assert not result.valid
        assert SQLValidationCode.UNKNOWN_TABLE in codes(result)

    def test_allowed_tables_permit(self) -> None:
        result = validate(
            "SELECT * FROM public.inventory LIMIT 10",
            allowed_tables=("public.inventory",),
        )
        assert result.valid

    def test_allowed_tables_reject(self) -> None:
        result = validate(
            "SELECT * FROM public.customer LIMIT 10",
            allowed_tables=("public.inventory", "public.material"),
        )
        assert not result.valid
        assert SQLValidationCode.TABLE_NOT_ALLOWED in codes(result)

    def test_schema_and_allowed_both_required(self) -> None:
        """§十九：DatabaseSchema ∩ allowed_tables 双重满足。"""
        # 在 schema 中存在，但不在 allowed → 拒绝
        result = validate(
            "SELECT * FROM public.material LIMIT 10",
            schema=make_schema(),
            allowed_tables=("public.inventory",),
        )
        assert not result.valid
        assert SQLValidationCode.TABLE_NOT_ALLOWED in codes(result)
        # 同时满足 → 通过
        result2 = validate(
            "SELECT * FROM public.inventory LIMIT 10",
            schema=make_schema(),
            allowed_tables=("public.inventory", "public.material"),
        )
        assert result2.valid

    def test_bare_name_resolved_via_schema(self) -> None:
        """§十七：裸表名按 DatabaseSchema 唯一解析为 public.*。"""
        result = validate(
            "SELECT * FROM inventory LIMIT 10", schema=make_schema()
        )
        assert result.valid
        assert result.referenced_tables == ("public.inventory",)

    def test_schema_qualified_table(self) -> None:
        result = validate(
            "SELECT * FROM public.inventory LIMIT 10", schema=make_schema()
        )
        assert result.valid
        assert result.referenced_tables == ("public.inventory",)

    def test_schema_case_normalized(self) -> None:
        """§十六：PUBLIC.INVENTORY / Public.Inventory 不能绕过匹配。"""
        for sql in (
            "SELECT * FROM PUBLIC.INVENTORY LIMIT 10",
            "SELECT * FROM Public.Inventory LIMIT 10",
        ):
            result = validate(sql, schema=make_schema())
            assert result.valid, sql
            assert result.referenced_tables == ("public.inventory",), sql

    def test_case_normalized_against_allowed_tables(self) -> None:
        result = validate(
            "SELECT * FROM PUBLIC.INVENTORY LIMIT 10",
            allowed_tables=("public.inventory",),
        )
        assert result.valid

    def test_ambiguous_bare_name_rejected(self) -> None:
        """§十七：多 schema 同名裸表名 → 拒绝，不猜。"""
        result = validate(
            "SELECT * FROM inventory LIMIT 10",
            schema=make_schema(multi_schema=True),
        )
        assert not result.valid
        assert SQLValidationCode.UNSUPPORTED_SQL in codes(result)

    def test_bare_name_no_schema_no_allowed_kept_bare(self) -> None:
        """§二十：schema=None + allowed=None → 仅安全校验。"""
        result = validate("SELECT * FROM inventory LIMIT 10")
        assert result.valid
        assert result.referenced_tables == ("inventory",)

    def test_bare_name_resolved_via_allowed_unique_suffix(self) -> None:
        result = validate(
            "SELECT * FROM inventory LIMIT 10",
            allowed_tables=("public.inventory", "public.material"),
        )
        assert result.valid
        assert result.referenced_tables == ("public.inventory",)

    def test_bare_name_ambiguous_in_allowed_rejected(self) -> None:
        result = validate(
            "SELECT * FROM inventory LIMIT 10",
            allowed_tables=("a.inventory", "b.inventory"),
        )
        assert not result.valid
        assert SQLValidationCode.UNSUPPORTED_SQL in codes(result)

    def test_allowed_bare_name_entry_matches(self) -> None:
        """白名单写裸表名（无歧义后缀）也能匹配规范名。"""
        result = validate(
            "SELECT * FROM public.inventory LIMIT 10",
            allowed_tables=("inventory",),
        )
        assert result.valid


# ============================================================
# F. LIMIT（35-40）
# ============================================================

class TestLimit:
    def test_no_limit_rejected_not_modified(self) -> None:
        sql = "SELECT * FROM public.inventory"
        result = validate(sql, schema=make_schema())
        assert not result.valid
        assert SQLValidationCode.ROW_LIMIT_REQUIRED in codes(result)
        # 不自动改写：normalized_sql 不含注入的 LIMIT
        assert "LIMIT 1000" not in (result.normalized_sql or "")

    def test_limit_100_ok(self) -> None:
        result = validate(
            "SELECT * FROM public.inventory LIMIT 100", schema=make_schema()
        )
        assert result.valid

    def test_limit_equals_max_rows_ok(self) -> None:
        result = validate(
            "SELECT * FROM public.inventory LIMIT 1000", schema=make_schema()
        )
        assert result.valid

    def test_limit_exceeds_max_rows_rejected(self) -> None:
        result = validate(
            "SELECT * FROM public.inventory LIMIT 10001", schema=make_schema()
        )
        assert not result.valid
        assert SQLValidationCode.ROW_LIMIT_EXCEEDED in codes(result)

    def test_custom_max_rows(self) -> None:
        result = validate(
            "SELECT * FROM public.inventory LIMIT 500",
            schema=make_schema(),
            max_rows=100,
        )
        assert not result.valid
        assert SQLValidationCode.ROW_LIMIT_EXCEEDED in codes(result)

    def test_offset_with_limit_ok(self) -> None:
        result = validate(
            "SELECT * FROM public.inventory LIMIT 100 OFFSET 20",
            schema=make_schema(),
        )
        assert result.valid

    def test_offset_without_limit_rejected(self) -> None:
        result = validate(
            "SELECT * FROM public.inventory OFFSET 20", schema=make_schema()
        )
        assert not result.valid
        assert SQLValidationCode.ROW_LIMIT_REQUIRED in codes(result)

    def test_non_literal_limit_rejected(self) -> None:
        result = validate(
            "SELECT * FROM public.inventory LIMIT 10 + :n", schema=make_schema()
        )
        assert not result.valid
        assert SQLValidationCode.UNSUPPORTED_SQL in codes(result)

    def test_default_max_rows_is_1000(self) -> None:
        assert DEFAULT_MAX_ROWS == 1000


# ============================================================
# G. Dangerous Functions（41-44）
# ============================================================

class TestDangerousFunctions:
    def test_pg_sleep_rejected(self) -> None:
        result = validate("SELECT pg_sleep(10)")
        assert not result.valid
        assert SQLValidationCode.DANGEROUS_OPERATION in codes(result)

    @pytest.mark.parametrize("sql", [
        "SELECT pg_advisory_lock(1)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT dblink('host=x', 'SELECT 1')",
        "SELECT lo_import('/tmp/f')",
        "SELECT setval('seq', 1)",
        "SELECT pg_terminate_backend(123)",
    ])
    def test_other_known_dangerous_rejected(self, sql: str) -> None:
        result = validate(sql + " LIMIT 1") if "pg_sleep" not in sql \
            else validate(sql)
        assert not result.valid, sql
        assert SQLValidationCode.DANGEROUS_OPERATION in codes(result), sql

    def test_count_allowed(self) -> None:
        result = validate(
            "SELECT count(*) FROM public.inventory LIMIT 10",
            schema=make_schema(),
        )
        assert result.valid

    @pytest.mark.parametrize("sql", [
        "SELECT upper(name) FROM public.material LIMIT 10",
        "SELECT now() LIMIT 1",
        "SELECT max(qty) FROM public.inventory LIMIT 10",
    ])
    def test_ordinary_functions_allowed(self, sql: str) -> None:
        result = validate(sql, schema=make_schema())
        assert result.valid, sql

    def test_string_literal_delete_not_misjudged(self) -> None:
        """§二十九：字符串字面量中出现 DELETE 不影响 AST 判断。"""
        sql = "SELECT 'DELETE' AS message FROM public.inventory LIMIT 1"
        result = validate(sql, schema=make_schema())
        assert result.valid
        assert SQLValidationCode.NON_READ_ONLY not in codes(result)
        assert SQLValidationCode.DANGEROUS_OPERATION not in codes(result)

    def test_column_named_delete_not_misjudged(self) -> None:
        """别名 / 列名中的关键字同样不被文本搜索误判。"""
        schema = DatabaseSchema(
            schema_name="public",
            tables=(make_table("ops_log", ("id", "delete_flag")),),
        )
        result = validate(
            "SELECT delete_flag AS update FROM public.ops_log LIMIT 10",
            schema=schema,
        )
        assert result.valid


# ============================================================
# H. SQL 注释（45-46）
# ============================================================

class TestComments:
    def test_line_comment(self) -> None:
        sql = (
            "SELECT * FROM public.inventory -- 尾注\nLIMIT 100"
        )
        result = validate(sql, schema=make_schema())
        assert result.valid

    def test_block_comment(self) -> None:
        sql = "SELECT * FROM /* 块注 */ public.inventory LIMIT 100"
        result = validate(sql, schema=make_schema())
        assert result.valid

    def test_comment_with_drop_keyword_not_misjudged(self) -> None:
        sql = "SELECT * FROM public.inventory -- DROP TABLE x\nLIMIT 100"
        result = validate(sql, schema=make_schema())
        assert result.valid


# ============================================================
# I. 不可变（47-49）
# ============================================================

class TestImmutability:
    def test_schema_not_mutated(self) -> None:
        schema = make_schema()
        snapshot = copy.deepcopy(schema)
        validate(
            "SELECT * FROM public.inventory LIMIT 10", schema=schema
        )
        assert schema == snapshot

    def test_allowed_tables_input_not_mutated(self) -> None:
        allowed = ["public.inventory", "public.material"]
        validate(
            "SELECT * FROM public.inventory LIMIT 10",
            allowed_tables=allowed,
        )
        assert allowed == ["public.inventory", "public.material"]

    def test_original_sql_not_modified(self) -> None:
        sql = "select * from public.inventory limit 10"
        validate(sql, schema=make_schema())
        assert sql == "select * from public.inventory limit 10"

    def test_dto_frozen(self) -> None:
        result = validate("SELECT 1 LIMIT 1")
        err = SQLValidationError(SQLValidationCode.EMPTY_SQL, "x")
        with pytest.raises(Exception):
            result.valid = False  # type: ignore[misc]
        with pytest.raises(Exception):
            err.code = SQLValidationCode.INVALID_SQL  # type: ignore[misc]


# ============================================================
# J. 确定性（50-51）
# ============================================================

class TestDeterminism:
    def test_same_sql_same_result(self) -> None:
        sql = "SELECT * FROM public.inventory LIMIT 100"
        r1 = validate(sql, schema=make_schema())
        r2 = validate(sql, schema=make_schema())
        assert r1 == r2

    def test_referenced_tables_sorted(self) -> None:
        result = validate(
            "SELECT * FROM public.material m "
            "JOIN public.inventory i ON i.material_code = m.code LIMIT 10",
            schema=make_schema(),
        )
        assert result.referenced_tables == tuple(
            sorted(result.referenced_tables)
        )

    def test_referenced_tables_deduplicated(self) -> None:
        result = validate(
            "SELECT * FROM public.inventory a "
            "JOIN public.inventory b ON a.id = b.id LIMIT 10",
            schema=make_schema(),
        )
        assert result.referenced_tables == ("public.inventory",)


# ============================================================
# 输入校验
# ============================================================

class TestInputValidation:
    @pytest.mark.parametrize("kwargs", [
        {"schema": {"tables": []}},
        {"max_rows": 0},
        {"max_rows": -1},
        {"max_rows": "100"},
        {"max_rows": True},
    ])
    def test_invalid_kwargs_raise(self, kwargs) -> None:
        with pytest.raises(SQLValidatorInputError):
            validate("SELECT 1 LIMIT 1", **kwargs)

    def test_non_string_sql_raises(self) -> None:
        with pytest.raises(SQLValidatorInputError):
            validate(123)  # type: ignore[arg-type]

    def test_string_allowed_tables_rejected(self) -> None:
        with pytest.raises(SQLValidatorInputError):
            validate(
                "SELECT 1 LIMIT 1", allowed_tables="public.inventory"
            )

    def test_protocol_satisfied(self) -> None:
        validator: SQLValidator = SQLValidatorService()
        assert callable(validator.validate)


# ============================================================
# §三十四 恶意案例汇总
# ============================================================

class TestMaliciousCases:
    """任务书 §三十四：只有最后一条在正确条件下通过。"""

    SCHEMA = make_schema()
    ALLOWED = ("public.inventory", "public.material")

    def _v(self, sql: str) -> SQLValidationResult:
        return validate(
            sql, schema=self.SCHEMA, allowed_tables=self.ALLOWED
        )

    def test_stack_smashing_drop(self) -> None:
        result = self._v(
            "SELECT * FROM public.inventory; DROP TABLE public.inventory;"
        )
        assert not result.valid

    def test_select_then_delete(self) -> None:
        result = self._v(
            "SELECT * FROM public.inventory;\nDELETE FROM public.inventory;"
        )
        assert not result.valid

    def test_string_delete_literal(self) -> None:
        result = self._v(
            "SELECT 'DELETE' AS message FROM public.inventory LIMIT 1"
        )
        assert result.valid  # 字面量不误判

    def test_limit_10000_exceeds(self) -> None:
        result = self._v("SELECT * FROM public.inventory LIMIT 10000")
        assert not result.valid
        assert SQLValidationCode.ROW_LIMIT_EXCEEDED in codes(result)

    def test_unknown_table(self) -> None:
        result = self._v("SELECT * FROM public.not_exists LIMIT 100")
        assert not result.valid
        assert SQLValidationCode.UNKNOWN_TABLE in codes(result)

    def test_final_valid_case(self) -> None:
        result = self._v("SELECT * FROM public.inventory LIMIT 100")
        assert result.valid
        assert result.errors == ()


# ============================================================
# 安全静态检查
# ============================================================

class TestSecurity:
    def test_no_db_or_session_dependency(self) -> None:
        import inspect
        import backend.app.services.sql_validator_service as mod
        source = inspect.getsource(mod)
        assert "sqlalchemy" not in source.lower()
        assert "from backend.app.db" not in source
        assert "create_engine" not in source
        assert "get_engine" not in source

    def test_no_llm_dependency(self) -> None:
        import inspect
        import backend.app.services.sql_validator_service as mod
        source = inspect.getsource(mod)
        assert "deepseek" not in source.lower()
        assert "from backend.app.services.llm" not in source
        assert "embedding" not in source.lower()


# ============================================================
# 真实库集成（RUN_DB_TESTS=1，只读 metadata，不执行被验证 SQL）
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
    async def test_valid_query_against_real_schema(self) -> None:
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )

        reset_engine_cache()
        engine = get_engine()
        assert engine is not None
        schema = await SchemaExplorerService(engine=engine).inspect(schema="public")

        result = validate(
            "SELECT id, title FROM public.knowledge_document LIMIT 10",
            schema=schema,
        )
        assert result.valid, result.errors
        assert result.referenced_tables == ("public.knowledge_document",)

    async def test_unknown_table_rejected_on_real_schema(self) -> None:
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )

        reset_engine_cache()
        engine = get_engine()
        schema = await SchemaExplorerService(engine=engine).inspect(schema="public")

        result = validate(
            "SELECT * FROM public.not_exists LIMIT 10", schema=schema
        )
        assert not result.valid
        assert SQLValidationCode.UNKNOWN_TABLE in codes(result)

    async def test_allowed_tables_narrowing_on_real_schema(self) -> None:
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )

        reset_engine_cache()
        engine = get_engine()
        schema = await SchemaExplorerService(engine=engine).inspect(schema="public")

        ok = validate(
            "SELECT id FROM public.knowledge_document LIMIT 5",
            schema=schema,
            allowed_tables=("public.knowledge_document",),
        )
        assert ok.valid

        blocked = validate(
            "SELECT id FROM public.knowledge_chunk LIMIT 5",
            schema=schema,
            allowed_tables=("public.knowledge_document",),
        )
        assert not blocked.valid
        assert SQLValidationCode.TABLE_NOT_ALLOWED in codes(blocked)

    async def test_write_rejected_on_real_schema(self) -> None:
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )

        reset_engine_cache()
        engine = get_engine()
        schema = await SchemaExplorerService(engine=engine).inspect(schema="public")

        result = validate("DELETE FROM public.knowledge_document", schema=schema)
        assert not result.valid
        assert SQLValidationCode.NON_READ_ONLY in codes(result)
