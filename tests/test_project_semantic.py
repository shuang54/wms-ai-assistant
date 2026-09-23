"""Business Semantic 模型 + Loader 测试（Phase 3.7.3）。

覆盖（任务书 §十六）：
    Model   1-4：frozen / 默认值 / aliases / relationship
    Loader  5-9：正常加载 / 不存在 project / 格式错误 / 必填缺失 / 空配置
    Security 23-25：未知字段(password 等)拒绝且不展开 secrets /
                    不读 .env / 不访问数据库
"""
from __future__ import annotations

import os

import pytest

from backend.app.projects import semantic_loader as loader_module
from backend.app.projects.semantic import (
    BusinessRelationship,
    ColumnSemantic,
    ProjectSemantic,
    TableSemantic,
)
from backend.app.projects.semantic_loader import (
    ProjectSemanticConfigError,
    ProjectSemanticLoader,
    ProjectSemanticNotFoundError,
)


# ============================================================
# Model（1-4）
# ============================================================

class TestSemanticModels:
    def test_frozen_immutable(self) -> None:
        ts = TableSemantic(table="public.t", business_name="T", aliases=("a",))
        cs = ColumnSemantic(table="public.t", column="c", business_name="C")
        rel = BusinessRelationship(
            source_table="public.a", source_column="x",
            target_table="public.b", target_column="y",
        )
        ps = ProjectSemantic(tables=(ts,), columns=(cs,), relationships=(rel,))
        for obj, attr, value in (
            (ts, "table", "z"), (cs, "column", "z"),
            (rel, "source_column", "z"), (ps, "tables", ()),
        ):
            with pytest.raises(Exception):  # FrozenInstanceError
                setattr(obj, attr, value)  # type: ignore[misc]

    def test_defaults(self) -> None:
        ps = ProjectSemantic()
        assert ps.tables == ()
        assert ps.columns == ()
        assert ps.relationships == ()
        ts = TableSemantic(table="public.t")
        assert ts.business_name is None
        assert ts.description is None
        assert ts.aliases == ()
        rel = BusinessRelationship(
            source_table="a", source_column="x", target_table="b", target_column="y"
        )
        assert rel.description is None

    def test_aliases(self) -> None:
        ts = TableSemantic(table="public.t", aliases=("库存", "库存明细", "SKU"))
        assert ts.aliases == ("库存", "库存明细", "SKU")

    def test_relationship(self) -> None:
        rel = BusinessRelationship(
            source_table="public.knowledge_chunk",
            source_column="document_id",
            target_table="public.knowledge_document",
            target_column="id",
            description="分片属于文档",
        )
        assert rel.source_table == "public.knowledge_chunk"
        assert rel.source_column == "document_id"
        assert rel.target_table == "public.knowledge_document"
        assert rel.target_column == "id"
        assert rel.description == "分片属于文档"

    def test_empty_semantic_is_valid(self) -> None:
        """空语义 = 项目尚未配置业务语义（合法状态）。"""
        assert ProjectSemantic().tables == ()


# ============================================================
# Loader（5-9）
# ============================================================

class TestLoaderHappyPath:
    def test_load_vietnam_wms_minimal_semantic(self) -> None:
        """加载当前项目的最小验证语义（2 表 / 4 列 / 1 关系）。"""
        semantic = ProjectSemanticLoader().load("vietnam-wms")
        assert isinstance(semantic, ProjectSemantic)

        table_keys = {ts.table for ts in semantic.tables}
        assert table_keys == {
            "public.knowledge_document", "public.knowledge_chunk",
        }
        doc = next(
            ts for ts in semantic.tables
            if ts.table == "public.knowledge_document"
        )
        assert doc.business_name == "知识文档"
        assert doc.aliases == ("知识文档", "文档")

        title = next(
            cs for cs in semantic.columns
            if cs.table == "public.knowledge_document" and cs.column == "title"
        )
        assert title.business_name == "文档标题"
        assert title.aliases == ("标题",)

        assert len(semantic.relationships) == 1
        rel = semantic.relationships[0]
        assert rel.source_table == "public.knowledge_chunk"
        assert rel.source_column == "document_id"
        assert rel.target_table == "public.knowledge_document"
        assert rel.target_column == "id"


class TestLoaderErrors:
    def test_unknown_project(self) -> None:
        with pytest.raises(ProjectSemanticNotFoundError) as exc_info:
            ProjectSemanticLoader().load("no_such_project")
        assert exc_info.value.project_id == "no_such_project"

    @pytest.mark.parametrize(
        "bad_id",
        ["", "../etc/passwd", "a/b", "a\\b", "..", "a b", 123, None],
    )
    def test_invalid_project_id_rejected(self, bad_id) -> None:
        """project_id 白名单（防路径穿越 / 非 str）。"""
        with pytest.raises(ProjectSemanticConfigError):
            ProjectSemanticLoader().load(bad_id)  # type: ignore[arg-type]

    def test_malformed_yaml(self, tmp_path) -> None:
        (tmp_path / "broken.yaml").write_text(
            "tables:\n  - table: [unclosed", encoding="utf-8"
        )
        with pytest.raises(ProjectSemanticConfigError, match="YAML"):
            ProjectSemanticLoader(base_dir=tmp_path).load("broken")

    def test_top_level_not_mapping(self, tmp_path) -> None:
        (tmp_path / "list.yaml").write_text("- a\n- b\n", encoding="utf-8")
        with pytest.raises(ProjectSemanticConfigError, match="顶层"):
            ProjectSemanticLoader(base_dir=tmp_path).load("list")

    def test_missing_required_field(self, tmp_path) -> None:
        (tmp_path / "missing.yaml").write_text(
            "tables:\n  - business_name: 库存\n", encoding="utf-8"
        )
        with pytest.raises(ProjectSemanticConfigError, match="table"):
            ProjectSemanticLoader(base_dir=tmp_path).load("missing")

    def test_missing_required_column_field(self, tmp_path) -> None:
        (tmp_path / "nocol.yaml").write_text(
            "columns:\n  - table: public.t\n    business_name: X\n",
            encoding="utf-8",
        )
        with pytest.raises(ProjectSemanticConfigError, match="column"):
            ProjectSemanticLoader(base_dir=tmp_path).load("nocol")

    def test_missing_relationship_field(self, tmp_path) -> None:
        (tmp_path / "rel.yaml").write_text(
            "relationships:\n"
            "  - source_table: public.a\n"
            "    source_column: x\n"
            "    target_table: public.b\n",
            encoding="utf-8",
        )
        with pytest.raises(ProjectSemanticConfigError, match="target_column"):
            ProjectSemanticLoader(base_dir=tmp_path).load("rel")

    def test_unknown_field_rejected(self, tmp_path) -> None:
        """未知字段拒绝（含 password 等可疑字段，§十六.23）。"""
        (tmp_path / "unknown.yaml").write_text(
            "tables:\n"
            "  - table: public.t\n"
            "    password: hunter2\n",
            encoding="utf-8",
        )
        with pytest.raises(ProjectSemanticConfigError, match="password"):
            ProjectSemanticLoader(base_dir=tmp_path).load("unknown")

    def test_unknown_top_level_section_rejected(self, tmp_path) -> None:
        (tmp_path / "topsec.yaml").write_text(
            "tables: []\ncredentials:\n  api_key: xxx\n", encoding="utf-8"
        )
        with pytest.raises(ProjectSemanticConfigError, match="credentials"):
            ProjectSemanticLoader(base_dir=tmp_path).load("topsec")

    def test_duplicate_table_rejected(self, tmp_path) -> None:
        (tmp_path / "dup.yaml").write_text(
            "tables:\n"
            "  - table: public.t\n"
            "    business_name: A\n"
            "  - table: public.t\n"
            "    business_name: B\n",
            encoding="utf-8",
        )
        with pytest.raises(ProjectSemanticConfigError, match="重复"):
            ProjectSemanticLoader(base_dir=tmp_path).load("dup")

    def test_duplicate_column_rejected(self, tmp_path) -> None:
        (tmp_path / "dupcol.yaml").write_text(
            "columns:\n"
            "  - table: public.t\n"
            "    column: a\n"
            "  - table: public.t\n"
            "    column: a\n",
            encoding="utf-8",
        )
        with pytest.raises(ProjectSemanticConfigError, match="重复"):
            ProjectSemanticLoader(base_dir=tmp_path).load("dupcol")

    def test_bad_alias_type_rejected(self, tmp_path) -> None:
        (tmp_path / "alias.yaml").write_text(
            "tables:\n"
            "  - table: public.t\n"
            "    aliases: 库存\n",  # 应为列表
            encoding="utf-8",
        )
        with pytest.raises(ProjectSemanticConfigError, match="aliases"):
            ProjectSemanticLoader(base_dir=tmp_path).load("alias")

    def test_empty_yaml_returns_empty_semantic(self, tmp_path) -> None:
        """空配置文件 → 合法的空 ProjectSemantic。"""
        (tmp_path / "empty.yaml").write_text("", encoding="utf-8")
        semantic = ProjectSemanticLoader(base_dir=tmp_path).load("empty")
        assert semantic == ProjectSemantic()

    def test_empty_sections_returns_empty_semantic(self, tmp_path) -> None:
        (tmp_path / "sections.yaml").write_text(
            "tables: []\ncolumns: []\nrelationships: []\n", encoding="utf-8"
        )
        semantic = ProjectSemanticLoader(base_dir=tmp_path).load("sections")
        assert semantic == ProjectSemantic()


# ============================================================
# Security（23-25）
# ============================================================

class TestLoaderSecurity:
    def test_no_env_expansion(self, tmp_path, monkeypatch) -> None:
        """配置值一律字面量：${DATABASE_URL} 不会被展开成真实值。"""
        monkeypatch.setenv("SEMANTIC_TEST_SECRET", "super-secret-value")
        (tmp_path / "expand.yaml").write_text(
            "tables:\n"
            "  - table: public.t\n"
            "    description: ${SEMANTIC_TEST_SECRET}\n",
            encoding="utf-8",
        )
        semantic = ProjectSemanticLoader(base_dir=tmp_path).load("expand")
        assert semantic.tables[0].description == "${SEMANTIC_TEST_SECRET}"
        assert "super-secret-value" not in repr(semantic)

    def test_loader_does_not_read_env_or_dotenv(self) -> None:
        """Loader 源码不读环境变量 / .env（静态断言）。"""
        source = open(loader_module.__file__, encoding="utf-8").read()
        assert "os.environ" not in source
        assert "getenv" not in source
        assert "load_dotenv" not in source

    def test_loader_does_not_access_database(self) -> None:
        """Loader 源码无 SQLAlchemy / DB import（静态断言，不误伤 docstring）。"""
        import re

        source = open(loader_module.__file__, encoding="utf-8").read()
        assert not re.search(r"^\s*(import|from)\s+sqlalchemy", source, re.M)
        assert "from backend.app.db" not in source
        assert "get_engine" not in source

    def test_loader_does_not_call_llm(self) -> None:
        source = open(loader_module.__file__, encoding="utf-8").read()
        assert "deepseek" not in source.lower()


__all__ = [
    "TestSemanticModels",
    "TestLoaderHappyPath",
    "TestLoaderErrors",
    "TestLoaderSecurity",
]
