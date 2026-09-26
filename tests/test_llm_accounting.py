"""LLM Token & Cost Accounting Contract 测试（Phase 3.10.9）。

覆盖（任务书 §二十二 Test 1-18）：

    1.  Full usage → 完整 Cost
    2.  Partial input usage → output/total 保持 None
    3.  Partial output usage → input/total 保持 None
    4.  usage=None → cost=None
    5.  Zero tokens → cost=0
    6.  Large tokens 无 overflow
    7.  Decimal 精度稳定
    8.  Negative pricing 拒绝
    9.  NaN pricing 拒绝
    10. Infinity pricing 拒绝
    11. bool 拒绝（True ≠ 1）
    12. string 隐式转换拒绝
    13. Negative token 拒绝（LLMUsage 构造层）
    14. Total mismatch 遵循既有 Contract（Cost 层不修复）
    15. Currency 显式（USD 保留，无默认币种，无自动转换）
    16. 纯函数（同输入恒同输出，输入不被修改）
    17. 0 网络
    18. 0 DB

synthetic pricing（input=1 / output=2，USD）——不含任何真实价格。
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
from decimal import Decimal

import pytest

from backend.app.llm.accounting import (
    LLMCost,
    LLMPricing,
    calculate_llm_cost,
    token_accounting_from_usage,
)
from backend.app.llm.client import LLMResponse, LLMUsage
from backend.app.llm.observability import observation_field_names


# ============================================================
# Helpers
# ============================================================

def _pricing(
    input_price: str = "1",
    output_price: str = "2",
    currency: str = "USD",
) -> LLMPricing:
    """synthetic pricing（测试专用，非真实价格）。"""
    return LLMPricing(
        input_price_per_1m_tokens=Decimal(input_price),
        output_price_per_1m_tokens=Decimal(output_price),
        currency=currency,
    )


def _imported_modules(module) -> set[str]:
    """解析模块实际 import 的顶层模块名（AST，不受 docstring 影响）。"""
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


# ============================================================
# Test 1 — Full usage
# ============================================================

def test_full_usage_calculates_complete_cost() -> None:
    """prompt=1_000_000 / completion=2_000_000，pricing input=1 / output=2
    → input_cost=1 / output_cost=4 / total_cost=5。"""
    usage = LLMUsage(prompt_tokens=1_000_000, completion_tokens=2_000_000,
                     total_tokens=3_000_000)
    cost = calculate_llm_cost(usage, _pricing())

    assert cost is not None
    assert cost.input_cost == Decimal("1")
    assert cost.output_cost == Decimal("4")
    assert cost.total_cost == Decimal("5")
    assert cost.currency == "USD"


def test_total_not_calculated_from_total_tokens_flat_price() -> None:
    """绝不用 total_tokens 乘统一价格（input/output 单价不同）。"""
    usage = LLMUsage(prompt_tokens=1_000_000, completion_tokens=2_000_000,
                     total_tokens=3_000_000)
    cost = calculate_llm_cost(usage, _pricing())
    # 若错误地 total(3M) × input_price(1) = 3 ≠ 正确的 5
    assert cost is not None and cost.total_cost == Decimal("5")


# ============================================================
# Test 2 / 3 — Partial usage（不补全、不当 0）
# ============================================================

def test_partial_input_usage_only_input_cost() -> None:
    """只有 prompt_tokens → input_cost 可算；output/total 保持 None。"""
    usage = LLMUsage(prompt_tokens=100)
    cost = calculate_llm_cost(usage, _pricing())

    assert cost is not None
    assert cost.input_cost == Decimal("0.0001")  # 100/1M × 1
    assert cost.output_cost is None
    assert cost.total_cost is None
    assert cost.currency == "USD"


def test_partial_output_usage_only_output_cost() -> None:
    """只有 completion_tokens → output_cost 可算；input/total 保持 None。"""
    usage = LLMUsage(completion_tokens=20)
    cost = calculate_llm_cost(usage, _pricing())

    assert cost is not None
    assert cost.input_cost is None
    assert cost.output_cost == Decimal("0.00004")  # 20/1M × 2
    assert cost.total_cost is None


def test_partial_usage_not_autocompleted() -> None:
    """Token Accounting 层不补全缺失字段（prompt=100 → total 仍 None）。"""
    accounting = token_accounting_from_usage(LLMUsage(prompt_tokens=100))
    assert accounting is not None
    assert accounting.prompt_tokens == 100
    assert accounting.completion_tokens is None
    assert accounting.total_tokens is None  # 不自动补 total_tokens=100


# ============================================================
# Test 4 — Missing usage
# ============================================================

def test_missing_usage_returns_none_cost() -> None:
    """usage=None → cost=None（不估算、不发第二次请求）。"""
    assert calculate_llm_cost(None, _pricing()) is None
    assert token_accounting_from_usage(None) is None


# ============================================================
# Test 5 — Zero tokens
# ============================================================

def test_zero_tokens_zero_cost() -> None:
    """prompt=0 / completion=0 → cost=0（0 是合法值，区别于 None）。"""
    usage = LLMUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    cost = calculate_llm_cost(usage, _pricing())

    assert cost is not None
    assert cost.input_cost == 0
    assert cost.output_cost == 0
    assert cost.total_cost == 0
    # 0 与 None 语义严格不同
    assert isinstance(cost.total_cost, Decimal)


# ============================================================
# Test 6 — Large tokens（无 overflow）
# ============================================================

def test_large_tokens_no_overflow() -> None:
    """10_000_000+ token 计算：Decimal 任意精度，无溢出。"""
    usage = LLMUsage(
        prompt_tokens=10_000_000,
        completion_tokens=25_000_000,
        total_tokens=35_000_000,
    )
    cost = calculate_llm_cost(usage, _pricing(input_price="1", output_price="2"))

    assert cost is not None
    assert cost.input_cost == Decimal("10")
    assert cost.output_cost == Decimal("50")
    assert cost.total_cost == Decimal("60")


# ============================================================
# Test 7 — Decimal precision
# ============================================================

def test_decimal_precision_stable() -> None:
    """同一输入多次计算结果完全相等（Decimal 确定性，无二进制浮点误差）。"""
    usage = LLMUsage(prompt_tokens=333_333, completion_tokens=777_777)
    pricing = _pricing(input_price="0.001", output_price="0.002")

    results = [calculate_llm_cost(usage, pricing) for _ in range(5)]
    assert all(r == results[0] for r in results)
    assert all(isinstance(r.input_cost, Decimal) for r in results)
    # 除数为 10^6 → 小数点位移精确，无精度损失
    # （333_333 × 0.001 / 1_000_000 = 0.000333333；777_777 × 0.002 / 1M = 0.001555554）
    assert results[0].input_cost == Decimal("0.000333333")
    assert results[0].output_cost == Decimal("0.001555554")
    assert results[0].total_cost == Decimal("0.001888887")


def test_decimal_no_binary_float_error() -> None:
    """0.1 类价格用 Decimal 表达无二进制浮点误差。"""
    usage = LLMUsage(prompt_tokens=1_000_000)
    cost = calculate_llm_cost(usage, _pricing(input_price="0.1"))
    assert cost is not None
    assert cost.input_cost == Decimal("0.1")


# ============================================================
# Test 8-12 — Pricing validation（严格 Contract）
# ============================================================

def test_negative_price_rejected() -> None:
    """Test 8：负价格拒绝。"""
    with pytest.raises(ValueError):
        LLMPricing(
            input_price_per_1m_tokens=Decimal("-0.1"),
            output_price_per_1m_tokens=Decimal("2"),
            currency="USD",
        )
    with pytest.raises(ValueError):
        LLMPricing(
            input_price_per_1m_tokens=Decimal("1"),
            output_price_per_1m_tokens=Decimal("-2"),
            currency="USD",
        )


def test_nan_price_rejected() -> None:
    """Test 9：NaN 价格拒绝。"""
    with pytest.raises(ValueError):
        LLMPricing(
            input_price_per_1m_tokens=Decimal("NaN"),
            output_price_per_1m_tokens=Decimal("2"),
            currency="USD",
        )


def test_infinity_price_rejected() -> None:
    """Test 10：Infinity 价格拒绝。"""
    with pytest.raises(ValueError):
        LLMPricing(
            input_price_per_1m_tokens=Decimal("Infinity"),
            output_price_per_1m_tokens=Decimal("2"),
            currency="USD",
        )


@pytest.mark.parametrize("bad_value", [True, False, "1.0", 1.0, 1, None])
def test_pricing_rejects_non_decimal_types(bad_value: object) -> None:
    """Test 11 / 12：bool（True ≠ 1）/ string / float / int / None 一律拒绝
    （金额语义必须显式 Decimal 构造，无隐式类型转换）。"""
    with pytest.raises(ValueError):
        LLMPricing(
            input_price_per_1m_tokens=bad_value,  # type: ignore[arg-type]
            output_price_per_1m_tokens=Decimal("2"),
            currency="USD",
        )
    with pytest.raises(ValueError):
        LLMPricing(
            input_price_per_1m_tokens=Decimal("1"),
            output_price_per_1m_tokens=bad_value,  # type: ignore[arg-type]
            currency="USD",
        )


def test_pricing_currency_must_be_explicit() -> None:
    """currency 必须显式指定：无默认值（缺失 → TypeError）、
    非 str / 空串拒绝（不默认 CNY，也不默认 USD）。"""
    with pytest.raises(TypeError):
        LLMPricing(  # type: ignore[call-arg]
            input_price_per_1m_tokens=Decimal("1"),
            output_price_per_1m_tokens=Decimal("2"),
        )
    with pytest.raises(ValueError):
        LLMPricing(
            input_price_per_1m_tokens=Decimal("1"),
            output_price_per_1m_tokens=Decimal("2"),
            currency=123,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        LLMPricing(
            input_price_per_1m_tokens=Decimal("1"),
            output_price_per_1m_tokens=Decimal("2"),
            currency="  ",
        )


# ============================================================
# Test 13 / 14 — Usage 边界（遵循既有 LLMUsage Contract）
# ============================================================

def test_negative_tokens_rejected_at_usage_layer() -> None:
    """Test 13：负数 token 在 LLMUsage 构造层被拒绝——无法构造出
    携带负数的 usage 进入 Accounting（不修改既有 Contract）。"""
    with pytest.raises(ValueError):
        LLMUsage(prompt_tokens=-1)
    with pytest.raises(ValueError):
        LLMUsage(completion_tokens=-1)


def test_total_mismatch_rejected_not_repaired() -> None:
    """Test 14：total 不一致（100+200≠999）在 LLMUsage 构造层拒绝；
    Accounting 层不做任何"修复"（既有 Contract 保持）。"""
    with pytest.raises(ValueError):
        LLMUsage(prompt_tokens=100, completion_tokens=200, total_tokens=999)
    # Accounting 对合法 usage 不做任何数值改写
    accounting = token_accounting_from_usage(
        LLMUsage(prompt_tokens=100, completion_tokens=200, total_tokens=300)
    )
    assert accounting == LLMUsage(100, 200, 300)


# ============================================================
# Test 15 — Currency
# ============================================================

def test_currency_explicit_and_preserved() -> None:
    """显式 USD 保留；无自动转换（模块无任何汇率 / 转换逻辑）。"""
    usage = LLMUsage(prompt_tokens=1_000_000, completion_tokens=2_000_000,
                     total_tokens=3_000_000)
    cost = calculate_llm_cost(usage, _pricing(currency="USD"))
    assert cost is not None and cost.currency == "USD"

    # 显式传入其它币种同样原样保留（不转换）
    cost_eur = calculate_llm_cost(usage, _pricing(currency="EUR"))
    assert cost_eur is not None and cost_eur.currency == "EUR"


def test_llm_cost_currency_required_and_validated() -> None:
    """LLMCost 直接构造：currency 必填且校验；金额字段校验对称。"""
    with pytest.raises(TypeError):
        LLMCost(  # type: ignore[call-arg]
            input_cost=Decimal("1"), output_cost=Decimal("2"),
            total_cost=Decimal("3"),
        )
    with pytest.raises(ValueError):
        LLMCost(
            input_cost=Decimal("-1"), output_cost=None,
            total_cost=None, currency="USD",
        )
    with pytest.raises(ValueError):
        LLMCost(
            input_cost=Decimal("1"), output_cost=Decimal("2"),
            total_cost=Decimal("999"), currency="USD",
        )


# ============================================================
# Test 16 — Pure function
# ============================================================

def test_calculate_is_pure_and_deterministic() -> None:
    """同 usage + 同 pricing → 恒同结果；输入对象不被修改。"""
    usage = LLMUsage(prompt_tokens=1_000, completion_tokens=2_000)
    pricing = _pricing()

    cost1 = calculate_llm_cost(usage, pricing)
    cost2 = calculate_llm_cost(usage, pricing)

    assert cost1 == cost2
    # 输入未被修改
    assert usage == LLMUsage(prompt_tokens=1_000, completion_tokens=2_000)
    assert pricing == _pricing()


# ============================================================
# Test 17 / 18 — No network / No DB（结构性）
# ============================================================

def test_accounting_no_network_imports() -> None:
    """Test 17：accounting 模块无任何 HTTP 客户端 import（0 网络）。"""
    import backend.app.llm.accounting as mod

    forbidden = {"httpx", "requests", "aiohttp", "urllib", "urllib3"}
    assert not any(
        name.split(".")[0] in forbidden for name in _imported_modules(mod)
    )


def test_accounting_no_db_imports() -> None:
    """Test 18：accounting 模块无任何 DB / 持久化 import（0 DB）。"""
    import backend.app.llm.accounting as mod

    forbidden = {"sqlalchemy", "psycopg", "sqlite3", "redis", "databases"}
    assert not any(
        name.split(".")[0] in forbidden for name in _imported_modules(mod)
    )


# ============================================================
# Contract isolation：LLMResponse / LLMObservation 不携带 cost
# ============================================================

def test_llm_response_and_observation_have_no_cost_fields() -> None:
    """LLMResponse / LLMObservation Contract 不变：
    无 cost / price / currency 字段（Observation ≠ Cost）。"""
    response_fields = {f.name for f in dataclasses.fields(LLMResponse)}
    for forbidden in ("cost", "price", "currency",
                      "input_cost", "output_cost", "total_cost"):
        assert forbidden not in response_fields
        assert forbidden not in observation_field_names()
