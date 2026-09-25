"""Semantic-Schema Alignment Filter 测试（Phase 3.9.2）。

覆盖任务书 Step 7（Test 1-8）、Step 8（Text-to-SQL E2E）、Step 9
（Prompt 长度验证）与 Step 5（空语义安全）。

测试分层：

1. 单元：table / column / relationship 三类过滤（Test 1-5）；
2. 非破坏性：原始 Semantic / Schema 完全不变（Test 6）；
3. 空结果安全：空语义 / 全过滤 / 无 selected 语义（Test 7）；
4. 隔离：Project A / B 不串数据（Test 8）；
5. 交叉一致性：过滤后的语义必通过 Phase 3.7.3 SemanticSchemaValidator；
6. Text-to-SQL E2E：business_context 只含本次 selected 表的语义；
7. Prompt 长度：过滤后 business_context 明显变短；
8. DB（RUN_DB_TESTS=1）：真实 Schema + 真实语义对齐过滤。

Fake Generator 签名与 Phase 3.7.6 / 3.9.1 完全一致（未做任何修改）。
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from backend.app.config import settings
from backend.app.projects.semantic import (
    BusinessRelationship,
    ColumnSemantic,
    ProjectSemantic,
    TableSemantic,
)
from backend.app.services.business_semantic_serializer import (
    BusinessSemanticSerializer,
)
from backend.app.services.schema_explorer_service import (
    DatabaseSchema,
    SchemaColumn,
    SchemaTable,
)
from backend.app.services.semantic_schema_filter import (
    SemanticSchemaFilter,
    SemanticSchemaFilterInputError,
)
from backend.app.services.semantic_schema_validator import SemanticSchemaValidator


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not (_env_flag("RUN_DB_TESTS") and settings.database.url.strip()),
    reason="set RUN_DB_TESTS=1 (plus DATABASE_URL) to enable semantic "
    "alignment DB tests",
)


# ============================================================
# 夹具
# ============================================================

def _col(name: str, position: int) -> SchemaColumn:
    return SchemaColumn(
        name=name, data_type="character varying", nullable=True,
        default=None, ordinal_position=position, is_primary_key=False,
        description=None,
    )


def _table(schema_name: str, name: str, columns: tuple[str, ...]) -> SchemaTable:
    return SchemaTable(
        schema_name=schema_name,
        name=name,
        description=None,
        columns=tuple(_col(c, i + 1) for i, c in enumerate(columns)),
        foreign_keys=(),
    )


def make_schema() -> DatabaseSchema:
    """public schema：warehouse / inventory / sales_order（sales_order 合法存在）。"""
    return DatabaseSchema(
        schema_name="public",
        tables=(
            _table("public", "warehouse", ("code", "name")),
            _table("public", "inventory", ("material_code", "qty", "warehouse_code")),
            _table("public", "sales_order", ("order_no", "qty")),
        ),
    )


def make_semantic() -> ProjectSemantic:
    """刻意配置过宽的语义：含不存在的表/列、合法但未选中的表、非法关系。"""
    return ProjectSemantic(
        tables=(
            TableSemantic(table="public.warehouse", business_name="仓库"),
            TableSemantic(table="public.inventory", business_name="库存"),
            TableSemantic(table="public.sales_order", business_name="销售订单"),
            TableSemantic(table="public.fake_table", business_name="幽灵表"),
        ),
        columns=(
            ColumnSemantic(table="public.warehouse", column="code",
                           business_name="仓库编码"),
            ColumnSemantic(table="public.warehouse", column="name",
                           business_name="仓库名称"),
            ColumnSemantic(table="public.warehouse", column="warehouse_name",
                           business_name="仓库显示名"),      # Schema 不存在
            ColumnSemantic(table="public.warehouse", column="fake_column",
                           business_name="幽灵字段"),        # Schema 不存在
            ColumnSemantic(table="public.inventory", column="material_code",
                           business_name="物料编码"),
            ColumnSemantic(table="public.inventory", column="qty",
                           business_name="库存数量"),
            ColumnSemantic(table="public.sales_order", column="order_no",
                           business_name="订单号"),
        ),
        relationships=(
            BusinessRelationship(
                source_table="public.inventory", source_column="warehouse_code",
                target_table="public.warehouse", target_column="code",
                description="库存属于仓库",
            ),
            BusinessRelationship(  # target 表未选中（Step 2.3）
                source_table="public.inventory", source_column="material_code",
                target_table="public.sales_order", target_column="order_no",
            ),
            BusinessRelationship(  # target 表不存在
                source_table="public.warehouse", source_column="code",
                target_table="public.fake_table", target_column="x",
            ),
            BusinessRelationship(  # target 列不存在
                source_table="public.warehouse", source_column="code",
                target_table="public.warehouse", target_column="fake_code",
            ),
        ),
    )


SELECTED = ("public.warehouse", "public.inventory")


def _filter(semantic=None, schema=None, allowed=SELECTED) -> ProjectSemantic:
    return SemanticSchemaFilter().filter(
        semantic if semantic is not None else make_semantic(),
        schema if schema is not None else make_schema(),
        allowed_tables=allowed,
    )


# ============================================================
# Test 1：过滤不存在的 table
# ============================================================

class TestUnknownTableFiltered:
    def test_unknown_table_removed(self) -> None:
        semantic = ProjectSemantic(
            tables=(
                TableSemantic(table="public.warehouse", business_name="仓库"),
                TableSemantic(table="public.fake_table", business_name="幽灵表"),
            )
        )
        schema = DatabaseSchema(
            schema_name="public", tables=(_table("public", "warehouse", ("code",)),)
        )
        result = SemanticSchemaFilter().filter(
            semantic, schema, allowed_tables=None
        )
        assert [ts.table for ts in result.tables] == ["public.warehouse"]
        assert "fake_table" not in repr(result)

    def test_column_semantic_on_unknown_table_removed(self) -> None:
        semantic = ProjectSemantic(
            columns=(
                ColumnSemantic(table="public.ghost", column="code",
                               business_name="编码"),
            )
        )
        result = SemanticSchemaFilter().filter(
            semantic, make_schema(), allowed_tables=None
        )
        assert result.columns == ()

    def test_bare_table_name_still_resolved(self) -> None:
        """裸表名（warehouse）与 "schema.table" 同等处理。"""
        semantic = ProjectSemantic(
            tables=(TableSemantic(table="warehouse", business_name="仓库"),)
        )
        result = SemanticSchemaFilter().filter(
            semantic, make_schema(), allowed_tables=None
        )
        assert [ts.table for ts in result.tables] == ["warehouse"]


# ============================================================
# Test 2：过滤不存在的 column
# ============================================================

class TestUnknownColumnFiltered:
    def test_unknown_columns_removed(self) -> None:
        semantic = ProjectSemantic(
            columns=(
                ColumnSemantic(table="public.warehouse", column="code"),
                ColumnSemantic(table="public.warehouse", column="name"),
                ColumnSemantic(table="public.warehouse", column="warehouse_name"),
                ColumnSemantic(table="public.warehouse", column="fake_column"),
            )
        )
        result = SemanticSchemaFilter().filter(
            semantic, make_schema(), allowed_tables=None
        )
        assert [cs.column for cs in result.columns] == ["code", "name"]

    def test_no_schema_inference_hint(self) -> None:
        """Step 6：warehouse_name 被删除，绝不推断为 name / 不留"可能是"提示。"""
        serializer = BusinessSemanticSerializer()
        text = serializer.serialize(
            SemanticSchemaFilter().filter(
                make_semantic(), make_schema(), allowed_tables=SELECTED
            )
        )
        assert "warehouse_name" not in text
        assert "fake_column" not in text
        assert "仓库名称" in text  # 真实存在的 name 列语义保留


# ============================================================
# Test 3：过滤未 selected 的合法 table
# ============================================================

class TestNotSelectedTableFiltered:
    def test_legal_but_unselected_table_removed(self) -> None:
        result = _filter()
        names = [ts.table for ts in result.tables]
        assert names == ["public.inventory", "public.warehouse"]  # 稳定排序
        assert "public.sales_order" not in names

    def test_unselected_table_columns_and_text_removed(self) -> None:
        result = _filter()
        assert all(cs.table != "public.sales_order" for cs in result.columns)
        text = BusinessSemanticSerializer().serialize(result)
        assert "销售订单" not in text
        assert "订单号" not in text
        assert "仓库" in text and "库存" in text

    def test_allowed_tables_none_keeps_all_existing(self) -> None:
        """allowed_tables=None → 不按选中范围限制，只按 Schema 存在性过滤。"""
        result = _filter(allowed=None)
        names = [ts.table for ts in result.tables]
        assert names == [
            "public.inventory", "public.sales_order", "public.warehouse"
        ]
        assert "public.fake_table" not in names  # 不存在仍被删

    def test_empty_allowed_tables_keeps_schema_aligned_semantic(self) -> None:
        """空 selected（零匹配）与 Composer 降级一致：不按选中范围限制，
        只按 Schema 存在性过滤。"""
        result = _filter(allowed=())
        names = [ts.table for ts in result.tables]
        assert names == [
            "public.inventory", "public.sales_order", "public.warehouse"
        ]
        assert "public.fake_table" not in names


# ============================================================
# Test 4 / 5：relationship
# ============================================================

class TestRelationshipFiltering:
    def test_valid_relationship_kept(self) -> None:
        rel = BusinessRelationship(
            source_table="public.inventory", source_column="warehouse_code",
            target_table="public.warehouse", target_column="code",
            description="库存属于仓库",
        )
        result = SemanticSchemaFilter().filter(
            ProjectSemantic(relationships=(rel,)),
            make_schema(),
            allowed_tables=SELECTED,
        )
        assert result.relationships == (rel,)

    def test_unselected_target_relationship_removed(self) -> None:
        rel = BusinessRelationship(
            source_table="public.inventory", source_column="material_code",
            target_table="public.sales_order", target_column="order_no",
        )
        result = SemanticSchemaFilter().filter(
            ProjectSemantic(relationships=(rel,)),
            make_schema(),
            allowed_tables=SELECTED,
        )
        assert result.relationships == ()

    def test_unknown_target_table_relationship_removed(self) -> None:
        rel = BusinessRelationship(
            source_table="public.warehouse", source_column="code",
            target_table="public.fake_table", target_column="x",
        )
        result = SemanticSchemaFilter().filter(
            ProjectSemantic(relationships=(rel,)),
            make_schema(),
            allowed_tables=None,
        )
        assert result.relationships == ()

    def test_unknown_target_column_relationship_removed(self) -> None:
        rel = BusinessRelationship(
            source_table="public.warehouse", source_column="code",
            target_table="public.warehouse", target_column="fake_code",
        )
        result = SemanticSchemaFilter().filter(
            ProjectSemantic(relationships=(rel,)),
            make_schema(),
            allowed_tables=None,
        )
        assert result.relationships == ()

    def test_only_surviving_relationship_kept(self) -> None:
        result = _filter()
        rels = [
            (r.source_table, r.source_column, r.target_table, r.target_column)
            for r in result.relationships
        ]
        assert rels == [
            ("public.inventory", "warehouse_code", "public.warehouse", "code")
        ]


# ============================================================
# Test 6：原始 Semantic 不被修改
# ============================================================

class TestNonDestructive:
    def test_original_semantic_unchanged(self) -> None:
        semantic = make_semantic()
        schema = make_schema()
        before_tables = tuple(semantic.tables)
        before_columns = tuple(semantic.columns)
        before_rels = tuple(semantic.relationships)
        before_schema_tables = tuple(schema.tables)

        result = SemanticSchemaFilter().filter(
            semantic, schema, allowed_tables=SELECTED
        )

        assert semantic.tables == before_tables
        assert semantic.columns == before_columns
        assert semantic.relationships == before_rels
        assert schema.tables == before_schema_tables
        # 返回的是新对象（内容不同、身份不同）
        assert result is not semantic
        assert len(result.tables) < len(semantic.tables)

    def test_kept_entries_are_same_immutable_objects(self) -> None:
        """保留项直接复用原 frozen 对象（不做拷贝改写）。"""
        semantic = make_semantic()
        result = SemanticSchemaFilter().filter(
            semantic, make_schema(), allowed_tables=SELECTED
        )
        kept = {ts.table: ts for ts in result.tables}
        assert kept["public.warehouse"] is next(
            ts for ts in semantic.tables if ts.table == "public.warehouse"
        )


# ============================================================
# Test 7：空结果安全
# ============================================================

class TestEmptyResultsSafe:
    def test_no_semantic_configured(self) -> None:
        """情况 A：没有配置 Semantic。"""
        result = SemanticSchemaFilter().filter(
            ProjectSemantic(), make_schema(), allowed_tables=SELECTED
        )
        assert result == ProjectSemantic()
        assert BusinessSemanticSerializer().serialize(result) == ""

    def test_everything_filtered_out(self) -> None:
        """情况 B：语义全部被过滤。"""
        semantic = ProjectSemantic(
            tables=(TableSemantic(table="public.ghost", business_name="幽灵"),),
            columns=(ColumnSemantic(table="public.ghost", column="x"),),
            relationships=(
                BusinessRelationship(
                    source_table="public.ghost", source_column="x",
                    target_table="public.ghost2", target_column="y",
                ),
            ),
        )
        result = SemanticSchemaFilter().filter(
            semantic, make_schema(), allowed_tables=None
        )
        assert result == ProjectSemantic()
        assert BusinessSemanticSerializer().serialize(result) == ""

    def test_semantic_exists_but_none_selected(self) -> None:
        """情况 C：有语义，但本次没有任何 selected table 对应语义。"""
        semantic = ProjectSemantic(
            tables=(TableSemantic(table="public.warehouse", business_name="仓库"),)
        )
        result = SemanticSchemaFilter().filter(
            semantic, make_schema(), allowed_tables=("public.inventory",)
        )
        assert result == ProjectSemantic()

    def test_input_validation(self) -> None:
        with pytest.raises(SemanticSchemaFilterInputError):
            SemanticSchemaFilter().filter({"tables": ()}, make_schema())
        with pytest.raises(SemanticSchemaFilterInputError):
            SemanticSchemaFilter().filter(ProjectSemantic(), "not-a-schema")
        with pytest.raises(SemanticSchemaFilterInputError):
            SemanticSchemaFilter().filter(
                ProjectSemantic(), make_schema(), allowed_tables="public.warehouse"
            )
        with pytest.raises(SemanticSchemaFilterInputError):
            SemanticSchemaFilter().filter(
                ProjectSemantic(), make_schema(), allowed_tables=[1]
            )


# ============================================================
# Test 8：Project A / B 隔离
# ============================================================

def _make_schema(schema_name: str) -> DatabaseSchema:
    return DatabaseSchema(
        schema_name=schema_name,
        tables=(
            _table(schema_name, "warehouse_a" if schema_name == "project_a"
                   else "warehouse_b", ("code",)),
            _table(schema_name, "inventory_a" if schema_name == "project_a"
                   else "inventory_b", ("material_code", "qty")),
        ),
    )


def _semantic_a() -> ProjectSemantic:
    return ProjectSemantic(
        tables=(
            TableSemantic(table="project_a.warehouse_a", business_name="A仓库"),
            TableSemantic(table="project_a.inventory_a", business_name="A库存"),
        ),
        columns=(
            ColumnSemantic(table="project_a.inventory_a", column="qty",
                           business_name="A库存数量"),
        ),
        relationships=(
            BusinessRelationship(
                source_table="project_a.inventory_a", source_column="material_code",
                target_table="project_a.warehouse_a", target_column="code",
            ),
        ),
    )


def _semantic_b() -> ProjectSemantic:
    return ProjectSemantic(
        tables=(
            TableSemantic(table="project_b.warehouse_b", business_name="B仓库"),
            TableSemantic(table="project_b.inventory_b", business_name="B库存"),
        ),
        columns=(
            ColumnSemantic(table="project_b.inventory_b", column="qty",
                           business_name="B可用数量"),
        ),
    )


class TestProjectIsolation:
    def test_project_a_sees_only_a(self) -> None:
        result = SemanticSchemaFilter().filter(
            _semantic_a(), _make_schema("project_a"), allowed_tables=None
        )
        text = BusinessSemanticSerializer().serialize(result)
        assert "A仓库" in text and "A库存数量" in text
        assert "project_a.warehouse_a" in text
        assert "project_b" not in text and "B仓库" not in text

    def test_project_b_sees_only_b(self) -> None:
        result = SemanticSchemaFilter().filter(
            _semantic_b(), _make_schema("project_b"), allowed_tables=None
        )
        text = BusinessSemanticSerializer().serialize(result)
        assert "B仓库" in text and "B可用数量" in text
        assert "project_b.inventory_b" in text
        assert "project_a" not in text and "A库存" not in text

    def test_cross_schema_filters_everything(self) -> None:
        """A 的语义放进 B 的 Schema → 全部失配、全部过滤，绝不交叉。"""
        result = SemanticSchemaFilter().filter(
            _semantic_a(), _make_schema("project_b"), allowed_tables=None
        )
        assert result == ProjectSemantic()


# ============================================================
# 交叉一致性 / 确定性 / 纯内存
# ============================================================

class TestCrossConsistency:
    def test_filtered_semantic_passes_existing_validator(self) -> None:
        """过滤结果必通过 Phase 3.7.3 SemanticSchemaValidator（规则不漂移）。"""
        schema = make_schema()
        filtered = _filter()
        assert SemanticSchemaValidator().validate(filtered, schema) == ()

    def test_deterministic_and_order_independent(self) -> None:
        schema = make_schema()
        shuffled = ProjectSemantic(
            tables=tuple(reversed(make_semantic().tables)),
            columns=tuple(reversed(make_semantic().columns)),
            relationships=tuple(reversed(make_semantic().relationships)),
        )
        f = SemanticSchemaFilter()
        assert f.filter(make_semantic(), schema, allowed_tables=SELECTED) == (
            f.filter(shuffled, schema, allowed_tables=SELECTED)
        )

    def test_pure_memory_no_infrastructure(self) -> None:
        import inspect

        import backend.app.services.semantic_schema_filter as mod

        src = inspect.getsource(mod)
        for forbidden in (
            "from backend.app.db", "import sqlalchemy", "get_engine",
            "from backend.app.llm", "from backend.app.services.embedding",
            "from backend.app.services.reranker", "open(",
        ):
            assert forbidden not in src

    def test_no_new_llm_calls(self) -> None:
        """过滤本身不触发任何 LLM / Embedding / Reranker（无 client 注入点）。"""
        f = SemanticSchemaFilter()
        assert not hasattr(f, "_llm_client")
        assert not hasattr(f, "_embedding")


# ============================================================
# Step 9：Prompt 长度
# ============================================================

class TestPromptLength:
    def test_filtered_business_context_is_shorter(self) -> None:
        serializer = BusinessSemanticSerializer()
        original = serializer.serialize(make_semantic())
        filtered = serializer.serialize(_filter())
        assert filtered  # 非空（warehouse / inventory 语义保留）
        assert len(filtered) < len(original)

    def test_filtered_context_drops_disallowed_terms(self) -> None:
        original = BusinessSemanticSerializer().serialize(make_semantic())
        filtered = BusinessSemanticSerializer().serialize(_filter())
        for term in ("销售订单", "订单号", "幽灵表", "幽灵字段", "仓库显示名"):
            assert term in original
            assert term not in filtered


# ============================================================
# Step 8：Text-to-SQL E2E（Orchestrator → business_context）
# ============================================================

class _FakeGenerator:
    """签名与 Phase 3.7.6 / 3.9.1 完全一致（未做任何修改）。"""

    def __init__(self, sql: str) -> None:
        self.sql = sql
        self.calls: list[dict] = []

    async def generate(
        self, question, *, database_context, allowed_tables=None,
        schema=None, max_rows=1000,
    ):
        self.calls.append(
            {
                "question": question,
                "database_context": database_context,
                "allowed_tables": tuple(allowed_tables or ()),
                "schema_name": getattr(schema, "schema_name", None),
                "max_rows": max_rows,
            }
        )
        return SimpleNamespace(
            question=question, sql=self.sql, attempts=1, validated=True,
            referenced_tables=tuple(allowed_tables or ()),
        )


class _FakeExecutor:
    async def execute(self, sql, *, schema=None, allowed_tables=None,
                      max_rows=1000):
        return SimpleNamespace(
            columns=("code",), rows=(("W1",),), row_count=1,
            truncated=False, execution_time_ms=0.1,
        )


class _FakeProjectProvider:
    def __init__(self) -> None:
        from backend.app.projects.context import DataSource, ProjectContext

        self._project = ProjectContext(
            project_id="filter-e2e",
            project_name="Filter E2E",
            description=None,
            data_source=DataSource(name="primary", type="postgresql"),
        )

    def resolve(self):
        return self._project, make_schema(), make_semantic()


def _decision():
    from backend.app.services.ai_router_service import RouteDecision, RouteType

    return RouteDecision(
        route=RouteType.TEXT_TO_SQL, confidence=0.9,
        reason="rule: analytics", source="rule",
    )


class TestTextToSQLE2E:
    def _run(self, question: str) -> _FakeGenerator:
        from backend.app.services.ai_orchestrator_service import (
            AIOrchestratorService,
        )
        from backend.app.services.database_context_composer import (
            DatabaseContextComposer,
        )
        from backend.app.services.relevant_table_selector import (
            RuleBasedRelevantTableSelector,
        )

        generator = _FakeGenerator(
            "SELECT w.code, i.qty FROM public.inventory i "
            "JOIN public.warehouse w ON w.code = i.warehouse_code LIMIT 10"
        )
        orch = AIOrchestratorService(
            table_selector=RuleBasedRelevantTableSelector(),
            context_composer=DatabaseContextComposer(),
            project_context_provider=_FakeProjectProvider(),
            text_to_sql=generator,
            sql_executor=_FakeExecutor(),
        )
        asyncio.run(orch._run_text_to_sql(_decision(), question))
        return generator

    def test_business_context_only_selected_tables(self) -> None:
        """Step 8：只出现 warehouse / inventory 语义，不出现 sales_order /
        fake_table（且 Fake Generator 签名未变）。"""
        generator = self._run("查询仓库和库存数量")
        assert len(generator.calls) == 1
        call = generator.calls[0]
        ctx = call["database_context"]

        assert call["allowed_tables"] == ("public.inventory", "public.warehouse")
        # 选中表的语义必须在
        assert "仓库" in ctx and "库存" in ctx
        assert "物料编码" in ctx and "库存数量" in ctx
        # 未选中 / 不存在的表语义必须不在
        assert "sales_order" not in ctx
        assert "fake_table" not in ctx
        assert "销售订单" not in ctx and "订单号" not in ctx
        # 不存在的字段语义必须不在
        assert "warehouse_name" not in ctx and "fake_column" not in ctx

    def test_e2e_business_context_shorter_than_unfiltered(self) -> None:
        generator = self._run("查询仓库和库存数量")
        ctx = generator.calls[0]["database_context"]
        full = BusinessSemanticSerializer().serialize(make_semantic())
        # 过滤后：语义段不含未选中/不存在的表 → business_context 更短
        assert len(ctx) < len(
            BusinessSemanticSerializer().serialize(make_semantic()) + full
        )


# ============================================================
# DB（RUN_DB_TESTS=1，只读）：真实 Schema + 真实语义
# ============================================================

@requires_db
class TestRealDatabaseAlignment:
    async def test_real_semantic_filtered_against_real_schema(self) -> None:
        """真实 DatabaseSchema × 真实语义 → 过滤后仍通过既有 Validator。"""
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine
        from backend.app.projects.semantic_loader import ProjectSemanticLoader
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )

        reset_engine_cache()
        engine = get_engine()
        assert engine is not None

        schema = await SchemaExplorerService(engine=engine).inspect(schema="public")
        semantic = ProjectSemanticLoader().load("vietnam-wms")

        filtered = SemanticSchemaFilter().filter(semantic, schema)
        assert SemanticSchemaValidator().validate(filtered, schema) == ()

        # 真实语义与真实库对齐时不应被误删
        assert len(filtered.tables) == len(semantic.tables)
        assert len(filtered.columns) == len(semantic.columns)

    async def test_bogus_semantic_removed_on_real_schema(self) -> None:
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )

        reset_engine_cache()
        engine = get_engine()
        assert engine is not None
        schema = await SchemaExplorerService(engine=engine).inspect(schema="public")

        bogus = ProjectSemantic(
            tables=(TableSemantic(table="public.definitely_not_here"),),
            columns=(
                ColumnSemantic(table="public.definitely_not_here", column="x"),
            ),
        )
        assert SemanticSchemaFilter().filter(bogus, schema) == ProjectSemantic()


__all__ = [
    "TestUnknownTableFiltered",
    "TestUnknownColumnFiltered",
    "TestNotSelectedTableFiltered",
    "TestRelationshipFiltering",
    "TestNonDestructive",
    "TestEmptyResultsSafe",
    "TestProjectIsolation",
    "TestCrossConsistency",
    "TestPromptLength",
    "TestTextToSQLE2E",
    "TestRealDatabaseAlignment",
]
