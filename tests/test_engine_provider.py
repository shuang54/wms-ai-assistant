"""Database Engine Provider 单元测试（Phase 3.8.1）。

不连接任何数据库（create_engine 是惰性的，不发起连接）；
不依赖网络 / LLM。
"""
from __future__ import annotations

import dataclasses

import pytest
from sqlalchemy.engine import Engine

from backend.app.config import DatabaseSettings
from backend.app.projects.engine_provider import (
    DEFAULT_CONNECTION_KEY,
    DatabaseEngineProviderError,
    DataSourceNotFoundError,
    DefaultDatabaseEngineProvider,
    get_default_engine_provider,
)
from backend.app.projects.models import DataSource


def _pg_url(dbname: str) -> str:
    """构造不真实连接的 PostgreSQL URL（create_engine 惰性，不 connect）。"""
    return f"postgresql+psycopg://user:secret@localhost:5432/{dbname}"


def _ds(name: str) -> DataSource:
    return DataSource(name=name, type="postgresql")


# ============================================================
# DataSource → Engine（任务书 §七）
# ============================================================

class TestEngineResolution:
    def test_engine_cached_per_key(self) -> None:
        """同一连接键重复调用返回同一 Engine（进程内缓存）。"""
        provider = DefaultDatabaseEngineProvider(
            extra_connections={
                "db-a": DatabaseSettings(url=_pg_url("db_a")),
            }
        )
        e1 = provider.get_engine(_ds("db-a"))
        e2 = provider.get_engine(_ds("db-a"))
        assert isinstance(e1, Engine)
        assert e1 is e2

    def test_two_connections_two_distinct_engines(self) -> None:
        """两个不同连接键 → 两个不同 Engine（数据源隔离的基础）。"""
        provider = DefaultDatabaseEngineProvider(
            extra_connections={
                "db-a": DatabaseSettings(url=_pg_url("db_a")),
                "db-b": DatabaseSettings(url=_pg_url("db_b")),
            }
        )
        ea = provider.get_engine(_ds("db-a"))
        eb = provider.get_engine(_ds("db-b"))
        assert isinstance(ea, Engine) and isinstance(eb, Engine)
        assert ea is not eb
        assert str(ea.url).endswith("db_a")
        assert str(eb.url).endswith("db_b")

    def test_unknown_data_source_rejected(self) -> None:
        """未注册的连接键 → DataSourceNotFoundError（用户不能指定任意连接）。"""
        provider = DefaultDatabaseEngineProvider()
        with pytest.raises(DataSourceNotFoundError, match="db-evil") as ei:
            provider.get_engine(_ds("db-evil"))
        assert ei.value.data_source_name == "db-evil"

    def test_primary_delegates_to_global_engine(self) -> None:
        """primary 键 → db.session.get_engine()（共享全局连接池）。"""
        from backend.app.db import session as db_session

        provider = DefaultDatabaseEngineProvider()
        if not db_session.get_engine():
            pytest.skip("DATABASE_URL 未配置，无法验证 primary 委托")
        engine = provider.get_engine(_ds(DEFAULT_CONNECTION_KEY))
        assert engine is db_session.get_engine()

    def test_primary_unavailable_without_database_url(self) -> None:
        """DATABASE_URL 为空 → primary 解析抛清晰错误（不静默降级）。"""
        from backend.app.db import session as db_session

        provider = DefaultDatabaseEngineProvider()
        if db_session.get_engine() is not None:
            pytest.skip("DATABASE_URL 已配置；空 URL 场景由其它单元覆盖")
        with pytest.raises(DatabaseEngineProviderError, match="primary"):
            provider.get_engine(_ds(DEFAULT_CONNECTION_KEY))

    def test_non_primary_empty_url_rejected_at_register(self) -> None:
        """URL 为空的连接注册 → 拒绝。"""
        provider = DefaultDatabaseEngineProvider()
        with pytest.raises(DatabaseEngineProviderError, match="为空"):
            provider.register_connection(
                "db-empty", DatabaseSettings(url="")
            )

    def test_reset_cache_rebuilds_engine(self) -> None:
        """reset_cache 后重建（测试辅助）。"""
        provider = DefaultDatabaseEngineProvider(
            extra_connections={"db-a": DatabaseSettings(url=_pg_url("db_a"))}
        )
        e1 = provider.get_engine(_ds("db-a"))
        provider.reset_cache()
        e2 = provider.get_engine(_ds("db-a"))
        assert e1 is not e2


# ============================================================
# 服务器端注册边界（任务书 §八 安全）
# ============================================================

class TestRegistrationBoundary:
    def test_primary_key_cannot_be_overridden(self) -> None:
        """primary 保留键不可覆盖（映射全局 DATABASE_URL）。"""
        provider = DefaultDatabaseEngineProvider()
        with pytest.raises(DatabaseEngineProviderError, match="保留"):
            provider.register_connection(
                DEFAULT_CONNECTION_KEY,
                DatabaseSettings(url=_pg_url("evil")),
            )

    def test_duplicate_connection_rejected(self) -> None:
        provider = DefaultDatabaseEngineProvider()
        provider.register_connection("db-a", DatabaseSettings(url=_pg_url("a")))
        with pytest.raises(DatabaseEngineProviderError, match="已注册"):
            provider.register_connection("db-a", DatabaseSettings(url=_pg_url("b")))

    def test_empty_key_rejected(self) -> None:
        provider = DefaultDatabaseEngineProvider()
        with pytest.raises(DatabaseEngineProviderError):
            provider.register_connection("  ", DatabaseSettings(url=_pg_url("a")))

    def test_get_engine_requires_datasource_instance(self) -> None:
        provider = DefaultDatabaseEngineProvider()
        with pytest.raises(DatabaseEngineProviderError):
            provider.get_engine("primary")  # type: ignore[arg-type]

    def test_no_url_string_parameter_anywhere(self) -> None:
        """get_engine / register_connection 均不接受 URL 字符串
        （凭据只能经 DatabaseSettings 从服务器端进入；HTTP 层无法注入）。"""
        provider = DefaultDatabaseEngineProvider()
        # DataSource 只有 name/type —— 没有任何承载 URL/密码的字段
        fields = {f.name for f in dataclasses.fields(DataSource)}
        assert fields == {"name", "type"}

    def test_default_provider_singleton(self) -> None:
        assert get_default_engine_provider() is get_default_engine_provider()


__all__ = [
    "TestEngineResolution",
    "TestRegistrationBoundary",
]
