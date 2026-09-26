"""LLM Observability Contract（Phase 3.10.6）—— 内部 DTO + 生命周期边界。

架构位置：

    LLM Request
        ↓
    LLM Provider（client.py / deepseek_provider.py）
        ↓
    LLMResponse / Exception
        ↓
    build_llm_observation_safe（本模块，纯函数边界）
        ↓
    LLMObservation（内部 DTO）

边界（务必阅读）：

    * Observation 是**内部结构化数据对象**，只记录调用元数据，
      不记录调用内容；
    * 不记录 Prompt / messages / system_prompt / tool definitions /
      SQL / RAG chunks（§十二 安全边界）；
    * 不记录 secrets：API Key / Authorization / password / headers /
      cookies / raw response / database_url；
    * 不记录 exception message / stack trace / raw provider response，
      失败只记录 ``error_type = type(exc).__name__``；
    * usage 复用 :class:`LLMUsage`（usage ≠ cost：无 price / cost /
      currency 字段）；
    * request_id 只来自 ``LLMResponse.metadata["request_id"]``，
      绝不使用 uuid 伪造；
    * Observation 创建失败绝不影响 LLM 主流程
      （Observability failure must never become business failure，
      见 :func:`build_llm_observation_safe`）；
    * 本阶段不持久化（无 DB / Redis / 文件写入），不接入
      OpenTelemetry / Prometheus / Langfuse / Sentry 等系统，
      不新增任何依赖；Observation 最终写到哪里留待后续阶段。

接入说明（Phase 3.10.7）：

    ``OpenAICompatibleClient`` 在构造时通过 ``observation_sink``
    注入 :class:`LLMObservationSink`（默认 :class:`NoopObservationSink`
    ——接收但不持久化、不外发）。每次实际 Provider 请求在 ``chat()``
    内部产生**恰好一个** Observation：

        t0 = perf_counter()
        try:
            result = impl(...)          # 原有调用逻辑（零改动）
        except BaseException as exc:
            emit(build_llm_observation_safe(error=exc, ...))
            raise                        # 原始异常原样继续传播
        emit(build_llm_observation_safe(response=result, ...))
        return result                    # 原样返回（identity 不变）

    暴露方式为 per-client sink（request-scoped record，无共享
    last_observation 状态 → 并发安全）；``generate()`` 内部复用
    ``chat()``，同一请求不会重复产生 Observation。
    ``LLMResponse.metadata`` 白名单契约（provider / request_id）
    不受影响。
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    # 仅类型检查期引用（本模块运行时零依赖 client，
    # 使 client.py 可正向 import 本模块而无循环）。
    from backend.app.llm.client import LLMResponse, LLMUsage

logger = logging.getLogger(__name__)

__all__ = [
    "LLMObservation",
    "LLMObservationSink",
    "NoopObservationSink",
    "LLMAccountingSink",
    "NoopAccountingSink",
    "build_llm_observation",
    "build_llm_observation_safe",
]


# ============================================================
# DTO
# ============================================================

@dataclass(frozen=True)
class LLMObservation:
    """一次 LLM 调用的观测记录（内部 DTO，Phase 3.10.6）。

    只描述"这次调用发生了什么"，不携带任何调用内容或 secrets。

    Attributes:
        provider:      provider 标识；来源为
                       ``LLMResponse.metadata["provider"]``（契约来源），
                       或调用方显式传入的确定标识（如失败场景）；
                       无法确定时为 None（不猜测）。
        model:         实际响应中的 model（非配置值）；失败 / 无响应
                       时为 None（不强行填配置值）。
        latency_ms:    调用耗时（毫秒，>= 0，有限数）；无法可靠测量时
                       为 None（不伪造）。拒绝负数 / NaN / Infinity / bool。
        success:       True 仅表示 Provider 成功返回符合 Contract 的
                       ``LLMResponse``；异常一律 False。
        finish_reason: 成功时来自 ``LLMResponse.finish_reason``
                       （未知字符串原样保留）；失败时 None。
        usage:         复用 :class:`LLMUsage`（不复制 token 字段）；
                       失败 / 无数据时 None。
        request_id:    来自 ``LLMResponse.metadata["request_id"]``；
                       缺失时 None（绝不生成 UUID）。
        error_type:    失败时的异常类名（``type(exc).__name__``）；
                       不含 exception message / stack trace。成功时 None。

    Raises:
        ValueError: latency_ms 为负数 / NaN / Infinity / 非数值。
    """

    provider: str | None = None
    model: str | None = None
    latency_ms: float | None = None
    success: bool = False
    finish_reason: str | None = None
    usage: LLMUsage | None = None
    request_id: str | None = None
    error_type: str | None = None

    def __post_init__(self) -> None:
        latency = self.latency_ms
        if latency is None:
            return
        if isinstance(latency, bool) or not isinstance(latency, (int, float)):
            raise ValueError(
                f"latency_ms 必须是数值或 None（got {type(latency).__name__}）"
            )
        if math.isnan(latency) or math.isinf(latency):
            raise ValueError("latency_ms 不允许 NaN / Infinity")
        if latency < 0:
            raise ValueError(f"latency_ms 不允许负数（got {latency}）")


# ============================================================
# Observation Sink（Phase 3.10.7：最小暴露接口）
# ============================================================

class LLMObservationSink(Protocol):
    """接收 LLMObservation 的最小接口（Phase 3.10.7）。

    Client 在每次实际 Provider 请求结束后调用 ``record()`` 恰好一次；
    sink 实现自行决定并发安全与去向（本阶段：不持久化、不外发）。
    """

    def record(self, observation: LLMObservation) -> None:
        """接收一条 Observation（不得抛出——Client 侧另有防御）。"""
        ...


class NoopObservationSink:
    """默认 No-op sink：接收但什么都不做（不持久化、不外发、不记日志）。"""

    def record(self, observation: LLMObservation) -> None:
        return None


# ============================================================
# Accounting Sink（Phase 3.10.11：request-level token facts）
# ============================================================

class LLMAccountingSink(Protocol):
    """接收 Observation 并派生 request-level Accounting 的最小接口
    （Phase 3.10.11）。

    契约：

        * Client 对每次实际 Provider 请求调用 ``record()``
          恰好一次（成功与失败都会调用——失败 Observation 的
          usage 为 None）；
        * 实现内部通过 ``token_accounting_from_usage(observation.usage)``
          派生 accounting：usage=None → accounting=None
          （不估算 token、不生成 0 tokens、不伪造 usage）；
        * **只读取** ``observation.usage``——不得读取 messages /
          prompt / SQL / RAG chunks / tool arguments / raw response /
          API key / headers；
        * 抛出任何异常都会被 Client 吞掉（warning）——
          绝不影响 LLM 调用结果与原始异常传播；
        * 不持久化、不外发、不做 aggregation / billing。
    """

    def record(self, observation: LLMObservation) -> None:
        """接收一条 Observation（实现方派生 accounting；不得抛出）。"""
        ...


class NoopAccountingSink:
    """默认 No-op accounting sink：接收但什么都不做
    （accounting_sink=None 等价语义；不持久化、不外发）。"""

    def record(self, observation: LLMObservation) -> None:
        return None


# ============================================================
# 内部 helpers（防御性提取：契约字段缺失 / 类型异常 → None）
# ============================================================

def _elapsed_ms(started_at: float | None) -> float | None:
    """从 perf_counter 起点计算耗时（毫秒）；无起点 → None（不伪造）。"""
    if started_at is None:
        return None
    return (time.perf_counter() - started_at) * 1000.0


def _metadata_str(response: LLMResponse, key: str) -> str | None:
    """从 metadata 取字符串字段；缺失 / 非字符串 → None。"""
    value = response.metadata.get(key)
    return value if isinstance(value, str) else None


# ============================================================
# 生命周期边界（纯函数）
# ============================================================

def build_llm_observation(
    *,
    started_at: float | None = None,
    response: LLMResponse | str | None = None,
    error: BaseException | None = None,
    provider: str | None = None,
) -> LLMObservation:
    """从单次 LLM 调用的结果构造 Observation（生命周期边界）。

    生命周期：call starts（``started_at = time.perf_counter()``）
    → Provider call → record response / exception → 本函数。

    Args:
        started_at: ``time.perf_counter()`` 起点；None → latency=None
                    （不伪造测量值）。
        response:   成功时的调用结果：``LLMResponse``（Tool Calling
                    契约）或 ``str``（Phase 2 契约——此时 model /
                    finish_reason / usage / request_id 无契约依据，
                    一律 None，provider 取显式传入值）。
        error:      失败时捕获的异常（只取类名，不取 message）。
        provider:   调用方已确定的 provider 标识（仅当 response 缺失
                    或其 metadata 无 provider 时使用；不猜测）。

    Returns:
        LLMObservation

    Raises:
        ValueError: response 与 error 均未提供（一次调用必居其一），
                    或同时提供（调用方逻辑错误）。
    """
    if response is None and error is None:
        raise ValueError("response 与 error 必须提供其一")
    if response is not None and error is not None:
        raise ValueError("response 与 error 只能提供其一")

    latency_ms = _elapsed_ms(started_at)

    if error is not None:
        # ---- failure observation（§七）----
        # 不记录 exception message / stack / raw response：
        # usage / request_id / finish_reason / model 均无 response 依据 → None。
        return LLMObservation(
            provider=provider,
            model=None,
            latency_ms=latency_ms,
            success=False,
            finish_reason=None,
            usage=None,
            request_id=None,
            error_type=type(error).__name__,
        )

    assert response is not None  # noqa: S101 —— 上方已互斥校验

    if isinstance(response, str):
        # ---- Phase 2 契约结果（str）：只有 provider / latency / success ----
        # model / usage / finish_reason / request_id 无契约依据 → None
        # （不回退配置值，不虚构）。
        return LLMObservation(
            provider=provider,
            model=None,
            latency_ms=latency_ms,
            success=True,
            finish_reason=None,
            usage=None,
            request_id=None,
            error_type=None,
        )

    # ---- success observation（LLMResponse 契约）----
    # provider：优先 contract 来源（metadata），缺失时用显式传入值。
    resolved_provider = _metadata_str(response, "provider") or provider
    return LLMObservation(
        provider=resolved_provider,
        model=response.model,
        latency_ms=latency_ms,
        success=True,
        finish_reason=response.finish_reason,
        usage=response.usage,
        request_id=_metadata_str(response, "request_id"),
        error_type=None,
    )


def build_llm_observation_safe(
    *,
    started_at: float | None = None,
    response: LLMResponse | str | None = None,
    error: BaseException | None = None,
    provider: str | None = None,
) -> LLMObservation | None:
    """:func:`build_llm_observation` 的安全包装。

    原则：**Observability failure must never become business failure** ——
    构造过程发生任何异常（如异常的 response 对象）时记 warning 并返回
    None，绝不向上抛出、绝不影响 LLM 主流程 / 业务结果。
    """
    try:
        return build_llm_observation(
            started_at=started_at,
            response=response,
            error=error,
            provider=provider,
        )
    except Exception:  # noqa: BLE001 —— 观测失败绝不穿透为业务失败
        logger.warning(
            "LLM observation 构造失败（不影响业务结果）",
            exc_info=True,
            extra={"llm_observation": "build_failed"},
        )
        return None


def observation_field_names() -> frozenset[str]:
    """LLMObservation 的字段名集合（测试 / 未来接入点校验用）。"""
    return frozenset(f.name for f in fields(LLMObservation))
