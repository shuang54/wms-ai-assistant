"""Schema Explorer Provider 架构验证（Phase 3.7.1.1）。

分两层：

    1. 单元测试（无需数据库，始终运行）：
       - SchemaExplorerService 委托 DatabaseMetadataProvider（fake 注入）
       - engine 与 provider 互斥
       - 旧签名 engine=... 自动包装 PostgreSQLMetadataProvider
       - PostgreSQLMetadataProvider 满足 Protocol（结构兼容）

    2. DB 集成测试（RUN_DB_TESTS=1，默认 skip）：
       验证完整链路仍然成立：
           SchemaExplorerService
                   ↓
           PostgreSQLMetadataProvider
                   ↓
           当前 PostgreSQL（2 业务表 / 19 字段 / PK / FK / COMMENT）
       绝不修改真实业务数据（全部只读 metadata 查询）。
"""
from __future__ import annotations

import os

import pytest

from backend.app.services import schema_explorer_service as sx_module
from backend.app.services.schema_explorer_service import (
    DatabaseMetadataProvider,
    DatabaseSchema,
    PostgreSQLMetadataProvider,
    SchemaTable,
)


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


# ============================================================
# 单元测试（无需数据库）
# ============================================================

class _FakeProvider:
    """记录调用参数的 fake Provider（不触 DB）。"""

    def __init__(self, result: DatabaseSchema) -> None:
        self.result = result
        self.calls: list[dict] = []

    async def inspect(self, *, schema: str | None = None) -> DatabaseSchema:
        self.calls.append({"schema": schema})
        return self.result


def _empty_schema_dto() -> DatabaseSchema:
    return DatabaseSchema(schema_name="public", tables=())


class TestProviderDelegation:
    async def test_service_delegates_to_provider(self) -> None:
        fake = _FakeProvider(_empty_schema_dto())
        service = sx_module.SchemaExplorerService(provider=fake)

        result = await service.inspect(schema="public")

        assert fake.calls == [{"schema": "public"}]
        assert result is fake.result  # 原样透传，不复制不吞错

    async def test_service_default_schema_forwarded(self) -> None:
        fake = _FakeProvider(_empty_schema_dto())
        service = sx_module.SchemaExplorerService(provider=fake)
        await service.inspect()
        assert fake.calls == [{"schema": None}]

    async def test_engine_and_provider_mutually_exclusive(self) -> None:
        fake = _FakeProvider(_empty_schema_dto())
        with pytest.raises(ValueError, match="不能同时指定"):
            sx_module.SchemaExplorerService(engine=object(), provider=fake)

    def test_legacy_engine_signature_wraps_postgres_provider(self) -> None:
        """旧签名 SchemaExplorerService(engine=...) → 内部 PostgreSQL Provider。"""
        sentinel_engine = object()
        service = sx_module.SchemaExplorerService(engine=sentinel_engine)  # type: ignore[arg-type]
        assert isinstance(service._provider, PostgreSQLMetadataProvider)
        assert service._provider._engine is sentinel_engine

    def test_default_construction_uses_postgres_provider(self) -> None:
        service = sx_module.SchemaExplorerService()
        assert isinstance(service._provider, PostgreSQLMetadataProvider)

    def test_postgres_provider_satisfies_protocol(self) -> None:
        """PostgreSQLMetadataProvider 是 DatabaseMetadataProvider 的实现。"""
        provider: DatabaseMetadataProvider = PostgreSQLMetadataProvider()
        assert hasattr(provider, "inspect")

    def test_schema_explorer_service_static_validate_kept(self) -> None:
        """兼容保留：Phase 3.7.1 的静态校验入口仍然可用。"""
        assert sx_module.SchemaExplorerService._validate_schema("public") == "public"
        with pytest.raises(sx_module.SchemaExplorerInputError):
            sx_module.SchemaExplorerService._validate_schema(
                'public"; DROP TABLE x'
            )


# ============================================================
# DB 集成测试（RUN_DB_TESTS=1）：完整链路
# ============================================================

@pytest.fixture(scope="module")
def engine():
    from backend.app.db import reset_engine_cache
    from backend.app.db.session import get_engine

    reset_engine_cache()
    eng = get_engine()
    assert eng is not None, "DATABASE_URL 未配置；请配置后启用 RUN_DB_TESTS=1"
    yield eng


@requires_db
class TestPostgreSQLMetadataProviderDirect:
    """直接使用 PostgreSQLMetadataProvider（不经 Service）读取当前库。"""

    async def test_current_database_business_tables(self, engine) -> None:
        provider = PostgreSQLMetadataProvider(engine=engine)
        result = await provider.inspect(schema="public")

        assert result.schema_name == "public"
        names = {t.name for t in result.tables}
        # 当前库 2 个业务表
        assert names == {"knowledge_document", "knowledge_chunk"}
        # 19 个字段
        assert sum(len(t.columns) for t in result.tables) == 19

    async def test_primary_keys_and_foreign_key(self, engine) -> None:
        provider = PostgreSQLMetadataProvider(engine=engine)
        result = await provider.inspect(schema="public")
        by_name = {t.name: t for t in result.tables}

        for name in ("knowledge_document", "knowledge_chunk"):
            pk_cols = [
                c.name for c in by_name[name].columns if c.is_primary_key
            ]
            assert pk_cols == ["id"]

        chunk = by_name["knowledge_chunk"]
        assert len(chunk.foreign_keys) == 1
        fk = chunk.foreign_keys[0]
        assert fk.source_table == "knowledge_chunk"
        assert fk.source_column == "document_id"
        assert fk.target_table == "knowledge_document"
        assert fk.target_column == "id"

    async def test_comments_present(self, engine) -> None:
        """ORM comment= 写入的 COMMENT 能被 Provider 读回。"""
        provider = PostgreSQLMetadataProvider(engine=engine)
        result = await provider.inspect(schema="public")
        doc = next(t for t in result.tables if t.name == "knowledge_document")
        assert doc.columns[0].name == "id"
        assert doc.columns[0].description is not None
        assert any(c.description for c in doc.columns)


@requires_db
class TestFullChainServiceToProvider:
    """SchemaExplorerService → PostgreSQLMetadataProvider → PostgreSQL。"""

    async def test_service_with_injected_provider(self, engine) -> None:
        provider = PostgreSQLMetadataProvider(engine=engine)
        service = sx_module.SchemaExplorerService(provider=provider)
        result = await service.inspect(schema="public")

        assert {t.name for t in result.tables} == {
            "knowledge_document", "knowledge_chunk",
        }
        assert all(isinstance(t, SchemaTable) for t in result.tables)

    async def test_service_legacy_engine_signature_still_works(
        self, engine
    ) -> None:
        """Phase 3.7.1 的旧调用方式在重构后行为不变。"""
        service = sx_module.SchemaExplorerService(engine=engine)
        direct = await PostgreSQLMetadataProvider(engine=engine).inspect(
            schema="public"
        )
        via_service = await service.inspect(schema="public")
        assert {t.name for t in via_service.tables} == {
            t.name for t in direct.tables
        }
        assert via_service.schema_name == direct.schema_name


__all__ = [
    "TestProviderDelegation",
    "TestPostgreSQLMetadataProviderDirect",
    "TestFullChainServiceToProvider",
]
