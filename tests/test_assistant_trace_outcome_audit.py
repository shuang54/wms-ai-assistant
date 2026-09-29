"""Assistant Trace Outcome Boundary Audit（Phase 3.12 Step 61）——**只读审计测试**。

回答：当前系统能否表达"这次 AI 请求最终是什么结果"（Success / Failure / Refusal / Empty）？

审计结论（本文件锁定现状，**不修改任何生产行为**）：
    * ``AIOrchestrationResult`` = route + content + data + metadata（**无** outcome / status 字段）
    * **route = 能力选择结果**，不等于业务 Outcome
    * 各路径可**间接**推断：Tool（metadata.tool_success）· T2SQL-refusal（metadata.refused）·
      RAG 空检索（metadata.rag_used_chunks == 0）· 失败（HTTP 5xx）
    * Assistant Trace 只能回答"调用了什么"（三段 records），**不能**回答最终业务 Outcome
      （无 route / 无 status / 无 refusal / 无 empty / 无 HTTP 结果 / 无失败原因汇总）
    * 决策：**OUTCOME_BOUNDARY_MISSING**（仅记录未来候选，本阶段不实现）
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.api import orchestrator_chat as root
from backend.app.main import app
from backend.app.services.llm_usage_persistence_service import (
    DatabaseLLMAccountingSink,
)
from backend.app.services.rag_service import RagService
from backend.app.services.text_to_sql_service import REFUSAL_MARKER
from backend.app.services.vector_search_service import VectorSearchResult

# 复用既有装配 / Fake（不重造）
from tests.test_assistant_trace_correlation_e2e import (  # noqa: PLC2701
    _RAG_QUESTION,
    _TOOL_QUESTION,
    _client,
    _response_body,
    e2e,
)
from tests.test_assistant_trace_multi_path_e2e import (  # noqa: PLC2701
    _install_t2sql_route,
    _retry_t2sql_client,
)

_T2SQL_QUESTION = "统计最近7天的入库单数量"
_TRACE = "/api/observability/assistant-trace"
_SECRET_SENTINEL = "STEP61-RAW-EXCEPTION-MESSAGE"


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


def _post(http: TestClient, question: str) -> Any:
    return http.post("/api/ai/chat", json={"question": question})


def _trace(http: TestClient, request_id: str) -> dict[str, Any]:
    response = http.get(f"{_TRACE}/{request_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _dump(label: str, response: Any) -> None:
    """审计证据输出（pytest -s）。"""
    print(f"\n[step61] --- {label} ---")
    print(f"[step61]   HTTP = {response.status_code}")
    if response.status_code == 200:
        payload = response.json()
        print(f"[step61]   route = {payload.get('route')!r}")
        content = payload.get("content")
        print(f"[step61]   content = {content!r}")
        data = payload.get("data")
        print(f"[step61]   data type = {type(data).__name__ if data is not None else None}")
        print(
            "[step61]   metadata = "
            + json.dumps(payload.get("metadata"), ensure_ascii=False, default=str)
        )
    else:
        print(f"[step61]   body = {response.text[:240]}")


# ============================================================
# 1 / 2. RAG：正常 + 空检索
# ============================================================

class TestRagOutcome:
    def test_rag_success_outcome(self, e2e) -> None:
        repository = e2e()[0]

        with TestClient(app) as http:
            response = _post(http, _RAG_QUESTION)
            _dump("RAG 正常", response)

            assert response.status_code == 200
            payload = response.json()
            assert payload["route"] == "rag"
            assert isinstance(payload["content"], str) and payload["content"].strip()
            assert payload["metadata"]["request_id"]
            assert "tool_success" not in payload["metadata"]
            assert "refused" not in payload["metadata"]

            trace = _trace(http, payload["metadata"]["request_id"])
            assert len(trace["llm_usage"]) == 1
            assert trace["tool_executions"] == []

        assert len(repository.rows) == 1

    def test_rag_empty_retrieval_outcome(
        self, e2e, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """空检索：真实 RagService + 空结果（不调用 LLM）。"""
        e2e()

        class _EmptyVectorSearch:
            async def search(self, query: str, *, top_k: int):  # noqa: ANN201
                return []

        rag = RagService(
            vector_search_service=_EmptyVectorSearch(),
            llm_client=None,
        )
        monkeypatch.setattr(root._default_orchestrator, "_rag", rag)

        with TestClient(app) as http:
            response = _post(http, _RAG_QUESTION)
            _dump("RAG 空检索", response)

            assert response.status_code == 200            # 空结果 ≠ 错误
            payload = response.json()
            assert payload["route"] == "rag"
            assert isinstance(payload["content"], str) and payload["content"].strip()
            assert payload["metadata"]["rag_used_chunks"] == 0
            assert payload["metadata"]["request_id"]


# ============================================================
# 3 / 4. TOOL：成功 + 失败
# ============================================================

class TestToolOutcome:
    def test_tool_success_outcome(self, e2e) -> None:
        e2e()

        with TestClient(app) as http:
            response = _post(http, _TOOL_QUESTION)
            _dump("TOOL 正常", response)

            assert response.status_code == 200
            payload = response.json()
            assert payload["route"] == "tool"
            assert payload["metadata"]["tool_success"] is True
            assert payload["metadata"]["tool_name"] == "get_inventory"

            trace = _trace(http, payload["metadata"]["request_id"])
            assert len(trace["tool_executions"]) == 1
            assert trace["tool_executions"][0]["success"] is True
            assert trace["tool_executions"][0]["request_id"] == payload["metadata"][
                "request_id"
            ]
            assert trace["llm_usage"] == []

    def test_tool_failure_outcome_is_http_200(
        self, e2e, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tool 业务失败：HTTP **200**（业务失败 ≠ 传输失败），metadata.tool_success=False。"""
        e2e()

        class _BoomHandler:
            async def __call__(self, arguments: Any):  # noqa: ANN401
                raise RuntimeError(_SECRET_SENTINEL)

        # 替换**装配中**的 registry handler（harness 的 Tool 执行边界使用同一 registry）
        monkeypatch.setitem(
            root._default_orchestrator._tools._handlers,  # noqa: SLF001
            "get_inventory",
            _BoomHandler(),
        )

        with TestClient(app) as http:
            response = _post(http, _TOOL_QUESTION)
            _dump("TOOL 失败（handler 抛错）", response)

            assert response.status_code == 200            # 业务失败 ≠ 传输失败
            payload = response.json()
            assert payload["route"] == "tool"
            assert payload["metadata"]["tool_success"] is False
            assert _SECRET_SENTINEL not in response.text   # 原始异常消息不外泄
            assert "RuntimeError" in payload["content"]    # 只暴露异常**类名**（规范文本）

            trace = _trace(http, payload["metadata"]["request_id"])
            assert len(trace["tool_executions"]) == 1
            record = trace["tool_executions"][0]
            assert record["success"] is False
            assert record["request_id"] == payload["metadata"]["request_id"]
            # 当前事实：Tool 记录**不含**失败原因（error_code / error_type 均为 None；
            # "为什么失败" 只存在于 chat content，不进入 Trace）
            assert record["error_code"] is None
            assert record["error_type"] is None
            assert _SECRET_SENTINEL not in json.dumps(record, ensure_ascii=False)


# ============================================================
# 5 / 6. TEXT_TO_SQL：成功 + Refusal
# ============================================================

class TestTextToSqlOutcome:
    def test_t2sql_success_outcome(self, e2e, monkeypatch: pytest.MonkeyPatch) -> None:
        repository = e2e()[0]
        client = _retry_t2sql_client(repository=repository)
        _install_t2sql_route(monkeypatch, client)

        with TestClient(app) as http:
            response = _post(http, _T2SQL_QUESTION)
            _dump("TEXT_TO_SQL 正常（重试后成功）", response)

            assert response.status_code == 200
            payload = response.json()
            assert payload["route"] == "text_to_sql"
            metadata = payload["metadata"]
            for key in (
                "decision_source",
                "route_reason",
                "row_count",
                "truncated",
                "execution_time_ms",
                "selected_tables",
                "project_id",
                "request_id",
            ):
                assert key in metadata, key
            assert metadata["row_count"] == 3
            assert "refused" not in metadata

            trace = _trace(http, metadata["request_id"])
            assert len(trace["llm_usage"]) == 2          # 真实重试 2 次
            assert trace["tool_executions"] == []

    def test_refusal_outcome(
        self, e2e, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Refusal：HTTP 200 + metadata.refused=True + data=None + 不进入 Executor。"""
        repository = e2e()[0]
        state = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            state["n"] += 1
            return httpx.Response(
                200,
                json=_response_body("step61-refusal-1", REFUSAL_MARKER),
            )

        client = _client_with_handler(handler, repository)
        executor = _install_t2sql_route(monkeypatch, client)

        with TestClient(app) as http:
            response = _post(http, _T2SQL_QUESTION)
            _dump("TEXT_TO_SQL refusal", response)

            assert response.status_code == 200
            payload = response.json()
            assert payload["route"] == "text_to_sql"
            assert payload["metadata"]["refused"] is True
            assert payload["data"] is None
            assert "refusal_reason" not in payload["metadata"]
            assert executor.sqls == []                   # 未进入 Executor
            assert state["n"] == 1                       # 1 次真实 LLM 调用

            trace = _trace(http, payload["metadata"]["request_id"])
            assert len(trace["llm_usage"]) == 1


# ============================================================
# 7 / 8. Failure：LLM / RAG / 生成失败
# ============================================================

class TestFailureOutcome:
    def test_llm_failure_outcome(self, e2e, monkeypatch: pytest.MonkeyPatch) -> None:
        repository = e2e()[0]
        failing = _client_with_handler(
            lambda request: httpx.Response(
                500, json={"error": {"message": _SECRET_SENTINEL}}
            ),
            repository,
        )
        monkeypatch.setattr(
            root._default_orchestrator, "_rag", _LlmBacked(failing)
        )

        with TestClient(app) as http:
            response = _post(http, _RAG_QUESTION)
            _dump("LLM 失败（provider 500）", response)

            assert response.status_code == 500
            assert _SECRET_SENTINEL not in response.text
            assert "AI 能力执行失败" in response.text

    def test_rag_failure_outcome_records_observation(
        self, e2e, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """RAG 检索失败：HTTP 500 + RAG observation 仍产生（Step 43 异常路径）。"""
        e2e()
        from backend.app.services.rag_execution_observation import (
            RagExecutionObservation,
        )

        observations: list[RagExecutionObservation] = []

        class _Recorder:
            def record(self, observation: RagExecutionObservation) -> None:
                observations.append(observation)

        class _BoomVectorSearch:
            async def search(self, query: str, *, top_k: int):  # noqa: ANN201
                raise RuntimeError(_SECRET_SENTINEL)

        rag = RagService(
            vector_search_service=_BoomVectorSearch(),
            llm_client=None,
            observer=_Recorder(),
        )
        monkeypatch.setattr(root._default_orchestrator, "_rag", rag)

        with TestClient(app) as http:
            response = _post(http, _RAG_QUESTION)
            _dump("RAG 检索失败", response)

            assert response.status_code == 500
            assert _SECRET_SENTINEL not in response.text
            assert len(observations) == 1                # 异常路径也产生 observation
            assert observations[0].request_id            # 可关联 assistant_request_id
            assert observations[0].result_count == 0

    def test_t2sql_generation_exhausted_outcome(
        self, e2e, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """重试耗尽（Validator 始终拒绝）：HTTP 500，且**无法**与生成失败区分。"""
        repository = e2e()[0]
        always_invalid = _client_with_handler(
            lambda request: httpx.Response(
                200, json=_response_body("step61-invalid-1", "DROP TABLE x")
            ),
            repository,
        )
        _install_t2sql_route(monkeypatch, always_invalid)

        with TestClient(app) as http:
            response = _post(http, _T2SQL_QUESTION)
            _dump("T2SQL 重试耗尽", response)

            assert response.status_code == 500
            assert "AI 能力执行失败" in response.text

    def test_capability_denied_is_403(self, e2e, monkeypatch: pytest.MonkeyPatch) -> None:
        """能力禁用：403（与 500 失败明确区分）。"""
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
            _dump("能力禁用（knowledge off）", response)

            assert response.status_code == 403
            assert _SECRET_SENTINEL not in response.text


def _client_with_handler(handler: Any, repository: Any) -> Any:
    """真实 Client + MockTransport + 真实 Accounting Sink（离线 → 内存仓储）。"""
    from backend.app.llm.client import OpenAICompatibleClient
    from backend.app.services.llm_usage_persistence_service import (
        LLMUsagePersistenceService,
    )

    sink = DatabaseLLMAccountingSink(
        persistence_service=LLMUsagePersistenceService(repository=repository)
    )
    return OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://step61.fake/v1",
        model="step61-model",
        provider="step61-provider",
        transport=httpx.MockTransport(handler),
        accounting_sink=sink,
    )


class _LlmBacked:
    """RAG 替身：调用真实 Client（可注入失败 transport）。"""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def answer(self, query: str, **kwargs: Any):  # noqa: ANN003
        from backend.app.services.rag_service import RagResponse

        response = await self._client.chat(
            messages=[{"role": "user", "content": query}]
        )
        return RagResponse(
            answer=getattr(response, "content", None) or "（无内容）",
            sources=(),
            used_chunks_count=1,
        )


__all__ = [
    "TestRagOutcome",
    "TestToolOutcome",
    "TestTextToSqlOutcome",
    "TestFailureOutcome",
]
