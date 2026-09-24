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
from backend.app.tools.registry import ToolResult


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