"""ProjectContext / DataSource 抽象测试（Phase 3.7.1.1）。

覆盖（全部无需数据库 / LLM）：
    - ProjectContext：创建 / frozen / 缺字段拒绝 / 空字段拒绝 /
      description 可空 / data_source 类型校验
    - DataSource：创建 / frozen / type 开放字符串 / 不保存敏感字段
    - get_default_project_context()：正常返回 / 与配置一致 / 无秘密信息
    - 安全：DTO 的字段集合与 repr 均不含密码 / DATABASE_URL / API Key
"""
from __future__ import annotations

import dataclasses
import os

import pytest

from backend.app.config import settings
from backend.app.projects import (
    DataSource,
    ProjectContext,
    ProjectContextError,
    get_default_project_context,
)
from backend.app.projects.context import (
    DEFAULT_DATA_SOURCE_NAME,
    DEFAULT_DATA_SOURCE_TYPE,
)


# ============================================================
# ProjectContext
# ============================================================

class TestProjectContextCreation:
    def test_create_success(self) -> None:
        ds = DataSource(name="primary", type="postgresql")
        ctx = ProjectContext(
            project_id="vietnam-wms",
            project_name="Vietnam WMS",
            description="Vietnam warehouse management system",
            data_source=ds,
        )
        assert ctx.project_id == "vietnam-wms"
        assert ctx.project_name == "Vietnam WMS"
        assert ctx.description == "Vietnam warehouse management system"
        assert ctx.data_source is ds

    def test_description_can_be_none(self) -> None:
        ctx = ProjectContext(
            project_id="p1",
            project_name="P1",
            description=None,
            data_source=DataSource(name="primary", type="postgresql"),
        )
        assert ctx.description is None

    def test_description_can_be_empty_string(self) -> None:
        ctx = ProjectContext(
            project_id="p1",
            project_name="P1",
            description="",
            data_source=DataSource(name="primary", type="postgresql"),
        )
        assert ctx.description == ""

    def test_missing_project_id_rejected(self) -> None:
        with pytest.raises(TypeError):  # dataclass 必填字段缺失
            ProjectContext(  # type: ignore[call-arg]
                project_name="P1",
                description=None,
                data_source=DataSource(name="primary", type="postgresql"),
            )

    def test_missing_project_name_rejected(self) -> None:
        with pytest.raises(TypeError):
            ProjectContext(  # type: ignore[call-arg]
                project_id="p1",
                description=None,
                data_source=DataSource(name="primary", type="postgresql"),
            )

    @pytest.mark.parametrize("empty", ["", "   "])
    def test_blank_project_id_rejected(self, empty: str) -> None:
        with pytest.raises(ProjectContextError):
            ProjectContext(
                project_id=empty,
                project_name="P1",
                description=None,
                data_source=DataSource(name="primary", type="postgresql"),
            )

    @pytest.mark.parametrize("empty", ["", "   "])
    def test_blank_project_name_rejected(self, empty: str) -> None:
        with pytest.raises(ProjectContextError):
            ProjectContext(
                project_id="p1",
                project_name=empty,
                description=None,
                data_source=DataSource(name="primary", type="postgresql"),
            )

    def test_non_string_description_rejected(self) -> None:
        with pytest.raises(ProjectContextError):
            ProjectContext(
                project_id="p1",
                project_name="P1",
                description=123,  # type: ignore[arg-type]
                data_source=DataSource(name="primary", type="postgresql"),
            )

    def test_non_data_source_rejected(self) -> None:
        with pytest.raises(ProjectContextError):
            ProjectContext(
                project_id="p1",
                project_name="P1",
                description=None,
                data_source="postgresql",  # type: ignore[arg-type]
            )

    def test_frozen_immutable(self) -> None:
        ctx = ProjectContext(
            project_id="p1",
            project_name="P1",
            description=None,
            data_source=DataSource(name="primary", type="postgresql"),
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            ctx.project_id = "x"  # type: ignore[misc]
        with pytest.raises(Exception):
            ctx.data_source = DataSource(name="other", type="mysql")  # type: ignore[misc]

    def test_holds_no_engine_or_session(self) -> None:
        """ProjectContext 不持有 Engine / Session / 连接能力。"""
        ctx = get_default_project_context()
        for attr in vars(ctx):
            value = getattr(ctx, attr)
            assert not hasattr(value, "connect")
            assert not hasattr(value, "execute")
            assert not hasattr(value, "cursor")
        # 没有任何 connect 类方法
        assert not callable(getattr(ctx, "connect", None))


# ============================================================
# DataSource
# ============================================================

class TestDataSource:
    def test_create_success(self) -> None:
        ds = DataSource(name="primary", type="postgresql")
        assert ds.name == "primary"
        assert ds.type == "postgresql"

    def test_type_is_open_string_not_hardcoded(self) -> None:
        """type 是开放字符串，Core 不绑定 PostgreSQL / WMS。"""
        assert DataSource(name="x", type="mysql").type == "mysql"
        assert DataSource(name="x", type="sqlserver").type == "sqlserver"
        assert DataSource(name="x", type="api").type == "api"

    def test_frozen_immutable(self) -> None:
        ds = DataSource(name="primary", type="postgresql")
        with pytest.raises(Exception):
            ds.type = "mysql"  # type: ignore[misc]

    @pytest.mark.parametrize("bad_name", ["", "   ", None, 123])
    def test_blank_name_rejected(self, bad_name) -> None:
        with pytest.raises(ProjectContextError):
            DataSource(name=bad_name, type="postgresql")  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad_type", ["", "   ", None, 123])
    def test_blank_type_rejected(self, bad_type) -> None:
        with pytest.raises(ProjectContextError):
            DataSource(name="primary", type=bad_type)  # type: ignore[arg-type]

    def test_no_secret_fields(self) -> None:
        """DTO 字段集合：只有 name / type，绝无敏感字段。"""
        field_names = {f.name for f in dataclasses.fields(DataSource)}
        assert field_names == {"name", "type"}
        for forbidden in (
            "password", "secret", "url", "api_key", "user", "dsn",
            "connection_string", "credentials",
        ):
            assert forbidden not in field_names

    def test_repr_contains_no_secrets(self) -> None:
        """repr / str 输出不包含环境中的 DATABASE_URL / API Key 内容。"""
        ds = DataSource(name="primary", type="postgresql")
        rendered = repr(ds) + str(ds)
        env_url = os.getenv("DATABASE_URL", "")
        if env_url:
            # 连接串中的密码部分绝不能出现
            if "@" in env_url:
                secret_part = env_url.split("@", 1)[0]
                assert secret_part not in rendered
        for env_key in ("LLM_API_KEY", "EMBEDDING_API_KEY"):
            value = os.getenv(env_key, "")
            if value:
                assert value not in rendered
        assert "password" not in rendered.lower()


# ============================================================
# Default Context
# ============================================================

class TestDefaultProjectContext:
    def test_returns_valid_context(self) -> None:
        ctx = get_default_project_context()
        assert isinstance(ctx, ProjectContext)
        assert ctx.project_id.strip()
        assert ctx.project_name.strip()
        assert isinstance(ctx.data_source, DataSource)
        assert ctx.data_source.name == DEFAULT_DATA_SOURCE_NAME == "primary"
        assert ctx.data_source.type == DEFAULT_DATA_SOURCE_TYPE == "postgresql"

    def test_matches_project_settings(self) -> None:
        ctx = get_default_project_context()
        assert ctx.project_id == settings.project.project_id
        assert ctx.project_name == settings.project.project_name
        # description：空配置 → None
        expected = settings.project.project_description or None
        assert ctx.description == expected

    def test_defaults_are_current_project(self) -> None:
        """配置默认值 = 当前项目（vietnam-wms），且可用环境变量覆盖。"""
        assert settings.project.project_id == "vietnam-wms"
        assert settings.project.project_name == "Vietnam WMS"

    def test_context_has_no_secrets(self) -> None:
        """默认 Context 不保存 / 不暴露 DATABASE_URL / 密码 / API Key。"""
        ctx = get_default_project_context()
        field_names = {f.name for f in dataclasses.fields(ProjectContext)}
        assert field_names == {
            "project_id", "project_name", "description", "data_source",
        }
        rendered = repr(ctx) + str(ctx)
        env_url = os.getenv("DATABASE_URL", "")
        if "@" in env_url:
            assert env_url.split("@", 1)[0] not in rendered
            assert env_url not in rendered
        for env_key in ("LLM_API_KEY", "EMBEDDING_API_KEY"):
            value = os.getenv(env_key, "")
            if value:
                assert value not in rendered

    def test_no_database_connection_responsibility(self) -> None:
        """DataSource 不负责连接：没有 connect / engine 相关属性。"""
        ds = get_default_project_context().data_source
        for forbidden in ("connect", "engine", "session", "url", "password"):
            assert not hasattr(ds, forbidden)


__all__ = [
    "TestProjectContextCreation",
    "TestDataSource",
    "TestDefaultProjectContext",
]
