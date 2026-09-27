"""ToolChatService 真实 ``get_inventory`` 集成测试（Phase 3.11 Step 11）。

验证链路 B 从 Mock Tool 升级为**真实只读** Tool：

```text
POST /api/chat/with-tools
    ↓
ToolChatService（LLM Function Calling + Multi-Step Loop）
    ↓
ToolExecutionService（capability + project scope）
    ↓
ToolRegistry（真实 GET_INVENTORY_DEFINITION + GetInventoryHandler）
    ↓
PostgreSQL（SELECT SUM(qty) …；BEGIN READ ONLY + 绑定参数 + timeout）
    ↓
ToolResult → role=tool → LLM → Final Answer
```

覆盖：

```text
Test 1  真实 E2E 成功（ToolCall → 真实 Handler → PostgreSQL → ToolResult → Final Answer）
Test 2  arguments 原样转发（material_code 逐字到达真实 Handler）
Test 3  missing required → Registry 拒绝（DB 0 次）
Test 4  unknown field    → Registry 拒绝（DB 0 次）
Test 5  SQL 注入输入      → Handler 校验拒绝（DB 0 次、数据未泄露）
Test 6  warehouse_code   → 真实 Handler 显式拒绝（不静默全仓汇总；DB 0 次）
Test 7  Multi-Step       → 两次真实查询（每次 ONE Tool execution）
Test 8  DB writes = 0    → 行数不变 + 语句审计（仅 SELECT / SET / ROLLBACK）
Test 9  project scope    → project-a 允许 → 真实执行
Test 10 API E2E          → HTTP → ToolChatService → 真实 Tool → PostgreSQL
Test 11 Capability denied（非 DB）→ project-b 拒绝、Handler 0 次
```

DB 集成测试需要：``RUN_DB_TESTS=1`` + ``DATABASE_URL`` 已配置；
使用**现有隔离 fixture**（schema ``inventory_tool_test``，见
``tests/test_get_inventory_tool.py``），绝不触碰 public.* 业务表。
LLM 全部为 Scripted Fake（**不**调用真实 DeepSeek）。
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sa_event
from sqlalchemy import text as sa_text

from backend.app.api import tool_chat as tool_chat_module
from backend.app.config import InventoryToolSettings
from backend.app.db import reset_engine_cache
from backend.app.db.session import get_engine
from backend.app.projects.capabilities import ProjectCapabilities
from backend.app.projects.models import DataSource, ProjectContext
from backend.app.projects.registry import ProjectRegistration
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorCapabilityError,
)
from backend.app.services.tool_chat_service import ToolChatService
from backend.app.services.tool_execution_service import ToolExecutionService
from backend.app.tools.get_inventory import (
    GetInventoryHandler,
    register_get_inventory_tool,
)
from backend.app.tools.mock_tools import (
    GET_INVENTORY_DEFINITION as MOCK_INVENTORY_DEFINITION,
    register_mock_tools,
)
from backend.app.tools.registry import ToolRegistry

from tests.test_get_inventory_tool import (  # noqa: E402
    _INVENTORY_TEST_SCHEMA,
    _INVENTORY_TEST_TABLE,
    _count_public_knowledge_rows,
    _setup_inventory_fixture,
    _teardown_inventory_fixture,
    requires_db,
)
from tests.test_tool_chat_service import (  # noqa: E402
    ScriptedLLMClient,
    _tool_call_llm_response,
)

#: 真实 Handler 的 ProjectContext override（与现有 fixture 一致）
_PROJECT_ID = "inventory-tool-test"

#: seed 数据（tests/test_get_inventory_tool.py::_INVENTORY_SEED_DATA）
_SEED = {"10001": 120.0, "10002": 80.0, "MAT-001": 250.0, "SKU.001": 5.0}

#: 只读语句白名单（Handler 只应产生这些语句）
_READ_ONLY_PREFIXES = ("SELECT", "SET", "SHOW", "ROLLBACK", "BEGIN", "COMMIT")


# ============================================================
# fixtures / helpers
# ============================================================

@pytest.fixture()
def real_engine() -> Any:
    """真实 PostgreSQL Engine + 隔离 fixture（复用既有 DB 测试基础设施）。"""
    reset_engine_cache()
    engine = get_engine()
    assert engine is not None, "DATABASE_URL 未配置"
    _setup_inventory_fixture(engine)
    try:
        yield engine
    finally:
        try:
            _teardown_inventory_fixture(engine)
        except Exception:  # noqa: BLE001
            pass
        reset_engine_cache()


def _real_handler(engine: Any) -> GetInventoryHandler:
    """真实 Handler，指向隔离 fixture schema（不是 Fake）。"""
    return GetInventoryHandler(
        engine=engine,
        inv_settings=InventoryToolSettings(
            schema_name=_INVENTORY_TEST_SCHEMA,
            table_name=_INVENTORY_TEST_TABLE,
        ),
        project_id=_PROJECT_ID,
    )


def _real_registry(engine: Any) -> ToolRegistry:
    """注册**真实** get_inventory 的 Registry（与生产装配同构）。"""
    registry = ToolRegistry()
    register_get_inventory_tool(registry, handler=_real_handler(engine))
    return registry


class _StatementRecorder:
    """捕获 Engine 上执行过的 SQL（只读审计 / Handler 调用证据）。"""

    def __init__(self, engine: Any) -> None:
        self.statements: list[str] = []
        self._engine = engine

        def _before(conn, cursor, statement, parameters, context, executemany):
            self.statements.append(" ".join(statement.split()))

        self._before = _before
        sa_event.listen(engine, "before_cursor_execute", _before)

    def close(self) -> None:
        sa_event.remove(
            self._engine, "before_cursor_execute", self._before
        )

    @property
    def selects(self) -> list[str]:
        return [
            s for s in self.statements if s.upper().startswith("SELECT")
        ]

    def assert_read_only(self) -> None:
        for statement in self.statements:
            assert statement.upper().startswith(_READ_ONLY_PREFIXES), (
                f"非只读语句被执行: {statement[:80]}"
            )


def _scoped_service(
    registry: ToolRegistry,
    llm: ScriptedLLMClient,
    *,
    project_id: str | None = None,
    capabilities: ProjectCapabilities | None = None,
) -> ToolChatService:
    return ToolChatService(
        llm_client=llm,
        execution_service=ToolExecutionService(
            registry=registry,
            capabilities=capabilities,
            project_id=project_id,
        ),
    )


def _fixture_row_count(engine: Any) -> int:
    with engine.connect() as conn:
        return int(conn.execute(sa_text(
            f'SELECT count(*) FROM "{_INVENTORY_TEST_SCHEMA}"'
            f'."{_INVENTORY_TEST_TABLE}"'
        )).scalar() or 0)


def _tool_payload(llm: ScriptedLLMClient, call_index: int = 1) -> dict:
    """第 ``call_index`` 轮 LLM 收到的最后一条 role=tool 消息负载。"""
    message = llm.calls[call_index]["messages"][-1]
    assert message["role"] == "tool"
    return json.loads(message["content"])


# ============================================================
# 非 DB：capability denied（§十六 / §二十九）
# ============================================================

class _CountingHandler:
    """计数 Handler（仅用于「未执行」断言；不是真实 DB 路径）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        return {"material_code": arguments.get("material_code"), "qty": 0}


class _StubProjectRegistry:
    """最小项目注册表（只提供 capability；不解析 Engine / Semantic）。"""

    def __init__(self, mapping: dict[str, tuple[str, ...]]) -> None:
        self._mapping = mapping

    def get(self, project_id: str) -> ProjectRegistration:
        from backend.app.projects.registry import ProjectNotFoundError

        if project_id not in self._mapping:
            raise ProjectNotFoundError(project_id)
        return ProjectRegistration(
            context=ProjectContext(
                project_id=project_id,
                project_name=project_id,
                description=None,
                data_source=DataSource(name="primary", type="postgresql"),
            ),
            schema_name="public",
            capabilities=ProjectCapabilities(
                tool_names=self._mapping[project_id]
            ),
        )


class TestCapabilityDeniedWithoutDb:
    """project scope 决定真实 Tool 是否可达（0 DB 参与）。"""

    def _registry(self) -> tuple[ToolRegistry, _CountingHandler]:
        handler = _CountingHandler()
        registry = ToolRegistry()
        registry.register(MOCK_INVENTORY_DEFINITION, handler)
        return registry, handler

    def _boundary(
        self, registry: ToolRegistry, project_id: str
    ) -> ToolExecutionService:
        registration = _StubProjectRegistry({
            "project-a": ("get_inventory",),
            "project-b": (),
        }).get(project_id)
        return ToolExecutionService(
            registry=registry,
            capabilities=registration.capabilities,
            project_id=project_id,
        )

    async def test_project_allowing_inventory_executes(self) -> None:
        registry, handler = self._registry()
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "10001"}),
            "回答",
        ])
        service = ToolChatService(
            llm_client=llm,
            execution_service=self._boundary(registry, "project-a"),
        )

        result = await service.chat("查询库存", registry=registry)

        assert handler.calls == 1
        assert [c.tool_name for c in result.tool_calls] == ["get_inventory"]

    async def test_project_without_inventory_denied_zero_handler(self) -> None:
        registry, handler = self._registry()
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "10001"}),
            "（不应到达这里）",
        ])
        service = ToolChatService(
            llm_client=llm,
            execution_service=self._boundary(registry, "project-b"),
        )

        with pytest.raises(AIOrchestratorCapabilityError) as excinfo:
            await service.chat("查询库存", registry=registry)

        assert excinfo.value.project_id == "project-b"
        assert excinfo.value.capability == "get_inventory"
        assert handler.calls == 0        # Handler 0 次 → DB 0 次
        assert len(llm.calls) == 1       # 授权失败不继续下一轮

    def test_real_tool_not_swapped_by_mock_in_production_registry(self) -> None:
        """生产 Registry 里 get_inventory 是真实 Definition（非 Mock）。"""
        assert (
            tool_chat_module._tool_registry.get_definition("get_inventory")
            is not MOCK_INVENTORY_DEFINITION
        )

    def test_mock_registry_still_available_for_tests(self) -> None:
        """Mock Tools 仍可用于单元测试（本文件 / Step 8-10 测试）。"""
        registry = ToolRegistry()
        register_mock_tools(registry)
        assert [d.name for d in registry.list_definitions()] == [
            "get_inventory", "get_work_order",
        ]


# ============================================================
# 真实 DB：Function Calling → 真实 get_inventory → PostgreSQL
# ============================================================

@requires_db()
class TestRealGetInventoryThroughToolChat:
    async def test_real_tool_success_end_to_end(self, real_engine: Any) -> None:
        """ToolCall → 真实 Handler → PostgreSQL → ToolResult → Final Answer。"""
        registry = _real_registry(real_engine)
        recorder = _StatementRecorder(real_engine)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT-001"}),
            "MAT-001 当前库存为 250。",
        ])
        service = _scoped_service(
            registry, llm, project_id=_PROJECT_ID
        )

        try:
            result = await service.chat("查询 MAT-001 库存", registry=registry)
        finally:
            recorder.close()

        assert result.answer == "MAT-001 当前库存为 250。"
        assert [c.tool_name for c in result.tool_calls] == ["get_inventory"]

        payload = _tool_payload(llm)
        assert payload["success"] is True
        assert payload["data"]["material_code"] == "MAT-001"
        assert float(payload["data"]["qty"]) == pytest.approx(250.0)
        assert payload["data"]["project_id"] == _PROJECT_ID

        # 真实 SQL：唯一一条 SELECT（只读；绑定参数，无字符串拼接）
        assert len(recorder.selects) == 1
        select_sql = recorder.selects[0]
        assert "SUM(QTY)" in select_sql.upper()
        assert _INVENTORY_TEST_TABLE in select_sql
        assert "MATERIAL_CODE = %(MATERIAL_CODE)S" in select_sql.upper()
        recorder.assert_read_only()

    async def test_material_code_forwarded_verbatim(
        self, real_engine: Any
    ) -> None:
        """LLM arguments 原样到达真实 Handler（10001 → seed 120.0）。"""
        registry = _real_registry(real_engine)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "10001"}),
            "10001 当前库存为 120。",
        ])
        service = _scoped_service(registry, llm, project_id=_PROJECT_ID)

        await service.chat("查询 10001 库存", registry=registry)

        payload = _tool_payload(llm)
        assert payload["data"]["material_code"] == "10001"
        assert float(payload["data"]["qty"]) == pytest.approx(
            _SEED["10001"]
        )

    async def test_missing_required_rejected_before_db(
        self, real_engine: Any
    ) -> None:
        """缺 material_code → Registry 拒绝（Handler 0 次 / DB 0 次）。"""
        registry = _real_registry(real_engine)
        recorder = _StatementRecorder(real_engine)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {}),
            "参数不足，请提供物料编码。",
        ])
        service = _scoped_service(registry, llm, project_id=_PROJECT_ID)

        try:
            await service.chat("查库存", registry=registry)
        finally:
            recorder.close()

        payload = _tool_payload(llm)
        assert payload["success"] is False
        assert "missing required" in payload["error"]
        assert recorder.statements == []          # 未触达数据库

    async def test_unknown_field_rejected_before_db(
        self, real_engine: Any
    ) -> None:
        """LLM 幻觉字段 → Registry 拒绝（不允许进入真实 DB）。"""
        registry = _real_registry(real_engine)
        recorder = _StatementRecorder(real_engine)
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory",
                {"material_code": "MAT-001", "fake_field": "xxx"},
            ),
            "参数不被支持。",
        ])
        service = _scoped_service(registry, llm, project_id=_PROJECT_ID)

        try:
            await service.chat("查询库存", registry=registry)
        finally:
            recorder.close()

        payload = _tool_payload(llm)
        assert payload["success"] is False
        assert "unknown field" in payload["error"]
        assert recorder.statements == []

    async def test_sql_injection_input_rejected(
        self, real_engine: Any
    ) -> None:
        """注入输入被 Handler 字符集校验拒绝：DB 0 次 / 数据未泄露。"""
        registry = _real_registry(real_engine)
        with real_engine.connect() as conn:
            rows_before = _count_public_knowledge_rows(conn)
        # 记录器必须在「行数基线查询」之后创建（只统计 Tool 执行期间的语句）
        recorder = _StatementRecorder(real_engine)
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory", {"material_code": "' OR 1=1 --"}
            ),
            "该编码不合法。",
        ])
        service = _scoped_service(registry, llm, project_id=_PROJECT_ID)

        try:
            await service.chat("查询库存", registry=registry)
        finally:
            recorder.close()

        payload = _tool_payload(llm)
        assert payload["success"] is False
        assert "非法字符" in payload["error"]
        assert "' OR 1=1" not in json.dumps(payload)   # 不回显注入输入
        assert recorder.statements == []               # 未执行任何 SQL
        with real_engine.connect() as conn:
            assert _count_public_knowledge_rows(conn) == rows_before

    async def test_warehouse_code_explicitly_rejected(
        self, real_engine: Any
    ) -> None:
        """warehouse_code 为契约保留字段：真实 Handler 显式拒绝
        （不静默按全仓汇总；DB 0 次）。"""
        registry = _real_registry(real_engine)
        recorder = _StatementRecorder(real_engine)
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory",
                {"material_code": "10001", "warehouse_code": "A01"},
            ),
            "该维度当前不受支持。",
        ])
        service = _scoped_service(registry, llm, project_id=_PROJECT_ID)

        try:
            await service.chat("查询 A01 仓库 10001 库存", registry=registry)
        finally:
            recorder.close()

        payload = _tool_payload(llm)
        assert payload["success"] is False
        assert "warehouse" in payload["error"]
        assert recorder.statements == []

    async def test_multi_step_two_real_queries(
        self, real_engine: Any
    ) -> None:
        """Multi-Step：两次真实查询，每次 ONE Tool execution。"""
        registry = _real_registry(real_engine)
        recorder = _StatementRecorder(real_engine)
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory", {"material_code": "MAT-001"}, call_id="c1"
            ),
            _tool_call_llm_response(
                "get_inventory", {"material_code": "10002"}, call_id="c2"
            ),
            "MT-001=250，10002=80。",
        ])
        service = _scoped_service(registry, llm, project_id=_PROJECT_ID)

        try:
            result = await service.chat("对比两个物料库存", registry=registry)
        finally:
            recorder.close()

        assert [c.tool_name for c in result.tool_calls] == [
            "get_inventory", "get_inventory",
        ]
        assert len(llm.calls) == 3
        assert len(recorder.selects) == 2          # 每次执行 1 条 SELECT
        assert float(_tool_payload(llm, 1)["data"]["qty"]) == pytest.approx(
            _SEED["MAT-001"]
        )
        assert float(_tool_payload(llm, 2)["data"]["qty"]) == pytest.approx(
            _SEED["10002"]
        )
        recorder.assert_read_only()

    async def test_db_writes_zero(self, real_engine: Any) -> None:
        """硬性要求：DB writes = 0（行数不变 + 语句仅只读）。"""
        registry = _real_registry(real_engine)
        with real_engine.connect() as conn:
            knowledge_before = _count_public_knowledge_rows(conn)
        inventory_before = _fixture_row_count(real_engine)

        recorder = _StatementRecorder(real_engine)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "SKU.001"}),
            "SKU.001 当前库存为 5。",
        ])
        service = _scoped_service(registry, llm, project_id=_PROJECT_ID)

        try:
            await service.chat("查询 SKU.001 库存", registry=registry)
        finally:
            recorder.close()

        with real_engine.connect() as conn:
            knowledge_after = _count_public_knowledge_rows(conn)
        assert knowledge_after == knowledge_before
        assert _fixture_row_count(real_engine) == inventory_before
        recorder.assert_read_only()                # SET / SELECT / ROLLBACK
        assert len(recorder.selects) == 1

    async def test_project_scope_allows_real_execution(
        self, real_engine: Any
    ) -> None:
        """project-a 允许 get_inventory → 真实执行（capability = PASS）。"""
        registry = _real_registry(real_engine)
        caps = ProjectCapabilities(tool_names=("get_inventory",))
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "10001"}),
            "10001 当前库存为 120。",
        ])
        service = _scoped_service(
            registry, llm, project_id="project-a", capabilities=caps
        )

        result = await service.chat("查询 10001 库存", registry=registry)

        assert [c.tool_name for c in result.tool_calls] == ["get_inventory"]
        assert float(_tool_payload(llm)["data"]["qty"]) == pytest.approx(
            _SEED["10001"]
        )

    def test_api_endpoint_real_tool(self, real_engine: Any, monkeypatch) -> None:
        """HTTP → ToolChatService → 真实 Tool → PostgreSQL（LLM = Fake）。"""
        registry = _real_registry(real_engine)
        monkeypatch.setattr(
            tool_chat_module, "_tool_registry", registry
        )
        monkeypatch.setattr(
            tool_chat_module,
            "_project_registry",
            lambda: _StubProjectRegistry({"project-a": ("get_inventory",)}),
        )
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT-001"}),
            "MAT-001 当前库存为 250。",
        ])
        monkeypatch.setattr(
            tool_chat_module,
            "_tool_chat_service",
            ToolChatService(llm_client=llm),
        )

        from backend.app.main import app

        with TestClient(app) as client:
            response = client.post(
                "/api/chat/with-tools",
                json={"message": "查询 MAT-001 库存", "project_id": "project-a"},
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["answer"] == "MAT-001 当前库存为 250。"
        assert payload["tool_calls"] == [{"tool_name": "get_inventory"}]
