"""Project Registry 单元测试（Phase 3.8.1，任务书 §14.1）。

不依赖数据库 / 网络 / LLM。
"""
from __future__ import annotations

import dataclasses

import pytest

from backend.app.config import settings
from backend.app.projects.context import get_default_project_context
from backend.app.projects.models import DataSource, ProjectContext, ProjectContextError
from backend.app.projects.registry import (
    DEFAULT_BUSINESS_SCHEMA,
    InMemoryProjectRegistry,
    ProjectNotFoundError,
    ProjectRegistration,
    ProjectRegistryError,
    get_default_project_registry,
)


def _make_context(project_id: str = "test-project") -> ProjectContext:
    return ProjectContext(
        project_id=project_id,
        project_name=f"Project {project_id}",
        description=None,
        data_source=DataSource(name="primary", type="postgresql"),
    )


def _make_registration(
    project_id: str = "test-project", schema_name: str = "project_a"
) -> ProjectRegistration:
    return ProjectRegistration(
        context=_make_context(project_id),
        schema_name=schema_name,
    )


# ============================================================
# 14.1 Project Registry
# ============================================================

class TestInMemoryProjectRegistry:
    def test_known_project_returns_registration(self) -> None:
        """已注册 project → ProjectRegistration（context + schema_name）。"""
        reg = InMemoryProjectRegistry()
        reg.register("project-a", _make_registration("project-a", "project_a"))

        r = reg.get("project-a")

        assert r.context.project_id == "project-a"
        assert r.context.project_name == "Project project-a"
        assert r.context.data_source.name == "primary"
        assert r.context.data_source.type == "postgresql"
        assert r.schema_name == "project_a"

    def test_unknown_project_clear_error(self) -> None:
        """未知 project → ProjectNotFoundError（不静默回退默认项目）。"""
        reg = InMemoryProjectRegistry()
        reg.register("project-a", _make_registration("project-a"))

        with pytest.raises(ProjectNotFoundError, match="project-b") as ei:
            reg.get("project-b")
        assert ei.value.project_id == "project-b"

    def test_empty_project_id_clear_error(self) -> None:
        """空 / 纯空白 / 非 str project_id → 清晰错误。"""
        reg = InMemoryProjectRegistry()
        reg.register("project-a", _make_registration("project-a"))

        for bad in ("", "   ", None, 123, []):
            with pytest.raises(ProjectRegistryError):
                reg.get(bad)  # type: ignore[arg-type]

    def test_invalid_project_id_format_rejected(self) -> None:
        """非法格式（路径穿越 / 特殊字符）→ 拒绝。"""
        reg = InMemoryProjectRegistry()
        for bad in ("../etc/passwd", "a b", "a/b", "a:b", "a!b"):
            with pytest.raises(ProjectRegistryError):
                reg.get(bad)

    def test_duplicate_registration_rejected(self) -> None:
        """重复注册 → 错误（防意外覆盖已注册数据源）。"""
        reg = InMemoryProjectRegistry()
        reg.register("project-a", _make_registration("project-a"))

        with pytest.raises(ProjectRegistryError, match="已注册"):
            reg.register("project-a", _make_registration("project-a"))

    def test_unregister_then_not_found(self) -> None:
        """unregister 后再 get → ProjectNotFoundError。"""
        reg = InMemoryProjectRegistry()
        reg.register("project-a", _make_registration("project-a"))
        reg.unregister("project-a")

        with pytest.raises(ProjectNotFoundError):
            reg.get("project-a")

    def test_unregister_unknown_rejected(self) -> None:
        with pytest.raises(ProjectRegistryError):
            InMemoryProjectRegistry().unregister("ghost")

    def test_list_project_ids_sorted(self) -> None:
        reg = InMemoryProjectRegistry()
        reg.register("b", _make_registration("b"))
        reg.register("a", _make_registration("a"))
        assert reg.list_project_ids() == ("a", "b")


class TestProjectRegistrationValidation:
    def test_schema_name_must_be_safe_identifier(self) -> None:
        """schema_name 进入 SQL，必须是合法标识符（注入串拒绝）。"""
        for bad in ("", "   ", "public; DROP TABLE x", 'a"b', "pg--x", "1abc"):
            with pytest.raises(ProjectRegistryError):
                ProjectRegistration(context=_make_context(), schema_name=bad)

    def test_valid_schema_names_accepted(self) -> None:
        for good in ("public", "project_a", "project_b", "_internal"):
            assert ProjectRegistration(
                context=_make_context(), schema_name=good
            ).schema_name == good

    def test_context_type_enforced(self) -> None:
        with pytest.raises(ProjectRegistryError):
            ProjectRegistration(context="not-a-context", schema_name="public")  # type: ignore[arg-type]

    def test_frozen(self) -> None:
        r = _make_registration()
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.schema_name = "other"  # type: ignore[misc]

    def test_no_secrets_in_registration(self) -> None:
        """ProjectRegistration / ProjectContext 不含敏感字段（任务书 §五）。"""
        r = _make_registration()
        # dataclass 字段白名单（拒绝未来意外加入 password/url 等字段）
        # Phase 3.8.2：新增 capabilities（能力配置，非敏感）
        assert {f.name for f in dataclasses.fields(r)} == {
            "context", "schema_name", "capabilities"
        }
        ctx_fields = {f.name for f in dataclasses.fields(r.context)}
        assert ctx_fields == {"project_id", "project_name", "description", "data_source"}
        ds_fields = {f.name for f in dataclasses.fields(r.context.data_source)}
        assert ds_fields == {"name", "type"}
        cap_fields = {
            f.name for f in dataclasses.fields(r.capabilities)
        }
        assert cap_fields == {
            "tool_names", "knowledge_enabled", "text_to_sql_enabled"
        }

    def test_project_context_validation_still_enforced(self) -> None:
        """嵌套 ProjectContext 校验仍然生效（empty project_id → ProjectContextError）。"""
        with pytest.raises(ProjectContextError):
            ProjectRegistration(
                context=ProjectContext(
                    project_id=" ",
                    project_name="x",
                    description=None,
                    data_source=DataSource(name="primary", type="postgresql"),
                ),
                schema_name="public",
            )


# ============================================================
# 默认注册表（兼容性，任务书 §十九）
# ============================================================

class TestDefaultRegistry:
    def test_default_registry_contains_configured_project(self) -> None:
        """默认注册表含配置默认项目（vietnam-wms）→ public schema。"""
        reg = get_default_project_registry()
        r = reg.get(settings.project.project_id)

        expected = get_default_project_context()
        assert r.context == expected
        assert r.schema_name == DEFAULT_BUSINESS_SCHEMA == "public"

    def test_default_registry_singleton(self) -> None:
        assert get_default_project_registry() is get_default_project_registry()

    def test_unknown_project_in_default_registry_404_semantics(self) -> None:
        """默认注册表中未知 project → ProjectNotFoundError（API 层 → 404）。"""
        with pytest.raises(ProjectNotFoundError):
            get_default_project_registry().get("not-registered-project")

    def test_default_registration_data_source_is_primary_postgresql(self) -> None:
        r = get_default_project_registry().get(settings.project.project_id)
        assert r.context.data_source.name == "primary"
        assert r.context.data_source.type == "postgresql"


__all__ = [
    "TestInMemoryProjectRegistry",
    "TestProjectRegistrationValidation",
    "TestDefaultRegistry",
]
