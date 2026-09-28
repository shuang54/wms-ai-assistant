"""Tool Observability Persistence Boundary —— 架构契约（Phase 3.11.26）。

Step 26 = Survey & Design：**不实现**持久化。本文件只保护**当前边界**，
并为未来 Persistence 实现提供不可回退的约束基线。

    C28  ToolExecutionService：不能 import SQLAlchemy / Repository / DB Session
    C29  Tool Handler：不能 import Tool Observability Persistence
    C30  Collector：不能 import SQLAlchemy / Repository / DB Session
    C31  QueryService：不能执行数据库写操作
    C32  Observability API：不能直接 import Repository / SQLAlchemy

附加（Survey 事实锁定）：

    * LLM Usage 的 "Sink → Service → Repository" 分层存在（设计参照）
    * 当前**不存在** Tool Execution ORM Model / Persistence Adapter /
      migration 目录（Step 26 未实现任何持久化）
    * 方向：Execution → Observability（**不是** Execution → Database）

0 DB / 0 Network / 0 LLM：全部静态 AST + 少量类型断言。
"""
from __future__ import annotations

import ast
import dataclasses
import os

import pytest

from backend.app.db.tool_execution_repository import (
    TOOL_EXECUTION_READ_COLUMNS,
)
from backend.app.services.in_memory_tool_execution_collector import (
    InMemoryToolExecutionCollector,
)
from backend.app.services.tool_observability_query_service import (
    ToolObservabilityQueryService,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_MODULES = {
    "execution_service": "backend/app/services/tool_execution_service.py",
    "record": "backend/app/services/tool_execution_record.py",
    "observer": "backend/app/services/tool_execution_observer.py",
    "collector": "backend/app/services/in_memory_tool_execution_collector.py",
    "query": "backend/app/services/tool_observability_query_service.py",
    "snapshot": "backend/app/services/tool_observability_snapshot.py",
    "metrics": "backend/app/services/tool_execution_metrics_service.py",
    "serialization": (
        "backend/app/services/tool_observability_serialization.py"
    ),
    "api": "backend/app/api/tool_observability.py",
    "usage_repository": "backend/app/db/llm_usage_repository.py",
    "usage_persistence_service": (
        "backend/app/services/llm_usage_persistence_service.py"
    ),
    # Step 27：Persistence 底座（ORM Model + Repository）
    "tool_model": "backend/app/db/models/tool_execution_record.py",
    "tool_repository": "backend/app/db/tool_execution_repository.py",
    # Step 28：Persistence Adapter / Service
    "persistence_adapter": (
        "backend/app/services/tool_execution_persistence_adapter.py"
    ),
    "persistence_service": (
        "backend/app/services/tool_execution_persistence_service.py"
    ),
    "root": "backend/app/api/orchestrator_chat.py",
    # Step 29：Persistent Query Boundary
    "persistent_query": (
        "backend/app/services/tool_execution_persistent_query_service.py"
    ),
}

_TOOLS_DIR = "backend/app/tools"

#: 持久化相关依赖（Observability 各层均不得出现）
_DB_MARKERS = (
    "sqlalchemy",
    "psycopg",
    "backend.app.db",
    "backend.app.repositories",
    "repository",
)


def _path(key_or_path: str) -> str:
    relative = _MODULES.get(key_or_path, key_or_path)
    return os.path.join(REPO_ROOT, *relative.split("/"))


def _tree(key_or_path: str) -> ast.Module:
    with open(_path(key_or_path), encoding="utf-8") as handle:
        return ast.parse(handle.read())


def _source(key_or_path: str) -> str:
    with open(_path(key_or_path), encoding="utf-8") as handle:
        return handle.read()


def _imports(key_or_path: str) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(_tree(key_or_path)):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def _identifiers(key_or_path: str) -> set[str]:
    return {
        node.id.lower()
        for node in ast.walk(_tree(key_or_path))
        if isinstance(node, ast.Name)
    } | {
        node.attr.lower()
        for node in ast.walk(_tree(key_or_path))
        if isinstance(node, ast.Attribute)
    }


def _db_imports(key_or_path: str) -> list[str]:
    return sorted(
        name
        for name in _imports(key_or_path)
        if any(marker in name for marker in _DB_MARKERS)
    )


# ============================================================
# C28 ToolExecutionService 不得依赖持久化
# ============================================================

class TestC28ExecutionServiceHasNoPersistence:
    def test_c28_1_no_sqlalchemy_or_repository_import(self) -> None:
        assert _db_imports("execution_service") == []

    def test_c28_2_no_session_or_engine_usage(self) -> None:
        identifiers = _identifiers("execution_service")
        for forbidden in ("session", "engine", "connection", "commit"):
            assert forbidden not in identifiers, forbidden

    def test_c28_3_execution_depends_only_on_observer_port(self) -> None:
        """方向：Execution → Observability（不是 → Database）。"""
        imports = _imports("execution_service")
        assert any(
            name.endswith("tool_execution_observer") for name in imports
        ), imports
        assert not any(
            name.endswith("in_memory_tool_execution_collector")
            for name in imports
        ), imports


# ============================================================
# C29 Tool Handler 不得依赖 Observability Persistence
# ============================================================

class TestC29ToolHandlerHasNoObservabilityPersistence:
    def test_c29_1_tools_do_not_import_observability(self) -> None:
        tools_dir = _path(_TOOLS_DIR)
        offenders: list[str] = []
        for filename in sorted(os.listdir(tools_dir)):
            if not filename.endswith(".py"):
                continue
            relative = f"{_TOOLS_DIR}/{filename}"
            for name in _imports(relative):
                if "tool_observability" in name or "tool_execution_" in name:
                    offenders.append((filename, name))
        assert offenders == [], offenders

    def test_c29_2_tools_have_no_persistence_write_capability(self) -> None:
        """Tool 可以有**业务只读** Engine（既有设计），但不得有任何
        观测持久化 / 写能力。"""
        tools_dir = _path(_TOOLS_DIR)
        for filename in sorted(os.listdir(tools_dir)):
            if not filename.endswith(".py"):
                continue
            identifiers = _identifiers(f"{_TOOLS_DIR}/{filename}")
            for forbidden in ("commit", "flush", "add_all", "on_execution"):
                assert forbidden not in identifiers, (filename, forbidden)


# ============================================================
# C30 Collector 不得依赖持久化
# ============================================================

class TestC30CollectorHasNoPersistence:
    def test_c30_1_no_sqlalchemy_repository_or_session(self) -> None:
        assert _db_imports("collector") == []

    def test_c30_2_collector_stays_pure_memory(self) -> None:
        collector = InMemoryToolExecutionCollector()
        public = {
            name for name in dir(collector) if not name.startswith("_")
        }
        assert not (public & {
            "session", "engine", "repository", "commit", "flush", "save",
        })
        # 内存 retention 语义仍在（Step 20）
        assert collector.max_records == 1000
        collector.clear()
        assert collector.records() == ()


# ============================================================
# C31 QueryService 不得执行写操作
# ============================================================

class TestC31QueryServiceHasNoWriteCapability:
    def test_c31_1_no_db_imports(self) -> None:
        assert _db_imports("query") == []

    def test_c31_2_no_write_operations(self) -> None:
        query = ToolObservabilityQueryService(InMemoryToolExecutionCollector())
        public = {name for name in dir(query) if not name.startswith("_")}
        assert not (public & {
            "clear", "append", "on_execution", "evict", "add", "save",
            "commit", "flush", "delete",
        }), public

    def test_c31_3_no_write_identifiers(self) -> None:
        identifiers = _identifiers("query")
        for forbidden in (
            "commit", "flush", "execute", "insert", "update", "delete",
            "session", "engine",
        ):
            assert forbidden not in identifiers, forbidden


# ============================================================
# C32 Observability API 不得直连 Repository / SQLAlchemy
# ============================================================

class TestC32ObservabilityApiHasNoRepository:
    def test_c32_1_no_repository_or_sqlalchemy_import(self) -> None:
        """Step 30：API 允许的**唯一** db 依赖 = 仓储错误类型
        （错误映射 → 502；与 ``api/usage.py`` 的既有 precedent 一致）。

        仍不得 import：SQLAlchemy / Session / ORM Model / Repository 类。
        """
        assert _db_imports("api") == [
            "backend.app.db.tool_execution_repository"
        ]
        identifiers = _identifiers("api")
        assert "toolexecutionrepository" not in identifiers
        assert "toolexecutionrecordmodel" not in identifiers
        assert "sqlalchemy" not in identifiers

    def test_c32_2_api_only_uses_query_service(self) -> None:
        identifiers = _identifiers("api")
        assert "get_tool_observability_query_service" in identifiers
        for forbidden in ("session", "engine", "commit", "repository"):
            assert forbidden not in identifiers, forbidden


# ============================================================
# Survey 事实锁定（设计参照 / 未实现）
# ============================================================

class TestPersistenceSurveyFacts:
    def test_llm_usage_reference_layering_exists(self) -> None:
        """参照架构：Execution → Sink/Service → Repository（不是直连）。"""
        service_imports = _imports("usage_persistence_service")
        assert any(
            name.endswith("llm_usage_repository") for name in service_imports
        )
        repository_imports = _imports("usage_repository")
        assert any(
            name.startswith("sqlalchemy") for name in repository_imports
        )
        assert any(
            name.endswith("db.session") for name in repository_imports
        )

    def test_tool_execution_orm_model_is_confined_to_ai_ops(self) -> None:
        """Step 26 时点：无 Tool Execution Model。
        Step 27：新增且**只能**是 `ai_ops.tool_execution_record`
        （不得出现在 public，也不得散落在其它 Model 文件）。"""
        from backend.app.db.models.tool_execution_record import (
            TOOL_EXECUTION_SCHEMA,
            TOOL_EXECUTION_TABLE,
            ToolExecutionRecordModel,
        )

        assert TOOL_EXECUTION_SCHEMA == "ai_ops"
        assert TOOL_EXECUTION_TABLE == "tool_execution_record"
        assert ToolExecutionRecordModel.__table__.schema == "ai_ops"

        models_dir = os.path.join(REPO_ROOT, "backend", "app", "db", "models")
        offenders = []
        for filename in sorted(os.listdir(models_dir)):
            if not filename.endswith(".py"):
                continue
            with open(
                os.path.join(models_dir, filename), encoding="utf-8"
            ) as handle:
                source = handle.read()
            if "tool_execution" in source and filename != (
                "tool_execution_record.py"
            ) and filename != "__init__.py":
                offenders.append(filename)
        assert offenders == [], offenders

    def test_no_persistence_adapter_implemented(self) -> None:
        """未实现 PersistenceAdapter / Repository / 后台持久化。"""
        services_dir = os.path.join(REPO_ROOT, "backend", "app", "services")
        offenders = [
            filename
            for filename in sorted(os.listdir(services_dir))
            if filename.endswith(".py")
            and "tool_observability_persistence" in filename
        ]
        assert offenders == [], offenders
        assert not os.path.exists(
            os.path.join(REPO_ROOT, "backend", "app", "repositories")
        )
        assert not os.path.exists(os.path.join(REPO_ROOT, "alembic"))

    def test_observability_modules_have_no_async_or_background_write(
        self,
    ) -> None:
        """无后台线程 / 队列 / 异步持久化（禁止项实测）。"""
        for key in ("record", "observer", "collector", "metrics", "query",
                    "snapshot", "serialization"):
            identifiers = _identifiers(key)
            for forbidden in (
                "thread", "to_thread", "create_task", "celery", "kafka",
                "redis", "queue", "background",
            ):
                assert forbidden not in identifiers, (key, forbidden)


# ============================================================
# C33 Step 27：Repository 允许持久化依赖；运行时不得反向依赖它
# ============================================================

class TestC33RepositoryBoundary:
    """C33：Persistence 底座（Step 27）不得反向侵入运行时。

        * ``ToolExecutionRepository`` **可以**依赖
          SQLAlchemy / Session / ORM Model / ``ToolExecutionRecord``；
        * ``ToolExecutionService`` **不得** import
          ``ToolExecutionRepository`` / SQLAlchemy / ``ToolExecutionRecordRow``；
        * ``InMemoryToolExecutionCollector`` **不得** import Repository；
        * Observer / QueryService / Snapshot / Metrics / API **不得** import
          Repository 或 ORM Model；
        * PersistenceAdapter / PersistenceService 仍未实现（Deferred）。
    """

    def test_c33_1_repository_may_depend_on_persistence_stack(self) -> None:
        imports = _imports("tool_repository")
        assert any(
            name.startswith("sqlalchemy") for name in imports
        ), imports
        assert any(
            name.endswith("db.session") for name in imports
        ), imports
        assert any(
            name.endswith("models.tool_execution_record") for name in imports
        ), imports
        assert any(
            name.endswith("services.tool_execution_record") for name in imports
        ), imports

    def test_c33_2_execution_service_has_no_repository_dependency(self) -> None:
        imports = _imports("execution_service")
        for forbidden in (
            "backend.app.db.tool_execution_repository",
            "backend.app.db.models.tool_execution_record",
            "sqlalchemy",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden
        identifiers = _identifiers("execution_service")
        for forbidden in (
            "toolexecutionrepository", "toolexecutionrecordrow", "session",
        ):
            assert forbidden not in identifiers, forbidden

    def test_c33_3_collector_has_no_repository_dependency(self) -> None:
        imports = _imports("collector")
        for forbidden in ("backend.app.db", "sqlalchemy"):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden

    def test_c33_4_observability_layers_have_no_repository_dependency(
        self,
    ) -> None:
        for key in ("query", "snapshot", "metrics", "serialization", "api",
                    "observer", "record"):
            imports = _imports(key)
            for forbidden in (
                "backend.app.db.tool_execution_repository",
                "backend.app.db.models.tool_execution_record",
                "backend.app.db",
                "sqlalchemy",
            ):
                if key == "api" and forbidden in (
                    "backend.app.db.tool_execution_repository",
                    "backend.app.db",
                ):
                    # Step 30：API 只允许仓储**错误类型**（错误映射）；
                    # 不得使用 Repository 类 / Session / ORM（见 C36.2）。
                    assert {
                        name for name in imports
                        if name.startswith("backend.app.db")
                    } == {"backend.app.db.tool_execution_repository"}
                    assert "toolexecutionrepository" not in _identifiers("api")
                    continue
                assert not any(
                    name == forbidden or name.startswith(forbidden + ".")
                    for name in imports
                ), (key, forbidden)

    def test_c33_5_no_persistence_adapter_or_service_yet(self) -> None:
        """Adapter / Service 仍 Deferred（Step 27 只做底座）。"""
        services_dir = os.path.join(REPO_ROOT, "backend", "app", "services")
        offenders = [
            filename
            for filename in sorted(os.listdir(services_dir))
            if filename.endswith(".py")
            and "tool_observability_persistence" in filename
        ]
        assert offenders == [], offenders

    def test_c33_6_repository_only_reachable_via_persistence_service(
        self,
    ) -> None:
        """Step 27 时点：除 db 层外无人 import Repository。
        Step 28：允许 ``ToolExecutionPersistenceService``（C34.3）。
        Step 29：允许 ``ToolExecutionPersistentQueryService``（C35.1）。
        执行链 / 观测链 / API 仍不得直接 import Repository。"""
        allowed = {
            _MODULES["tool_repository"],
            _MODULES["tool_model"],
            "backend/app/services/tool_execution_persistence_service.py",
            _MODULES["persistent_query"],
            # Step 30：API 仅导入该模块的**错误类型**用于 502 映射
            _MODULES["api"],
            # Step 41：Assistant Trace API 同样只导入**错误类型**（502）
            "backend/app/api/assistant_trace.py",
        }
        offenders: list[str] = []
        backend_dir = os.path.join(REPO_ROOT, "backend", "app")
        for dirpath, _dirnames, filenames in os.walk(backend_dir):
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                full = os.path.join(dirpath, filename)
                relative = os.path.relpath(full, REPO_ROOT).replace("\\", "/")
                if relative in allowed:
                    continue
                with open(full, encoding="utf-8-sig") as handle:
                    source = handle.read()
                if "tool_execution_repository" in source:
                    offenders.append(relative)
        assert offenders == [], offenders

    def test_c33_7_runtime_chain_unchanged(self) -> None:
        """ToolExecutionService 仍然 → ToolRegistry（不是 → Repository）。"""
        imports = _imports("execution_service")
        assert any(
            name.startswith("backend.app.tools") for name in imports
        ), imports
        assert not any(name.startswith("backend.app.db") for name in imports)

    def test_c33_8_repository_returns_internal_row_not_orm(self) -> None:
        from backend.app.db.tool_execution_repository import (
            ToolExecutionRecordRow,
            ToolExecutionRepository,
        )

        public = {
            name
            for name in dir(ToolExecutionRepository)
            if not name.startswith("_")
        }
        assert public == {
            "create", "get_by_request_id",
            "list_recent", "build_recent_select",     # Step 29 只读新增
            "get_metrics", "build_metrics_select",    # Step 33 SQL 聚合
            "build_insert", "build_request_select",
        }, public
        assert ToolExecutionRecordRow.__dataclass_params__.frozen is True
        # Row 是内部 dataclass：不是 ORM Model（无 __tablename__ / 无 metadata）
        assert not hasattr(ToolExecutionRecordRow, "__tablename__")
        assert [
            field.name for field in dataclasses.fields(ToolExecutionRecordRow)
        ] == list(TOOL_EXECUTION_READ_COLUMNS)


# ============================================================
# C34 Step 28：Persistence Adapter 接入（Observer → Persistence）
# ============================================================

class TestC34PersistenceAdapterIntegration:
    """C34：Observer 端口接到 Persistence，但边界不得反向渗透。

        C34.1  Adapter 实现 Observer Protocol
        C34.2  Adapter 不 import SQLAlchemy / ORM / Repository / Session
        C34.3  PersistenceService 可以依赖 Repository
        C34.4  ToolExecutionService 不 import Adapter / Service
        C34.5  Collector 不 import Adapter
        C34.6  Adapter 失败不传播（Observer 契约）
        C34.7  Composite 在子 observer 失败后继续
        C34.8  Composition Root 是唯一创建 Adapter 的地方
        C34.9  无 API 模块直接 import Repository
        C34.10 Tool Handler 不 import Persistence 模块
    """

    @staticmethod
    def _record():
        from datetime import datetime, timedelta, timezone

        from backend.app.services.tool_execution_record import (
            ToolExecutionRecord,
        )

        started = datetime(2026, 9, 26, 10, 20, 30, tzinfo=timezone.utc)
        return ToolExecutionRecord(
            request_id="req-c34",
            round=1,
            tool_name="get_inventory",
            started_at=started,
            finished_at=started + timedelta(milliseconds=1.0),
            duration_ms=1.0,
            success=True,
        )

    def test_c34_1_adapter_implements_observer_protocol(self) -> None:
        from backend.app.services.tool_execution_observer import (
            ToolExecutionObserver,
        )
        from backend.app.services.tool_execution_persistence_adapter import (
            ToolExecutionPersistenceAdapter,
        )

        adapter = ToolExecutionPersistenceAdapter()

        assert isinstance(adapter, ToolExecutionObserver)
        assert callable(adapter.on_execution)
        assert not hasattr(adapter, "save")
        assert not hasattr(adapter, "write")

    def test_c34_2_adapter_has_no_db_dependency(self) -> None:
        imports = _imports("persistence_adapter")
        for forbidden in (
            "sqlalchemy", "backend.app.db", "backend.app.db.session",
            "backend.app.db.models.tool_execution_record",
            "backend.app.db.tool_execution_repository",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden
        identifiers = _identifiers("persistence_adapter")
        for forbidden in ("session", "engine", "insert", "commit",
                          "rollback", "dumps"):
            assert forbidden not in identifiers, forbidden

    def test_c34_3_service_may_depend_on_repository(self) -> None:
        imports = _imports("persistence_service")
        assert any(
            name.endswith("db.tool_execution_repository")
            for name in imports
        ), imports
        identifiers = _identifiers("persistence_service")
        assert "create" in identifiers or "persist" in identifiers

    def test_c34_4_execution_service_has_no_persistence_dependency(self) -> None:
        imports = _imports("execution_service")
        for forbidden in (
            "backend.app.services.tool_execution_persistence_adapter",
            "backend.app.services.tool_execution_persistence_service",
            "backend.app.db",
            "sqlalchemy",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden

    def test_c34_5_collector_has_no_persistence_dependency(self) -> None:
        imports = _imports("collector")
        for forbidden in (
            "backend.app.services.tool_execution_persistence_adapter",
            "backend.app.services.tool_execution_persistence_service",
            "backend.app.db",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden

    def test_c34_6_adapter_failure_does_not_propagate(self) -> None:
        from backend.app.services.tool_execution_persistence_adapter import (
            ToolExecutionPersistenceAdapter,
        )

        class _ExplodingService:
            def persist(self, record):
                raise RuntimeError("db down")

        adapter = ToolExecutionPersistenceAdapter(
            persistence_service=_ExplodingService()  # type: ignore[arg-type]
        )

        adapter.on_execution(self._record())      # 不抛异常

    def test_c34_7_composite_continues_after_child_failure(self) -> None:
        from backend.app.services.tool_execution_persistence_adapter import (
            CompositeToolExecutionObserver,
        )

        seen: list[str] = []

        class _Exploding:
            def on_execution(self, record):
                seen.append("exploding")
                raise RuntimeError("boom")

        class _Recording:
            def on_execution(self, record):
                seen.append("recording")

        composite = CompositeToolExecutionObserver(_Exploding(), _Recording())

        composite.on_execution(self._record())

        assert seen == ["exploding", "recording"]  # 后续 observer 仍被调用

    def test_c34_8_composition_root_only_creates_adapter(self) -> None:
        source = _source("root")
        assert "ToolExecutionPersistenceAdapter" in source
        assert "ToolExecutionPersistenceService" in source
        offenders: list[str] = []
        backend_dir = os.path.join(REPO_ROOT, "backend", "app")
        for dirpath, _dirnames, filenames in os.walk(backend_dir):
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                full = os.path.join(dirpath, filename)
                relative = os.path.relpath(full, REPO_ROOT).replace("\\", "/")
                if relative in {
                    _MODULES["persistence_adapter"],
                    _MODULES["persistence_service"],
                    _MODULES["root"],
                }:
                    continue
                with open(full, encoding="utf-8-sig") as handle:
                    text = handle.read()
                if "ToolExecutionPersistenceAdapter(" in text:
                    offenders.append(relative)
        assert offenders == [], offenders

    def test_c34_9_no_api_module_imports_repository(self) -> None:
        """API 不得使用 Repository 类 / SQLAlchemy（Step 30：仅允许错误类型）。"""
        api_dir = os.path.join(REPO_ROOT, "backend", "app", "api")
        for filename in sorted(os.listdir(api_dir)):
            if not filename.endswith(".py"):
                continue
            with open(
                os.path.join(api_dir, filename), encoding="utf-8"
            ) as handle:
                source = handle.read()
            assert "ToolExecutionRepository(" not in source, filename
            assert "sqlalchemy" not in source, filename
        # 观测 API 模块本身不得出现 session / ORM（health.py 的
        # ``db.session.ping_database`` 属既有 Phase 3.1 代码，不在此约束内）
        observability_source = _source("api").lower()
        assert "session" not in observability_source
        assert "engine" not in observability_source

    def test_c34_10_tool_handler_has_no_persistence_dependency(self) -> None:
        tools_dir = os.path.join(REPO_ROOT, "backend", "app", "tools")
        for filename in sorted(os.listdir(tools_dir)):
            if not filename.endswith(".py"):
                continue
            relative = f"backend/app/tools/{filename}"
            imports = _imports(relative)
            for forbidden in (
                "backend.app.services.tool_execution_persistence_adapter",
                "backend.app.services.tool_execution_persistence_service",
                "backend.app.db.tool_execution_repository",
            ):
                assert not any(
                    name == forbidden or name.startswith(forbidden + ".")
                    for name in imports
                ), (filename, forbidden)

    def test_c34_11_observer_fanout_composition_and_api_unchanged(self) -> None:
        """Composition Root：fan-out = Collector + Adapter；API 仍只读内存。"""
        from backend.app.api import orchestrator_chat as root
        from backend.app.services.in_memory_tool_execution_collector import (
            InMemoryToolExecutionCollector,
        )
        from backend.app.services.tool_execution_persistence_adapter import (
            CompositeToolExecutionObserver,
            ToolExecutionPersistenceAdapter,
        )

        observer = root._TOOL_EXECUTION_OBSERVER
        assert isinstance(observer, CompositeToolExecutionObserver)
        assert isinstance(observer.observers[0], InMemoryToolExecutionCollector)
        assert isinstance(observer.observers[1], ToolExecutionPersistenceAdapter)
        # 模块级只允许这 4 个装配对象（无第二个 Collector / 无隐式工厂）
        assert sorted(
            name
            for name in vars(root)
            if name.startswith("_TOOL_EXECUTION_")
        ) == [
            "_TOOL_EXECUTION_COLLECTOR",
            "_TOOL_EXECUTION_OBSERVER",
            "_TOOL_EXECUTION_PERSISTENCE_ADAPTER",
            "_TOOL_EXECUTION_PERSISTENCE_SERVICE",
        ]
        api_source = _source("api")
        # Step 30：API 只用仓储**错误类型**做 502 映射（不构造 Repository）
        assert "ToolExecutionRepository(" not in api_source
        assert "sqlalchemy" not in api_source.lower()


# ============================================================
# C35 Step 29：Persistent Query Boundary
# ============================================================

class TestC35PersistentQueryBoundary:
    """C35：数据库只读查询边界（与 Runtime Query 并列，**不**合并）。

        C35.1  Persistent Query Service 可以依赖 Repository
        C35.2  Persistent Query Service 不 import SQLAlchemy / Session / ORM
        C35.3  Repository 可以执行 SELECT
        C35.4  Repository 查询不执行 INSERT / UPDATE / DELETE / DDL
        C35.5  Persistent Query Service 不 import Collector
        C35.6  Persistent Query Service 不 import 执行链 / Registry / Handler
        C35.7  Persistent Query Service 不修改 Collector
        C35.8  HTTP API 当前不依赖 Persistent Query Service
        C35.9  Snapshot 字段仍然严格 11 个
        C35.10 ORM Model 不泄漏到 Service / API
    """

    @staticmethod
    def _row():
        from datetime import datetime, timedelta, timezone

        from backend.app.db.tool_execution_repository import (
            ToolExecutionRecordRow,
        )

        started = datetime(2026, 9, 28, 11, 0, 0, tzinfo=timezone.utc)
        return ToolExecutionRecordRow(
            id=1,
            request_id="req-c35",
            round=1,
            tool_name="get_inventory",
            started_at=started,
            finished_at=started + timedelta(milliseconds=1.0),
            duration_ms=1.0,
            success=True,
            project_id="project-a",
            tool_call_id=None,
            error_code=None,
            error_type=None,
        )

    def test_c35_1_persistent_query_may_depend_on_repository(self) -> None:
        imports = _imports("persistent_query")
        assert any(
            name.endswith("db.tool_execution_repository")
            for name in imports
        ), imports

    def test_c35_2_persistent_query_has_no_sqlalchemy_or_orm(self) -> None:
        imports = _imports("persistent_query")
        for forbidden in (
            "sqlalchemy",
            "backend.app.db.session",
            "backend.app.db.models.tool_execution_record",
            "backend.app.db.models",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden
        identifiers = _identifiers("persistent_query")
        for forbidden in ("session", "engine", "execute", "insert", "commit"):
            assert forbidden not in identifiers, forbidden

    def test_c35_3_repository_can_select(self) -> None:
        from backend.app.db.tool_execution_repository import (
            ToolExecutionRepository,
        )

        repository = ToolExecutionRepository()
        statement = repository.build_recent_select(limit=5)

        assert statement is not None
        assert "SELECT" in str(statement).upper()

    def test_c35_4_repository_query_is_select_only(self) -> None:
        import inspect

        from backend.app.db import tool_execution_repository as module

        source = inspect.getsource(module.ToolExecutionRepository.list_recent)
        for forbidden in ("insert(", "update(", "delete(", "DELETE",
                          "UPDATE", "TRUNCATE", "DROP", "CREATE"):
            assert forbidden not in source, forbidden

    def test_c35_5_persistent_query_has_no_collector_dependency(self) -> None:
        imports = _imports("persistent_query")
        assert not any(
            "in_memory_tool_execution_collector" in name for name in imports
        ), imports
        identifiers = _identifiers("persistent_query")
        for forbidden in ("collector", "on_execution", "clear", "evict"):
            assert forbidden not in identifiers, forbidden

    def test_c35_6_persistent_query_has_no_execution_chain_dependency(
        self,
    ) -> None:
        imports = _imports("persistent_query")
        for forbidden in (
            "backend.app.services.tool_execution_service",
            "backend.app.services.ai_orchestrator_service",
            "backend.app.services.ai_router_service",
            "backend.app.tools",
            "backend.app.api",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden

    def test_c35_7_persistent_query_does_not_mutate_collector(self) -> None:
        """行为断言：查询不触发 Collector 变更（两者互不感知）。"""
        from backend.app.services.in_memory_tool_execution_collector import (
            InMemoryToolExecutionCollector,
        )
        from backend.app.services.tool_execution_persistent_query_service import (
            ToolExecutionPersistentQueryService,
        )

        collector = InMemoryToolExecutionCollector()

        class _Repository:
            def list_recent(
                self,
                *,
                limit,
                offset=0,
                project_id=None,
                tool_name=None,
                success=None,
            ):
                return [TestC35PersistentQueryBoundary._row()]

        service = ToolExecutionPersistentQueryService(
            repository=_Repository()  # type: ignore[arg-type]
        )

        snapshots = service.list_recent(limit=1)

        assert len(snapshots) == 1
        assert collector.records() == ()          # Collector 未被触碰
        assert collector.max_records == 1000

    def test_c35_8_only_history_api_depends_on_persistent_query(self) -> None:
        """Step 29 时点：无 API 依赖 Persistent Query Service。
        Step 30：**唯一**允许的消费方 = ``tool_observability.py``
        （History 端点；C36.1）；其它 api 模块仍不得引用，且任何 API
        都不得构造 Repository。"""
        allowed = {
            "tool_observability.py",     # History 端点（C36.1）
            "orchestrator_chat.py",      # Composition Root 装配 accessor
        }
        api_dir = os.path.join(REPO_ROOT, "backend", "app", "api")
        for filename in sorted(os.listdir(api_dir)):
            if not filename.endswith(".py"):
                continue
            with open(
                os.path.join(api_dir, filename), encoding="utf-8"
            ) as handle:
                source = handle.read()
            assert "ToolExecutionRepository(" not in source, filename
            if filename in allowed:
                continue
            assert "persistent_query" not in source, filename
            assert "PersistentQueryService" not in source, filename

    def test_c35_9_snapshot_still_has_eleven_fields(self) -> None:
        from backend.app.services.tool_observability_snapshot import (
            ToolExecutionSnapshot,
        )
        from backend.app.services.tool_execution_persistent_query_service import (
            ToolExecutionPersistentQueryService,
        )

        service = ToolExecutionPersistentQueryService(
            repository=type(
                "_Repo", (), {"list_recent": lambda self, *, limit: [self._row()]}
            )()
        )
        snapshot = service._to_snapshot(  # noqa: SLF001 —— 契约断言
            self._row()
        )

        assert isinstance(snapshot, ToolExecutionSnapshot)
        assert set(vars(snapshot)) == {
            "request_id", "round", "tool_name", "started_at", "finished_at",
            "duration_ms", "success", "project_id", "tool_call_id",
            "error_code", "error_type",
        }
        snapshot.assert_field_whitelist()

    def test_c35_10_orm_model_does_not_leak_to_service_or_api(self) -> None:
        for key in ("persistent_query", "persistence_service",
                    "persistence_adapter", "api", "query"):
            identifiers = _identifiers(key)
            assert "toolexecutionrecordmodel" not in identifiers, key
            assert "__tablename__" not in identifiers, key


# ============================================================
# C36 Step 30：Persistent History HTTP API Boundary
# ============================================================

class TestC36PersistentHistoryHttpBoundary:
    """C36：持久历史 HTTP 边界（与 Runtime API **并列**，不 fallback / 不 merge）。

        C36.1  HTTP API 可以依赖 ToolExecutionPersistentQueryService
        C36.2  HTTP API 不 import SQLAlchemy / Session / ORM Model / Repository
        C36.3  HTTP API 不依赖 ToolExecutionService / Registry / Handler
        C36.4  History 端点不 import Collector / Runtime QueryService
        C36.5  History API 不修改 Runtime API 行为
        C36.6  Response 只暴露 Snapshot 11 字段
        C36.7  无 Runtime → Persistent fallback
        C36.8  无 Persistent → Runtime fallback
        C36.9  无 POST / PUT / PATCH / DELETE
        C36.10 只通过 PersistentQueryService 读取
    """

    @staticmethod
    def _client():
        from fastapi.testclient import TestClient

        from backend.app.api import orchestrator_chat as root
        from backend.app.main import app

        root._TOOL_EXECUTION_COLLECTOR.clear()
        return TestClient(app), root

    @staticmethod
    def _snapshot(request_id: str = "database-B"):
        from datetime import datetime, timedelta, timezone

        from backend.app.services.tool_execution_record import (
            ToolExecutionRecord,
        )
        from backend.app.services.tool_observability_snapshot import (
            ToolExecutionSnapshot,
        )

        started = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)
        record = ToolExecutionRecord(
            request_id=request_id,
            round=1,
            tool_name="get_inventory",
            started_at=started,
            finished_at=started + timedelta(milliseconds=1.0),
            duration_ms=1.0,
            success=True,
            project_id="test-project",
        )
        return ToolExecutionSnapshot.from_record(record)

    def test_c36_1_api_may_depend_on_persistent_query_service(self) -> None:
        identifiers = _identifiers("api")
        assert "get_tool_execution_persistent_query_service" in identifiers
        assert "list_recent" in identifiers

    def test_c36_2_api_has_no_sqlalchemy_session_or_orm(self) -> None:
        imports = _imports("api")
        for forbidden in (
            "sqlalchemy", "backend.app.db.session", "backend.app.db.models",
            "backend.app.db.tool_execution_repository.ToolExecutionRepository",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden
        identifiers = _identifiers("api")
        for forbidden in ("toolexecutionrecordmodel", "session", "engine"):
            assert forbidden not in identifiers, forbidden

    def test_c36_3_api_has_no_execution_chain_dependency(self) -> None:
        imports = _imports("api")
        for forbidden in (
            "backend.app.services.tool_execution_service",
            "backend.app.services.ai_orchestrator_service",
            "backend.app.tools",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden

    def test_c36_4_api_module_does_not_import_collector_or_runtime_query(
        self,
    ) -> None:
        """History 端点只用 persistent accessor；模块不 import Collector /
        Runtime QueryService 类（仅经 Composition Root accessor）。"""
        imports = _imports("api")
        for forbidden in (
            "backend.app.services.in_memory_tool_execution_collector",
            "backend.app.services.tool_observability_query_service",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden

    def test_c36_5_history_api_does_not_change_runtime_api(self) -> None:
        client, root = self._client()
        try:
            from backend.app.services.tool_execution_record import (
                ToolExecutionRecord,
            )

            before = client.get("/api/observability/tools").json()
            assert before == {"records": []}
            client.get("/api/observability/tools/history")   # 触发持久路径
            after = client.get("/api/observability/tools").json()
            assert after == {"records": []}
            assert root._TOOL_EXECUTION_COLLECTOR.records() == ()
        finally:
            root._TOOL_EXECUTION_COLLECTOR.clear()

    def test_c36_6_response_exposes_only_snapshot_fields(self) -> None:
        from backend.app.api.tool_observability import (
            ToolExecutionHistoryItemResponse,
            ToolExecutionHistoryResponse,
        )

        assert set(ToolExecutionHistoryItemResponse.model_fields) == {
            "request_id", "round", "tool_name", "started_at", "finished_at",
            "duration_ms", "success", "project_id", "tool_call_id",
            "error_code", "error_type",
        }
        assert set(ToolExecutionHistoryResponse.model_fields) == {
            "items", "limit", "offset"       # Step 31：分页元信息（无 total_count）
        }

    def test_c36_7_and_8_no_fallback_either_direction(
        self, monkeypatch
    ) -> None:
        """Runtime ↔ Persistent 互不回退：DB 失败 → 502（不返回内存数据）；
        运行时端点不受 DB 状态影响。"""
        from backend.app.api import tool_observability as api
        from backend.app.db.tool_execution_repository import (
            ToolExecutionRepositoryError,
        )

        client, root = self._client()
        try:
            from datetime import datetime, timedelta, timezone

            from backend.app.services.tool_execution_record import (
                ToolExecutionRecord,
            )

            started = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)
            root._TOOL_EXECUTION_COLLECTOR.on_execution(
                ToolExecutionRecord(
                    request_id="memory-A",
                    round=1,
                    tool_name="get_inventory",
                    started_at=started,
                    finished_at=started + timedelta(milliseconds=1.0),
                    duration_ms=1.0,
                    success=True,
                )
            )

            class _BrokenService:
                def list_recent(
                    self,
                    *,
                    limit,
                    offset=0,
                    project_id=None,
                    tool_name=None,
                    success=None,
                ):
                    raise ToolExecutionRepositoryError("db down")

            monkeypatch.setattr(
                api,
                "get_tool_execution_persistent_query_service",
                lambda: _BrokenService(),
            )

            broken = client.get("/api/observability/tools/history")
            assert broken.status_code == 502
            assert "memory-A" not in broken.text      # C36.8：无回退
            runtime = client.get("/api/observability/tools").json()
            assert [r["request_id"] for r in runtime["records"]] == [
                "memory-A"
            ]                                          # C36.7：互不影响
        finally:
            root._TOOL_EXECUTION_COLLECTOR.clear()

    def test_c36_9_no_write_methods(self) -> None:
        tree = _tree("api")
        methods = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "router"
        }
        assert methods == {"get"}, methods
        for forbidden in ("post", "put", "patch", "delete"):
            assert forbidden not in methods, forbidden

    def test_c36_10_reads_only_through_persistent_query_service(self) -> None:
        """API 模块内的持久读取只经 accessor（无 Repository 调用 / 无 SQL）。"""
        tree = _tree("api")
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        }
        for forbidden in (
            "create", "insert", "update", "delete", "execute", "commit",
            "list_by_project", "list_by_tool", "count", "aggregate",
        ):
            assert forbidden not in called, forbidden
        assert "list_recent" in called


# ============================================================
# C37 Step 31：Persistent History Pagination Boundary
# ============================================================

class TestC37PersistentHistoryPaginationBoundary:
    """C37：分页 = 仅 LIMIT + OFFSET（同一稳定排序窗口）。

        C37.1  History API 允许 limit + offset
        C37.2  History API 不允许 filter / project_id / tool_name / success /
               时间范围 / keyword
        C37.3  Repository 使用 ORDER BY started_at DESC, id DESC + LIMIT + OFFSET
        C37.4  PersistentQueryService 不自行排序
        C37.5  API 不自行排序
        C37.6  Runtime API 不使用 Persistent Pagination
        C37.7  Persistent API 不读取 Collector
        C37.8  不存在 fallback
        C37.9  不存在 total_count / COUNT（**History 分页路径**）
        C37.10 不存在 Metrics aggregation（**History / Runtime 路径**）

    Step 33 追加：Persistent **Metrics** 是新独立路径（C39），
    其 SQL 聚合（COUNT / SUM / AVG / MAX）不影响本节结论 ——
    History 的 recent SELECT 仍无 ``COUNT``、响应仍无 ``total_count``。
    """

    def test_c37_1_history_api_accepts_limit_and_offset(self) -> None:
        """Step 31：分页参数。
        Step 32：追加精确过滤参数（project_id / tool_name / success）——
        此处只锁分页两个参数的默认值。"""
        import inspect

        from backend.app.api.tool_observability import (
            list_tool_execution_history,
        )

        signature = inspect.signature(list_tool_execution_history)
        assert list(signature.parameters)[:2] == ["limit", "offset"]
        assert signature.parameters["limit"].default == 100
        assert signature.parameters["offset"].default == 0

    def test_c37_2_no_fuzzy_or_time_range_parameters(self) -> None:
        """Step 31 时点：无任何 filter 参数。
        Step 32：允许**精确**过滤（project_id / tool_name / success，见 C38），
        但仍不允许模糊 / 时间范围 / 关键词 / cursor 等参数。"""
        import inspect

        from backend.app.api.tool_observability import (
            list_tool_execution_history,
        )

        parameters = set(
            inspect.signature(list_tool_execution_history).parameters
        )
        for forbidden in (
            "from_", "to", "start_time", "end_time", "keyword", "query",
            "cursor", "page", "q", "search",
        ):
            assert forbidden not in parameters, forbidden

    def test_c37_3_repository_sql_shape(self) -> None:
        from backend.app.db.tool_execution_repository import (
            ToolExecutionRepository,
        )

        statement = ToolExecutionRepository().build_recent_select(
            limit=2, offset=4
        )
        compiled = str(
            statement.compile(compile_kwargs={"literal_binds": True})
        )

        assert "ORDER BY" in compiled
        assert "started_at DESC" in compiled
        assert "id DESC" in compiled
        assert "LIMIT 2" in compiled
        assert "OFFSET 4" in compiled

    def test_c37_4_and_5_no_reordering_in_service_or_api(self) -> None:
        for key in ("persistent_query", "api"):
            tree = _tree(key)
            called = {
                node.func.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
            }
            for forbidden in ("sort", "sorted", "reverse", "order_by"):
                assert forbidden not in called, (key, forbidden)

    def test_c37_6_runtime_api_has_no_pagination(self) -> None:
        import inspect

        from backend.app.api.tool_observability import (
            list_tool_executions,
            tool_execution_metrics,
        )

        assert list(inspect.signature(list_tool_executions).parameters) == []
        assert list(inspect.signature(tool_execution_metrics).parameters) == []

    def test_c37_7_persistent_api_does_not_read_collector(self) -> None:
        identifiers = _identifiers("api")
        for forbidden in ("collector", "on_execution", "clear", "evict"):
            assert forbidden not in identifiers, forbidden

    def test_c37_8_no_fallback(self, monkeypatch) -> None:
        from fastapi.testclient import TestClient

        from backend.app.api import orchestrator_chat as root
        from backend.app.api import tool_observability as api
        from backend.app.db.tool_execution_repository import (
            ToolExecutionRepositoryError,
        )
        from backend.app.main import app

        root._TOOL_EXECUTION_COLLECTOR.clear()
        try:
            class _Broken:
                def list_recent(
                    self,
                    *,
                    limit,
                    offset=0,
                    project_id=None,
                    tool_name=None,
                    success=None,
                ):
                    raise ToolExecutionRepositoryError("db down")

            monkeypatch.setattr(
                api,
                "get_tool_execution_persistent_query_service",
                lambda: _Broken(),
            )
            with TestClient(app) as client:
                response = client.get("/api/observability/tools/history")
            assert response.status_code == 502
        finally:
            root._TOOL_EXECUTION_COLLECTOR.clear()

    def test_c37_9_no_total_count_or_count_sql(self) -> None:
        from backend.app.api.tool_observability import (
            ToolExecutionHistoryResponse,
        )
        from backend.app.db.tool_execution_repository import (
            ToolExecutionRepository,
        )

        assert "total_count" not in ToolExecutionHistoryResponse.model_fields
        compiled = str(
            ToolExecutionRepository()
            .build_recent_select(limit=1, offset=0)
            .compile(compile_kwargs={"literal_binds": True})
        ).upper()
        assert "COUNT(" not in compiled          # History 路径无计数查询
        # Step 33：Metrics 聚合是**独立**路径（Repository.get_metrics /
        # Service.metrics → C39）；History 路径不受影响。
        from backend.app.services.tool_execution_persistent_query_service import (
            ToolExecutionPersistentQueryService,
        )

        assert {
            name for name in dir(ToolExecutionPersistentQueryService)
            if not name.startswith("_")
        } == {
            "list_recent",          # History 分页
            "list_by_request_id",   # Step 41：Assistant Trace 的 Tool 数据源
            "metrics",              # Step 33
            "repository",
        }
        # Metrics 只能经专门方法访问（不出现在 list_recent 的签名里）
        import inspect

        assert "metrics" not in inspect.signature(
            ToolExecutionPersistentQueryService.list_recent
        ).parameters
        for cls in (ToolExecutionRepository, ToolExecutionPersistentQueryService):
            public = {name for name in dir(cls) if not name.startswith("_")}
            for forbidden in ("aggregate", "count_all", "total_count"):
                assert forbidden not in public, (cls.__name__, forbidden)

    def test_c37_10_no_metrics_aggregation_in_api(self) -> None:
        tree = _tree("api")
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        for forbidden in ("sum", "max", "min", "count"):
            assert forbidden not in called, forbidden
        # History 端点不计算 / 不返回任何 Metrics 指标字段
        # （``success_rate`` 等仅作为 Runtime Metrics 响应 DTO 字段存在）
        from backend.app.api.tool_observability import (
            ToolExecutionHistoryResponse,
        )

        assert not (
            set(ToolExecutionHistoryResponse.model_fields)
            & {"success_rate", "failure_rate", "total_count"}
        )
        history_fn = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "list_tool_execution_history"
        )
        history_identifiers = {
            node.id
            for node in ast.walk(history_fn)
            if isinstance(node, ast.Name)
        } | {
            node.attr
            for node in ast.walk(history_fn)
            if isinstance(node, ast.Attribute)
        }
        for forbidden in (
            "success_rate", "failure_rate", "average_duration_ms",
            "max_duration_ms", "total_duration_ms",
        ):
            assert forbidden not in history_identifiers, forbidden


# ============================================================
# C38 Step 32：Persistent History Filtering Boundary
# ============================================================

class TestC38PersistentHistoryFilteringBoundary:
    """C38：**精确**过滤（project_id / tool_name / success）+ 分页 + 稳定排序。

        C38.1  History API 支持 limit / offset / project_id / tool_name / success
        C38.2  Runtime API 不增加上述参数
        C38.3  Filtering 在 Repository / SQL 层完成
        C38.4  Service 不执行 Python filtering
        C38.5  API 不执行 Python filtering
        C38.6  过滤值必须使用 bound parameters（无注入 / 无 text() 拼接）
        C38.7  过滤发生在 LIMIT / OFFSET 之前
        C38.8  排序仍然 started_at DESC, id DESC
        C38.9  不允许 COUNT / SUM / AVG / Metrics
        C38.10 不允许 LIKE / ILIKE / regex / fuzzy / keyword search
        C38.11 不存在 Runtime / Persistent fallback
        C38.12 Snapshot 仍严格 11 fields
    """

    def test_c38_1_history_api_supports_five_parameters(self) -> None:
        import inspect

        from backend.app.api.tool_observability import (
            list_tool_execution_history,
        )

        signature = inspect.signature(list_tool_execution_history)

        assert list(signature.parameters) == [
            "limit", "offset", "project_id", "tool_name", "success"
        ]
        assert signature.parameters["project_id"].default is None
        assert signature.parameters["tool_name"].default is None
        assert signature.parameters["success"].default is None

    def test_c38_2_runtime_api_unchanged(self) -> None:
        import inspect

        from backend.app.api.tool_observability import (
            list_tool_executions,
            tool_execution_metrics,
        )

        assert list(inspect.signature(list_tool_executions).parameters) == []
        assert list(inspect.signature(tool_execution_metrics).parameters) == []

    def test_c38_3_and_6_repository_builds_bound_exact_filters(self) -> None:
        from backend.app.db.tool_execution_repository import (
            ToolExecutionRepository,
        )

        statement = ToolExecutionRepository().build_recent_select(
            limit=3,
            offset=1,
            project_id="project-a",
            tool_name="get_inventory",
            success=True,
        )
        sql = str(statement)
        params = statement.compile().params

        assert params["project_id_1"] == "project-a"     # bound parameters
        assert params["tool_name_1"] == "get_inventory"
        assert "LIKE" not in sql.upper()
        assert "ILIKE" not in sql.upper()
        assert "OR 1=1" not in sql

    def test_c38_4_and_5_no_python_filtering(self) -> None:
        for key in ("persistent_query", "api"):
            tree = _tree(key)
            called = {
                node.func.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
            }
            for forbidden in ("filter", "sorted", "order_by"):
                assert forbidden not in called, (key, forbidden)
            # 无「带条件的推导式」（= Python 层过滤）；纯映射推导式允许
            assert not [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.ListComp)
                and any(gen.ifs for gen in node.generators)
            ], key

    def test_c38_7_filter_before_limit_and_offset(self) -> None:
        from backend.app.db.tool_execution_repository import (
            ToolExecutionRepository,
        )

        sql = str(
            ToolExecutionRepository()
            .build_recent_select(
                limit=2,
                offset=4,
                project_id="project-a",
                tool_name="get_inventory",
                success=False,
            )
            .compile(compile_kwargs={"literal_binds": True})
        ).upper()

        assert sql.index("WHERE") < sql.index("ORDER BY")
        assert sql.index("ORDER BY") < sql.index("LIMIT")
        assert sql.index("LIMIT") < sql.index("OFFSET")

    def test_c38_8_ordering_unchanged(self) -> None:
        from backend.app.db.tool_execution_repository import (
            ToolExecutionRepository,
        )

        sql = str(
            ToolExecutionRepository()
            .build_recent_select(limit=1, project_id="project-a")
            .compile(compile_kwargs={"literal_binds": True})
        )

        assert "started_at DESC" in sql
        assert "id DESC" in sql
        assert "ORDER BY" in sql
        # 过滤列不参与排序
        assert "ORDER BY" in sql and "project_id" not in sql.split(
            "ORDER BY"
        )[1]

    def test_c38_9_no_metrics_aggregation(self) -> None:
        for key in ("persistent_query", "api"):
            tree = _tree(key)
            called = {
                node.func.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            for forbidden in ("sum", "max", "min", "count", "avg"):
                assert forbidden not in called, (key, forbidden)
        from backend.app.db.tool_execution_repository import (
            ToolExecutionRepository,
        )

        sql = str(
            ToolExecutionRepository()
            .build_recent_select(limit=1)
            .compile()
        ).upper()
        assert "COUNT(" not in sql

    def test_c38_10_no_fuzzy_or_like_or_regex(self) -> None:
        for key in ("tool_repository", "persistent_query", "api"):
            identifiers = _identifiers(key)
            for forbidden in (
                "like", "ilike", "regexp", "contains", "startswith",
                "match", "ilike_", "fuzzy",
            ):
                assert forbidden not in identifiers, (key, forbidden)

    def test_c38_11_no_fallback(self) -> None:
        """DB 失败 + 过滤请求 → 502（不回退内存视图）。"""
        from fastapi.testclient import TestClient

        from backend.app.api import orchestrator_chat as root
        from backend.app.api import tool_observability as api
        from backend.app.db.tool_execution_repository import (
            ToolExecutionRepositoryError,
        )
        from backend.app.main import app

        root._TOOL_EXECUTION_COLLECTOR.clear()
        try:
            class _Broken:
                def list_recent(
                    self,
                    *,
                    limit,
                    offset=0,
                    project_id=None,
                    tool_name=None,
                    success=None,
                ):
                    raise ToolExecutionRepositoryError("db down")

            # 使用 unittest.mock 语义的 monkeypatch 需要 fixture；此处直接
            # 临时替换并恢复，避免给本契约测试引入 fixture 依赖。
            original = api.get_tool_execution_persistent_query_service
            api.get_tool_execution_persistent_query_service = lambda: _Broken()
            try:
                with TestClient(app) as client:
                    response = client.get(
                        "/api/observability/tools/history"
                        "?project_id=project-a"
                    )
                assert response.status_code == 502
            finally:
                api.get_tool_execution_persistent_query_service = original
        finally:
            root._TOOL_EXECUTION_COLLECTOR.clear()

    def test_c38_12_snapshot_still_eleven_fields(self) -> None:
        from backend.app.api.tool_observability import (
            ToolExecutionHistoryItemResponse,
        )

        assert set(ToolExecutionHistoryItemResponse.model_fields) == {
            "request_id", "round", "tool_name", "started_at", "finished_at",
            "duration_ms", "success", "project_id", "tool_call_id",
            "error_code", "error_type",
        }


# ============================================================
# C39 Step 33：Persistent Metrics Boundary
# ============================================================

_METRICS_PATH = "/api/observability/tools/metrics/persistent"

_METRICS_RESPONSE_FIELDS = {
    "total_count", "success_count", "failure_count", "success_rate",
    "failure_rate", "total_duration_ms", "average_duration_ms",
    "max_duration_ms",
}


class TestC39PersistentMetricsBoundary:
    """C39：数据库侧聚合指标（**只读 / SQL 聚合 / 与 Runtime 隔离**）。

        C39.1  Persistent Metrics API 存在
        C39.2  Runtime Metrics API 不变
        C39.3  Persistent Metrics 只依赖 PersistentQueryService
        C39.4  PersistentQueryService 不访问 ORM / Session
        C39.5  Repository 执行 SQL aggregation
        C39.6  过滤发生在 SQL 层（bound parameters）
        C39.7  无 Python 全量 aggregation
        C39.8  除聚合外无无关列 / 无 SELECT *
        C39.9  无 LIKE / ILIKE / regex / fuzzy
        C39.10 无 fallback / merge / Runtime Metrics
        C39.11 Metrics 响应不含 Snapshot / 敏感字段
        C39.12 Persistent Metrics 不支持 limit / offset
    """

    def test_c39_1_persistent_metrics_endpoint_exists(self) -> None:
        from fastapi.testclient import TestClient

        from backend.app.main import app

        paths = set(app.openapi()["paths"])
        assert (
            "/api/observability/tools/metrics/persistent" in paths
        ), sorted(p for p in paths if "observability" in p)
        with TestClient(app) as client:
            status = client.get(_METRICS_PATH).status_code
        assert status in {200, 502}      # 200（DB 可用）/ 502（DB 不可用）

    def test_c39_2_runtime_metrics_unchanged(self) -> None:
        import inspect

        from backend.app.api.tool_observability import (
            persistent_tool_execution_metrics,
            tool_execution_metrics,
        )

        assert list(inspect.signature(tool_execution_metrics).parameters) == []
        assert list(
            inspect.signature(persistent_tool_execution_metrics).parameters
        ) == ["project_id", "tool_name", "success"]

    def test_c39_3_api_uses_persistent_query_service_only(self) -> None:
        identifiers = _identifiers("api")

        assert "get_tool_execution_persistent_query_service" in identifiers
        assert "metrics" in identifiers
        for forbidden in ("collector", "on_execution", "evict"):
            assert forbidden not in identifiers, forbidden

    def test_c39_4_service_has_no_orm_or_session_access(self) -> None:
        imports = _imports("persistent_query")
        for forbidden in (
            "sqlalchemy",
            "backend.app.db.session",
            "backend.app.db.models",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden
        identifiers = _identifiers("persistent_query")
        for forbidden in ("session", "engine", "execute", "rollback"):
            assert forbidden not in identifiers, forbidden

    def test_c39_5_repository_performs_sql_aggregation(self) -> None:
        from backend.app.db.tool_execution_repository import (
            ToolExecutionRepository,
        )

        sql = str(
            ToolExecutionRepository()
            .build_metrics_select(
                project_id="project-a",
                tool_name="get_inventory",
                success=True,
            )
            .compile(compile_kwargs={"literal_binds": True})
        )

        assert "count(*) AS total_count" in sql
        assert "count(*) FILTER (WHERE" in sql
        assert "sum(" in sql and "avg(" in sql and "max(" in sql
        assert " ORDER BY " not in sql.upper()
        assert " LIMIT " not in sql.upper()
        assert "WHERE " in sql.upper()          # 过滤下推 SQL

    def test_c39_6_filters_are_sql_bound_and_exact(self) -> None:
        from backend.app.db.tool_execution_repository import (
            ToolExecutionRepository,
        )

        statement = ToolExecutionRepository().build_metrics_select(
            project_id="project-a",
            tool_name="get_inventory",
            success=False,
        )
        params = statement.compile().params
        sql = str(statement)

        assert params["project_id_1"] == "project-a"
        assert params["tool_name_1"] == "get_inventory"
        assert "LIKE" not in sql.upper()
        assert "ILIKE" not in sql.upper()
        assert "OR 1=1" not in sql

    def test_c39_7_no_python_aggregation(self) -> None:
        from backend.app.services.tool_execution_persistent_query_service import (
            ToolExecutionPersistentQueryService,
        )

        tree = _tree("persistent_query")
        metrics_fn = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "metrics"
        )
        called = {
            node.func.id
            for node in ast.walk(metrics_fn)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        for forbidden in ("sum", "min", "max", "len", "sorted"):
            assert forbidden not in called, forbidden
        assert not [
            node
            for node in ast.walk(metrics_fn)
            if isinstance(node, (ast.ListComp, ast.GeneratorExp))
        ]
        assert {
            name
            for name in dir(ToolExecutionPersistentQueryService)
            if not name.startswith("_")
        } == {
            "list_recent",          # History 分页
            "list_by_request_id",   # Step 41：Assistant Trace 的 Tool 数据源
            "metrics",              # Step 33
            "repository",
        }

    def test_c39_8_no_select_star_or_unrelated_columns(self) -> None:
        from backend.app.db.tool_execution_repository import (
            ToolExecutionRepository,
        )

        sql = str(
            ToolExecutionRepository().build_metrics_select()
        ).upper()

        assert "SELECT *" not in sql
        for forbidden in (
            "REQUEST_ID", "TOOL_CALL_ID", "ERROR_CODE", "ERROR_TYPE",
            "STARTED_AT", "FINISHED_AT",
        ):
            assert forbidden not in sql, forbidden

    def test_c39_9_no_fuzzy_matching(self) -> None:
        for key in ("tool_repository", "persistent_query", "api"):
            identifiers = _identifiers(key)
            for forbidden in (
                "like", "ilike", "regexp", "contains", "startswith",
                "match", "similar", "fuzzy",
            ):
                assert forbidden not in identifiers, (key, forbidden)

    def test_c39_10_no_fallback_or_merge(self) -> None:
        """DB 失败 → 502（不回退运行时的内存指标）。"""
        from fastapi.testclient import TestClient

        from backend.app.api import orchestrator_chat as root
        from backend.app.api import tool_observability as api
        from backend.app.db.tool_execution_repository import (
            ToolExecutionRepositoryError,
        )
        from backend.app.main import app

        root._TOOL_EXECUTION_COLLECTOR.clear()
        original = api.get_tool_execution_persistent_query_service
        try:
            class _Broken:
                def list_recent(self, **kwargs):
                    raise ToolExecutionRepositoryError("db down")

                def metrics(self, **kwargs):
                    raise ToolExecutionRepositoryError("db down")

            api.get_tool_execution_persistent_query_service = (
                lambda: _Broken()
            )
            with TestClient(app) as client:
                response = client.get(f"{_METRICS_PATH}?project_id=project-a")
                runtime = client.get("/api/observability/tools/metrics")
            assert response.status_code == 502
            assert "total_count" not in response.text
            assert runtime.status_code == 200     # 运行时端点不受影响
        finally:
            api.get_tool_execution_persistent_query_service = original
            root._TOOL_EXECUTION_COLLECTOR.clear()

    def test_c39_11_response_excludes_sensitive_fields(self) -> None:
        from backend.app.api.tool_observability import (
            ToolObservabilityMetricsResponse,
        )

        fields = set(ToolObservabilityMetricsResponse.model_fields)

        assert fields == _METRICS_RESPONSE_FIELDS
        for forbidden in (
            "request_id", "round", "tool_name", "started_at", "finished_at",
            "project_id", "tool_call_id", "error_code", "error_type",
            "arguments", "result", "sql", "rows", "query", "id",
        ):
            assert forbidden not in fields, forbidden

    def test_c39_12_no_limit_or_offset(self) -> None:
        import inspect

        from backend.app.api.tool_observability import (
            persistent_tool_execution_metrics,
        )

        parameters = set(
            inspect.signature(persistent_tool_execution_metrics).parameters
        )

        assert parameters == {"project_id", "tool_name", "success"}
        for forbidden in ("limit", "offset", "cursor", "page"):
            assert forbidden not in parameters, forbidden
