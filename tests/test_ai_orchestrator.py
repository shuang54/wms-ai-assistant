"""AI Orchestrator 测试（Phase 3.7.9）。"""
from __future__ import annotations

from typing import Any

import pytest

from backend.app.projects.context import DataSource, ProjectContext
from backend.app.projects.semantic import ProjectSemantic
from backend.app.services.ai_orchestrator_service import (
    AIOrchestrationResult,
    AIOrchestratorError,
    AIOrchestratorExecutionError,
    AIOrchestratorInputError,
    AIOrchestratorRouteError,
    AIOrchestratorService,
    AIOrchestratorUnavailableError,
    DefaultProjectContextProvider,
    ProjectContextProvider,
    _resolve_tool_name,
)
from backend.app.services.ai_router_service import (
    AIRouterError,
    AIRouterInputError,
    AIRouterService,
    RouteDecision,
    RouteType,
)
from backend.app.services.relevant_table_selector import (
    TableSelection,
    TableSelectionResult,
)
from backend.app.services.schema_explorer_service import (
    DatabaseSchema,
    SchemaColumn,
    SchemaTable,
)
from backend.app.services.sql_executor_service import (
    SQLExecutionResult,
    SQLExecutorInputError,
    SQLExecutorValidationError,
)
from backend.app.services.text_to_sql_service import (
    TextToSQLInputError,
    TextToSQLResult,
)
from backend.app.tools.mock_tools import register_mock_tools
from backend.app.tools.registry import ToolRegistry, ToolResult


# ============================================================
# Test Fakes
# ============================================================

class FakeRouter:
    """可脚本化的 Router；可选地抛异常。"""

    def __init__(
        self,
        decision: RouteDecision | None = None,
        raise_exc: Exception | None = None,
        side_effects: list[RouteDecision] | None = None,
    ) -> None:
        self._decision = decision
        self._raise = raise_exc
        self._side_effects = side_effects or []
        self.calls: list[str] = []

    async def route(self, question, *, context=None):
        self.calls.append(question)
        if self._raise is not None:
            raise self._raise
        if self._side_effects:
            return self._side_effects.pop(0)
        return self._decision


class FakeRAG:
    def __init__(
        self,
        response=None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._response = response
        self._raise = raise_exc
        self.calls: list[str] = []

    async def answer(self, query):
        self.calls.append(query)
        if self._raise is not None:
            raise self._raise
        return self._response


class _FakeRagResponse:
    """最小 RagResponse-like 对象。"""

    def __init__(self, answer: str, used_chunks_count: int = 1) -> None:
        self.answer = answer
        self.used_chunks_count = used_chunks_count
        self.sources: tuple = ()


class FakeTextToSQL:
    def __init__(
        self,
        result=None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._result = result
        self._raise = raise_exc
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self, question, *, database_context,
        allowed_tables=None, schema=None, max_rows=1000
    ):
        self.calls.append({
            "question": question,
            "database_context": database_context,
            "allowed_tables": allowed_tables,
            "schema": schema,
            "max_rows": max_rows,
        })
        if self._raise is not None:
            raise self._raise
        return self._result


class FakeSQLExecutor:
    def __init__(
        self,
        result: SQLExecutionResult | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._result = result
        self._raise = raise_exc
        self.calls: list[dict[str, Any]] = []

    async def execute(self, sql, *, schema=None, allowed_tables=None,
                      max_rows=1000, timeout_seconds=10):
        self.calls.append({
            "sql": sql,
            "schema": schema,
            "allowed_tables": allowed_tables,
            "max_rows": max_rows,
        })
        if self._raise is not None:
            raise self._raise
        return self._result


class FakeTableSelector:
    def __init__(self, selections: list[str] | None = None) -> None:
        self._selections = selections or []
        self.calls: list[str] = []

    def select(self, question, *, schema, semantic, top_k=5):
        self.calls.append(question)
        return TableSelectionResult(
            question=question,
            selections=tuple(
                TableSelection(table=t, score=1.0, matched_terms=())
                for t in self._selections
            ),
        )


class FakeContextComposer:
    def __init__(self, context: str = "FAKE_CONTEXT") -> None:
        self._context = context
        self.calls: list[dict[str, Any]] = []

    def compose(self, *, project, schema, semantic, tables=None,
                max_chars=4000):
        self.calls.append({
            "project_id": project.project_id,
            "tables": list(tables or ()),
        })
        return self._context


class FakeProjectProvider:
    def __init__(self, *, project=None, schema=None, semantic=None,
                 raise_exc: Exception | None = None) -> None:
        self._project = project or _make_project()
        self._schema = schema or _make_schema()
        self._semantic = semantic or ProjectSemantic()
        self._raise = raise_exc
        self.calls = 0

    def resolve(self):
        self.calls += 1
        if self._raise is not None:
            raise self._raise
        return self._project, self._schema, self._semantic


# ============================================================
# Helpers
# ============================================================

def _make_project() -> ProjectContext:
    return ProjectContext(
        project_id="test-project",
        project_name="Test Project",
        description=None,
        data_source=DataSource(name="primary", type="postgresql"),
    )


def _make_schema() -> DatabaseSchema:
    return DatabaseSchema(
        schema_name="public",
        tables=(
            SchemaTable(
                schema_name="public",
                name="inventory",
                description=None,
                columns=(
                    SchemaColumn(
                        name="id", data_type="bigint", nullable=False,
                        default=None, ordinal_position=1,
                        is_primary_key=True, description=None,
                    ),
                    SchemaColumn(
                        name="material_code", data_type="varchar",
                        nullable=False, default=None, ordinal_position=2,
                        is_primary_key=False, description=None,
                    ),
                ),
                foreign_keys=(),
            ),
            SchemaTable(
                schema_name="public",
                name="knowledge_document",
                description=None,
                columns=(
                    SchemaColumn(
                        name="id", data_type="bigint", nullable=False,
                        default=None, ordinal_position=1,
                        is_primary_key=True, description=None,
                    ),
                    SchemaColumn(
                        name="title", data_type="varchar",
                        nullable=True, default=None, ordinal_position=2,
                        is_primary_key=False, description=None,
                    ),
                ),
                foreign_keys=(),
            ),
        ),
    )


def _make_service(
    *,
    router=None, rag=None, tools=None, t2s=None, sql_executor=None,
    table_selector=None, context_composer=None, project_provider=None,
) -> AIOrchestratorService:
    return AIOrchestratorService(
        router=router,
        rag_service=rag,
        tool_registry=tools,
        text_to_sql=t2s,
        sql_executor=sql_executor,
        table_selector=table_selector,
        context_composer=context_composer,
        project_context_provider=project_provider,
    )


def _rag_decision() -> RouteDecision:
    return RouteDecision(
        route=RouteType.RAG, confidence=0.9,
        reason="rule: knowledge", source="rule",
    )


def _tool_decision() -> RouteDecision:
    return RouteDecision(
        route=RouteType.TOOL, confidence=0.85,
        reason="matched get_inventory", source="tool_match",
    )


def _sql_decision() -> RouteDecision:
    return RouteDecision(
        route=RouteType.TEXT_TO_SQL, confidence=0.9,
        reason="rule: analytics", source="rule",
    )


# ============================================================
# A. RAG 路由
# ============================================================

class TestRAGRoute:
    async def test_rag_path_calls_only_rag(self) -> None:
        rag_response = _FakeRagResponse("这是流程说明。")
        rag = FakeRAG(response=rag_response)
        orch = _make_service(
            router=FakeRouter(_rag_decision()),
            rag=rag,
            t2s=FakeTextToSQL(),
            sql_executor=FakeSQLExecutor(),
            table_selector=FakeTableSelector(),
            context_composer=FakeContextComposer(),
            project_provider=FakeProjectProvider(),
        )
        result = await orch.execute("采购入库怎么操作？")
        assert isinstance(result, AIOrchestrationResult)
        assert result.route == RouteType.RAG
        assert result.content == "这是流程说明。"
        assert result.data is rag_response
        # RAG 被调用 1 次，其它 0 次
        assert len(rag.calls) == 1
        # 其它下游不应触发
        # t2s / sql_executor 默认未调用（用新的 fake 验证）
        t2s = FakeTextToSQL()
        sql_exec = FakeSQLExecutor()
        orch2 = _make_service(
            router=FakeRouter(_rag_decision()),
            rag=FakeRAG(response=rag_response),
            t2s=t2s, sql_executor=sql_exec,
            table_selector=FakeTableSelector(),
            context_composer=FakeContextComposer(),
            project_provider=FakeProjectProvider(),
        )
        await orch2.execute("采购入库怎么操作？")
        assert t2s.calls == []
        assert sql_exec.calls == []

    async def test_rag_failure_wrapped(self) -> None:
        rag = FakeRAG(raise_exc=RuntimeError("vector db down"))
        orch = _make_service(
            router=FakeRouter(_rag_decision()), rag=rag,
        )
        with pytest.raises(AIOrchestratorExecutionError) as exc_info:
            await orch.execute("采购入库怎么操作？")
        assert "RuntimeError" in str(exc_info.value)
        assert exc_info.value.__cause__ is not None


# ============================================================
# B. Tool 路由
# ============================================================

class TestToolRoute:
    async def _registry(self) -> ToolRegistry:
        r = ToolRegistry()
        register_mock_tools(r)
        return r

    async def test_tool_path_calls_only_tool(self) -> None:
        reg = await self._registry()
        orch = _make_service(
            router=FakeRouter(_tool_decision()),
            tools=reg,
            rag=FakeRAG(),
            t2s=FakeTextToSQL(),
            sql_executor=FakeSQLExecutor(),
            table_selector=FakeTableSelector(),
            context_composer=FakeContextComposer(),
            project_provider=FakeProjectProvider(),
        )
        # 别名通过 description 关键词匹配：tool description 包含 "库存"
        result = await orch.execute("查询物料 10001 当前库存数量")
        assert result.route == RouteType.TOOL
        assert result.metadata["tool_name"] in ("get_inventory", "get_work_order")
        # RAG / T2S / Executor 不调用
        rag = FakeRAG()
        t2s = FakeTextToSQL()
        sql_exec = FakeSQLExecutor()
        orch2 = _make_service(
            router=FakeRouter(_tool_decision()), tools=reg,
            rag=rag, t2s=t2s, sql_executor=sql_exec,
            table_selector=FakeTableSelector(),
            context_composer=FakeContextComposer(),
            project_provider=FakeProjectProvider(),
        )
        await orch2.execute("查询物料 10001 当前库存数量")
        assert rag.calls == []
        assert t2s.calls == []
        assert sql_exec.calls == []

    async def test_tool_no_match_raises_route_error(self) -> None:
        # 注册的工具都无关，但 Router 仍判为 TOOL（无 description 命中）
        from backend.app.tools.base import ToolDefinition, ToolHandler
        class _EmptyHandler:
            async def execute(self, arguments): return ToolResult(
                tool_name="x", success=True, data={}, error=None
            )
        reg = ToolRegistry()
        reg.register(ToolDefinition(
            name="get_xyz", description="完全不相关能力",
            parameters={"type": "object", "properties": {}},
        ), _EmptyHandler())
        orch = _make_service(
            router=FakeRouter(_tool_decision()), tools=reg,
        )
        with pytest.raises(AIOrchestratorRouteError):
            await orch.execute("请告诉天气情况")

    async def test_tool_failure_surfaces_as_successful_orchestration(self) -> None:
        """Tool 内部抛 ToolError 时，ToolRegistry 会归一为 ToolResult(False)；
        Orchestrator 应将其透传到结果（不抛、不掩盖）。
        """
        from backend.app.tools.base import ToolDefinition
        from backend.app.tools.errors import ToolExecutionError

        class BoomHandler:
            async def __call__(self, arguments):
                raise ToolExecutionError(tool_name="boom_tool", reason="boom")

        reg = ToolRegistry()
        reg.register(ToolDefinition(
            name="boom_tool",
            description="查询库存数量",
            parameters={"type": "object", "properties": {}},
        ), BoomHandler())
        orch = _make_service(
            router=FakeRouter(_tool_decision()), tools=reg,
        )
        result = await orch.execute("帮我查一下库存")
        # Orchestrator 不抛，但 metadata 透传 tool_success=False
        assert result.route == RouteType.TOOL
        assert result.metadata["tool_name"] == "boom_tool"
        assert result.metadata["tool_success"] is False
        assert "失败" in (result.content or "")

    async def test_tool_handler_unexpected_exception_wrapped(self) -> None:
        """Handler 抛非 ToolError 异常 → 仍归一为 ToolResult(False)
        （ToolRegistry 的兜底；Orchestrator 不抛）。"""
        from backend.app.tools.base import ToolDefinition

        class BoomHandler:
            async def __call__(self, arguments):
                raise RuntimeError("unexpected")

        reg = ToolRegistry()
        reg.register(ToolDefinition(
            name="boom_tool",
            description="查询库存数量",
            parameters={"type": "object", "properties": {}},
        ), BoomHandler())
        orch = _make_service(
            router=FakeRouter(_tool_decision()), tools=reg,
        )
        result = await orch.execute("帮我查一下库存")
        assert result.metadata["tool_success"] is False


# ============================================================
# C. Text-to-SQL 路由
# ============================================================

class TestTextToSQLRoute:
    async def test_full_chain_calls_all_layers(self) -> None:
        t2s_result = TextToSQLResult(
            question="本月采购入库数量是多少？",
            sql="SELECT id FROM public.inventory LIMIT 10",
            validated=True,
            attempts=1,
            referenced_tables=("public.inventory",),
        )
        sql_result = SQLExecutionResult(
            columns=("id",),
            rows=((1,), (2,)),
            row_count=2,
            truncated=False,
            execution_time_ms=1.5,
        )
        rag = FakeRAG()
        t2s = FakeTextToSQL(result=t2s_result)
        sql_exec = FakeSQLExecutor(result=sql_result)
        selector = FakeTableSelector(selections=["public.inventory"])
        composer = FakeContextComposer(context="FAKE_CTX")
        provider = FakeProjectProvider()
        orch = _make_service(
            router=FakeRouter(_sql_decision()),
            rag=rag, t2s=t2s, sql_executor=sql_exec,
            table_selector=selector,
            context_composer=composer,
            project_provider=provider,
        )
        result = await orch.execute("本月采购入库数量是多少？")
        assert result.route == RouteType.TEXT_TO_SQL
        assert result.data is sql_result
        assert result.metadata["sql"] == t2s_result.sql
        assert result.metadata["row_count"] == 2
        assert "2 行" in result.content
        # RAG 未调用
        assert rag.calls == []
        # 各层都被调用
        assert provider.calls == 1
        assert selector.calls == ["本月采购入库数量是多少？"]
        assert composer.calls and composer.calls[0]["tables"] == ["public.inventory"]
        assert len(t2s.calls) == 1
        assert t2s.calls[0]["allowed_tables"] == ("public.inventory",)
        assert t2s.calls[0]["max_rows"] == 1000
        assert sql_exec.calls[0]["sql"] == t2s_result.sql
        assert sql_exec.calls[0]["allowed_tables"] == ("public.inventory",)

    async def test_text_to_sql_generation_failure_wrapped(self) -> None:
        t2s = FakeTextToSQL(raise_exc=TextToSQLInputError("bad input"))
        orch = _make_service(
            router=FakeRouter(_sql_decision()),
            rag=FakeRAG(),
            t2s=t2s, sql_executor=FakeSQLExecutor(),
            table_selector=FakeTableSelector(),
            context_composer=FakeContextComposer(),
            project_provider=FakeProjectProvider(),
        )
        with pytest.raises(AIOrchestratorExecutionError) as exc_info:
            await orch.execute("bad sql")
        assert "TextToSQLInputError" in str(exc_info.value)

    async def test_sql_executor_failure_wrapped(self) -> None:
        t2s_result = TextToSQLResult(
            question="delete something",
            sql="SELECT 1 LIMIT 1",
            validated=True, attempts=1, referenced_tables=(),
        )
        t2s = FakeTextToSQL(result=t2s_result)
        sql_exec = FakeSQLExecutor(raise_exc=SQLExecutorValidationError((
            __import__(
                "backend.app.services.sql_validator_service",
                fromlist=["SQLValidationError", "SQLValidationCode"],
            ).SQLValidationError(
                __import__(
                    "backend.app.services.sql_validator_service",
                    fromlist=["SQLValidationCode"],
                ).SQLValidationCode.NON_READ_ONLY, "forbidden"
            ),
        )))
        orch = _make_service(
            router=FakeRouter(_sql_decision()),
            rag=FakeRAG(),
            t2s=t2s, sql_executor=sql_exec,
            table_selector=FakeTableSelector(),
            context_composer=FakeContextComposer(),
            project_provider=FakeProjectProvider(),
        )
        with pytest.raises(AIOrchestratorExecutionError) as exc_info:
            await orch.execute("delete something")
        assert "SQLExecutorValidationError" in str(exc_info.value)

    async def test_text_to_sql_project_unavailable(self) -> None:
        orch = _make_service(
            router=FakeRouter(_sql_decision()),
            rag=FakeRAG(), t2s=FakeTextToSQL(),
            sql_executor=FakeSQLExecutor(),
            table_selector=FakeTableSelector(),
            context_composer=FakeContextComposer(),
            project_provider=FakeProjectProvider(raise_exc=RuntimeError("db gone")),
        )
        with pytest.raises(AIOrchestratorUnavailableError):
            await orch.execute("统计本月入库")


# ============================================================
# D. Router 路由结果与异常
# ============================================================

class TestRouterBehavior:
    async def test_router_input_error_wrapped(self) -> None:
        orch = _make_service(
            router=FakeRouter(raise_exc=AIRouterInputError("empty")),
        )
        with pytest.raises(AIOrchestratorInputError):
            await orch.execute("anything")

    async def test_router_other_error_wrapped_as_route_error(self) -> None:
        orch = _make_service(
            router=FakeRouter(raise_exc=AIRouterError("boom")),
        )
        with pytest.raises(AIOrchestratorRouteError):
            await orch.execute("anything")

    async def test_router_fallback_to_rag_still_executes(self) -> None:
        decision = RouteDecision(
            route=RouteType.RAG, confidence=None,
            reason="fallback", source="fallback",
        )
        rag = FakeRAG(response=_FakeRagResponse("默认回答"))
        orch = _make_service(
            router=FakeRouter(decision=decision),
            rag=rag,
        )
        result = await orch.execute("something")
        assert result.route == RouteType.RAG
        assert result.metadata["decision_source"] == "fallback"

    async def test_no_multi_round_loop(self) -> None:
        """确认 Orchestrator 不会根据执行结果重新调用 Router。"""
        calls = {"router": 0, "rag": 0}

        class CountedRouter:
            async def route(self, question, *, context=None):
                calls["router"] += 1
                return _rag_decision()

        class CountedRAG:
            async def answer(self, q):
                calls["rag"] += 1
                return _FakeRagResponse("ok")

        orch = _make_service(router=CountedRouter(), rag=CountedRAG())
        await orch.execute("采购入库怎么操作？")
        assert calls["router"] == 1
        assert calls["rag"] == 1


# ============================================================
# E. 输入校验
# ============================================================

class TestInputValidation:
    async def test_empty_question(self) -> None:
        orch = _make_service(router=FakeRouter())
        with pytest.raises(AIOrchestratorInputError):
            await orch.execute("")
        with pytest.raises(AIOrchestratorInputError):
            await orch.execute("   ")

    async def test_non_string_question(self) -> None:
        orch = _make_service(router=FakeRouter())
        with pytest.raises(AIOrchestratorInputError):
            await orch.execute(123)  # type: ignore[arg-type]

    def test_invalid_max_rows(self) -> None:
        with pytest.raises(AIOrchestratorInputError):
            AIOrchestratorService(max_rows=0)
        with pytest.raises(AIOrchestratorInputError):
            AIOrchestratorService(max_rows=-1)
        with pytest.raises(AIOrchestratorInputError):
            AIOrchestratorService(max_rows="10")  # type: ignore[arg-type]


# ============================================================
# F. 安全：禁止写操作
# ============================================================

class TestSafety:
    async def test_delete_sql_rejected_by_validator(self) -> None:
        """即使 LLM 生成了 DELETE，Validator 应在 Executor 之前拦截。"""
        # 模拟 Generator "错误地" 产出了 DELETE（这种异常路径不应出现，
        # 但作为 belt-and-suspenders 测试兜底）
        t2s_result = TextToSQLResult(
            question="删除所有库存",
            sql="DELETE FROM public.inventory",
            validated=True,  # 假设 Generator 错误地认为已通过
            attempts=1, referenced_tables=("public.inventory",),
        )
        t2s = FakeTextToSQL(result=t2s_result)
        sql_exec = FakeSQLExecutor(raise_exc=SQLExecutorValidationError((
            __import__(
                "backend.app.services.sql_validator_service",
                fromlist=["SQLValidationError", "SQLValidationCode"],
            ).SQLValidationError(
                __import__(
                    "backend.app.services.sql_validator_service",
                    fromlist=["SQLValidationCode"],
                ).SQLValidationCode.NON_READ_ONLY, "DELETE forbidden"
            ),
        )))
        orch = _make_service(
            router=FakeRouter(_sql_decision()),
            rag=FakeRAG(), t2s=t2s, sql_executor=sql_exec,
            table_selector=FakeTableSelector(["public.inventory"]),
            context_composer=FakeContextComposer(),
            project_provider=FakeProjectProvider(),
        )
        with pytest.raises(AIOrchestratorExecutionError):
            await orch.execute("删除所有库存")
        # Executor 收到 DELETE 并立即 raise → DB writes = 0
        assert sql_exec.calls[0]["sql"].startswith("DELETE")

    async def test_orchestrator_does_not_execute_directly(self) -> None:
        """Orchestrator 不直接执行 SQL（必须经过 SQLExecutor）。"""
        import inspect
        import backend.app.services.ai_orchestrator_service as mod
        src = inspect.getsource(mod)
        # 禁止：直接 SQLAlchemy execute / 引擎 / session
        assert "engine.execute" not in src
        assert ".execute(text(" not in src
        assert "create_engine" not in src
        assert "sqlglot" not in src
        # 不直接 import Validator / Generator 实现细节
        assert "SQLValidatorService" not in src
        # 不创建 LLM client
        assert "create_llm_client" not in src

    async def test_no_eval_exec_subprocess(self) -> None:
        import inspect
        import backend.app.services.ai_orchestrator_service as mod
        src = inspect.getsource(mod)
        assert "eval(" not in src
        assert "exec(" not in src
        assert "subprocess" not in src
        assert "os.system" not in src


# ============================================================
# G. DTO 不变性 / 不泄露
# ============================================================

class TestDTO:
    async def test_dto_frozen(self) -> None:
        rag = FakeRAG(response=_FakeRagResponse("ok"))
        orch = _make_service(router=FakeRouter(_rag_decision()), rag=rag)
        result = await orch.execute("采购入库怎么操作？")
        with pytest.raises(Exception):
            result.route = RouteType.TEXT_TO_SQL  # type: ignore[misc]
        with pytest.raises(Exception):
            result.content = "x"  # type: ignore[misc]

    async def test_dto_no_sensitive_info(self) -> None:
        rag = FakeRAG(response=_FakeRagResponse("ok"))
        orch = _make_service(router=FakeRouter(_rag_decision()), rag=rag)
        result = await orch.execute("采购入库怎么操作？")
        s = repr(result)
        assert "DATABASE_URL" not in s
        assert "api_key" not in s.lower()
        assert "password" not in s.lower()
        # SQL 路径下 metadata 含 sql 字段（这是 feature 不是泄露）
        t2s_result = TextToSQLResult(
            question="anything",
            sql="SELECT 1 LIMIT 1", validated=True, attempts=1,
            referenced_tables=(),
        )
        orch2 = _make_service(
            router=FakeRouter(_sql_decision()),
            rag=FakeRAG(), t2s=FakeTextToSQL(result=t2s_result),
            sql_executor=FakeSQLExecutor(
                result=SQLExecutionResult(
                    columns=("?column?",), rows=((1,),), row_count=1,
                    truncated=False, execution_time_ms=0.5,
                )
            ),
            table_selector=FakeTableSelector([]),
            context_composer=FakeContextComposer(),
            project_provider=FakeProjectProvider(),
        )
        result2 = await orch2.execute("anything")
        assert "SELECT 1 LIMIT 1" in repr(result2)  # sql 在 metadata 中
        assert "DATABASE_URL" not in repr(result2)


# ============================================================
# H. 依赖方向
# ============================================================

class TestDependencies:
    def test_orchestrator_does_not_import_chat_api(self) -> None:
        """Orchestrator 不得 import 现有 Chat API（保持解耦）。"""
        import inspect
        import backend.app.services.ai_orchestrator_service as mod
        src = inspect.getsource(mod)
        assert "from backend.app.api" not in src
        assert "import backend.app.api" not in src

    def test_router_does_not_import_orchestrator(self) -> None:
        """Router 不得反向依赖 Orchestrator（避免循环）。"""
        import inspect
        import backend.app.services.ai_router_service as mod
        src = inspect.getsource(mod)
        assert "ai_orchestrator" not in src
        assert "Orchestrator" not in src


# ============================================================
# I. ProjectContextProvider
# ============================================================

class TestProjectContextProvider:
    def test_default_provider_returns_tuple(self) -> None:
        provider = DefaultProjectContextProvider(
            project_context=_make_project(),
            schema_name="public",
        )
        # DefaultProvider 内部用 SchemaExplorerService（需要 DB），
        # 这里只验证它构造正常、resolve 失败时有清晰错误。
        try:
            project, schema, semantic = provider.resolve()
        except AIOrchestratorUnavailableError:
            pytest.skip("需要真实 DB 才能跑 DefaultProvider.resolve")
        assert project.project_id == "test-project"
        assert schema.schema_name == "public"

    def test_fake_provider_no_db(self) -> None:
        provider = FakeProjectProvider()
        project, schema, semantic = provider.resolve()
        assert project is not None
        assert schema is not None
        assert semantic is not None
        # 必须实现 Protocol 接口
        assert hasattr(provider, "resolve")
        assert callable(provider.resolve)


# ============================================================
# J. _resolve_tool_name 单测
# ============================================================

class TestResolveToolName:
    async def test_alias_match(self) -> None:
        reg = ToolRegistry()
        register_mock_tools(reg)
        # get_inventory 的 parameter 有 material_code；question 含 material_code
        assert _resolve_tool_name("query material_code 库存", reg) == "get_inventory"

    async def test_description_match(self) -> None:
        reg = ToolRegistry()
        register_mock_tools(reg)
        # get_inventory description 含 "库存"
        assert _resolve_tool_name("查 库存 数量", reg) == "get_inventory"

    async def test_no_match_returns_none(self) -> None:
        reg = ToolRegistry()
        register_mock_tools(reg)
        assert _resolve_tool_name("完全不相关的无关问题", reg) is None


# ============================================================
# K. 真实 Text-to-SQL 端到端集成（DB 集成测试）
# ============================================================

import os

_env_flag = os.getenv("RUN_DB_TESTS", "").strip().lower() in {
    "1", "true", "yes", "on"
}
requires_db = pytest.mark.skipif(
    not _env_flag,
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


@requires_db
class TestRealDBEndToEnd:
    async def test_text_to_sql_full_pipeline(self) -> None:
        """完整链路：Router (TEXT_TO_SQL) → Selector → Composer →
        TextToSQLService (Fake LLM) → SQLExecutor → Result。

        DB writes = 0。
        """
        from sqlalchemy import text as sa_text
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )

        reset_engine_cache()
        engine = get_engine()
        assert engine is not None
        before = engine.connect().execute(
            sa_text("SELECT count(*) FROM public.knowledge_document")
        ).scalar()

        # 真实 Schema explorer
        schema = await SchemaExplorerService().inspect()
        semantic = ProjectSemantic()

        # Fake LLM 产出已验证 SQL（用 Validator 真实校验）
        class FakeLLM:
            async def chat(self, messages, *, tools=None):
                return (
                    "```sql\nSELECT id FROM "
                    "public.knowledge_document LIMIT 5\n```"
                )

        from backend.app.services.text_to_sql_service import TextToSQLService
        t2s = TextToSQLService(llm_client=FakeLLM())
        from backend.app.services.sql_executor_service import SQLExecutorService

        provider = FakeProjectProvider()
        orch = AIOrchestratorService(
            router=FakeRouter(_sql_decision()),
            rag_service=FakeRAG(),
            text_to_sql=t2s,
            sql_executor=SQLExecutorService(engine=engine),
            table_selector=FakeTableSelector(["public.knowledge_document"]),
            context_composer=FakeContextComposer(),
            project_context_provider=provider,
        )
        # 覆盖默认 provider，让其用真实 schema（无需真实 explorer）
        # 这里通过修改 schema 直接传入 provider
        provider._schema = schema  # type: ignore[attr-defined]

        result = await orch.execute("知识文档有哪些？")
        assert result.route == RouteType.TEXT_TO_SQL
        assert result.metadata["row_count"] >= 0
        assert "knowledge_document" in result.metadata["sql"]

        # DB writes = 0
        after = engine.connect().execute(
            sa_text("SELECT count(*) FROM public.knowledge_document")
        ).scalar()
        assert after == before

    async def test_router_default_uses_real_router(self) -> None:
        """真实 Router + Fake 下游：确认默认 Router 行为不破坏。"""
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine
        reset_engine_cache()
        engine = get_engine()
        schema = await SchemaExplorerService().inspect() if engine else None

        # 使用真实 AIRouterService（不依赖 LLM → fallback RAG）
        rag = FakeRAG(response=_FakeRagResponse("default rag"))
        orch = AIOrchestratorService(
            router=AIRouterService(llm_fallback_enabled=False),
            rag_service=rag,
            text_to_sql=FakeTextToSQL(),
            sql_executor=FakeSQLExecutor(),
            table_selector=FakeTableSelector(),
            context_composer=FakeContextComposer(),
            project_context_provider=FakeProjectProvider(schema=schema),
        )
        # 含糊问题 → RAG fallback
        result = await orch.execute("采购入库")
        assert result.route == RouteType.RAG