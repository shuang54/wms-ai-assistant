"""Tool Runtime Reliability & Failure Contract（Phase 3.11 Step 12）。

本文件只验证**失败契约**（不新增业务 Tool、不新增错误类型）：

```text
LLM Function Calling
    ↓
ToolChatService（Multi-Step / 预算 / 单 ToolCall）
    ↓
ToolExecutionService（capability + project scope）
    ↓
ToolRegistry（Schema Validation Authority）
    ↓
Real get_inventory → PostgreSQL
```

Failure Matrix（每条对应一个或多个测试）：

```text
Case A  Capability denied      → AIOrchestratorCapabilityError（不降级为 ToolResult）/
                                 Handler 0 / 不继续下一轮 LLM
Case B  Unknown Tool           → ToolResult(False)：不猜 / 不 fallback / Handler 0 /
                                 不改 ToolCall.name
Case C  Missing required       → Registry reject（Handler 0 / SQL 0）
Case D  Unknown argument       → Registry reject（不静默删除字段 / Handler 0 / SQL 0）
Case E  Invalid argument type  → Registry reject（无 Python coercion / Handler 0 / SQL 0）
Case F  Handler validation     → Schema PASS → Handler REJECT（ToolResult False / SQL 0）
Case G  DB failure             → ToolResult(False)：cause_type only / 无连接串泄露 /
                                 不 retry（connect 恰好 1 次）
Case H  Failure + Multi-Step   → ToolResult(False) 经现有 contract 继续（授权失败不同径）
Case I  Budget boundary        → max_rounds = 1 / 20：不多执行、不多调用 LLM、原错误
Case J  Multiple ToolCall      → MultipleToolCallsError（任何执行之前）
Case K  Malformed ToolCall     → LLMToolCallFormatError（真实 parser；无 Tool 执行）
Case M  Real DB happy path     → SELECT only / READ ONLY / bound param / timeout / rollback
```

DB 集成测试（``Case *`` 的 SQL=0 证据 + Case M）需要
``RUN_DB_TESTS=1`` + ``DATABASE_URL``，复用既有隔离 fixture
（``inventory_tool_test``）；**LLM 全部为 Scripted Fake**（Real LLM = NO）。
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api import tool_chat as tool_chat_module
from backend.app.llm.client import (
    LLMToolCallFormatError,
    LLMResponse,
    OpenAICompatibleClient,
    ToolCall,
)
from backend.app.projects.capabilities import ProjectCapabilities
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorCapabilityError,
)
from backend.app.services.tool_chat_service import (
    MultipleToolCallsError,
    ToolCallingBudgetExceededError,
    ToolChatService,
)
from backend.app.services.tool_execution_service import ToolExecutionService
from backend.app.tools.base import validate_arguments
from backend.app.tools.get_inventory import (
    GET_INVENTORY_DEFINITION,
    GetInventoryHandler,
    register_get_inventory_tool,
)
from backend.app.tools.mock_tools import register_mock_tools
from backend.app.tools.registry import ToolRegistry

from tests.test_tool_chat_real_get_inventory import (  # noqa: E402
    _PROJECT_ID,
    _real_registry,
    _scoped_service,
    _StatementRecorder,
    _StubProjectRegistry,
    _tool_payload,
    real_engine,
)
from tests.test_get_inventory_tool import requires_db  # noqa: E402
from tests.test_tool_chat_service import (  # noqa: E402
    ScriptedLLMClient,
    _tool_call_llm_response,
)

#: 敏感信息样本（任何 error / message / HTTP body 都不得包含）
_SECRETS = (
    "postgresql://",
    "wms_user",
    "s3cret-pw",
    "db.internal",
    "password",
    "Password",
    "Authorization",
    "Bearer ",
    "sk-",
    "DATABASE_URL",
    "Traceback",
)


def _assert_no_secrets(text: str) -> None:
    for secret in _SECRETS:
        assert secret not in text, f"泄露敏感片段: {secret!r}"


# ============================================================
# 测试辅助（仅本文件使用）
# ============================================================

class _CountingExecution(ToolExecutionService):
    """记录 execute 调用的真实执行边界（验证「执行 / 不执行」）。

    Phase 3.11 Step 13：接受并记录 ``context``（Step 13 起 Service 会传）。
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.contexts: list[Any] = []

    async def execute(
        self,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
        context: Any = None,
    ) -> Any:
        self.calls.append((tool_name, arguments))
        self.contexts.append(context)
        return await super().execute(
            tool_name, arguments=arguments, context=context
        )


class _RecordingHandler:
    """计数 Handler（Schema 层拒绝时调用次数必须为 0）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        return {"material_code": arguments.get("material_code"), "qty": 1.0}


class _ForbiddenEngine:
    """任何 connect() 调用都会失败 → 证明未触达数据库。"""

    def __init__(self) -> None:
        self.connect_attempts = 0

    def connect(self) -> Any:
        self.connect_attempts += 1
        raise AssertionError("DB 不应被访问（Schema / 参数校验应先拒绝）")


class _FailingEngine:
    """连接失败 Engine（DB failure injection；0 网络，仅本进程 stub）。

    异常消息刻意携带「看起来像连接串」的内容，用于验证
    ToolResult / tool message / 日志外露面**不**透传底层消息。
    """

    def __init__(self) -> None:
        self.connect_attempts = 0

    def connect(self) -> Any:
        self.connect_attempts += 1
        raise RuntimeError(
            "connection refused: postgresql://wms_user:s3cret-pw@db.internal:5432/wms"
        )


def _registry_with_real_definition(handler: Any) -> ToolRegistry:
    """真实 GET_INVENTORY_DEFINITION + 指定 Handler（Schema 契约不变）。"""
    registry = ToolRegistry()
    register_get_inventory_tool(registry, handler=handler)
    return registry


def _mock_registry() -> ToolRegistry:
    """Mock Tools Registry（仅 Service 语义：预算 / 失败继续）。"""
    registry = ToolRegistry()
    register_mock_tools(registry)
    return registry


# ============================================================
# Case A：Capability denied
# ============================================================

class TestCapabilityDenied:
    """授权失败 ≠ Tool 失败：异常穿透、Handler 0 次、不继续 LLM。"""

    async def test_capability_denied_no_handler_and_no_second_round(
        self,
    ) -> None:
        handler = _RecordingHandler()
        registry = _registry_with_real_definition(handler)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT-001"}),
            "（不应到达这里）",
        ])
        service = ToolChatService(
            llm_client=llm,
            execution_service=ToolExecutionService(
                registry=registry,
                capabilities=ProjectCapabilities(tool_names=()),
                project_id="project-b",
            ),
        )

        with pytest.raises(AIOrchestratorCapabilityError) as excinfo:
            await service.chat("查询库存", registry=registry)

        assert excinfo.value.capability == "get_inventory"
        assert excinfo.value.project_id == "project-b"
        assert handler.calls == 0            # Handler 0 次 → DB 0 次
        assert len(llm.calls) == 1           # 不继续下一轮 LLM
        _assert_no_secrets(str(excinfo.value))

    async def test_capability_denied_is_not_downgraded_to_tool_result(
        self,
    ) -> None:
        """授权失败**不**转成 ToolResult(False)：异常穿透 + 不委派 Handler。"""
        handler = _RecordingHandler()
        registry = _registry_with_real_definition(handler)
        execution = _CountingExecution(
            registry=registry,
            capabilities=ProjectCapabilities(tool_names=()),
            project_id="project-b",
        )
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT-001"}),
            "最终回答",
        ])
        service = ToolChatService(llm_client=llm, execution_service=execution)

        with pytest.raises(AIOrchestratorCapabilityError):
            await service.chat("查询库存", registry=registry)

        # 边界被调用（服务不绕过边界），但 capability 校验先于 Registry 委派
        assert execution.calls == [
            ("get_inventory", {"material_code": "MAT-001"})
        ]
        assert handler.calls == 0      # Handler 0 / DB 0
        assert len(llm.calls) == 1     # 异常穿透 → 不继续下一轮


# ============================================================
# Case B：Unknown Tool
# ============================================================

class TestUnknownTool:
    """未注册 Tool：不猜、不 fallback、Handler 0 次、名称不被改写。"""

    async def test_unknown_tool_no_fallback_no_handler(self) -> None:
        handler = _RecordingHandler()
        registry = _registry_with_real_definition(handler)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("unknown_tool", {"material_code": "MAT-001"}),
            "该工具不可用。",
        ])
        service = ToolChatService(llm_client=llm)

        result = await service.chat("查询库存", registry=registry)

        assert result.answer == "该工具不可用。"
        assert [c.tool_name for c in result.tool_calls] == ["unknown_tool"]
        payload = _tool_payload(llm)
        assert payload["success"] is False
        assert "未注册" in payload["error"]
        assert handler.calls == 0            # 不 fallback 到 get_inventory

    async def test_unknown_tool_name_not_rewritten(self) -> None:
        """回传 LLM 的 assistant tool_call 名称仍是 LLM 给的原值。"""
        handler = _RecordingHandler()
        registry = _registry_with_real_definition(handler)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("totally_unknown", {"x": 1}, call_id="c9"),
            "回答",
        ])
        service = ToolChatService(llm_client=llm)

        await service.chat("q", registry=registry)

        assistant_msg = llm.calls[1]["messages"][1]
        call = assistant_msg["tool_calls"][0]
        assert call["function"]["name"] == "totally_unknown"
        assert call["id"] == "c9"


# ============================================================
# Case C / D / E：Registry Schema Validation（唯一入口）
# ============================================================

class TestArgumentValidationFailure:
    """Schema 拒绝 → Handler 0 次（Registry 是唯一校验入口）。"""

    async def test_missing_required_rejected(self) -> None:
        handler = _RecordingHandler()
        registry = _registry_with_real_definition(handler)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {}),
            "请提供物料编码。",
        ])
        service = ToolChatService(llm_client=llm)

        await service.chat("查库存", registry=registry)

        payload = _tool_payload(llm)
        assert payload["success"] is False
        assert "missing required field(s)" in payload["error"]
        assert handler.calls == 0

    async def test_unknown_field_not_silently_dropped(self) -> None:
        handler = _RecordingHandler()
        registry = _registry_with_real_definition(handler)
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory",
                {"material_code": "MAT-001", "fake_field": "xxx"},
            ),
            "参数不被支持。",
        ])
        service = ToolChatService(llm_client=llm)

        await service.chat("查库存", registry=registry)

        payload = _tool_payload(llm)
        assert payload["success"] is False
        assert "unknown field(s)" in payload["error"]
        assert handler.calls == 0

    async def test_invalid_type_rejected_without_coercion(self) -> None:
        """material_code=123 → Schema 拒绝（不自动 str() 转换）。"""
        handler = _RecordingHandler()
        registry = _registry_with_real_definition(handler)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": 123}),
            "请提供字符串编码。",
        ])
        service = ToolChatService(llm_client=llm)

        await service.chat("查库存", registry=registry)

        payload = _tool_payload(llm)
        assert payload["success"] is False
        assert "expected string, got int" in payload["error"]
        assert handler.calls == 0

    def test_non_object_arguments_rejected_at_dto(self) -> None:
        """真实行为：ToolCall.arguments 必须是 dict（DTO 层即拒绝，更早于执行链）。"""
        with pytest.raises(ValueError):
            ToolCall(id="c1", name="get_inventory", arguments=[1, 2])  # type: ignore[arg-type]

    async def test_non_object_arguments_rejected_at_registry(self) -> None:
        """纵深防御：即使绕过 DTO，Registry 仍拒绝（handler 0 次）。"""
        handler = _RecordingHandler()
        registry = _registry_with_real_definition(handler)

        result = await registry.execute(
            "get_inventory", arguments=[1, 2]  # type: ignore[arg-type]
        )

        assert result.success is False
        assert "arguments 必须为 mapping" in result.error
        assert handler.calls == 0


# ============================================================
# Case F：Handler 业务校验（Schema PASS → Handler REJECT）
# ============================================================

class TestHandlerValidationFailure:
    """Schema 通过但业务字符集拒绝：SQL 0 次、原始输入不回显。"""

    def test_schema_accepts_injection_looking_value(self) -> None:
        """前置证据：该值通过 JSON Schema（字符串），拒绝只能来自 Handler。"""
        validate_arguments(
            GET_INVENTORY_DEFINITION.parameters,
            {"material_code": "' OR 1=1 --"},
        )

    async def test_handler_rejects_before_db(self) -> None:
        engine = _ForbiddenEngine()
        handler = GetInventoryHandler(engine=engine, project_id=_PROJECT_ID)
        registry = _registry_with_real_definition(handler)
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory", {"material_code": "' OR 1=1 --"}
            ),
            "该编码不合法。",
        ])
        service = ToolChatService(llm_client=llm)

        await service.chat("查库存", registry=registry)

        payload = _tool_payload(llm)
        assert payload["success"] is False
        assert "ToolValidationError" in payload["error"]
        assert "数据库查询失败" not in payload["error"]   # 拒绝发生在查询之前
        assert "' OR 1=1" not in json.dumps(payload, ensure_ascii=False)
        assert engine.connect_attempts == 0              # SQL 0 次


# ============================================================
# Case G：DB failure（stub engine，0 网络）
# ============================================================

class TestDatabaseFailure:
    """真实 Handler + 连接失败 → ToolResult(False)：不 retry、不泄露。"""

    def _build(
        self, script: list
    ) -> tuple[ToolChatService, ToolRegistry, _FailingEngine, ScriptedLLMClient]:
        engine = _FailingEngine()
        registry = _registry_with_real_definition(
            GetInventoryHandler(engine=engine, project_id=_PROJECT_ID)
        )
        llm = ScriptedLLMClient(script)
        service = ToolChatService(llm_client=llm)
        return service, registry, engine, llm

    async def test_db_failure_is_tool_result_false_without_retry(self) -> None:
        engine = _FailingEngine()
        registry = _registry_with_real_definition(
            GetInventoryHandler(engine=engine, project_id=_PROJECT_ID)
        )
        execution = _CountingExecution(registry=registry)

        result = await execution.execute(
            "get_inventory", arguments={"material_code": "MAT-001"}
        )

        assert result.success is False
        assert result.tool_name == "get_inventory"
        assert "数据库查询失败" in result.error
        assert engine.connect_attempts == 1        # 恰好一次：无 retry
        assert "[RuntimeError]" in result.error    # 仅 cause_type
        _assert_no_secrets(result.error)

    async def test_db_failure_continues_multi_step_and_stays_sanitized(
        self,
    ) -> None:
        """ToolResult(False) → 按 Multi-Step contract 继续（不是授权失败路径）。"""
        service, registry, engine, llm = self._build([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT-001"}),
            "数据库暂时不可用。",
        ])

        result = await service.chat("查询 MAT-001 库存", registry=registry)

        assert result.answer == "数据库暂时不可用。"
        assert [c.tool_name for c in result.tool_calls] == ["get_inventory"]
        assert engine.connect_attempts == 1
        payload = _tool_payload(llm)
        assert payload["success"] is False
        _assert_no_secrets(json.dumps(payload, ensure_ascii=False))


# ============================================================
# Case K：Malformed ToolCall（真实 parser）
# ============================================================

def _parser() -> OpenAICompatibleClient:
    """真实 parser 实例（不发起任何网络请求）。"""
    return OpenAICompatibleClient(
        api_key="test-key", base_url="http://localhost/v1", model="test-model"
    )


class TestMalformedToolCall:
    """malformed → LLMToolCallFormatError（复用现有错误类型）+ 无执行。"""

    def test_invalid_json_arguments_rejected_by_parser(self) -> None:
        with pytest.raises(LLMToolCallFormatError):
            _parser()._parse_tool_arguments("c1", "{not-json")

    def test_non_object_arguments_rejected_by_parser(self) -> None:
        with pytest.raises(LLMToolCallFormatError):
            _parser()._parse_tool_arguments("c1", "[1, 2, 3]")

    def test_missing_id_rejected_by_parser(self) -> None:
        with pytest.raises(LLMToolCallFormatError):
            _parser()._parse_tool_calls([
                {"function": {"name": "get_inventory", "arguments": "{}"}},
            ])

    def test_multiple_tool_calls_rejected_by_parser(self) -> None:
        calls = [
            {"id": f"c{i}", "function": {"name": "get_inventory", "arguments": "{}"}}
            for i in range(2)
        ]
        with pytest.raises(LLMToolCallFormatError):
            _parser()._parse_tool_calls(calls)

    async def test_service_propagates_format_error_without_execution(
        self,
    ) -> None:
        handler = _RecordingHandler()
        registry = _registry_with_real_definition(handler)
        execution = _CountingExecution(registry=registry)
        llm = ScriptedLLMClient([LLMToolCallFormatError("arguments 不是合法 JSON")])
        service = ToolChatService(llm_client=llm, execution_service=execution)

        with pytest.raises(LLMToolCallFormatError):
            await service.chat("查库存", registry=registry)

        assert execution.calls == []     # 无 Tool 执行
        assert handler.calls == 0        # 无 Handler / 无 DB
        assert len(llm.calls) == 1       # 不重试 LLM 调用


# ============================================================
# Case I / J：预算边界 与 多 ToolCall
# ============================================================

class TestBudgetBoundary:
    """max_rounds = 1 / 20：不多执行 Tool、不多调用 LLM、不 retry。"""

    async def test_budget_one_round(self) -> None:
        registry = _mock_registry()
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "M1"}),
        ])  # 脚本耗尽后重复最后一项 → 永远请求 Tool
        service = ToolChatService(llm_client=llm, max_tool_rounds=1)

        with pytest.raises(ToolCallingBudgetExceededError) as excinfo:
            await service.chat("q", registry=registry)

        assert excinfo.value.max_rounds == 1
        assert excinfo.value.requested_tool == "get_inventory"
        assert len(llm.calls) == 2       # 1 次执行 + 1 次拒绝（无第 3 次）

    async def test_budget_twenty_rounds(self) -> None:
        registry = _mock_registry()
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "M1"}),
        ])
        service = ToolChatService(llm_client=llm, max_tool_rounds=20)

        with pytest.raises(ToolCallingBudgetExceededError) as excinfo:
            await service.chat("q", registry=registry)

        assert excinfo.value.max_rounds == 20
        assert len(llm.calls) == 21      # 20 次执行 + 1 次拒绝，无 retry


class TestMultipleToolCallsRejected:
    """一轮多个 ToolCall → 任何执行之前拒绝。"""

    async def test_rejected_before_any_execution(self) -> None:
        handler = _RecordingHandler()
        registry = _registry_with_real_definition(handler)
        execution = _CountingExecution(registry=registry)
        llm = ScriptedLLMClient([
            LLMResponse(
                content=None,
                tool_calls=(
                    ToolCall(
                        id="c1",
                        name="get_inventory",
                        arguments={"material_code": "MAT-001"},
                    ),
                    ToolCall(
                        id="c2",
                        name="get_inventory",
                        arguments={"material_code": "MAT-002"},
                    ),
                ),
            ),
            "回答",
        ])
        service = ToolChatService(llm_client=llm, execution_service=execution)

        with pytest.raises(MultipleToolCallsError) as excinfo:
            await service.chat("q", registry=registry)

        assert excinfo.value.count == 2
        assert execution.calls == []     # 拒绝早于执行边界
        assert handler.calls == 0        # Handler 0 / DB 0
        assert len(llm.calls) == 1


# ============================================================
# Error Sanitization（§十七）：失败面不得泄露内部信息
# ============================================================

class TestErrorSanitization:
    """ToolResult / tool message / HTTP / 异常 / DTO 全部不含敏感信息。"""

    def _client(self, monkeypatch, script: list):
        from backend.app.main import app

        registry = _mock_registry()
        monkeypatch.setattr(tool_chat_module, "_tool_registry", registry)
        monkeypatch.setattr(
            tool_chat_module, "_tool_chat_service", ToolChatService(
                llm_client=ScriptedLLMClient(script)
            )
        )
        return TestClient(app)

    def test_tool_result_dto_has_no_internal_fields(self) -> None:
        """ToolResult 字段白名单：无 session / connection / traceback。"""
        import dataclasses

        from backend.app.tools.base import ToolResult

        assert {f.name for f in dataclasses.fields(ToolResult)} == {
            "tool_name", "success", "data", "error",
        }

    async def test_failure_tool_message_keys_and_no_secrets(self) -> None:
        """失败 tool message 只有 success / error，且不含敏感信息。"""
        engine = _FailingEngine()
        registry = _registry_with_real_definition(
            GetInventoryHandler(engine=engine, project_id=_PROJECT_ID)
        )
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT-001"}),
            "回答",
        ])
        service = ToolChatService(llm_client=llm)

        result = await service.chat("q", registry=registry)

        tool_msg = llm.calls[1]["messages"][-1]
        payload = json.loads(tool_msg["content"])
        assert set(payload.keys()) == {"success", "error"}
        _assert_no_secrets(json.dumps(payload, ensure_ascii=False))
        # Service DTO 只含 answer + tool_calls（无 handler / SQL / metadata 泄漏）
        assert set(vars(result)) == {"answer", "tool_calls"}
        _assert_no_secrets(result.answer)

    def test_http_403_capability_denied_sanitized(self, monkeypatch) -> None:
        monkeypatch.setattr(
            tool_chat_module,
            "_project_registry",
            lambda: _StubProjectRegistry({"project-b": ()}),
        )
        client = self._client(
            monkeypatch,
            [
                _tool_call_llm_response(
                    "get_inventory", {"material_code": "MAT-001"}
                ),
                "回答",
            ],
        )

        with client as c:
            response = c.post(
                "/api/chat/with-tools",
                json={"message": "查库存", "project_id": "project-b"},
            )

        assert response.status_code == 403
        _assert_no_secrets(response.text)

    def test_http_502_multiple_tool_calls_sanitized(self, monkeypatch) -> None:
        client = self._client(
            monkeypatch,
            [
                LLMResponse(
                    content=None,
                    tool_calls=(
                        ToolCall(
                            id="c1",
                            name="get_inventory",
                            arguments={"material_code": "M1"},
                        ),
                        ToolCall(
                            id="c2",
                            name="get_inventory",
                            arguments={"material_code": "M2"},
                        ),
                    ),
                ),
            ],
        )

        with client as c:
            response = c.post(
                "/api/chat/with-tools", json={"message": "查库存"}
            )

        assert response.status_code == 502
        _assert_no_secrets(response.text)

    def test_http_502_budget_exceeded_sanitized(self, monkeypatch) -> None:
        from backend.app.main import app

        registry = _mock_registry()
        monkeypatch.setattr(tool_chat_module, "_tool_registry", registry)
        monkeypatch.setattr(
            tool_chat_module,
            "_tool_chat_service",
            ToolChatService(
                llm_client=ScriptedLLMClient([
                    _tool_call_llm_response(
                        "get_inventory", {"material_code": "M1"}
                    ),
                ]),
                max_tool_rounds=1,
            ),
        )

        with TestClient(app) as c:
            response = c.post(
                "/api/chat/with-tools", json={"message": "查库存"}
            )

        assert response.status_code == 502
        _assert_no_secrets(response.text)

    def test_http_503_llm_config_error_sanitized(self, monkeypatch) -> None:
        from backend.app.llm.client import LLMConfigError

        client = self._client(monkeypatch, [LLMConfigError("LLM_API_KEY 未配置")])

        with client as c:
            response = c.post(
                "/api/chat/with-tools", json={"message": "查库存"}
            )

        assert response.status_code == 503
        _assert_no_secrets(response.text)

    def test_http_500_tool_chat_error_sanitized(self, monkeypatch) -> None:
        from backend.app.services.tool_chat_service import ToolChatError

        class _FailingService:
            async def chat(self, message, *, registry, execution_service=None):
                raise ToolChatError("内部失败（无敏感信息）")

        from backend.app.main import app

        monkeypatch.setattr(
            tool_chat_module, "_tool_chat_service", _FailingService()
        )
        with TestClient(app) as c:
            response = c.post(
                "/api/chat/with-tools", json={"message": "查库存"}
            )

        assert response.status_code == 500
        _assert_no_secrets(response.text)
        assert "Traceback" not in response.text

    def test_capability_and_budget_exception_messages_sanitized(self) -> None:
        capability_error = AIOrchestratorCapabilityError(
            "Tool 'get_inventory' 未在该项目启用",
            capability="get_inventory",
            project_id="project-b",
        )
        budget_error = ToolCallingBudgetExceededError(5, "get_inventory")
        _assert_no_secrets(str(capability_error))
        _assert_no_secrets(str(budget_error))


# ============================================================
# Real PostgreSQL：SQL=0 证据 / 失败后继续 / happy path
# ============================================================

@requires_db()
class TestRealToolFailureContractDb:
    """真实 Registry + 真实 DB：失败用例 SQL = 0；happy path 只读。"""

    @pytest.mark.parametrize(
        ("tool_name", "arguments", "error_fragment"),
        [
            ("unknown_tool", {"material_code": "MAT-001"}, "未注册"),          # B
            ("get_inventory", {}, "missing required field(s)"),               # C
            (
                "get_inventory",
                {"material_code": "MAT-001", "fake_field": "x"},
                "unknown field(s)",
            ),                                                                # D
            ("get_inventory", {"material_code": 123}, "expected string"),     # E
        ],
    )
    async def test_rejections_execute_zero_sql(
        self,
        real_engine: Any,
        tool_name: str,
        arguments: dict[str, Any],
        error_fragment: str,
    ) -> None:
        registry = _real_registry(real_engine)
        recorder = _StatementRecorder(real_engine)
        llm = ScriptedLLMClient([
            _tool_call_llm_response(tool_name, arguments),
            "已按错误说明回答。",
        ])
        service = _scoped_service(registry, llm, project_id=_PROJECT_ID)

        try:
            result = await service.chat("查询库存", registry=registry)
        finally:
            recorder.close()

        assert result.answer == "已按错误说明回答。"
        payload = _tool_payload(llm)
        assert payload["success"] is False
        assert error_fragment in payload["error"]
        assert recorder.statements == []      # 真实 DB：0 条语句

    async def test_failure_then_success_continues(self, real_engine: Any) -> None:
        """Case H：Handler 校验失败（0 SQL）→ ToolResult(False) → 继续 → 成功（1 SQL）。"""
        registry = _real_registry(real_engine)
        recorder = _StatementRecorder(real_engine)
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory", {"material_code": "' OR 1=1 --"}, call_id="c1"
            ),
            _tool_call_llm_response(
                "get_inventory", {"material_code": "MAT-001"}, call_id="c2"
            ),
            "第二次查询成功：250。",
        ])
        service = _scoped_service(registry, llm, project_id=_PROJECT_ID)

        try:
            result = await service.chat("先失败再成功", registry=registry)
        finally:
            recorder.close()

        assert [c.tool_name for c in result.tool_calls] == [
            "get_inventory", "get_inventory",
        ]
        assert _tool_payload(llm, 1)["success"] is False
        assert float(_tool_payload(llm, 2)["data"]["qty"]) == pytest.approx(250.0)
        assert len(recorder.selects) == 1      # 失败那次未触达 DB
        recorder.assert_read_only()

    async def test_real_happy_path_regression(self, real_engine: Any) -> None:
        """Case M：MAT-001 → 250.0（SELECT only / READ ONLY / 绑定参数 / timeout / rollback）。"""
        from sqlalchemy import event as sa_event

        registry = _real_registry(real_engine)
        recorder = _StatementRecorder(real_engine)
        rollbacks: list[bool] = []
        sa_event.listen(real_engine, "rollback", lambda conn: rollbacks.append(True))
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT-001"}),
            "MAT-001 当前库存为 250。",
        ])
        service = _scoped_service(registry, llm, project_id=_PROJECT_ID)

        try:
            result = await service.chat("查询 MAT-001 库存", registry=registry)
        finally:
            recorder.close()

        assert result.answer == "MAT-001 当前库存为 250。"
        payload = _tool_payload(llm)
        assert payload["success"] is True
        assert float(payload["data"]["qty"]) == pytest.approx(250.0)

        # 只读证据：恰好 3 条语句（READ ONLY + statement_timeout + 1 条 SELECT）
        recorder.assert_read_only()
        upper = [s.upper() for s in recorder.statements]
        assert len(upper) == 3, upper
        assert sum(1 for s in upper if s.startswith("SELECT")) == 1
        assert any(s.startswith("SET TRANSACTION READ ONLY") for s in upper)
        assert any("SET LOCAL STATEMENT_TIMEOUT" in s for s in upper)
        # 绑定参数（非字符串拼接）
        assert any("MATERIAL_CODE = %(MATERIAL_CODE)S" in s for s in upper)
        # 事务结束即回滚（连接归还；ConnectionEvents.rollback）
        assert rollbacks

    def test_api_capability_denied_executes_zero_sql(
        self, real_engine: Any, monkeypatch
    ) -> None:
        """Case A（HTTP）：403 + Handler 0 + SQL 0（真实 Registry）。"""
        registry = _real_registry(real_engine)
        recorder = _StatementRecorder(real_engine)
        monkeypatch.setattr(tool_chat_module, "_tool_registry", registry)
        monkeypatch.setattr(
            tool_chat_module,
            "_project_registry",
            lambda: _StubProjectRegistry({"project-b": ()}),
        )
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT-001"}),
            "（不应到达这里）",
        ])
        monkeypatch.setattr(
            tool_chat_module,
            "_tool_chat_service",
            ToolChatService(llm_client=llm),
        )

        from backend.app.main import app

        try:
            with TestClient(app) as client:
                response = client.post(
                    "/api/chat/with-tools",
                    json={"message": "查询 MAT-001 库存", "project_id": "project-b"},
                )
        finally:
            recorder.close()

        assert response.status_code == 403
        assert recorder.statements == []
        assert len(llm.calls) == 1
        _assert_no_secrets(response.text)
