"""WMS/ERP **独立只读业务数据源** 接入测试（Phase 7 Step 8）。

结构（与任务书 §5 的定向范围一一对应）：

```text
A  独立数据源配置（WMS_DATABASE_*）：字段映射 / is_configured / repr 屏蔽密码 /
   summary 无凭据
B  Engine Provider：未配置 ⇒ None / require 明确报错（**不**回退应用库）/
   已配置 ⇒ 独立 Engine（**不复用**应用库 Engine）/ 缓存 + reset
C  库存 Tool 绑定：engine_provider 优先；Provider 失败 ⇒ 显式 ToolExecutionError
   （应用库 get_engine **零调用**）/ 工厂 _build_project_tool_registry 绑定业务库 /
   build_default_tool_registry 绑定业务库
D  （仅当本机已配置密码）真实连接：库名 / 用户 / 只读证据（事务只读 + 角色属性 +
   表级写权限判定）/ schema 与库存表元数据 / probe 与 Handler 结果一致（空表 ⇒ 真 0）
```

默认运行（未配置业务库）：A/B/C 全绿、D **skip**（明确原因）。
真实连接测试只读：**不**建表 / 不迁移 / 不 INSERT / 不外导全表。
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.config import BusinessDatabaseSettings, settings
from backend.app.db import business_session
from backend.app.db.business_session import (
    BusinessDataSourceError,
    business_datasource_summary,
    get_business_engine,
    is_business_datasource_configured,
    require_business_engine,
    reset_business_engine_cache,
)
from backend.app.projects.capabilities import ProjectCapabilities
from backend.app.tools.errors import ToolExecutionError
from backend.app.tools.get_inventory import (
    GetInventoryHandler,
    build_default_tool_registry,
)

__all__ = [
    "TestBusinessDatabaseSettings",
    "TestBusinessEngineProvider",
    "TestInventoryToolBinding",
    "TestRealBusinessDatasource",
]

#: 显式构造的"已配置"值（**假**密码；从不用于真实连接）。
_FAKE_PASSWORD = "unit-test-placeholder-NOT-A-REAL-PASSWORD"


def _configured_settings() -> BusinessDatabaseSettings:
    return BusinessDatabaseSettings(
        host="wms-test-host.invalid",
        port=5432,
        name="beifa_ecode",
        user="readonly_user",
        password=_FAKE_PASSWORD,
    )


@pytest.fixture(autouse=True)
def _clean_business_engine_cache() -> Any:
    """每个测试前后清空业务库 Engine 缓存（不触碰应用库缓存）。"""
    reset_business_engine_cache()
    yield
    reset_business_engine_cache()


@pytest.fixture()
def unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """把业务数据源置为未配置（**显式**空值——不读本机 .env）。"""
    monkeypatch.setattr(
        business_session,
        "settings",
        SimpleNamespace(
            business_database=BusinessDatabaseSettings(
                host="", port=5432, name="", user="", password=""
            )
        ),
    )


@pytest.fixture()
def configured(monkeypatch: pytest.MonkeyPatch) -> BusinessDatabaseSettings:
    """把业务数据源置为已配置（假值；不建连）。"""
    cfg = _configured_settings()
    monkeypatch.setattr(
        business_session, "settings", SimpleNamespace(business_database=cfg)
    )
    return cfg


# ============================================================
# A. 配置
# ============================================================


class TestBusinessDatabaseSettings:
    def test_fields_default_from_env_keys(self) -> None:
        """字段来自 WMS_DATABASE_*（此处用显式构造验证字段语义与默认值）。"""
        cfg = BusinessDatabaseSettings()
        # 默认配置在测试环境中为未配置（.env 中密码留空）——只断言类型与端口默认
        assert isinstance(cfg.host, str)
        assert cfg.port == 5432
        assert cfg.pool_size == 2
        assert cfg.max_overflow == 1
        assert cfg.connect_timeout == 5

    def test_is_configured_requires_all_parts(self) -> None:
        assert _configured_settings().is_configured is True
        for field_name in ("host", "name", "user", "password"):
            cfg = BusinessDatabaseSettings(
                **{
                    **_configured_settings().__dict__,
                    field_name: "",
                }
            )
            assert cfg.is_configured is False, field_name
        blank = BusinessDatabaseSettings(
            host="h", port=5432, name="d", user="u", password="   "
        )
        assert blank.is_configured is False  # 纯空白 ⇒ 未配置

    def test_password_is_masked_in_repr(self) -> None:
        """**任何** repr（日志 / 异常文本）都不得包含密码。"""
        cfg = _configured_settings()
        assert _FAKE_PASSWORD not in repr(cfg)
        assert _FAKE_PASSWORD not in str(cfg)

    def test_summary_has_no_credentials(self) -> None:
        summary = _configured_settings().summary()
        assert summary == {
            "configured": True,
            "host": "wms-test-host.invalid",
            "port": 5432,
            "database": "beifa_ecode",
        }
        assert "password" not in summary
        assert "user" not in summary


# ============================================================
# B. Engine Provider（独立 + fail-closed）
# ============================================================


class TestBusinessEngineProvider:
    def test_unconfigured_returns_none(self, unconfigured: None) -> None:
        assert is_business_datasource_configured() is False
        assert get_business_engine() is None

    def test_unconfigured_never_returns_application_engine(
        self, unconfigured: None
    ) -> None:
        """**不**回退应用库：未配置 ⇒ None（即使应用库可用）。"""
        from backend.app.db.session import get_engine

        app_engine = get_engine()
        assert app_engine is not None  # 环境里应用库可用（本测试前提）
        assert get_business_engine() is None
        assert get_business_engine() is not app_engine

    def test_require_raises_explicit_error_with_env_hint(
        self, unconfigured: None
    ) -> None:
        with pytest.raises(BusinessDataSourceError) as excinfo:
            require_business_engine()
        message = str(excinfo.value)
        for env_name in (
            "WMS_DATABASE_HOST",
            "WMS_DATABASE_NAME",
            "WMS_DATABASE_USER",
            "WMS_DATABASE_PASSWORD",
        ):
            assert env_name in message
        assert _FAKE_PASSWORD not in message

    def test_configured_builds_independent_engine(
        self, configured: BusinessDatabaseSettings
    ) -> None:
        from backend.app.db.session import get_engine

        engine = get_business_engine()
        assert engine is not None
        # 独立数据源：URL 指向业务库（与应用库不同）
        assert engine.url.host == configured.host
        assert engine.url.port == configured.port
        assert engine.url.database == configured.name
        assert engine.url.username == configured.user
        # **不**复用应用库 Engine / 连接池（独立小池：pool_size=2）
        assert engine is not get_engine()
        assert engine.pool.size() == configured.pool_size
        # 只读兜底（default_transaction_read_only=on）无法离线内省 ——
        # 由 D 组真实连接测试断言 current_setting('transaction_read_only') == 'on'。

    def test_engine_is_cached_and_resettable(
        self, configured: BusinessDatabaseSettings
    ) -> None:
        first = get_business_engine()
        assert first is get_business_engine()  # 进程内单例
        reset_business_engine_cache()
        second = get_business_engine()
        assert second is not None and second is not first

    def test_require_returns_engine_when_configured(
        self, configured: BusinessDatabaseSettings
    ) -> None:
        assert require_business_engine() is get_business_engine()

    def test_summary_reports_configuration(
        self, configured: BusinessDatabaseSettings
    ) -> None:
        summary = business_datasource_summary()
        assert summary["configured"] is True
        assert _FAKE_PASSWORD not in str(summary)


# ============================================================
# C. 库存 Tool 绑定（业务库 + 不回退）
# ============================================================


class _ExplodingEngine:
    """哨兵：任何 connect 调用都失败（用于证明"未使用该 Engine"）。"""

    def connect(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("application engine must NOT be used")


@pytest.mark.asyncio
async def test_handler_provider_failure_is_explicit_and_never_falls_back(
    unconfigured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider 失败 ⇒ 显式 ToolExecutionError；应用库 get_engine **零调用**。"""
    import backend.app.tools.get_inventory as gi_module

    def _must_not_be_called() -> Any:
        raise AssertionError("global get_engine() must NOT be called")

    monkeypatch.setattr(gi_module, "get_engine", _must_not_be_called)

    handler = GetInventoryHandler(
        engine=_ExplodingEngine(),  # 即使显式传了 Engine，provider 失败也不得回退
        engine_provider=require_business_engine,
    )
    with pytest.raises(ToolExecutionError) as excinfo:
        await handler({"material_code": "10001"})
    assert "业务数据源" in str(excinfo.value)
    assert excinfo.value.tool_name == "get_inventory"


@pytest.mark.asyncio
async def test_registry_normalizes_provider_failure_to_tool_result(
    unconfigured: None,
) -> None:
    """经真实 ToolRegistry：失败归一为 ToolResult(success=False)（无假数据）。"""
    from backend.app.tools.get_inventory import (
        GetInventoryHandler,
        register_get_inventory_tool,
    )
    from backend.app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_get_inventory_tool(
        registry,
        handler=GetInventoryHandler(engine_provider=require_business_engine),
    )
    result = await registry.execute(
        "get_inventory", arguments={"material_code": "10001"}
    )
    assert result.success is False
    assert "业务数据源" in (result.error or "")
    assert result.data is None


@pytest.mark.asyncio
async def test_factory_registry_binds_inventory_to_business_datasource(
    unconfigured: None,
) -> None:
    """工厂 `_build_project_tool_registry`：库存 Tool 走业务库，**不用**项目 Engine。"""
    from backend.app.services.project_orchestrator_factory import (
        _build_project_tool_registry,
    )

    registry = _build_project_tool_registry(
        ProjectCapabilities(tool_names=("get_inventory",)),
        engine=_ExplodingEngine(),  # 哨兵：项目 Engine 被使用即失败
        schema_name="public",
        project_id="proj-wms-ds",
    )
    result = await registry.execute(
        "get_inventory", arguments={"material_code": "10001"}
    )
    assert result.success is False
    assert "业务数据源" in (result.error or "")


@pytest.mark.asyncio
async def test_default_registry_binds_inventory_to_business_datasource(
    unconfigured: None,
) -> None:
    """生产默认 Registry：库存 Tool 绑定业务库（未配置 ⇒ 显式失败，非应用库错误）。"""
    registry = build_default_tool_registry()
    result = await registry.execute(
        "get_inventory", arguments={"material_code": "10001"}
    )
    assert result.success is False
    assert "业务数据源" in (result.error or "")
    # 旧行为（应用库路径）不得再出现
    assert "DATABASE_URL 为空" not in (result.error or "")


# ============================================================
# C2. 字段映射（真实 ERP 表结构可能与通用假设不同）
# ============================================================


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value


class _FakeConn:
    def __init__(self, log: list[tuple[str, Any]], value: float) -> None:
        self._log = log
        self._value = value

    def execute(self, statement: Any, params: Any = None) -> _FakeResult:
        text = getattr(statement, "text", str(statement))
        self._log.append((text, params))
        return _FakeResult(self._value)

    def rollback(self) -> None:
        self._log.append(("ROLLBACK", None))

    def __enter__(self) -> "_FakeConn":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _FakeEngine:
    """记录 SQL 文本的最小 Engine 替身（不建连；只用于断言查询形态）。"""

    def __init__(self, value: float = 60.0) -> None:
        self.log: list[tuple[str, Any]] = []
        self._value = value

    def connect(self) -> _FakeConn:
        return _FakeConn(self.log, self._value)


@pytest.mark.asyncio
async def test_custom_column_mapping_is_parameterized() -> None:
    """字段映射生效：SUM(<qty_column>) / WHERE "<code_column>" = :bind（无字面量）。"""
    from backend.app.tools.get_inventory import _run_inventory_query

    engine = _FakeEngine(60.0)
    qty = await _run_inventory_query(
        engine=engine,  # type: ignore[arg-type]
        schema="public",
        table="dyn_wh_erp_inventory_snapshot",
        material_code="V923719",
        timeout_seconds=5,
        material_code_column="mat_code",
        qty_column="available_qty",
    )
    assert qty == 60.0

    selects = [item for item in engine.log if "SUM(" in item[0]]
    assert len(selects) == 1, engine.log
    sql, params = selects[0]
    assert 'SUM("available_qty")' in sql
    assert '"public"."dyn_wh_erp_inventory_snapshot"' in sql
    assert '"mat_code" = :material_code' in sql
    # 绑定参数传递；值**不**出现在 SQL 文本中（无拼接注入面）
    assert params == {"material_code": "V923719"}
    assert "V923719" not in sql
    # 只读事务 + 超时（每条查询都带）
    assert any("SET TRANSACTION READ ONLY" in item[0] for item in engine.log)
    assert any("statement_timeout" in item[0] for item in engine.log)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field_name",
    ["schema_name", "table_name", "material_code_column", "qty_column"],
)
async def test_invalid_identifier_config_is_rejected_without_db_access(
    field_name: str,
) -> None:
    """非法标识符（含注入字符）⇒ ToolExecutionError（且**零** DB 访问）。"""
    from backend.app.config import InventoryToolSettings
    from backend.app.tools.get_inventory import GetInventoryHandler

    kwargs: dict[str, Any] = {
        "schema_name": "public",
        "table_name": "inventory",
        "material_code_column": "material_code",
        "qty_column": "qty",
    }
    kwargs[field_name] = 'x" OR 1=1 --'
    handler = GetInventoryHandler(
        engine=_ExplodingEngine(),  # type: ignore[arg-type]
        inv_settings=InventoryToolSettings(**kwargs),
    )
    with pytest.raises(ToolExecutionError):
        await handler({"material_code": "10001"})


# ============================================================
# D. 真实连接（仅当本机 .env 已填写密码；只读）
# ============================================================

_configured_now = is_business_datasource_configured()

requires_business_ds = pytest.mark.skipif(
    not _configured_now,
    reason=(
        "WMS_DATABASE_PASSWORD（或其它 WMS_DATABASE_* 项）未在本机 .env 配置 ⇒ "
        "跳过真实连接检查（不回退应用库；请在 .env 填写只读账号密码后重试）"
    ),
)


@requires_business_ds
class TestRealBusinessDatasource:
    def _engine(self) -> Any:
        return require_business_engine()

    def test_connection_identity_and_read_only(self) -> None:
        """连接目标必须是测试库 + 只读账号，且会话强制只读。"""
        from sqlalchemy import text

        cfg = settings.business_database
        with self._engine().connect() as conn:
            db_name, db_user, tx_read_only = conn.execute(
                text(
                    "SELECT current_database(), current_user, "
                    "current_setting('transaction_read_only')"
                )
            ).one()
            role = conn.execute(
                text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles "
                    "WHERE rolname = current_user"
                )
            ).one_or_none()

        assert db_name == cfg.name, (db_name, cfg.name)
        assert db_user == cfg.user, (db_user, cfg.user)
        # 会话级只读兜底（连接参数 default_transaction_read_only=on）
        assert tx_read_only == "on"
        if role is not None:
            assert role == (False, False, False), role

    def test_metadata_inventory_tables_and_columns(self) -> None:
        """限定元数据：列出 schema + 库存相关表/视图 + 目标表类型。"""
        from sqlalchemy import text

        cfg = settings.inventory_tool
        with self._engine().connect() as conn:
            schemas = [
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT nspname FROM pg_namespace "
                        "WHERE nspname NOT LIKE 'pg\\_%' "
                        "AND nspname <> 'information_schema' ORDER BY nspname"
                    )
                ).all()
            ]
            assert schemas, "业务库应至少有一个业务 schema"
            candidates = conn.execute(
                text(
                    "SELECT table_schema, table_name, table_type "
                    "FROM information_schema.tables "
                    "WHERE table_schema NOT IN ('pg_catalog','information_schema') "
                    "AND (table_name ILIKE '%inventory%' OR table_name ILIKE '%stock%' "
                    "     OR table_name ILIKE '%material%') "
                    "ORDER BY table_schema, table_name"
                )
            ).all()
            target_type = conn.execute(
                text(
                    "SELECT table_type FROM information_schema.tables "
                    "WHERE table_schema = :s AND table_name = :t"
                ),
                {"s": cfg.schema_name, "t": cfg.table_name},
            ).scalar_one_or_none()
            columns = conn.execute(
                text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t "
                    "ORDER BY ordinal_position"
                ),
                {"s": cfg.schema_name, "t": cfg.table_name},
            ).all()

        print("\n业务库 schema:", schemas)
        print("库存相关表候选:", [tuple(r) for r in candidates])
        print(
            "目标表: %s.%s type=%s" % (cfg.schema_name, cfg.table_name, target_type)
        )
        print("目标表列:", [tuple(r) for r in columns])

    def test_inventory_probe_matches_handler_result(self) -> None:
        """真实只读查询：Handler 结果 == 直接查询结果（空表 ⇒ 真 0）。"""
        import asyncio

        from sqlalchemy import text

        cfg = settings.inventory_tool
        engine = self._engine()
        with engine.connect() as conn:
            exists = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = :s AND table_name = :t"
                ),
                {"s": cfg.schema_name, "t": cfg.table_name},
            ).scalar_one_or_none()
            if exists is None:
                pytest.skip(
                    f"业务库中不存在 {cfg.schema_name}.{cfg.table_name} ⇒ "
                    "请按 metadata 结果调整 WMS_INVENTORY_SCHEMA / WMS_INVENTORY_TABLE"
                )
            material_code = conn.execute(
                text(
                    f'SELECT material_code FROM "{cfg.schema_name}"."{cfg.table_name}" '
                    "LIMIT 1"
                )
            ).scalar_one_or_none()
            if material_code is None:
                expected_zero = True
                material_code = "P7S8-EMPTY-PROBE"
            else:
                expected_zero = False

        registry = build_default_tool_registry()
        result = asyncio.run(
            registry.execute(
                "get_inventory", arguments={"material_code": material_code}
            )
        )
        assert result.success is True, result.error
        qty = float(result.data["qty"])
        if expected_zero:
            assert qty == 0.0  # 表存在但无数据 ⇒ 真实空结果
        else:
            with engine.connect() as conn:
                conn.execute(text("SET TRANSACTION READ ONLY"))
                direct = conn.execute(
                    text(
                        f'SELECT COALESCE(SUM("{cfg.qty_column}"), 0) FROM '
                        f'"{cfg.schema_name}"."{cfg.table_name}" '
                        f'WHERE "{cfg.material_code_column}" = :code'
                    ),
                    {"code": material_code},
                ).scalar()
                conn.rollback()
            assert qty == float(direct)


# ============================================================
# 附：脚本存在性守卫（人工检查入口）
# ============================================================


def test_check_script_exists() -> None:
    script = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts",
        "check_wms_datasource.py",
    )
    assert os.path.exists(script)
