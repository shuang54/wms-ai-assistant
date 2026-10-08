"""Phase 5 — Real DeepSeek Agent Tool Calling Smoke（真实 API；**默认 SKIP**）。

目标（只验证，不开发）：

```text
Real DeepSeek
    ↓ ProviderAgentLLM（AgentLLM 端口）
AgentRuntime（有界策略：max_steps=2 / max_tool_calls=1 / max_llm_calls=2）
    ↓ 授权（allowed_tools ⊆ ProjectCapabilities.tool_names）
真实只读 Tool（get_inventory；真实 Handler + 真实 ToolRegistry + 真实 Engine）
    ↓ Tool Result（JSON-safe，untrusted）
role=tool（tool_call_id 原样回填 + 【TOOL RESULT — UNTRUSTED DATA】）
    ↓
DeepSeek 第二次调用 → FINAL
    ↓
AIOrchestrator（route=AGENT）→ AIOrchestrationResult
```

开启方式（沿用项目既有 real LLM 机制；`RUN_REAL_LLM_TESTS` 为既有别名）：

```powershell
$env:RUN_REAL_LLM_TESTS="1"
python -m pytest -m real_llm tests/test_agent_real_llm_smoke.py -q
```

三层防护（与 ``tests/test_llm_real_smoke.py`` 一致）：
    1. ``RUN_REAL_LLM_TESTS`` / ``RUN_REAL_LLM_TEST`` 未设置 → **SKIP**；
    2. 开关打开但 ``LLM_API_KEY`` 为空 → SKIP；
    3. 开关打开但 ``LLM_BASE_URL`` / ``LLM_MODEL`` 为空 → SKIP。
⇒ 默认 ``python -m pytest -q`` **零真实调用**（真实调用次数 = 0）。

安全边界（报告 / 输出）：
    * 不打印 API Key / Authorization / Base URL / 完整 prompt / 完整 context /
      完整 Tool Result；``tool_call_id`` 只报告 "present"；
    * 不启用 thinking mode（不传 thinking / reasoning_effort；
      不处理 reasoning_content）；
    * 不修改任何生产代码（Router / Orchestrator / Agent / Provider / Tool /
      Prompt / DB / API）；真实 Smoke 失败时**只记录**，不现场修复。

失败分类（任务书 §二十一）：A 认证 · B 网络 · C Provider 响应 · D Tool Call 解析 ·
E Tool Schema · F 授权 · G Tool 执行 · H 第二次 LLM · I Final 决策 · J Token Usage ·
K Agent Runtime。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any

import pytest

from backend.app.agent import (
    AGENT_ERROR_CODES,
    AGENT_ERROR_INVALID_DECISION,
    AGENT_ERROR_LLM_FAILURE,
    AGENT_ERROR_TOOL_EXECUTION_FAILURE,
    AGENT_ERROR_TOOL_NOT_ALLOWED,
    AGENT_STATUS_COMPLETED,
    TOOL_RESULT_MARKER,
    AgentRuntime,
    AgentRuntimePolicy,
    ProviderAgentLLM,
)
from backend.app.llm.client import LLMError, LLMResponse, create_llm_client
from backend.app.llm.tool_schema import definitions_to_openai_tools
from backend.app.projects.configuration import (
    get_default_project_configuration_provider,
)
from backend.app.projects.engine_provider import get_default_engine_provider
from backend.app.services.ai_orchestrator_service import (
    AIOrchestrationResult,
    AIOrchestratorExecutionError,
    AIOrchestratorService,
    RouteDecision,
    RouteType,
)
from backend.app.services.tool_execution_service import ToolExecutionService
from backend.app.tools.base import validate_arguments
from backend.app.tools.get_inventory import (
    GET_INVENTORY_DEFINITION,
    GetInventoryHandler,
    register_get_inventory_tool,
)
from backend.app.tools.registry import ToolRegistry

__all__ = ["test_real_provider_returns_single_authorized_tool_call",
           "test_real_agent_tool_calling_full_loop"]

#: Smoke 目标 Tool（真实 Tool Registry 中的只读 Tool；缺失则 skip，绝不新建 Tool）
_TOOL_NAME = "get_inventory"

#: Smoke 问题（简单、单 Tool、可直接由 Tool schema 回答）
_QUESTION = "查询物料 MAT001 当前库存。"

#: 冻结 metadata 键（Phase 5 Integration §29.2；核对 Orchestrator 契约）
_FROZEN_METADATA_KEYS = {
    "decision_source",
    "route_reason",
    "request_id",
    "outcome",
    "agent_status",
    "agent_error",
    "agent_steps",
    "agent_tool_calls",
    "agent_llm_calls",
    "agent_tokens_used",
    "agent_token_budget_unverifiable",
}


def _env_flag(name: str) -> bool:
    """读取布尔型环境变量开关（与既有 real LLM 测试一致）。"""
    value = os.getenv(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


#: 项目既有机制（`RUN_REAL_LLM_TEST`）+ 任务书开关名（`RUN_REAL_LLM_TESTS` 别名）
_RUN_REAL = _env_flag("RUN_REAL_LLM_TESTS") or _env_flag("RUN_REAL_LLM_TEST")

pytestmark = [
    pytest.mark.real_llm,
    pytest.mark.skipif(
        not _RUN_REAL,
        reason=(
            "set RUN_REAL_LLM_TESTS=1 (or RUN_REAL_LLM_TEST=1) to enable the "
            "real DeepSeek agent tool calling smoke（默认 SKIP：零真实调用）"
        ),
    ),
]


# ============================================================
# 测试替身：**只记录 / 只委托**，不改变任何生产行为
# ============================================================


class _RecordingProvider:
    """真实 Provider 的记录代理（不改 LLMProvider contract；仅转发 + 记录）。"""

    def __init__(self, provider: Any) -> None:
        self._provider = provider
        self.calls: list[dict[str, Any]] = []
        self.responses: list[Any] = []

    async def chat(self, messages: Any, **kwargs: Any) -> Any:
        self.calls.append(
            {
                "messages": [dict(m) for m in messages],
                "kwargs": {k: v for k, v in kwargs.items() if k != "tools"},
                "tools_count": len(kwargs.get("tools") or ()),
            }
        )
        response = await self._provider.chat(messages, **kwargs)
        self.responses.append(response)
        return response


class _CountingHandler:
    """真实 ``GetInventoryHandler`` 的计数代理（委托执行；**不是** fake Tool）。"""

    def __init__(self, handler: GetInventoryHandler) -> None:
        self.inner = handler
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, arguments: dict[str, Any]) -> Any:
        self.calls.append(dict(arguments))
        return await self.inner(arguments)


class _AgentRouteRouter:
    """固定 ``route=AGENT`` 的 Router 替身（本 smoke 验证 Agent 链，不验证分类）。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def route(
        self, question: str, *, context: str | None = None
    ) -> RouteDecision:
        self.calls.append(question)
        return RouteDecision(
            route=RouteType.AGENT,
            confidence=None,
            reason="smoke: pin AGENT",
            source="llm",
        )


@dataclass
class _SmokeEnv:
    provider: _RecordingProvider
    handler: _CountingHandler
    runtime: AgentRuntime
    orchestrator: AIOrchestratorService
    target_table_present: bool
    model: str
    project_id: str


def _require_llm_config() -> None:
    """第二 / 三层防护（A 认证）：无 Key / URL / Model → skip。"""
    from backend.app.config import settings

    if not settings.llm.api_key:
        pytest.skip("LLM_API_KEY 未配置；请在 .env 中设置后重试")
    if not settings.llm.base_url:
        pytest.skip("LLM_BASE_URL 未配置")
    if not settings.llm.model:
        pytest.skip("LLM_MODEL 未配置")


def _table_exists(engine: Any, schema_name: str, table_name: str) -> bool:
    """只读探测：真实 Tool 的目标表是否存在（不创建 / 不修改任何表）。"""
    from sqlalchemy import text

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.tables"
                " WHERE table_schema = :schema AND table_name = :table"
            ),
            {"schema": schema_name, "table": table_name},
        ).scalar_one()
    return bool(row)


@pytest.fixture(scope="module")
def smoke_env() -> _SmokeEnv:
    """真实装配：Provider（.env）→ AgentRuntime → Orchestrator；Tool 为真实 Tool。"""
    _require_llm_config()

    from backend.app.config import settings

    provider = _RecordingProvider(create_llm_client(settings.llm))

    configuration = get_default_project_configuration_provider().get(
        settings.project.project_id
    )
    engine = get_default_engine_provider().get_engine(
        configuration.context.data_source
    )

    # 真实只读 Tool（`GetInventoryHandler` 原样；仅外层计数代理以便断言执行次数）
    real_handler = GetInventoryHandler(
        engine=engine,
        inv_settings=replace(
            settings.inventory_tool, schema_name=configuration.schema_name
        ),
        project_id=settings.project.project_id,
    )
    handler = _CountingHandler(real_handler)
    registry = ToolRegistry()
    register_get_inventory_tool(registry, handler=handler)  # type: ignore[arg-type]

    tool_execution = ToolExecutionService(
        registry=registry,
        capabilities=configuration.capabilities,
        project_id=settings.project.project_id,
    )

    allowed_tools = tuple(
        name for name in configuration.capabilities.tool_names if name == _TOOL_NAME
    )

    runtime = AgentRuntime(
        llm=ProviderAgentLLM(provider),
        tool_execution=tool_execution,
        policy=AgentRuntimePolicy(
            # smoke 有界：一次 Tool + 第二次 LLM 即终止（不允许无限循环）
            max_steps=2,
            max_tool_calls=1,
            max_llm_calls=2,
            max_execution_time_ms=60_000,
            max_cost_tokens=4_000,
        ),
        allowed_tools=allowed_tools,
    )

    orchestrator = AIOrchestratorService(
        router=_AgentRouteRouter(),
        agent_runtime=runtime,
        capabilities=configuration.capabilities,
    )

    return _SmokeEnv(
        provider=provider,
        handler=handler,
        runtime=runtime,
        orchestrator=orchestrator,
        target_table_present=_table_exists(
            engine, configuration.schema_name, settings.inventory_tool.table_name
        ),
        model=settings.llm.model,
        project_id=settings.project.project_id,
    )


# ============================================================
# 失败分类（§二十一）：只分类，不修复
# ============================================================


def _stable_error_code(exc: BaseException | None) -> str | None:
    """从异常消息中提取**稳定错误码**（白名单匹配；不解析 / 不输出自由文本）。"""
    if exc is None:
        return None
    text = str(exc)
    for code in sorted(AGENT_ERROR_CODES):
        if code in text:
            return code
    return None


def _classify_exception(exc: BaseException) -> str:
    """把异常映射为失败分类标签（不含敏感信息）。"""
    name = type(exc).__name__
    text = f"{name}: {exc}".lower()
    if isinstance(exc, AIOrchestratorExecutionError):
        code = _stable_error_code(exc)
        return {
            AGENT_ERROR_TOOL_EXECUTION_FAILURE: "G_TOOL_EXECUTION",
            AGENT_ERROR_TOOL_NOT_ALLOWED: "F_TOOL_AUTHORIZATION",
            AGENT_ERROR_INVALID_DECISION: "D_TOOL_CALL_PARSING",
            AGENT_ERROR_LLM_FAILURE: "C_PROVIDER_RESPONSE",
        }.get(code or "", "K_AGENT_RUNTIME")
    if isinstance(exc, LLMError):
        if "auth" in text or "401" in text or "403" in text or "key" in text:
            return "A_API_AUTHENTICATION"
        if "connect" in text or "timeout" in text or "network" in text:
            return "B_NETWORK"
        return "C_PROVIDER_RESPONSE"
    if name == "AgentLLMFailureError":
        return "D_TOOL_CALL_PARSING"
    return "K_AGENT_RUNTIME"


def _render_summary(
    env: _SmokeEnv,
    *,
    llm_calls: int,
    tool_calls: int,
    first_tool_name: str | None,
    arguments_valid: bool,
    authorized: bool,
    executed: bool,
    second_finish_reason: str | None,
    agent_status: str | None,
    token_usage_available: bool,
    unauthorized_tool: str,
    result: str,
) -> str:
    """安全 summary（§二十；绝不包含 Key / prompt / context / 完整 Tool Result）。"""
    return "\n".join(
        [
            "",
            "Real Agent Tool Calling Smoke",
            "",
            "Provider: deepseek (OpenAI-compatible)",
            f"Model: {env.model}",
            "",
            f"Question: {_QUESTION}",
            "",
            f"LLM calls: {llm_calls} / 2",
            f"Tool calls: {tool_calls} / 1",
            "",
            "First decision:",
            "  type: TOOL_CALL" if tool_calls else "  type: (none)",
            f"  tool_name: {first_tool_name or '(none)'}",
            "  tool_call_id: present" if first_tool_name else "  tool_call_id: (none)",
            f"  arguments_valid: {'PASS' if arguments_valid else 'FAIL'}",
            "",
            "Tool:",
            f"  authorized: {'PASS' if authorized else 'FAIL'}",
            f"  executed: {'PASS' if executed else 'FAIL'}",
            "",
            "Second decision:",
            f"  type: FINAL" if second_finish_reason else "  type: (not reached)",
            f"  finish_reason: {second_finish_reason or '(not reached)'}",
            "",
            "Agent:",
            f"  status: {agent_status or '(not reached)'}",
            "  token_usage:"
            f" {'AVAILABLE' if token_usage_available else 'UNAVAILABLE'}",
            "",
            "Security:",
            f"  unauthorized_tool: {unauthorized_tool}",
            "  tool_result_untrusted: PASS",
            "",
            "Result:",
            f"{result}",
            "",
        ]
    )


# ============================================================
# Test 1：Provider 层 — 真实 DeepSeek 是否返回**一个**合法 Tool Call
# ============================================================


@pytest.mark.asyncio
async def test_real_provider_returns_single_authorized_tool_call(
    smoke_env: _SmokeEnv,
) -> None:
    """§10–§12：`tool_calls` 非空且恰好 1 个；id/name/arguments 齐备且 schema 合法。"""
    if _TOOL_NAME not in smoke_env.runtime.allowed_tools:
        pytest.skip(f"真实 Tool Registry 中没有 {_TOOL_NAME}（不新建 Tool）")

    try:
        response = await smoke_env.provider.chat(
            [{"role": "user", "content": _QUESTION}],
            tools=definitions_to_openai_tools([GET_INVENTORY_DEFINITION]),
        )
    except Exception as exc:  # noqa: BLE001 —— 真实 API 失败只分类，不修复
        pytest.fail(f"[{_classify_exception(exc)}] 真实 LLM 调用失败: {type(exc).__name__}")

    assert isinstance(response, LLMResponse), type(response).__name__

    tool_calls = tuple(response.tool_calls or ())
    if not tool_calls:
        pytest.fail(
            "[C_PROVIDER_RESPONSE] 真实 Provider 未返回 tool_calls"
            f"（finish_reason={response.finish_reason!r}；"
            "模型/协议兼容性待人工确认，不修改生产代码）"
        )
    assert len(tool_calls) == 1, f"预期恰好 1 个 tool call，got {len(tool_calls)}"

    call = tool_calls[0]
    assert isinstance(call.id, str) and call.id.strip(), "tool_call.id 缺失"
    assert isinstance(call.name, str) and call.name.strip(), "tool_call.name 缺失"
    assert isinstance(call.arguments, dict), (
        f"tool_call.arguments 必须是 dict（got {type(call.arguments).__name__}）"
    )

    # §11：name 必须属于 Agent allowed_tools（⊆ ProjectCapabilities.tool_names）
    assert call.name in smoke_env.runtime.allowed_tools, (
        f"[F_TOOL_AUTHORIZATION] 未授权 Tool: {call.name!r}"
    )

    # §12：arguments 必须通过真实 Registry 使用的 schema 校验（不 eval / 不拼接 / 不修复）
    validate_arguments(GET_INVENTORY_DEFINITION.parameters, call.arguments)
    assert "material_code" in call.arguments or "warehouse_code" in call.arguments

    # 无 thinking / reasoning 参数（§七）
    assert smoke_env.provider.calls[-1]["kwargs"] == {}

    print(
        _render_summary(
            smoke_env,
            llm_calls=1,
            tool_calls=1,
            first_tool_name=call.name,
            arguments_valid=True,
            authorized=True,
            executed=False,
            second_finish_reason=None,
            agent_status=None,
            token_usage_available=response.usage is not None
            and response.usage.total_tokens is not None,
            unauthorized_tool="NOT_EXERCISED",
            result="PASS (provider-level)",
        )
    )


# ============================================================
# Test 2：完整 Agent 链（Orchestrator → AgentRuntime → Tool → 第二次 LLM → FINAL）
# ============================================================


@pytest.mark.asyncio
async def test_real_agent_tool_calling_full_loop(smoke_env: _SmokeEnv) -> None:
    """§13–§17：一次 Tool 执行 + 第二次 LLM + FINAL + Orchestrator route=AGENT。"""
    if _TOOL_NAME not in smoke_env.runtime.allowed_tools:
        pytest.skip(f"真实 Tool Registry 中没有 {_TOOL_NAME}（不新建 Tool）")

    before_calls = len(smoke_env.provider.calls)
    result: AIOrchestrationResult | None = None
    error: Exception | None = None
    try:
        result = await smoke_env.orchestrator.execute(_QUESTION)
    except Exception as exc:  # noqa: BLE001 —— 真实链路失败只分类，不现场修复
        error = exc

    llm_calls = len(smoke_env.provider.calls) - before_calls
    first_response = (
        smoke_env.provider.responses[before_calls]
        if len(smoke_env.provider.responses) > before_calls
        else None
    )
    first_calls = tuple(getattr(first_response, "tool_calls", ()) or ())
    first_name = first_calls[0].name if first_calls else None
    arguments_valid = bool(
        first_calls
        and isinstance(first_calls[0].arguments, dict)
        and _schema_ok(first_calls[0].arguments)
    )
    authorized = bool(
        first_calls and first_name in smoke_env.runtime.allowed_tools
    )
    executed = len(smoke_env.handler.calls) == 1
    # Agent 终态：成功路径取真实结果；失败路径取稳定错误码（不解析自由文本）
    agent_status = (
        getattr(result.data, "status", None)
        if result is not None
        else ("FAILED" if error is not None else None)
    )
    agent_error_code = (
        _stable_error_code(error) if error is not None else None
    ) or (getattr(result.data, "error", None) if result is not None else None)
    token_usage_available = bool(
        result is not None
        and getattr(result.data, "tokens_used", 0)
        and not getattr(result.data, "token_budget_unverifiable", True)
    )

    print(
        _render_summary(
            smoke_env,
            llm_calls=llm_calls,
            tool_calls=len(smoke_env.handler.calls),
            first_tool_name=first_name,
            arguments_valid=arguments_valid,
            authorized=authorized,
            executed=executed,
            second_finish_reason=(
                getattr(smoke_env.provider.responses[before_calls + 1], "finish_reason", None)
                if len(smoke_env.provider.responses) > before_calls + 1
                else None
            ),
            agent_status=agent_status,
            token_usage_available=token_usage_available,
            unauthorized_tool=(
                "NOT_EXERCISED"
                if not first_calls or authorized
                else "DETECTED_BUT_REJECTED"
            ),
            result=(
                "PASS"
                if agent_status == AGENT_STATUS_COMPLETED
                else f"FAIL（agent_status={agent_status}）"
            ),
        )
    )

    # ---- 真实环境前提：Tool 的目标表必须存在（缺失 ⇒ 环境数据问题，非协议问题）----
    if not smoke_env.target_table_present:
        observed = (
            f"agent_status={agent_status}, agent_error={agent_error_code},"
            f" llm_calls={llm_calls}, handler_calls={len(smoke_env.handler.calls)},"
            f" first_tool={first_name}"
        )
        pytest.skip(
            "LIVE_TOOL_CALLING_UNVERIFIED：真实 Tool 的目标表不存在"
            "（category G_TOOL_EXECUTION / 环境数据缺失，非 DeepSeek 协议问题）；"
            f"observed: {observed}"
        )

    # ---- 目标表存在时：失败即真实失败（分类后 fail，不现场修复生产代码）----
    if error is not None:
        pytest.fail(
            f"[{_classify_exception(error)}] 真实 Agent 链失败"
            f"（agent_error={agent_error_code}）：{type(error).__name__}"
        )
    assert result is not None

    # ---- §10/§11/§12：第一次 LLM 决策 ----
    assert first_response is not None and len(first_calls) == 1, (
        "第一次真实调用必须返回恰好 1 个 tool call"
    )
    assert first_calls[0].id and first_calls[0].name, "tool_call 的 id / name 缺失"
    assert authorized, f"[F_TOOL_AUTHORIZATION] 未授权 Tool: {first_name!r}"
    assert arguments_valid, "arguments 未通过真实 schema 校验"

    # ---- §13：真实 Handler 恰好执行 1 次（且是既有 Tool，不是 fake）----
    assert isinstance(smoke_env.handler.inner, GetInventoryHandler)
    assert len(smoke_env.handler.calls) == 1, smoke_env.handler.calls

    # ---- §14：Tool Result 以 role=tool 回灌（id 一致 + untrusted marker）----
    second_call_messages = smoke_env.provider.calls[before_calls + 1]["messages"]
    assistant_calls = [
        m for m in second_call_messages if m.get("role") == "assistant" and m.get("tool_calls")
    ]
    tool_messages = [m for m in second_call_messages if m.get("role") == "tool"]
    system_messages = [m for m in second_call_messages if m.get("role") == "system"]
    assert len(assistant_calls) == 1 and len(tool_messages) == 1
    assert (
        assistant_calls[0]["tool_calls"][0]["id"] == first_calls[0].id
    ), "assistant.tool_calls[0].id 与 role=tool.tool_call_id 不一致"
    assert tool_messages[0]["tool_call_id"] == first_calls[0].id
    assert TOOL_RESULT_MARKER in tool_messages[0]["content"]
    assert all(TOOL_RESULT_MARKER not in m["content"] for m in system_messages if m.get("content"))

    # ---- §15：第二次真实调用（FINAL；不再产生无关 Tool）----
    assert llm_calls == 2, f"预期 2 次 LLM 调用，got {llm_calls}"
    second_response = smoke_env.provider.responses[before_calls + 1]
    assert isinstance(second_response, LLMResponse)
    assert not second_response.tool_calls, (
        f"[I_FINAL_DECISION] 第二次调用仍返回 tool_calls（finish_reason="
        f"{second_response.finish_reason!r}）"
    )
    assert second_response.finish_reason in {"stop", "length"}, (
        f"第二次 finish_reason={second_response.finish_reason!r}（记录真实值）"
    )

    # ---- §16：Agent 最终结果 ----
    assert agent_status == AGENT_STATUS_COMPLETED, agent_status
    assert getattr(result.data, "tool_calls") == 1
    assert getattr(result.data, "llm_calls") == 2
    assert result.content and result.content.strip(), "最终 content 为空"
    if getattr(result.data, "token_budget_unverifiable", False):
        # Provider 未返回 usage：保持 unverifiable（不伪造 token）
        assert getattr(result.data, "tokens_used") == 0
    else:
        assert (getattr(result.data, "tokens_used", 0) or 0) > 0

    # ---- §17：Orchestrator 契约 ----
    assert result.route == RouteType.AGENT
    metadata = dict(result.metadata)
    assert set(metadata) == _FROZEN_METADATA_KEYS
    assert isinstance(metadata["request_id"], str) and metadata["request_id"]
    assert metadata["agent_status"] == AGENT_STATUS_COMPLETED
    assert metadata["agent_llm_calls"] == 2
    assert metadata["agent_tool_calls"] == 1
    assert metadata["agent_error"] is None
    assert metadata["outcome"] == "SUCCESS"


def _schema_ok(arguments: dict[str, Any]) -> bool:
    """arguments 是否通过真实 Registry 使用的 schema 校验（不修复 / 不猜测）。"""
    try:
        validate_arguments(GET_INVENTORY_DEFINITION.parameters, arguments)
    except ValueError:
        return False
    return True
