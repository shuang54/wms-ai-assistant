"""Phase 5 P-4 Integration — Agent Run Scope（**离线**；零 DB / 零真实 LLM）。

覆盖（任务书 §五–§十 / §二十五 / §二十六 / §三十三）：

```text
1  outside scope   → current_agent_run_id() is None
2  inside scope    → run_id
3  exit            → None（token/ finally 恢复）
4  nested          → 内层覆盖、退出恢复外层
5  concurrent      → asyncio task 隔离（task A ≠ run-B）
6  thread          → 线程隔离（不泄漏）
7  scope 不生成     → 空 / 非 str ⇒ ValueError；run_id 由 Runtime 签发
8  runtime binding → AgentRuntime.run() 内可见同一 run_id；退出后 None
9  security        → Scope 只保存 run_id 一个字符串（不含 prompt / context / 结果）
```
"""
from __future__ import annotations

import ast
import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from backend.app.agent import (
    AGENT_STATUS_COMPLETED,
    DECISION_FINAL,
    DECISION_TOOL_CALL,
    AgentDecision,
    AgentRuntime,
    AgentRuntimePolicy,
)
from backend.app.agent.ids import (
    _AGENT_RUN_ID,
    agent_run_scope,
    current_agent_run_id,
    new_agent_run_id,
)
from tests.test_agent_runtime import FakeLLM, RecordingHandler, _execution_service

__all__ = [
    "TestAgentRunScopeContract",
    "TestAgentRunScopeIsolation",
    "TestAgentRunScopeRuntimeBinding",
]

_REPO_ROOT = Path(__file__).resolve().parents[1]
_AGENT_DIR = _REPO_ROOT / "backend" / "app" / "agent"

_TOOL_NAME = "get_inventory"
_TOOL_CALL = AgentDecision(
    type=DECISION_TOOL_CALL,
    tool_name=_TOOL_NAME,
    arguments={"material_code": "MAT001"},
    tool_call_id="call_scope_1",
)
_FINAL = AgentDecision(type=DECISION_FINAL, content="库存 120 件。")


class RecordingSink:
    """记录写入点收到的身份/参数 + 写入时刻的 Agent Run Scope 值。"""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._error = error

    async def arecord_tool_result(self, **kwargs: Any) -> int | None:
        self.calls.append({**kwargs, "scope_run_id": current_agent_run_id()})
        if self._error is not None:
            raise self._error
        return 1


class ScopeCapturingHandler:
    """test-local Tool Handler：记录执行期间读到的 Agent Run Scope 值。"""

    def __init__(self) -> None:
        self.scope_values: list[str | None] = []
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, arguments: dict[str, Any]) -> Any:
        self.scope_values.append(current_agent_run_id())
        self.calls.append(dict(arguments))
        return {"material_code": "MAT001", "quantity": 120}


def _runtime(llm: FakeLLM, *, sink: Any = None) -> AgentRuntime:
    service, _registry, _handler = _execution_service()
    return AgentRuntime(
        llm=llm,
        tool_execution=service,
        policy=AgentRuntimePolicy(
            max_execution_time_ms=30_000, max_cost_tokens=1_000
        ),
        allowed_tools=(_TOOL_NAME,),
        tool_result_sink=sink,
    )


class TestAgentRunScopeContract:
    def test_outside_scope_is_none(self) -> None:
        assert current_agent_run_id() is None

    def test_inside_scope_returns_run_id_and_resets_on_exit(self) -> None:
        with agent_run_scope("run-001"):
            assert current_agent_run_id() == "run-001"
        assert current_agent_run_id() is None

    def test_nested_scope_restores_outer(self) -> None:
        with agent_run_scope("run-A"):
            assert current_agent_run_id() == "run-A"
            with agent_run_scope("run-B"):
                assert current_agent_run_id() == "run-B"
            assert current_agent_run_id() == "run-A"
        assert current_agent_run_id() is None

    def test_scope_resets_even_on_exception(self) -> None:
        with pytest.raises(RuntimeError):
            with agent_run_scope("run-err"):
                raise RuntimeError("boom")

        assert current_agent_run_id() is None

    def test_scope_accepts_only_existing_run_id(self) -> None:
        """§七：scope **不生成** run_id（空 / 非 str ⇒ ValueError）。"""
        for value in ("", "   ", None, 123):
            with pytest.raises(ValueError):
                with agent_run_scope(value):  # type: ignore[arg-type]
                    pass

        assert current_agent_run_id() is None

    def test_scope_stores_run_id_only(self) -> None:
        """§三十三：ContextVar 只保存 run_id 一个字符串。"""
        with agent_run_scope("run-only-id"):
            assert _AGENT_RUN_ID.get() == "run-only-id"
            assert isinstance(_AGENT_RUN_ID.get(), str)

    def test_new_agent_run_id_is_issued_outside_scope_semantics(self) -> None:
        """run_id 仍由 Runtime 层签发（scope 只绑定）。"""
        run_id = new_agent_run_id()

        assert run_id != new_agent_run_id()
        assert current_agent_run_id() is None  # 生成 ≠ 绑定


class TestAgentRunScopeIsolation:
    async def test_concurrent_tasks_are_isolated(self) -> None:
        """§二十六：task A ≠ run-B（ContextVar per-task 隔离）。"""
        seen: dict[str, str | None] = {}

        async def worker(name: str, expected: str) -> None:
            with agent_run_scope(expected):
                await asyncio.sleep(0)  # 交错点
                seen[name] = current_agent_run_id()
                await asyncio.sleep(0)
                seen[f"{name}-after"] = current_agent_run_id()

        await asyncio.gather(worker("A", "run-A"), worker("B", "run-B"))

        assert seen == {
            "A": "run-A",
            "A-after": "run-A",
            "B": "run-B",
            "B-after": "run-B",
        }

    async def test_scope_does_not_leak_across_tasks(self) -> None:
        async def scoped() -> None:
            with agent_run_scope("run-scoped"):
                await asyncio.sleep(0)

        await asyncio.gather(scoped(), asyncio.sleep(0))

        assert current_agent_run_id() is None

    def test_thread_isolation(self) -> None:
        """线程同样隔离：子线程看不到父线程 scope；父线程退出后恢复 None。"""
        seen: list[str | None] = []

        def worker() -> None:
            seen.append(current_agent_run_id())

        with agent_run_scope("run-thread"):
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join()
            assert current_agent_run_id() == "run-thread"

        assert seen == [None]
        assert current_agent_run_id() is None


class TestAgentRunScopeRuntimeBinding:
    """§十：Scope 必须覆盖 LLM 决策 → 授权 → Tool 执行（含写入点）→ 返回。"""

    async def test_scope_visible_at_tool_result_write_point(self) -> None:
        sink = RecordingSink()
        llm = FakeLLM([_TOOL_CALL, _FINAL])
        runtime = _runtime(llm, sink=sink)

        result = await runtime.run(question="查询 MAT001 库存", request_id="req-1")

        assert result.status == AGENT_STATUS_COMPLETED
        assert len(sink.calls) == 1
        call = sink.calls[0]
        # run_id 由 Runtime 签发；写入点通过 Agent Run Scope 读到**同一个**值
        assert call["scope_run_id"] == result.run_id
        assert call["step_index"] == 0
        assert call["tool_name"] == _TOOL_NAME
        assert call["tool_call_id"] == "call_scope_1"
        assert call["error_code"] is None
        assert getattr(call["result"], "success", None) is True
        # 写入发生在**第二轮 LLM 之前**（立即写入）
        assert llm.calls == 2
        # Run 结束后 scope 已恢复
        assert current_agent_run_id() is None

    async def test_tool_execution_sees_scope_during_execute(self) -> None:
        """Tool 执行期间 scope 已经存在（§十 生命周期要求）。"""
        handler = ScopeCapturingHandler()
        service, _registry, _h = _execution_service(handler)

        runtime = AgentRuntime(
            llm=FakeLLM([_TOOL_CALL, _FINAL]),
            tool_execution=service,
            policy=AgentRuntimePolicy(
                max_execution_time_ms=30_000, max_cost_tokens=1_000
            ),
            allowed_tools=(_TOOL_NAME,),
        )

        result = await runtime.run(question="查询 MAT001 库存")

        assert handler.scope_values == [result.run_id]
        assert handler.calls == [{"material_code": "MAT001"}]
        assert current_agent_run_id() is None

    async def test_runtime_without_sink_keeps_scope_and_result(self) -> None:
        llm = FakeLLM([_FINAL])
        runtime = _runtime(llm)

        result = await runtime.run(question="库存多少")

        assert result.status == AGENT_STATUS_COMPLETED
        assert runtime.tool_result_sink is None
        assert current_agent_run_id() is None

    def test_agent_run_scope_source_audit(self) -> None:
        """§三十三：scope 模块不 import DB / LLM / Tool / Conversation；无 global 状态。"""
        source = (_AGENT_DIR / "ids.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        for module in modules:
            assert not module.startswith("backend.app"), module
            assert not module.startswith("sqlalchemy"), module
        assert modules == {
            "__future__",
            "uuid",
            "collections.abc",
            "contextlib",
            "contextvars",
        }
        # 禁止 global mutable state（AST 层：无 ast.Global）
        assert not [node for node in ast.walk(tree) if isinstance(node, ast.Global)]
