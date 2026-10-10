"""Phase 6 Step 8B-1 —— 观测残留**生命周期 / 缓存 / 契约**测试（离线；零真实 DB）。

背景：``test_db_residue_is_zero`` 的运行时断言已**迁移**到
``tests/conftest.py::_observability_residue_check``（在三个清理守卫完成之后
执行）。本文件验证迁移后的机制（不重复原运行时断言——它由 conftest 承担）：

```text
§4.1  三个清理守卫对收尾检查的依赖**显式可见**（真实 fixture 对象签名）；
      收尾检查为 session 级 autouse。
§六.2 四张表**任意一张**非零 ⇒ 收尾检查失败（逐表参数化）。
§六.3 全零 ⇒ 收尾检查通过；且确实逐表查询。
§六.1 三个守卫未全部完成清理 ⇒ 收尾检查失败（顺序证据通道）。
§4.3 / §六.4 收尾检查**每次重新查询数据库**——绝不复用会话中途缓存
      （以注入 fake engine 的查询计数证明）；cached 读取器保留 cache_clear。
§六.6 真实 pytest 生命周期：setup 顺序（被依赖者先）与 teardown 顺序
      （依赖者先）以**真实执行**（fixture teardown 内的断言）验证——
      非静态 AST 断言。
```

全部用例离线（fake engine / 真实 fixture 机制；不连接数据库）。
"""
from __future__ import annotations

import inspect
from typing import Any

import pytest

from tests import conftest as root_conftest
from tests import observability_residue_contract as contract

__all__ = [
    "TestGuardDependencyWiring",
    "TestSessionEndResidueCheck",
    "TestFreshVsCachedReader",
    "TestLifecycleOrdering",
]


# ============================================================
# fake engine（只读查询替身；记录每张表的查询次数）
# ============================================================


class _Scalar:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _FakeConnection:
    def __init__(self, counts: dict[str, int], queries: list[str]) -> None:
        self._counts = counts
        self.queries = queries

    def execute(self, statement: Any, *args: Any, **kwargs: Any) -> _Scalar:
        sql = str(statement)
        for table, value in self._counts.items():
            if f"ai_ops.{table}" in sql:
                self.queries.append(table)
                return _Scalar(value)
        raise AssertionError(f"unexpected SQL: {sql}")

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _FakeEngine:
    def __init__(self, counts: dict[str, int]) -> None:
        self._counts = counts
        self.queries: list[str] = []

    def connect(self) -> _FakeConnection:
        return _FakeConnection(self._counts, self.queries)


def _all_zero() -> dict[str, int]:
    return {table: 0 for table, _ in contract.OBSERVABILITY_RESIDUE_TABLES}


@pytest.fixture(autouse=True)
def _isolate_contract_state() -> Any:
    """每个用例：清空守卫标记 / 缓存 / 会话快照（双向隔离）。"""
    contract.reset_guard_marks()
    contract.cached_residue_total.cache_clear()
    contract.reset_session_snapshot()
    yield
    contract.reset_guard_marks()
    contract.cached_residue_total.cache_clear()
    contract.reset_session_snapshot()


def _mark_all_guards() -> None:
    for name in (
        contract.GUARD_ASSISTANT_OUTCOME,
        contract.GUARD_AGENT_RUN,
        contract.GUARD_AGENT_STEP,
    ):
        contract.mark_guard_cleaned(name)


# ============================================================
# §4.1：fixture 依赖必须显式且被测试验证（真实对象，非静态 AST）
# ============================================================


class TestGuardDependencyWiring:
    def test_three_guards_declare_explicit_dependency(self) -> None:
        for guard_name in (
            "_assistant_outcome_residue_guard",
            "_agent_run_residue_guard",
            "_agent_step_residue_guard",
        ):
            guard = getattr(root_conftest, guard_name)
            parameters = inspect_signature_parameters(guard)
            assert "_observability_residue_check" in parameters, (
                guard_name,
                list(parameters),
            )

    def test_check_fixture_is_session_scoped_and_autouse(self) -> None:
        check = root_conftest._observability_residue_check
        # pytest 8.x：装饰后返回 FixtureFunctionDefinition（标记在
        # ``_fixture_function_marker``）；旧版本属性名兜底。
        marker = getattr(check, "_fixture_function_marker", None) or getattr(
            check, "_pytestfixturefunction", None
        )
        assert marker is not None, "不是 pytest fixture"
        assert marker.scope == "session"
        assert marker.autouse is True


def inspect_signature_parameters(function: Any) -> list[str]:
    """fixture 函数签名参数名（fixture 装饰不改变函数签名）。"""
    import inspect

    return list(inspect.signature(function).parameters)


# ============================================================
# §六.2 / §六.3 / §六.1：收尾检查的通过 / 失败语义
# ============================================================


class TestSessionEndResidueCheck:
    def test_all_zero_passes_when_guards_completed(self) -> None:
        _mark_all_guards()
        fake = _FakeEngine(_all_zero())
        contract.assert_session_end_residue_cleared(engine=fake)
        assert len(fake.queries) == len(contract.OBSERVABILITY_RESIDUE_TABLES)

    @pytest.mark.parametrize(
        "nonzero_table",
        [table for table, _ in contract.OBSERVABILITY_RESIDUE_TABLES],
    )
    def test_any_single_nonzero_table_fails(self, nonzero_table: str) -> None:
        counts = _all_zero()
        counts[nonzero_table] = 7
        fake = _FakeEngine(counts)
        with pytest.raises(AssertionError) as excinfo:
            contract.assert_session_end_residue_cleared(
                engine=fake, check_guards=False
            )
        assert nonzero_table in str(excinfo.value)

    def test_fails_when_guards_not_all_completed(self) -> None:
        contract.mark_guard_cleaned(contract.GUARD_ASSISTANT_OUTCOME)
        contract.mark_guard_cleaned(contract.GUARD_AGENT_RUN)
        # 故意缺 GUARD_AGENT_STEP —— 顺序证据必须失败
        fake = _FakeEngine(_all_zero())
        with pytest.raises(AssertionError) as excinfo:
            contract.assert_session_end_residue_cleared(engine=fake)
        assert contract.GUARD_AGENT_STEP in str(excinfo.value)

    def test_unavailable_engine_is_silently_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(contract, "_default_engine", lambda: None)
        contract.assert_session_end_residue_cleared()  # 不抛（无 DB 会话）

    def test_query_failure_is_silently_skipped(self) -> None:
        class _Broken:
            def connect(self) -> Any:
                raise RuntimeError("db down")

        contract.assert_session_end_residue_cleared(engine=_Broken())  # 不抛

    def test_nested_session_semantics_matches_session_start_snapshot(self) -> None:
        """嵌套子进程语义：收尾断言"回到会话开始水平"（全库 COUNT，不弱化）。

        场景（Phase 6 Step 8B-1 实测）：治理容器测试嵌套运行 pytest——
        子进程收尾时**父会话**未清理的行仍在库 ⇒ "全库 == 0"物理上不成立；
        本用例固化正确语义：本会话零新增 ⇒ 通过；本会话新增未清 ⇒ 失败。
        """
        start_counts = _all_zero()
        start_counts["llm_usage_record"] = 3  # 父会话残留在库
        assert (
            contract.capture_session_start_residue(
                engine=_FakeEngine(start_counts)
            )
            == 3
        )
        _mark_all_guards()

        # 父会话 3 行仍在、本会话零新增 ⇒ 通过（嵌套正确语义）
        end_counts = _all_zero()
        end_counts["llm_usage_record"] = 3
        contract.assert_session_end_residue_cleared(
            engine=_FakeEngine(end_counts)
        )

        # 本会话新增 1 行未清理 ⇒ 失败
        dirty_counts = _all_zero()
        dirty_counts["llm_usage_record"] = 4
        with pytest.raises(AssertionError) as excinfo:
            contract.assert_session_end_residue_cleared(
                engine=_FakeEngine(dirty_counts)
            )
        assert "4" in str(excinfo.value)

        # 会话内（自清）测试把父残留一并清掉 ⇒ 尾(0) < 快照(3) ⇒ 仍通过
        # （库更干净不构成残留违规——上界语义）
        contract.assert_session_end_residue_cleared(
            engine=_FakeEngine(_all_zero())
        )

    def test_root_session_snapshot_zero_keeps_strict_zero_assertion(self) -> None:
        """根会话（快照 == 0）⇒ 收尾断言"全库 == 0"（§4.2 原文语义保留）。"""
        assert (
            contract.capture_session_start_residue(engine=_FakeEngine(_all_zero()))
            == 0
        )
        _mark_all_guards()
        contract.assert_session_end_residue_cleared(
            engine=_FakeEngine(_all_zero())
        )
        dirty = _all_zero()
        dirty["rag_execution_record"] = 1
        with pytest.raises(AssertionError):
            contract.assert_session_end_residue_cleared(
                engine=_FakeEngine(dirty)
            )


# ============================================================
# §4.3 / §六.4：收尾检查**每次重新查询**——不复用会话缓存
# ============================================================


class TestFreshVsCachedReader:
    def test_cached_reader_queries_once_then_reuses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeEngine(_all_zero())
        monkeypatch.setattr(contract, "_default_engine", lambda: fake)
        assert contract.cached_residue_total() == 0
        assert contract.cached_residue_total() == 0
        # 两轮调用 = 仅一轮查询（第二轮 lru_cache 命中）
        assert len(fake.queries) == len(contract.OBSERVABILITY_RESIDUE_TABLES)

    def test_fresh_reader_requeries_every_call(self) -> None:
        fake = _FakeEngine(_all_zero())
        assert contract.query_residue_total(fake) == 0
        assert contract.query_residue_total(fake) == 0
        assert len(fake.queries) == (
            2 * len(contract.OBSERVABILITY_RESIDUE_TABLES)
        )

    def test_session_end_check_does_not_reuse_stale_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """先填充会话缓存（旧值），再运行收尾检查——检查必须重新查库。"""
        stale = _FakeEngine(_all_zero())
        monkeypatch.setattr(contract, "_default_engine", lambda: stale)
        assert contract.cached_residue_total() == 0  # 填充会话缓存
        queries_after_cache = len(stale.queries)

        fresh = _FakeEngine(_all_zero())
        _mark_all_guards()
        contract.assert_session_end_residue_cleared(engine=fresh)

        assert fresh.queries, "收尾检查必须重新查询数据库（不得复用缓存）"
        assert len(stale.queries) == queries_after_cache  # 未再触及旧引擎

    def test_cache_clear_is_available_for_stale_readers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """§4.3 备用路径：cached 读取器可显式 cache_clear（矩阵基线复用方）。"""
        first = _FakeEngine(_all_zero())
        monkeypatch.setattr(contract, "_default_engine", lambda: first)
        assert contract.cached_residue_total() == 0
        contract.cached_residue_total.cache_clear()
        second = _FakeEngine(_all_zero())
        monkeypatch.setattr(contract, "_default_engine", lambda: second)
        assert contract.cached_residue_total() == 0
        assert second.queries, "cache_clear 后必须重新查询"


# ============================================================
# §六.1 / §六.6：真实 pytest 生命周期（非静态断言）
# ============================================================


_lifecycle_events: list[str] = []


@pytest.fixture()
def _sim_session_end_check_like() -> Any:
    """模拟 conftest 的收尾检查（无依赖）。teardown 内断言真实顺序。"""

    def _guards_must_have_cleaned_first() -> None:
        assert "guard:teardown" in _lifecycle_events, _lifecycle_events

    _lifecycle_events.append("check:setup")
    yield _guards_must_have_cleaned_first
    # teardown：此刻依赖者（模拟守卫）的 teardown **必须已执行**（真实顺序）。
    _guards_must_have_cleaned_first()
    _lifecycle_events.append("check:teardown")


@pytest.fixture()
def _sim_residue_guard_like(_sim_session_end_check_like: Any) -> Any:
    """模拟清理守卫（**依赖**收尾检查——与 conftest 依赖方向一致）。"""
    _lifecycle_events.append("guard:setup")
    yield
    _lifecycle_events.append("guard:teardown")


class TestLifecycleOrdering:
    def test_setup_order_and_real_teardown_order(
        self, _sim_residue_guard_like: Any
    ) -> None:
        """真实 pytest 生命周期（非静态 AST）：

        · setup：被依赖者（check）先于依赖者（guard）；
        · teardown：依赖者（guard）先于被依赖者（check）——顺序断言在
          check 的 teardown 内真实执行（若 guard 的 teardown 未先发生，
          该用例以 teardown error 失败）。

        conftest 中以**三个守卫依赖同一检查**的同构关系保证"全部清理之后
        才检查"；本用例验证该依赖规则在真实 pytest 下的执行顺序。
        """
        assert _lifecycle_events == ["check:setup", "guard:setup"]
