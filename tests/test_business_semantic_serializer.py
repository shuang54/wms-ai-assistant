"""BusinessSemanticSerializer + Composer 测试（Phase 3.7.3）。

覆盖（任务书 §十六）：
    Serializer 10-15：table / column / aliases / relationship /
                     稳定排序 / 空 semantic
    Security   21-23：无 secrets 展开 / 不读 .env / 不访问数据库
    Composer：三段组合、预算截断、无语义时跳过语义段
"""
from __future__ import annotations

import pytest

from backend.app.projects.models import DataSource, ProjectContext
from backend.app.projects.semantic import (
    BusinessRelationship,
    ColumnSemantic,
    ProjectSemantic,
    TableSemantic,
)
from backend.app.services import business_semantic_serializer as bs_module
from backend.app.services.business_semantic_serializer import (
    BusinessSemanticSerializer,
)
from backend.app.services.database_context_composer import (
    DatabaseContextComposer,
)
from backend.app.services.schema_explorer_service import (
    DatabaseSchema,
    SchemaColumn,
    SchemaTable,
)
from backend.app.services.schema_serializer_service import (
    SchemaSerializerInputError,
)


# ============================================================
# 构造工具
# ============================================================

def make_semantic() -> ProjectSemantic:
    return ProjectSemantic(
        tables=(
            TableSemantic(
                table="public.knowledge_document",
                business_name="知识文档",
                description="知识库文档元数据表",
                aliases=("知识文档", "文档"),
            ),
            TableSemantic(table="public.knowledge_chunk", business_name="知识分片"),
        ),
        columns=(
            ColumnSemantic(
                table="public.knowledge_document",
                column="title",
                business_name="文档标题",
                description="文档展示标题",
                aliases=("标题",),
            ),
            ColumnSemantic(
                table="public.knowledge_document",
                column="file_name",
                business_name="文件名",
            ),
        ),
        relationships=(
            BusinessRelationship(
                source_table="public.knowledge_chunk",
                source_column="document_id",
                target_table="public.knowledge_document",
                target_column="id",
                description="分片属于文档",
            ),
        ),
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


def make_project() -> ProjectContext:
    return ProjectContext(
        project_id="vietnam-wms",
        project_name="Vietnam WMS",
        description=None,
        data_source=DataSource(name="primary", type="postgresql"),
    )


# ============================================================
# Serializer（10-15）
# ============================================================

class TestSerializer:
    def test_table_semantic(self) -> None:
        result = BusinessSemanticSerializer().serialize(make_semantic())
        assert "Business Semantics:" in result.split("\n")[0]
        assert "## Table: public.knowledge_document" in result
        assert "Business Name: 知识文档" in result
        assert "Description: 知识库文档元数据表" in result

    def test_table_aliases(self) -> None:
        result = BusinessSemanticSerializer().serialize(make_semantic())
        assert "Aliases:" in result
        assert "- 知识文档" in result
        assert "- 文档" in result

    def test_column_semantic_grouped_under_table(self) -> None:
        result = BusinessSemanticSerializer().serialize(make_semantic())

        def section(header: str) -> str:
            """提取某个 ## 标题段（到下一个 ## 段为止）。"""
            part = result.split(header, 1)[1]
            nxt = part.find("\n## ")
            return part if nxt == -1 else part[:nxt]

        doc_section = section("## Table: public.knowledge_document")
        chunk_section = section("## Table: public.knowledge_chunk")
        assert "- title" in doc_section
        assert "  Business Name: 文档标题" in doc_section
        assert "  Description: 文档展示标题" in doc_section
        assert "  Aliases: 标题" in doc_section
        # knowledge_chunk 段不含 title（字段语义不跨表泄漏）
        assert "- title" not in chunk_section

    def test_column_missing_fields_omitted(self) -> None:
        """字段语义缺省的属性行整行省略，不输出占位猜测。"""
        result = BusinessSemanticSerializer().serialize(make_semantic())
        assert "- file_name\n  Business Name: 文件名" in result
        assert "  Description:" not in result.split("- file_name")[1].split("\n- ")[0]

    def test_relationship(self) -> None:
        result = BusinessSemanticSerializer().serialize(make_semantic())
        assert "## Business Relationships" in result
        assert (
            "- public.knowledge_chunk.document_id"
            " -> public.knowledge_document.id — 分片属于文档" in result
        )

    def test_relationship_without_description(self) -> None:
        rel = BusinessRelationship(
            source_table="public.a", source_column="x",
            target_table="public.b", target_column="y",
        )
        result = BusinessSemanticSerializer().serialize(
            ProjectSemantic(relationships=(rel,))
        )
        assert "- public.a.x -> public.b.y" in result
        assert " — " not in result

    def test_stable_output_same_input(self) -> None:
        serializer = BusinessSemanticSerializer()
        assert serializer.serialize(make_semantic()) == serializer.serialize(
            make_semantic()
        )

    def test_stable_output_shuffled_input(self) -> None:
        """输入顺序打乱 → 输出仍按稳定键排序，字节级一致。"""
        s = make_semantic()
        shuffled = ProjectSemantic(
            tables=tuple(reversed(s.tables)),
            columns=tuple(reversed(s.columns)),
            relationships=tuple(reversed(s.relationships)),
        )
        assert (
            BusinessSemanticSerializer().serialize(s)
            == BusinessSemanticSerializer().serialize(shuffled)
        )

    def test_empty_semantic_returns_empty_string(self) -> None:
        assert BusinessSemanticSerializer().serialize(ProjectSemantic()) == ""

    def test_semantic_without_table_semantic_but_columns(self) -> None:
        """只有字段语义（无表语义）→ 仍按表分组输出表段。"""
        cs = ColumnSemantic(table="public.t", column="x", business_name="X")
        result = BusinessSemanticSerializer().serialize(
            ProjectSemantic(columns=(cs,))
        )
        assert "## Table: public.t" in result
        assert "- x" in result
        assert "Business Name: X" in result

    def test_non_project_semantic_rejected(self) -> None:
        with pytest.raises(TypeError):
            BusinessSemanticSerializer().serialize({"tables": []})  # type: ignore[arg-type]


# ============================================================
# Security（21-23）
# ============================================================

class TestSerializerSecurity:
    def test_no_env_expansion_in_output(self, monkeypatch) -> None:
        """语义值一律字面量输出：${...} 不会被展开。"""
        monkeypatch.setenv("SEMANTIC_SER_TEST", "top-secret")
        ts = TableSemantic(
            table="public.t", description="${SEMANTIC_SER_TEST}"
        )
        result = BusinessSemanticSerializer().serialize(
            ProjectSemantic(tables=(ts,))
        )
        assert "${SEMANTIC_SER_TEST}" in result
        assert "top-secret" not in result

    def test_serializer_module_has_no_env_or_db_access(self) -> None:
        """静态断言 import 层（不误伤 docstring 中的字样）。"""
        import re

        source = open(bs_module.__file__, encoding="utf-8").read()
        assert not re.search(r"^\s*(import|from)\s+sqlalchemy", source, re.M)
        assert "from backend.app.db" not in source
        assert "os.environ" not in source
        assert "getenv" not in source
        assert "load_dotenv" not in source

    def test_output_contains_no_connection_strings(self) -> None:
        result = BusinessSemanticSerializer().serialize(make_semantic())
        assert "postgresql://" not in result
        assert "localhost" not in result
        assert "@5432" not in result


# ============================================================
# Composer
# ============================================================

class TestComposer:
    def test_compose_three_sections(self) -> None:
        result = DatabaseContextComposer().compose(
            project=make_project(),
            schema=make_schema(),
            semantic=make_semantic(),
        )
        # 段 1：项目头
        assert result.startswith(
            "Project: Vietnam WMS\nData Source: primary\nDatabase Type: postgresql"
        )
        # 段 2：Schema 事实
        assert "## Database Schema" in result
        assert "## Table: public.knowledge_document" in result
        assert "- id: bigint [PK, NOT NULL]" in result
        # 段 3：业务语义
        assert "Business Semantics:" in result
        assert "Business Name: 知识文档" in result
        # 顺序：项目头 → Schema → Semantic
        assert result.index("Project:") < result.index("## Database Schema")
        assert result.index("## Database Schema") < result.index("Business Semantics:")

    def test_compose_without_semantic(self) -> None:
        result = DatabaseContextComposer().compose(
            project=make_project(), schema=make_schema(), semantic=None
        )
        assert "Business Semantics:" not in result
        assert "## Database Schema" in result

    def test_compose_with_empty_semantic(self) -> None:
        result = DatabaseContextComposer().compose(
            project=make_project(),
            schema=make_schema(),
            semantic=ProjectSemantic(),
        )
        assert "Business Semantics:" not in result

    def test_compose_table_selection_forwarded(self) -> None:
        result = DatabaseContextComposer().compose(
            project=make_project(),
            schema=make_schema(),
            semantic=make_semantic(),
            tables=["knowledge_document"],
        )
        assert "## Table: public.knowledge_document" in result
        assert "## Table: public.knowledge_chunk" not in result.split(
            "Business Semantics:"
        )[0]

    def test_compose_budget_truncation_marker(self) -> None:
        result = DatabaseContextComposer().compose(
            project=make_project(),
            schema=make_schema(),
            semantic=make_semantic(),
            max_chars=80,
        )
        assert "[Database Context truncated: max_chars=80]" in result
        assert result.split("\n")[-1].startswith("[Database Context truncated")
        # 项目头优先保留
        assert result.startswith("Project: Vietnam WMS")

    def test_compose_rejects_non_positive_budget(self) -> None:
        with pytest.raises(SchemaSerializerInputError):
            DatabaseContextComposer().compose(
                project=make_project(),
                schema=make_schema(),
                semantic=None,
                max_chars=0,
            )

    def test_compose_stable(self) -> None:
        composer = DatabaseContextComposer()
        r1 = composer.compose(
            project=make_project(), schema=make_schema(), semantic=make_semantic()
        )
        r2 = composer.compose(
            project=make_project(), schema=make_schema(), semantic=make_semantic()
        )
        assert r1 == r2


__all__ = [
    "TestSerializer",
    "TestSerializerSecurity",
    "TestComposer",
]
