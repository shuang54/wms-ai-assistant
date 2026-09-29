"""Assistant Outcome Contract Design Audit（Phase 3.12 Step 62）——**设计审计测试**。

本文件**不修改任何生产代码**；只在测试内定义一个 **candidate outcome projection**
（纯函数），用**真实驱动出来的响应包络**验证该 Contract 能否覆盖当前全部行为：

    SUCCESS · EMPTY · REFUSED · FAILED

候选判定优先级（本阶段审计结论）：

    REFUSED  →  FAILED  →  EMPTY  →  SUCCESS

    1) REFUSED：业务意图信号（``metadata.refused is True``）—— 预期的安全行为，
       不是失败、也不是空结果
    2) FAILED ：传输 / 执行失败（HTTP ≥ 400，或 ``tool_success is False`` 的 200 包络）
       —— 失败优先于"没有结果"
    3) EMPTY  ：请求正常完成但**没有可提供的业务结果**（当前唯一可判定信号：
       ``rag_used_chunks == 0`` 的 RAG 空检索；LLM 0 次）
    4) SUCCESS：其余正常完成

禁止（与任务书一致）：不新增 Outcome DTO / 字段 / API 参数 / DB 变更；
    不把 exception message / prompt / SQL / chunk 正文 / 凭据 纳入判定或输出。
"""
from __future__ import annotations

import inspect
import os
from typing import Any, Mapping

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.api import orchestrator_chat as root
from backend.app.main import app
from backend.app.services.llm_usage_persistence_service import (
    DatabaseLLMAccountingSink,
    LLMUsagePersistenceService,
)
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

SUCCESS = "SUCCESS"
EMPTY = "EMPTY"
REFUSED = "REFUSED"
FAILED = "FAILED"

_T2SQL_QUESTION = "统计最近7天的入库单数量"

#: 候选 Contract 允许读取的**输入白名单**（超出即为设计缺陷）
ALLOWED_INPUT_KEYS = frozenset(
    {"status_code", "route", "refused", "tool_success", "rag_used_chunks"}
)

#: 禁止进入判定与输出的内容（allowlist 原则）
FORBIDDEN_TOKENS = (
    "detail",
    "prompt",
    "messages",
    "sql",
    "chunk",
    "content",
    "api_key",
    "authorization",
    "password",
    "database_url",
    "traceback",
    "error_message",
)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


def project_outcome(*, status_code: int, payload: Mapping[str, Any] | None) -> str:
    """**候选** outcome projection（仅测试内；优先级 REFUSED → FAILED → EMPTY → SUCCESS）。

    输入白名单：``status_code`` · ``payload["route"]`` ·
    ``payload["metadata"]{refused, tool_success, rag_used_chunks}``。
    **不读取** detail / content / data / prompt / SQL / chunk / 凭据。
    """
    if status_code >= 400:
        return FAILED
    if payload is None:
        return FAILED
    metadata = payload.get("metadata") or {}
    if metadata.get("refused") is True:
        return REFUSED
    route = payload.get("route")
    if route == "tool" and metadata.get("tool_success") is False:
        return FAILED
    if route == "rag" and metadata.get("rag_used_chunks") == 0:
        return EMPTY
    return SUCCESS


def _ask(http: TestClient, question: str) -> tuple[int, Any]:
    response = http.post("/api/ai/chat", json={"question": question})
    return response.status_code, (
        response.json() if response.status_code == 200 else None
    )


def _client_with_handler(handler: Any, repository: Any) -> Any:
    """真实 Client + MockTransport + 真实 Accounting Sink（离线 → 内存仓储）。"""
    from backend.app.llm.client import OpenAICompatibleClient

    return OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://step62.fake/v1",
        model="step62-model",
        provider="step62-provider",
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
        raise RuntimeError("step62-rag-boom")


# ============================================================
# 1. 真实包络 × 候选 Contract（Cases A ~ I）
# ============================================================

class TestProjectionCoversRealBehavior:
    def test_case_a_rag_success(self, e2e) -> None:
        e2e()
        with TestClient(app) as http:
            status, payload = _ask(http, _RAG_QUESTION)

        assert status == 200
        assert project_outcome(status_code=status, payload=payload) == SUCCESS

    def test_case_b_rag_empty(self, e2e, monkeypatch: pytest.MonkeyPatch) -> None:
        e2e()
        from backend.app.services.rag_service import RagService

        monkeypatch.setattr(
            root._default_orchestrator,
            "_rag",
            RagService(vector_search_service=_EmptyVectorSearch(), llm_client=None),
        )
        with TestClient(app) as http:
            status, payload = _ask(http, _RAG_QUESTION)

        assert status == 200
        assert payload["metadata"]["rag_used_chunks"] == 0
        assert project_outcome(status_code=status, payload=payload) == EMPTY
        assert project_outcome(status_code=status, payload=payload) != FAILED

    def test_case_c_t2sql_refused(self, e2e, monkeypatch: pytest.MonkeyPatch) -> None:
        repository = e2e()[0]
        client = _client_with_handler(
            lambda request: httpx.Response(
                200, json=_response_body("step62-refusal-1", REFUSAL_MARKER)
            ),
            repository,
        )
        _install_t2sql_route(monkeypatch, client)

        with TestClient(app) as http:
            status, payload = _ask(http, _T2SQL_QUESTION)

        assert status == 200
        assert payload["metadata"]["refused"] is True
        assert project_outcome(status_code=status, payload=payload) == REFUSED
        assert project_outcome(status_code=status, payload=payload) != FAILED

    def test_case_d_t2sql_retry_then_success(
        self, e2e, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repository = e2e()[0]
        _install_t2sql_route(monkeypatch, _retry_t2sql_client(repository=repository))

        with TestClient(app) as http:
            status, payload = _ask(http, _T2SQL_QUESTION)

        assert status == 200
        assert payload["metadata"]["row_count"] == 3
        assert project_outcome(status_code=status, payload=payload) == SUCCESS
        # 2 次 LLM 调用（重试）仍然 = SUCCESS ⇒ LLM call 数不是 outcome 判定依据
        assert len(repository.rows) == 2

    def test_case_e_t2sql_retry_exhausted(
        self, e2e, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repository = e2e()[0]
        client = _client_with_handler(
            lambda request: httpx.Response(
                200, json=_response_body("step62-invalid-1", "DROP TABLE x")
            ),
            repository,
        )
        _install_t2sql_route(monkeypatch, client)

        with TestClient(app) as http:
            status, payload = _ask(http, _T2SQL_QUESTION)

        assert status == 500
        assert project_outcome(status_code=status, payload=payload) == FAILED

    def test_case_f_tool_success(self, e2e) -> None:
        e2e()
        with TestClient(app) as http:
            status, payload = _ask(http, _TOOL_QUESTION)

        assert status == 200
        assert payload["metadata"]["tool_success"] is True
        assert project_outcome(status_code=status, payload=payload) == SUCCESS

    def test_case_g_tool_failure_is_http_200_but_failed(
        self, e2e, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**关键审计结论**：HTTP 200 + tool_success=False ⇒ Business Outcome = FAILED。"""
        e2e()

        class _BoomHandler:
            async def __call__(self, arguments: Any):  # noqa: ANN401
                raise RuntimeError("step62-tool-boom")

        monkeypatch.setitem(
            root._default_orchestrator._tools._handlers,  # noqa: SLF001
            "get_inventory",
            _BoomHandler(),
        )
        with TestClient(app) as http:
            status, payload = _ask(http, _TOOL_QUESTION)

        assert status == 200                              # 传输成功
        assert payload["metadata"]["tool_success"] is False
        assert project_outcome(status_code=status, payload=payload) == FAILED
        # ⇒ HTTP 200 **不能**直接定义为业务 SUCCESS

    def test_case_h_rag_runtime_failure(
        self, e2e, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        e2e()
        from backend.app.services.rag_service import RagService

        monkeypatch.setattr(
            root._default_orchestrator,
            "_rag",
            RagService(vector_search_service=_BoomVectorSearch(), llm_client=None),
        )
        with TestClient(app) as http:
            status, payload = _ask(http, _RAG_QUESTION)

        assert status == 500
        assert project_outcome(status_code=status, payload=payload) == FAILED

    def test_case_i_llm_failure(self, e2e, monkeypatch: pytest.MonkeyPatch) -> None:
        e2e()
        from backend.app.services.rag_service import RagService

        repository = e2e()[0]
        failing = _client_with_handler(
            lambda request: httpx.Response(500, json={"error": {"message": "boom"}}),
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

        monkeypatch.setattr(root._default_orchestrator, "_rag", _LlmRag())
        with TestClient(app) as http:
            status, payload = _ask(http, _RAG_QUESTION)

        assert status == 500
        assert project_outcome(status_code=status, payload=payload) == FAILED

    def test_capability_denied_is_failed(self, e2e, monkeypatch: pytest.MonkeyPatch) -> None:
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
            status, payload = _ask(http, _RAG_QUESTION)

        assert status == 403
        assert project_outcome(status_code=status, payload=payload) == FAILED


# ============================================================
# 2. Projection Cases：优先级与"底层成功 ≠ Assistant success"
# ============================================================

class TestProjectionCases:
    def test_case_j_underlying_success_but_assistant_failure(self) -> None:
        """Case J：LLM 成功 + Tool 成功 + 最终组装/传输失败 ⇒ **FAILED**。

        当前**不可观测**（无已知触发路径；Orchestrator 组装失败会直接抛异常 →
        HTTP 500 且无包络）—— 此处用 projection 输入验证 Contract **能够容纳**
        该状态：底层执行信号（若有）不得覆盖请求级失败。
        """
        synthetic_payload = {
            "route": "tool",
            "content": "tool answered",
            "metadata": {"tool_success": True, "request_id": "step62-synthetic"},
        }

        assert project_outcome(status_code=200, payload=synthetic_payload) == SUCCESS
        assert project_outcome(status_code=500, payload=synthetic_payload) == FAILED
        assert project_outcome(status_code=500, payload=None) == FAILED

    def test_precedence_refused_wins_over_tool_failure(self) -> None:
        """优先级：REFUSED 先于 FAILED（业务意图信号优先，即便同时出现失败信号）。"""
        payload = {
            "route": "tool",
            "metadata": {"refused": True, "tool_success": False},
        }

        assert project_outcome(status_code=200, payload=payload) == REFUSED

    def test_precedence_failed_wins_over_empty(self) -> None:
        """优先级：FAILED 先于 EMPTY（失败优先于"没有结果"）。"""
        payload = {"route": "rag", "metadata": {"rag_used_chunks": 0}}

        assert project_outcome(status_code=500, payload=payload) == FAILED
        assert project_outcome(status_code=200, payload=payload) == EMPTY

    def test_precedence_empty_wins_over_success(self) -> None:
        payload = {"route": "rag", "metadata": {"rag_used_chunks": 0}}

        assert project_outcome(status_code=200, payload=payload) == EMPTY

    def test_content_and_data_are_auxiliary_only(self) -> None:
        """content / data 不参与判定（同场景不同 content → 相同 outcome）。"""
        empty_signal = {"route": "rag", "metadata": {"rag_used_chunks": 0}}

        first = {**empty_signal, "content": "知识库中没有找到…", "data": None}
        second = {**empty_signal, "content": "A", "data": {"fake": True}}

        assert project_outcome(status_code=200, payload=first) == project_outcome(
            status_code=200, payload=second
        ) == EMPTY

    def test_t2sql_zero_rows_is_success_not_empty(self) -> None:
        """设计决定：SQL 成功执行但 0 行 ⇒ **SUCCESS**（查询已回答"无匹配数据"）。

        与 RAG 空检索不同：RAG 空检索时 LLM **未被调用**（无生成结果可言）。
        （标注为 candidate decision —— 需产品确认；本阶段只记录。）
        """
        payload = {
            "route": "text_to_sql",
            "metadata": {"row_count": 0, "truncated": False},
        }

        assert project_outcome(status_code=200, payload=payload) == SUCCESS

    def test_missing_metadata_falls_back_to_success(self) -> None:
        """无业务信号的 200 包络 → SUCCESS（不猜测失败）。"""
        assert project_outcome(status_code=200, payload={"route": "rag"}) == SUCCESS
        assert project_outcome(status_code=200, payload={}) == SUCCESS


# ============================================================
# 3. Contract 输入白名单 / 安全边界（静态）
# ============================================================

def _projection_code_tokens() -> tuple[set[str], set[str]]:
    """返回（标识符名, 字符串常量）——**排除 docstring**（只审计代码本身）。"""
    import ast

    tree = ast.parse(inspect.getsource(project_outcome))
    function = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef)
    )
    body = function.body[1:] if ast.get_docstring(function) else function.body
    names: set[str] = set()
    constants: set[str] = set()
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            constants.add(node.value)
    return names, constants


class TestContractInputBoundary:
    def test_projection_reads_only_allowlisted_keys(self) -> None:
        names, constants = _projection_code_tokens()

        assert set(ALLOWED_INPUT_KEYS) == {
            "status_code",
            "route",
            "refused",
            "tool_success",
            "rag_used_chunks",
        }
        for key in ALLOWED_INPUT_KEYS:
            assert key in names or key in constants, key

    def test_projection_never_reads_forbidden_payload_sections(self) -> None:
        names, constants = _projection_code_tokens()

        for token in FORBIDDEN_TOKENS:
            assert token not in names, f"标识符: {token}"
            assert token not in constants, f"字符串常量: {token}"

    def test_failure_detail_is_not_part_of_the_contract(self) -> None:
        """当前失败信息只在 HTTP ``detail``（且仅到异常类名）—— 候选 Contract 不使用它。"""
        names, constants = _projection_code_tokens()

        assert "detail" not in names | constants
        assert "response" not in names            # 不接受原始 response / 响应文本

    def test_no_outcome_fields_in_current_contract(self) -> None:
        """现状：响应与 metadata **尚无** outcome / status 字段（契约未被修改）。"""
        from backend.app.api.orchestrator_chat import ChatResponse

        assert set(ChatResponse.model_fields) == {
            "route",
            "content",
            "data",
            "metadata",
        }
        for dto in (ChatResponse,):
            for name in dto.model_fields:
                assert "outcome" not in name.lower()
                assert "status" not in name.lower()


__all__ = [
    "SUCCESS",
    "EMPTY",
    "REFUSED",
    "FAILED",
    "project_outcome",
    "TestProjectionCoversRealBehavior",
    "TestProjectionCases",
    "TestContractInputBoundary",
]
