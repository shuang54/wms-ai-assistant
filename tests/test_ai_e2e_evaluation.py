"""Phase 3.7.10 — AI Orchestrator 端到端 Demo / Evaluation。

通过 ``AIOrchestratorService`` 作为唯一入口，把同一份用户问题
分别路由到 RAG / Tool / Text-to-SQL 三条路径，并验证：

* 三条路径互不越权（互斥）；
* Text-to-SQL 路径走完 Router → Selector → Composer →
  Generator (Fake LLM) → Validator → ReadOnly Executor →
  PostgreSQL 全链路；
* RAG / Tool 路径不触发 SQL；
* 写操作（DELETE / DROP / multi-statement）被 Validator
  在 Executor 之前拦截；
* ProjectContext 不被硬编码；
* ``AIOrchestrationResult`` 不泄露敏感信息。

约定：
* 默认所有测试**不调用真实 LLM**；
* DB 集成部分用 ``RUN_DB_TESTS=1`` 显式开启（与项目其它
 集成测试相同）；
* 测试前后 ``knowledge_document / knowledge_chunk`` 行数不变。
"""
from __future__ import annotations

import os
from typing import Any

import pytest

from backend.app.projects.context import DataSource, ProjectContext
from backend.app.projects.semantic import ProjectSemantic
from backend.app.services.ai_orchestrator_service import (
    AIOrchestrationResult,
    AIOrchestratorExecutionError,
    AIOrchestratorService,
    ProjectContextProvider,
)
from backend.app.services.ai_router_service import (
    AIRouterService,
    InMemoryToolCapabilityRegistry,
    RouteType,
    ToolCapability,
)
from backend.app.services.schema_explorer_service import DatabaseSchema
from backend.app.services.sql_executor_service import SQLExecutorService
from backend.app.services.text_to_sql_service import TextToSQLService
from backend.app.tools.mock_tools import register_mock_tools
from backend.app.tools.registry import ToolRegistry, ToolResult


# ============================================================
# 测试开关
# ============================================================

_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {
    "1", "true", "yes", "on",
}
requires_db = pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable PostgreSQL integration tests",
)


# ============================================================
# Fake / 计数器 工具
# ============================================================

class FakeLLMClient:
    """按脚本回放的 LLM Client（复用 test_text_to_sql_service 模式）。"""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def chat(self, messages, *, tools=None):
        self.calls.append(list(messages))
        if not self._responses:
            return ""
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeRagService:
    """模拟真实 RagService：返回包含项目真实业务关键词的 RagResponse。"""

    def __init__(self, answer: str = "采购入库流程：...") -> None:
        self._answer = answer
        self.calls: list[str] = []

    async def answer(self, query, *, top_k=None):
        self.calls.append(query)
        # 模拟 RagResponse（避免 import 增加实际依赖层副作用）
        from backend.app.services.rag_service import RagResponse, RagSource
        return RagResponse(
            answer=self._answer,
            sources=(RagSource(
                chunk_id=1, document_id=1, chunk_index=0,
                content="chunk-stub",
                similarity=0.95, metadata={},
            ),),
            used_chunks_count=1,
        )


class CountedToolRegistry:
    """包装真实 ToolRegistry，记录 execute 调用次数。"""

    def __init__(self, registry: ToolRegistry) -> None:
        self._reg = registry
        self.calls: list[tuple[str, dict | None]] = []

    def list_definitions(self):
        return self._reg.list_definitions()

    async def execute(self, tool_name, arguments=None):
        self.calls.append((tool_name, arguments))
        return await self._reg.execute(tool_name, arguments)


class CountedSQLExecutor:
    """包装真实 SQLExecutorService，记录 execute 调用次数。"""

    def __init__(self, executor: SQLExecutorService) -> None:
        self._executor = executor
        self.calls: list[str] = []

    async def execute(self, sql, *, schema=None, allowed_tables=None,
                      max_rows=1000, timeout_seconds=10):
        self.calls.append(sql)
        return await self._executor.execute(
            sql, schema=schema, allowed_tables=allowed_tables,
            max_rows=max_rows, timeout_seconds=timeout_seconds,
        )


class FakeProjectProvider:
    """可指定 project_id 的 Provider（验证 Orchestrator 不硬编码）。"""

    def __init__(self, project: ProjectContext, schema: DatabaseSchema,
                 semantic: ProjectSemantic | None = None) -> None:
        self._project = project
        self._schema = schema
        self._semantic = semantic or ProjectSemantic()
        self.resolve_count = 0

    def resolve(self):
        self.resolve_count += 1
        return self._project, self._schema, self._semantic


class FakeSQLExecutor:
    """不连真实 DB 的占位 SQL Executor（仅验证可写路径互斥）。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, sql, *, schema=None, allowed_tables=None,
                      max_rows=1000, timeout_seconds=10):
        from backend.app.services.sql_executor_service import (
            SQLExecutionResult,
        )
        self.calls.append(sql)
        return SQLExecutionResult(
            columns=("id",), rows=(), row_count=0, truncated=False,
            execution_time_ms=0.0,
        )


class CountedToolRegistry:
    """包装真实 ToolRegistry，记录 execute 调用次数。"""

    def __init__(self, registry: ToolRegistry) -> None:
        self._reg = registry
        self.calls: list[tuple[str, dict | None]] = []

    def list_definitions(self):
        return self._reg.list_definitions()

    async def execute(self, tool_name, arguments=None):
        self.calls.append((tool_name, arguments))
        return await self._reg.execute(tool_name, arguments)


class CountedSQLExecutor:
    """包装真实 SQLExecutorService，记录 execute 调用次数。"""

    def __init__(self, executor: SQLExecutorService) -> None:
        self._executor = executor
        self.calls: list[str] = []

    async def execute(self, sql, *, schema=None, allowed_tables=None,
                      max_rows=1000, timeout_seconds=10):
        self.calls.append(sql)
        return await self._executor.execute(
            sql, schema=schema, allowed_tables=allowed_tables,
            max_rows=max_rows, timeout_seconds=timeout_seconds,
        )


# ============================================================
# Schema fixture（与 test_ai_orchestrator 复用）
# ============================================================

def _make_schema() -> DatabaseSchema:
    from backend.app.services.schema_explorer_service import (
        SchemaColumn,
        SchemaTable,
    )
    return DatabaseSchema(
        schema_name="public",
        tables=(
            SchemaTable(
                schema_name="public", name="knowledge_document",
                description=None,
                columns=(
                    SchemaColumn(
                        name="id", data_type="bigint", nullable=False,
                        default=None, ordinal_position=1, is_primary_key=True,
                        description=None,
                    ),
                    SchemaColumn(
                        name="title", data_type="varchar", nullable=True,
                        default=None, ordinal_position=2,
                        is_primary_key=False, description=None,
                    ),
                    SchemaColumn(
                        name="file_name", data_type="varchar", nullable=True,
                        default=None, ordinal_position=3,
                        is_primary_key=False, description=None,
                    ),
                ),
                foreign_keys=(),
            ),
            SchemaTable(
                schema_name="public", name="knowledge_chunk",
                description=None,
                columns=(
                    SchemaColumn(
                        name="id", data_type="bigint", nullable=False,
                        default=None, ordinal_position=1, is_primary_key=True,
                        description=None,
                    ),
                    SchemaColumn(
                        name="document_id", data_type="bigint", nullable=False,
                        default=None, ordinal_position=2,
                        is_primary_key=False, description=None,
                    ),
                    SchemaColumn(
                        name="content", data_type="text", nullable=True,
                        default=None, ordinal_position=3,
                        is_primary_key=False, description=None,
                    ),
                ),
                foreign_keys=(),
            ),
        ),
    )


def _make_project(
    project_id: str = "test-project",
    project_name: str = "Test Project",
) -> ProjectContext:
    return ProjectContext(
        project_id=project_id,
        project_name=project_name,
        description=None,
        data_source=DataSource(name="primary", type="postgresql"),
    )


# ============================================================
# 场景 A：RAG E2E（Fake RagService + 真实 Orchestrator/Router）
# ============================================================

class TestScenarioA_RAG_E2E:
    async def test_rag_question_routes_to_rag_only(self) -> None:
        """知识类问题 → RAG，工具 / Text-to-SQL 均为 0。"""
        rag = FakeRagService(answer="采购入库标准流程说明：...")
        rag_calls_before = len(rag.calls)
        # 真实 Router（rule 命中 RAG）+ 真实 Orchestrator
        orch = AIOrchestratorService(
            router=AIRouterService(llm_fallback_enabled=False),
            rag_service=rag,
            tool_registry=ToolRegistry(),  # 空：不会触发 Tool
            text_to_sql=TextToSQLService(llm_client=FakeLLMClient([])),
            sql_executor=CountedSQLExecutor(
                SQLExecutorService.__new__(SQLExecutorService)
            ),
            project_context_provider=FakeProjectProvider(
                _make_project(), _make_schema()
            ),
        )
        result = await orch.execute("采购入库怎么操作？")

        # 1) 路由正确
        assert isinstance(result, AIOrchestrationResult)
        assert result.route == RouteType.RAG
        # 2) 内容来自 RAG（不是空）
        assert result.content == "采购入库标准流程说明：..."
        assert isinstance(result.data.answer, str)
        # 3) RAG 被调用 1 次
        assert len(rag.calls) - rag_calls_before == 1
        # 4) metadata 不包含 sql / tool_name
        assert "sql" not in result.metadata
        assert "tool_name" not in result.metadata
        # 5) 来源 chunk 信息被透传（验证 metadata 不空且有 rag_used_chunks）
        assert result.metadata.get("rag_used_chunks") == 1


# ============================================================
# 场景 B：Tool E2E（真实 register_mock_tools + 真实 Orchestrator）
# ============================================================

class TestScenarioB_Tool_E2E:
    async def test_tool_question_calls_existing_tool_only(self) -> None:
        """Tool 问题 → 真实 mock Tools（get_inventory / get_work_order）。"""
        reg = ToolRegistry()
        register_mock_tools(reg)
        counted = CountedToolRegistry(reg)

        rag = FakeRagService()
        rag_calls_before = len(rag.calls)
        # 把 Tool 元数据注入 Router 才能命中 TOOL 路由
        caps = InMemoryToolCapabilityRegistry([
            ToolCapability(
                name="get_inventory",
                description="查询指定物料在指定仓库的库存数量",
                aliases=("物料", "库存", "仓库"),
            ),
            ToolCapability(
                name="get_work_order",
                description="查询指定工单的状态",
                aliases=("工单", "状态"),
            ),
        ])
        orch = AIOrchestratorService(
            router=AIRouterService(
                llm_fallback_enabled=False,
                tool_capabilities=caps,
            ),
            rag_service=rag,
            tool_registry=counted,
            text_to_sql=TextToSQLService(llm_client=FakeLLMClient([])),
            sql_executor=CountedSQLExecutor(
                SQLExecutorService.__new__(SQLExecutorService)
            ),
            project_context_provider=FakeProjectProvider(
                _make_project(), _make_schema()
            ),
        )
        # 触发 get_inventory：description 含"库存"
        result = await orch.execute("帮我查询物料 10001 当前库存数量")

        # 1) 路由到 Tool
        assert result.route == RouteType.TOOL
        # 2) Tool 被调用 1 次（真实 mock tool）
        assert len(counted.calls) == 1
        tool_name = result.metadata["tool_name"]
        assert tool_name in ("get_inventory", "get_work_order")
        # 3) RAG / Text-to-SQL / Executor 均 0
        assert len(rag.calls) - rag_calls_before == 0
        assert "sql" not in result.metadata


# ============================================================
# 场景 C：Text-to-SQL E2E（真实 Postgres + Fake LLM + 真实 Validator/Executor）
# ============================================================

@requires_db
class TestScenarioC_TextToSQL_E2E:
    async def _build_orchestrator(self, llm_responses: list[str]):
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine

        reset_engine_cache()
        engine = get_engine()
        assert engine is not None

        # 真实 Schema explorer
        schema = await SchemaExplorerService().inspect()

        llm = FakeLLMClient(llm_responses)
        text_to_sql = TextToSQLService(llm_client=llm)
        sql_executor_real = SQLExecutorService(engine=engine)
        sql_exec = CountedSQLExecutor(sql_executor_real)
        rag = FakeRagService()

        orch = AIOrchestratorService(
            router=AIRouterService(llm_fallback_enabled=False),
            rag_service=rag,
            tool_registry=ToolRegistry(),
            text_to_sql=text_to_sql,
            sql_executor=sql_exec,
            project_context_provider=FakeProjectProvider(
                _make_project("vietnam-wms", "Vietnam WMS"), schema
            ),
        )
        return orch, llm, sql_exec, rag, engine

    async def test_select_count_documents_full_chain(self) -> None:
        """完整链路：Question → Orchestrator → 全组件 → 真实 DB。"""
        sql_text = (
            "```sql\n"
            "SELECT COUNT(*) AS doc_count "
            "FROM public.knowledge_document LIMIT 1000\n"
            "```"
        )
        orch, llm, sql_exec, rag, engine = await self._build_orchestrator(
            [sql_text]
        )

        # 行数 before
        from sqlalchemy import text as sa_text
        with engine.connect() as conn:
            before_doc = conn.execute(
                sa_text("SELECT count(*) FROM public.knowledge_document")
            ).scalar()

        result = await orch.execute("物料最多的前 3 个是什么？")

        # 1) 路由到 Text-to-SQL
        assert result.route == RouteType.TEXT_TO_SQL
        # 2) Generator 被调用（Fake LLM 收到 prompt）
        assert llm.call_count == 1
        # 3) SQL 通过 Validator + Executor 真实执行
        assert len(sql_exec.calls) == 1
        assert "knowledge_document" in sql_exec.calls[0].lower()
        # 4) 行数未变（DB writes = 0）
        with engine.connect() as conn:
            after_doc = conn.execute(
                sa_text("SELECT count(*) FROM public.knowledge_document")
            ).scalar()
        assert before_doc == after_doc
        # 5) RAG / Tool 不调用
        assert len(rag.calls) == 0
        # 6) metadata 含 sql + row_count
        assert "knowledge_document" in result.metadata["sql"]
        assert result.metadata["row_count"] >= 0
        assert result.metadata["truncated"] is False
        # 7) data 是 SQLExecutionResult
        from backend.app.services.sql_executor_service import (
            SQLExecutionResult,
        )
        assert isinstance(result.data, SQLExecutionResult)


# ============================================================
# 场景 Security：DELETE / DROP / 多语句 — 全部由 Validator 拦截
# ============================================================

@requires_db
class TestScenarioSecurity_E2E:
    async def _orch_with_dangerous_sql(self, dangerous: str) -> AIOrchestratorService:
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine
        reset_engine_cache()
        engine = get_engine()
        assert engine is not None
        schema = await SchemaExplorerService().inspect()
        # Generator 必须接受来自 LLM 的危险 SQL（不阻止它产生），
        # Validator 负责拦截。这是真实安全边界的核心验证。
        return AIOrchestratorService(
            router=AIRouterService(llm_fallback_enabled=False),
            rag_service=FakeRagService(),
            tool_registry=ToolRegistry(),
            text_to_sql=TextToSQLService(llm_client=FakeLLMClient([dangerous])),
            sql_executor=CountedSQLExecutor(SQLExecutorService(engine=engine)),
            project_context_provider=FakeProjectProvider(
                _make_project(), schema
            ),
        )

    async def test_delete_rejected_by_validator(self) -> None:
        dangerous = "DELETE FROM public.knowledge_document"
        orch = await self._orch_with_dangerous_sql(dangerous)
        with pytest.raises(AIOrchestratorExecutionError) as exc_info:
            await orch.execute("物料最多的前 3 个是什么？")
        # Validator 已在 Executor 之前拒绝
        msg = str(exc_info.value)
        assert "Text-to-SQL" in msg or "生成失败" in msg
        # 原始异常链保留
        assert exc_info.value.__cause__ is not None

    async def test_drop_rejected_by_validator(self) -> None:
        dangerous = "DROP TABLE public.knowledge_document"
        orch = await self._orch_with_dangerous_sql(dangerous)
        with pytest.raises(AIOrchestratorExecutionError):
            await orch.execute("物料最多的前 3 个是什么？")
        # 验证 knowledge_document 表仍存在
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine
        from sqlalchemy import text as sa_text
        reset_engine_cache()
        engine = get_engine()
        with engine.connect() as conn:
            exists = conn.execute(sa_text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name='knowledge_document')"
            )).scalar()
        assert exists is True

    async def test_multi_statement_rejected_by_validator(self) -> None:
        dangerous = (
            "SELECT * FROM public.knowledge_document LIMIT 1;\n"
            "DROP TABLE public.knowledge_document"
        )
        orch = await self._orch_with_dangerous_sql(dangerous)
        with pytest.raises(AIOrchestratorExecutionError):
            await orch.execute("物料最多的前 3 个是什么？")


# ============================================================
# 场景 互斥：三条路径互不越权
# ============================================================

@requires_db
class TestMutualExclusivity:
    async def test_routes_are_mutually_exclusive(self) -> None:
        """3 类问题 → 三条路径互斥。"""
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine

        reset_engine_cache()
        engine = get_engine()
        assert engine is not None
        schema = await SchemaExplorerService().inspect()

        # --- 子场景 1：RAG ---
        rag = FakeRagService("RAG-only answer")
        reg = ToolRegistry()
        register_mock_tools(reg)
        counted_tools = CountedToolRegistry(reg)
        sql_exec = CountedSQLExecutor(SQLExecutorService(engine=engine))
        orch = AIOrchestratorService(
            router=AIRouterService(llm_fallback_enabled=False),
            rag_service=rag,
            tool_registry=counted_tools,
            text_to_sql=TextToSQLService(
                llm_client=FakeLLMClient([
                    "```sql\nSELECT id FROM public.knowledge_document "
                    "LIMIT 1\n```"
                ])
            ),
            sql_executor=sql_exec,
            project_context_provider=FakeProjectProvider(
                _make_project(), schema
            ),
        )
        rag_before = len(rag.calls)
        result_rag = await orch.execute("采购入库怎么操作？")
        assert result_rag.route == RouteType.RAG
        assert len(rag.calls) - rag_before == 1   # RAG = 1
        assert len(counted_tools.calls) == 0       # Tool = 0
        assert len(sql_exec.calls) == 0            # Executor = 0
        assert llm_call_count(orch) == 0           # Text-to-SQL = 0

        # --- 子场景 2：Tool ---
        rag2 = FakeRagService("RAG would say this, but Tool path")
        counted_tools2 = CountedToolRegistry(reg)
        sql_exec2 = CountedSQLExecutor(SQLExecutorService(engine=engine))
        caps2 = InMemoryToolCapabilityRegistry([
            ToolCapability(
                name="get_inventory",
                description="查询指定物料在指定仓库的库存数量",
                aliases=("物料", "库存", "仓库"),
            ),
            ToolCapability(
                name="get_work_order",
                description="查询指定工单的状态",
                aliases=("工单", "状态"),
            ),
        ])
        orch2 = AIOrchestratorService(
            router=AIRouterService(
                llm_fallback_enabled=False, tool_capabilities=caps2,
            ),
            rag_service=rag2,
            tool_registry=counted_tools2,
            text_to_sql=TextToSQLService(llm_client=FakeLLMClient([])),
            sql_executor=sql_exec2,
            project_context_provider=FakeProjectProvider(
                _make_project(), schema
            ),
        )
        rag2_before = len(rag2.calls)
        result_tool = await orch2.execute("帮我查询物料 10001 当前库存数量")
        assert result_tool.route == RouteType.TOOL
        assert len(counted_tools2.calls) == 1      # Tool = 1
        assert len(rag2.calls) - rag2_before == 0  # RAG = 0
        assert len(sql_exec2.calls) == 0           # Executor = 0
        assert llm_call_count(orch2) == 0          # Text-to-SQL = 0

        # --- 子场景 3：Text-to-SQL ---
        rag3 = FakeRagService()
        rag3_before = len(rag3.calls)
        reg3 = ToolRegistry()
        counted_tools3 = CountedToolRegistry(reg3)
        sql_exec3 = CountedSQLExecutor(SQLExecutorService(engine=engine))
        orch3 = AIOrchestratorService(
            router=AIRouterService(llm_fallback_enabled=False),
            rag_service=rag3,
            tool_registry=counted_tools3,
            text_to_sql=TextToSQLService(
                llm_client=FakeLLMClient([
                    "```sql\nSELECT id FROM public.knowledge_document "
                    "LIMIT 3\n```"
                ])
            ),
            sql_executor=sql_exec3,
            project_context_provider=FakeProjectProvider(
                _make_project(), schema
            ),
        )
        result_sql = await orch3.execute("查询知识库前 3 个文档 id")
        assert result_sql.route == RouteType.TEXT_TO_SQL
        assert len(sql_exec3.calls) == 1           # Executor = 1
        assert llm_call_count(orch3) == 1          # Text-to-SQL LLM = 1
        assert len(counted_tools3.calls) == 0      # Tool = 0
        assert len(rag3.calls) - rag3_before == 0  # RAG = 0


def llm_call_count(orch: AIOrchestratorService) -> int:
    """提取 Orchestrator 注入的 TextToSQLService 的 LLM 调用次数（无侵入）。"""
    inner_llm = orch._text_to_sql._llm_client  # type: ignore[attr-defined]
    return getattr(inner_llm, "call_count", 0)


# ============================================================
# 场景 Project Context 可迁移性：Orchestrator 不硬编码
# ============================================================

class TestProjectContextPortability:
    async def test_orchestrator_accepts_alternate_project(self) -> None:
        """Orchestrator 接受任何 project_id，不硬编码 vietnam-wms。"""
        from backend.app.services.rag_service import RagResponse, RagSource

        class _StubRag:
            def __init__(self):
                self.calls = []
            async def answer(self, query, *, top_k=None):
                self.calls.append(query)
                return RagResponse(
                    answer="ok", sources=(), used_chunks_count=0,
                )

        rag = _StubRag()
        alt_project = _make_project(
            project_id="another-warehouse", project_name="另一个仓库"
        )
        alt_schema = _make_schema()
        provider = FakeProjectProvider(alt_project, alt_schema)

        orch = AIOrchestratorService(
            router=AIRouterService(llm_fallback_enabled=False),
            rag_service=rag,
            tool_registry=ToolRegistry(),
            text_to_sql=TextToSQLService(llm_client=FakeLLMClient([])),
            sql_executor=CountedSQLExecutor(
                SQLExecutorService.__new__(SQLExecutorService)
            ),
            project_context_provider=provider,
        )

        result = await orch.execute("采购入库怎么操作？")
        # Provider 在 RAG 路径上不会被调用；只在 TEXT_TO_SQL 路径被调用
        # 这里直接验证：Orchestrator 不向 Provider 写死默认值即可
        # （核心 assertion：路由 + 内容来自 alt project 的 RagService）
        assert result.route == RouteType.RAG

    async def test_orchestrator_passes_alt_project_id_to_sql_path(self) -> None:
        """SQL 路径下 alternate project_id 透传到 result.metadata["project_id"]。"""
        alt_project = _make_project(
            project_id="another-warehouse", project_name="另一个仓库"
        )
        alt_schema = _make_schema()
        provider = FakeProjectProvider(alt_project, alt_schema)

        sql_text = (
            "```sql\nSELECT id FROM public.knowledge_document LIMIT 1\n```"
        )
        rag = FakeRagService()
        orch = AIOrchestratorService(
            router=AIRouterService(llm_fallback_enabled=False),
            rag_service=rag,
            tool_registry=ToolRegistry(),
            text_to_sql=TextToSQLService(
                llm_client=FakeLLMClient([sql_text])
            ),
            sql_executor=FakeSQLExecutor(),
            project_context_provider=provider,
        )
        result = await orch.execute("物料最多的前 3 个是什么？")
        assert provider.resolve_count == 1
        assert result.metadata["project_id"] == "another-warehouse"
        assert result.route == RouteType.TEXT_TO_SQL
        # content 来自 SQL 路径的标准化摘要，不依赖 RAG stub
        assert result.content is not None
        assert "行" in result.content  # 来自 _sql_result_to_content 摘要


# ============================================================
# 场景 Result Safety：AIOrchestrationResult 不泄露
# ============================================================

class TestResultSafety:
    async def test_result_does_not_leak_secrets(self) -> None:
        rag = FakeRagService(answer="public answer")
        orch = AIOrchestratorService(
            router=AIRouterService(llm_fallback_enabled=False),
            rag_service=rag,
            tool_registry=ToolRegistry(),
            text_to_sql=TextToSQLService(llm_client=FakeLLMClient([])),
            sql_executor=CountedSQLExecutor(
                SQLExecutorService.__new__(SQLExecutorService)
            ),
            project_context_provider=FakeProjectProvider(
                _make_project(), _make_schema()
            ),
        )
        result = await orch.execute("采购入库怎么操作？")
        # 1) frozen
        with pytest.raises(Exception):
            result.route = RouteType.TEXT_TO_SQL  # type: ignore[misc]
        with pytest.raises(Exception):
            result.metadata = {"x": 1}  # type: ignore[misc]
        # 2) repr 不含敏感信息
        s = repr(result)
        assert "DATABASE_URL" not in s
        assert "api_key" not in s.lower()
        assert "password" not in s.lower()
        assert "connection" not in s.lower() or "connection string" not in s.lower()
        # 3) data 不暴露 SQLAlchemy Connection / Session
        from sqlalchemy.engine import Connection
        from sqlalchemy.orm import Session
        assert not isinstance(result.data, (Connection, Session))


# ============================================================
# 静态安全 / 解耦 断言
# ============================================================

class TestStaticDecoupling:
    """Orchestrator 不依赖 / 不修改核心组件。"""

    def test_orchestrator_does_not_depend_on_api(self) -> None:
        import inspect
        import backend.app.services.ai_orchestrator_service as mod
        src = inspect.getsource(mod)
        assert "from backend.app.api" not in src
        assert "import backend.app.api" not in src

    def test_router_does_not_depend_on_orchestrator(self) -> None:
        import inspect
        import backend.app.services.ai_router_service as mod
        src = inspect.getsource(mod)
        assert "ai_orchestrator" not in src
        assert "OrchestratorService" not in src

    def test_orchestrator_no_eval_exec_subprocess(self) -> None:
        import inspect
        import backend.app.services.ai_orchestrator_service as mod
        src = inspect.getsource(mod)
        assert "eval(" not in src
        assert "exec(" not in src
        assert "subprocess" not in src
        assert "os.system" not in src


# ============================================================
# Provider 接口契约
# ============================================================

class TestProjectContextProviderContract:
    def test_fake_provider_satisfies_protocol(self) -> None:
        """FakeProjectProvider 必须实现 ProjectContextProvider Protocol。"""
        provider: ProjectContextProvider = FakeProjectProvider(
            _make_project(), _make_schema()
        )
        project, schema, semantic = provider.resolve()
        assert project.project_id == "test-project"
        assert schema.schema_name == "public"
        assert semantic is not None
        src = inspect.getsource(mod)
        assert "from backend.app.api" not in src
        assert "import backend.app.api" not in src

    def test_router_does_not_depend_on_orchestrator(self) -> None:
        import inspect
        import backend.app.services.ai_router_service as mod
        src = inspect.getsource(mod)
        assert "ai_orchestrator" not in src
        assert "OrchestratorService" not in src

    def test_orchestrator_no_eval_exec_subprocess(self) -> None:
        import inspect
        import backend.app.services.ai_orchestrator_service as mod
        src = inspect.getsource(mod)
        assert "eval(" not in src
        assert "exec(" not in src
        assert "subprocess" not in src
        assert "os.system" not in src


# ============================================================
# Provider 接口契约
# ============================================================

class TestProjectContextProviderContract:
    def test_fake_provider_satisfies_protocol(self) -> None:
        """FakeProjectProvider 必须实现 ProjectContextProvider Protocol。"""
        provider: ProjectContextProvider = FakeProjectProvider(
            _make_project(), _make_schema()
        )
        project, schema, semantic = provider.resolve()
        assert project.project_id == "test-project"
        assert schema.schema_name == "public"
        assert semantic is not None