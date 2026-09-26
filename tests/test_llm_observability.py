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
