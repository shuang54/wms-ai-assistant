"""LLM Usage & Cost Consumption Boundary（Phase 3.10.13）。

明确"Usage / Cost 数据由谁消费、消费边界是什么"：

    LLM Request
        ↓
    LLMObservation（runtime facts）
        ↓
    LLMUsage（Provider usage fact）
        ↓
    Usage / Cost Consumer（本模块，纯函数）

三层语义（务必区分）：

    Usage  = 已发生的事实（provider 返回的 token 计数；非估算 /
             非计算 / 非 billing token）
    Pricing= 外部显式输入（调用方提供，本模块不猜价）
    Cost   = 显式计算结果（Usage + Pricing → LLMCost）

边界：

    * **纯函数**：无 IO / 无网络 / 无 DB / 无文件 / 无 sleep /
      无 retry / 无全局或共享可变状态；deterministic；
    * **request-level**：一次 Observation 一次消费；不做
      session / user / project / day / month aggregation；
    * **identity 保持**：``consume_usage(observation) is
      observation.usage``——不复制、不重建 LLMUsage；
    * **不补全 / 不估算**：usage=None → None（不是 0、不是
      ``LLMUsage(0, 0, 0)``）；partial usage 原样保留；
    * **不新增重复 DTO**：消费结果就是 ``LLMUsage`` / ``LLMCost``
      （不创建 UsageRecord / AccountingRecord 等）；
    * **Cost 复用现有** ``calculate_llm_cost``（不重复实现）；
      ``pricing=None`` → ``None``（不猜价；不修改已有 Contract）；
    * Consumer 只读取 ``observation.usage``——不接触 prompt /
      messages / SQL / RAG chunks / tool arguments / tool results /
      raw response / headers / API key / database URL；
    * Consumer 是 **optional** API：不使用它，现有
      LLMObservationSink / LLMAccountingSink / Client 行为完全不变。
"""
from __future__ import annotations

from backend.app.llm.accounting import (
    LLMCost,
    LLMPricing,
    calculate_llm_cost,
    token_accounting_from_usage,
)
from backend.app.llm.client import LLMUsage
from backend.app.llm.observability import LLMObservation

__all__ = [
    "consume_usage",
    "consume_cost",
]


def consume_usage(observation: LLMObservation | None) -> LLMUsage | None:
    """消费一次 LLM 请求的 Usage（request-level，纯函数）。

    Observation → usage：保持 identity（同一对象），不复制、
    不重算 ``total_tokens``、不做 ``prompt + completion``、
    不估算、不 round、不补全缺失字段。

    Args:
        observation: 一次 LLM 请求的 Observation。

    Returns:
        ``observation.usage`` 本身（identity）；
        observation 为 None 或其 usage 为 None → ``None``。
    """
    if observation is None:
        return None
    return token_accounting_from_usage(observation.usage)


def consume_cost(
    observation: LLMObservation | None,
    pricing: LLMPricing | None,
) -> LLMCost | None:
    """消费一次 LLM 请求的成本（request-level，纯函数）。

    流程（不重复实现计算，只做边界转发）：

        observation → usage → calculate_llm_cost(usage, pricing)

    Args:
        observation: 一次 LLM 请求的 Observation（提供 usage）。
        pricing:     **显式**价格方案；``None`` → ``None``
                     （不猜价、不自动获取、不硬编码 model→price）。

    Returns:
        LLMCost（Decimal，原始精度）；
        observation / pricing / usage 任一为 None → ``None``。
    """
    if observation is None or pricing is None:
        return None
    return calculate_llm_cost(observation.usage, pricing)
