"""LLMProvider 抽象 / DeepSeekProvider delegation / Provider 注入测试（Phase 3.10.1）。

覆盖（任务书 §八）：

    1. Provider interface  —— 具体 Provider 满足 ``LLMProvider`` 抽象；
    2. Delegation          —— DeepSeekProvider → Fake DeepSeek Client
                              （参数透传 / 结果转发 / 零网络请求）；
    3. Provider injection  —— 核心服务可注入 ``LLMProvider``，
                              不强绑定具体 DeepSeek Client；
    4. Fake Provider       —— ``FakeLLMProvider``（仅测试使用，
                              不进 production runtime）；
    5. Composition         —— ``create_llm_client`` 装配规则：
                              有 Key → DeepSeekProvider(OpenAICompatibleClient)，
                              无 Key → MockLLMClient。

全部使用 Fake / 内存对象，不发生真实网络请求。
"""
from __future__ import annotations

import copy
from typing import Any

import httpx
import pytest

from backend.app.config import LLMSettings
from backend.app.llm.client import (
    LLMClient,
    LLMConfigError,
    LLMRequestError,
    LLMResponse,
    LLMResponseError,
    LLMToolCallFormatError,
    MockLLMClient,
    OpenAICompatibleClient,
    ToolCall,
    create_llm_client,
)
from backend.app.llm.deepseek_provider import DeepSeekProvider
from backend.app.llm.provider import LLMProvider
from backend.app.llm.retry import is_retryable_llm_error
from backend.app.services.ai_router_service import AIRouterService
from backend.app.services.rag_service import RagService
from backend.app.services.text_to_sql_service import (
    REFUSAL_MARKER,
    TextToSQLService,
)
from backend.app.services.tool_chat_service import ToolChatService


# ============================================================
# Fake DeepSeek Client（模拟现有 OpenAICompatibleClient 调用面）
# ============================================================

class FakeDeepSeekClient:
    """模拟现有 DeepSeek OpenAI-compatible Client 的最小调用面。

    记录全部调用参数供断言；返回预置结果；零网络请求。
    """

    def __init__(self) -> None:
        self.generate_prompts: list[str] = []
        self.chat_calls: list[dict[str, Any]] = []

    async def generate(self, prompt: str) -> str:
        self.generate_prompts.append(prompt)
        return "SELECT 1"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> str | LLMResponse:
        self.chat_calls.append(
            {
                "messages": copy.deepcopy(messages),
                "tools": copy.deepcopy(tools),
            }
        )
        if tools:
            return LLMResponse(
                content=None,
                tool_calls=(
                    ToolCall(
                        id="call_1",
                        name="get_inventory",
                        arguments={"material_code": "MAT001"},
                    ),
                ),
            )
        return "plain answer"


# ============================================================
# Fake LLM Provider（任务书 §8.5：仅测试使用）
# ============================================================

class FakeLLMProvider:
    """极小 Fake ``LLMProvider``（仅测试使用，不进 production runtime）。

    支持两种回放方式：
        * 固定回复：``FakeLLMProvider(answer="...")``；
        * 脚本队列：``FakeLLMProvider(answers=[...])``（每次 chat 依序
          消费一条，用于语义 retry 回归；耗尽后回退固定回复）。
    """

    def __init__(
        self,
        answer: str = "fake answer",
        answers: list[str] | None = None,
    ) -> None:
        self.answer = answer
        self._answers = list(answers) if answers is not None else None
        self.generate_calls: list[str] = []
        self.chat_calls: list[dict[str, Any]] = []

    async def generate(self, prompt: str) -> str:
        self.generate_calls.append(prompt)
        return self.answer

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> str | LLMResponse:
        self.chat_calls.append(
            {"messages": messages, "tools": tools}
        )
        if self._answers:
            return self._answers.pop(0)
        return self.answer


def _make_client(handler) -> OpenAICompatibleClient:
    """用 httpx.MockTransport 构造真实 Client（零网络请求）。"""
    return OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="test-model",
        provider="test",
        transport=httpx.MockTransport(handler),
    )


def _as_provider(provider: Any) -> LLMProvider:
    """结构化验证：实现可赋值给 ``LLMProvider`` 抽象注解。

    Protocol 为 structural typing（非 runtime_checkable），
    赋值给抽象注解即静态类型层面的满足性检查。
    """
    checked: LLMProvider = provider
    return checked


# ============================================================
# 1. Provider interface
# ============================================================

def test_llm_client_alias_is_llm_provider() -> None:
    """client.LLMClient 是 provider.LLMProvider 的向后兼容别名（同一对象）。"""
    assert LLMClient is LLMProvider


def test_deepseek_provider_satisfies_llm_provider_protocol() -> None:
    """DeepSeekProvider 满足 LLMProvider 抽象接口。"""
    provider = DeepSeekProvider(FakeDeepSeekClient())
    _as_provider(provider)  # 静态结构检查
    # 运行时 duck-typing：抽象要求的两个方法均存在且可调用
    assert callable(provider.generate)
    assert callable(provider.chat)


def test_mock_llm_client_satisfies_llm_provider_protocol() -> None:
    """MockLLMClient（无 Key 回退实现）满足 LLMProvider 抽象接口。"""
    provider = MockLLMClient(system_prompt="")
    _as_provider(provider)
    assert callable(provider.generate)
    assert callable(provider.chat)


def test_fake_llm_provider_satisfies_llm_provider_protocol() -> None:
    """FakeLLMProvider 满足 LLMProvider 抽象接口。"""
    provider = FakeLLMProvider()
    _as_provider(provider)
    assert callable(provider.generate)
    assert callable(provider.chat)


def test_fake_provider_not_in_production_runtime() -> None:
    """FakeLLMProvider 只存在于测试模块，production llm 包不导出 / 不含 Fake。"""
    import backend.app.llm.deepseek_provider as deepseek_module
    import backend.app.llm.provider as provider_module

    assert "FakeLLMProvider" not in deepseek_module.__all__
    assert "FakeLLMProvider" not in provider_module.__all__
    assert not hasattr(provider_module, "FakeLLMProvider")
    assert not hasattr(deepseek_module, "FakeLLMProvider")


# ============================================================
# 2. DeepSeek Provider delegation（零网络）
# ============================================================

async def test_deepseek_provider_delegates_generate() -> None:
    """generate：参数原样透传，返回原样转发。"""
    client = FakeDeepSeekClient()
    provider = DeepSeekProvider(client)

    result = await provider.generate("列出所有仓库")

    assert result == "SELECT 1"
    assert client.generate_prompts == ["列出所有仓库"]
    assert client.chat_calls == []  # 未发生其它调用


async def test_deepseek_provider_delegates_chat_without_tools() -> None:
    """chat（无 tools）：messages 原样透传，str 返回原样转发。"""
    client = FakeDeepSeekClient()
    provider = DeepSeekProvider(client)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "你好"},
    ]

    result = await provider.chat(messages)

    assert result == "plain answer"
    assert isinstance(result, str)
    assert client.chat_calls == [{"messages": messages, "tools": None}]


async def test_deepseek_provider_delegates_chat_with_tools() -> None:
    """chat（tools 非空）：tools 原样透传，LLMResponse（含 tool_calls）
    原样转发——不丢失、不转换、不发起网络请求。"""
    client = FakeDeepSeekClient()
    provider = DeepSeekProvider(client)
    messages = [{"role": "user", "content": "查询 MAT001 库存"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_inventory",
                "description": "查询库存",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    result = await provider.chat(messages, tools=tools)

    assert isinstance(result, LLMResponse)
    assert result.content is None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[0].name == "get_inventory"
    assert result.tool_calls[0].arguments == {"material_code": "MAT001"}
    # 参数原样到达内层 Client
    assert client.chat_calls[0]["messages"] == messages
    assert client.chat_calls[0]["tools"] == tools


# ============================================================
# 3. Provider injection（核心服务依赖抽象，不强绑定具体 Client）
# ============================================================

def test_text_to_sql_service_accepts_llm_provider() -> None:
    """TextToSQLService 接收并使用注入的 LLMProvider。"""
    provider = FakeLLMProvider()
    service = TextToSQLService(llm_client=provider)

    assert service._get_llm_client() is provider


async def test_text_to_sql_service_end_to_end_with_fake_provider() -> None:
    """行为回归：Question → TextToSQLService → LLMProvider → SQL
    → Validator 链路在 Provider 抽象下保持不变。"""
    provider = FakeLLMProvider(answer="```sql\nSELECT 1 LIMIT 1\n```")
    service = TextToSQLService(llm_client=provider, max_attempts=1)

    result = await service.generate("知识文档有哪些？", database_context="ctx")

    assert result.validated is True
    assert result.sql == "SELECT 1 LIMIT 1"
    assert len(provider.chat_calls) == 1  # LLM 调用点走 Provider


def test_rag_service_accepts_llm_provider() -> None:
    """RagService（RAG LLM 调用点）接收并使用注入的 LLMProvider。"""
    provider = FakeLLMProvider()
    service = RagService(llm_client=provider)

    assert service._get_llm_client() is provider


def test_tool_chat_service_accepts_llm_provider() -> None:
    """ToolChatService（Tool Calling LLM 调用点）接收 LLMProvider。"""
    provider = FakeLLMProvider()
    service = ToolChatService(provider)

    assert service._llm_client is provider


def test_ai_router_service_accepts_llm_provider() -> None:
    """AIRouterService（Router LLM fallback 调用点）接收 LLMProvider。"""
    provider = FakeLLMProvider()
    service = AIRouterService(llm_client=provider)

    assert service._llm is provider


# ============================================================
# 4. Composition（application/root 层装配规则，不触网）
# ============================================================

def test_create_llm_client_with_key_wraps_existing_client() -> None:
    """有 Key：DeepSeekProvider 包装**现有** OpenAICompatibleClient
    （不重新实现 HTTP Client；配置语义不变）。"""
    llm_settings = LLMSettings(
        api_key="test-key",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        provider="deepseek",
    )

    provider = create_llm_client(llm_settings)

    assert isinstance(provider, DeepSeekProvider)
    # 现有 DeepSeek Client 原样作为 delegation 内层
    assert isinstance(provider._client, OpenAICompatibleClient)
    # Provider 接口不持有 / 不暴露 API Key
    assert not hasattr(provider, "api_key")


def test_create_llm_client_without_key_returns_mock_provider() -> None:
    """无 Key：回退 MockLLMClient（同样满足 LLMProvider 抽象）。"""
    llm_settings = LLMSettings(api_key="", base_url="", model="")

    provider = create_llm_client(llm_settings)

    assert isinstance(provider, MockLLMClient)
    _as_provider(provider)


# ============================================================
# Phase 3.10.2 — 1. Timeout configuration
# ============================================================

def test_openai_compatible_client_applies_configured_timeouts() -> None:
    """构造参数 → httpx.Timeout 正确应用（Timeout owner = Client 层）。"""
    client = OpenAICompatibleClient(
        api_key="k",
        base_url="https://api.example.com/v1",
        model="m",
        timeout_connect=1.5,
        timeout_read=30.0,
        timeout_write=2.5,
        timeout_pool=3.5,
    )

    timeout = client._timeout
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 1.5
    assert timeout.read == 30.0
    assert timeout.write == 2.5
    assert timeout.pool == 3.5


def test_create_llm_client_passes_timeouts_through_provider() -> None:
    """LLMSettings.timeout_* → 工厂 → DeepSeekProvider → 内层 Client 正确传递。"""
    llm_settings = LLMSettings(
        api_key="k",
        base_url="https://api.example.com/v1",
        model="m",
        timeout_connect=1.0,
        timeout_read=2.0,
        timeout_write=3.0,
        timeout_pool=4.0,
    )

    provider = create_llm_client(llm_settings)

    assert isinstance(provider, DeepSeekProvider)
    timeout = provider._client._timeout
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (
        1.0, 2.0, 3.0, 4.0,
    )


def test_llm_settings_timeout_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """默认值：connect 10s / read 60s / write 10s / pool 10s（未配置时）。"""
    for key in (
        "LLM_TIMEOUT_CONNECT",
        "LLM_TIMEOUT_READ",
        "LLM_TIMEOUT_WRITE",
        "LLM_TIMEOUT_POOL",
    ):
        monkeypatch.delenv(key, raising=False)

    s = LLMSettings()

    assert s.timeout_connect == 10.0
    assert s.timeout_read == 60.0
    assert s.timeout_write == 10.0
    assert s.timeout_pool == 10.0


# ============================================================
# Phase 3.10.2 — 2. Retry classification（纯函数；无网络 / 无 sleep）
# ============================================================

@pytest.mark.parametrize("status_code", [429, 500, 502, 503])
def test_retryable_http_status_codes(status_code: int) -> None:
    """429 / 5xx → 可重试。"""
    error = LLMRequestError(f"HTTP {status_code}", status_code=status_code)
    assert is_retryable_llm_error(error) is True


@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
def test_non_retryable_http_status_codes(status_code: int) -> None:
    """4xx（除 429）→ 不可重试（请求本身有问题，重试必然复现）。"""
    error = LLMRequestError(f"HTTP {status_code}", status_code=status_code)
    assert is_retryable_llm_error(error) is False


@pytest.mark.parametrize(
    "error",
    [
        LLMRequestError("连接失败"),   # status_code=None → 网络级失败
        LLMRequestError("请求超时"),   # status_code=None → 网络级失败
    ],
)
def test_transport_level_errors_are_retryable(error: LLMRequestError) -> None:
    """连接失败 / 超时（status_code=None）→ 可重试。"""
    assert error.status_code is None
    assert is_retryable_llm_error(error) is True


def test_config_error_not_retryable() -> None:
    """配置错误（Key / URL / Model 缺失）→ 不可重试。"""
    assert is_retryable_llm_error(LLMConfigError("LLM_API_KEY 未配置")) is False


def test_response_error_not_retryable() -> None:
    """响应结构异常（含 tool call 格式错误）→ 不可重试。"""
    assert is_retryable_llm_error(LLMResponseError("结构不符合预期")) is False
    assert is_retryable_llm_error(LLMToolCallFormatError("tool_call 非法")) is False


def test_non_llm_exception_not_retryable() -> None:
    """非 LLM 层异常（代码 bug / 意外异常）→ 不做隐式重试。"""
    assert is_retryable_llm_error(ValueError("bug")) is False
    assert is_retryable_llm_error(KeyError("x")) is False


def test_classification_is_deterministic_and_pure() -> None:
    """确定性：同一异常重复分类结果一致；分类不修改异常对象。"""
    error = LLMRequestError("x", status_code=429)
    first = is_retryable_llm_error(error)
    second = is_retryable_llm_error(error)
    assert first is second is True
    assert error.status_code == 429  # 输入未被修改（无副作用）


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(429, True), (500, True), (502, True), (503, True),
     (400, False), (401, False), (403, False)],
)
async def test_real_client_http_errors_classify_correctly(
    status_code: int, expected: bool
) -> None:
    """真实 Client 非 2xx 抛出的 LLMRequestError 携带结构化 status_code，
    且分类函数给出正确结论（分类依据不是异常 message 字符串）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": {"message": "x"}})

    provider = DeepSeekProvider(_make_client(handler))

    with pytest.raises(LLMRequestError) as exc_info:
        await provider.generate("hi")

    assert exc_info.value.status_code == status_code
    assert is_retryable_llm_error(exc_info.value) is expected


# ============================================================
# Phase 3.10.2 — 3. No automatic retry（one call → one invocation）
# ============================================================

async def test_no_automatic_retry_on_success() -> None:
    """成功路径：one call → 一次底层 client invocation（无隐式多次调用）。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant",
                                           "content": "ok"}}]},
        )

    provider = DeepSeekProvider(_make_client(handler))

    assert await provider.generate("hi") == "ok"
    assert calls["n"] == 1


async def test_no_automatic_retry_on_failure() -> None:
    """失败路径（503）：同样只发起一次底层调用——本阶段不实现
    transport retry，失败不会产生 sleep + retry 循环。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"error": "unavailable"})

    provider = DeepSeekProvider(_make_client(handler))

    with pytest.raises(LLMRequestError):
        await provider.generate("hi")

    assert calls["n"] == 1


async def test_no_automatic_retry_on_timeout() -> None:
    """超时路径：只发起一次底层调用（超时 → LLMRequestError，无重试）。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("too slow")

    provider = DeepSeekProvider(_make_client(handler))

    with pytest.raises(LLMRequestError):
        await provider.generate("hi")

    assert calls["n"] == 1


# ============================================================
# Phase 3.10.2 — 4. Provider abstraction（SDK / HTTP 异常不泄漏）
# ============================================================

async def test_transport_errors_do_not_leak_through_provider() -> None:
    """Provider 对上层隐藏 transport 实现：连接失败 → LLM 层异常，
    httpx 异常类型不泄漏到 AI Core。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    provider = DeepSeekProvider(_make_client(handler))

    with pytest.raises(LLMRequestError) as exc_info:
        await provider.generate("hi")

    assert not isinstance(exc_info.value, httpx.HTTPError)
    assert exc_info.value.status_code is None
    assert is_retryable_llm_error(exc_info.value) is True


async def test_timeout_errors_do_not_leak_through_provider() -> None:
    """读超时 → LLM 层异常（非 httpx 类型），且被分类为可重试。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    provider = DeepSeekProvider(_make_client(handler))

    with pytest.raises(LLMRequestError) as exc_info:
        await provider.generate("hi")

    assert not isinstance(exc_info.value, httpx.HTTPError)
    assert exc_info.value.status_code is None
    assert is_retryable_llm_error(exc_info.value) is True


# ============================================================
# Phase 3.10.2 — 5. Text-to-SQL semantic retry regression
# ============================================================

async def test_text_to_sql_semantic_retry_regression() -> None:
    """语义级 retry 保持原行为：
    invalid SQL（缺 LIMIT）→ Validator reject → LLM 语义 retry
    → 第二次生成通过。这是业务层 retry，不是 transport retry；
    Provider 层不做任何干预。"""
    provider = FakeLLMProvider(answers=["SELECT 1", "SELECT 1 LIMIT 1"])
    service = TextToSQLService(llm_client=provider, max_attempts=3)

    result = await service.generate("知识文档有哪些？", database_context="ctx")

    assert result.validated is True
    assert result.sql == "SELECT 1 LIMIT 1"
    assert result.attempts == 2            # Validator 拒绝 1 次 → 语义 retry 1 次
    assert len(provider.chat_calls) == 2   # 两次都是真实 LLM 调用点


# ============================================================
# Phase 3.10.2 — 6. Refusal regression（Phase 3.9.25 行为不变）
# ============================================================

class _RefusalGuardValidator:
    """Refusal 路径守卫 Validator：一旦被调用即失败（Validator=0）。"""

    def __init__(self) -> None:
        self.calls = 0

    def validate(self, sql: str, **kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("refusal 路径不应进入 Validator")


async def test_refusal_not_treated_as_transport_failure() -> None:
    """refusal 不被误判为 transport failure：
    attempts=1、单次 LLM 调用、Validator=0、无任何 retry。
    （Executor 不在 TextToSQLService 链路内；sql=None 保证不可被执行。）"""
    provider = FakeLLMProvider(answer=REFUSAL_MARKER)
    validator = _RefusalGuardValidator()
    service = TextToSQLService(
        llm_client=provider, validator=validator, max_attempts=3,
    )

    result = await service.generate("删除所有库存", database_context="ctx")

    assert result.status == "refusal"
    assert result.attempts == 1
    assert result.sql is None
    assert result.validated is False
    assert result.refusal_reason is not None
    assert len(provider.chat_calls) == 1   # llm_calls=1，无 retry
    assert validator.calls == 0            # Validator=0
