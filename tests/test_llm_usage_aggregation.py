"""LLM Usage Aggregation Contract 测试（Phase 3.10.19）。

纯内存 / 0 DB / 0 网络 / 0 SQL —— 全部直接构造
`LLMUsageRecordView`（§十六），不需要 RUN_DB_TESTS。

覆盖（§十五）：

    1.  Empty            （[] → 零值合法结果）
    2.  Single record    （requests=1，tokens=原值）
    3.  Multiple records （求和，§六示例）
    4.  NULL token       （None ≠ 0，未知信息不丢失）
    5.  Provider 分组
    6.  Model 分组
    7.  Provider+Model 组合分组
    8.  Input order independence（A B C vs C A B）
    9.  Duplicate records（不去重）
    10. Immutability     （frozen）

附加：total 独立求和（§十三）、稳定排序、非法输入、
无 DB/SQL/cost 依赖（静态检查）。
"""
from __future__ import annotations

import ast
import os
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from backend.app.services.llm_usage_aggregation_service import (
    LLM_USAGE_AGGREGATE_FIELDS,
    LLMUsageAggregate,
    LLMUsageAggregationInputError,
    LLMUsageAggregationService,
    ModelUsageAggregate,
    ProviderModelUsageAggregate,
    ProviderUsageAggregate,
)
from backend.app.services.llm_usage_query_service import LLMUsageRecordView

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_AGGREGATION_MODULE = "backend/app/services/llm_usage_aggregation_service.py"


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def _view(
    *,
    view_id: int = 1,
    request_id: str | None = "req-001",
    provider: str | None = "deepseek",
    model: str | None = "deepseek-chat",
    prompt_tokens: int | None = 10,
    completion_tokens: int | None = 20,
    total_tokens: int | None = 30,
    hours: int = 0,
) -> LLMUsageRecordView:
    return LLMUsageRecordView(
        id=view_id,
        request_id=request_id,
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        created_at=_utc(2024, 1, 1) + timedelta(hours=hours),
    )


def _views(*specs: dict[str, Any]) -> list[LLMUsageRecordView]:
    return [_view(view_id=i + 1, **spec) for i, spec in enumerate(specs)]


# ============================================================
# 1 / 2 / 3. 基础聚合
# ============================================================

class TestBasicAggregation:
    def test_empty_input_returns_valid_zero_aggregate(self) -> None:
        """§十二：[] → 合法结果（不 None、不抛异常）。"""
        aggregate = LLMUsageAggregationService().aggregate([])

        assert isinstance(aggregate, LLMUsageAggregate)
        assert aggregate.total_requests == 0
        assert aggregate.prompt_tokens == 0
        assert aggregate.completion_tokens == 0
        assert aggregate.total_tokens == 0
        # 空集上没有任何未知值
        assert aggregate.prompt_tokens_known is True
        assert aggregate.completion_tokens_known is True
        assert aggregate.total_tokens_known is True

    def test_single_record_keeps_original_values(self) -> None:
        """§十五.2：requests=1，tokens=原值。"""
        aggregate = LLMUsageAggregationService().aggregate(
            [_view(request_id="req-001", prompt_tokens=7,
                   completion_tokens=3, total_tokens=10)]
        )

        assert aggregate.total_requests == 1
        assert aggregate.prompt_tokens == 7
        assert aggregate.completion_tokens == 3
        assert aggregate.total_tokens == 10
        assert aggregate.prompt_tokens_known is True

    def test_multiple_records_sum(self) -> None:
        """§六：3 条 → 3 / 600 / 350 / 950。"""
        records = [
            _view(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            _view(prompt_tokens=200, completion_tokens=100, total_tokens=300),
            _view(prompt_tokens=300, completion_tokens=200, total_tokens=500),
        ]

        aggregate = LLMUsageAggregationService().aggregate(records)

        assert aggregate.total_requests == 3
        assert aggregate.prompt_tokens == 600
        assert aggregate.completion_tokens == 350
        assert aggregate.total_tokens == 950

    def test_accepts_generator_input(self) -> None:
        records = (_view(prompt_tokens=n) for n in (1, 2, 3))

        aggregate = LLMUsageAggregationService().aggregate(records)

        assert aggregate.total_requests == 3
        assert aggregate.prompt_tokens == 6

    def test_total_tokens_is_independent_observation(self) -> None:
        """§十三：total=999 → 求和 999，绝不重算为 prompt+completion。"""
        aggregate = LLMUsageAggregationService().aggregate(
            [_view(prompt_tokens=100, completion_tokens=50, total_tokens=999)]
        )

        assert aggregate.prompt_tokens == 100
        assert aggregate.completion_tokens == 50
        assert aggregate.total_tokens == 999
        assert aggregate.total_tokens != (
            aggregate.prompt_tokens + aggregate.completion_tokens
        )


# ============================================================
# 4. NULL 语义
# ============================================================

class TestNullTokenSemantics:
    def test_none_is_not_treated_as_zero(self) -> None:
        """§七：100 + NULL + 200 → 300，且 known=False（未知不丢失）。"""
        aggregate = LLMUsageAggregationService().aggregate(
            [
                _view(prompt_tokens=100),
                _view(prompt_tokens=None, completion_tokens=None,
                      total_tokens=None),
                _view(prompt_tokens=200),
            ]
        )

        assert aggregate.total_requests == 3
        assert aggregate.prompt_tokens == 300
        assert aggregate.prompt_tokens_known is False

    def test_all_columns_track_known_independently(self) -> None:
        aggregate = LLMUsageAggregationService().aggregate(
            [
                _view(prompt_tokens=10, completion_tokens=None,
                      total_tokens=10),
                _view(prompt_tokens=20, completion_tokens=5,
                      total_tokens=None),
            ]
        )

        assert aggregate.prompt_tokens == 30
        assert aggregate.prompt_tokens_known is True
        assert aggregate.completion_tokens == 5
        assert aggregate.completion_tokens_known is False
        assert aggregate.total_tokens == 10
        assert aggregate.total_tokens_known is False

    def test_explicit_zero_is_known_and_counted(self) -> None:
        """None（未知）≠ 0（显式零）。"""
        aggregate = LLMUsageAggregationService().aggregate(
            [_view(prompt_tokens=0, completion_tokens=0, total_tokens=0)]
        )

        assert aggregate.prompt_tokens == 0
        assert aggregate.prompt_tokens_known is True

    def test_all_null_columns_sum_to_known_part_only(self) -> None:
        aggregate = LLMUsageAggregationService().aggregate(
            [
                _view(prompt_tokens=None),
                _view(prompt_tokens=None),
            ]
        )

        assert aggregate.total_requests == 2
        assert aggregate.prompt_tokens == 0
        assert aggregate.prompt_tokens_known is False


# ============================================================
# 5 / 6 / 7. 分组
# ============================================================

class TestGrouping:
    def test_group_by_provider(self) -> None:
        """§八：provider A / provider B 正确分组。"""
        records = [
            _view(provider="deepseek", prompt_tokens=10),
            _view(provider="openai", prompt_tokens=20),
            _view(provider="deepseek", prompt_tokens=30),
        ]

        groups = LLMUsageAggregationService().aggregate_by_provider(records)

        assert [g.provider for g in groups] == ["deepseek", "openai"]
        by_provider = {g.provider: g.aggregate for g in groups}
        assert by_provider["deepseek"].total_requests == 2
        assert by_provider["deepseek"].prompt_tokens == 40
        assert by_provider["openai"].total_requests == 1
        assert by_provider["openai"].prompt_tokens == 20

    def test_group_by_provider_keeps_null_group(self) -> None:
        records = [
            _view(provider="deepseek", prompt_tokens=10),
            _view(provider=None, prompt_tokens=5),
        ]

        groups = LLMUsageAggregationService().aggregate_by_provider(records)

        assert [(g.provider, g.aggregate.total_requests) for g in groups] == [
            ("deepseek", 1),
            (None, 1),
        ]
        null_group = groups[-1]
        assert null_group.provider is None
        assert null_group.aggregate.prompt_tokens == 5

    def test_group_by_model(self) -> None:
        """§九：model A / model B 正确分组。"""
        records = [
            _view(model="deepseek-chat", prompt_tokens=10),
            _view(model="gpt-4o", prompt_tokens=20),
            _view(model="deepseek-chat", prompt_tokens=30),
        ]

        groups = LLMUsageAggregationService().aggregate_by_model(records)

        assert [g.model for g in groups] == ["deepseek-chat", "gpt-4o"]
        by_model = {g.model: g.aggregate for g in groups}
        assert by_model["deepseek-chat"].total_requests == 2
        assert by_model["deepseek-chat"].prompt_tokens == 40
        assert by_model["gpt-4o"].total_requests == 1

    def test_group_by_provider_model_combination(self) -> None:
        """§十：组合键 (provider, model)。"""
        records = [
            _view(provider="deepseek", model="deepseek-chat",
                  prompt_tokens=10),
            _view(provider="deepseek", model="deepseek-reasoner",
                  prompt_tokens=20),
            _view(provider="deepseek", model="deepseek-chat",
                  prompt_tokens=30),
            _view(provider="openai", model="gpt-4o", prompt_tokens=40),
        ]

        groups = LLMUsageAggregationService().aggregate_by_provider_model(
            records
        )

        keys = [(g.provider, g.model) for g in groups]
        assert keys == [
            ("deepseek", "deepseek-chat"),
            ("deepseek", "deepseek-reasoner"),
            ("openai", "gpt-4o"),
        ]
        by_key = {(g.provider, g.model): g.aggregate for g in groups}
        assert by_key[("deepseek", "deepseek-chat")].total_requests == 2
        assert by_key[("deepseek", "deepseek-chat")].prompt_tokens == 40
        assert by_key[("openai", "gpt-4o")].prompt_tokens == 40

    def test_group_output_is_tuple_not_dict(self) -> None:
        """§八：对外不暴露裸 dict，返回稳定 tuple。"""
        records = [_view(provider="a"), _view(provider="b")]

        groups = LLMUsageAggregationService().aggregate_by_provider(records)

        assert isinstance(groups, tuple)
        assert all(isinstance(g, ProviderUsageAggregate) for g in groups)

        models = LLMUsageAggregationService().aggregate_by_model(records)
        assert isinstance(models, tuple)
        assert all(isinstance(g, ModelUsageAggregate) for g in models)

        pairs = LLMUsageAggregationService().aggregate_by_provider_model(records)
        assert isinstance(pairs, tuple)
        assert all(isinstance(g, ProviderModelUsageAggregate) for g in pairs)

    def test_empty_grouping_returns_empty_tuple(self) -> None:
        service = LLMUsageAggregationService()
        assert service.aggregate_by_provider([]) == ()
        assert service.aggregate_by_model([]) == ()
        assert service.aggregate_by_provider_model([]) == ()


# ============================================================
# 8 / 9 / 10. 确定性 / 去重 / 不可变
# ============================================================

class TestDeterminismAndImmutability:
    def test_input_order_independence(self) -> None:
        """§十一：A B C 与 C A B → 相同聚合结果。"""
        a = _view(provider="deepseek", model="deepseek-chat",
                  prompt_tokens=100, completion_tokens=50, total_tokens=150)
        b = _view(provider="openai", model="gpt-4o",
                  prompt_tokens=200, completion_tokens=100, total_tokens=300)
        c = _view(provider=None, model=None,
                  prompt_tokens=300, completion_tokens=200, total_tokens=500)
        service = LLMUsageAggregationService()

        assert service.aggregate([a, b, c]) == service.aggregate([c, a, b])
        assert (
            service.aggregate_by_provider([a, b, c])
            == service.aggregate_by_provider([c, a, b])
        )
        assert (
            service.aggregate_by_model([b, c, a])
            == service.aggregate_by_model([c, b, a])
        )
        assert (
            service.aggregate_by_provider_model([a, b, c])
            == service.aggregate_by_provider_model([c, a, b])
        )

    def test_group_order_is_deterministic_across_runs(self) -> None:
        """分组排序不依赖 dict insertion order（多次运行一致）。"""
        records = [
            _view(provider="zeta"),
            _view(provider="alpha"),
            _view(provider="mid"),
        ]
        service = LLMUsageAggregationService()

        first = service.aggregate_by_provider(records)
        second = service.aggregate_by_provider(list(reversed(records)))

        assert [g.provider for g in first] == ["alpha", "mid", "zeta"]
        assert first == second

    def test_duplicate_records_are_counted_separately(self) -> None:
        """§十五.9：两条完全相同的记录 = 两次 Usage（不去重）。"""
        record = _view(request_id="req-dup", prompt_tokens=10,
                       completion_tokens=5, total_tokens=15)

        aggregate = LLMUsageAggregationService().aggregate([record, record])

        assert aggregate.total_requests == 2
        assert aggregate.prompt_tokens == 20
        assert aggregate.completion_tokens == 10
        assert aggregate.total_tokens == 30

    def test_aggregate_dto_is_frozen(self) -> None:
        """§十：Aggregate DTO 不可变。"""
        aggregate = LLMUsageAggregationService().aggregate([_view()])

        with pytest.raises(FrozenInstanceError):
            aggregate.total_requests = 5  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            aggregate.prompt_tokens = 1  # type: ignore[misc]

    def test_group_dtos_are_frozen(self) -> None:
        service = LLMUsageAggregationService()
        provider_group = service.aggregate_by_provider([_view()])[0]
        model_group = service.aggregate_by_model([_view()])[0]
        pair_group = service.aggregate_by_provider_model([_view()])[0]

        with pytest.raises(FrozenInstanceError):
            provider_group.provider = "x"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            model_group.model = "x"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            pair_group.aggregate = provider_group.aggregate  # type: ignore[misc]


# ============================================================
# Contract 边界（字段白名单 / 非法输入 / 纯内存）
# ============================================================

class TestAggregationContract:
    def test_aggregate_fields_match_contract(self) -> None:
        """§五 / §十四：字段 = usage 统计；无 cost / price / currency。"""
        assert set(LLM_USAGE_AGGREGATE_FIELDS) == {
            "total_requests",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "prompt_tokens_known",
            "completion_tokens_known",
            "total_tokens_known",
        }
        lowered = {name.lower() for name in LLM_USAGE_AGGREGATE_FIELDS}
        for fragment in ("cost", "price", "currency"):
            assert fragment not in lowered

    def test_rejects_non_view_records(self) -> None:
        with pytest.raises(LLMUsageAggregationInputError):
            LLMUsageAggregationService().aggregate(["not-a-view"])  # type: ignore[list-item]
        with pytest.raises(LLMUsageAggregationInputError):
            LLMUsageAggregationService().aggregate([None])  # type: ignore[list-item]

    def test_no_db_sql_or_cost_knowledge(self) -> None:
        """§四 / §十四：纯内存 —— 不 import sqlalchemy / db / repository /
        accounting；不知道 PostgreSQL 与 Cost。"""
        path = os.path.join(REPO_ROOT, *_AGGREGATION_MODULE.split("/"))
        with open(path, encoding="utf-8") as handle:
            source = handle.read()

        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {
            "sqlalchemy", "psycopg", "redis", "celery", "kafka",
            "backend.app.db", "backend.app.llm",
        }
        assert not any(name in forbidden for name in imported), imported

        # 代码标识符层面（不含 docstring）：无 DB / SQL / Cost 概念
        identifiers: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                identifiers.add(node.id.lower())
            elif isinstance(node, ast.Attribute):
                identifiers.add(node.attr.lower())
        for fragment in (
            "session", "execute", "engine", "select_", "currency",
            "price", "cost", "pricing",
        ):
            assert not any(fragment in ident for ident in identifiers), (
                f"聚合代码不应出现 {fragment}"
            )
