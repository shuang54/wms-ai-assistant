"""LLM Usage Analytics Read Facade 测试（Phase 3.10.21）。

只验证 **Application Composition**：

    Facade
        ↓ 调用一次
    Query Runtime（Fake，0 DB / 0 Network）
        ↓
    Analytics Service（真实 / Recording / Exploding Fake）
        ↓
    LLMUsageAnalyticsSnapshot

不复制 Phase 3.10.19 / 3.10.20 已验证的聚合算法与 Analytics Contract。

覆盖：

    1.  Facade 依赖校验（None / 缺少 query_async → TypeError）
    2.  注入关系暴露（query_runtime / analytics_service）
    3.  默认 Analytics Service
    4.  snapshot 组合链（total / by_provider / by_model /
        by_provider_model 全部正确且互相一致）
    5.  空记录 → 合法零值 Snapshot
    6.  Filter 原样透传（含 None → 默认 Filter 语义）
    7.  NULL 语义不被改变（None ≠ 0；0 → known=True）
    8.  query_snapshot 只调用 Runtime 一次（无 4 次 DB 查询）
    9.  各便捷方法各自只调用 Runtime 一次
    10. summary / by_provider / by_model / by_provider_model 结果
    11. Runtime 异常原样传播（不包装 / 不吞掉）
    12. Analytics 异常原样传播
    13. 与真实 LLMUsageQueryRuntimeBridge 组合（Fake 同步 Service）
    14. Immutability（frozen / tuple，不重新包装）
    15. 返回类型只允许既有 DTO
    16. 静态检查：无 DB / 基础设施 import；无第二套分页常量；
        无 Session / Engine / SQL 标识符

0 DB / 0 Network / 0 RUN_DB_TESTS。
"""
from __future__ import annotations

import ast
import asyncio
import os
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from backend.app.services.llm_usage_aggregation_service import (
    LLMUsageAggregate,
    ModelUsageAggregate,
    ProviderModelUsageAggregate,
    ProviderUsageAggregate,
)
from backend.app.services.llm_usage_analytics_facade import (
    LLMUsageAnalyticsReadFacade,
)
from backend.app.services.llm_usage_analytics_service import (
    LLMUsageAnalyticsService,
    LLMUsageAnalyticsSnapshot,
)
from backend.app.services.llm_usage_query_runtime import (
    LLMUsageQueryRuntimeBridge,
)
from backend.app.services.llm_usage_query_service import (
    LLMUsageQueryFilter,
    LLMUsageRecordView,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_FACADE_MODULE = "backend/app/services/llm_usage_analytics_facade.py"

# 复用 Phase 3.10.20 已有的 view 构造器与一致性断言（不复制测试框架）
from tests.test_llm_usage_analytics import (  # noqa: E402
    _assert_consistent,
    _view,
)


# ============================================================
# Fakes（0 DB / 0 Network）
# ============================================================

class FakeQueryRuntime:
    """async Query Runtime 替身：记录调用次数与收到的 Filter。"""

    def __init__(
        self,
        records: list[LLMUsageRecordView] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._records = records if records is not None else []
        self._error = error
        self.call_count = 0
        self.received_filters: list[LLMUsageQueryFilter | None] = []

    async def query_async(
        self,
        query_filter: LLMUsageQueryFilter | None = None,
    ) -> list[LLMUsageRecordView]:
        self.call_count += 1
        self.received_filters.append(query_filter)
        if self._error is not None:
            raise self._error
        return list(self._records)


class RecordingAnalyticsService:
    """记录 snapshot 输入；统计计算委托真实 Analytics。"""

    def __init__(self) -> None:
        self._real = LLMUsageAnalyticsService()
        self.snapshot_inputs: list[Any] = []

    @property
    def last_snapshot_input(self) -> Any:
        return self.snapshot_inputs[-1]

    def snapshot(self, records: Any) -> Any:
        self.snapshot_inputs.append(records)
        return self._real.snapshot(records)

    def summary(self, records: Any) -> Any:
        return self._real.summary(records)

    def by_provider(self, records: Any) -> Any:
        return self._real.by_provider(records)

    def by_model(self, records: Any) -> Any:
        return self._real.by_model(records)

    def by_provider_model(self, records: Any) -> Any:
        return self._real.by_provider_model(records)


class ExplodingAnalyticsService:
    """抛出注入异常的 Analytics 替身（验证异常不被重新包装）。"""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def snapshot(self, records: Any) -> Any:
        raise self.error

    def summary(self, records: Any) -> Any:
        raise self.error

    def by_provider(self, records: Any) -> Any:
        raise self.error

    def by_model(self, records: Any) -> Any:
        raise self.error

    def by_provider_model(self, records: Any) -> Any:
        raise self.error


class FakeSyncQueryService:
    """最小同步查询服务替身（满足 Runtime Bridge 的注入要求）。"""

    def __init__(self, records: list[LLMUsageRecordView]) -> None:
        self._records = records

    def query(self, query_filter: Any = None) -> list[LLMUsageRecordView]:
        return list(self._records)

    def list_records(self, **kwargs: Any) -> list[LLMUsageRecordView]:
        return list(self._records)

    def get_by_request_id(self, request_id: str) -> LLMUsageRecordView | None:
        return None


def _facade(
    runtime: Any,
    analytics: Any = None,
) -> LLMUsageAnalyticsReadFacade:
    return LLMUsageAnalyticsReadFacade(
        query_runtime=runtime,
        analytics_service=analytics,
    )


# ============================================================
# 1 ~ 3. Facade 依赖 Contract
# ============================================================

class TestFacadeContract:
    def test_rejects_invalid_query_runtime(self) -> None:
        with pytest.raises(TypeError):
            LLMUsageAnalyticsReadFacade(query_runtime=None)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            LLMUsageAnalyticsReadFacade(query_runtime=object())  # type: ignore[arg-type]

    def test_exposes_injected_dependencies(self) -> None:
        runtime = FakeQueryRuntime()
        analytics = RecordingAnalyticsService()
        facade = _facade(runtime, analytics)

        assert facade.query_runtime is runtime
        assert facade.analytics_service is analytics

    def test_default_analytics_service_used_when_omitted(self) -> None:
        facade = _facade(FakeQueryRuntime())

        assert isinstance(facade.analytics_service, LLMUsageAnalyticsService)


# ============================================================
# 4 ~ 7. Snapshot 组合链
# ============================================================

class TestQuerySnapshot:
    def test_snapshot_composes_runtime_and_analytics(self) -> None:
        records = [
            _view(view_id=1, provider="deepseek", model="deepseek-chat",
                  prompt_tokens=100, completion_tokens=50, total_tokens=150),
            _view(view_id=2, provider="deepseek", model="deepseek-reasoner",
                  prompt_tokens=200, completion_tokens=100, total_tokens=300),
            _view(view_id=3, provider="openai", model="gpt-4o",
                  prompt_tokens=300, completion_tokens=200, total_tokens=500),
        ]
        analytics = RecordingAnalyticsService()
        facade = _facade(FakeQueryRuntime(records), analytics)

        snapshot = asyncio.run(facade.query_snapshot())

        assert isinstance(snapshot, LLMUsageAnalyticsSnapshot)
        assert snapshot.total.total_requests == 3
        assert snapshot.total.prompt_tokens == 600
        assert snapshot.total.completion_tokens == 350
        assert snapshot.total.total_tokens == 950
        assert [g.provider for g in snapshot.by_provider] == [
            "deepseek", "openai",
        ]
        assert [g.model for g in snapshot.by_model] == [
            "deepseek-chat", "deepseek-reasoner", "gpt-4o",
        ]
        assert len(snapshot.by_provider_model) == 3
        _assert_consistent(snapshot)
        # Analytics 收到的正是 Runtime 返回的那份 records
        assert analytics.last_snapshot_input == records

    def test_empty_records_returns_valid_zero_snapshot(self) -> None:
        facade = _facade(FakeQueryRuntime([]))

        snapshot = asyncio.run(facade.query_snapshot())

        assert snapshot.total.total_requests == 0
        assert snapshot.total.prompt_tokens == 0
        assert snapshot.total.prompt_tokens_known is True
        assert snapshot.by_provider == ()
        assert snapshot.by_model == ()
        assert snapshot.by_provider_model == ()

    def test_filter_is_passed_through_unchanged(self) -> None:
        runtime = FakeQueryRuntime([_view()])
        facade = _facade(runtime)

        usage_filter = LLMUsageQueryFilter(
            provider="deepseek", limit=100, offset=20,
        )
        asyncio.run(facade.query_snapshot(usage_filter))
        assert runtime.received_filters == [usage_filter]

        # None → 原样传给 Runtime（默认 Filter 语义由 Runtime / Service 决定）
        asyncio.run(facade.query_snapshot())
        assert runtime.received_filters == [usage_filter, None]

    def test_null_token_semantics_preserved(self) -> None:
        """Facade 不改变 NULL 语义：None ≠ 0；显式 0 → known=True。"""
        facade = _facade(FakeQueryRuntime([
            _view(view_id=1, prompt_tokens=100),
            _view(view_id=2, prompt_tokens=None, completion_tokens=None,
                  total_tokens=None),
            _view(view_id=3, prompt_tokens=200),
        ]))

        snapshot = asyncio.run(facade.query_snapshot())

        assert snapshot.total.prompt_tokens == 300
        assert snapshot.total.prompt_tokens_known is False

        zero = asyncio.run(
            _facade(FakeQueryRuntime([
                _view(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            ])).query_snapshot()
        )
        assert zero.total.prompt_tokens == 0
        assert zero.total.prompt_tokens_known is True


# ============================================================
# 8 / 9. Runtime 只被调用一次
# ============================================================

class TestSingleQuery:
    def test_query_snapshot_calls_runtime_exactly_once(self) -> None:
        """snapshot = 1 次 DB 查询，不是 summary→DB + provider→DB ... ×4。"""
        runtime = FakeQueryRuntime([_view(), _view(view_id=2)])
        facade = _facade(runtime)

        asyncio.run(facade.query_snapshot())

        assert runtime.call_count == 1

    def test_each_convenience_method_calls_runtime_once(self) -> None:
        for method_name in (
            "query_summary",
            "query_by_provider",
            "query_by_model",
            "query_by_provider_model",
        ):
            runtime = FakeQueryRuntime([_view()])
            facade = _facade(runtime)

            asyncio.run(getattr(facade, method_name)())

            assert runtime.call_count == 1, method_name


# ============================================================
# 10. 便捷方法结果
# ============================================================

class TestConvenienceQueries:
    def test_query_summary_matches_total(self) -> None:
        records = [
            _view(view_id=1, prompt_tokens=10),
            _view(view_id=2, prompt_tokens=5),
        ]
        facade = _facade(FakeQueryRuntime(records))

        summary = asyncio.run(facade.query_summary())

        assert isinstance(summary, LLMUsageAggregate)
        assert summary.total_requests == 2
        assert summary.prompt_tokens == 15

    def test_query_by_provider_and_by_model(self) -> None:
        records = [
            _view(view_id=1, provider="deepseek", model="deepseek-chat",
                  prompt_tokens=10),
            _view(view_id=2, provider="openai", model="gpt-4o",
                  prompt_tokens=20),
        ]
        facade = _facade(FakeQueryRuntime(records))

        by_provider = asyncio.run(facade.query_by_provider())
        by_model = asyncio.run(facade.query_by_model())

        assert [g.provider for g in by_provider] == ["deepseek", "openai"]
        assert [g.model for g in by_model] == ["deepseek-chat", "gpt-4o"]
        assert all(isinstance(g, ProviderUsageAggregate) for g in by_provider)
        assert all(isinstance(g, ModelUsageAggregate) for g in by_model)

    def test_query_by_provider_model(self) -> None:
        records = [
            _view(view_id=1, provider="deepseek", model="deepseek-chat",
                  prompt_tokens=10),
            _view(view_id=2, provider="deepseek", model="deepseek-reasoner",
                  prompt_tokens=20),
        ]
        facade = _facade(FakeQueryRuntime(records))

        by_pair = asyncio.run(facade.query_by_provider_model())

        keys = [(g.provider, g.model) for g in by_pair]
        assert keys == [
            ("deepseek", "deepseek-chat"),
            ("deepseek", "deepseek-reasoner"),
        ]
        assert all(
            isinstance(g, ProviderModelUsageAggregate) for g in by_pair
        )


# ============================================================
# 11 / 12. 错误传播（§十七：不包装 / 不吞掉）
# ============================================================

class TestErrorPropagation:
    def test_runtime_error_propagates_as_is(self) -> None:
        class CustomQueryError(Exception):
            pass

        error = CustomQueryError("query boom")
        facade = _facade(FakeQueryRuntime(error=error))

        with pytest.raises(CustomQueryError) as excinfo:
            asyncio.run(facade.query_snapshot())

        assert excinfo.value is error

    def test_analytics_error_propagates_as_is(self) -> None:
        class CustomAnalyticsError(Exception):
            pass

        error = CustomAnalyticsError("analytics boom")
        facade = _facade(
            FakeQueryRuntime([_view()]),
            ExplodingAnalyticsService(error),
        )

        with pytest.raises(CustomAnalyticsError) as excinfo:
            asyncio.run(facade.query_snapshot())

        assert excinfo.value is error


# ============================================================
# 13. 与真实 Runtime Bridge 组合
# ============================================================

class TestCompositionWithRealBridge:
    def test_works_with_real_runtime_bridge(self) -> None:
        """Facade → 真实 LLMUsageQueryRuntimeBridge → Fake 同步 Service。"""
        records = [_view(view_id=1, provider="deepseek", prompt_tokens=7)]
        runtime = LLMUsageQueryRuntimeBridge(
            query_service=FakeSyncQueryService(records),
        )
        facade = LLMUsageAnalyticsReadFacade(query_runtime=runtime)

        snapshot = asyncio.run(facade.query_snapshot())

        assert snapshot.total.total_requests == 1
        assert snapshot.total.prompt_tokens == 7
        assert [g.provider for g in snapshot.by_provider] == ["deepseek"]


# ============================================================
# 14 / 15. Immutability / 返回类型
# ============================================================

class TestImmutability:
    def test_snapshot_is_frozen(self) -> None:
        facade = _facade(FakeQueryRuntime([_view()]))
        snapshot = asyncio.run(facade.query_snapshot())

        with pytest.raises(FrozenInstanceError):
            snapshot.total = snapshot.total  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            snapshot.by_provider = ()  # type: ignore[misc]

    def test_returns_existing_dto_types_only(self) -> None:
        """只返回既有 frozen DTO / tuple，不重新包装成可变结构。"""
        facade = _facade(FakeQueryRuntime([_view()]))

        snapshot = asyncio.run(facade.query_snapshot())
        summary = asyncio.run(facade.query_summary())
        by_provider = asyncio.run(facade.query_by_provider())
        by_model = asyncio.run(facade.query_by_model())
        by_pair = asyncio.run(facade.query_by_provider_model())

        assert isinstance(snapshot, LLMUsageAnalyticsSnapshot)
        assert isinstance(summary, LLMUsageAggregate)
        assert isinstance(by_provider, tuple)
        assert isinstance(by_model, tuple)
        assert isinstance(by_pair, tuple)
        assert all(
            isinstance(g, ProviderUsageAggregate) for g in by_provider
        )


# ============================================================
# 16. 静态依赖检查（AST，非全文扫描）
# ============================================================

class TestNoForbiddenDependency:
    def _facade_module_ast(self) -> ast.Module:
        path = os.path.join(REPO_ROOT, *_FACADE_MODULE.split("/"))
        with open(path, encoding="utf-8") as handle:
            return ast.parse(handle.read())

    def _imported_modules(self) -> set[str]:
        tree = self._facade_module_ast()
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        return imported

    def test_no_db_or_infra_imports(self) -> None:
        """Facade 不直接依赖 DB / 基础设施模块（Facade → Runtime 允许）。"""
        imported = self._imported_modules()
        forbidden = {
            "sqlalchemy", "psycopg", "redis", "celery", "kafka",
            "backend.app.db",
        }
        assert not any(name in forbidden for name in imported), imported

    def test_no_forbidden_identifiers_or_pagination_constants(self) -> None:
        """无 Session / Engine / SQL 标识符；无第二套 PAGE_SIZE 常量
        （AST Name/Attribute 检查，docstring 不算违规）。"""
        tree = self._facade_module_ast()

        identifiers: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                identifiers.add(node.id.lower())
            elif isinstance(node, ast.Attribute):
                identifiers.add(node.attr.lower())

        for fragment in (
            "session", "engine", "connection", "execute", "sql",
            "page_size",
        ):
            assert not any(
                fragment in ident for ident in identifiers
            ), fragment
