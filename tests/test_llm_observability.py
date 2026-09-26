"""LLM Observability Contract 测试（Phase 3.10.6）。

覆盖（任务书 §十九 Test 1-16）：

    1.  成功调用 success=True
    2.  失败调用 success=False
    3.  latency 合法值（0 / 1 / 100.5）
    4.  latency 非法值（-1 / NaN / Infinity）
    5.  model 来自 LLMResponse.model（非配置值）
    6.  provider 来自 metadata / provider Contract
    7.  usage 复用 LLMUsage（不复制字段）
    8.  request_id 来自真实 metadata；缺失 → None；不生成 UUID
    9.  finish_reason 原样保留
    10. failure observation（usage=None / request_id=None）
    11. error_type = 类名，不含 exception message
    12. secret isolation（api_key / authorization / password / headers /
        prompt / messages / raw_response / database_url）
    13. cost isolation（无 cost / price / currency 字段）
    14. DB isolation（0 DB calls，无 DB import）
    15. network isolation（Observation 构造 0 网络调用）
    16. observability failure isolation（构造失败不影响业务结果）
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
import time

import pytest

from backend.app.llm.client import LLMRequestError, LLMResponse, LLMUsage
from backend.app.llm.observability import (
    LLMObservation,
    build_llm_observation,
    build_llm_observation_safe,
    observation_field_names,
)


# ============================================================
# Helpers
# ============================================================

def _full_response(**overrides) -> LLMResponse:
    """构造携带完整 contract 字段的 LLMResponse（内存对象）。"""
    kwargs = dict(
        content="answer",
        tool_calls=(),
        model="deepseek-chat",
        finish_reason="stop",
        usage=LLMUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120),
        metadata={"provider": "deepseek", "request_id": "chatcmpl-abc-123"},
    )
    kwargs.update(overrides)
    return LLMResponse(**kwargs)


def _recent_start() -> float:
    """模拟调用起点（0.5ms 前的 perf_counter）。"""
    return time.perf_counter()


# ============================================================
# Test 1 / Test 5-9：成功调用全字段映射
# ============================================================

def test_successful_call_observation() -> None:
    """Test 1 / 5 / 6 / 7 / 8 / 9：成功调用 → success=True 且全部字段
    来自 LLMResponse Contract（model / provider / usage / request_id /
    finish_reason），latency 来自真实测量。"""
    response = _full_response()
    observation = build_llm_observation(
        started_at=_recent_start(), response=response,
    )

    assert observation.success is True
    assert observation.model == "deepseek-chat"          # 实际响应 model
    assert observation.provider == "deepseek"            # metadata contract
    assert observation.usage is response.usage           # 复用同一 LLMUsage
    assert observation.request_id == "chatcmpl-abc-123"  # metadata contract
    assert observation.finish_reason == "stop"
    assert observation.error_type is None
    assert observation.latency_ms is not None
    assert observation.latency_ms >= 0


def test_model_comes_from_response_not_config() -> None:
    """Test 5：model 取 LLMResponse.model；响应缺 model → None，
    绝不用配置值兜底（本测试不接触任何 settings）。"""
    observation = build_llm_observation(
        response=_full_response(model=None),
    )
    assert observation.model is None

    observation2 = build_llm_observation(
        response=_full_response(model="actual-served-model"),
    )
    assert observation2.model == "actual-served-model"


def test_provider_from_metadata_contract() -> None:
    """Test 6：provider 优先来自 metadata（契约来源）；
    metadata 缺失时允许调用方显式传入确定标识；均无 → None（不猜测）。"""
    assert build_llm_observation(
        response=_full_response(metadata={"provider": "from-metadata"}),
    ).provider == "from-metadata"

    assert build_llm_observation(
        response=_full_response(metadata={}),
        provider="explicit-tag",
    ).provider == "explicit-tag"

    assert build_llm_observation(
        response=_full_response(metadata={}),
    ).provider is None


def test_usage_reuses_llm_usage_type() -> None:
    """Test 7：usage 复用 LLMUsage（同一对象），不复制 token 字段。"""
    usage = LLMUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120)
    observation = build_llm_observation(response=_full_response(usage=usage))

    assert observation.usage is usage
    assert observation.usage is not None
    assert observation.usage.prompt_tokens == 100
    # Observation 自身没有 token 复制字段
    assert "prompt_tokens" not in observation_field_names()


def test_request_id_from_metadata_none_when_missing() -> None:
    """Test 8：request_id 来自 metadata；缺失 → None。"""
    assert build_llm_observation(
        response=_full_response(metadata={"provider": "p"}),
    ).request_id is None

    assert build_llm_observation(
        response=_full_response(),
    ).request_id == "chatcmpl-abc-123"


def test_finish_reason_preserved_verbatim() -> None:
    """Test 9：未知 finish_reason 字符串原样保留（不建枚举 / 不转换）。"""
    observation = build_llm_observation(
        response=_full_response(finish_reason="some_future_reason"),
    )
    assert observation.finish_reason == "some_future_reason"


def _imported_modules(module) -> set[str]:
    """解析模块实际 import 的顶层模块名（AST，不受注释/docstring 影响）。"""
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_no_uuid_fabrication_for_request_id() -> None:
    """Test 8（补充）：observability 模块不 import uuid（结构性保证
    不伪造 Provider request ID）。"""
    import backend.app.llm.observability as mod

    assert not any(
        name.split(".")[0] == "uuid" for name in _imported_modules(mod)
    )


# ============================================================
# Test 2 / Test 10 / Test 11：失败调用
# ============================================================

def test_failure_call_observation() -> None:
    """Test 2 / 10：失败调用 → success=False；无 response 依据的字段
    （model / finish_reason / usage / request_id）一律 None（不虚构）。"""
    observation = build_llm_observation(
        started_at=_recent_start(),
        error=LLMRequestError("connection failed"),
        provider="deepseek",  # 调用方已确定的标识（§七示例形态）
    )

    assert observation.success is False
    assert observation.provider == "deepseek"
    assert observation.model is None
    assert observation.finish_reason is None
    assert observation.usage is None
    assert observation.request_id is None
    assert observation.error_type == "LLMRequestError"
    assert observation.latency_ms is not None
    assert observation.latency_ms >= 0


def test_error_type_is_class_name_only() -> None:
    """Test 11：error_type 只含异常类名，绝不含 exception message。"""
    observation = build_llm_observation(
        error=LLMRequestError("SECRET-ERROR-CONTENT sk-live-abcdef"),
    )
    assert observation.error_type == "LLMRequestError"
    assert "SECRET-ERROR-CONTENT" not in str(observation)
    assert "sk-live-abcdef" not in dataclasses.asdict(observation).values()


def test_failure_without_provider_stays_none() -> None:
    """失败且调用方无确定 provider 标识 → None（不猜测、不伪造）。"""
    observation = build_llm_observation(error=LLMRequestError("timeout"))
    assert observation.provider is None
    assert observation.success is False


# ============================================================
# Test 3 / Test 4：latency contract
# ============================================================

@pytest.mark.parametrize("valid_latency", [0, 1, 100.5, 0.0])
def test_latency_valid_values(valid_latency: float) -> None:
    """Test 3：0 / 1 / 100.5 等非负有限值合法。"""
    observation = LLMObservation(latency_ms=valid_latency, success=True)
    assert observation.latency_ms == valid_latency


@pytest.mark.parametrize(
    "bad_latency",
    [-1, -0.001, float("nan"), float("inf"), float("-inf")],
)
def test_latency_invalid_values_rejected(bad_latency: float) -> None:
    """Test 4：负数 / NaN / Infinity 一律拒绝。"""
    with pytest.raises(ValueError):
        LLMObservation(latency_ms=bad_latency, success=True)


def test_latency_none_when_not_measured() -> None:
    """无法可靠测量（started_at=None）→ latency=None（不伪造）。"""
    observation = build_llm_observation(response=_full_response())
    assert observation.latency_ms is None


# ============================================================
# build 入参契约
# ============================================================

def test_build_requires_response_or_error() -> None:
    """response / error 都不提供 → ValueError；同时提供 → ValueError
    （一次调用必居其一，调用方逻辑错误立即暴露）。"""
    with pytest.raises(ValueError):
        build_llm_observation(started_at=_recent_start())
    with pytest.raises(ValueError):
        build_llm_observation(
            response=_full_response(), error=LLMRequestError("x"),
        )


# ============================================================
# Test 12 / Test 13：secret / cost isolation
# ============================================================

def test_secret_isolation() -> None:
    """Test 12：Observation 不携带任何 secrets / 调用内容。"""
    secrets = {
        "api_key": "SK-SECRET-XYZ",
        "authorization": "Bearer SK-SECRET-XYZ",
        "password": "DB-PASSWORD-XYZ",
        "database_url": "postgresql://user:DB-PASSWORD-XYZ@host/db",
        "prompt": "SECRET-PROMPT-CONTENT",
        "messages": '[{"role":"user","content":"SECRET-MESSAGE"}]',
        "raw_response": '{"choices":[]}',
        "headers": '{"authorization":"Bearer X"}',
    }
    # 把 secrets 藏在异常 message 和 response 的各处，验证不会进入 Observation
    poisoned_error = LLMRequestError(" ".join(str(v) for v in secrets.values()))
    poisoned_response = _full_response(
        content=secrets["prompt"],
        metadata={
            "provider": "deepseek",
            "request_id": "chatcmpl-abc-123",
            "api_key": secrets["api_key"],        # 模拟异常 metadata
            "authorization": secrets["authorization"],
        },
    )

    for observation in (
        build_llm_observation(error=poisoned_error, provider="deepseek"),
        build_llm_observation(response=poisoned_response),
    ):
        as_dict = dataclasses.asdict(observation)
        assert set(as_dict) <= {
            "provider", "model", "latency_ms", "success",
            "finish_reason", "usage", "request_id", "error_type",
        }  # 字段白名单：没有任何 secrets 容器字段
        for value in as_dict.values():
            for secret in secrets.values():
                if isinstance(value, str):
                    assert secret not in value
    # metadata 中的多余键（api_key / authorization）不进入 Observation
    clean = dataclasses.asdict(
        build_llm_observation(response=poisoned_response)
    )
    assert "SK-SECRET-XYZ" not in str(clean)
    assert "Bearer" not in str(clean)


def test_cost_isolation() -> None:
    """Test 13：Observation 无 cost / price / currency 字段（usage ≠ cost）。"""
    names = observation_field_names()
    for forbidden in ("cost", "total_cost", "input_price", "output_price",
                      "currency", "price"):
        assert forbidden not in names


def test_no_prompt_content_fields() -> None:
    """§十二：Observation 字段集不含任何调用内容字段。"""
    names = observation_field_names()
    for forbidden in ("messages", "system_prompt", "user_prompt",
                      "prompt", "tools", "tool_definitions",
                      "sql", "chunks", "content"):
        assert forbidden not in names


# ============================================================
# Test 14 / Test 15：DB / network isolation（结构性）
# ============================================================

def test_no_db_imports() -> None:
    """Test 14：observability 模块不 import 任何 DB 相关模块。"""
    import backend.app.llm.observability as mod

    forbidden = {"sqlalchemy", "psycopg", "sqlite3", "redis", "databases"}
    assert not any(
        name.split(".")[0] in forbidden for name in _imported_modules(mod)
    )


def test_no_network_calls_in_observability() -> None:
    """Test 15：observability 模块不 import 任何 HTTP 客户端
    （Observation 构造是纯内存操作，0 网络调用）。"""
    import backend.app.llm.observability as mod

    forbidden = {"httpx", "requests", "aiohttp", "urllib", "urllib3"}
    assert not any(
        name.split(".")[0] in forbidden for name in _imported_modules(mod)
    )


# ============================================================
# Test 16：observability failure isolation
# ============================================================

class _PoisonMetadata(dict):  # type: ignore[type-arg]
    """metadata.get() 抛异常的毒对象（模拟观测层故障）。"""

    def get(self, key: str, default: Any = None) -> Any:
        raise RuntimeError("observation layer failure")


def test_observability_failure_never_breaks_business() -> None:
    """Test 16：Observation 构造失败 → safe 返回 None + 不抛出；
    原 LLMResponse 完全不受影响（业务结果保持原样）。"""
    poisoned_response = LLMResponse(
        content="business answer",
        metadata=_PoisonMetadata(),
    )

    # safe 路径：观测失败被吞掉
    observation = build_llm_observation_safe(
        started_at=_recent_start(), response=poisoned_response,
    )
    assert observation is None

    # 业务结果不受影响：原 response 仍可正常使用
    assert poisoned_response.content == "business answer"
    assert poisoned_response.model is None


def test_safe_wrapper_returns_observation_on_normal_input() -> None:
    """safe 包装对正常输入行为与 build 一致。"""
    response = _full_response()
    observation = build_llm_observation_safe(response=response)
    assert observation is not None
    assert observation.success is True
    assert observation.provider == "deepseek"


def test_safe_wrapper_swallows_build_validation_error() -> None:
    """构造校验异常（如 NaN latency 由外部数据注入）同样被 safe 吞掉。"""
    poisoned_start: float = time.perf_counter()
    # 直接构造含 NaN 的场景：通过 monkeypatch _elapsed_ms 模拟测量层故障
    import backend.app.llm.observability as mod

    original = mod._elapsed_ms
    mod._elapsed_ms = lambda started_at: float("nan")
    try:
        observation = build_llm_observation_safe(
            started_at=poisoned_start, response=_full_response(),
        )
        assert observation is None  # 观测失败 → None，不抛出
    finally:
        mod._elapsed_ms = original


# ============================================================
# Phase 3.10.7 — Observation Integration（接入真实 Client 生命周期）
# ============================================================

import asyncio  # noqa: E402
import json  # noqa: E402

import httpx  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from backend.app.llm.client import (  # noqa: E402
    LLMResponse,
    LLMToolCallFormatError,
    MockLLMClient,
    NoopObservationSink,
    OpenAICompatibleClient,
    create_llm_client,
)
from backend.app.config import LLMSettings  # noqa: E402
from backend.app.llm.observability import (  # noqa: E402
    LLMObservationSink,
    build_llm_observation as _build_for_str_contract,
)
from backend.app.llm.structured import parse_structured_response  # noqa: E402


class CollectingSink:
    """测试用 sink：内存收集全部 Observation。"""

    def __init__(self) -> None:
        self.observations: list[LLMObservation] = []

    def record(self, observation: LLMObservation) -> None:
        self.observations.append(observation)


class FailingSink:
    """故意失败的 sink（模拟观测层故障）。"""

    def record(self, observation: LLMObservation) -> None:
        raise RuntimeError("observation sink failure")


_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_inventory",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


def _obs_client(handler, sink=None) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="configured-model",
        provider="test",
        transport=httpx.MockTransport(handler),
        observation_sink=sink,
    )


def _ok_body(content: str = "hello", **overrides) -> dict:
    body = {
        "id": "chatcmpl-obs-1",
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        },
    }
    body.update(overrides)
    return body


# ---- Test 1-5：成功调用经 sink 暴露 ----

async def test_success_observation_via_sink() -> None:
    """Test 1-5：一次真实调用（MockTransport）产生一个 Observation：
    success / model / usage identity / metadata / latency 全部正确。"""
    sink = CollectingSink()
    client = _obs_client(
        lambda request: httpx.Response(200, json=_ok_body()), sink,
    )

    response = await client.chat(
        [{"role": "user", "content": "hi"}], tools=_TOOLS,
    )

    assert len(sink.observations) == 1  # 恰好一个 Observation
    observation = sink.observations[0]
    assert observation.success is True
    assert observation.latency_ms is not None and observation.latency_ms >= 0
    assert observation.model == "deepseek-chat"       # 实际响应，非配置值
    assert observation.provider == "test"             # metadata contract
    assert observation.request_id == "chatcmpl-obs-1"  # metadata contract
    assert observation.finish_reason == "stop"
    assert observation.usage is response.usage        # 复用同一 LLMUsage
    assert observation.error_type is None


async def test_str_path_observation_via_sink() -> None:
    """无 tools 路径（Phase 2 str 契约）：public 返回仍是 str，
    且 lifecycle 可见 Provider 实际 usage / model / finish_reason /
    request_id（Phase 3.10.12 Usage Visibility Bridge——不回退配置值，
    不虚构，全部来自实际响应）。"""
    sink = CollectingSink()
    client = _obs_client(
        lambda request: httpx.Response(200, json=_ok_body()), sink,
    )

    result = await client.chat([{"role": "user", "content": "hi"}])

    assert result == "hello"                          # public str 契约不变
    assert len(sink.observations) == 1
    observation = sink.observations[0]
    assert observation.success is True
    assert observation.provider == "test"
    # Usage Visibility：来自实际响应（非配置值、非虚构）
    assert observation.model == "deepseek-chat"
    assert observation.usage == LLMUsage(
        prompt_tokens=100, completion_tokens=20, total_tokens=120,
    )
    assert observation.finish_reason == "stop"
    assert observation.request_id == "chatcmpl-obs-1"
    assert observation.latency_ms is not None and observation.latency_ms >= 0


def test_build_supports_str_contract_result() -> None:
    """build helper 直接接受 str 结果（Phase 2 契约分支）。"""
    observation = _build_for_str_contract(response="plain", provider="p")
    assert observation.success is True
    assert observation.provider == "p"
    assert observation.model is None
    assert observation.usage is None
    assert observation.finish_reason is None
    assert observation.request_id is None


# ---- Test 6 / 7：失败 observation 与原始异常传播 ----

async def test_failure_observation_and_reraise() -> None:
    """Test 6：失败调用 → sink 收到 success=False 的 Observation，
    同时 LLMRequestError 继续向上抛出（不被吞掉）。"""
    sink = CollectingSink()
    client = _obs_client(
        lambda request: httpx.Response(503, json={"error": "unavailable"}), sink,
    )

    with pytest.raises(LLMRequestError):
        await client.chat([{"role": "user", "content": "hi"}], tools=_TOOLS)

    assert len(sink.observations) == 1
    observation = sink.observations[0]
    assert observation.success is False
    assert observation.error_type == "LLMRequestError"
    assert observation.model is None
    assert observation.usage is None
    assert observation.request_id is None
    assert observation.finish_reason is None
    assert observation.provider == "test"
    assert observation.latency_ms is not None and observation.latency_ms >= 0


async def test_original_exception_preserved() -> None:
    """Test 7：原始异常原样继续传播——类型不变、__cause__ 链保留
    （Observation 接入不替换 / 不包装原始异常）。"""

    def handler(request):
        raise httpx.ConnectError("boom")

    client = _obs_client(handler, CollectingSink())
    with pytest.raises(LLMRequestError) as exc_info:
        await client.chat([{"role": "user", "content": "hi"}], tools=_TOOLS)

    assert isinstance(exc_info.value, LLMRequestError)
    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)


# ---- Test 8：Observation failure isolation ----

async def test_failing_sink_does_not_affect_success() -> None:
    """Test 8a：sink 故意失败 → 成功调用仍然成功（结果原样）。"""
    client = _obs_client(
        lambda request: httpx.Response(200, json=_ok_body("ok-answer")),
        FailingSink(),
    )
    result = await client.chat([{"role": "user", "content": "hi"}])
    assert result == "ok-answer"


async def test_failing_sink_does_not_affect_failure() -> None:
    """Test 8b：sink 故意失败 → 失败调用仍抛原始 LLMRequestError
    （不被 sink 异常替换）。"""
    client = _obs_client(
        lambda request: httpx.Response(503, json={"error": "x"}), FailingSink(),
    )
    with pytest.raises(LLMRequestError):
        await client.chat([{"role": "user", "content": "hi"}])


async def test_builder_failure_does_not_affect_business(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test 8c：Observation builder 故障 → 成功仍成功、失败仍抛
    原始异常（Observability failure must never become business failure）。"""
    import backend.app.llm.client as client_mod

    def boom(**kwargs):
        raise RuntimeError("builder failure")

    monkeypatch.setattr(client_mod, "build_llm_observation_safe", boom)

    success_client = _obs_client(
        lambda request: httpx.Response(200, json=_ok_body("fine")),
        CollectingSink(),
    )
    assert await success_client.chat([{"role": "user", "content": "hi"}]) == "fine"

    failure_client = _obs_client(
        lambda request: httpx.Response(500, json={"error": "e"}),
        CollectingSink(),
    )
    with pytest.raises(LLMRequestError):
        await failure_client.chat([{"role": "user", "content": "hi"}])


# ---- Test 9 / 10 / 11：Tool Calling / generate() / chat() 契约 ----

async def test_tool_calling_observation_unchanged() -> None:
    """Test 9：Tool Calling 路径——observation 正确记录
    finish_reason="tool_calls"，tool_calls 本身不发生改变。"""
    sink = CollectingSink()
    body = _ok_body(
        content=None,
        finish_reason="tool_calls",
        choices=[
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "get_inventory",
                                "arguments": '{"material_code": "MAT001"}',
                            },
                        }
                    ],
                },
            }
        ],
    )
    client = _obs_client(
        lambda request: httpx.Response(200, json=body), sink,
    )
    response = await client.chat(
        [{"role": "user", "content": "hi"}], tools=_TOOLS,
    )

    assert isinstance(response, LLMResponse)
    assert response.tool_calls[0].name == "get_inventory"  # tool_calls 不变
    assert response.tool_calls[0].arguments == {"material_code": "MAT001"}
    assert len(sink.observations) == 1
    assert sink.observations[0].finish_reason == "tool_calls"
    assert sink.observations[0].success is True


async def test_generate_single_observation() -> None:
    """Test 10：generate() → str 仍然成立；内部经 chat() 复用，
    一次实际 Provider request 恰好一个 Observation（不重复记录）；
    Usage Visibility：observation 携带实际响应的 model。"""
    sink = CollectingSink()
    client = _obs_client(
        lambda request: httpx.Response(200, json=_ok_body()), sink,
    )
    answer = await client.generate("ping")

    assert answer == "hello"
    assert len(sink.observations) == 1
    assert sink.observations[0].success is True
    assert sink.observations[0].model == "deepseek-chat"  # 实际响应 model
    assert sink.observations[0].usage is not None
    assert sink.observations[0].usage.total_tokens == 120


async def test_chat_return_contract_unchanged() -> None:
    """Test 11：chat(messages) → str；chat(messages, tools=...) → LLMResponse
    （接入 Observation 后返回契约完全不变）。"""
    client = _obs_client(
        lambda request: httpx.Response(200, json=_ok_body()), CollectingSink(),
    )
    plain = await client.chat([{"role": "user", "content": "hi"}])
    assert isinstance(plain, str)

    with_tools = await client.chat(
        [{"role": "user", "content": "hi"}], tools=_TOOLS,
    )
    assert isinstance(with_tools, LLMResponse)


# ---- Test 12：Structured Response 兼容 ----

class _DemoAnswer(BaseModel):
    answer: str


async def test_structured_response_still_works() -> None:
    """Test 12：LLMResponse.content → parse_structured_response 正常
    （Observation 记录 LLM request，不包含 parser 延迟/职责）。"""
    sink = CollectingSink()
    body = _ok_body(content='{"answer":"structured-ok"}')
    client = _obs_client(
        lambda request: httpx.Response(200, json=body), sink,
    )
    response = await client.chat(
        [{"role": "user", "content": "hi"}], tools=_TOOLS,
    )
    assert isinstance(response, LLMResponse) and response.content

    typed = parse_structured_response(response.content, _DemoAnswer)
    assert typed.answer == "structured-ok"
    assert len(sink.observations) == 1  # 不因 parser 产生额外 Observation


# ---- Test 14：并发安全 ----

async def test_concurrent_requests_no_cross_talk() -> None:
    """Test 14：同一 client 并发 5 个请求 → 5 个 Observation，
    request_id 互不相同（无共享 last_observation 状态导致交叉覆盖）。"""
    sink = CollectingSink()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        tag = body["messages"][0]["content"]  # req-0 .. req-4
        return httpx.Response(
            200,
            json={
                "id": f"chatcmpl-{tag}",
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant",
                                    "content": f"answer-{tag}"},
                    }
                ],
            },
        )

    client = _obs_client(handler, sink)
    results = await asyncio.gather(
        *[
            client.chat([{"role": "user", "content": f"req-{i}"}],
                        tools=_TOOLS)
            for i in range(5)
        ]
    )

    assert all(r.content == f"answer-req-{i}" for i, r in enumerate(results))
    assert len(sink.observations) == 5  # 每个请求恰好一个 Observation
    request_ids = {obs.request_id for obs in sink.observations}
    assert request_ids == {f"chatcmpl-req-{i}" for i in range(5)}  # 无交叉
    assert all(obs.success for obs in sink.observations)


# ---- 默认行为 / 工厂透传 / No-op ----

async def test_noop_sink_is_default_and_harmless() -> None:
    """默认（不传 sink）= No-op：调用行为完全不变、无异常。"""
    client = _obs_client(
        lambda request: httpx.Response(200, json=_ok_body()),
        None,
    )
    assert isinstance(client._observation_sink, NoopObservationSink)
    assert await client.generate("hi") == "hello"

    NoopObservationSink().record(
        build_llm_observation(response=_full_response())
    )  # no-op，不抛出


def test_create_llm_client_passes_sink_to_real_client() -> None:
    """工厂把 sink 透传给真实 Client（Mock 分支不产生 Observation）。"""
    sink = CollectingSink()
    llm_settings = LLMSettings(
        api_key="k", base_url="https://api.example.com/v1", model="m",
    )
    provider = create_llm_client(llm_settings, observation_sink=sink)
    assert provider._client._observation_sink is sink  # type: ignore[attr-defined]


def test_sink_protocol_structural() -> None:
    """CollectingSink / FailingSink / NoopObservationSink 均满足
    LLMObservationSink Protocol（structural typing）。"""
    def _accept(sink: LLMObservationSink) -> LLMObservationSink:
        return sink

    assert _accept(CollectingSink()) is not None
    assert _accept(FailingSink()) is not None
    assert _accept(NoopObservationSink()) is not None


async def test_observations_never_contain_prompt_content() -> None:
    """§二十一：Observation 不携带请求内容（messages / prompt），
    接入后字段白名单未扩大。"""
    import dataclasses

    sink = CollectingSink()
    client = _obs_client(
        lambda request: httpx.Response(200, json=_ok_body()), sink,
    )
    secret_prompt = "SECRET-PROMPT-CONTENT-XYZ"
    await client.chat([{"role": "user", "content": secret_prompt}],
                      tools=_TOOLS)

    as_dict = dataclasses.asdict(sink.observations[0])
    assert set(as_dict) <= {
        "provider", "model", "latency_ms", "success",
        "finish_reason", "usage", "request_id", "error_type",
    }
    assert secret_prompt not in str(as_dict)
