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

接入说明（当前架构决策，Phase 3.10.6）：

    ``OpenAICompatibleClient.chat()`` 的对外契约是
    ``str | LLMResponse``（Phase 2 / 3.6.2 语义，不可破坏）；
    ``LLMResponse.metadata`` 是白名单契约（provider / request_id）。
    因此本阶段**不**把 Observation 塞入返回值或 metadata，
    也不使用有并发缺陷的"实例最后状态"。生命周期边界为：
    调用方在 Provider 调用前后用 ``time.perf_counter()`` 记录起点，
    结束后调用 :func:`build_llm_observation_safe`（response 或
    exception）得到 Observation。业务服务本阶段不接入。
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, fields
from typing import Any

from backend.app.llm.client import LLMResponse, LLMUsage

logger = logging.getLogger(__name__)

__all__ = [
    "LLMObservation",
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
    response: LLMResponse | None = None,
    error: BaseException | None = None,
    provider: str | None = None,
) -> LLMObservation:
    """从单次 LLM 调用的结果构造 Observation（生命周期边界）。

    生命周期：call starts（``started_at = time.perf_counter()``）
    → Provider call → record response / exception → 本函数。

    Args:
        started_at: ``time.perf_counter()`` 起点；None → latency=None
                    （不伪造测量值）。
        response:   成功时 Provider 返回的 LLMResponse。
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
    # ---- success observation ----
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
    response: LLMResponse | None = None,
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
