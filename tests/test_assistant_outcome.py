"""Assistant Outcome 最小生产实现（Phase 3.12 Step 63）——真实 API 边界测试。

被测链路（**真实**）：

    POST /api/ai/chat
        ↓  真实 FastAPI app / 真实 Orchestrator / 真实 Router
    AIOrchestrationResult.metadata["outcome"]        （Orchestrator 层判定）
        ↓
    ChatResponse（envelope 不变：route / content / data / metadata）

固定 4 态：``SUCCESS`` · ``EMPTY`` · ``REFUSED`` · ``FAILED``
优先级：REFUSED > FAILED > EMPTY > SUCCESS（判定白名单：route / refused /
tool_success / rag_used_chunks —— 不做 content 文本匹配）

只 Fake 外部边界（LLM transport / 检索 / Tool handler / SQL 执行）；
复用 Step 40 离线装配与 Step 60 的 TEXT_TO_SQL 路由 helpers（不重造 Fake）。
0 真实 LLM · 0 网络 · 0 DB（默认套件）。
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.api import orchestrator_chat as root
from backend.app.dto.assistant_outcome import (
    AssistantOutcome,
    determine_assistant_outcome,
)
from backend.app.main import app
from backend.app.services.llm_usage_persistence_service import (
    DatabaseLLMAccountingSink,
    LLMUsagePersistenceService,
)
from backend.app.services.sql_executor_service import SQLExecutionResult
from backend.app.services.text_to_sql_service import REFUSAL_MARKER

# 复用既有装配 / Fake（不重造）
from tests.test_assistant_trace_correlation_e2e import (  # noqa: PLC2701
    _RAG_QUESTION,
    _TOOL_QUESTION,
    _response_body,
    e2e,
)
from tests.test_assistant_trace_multi_path_e2e import (  # noqa: PLC2701
    _install_t2sql_route,
    _retry_t2sql_client,
)

_T2SQL_QUESTION = "统计最近7天的入库单数量"
_OUTCOME_VALUES = {"SUCCESS", "EMPTY", "REFUSED", "FAILED"}
_SECRET_SENTINEL = "STEP63-SECRET-SENTINEL"


def _post(http: TestClient, question: str) -> Any:
    return http.post("/api/ai/chat", json={"question": question})


def _outcome_of(response: Any) -> str:
    assert response.status_code == 200, response.text
    return response.json()["metadata"]["outcome"]


def _client_with_handler(handler: Any, repository: Any) -> Any:
    from backend.app.llm.client import OpenAICompatibleClient

    return OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://step63.fake/v1",
        model="step63-model",
        provider="step63-provider",
        transport=httpx.MockTransport(handler),
        accounting_sink=DatabaseLLMAccountingSink(
            persistence_service=LLMUsagePersistenceService(repository=repository)
        ),
    )


class _EmptyVectorSearch:
    async def search(self, query: str, *, top_k: int):  # noqa: ANN201
        return []


class _BoomVectorSearch:
    async def search(self, query: str, *, top_k: int):  # noqa: ANN201
        raise RuntimeError(_SECRET_SENTINEL)


class _ZeroRowExecutor:
    """SQL 成功执行但 **0 行**（仍是有效业务结果 ⇒ SUCCESS）。"""

    def __init__(self) -> None:
        self.sqls: list[str] = []

    async def execute(self, sql: str, **kwargs: Any) -> SQLExecutionResult:  # noqa: ANN003
        self.sqls.append(sql)
        return SQLExecutionResult(
            columns=("id", "title"),
            rows=(),
            row_count=0,
            truncated=False,
            execution_time_ms=0.4,
        )


class _BoomExecutor:
    """SQL 执行失败（⇒ HTTP 500 / FAILED）。"""

    async def execute(self, sql: str, **kwargs: Any) -> SQLExecutionResult:  # noqa: ANN003
        raise RuntimeError(_SECRET_SENTINEL)


def _fresh_rag(monkeypatch: pytest.MonkeyPatch, service: Any) -> None:
    monkeypatch.setattr(root._default_orchestrator, "_rag", service)


# ============================================================
# 1. SUCCESS
# ============================================================

class TestSuccessOutcome:
    def test_rag_success(self, e2e) -> None:
        e2e()
        with TestClient(app) as http:
            response = _post(http, _RAG_QUESTION)

        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "rag"
        assert payload["metadata"]["rag_used_chunks"] >= 1
        assert payload["metadata"]["outcome"] == AssistantOutcome.SUCCESS

    def test_tool_success(self, e2e) -> None:
        e2e()
        with TestClient(app) as http:
            response = _post(http, _TOOL_QUESTION)

        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "tool"
        assert payload["metadata"]["tool_success"] is True
        assert payload["metadata"]["outcome"] == "SUCCESS"

    def test_t2sql_success_after_retry(
        self, e2e, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repository = e2e()[0]
        _install_t2sql_route(monkeypatch, _retry_t2sql_client(repository=repository))

        with TestClient(app) as http:
            response = _post(http, _T2SQL_QUESTION)

        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "text_to_sql"
        assert payload["metadata"]["row_count"] == 3
        assert payload["metadata"]["outcome"] == "SUCCESS"
        assert len(repository.rows) == 2          # 重试 2 次 LLM 仍 = SUCCESS（无 RETRY/PARTIAL）

    def test_t2sql_zero_rows_is_still_success(
        self, e2e, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """row_count=0 ⇒ **SUCCESS**（不是 EMPTY；查询已给出有效业务结果）。"""
        repository = e2e()[0]
        _install_t2sql_route(monkeypatch, _retry_t2sql_client(repository=repository))
        executor = _ZeroRowExecutor()
        monkeypatch.setattr(root._default_orchestrator, "_sql_executor", executor)

        with TestClient(app) as http:
            response = _post(http, _T2SQL_QUESTION)

        assert response.status_code == 200
        payload = response.json()
        assert payload["metadata"]["row_count"] == 0
        assert payload["metadata"]["outcome"] == "SUCCESS"
        assert payload["metadata"]["outcome"] != "EMPTY"
        assert len(executor.sqls) == 1


# ============================================================
# 2. EMPTY
# ============================================================

class TestEmptyOutcome:
    def test_rag_empty_retrieval(self, e2e, monkeypatch: pytest.MonkeyPatch) -> None:
        e2e()
        from backend.app.services.rag_service import RagService

        _fresh_rag(
            monkeypatch,
            RagService(vector_search_service=_EmptyVectorSearch(), llm_client=None),
        )

        with TestClient(app) as http:
            response = _post(http, _RAG_QUESTION)

        assert response.status_code == 200                     # 空检索 ≠ 错误
        payload = response.json()
        assert payload["route"] == "rag"
        assert payload["metadata"]["rag_used_chunks"] == 0
        assert payload["metadata"]["outcome"] == "EMPTY"
        assert payload["content"]                              # 固定提示语仍在（未改 content）

    def test_empty_only_applies_to_rag(self) -> None:
        """判定函数不接受 row_count/其它信号 ⇒ 从接口层杜绝误判 EMPTY。"""
        import inspect

        signature = inspect.signature(determine_assistant_outcome)

        assert set(signature.parameters) == {
            "route",
            "refused",
            "tool_success",
            "rag_used_chunks",
        }
        assert determine_assistant_outcome(route="tool") == AssistantOutcome.SUCCESS
        assert (
            determine_assistant_outcome(route="text_to_sql")
            == AssistantOutcome.SUCCESS
        )


# ============================================================
# 3. REFUSED
# ============================================================

class TestRefusedOutcome:
    def test_t2sql_refusal(self, e2e, monkeypatch: pytest.MonkeyPatch) -> None:
        repository = e2e()[0]
        _install_t2sql_route(
            monkeypatch,
            _client_with_handler(
                lambda request: httpx.Response(
                    200, json=_response_body("step63-refusal-1", REFUSAL_MARKER)
                ),
                repository,
            ),
        )

        with TestClient(app) as http:
            response = _post(http, _T2SQL_QUESTION)

        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "text_to_sql"
        assert payload["metadata"]["refused"] is True
        assert payload["data"] is None
        assert payload["metadata"]["outcome"] == "REFUSED"
        assert payload["metadata"]["outcome"] != "FAILED"
        assert len(repository.rows) == 1               # LLM 1 次（refusal 仍是真实调用）

    def test_refused_takes_precedence_over_tool_failure(self) -> None:
        assert (
            determine_assistant_outcome(
                route="tool", refused=True, tool_success=False
            )
            == AssistantOutcome.REFUSED
        )


# ============================================================
# 4. FAILED
# ============================================================

class TestFailedOutcome:
    def test_tool_failure_is_http_200_but_failed(
        self, e2e, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        e2e()

        class _BoomHandler:
            async def __call__(self, arguments: Any):  # noqa: ANN401
                raise RuntimeError(_SECRET_SENTINEL)

        monkeypatch.setitem(
            root._default_orchestrator._tools._handlers,  # noqa: SLF001
            "get_inventory",
            _BoomHandler(),
        )

        with TestClient(app) as http:
            response = _post(http, _TOOL_QUESTION)

        assert response.status_code == 200                 # **HTTP 保持 200（兼容性要求）**
        payload = response.json()
        assert payload["metadata"]["tool_success"] is False
        assert payload["metadata"]["outcome"] == "FAILED"  # 但业务结果是 FAILED
        assert _SECRET_SENTINEL not in response.text

    def test_llm_failure(self, e2e, monkeypatch: pytest.MonkeyPatch) -> None:
        repository = e2e()[0]
        failing = _client_with_handler(
            lambda request: httpx.Response(500, json={"error": {"message": _SECRET_SENTINEL}}),
            repository,
        )

        class _LlmRag:
            async def answer(self, query: str, **kwargs: Any):  # noqa: ANN003
                from backend.app.services.rag_service import RagResponse

                result = await failing.chat([{"role": "user", "content": query}])
                return RagResponse(
                    answer=getattr(result, "content", None) or "",
                    sources=(),
                    used_chunks_count=1,
                )

        _fresh_rag(monkeypatch, _LlmRag())
        with TestClient(app) as http:
            response = _post(http, _RAG_QUESTION)

        assert response.status_code == 500
        body = response.json()
        assert set(body) == {"detail"}                     # **error 响应结构未变**
        assert "outcome" not in json.dumps(body)
        assert _SECRET_SENTINEL not in response.text

    def test_rag_runtime_failure(self, e2e, monkeypatch: pytest.MonkeyPatch) -> None:
        e2e()
        from backend.app.services.rag_service import RagService

        _fresh_rag(
            monkeypatch, RagService(vector_search_service=_BoomVectorSearch(), llm_client=None)
        )
        with TestClient(app) as http:
            response = _post(http, _RAG_QUESTION)

        assert response.status_code == 500
        assert set(response.json()) == {"detail"}

    def test_t2sql_retry_exhausted(self, e2e, monkeypatch: pytest.MonkeyPatch) -> None:
        repository = e2e()[0]
        _install_t2sql_route(
            monkeypatch,
            _client_with_handler(
                lambda request: httpx.Response(
                    200, json=_response_body("step63-invalid-1", "DROP TABLE x")
                ),
                repository,
            ),
        )
        with TestClient(app) as http:
            response = _post(http, _T2SQL_QUESTION)

        assert response.status_code == 500
        assert set(response.json()) == {"detail"}          # detail 文本结构不变

    def test_sql_execution_failure(self, e2e, monkeypatch: pytest.MonkeyPatch) -> None:
        repository = e2e()[0]
        _install_t2sql_route(monkeypatch, _retry_t2sql_client(repository=repository))
        monkeypatch.setattr(root._default_orchestrator, "_sql_executor", _BoomExecutor())

        with TestClient(app) as http:
            response = _post(http, _T2SQL_QUESTION)

        assert response.status_code == 500
        assert set(response.json()) == {"detail"}
        assert _SECRET_SENTINEL not in response.text

    def test_capability_disabled(self, e2e, monkeypatch: pytest.MonkeyPatch) -> None:
        e2e()
        from backend.app.projects.capabilities import ProjectCapabilities

        monkeypatch.setattr(
            root._default_orchestrator,
            "_capabilities",
            ProjectCapabilities(
                knowledge_enabled=False,
                text_to_sql_enabled=False,
                tool_names=(),
            ),
        )
        with TestClient(app) as http:
            response = _post(http, _RAG_QUESTION)

        assert response.status_code == 403
        assert set(response.json()) == {"detail"}


# ============================================================
# 5. Backward compatibility / Security / Determinism / Concurrency
# ============================================================

class TestBackwardCompatibility:
    def test_envelope_unchanged(self) -> None:
        from backend.app.api.orchestrator_chat import ChatResponse

        assert set(ChatResponse.model_fields) == {
            "route",
            "content",
            "data",
            "metadata",
        }

    def test_existing_metadata_keys_are_preserved(self, e2e) -> None:
        e2e()
        with TestClient(app) as http:
            rag = _post(http, _RAG_QUESTION).json()
            tool = _post(http, _TOOL_QUESTION).json()

        assert {
            "decision_source",
            "route_reason",
            "knowledge_scope",
            "rag_used_chunks",
            "request_id",
            "outcome",
        } <= set(rag["metadata"])
        assert {
            "decision_source",
            "route_reason",
            "tool_name",
            "tool_success",
            "request_id",
            "outcome",
        } <= set(tool["metadata"])

    def test_http_status_semantics_unchanged(
        self, e2e, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tool failure / refusal / RAG empty 全部保持 HTTP 200。"""
        e2e()
        from backend.app.services.rag_service import RagService

        class _BoomHandler:
            async def __call__(self, arguments: Any):  # noqa: ANN401
                raise RuntimeError("boom")

        monkeypatch.setitem(
            root._default_orchestrator._tools._handlers,  # noqa: SLF001
            "get_inventory",
            _BoomHandler(),
        )
        with TestClient(app) as http:
            assert _post(http, _TOOL_QUESTION).status_code == 200

        _fresh_rag(
            monkeypatch, RagService(vector_search_service=_EmptyVectorSearch(), llm_client=None)
        )
        with TestClient(app) as http:
            assert _post(http, _RAG_QUESTION).status_code == 200


class TestSecurity:
    def test_outcome_is_always_one_of_four_values(self, e2e) -> None:
        e2e()
        with TestClient(app) as http:
            outcomes = {
                _outcome_of(_post(http, _RAG_QUESTION)),
                _outcome_of(_post(http, _TOOL_QUESTION)),
            }

        assert outcomes <= _OUTCOME_VALUES

    def test_metadata_has_no_forbidden_fields_or_secret_sentinels(
        self, e2e, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repository = e2e()[0]
        _install_t2sql_route(monkeypatch, _retry_t2sql_client(repository=repository))

        with TestClient(app) as http:
            payload = _post(http, _T2SQL_QUESTION).json()

        metadata = payload["metadata"]
        assert metadata["outcome"] in _OUTCOME_VALUES
        for forbidden_key in (
            "exception",
            "error_message",
            "stacktrace",
            "stack_trace",
            "raw_error",
            "prompt",
            "messages",
            "error_class",
        ):
            assert forbidden_key not in metadata, forbidden_key

        blob = json.dumps(metadata, ensure_ascii=False, default=str)
        for sentinel in (_SECRET_SENTINEL, "test-key", "Bearer ", "postgresql://"):
            assert sentinel not in blob, sentinel

    def test_outcome_does_not_depend_on_content_text(
        self, e2e, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """判定不做文本匹配：把 content 换成"失败"字样，outcome 不变。"""
        e2e()
        from backend.app.services.rag_service import RagResponse

        class _TrickRag:
            def __init__(self, chunks: int, text: str) -> None:
                self._chunks = chunks
                self._text = text

            async def answer(self, query: str, **kwargs: Any):  # noqa: ANN003
                return RagResponse(
                    answer=self._text, sources=(), used_chunks_count=self._chunks
                )

        _fresh_rag(monkeypatch, _TrickRag(1, "失败"))          # 含"失败"但 chunks=1
        with TestClient(app) as http:
            assert _outcome_of(_post(http, _RAG_QUESTION)) == "SUCCESS"

        _fresh_rag(monkeypatch, _TrickRag(0, "成功"))          # 含"成功"但 chunks=0
        with TestClient(app) as http:
            assert _outcome_of(_post(http, _RAG_QUESTION)) == "EMPTY"


class TestDeterminismAndConcurrency:
    def test_same_state_same_outcome(self, e2e) -> None:
        e2e()
        with TestClient(app) as http:
            first = _outcome_of(_post(http, _TOOL_QUESTION))
            second = _outcome_of(_post(http, _TOOL_QUESTION))

        assert first == second == "SUCCESS"

    def test_five_concurrent_requests_do_not_cross_outcomes(self, e2e) -> None:
        e2e()

        def _run(_index: int) -> tuple[str, Any, str]:
            with TestClient(app) as http:
                payload = _post(http, _TOOL_QUESTION).json()
            return payload["metadata"]["request_id"], payload["metadata"]["outcome"], payload["route"]

        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(_run, range(5)))

        assert {outcome for _id, outcome, _route in results} == {"SUCCESS"}
        assert {route for _id, _outcome, route in results} == {"tool"}
        assert len({request_id for request_id, _o, _r in results}) == 5   # 无串线


__all__ = [
    "TestSuccessOutcome",
    "TestEmptyOutcome",
    "TestRefusedOutcome",
    "TestFailedOutcome",
    "TestBackwardCompatibility",
    "TestSecurity",
    "TestDeterminismAndConcurrency",
]
