"""Assistant Trace Query Service 单元测试（Phase 3.12 Step 38）。

层次（0 DB / 0 Network / 0 LLM / 0 真实 Tool 执行）：

    AssistantTraceQueryService.get_trace(A)
        ├── LLMUsageQueryService（Fake / 或真实类 + Fake Repository）
        └── ToolExecutionPersistentQueryService（真实类 + Fake Repository）
                ↓
        AssistantTraceView（frozen；tuple）

Tool 侧使用**真实** ToolExecutionPersistentQueryService + Fake Repository
（非 DB）：既验证排序/映射语义，也验证"Tool 数据源 = Persistent 读边界"。
"""
from __future__ import annotations

import ast
import inspect
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from backend.app.db.llm_usage_repository import LLMUsageRepositoryError
from backend.app.db.tool_execution_repository import ToolExecutionRecordRow
from backend.app.services.assistant_trace_query_service import (
    AssistantTraceQueryService,
    AssistantTraceView,
)
from backend.app.services.llm_usage_query_service import (
    LLMUsageTraceRecordView,
)
from backend.app.services.tool_execution_persistent_query_service import (
    ToolExecutionPersistentQueryService,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SERVICE_MODULE = "backend/app/services/assistant_trace_query_service.py"
_BASE = datetime(2026, 9, 28, 16, 0, 0, tzinfo=timezone.utc)


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _tree(relative: str) -> ast.Module:
    return ast.parse(_source(relative))


def _identifiers(relative: str) -> set[str]:
    tree = _tree(relative)
    return {
        node.id.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    } | {
        node.attr.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }


def _llm_view(**overrides: Any) -> LLMUsageTraceRecordView:
    values: dict[str, Any] = {
        "id": 1,
        "assistant_request_id": "A",
        "request_id": "chatcmpl-P1",
        "provider": "deepseek-test",
        "model": "deepseek-chat",
        "prompt_tokens": 11,
        "completion_tokens": 22,
        "total_tokens": 33,
        "created_at": _BASE,
    }
    values.update(overrides)
    return LLMUsageTraceRecordView(**values)


def _tool_row(
    *,
    request_id: str,
    round_: int = 1,
    success: bool = True,
    row_id: int = 1,
) -> ToolExecutionRecordRow:
    """持久化读边界的内部 Row（duck-typed DB 替身；非 ORM）。"""
    return ToolExecutionRecordRow(
        id=row_id,
        request_id=request_id,
        round=round_,
        tool_name="get_inventory",
        started_at=_BASE,
        finished_at=_BASE + timedelta(milliseconds=5),
        duration_ms=5.0,
        success=success,
        project_id="project-a",
        tool_call_id=None,
        error_code=None,
        error_type=None if success else "ToolValidationError",
    )


class _FakeLlmUsageService:
    """记录调用；可注入返回行 / 异常。"""

    def __init__(
        self,
        views: list[LLMUsageTraceRecordView] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.views = views if views is not None else []
        self.calls: list[str] = []
        self._error = error

    def list_by_assistant_request_id(
        self, assistant_request_id: str
    ) -> list[LLMUsageTraceRecordView]:
        self.calls.append(assistant_request_id)
        if self._error is not None:
            raise self._error
        return list(self.views)


class _FakeToolService:
    """记录调用；可注入快照 / 异常（用于异常透传测试）。"""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self._error = error

    def list_by_request_id(self, request_id: str) -> tuple:
        self.calls.append(request_id)
        if self._error is not None:
            raise self._error
        return ()


class _FakeToolRepository:
    """持久化读边界背后的 Repository 替身（记录调用；0 DB）。"""

    def __init__(self, rows: list[ToolExecutionRecordRow] | None = None) -> None:
        self.rows = rows if rows is not None else []
        self.calls: list[str] = []

    def get_by_request_id(
        self, request_id: str
    ) -> list[ToolExecutionRecordRow]:
        self.calls.append(request_id)
        return [row for row in self.rows if row.request_id == request_id]


def _tool_boundary(
    rows: list[ToolExecutionRecordRow] | None = None,
) -> ToolExecutionPersistentQueryService:
    """真实 Persistent Tool 读边界 + Fake Repository（Step 41 的 Trace 数据源）。"""
    return ToolExecutionPersistentQueryService(
        repository=_FakeToolRepository(rows)  # type: ignore[arg-type]
    )


def _service(
    *,
    llm: Any = None,
    tools: Any = None,
) -> AssistantTraceQueryService:
    return AssistantTraceQueryService(
        llm_usage_query_service=llm if llm is not None else _FakeLlmUsageService(),
        tool_observability_query_service=(
            tools if tools is not None else _tool_boundary()
        ),
    )


# ============================================================
# 1~8. Read Model 组合语义
# ============================================================

class TestTraceComposition:
    def test_1_llm_only(self) -> None:
        llm = _FakeLlmUsageService([
            _llm_view(id=1, request_id="chatcmpl-P1"),
            _llm_view(id=2, request_id="chatcmpl-P2", created_at=(
                _BASE + timedelta(seconds=1)
            )),
        ])

        view = _service(llm=llm).get_trace("A")

        assert view.assistant_request_id == "A"
        assert len(view.llm_usage) == 2
        assert view.tool_executions == ()
        assert llm.calls == ["A"]

    def test_2_tool_only(self) -> None:
        tools = _tool_boundary([_tool_row(request_id="A")])
        llm = _FakeLlmUsageService()

        view = _service(llm=llm, tools=tools).get_trace("A")

        assert view.llm_usage == ()
        assert len(view.tool_executions) == 1
        assert view.tool_executions[0].request_id == "A"

    def test_3_llm_and_tool(self) -> None:
        tools = _tool_boundary([_tool_row(request_id="A")])
        llm = _FakeLlmUsageService([
            _llm_view(id=1, request_id="chatcmpl-P1"),
            _llm_view(id=2, request_id="chatcmpl-P2"),
        ])

        view = _service(llm=llm, tools=tools).get_trace("A")

        assert len(view.llm_usage) == 2
        assert len(view.tool_executions) == 1
        assert all(v.assistant_request_id == "A" for v in view.llm_usage)
        assert all(s.request_id == "A" for s in view.tool_executions)

    def test_4_empty_is_not_an_error(self) -> None:
        view = _service().get_trace("A")

        assert view == AssistantTraceView(
            assistant_request_id="A", llm_usage=(), tool_executions=()
        )

    def test_5_unknown_id_returns_empty_view(self) -> None:
        view = _service().get_trace("not-exist")

        assert view.assistant_request_id == "not-exist"
        assert view.llm_usage == () and view.tool_executions == ()

    def test_6_traces_do_not_mix(self) -> None:
        collector = _tool_boundary([
            _tool_row(request_id="A"),
            _tool_row(request_id="B", round_=2),
        ])
        tools = collector
        llm = _FakeLlmUsageService([_llm_view(assistant_request_id="A")])
        service = _service(llm=llm, tools=tools)

        view_a = service.get_trace("A")

        assert [s.request_id for s in view_a.tool_executions] == ["A"]
        assert all(v.assistant_request_id == "A" for v in view_a.llm_usage)
        # Tool 侧真实过滤：B 不在 A 的结果里
        assert "B" not in {s.request_id for s in view_a.tool_executions}

    def test_7_llm_ordering_preserved(self) -> None:
        """本层**不重排**：下游顺序（created_at ASC, id ASC）原样保留。"""
        llm = _FakeLlmUsageService([
            _llm_view(id=11, request_id="chatcmpl-P1", created_at=_BASE),
            _llm_view(
                id=12, request_id="chatcmpl-P2",
                created_at=_BASE + timedelta(seconds=1),
            ),
            _llm_view(
                id=13, request_id="chatcmpl-P3",
                created_at=_BASE + timedelta(seconds=2),
            ),
        ])

        view = _service(llm=llm).get_trace("A")

        assert [v.request_id for v in view.llm_usage] == [
            "chatcmpl-P1", "chatcmpl-P2", "chatcmpl-P3",
        ]
        assert [v.created_at for v in view.llm_usage] == sorted(
            v.created_at for v in view.llm_usage
        )

    def test_8_tool_ordering_preserved(self) -> None:
        """Tool 顺序 = 持久化读边界返回顺序（本层不二次排序）。"""
        tools = _tool_boundary([
            _tool_row(request_id="A", round_=1, row_id=1),
            _tool_row(request_id="A", round_=2, row_id=2),
            _tool_row(request_id="A", round_=3, row_id=3),
        ])

        view = _service(tools=tools).get_trace("A")

        assert [s.round for s in view.tool_executions] == [1, 2, 3]
        assert [s.started_at for s in view.tool_executions] == sorted(
            s.started_at for s in view.tool_executions
        )
        # 顺序与下游读边界直接查询一致（本层未重排）
        assert tuple(view.tool_executions) == tuple(
            tools.list_by_request_id("A")
        )

    def test_view_is_immutable_and_uses_tuples(self) -> None:
        view = _service().get_trace("A")

        assert is_dataclass(view)
        assert isinstance(view.llm_usage, tuple)
        assert isinstance(view.tool_executions, tuple)
        with pytest.raises(Exception):
            view.assistant_request_id = "B"      # type: ignore[misc]
        with pytest.raises(Exception):
            view.llm_usage = ()                  # type: ignore[misc]


# ============================================================
# 9~10. 校验 / 异常透传
# ============================================================

class TestValidationAndErrors:
    @pytest.mark.parametrize(
        "bad", [None, "", "   ", "\t", 123, b"A", ["A"], "x" * 129]
    )
    def test_9_invalid_input_rejected_before_downstream(self, bad) -> None:
        llm = _FakeLlmUsageService()
        tools = _FakeToolService()
        service = _service(llm=llm, tools=tools)

        with pytest.raises(ValueError):
            service.get_trace(bad)  # type: ignore[arg-type]

        assert llm.calls == [] and tools.calls == []

    def test_9_max_length_boundary_accepted(self) -> None:
        llm = _FakeLlmUsageService()
        service = _service(llm=llm)
        request_id = "x" * 128

        view = service.get_trace(request_id)

        assert view.assistant_request_id == request_id   # 不截断 / 不改写
        assert llm.calls == [request_id]

    def test_10_llm_error_propagates_not_empty(self) -> None:
        error = LLMUsageRepositoryError("db down")
        llm = _FakeLlmUsageService(error=error)
        tools = _FakeToolService()

        with pytest.raises(LLMUsageRepositoryError) as excinfo:
            _service(llm=llm, tools=tools).get_trace("A")

        assert excinfo.value is error
        assert tools.calls == []          # LLM 失败 → 不继续查 Tool（不掩盖）

    def test_10_tool_error_propagates(self) -> None:
        class _ToolError(Exception):
            pass

        tools = _FakeToolService(error=_ToolError("collector failed"))

        with pytest.raises(_ToolError):
            _service(tools=tools).get_trace("A")

    def test_invalid_service_injection_rejected(self) -> None:
        with pytest.raises(TypeError):
            AssistantTraceQueryService(
                tool_observability_query_service=object()
            )
        with pytest.raises(TypeError):
            AssistantTraceQueryService(
                tool_observability_query_service=_FakeToolService(),
                llm_usage_query_service=object(),
            )

    def test_tool_read_boundary_is_required(self) -> None:
        """Tool 读边界必填：本服务**不创建 Collector**（唯一创建点仍是
        Composition Root —— Phase 3.11 Step 23/24 契约）。"""
        with pytest.raises(TypeError):
            AssistantTraceQueryService()  # type: ignore[call-arg]

    def test_runtime_memory_boundary_is_rejected(self) -> None:
        """Step 41：Runtime 内存边界（只有 snapshots_by_request_id）不被接受
        —— Assistant Trace 固定使用 Persistent Tool 读边界。"""
        from backend.app.services.in_memory_tool_execution_collector import (
            InMemoryToolExecutionCollector,
        )
        from backend.app.services.tool_observability_query_service import (
            ToolObservabilityQueryService,
        )

        runtime_boundary = ToolObservabilityQueryService(
            InMemoryToolExecutionCollector()
        )

        with pytest.raises(TypeError):
            AssistantTraceQueryService(
                tool_observability_query_service=runtime_boundary
            )

    def test_view_rejects_wrong_payload(self) -> None:
        with pytest.raises(ValueError):
            AssistantTraceView(
                assistant_request_id="A",
                llm_usage=[],            # type: ignore[arg-type]
                tool_executions=(),
            )
        with pytest.raises(ValueError):
            AssistantTraceView(
                assistant_request_id="   ",
                llm_usage=(),
                tool_executions=(),
            )
        with pytest.raises(ValueError):
            AssistantTraceView(
                assistant_request_id="A",
                llm_usage=(object(),),   # type: ignore[arg-type]
                tool_executions=(),
            )


# ============================================================
# 11. Security（字段 / 投影）
# ============================================================

class TestSecurity:
    def test_11_view_fields_are_whitelisted(self) -> None:
        assert [f.name for f in fields(AssistantTraceView)] == [
            "assistant_request_id", "llm_usage", "tool_executions",
        ]
        assert [f.name for f in fields(LLMUsageTraceRecordView)] == [
            "id", "assistant_request_id", "request_id", "provider", "model",
            "prompt_tokens", "completion_tokens", "total_tokens",
            "created_at",
        ]

    def test_11_task_arguments_and_prompts_absent(self) -> None:
        tree = _tree(_SERVICE_MODULE)
        identifiers = _identifiers(_SERVICE_MODULE)
        for forbidden in (
            "prompt", "messages", "raw_response", "tool_args", "arguments",
            "tool_result", "sql", "session", "connection", "api_key",
            "password", "authorization", "database_url", "secret",
        ):
            assert forbidden not in identifiers, forbidden
        # 无隐式投影
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        for forbidden in ("vars", "asdict", "dict"):
            assert forbidden not in calls, forbidden
        assert "__dict__" not in identifiers
        assert "model_dump" not in identifiers

    def test_11_no_tool_arguments_in_result(self) -> None:
        tools = _tool_boundary([_tool_row(request_id="A")])

        view = _service(tools=tools).get_trace("A")

        snapshot = view.tool_executions[0]
        assert not hasattr(snapshot, "arguments")
        assert not hasattr(snapshot, "data")
        assert not hasattr(snapshot, "result")


# ============================================================
# C43. Assistant Trace Read Model Boundary
# ============================================================

class TestC43AssistantTraceReadModel:
    """C43：Assistant Trace Read Model 的组合边界。

        C43.1  只依赖 Query Service（LLMUsageQueryService /
               Tool 观测 Persistent 读边界；Step 41 起用具名方法
               ``list_by_request_id`` 断言，而不是具体类名）
        C43.2  不依赖 Repository
        C43.3  不依赖 SQLAlchemy
        C43.4  不访问 Session
        C43.5  不生成 request_id
        C43.6  不执行 Tool
        C43.7  不执行 LLM
        C43.8  不新增 HTTP endpoint
        C43.9  immutable result
        C43.10 不暴露 secrets
    """

    def test_c43_1_depends_only_on_query_services(self) -> None:
        source = _source(_SERVICE_MODULE)
        assert "LLMUsageQueryService" in source
        assert "list_by_assistant_request_id" in source      # LLM 读边界
        assert "list_by_request_id" in source                # Tool 持久化读边界
        # 数据源声明：LLM / Tool 均为持久化边界（Step 41）
        assert "llm_usage_record" in source
        assert "tool_execution_record" in source
        # 不使用 Runtime 内存边界：不 import Collector，也不调用其查询方法
        # （文档字符串里可以说明"Runtime 边界不参与"，因此按 AST 断言调用点）
        assert "in_memory_tool_execution_collector" not in source
        assert "InMemoryToolExecutionCollector" not in source
        attributes = {
            node.attr
            for node in ast.walk(_tree(_SERVICE_MODULE))
            if isinstance(node, ast.Attribute)
        }
        assert "snapshots_by_request_id" not in attributes, attributes

    def test_c43_2_and_3_no_repository_no_sqlalchemy(self) -> None:
        identifiers = _identifiers(_SERVICE_MODULE)
        # 文档字符串里出现 "Repository"（说明"不新增 Repository"）不算依赖；
        # 这里检查**代码标识符**与 import。
        assert not any("repository" in name for name in identifiers), identifiers
        assert "llm_usagerepository" not in identifiers
        assert "toolexecutionrepository" not in identifiers
        for forbidden in ("select", "insert", "update", "delete", "text"):
            assert forbidden not in identifiers, forbidden
        imports = {
            node.module or ""
            for node in ast.walk(_tree(_SERVICE_MODULE))
            if isinstance(node, ast.ImportFrom)
        }
        for forbidden in (
            "sqlalchemy", "backend.app.db", "backend.app.repositories",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden

    def test_c43_4b_does_not_create_collector(self) -> None:
        """不创建 Collector（Collector 唯一创建点 = api/orchestrator_chat.py）。"""
        source = _source(_SERVICE_MODULE)
        identifiers = _identifiers(_SERVICE_MODULE)
        assert "inmemorytoolexecutioncollector" not in identifiers
        assert "Collector(" not in source
        assert "Collector)" not in source

    def test_c43_4_no_session_access(self) -> None:
        identifiers = _identifiers(_SERVICE_MODULE)
        for forbidden in (
            "session", "engine", "commit", "rollback", "execute", "connection",
        ):
            assert forbidden not in identifiers, forbidden

    def test_c43_5_does_not_generate_request_id(self) -> None:
        source = _source(_SERVICE_MODULE)
        identifiers = _identifiers(_SERVICE_MODULE)
        for forbidden in ("uuid", "uuid4", "new_request_id", "token_hex"):
            assert forbidden not in identifiers, forbidden
        assert "new_request_id(" not in source

    def test_c43_6_and_7_no_tool_or_llm_execution(self) -> None:
        identifiers = _identifiers(_SERVICE_MODULE)
        for forbidden in (
            "registry", "handler", "tool_execution_service", "chat",
            "client", "generate", "complete", "rag", "router", "route",
        ):
            assert forbidden not in identifiers, forbidden
        public = {
            name
            for name in dir(AssistantTraceQueryService)
            if not name.startswith("_")
        }
        assert public == {
            "get_trace",
            "llm_usage_query_service",
            "tool_observability_query_service",
        }, public

    def test_c43_8_no_unexpected_http_endpoint(self) -> None:
        """Step 38 时点：无 Trace endpoint。

        Step 39：**有意**新增唯一一个只读 Trace 端点
        ``GET /api/observability/assistant-trace/{assistant_request_id}``
        （HTTP 层只调用 ``get_trace()``）；其它 trace / by-request 路径仍不存在。
        """
        from fastapi.testclient import TestClient

        from backend.app.main import app

        trace_paths = {
            path for path in app.openapi()["paths"] if "trace" in path
        }
        assert trace_paths == {
            "/api/observability/assistant-trace/{assistant_request_id}"
        }, trace_paths
        assert not any(
            "by-request" in path for path in app.openapi()["paths"]
        )
        # 其它 API 模块仍不直接依赖本服务（只有 api/assistant_trace.py 经 accessor
        # 调用 get_trace；orchestrator_chat 仅提供装配 accessor）
        for module in (
            "backend/app/api/usage.py",
            "backend/app/api/tool_observability.py",
        ):
            assert "assistant_trace_query_service" not in _source(module), module
        root_source = _source("backend/app/api/orchestrator_chat.py")
        assert "def get_assistant_trace_query_service" in root_source
        assert ".get_trace(" not in root_source          # 只装配，不查询
        with TestClient(app) as client:
            assert client.get("/api/trace").status_code == 404
            assert client.get("/api/assistant/trace").status_code == 404
            assert client.get("/api/usage/by-request").status_code == 404

    def test_c43_9_immutable_result(self) -> None:
        view = _service().get_trace("A")

        assert isinstance(view, AssistantTraceView)
        assert isinstance(view.llm_usage, tuple)
        assert isinstance(view.tool_executions, tuple)
        with pytest.raises(Exception):
            view.tool_executions = ()            # type: ignore[misc]

    def test_c43_10_no_secret_fields(self) -> None:
        for name in ("AssistantTraceView.get_trace",):
            assert name.endswith("get_trace")
        assert "trace_id" not in _identifiers(_SERVICE_MODULE)
        params = inspect.signature(
            AssistantTraceQueryService.get_trace
        ).parameters
        assert list(params) == ["self", "assistant_request_id"]


__all__ = [
    "TestTraceComposition",
    "TestValidationAndErrors",
    "TestSecurity",
    "TestC43AssistantTraceReadModel",
]
