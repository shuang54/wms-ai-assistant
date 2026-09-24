"""Project Context 数据源切换集成测试（Phase 3.8.1）。

默认 SKIP；``RUN_DB_TESTS=1``（+ DATABASE_URL）后执行。

覆盖任务书验收标准（§二十一）：

    14.1 Project Registry Test          → tests/test_project_registry.py（单元）
    14.2 DataSource Isolation Test      → 本文件 TestDataSourceIsolation
    Tool E2E（§十二）                    → 本文件 TestToolDataSourceSwitch
    Text-to-SQL E2E（§十五）             → 本文件 TestTextToSqlDataSourceSwitch
    Cross-project Security（§十六）      → 本文件 TestCrossProjectSecurity
    API E2E（§十七）                     → 本文件 TestApiProjectE2E

测试数据源布局（单 PostgreSQL 实例，用不同 schema 验证切换，任务书 §六）：

    schema project_a: inventory 表，material 10001 → qty 100
    schema project_b: inventory 表，material 10001 → qty 999

验证目标：

    project-a → project_a.inventory → 100
    project-b → project_b.inventory → 999

Text-to-SQL 的 LLM Generator 用**确定性 Fake**（记录 database_context、
返回固定 SQL），真实 LLM 生成不在本文件范围（成本控制 + 确定性）；
Schema Explorer / SQL Validator / SQL Executor / Tool 查询全部真实。
"""
from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.config import settings
from backend.app.projects.models import DataSource, ProjectContext
from backend.app.projects.registry import (
    InMemoryProjectRegistry,
    ProjectRegistration,
)
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorExecutionError,
)


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not (_env_flag("RUN_DB_TESTS") and settings.database.url.strip()),
    reason="set RUN_DB_TESTS=1 (plus DATABASE_URL) to enable project "
    "datasource isolation tests",
)

SCHEMA_A = "project_a"
SCHEMA_B = "project_b"

QUESTION_TOOL = "查询物料 10001 当前库存"
QUESTION_T2S = "统计所有物料的库存汇总明细"


# ============================================================
# Fixtures：schema 隔离的数据准备 / 清理
# ============================================================

@pytest.fixture(scope="module")
def engine():
    from backend.app.db import reset_engine_cache
    from backend.app.db.base import Base
    from backend.app.db.session import get_engine

    reset_engine_cache()
    eng = get_engine()
    assert eng is not None, "DATABASE_URL 未配置"

    from backend.app.db import models  # noqa: F401  # register models

    Base.metadata.create_all(eng)
    yield eng


@pytest.fixture(scope="module", autouse=True)
def _schemas(engine):
    """module 级 autouse：创建 project_a / project_b 两个 schema + 隔离数据。

    teardown 时 DROP SCHEMA ... CASCADE（只删测试 schema，不动 public）。
    （requires_db skip 的用例不会触发 fixture setup）
    """
    with engine.begin() as conn:
        for schema in (SCHEMA_A, SCHEMA_B):
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            conn.execute(text(
                f'CREATE TABLE "{schema}"."inventory" ('
                f"  material_code VARCHAR(64) PRIMARY KEY,"
                f"  qty NUMERIC NOT NULL)"
            ))
        conn.execute(text(
            f'INSERT INTO "{SCHEMA_A}"."inventory" (material_code, qty) '
            f"VALUES ('10001', 100)"
        ))
        conn.execute(text(
            f'INSERT INTO "{SCHEMA_B}"."inventory" (material_code, qty) '
            f"VALUES ('10001', 999)"
        ))
    yield
    with engine.begin() as conn:
        for schema in (SCHEMA_A, SCHEMA_B):
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def _registration(project_id: str, schema_name: str) -> ProjectRegistration:
    return ProjectRegistration(
        context=ProjectContext(
            project_id=project_id,
            project_name=f"Project {project_id}",
            description=None,
            data_source=DataSource(name="primary", type="postgresql"),
        ),
        schema_name=schema_name,
    )


@contextmanager
def _test_registry():
    """构造含 project-a / project-b 的测试注册表（数据源均为 primary
    连接 → 全局 Engine；schema 分别为 project_a / project_b）。"""
    registry = InMemoryProjectRegistry()
    registry.register("project-a", _registration("project-a", SCHEMA_A))
    registry.register("project-b", _registration("project-b", SCHEMA_B))
    yield registry


def _build_project_orchestrator(project_id: str):
    """构造绑定项目数据源的 Orchestrator（真实依赖，base 为全局默认）。"""
    from backend.app.api import orchestrator_chat as orch_module
    from backend.app.services.project_orchestrator_factory import (
        build_orchestrator_for_project,
    )

    with _test_registry() as registry:
        orch = build_orchestrator_for_project(
            project_id,
            base=orch_module._default_orchestrator,
            registry=registry,
        )
    return orch


class _RecordingT2SGenerator:
    """确定性 Fake Text-to-SQL Generator（不调 LLM）。

    记录收到的 database_context / allowed_tables / schema，
    返回固定 SQL（LIMIT 满足 Validator 要求）。
    """

    def __init__(self, sql_to_return: str) -> None:
        self.sql_to_return = sql_to_return
        self.calls: list[dict] = []

    async def generate(
        self,
        question: str,
        *,
        database_context: str,
        allowed_tables=None,
        schema=None,
        max_rows: int = 1000,
    ):
        self.calls.append(
            {
                "question": question,
                "database_context": database_context,
                "allowed_tables": tuple(allowed_tables or ()),
                "schema_name": getattr(schema, "schema_name", None),
            }
        )
        return SimpleNamespace(sql=self.sql_to_return)


# ============================================================
# 14.2 DataSource Isolation（引擎 / schema 真实切换）
# ============================================================

@requires_db
class TestDataSourceIsolation:
    def test_registry_resolves_different_schemas(self) -> None:
        """project-a / project-b → 不同 schema_name（数据源身份不同）。"""
        with _test_registry() as registry:
            assert registry.get("project-a").schema_name == SCHEMA_A
            assert registry.get("project-b").schema_name == SCHEMA_B
            assert (
                registry.get("project-a").context.project_id == "project-a"
            )

    def test_per_project_schema_inspection_isolated(self) -> None:
        """Schema Explorer 按 project_id inspect 对应 schema：
        project-a 只见 project_a 的表，project-b 只见 project_b 的表。"""
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )

        for schema in (SCHEMA_A, SCHEMA_B):
            explorer = SchemaExplorerService()
            db_schema = asyncio.run(explorer.inspect(schema=schema))
            tables = {t.name for t in db_schema.tables}
            assert tables == {"inventory"}, (
                f"{schema} 应只含 inventory 表（实际: {tables}）"
            )
            assert db_schema.schema_name == schema

    def test_per_project_executor_returns_different_data(self) -> None:
        """同一 Engine 上按项目 schema 隔离：直接 SQL 查询两个 schema
        返回 100 / 999（数据隔离基础）。"""
        from backend.app.services.sql_executor_service import SQLExecutorService

        executor = SQLExecutorService()
        for schema, expected in ((SCHEMA_A, 100.0), (SCHEMA_B, 999.0)):
            result = asyncio.run(executor.execute(
                f'SELECT qty FROM "{schema}"."inventory" '
                f"WHERE material_code = '10001' LIMIT 1"
            ))
            assert result.row_count == 1
            assert float(result.rows[0][0]) == expected


# ============================================================
# Tool 数据源切换（任务书 §十二）
# ============================================================

@requires_db
class TestToolDataSourceSwitch:
    def test_project_a_tool_returns_100(self) -> None:
        """project-a → get_inventory → qty 100（真实 DB 查询，无 LLM）。"""
        orch = _build_project_orchestrator("project-a")
        result = asyncio.run(orch.execute(QUESTION_TOOL))

        assert result.route.value == "tool"
        assert result.data is not None and result.data.success
        assert result.data.data["qty"] == 100.0
        assert result.data.data["project_id"] == "project-a"
        assert result.data.data["material_code"] == "10001"

    def test_project_b_tool_returns_999(self) -> None:
        """project-b → get_inventory → qty 999（证明真正切换）。"""
        orch = _build_project_orchestrator("project-b")
        result = asyncio.run(orch.execute(QUESTION_TOOL))

        assert result.route.value == "tool"
        assert result.data is not None and result.data.success
        assert result.data.data["qty"] == 999.0
        assert result.data.data["project_id"] == "project-b"


# ============================================================
# Text-to-SQL 数据源切换（任务书 §十五）
# ============================================================

@requires_db
class TestTextToSqlDataSourceSwitch:
    def _run(self, project_id: str, schema: str) -> tuple:
        """跑一次 project 的 T2S 链路，返回 (orchestrator 结果, generator 记录)。"""
        orch = _build_project_orchestrator(project_id)
        fake_gen = _RecordingT2SGenerator(
            "SELECT material_code, qty FROM inventory LIMIT 10"
        )
        orch._text_to_sql = fake_gen  # type: ignore[assignment]
        result = asyncio.run(orch.execute(QUESTION_T2S))
        return result, fake_gen

    def test_project_a_t2s_uses_schema_a(self) -> None:
        """project-a：Schema A → T2S → Validator → Executor A → 100。"""
        result, gen = self._run("project-a", SCHEMA_A)

        assert result.route.value == "text_to_sql"
        # Generator 收到的上下文来自 project_a schema
        assert len(gen.calls) == 1
        assert gen.calls[0]["schema_name"] == SCHEMA_A
        assert "inventory" in gen.calls[0]["database_context"]
        # 执行结果来自 project_a 数据
        assert result.data.row_count == 1
        assert float(result.data.rows[0][1]) == 100.0
        assert result.metadata["project_id"] == "project-a"

    def test_project_b_t2s_uses_schema_b(self) -> None:
        """project-b：Schema B → T2S → Validator → Executor B → 999。"""
        result, gen = self._run("project-b", SCHEMA_B)

        assert result.route.value == "text_to_sql"
        assert gen.calls[0]["schema_name"] == SCHEMA_B
        assert result.data.row_count == 1
        assert float(result.data.rows[0][1]) == 999.0
        assert result.metadata["project_id"] == "project-b"

    def test_t2s_results_differ_between_projects(self) -> None:
        """两个项目执行同一问题 → 结果不同（100 vs 999）。"""
        ra, _ = self._run("project-a", SCHEMA_A)
        rb, _ = self._run("project-b", SCHEMA_B)
        qty_a = float(ra.data.rows[0][1])
        qty_b = float(rb.data.rows[0][1])
        assert qty_a != qty_b
        assert {qty_a, qty_b} == {100.0, 999.0}


# ============================================================
# 跨项目访问安全（任务书 §十六）
# ============================================================

@requires_db
class TestCrossProjectSecurity:
    def _schema_a_executor_and_schema(self):
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )
        from backend.app.services.sql_executor_service import SQLExecutorService

        explorer = SchemaExplorerService()
        schema_a = asyncio.run(explorer.inspect(schema=SCHEMA_A))
        executor = SQLExecutorService()
        return schema_a, executor

    def test_cross_schema_table_reference_rejected(self) -> None:
        """project-a 上下文执行 `SELECT * FROM project_b.inventory`
        → SQLValidator UNKNOWN_TABLE 拒绝（不执行）。"""
        schema_a, executor = self._schema_a_executor_and_schema()

        with pytest.raises(Exception) as ei:
            asyncio.run(executor.execute(
                f'SELECT * FROM "{SCHEMA_B}"."inventory" LIMIT 10',
                schema=schema_a,
                allowed_tables=(f"{SCHEMA_A}.inventory",),
            ))
        # 必须是校验拒绝（而非执行成功 / 其它错误）
        assert type(ei.value).__name__ == "SQLExecutorValidationError"
        assert "project_b" in str(ei.value) or "not exist" in str(
            ei.value
        ) or "UNKNOWN" in str(ei.value) or "不存在" in str(ei.value)

    def test_unknown_table_in_project_schema_rejected(self) -> None:
        """引用 project_a schema 中不存在的表 → 拒绝。"""
        schema_a, executor = self._schema_a_executor_and_schema()

        with pytest.raises(Exception) as ei:
            asyncio.run(executor.execute(
                "SELECT * FROM orders LIMIT 10",
                schema=schema_a,
            ))
        assert type(ei.value).__name__ == "SQLExecutorValidationError"

    def test_malicious_sql_via_t2s_path_blocked(self) -> None:
        """Fake Generator 返回跨项目 SQL（模拟被诱导的 LLM）→
        整条 T2S 链路拒绝（Orchestrator 包装为 ExecutionError）。"""
        orch = _build_project_orchestrator("project-a")
        orch._text_to_sql = _RecordingT2SGenerator(  # type: ignore[assignment]
            f'SELECT * FROM "{SCHEMA_B}"."inventory" LIMIT 10'
        )

        with pytest.raises(AIOrchestratorExecutionError):
            asyncio.run(orch.execute(QUESTION_T2S))

    def test_project_a_tool_cannot_read_project_b(self) -> None:
        """project-a 的 get_inventory（绑定 project_a schema）查 project_b
        数据 → 返回 0（隔离：工具只能读本项目 schema）。"""
        orch = _build_project_orchestrator("project-a")
        # 999 只在 project_b 中；用 project-a 工具查询一个仅存在于
        # project_b 的物料 → qty=0（读不到对方数据）
        with _schemas_engine() as eng:
            with eng.begin() as conn:
                conn.execute(text(
                    f'INSERT INTO "{SCHEMA_B}"."inventory" '
                    f"(material_code, qty) VALUES ('20002', 777)"
                ))
        try:
            result = asyncio.run(orch.execute("查询物料 20002 当前库存"))
            assert result.route.value == "tool"
            assert result.data is not None and result.data.success
            assert result.data.data["qty"] == 0.0
        finally:
            with _schemas_engine() as eng:
                with eng.begin() as conn:
                    conn.execute(text(
                        f'DELETE FROM "{SCHEMA_B}"."inventory" '
                        f"WHERE material_code = '20002'"
                    ))


@contextmanager
def _schemas_engine():
    """获取全局 Engine（供跨项目测试的辅助数据写入）。"""
    from backend.app.db.session import get_engine

    eng = get_engine()
    assert eng is not None
    yield eng


# ============================================================
# API E2E（任务书 §十七）
# ============================================================

@requires_db
class TestApiProjectE2E:
    def _post(self, client: TestClient, project_id: str | None) -> dict:
        payload: dict = {"question": QUESTION_TOOL}
        if project_id is not None:
            payload["project_id"] = project_id
        response = client.post("/api/ai/chat", json=payload)
        assert response.status_code == 200, response.text
        return response.json()

    def test_api_project_a_and_b_return_different_data(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST /api/ai/chat：project-a → 100 / project-b → 999
        （route=tool，真实 DB 查询，无 LLM 调用）。"""
        import backend.app.services.project_orchestrator_factory as factory

        with _test_registry() as registry:
            monkeypatch.setattr(
                factory, "get_default_project_registry", lambda: registry
            )
            from backend.app.main import app

            with TestClient(app) as client:
                pa = self._post(client, "project-a")
                pb = self._post(client, "project-b")

        assert pa["route"] == "tool"
        assert pb["route"] == "tool"
        qty_a = pa["data"]["data"]["qty"]
        qty_b = pb["data"]["data"]["qty"]
        assert qty_a == 100.0, pa
        assert qty_b == 999.0, pb
        assert pa["data"]["data"]["project_id"] == "project-a"
        assert pb["data"]["data"]["project_id"] == "project-b"

    def test_api_unknown_project_404(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未注册 project_id → HTTP 404（project_id 只能选择已注册数据源）。"""
        import backend.app.services.project_orchestrator_factory as factory

        with _test_registry() as registry:
            monkeypatch.setattr(
                factory, "get_default_project_registry", lambda: registry
            )
            from backend.app.main import app

            with TestClient(app) as client:
                response = client.post(
                    "/api/ai/chat",
                    json={"question": QUESTION_TOOL, "project_id": "ghost"},
                )
        assert response.status_code == 404, response.text
        assert "ghost" in response.json()["detail"]

    def test_api_no_project_id_uses_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """project_id 未提供 → 默认 Orchestrator（不查注册表，旧行为）。"""
        import backend.app.services.project_orchestrator_factory as factory

        with _test_registry() as registry:
            monkeypatch.setattr(
                factory, "get_default_project_registry", lambda: registry
            )
            from backend.app.main import app

            with TestClient(app) as client:
                # 默认 vietnam-wms 的 public schema 无 inventory 表
                # → Tool 返回失败结果（ToolResult success=False）但 HTTP 仍 200
                payload = self._post(client, None)

        assert payload["route"] in {"tool", "rag"}
        # 未提供 project_id 不应命中 project-a/b 的数据
        data = payload.get("data") or {}
        tool_data = data.get("data") if isinstance(data, dict) else None
        if tool_data:
            assert tool_data.get("qty") in (None, 0.0)

    def test_api_default_project_id_equivalent_to_default(self) -> None:
        """project_id = 配置默认值（vietnam-wms）→ 默认注册表命中，
        行为与"未提供 project_id"等价（兼容性，任务书 §十九）。"""
        from backend.app.main import app

        with TestClient(app) as client:
            payload = self._post(client, settings.project.project_id)

        # 与默认路径同构：路由正常返回（public schema 无库存表 →
        # tool 失败但 HTTP 200），关键是不抛 404/500
        assert payload["route"] in {"tool", "rag"}
