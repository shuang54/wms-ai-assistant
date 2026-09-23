"""SemanticSchemaValidator 测试（Phase 3.7.3）。

覆盖（任务书 §十六）：
    Validator 16-22：存在/不存在 table、存在/不存在 column、
                     relationship source/target 不存在、
                     多错误一次全部返回
    Integration 26：真实 DatabaseSchema + 当前最小 ProjectSemantic → PASS
    真实 DB 集成（RUN_DB_TESTS=1）：
        SchemaExplorerService → DatabaseSchema → vietnam-wms 语义 → PASS
        （只读，不修改任何数据库数据）
"""
from __future__ import annotations

import os

import pytest

from backend.app.projects.semantic import (
    BusinessRelationship,
    ColumnSemantic,
    ProjectSemantic,
    TableSemantic,
)
from backend.app.services.semantic_schema_validator import (
    SemanticMismatch,
    SemanticSchemaMisalignmentError,
    SemanticSchemaValidator,
)
from backend.app.services.schema_explorer_service import (
    DatabaseSchema,
    SchemaColumn,
    SchemaTable,
)


def make_schema() -> DatabaseSchema:
    doc = SchemaTable(
        schema_name="public",
        name="knowledge_document",
        description=None,
        columns=(
            SchemaColumn("id", "bigint", False, None, 1, True, None),
            SchemaColumn("title", "character varying(512)", False, None, 2, False, None),
        ),
        foreign_keys=(),
    )
    chunk = SchemaTable(
        schema_name="public",
        name="knowledge_chunk",
        description=None,
        columns=(
            SchemaColumn("id", "bigint", False, None, 1, True, None),
            SchemaColumn("document_id", "bigint", False, None, 2, False, None),
        ),
        foreign_keys=(),
    )
    return DatabaseSchema(schema_name="public", tables=(doc, chunk))


# ============================================================
# 对齐 PASS（16 / 18）
# ============================================================

class TestAligned:
    def test_fully_aligned_semantic(self) -> None:
        semantic = ProjectSemantic(
            tables=(
                TableSemantic(table="public.knowledge_document",
                              business_name="知识文档"),
                TableSemantic(table="knowledge_chunk",
                              business_name="知识分片"),  # 裸表名也应命中
            ),
            columns=(
                ColumnSemantic(table="public.knowledge_document",
                               column="title", business_name="标题"),
                ColumnSemantic(table="public.knowledge_chunk",
                               column="document_id", business_name="文档ID"),
            ),
            relationships=(
                BusinessRelationship(
                    source_table="public.knowledge_chunk",
                    source_column="document_id",
                    target_table="public.knowledge_document",
                    target_column="id",
                ),
            ),
        )
        assert SemanticSchemaValidator().validate(semantic, make_schema()) == ()

    def test_empty_semantic_always_aligned(self) -> None:
        assert (
            SemanticSchemaValidator().validate(
                ProjectSemantic(), make_schema()
            )
            == ()
        )

    def test_validate_or_raise_pass(self) -> None:
        semantic = ProjectSemantic(
            tables=(TableSemantic(table="public.knowledge_document"),)
        )
        SemanticSchemaValidator().validate_or_raise(semantic, make_schema())  # 不抛


# ============================================================
# 失配检测（17 / 19-22）
# ============================================================

class TestMismatches:
    def test_unknown_table(self) -> None:
        semantic = ProjectSemantic(
            tables=(TableSemantic(table="public.inventory"),)
        )
        mismatches = SemanticSchemaValidator().validate(semantic, make_schema())
        assert len(mismatches) == 1
        assert mismatches[0].kind == "unknown_table"
        assert mismatches[0].reference == "public.inventory"
        assert "unknown table" in mismatches[0].message

    def test_unknown_column(self) -> None:
        semantic = ProjectSemantic(
            columns=(
                ColumnSemantic(table="public.knowledge_document",
                               column="material_code"),
            )
        )
        mismatches = SemanticSchemaValidator().validate(semantic, make_schema())
        assert len(mismatches) == 1
        assert mismatches[0].kind == "unknown_column"
        assert "public.knowledge_document.material_code" in mismatches[0].message

    def test_column_on_unknown_table(self) -> None:
        semantic = ProjectSemantic(
            columns=(ColumnSemantic(table="public.inventory", column="qty"),)
        )
        mismatches = SemanticSchemaValidator().validate(semantic, make_schema())
        assert len(mismatches) == 1
        assert mismatches[0].kind == "unknown_table"

    def test_relationship_source_table_missing(self) -> None:
        semantic = ProjectSemantic(
            relationships=(
                BusinessRelationship(
                    source_table="public.material",
                    source_column="code",
                    target_table="public.knowledge_document",
                    target_column="id",
                ),
            )
        )
        mismatches = SemanticSchemaValidator().validate(semantic, make_schema())
        assert len(mismatches) == 1
        assert mismatches[0].kind == "unknown_relationship_source"

    def test_relationship_source_column_missing(self) -> None:
        semantic = ProjectSemantic(
            relationships=(
                BusinessRelationship(
                    source_table="public.knowledge_chunk",
                    source_column="material_code",  # 列不存在
                    target_table="public.knowledge_document",
                    target_column="id",
                ),
            )
        )
        mismatches = SemanticSchemaValidator().validate(semantic, make_schema())
        assert len(mismatches) == 1
        assert mismatches[0].kind == "unknown_relationship_source"
        assert "public.knowledge_chunk.material_code" in mismatches[0].message

    def test_relationship_target_missing(self) -> None:
        semantic = ProjectSemantic(
            relationships=(
                BusinessRelationship(
                    source_table="public.knowledge_chunk",
                    source_column="document_id",
                    target_table="public.material",
                    target_column="code",
                ),
            )
        )
        mismatches = SemanticSchemaValidator().validate(semantic, make_schema())
        assert len(mismatches) == 1
        assert mismatches[0].kind == "unknown_relationship_target"

    def test_all_errors_reported_at_once(self) -> None:
        """多个失配一次全部返回，绝不发现第一个就停（§十六.22）。"""
        semantic = ProjectSemantic(
            tables=(
                TableSemantic(table="public.inventory"),        # 未知表
                TableSemantic(table="public.sales_order"),      # 未知表
            ),
            columns=(
                ColumnSemantic(table="public.knowledge_document",
                               column="material_code"),          # 未知列
            ),
            relationships=(
                BusinessRelationship(
                    source_table="public.material", source_column="code",
                    target_table="public.erp", target_column="id",   # 双端未知
                ),
            ),
        )
        mismatches = SemanticSchemaValidator().validate(semantic, make_schema())
        kinds = sorted(m.kind for m in mismatches)
        references = {m.reference for m in mismatches}
        # 2 未知表(表语义) + 1 未知列 + 1 未知表(列语义的表存在，OK)
        # + relationship source/target 各 1 → 共 5
        assert len(mismatches) == 5
        assert kinds == [
            "unknown_column", "unknown_relationship_source",
            "unknown_relationship_target", "unknown_table", "unknown_table",
        ]
        assert "public.inventory" in references
        assert "public.sales_order" in references

    def test_validate_or_raise_aggregates_all(self) -> None:
        semantic = ProjectSemantic(
            tables=(
                TableSemantic(table="public.inventory"),
                TableSemantic(table="public.sales_order"),
            ),
        )
        with pytest.raises(SemanticSchemaMisalignmentError) as exc_info:
            SemanticSchemaValidator().validate_or_raise(semantic, make_schema())
        assert len(exc_info.value.mismatches) == 2
        message = str(exc_info.value)
        assert "public.inventory" in message
        assert "public.sales_order" in message
        assert "2 处" in message

    def test_no_auto_fix(self) -> None:
        """失配只报错：既不改语义也不改 Schema（DTO frozen，输入不变）。"""
        semantic = ProjectSemantic(
            columns=(
                ColumnSemantic(table="public.knowledge_document",
                               column="material_code"),
            )
        )
        schema = make_schema()
        SemanticSchemaValidator().validate(semantic, schema)
        # 输入完全未变
        assert semantic.columns[0].column == "material_code"
        assert schema.tables[0].columns[1].name == "title"

    def test_bare_table_name_resolved(self) -> None:
        """裸表名在当前 schema 内命中（与 SchemaSerializer 选择规则一致）。"""
        semantic = ProjectSemantic(
            tables=(TableSemantic(table="knowledge_document"),)
        )
        assert SemanticSchemaValidator().validate(semantic, make_schema()) == ()


# ============================================================
# DB 集成（RUN_DB_TESTS=1，只读）：当前库 × 当前语义
# ============================================================

def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


@requires_db
class TestRealDatabaseAlignment:
    async def test_current_semantic_aligned_with_real_schema(self) -> None:
        """Explorer → DatabaseSchema → 当前 vietnam-wms 语义 → Validator PASS。"""
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

        validator = SemanticSchemaValidator()
        assert validator.validate(semantic, schema) == ()
        validator.validate_or_raise(semantic, schema)  # 不抛

    async def test_misaligned_semantic_detected_on_real_schema(self) -> None:
        """故意错配的语义在真实库上被逐条识别（负例验证）。"""
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )

        reset_engine_cache()
        engine = get_engine()
        schema = await SchemaExplorerService(engine=engine).inspect(schema="public")

        bad_semantic = ProjectSemantic(
            tables=(TableSemantic(table="public.inventory"),),
            columns=(
                ColumnSemantic(table="public.knowledge_document",
                               column="material_code"),
            ),
        )
        mismatches = SemanticSchemaValidator().validate(bad_semantic, schema)
        assert {m.kind for m in mismatches} == {"unknown_table", "unknown_column"}


__all__ = [
    "TestAligned",
    "TestMismatches",
    "TestRealDatabaseAlignment",
]
