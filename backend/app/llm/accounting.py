"""LLM Token & Cost Accounting Contract（Phase 3.10.9）。

两个不同层次（务必区分）：

    层次 1：provider 事实
        Provider Response → OpenAICompatibleClient → LLMUsage
        （LLMUsage 不知道价格；LLMResponse / LLMObservation
          均不携带 cost / price / currency）

    层次 2：费用计算（本模块，显式输入 Pricing）
        LLMUsage + LLMPricing → calculate_llm_cost → LLMCost

边界：

    * **复用** ``client.LLMUsage`` 作为 Token Accounting DTO
      （frozen、``int | None``、total 一致性构造校验均已满足
      Accounting Contract——不新增重复 DTO）；
    * Accounting 不重新解析 provider response（SDK isolation
      仍由 Client 负责；禁止 raw SDK response → Accounting）；
    * Pricing / Cost 与 Provider / Observation 完全解耦：
      同一个 request 可以套用多套 Pricing；本阶段**不含**
      任何真实 Provider 价格（禁止硬编码 model → price）；
    * 纯函数：无网络 / 无 DB / 无环境依赖 / 无全局可变状态 /
      deterministic（同输入恒同输出）;
    * 不推断 provider 未提供的数据：partial usage 原样保留
      （缺 completion → output/total cost 为 None，不当 0）；
      usage=None → cost=None（不估算 token、不发第二次请求）；
    * 不修正 provider 数据：total 一致性由 LLMUsage 构造层
      保证（非法数据在构造时已被拒绝，不会到达本层）；
    * Decimal 计算（金额语义；Python 标准库，无新依赖），
      **不自动 rounding**——本阶段 Cost 是计算 Contract，
      不定义账单展示 / 结算精度；
    * 不持久化、不结算账单、不做货币转换 / 汇率。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from backend.app.llm.client import LLMUsage

__all__ = [
    "LLMPricing",
    "LLMCost",
    "calculate_llm_cost",
    "token_accounting_from_usage",
]

#: 每百万 token（价格语义单位）。
_MILLION: Decimal = Decimal(1_000_000)


# ============================================================
# 内部校验（严格 Contract：无隐式类型转换）
# ============================================================

def _validate_decimal_amount(owner: str, name: str, value: Any) -> None:
    """金额类字段校验：仅接受非负、有限的 Decimal（bool / str /
    float / int 等一律拒绝——金额语义必须显式 Decimal 构造）。"""
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise ValueError(
            f"{owner}.{name} 必须是 decimal.Decimal"
            f"（got {type(value).__name__}；禁止隐式转换）"
        )
    if value.is_nan() or value.is_infinite():
        raise ValueError(f"{owner}.{name} 不允许 NaN / Infinity")
    if value < 0:
        raise ValueError(f"{owner}.{name} 不允许负数（got {value}）")


def _validate_currency(owner: str, value: Any) -> None:
    """币种校验：必须显式指定非空字符串（不默认任何币种，
    不做大小写转换 / 货币转换）。"""
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError(
            f"{owner}.currency 必须是显式指定的非空字符串"
            f"（got {type(value).__name__}）"
        )
    if not value.strip():
        raise ValueError(f"{owner}.currency 不能为空")


# ============================================================
# DTO
# ============================================================

@dataclass(frozen=True)
class LLMPricing:
    """价格方案（Phase 3.10.9；不含真实 Provider 价格）。

    描述"每百万 token 的单价 + 币种"；由调用方显式提供，
    本模块不内置任何 model → price 映射（禁止
    ``if model == "deepseek-chat": price = ...``）。

    Attributes:
        input_price_per_1m_tokens:  每 1M prompt token 单价（>= 0）。
        output_price_per_1m_tokens: 每 1M completion token 单价（>= 0）。
        currency:                   显式币种（如 "USD"）；不默认、
                                    不转换（成本与价格同币种）。

    Raises:
        ValueError: 价格非 Decimal / 负数 / NaN / Infinity，
                    或 currency 非法。
    """

    input_price_per_1m_tokens: Decimal
    output_price_per_1m_tokens: Decimal
    currency: str

    def __post_init__(self) -> None:
        _validate_decimal_amount(
            "LLMPricing", "input_price_per_1m_tokens",
            self.input_price_per_1m_tokens,
        )
        _validate_decimal_amount(
            "LLMPricing", "output_price_per_1m_tokens",
            self.output_price_per_1m_tokens,
        )
        _validate_currency("LLMPricing", self.currency)


@dataclass(frozen=True)
class LLMCost:
    """一次 LLM 调用的计算成本（计算 Contract；非账单）。

    Attributes:
        input_cost:  prompt 部分成本；usage 缺该侧 token 时为 None
                     （缺失 ≠ 0，不推断）。
        output_cost: completion 部分成本；同上。
        total_cost:  input + output；仅当两侧都可计算时非 None。
        currency:    与来源 LLMPricing 一致的显式币种。

    Raises:
        ValueError: 金额字段非 Decimal / 负数 / NaN / Infinity，
                     total 与 input + output 不一致，或 currency 非法。
    """

    input_cost: Decimal | None
    output_cost: Decimal | None
    total_cost: Decimal | None
    currency: str

    def __post_init__(self) -> None:
        for name in ("input_cost", "output_cost", "total_cost"):
            value = getattr(self, name)
            if value is not None:
                _validate_decimal_amount("LLMCost", name, value)
        if (
            self.input_cost is not None
            and self.output_cost is not None
            and self.total_cost is not None
            and self.total_cost != self.input_cost + self.output_cost
        ):
            raise ValueError(
                "LLMCost.total_cost 必须等于 input_cost + output_cost"
            )
        _validate_currency("LLMCost", self.currency)


# ============================================================
# Token Accounting（§五：优先复用 LLMUsage，不新增重复 DTO）
# ============================================================

def token_accounting_from_usage(usage: LLMUsage | None) -> LLMUsage | None:
    """Token Accounting 视图：把 provider 事实（LLMUsage）纳入记账层。

    行为锚点（Phase 3.10.9 §七 / §八）：

        * ``usage=None`` → ``None``（provider 未提供 → 无记账数据）；
        * partial usage 原样保留（prompt=100 / 其余 None 不补全
          ``total_tokens=100``——不推断 provider 未提供的数据）；
        * 不修正 / 不重算任何数值：total 一致性由 ``LLMUsage``
          构造层保证（违反一致性 / 负数 / 非法类型在构造时已被
          拒绝，不会进入 Accounting 层）。

    Accounting 不重新解析 provider response——SDK isolation
    仍然由 Client 负责。
    """
    return usage


# ============================================================
# Cost Calculation（纯函数）
# ============================================================

def calculate_llm_cost(
    usage: LLMUsage | None,
    pricing: LLMPricing,
) -> LLMCost | None:
    """按 Pricing 计算 LLMUsage 的成本（纯函数，deterministic）。

    公式（input / output 单价可能不同，绝不用 total_tokens 乘统一价）：

        input_cost  = prompt_tokens     / 1_000_000 × input_price
        output_cost = completion_tokens / 1_000_000 × output_price
        total_cost  = input_cost + output_cost

    Partial usage 语义（§十三）：

        * 两侧齐备 → 完整计算；
        * 仅 prompt → 只有 input_cost（output / total 为 None）；
        * 仅 completion → 只有 output_cost（input / total 为 None）；
        * usage=None → 返回 None（cost=None）。

    纵深防御：负数 token 在 ``LLMUsage`` 构造层已被拒绝；本函数
    再次拒绝（正常路径不会触发）。

    Args:
        usage:    LLM 调用的 token usage；None → 返回 None。
        pricing:  显式价格方案（本模块不提供任何真实价格）。

    Returns:
        LLMCost（Decimal，原始精度，不自动 rounding）；
        usage 为 None 时返回 None。
    """
    if usage is None:
        return None

    for name in ("prompt_tokens", "completion_tokens"):
        tokens = getattr(usage, name)
        if tokens is not None:
            if isinstance(tokens, bool) or not isinstance(tokens, int):
                raise ValueError(
                    f"usage.{name} 必须是 int 或 None"
                    f"（got {type(tokens).__name__}）"
                )
            if tokens < 0:
                raise ValueError(f"usage.{name} 不允许负数（got {tokens}）")

    input_cost: Decimal | None = None
    if usage.prompt_tokens is not None:
        input_cost = (
            Decimal(usage.prompt_tokens)
            * pricing.input_price_per_1m_tokens
            / _MILLION
        )

    output_cost: Decimal | None = None
    if usage.completion_tokens is not None:
        output_cost = (
            Decimal(usage.completion_tokens)
            * pricing.output_price_per_1m_tokens
            / _MILLION
        )

    total_cost: Decimal | None = None
    if input_cost is not None and output_cost is not None:
        total_cost = input_cost + output_cost

    return LLMCost(
        input_cost=input_cost,
        output_cost=output_cost,
        total_cost=total_cost,
        currency=pricing.currency,
    )
