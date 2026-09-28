"""AIOrchestrator Tool Execution Observability Integration（Phase 3.11 Step 18）。

锁定：AIOrchestrator 主链路 **TOOL 路径**接入既有
ToolExecutionContext + Observer / Collector / Metrics（全部为**装配**，
不新增执行能力、不修改任何 Step 13~17 组件）：

    AIOrchestrator.execute(question)
        ↓ new_request_id()（一次 execute 一个）
    Router（未修改）→ RouteType.TOOL → decision.tool_name
        ↓
    ToolExecutionContext（round=1 / project_id=边界作用域 / tool_call_id=None）
        ↓
    ToolExecutionService.execute(..., context=Context)（未修改）
        ↓
    ToolRegistry → Tool → ToolResult（未修改）
        ↓ ToolExecutionRecord（未修改）
    ToolExecutionObserver（注入；None = 旧行为）→ Collector（调用方持有）
        ↓
    ToolExecutionMetricsService.snapshot(collector.records())（未修改）

覆盖（§十九）：

    Context          1~5   （context 创建 / request_id / round / call id / project_id）
    Collector        6~10  （成功 / 失败 / RAG / Text-to-SQL / capability denied）
    Isolation        11~12 （两次 execute 不同 request_id；一次 execute ≤ 1 Record）
    Metrics          13~16 （成功 / 失败 / RAG 空 / Text-to-SQL 空）
    Failure isolate  17~20 （observer 失败不改 ToolResult / 结果 / 不 retry / 不 fallback）
    Security         21~25 （只收 Record；无 question / arguments / data / SQL / secret）
    附加：默认兼容（observer=None）、注入校验、装配边界（不创建 Collector）。

0 DB / 0 Network / 0 Real LLM / 0 Tool 真实 Handler（全部 Fake + Mock 边界）。
"""
from __future__ import annotations

import ast
import json
import os
from typing import Any

import pytest

from backend.app.projects.capabilities import ProjectCapabilities
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorCapabilityError,
    AIOrchestratorInputError,
    AIOrchestratorService,
)
from backend.app.services.in_memory_tool_execution_collector import (
    InMemoryToolExecutionCollector,
)
from backend.app.services.tool_execution_context import ToolExecutionContext
from backend.app.services.tool_execution_metrics_service import (
    ToolExecutionMetricsService,
)
from backend.app.services.tool_execution_record import ToolExecutionRecord
from backend.app.services.tool_execution_service import ToolExecutionService
from backend.app.services.text_to_sql_service import TextToSQLResult
from backend.app.services.sql_executor_service import SQLExecutionResult
from backend.app.tools.get_inventory import GET_INVENTORY_DEFINITION
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
    _FakeRagResponse,
    _rag_decision,
    _sql_decision,
    _tool_decision,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_ORCHESTRATOR_MODULE = "backend/app/services/ai_orchestrator_service.py"

_QUESTION = "查询物料 MAT-001 当前库存"
_RAG_QUESTION = "采购入库怎么操作？"
_T2S_QUESTION = "统计当前知识库文档数量"


# ============================================================
# 测试替身（本地；生产代码无这些实现）
# ============================================================

class _RecordingHandler:
    """记录 arguments 的 Tool Handler（0 DB）。"""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.calls = 0
        self.last_arguments: dict[str, Any] | None = None
        self._data = data if data is not None else {"qty": 250.0}

    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        self.last_arguments = dict(arguments)
        return dict(self._data)


class _RecordingExecution(ToolExecutionService):
    """真实执行边界 + context 记录（验证 Context 创建与透传）。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.contexts: list[ToolExecutionContext | None] = []

    async def execute(  # type: ignore[override]
        self,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        self.contexts.append(context)
        return await super().execute(
            tool_name, arguments=arguments, context=context
        )


class _RecordingObserver:
    """内存 observer（只接收 Record；不做统计 / 不聚合）。"""

    def __init__(self) -> None:
        self.records: list[ToolExecutionRecord] = []

    def on_execution(self, record: ToolExecutionRecord) -> None:
        self.records.append(record)


class _ExplodingObserver:
    """自身抛异常的 observer（验证 Step 15 失败隔离在 Orchestrator 层仍成立）。"""

    def __init__(self) -> None:
        self.calls = 0

    def on_execution(self, record: ToolExecutionRecord) -> None:
        self.calls += 1
        raise RuntimeError("observer boom")


class _CountingRouter:
    """记录 route() 调用次数（验证不 retry / 不 fallback）。"""

    def __init__(self, decision: Any) -> None:
        self._decision = decision
        self.calls = 0

    async def route(self, question: str, *, context: str | None = None):
        self.calls += 1
        return self._decision


class _ScopedProvider:
    """最小 ProjectContext provider（与 DefaultProjectContextProvider 同形状：
    暴露 ``_project_context``，供 Orchestrator 尽力读取授权作用域）。"""

    def __init__(self, project_id: str | None) -> None:
        self._project_context = (
            None if project_id is None else _project_context(project_id)
        )

    def resolve(self):  # TOOL 路径不调用；保留协议签名
        raise AssertionError("TOOL 路径不应解析 ProjectContext")


def _project_context(project_id: str):
    from backend.app.projects.context import DataSource, ProjectContext

    return ProjectContext(
        project_id=project_id,
        project_name=project_id,
        description=None,
        data_source=DataSource(name="primary", type="postgresql"),
    )


# ============================================================
# 装配 helper
# ============================================================

def _registry_with_recording_handler(
    handler: _RecordingHandler | None = None,
) -> tuple[ToolRegistry, _RecordingHandler]:
    handler = handler or _RecordingHandler()
    registry = ToolRegistry()
    registry.register(GET_INVENTORY_DEFINITION, handler)
    return registry, handler


def _orchestrator(
    *,
    router: Any,
    registry: ToolRegistry | None = None,
    rag: Any = None,
    t2s: Any = None,
    sql_executor: Any = None,
    table_selector: Any = None,
    context_composer: Any = None,
    project_provider: Any = None,
    execution: ToolExecutionService | None = None,
    observer: Any = None,
    capabilities: ProjectCapabilities | None = None,
) -> AIOrchestratorService:
    """构造 Orchestrator（全部下游注入 Fake / Mock；0 DB / 0 LLM）。"""
    return AIOrchestratorService(
        router=router,
        rag_service=rag if rag is not None else FakeRAG(),
        tool_registry=registry,
        text_to_sql=t2s if t2s is not None else FakeTextToSQL(),
        sql_executor=(
            sql_executor if sql_executor is not None else FakeSQLExecutor()
        ),
        table_selector=table_selector or FakeTableSelector(),
        context_composer=context_composer or FakeContextComposer(),
        project_context_provider=project_provider or FakeProjectProvider(),
        capabilities=capabilities,
        tool_execution_service=execution,
        tool_execution_observer=observer,
    )


def _t2s_result() -> TextToSQLResult:
    return TextToSQLResult(
        question=_T2S_QUESTION,
        sql="SELECT count(*) FROM public.knowledge_document LIMIT 1",
        validated=True,
        attempts=1,
        referenced_tables=("public.knowledge_document",),
    )


def _sql_execution_result() -> SQLExecutionResult:
    return SQLExecutionResult(
        columns=("count",),
        rows=((3,),),
        row_count=1,
        truncated=False,
        execution_time_ms=1.0,
    )


def _rag_orchestrator(**kwargs: Any) -> AIOrchestratorService:
    return _orchestrator(
        router=FakeRouter(_rag_decision()),
        rag=FakeRAG(response=_FakeRagResponse("流程说明")),
        **kwargs,
    )


def _t2s_orchestrator(**kwargs: Any) -> AIOrchestratorService:
    return _orchestrator(
        router=FakeRouter(_sql_decision()),
        t2s=FakeTextToSQL(result=_t2s_result()),
        sql_executor=FakeSQLExecutor(result=_sql_execution_result()),
        **kwargs,
    )


# ============================================================
# 1~7. Context（§四 / §五 / §六 / §十八）
# ============================================================

class TestToolPathContext:
    async def test_tool_path_creates_context(self) -> None:
        registry, _handler = _registry_with_recording_handler()
        execution = _RecordingExecution(registry=registry)

        result = await _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            execution=execution,
        ).execute(_QUESTION)

        assert result.route.value == "tool"
        assert len(execution.contexts) == 1
        assert isinstance(execution.contexts[0], ToolExecutionContext)

    async def test_request_id_exists(self) -> None:
        registry, _handler = _registry_with_recording_handler()
        execution = _RecordingExecution(registry=registry)

        await _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            execution=execution,
        ).execute(_QUESTION)

        context = execution.contexts[0]
        assert isinstance(context, ToolExecutionContext)
        assert isinstance(context.request_id, str)
        assert context.request_id.strip()

    async def test_round_is_one(self) -> None:
        registry, _handler = _registry_with_recording_handler()
        execution = _RecordingExecution(registry=registry)

        await _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            execution=execution,
        ).execute(_QUESTION)

        assert execution.contexts[0].round == 1

    async def test_tool_call_id_is_none(self) -> None:
        """本链路不是 Function Calling round → 不伪造 call id。"""
        registry, _handler = _registry_with_recording_handler()
        execution = _RecordingExecution(registry=registry)

        await _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            execution=execution,
        ).execute(_QUESTION)

        assert execution.contexts[0].tool_call_id is None

    async def test_project_id_from_execution_scope(self) -> None:
        """project_id = 执行边界（服务器端）授权作用域。"""
        registry, _handler = _registry_with_recording_handler()
        execution = _RecordingExecution(registry=registry, project_id="project-a")

        await _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            execution=execution,
        ).execute(_QUESTION)

        assert execution.contexts[0].project_id == "project-a"

    async def test_project_id_propagated_from_server_side_provider(self) -> None:
        """默认边界：作用域来自服务器端 ProjectContext（非请求文本）。"""
        registry, _handler = _registry_with_recording_handler()
        collector = InMemoryToolExecutionCollector()

        await _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            project_provider=_ScopedProvider("project-a"),
            observer=collector,
        ).execute(_QUESTION)

        records = collector.records()
        assert len(records) == 1
        assert records[0].project_id == "project-a"

    async def test_project_id_not_derived_from_question_text(self) -> None:
        """问题文本提到别的 project id / 其它业务标识 → 作用域不变。"""
        registry, _handler = _registry_with_recording_handler()
        execution = _RecordingExecution(registry=registry, project_id="project-a")

        await _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            execution=execution,
        ).execute("查询 project-b 的物料 MAT-001 当前库存")

        assert execution.contexts[0].project_id == "project-a"
        assert "project-b" not in repr(execution.contexts[0])

    async def test_context_not_leaked_into_arguments_or_metadata(self) -> None:
        """Context 不进入 Tool arguments；metadata 只带 trace id（Step 35）。

        Phase 3.11 Step 18 原始契约：metadata 不含 request_id。
        Phase 3.12 Step 35：request_id **有意**进入 metadata（Assistant
        Trace Contract）；仍不允许出现 project_id / arguments / 执行细节。
        """
        registry, handler = _registry_with_recording_handler()
        execution = _RecordingExecution(registry=registry, project_id="project-a")

        result = await _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            execution=execution,
        ).execute(_QUESTION)

        assert handler.last_arguments == {"material_code": "MAT-001"}
        assert set(result.metadata) == {
            "decision_source", "route_reason", "tool_name", "tool_success",
            "request_id",              # Step 35：Assistant Trace ID
        }
        context = execution.contexts[0]
        # 唯一关联字段：metadata.request_id == ToolExecutionRecord.request_id
        assert result.metadata["request_id"] == context.request_id
        # 作用域 / 参数 / 结果数据仍不得出现在 metadata
        assert context.project_id not in json.dumps(result.metadata)
        assert "MAT-001" not in json.dumps(result.metadata)
        assert "arguments" not in result.metadata


# ============================================================
# 8~14. Collector（§十 / §十一 / §十二 / §十三 / §十四）
# ============================================================

class TestCollector:
    async def test_successful_tool_produces_one_record(self) -> None:
        registry, handler = _registry_with_recording_handler()
        collector = InMemoryToolExecutionCollector()

        result = await _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            observer=collector,
        ).execute(_QUESTION)

        records = collector.records()
        assert len(records) == 1
        record = records[0]
        assert record.assert_field_whitelist() is None
        assert record.tool_name == "get_inventory"
        assert record.round == 1
        assert record.tool_call_id is None
        assert record.success is True
        assert record.error_code is None and record.error_type is None
        assert result.metadata["tool_success"] is True
        assert handler.calls == 1

    async def test_failed_tool_produces_one_failure_record(self) -> None:
        """ToolResult(False)（未注册 Tool）→ 1 条 failure Record（不 retry）。"""
        registry = ToolRegistry()          # 空 registry → Tool 未注册
        collector = InMemoryToolExecutionCollector()

        result = await _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            observer=collector,
        ).execute(_QUESTION)

        records = collector.records()
        assert len(records) == 1
        assert records[0].success is False
        assert result.metadata["tool_success"] is False

    async def test_rag_route_produces_zero_records(self) -> None:
        collector = InMemoryToolExecutionCollector()
        rag = FakeRAG(response=type("_R", (), {"answer": "ok"})())

        result = await _orchestrator(
            router=FakeRouter(_rag_decision()),
            rag=rag,
            observer=collector,
        ).execute(_RAG_QUESTION)

        assert result.route.value == "rag"
        assert collector.records() == ()

    async def test_text_to_sql_route_produces_zero_records(self) -> None:
        collector = InMemoryToolExecutionCollector()

        result = await _t2s_orchestrator(observer=collector).execute(
            _T2S_QUESTION
        )

        assert result.route.value == "text_to_sql"
        assert collector.records() == ()

    async def test_capability_denied_produces_zero_records(self) -> None:
        registry, handler = _registry_with_recording_handler()
        collector = InMemoryToolExecutionCollector()

        with pytest.raises(AIOrchestratorCapabilityError):
            await _orchestrator(
                router=FakeRouter(_tool_decision()),
                registry=registry,
                observer=collector,
                capabilities=ProjectCapabilities(tool_names=()),
            ).execute(_QUESTION)

        assert collector.records() == ()   # 拒绝发生在执行之前
        assert handler.calls == 0

    async def test_no_observer_keeps_default_behavior(self) -> None:
        """observer=None → 不产生 Record，执行结果与旧行为一致。"""
        registry, handler = _registry_with_recording_handler()
        orchestrator = _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
        )

        result = await orchestrator.execute(_QUESTION)

        assert orchestrator.tool_execution_observer is None
        assert result.metadata["tool_success"] is True
        assert handler.calls == 1
        assert result.data.success is True

    async def test_observer_can_be_any_protocol_implementation(self) -> None:
        registry, _handler = _registry_with_recording_handler()
        observer = _RecordingObserver()

        await _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            observer=observer,
        ).execute(_QUESTION)

        assert len(observer.records) == 1
        assert isinstance(observer.records[0], ToolExecutionRecord)


# ============================================================
# 15~18. Request isolation（§十七）
# ============================================================

class TestRequestIsolation:
    async def test_two_executes_have_different_request_ids(self) -> None:
        registry, _handler = _registry_with_recording_handler()
        collector = InMemoryToolExecutionCollector()
        orchestrator = _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            observer=collector,
        )

        await orchestrator.execute(_QUESTION)
        await orchestrator.execute(_QUESTION)

        records = collector.records()
        assert len(records) == 2
        assert records[0].request_id != records[1].request_id

    async def test_one_execute_produces_at_most_one_record(self) -> None:
        registry, _handler = _registry_with_recording_handler()
        collector = InMemoryToolExecutionCollector()
        execution = _RecordingExecution(registry=registry, observer=collector)

        await _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            execution=execution,
        ).execute(_QUESTION)

        context = execution.contexts[0]
        records = collector.records()
        assert len(records) == 1                       # <= 1（当前 contract）
        assert records[0].request_id == context.request_id
        assert len(collector.records_by_request_id(context.request_id)) == 1

    async def test_record_request_id_matches_execute_request_id(self) -> None:
        registry, _handler = _registry_with_recording_handler()
        collector = InMemoryToolExecutionCollector()
        execution = _RecordingExecution(
            registry=registry, observer=collector
        )

        await _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            execution=execution,
        ).execute(_QUESTION)

        context = execution.contexts[0]
        records = collector.records()
        assert records[0].request_id == context.request_id


# ============================================================
# 19~22. Metrics（§十六；Read Model 未修改）
# ============================================================

class TestMetrics:
    async def test_success_metrics(self) -> None:
        registry, _handler = _registry_with_recording_handler()
        collector = InMemoryToolExecutionCollector()

        await _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            observer=collector,
        ).execute(_QUESTION)

        snapshot = ToolExecutionMetricsService.snapshot(collector.records())
        assert snapshot.total_count == 1
        assert snapshot.success_count == 1
        assert snapshot.failure_count == 0
        assert snapshot.success_rate == 1.0

    async def test_failure_metrics(self) -> None:
        collector = InMemoryToolExecutionCollector()

        await _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=ToolRegistry(),
            observer=collector,
        ).execute(_QUESTION)

        snapshot = ToolExecutionMetricsService.snapshot(collector.records())
        assert snapshot.total_count == 1
        assert snapshot.success_count == 0
        assert snapshot.failure_count == 1
        assert snapshot.failure_rate == 1.0

    async def test_rag_metrics_empty(self) -> None:
        collector = InMemoryToolExecutionCollector()

        await _rag_orchestrator(observer=collector).execute(_RAG_QUESTION)

        snapshot = ToolExecutionMetricsService.snapshot(collector.records())
        assert snapshot.total_count == 0
        assert snapshot.success_rate is None
        assert snapshot.average_duration_ms is None

    async def test_text_to_sql_metrics_empty(self) -> None:
        collector = InMemoryToolExecutionCollector()

        await _t2s_orchestrator(observer=collector).execute(_T2S_QUESTION)

        snapshot = ToolExecutionMetricsService.snapshot(collector.records())
        assert snapshot.total_count == 0
        assert snapshot.failure_rate is None
        assert snapshot.max_duration_ms is None


# ============================================================
# 23~27. Observer failure isolation（§十五）
# ============================================================

class TestObserverFailureIsolation:
    def _orchestrator_with_exploding_observer(
        self,
    ) -> tuple[AIOrchestratorService, _CountingRouter, _RecordingHandler,
               _ExplodingObserver, FakeRAG]:
        registry, handler = _registry_with_recording_handler()
        router = _CountingRouter(_tool_decision())
        observer = _ExplodingObserver()
        rag = FakeRAG()
        orchestrator = _orchestrator(
            router=router,
            registry=registry,
            rag=rag,
            observer=observer,
        )
        return orchestrator, router, handler, observer, rag

    async def test_observer_failure_does_not_alter_tool_result(self) -> None:
        orchestrator, _router, handler, observer, _rag = (
            self._orchestrator_with_exploding_observer()
        )

        result = await orchestrator.execute(_QUESTION)

        assert result.metadata["tool_success"] is True
        assert result.data.success is True
        assert result.data.data == {"qty": 250.0}   # Handler 返回值未被改写
        assert observer.calls == 1
        assert handler.calls == 1

    async def test_observer_failure_does_not_alter_orchestration_result(
        self,
    ) -> None:
        orchestrator, _router, _handler, _observer, _rag = (
            self._orchestrator_with_exploding_observer()
        )

        result = await orchestrator.execute(_QUESTION)

        assert result.route.value == "tool"
        assert result.content is not None
        assert "失败" not in (result.content or "")

    async def test_observer_failure_does_not_trigger_retry(self) -> None:
        orchestrator, router, handler, _observer, _rag = (
            self._orchestrator_with_exploding_observer()
        )

        await orchestrator.execute(_QUESTION)

        assert handler.calls == 1       # Tool 恰好执行一次（无 retry / 无重跑）
        assert router.calls == 1        # Router 恰好调用一次（无重新路由）

    async def test_observer_failure_does_not_trigger_fallback(self) -> None:
        orchestrator, _router, _handler, _observer, rag = (
            self._orchestrator_with_exploding_observer()
        )

        result = await orchestrator.execute(_QUESTION)

        assert result.route.value == "tool"     # 不 fallback 到 RAG
        assert rag.calls == []
        assert result.data.success is True

    async def test_observer_failure_not_converted_into_tool_failure(self) -> None:
        """observer 异常不冒泡、不变成 Tool 失败（边界隔离在 Orchestrator 层可见）。"""
        registry, _handler = _registry_with_recording_handler()
        boundary = ToolExecutionService(
            registry=registry, observer=_ExplodingObserver()
        )

        result = await boundary.execute(
            "get_inventory", arguments={"material_code": "MAT-001"}
        )

        assert result.success is True
        assert result.data == {"qty": 250.0}

    async def test_observer_shape_is_validated(self) -> None:
        registry, _handler = _registry_with_recording_handler()

        with pytest.raises(AIOrchestratorInputError):
            _orchestrator(
                router=FakeRouter(_tool_decision()),
                registry=registry,
                observer="not-an-observer",
            )


# ============================================================
# 28~33. Security（§十九 Security）
# ============================================================

class TestSecurity:
    async def test_collector_receives_only_records(self) -> None:
        registry, _handler = _registry_with_recording_handler()
        collector = InMemoryToolExecutionCollector()

        await _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            observer=collector,
        ).execute(_QUESTION)

        for record in collector.records():
            assert isinstance(record, ToolExecutionRecord)
            record.assert_field_whitelist()

    async def test_record_has_no_question_arguments_or_result_data(self) -> None:
        registry, _handler = _registry_with_recording_handler(
            _RecordingHandler({"qty": 250.0, "marker": "DATA-LEAK-MARKER"})
        )
        collector = InMemoryToolExecutionCollector()

        await _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            observer=collector,
        ).execute("查询物料 MAT-SECRET-001 当前库存")

        record = collector.records()[0]
        text = repr(record)
        for secret in (
            "MAT-SECRET-001",       # arguments / question 内容
            "查询物料",              # question 文本
            "DATA-LEAK-MARKER",     # ToolResult.data 值
            "qty",                  # ToolResult.data 键
            "material_code",        # 业务字段名
        ):
            assert secret not in text, secret

    async def test_record_has_no_sql_or_credentials(self) -> None:
        registry, _handler = _registry_with_recording_handler()
        collector = InMemoryToolExecutionCollector()

        await _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            observer=collector,
        ).execute(_QUESTION)

        text = repr(collector.records()[0]).lower()
        for forbidden in (
            "select", "insert", "update", "delete", "password",
            "postgres", "localhost", "database_url", "authorization",
            "api_key", "traceback", "runtimeerror",
        ):
            assert forbidden not in text, forbidden

    async def test_observer_gets_no_capability_error(self) -> None:
        registry, _handler = _registry_with_recording_handler()
        collector = InMemoryToolExecutionCollector()

        with pytest.raises(AIOrchestratorCapabilityError):
            await _orchestrator(
                router=FakeRouter(_tool_decision()),
                registry=registry,
                observer=collector,
                capabilities=ProjectCapabilities(tool_names=()),
            ).execute(_QUESTION)

        assert collector.records() == ()

    def test_orchestrator_does_not_create_collector(self) -> None:
        registry, _handler = _registry_with_recording_handler()
        collector = InMemoryToolExecutionCollector()

        orchestrator = _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            observer=collector,
        )

        assert orchestrator.tool_execution_observer is collector

    def test_observer_and_execution_service_conflict_rejected(self) -> None:
        """两个来源同时提供 → 显式拒绝（不静默失效）。"""
        registry, _handler = _registry_with_recording_handler()
        boundary = ToolExecutionService(registry=registry)
        collector = InMemoryToolExecutionCollector()

        with pytest.raises(AIOrchestratorInputError):
            _orchestrator(
                router=FakeRouter(_tool_decision()),
                registry=registry,
                execution=boundary,
                observer=collector,
            )

    def test_same_observer_on_both_is_allowed(self) -> None:
        registry, _handler = _registry_with_recording_handler()
        collector = InMemoryToolExecutionCollector()
        boundary = ToolExecutionService(registry=registry, observer=collector)

        orchestrator = _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            execution=boundary,
            observer=collector,
        )

        assert orchestrator.tool_execution_observer is collector


# ============================================================
# 34~37. 静态边界 / 默认兼容（C20 支撑；细节见架构契约）
# ============================================================

class TestStaticBoundaries:
    def test_orchestrator_module_has_no_collector_or_metrics(self) -> None:
        with open(
            os.path.join(REPO_ROOT, *_ORCHESTRATOR_MODULE.split("/")),
            encoding="utf-8",
        ) as handle:
            source = handle.read()
        assert "InMemoryToolExecutionCollector" not in source
        assert "Collector()" not in source
        assert "tool_execution_metrics_service" not in source
        assert "ToolExecutionMetrics" not in source

    def test_no_new_execution_or_persistence_imports(self) -> None:
        with open(
            os.path.join(REPO_ROOT, *_ORCHESTRATOR_MODULE.split("/")),
            encoding="utf-8",
        ) as handle:
            tree = ast.parse(handle.read())
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        for forbidden in (
            "sqlalchemy", "psycopg", "backend.app.db", "backend.app.api",
            "redis", "kafka", "celery", "opentelemetry", "httpx",
            "backend.app.services.tool_execution_metrics_service",
            "backend.app.services.in_memory_tool_execution_collector",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden

    def test_request_id_created_once_per_execute(self) -> None:
        """``new_request_id()`` 只在 execute() 顶层调用一次（无循环 / 无 retry）。"""
        with open(
            os.path.join(REPO_ROOT, *_ORCHESTRATOR_MODULE.split("/")),
            encoding="utf-8",
        ) as handle:
            tree = ast.parse(handle.read())
        callers: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(
                node.func, ast.Name
            ) and node.func.id == "new_request_id":
                callers.append(node.func.id)
        assert len(callers) == 1          # 唯一调用点
        # 该调用位于 AIOrchestratorService.execute() 内（而非 _run_tool / 循环体）
        service_cls = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "AIOrchestratorService"
        )
        execute_fn = next(
            node
            for node in service_cls.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "execute"
        )
        assert any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "new_request_id"
            for node in ast.walk(execute_fn)
        )

    def test_context_construction_is_single_and_literal(self) -> None:
        """ToolExecutionContext 只在一处构造（round=1 / tool_call_id=None）。"""
        with open(
            os.path.join(REPO_ROOT, *_ORCHESTRATOR_MODULE.split("/")),
            encoding="utf-8",
        ) as handle:
            source = handle.read()
        assert source.count("ToolExecutionContext(") == 1
        assert "round=1" in source
        assert "tool_call_id=None" in source
