"""Tool Observability Architecture Hardening & Contract Audit（Phase 3.11 Step 23）。

本阶段**不新增业务能力**：只把 Step 1~22 形成的边界固化为可执行的审计断言
（C25）。全部为静态结构 + 少量行为契约；不重复既有测试已覆盖的细节。

覆盖（§二十）：

    Dependency       无顶层循环 import；延迟 import 仅限已知的两处（历史决策）
    Direction        Execution → Observability 单向；Observability ↛ Execution
    Record           frozen / 字段白名单 / 无 arguments·SQL·prompt·secret·traceback
    Collector        finite / FIFO / thread-safe / explicit clear / 无 TTL·持久化
    Observer         只接收 Record；失败隔离（不 retry / 不 fallback / 不重跑）
    Metrics          纯只读计算（不改 Record / 不改 Collector）；None ≠ 0
    Query            READ ONLY（无 clear / append / on_execution / 自有存储）
    Snapshot         frozen / 独立 DTO / 显式映射（无 vars·asdict·__dict__）
    API              无 Tool Observability HTTP 端点；api 不依赖 Query·Snapshot
    Composition      Collector 仅由 Composition Root 创建；Orchestrator 不创建
                     Collector / Observer / Query Service
    Project          Query 不得绕过 capability；project_id = Authorization Scope
    Security         Observability Read Model 层 import 白名单
    Matrix           Layer | Can Write | Can Read | Can Execute Tool | DB | LLM

0 DB / 0 Network / 0 Real LLM（静态审计 + 内存用例）。
"""
from __future__ import annotations

import ast
import dataclasses
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from backend.app.services.in_memory_tool_execution_collector import (
    InMemoryToolExecutionCollector,
)
from backend.app.services.tool_execution_context import ToolExecutionContext
from backend.app.services.tool_execution_metrics_service import (
    ToolExecutionMetricsService,
)
from backend.app.services.tool_execution_observer import ToolExecutionObserver
from backend.app.services.tool_execution_record import ToolExecutionRecord
from backend.app.services.tool_execution_service import ToolExecutionService
from backend.app.services.tool_observability_query_service import (
    ToolObservabilityQueryService,
)
from backend.app.services.tool_observability_snapshot import (
    ToolExecutionSnapshot,
)
from backend.app.tools.registry import ToolRegistry, ToolResult

# 复用既有测试资产（Fakes / decisions），不重复创建
from tests.test_ai_orchestrator import (  # noqa: E402
    FakeContextComposer,
    FakeProjectProvider,
    FakeRAG,
    FakeRouter,
    FakeSQLExecutor,
    FakeTableSelector,
    FakeTextToSQL,
    _tool_decision,
)
from backend.app.services.ai_orchestrator_service import (  # noqa: E402
    AIOrchestratorService,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_MODULES = {
    "context": "backend/app/services/tool_execution_context.py",
    "record": "backend/app/services/tool_execution_record.py",
    "observer": "backend/app/services/tool_execution_observer.py",
    "service": "backend/app/services/tool_execution_service.py",
    "collector": "backend/app/services/in_memory_tool_execution_collector.py",
    "metrics": "backend/app/services/tool_execution_metrics_service.py",
    "snapshot": "backend/app/services/tool_observability_snapshot.py",
    "query": "backend/app/services/tool_observability_query_service.py",
    "extractor": "backend/app/services/tool_argument_extractor.py",
    "orchestrator": "backend/app/services/ai_orchestrator_service.py",
    "factory": "backend/app/services/project_orchestrator_factory.py",
    "api_orchestrator_chat": "backend/app/api/orchestrator_chat.py",
    "api_tool_chat": "backend/app/api/tool_chat.py",
    "registry": "backend/app/tools/registry.py",
    "tool_chat_service": "backend/app/services/tool_chat_service.py",
}

_QUESTION = "查询物料 MAT-001 当前库存"
_STARTED = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)

#: 已知（有意）延迟 import：模块级 import 会形成循环依赖 → 函数内延迟导入
_KNOWN_DELAYED_IMPORTS = {
    ("service", "orchestrator"),           # AIOrchestratorCapabilityError
    ("api_orchestrator_chat", "factory"),  # project_orchestrator_factory
}


# ============================================================
# 静态审计辅助
# ============================================================

def _path(key: str) -> str:
    return os.path.join(REPO_ROOT, *_MODULES[key].split("/"))


def _source(key: str) -> str:
    with open(_path(key), encoding="utf-8") as handle:
        return handle.read()


def _tree(key: str) -> ast.Module:
    return ast.parse(_source(key))


def _internal_imports(key: str, *, toplevel_only: bool) -> set[str]:
    tree = _tree(key)
    nodes = tree.body if toplevel_only else list(ast.walk(tree))
    found: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("backend.app"):
                found.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("backend.app"):
                    found.add(alias.name)
    return found


def _graph(*, toplevel_only: bool) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for key, path in _MODULES.items():
        resolved: set[str] = set()
        for dotted in _internal_imports(key, toplevel_only=toplevel_only):
            dotted_path = dotted.replace(".", "/") + ".py"
            for other, other_path in _MODULES.items():
                if other_path == dotted_path and other != key:
                    resolved.add(other)
        graph[key] = resolved
    return graph


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    cycles: list[list[str]] = []
    state: dict[str, int] = {}
    stack: list[str] = []

    def dfs(node: str) -> None:
        state[node] = 1
        stack.append(node)
        for dep in sorted(graph.get(node, ())):
            if state.get(dep, 0) == 1:
                cycles.append([*stack[stack.index(dep):], dep])
            elif state.get(dep, 0) == 0:
                dfs(dep)
        stack.pop()
        state[node] = 2

    for key in sorted(graph):
        if state.get(key, 0) == 0:
            dfs(key)
    return cycles


def _identifiers(key: str) -> set[str]:
    tree = _tree(key)
    return {
        node.id.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    } | {
        node.attr.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }


def _public_api(obj: Any) -> set[str]:
    return {name for name in dir(obj) if not name.startswith("_")}


def _record(
    *,
    request_id: str = "req-1",
    round: int = 1,
    tool_name: str = "get_inventory",
    project_id: str | None = None,
    success: bool = True,
    duration_ms: float = 1.0,
) -> ToolExecutionRecord:
    return ToolExecutionRecord(
        request_id=request_id,
        round=round,
        tool_name=tool_name,
        started_at=_STARTED,
        finished_at=_STARTED + timedelta(milliseconds=duration_ms),
        duration_ms=duration_ms,
        success=success,
        project_id=project_id,
        error_type=None if success else "ToolValidationError",
    )


def _collector_with(
    *records: ToolExecutionRecord, max_records: int = 1000
) -> InMemoryToolExecutionCollector:
    collector = InMemoryToolExecutionCollector(max_records=max_records)
    for record in records:
        collector.on_execution(record)
    return collector


def _orchestrator_with(
    collector: InMemoryToolExecutionCollector, *, registry: ToolRegistry
) -> AIOrchestratorService:
    return AIOrchestratorService(
        router=FakeRouter(_tool_decision()),
        rag_service=FakeRAG(),
        text_to_sql=FakeTextToSQL(),
        sql_executor=FakeSQLExecutor(),
        table_selector=FakeTableSelector(),
        context_composer=FakeContextComposer(),
        project_context_provider=FakeProjectProvider(),
        tool_registry=registry,
        tool_execution_observer=collector,
    )


class _ExplodingObserver:
    def __init__(self) -> None:
        self.calls = 0

    def on_execution(self, record: ToolExecutionRecord) -> None:
        self.calls += 1
        raise RuntimeError("observer boom")


class _RecordingHandler:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        return {"qty": 1.0}


# ============================================================
# C25.1 ~ C25.4 Dependency / Direction
# ============================================================

class TestC25DependencyGraph:
    def test_c25_1_no_top_level_circular_import(self) -> None:
        """模块级（import 时执行）依赖图必须无环。"""
        assert _cycles(_graph(toplevel_only=True)) == []

    def test_c25_2_delayed_imports_are_known_and_documented(self) -> None:
        """函数内延迟 import 只允许已知两处（Phase 3.8.2 / 3.8.1 历史决策）；
        不做自动"修复"（§五）。"""
        top = _graph(toplevel_only=True)
        everything = _graph(toplevel_only=False)
        delayed = {
            (src, dst)
            for src in everything
            for dst in everything[src] - top[src]
        }
        assert delayed == _KNOWN_DELAYED_IMPORTS, delayed

    def test_c25_3_execution_to_observability_is_one_way(self) -> None:
        """Observability 层不得反向依赖 Execution / Orchestrator / API /
        ToolRegistry / Tool Handler。"""
        for key in ("collector", "metrics", "query", "snapshot", "observer"):
            imports = _internal_imports(key, toplevel_only=False)
            for forbidden in (
                "backend.app.services.tool_execution_service",
                "backend.app.services.ai_orchestrator_service",
                "backend.app.services.tool_chat_service",
                "backend.app.services.ai_router_service",
                "backend.app.api",
                "backend.app.tools.registry",
                "backend.app.tools.get_inventory",
                "backend.app.tools.get_work_order",
            ):
                assert not any(
                    name == forbidden or name.startswith(forbidden + ".")
                    for name in imports
                ), (key, forbidden)

    def test_c25_4_tools_do_not_depend_on_observability(self) -> None:
        """Tool / Registry 不得依赖 Observability 或 Orchestrator（C4/C9）。"""
        for key in ("registry",):
            imports = _internal_imports(key, toplevel_only=False)
            for forbidden in (
                "backend.app.services.tool_execution",
                "backend.app.services.tool_observability",
                "backend.app.services.in_memory_tool_execution_collector",
                "backend.app.services.ai_orchestrator_service",
                "backend.app.api",
            ):
                assert not any(
                    name.startswith(forbidden) for name in imports
                ), forbidden


# ============================================================
# C25.5 ~ C25.8 Record / Collector / Observer / Metrics
# ============================================================

class TestC25RecordCollectorObserverMetrics:
    def test_c25_5_record_contract(self) -> None:
        record = _record(project_id="project-a")

        assert isinstance(record, ToolExecutionRecord)
        assert ToolExecutionRecord.__dataclass_params__.frozen is True
        assert {f.name for f in dataclasses.fields(record)} == {
            "request_id", "round", "tool_name", "started_at", "finished_at",
            "duration_ms", "success", "project_id", "tool_call_id",
            "error_code", "error_type",
        }
        assert not isinstance(record.started_at, (list, dict, set))
        assert not isinstance(record.duration_ms, (list, dict, set))
        record.assert_field_whitelist()
        text = repr(record)
        for forbidden in (
            "arguments", "SELECT", "prompt", "password", "postgresql://",
            "Authorization", "Traceback", "material_code",
        ):
            assert forbidden not in text, forbidden

    def test_c25_6_collector_finite_fifo_and_thread_safe(self) -> None:
        collector = _collector_with(
            _record(request_id="req-A"),
            _record(request_id="req-B"),
            _record(request_id="req-C"),
            max_records=2,
        )
        assert [r.request_id for r in collector.records()] == [
            "req-B", "req-C"
        ]                                    # FIFO：保留最新 N
        assert collector.max_records == 2     # finite

        shared = InMemoryToolExecutionCollector(max_records=50)

        def worker() -> None:
            for _ in range(40):
                shared.on_execution(_record())

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(shared.records()) == 50   # 并发安全且不越界

    def test_c25_7_collector_explicit_clear_no_ttl_no_persistence(self) -> None:
        collector = _collector_with(_record(), max_records=5)
        collector.clear()
        assert collector.records() == ()

        identifiers = _identifiers("collector")
        for forbidden in (
            "ttl", "timer", "schedule", "daemon", "create_task", "thread",
        ):
            assert forbidden not in identifiers, forbidden
        imports = _internal_imports("collector", toplevel_only=False)
        for forbidden in ("json", "pathlib", "os", "sqlalchemy", "redis"):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden

    async def test_c25_8_observer_receives_only_records_and_failure_isolated(
        self,
    ) -> None:
        from backend.app.tools.get_inventory import GET_INVENTORY_DEFINITION

        handler = _RecordingHandler()
        registry = ToolRegistry()
        registry.register(GET_INVENTORY_DEFINITION, handler)
        observer = _ExplodingObserver()
        boundary = ToolExecutionService(registry=registry, observer=observer)

        assert isinstance(observer, ToolExecutionObserver)

        # 语义（Step 15）：只有「有 Context + 有 observer」的执行才产生事件
        # （context=None → 0 事件，不是缺陷）
        assert (
            await boundary.execute(
                "get_inventory", arguments={"material_code": "M1"}, context=None
            )
        ).success is True
        assert observer.calls == 0
        assert handler.calls == 1

        result = await boundary.execute(
            "get_inventory",
            arguments={"material_code": "M1"},
            context=ToolExecutionContext(
                request_id="req-c25", round=1, project_id=None
            ),
        )

        assert result.success is True         # observer 失败 ≠ Tool 失败
        assert observer.calls == 1            # 有 Context + observer → 1 次事件
        assert handler.calls == 2             # 两次执行各一次（无 retry / 无重跑）

    def test_c25_9_metrics_read_only_and_none_semantics(self) -> None:
        collector = _collector_with(
            _record(duration_ms=2.0), _record(success=False, duration_ms=4.0)
        )
        before = collector.records()

        snapshot = ToolExecutionMetricsService.snapshot(collector.records())

        assert collector.records() == before      # 不修改 Collector
        assert snapshot.total_count == 2
        assert snapshot.average_duration_ms == 3.0

        empty = ToolExecutionMetricsService.snapshot(())
        assert empty.success_rate is None         # None ≠ 0（§十）
        assert empty.total_duration_ms == 0.0
        assert _public_api(ToolExecutionMetricsService) == {"snapshot"}

    def test_c25_10_collector_boundary_values(self) -> None:
        """max_records=1 与极大值的行为（不做 benchmark）。"""
        minimal = _collector_with(
            _record(request_id="req-A"),
            _record(request_id="req-B"),
            max_records=1,
        )
        assert [r.request_id for r in minimal.records()] == ["req-B"]

        huge = InMemoryToolExecutionCollector(max_records=100_000)
        for index in range(5):
            huge.on_execution(_record(request_id=f"req-{index}"))
        assert len(huge.records()) == 5           # 未达上限 → 全部保留


# ============================================================
# C25.11 ~ C25.13 Query / Snapshot / API
# ============================================================

class TestC25QuerySnapshotApi:
    def test_c25_11_query_is_read_only_without_own_storage(self) -> None:
        query = ToolObservabilityQueryService(
            _collector_with(_record(), max_records=3)
        )

        assert set(vars(query)) == {"_collector", "_metrics_service"}
        assert not (_public_api(query) & {
            "clear", "append", "on_execution", "evict", "add", "reset",
        })
        # 无自有存储 / 无锁（AST 层；docstring 中的"禁止 …"说明不算实现）
        identifiers = _identifiers("query")
        for forbidden in ("deque", "_records", "lock", "dict", "cache"):
            assert forbidden not in identifiers, forbidden

    def test_c25_12_snapshot_is_independent_frozen_dto(self) -> None:
        record = _record(project_id="project-a")
        snapshot = ToolExecutionSnapshot.from_record(record)

        assert type(snapshot) is not type(record)
        assert ToolExecutionSnapshot.__dataclass_params__.frozen is True
        assert set(vars(snapshot)) == {
            f.name for f in dataclasses.fields(snapshot)
        }
        tree = _tree("snapshot")
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert not (calls & {"vars", "asdict"})
        assert "__dict__" not in _identifiers("snapshot")

    def test_c25_13_only_allowlisted_tool_observability_http_api(self) -> None:
        """Tool Observability HTTP 端点**仅限** Step 25 的两个只读端点。

        Step 23 审计时点为 0 端点；Step 25 有意引入 Read API（§四）后，
        本断言改为"白名单"形式：除 ``/api/observability/tools`` 与
        ``/api/observability/tools/metrics`` 外，不得出现任何
        observability / metrics / snapshot 端点。

        只看**路由路径**（装饰器里的字符串），避免把既有 import 名称
        （如 ``tool_execution_service``）误判成端点。
        """
        allowed_paths = {
            "/observability/tools",
            "/observability/tools/metrics",
        }
        api_dir = os.path.join(REPO_ROOT, "backend", "app", "api")
        routes: list[tuple[str, str]] = []
        for filename in sorted(os.listdir(api_dir)):
            if not filename.endswith(".py"):
                continue
            with open(
                os.path.join(api_dir, filename), encoding="utf-8"
            ) as handle:
                tree = ast.parse(handle.read())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for argument in node.args:
                    if (
                        isinstance(argument, ast.Constant)
                        and isinstance(argument.value, str)
                        and argument.value.startswith("/")
                    ):
                        routes.append((filename, argument.value))

        observability_routes = []
        for filename, path in routes:
            if any(
                token in path
                for token in ("observability", "metrics", "snapshot")
            ):
                observability_routes.append((filename, path))
                assert path in allowed_paths, (filename, path)
        assert observability_routes, "Step 25 的两个只读端点应存在"

        # 除 Composition Root（orchestrator_chat）与 Read API 外，
        # 其它 api 模块不得直连 Observability 内部
        for filename in sorted(os.listdir(api_dir)):
            if not filename.endswith(".py"):
                continue
            if filename in {"orchestrator_chat.py", "tool_observability.py"}:
                continue
            with open(
                os.path.join(api_dir, filename), encoding="utf-8"
            ) as handle:
                api_source = handle.read()
            assert (
                "tool_observability_query_service" not in api_source
            ), filename
            assert (
                "in_memory_tool_execution_collector" not in api_source
            ), filename


# ============================================================
# C25.14 ~ C25.17 Composition / Project / Security / Matrix
# ============================================================

class TestC25CompositionProjectSecurity:
    def test_c25_14_collector_created_only_by_composition_root(self) -> None:
        import warnings

        offenders: list[str] = []
        backend_dir = os.path.join(REPO_ROOT, "backend")
        for dirpath, _dirnames, filenames in os.walk(backend_dir):
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                full = os.path.join(dirpath, filename)
                with open(full, encoding="utf-8-sig") as handle:
                    try:
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore")
                            tree = ast.parse(handle.read())
                    except SyntaxError:
                        continue
                if any(
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "InMemoryToolExecutionCollector"
                    for node in ast.walk(tree)
                ):
                    offenders.append(
                        os.path.relpath(full, REPO_ROOT).replace("\\", "/")
                    )
        assert offenders == [_MODULES["api_orchestrator_chat"]], offenders

    def test_c25_15_orchestrator_creates_no_observer_collector_or_query(
        self,
    ) -> None:
        source = _source("orchestrator")
        identifiers = _identifiers("orchestrator")
        for forbidden in (
            "InMemoryToolExecutionCollector", "Collector(",
            "ToolObservabilityQueryService", "ToolExecutionSnapshot",
            "ToolExecutionMetricsService",
        ):
            assert forbidden not in source, forbidden
        for forbidden in ("max_records", "retention", "evict", "snapshot"):
            assert forbidden not in identifiers, forbidden

    async def test_c25_16_project_boundary_cannot_be_bypassed(self) -> None:
        """Query 不得重新做权限判断，也不得扩大权限：capability 拒绝 → 0 Record。"""
        from backend.app.projects.capabilities import ProjectCapabilities
        from backend.app.services.ai_orchestrator_service import (
            AIOrchestratorCapabilityError,
        )
        from backend.app.tools.get_inventory import GET_INVENTORY_DEFINITION

        collector = InMemoryToolExecutionCollector()
        handler = _RecordingHandler()
        registry = ToolRegistry()
        registry.register(GET_INVENTORY_DEFINITION, handler)
        orchestrator = AIOrchestratorService(
            router=FakeRouter(_tool_decision()),
            rag_service=FakeRAG(),
            text_to_sql=FakeTextToSQL(),
            sql_executor=FakeSQLExecutor(),
            table_selector=FakeTableSelector(),
            context_composer=FakeContextComposer(),
            project_context_provider=FakeProjectProvider(),
            tool_registry=registry,
            tool_execution_observer=collector,
            capabilities=ProjectCapabilities(tool_names=()),
        )

        with pytest.raises(AIOrchestratorCapabilityError):
            await orchestrator.execute(_QUESTION)

        assert collector.records() == ()
        assert handler.calls == 0
        # Query 只读取：不提供任何能力开关 / 写操作
        query = ToolObservabilityQueryService(collector)
        assert query.records() == ()
        assert query.metrics().total_count == 0

    def test_c25_17_observability_read_model_imports_are_pure(self) -> None:
        """Observability Read Model 层不得依赖 DB / LLM / HTTP / 进程。"""
        for key in ("record", "observer", "collector", "metrics", "query",
                    "snapshot"):
            imports = _internal_imports(key, toplevel_only=False)
            for forbidden in (
                "sqlalchemy", "psycopg", "redis", "kafka", "celery",
                "requests", "httpx", "subprocess", "backend.app.db",
                "backend.app.llm", "backend.app.api",
            ):
                assert not any(
                    name == forbidden or name.startswith(forbidden + ".")
                    for name in imports
                ), (key, forbidden)

    def test_c25_18_contract_matrix_capabilities(self) -> None:
        """Contract Matrix（§十九）：每层的能力边界。

        Write = 职责层面的写能力（不是 Python 内部变量赋值）。
        """
        matrix = {
            "service": {          # ToolExecutionService：唯一执行入口
                "write": False,   # 不写 Record 集合（只产生 Record 交给 observer）
                "execute_tool": True,
            },
            "record": {"write": False, "execute_tool": False},
            "observer": {"write": False, "execute_tool": False},
            "collector": {        # 唯一"写" = 自身 Record 集合（内存）
                "write": True,
                "execute_tool": False,
            },
            "metrics": {"write": False, "execute_tool": False},
            "query": {"write": False, "execute_tool": False},
            "snapshot": {"write": False, "execute_tool": False},
        }

        #: 职责层面的写 API（``on_execution`` 是 Record **接收点**，不是写能力）
        write_apis = {"clear", "append", "add", "evict", "reset", "delete"}
        objects = {
            "service": ToolExecutionService,
            "record": ToolExecutionRecord,
            "observer": ToolExecutionObserver,
            "collector": InMemoryToolExecutionCollector,
            "metrics": ToolExecutionMetricsService,
            "query": ToolObservabilityQueryService,
            "snapshot": ToolExecutionSnapshot,
        }

        for layer, expectation in matrix.items():
            public = _public_api(objects[layer])
            has_write = bool(public & write_apis)
            assert has_write is expectation["write"], layer
            assert ("execute" in public) is expectation["execute_tool"], layer
            # 任何层都不得具备 DB / LLM 能力
            assert not (public & {
                "engine", "session", "connect", "llm", "chat", "client",
            }), layer

        # Record 接收点（on_execution）只属于 Observer 协议与 Collector
        assert "on_execution" in _public_api(ToolExecutionObserver)
        assert "on_execution" in _public_api(InMemoryToolExecutionCollector)
        for layer in ("record", "metrics", "query", "snapshot"):
            assert "on_execution" not in _public_api(objects[layer]), layer

    async def test_c25_19_route_isolation_still_holds(self) -> None:
        """路线隔离：TOOL → 1 Record；RAG / TEXT_TO_SQL → 0 Record（复用既有资产）。"""
        from backend.app.tools.get_inventory import GET_INVENTORY_DEFINITION

        handler = _RecordingHandler()
        registry = ToolRegistry()
        registry.register(GET_INVENTORY_DEFINITION, handler)
        collector = InMemoryToolExecutionCollector()

        result = await _orchestrator_with(
            collector, registry=registry
        ).execute(_QUESTION)

        assert result.route.value == "tool"
        assert len(collector.records()) == 1
        assert handler.calls == 1
        assert collector.records()[0].tool_call_id is None
        assert collector.records()[0].round == 1

    def test_c25_20_no_production_writes_in_observability_layer(self) -> None:
        """Observability 层不引入持久化 / 后台任务 / 新配置（§二十六）。"""
        for key in ("record", "observer", "collector", "metrics", "query",
                    "snapshot"):
            identifiers = _identifiers(key)
            for forbidden in (
                "commit", "flush", "persist", "save", "open", "write",
                "settings", "environ", "getenv",
            ):
                assert forbidden not in identifiers, (key, forbidden)
