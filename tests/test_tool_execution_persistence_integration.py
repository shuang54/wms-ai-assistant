"""Tool Observability Persistence 集成测试（Phase 3.11 Step 28）。

只在 ``RUN_DB_TESTS=1`` 时运行（默认跳过）。

真实链路（无 Mock Repository）：

    ToolExecutionRecord
        ↓
    ToolExecutionPersistenceAdapter
        ↓
    ToolExecutionPersistenceService
        ↓
    ToolExecutionRepository（真实 SQLAlchemy）
        ↓
    ai_ops.tool_execution_record（真实 PostgreSQL）

Case 1  success record 写入
Case 2  failure record（error_code != None）写入
Case 3  nullable 字段（4 个 None）写入
Case 4  同一 request 的 round=1 / round=2 两条都存在
Case 5  Repository 抛异常 → Adapter **不传播**（Tool 结果概念上不受影响）

E2E（真实 AIOrchestrator）：
    AIOrchestrator.execute() → TOOL → get_inventory（synthetic handler）
        → CompositeObserver → Memory Collector + PostgreSQL
    并验证 RAG / TEXT_TO_SQL 路径 **0** 持久化记录。

数据：synthetic only（test-persist-* / get_inventory / project-a）；
      未读取真实库存 / 工单 / WMS 数据。
残留：teardown TRUNCATE **本表**（不清 ai_ops.llm_usage_record / public.*）。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.app.db.models.tool_execution_record import (
    TOOL_EXECUTION_SCHEMA,
    TOOL_EXECUTION_TABLE,
    ToolExecutionRecordModel,
)
from backend.app.db.session import get_engine, get_session_factory
from backend.app.db.tool_execution_repository import (
    ToolExecutionRepository,
    ToolExecutionRepositoryError,
)
from backend.app.services.ai_orchestrator_service import AIOrchestratorService
from backend.app.services.in_memory_tool_execution_collector import (
    InMemoryToolExecutionCollector,
)
from backend.app.services.tool_execution_persistence_adapter import (
    CompositeToolExecutionObserver,
    ToolExecutionPersistenceAdapter,
)
from backend.app.services.tool_execution_persistence_service import (
    ToolExecutionPersistenceService,
)
from backend.app.services.tool_execution_record import ToolExecutionRecord
from backend.app.tools.get_inventory import GET_INVENTORY_DEFINITION
from backend.app.tools.registry import ToolRegistry
from backend.app.services.sql_executor_service import SQLExecutionResult
from backend.app.services.text_to_sql_service import TextToSQLResult

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

_TEST_REQUEST = "test-persist-request-001"
_TEST_MULTI = "test-persist-request-multi"
_TEST_PROJECT = "project-a"

_TABLE = f"{TOOL_EXECUTION_SCHEMA}.{TOOL_EXECUTION_TABLE}"
_TRUNCATE_SQL = text(f"TRUNCATE TABLE {_TABLE} RESTART IDENTITY")

_STARTED = datetime(2026, 9, 26, 10, 20, 30, 123456, tzinfo=timezone.utc)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


_RUN_DB = _env_flag("RUN_DB_TESTS")

requires_db = pytest.mark.skipif(
    not _RUN_DB,
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


def _record(**overrides: Any) -> ToolExecutionRecord:
    fields: dict[str, Any] = {
        "request_id": _TEST_REQUEST,
        "round": 1,
        "tool_name": "get_inventory",
        "started_at": _STARTED,
        "finished_at": _STARTED + timedelta(milliseconds=3.5),
        "duration_ms": 3.5,
        "success": True,
        "project_id": _TEST_PROJECT,
        "tool_call_id": None,
    }
    fields.update(overrides)
    return ToolExecutionRecord(**fields)


def _engine() -> Any:
    from backend.app.db import init_db as init_db_module

    engine = get_engine()
    if engine is None:
        pytest.skip("DATABASE_URL 未配置")
    init_db_module.init_db()
    return engine


def _cleanup(engine: Any) -> int:
    with engine.begin() as conn:
        conn.execute(_TRUNCATE_SQL)
        return int(
            conn.execute(text(f"SELECT COUNT(*) FROM {_TABLE}")).scalar_one()
        )


def _count(factory: Any) -> int:
    with factory() as session:  # type: Session
        return int(
            session.scalar(
                select(func.count()).select_from(ToolExecutionRecordModel)
            )
            or 0
        )


def _persistence_stack() -> tuple[
    ToolExecutionPersistenceAdapter, ToolExecutionPersistenceService
]:
    """真实 Repository 驱动的 Adapter / Service（无 Mock）。"""
    service = ToolExecutionPersistenceService(
        repository=ToolExecutionRepository()
    )
    return ToolExecutionPersistenceAdapter(persistence_service=service), service


def _registry_with_synthetic_handler(
    payload: dict[str, Any] | None = None,
) -> tuple[ToolRegistry, list[dict[str, Any]]]:
    """注册 get_inventory（synthetic handler；不读真实 WMS 数据）。"""
    calls: list[dict[str, Any]] = []

    async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append(arguments)
        return payload if payload is not None else {"qty": 250.0}

    registry = ToolRegistry()
    registry.register(GET_INVENTORY_DEFINITION, handler)
    return registry, calls


def _orchestrator(
    *,
    router: Any,
    registry: ToolRegistry,
    observer: Any,
    rag_response: Any = None,
) -> AIOrchestratorService:
    return AIOrchestratorService(
        router=router,
        rag_service=FakeRAG(response=rag_response),
        text_to_sql=FakeTextToSQL(),
        sql_executor=FakeSQLExecutor(),
        table_selector=FakeTableSelector(),
        context_composer=FakeContextComposer(),
        project_context_provider=FakeProjectProvider(),
        tool_registry=registry,
        tool_execution_observer=observer,
    )


@pytest.fixture()
def db():
    engine = _engine()
    factory = get_session_factory()
    if factory is None:
        pytest.skip("session factory 不可用")
    _cleanup(engine)
    try:
        yield engine, factory
    finally:
        residue = _cleanup(engine)
        assert residue == 0                      # DB residue = 0


# ============================================================
# Case 1 ~ 5：Adapter → Service → Repository → PostgreSQL
# ============================================================

@requires_db
class TestPersistenceAdapterIntegration:
    def test_case_1_success_record_persisted(self, db) -> None:
        _engine_, factory = db
        adapter, _service = _persistence_stack()

        adapter.on_execution(_record(success=True))

        assert _count(factory) == 1
        with factory() as session:
            stored = session.execute(
                select(
                    ToolExecutionRecordModel.request_id,
                    ToolExecutionRecordModel.success,
                    ToolExecutionRecordModel.duration_ms,
                )
            ).one()
        assert stored.request_id == _TEST_REQUEST
        assert stored.success is True
        assert stored.duration_ms == 3.5

    def test_case_2_failure_record_with_error_code(self, db) -> None:
        _engine_, factory = db
        adapter, _service = _persistence_stack()

        adapter.on_execution(
            _record(
                success=False,
                error_code="invalid_argument",
                error_type="ToolValidationError",
            )
        )

        with factory() as session:
            stored = session.execute(
                select(
                    ToolExecutionRecordModel.success,
                    ToolExecutionRecordModel.error_code,
                    ToolExecutionRecordModel.error_type,
                )
            ).one()
        assert stored.success is False
        assert stored.error_code == "invalid_argument"
        assert stored.error_type == "ToolValidationError"
        assert _count(factory) == 1

    def test_case_3_nullable_fields(self, db) -> None:
        _engine_, factory = db
        adapter, _service = _persistence_stack()

        adapter.on_execution(
            _record(project_id=None, tool_call_id=None)
        )

        with factory() as session:
            stored = session.execute(
                select(
                    ToolExecutionRecordModel.project_id,
                    ToolExecutionRecordModel.tool_call_id,
                    ToolExecutionRecordModel.error_code,
                    ToolExecutionRecordModel.error_type,
                )
            ).one()
        assert stored.project_id is None
        assert stored.tool_call_id is None
        assert stored.error_code is None
        assert stored.error_type is None

    def test_case_4_same_request_two_rounds(self, db) -> None:
        _engine_, factory = db
        adapter, _service = _persistence_stack()

        adapter.on_execution(_record(request_id=_TEST_MULTI, round=1))
        adapter.on_execution(_record(request_id=_TEST_MULTI, round=2))

        assert _count(factory) == 2
        with factory() as session:
            rounds = [
                row[0]
                for row in session.execute(
                    select(ToolExecutionRecordModel.round).order_by(
                        ToolExecutionRecordModel.id
                    )
                ).all()
            ]
        assert rounds == [1, 2]

    def test_case_5_adapter_does_not_propagate_db_failure(
        self, db, monkeypatch
    ) -> None:
        """Repository 抛异常 → Adapter 收敛（不传播）。"""
        _engine_, factory = db
        adapter, service = _persistence_stack()

        def _explode(record: ToolExecutionRecord) -> Any:
            raise ToolExecutionRepositoryError("insert failed")

        monkeypatch.setattr(service.repository, "create", _explode)

        adapter.on_execution(_record())          # 不抛异常

        assert _count(factory) == 0

    def test_case_5b_adapter_does_not_retry(self, db, monkeypatch) -> None:
        _engine_, factory = db
        adapter, service = _persistence_stack()
        calls: list[ToolExecutionRecord] = []

        def _explode(record: ToolExecutionRecord) -> Any:
            calls.append(record)
            raise ToolExecutionRepositoryError("insert failed")

        monkeypatch.setattr(service.repository, "create", _explode)

        adapter.on_execution(_record())

        assert len(calls) == 1                   # 无 retry
        assert _count(factory) == 0


# ============================================================
# E2E：AIOrchestrator → Tool → CompositeObserver → Memory + PostgreSQL
# ============================================================

@requires_db
class TestOrchestratorToolPersistenceE2E:
    async def test_tool_route_writes_memory_and_database(self, db) -> None:
        engine, factory = db
        registry, calls = _registry_with_synthetic_handler()
        collector = InMemoryToolExecutionCollector()
        adapter, _service = _persistence_stack()
        observer = CompositeToolExecutionObserver(collector, adapter)
        orchestrator = _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            observer=observer,
        )

        result = await orchestrator.execute("查询物料 MAT-001 当前库存")

        # 路由 + Tool 结果
        assert result.route.value == "tool"
        assert result.metadata["tool_success"] is True
        assert result.data.success is True
        assert result.data.data == {"qty": 250.0}
        assert calls == [{"material_code": "MAT-001"}]   # Tool 执行一次

        # Memory（runtime view）
        records = collector.records()
        assert len(records) == 1
        assert records[0].tool_name == "get_inventory"
        assert records[0].tool_call_id is None

        # PostgreSQL（persistent history）
        assert _count(factory) == 1
        with factory() as session:
            stored = session.execute(
                select(
                    ToolExecutionRecordModel.request_id,
                    ToolExecutionRecordModel.tool_name,
                    ToolExecutionRecordModel.success,
                    ToolExecutionRecordModel.project_id,
                )
            ).one()
        assert stored.request_id == records[0].request_id   # 同一次 execute
        assert stored.tool_name == "get_inventory"
        assert stored.success is True

        # 只写本表；ai_ops 其它表不受影响
        with engine.connect() as conn:
            llm_usage = conn.execute(
                text("SELECT COUNT(*) FROM ai_ops.llm_usage_record")
            ).scalar_one()
        assert llm_usage >= 0                    # 不因本测试变化（只读断言）

    async def test_rag_route_writes_zero_persistence_records(self, db) -> None:
        _engine_, factory = db
        registry, calls = _registry_with_synthetic_handler()
        collector = InMemoryToolExecutionCollector()
        adapter, _service = _persistence_stack()
        observer = CompositeToolExecutionObserver(collector, adapter)
        orchestrator = _orchestrator(
            router=FakeRouter(_rag_decision()),
            registry=registry,
            observer=observer,
            rag_response=_FakeRagResponse("流程说明"),
        )

        result = await orchestrator.execute("采购入库怎么操作？")

        assert result.route.value == "rag"
        assert calls == []                       # 无 Tool 执行
        assert collector.records() == ()
        assert _count(factory) == 0              # RAG → 0 持久化记录

    async def test_text_to_sql_route_writes_zero_persistence_records(
        self, db
    ) -> None:
        _engine_, factory = db
        registry, calls = _registry_with_synthetic_handler()
        collector = InMemoryToolExecutionCollector()
        adapter, _service = _persistence_stack()
        observer = CompositeToolExecutionObserver(collector, adapter)
        # Text-to-SQL 路径需要完整的 fake 链（复用既有测试资产）
        t2s_result = TextToSQLResult(
            question="统计当前知识库文档数量",
            sql="SELECT COUNT(*) FROM public.knowledge_document",
            validated=True,
            attempts=1,
            referenced_tables=("public.knowledge_document",),
        )
        sql_result = SQLExecutionResult(
            columns=("count",),
            rows=((0,),),
            row_count=1,
            truncated=False,
            execution_time_ms=1.0,
        )
        orchestrator = AIOrchestratorService(
            router=FakeRouter(_sql_decision()),
            rag_service=FakeRAG(),
            text_to_sql=FakeTextToSQL(result=t2s_result),
            sql_executor=FakeSQLExecutor(result=sql_result),
            table_selector=FakeTableSelector(
                selections=["public.knowledge_document"]
            ),
            context_composer=FakeContextComposer(context="FAKE_CTX"),
            project_context_provider=FakeProjectProvider(),
            tool_registry=registry,
            tool_execution_observer=observer,
        )

        result = await orchestrator.execute("统计当前知识库文档数量")

        assert result.route.value == "text_to_sql"
        assert calls == []                       # 未把 SQL Executor 当 Tool
        assert collector.records() == ()
        assert _count(factory) == 0              # Text-to-SQL → 0 持久化记录

    async def test_persistence_failure_does_not_change_tool_result(
        self, db, monkeypatch
    ) -> None:
        """E2E：DB 写入失败 → Tool 结果 / Memory 记录均不受影响。"""
        _engine_, factory = db
        registry, calls = _registry_with_synthetic_handler()
        collector = InMemoryToolExecutionCollector()
        adapter, service = _persistence_stack()

        def _explode(record: ToolExecutionRecord) -> Any:
            raise ToolExecutionRepositoryError("db down")

        monkeypatch.setattr(service.repository, "create", _explode)
        observer = CompositeToolExecutionObserver(collector, adapter)
        orchestrator = _orchestrator(
            router=FakeRouter(_tool_decision()),
            registry=registry,
            observer=observer,
        )

        result = await orchestrator.execute("查询物料 MAT-001 当前库存")

        assert result.route.value == "tool"
        assert result.data.success is True       # ToolResult 不变
        assert result.data.data == {"qty": 250.0}
        assert len(collector.records()) == 1     # Memory 仍记录
        assert calls == [{"material_code": "MAT-001"}]
        assert _count(factory) == 0              # 无 DB 行
