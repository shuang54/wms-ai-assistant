"""Project Capability 隔离集成测试（Phase 3.8.2）。

默认 SKIP；``RUN_DB_TESTS=1``（+ DATABASE_URL）后执行。

覆盖任务书验收标准（§十四 / §十五 / §十六 / §十七 / §二十）：

    14.1 Project Capability         → tests/test_project_capabilities.py（单元）
    14.2 Tool filtering             → 本文件 TestFactoryCapabilityFiltering
    14.3 Tool execution isolation   → 本文件 TestToolExecutionIsolation
    14.4 RAG capability             → 本文件 TestApiCapabilityE2E
    14.5 Text-to-SQL capability     → 本文件 TestTextToSqlCapability
    API E2E（§十五）                 → 本文件 TestApiCapabilityE2E
    Router 回归（§十六）             → 本文件 TestApiCapabilityE2E
    安全边界（§十七）                → 本文件 TestApiCapabilityE2E

测试项目（单 PostgreSQL 实例，复用 Phase 3.8.1 的双 schema）：

    project-a：schema project_a
               Tools=[get_inventory] / Knowledge=on / T2S=on（全能力）
    project-b：schema project_b
               Tools=[] / Knowledge=off / T2S=off（全部禁用 → 任何问题 403）
    project-c：schema project_b
               Tools=[] / Knowledge=on / T2S=off（§四 sales-demo 型项目）

RAG / T2S 下游用确定性 Fake（计数 + 固定返回）；Schema Explorer /
SQL Executor 为真实实例的计数包装（证明 capability denied 场景
**0 次数据库访问**）。
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
from backend.app.projects.capabilities import ProjectCapabilities
from backend.app.projects.models import DataSource, ProjectContext
from backend.app.projects.registry import (
    InMemoryProjectRegistry,
    ProjectRegistration,
)
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorCapabilityError,
)
from backend.app.services.project_orchestrator_factory import (
    build_orchestrator_for_project,
)
from backend.app.services.schema_explorer_service import (
    SchemaExplorerService as RealSchemaExplorerService,
)
from backend.app.services.sql_executor_service import (
    SQLExecutorService as RealSQLExecutorService,
)

import backend.app.services.project_orchestrator_factory as factory_module


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not (_env_flag("RUN_DB_TESTS") and settings.database.url.strip()),
    reason="set RUN_DB_TESTS=1 (plus DATABASE_URL) to enable project "
    "capability isolation tests",
)

SCHEMA_A = "project_a"
SCHEMA_B = "project_b"

QUESTION_TOOL = "查询物料 10001 当前库存"
QUESTION_T2S = "统计所有物料的库存汇总明细"
QUESTION_RAG = "WMS 盘点流程是什么？"

CAPS_FULL = ProjectCapabilities(
    tool_names=("get_inventory",),
    knowledge_enabled=True,
    text_to_sql_enabled=True,
)
CAPS_NONE = ProjectCapabilities(
    tool_names=(),
    knowledge_enabled=False,
    text_to_sql_enabled=False,
)
CAPS_KNOWLEDGE_ONLY = ProjectCapabilities(
    tool_names=(),
    knowledge_enabled=True,
    text_to_sql_enabled=False,
)


# ============================================================
# Fixtures：schema 数据 + 测试注册表 + 计数包装
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
    """module 级 autouse：project_a / project_b 两个 schema + 隔离数据。"""
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


def _registration(
    project_id: str, schema_name: str, capabilities: ProjectCapabilities
) -> ProjectRegistration:
    return ProjectRegistration(
        context=ProjectContext(
            project_id=project_id,
            project_name=f"Project {project_id}",
            description=None,
            data_source=DataSource(name="primary", type="postgresql"),
        ),
        schema_name=schema_name,
        capabilities=capabilities,
    )


@contextmanager
def _test_registry():
    registry = InMemoryProjectRegistry()
    registry.register("project-a", _registration("project-a", SCHEMA_A, CAPS_FULL))
    registry.register("project-b", _registration("project-b", SCHEMA_B, CAPS_NONE))
    registry.register(
        "project-c", _registration("project-c", SCHEMA_B, CAPS_KNOWLEDGE_ONLY)
    )
    yield registry


# ---- 计数包装（证明 capability denied → 0 次数据库访问） ----

class _CountingSQLExecutor:
    """真实 SQLExecutorService 的计数包装（构造参数兼容 factory 注入）。"""

    instances: list["_CountingSQLExecutor"] = []

    def __init__(self, engine=None) -> None:
        self._inner = RealSQLExecutorService(engine=engine)
        self.execute_calls = 0
        _CountingSQLExecutor.instances.append(self)

    async def execute(self, sql, **kwargs):
        self.execute_calls += 1
        return await self._inner.execute(sql, **kwargs)

    @classmethod
    def total_execute_calls(cls) -> int:
        return sum(i.execute_calls for i in cls.instances)


class _CountingSchemaExplorer:
    """真实 SchemaExplorerService 的计数包装。"""

    instances: list["_CountingSchemaExplorer"] = []

    def __init__(self, engine=None) -> None:
        self._inner = RealSchemaExplorerService(engine=engine)
        self.inspect_calls = 0
        _CountingSchemaExplorer.instances.append(self)

    async def inspect(self, schema=None):
        self.inspect_calls += 1
        return await self._inner.inspect(schema=schema)

    @classmethod
    def total_inspect_calls(cls) -> int:
        return sum(i.inspect_calls for i in cls.instances)


@pytest.fixture
def _counting_db(monkeypatch: pytest.MonkeyPatch):
    """把工厂里的 Executor / Explorer 替换为计数包装（每次测试重置）。"""
    _CountingSQLExecutor.instances.clear()
    _CountingSchemaExplorer.instances.clear()
    monkeypatch.setattr(factory_module, "SQLExecutorService", _CountingSQLExecutor)
    monkeypatch.setattr(
        factory_module, "SchemaExplorerService", _CountingSchemaExplorer
    )
    yield


# ---- Fake RAG / T2S（确定性，不调 LLM） ----

class _FakeRag:
    def __init__(self) -> None:
        self.calls = 0

    async def answer(self, question: str, *, top_k=None, knowledge_scope=None):
        # Phase 3.8.4：工厂默认为启用知识的项目解析 knowledge_scope；
        # Fake 接受该参数以保证与真实 RagService 签名兼容。
        self.calls += 1
        return SimpleNamespace(
            answer="知识库回答", used_chunks_count=1, sources=()
        )


class _RecordingT2S:
    def __init__(self, sql: str = "SELECT material_code, qty FROM inventory LIMIT 10") -> None:
        self.sql = sql
        self.calls = 0

    async def generate(self, question, **kwargs):
        self.calls += 1
        return SimpleNamespace(sql=self.sql)


def _build(project_id: str):
    """构造绑定项目（真实数据源 + 能力配置）的 Orchestrator。"""
    from backend.app.api import orchestrator_chat as orch_module

    with _test_registry() as registry:
        return build_orchestrator_for_project(
            project_id,
            base=orch_module._default_orchestrator,
            registry=registry,
        )


# ============================================================
# 14.2 Tool filtering（工厂组装层）
# ============================================================

@requires_db
class TestFactoryCapabilityFiltering:
    def test_project_a_registry_contains_allowed_tool(self) -> None:
        """Project A（get_inventory 允许）→ Tool Registry 含 get_inventory。"""
        orch = _build("project-a")
        names = {d.name for d in orch._tools.list_definitions()}
        assert names == {"get_inventory"}

    def test_project_b_registry_empty(self) -> None:
        """Project B（tools=[]）→ Tool Registry 为空；Router 看不到任何 Tool。"""
        orch = _build("project-b")
        assert len(orch._tools.list_definitions()) == 0
        # Router 的 capability 元数据来自该 Registry → 同样为空
        caps = orch._router._get_capabilities()
        assert tuple(caps) == ()

    def test_project_b_router_flags_disabled(self) -> None:
        """Project B 的 Router：knowledge / text_to_sql 开关关闭。"""
        orch = _build("project-b")
        assert orch._router._knowledge_enabled is False
        assert orch._router._text_to_sql_enabled is False

    def test_project_c_router_knowledge_only(self) -> None:
        """Project C（knowledge only）→ Router 只开 knowledge。"""
        orch = _build("project-c")
        assert orch._router._knowledge_enabled is True
        assert orch._router._text_to_sql_enabled is False

    def test_default_project_registration_full_capabilities(self) -> None:
        """默认项目（vietnam-wms）→ 默认能力（get_inventory + 全开，§十三）。"""
        orch = build_orchestrator_for_project(settings.project.project_id)
        names = {d.name for d in orch._tools.list_definitions()}
        assert names == {"get_inventory"}
        assert orch._router._knowledge_enabled is True
        assert orch._router._text_to_sql_enabled is True


# ============================================================
# 14.3 Tool execution isolation
# ============================================================

@requires_db
class TestToolExecutionIsolation:
    def test_project_a_tool_succeeds(self) -> None:
        """A + get_inventory → 成功（真实 DB 查询 project_a → qty 100）。"""
        orch = _build("project-a")
        result = asyncio.run(orch.execute(QUESTION_TOOL))

        assert result.route.value == "tool"
        assert result.data.success
        assert result.data.data["qty"] == 100.0
        assert result.data.data["project_id"] == "project-a"

    def test_project_b_denied_zero_db_queries(self, _counting_db) -> None:
        """B + 库存问题 → capability denied；**0 次业务数据库查询**。

        Router 看不到 Tool（过滤）→ 不走 TOOL；T2S / Knowledge 均禁用
        → Orchestrator 在任何 RagService / Schema / SQL 访问前拒绝。
        """
        orch = _build("project-b")
        with pytest.raises(AIOrchestratorCapabilityError) as ei:
            asyncio.run(orch.execute(QUESTION_TOOL))
        assert ei.value.capability in {"knowledge", "get_inventory", "text_to_sql"}

        assert _CountingSQLExecutor.total_execute_calls() == 0
        assert _CountingSchemaExplorer.total_inspect_calls() == 0


# ============================================================
# 14.5 Text-to-SQL capability
# ============================================================

@requires_db
class TestTextToSqlCapability:
    def test_project_a_t2s_normal(self, _counting_db) -> None:
        """A（T2S 开）→ 正常执行（Fake Generator + 真实 Validator/Executor）。"""
        from backend.app.api import orchestrator_chat as orch_module

        fake_t2s = _RecordingT2S()
        monkeypatch_target = orch_module._default_orchestrator
        original = monkeypatch_target._text_to_sql
        monkeypatch_target._text_to_sql = fake_t2s
        try:
            orch = _build("project-a")
            result = asyncio.run(orch.execute(QUESTION_T2S))
        finally:
            monkeypatch_target._text_to_sql = original

        assert result.route.value == "text_to_sql"
        assert fake_t2s.calls == 1
        assert result.data.row_count == 1
        assert float(result.data.rows[0][1]) == 100.0  # project_a 数据

    def test_project_b_t2s_denied_before_any_db_access(self, _counting_db) -> None:
        """B（T2S 禁）+ 分析问题 → capability denied；
        Generator 0 次、Executor 0 次、Schema inspect 0 次。"""
        from backend.app.api import orchestrator_chat as orch_module

        fake_t2s = _RecordingT2S()
        target = orch_module._default_orchestrator
        original = target._text_to_sql
        target._text_to_sql = fake_t2s
        try:
            orch = _build("project-b")
            with pytest.raises(AIOrchestratorCapabilityError) as ei:
                asyncio.run(orch.execute(QUESTION_T2S))
        finally:
            target._text_to_sql = original

        assert ei.value.capability in {"text_to_sql", "knowledge"}
        assert fake_t2s.calls == 0
        assert _CountingSQLExecutor.total_execute_calls() == 0
        assert _CountingSchemaExplorer.total_inspect_calls() == 0

    def test_project_c_analytics_not_routed_to_t2s(self, _counting_db) -> None:
        """C（knowledge only）+ 分析问题 → Router 不选 TEXT_TO_SQL，
        落到 RAG（FakeRag 兜底），Executor 0 次业务查询（§十六）。"""
        from backend.app.api import orchestrator_chat as orch_module

        fake_t2s = _RecordingT2S()
        fake_rag = _FakeRag()
        target = orch_module._default_orchestrator
        orig_t2s, orig_rag = target._text_to_sql, target._rag
        target._text_to_sql, target._rag = fake_t2s, fake_rag
        try:
            orch = _build("project-c")
            result = asyncio.run(orch.execute(QUESTION_T2S))
        finally:
            target._text_to_sql, target._rag = orig_t2s, orig_rag

        assert result.route.value == "rag"
        assert fake_t2s.calls == 0
        assert fake_rag.calls == 1
        assert _CountingSQLExecutor.total_execute_calls() == 0


# ============================================================
# API E2E（§十五 / §十六 / §十七）
# ============================================================

@requires_db
class TestApiCapabilityE2E:
    @contextmanager
    def _api(self, monkeypatch, *, rag=None, t2s=None):
        """TestClient + 测试注册表 + 可选 Fake RAG/T2S（挂在 base 单例上）。"""
        from backend.app.api import orchestrator_chat as orch_module
        from backend.app.main import app

        with _test_registry() as registry:
            monkeypatch.setattr(
                factory_module, "get_default_project_registry", lambda: registry
            )
            target = orch_module._default_orchestrator
            originals = {}
            if rag is not None:
                originals["_rag"] = target._rag
                target._rag = rag
            if t2s is not None:
                originals["_text_to_sql"] = target._text_to_sql
                target._text_to_sql = t2s
            try:
                with TestClient(app) as client:
                    yield client
            finally:
                for attr, value in originals.items():
                    setattr(target, attr, value)

    def _post(self, client, project_id, question, **extra) -> object:
        payload = {"question": question, "project_id": project_id, **extra}
        return client.post("/api/ai/chat", json=payload)

    # ---- Project A：全能力（§十六 回归：原路由行为继续成立） ----

    def test_api_project_a_tool_route(self, monkeypatch, _counting_db) -> None:
        """A + 库存问题 → route=tool，qty=100（真实 DB）。"""
        with self._api(monkeypatch) as client:
            resp = self._post(client, "project-a", QUESTION_TOOL)
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["route"] == "tool"
        assert payload["data"]["data"]["qty"] == 100.0

    def test_api_project_a_rag_route(self, monkeypatch, _counting_db) -> None:
        """A + 知识问题 → route=rag（FakeRag 正常调用，§十六 回归）。"""
        fake_rag = _FakeRag()
        with self._api(monkeypatch, rag=fake_rag) as client:
            resp = self._post(client, "project-a", QUESTION_RAG)
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["route"] == "rag"
        assert fake_rag.calls == 1

    def test_api_project_a_t2s_route(self, monkeypatch, _counting_db) -> None:
        """A + 分析问题 → route=text_to_sql（Fake Generator + 真实执行）。"""
        fake_t2s = _RecordingT2S()
        with self._api(monkeypatch, t2s=fake_t2s) as client:
            resp = self._post(client, "project-a", QUESTION_T2S)
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["route"] == "text_to_sql"
        assert fake_t2s.calls == 1
        assert payload["data"]["project_id"] == "project-a"

    # ---- Project B：全部禁用 → capability denied（§十五） ----

    def test_api_project_b_capability_denied(self, monkeypatch, _counting_db) -> None:
        """B + 库存问题 → HTTP 403（capability denied）+ 0 次业务 DB 查询。"""
        with self._api(monkeypatch) as client:
            resp = self._post(client, "project-b", QUESTION_TOOL)
        assert resp.status_code == 403, resp.text
        assert "未启用" in resp.json()["detail"]
        assert _CountingSQLExecutor.total_execute_calls() == 0
        assert _CountingSchemaExplorer.total_inspect_calls() == 0

    def test_api_project_b_rag_denied_zero_rag_calls(
        self, monkeypatch, _counting_db
    ) -> None:
        """B（knowledge 禁用）+ 知识问题 → 403；RagService 0 次调用（§14.4）。"""
        fake_rag = _FakeRag()
        with self._api(monkeypatch, rag=fake_rag) as client:
            resp = self._post(client, "project-b", QUESTION_RAG)
        assert resp.status_code == 403, resp.text
        assert fake_rag.calls == 0

    # ---- Project C：knowledge only → Router 不选被禁用能力（§十六） ----

    def test_api_project_c_tool_question_falls_to_rag(
        self, monkeypatch, _counting_db
    ) -> None:
        """C + 库存问题 → 不走 TOOL / T2S，落 RAG（FakeRag）。"""
        fake_rag = _FakeRag()
        with self._api(monkeypatch, rag=fake_rag) as client:
            resp = self._post(client, "project-c", QUESTION_TOOL)
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["route"] == "rag"
        assert fake_rag.calls == 1
        assert _CountingSQLExecutor.total_execute_calls() == 0

    def test_api_project_c_analytics_not_t2s(self, monkeypatch, _counting_db) -> None:
        """C + 分析问题 → route=rag（TEXT_TO_SQL 未被 Router 选择）。"""
        fake_rag = _FakeRag()
        fake_t2s = _RecordingT2S()
        with self._api(monkeypatch, rag=fake_rag, t2s=fake_t2s) as client:
            resp = self._post(client, "project-c", QUESTION_T2S)
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["route"] == "rag"
        assert fake_t2s.calls == 0

    # ---- 安全边界（§十七）：HTTP 无法注入能力 ----

    def test_api_extra_fields_cannot_enable_tools(
        self, monkeypatch, _counting_db
    ) -> None:
        """请求体携带 tool_names / text_to_sql_enabled / database_url
        等额外字段 → 被忽略（Pydantic 默认丢弃未知字段），B 仍 403。"""
        with self._api(monkeypatch) as client:
            resp = self._post(
                client,
                "project-b",
                QUESTION_TOOL,
                tool_names=["get_inventory"],
                text_to_sql_enabled=True,
                knowledge_enabled=True,
                database_url="postgresql://evil",
            )
        assert resp.status_code == 403, resp.text
        assert _CountingSQLExecutor.total_execute_calls() == 0

    def test_api_unknown_project_404(self, monkeypatch, _counting_db) -> None:
        with self._api(monkeypatch) as client:
            resp = self._post(client, "ghost", QUESTION_TOOL)
        assert resp.status_code == 404, resp.text

    # ---- 兼容：默认项目 / 不带 project_id 行为不变（§十三） ----

    def test_api_no_project_id_unchanged(self, monkeypatch, _counting_db) -> None:
        """不带 project_id → 默认 Orchestrator（不查注册表，旧行为）。"""
        from backend.app.main import app

        with TestClient(app) as client:
            resp = client.post("/api/ai/chat", json={"question": QUESTION_TOOL})
        assert resp.status_code == 200, resp.text
        assert resp.json()["route"] in {"tool", "rag", "text_to_sql"}

    def test_api_default_project_full_capabilities(
        self, monkeypatch, _counting_db
    ) -> None:
        """project_id=vietnam-wms → 默认注册表 → 全能力（工具正常注册）。"""
        from backend.app.main import app

        with TestClient(app) as client:
            resp = client.post(
                "/api/ai/chat",
                json={
                    "question": QUESTION_TOOL,
                    "project_id": settings.project.project_id,
                },
            )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        # 默认项目 public schema 无 inventory 表 → Tool 可能失败但路由可达
        assert payload["route"] in {"tool", "rag"}


__all__ = [
    "TestFactoryCapabilityFiltering",
    "TestToolExecutionIsolation",
    "TestTextToSqlCapability",
    "TestApiCapabilityE2E",
]
