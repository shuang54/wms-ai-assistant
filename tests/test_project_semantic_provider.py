"""Project Semantic Provider 单元测试（Phase 3.8.3，任务书 §16.1-16.5）。

不依赖数据库 / 网络 / LLM。
覆盖：Provider（known/unknown / InMemory 隔离 / LoaderBacked 兼容）、
Serializer A/B 隔离、Selector 项目语义匹配、Composer 组合、
DefaultProjectContextProvider 的 semantic_provider 注入路径。
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.app.projects.models import DataSource, ProjectContext
from backend.app.projects.semantic import (
    ColumnSemantic,
    ProjectSemantic,
    TableSemantic,
)
from backend.app.projects.semantic_loader import (
    ProjectSemanticConfigError,
    ProjectSemanticNotFoundError,
)
from backend.app.projects.semantic_provider import (
    InMemoryProjectSemanticProvider,
    LoaderBackedProjectSemanticProvider,
    get_default_project_semantic_provider,
)
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorUnavailableError,
    DefaultProjectContextProvider,
)
from backend.app.services.business_semantic_serializer import (
    BusinessSemanticSerializer,
)
from backend.app.services.database_context_composer import DatabaseContextComposer
from backend.app.services.relevant_table_selector import (
    RuleBasedRelevantTableSelector,
)

# ============================================================
# 测试语义：A / B 两套明显不同的业务语义（任务书 §七）
# 同一表结构 inventory，业务名称 / 别名不同
# ============================================================

SEMANTIC_A = ProjectSemantic(
    tables=(
        TableSemantic(
            table="project_a.inventory",
            business_name="库存",
            description="库存明细",
            aliases=("库存", "库存表"),
        ),
    ),
    columns=(
        ColumnSemantic(
            table="project_a.inventory",
            column="material_code",
            business_name="物料编码",
            aliases=("物料编码", "料号"),
        ),
        ColumnSemantic(
            table="project_a.inventory",
            column="qty",
            business_name="库存数量",
        ),
    ),
)

SEMANTIC_B = ProjectSemantic(
    tables=(
        TableSemantic(
            table="project_b.inventory",
            business_name="可用库存",
            description="可用库存明细",
            aliases=("可用库存",),
        ),
    ),
    columns=(
        ColumnSemantic(
            table="project_b.inventory",
            column="material_code",
            business_name="产品编号",
            aliases=("产品编号",),
        ),
        ColumnSemantic(
            table="project_b.inventory",
            column="qty",
            business_name="可用数量",
        ),
    ),
)


# ============================================================
# 16.1 Semantic Provider
# ============================================================

class TestInMemoryProjectSemanticProvider:
    def test_known_project_returns_semantic(self) -> None:
        """known project → semantic（§16.1）。"""
        provider = InMemoryProjectSemanticProvider()
        provider.register("project-a", SEMANTIC_A)
        assert provider.get("project-a") == SEMANTIC_A

    def test_unknown_project_clear_error(self) -> None:
        """unknown project → clear error（不静默、不回退）。"""
        provider = InMemoryProjectSemanticProvider()
        provider.register("project-a", SEMANTIC_A)
        with pytest.raises(ProjectSemanticNotFoundError) as ei:
            provider.get("project-b")
        assert ei.value.project_id == "project-b"

    def test_projects_isolated(self) -> None:
        """project-a → A，project-b → B（语义不串）。"""
        provider = InMemoryProjectSemanticProvider()
        provider.register("project-a", SEMANTIC_A)
        provider.register("project-b", SEMANTIC_B)
        assert provider.get("project-a") == SEMANTIC_A
        assert provider.get("project-b") == SEMANTIC_B
        assert provider.get("project-a") != provider.get("project-b")

    def test_duplicate_register_rejected(self) -> None:
        provider = InMemoryProjectSemanticProvider()
        provider.register("project-a", SEMANTIC_A)
        with pytest.raises(ProjectSemanticConfigError, match="已注册"):
            provider.register("project-a", SEMANTIC_B)

    def test_register_validates_types(self) -> None:
        provider = InMemoryProjectSemanticProvider()
        with pytest.raises(ProjectSemanticConfigError):
            provider.register("", SEMANTIC_A)
        with pytest.raises(ProjectSemanticConfigError):
            provider.register("project-a", {"tables": []})  # type: ignore[arg-type]

    def test_unregister(self) -> None:
        provider = InMemoryProjectSemanticProvider()
        provider.register("project-a", SEMANTIC_A)
        provider.unregister("project-a")
        with pytest.raises(ProjectSemanticNotFoundError):
            provider.get("project-a")
        with pytest.raises(ProjectSemanticConfigError):
            provider.unregister("project-a")

    def test_list_project_ids(self) -> None:
        provider = InMemoryProjectSemanticProvider()
        provider.register("project-b", SEMANTIC_B)
        provider.register("project-a", SEMANTIC_A)
        assert provider.list_project_ids() == ("project-a", "project-b")

    def test_get_validates_project_id(self) -> None:
        provider = InMemoryProjectSemanticProvider()
        with pytest.raises(ProjectSemanticConfigError):
            provider.get("   ")
        with pytest.raises(ProjectSemanticConfigError):
            provider.get(123)  # type: ignore[arg-type]


class TestLoaderBackedProjectSemanticProvider:
    def test_loads_existing_yaml(self, tmp_path) -> None:
        """文件名 == project_id 约定：存在 → 解析返回。"""
        (tmp_path / "project-a.yaml").write_text(
            "tables:\n"
            "  - table: project_a.inventory\n"
            "    business_name: 库存\n",
            encoding="utf-8",
        )
        provider = LoaderBackedProjectSemanticProvider(base_dir=tmp_path)
        semantic = provider.get("project-a")
        assert semantic.tables[0].business_name == "库存"

    def test_missing_yaml_empty_semantic(self, tmp_path) -> None:
        """缺文件 → 空语义（兼容旧行为，合法状态）。"""
        provider = LoaderBackedProjectSemanticProvider(base_dir=tmp_path)
        semantic = provider.get("ghost-project")
        assert semantic == ProjectSemantic()

    def test_broken_yaml_config_error_passthrough(self, tmp_path) -> None:
        """配置错误透传（不静默吞掉）。"""
        (tmp_path / "broken.yaml").write_text(
            "tables:\n  - table: x\n    unknown_field: 1\n",
            encoding="utf-8",
        )
        provider = LoaderBackedProjectSemanticProvider(base_dir=tmp_path)
        with pytest.raises(ProjectSemanticConfigError):
            provider.get("broken")

    def test_default_provider_is_loader_backed(self) -> None:
        """默认单例 = LoaderBacked（旧行为等价）。"""
        provider = get_default_project_semantic_provider()
        assert isinstance(provider, LoaderBackedProjectSemanticProvider)
        # vietnam-wms.yaml 存在 → 加载真实语义
        semantic = provider.get("vietnam-wms")
        assert semantic.tables  # 配置了表语义
        assert any("knowledge_document" in t.table for t in semantic.tables)

    def test_loader_and_base_dir_mutually_exclusive(self, tmp_path) -> None:
        from backend.app.projects.semantic_loader import ProjectSemanticLoader

        with pytest.raises(ProjectSemanticConfigError):
            LoaderBackedProjectSemanticProvider(
                loader=ProjectSemanticLoader(base_dir=tmp_path),
                base_dir=tmp_path,
            )


# ============================================================
# 16.3 Serializer：A / B 隔离
# ============================================================

class TestSerializerIsolation:
    def test_semantic_a_serializes_only_a(self) -> None:
        text = BusinessSemanticSerializer().serialize(SEMANTIC_A)
        assert "库存" in text and "物料编码" in text and "库存数量" in text
        assert "可用库存" not in text
        assert "产品编号" not in text
        assert "可用数量" not in text

    def test_semantic_b_serializes_only_b(self) -> None:
        text = BusinessSemanticSerializer().serialize(SEMANTIC_B)
        assert "可用库存" in text and "产品编号" in text and "可用数量" in text
        # B 语义文本中不含 A 专属业务名（"库存数量" vs "可用数量"）
        assert "物料编码" not in text
        assert "库存数量" not in text

    def test_serializer_deterministic(self) -> None:
        s = BusinessSemanticSerializer()
        assert s.serialize(SEMANTIC_A) == s.serialize(SEMANTIC_A)


# ============================================================
# 16.4 Relevant Table Selector：项目语义影响匹配
# ============================================================

@dataclass(frozen=True)
class _FakeColumn:
    name: str
    type: str = "varchar"


@dataclass(frozen=True)
class _FakeTable:
    schema_name: str
    name: str
    columns: tuple = ()


def _schema_for(schema_name: str):
    """构造最小 DatabaseSchema（Selector / Composer 只读这些字段）。"""
    from backend.app.services.schema_explorer_service import (
        DatabaseSchema,
        SchemaColumn,
        SchemaTable,
    )

    def _col(name: str, pos: int, pk: bool = False) -> SchemaColumn:
        return SchemaColumn(
            name=name,
            data_type="varchar" if pk is False and name == "material_code" else "numeric",
            nullable=False,
            default=None,
            ordinal_position=pos,
            is_primary_key=pk,
            description=None,
        )

    return DatabaseSchema(
        schema_name=schema_name,
        tables=(
            SchemaTable(
                schema_name=schema_name,
                name="inventory",
                description=None,
                columns=(
                    _col("material_code", 1, pk=True),
                    _col("qty", 2),
                ),
                foreign_keys=(),
            ),
        ),
    )


class TestSelectorWithProjectSemantic:
    def test_project_a_alias_matches_inventory(self) -> None:
        """A："库存" → 匹配 project_a.inventory（business_name 命中）。"""
        selector = RuleBasedRelevantTableSelector()
        result = selector.select(
            "库存最多的物料有哪些", _schema_for("project_a"), SEMANTIC_A
        )
        assert [s.table for s in result.selections] == ["project_a.inventory"]
        assert "库存" in result.selections[0].matched_terms

    def test_project_b_alias_matches_inventory(self) -> None:
        """B："可用库存" → 匹配 project_b.inventory（business_name 命中）。"""
        selector = RuleBasedRelevantTableSelector()
        result = selector.select(
            "可用库存最多的产品有哪些", _schema_for("project_b"), SEMANTIC_B
        )
        assert [s.table for s in result.selections] == ["project_b.inventory"]
        assert "可用库存" in result.selections[0].matched_terms

    def test_a_semantic_does_not_apply_to_b_schema(self) -> None:
        """A 语义（引用 project_a.inventory）+ Schema B → 引用不存在
        → 语义加分失效，退化为表名/列名匹配（不串语义，§十一）。"""
        selector = RuleBasedRelevantTableSelector()
        result = selector.select(
            "可用库存最多的产品有哪些",  # B 的问法
            _schema_for("project_b"),
            SEMANTIC_A,  # 错配 A 语义
        )
        # A 语义的 business_name "库存" 是 "可用库存" 的子串 → 仍会命中，
        # 但 A 语义的表引用 project_a.inventory 在 Schema B 中不存在 →
        # 语义不索引到 project_b.inventory；只剩表名/列名匹配
        # （"inventory" 不在中文问句中 → 不命中）
        assert result.selections == ()

    def test_b_semantic_column_alias_project_b(self) -> None:
        """B："产品编号"（material_code 别名）→ 匹配 project_b.inventory。"""
        selector = RuleBasedRelevantTableSelector()
        result = selector.select(
            "产品编号 P001 的可用数量", _schema_for("project_b"), SEMANTIC_B
        )
        assert [s.table for s in result.selections] == ["project_b.inventory"]
        assert "产品编号" in result.selections[0].matched_terms

    def test_a_semantic_column_alias_not_in_b(self) -> None:
        """A："料号"（A 专属别名）→ 在 B 语义下不命中（问句中不含
        B 的任何业务术语 / 表名列名）。"""
        selector = RuleBasedRelevantTableSelector()
        result = selector.select(
            "料号 P001 有多少", _schema_for("project_b"), SEMANTIC_B
        )
        assert result.selections == ()

    def test_a_semantic_column_alias_in_a(self) -> None:
        """A："料号" → A 语义下命中（对照组）。"""
        selector = RuleBasedRelevantTableSelector()
        result = selector.select(
            "料号 P001 的库存数量", _schema_for("project_a"), SEMANTIC_A
        )
        assert [s.table for s in result.selections] == ["project_a.inventory"]
        assert "料号" in result.selections[0].matched_terms


# ============================================================
# 16.5 Context Composer：Schema + Semantic 同源组合
# ============================================================

def _make_project(project_id: str) -> ProjectContext:
    return ProjectContext(
        project_id=project_id,
        project_name=f"Project {project_id}",
        description=None,
        data_source=DataSource(name="primary", type="postgresql"),
    )


class TestComposerIsolation:
    def test_schema_a_plus_semantic_a(self) -> None:
        composer = DatabaseContextComposer()
        text = composer.compose(
            project=_make_project("project-a"),
            schema=_schema_for("project_a"),
            semantic=SEMANTIC_A,
        )
        assert "project_a.inventory" in text
        assert "库存" in text and "物料编码" in text
        # Schema A + Semantic A：不含 B 的语义与 schema
        assert "可用库存" not in text
        assert "产品编号" not in text
        assert "project_b" not in text

    def test_schema_b_plus_semantic_b(self) -> None:
        composer = DatabaseContextComposer()
        text = composer.compose(
            project=_make_project("project-b"),
            schema=_schema_for("project_b"),
            semantic=SEMANTIC_B,
        )
        assert "project_b.inventory" in text
        assert "可用库存" in text and "产品编号" in text
        assert "物料编码" not in text
        assert "库存数量" not in text
        assert "project_a" not in text


# ============================================================
# DefaultProjectContextProvider：semantic_provider 注入路径
# ============================================================

class _FakeExplorer:
    """async inspect 桩（DefaultProvider 内部 _inspect_sync → asyncio.run）。"""

    async def inspect(self, schema=None):
        return _schema_for(schema or "project_a")


class TestProviderInjection:
    def test_provider_injection_semantic_used(self, monkeypatch) -> None:
        """注入 semantic_provider → resolve 返回该项目的语义（§12：
        Orchestrator 不读 YAML，通过 Provider 拿语义）。"""
        provider = InMemoryProjectSemanticProvider()
        provider.register("project-a", SEMANTIC_A)

        ctx_provider = DefaultProjectContextProvider(
            project_context=_make_project("project-a"),
            explorer=_FakeExplorer(),
            schema_name="project_a",
            semantic_provider=provider,
        )
        project, schema, semantic = ctx_provider.resolve()
        assert project.project_id == "project-a"
        assert schema.schema_name == "project_a"
        assert semantic == SEMANTIC_A

    def test_provider_unknown_project_unavailable_error(self, monkeypatch) -> None:
        """注入的 Provider 未注册该项目 → clear error（503 语义，
        不静默空语义）。"""
        provider = InMemoryProjectSemanticProvider()
        provider.register("project-a", SEMANTIC_A)

        ctx_provider = DefaultProjectContextProvider(
            project_context=_make_project("project-b"),
            explorer=_FakeExplorer(),
            schema_name="project_b",
            semantic_provider=provider,
        )
        with pytest.raises(AIOrchestratorUnavailableError, match="业务语义"):
            ctx_provider.resolve()

    def test_no_provider_falls_back_to_loader_path(self) -> None:
        """未注入 provider → 旧 Loader 路径（缺文件 → 空语义，兼容；
        "no-yaml-project" 在默认 semantic 目录下无 yaml 文件）。"""
        ctx_provider = DefaultProjectContextProvider(
            project_context=_make_project("no-yaml-project"),
            explorer=_FakeExplorer(),
            schema_name="project_a",
        )
        project, schema, semantic = ctx_provider.resolve()
        assert semantic == ProjectSemantic()

    def test_legacy_semantic_loader_param_still_works(self, tmp_path) -> None:
        """旧 semantic_loader 参数保留兼容（既有测试依赖）。"""
        from backend.app.projects.semantic_loader import ProjectSemanticLoader

        (tmp_path / "legacy.yaml").write_text(
            "tables:\n  - table: project_a.inventory\n    business_name: 库存\n",
            encoding="utf-8",
        )
        ctx_provider = DefaultProjectContextProvider(
            project_context=_make_project("legacy"),
            explorer=_FakeExplorer(),
            schema_name="project_a",
            semantic_loader=ProjectSemanticLoader(base_dir=tmp_path),
        )
        _, _, semantic = ctx_provider.resolve()
        assert semantic.tables[0].business_name == "库存"


__all__ = [
    "TestInMemoryProjectSemanticProvider",
    "TestLoaderBackedProjectSemanticProvider",
    "TestSerializerIsolation",
    "TestSelectorWithProjectSemantic",
    "TestComposerIsolation",
    "TestProviderInjection",
]
