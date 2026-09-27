"""ToolChatService ProjectContext 测试（Phase 3.11 Step 10）。

验证项目作用域（project scope）只作为**执行上下文**进入执行边界：

    project_id
        ↓ ProjectRegistry（服务器端；未注册 → ProjectNotFoundError）
    ProjectRegistration.capabilities
        ↓
    ToolExecutionService(capabilities, project_id)   ← 执行上下文
        ↓
    ToolRegistry → Mock Tool（Handler 只收 arguments）

覆盖（§十七 / §十八 / §十九）：

```text
A   project-a（允许 get_inventory）→ success
B   project-b（不允许）            → denied（capability 拒绝）
    cross-project：同名 Tool 在不同项目结果不同（无跨项目白名单混用）
§十八 Handler 只收到 arguments（无 project_id / request_id / tool_call_id）
API project_id 正常（200） / 未注册（404） / 能力拒绝（403） /
    未提供 project_id（旧行为 200）/ OpenAPI schema + 错误响应文档
```

0 DB / 0 Network / 0 真实 LLM（Mock Tools + Scripted LLM）。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.app.api import tool_chat as tool_chat_module
from backend.app.projects.capabilities import ProjectCapabilities
from backend.app.projects.models import DataSource, ProjectContext
from backend.app.projects.registry import (
    InMemoryProjectRegistry,
    ProjectRegistration,
)
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorCapabilityError,
)
from backend.app.services.tool_chat_service import ToolChatService
from backend.app.services.tool_execution_service import ToolExecutionService
from backend.app.tools.mock_tools import GET_INVENTORY_DEFINITION
from backend.app.tools.registry import ToolRegistry

from tests.test_tool_chat_service import (  # noqa: E402
    ScriptedLLMClient,
    _tool_call_llm_response,
)
from tests.test_tool_chat_service_characterization import (  # noqa: E402
    _SignatureRecordingHandler,
)

PROJECT_A = "project-a"
PROJECT_B = "project-b"
PROJECT_C = "project-c"


# ============================================================
# 测试注册表（0 DB：只描述能力，不解析 Engine / Semantic）
# ============================================================

def _registration(project_id: str, tool_names: tuple[str, ...]):
    return ProjectRegistration(
        context=ProjectContext(
            project_id=project_id,
            project_name=project_id.upper(),
            description=None,
            data_source=DataSource(name="primary", type="postgresql"),
        ),
        schema_name="public",
        capabilities=ProjectCapabilities(tool_names=tool_names),
    )


def _test_registry() -> InMemoryProjectRegistry:
    registry = InMemoryProjectRegistry()
    registry.register(PROJECT_A, _registration(PROJECT_A, ("get_inventory",)))
    registry.register(PROJECT_B, _registration(PROJECT_B, ()))
    registry.register(
        PROJECT_C, _registration(PROJECT_C, ("get_work_order",))
    )
    return registry


def _mock_registry() -> ToolRegistry:
    """模块级 Mock Tool 集合（与 API 单例同构；0 DB）。"""
    from backend.app.tools.mock_tools import register_mock_tools

    registry = ToolRegistry()
    register_mock_tools(registry)
    return registry


def _api_style_execution(
    project_id: str, *, registry: ToolRegistry
) -> ToolExecutionService:
    """等价 API 装配：project_id → registration.capabilities → 边界。"""
    registration = _test_registry().get(project_id)
    return ToolExecutionService(
        registry=registry,
        capabilities=registration.capabilities,
        project_id=project_id,
    )


# ============================================================
# A / B / cross-project（Service 层）
# ============================================================

class TestProjectScopeCapability:
    async def test_project_a_allows_inventory(self) -> None:
        registry = _mock_registry()
        execution = _api_style_execution(PROJECT_A, registry=registry)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT001"}),
            "MAT001 当前库存为 1000 PCS。",
        ])
        service = ToolChatService(llm_client=llm, execution_service=execution)

        result = await service.chat("查询库存", registry=registry)

        assert result.answer == "MAT001 当前库存为 1000 PCS。"
        assert [c.tool_name for c in result.tool_calls] == ["get_inventory"]
        payload = json.loads(llm.calls[1]["messages"][-1]["content"])
        assert payload["data"]["quantity"] == 1000   # Mock Tool 真实执行

    async def test_project_b_denies_inventory(self) -> None:
        registry = _mock_registry()
        execution = _api_style_execution(PROJECT_B, registry=registry)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT001"}),
            "（不应到达这里）",
        ])
        service = ToolChatService(llm_client=llm, execution_service=execution)

        with pytest.raises(AIOrchestratorCapabilityError) as excinfo:
            await service.chat("查询库存", registry=registry)

        assert excinfo.value.capability == "get_inventory"
        assert excinfo.value.project_id == PROJECT_B
        assert len(llm.calls) == 1

    async def test_cross_project_scope_is_isolated(self) -> None:
        """同名 Tool：project-a 允许、project-b 拒绝、project-c 只允许工单。"""
        registry = _mock_registry()
        question_call = _tool_call_llm_response(
            "get_inventory", {"material_code": "MAT001"}
        )

        # project-a → OK
        llm_a = ScriptedLLMClient([question_call, "A 回答"])
        service_a = ToolChatService(
            llm_client=llm_a,
            execution_service=_api_style_execution(PROJECT_A, registry=registry),
        )
        assert (await service_a.chat("查询库存", registry=registry)).answer == "A 回答"

        # project-b → denied
        llm_b = ScriptedLLMClient([question_call, "B 回答"])
        service_b = ToolChatService(
            llm_client=llm_b,
            execution_service=_api_style_execution(PROJECT_B, registry=registry),
        )
        with pytest.raises(AIOrchestratorCapabilityError):
            await service_b.chat("查询库存", registry=registry)

        # project-c（只允许 get_work_order）→ get_inventory denied
        llm_c = ScriptedLLMClient([question_call, "C 回答"])
        service_c = ToolChatService(
            llm_client=llm_c,
            execution_service=_api_style_execution(PROJECT_C, registry=registry),
        )
        with pytest.raises(AIOrchestratorCapabilityError) as excinfo:
            await service_c.chat("查询库存", registry=registry)
        assert excinfo.value.project_id == PROJECT_C

    async def test_project_c_allows_work_order(self) -> None:
        registry = _mock_registry()
        execution = _api_style_execution(PROJECT_C, registry=registry)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_work_order", {"work_order_no": "WO1"}),
            "WO1 状态 RELEASED。",
        ])
        service = ToolChatService(llm_client=llm, execution_service=execution)

        result = await service.chat("查询工单", registry=registry)

        assert [c.tool_name for c in result.tool_calls] == ["get_work_order"]
        assert result.answer == "WO1 状态 RELEASED。"


# ============================================================
# §十八：project scope 不进入 Handler arguments
# ============================================================

class TestProjectScopeNotLeakedToHandler:
    async def test_handler_receives_only_arguments(self) -> None:
        handler = _SignatureRecordingHandler()
        registry = ToolRegistry()
        registry.register(GET_INVENTORY_DEFINITION, handler)
        execution = _api_style_execution(PROJECT_A, registry=registry)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT001"}),
            "最终回答",
        ])
        service = ToolChatService(llm_client=llm, execution_service=execution)

        await service.chat("查询库存", registry=registry)

        assert len(handler.signatures) == 1
        args, kwargs = handler.signatures[0]
        assert kwargs == {}
        assert len(args) == 1
        assert args[0] == {"material_code": "MAT001"}
        # project_id / request_id / tool_call_id 一律不进入 Handler
        rendered = str(args) + str(kwargs)
        for forbidden in ("project", "request_id", "tool_call_id", "call_001"):
            assert forbidden not in rendered, forbidden

    async def test_llm_messages_contain_no_project_scope(self) -> None:
        """project_id 也不进入 LLM 输入（messages / tool message 内容）。"""
        registry = _mock_registry()
        execution = _api_style_execution(PROJECT_A, registry=registry)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT001"}),
            "最终回答",
        ])
        service = ToolChatService(llm_client=llm, execution_service=execution)

        await service.chat("查询库存", registry=registry)

        for call in llm.calls:
            rendered = str(call["messages"]) + str(call["tools"])
            assert PROJECT_A not in rendered
            assert "project" not in rendered.lower()


# ============================================================
# API：project_id（§十九）
# ============================================================

@pytest.fixture()
def client(monkeypatch):
    """测试客户端：注入测试项目注册表 + Mock Registry + 真实 Service。

    Phase 3.11 Step 11：生产 Registry 为真实只读 get_inventory（会访问
    PostgreSQL）→ 本文件的 HTTP 用例注入 Mock Registry（只验证
    project scope / capability 与装配行为；真实 Tool 的 E2E 见
    ``tests/test_tool_chat_real_get_inventory.py``，DB-gated）。
    """

    def _make(llm: ScriptedLLMClient):
        monkeypatch.setattr(
            tool_chat_module, "_project_registry", _test_registry
        )
        monkeypatch.setattr(
            tool_chat_module, "_tool_registry", _mock_registry()
        )
        monkeypatch.setattr(
            tool_chat_module,
            "_tool_chat_service",
            ToolChatService(llm_client=llm),
        )
        from backend.app.main import app

        return TestClient(app)

    return _make


class TestApiProjectId:
    def test_project_id_allowed_returns_200(self, client) -> None:
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT001"}),
            "MAT001 当前库存为 1000 PCS。",
        ])
        with client(llm) as c:
            response = c.post(
                "/api/chat/with-tools",
                json={"message": "查询库存", "project_id": PROJECT_A},
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["answer"] == "MAT001 当前库存为 1000 PCS。"
        assert payload["tool_calls"] == [{"tool_name": "get_inventory"}]

    def test_project_id_capability_denied_returns_403(self, client) -> None:
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT001"}),
            "（不应到达这里）",
        ])
        with client(llm) as c:
            response = c.post(
                "/api/chat/with-tools",
                json={"message": "查询库存", "project_id": PROJECT_B},
            )

        assert response.status_code == 403
        detail = response.json()["detail"]
        assert "项目能力未启用" in detail
        assert "get_inventory" in detail
        assert len(llm.calls) == 1          # 拒绝后不继续 LLM
        for fragment in ("Traceback", "sk-", "DATABASE_URL"):
            assert fragment not in response.text

    def test_unknown_project_returns_404(self, client) -> None:
        llm = ScriptedLLMClient(["不会用到"])
        with client(llm) as c:
            response = c.post(
                "/api/chat/with-tools",
                json={"message": "查询库存", "project_id": "project-x"},
            )

        assert response.status_code == 404
        assert "项目未注册" in response.json()["detail"]
        assert llm.calls == []              # fail fast：未进入 Tool Chat 链路

    def test_without_project_id_keeps_legacy_behavior(self, client) -> None:
        """未提供 project_id → 不查注册表、不限制 Tool 能力（旧行为）。"""
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_work_order", {"work_order_no": "WO1"}),
            "WO1 状态 RELEASED。",
        ])
        with client(llm) as c:
            response = c.post(
                "/api/chat/with-tools", json={"message": "查询工单"}
            )

        assert response.status_code == 200
        assert response.json()["tool_calls"] == [{"tool_name": "get_work_order"}]

    def test_project_id_invalid_length_rejected_422(self, client) -> None:
        llm = ScriptedLLMClient(["不会用到"])
        with client(llm) as c:
            response = c.post(
                "/api/chat/with-tools",
                json={"message": "查询库存", "project_id": "x" * 200},
            )

        assert response.status_code == 422
        assert llm.calls == []


class TestApiOpenApiContract:
    def _openapi(self) -> dict:
        from backend.app.main import app

        with TestClient(app) as c:
            return c.get("/openapi.json").json()

    def test_request_schema_exposes_project_id(self) -> None:
        schema = self._openapi()["components"]["schemas"]["ToolChatRequest"]
        assert set(schema["properties"]) == {"message", "project_id"}

    def test_endpoint_documents_403_and_404(self) -> None:
        post = self._openapi()["paths"]["/api/chat/with-tools"]["post"]
        assert "403" in post["responses"]
        assert "404" in post["responses"]

    def test_response_schema_unchanged(self) -> None:
        schema = self._openapi()["components"]["schemas"]["ToolChatApiResponse"]
        assert set(schema["properties"]) == {"answer", "tool_calls"}
