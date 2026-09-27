"""LLM Usage Analytics Contract 测试（Phase 3.10.20）。

重点：**Analytics → Aggregation 组合是否正确**（§二十一），
不复制 Phase 3.10.19 已验证的聚合算法细节。

覆盖：

    1.  empty input
    2.  single record
    3.  multiple records
    4.  generator input
    5.  input order independence
    6.  provider=None 独立分组
    7.  model=None 独立分组
    8.  NULL token 语义（None ≠ 0，`*_known` 保持）
    9.  duplicate records（不去重）
    10. summary / provider / model / provider+model consistency
    11. snapshot 一次消费 generator、四视图同源
    12. immutability
    13. invalid None / str / bytes / 非 iterable 输入
    14. aggregation exception 原样传播（same exception type）
    15. no database dependency（AST Name/Attribute 检查，非全文扫描）

纯内存：0 DB / 0 Network / 0 RUN_DB_TESTS。
"""
from __future__ import annotations

import ast
import os
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import pytest

from backend.app.services.llm_usage_aggregation_service import (
    LLMUsageAggregate,
    LLMUsageAggregationError,
    LLMUsageAggregationInputError,
    LLMUsageAggregationService,
)
from backend.app.services.llm_usage_analytics_service import (
    LLMUsageAnalyticsInputError,
    LLMUsageAnalyticsService,
    LLMUsageAnalyticsSnapshot,
)
from backend.app.services.llm_usage_query_service import LLMUsageRecordView

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_ANALYTICS_MODULE = "backend/app/services/llm_usage_analytics_service.py"


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
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc)
        + timedelta(hours=hours),
    )


def _service() -> LLMUsageAnalyticsService:
    return LLMUsageAnalyticsService()


def _assert_consistent(snapshot: LLMUsageAnalyticsSnapshot) -> None:
    """§十二 / §十三：四个视图的 requests / tokens 必须互相一致。"""
    total = snapshot.total

    provider_requests = sum(
        g.aggregate.total_requests for g in snapshot.by_provider
    )
    model_requests = sum(
        g.aggregate.total_requests for g in snapshot.by_model
    )
    pair_requests = sum(
        g.aggregate.total_requests for g in snapshot.by_provider_model
    )
    assert provider_requests == total.total_requests
    assert model_requests == total.total_requests
    assert pair_requests == total.total_requests

    for column in ("prompt_tokens", "completion_tokens", "total_tokens"):
        provider_sum = sum(
            getattr(g.aggregate, column) for g in snapshot.by_provider
        )
        model_sum = sum(
            getattr(g.aggregate, column) for g in snapshot.by_model
        )
        pair_sum = sum(
            getattr(g.aggregate, column) for g in snapshot.by_provider_model
        )
        assert provider_sum == getattr(total, column)
        assert model_sum == getattr(total, column)
        assert pair_sum == getattr(total, column)


class ExplodingAggregationService:
    """抛出 LLMUsageAggregationError 的替身（验证异常不被重新包装）。"""

    def __init__(self, error: LLMUsageAggregationError) -> None:
        self.error = error

    def aggregate(self, records: Any) -> Any:
        raise self.error

    def aggregate_by_provider(self, records: Any) -> Any:
        raise self.error

    def aggregate_by_model(self, records: Any) -> Any:
        raise self.error

    def aggregate_by_provider_model(self, records: Any) -> Any:
        raise self.error


# ============================================================
# 1 / 2 / 3. 基础结果
# ============================================================

class TestAnalyticsBasics:
    def test_empty_input_returns_valid_zero_analytics(self) -> None:
        snapshot = _service().snapshot([])

        assert isinstance(snapshot, LLMUsageAnalyticsSnapshot)
        assert snapshot.total.total_requests == 0
        assert snapshot.total.prompt_tokens == 0
        assert snapshot.total.prompt_tokens_known is True
        assert snapshot.by_provider == ()
        assert snapshot.by_model == ()
        assert snapshot.by_provider_model == ()
        _assert_consistent(snapshot)

    def test_single_record_all_views(self) -> None:
        records = [
            _view(request_id="req-001", provider="deepseek",
                  model="deepseek-chat", prompt_tokens=7,
                  completion_tokens=3, total_tokens=10)
        ]

        snapshot = _service().snapshot(records)

        assert snapshot.total.total_requests == 1
        assert snapshot.total.total_tokens == 10
        assert [g.provider for g in snapshot.by_provider] == ["deepseek"]
        assert snapshot.by_provider[0].aggregate.total_tokens == 10
        assert [g.model for g in snapshot.by_model] == ["deepseek-chat"]
        assert len(snapshot.by_provider_model) == 1
        pair = snapshot.by_provider_model[0]
        assert (pair.provider, pair.model) == ("deepseek", "deepseek-chat")
        _assert_consistent(snapshot)

    def test_multiple_records_consistency(self) -> None:
        records = [
            _view(view_id=1, provider="deepseek", model="deepseek-chat",
                  prompt_tokens=100, completion_tokens=50, total_tokens=150),
            _view(view_id=2, provider="deepseek", model="deepseek-reasoner",
                  prompt_tokens=200, completion_tokens=100, total_tokens=300),
            _view(view_id=3, provider="openai", model="gpt-4o",
                  prompt_tokens=300, completion_tokens=200, total_tokens=500),
        ]

        snapshot = _service().snapshot(records)

        assert snapshot.total.total_requests == 3
        assert snapshot.total.prompt_tokens == 600
        assert snapshot.total.completion_tokens == 350
        assert snapshot.total.total_tokens == 950
        assert len(snapshot.by_provider) == 2
        assert len(snapshot.by_model) == 3
        assert len(snapshot.by_provider_model) == 3
        _assert_consistent(snapshot)

    def test_total_tokens_is_independent_observation(self) -> None:
        """特别验证 4：total=999 → 求和 999，不重算 prompt+completion。"""
        snapshot = _service().snapshot(
            [_view(prompt_tokens=100, completion_tokens=50, total_tokens=999)]
        )

        assert snapshot.total.prompt_tokens == 100
        assert snapshot.total.completion_tokens == 50
        assert snapshot.total.total_tokens == 999
        assert snapshot.total.total_tokens != (
            snapshot.total.prompt_tokens + snapshot.total.completion_tokens
        )

    def test_summary_matches_aggregation_service_directly(self) -> None:
        records = [_view(prompt_tokens=5), _view(prompt_tokens=7)]
        expected = LLMUsageAggregationService().aggregate(records)

        assert _service().summary(records) == expected


# ============================================================
# 4. generator / snapshot 单次消费
# ============================================================

class TestGeneratorAndSnapshot:
    def test_generator_input_produces_all_views(self) -> None:
        def gen() -> Iterator[LLMUsageRecordView]:
            yield _view(view_id=1, provider="deepseek", model="deepseek-chat",
                        prompt_tokens=10)
            yield _view(view_id=2, provider="openai", model="gpt-4o",
                        prompt_tokens=20)

        snapshot = _service().snapshot(gen())

        assert snapshot.total.total_requests == 2
        assert len(snapshot.by_provider) == 2
        assert len(snapshot.by_model) == 2
        assert len(snapshot.by_provider_model) == 2
        _assert_consistent(snapshot)

    def test_snapshot_consumes_generator_once(self) -> None:
        """特别验证 1 / 范围 14：generator 只被消费一次，四视图同源。"""
        consumed: list[int] = []

        def gen() -> Iterator[LLMUsageRecordView]:
            for n in (1, 2, 3):
                consumed.append(n)
                yield _view(view_id=n, prompt_tokens=n)

        snapshot = _service().snapshot(gen())

        # 物化只发生一次：不是 4 个视图各自重新消费
        assert consumed == [1, 2, 3]
        assert snapshot.total.total_requests == 3
        assert len(snapshot.by_provider) >= 1
        assert len(snapshot.by_model) >= 1
        assert len(snapshot.by_provider_model) >= 1
        _assert_consistent(snapshot)

    def test_one_shot_iterator_not_prematurely_exhausted(self) -> None:
        """一次性 iterator：snapshot 后四视图都非空（不被中途耗尽）。"""
        records = iter(
            [_view(view_id=1, prompt_tokens=10),
             _view(view_id=2, prompt_tokens=20)]
        )

        snapshot = _service().snapshot(records)

        assert snapshot.total.total_requests == 2
        assert snapshot.by_provider != ()
        assert snapshot.by_model != ()
        assert snapshot.by_provider_model != ()

    def test_individual_methods_materialize_independently(self) -> None:
        """summary() 与 by_provider() 各自独立物化（不共享已耗尽 iterable）。"""
        records = [_view(provider="deepseek", prompt_tokens=10)]

        assert _service().summary(records).total_requests == 1
        assert len(_service().by_provider(records)) == 1


# ============================================================
# 5. 输入顺序无关
# ============================================================

class TestInputOrderIndependence:
    def test_snapshot_is_order_independent(self) -> None:
        a = _view(view_id=1, provider="deepseek", model="deepseek-chat",
                  prompt_tokens=100, completion_tokens=50, total_tokens=150)
        b = _view(view_id=2, provider="openai", model="gpt-4o",
                  prompt_tokens=200, completion_tokens=100, total_tokens=300)
        c = _view(view_id=3, provider=None, model=None,
                  prompt_tokens=300, completion_tokens=200, total_tokens=500)
        service = _service()

        assert service.snapshot([a, b, c]) == service.snapshot([c, a, b])
        assert service.summary([a, b, c]) == service.summary([c, a, b])
        assert service.by_provider([a, b, c]) == service.by_provider([c, a, b])
        assert service.by_model([a, b, c]) == service.by_model([c, a, b])


# ============================================================
# 6 / 7. None 维度独立分组
# ============================================================

class TestNoneDimensionGroups:
    def test_provider_none_is_its_own_group(self) -> None:
        snapshot = _service().snapshot(
            [_view(provider="deepseek", prompt_tokens=10),
             _view(provider=None, prompt_tokens=5)]
        )

        providers = [(g.provider, g.aggregate.total_requests)
                     for g in snapshot.by_provider]
        assert ("deepseek", 1) in providers
        assert (None, 1) in providers
        null_group = [g for g in snapshot.by_provider if g.provider is None][0]
        assert null_group.aggregate.prompt_tokens == 5
        _assert_consistent(snapshot)

    def test_model_none_is_its_own_group(self) -> None:
        snapshot = _service().snapshot(
            [_view(model="deepseek-chat", prompt_tokens=10),
             _view(model=None, prompt_tokens=5)]
        )

        models = [(g.model, g.aggregate.total_requests)
                  for g in snapshot.by_model]
        assert ("deepseek-chat", 1) in models
        assert (None, 1) in models
        _assert_consistent(snapshot)

    def test_provider_model_none_combination(self) -> None:
        snapshot = _service().snapshot(
            [_view(provider="deepseek", model=None, prompt_tokens=10),
             _view(provider=None, model="deepseek-chat", prompt_tokens=5)]
        )

        keys = {(g.provider, g.model) for g in snapshot.by_provider_model}
        assert ("deepseek", None) in keys
        assert (None, "deepseek-chat") in keys
        _assert_consistent(snapshot)


# ============================================================
# 8. NULL token 语义
# ============================================================

class TestNullTokenSemantics:
    def test_none_is_not_zero_and_marks_unknown(self) -> None:
        """特别验证 3：100 + None + 200 → 300 且 known=False。"""
        snapshot = _service().snapshot(
            [_view(prompt_tokens=100),
             _view(prompt_tokens=None, completion_tokens=None,
                   total_tokens=None),
             _view(prompt_tokens=200)]
        )

        assert snapshot.total.total_requests == 3
        assert snapshot.total.prompt_tokens == 300
        assert snapshot.total.prompt_tokens_known is False
        # None != 0：总和不是 100 + 0 + 200 的"隐含 0"
        assert snapshot.total.prompt_tokens != 200

    def test_explicit_zero_is_known(self) -> None:
        snapshot = _service().snapshot(
            [_view(prompt_tokens=0, completion_tokens=0, total_tokens=0)]
        )

        assert snapshot.total.prompt_tokens == 0
        assert snapshot.total.prompt_tokens_known is True

    def test_known_flag_reflected_in_group_views(self) -> None:
        """分组视图的 known 标志由该分组自己的记录决定。"""
        snapshot = _service().snapshot(
            [_view(provider="deepseek", prompt_tokens=10),
             _view(provider="deepseek", prompt_tokens=None,
                   completion_tokens=None, total_tokens=None),
             _view(provider="openai", prompt_tokens=20)]
        )

        by_provider = {g.provider: g.aggregate for g in snapshot.by_provider}
        assert by_provider["deepseek"].prompt_tokens_known is False
        assert by_provider["openai"].prompt_tokens_known is True
        _assert_consistent(snapshot)


# ============================================================
# 9. 重复记录不去重
# ============================================================

class TestDuplicateRecords:
    def test_duplicate_records_are_counted_separately(self) -> None:
        record = _view(request_id="req-dup", prompt_tokens=10,
                       completion_tokens=5, total_tokens=15)

        snapshot = _service().snapshot([record, record])

        assert snapshot.total.total_requests == 2
        assert snapshot.total.prompt_tokens == 20
        assert snapshot.total.completion_tokens == 10
        assert snapshot.total.total_tokens == 30
        _assert_consistent(snapshot)


# ============================================================
# 10 ~ 13. Consistency Contract（覆盖范围 10~13）
# ============================================================

class TestConsistencyContract:
    def test_requests_consistent_across_views(self) -> None:
        """范围 10~13：Σ provider / model / provider-model requests == summary。"""
        records = [
            _view(view_id=1, provider="deepseek", model="deepseek-chat"),
            _view(view_id=2, provider="deepseek", model="deepseek-reasoner"),
            _view(view_id=3, provider="openai", model="gpt-4o"),
            _view(view_id=4, provider=None, model=None),
        ]

        snapshot = _service().snapshot(records)

        assert sum(g.aggregate.total_requests for g in snapshot.by_provider) \
            == snapshot.total.total_requests
        assert sum(g.aggregate.total_requests for g in snapshot.by_model) \
            == snapshot.total.total_requests
        assert sum(g.aggregate.total_requests for g in snapshot.by_provider_model) \
            == snapshot.total.total_requests
        _assert_consistent(snapshot)

    def test_token_sums_consistent_across_views(self) -> None:
        records = [
            _view(view_id=1, provider="deepseek", prompt_tokens=100,
                  completion_tokens=10, total_tokens=110),
            _view(view_id=2, provider="openai", prompt_tokens=200,
                  completion_tokens=20, total_tokens=220),
        ]

        snapshot = _service().snapshot(records)

        assert sum(g.aggregate.prompt_tokens for g in snapshot.by_provider) \
            == snapshot.total.prompt_tokens == 300
        assert sum(g.aggregate.total_tokens for g in snapshot.by_provider_model) \
            == snapshot.total.total_tokens == 330
        _assert_consistent(snapshot)


# ============================================================
# 12 / 16. Immutability
# ============================================================

class TestImmutability:
    def test_snapshot_is_frozen(self) -> None:
        snapshot = _service().snapshot([_view()])
        with pytest.raises(FrozenInstanceError):
            snapshot.total = snapshot.total  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            snapshot.by_provider = ()  # type: ignore[misc]

    def test_nested_dtos_are_frozen(self) -> None:
        snapshot = _service().snapshot(
            [_view(provider="deepseek", model="deepseek-chat")]
        )
        aggregate = snapshot.total
        provider_group = snapshot.by_provider[0]
        model_group = snapshot.by_model[0]
        pair_group = snapshot.by_provider_model[0]

        assert isinstance(aggregate, LLMUsageAggregate)
        with pytest.raises(FrozenInstanceError):
            aggregate.total_requests = 5  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            provider_group.provider = "x"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            model_group.model = "x"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            pair_group.aggregate = aggregate  # type: ignore[misc]


# ============================================================
# 13 / 17 ~ 20. 错误 Contract
# ============================================================

class TestErrorContract:
    def test_none_input_rejected(self) -> None:
        with pytest.raises(LLMUsageAnalyticsInputError):
            _service().snapshot(None)  # type: ignore[arg-type]
        with pytest.raises(LLMUsageAnalyticsInputError):
            _service().summary(None)  # type: ignore[arg-type]

    def test_string_input_rejected(self) -> None:
        with pytest.raises(LLMUsageAnalyticsInputError):
            _service().snapshot("not-a-view")  # type: ignore[arg-type]

    def test_bytes_input_rejected(self) -> None:
        with pytest.raises(LLMUsageAnalyticsInputError):
            _service().snapshot(b"bytes")  # type: ignore[arg-type]

    def test_non_iterable_input_rejected(self) -> None:
        with pytest.raises(LLMUsageAnalyticsInputError):
            _service().snapshot(123)  # type: ignore[arg-type]

    def test_aggregation_input_error_propagates_as_is(self) -> None:
        """范围 20：底层异常不被重新包装成 Analytics 异常。"""
        # 真实 Aggregation 遇到非 View 元素 → LLMUsageAggregationInputError
        with pytest.raises(LLMUsageAggregationInputError):
            _service().summary(["not-a-view"])  # type: ignore[list-item]

    def test_aggregation_error_keeps_original_type(self) -> None:
        """特别验证 8：Analytics 不把底层异常包装成无意义新异常。"""

        class CustomAggregationError(LLMUsageAggregationError):
            pass

        error = CustomAggregationError("boom")
        analytics = LLMUsageAnalyticsService(
            aggregation_service=ExplodingAggregationService(error)
        )

        with pytest.raises(CustomAggregationError):
            analytics.summary([_view()])


# ============================================================
# 15 / 21 / 22. 静态依赖检查（AST，非全文扫描）
# ============================================================

class TestNoDatabaseDependency:
    def _imported_modules(self) -> set[str]:
        path = os.path.join(REPO_ROOT, *_ANALYTICS_MODULE.split("/"))
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        return imported

    def test_no_db_or_infra_imports(self) -> None:
        """范围 21 / 22：无 sqlalchemy / psycopg / redis / celery / kafka / db。"""
        imported = self._imported_modules()
        forbidden = {
            "sqlalchemy", "psycopg", "redis", "celery", "kafka",
            "backend.app.db",
        }
        assert not any(name in forbidden for name in imported), imported

    def test_no_db_sql_cost_identifiers_in_code(self) -> None:
        """AST Name/Attribute 检查（docstring 不算违规）。"""
        path = os.path.join(REPO_ROOT, *_ANALYTICS_MODULE.split("/"))
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())

        identifiers: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                identifiers.add(node.id.lower())
            elif isinstance(node, ast.Attribute):
                identifiers.add(node.attr.lower())

        for fragment in (
            "session", "execute", "engine", "currency", "price",
            "cost", "pricing", "sql",
        ):
            assert not any(fragment in ident for ident in identifiers), fragment


# ============================================================
# 组合关系（Analytics 复用 Aggregation）
# ============================================================

class TestComposition:
    def test_uses_injected_aggregation_service(self) -> None:
        fake = ExplodingAggregationService(LLMUsageAggregationError("x"))
        analytics = LLMUsageAnalyticsService(aggregation_service=fake)

        assert analytics.aggregation_service is fake

    def test_default_aggregation_service_is_used(self) -> None:
        analytics = _service()
        assert isinstance(analytics.aggregation_service, LLMUsageAggregationService)


