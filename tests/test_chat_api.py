"""AI Chat API 测试（Phase 3.7.11：Orchestrator-backed Unified Chat）。

通过 monkeypatch 替换：

    backend.app.api.orchestrator_chat._default_orchestrator

注入 Fake Orchestrator，验证：

    1. POST /api/ai/chat 正常请求 → 200 + ChatResponse（按 route 分支）
    2. 空 question → 422
    3. 纯空白 question → 422
    4. RAG 路由 → data 包含 sources 元数据（不含 chunk content）
    5. Tool 路由 → data 包含 tool_name + success + data
    6. Text-to-SQL 路由 → data 包含 columns / rows / row_count
    7. project_id 透传到 Orchestrator.execute() 的 ProjectContextProvider
    8. Orchestrator 抛异常 → 500，且响应不泄露 traceback / SQL / secrets
    9. 每次请求 Orchestrator.execute() 仅被调用一次
    10. API 层不直接调用 RagService / ToolRegistry / TextToSQLService /
        SQLValidator / SQLExecutor（静态 import 检查 + 调用计数）

不发起真实 LLM / DB 调用（Fake Orchestrator）。
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.api import orchestrator_chat as orch_module
from backend.app.main import app
from backend.app.projects.context import ProjectContext
from backend.app.services.ai_orchestrator_service import (
    AIOrchestrationResult,
    AIOrchestratorExecutionError,
    AIOrchestratorInputError,
    AIOrchestratorRouteError,
    AIOrchestratorUnavailableError,
)
from backend.app.services.ai_router_service import RouteType
from backend.app.services.rag_service import RagResponse, RagSource
from backend.app.tools.registry import ToolRegistry, ToolResult


# ============================================================
# Fake Orchestrator
# ============================================================

class FakeOrchestrator:
    """替身 AIOrchestratorService。

    通过 ``set_response`` / ``set_side_effects`` 注入预编排结果。
    记录 ``execute()`` 调用（question + kwargs），用于验证。
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._side_effects: list = []
        self._default_response: AIOrchestrationResult | None = None

    def set_response(self, result: AIOrchestrationResult) -> None:
        self._default_response = result

    def set_side_effects(self, side_effects: list) -> None:
        """按调用顺序返回响应；超出后抛 IndexError。"""
        self._side_effects = list(side_effects)
        self._default_response = None

    async def execute(self, question, **kwargs):
        self.calls.append((question, dict(kwargs)))
        if self._side_effects:
            next_item = self._side_effects.pop(0)
            if isinstance(next_item, BaseException):
                raise next_item
            return next_item
        if self._default_response is not None:
            return self._default_response
        raise AssertionError("FakeOrchestrator: no response configured")


def _rag_result() -> AIOrchestrationResult:
    return AIOrchestrationResult(
        route=RouteType.RAG,
        content="采购入库需要先创建入库通知",
        data=RagResponse(
            answer="采购入库需要先创建入库通知",
            sources=(
                RagSource(
                    chunk_id=101,
                    document_id=12,
                    chunk_index=3,
                    content="chunk-content-NEVER-LEAK",
                    similarity=0.95,
                    metadata={"heading_path": ["采购入库", "操作步骤"]},
                ),
            ),
            used_chunks_count=1,
        ),
        metadata={
            "decision_source": "rule",
            "route_reason": "业务知识 / 流程 / 操作提问",
            "rag_used_chunks": 1,
        },
    )


def _tool_result() -> AIOrchestrationResult:
    return AIOrchestrationResult(
        route=RouteType.TOOL,
        content="material_code=10001 qty=120",
        data=ToolResult(
            tool_name="get_inventory",
            success=True,
            data={"material_code": "10001", "qty": 120},
            error=None,
        ),
        metadata={
            "decision_source": "tool_match",
            "route_reason": "命中 get_inventory 别名",
            "tool_name": "get_inventory",
            "tool_success": True,
        },
    )


def _sql_result() -> AIOrchestrationResult:
    # 通过 dict-based mock 避免 import SQLExecutionResult（不依赖 DB 模块）
    class _ExecLike:
        columns = ("material_code", "qty")
        rows = (("10001", 120), ("10002", 80))
        row_count = 2
        truncated = False
        execution_time_ms = 12.3

    return AIOrchestrationResult(
        route=RouteType.TEXT_TO_SQL,
        content="查询返回 2 行",
        data=_ExecLike(),
        metadata={
            "decision_source": "rule",
            "route_reason": "数据分析特征",
            "sql": "SELECT material_code, qty FROM inventory LIMIT 2",
            "row_count": 2,
            "truncated": False,
            "execution_time_ms": 12.3,
            "selected_tables": ["public.inventory"],
            "project_id": "vietnam-wms",
        },
    )


# ============================================================
# Fixture
# ============================================================

@pytest.fixture()
def client(monkeypatch):
    """构造测试客户端 + Fake Orchestrator，yield (TestClient, FakeOrchestrator)。"""

    @contextmanager
    def _make(
        *,
        side_effects: list | None = None,
        default_response: AIOrchestrationResult | None = None,
        raise_server_exceptions: bool = True,
    ):
        fake = FakeOrchestrator()
        if side_effects is not None:
            fake.set_side_effects(side_effects)
        if default_response is not None:
            fake.set_response(default_response)

        monkeypatch.setattr(orch_module, "_default_orchestrator", fake)
        with TestClient(
            app, raise_server_exceptions=raise_server_exceptions
        ) as c:
            yield c, fake

    return _make


# ============================================================
# 1. 正常请求
# ============================================================

class TestNormalRequest:
    def test_returns_200_with_chat_response_envelope(self, client) -> None:
        """POST /api/ai/chat → 200 + ChatResponse（route / content / metadata）。"""
        with client(default_response=_rag_result()) as (c, _fake):
            response = c.post(
                "/api/ai/chat",
                json={"question": "采购入库怎么操作？"},
            )
        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "rag"
        assert payload["content"] == "采购入库需要先创建入库通知"
        assert isinstance(payload["metadata"], dict)
        assert payload["metadata"]["rag_used_chunks"] == 1

    def test_question_passed_to_orchestrator(self, client) -> None:
        """question 原样（未 strip，由 Pydantic 校验后）传递给 Orchestrator。"""
        with client(default_response=_rag_result()) as (c, fake):
            c.post("/api/ai/chat", json={"question": "物料最多的前 3 个是什么？"})
        assert len(fake.calls) == 1
        question, kwargs = fake.calls[0]
        assert question == "物料最多的前 3 个是什么？"
        assert kwargs == {}

    def test_orchestrator_called_exactly_once_per_request(self, client) -> None:
        """一次请求只调用一次 Orchestrator.execute()（无 loop / Agent）。"""
        with client(default_response=_rag_result()) as (c, fake):
            c.post("/api/ai/chat", json={"question": "q1"})
            c.post("/api/ai/chat", json={"question": "q2"})
            c.post("/api/ai/chat", json={"question": "q3"})
        assert len(fake.calls) == 3

    def test_openapi_contains_ai_chat_endpoint(self, client) -> None:
        with client(default_response=_rag_result()) as (c, _fake):
            spec = c.get("/openapi.json").json()
        assert "/api/ai/chat" in spec["paths"]
        post = spec["paths"]["/api/ai/chat"]["post"]
        assert "200" in post["responses"]
        assert "400" in post["responses"]
        assert "422" in post["responses"]
        assert "500" in post["responses"]


# ============================================================
# 2. 入参校验
# ============================================================

class TestValidation:
    @pytest.mark.parametrize("question", ["", "   ", "\n\t "])
    def test_blank_question_rejected(self, client, question) -> None:
        """""（Pydantic min_length=1）与纯空白（field_validator）均 422。"""
        with client() as (c, fake):
            response = c.post("/api/ai/chat", json={"question": question})
        assert response.status_code == 422
        assert fake.calls == []

    def test_missing_question_rejected_422(self, client) -> None:
        with client() as (c, fake):
            response = c.post("/api/ai/chat", json={})
        assert response.status_code == 422
        assert fake.calls == []

    def test_non_string_question_rejected_422(self, client) -> None:
        with client() as (c, fake):
            response = c.post("/api/ai/chat", json={"question": 123})
        assert response.status_code == 422
        assert fake.calls == []

    def test_project_id_too_long_rejected_422(self, client) -> None:
        """project_id 长度 > 128 → 422（Pydantic max_length）。"""
        with client() as (c, fake):
            response = c.post(
                "/api/ai/chat",
                json={"question": "q", "project_id": "a" * 200},
            )
        assert response.status_code == 422
        assert fake.calls == []


# ============================================================
# 3. RAG 路由
# ============================================================

class TestRagRoute:
    def test_rag_response_does_not_leak_chunk_content(self, client) -> None:
        """RAG 路径：sources 仅元数据，**不**返回 chunk content。"""
        with client(default_response=_rag_result()) as (c, _fake):
            payload = c.post(
                "/api/ai/chat", json={"question": "q"}
            ).json()
        assert payload["route"] == "rag"
        data = payload["data"]
        assert data is not None
        assert len(data["sources"]) == 1
        src = data["sources"][0]
        assert set(src.keys()) == {
            "chunk_id",
            "document_id",
            "chunk_index",
            "similarity",
            "metadata",
        }
        # 严禁泄露 chunk content
        assert "content" not in src
        # 严禁泄露 Raw RAG 内部对象
        body_text = c.post(
            "/api/ai/chat", json={"question": "q"}
        ).text
        assert "chunk-content-NEVER-LEAK" not in body_text

    def test_rag_used_chunks_count_propagated(self, client) -> None:
        with client(default_response=_rag_result()) as (c, _fake):
            payload = c.post(
                "/api/ai/chat", json={"question": "q"}
            ).json()
        assert payload["data"]["used_chunks_count"] == 1


# ============================================================
# 4. Tool 路由
# ============================================================

class TestToolRoute:
    def test_tool_response_includes_tool_metadata(self, client) -> None:
        """Tool 路径：data 包含 tool_name / success / data，不暴露 Handler。"""
        with client(default_response=_tool_result()) as (c, _fake):
            payload = c.post(
                "/api/ai/chat", json={"question": "查询物料 10001 库存"}
            ).json()
        assert payload["route"] == "tool"
        data = payload["data"]
        assert data is not None
        assert data["tool_name"] == "get_inventory"
        assert data["success"] is True
        assert data["data"] == {"material_code": "10001", "qty": 120}
        assert data["error"] is None
        assert payload["metadata"]["tool_name"] == "get_inventory"

    def test_tool_metadata_does_not_leak_handler(self, client) -> None:
        """响应中**不**暴露 Tool handler / ToolDefinition。"""
        with client(default_response=_tool_result()) as (c, _fake):
            body = c.post(
                "/api/ai/chat", json={"question": "q"}
            ).text
        # 严禁出现 handler 关键字
        for keyword in ("handler", "callable", "ToolDefinition", "parameters"):
            assert keyword not in body


# ============================================================
# 5. Text-to-SQL 路由
# ============================================================

class TestSqlRoute:
    def test_sql_response_includes_columns_rows(self, client) -> None:
        """SQL 路径：data 包含 columns / rows / row_count。"""
        with client(default_response=_sql_result()) as (c, _fake):
            payload = c.post(
                "/api/ai/chat",
                json={"question": "物料最多的前 3 个是什么？"},
            ).json()
        assert payload["route"] == "text_to_sql"
        data = payload["data"]
        assert data is not None
        assert data["columns"] == ["material_code", "qty"]
        assert data["row_count"] == 2
        assert data["truncated"] is False
        assert data["execution_time_ms"] == pytest.approx(12.3)
        assert data["referenced_tables"] == ["public.inventory"]
        assert data["project_id"] == "vietnam-wms"
        # rows 是纯 list[list]，不暴露 SQLAlchemy Row
        assert isinstance(data["rows"], list)
        assert all(isinstance(r, list) for r in data["rows"])
        assert data["rows"] == [["10001", 120], ["10002", 80]]

    def test_sql_metadata_propagates(self, client) -> None:
        with client(default_response=_sql_result()) as (c, _fake):
            payload = c.post(
                "/api/ai/chat",
                json={"question": "q"},
            ).json()
        meta = payload["metadata"]
        assert meta["selected_tables"] == ["public.inventory"]
        assert meta["row_count"] == 2


# ============================================================
# 6. project_id 透传
# ============================================================

class TestProjectContextPortability:
    def test_project_id_propagates_to_orchestrator(
        self, client, monkeypatch
    ) -> None:
        """project_id 透传到 Orchestrator.execute（不硬编码默认值）。"""
        # 让 _build_orchestrator_for_project_id 返回一个使用 fake 的 orchestrator
        calls: list[str] = []

        def fake_build(project_id: str):
            calls.append(project_id)

            class _StubOrch:
                async def execute(self, question, **kwargs):
                    return _rag_result()
            return _StubOrch()

        monkeypatch.setattr(
            orch_module,
            "_build_orchestrator_for_project_id",
            fake_build,
        )

        with client(default_response=_rag_result()) as (c, _fake):
            response = c.post(
                "/api/ai/chat",
                json={
                    "question": "q",
                    "project_id": "another-warehouse",
                },
            )
        assert response.status_code == 200
        assert calls == ["another-warehouse"]

    def test_no_project_id_uses_default_orchestrator(self, client) -> None:
        """未传 project_id → 使用默认 Orchestrator（不走 factory）。"""
        with client(default_response=_rag_result()) as (c, fake):
            response = c.post(
                "/api/ai/chat",
                json={"question": "q"},
            )
        assert response.status_code == 200
        # 默认 Orchestrator 仍然调用一次
        assert len(fake.calls) == 1


# ============================================================
# 7. Orchestrator 异常映射
# ============================================================

class TestErrorMapping:
    @pytest.mark.parametrize(
        ("exc", "expected_status", "expected_fragment"),
        [
            (AIOrchestratorInputError("question 为空"), 400, "非法输入"),
            (
                AIOrchestratorRouteError("Router 决策失败"),
                502,
                "路由决策失败",
            ),
            (
                AIOrchestratorUnavailableError("Schema 不可用"),
                503,
                "项目上下文不可用",
            ),
            (
                AIOrchestratorExecutionError("RAG 执行失败"),
                500,
                "AI 能力执行失败",
            ),
        ],
    )
    def test_orchestrator_errors_map_to_status(
        self, client, exc, expected_status, expected_fragment
    ) -> None:
        with client(side_effects=[exc]) as (c, _fake):
            response = c.post("/api/ai/chat", json={"question": "q"})
        assert response.status_code == expected_status
        assert expected_fragment in response.json()["detail"]

    def test_unknown_error_returns_500_without_leaking_traceback(
        self, client
    ) -> None:
        """未预期异常 → 500，响应不包含 traceback / Python stack。"""
        with client(
            side_effects=[RuntimeError("internal boom")],
            raise_server_exceptions=False,
        ) as (c, _fake):
            response = c.post("/api/ai/chat", json={"question": "q"})
        assert response.status_code == 500
        body = response.text
        for forbidden in (
            "Traceback",
            'File "',
            "internal boom",
            "Traceback (most recent call last)",
        ):
            assert forbidden not in body

    @pytest.mark.parametrize(
        "secret_fragment",
        [
            "sk-abc123",
            "Authorization",
            "Bearer ",
            "postgresql://",
            "postgres:secret",
            "api_key=",
        ],
    )
    def test_error_response_never_leaks_secrets(
        self, client, secret_fragment
    ) -> None:
        with client(
            side_effects=[RuntimeError("内部 boom " + secret_fragment)],
            raise_server_exceptions=False,
        ) as (c, _fake):
            response = c.post("/api/ai/chat", json={"question": "q"})
        assert response.status_code == 500
        assert secret_fragment not in response.text


# ============================================================
# 8. API 层不绕过 Orchestrator（静态 + 调用计数）
# ============================================================

class TestApiDoesNotBypassOrchestrator:
    def test_api_module_does_not_import_forbidden_services(self) -> None:
        """静态检查：API 层 import 不引入 RAG / Tool / T2S / Validator / Executor。

        允许 import ``AIOrchestratorService``（依赖注入工厂），
        禁止直接 import 上述底层服务。
        """
        forbidden_imports = (
            "from backend.app.services.rag_service import",
            "from backend.app.services.tool_registry import",
            "from backend.app.services.text_to_sql_service import",
            "from backend.app.services.sql_validator_service import",
            "from backend.app.services.sql_executor_service import",
        )

        source_path = Path(orch_module.__file__).resolve()
        source = source_path.read_text(encoding="utf-8")
        # 用 AST 解析并扫描全部 import 节点（避免注释误判）
        tree = ast.parse(source)
        import_lines: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                import_lines.append(
                    f"from {node.module} import "
                    + ", ".join(a.name for a in node.names)
                )
            elif isinstance(node, ast.Import):
                import_lines.append(
                    "import " + ", ".join(a.name for a in node.names)
                )

        joined = "\n".join(import_lines)
        for forbidden in forbidden_imports:
            assert forbidden not in joined, (
                f"API 层禁止直接 import 底层服务: {forbidden}\n"
                f"实际 import: {joined}"
            )

    def test_api_endpoint_calls_only_orchestrator(self, client) -> None:
        """运行时检查：endpoint 路径上只走 Orchestrator.execute()。"""
        with client(default_response=_rag_result()) as (c, fake):
            c.post("/api/ai/chat", json={"question": "q1"})
            c.post("/api/ai/chat", json={"question": "q2"})
        # Fake Orchestrator.execute 被精确调用两次
        assert len(fake.calls) == 2


# ============================================================
# C40. Assistant Trace Contract（Phase 3.12 Step 35）
# ============================================================

_API_MODULE = "backend/app/api/orchestrator_chat.py"
_ORCH_MODULE = "backend/app/services/ai_orchestrator_service.py"
_OLD_CHAT_MODULE = "backend/app/api/chat.py"
_TOOL_CHAT_MODULE = "backend/app/api/tool_chat.py"
_RAG_MODULE = "backend/app/api/rag.py"


def _module_source(path: str) -> str:
    return (
        Path(__file__).resolve().parent.parent / path
    ).read_text(encoding="utf-8")


def _module_tree(path: str) -> ast.Module:
    return ast.parse(_module_source(path))


def _identifiers(tree: ast.Module) -> set[str]:
    names = {
        node.id.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    } | {
        node.attr.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }
    return names


def _with_request_id(
    result: AIOrchestrationResult, request_id: str
) -> AIOrchestrationResult:
    """复制 Orchestrator 结果并注入 trace id（模拟真实 Orchestrator 产出）。"""
    from dataclasses import replace

    return replace(result, metadata={**dict(result.metadata),
                                     "request_id": request_id})


class _TraceRouter:
    """最小 Router 替身（固定 decision；不改 Router 生产实现）。"""

    def __init__(self, decision) -> None:
        self._decision = decision

    async def route(self, question, *, context=None):
        return self._decision


class _TraceRag:
    """最小 RagService 替身（0 LLM / 0 DB）。"""

    def __init__(self, answer: str = "采购入库需要先创建入库通知") -> None:
        self._response = RagResponse(answer=answer, sources=(), used_chunks_count=1)

    async def answer(self, query, **kwargs):  # noqa: ANN003
        return self._response


class _TraceToolHandler:
    def __init__(self) -> None:
        self.calls = 0
        self.last_arguments: dict | None = None

    async def __call__(self, arguments):
        self.calls += 1
        self.last_arguments = dict(arguments)
        return dict(arguments)


class _TraceTextToSQL:
    async def generate(self, question, **kwargs):  # noqa: ANN003
        raise AssertionError("T2S 不应在本测试路径被调用")


class _TraceSqlExecutor:
    async def execute(self, sql, **kwargs):  # noqa: ANN003
        raise AssertionError("SQL Executor 不应在本测试路径被调用")


class _TraceTableSelector:
    def select(self, question, *, schema, semantic, top_k=5):
        from backend.app.services.relevant_table_selector import (
            TableSelectionResult,
        )

        return TableSelectionResult(question=question, selections=())


class _TraceContextComposer:
    def compose(self, *, project, schema, semantic, tables=None, max_chars=4000):
        return "TRACE_CONTEXT"


class _TraceProjectProvider:
    def resolve(self):
        from backend.app.projects.context import DataSource, ProjectContext
        from backend.app.projects.semantic import ProjectSemantic
        from backend.app.services.schema_explorer_service import DatabaseSchema

        return (
            ProjectContext(
                project_id="project-a",
                project_name="Project A",
                description=None,
                data_source=DataSource(name="primary", type="postgresql"),
            ),
            DatabaseSchema(schema_name="project_a", tables=()),
            ProjectSemantic(),
        )


def _real_orchestrator(decision, *, registry=None, collector=None):
    """真实 AIOrchestratorService + 真实执行边界 + 内存 Collector（0 DB）。"""
    from backend.app.services.ai_orchestrator_service import (
        AIOrchestratorService,
    )
    from backend.app.services.tool_execution_service import ToolExecutionService

    if registry is None:
        from backend.app.tools.get_inventory import GET_INVENTORY_DEFINITION

        registry = ToolRegistry()
        registry.register(GET_INVENTORY_DEFINITION, _TraceToolHandler())
    # 执行边界持有 observer（与 Orchestrator 注入**同一个**对象，
    # 满足边界一致性校验：不允许两个观测来源）
    execution = ToolExecutionService(registry=registry, observer=collector)
    orchestrator = AIOrchestratorService(
        router=_TraceRouter(decision),
        rag_service=_TraceRag(),
        tool_registry=registry,
        text_to_sql=_TraceTextToSQL(),
        sql_executor=_TraceSqlExecutor(),
        table_selector=_TraceTableSelector(),
        context_composer=_TraceContextComposer(),
        project_context_provider=_TraceProjectProvider(),
        tool_execution_service=execution,
        tool_execution_observer=collector,
    )
    return orchestrator, registry


class TestC40AssistantTraceContract:
    """C40：Assistant Trace Contract（`/api/ai/chat` 成功响应可关联观测）。

        C40.1  成功响应含 metadata.request_id
        C40.2  request_id 唯一来源 = AIOrchestrator.new_request_id()
        C40.3  API 不重新生成 request_id
        C40.4  AIOrchestrationResult DTO 结构不变
        C40.5  RAG / Tool / T2S 三条成功路径均携带
        C40.6  Tool 路径：响应 request_id == ToolExecutionRecord.request_id
        C40.7  RAG / T2S 不新增 Tool Record
        C40.8  request body 不新增 request_id
        C40.9  错误 HTTP contract 不变（错误响应不含 request_id）
        C40.10 metadata 不含敏感信息；request_id 非空字符串
        C40.11 /api/chat 不受影响
        C40.12 /api/chat/with-tools 不受影响
    """

    # ---- C40.1 / C40.3 / C40.5 三路由透出 ----

    @pytest.mark.parametrize(
        ("result_factory", "expected_route"),
        [
            (_rag_result, "rag"),
            (_tool_result, "tool"),
            (_sql_result, "text_to_sql"),
        ],
    )
    def test_c40_1_and_5_each_route_carries_request_id(
        self, client, result_factory, expected_route
    ) -> None:
        trace_id = f"trace-{expected_route}-1"
        with client(
            default_response=_with_request_id(result_factory(), trace_id)
        ) as (c, _fake):
            response = c.post("/api/ai/chat", json={"question": "q"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == expected_route
        assert payload["metadata"]["request_id"] == trace_id
        assert isinstance(payload["metadata"]["request_id"], str)
        assert payload["metadata"]["request_id"] != ""

    def test_c40_3_api_does_not_generate_request_id(self, client) -> None:
        """Orchestrator 未给 request_id → API **不**补齐（不生成第二个 ID）。"""
        with client(default_response=_rag_result()) as (c, _fake):
            payload = c.post("/api/ai/chat", json={"question": "q"}).json()

        assert "request_id" not in payload["metadata"]

    def test_c40_2_and_3_static_single_source(self) -> None:
        api_tree = _module_tree(_API_MODULE)
        orch_source = _module_source(_ORCH_MODULE)

        # 唯一生成点：Orchestrator 的 execute()
        assert orch_source.count("new_request_id()") == 2   # import + 1 次调用
        api_identifiers = _identifiers(api_tree)
        for forbidden in ("uuid", "uuid4", "new_request_id", "token_hex"):
            assert forbidden not in api_identifiers, forbidden
        api_imports = {
            alias.name
            for node in ast.walk(api_tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module or ""
            for node in ast.walk(api_tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert not any("uuid" in name for name in api_imports), api_imports

    # ---- C40.4 DTO 结构不变 ----

    def test_c40_4_result_dto_structure_unchanged(self) -> None:
        from dataclasses import fields

        assert [f.name for f in fields(AIOrchestrationResult)] == [
            "route", "content", "data", "metadata",
        ]

    # ---- C40.6 Tool 路径真实关联（HTTP → Orchestrator → 执行边界 → Record） ----

    def test_c40_6_tool_request_id_matches_tool_record(
        self, monkeypatch
    ) -> None:
        from backend.app.services.in_memory_tool_execution_collector import (
            InMemoryToolExecutionCollector,
        )
        from backend.app.services.ai_router_service import RouteDecision

        collector = InMemoryToolExecutionCollector()
        decision = RouteDecision(
            route=RouteType.TOOL,
            confidence=0.9,
            reason="matched get_inventory",
            source="tool_match",
            tool_name="get_inventory",
        )
        orchestrator, _registry = _real_orchestrator(
            decision, collector=collector
        )
        collector.clear()
        monkeypatch.setattr(orch_module, "_default_orchestrator", orchestrator)
        # project_id 分支：复用同一个真实 Orchestrator（服务器端注册表
        # 校验在其它测试覆盖；此处只验证 trace 关联）
        monkeypatch.setattr(
            orch_module,
            "_build_orchestrator_for_project_id",
            lambda _project_id: orchestrator,
        )

        with TestClient(app) as c:
            response = c.post(
                "/api/ai/chat",
                json={
                    "question": "查询物料 MAT-001 当前库存",
                    "project_id": "project-a",
                },
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "tool"
        request_id = payload["metadata"]["request_id"]
        records = collector.records()
        assert len(records) == 1
        assert request_id == records[0].request_id      # 同一 ID
        assert records[0].round == 1
        assert records[0].tool_call_id is None
        assert records[0].tool_name == "get_inventory"
        collector.clear()

    def test_c40_7_rag_route_produces_zero_tool_records(
        self, monkeypatch
    ) -> None:
        from backend.app.services.in_memory_tool_execution_collector import (
            InMemoryToolExecutionCollector,
        )
        from backend.app.services.ai_router_service import RouteDecision

        collector = InMemoryToolExecutionCollector()
        orchestrator, _registry = _real_orchestrator(
            RouteDecision(
                route=RouteType.RAG,
                confidence=0.9,
                reason="rule: knowledge",
                source="rule",
            ),
            collector=collector,
        )
        collector.clear()
        monkeypatch.setattr(orch_module, "_default_orchestrator", orchestrator)

        with TestClient(app) as c:
            payload = c.post(
                "/api/ai/chat", json={"question": "采购入库流程是什么"}
            ).json()

        assert payload["route"] == "rag"
        assert payload["metadata"]["request_id"]
        assert collector.records() == ()          # RAG → 0 Tool Record

    # ---- C40.8 request body 不变 ----

    def test_c40_8_request_body_schema_unchanged(self, client) -> None:
        assert set(orch_module.ChatRequest.model_fields) == {
            "question", "project_id",
        }
        # 客户端传入 request_id → 被忽略（响应里的 ID 只来自 Orchestrator）
        with client(
            default_response=_with_request_id(_rag_result(), "server-id")
        ) as (c, _fake):
            payload = c.post(
                "/api/ai/chat",
                json={"question": "q", "request_id": "client-injected"},
            ).json()

        assert payload["metadata"]["request_id"] == "server-id"

    # ---- C40.9 错误 contract 不变 ----

    def test_c40_9_error_contract_unchanged(self, client) -> None:
        with client(
            side_effects=[AIOrchestratorRouteError("Router 决策失败")]
        ) as (c, _fake):
            response = c.post("/api/ai/chat", json={"question": "q"})

        assert response.status_code == 502
        payload = response.json()
        assert set(payload) == {"detail"}          # 错误 envelope 未变
        assert "request_id" not in payload
        assert "request_id" not in response.text

    # ---- C40.10 metadata 安全 ----

    def test_c40_10_metadata_has_no_sensitive_keys(self, client) -> None:
        with client(
            default_response=_with_request_id(_tool_result(), "trace-secure-1")
        ) as (c, _fake):
            metadata = c.post(
                "/api/ai/chat", json={"question": "q"}
            ).json()["metadata"]

        for forbidden in (
            "api_key", "password", "authorization", "database_url",
            "connection_string", "sqlalchemy", "session", "prompt",
            "traceback", "arguments", "tool_result", "data",
        ):
            assert forbidden not in metadata, forbidden
        assert metadata["request_id"] == "trace-secure-1"

    # ---- C40.11 / C40.12 旧 API 不变 ----

    def test_c40_11_old_chat_api_unchanged(self) -> None:
        tree = _module_tree(_OLD_CHAT_MODULE)

        from backend.app.api.chat import ChatResponse as LegacyChatResponse

        assert _identifiers(tree) & {"uuid", "uuid4", "request_id"} == set()
        assert "request_id" not in _module_source(_OLD_CHAT_MODULE)
        # 旧 envelope 仍为 answer / sources / used_chunks_count（无 metadata 段）
        assert set(LegacyChatResponse.model_fields) == {
            "answer", "sources", "used_chunks_count",
        }

    def test_c40_12_tool_chat_api_unchanged(self) -> None:
        tree = _module_tree(_TOOL_CHAT_MODULE)

        assert _identifiers(tree) & {"uuid", "uuid4", "request_id"} == set()
        assert "request_id" not in _module_source(_TOOL_CHAT_MODULE)

    def test_c40_11_and_12_rag_api_unchanged(self) -> None:
        tree = _module_tree(_RAG_MODULE)

        assert _identifiers(tree) & {"uuid", "uuid4", "request_id"} == set()
        assert "request_id" not in _module_source(_RAG_MODULE)

    def test_c40_11_and_12_response_models_unchanged(self) -> None:
        from backend.app.api.chat import ChatResponse as LegacyChatResponse
        from backend.app.api.tool_chat import ToolChatApiResponse

        assert set(LegacyChatResponse.model_fields) == {
            "answer", "sources", "used_chunks_count",
        }
        assert set(ToolChatApiResponse.model_fields) == {
            "answer", "tool_calls",
        }
        # /api/ai/chat 的 envelope 仍为 route / content / data / metadata
        assert set(orch_module.ChatResponse.model_fields) == {
            "route", "content", "data", "metadata",
        }


__all__ = [
    "FakeOrchestrator",
    "_tool_result",
    "_rag_result",
    "_sql_result",
    "TestNormalRequest",
    "TestValidation",
    "TestRagRoute",
    "TestToolRoute",
    "TestSqlRoute",
    "TestProjectContextPortability",
    "TestErrorMapping",
    "TestApiDoesNotBypassOrchestrator",
]


_ = ProjectContext  # 防止 ProjectContext unused import 警告