"""Tool Execution Context Contract（Phase 3.11 Step 13）。

锁定 **Runtime Execution Context** 的 DTO / 生命周期 / 隔离边界：

```text
ToolChatService                       （Context 创建者）
    ↓  request_id（一次 chat 一个）+ round + tool_call_id（LLM 原值）+ project_id（边界作用域）
ToolExecutionContext                   （frozen DTO；字段白名单 4 项）
    ↓
ToolExecutionService.execute(..., context=…)   （接收 / 校验；不改 Context / 不生成 request_id）
    ↓
ToolRegistry.execute(tool_name, arguments)     （**不接收** Context）
    ↓
Tool Handler(arguments)                        （**不接收** Context）
```

覆盖（§十九 / §二十 / §二十三 / §二十四）：

```text
Context DTO       创建 / frozen / 字段校验 / 字段白名单
request_id        非空 / 唯一 / 一次 chat 一个 / 多轮共享 / 无 secret
project_id        来自执行边界作用域（LLM 不可控制）
tool_call_id      = LLM ToolCall.id 原值（不重新生成）
round             从 1 开始 / 每轮递增 / one round = one Tool execution
Lifecycle         Round 1 → Round 2（request_id 相同；tool_call_id / round 不同）
Isolation         arguments / LLM messages / Registry / Handler 均无 Context
Immutability      Context 不被执行边界修改；每轮新实例
Security          Context 只含 4 字段；无 API key / password / connection string
Real Regression   1 个真实 get_inventory（DB-gated，RUN_DB_TESTS=1）
```

0 DB / 0 Network / 0 Real LLM（除 1 个 DB-gated 只读回归）。
"""
from __future__ import annotations

import ast
import dataclasses
import json
import os
from typing import Any

import pytest

from backend.app.projects.capabilities import ProjectCapabilities
from backend.app.services.tool_chat_service import ToolChatService
from backend.app.services.tool_execution_context import (
    ToolExecutionContext,
    new_request_id,
)
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

from tests.test_get_inventory_tool import requires_db  # noqa: E402
from tests.test_tool_chat_real_get_inventory import (  # noqa: E402
    _PROJECT_ID,
    _real_registry,
    _tool_payload,
    real_engine,
)
from tests.test_tool_chat_service import (  # noqa: E402
    ScriptedLLMClient,
    _tool_call_llm_response,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CONTEXT_MODULE = "backend/app/services/tool_execution_context.py"
_EXECUTION_MODULE = "backend/app/services/tool_execution_service.py"
_SERVICE_MODULE = "backend/app/services/tool_chat_service.py"

_SECRETS = (
    "postgresql://", "password", "DATABASE_URL", "Authorization",
    "Bearer ", "sk-", "wms_user", "s3cret",
)


# ============================================================
# 测试替身
# ============================================================

class _RecordingBoundary(ToolExecutionService):
    """真实执行边界 + Context 记录（行为完全委托）。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.contexts: list[ToolExecutionContext | None] = []

    async def execute(  # type: ignore[override]
        self,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
        context: ToolExecutionContext | None = None,
    ) -> Any:
        self.calls.append((tool_name, arguments))
        self.contexts.append(context)
        return await super().execute(
            tool_name, arguments=arguments, context=context
        )


class _CountingRegistry:
    """包装真实 Registry：记录 execute 入参（Registry 侧隔离证据）。"""

    def __init__(self, inner: ToolRegistry) -> None:
        self._inner = inner
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def list_definitions(self):
        return self._inner.list_definitions()

    def get_definition(self, tool_name: str):
        return self._inner.get_definition(tool_name)

    async def execute(self, tool_name: str, arguments: Any = None) -> Any:
        self.calls.append((tool_name, arguments))
        return await self._inner.execute(tool_name, arguments)


class _SignatureRecordingHandler:
    """记录 Handler 收到的位置 / 关键字参数（Handler 侧隔离证据）。"""

    def __init__(self) -> None:
        self.signatures: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.signatures.append((args, kwargs))
        return {"ok": True}


def _mock_registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_mock_tools(registry)
    return registry


def _scoped_boundary(
    registry: ToolRegistry,
    *,
    project_id: str | None = None,
    recording: bool = True,
) -> Any:
    cls = _RecordingBoundary if recording else ToolExecutionService
    capabilities = (
        ProjectCapabilities(tool_names=("get_inventory",))
        if project_id
        else None
    )
    return cls(
        registry=registry,
        capabilities=capabilities,
        project_id=project_id,
    )


def _all_messages_text(llm: ScriptedLLMClient, index: int = -1) -> str:
    return json.dumps(llm.calls[index]["messages"], ensure_ascii=False)


# ============================================================
# Context DTO（§四 / §五 / §二十三）
# ============================================================

class TestContextDto:
    def test_create_with_all_fields(self) -> None:
        ctx = ToolExecutionContext(
            request_id="req-1", round=2, project_id="project-a",
            tool_call_id="call_002",
        )
        assert ctx.request_id == "req-1"
        assert ctx.round == 2
        assert ctx.project_id == "project-a"
        assert ctx.tool_call_id == "call_002"

    def test_optional_fields_default_to_none(self) -> None:
        ctx = ToolExecutionContext(request_id="req-1", round=1)
        assert ctx.project_id is None
        assert ctx.tool_call_id is None

    @pytest.mark.parametrize(
        "field_name", ["request_id", "round", "project_id", "tool_call_id"]
    )
    def test_frozen(self, field_name: str) -> None:
        ctx = ToolExecutionContext(
            request_id="req-1", round=1, project_id="p", tool_call_id="c1"
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(ctx, field_name, "changed")

    @pytest.mark.parametrize("bad", ["", "   ", None, 123, b"x"])
    def test_request_id_must_be_non_empty_str(self, bad: Any) -> None:
        with pytest.raises(ValueError):
            ToolExecutionContext(request_id=bad, round=1)

    @pytest.mark.parametrize("bad", [0, -1, -99, "1", None, True, 1.5])
    def test_round_must_be_positive_int(self, bad: Any) -> None:
        with pytest.raises(ValueError):
            ToolExecutionContext(request_id="req-1", round=bad)

    @pytest.mark.parametrize("field_name", ["project_id", "tool_call_id"])
    @pytest.mark.parametrize("bad", ["", "   ", 123])
    def test_optional_fields_reject_empty_or_wrong_type(
        self, field_name: str, bad: Any
    ) -> None:
        kwargs = {"request_id": "req-1", "round": 1, field_name: bad}
        with pytest.raises(ValueError):
            ToolExecutionContext(**kwargs)

    def test_field_whitelist(self) -> None:
        ctx = ToolExecutionContext(request_id="req-1", round=1)
        ctx.assert_field_whitelist()
        assert {f.name for f in dataclasses.fields(ctx)} == {
            "request_id", "project_id", "tool_call_id", "round",
        }

    def test_context_has_no_secret_values(self) -> None:
        ctx = ToolExecutionContext(
            request_id="req-1", round=1, project_id="project-a",
            tool_call_id="call_001",
        )
        text = f"{ctx!r} {ctx} {dataclasses.asdict(ctx)}"
        for secret in _SECRETS:
            assert secret not in text, secret


# ============================================================
# request_id（§五 / §十七）
# ============================================================

class TestRequestId:
    def test_new_request_id_is_non_empty_str(self) -> None:
        rid = new_request_id()
        assert isinstance(rid, str)
        assert rid.strip()
        assert len(rid) == 36

    def test_new_request_id_is_unique(self) -> None:
        ids = {new_request_id() for _ in range(100)}
        assert len(ids) == 100

    def test_new_request_id_has_no_secret(self) -> None:
        rid = new_request_id()
        for secret in _SECRETS:
            assert secret not in rid, secret

    def test_request_id_does_not_depend_on_tool_name(self) -> None:
        """生成函数与 Tool 无关（无参数；不读取 registry / definition）。"""
        import inspect

        from backend.app.services import tool_execution_context as module

        assert list(inspect.signature(module.new_request_id).parameters) == []
        source = inspect.getsource(module)
        assert "tool_name" not in source


# ============================================================
# 生命周期（§九 / §十九 §20）
# ============================================================

class TestContextLifecycle:
    async def test_single_round_context(self) -> None:
        registry = _mock_registry()
        boundary = _scoped_boundary(registry, project_id="project-a")
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory", {"material_code": "MAT-001"}, call_id="call_001"
            ),
            "回答",
        ])
        service = ToolChatService(llm_client=llm, execution_service=boundary)

        await service.chat("查询库存", registry=registry)

        assert len(boundary.contexts) == 1
        ctx = boundary.contexts[0]
        assert isinstance(ctx, ToolExecutionContext)
        assert ctx.request_id.strip()
        assert ctx.project_id == "project-a"
        assert ctx.tool_call_id == "call_001"
        assert ctx.round == 1

    async def test_multi_round_shared_request_id(self) -> None:
        registry = _mock_registry()
        boundary = _scoped_boundary(registry, project_id="project-a")
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory", {"material_code": "M1"}, call_id="call_001"
            ),
            _tool_call_llm_response(
                "get_inventory", {"material_code": "M2"}, call_id="call_002"
            ),
            _tool_call_llm_response(
                "get_inventory", {"material_code": "M3"}, call_id="call_003"
            ),
            "最终回答",
        ])
        service = ToolChatService(llm_client=llm, execution_service=boundary)

        result = await service.chat("三步查询", registry=registry)

        contexts = boundary.contexts
        assert len(contexts) == 3
        assert len(result.tool_calls) == 3
        # 同一次 chat：request_id 相同
        assert len({c.request_id for c in contexts}) == 1
        # round 从 1 起逐轮递增
        assert [c.round for c in contexts] == [1, 2, 3]
        # tool_call_id = LLM 原值（不重新生成）
        assert [c.tool_call_id for c in contexts] == [
            "call_001", "call_002", "call_003",
        ]
        # project scope 全程一致
        assert {c.project_id for c in contexts} == {"project-a"}

    async def test_distinct_tool_call_ids_per_round(self) -> None:
        registry = _mock_registry()
        boundary = _scoped_boundary(registry)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {}, call_id="abc"),
            _tool_call_llm_response("get_inventory", {}, call_id="xyz"),
            "回答",
        ])
        service = ToolChatService(llm_client=llm, execution_service=boundary)

        await service.chat("q", registry=registry)

        ids = [c.tool_call_id for c in boundary.contexts]
        assert ids == ["abc", "xyz"]
        assert ids[0] != ids[1]

    async def test_each_chat_gets_new_request_id(self) -> None:
        registry = _mock_registry()
        boundary = _scoped_boundary(registry)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {}, call_id="c1"),
            "answer-1",
            _tool_call_llm_response("get_inventory", {}, call_id="c2"),
            "answer-2",
        ])
        service = ToolChatService(llm_client=llm, execution_service=boundary)

        await service.chat("q1", registry=registry)
        await service.chat("q2", registry=registry)

        first, second = boundary.contexts
        assert first.request_id != second.request_id   # 新请求 = 新 request_id
        assert first.round == 1 and second.round == 1  # 每轮重新计数
        assert first.tool_call_id == "c1"
        assert second.tool_call_id == "c2"

    async def test_project_scope_from_boundary_or_none(self) -> None:
        registry = _mock_registry()
        scoped = _scoped_boundary(registry, project_id="project-a")
        unscoped = _scoped_boundary(registry)
        llm_a = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {}), "a",
        ])
        llm_b = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {}), "b",
        ])

        await ToolChatService(
            llm_client=llm_a, execution_service=scoped
        ).chat("q", registry=registry)
        await ToolChatService(
            llm_client=llm_b, execution_service=unscoped
        ).chat("q", registry=registry)

        assert scoped.contexts[0].project_id == "project-a"
        assert unscoped.contexts[0].project_id is None

    async def test_context_never_contains_capability_or_project_registry(self) -> None:
        """Context 只含 4 个字段（无 capability / registry / handler）。"""
        registry = _mock_registry()
        boundary = _scoped_boundary(registry, project_id="project-a")
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {}), "回答",
        ])

        await ToolChatService(
            llm_client=llm, execution_service=boundary
        ).chat("q", registry=registry)

        ctx = boundary.contexts[0]
        assert {f.name for f in dataclasses.fields(ctx)} == {
            "request_id", "project_id", "tool_call_id", "round",
        }
        text = repr(ctx)
        for forbidden in ("capabilit", "registry", "handler", "engine"):
            assert forbidden not in text.lower()


# ============================================================
# Immutability（§二十四）
# ============================================================

class TestImmutability:
    async def test_context_not_mutated_by_execution(self) -> None:
        registry = _mock_registry()
        boundary = _scoped_boundary(registry, project_id="project-a")
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {}, call_id="c1"),
            _tool_call_llm_response("get_inventory", {}, call_id="c2"),
            "回答",
        ])

        await ToolChatService(
            llm_client=llm, execution_service=boundary
        ).chat("q", registry=registry)

        first = boundary.contexts[0]
        # 执行结束后字段未被改写（frozen + 边界不改 Context）
        assert (first.request_id, first.round) == (first.request_id, 1)
        assert first.tool_call_id == "c1"
        with pytest.raises(dataclasses.FrozenInstanceError):
            first.round = 2  # type: ignore[misc]

    async def test_new_context_instance_per_round(self) -> None:
        registry = _mock_registry()
        boundary = _scoped_boundary(registry)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {}, call_id="c1"),
            _tool_call_llm_response("get_inventory", {}, call_id="c2"),
            "回答",
        ])

        await ToolChatService(
            llm_client=llm, execution_service=boundary
        ).chat("q", registry=registry)

        first, second = boundary.contexts
        assert first is not second              # 每轮新实例（不 mutate 旧实例）
        assert id(first) != id(second)

    def test_execution_service_does_not_generate_request_id(self) -> None:
        """request_id 生成只属于 ToolChatService（边界不生成）。"""
        with open(
            os.path.join(REPO_ROOT, *(_EXECUTION_MODULE.split("/"))),
            encoding="utf-8",
        ) as fh:
            source = fh.read()
        assert "new_request_id" not in source
        assert "uuid" not in source


# ============================================================
# Isolation（§十 / §十一 / §十四 / §十五 / §二十二）
# ============================================================

class TestContextIsolation:
    async def test_arguments_contain_no_context(self) -> None:
        registry = _mock_registry()
        boundary = _scoped_boundary(registry, project_id="project-a")
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory", {"material_code": "MAT-001"}, call_id="c1"
            ),
            "回答",
        ])

        await ToolChatService(
            llm_client=llm, execution_service=boundary
        ).chat("查询库存", registry=registry)

        # arguments 逐字等于 LLM 原值（无 request_id / project_id / round）
        assert boundary.calls == [("get_inventory", {"material_code": "MAT-001"})]

    async def test_llm_messages_contain_no_context(self) -> None:
        registry = _mock_registry()
        boundary = _scoped_boundary(registry, project_id="project-a")
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory", {"material_code": "MAT-001"}, call_id="c1"
            ),
            "回答",
        ])
        service = ToolChatService(llm_client=llm, execution_service=boundary)

        await service.chat("查询库存", registry=registry)

        ctx = boundary.contexts[0]
        for index in range(len(llm.calls)):
            text = _all_messages_text(llm, index)
            assert ctx.request_id not in text          # request_id 不进 messages
            assert "project-a" not in text             # project scope 不进 messages
            payload = json.loads(text)
            for message in payload:
                assert "request_id" not in message
                assert "round" not in message
                if message["role"] != "tool":
                    # tool message 的 tool_call_id 属 OpenAI 协议，不是 Context
                    assert "tool_call_id" not in message or (
                        message["role"] == "assistant"
                    )

    async def test_registry_does_not_receive_context(self) -> None:
        registry = _CountingRegistry(_mock_registry())
        boundary = _scoped_boundary(registry, project_id="project-a")  # type: ignore[arg-type]
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory", {"material_code": "MAT-001"}, call_id="c1"
            ),
            "回答",
        ])

        await ToolChatService(
            llm_client=llm, execution_service=boundary
        ).chat("查询库存", registry=registry)  # type: ignore[arg-type]

        # Registry 只看到 (tool_name, arguments)
        assert registry.calls == [("get_inventory", {"material_code": "MAT-001"})]

    def test_registry_execute_signature_has_no_context(self) -> None:
        import inspect

        params = inspect.signature(ToolRegistry.execute).parameters
        assert set(params) == {"self", "tool_name", "arguments"}

    def test_handler_receives_only_arguments(self) -> None:
        import inspect

        signature = inspect.signature(GetInventoryHandler.__call__)
        assert list(signature.parameters) == ["self", "arguments"]

    async def test_llm_cannot_control_project_id(self) -> None:
        """LLM 把 project_id 塞进 arguments → Registry 拒绝（非 schema 字段），
        且 Context.project_id 仍是服务器端作用域（不被 LLM 影响）。"""
        handler = _SignatureRecordingHandler()
        registry = ToolRegistry()
        registry.register(MOCK_INVENTORY_DEFINITION, handler)
        boundary = _scoped_boundary(registry, project_id="project-a")
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory",
                {"material_code": "MAT-001", "project_id": "project-b"},
                call_id="c1",
            ),
            "回答",
        ])

        await ToolChatService(
            llm_client=llm, execution_service=boundary
        ).chat("查询库存", registry=registry)

        assert handler.signatures == []                     # Handler 0 次（未知字段拒绝）
        assert boundary.contexts[0].project_id == "project-a"
        # 服务端未采纳 LLM 伪造的作用域：tool 结果里没有它
        # （assistant 消息回显 LLM 自己的 arguments 属 OpenAI 协议，不算采纳）
        tool_message = llm.calls[1]["messages"][-1]
        assert tool_message["role"] == "tool"
        assert "project-b" not in tool_message["content"]
        assert "unknown field" in tool_message["content"]

    async def test_llm_cannot_control_round_or_request_id(self) -> None:
        handler = _SignatureRecordingHandler()
        registry = ToolRegistry()
        registry.register(MOCK_INVENTORY_DEFINITION, handler)
        boundary = _scoped_boundary(registry, project_id="project-a")
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory",
                {
                    "material_code": "MAT-001",
                    "round": 99,
                    "request_id": "forged",
                },
                call_id="c1",
            ),
            "回答",
        ])

        await ToolChatService(
            llm_client=llm, execution_service=boundary
        ).chat("查询库存", registry=registry)

        ctx = boundary.contexts[0]
        assert ctx.round == 1
        assert ctx.request_id != "forged"
        assert handler.signatures == []


# ============================================================
# 边界校验（§十二 / §十三）
# ============================================================

class TestBoundaryContextValidation:
    async def test_invalid_context_type_rejected(self) -> None:
        registry = _CountingRegistry(_mock_registry())
        boundary = ToolExecutionService(registry=registry)  # type: ignore[arg-type]

        with pytest.raises(TypeError):
            await boundary.execute(
                "get_inventory", arguments={}, context=object()  # type: ignore[arg-type]
            )

        assert registry.calls == []

    async def test_scope_mismatch_rejected(self) -> None:
        registry = _CountingRegistry(_mock_registry())
        boundary = ToolExecutionService(
            registry=registry,  # type: ignore[arg-type]
            capabilities=ProjectCapabilities(tool_names=("get_inventory",)),
            project_id="project-a",
        )
        forged = ToolExecutionContext(
            request_id="req-1", round=1, project_id="project-b",
            tool_call_id="c1",
        )

        with pytest.raises(ValueError):
            await boundary.execute(
                "get_inventory", arguments={"material_code": "M1"}, context=forged
            )

        assert registry.calls == []      # Registry 0 次调用

    async def test_context_optional_and_backward_compatible(self) -> None:
        registry = _mock_registry()
        boundary = ToolExecutionService(registry=registry)

        result = await boundary.execute(
            "get_inventory", arguments={"material_code": "M1"}
        )

        assert result.success is True

    async def test_context_does_not_change_result(self) -> None:
        registry = _mock_registry()
        boundary = _scoped_boundary(registry, project_id="project-a")

        without = await ToolExecutionService(registry=registry).execute(
            "get_inventory", arguments={"material_code": "M1"}
        )
        with_ctx = await boundary.execute(
            "get_inventory",
            arguments={"material_code": "M1"},
            context=ToolExecutionContext(
                request_id="req-1", round=1, project_id="project-a",
                tool_call_id="c1",
            ),
        )

        assert without.data == with_ctx.data
        assert with_ctx.success is True

    def test_api_contract_unchanged(self) -> None:
        """API DTO / endpoint 不因 Context 改变（无 request_id 字段）。"""
        from backend.app.api.tool_chat import ToolChatRequest

        assert set(ToolChatRequest.model_fields) == {"message", "project_id"}


# ============================================================
# Real get_inventory regression（§二十一：1 个 DB-gated）
# ============================================================

@requires_db()
class TestRealToolWithContextDb:
    async def test_real_get_inventory_with_context(
        self, real_engine: Any
    ) -> None:
        """Context 不破坏真实链路：MAT-001 → 250.0 + Context 字段正确。"""
        registry = _real_registry(real_engine)
        boundary = _RecordingBoundary(
            registry=registry, project_id=_PROJECT_ID
        )
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory", {"material_code": "MAT-001"}, call_id="call_001"
            ),
            "MAT-001 当前库存为 250。",
        ])
        service = ToolChatService(llm_client=llm, execution_service=boundary)

        result = await service.chat("查询 MAT-001 库存", registry=registry)

        assert result.answer == "MAT-001 当前库存为 250。"
        payload = _tool_payload(llm)
        assert payload["success"] is True
        assert float(payload["data"]["qty"]) == pytest.approx(250.0)

        ctx = boundary.contexts[0]
        assert ctx.project_id == _PROJECT_ID
        assert ctx.tool_call_id == "call_001"
        assert ctx.round == 1
        assert ctx.request_id.strip()
        # Context 未进入业务 arguments / ToolResult
        assert boundary.calls == [
            ("get_inventory", {"material_code": "MAT-001"})
        ]
        assert "request_id" not in json.dumps(payload, ensure_ascii=False)
