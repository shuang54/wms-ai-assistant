"""Relevant Table Selector 测试（Phase 3.7.4）。

覆盖（任务书 §二十二）：
    基础 1-5：空 schema / 单表 / 多表 / 无 semantic / 有 semantic
    匹配 6-12：table name / business name / alias / description /
               column name / column business name / column alias
    中文 13-15：substring / 中英混合 / 大小写不敏感
    排序 16-18：score DESC / 同分按表名 ASC / 结果稳定
    去重 19-20：同表多命中只出现一次 / matched_terms 去重
    top_k 21-24：1 / 5 / 超过候选数 / 非法值
    零匹配 25-26：空 selections / 不默认返回全部表
    Schema 事实 27：Semantic 有但 Schema 没有的表不返回
    不可变 28-30：不改 DatabaseSchema / 不改 ProjectSemantic / 幂等
    安全：静态断言无 SQLAlchemy / env / LLM / Embedding 依赖
    DB 集成（RUN_DB_TESTS=1）：Explorer + Loader + Selector 全链路
"""
from __future__ import annotations

import copy
import os
import re

import pytest

from backend.app.projects.semantic import (
    ColumnSemantic,
    ProjectSemantic,
    TableSemantic,
)
from backend.app.services import relevant_table_selector as rts_module
from backend.app.services.relevant_table_selector import (
    DEFAULT_TOP_K,
    MAX_TOP_K,
    RelevantTableSelector,
    RelevantTableSelectorInputError,
    RuleBasedRelevantTableSelector,
    TableSelection,
    TableSelectionResult,
    TableSelectionWeights,
)
from backend.app.services.schema_explorer_service import (
    DatabaseSchema,
    SchemaColumn,
    SchemaTable,
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
                is_primary_key=(i == 0 and c == "id"),
                description=None,
            )
            for i, c in enumerate(columns)
        ),
        foreign_keys=(),
    )


def make_wms_schema() -> DatabaseSchema:
    """inventory / material / warehouse 三表（数据库事实）。"""
    return DatabaseSchema(
        schema_name="public",
        tables=(
            make_table(
                "inventory",
                ("id", "material_code", "qty", "warehouse_id"),
            ),
            make_table("material", ("id", "code", "name")),
            make_table("warehouse", ("id", "name")),
        ),
    )


def make_wms_semantic() -> ProjectSemantic:
    """§七 / §十一 示例语义（人工配置）。"""
    return ProjectSemantic(
        tables=(
            TableSemantic(
                table="public.inventory",
                business_name="库存",
                description="当前库存明细",
                aliases=("库存", "库存明细"),
            ),
            TableSemantic(
                table="public.material",
                business_name="物料",
                aliases=("物料",),
            ),
            TableSemantic(
                table="public.warehouse",
                business_name="仓库",
                aliases=("仓库",),
            ),
        ),
        columns=(
            ColumnSemantic(
                table="public.inventory",
                column="material_code",
                business_name="物料编码",
                aliases=("物料编码", "料号", "SKU"),
            ),
            ColumnSemantic(
                table="public.inventory",
                column="qty",
                business_name="库存数量",
                aliases=("库存数量", "数量"),
            ),
        ),
    )


def select(
    question: str,
    schema: DatabaseSchema | None = None,
    semantic: ProjectSemantic | None = None,
    **kwargs,
) -> TableSelectionResult:
    return RuleBasedRelevantTableSelector().select(
        question,
        schema if schema is not None else make_wms_schema(),
        semantic if semantic is not None else make_wms_semantic(),
        **kwargs,
    )


# ============================================================
# 输入校验
# ============================================================

class TestInputValidation:
    def test_non_string_question_rejected(self) -> None:
        with pytest.raises(RelevantTableSelectorInputError):
            select(123)  # type: ignore[arg-type]

    def test_non_database_schema_rejected(self) -> None:
        with pytest.raises(RelevantTableSelectorInputError):
            select("库存", schema={"tables": []})  # type: ignore[arg-type]

    def test_non_project_semantic_rejected(self) -> None:
        with pytest.raises(RelevantTableSelectorInputError):
            RuleBasedRelevantTableSelector().select(
                "库存", make_wms_schema(), None  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize("bad", [0, -1, MAX_TOP_K + 1, "5", 5.0, True, None])
    def test_invalid_top_k_rejected(self, bad) -> None:
        with pytest.raises(RelevantTableSelectorInputError):
            select("库存", top_k=bad)

    @pytest.mark.parametrize("ok", [1, 5, MAX_TOP_K])
    def test_valid_top_k_accepted(self, ok: int) -> None:
        assert select("库存", top_k=ok).selections

    def test_blank_question_returns_empty_result(self) -> None:
        result = select("   ")
        assert result.selections == ()
        assert result.question == "   "  # 原样保留

    def test_default_top_k_is_5(self) -> None:
        assert DEFAULT_TOP_K == 5


# ============================================================
# 基础（1-5）
# ============================================================

class TestBasics:
    def test_empty_schema(self) -> None:
        result = select(
            "库存", schema=DatabaseSchema(schema_name="public", tables=())
        )
        assert result.selections == ()

    def test_single_table(self) -> None:
        schema = DatabaseSchema(
            schema_name="public", tables=(make_table("inventory"),)
        )
        semantic = ProjectSemantic(
            tables=(TableSemantic(table="public.inventory", business_name="库存"),)
        )
        result = select("查库存", schema=schema, semantic=semantic)
        assert [s.table for s in result.selections] == ["public.inventory"]

    def test_multiple_tables(self) -> None:
        result = select("查询 SKU 10001 当前库存")
        tables = [s.table for s in result.selections]
        assert tables == ["public.inventory"]  # 只有库存表命中

    def test_without_semantic_matches_by_names(self) -> None:
        """无语义也能按表名 / 列名做最基本匹配（§十七）。"""
        result = select("inventory", semantic=ProjectSemantic())
        assert [s.table for s in result.selections] == ["public.inventory"]
        assert result.selections[0].score == 3.0

    def test_without_semantic_matches_by_column_name(self) -> None:
        result = select("material_code 是什么", semantic=ProjectSemantic())
        tables = [s.table for s in result.selections]
        # "material" 是 "material_code" 的子串 → material 也按表名命中(+3)
        assert tables == ["public.material", "public.inventory"]
        inventory = result.selections[1]
        assert inventory.matched_terms == ("material_code",)
        assert inventory.score == 2.0  # 列名命中

    def test_with_semantic(self) -> None:
        result = select("查仓库")
        assert [s.table for s in result.selections] == ["public.warehouse"]


# ============================================================
# 匹配（6-12）：逐类权重精确验证
# ============================================================

class TestMatchingRules:
    """每类命中单独验证精确得分（隔离 fixture，避免交叉加分）。"""

    def _isolated(
        self,
        question: str,
        *,
        table_semantic: TableSemantic | None = None,
        column_semantics: tuple[ColumnSemantic, ...] = (),
    ) -> TableSelectionResult:
        schema = DatabaseSchema(
            schema_name="public",
            tables=(
                make_table("inventory", ("id", "material_code", "qty")),
            ),
        )
        semantic = ProjectSemantic(
            tables=(table_semantic,) if table_semantic else (),
            columns=column_semantics,
        )
        return select(question, schema=schema, semantic=semantic)

    def test_table_name_hit(self) -> None:
        result = self._isolated("inventory 表结构")
        assert result.selections[0].score == 3.0
        assert result.selections[0].matched_terms == ("inventory",)

    def test_table_business_name_hit(self) -> None:
        result = self._isolated(
            "查库存",
            table_semantic=TableSemantic(table="public.inventory", business_name="库存"),
        )
        assert result.selections[0].score == 5.0
        assert result.selections[0].matched_terms == ("库存",)

    def test_table_alias_hit(self) -> None:
        result = self._isolated(
            "库存明细",
            table_semantic=TableSemantic(
                table="public.inventory", aliases=("库存明细",)
            ),
        )
        assert result.selections[0].score == 4.0
        assert result.selections[0].matched_terms == ("库存明细",)

    def test_table_description_hit(self) -> None:
        result = self._isolated(
            "当前库存明细是多少",
            table_semantic=TableSemantic(
                table="public.inventory", description="当前库存明细"
            ),
        )
        assert result.selections[0].score == 2.0

    def test_column_name_hit(self) -> None:
        result = self._isolated("qty 字段")
        assert result.selections[0].score == 2.0
        assert result.selections[0].matched_terms == ("qty",)

    def test_column_name_underscore_variant(self) -> None:
        """material_code ↔ material document 式变体（下划线 → 空格）。"""
        result = self._isolated("material code 含义")
        assert [s.table for s in result.selections] == ["public.inventory"]
        assert "material code" in result.selections[0].matched_terms

    def test_column_business_name_hit(self) -> None:
        result = self._isolated(
            "物料编码",
            column_semantics=(
                ColumnSemantic(
                    table="public.inventory",
                    column="material_code",
                    business_name="物料编码",
                ),
            ),
        )
        assert result.selections[0].score == 4.0

    def test_column_alias_hit(self) -> None:
        result = self._isolated(
            "查 SKU",
            column_semantics=(
                ColumnSemantic(
                    table="public.inventory",
                    column="material_code",
                    aliases=("SKU",),
                ),
            ),
        )
        assert result.selections[0].score == 3.0
        assert result.selections[0].matched_terms == ("sku",)

    def test_pure_digit_terms_ignored(self) -> None:
        """纯数字不参与匹配（§十一）：'10001' 不会带来任何加分。"""
        result = self._isolated(
            "库存 10001",
            table_semantic=TableSemantic(
                table="public.inventory", aliases=("10001", "库存")
            ),
        )
        # 只有 库存 命中（10001 被忽略）
        assert result.selections[0].score == 4.0
        assert result.selections[0].matched_terms == ("库存",)

    def test_short_terms_ignored(self) -> None:
        """长度 < 2 的术语跳过：alias '库' 不加分，只有 '库存' 命中。"""
        result = self._isolated(
            "查库存",
            table_semantic=TableSemantic(
                table="public.inventory", aliases=("库", "库存")
            ),
        )
        assert result.selections[0].score == 4.0
        assert result.selections[0].matched_terms == ("库存",)


# ============================================================
# 中文（13-15）
# ============================================================

class TestChineseMatching:
    def test_chinese_substring(self) -> None:
        """'库存' 命中 '查询当前库存'（不做 split 分词）。"""
        result = select("查询当前库存")
        assert [s.table for s in result.selections] == ["public.inventory"]

    def test_mixed_chinese_english(self) -> None:
        result = select("查询 SKU 10001 当前库存")
        top = result.selections[0]
        assert top.table == "public.inventory"
        assert set(top.matched_terms) == {"库存", "sku"}
        # business_name(库存)=5 + alias(库存)=4 + column alias(sku)=3
        assert top.score == 12.0

    def test_english_case_insensitive(self) -> None:
        for question in ("查sku", "查SKU", "查Sku"):
            result = self._sku_question(question)
            assert result.selections[0].matched_terms == ("sku",)

    @staticmethod
    def _sku_question(question: str) -> TableSelectionResult:
        schema = DatabaseSchema(
            schema_name="public",
            tables=(make_table("inventory", ("id", "material_code")),),
        )
        semantic = ProjectSemantic(
            columns=(
                ColumnSemantic(
                    table="public.inventory",
                    column="material_code",
                    aliases=("SKU",),
                ),
            ),
        )
        return select(question, schema=schema, semantic=semantic)

    def test_digits_do_not_inflate_score(self) -> None:
        """§十一 示例：物料 + 库存 均命中，10001 被忽略，各 9 分并列。"""
        result = select("查询物料 10001 的库存")
        scores = {s.table: s.score for s in result.selections}
        assert scores["public.inventory"] == 9.0  # 库存 5 + alias 4
        assert scores["public.material"] == 9.0   # 物料 5 + alias 4


# ============================================================
# 排序（16-18）
# ============================================================

class TestOrdering:
    def test_score_desc(self) -> None:
        """chunk 语义双 alias 命中(8) > document 单 alias 命中(4)。"""
        schema = DatabaseSchema(
            schema_name="public",
            tables=(make_table("knowledge_document"), make_table("knowledge_chunk")),
        )
        semantic = ProjectSemantic(
            tables=(
                TableSemantic(table="public.knowledge_document", aliases=("文档",)),
                TableSemantic(
                    table="public.knowledge_chunk", aliases=("知识分片", "分片")
                ),
            ),
        )
        result = select("文档有哪些知识分片", schema=schema, semantic=semantic)
        scores = [s.score for s in result.selections]
        assert scores == sorted(scores, reverse=True)
        assert result.selections[0].table == "public.knowledge_chunk"

    def test_tie_breaks_by_table_name_asc(self) -> None:
        schema = DatabaseSchema(
            schema_name="public",
            tables=(make_table("beta"), make_table("alpha")),
        )
        result = select("alpha beta", schema=schema, semantic=ProjectSemantic())
        tables = [s.table for s in result.selections]
        assert tables == ["public.alpha", "public.beta"]

    def test_tie_order_independent_of_question_order(self) -> None:
        """同分时输出与问题中词序无关，只按表名 ASC。"""
        schema = DatabaseSchema(
            schema_name="public",
            tables=(make_table("beta"), make_table("alpha")),
        )
        r1 = select("alpha beta", schema=schema, semantic=ProjectSemantic())
        r2 = select("beta alpha", schema=schema, semantic=ProjectSemantic())
        assert [s.table for s in r1.selections] == [s.table for s in r2.selections]

    def test_result_stable_across_calls(self) -> None:
        r1 = select("查询当前库存和仓库")
        r2 = select("查询当前库存和仓库")
        assert r1 == r2


# ============================================================
# 去重（19-20）
# ============================================================

class TestDedup:
    def test_multiple_hits_single_selection(self) -> None:
        """多路命中 → inventory 只出现一次，分数按命中项精确累加。"""
        result = select("inventory 的库存数量是多少")
        inventory_tables = [
            s for s in result.selections if s.table == "public.inventory"
        ]
        assert len(inventory_tables) == 1
        top = inventory_tables[0]
        # name(3) + business_name 库存(5) + alias 库存(4)
        # + column business_name 库存数量(4)
        # + column alias 库存数量(3) + column alias 数量(3) = 22
        assert top.score == 22.0
        assert set(top.matched_terms) == {"inventory", "库存", "库存数量", "数量"}

    def test_matched_terms_deduplicated(self) -> None:
        result = select("查库存")
        terms = result.selections[0].matched_terms
        # business_name 与 alias 同为 "库存"，matched_terms 只出现一次
        assert terms.count("库存") == 1
        assert terms == tuple(sorted(set(terms)))


# ============================================================
# top_k（21-24）
# ============================================================

class TestTopK:
    def _three_tables_schema(self) -> DatabaseSchema:
        return DatabaseSchema(
            schema_name="public",
            tables=(make_table("a1"), make_table("b2"), make_table("c3")),
        )

    def test_top_k_1(self) -> None:
        limited = select(
            "a1 b2 c3",
            schema=self._three_tables_schema(),
            semantic=ProjectSemantic(),
            top_k=1,
        )
        assert len(limited.selections) == 1
        assert limited.selections[0].table == "public.a1"

    def test_top_k_5(self) -> None:
        result = select(
            "a1 b2 c3",
            schema=self._three_tables_schema(),
            semantic=ProjectSemantic(),
        )
        assert len(result.selections) == 3

    def test_top_k_exceeds_candidates(self) -> None:
        """top_k(=5) 大于命中表数(3) → 返回全部命中表，不报错。"""
        result = select(
            "a1 b2 c3",
            schema=self._three_tables_schema(),
            semantic=ProjectSemantic(),
        )
        assert len(result.selections) == 3


# ============================================================
# 零匹配（25-26）
# ============================================================

class TestZeroMatch:
    def test_irrelevant_question_returns_empty(self) -> None:
        result = select("今天北京天气怎么样")
        assert result.selections == ()
        assert result.question == "今天北京天气怎么样"

    def test_zero_match_does_not_return_all_tables(self) -> None:
        result = select("今天北京天气怎么样")
        assert len(result.selections) == 0  # 绝不默认返回 3 张表


# ============================================================
# Schema 事实约束（27）
# ============================================================

class TestSchemaFactConstraint:
    def test_semantic_only_table_not_returned(self) -> None:
        """Semantic 配置了 sales_order 但 Schema 没有 → 不返回（§十八）。"""
        schema = DatabaseSchema(
            schema_name="public",
            tables=(make_table("inventory"),),
        )
        semantic = ProjectSemantic(
            tables=(
                TableSemantic(table="public.sales_order", business_name="订单"),
                TableSemantic(table="public.inventory", business_name="库存"),
            ),
            columns=(
                ColumnSemantic(
                    table="public.sales_order", column="order_no",
                    business_name="订单号",
                ),
            ),
        )
        result = select("查订单", schema=schema, semantic=semantic)
        assert [s.table for s in result.selections] == []

    def test_column_semantic_on_missing_table_ignored(self) -> None:
        schema = DatabaseSchema(
            schema_name="public",
            tables=(make_table("inventory"),),
        )
        semantic = ProjectSemantic(
            columns=(
                ColumnSemantic(
                    table="public.sales_order",
                    column="order_no",
                    aliases=("订单",),
                ),
            ),
        )
        result = select("查订单", schema=schema, semantic=semantic)
        assert result.selections == ()

    def test_bare_table_name_semantic_resolved(self) -> None:
        """裸表名语义在当前 schema 内解析（与 Validator 规则一致）。"""
        schema = DatabaseSchema(
            schema_name="public", tables=(make_table("inventory"),)
        )
        semantic = ProjectSemantic(
            tables=(TableSemantic(table="inventory", business_name="库存"),)
        )
        result = select("查库存", schema=schema, semantic=semantic)
        assert [s.table for s in result.selections] == ["public.inventory"]


# ============================================================
# 不可变（28-30）
# ============================================================

class TestImmutability:
    def test_does_not_mutate_schema(self) -> None:
        schema = make_wms_schema()
        snapshot = copy.deepcopy(schema)
        select("查询 SKU 10001 当前库存", schema=schema)
        assert schema == snapshot

    def test_does_not_mutate_semantic(self) -> None:
        semantic = make_wms_semantic()
        snapshot = copy.deepcopy(semantic)
        select("查询当前库存", semantic=semantic)
        assert semantic == snapshot

    def test_dto_frozen(self) -> None:
        sel = TableSelection(table="public.t", score=1.0, matched_terms=("a",))
        result = TableSelectionResult(question="q", selections=(sel,))
        with pytest.raises(Exception):  # FrozenInstanceError
            sel.score = 2.0  # type: ignore[misc]
        with pytest.raises(Exception):
            result.question = "x"  # type: ignore[misc]
        with pytest.raises(Exception):
            TableSelectionWeights().table_name = 1.0  # type: ignore[misc]

    def test_same_input_same_output_repeated(self) -> None:
        selector = RuleBasedRelevantTableSelector()
        r1 = selector.select("库存", make_wms_schema(), make_wms_semantic())
        r2 = selector.select("库存", make_wms_schema(), make_wms_semantic())
        assert r1 == r2
        assert r1.selections == r2.selections


# ============================================================
# 协议 / 权重
# ============================================================

class TestProtocolAndWeights:
    def test_rule_based_satisfies_protocol(self) -> None:
        selector: RelevantTableSelector = RuleBasedRelevantTableSelector()
        assert callable(selector.select)

    def test_custom_weights_injected(self) -> None:
        schema = DatabaseSchema(
            schema_name="public", tables=(make_table("inventory"),)
        )
        semantic = ProjectSemantic(
            tables=(TableSemantic(table="public.inventory", business_name="库存"),)
        )
        selector = RuleBasedRelevantTableSelector(
            weights=TableSelectionWeights(table_business_name=100.0)
        )
        result = selector.select("查库存", schema, semantic)
        assert result.selections[0].score == 100.0

    def test_default_weights_values(self) -> None:
        w = TableSelectionWeights()
        assert (w.table_name, w.table_business_name, w.table_alias,
                w.table_description) == (3.0, 5.0, 4.0, 2.0)
        assert (w.column_name, w.column_business_name, w.column_alias) == (
            2.0, 4.0, 3.0,
        )


# ============================================================
# 安全（§二十五：静态检查）
# ============================================================

class TestSecurity:
    def test_no_sqlalchemy_or_db_session_import(self) -> None:
        source = open(rts_module.__file__, encoding="utf-8").read()
        assert not re.search(r"^\s*(import|from)\s+sqlalchemy", source, re.M)
        assert "from backend.app.db" not in source
        assert "get_engine" not in source
        assert "SchemaExplorerService" not in source  # 不自动调用 Explorer

    def test_no_env_or_dotenv_access(self) -> None:
        source = open(rts_module.__file__, encoding="utf-8").read()
        assert "os.environ" not in source
        assert "getenv" not in source
        assert "load_dotenv" not in source

    def test_no_llm_or_embedding_dependency(self) -> None:
        source = open(rts_module.__file__, encoding="utf-8").read()
        assert "from backend.app.services.llm" not in source
        assert "from backend.app.services.embedding" not in source
        assert "vector_search" not in source
        assert "deepseek" not in source.lower()

    def test_result_exposes_no_connection_info(self) -> None:
        result = select("查询当前库存")
        rendered = repr(result)
        assert "postgresql://" not in rendered
        assert "password" not in rendered.lower()


# ============================================================
# DB 集成（RUN_DB_TESTS=1，只读）：
# SchemaExplorerService + ProjectSemanticLoader + Selector 全链路
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
    async def test_document_question_selects_document_table(self) -> None:
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

        result = RuleBasedRelevantTableSelector().select(
            "知识文档有哪些？", schema, semantic
        )
        assert result.selections, "应当至少命中一张表"
        assert result.selections[0].table == "public.knowledge_document"
        top = result.selections[0]
        assert top.score > 0
        assert "知识文档" in top.matched_terms  # 可解释

    async def test_chunk_question_selects_chunk_table(self) -> None:
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine
        from backend.app.projects.semantic_loader import ProjectSemanticLoader
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )

        reset_engine_cache()
        engine = get_engine()
        schema = await SchemaExplorerService(engine=engine).inspect(schema="public")
        semantic = ProjectSemanticLoader().load("vietnam-wms")

        result = RuleBasedRelevantTableSelector().select(
            "文档有哪些知识分片？", schema, semantic
        )
        assert result.selections[0].table == "public.knowledge_chunk"
        assert "知识分片" in result.selections[0].matched_terms

    async def test_irrelevant_question_zero_match_on_real_schema(self) -> None:
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine
        from backend.app.projects.semantic_loader import ProjectSemanticLoader
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )

        reset_engine_cache()
        engine = get_engine()
        schema = await SchemaExplorerService(engine=engine).inspect(schema="public")
        semantic = ProjectSemanticLoader().load("vietnam-wms")

        result = RuleBasedRelevantTableSelector().select(
            "今天北京天气怎么样", schema, semantic
        )
        assert result.selections == ()


__all__ = [
    "TestInputValidation",
    "TestBasics",
    "TestMatchingRules",
    "TestChineseMatching",
    "TestOrdering",
    "TestDedup",
    "TestTopK",
    "TestZeroMatch",
    "TestSchemaFactConstraint",
    "TestImmutability",
    "TestProtocolAndWeights",
    "TestSecurity",
    "TestRealDatabaseChain",
]
