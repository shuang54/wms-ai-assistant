"""RAG API 测试（Phase 3.5.5）。

通过 monkeypatch 替换 api.rag._rag_service 为 FakeRagService，
验证：

    - 正常路径：200 + answer / sources / used_chunks_count
    - top_k 透传与默认值（None 透传，不擅自覆盖）
    - 入参校验：空 query / 纯空白 / 非法 top_k → 422，且不调用 Service
    - 空检索结果 → 200（非错误）
    - Service 异常 → 相应 HTTP 状态码（不伪装成功、不泄露敏感信息）
    - RagResponse → RagAnswerResponse 字段映射完整

全部 Mock RagService，**不发起真实 DB / Embedding / LLM 调用**。
真实链路由 tests/test_rag_service.py（DB 集成）与
tests/test_llm_real_smoke.py（可选真实 LLM）覆盖。
"""
from __future__ import annotations

import pytest
from contextlib import contextmanager
from fastapi.testclient import TestClient

from backend.app.api import rag as rag_module
from backend.app.embedding.exceptions import (
    EmbeddingAPIError,
    EmbeddingConfigurationError,
    EmbeddingError,
    EmbeddingResponseError,
)
from backend.app.llm.client import (
    LLMConfigError,
    LLMError,
    LLMRequestError,
    LLMResponseError,
)
from backend.app.main import app
from backend.app.services.rag_service import (
    DEFAULT_EMPTY_ANSWER,
    RagResponse,
    RagSource,
)
from backend.app.services.vector_search_service import (
    VectorSearchError,
    VectorSearchInputError,
    VectorSearchParameterError,
)


# ============================================================
# Fake Service
# ============================================================

class FakeRagService:
    """记录调用并可注入响应 / 异常的 RagService 替身。"""

    def __init__(
        self,
        *,
        response: RagResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self._response = response or RagResponse(
            answer="mock-answer",
            sources=(
                RagSource(
                    chunk_id=1,
                    document_id=10,
                    chunk_index=0,
                    content="chunk-内容",
                    similarity=0.91,
                    metadata={"heading_path": ["采购入库", "操作步骤"]},
                ),
            ),
            used_chunks_count=1,
        )
        self._error = error
        self.calls: list[tuple[str, int | None]] = []

    async def answer(self, query: str, *, top_k: int | None = None) -> RagResponse:
        self.calls.append((query, top_k))
        if self._error is not None:
            raise self._error
        return self._response


@pytest.fixture()
def client(monkeypatch):
    """构造测试客户端；返回 context manager，yield (TestClient, FakeRagService)。"""

    @contextmanager
    def _make(
        *,
        response: RagResponse | None = None,
        error: Exception | None = None,
        raise_server_exceptions: bool = True,
    ):
        fake = FakeRagService(response=response, error=error)
        monkeypatch.setattr(rag_module, "_rag_service", fake)
        with TestClient(
            app, raise_server_exceptions=raise_server_exceptions
        ) as c:
            yield c, fake

    return _make


# ============================================================
# 1. 正常请求
# ============================================================

class TestNormalRequest:
    def test_returns_200_with_answer_and_sources(self, client) -> None:
        """POST /api/rag/answer → 200 + answer / sources / used_chunks_count。"""
        with client() as (c, _fake):
            response = c.post("/api/rag/answer", json={"query": "采购入库怎么操作？"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["answer"] == "mock-answer"
        assert payload["used_chunks_count"] == 1
        assert len(payload["sources"]) == 1
        src = payload["sources"][0]
        assert src["chunk_id"] == 1
        assert src["document_id"] == 10
        assert src["chunk_index"] == 0
        assert src["content"] == "chunk-内容"
        assert src["similarity"] == pytest.approx(0.91)
        assert src["metadata"] == {"heading_path": ["采购入库", "操作步骤"]}

    def test_passes_query_to_service(self, client) -> None:
        """query 原样传给 RagService.answer。"""
        with client() as (c, fake):
            c.post("/api/rag/answer", json={"query": "  采购入库？  "})
        assert len(fake.calls) == 1
        assert fake.calls[0][0] == "  采购入库？  "

    def test_openapi_contains_rag_endpoint(self, client) -> None:
        """FastAPI 自动 OpenAPI 必须包含 /api/rag/answer。"""
        with client() as (c, _fake):
            spec = c.get("/openapi.json").json()
        assert "/api/rag/answer" in spec["paths"]
        post = spec["paths"]["/api/rag/answer"]["post"]
        assert "requestBody" in post
        assert "200" in post["responses"]


# ============================================================
# 2. top_k
# ============================================================

class TestTopK:
    def test_explicit_top_k_forwarded_to_service(self, client) -> None:
        """top_k=3 → RagService.answer(top_k=3)。"""
        with client() as (c, fake):
            c.post("/api/rag/answer", json={"query": "q", "top_k": 3})
        assert fake.calls == [("q", 3)]

    def test_default_top_k_forwarded_as_none(self, client) -> None:
        """省略 top_k → 传 None（由 Service 层取 RAG_TOP_K 默认值，Router 不复制配置逻辑）。"""
        with client() as (c, fake):
            c.post("/api/rag/answer", json={"query": "q"})
        assert fake.calls == [("q", None)]

    @pytest.mark.parametrize("top_k", [1, 5, 50])
    def test_valid_top_k_bounds_accepted(self, client, top_k) -> None:
        with client() as (c, fake):
            response = c.post(
                "/api/rag/answer", json={"query": "q", "top_k": top_k}
            )
        assert response.status_code == 200
        assert fake.calls == [("q", top_k)]


# ============================================================
# 3. 入参校验（422，且不调 Service）
# ============================================================

class TestValidation:
    @pytest.mark.parametrize("query", ["", "   ", "\n\t "])
    def test_blank_query_rejected_422(self, client, query) -> None:
        """空 / 纯空白 query → 422，且不调用 RagService。"""
        with client() as (c, fake):
            response = c.post("/api/rag/answer", json={"query": query})
        assert response.status_code == 422
        assert fake.calls == []

    def test_missing_query_rejected_422(self, client) -> None:
        with client() as (c, fake):
            response = c.post("/api/rag/answer", json={})
        assert response.status_code == 422
        assert fake.calls == []

    def test_non_string_query_rejected_422(self, client) -> None:
        with client() as (c, fake):
            response = c.post("/api/rag/answer", json={"query": 123})
        assert response.status_code == 422
        assert fake.calls == []

    @pytest.mark.parametrize("top_k", [0, -1, 51, 100])
    def test_invalid_top_k_rejected_422(self, client, top_k) -> None:
        """top_k 越界 [1, 50] → 422，且不调用 RagService。"""
        with client() as (c, fake):
            response = c.post(
                "/api/rag/answer", json={"query": "q", "top_k": top_k}
            )
        assert response.status_code == 422
        assert fake.calls == []

    def test_non_integer_top_k_rejected_422(self, client) -> None:
        """非整数字符串 top_k 由 Pydantic 拒绝（strict 数值语义之外的类型仍拒绝）。"""
        with client() as (c, fake):
            response = c.post(
                "/api/rag/answer", json={"query": "q", "top_k": "abc"}
            )
        assert response.status_code == 422
        assert fake.calls == []

    def test_float_top_k_rejected_422(self, client) -> None:
        """浮点 top_k（如 2.5）→ 422。"""
        with client() as (c, fake):
            response = c.post(
                "/api/rag/answer", json={"query": "q", "top_k": 2.5}
            )
        assert response.status_code == 422
        assert fake.calls == []


# ============================================================
# 4. 空检索结果 → 200
# ============================================================

class TestEmptyKnowledgeBase:
    def test_empty_search_returns_200_canned_answer(self, client) -> None:
        """空知识库不是服务器错误：200 + 默认提示语 + sources=[]。"""
        empty = RagResponse(
            answer=DEFAULT_EMPTY_ANSWER,
            sources=(),
            used_chunks_count=0,
        )
        with client(response=empty) as (c, _fake):
            response = c.post("/api/rag/answer", json={"query": "任何问题"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["answer"] == DEFAULT_EMPTY_ANSWER
        assert payload["sources"] == []
        assert payload["used_chunks_count"] == 0

    def test_empty_sources_serializes_as_list_not_tuple(self, client) -> None:
        """JSON 序列化：sources 必须是 [] 而非其它形式。"""
        empty = RagResponse(answer="x", sources=(), used_chunks_count=0)
        with client(response=empty) as (c, _fake):
            payload = c.post("/api/rag/answer", json={"query": "q"}).json()
        assert payload["sources"] == []
        assert isinstance(payload["sources"], list)


# ============================================================
# 5. Service 异常 → HTTP 状态码映射
# ============================================================

class TestErrorMapping:
    @pytest.mark.parametrize(
        ("exc", "expected_status", "expected_fragment"),
        [
            (VectorSearchInputError("query 为空"), 400, "query 为空"),
            (VectorSearchParameterError("top_k 越界"), 422, "top_k"),
            (EmbeddingConfigurationError("api key missing"), 503, "配置错误"),
            (EmbeddingAPIError("硅基流动 503"), 502, "请求失败"),
            (EmbeddingResponseError("bad json"), 502, "响应解析失败"),
            (EmbeddingError("dimension mismatch"), 500, "调用异常"),
            (VectorSearchError("pgvector down"), 502, "向量检索失败"),
            (LLMConfigError("LLM key missing"), 503, "LLM 配置错误"),
            (LLMRequestError("DeepSeek 500"), 502, "LLM 请求失败"),
            (LLMResponseError("bad llm json"), 502, "LLM 响应解析失败"),
        ],
    )
    def test_known_service_errors_map_to_status(
        self, client, exc, expected_status, expected_fragment
    ) -> None:
        """已知异常 → 明确状态码，不伪装成功。"""
        with client(error=exc) as (c, _fake):
            response = c.post("/api/rag/answer", json={"query": "q"})
        assert response.status_code == expected_status
        detail = response.json()["detail"]
        assert expected_fragment in detail

    def test_unknown_error_returns_500_without_leaking_traceback(self, client) -> None:
        """未知异常 → 500（ServerErrorMiddleware 兜底），且响应不含 traceback。"""
        with client(error=RuntimeError("internal boom"), raise_server_exceptions=False) as (c, _fake):
            response = c.post("/api/rag/answer", json={"query": "q"})
        assert response.status_code == 500
        body = response.text
        assert "Traceback" not in body
        assert "File \"" not in body
        assert "internal boom" not in body

    @pytest.mark.parametrize(
        "secret_fragment",
        [
            "sk-abc123",
            "Authorization",
            "Bearer ",
            "postgresql://",
            "SELECT ",
            "<=>",
        ],
    )
    def test_error_response_never_leaks_secrets(self, client, secret_fragment) -> None:
        """异常响应中不得出现 API Key / 连接串 / SQL / pgvector 算子。"""
        with client(error=LLMRequestError("upstream failed")) as (c, _fake):
            response = c.post("/api/rag/answer", json={"query": "q"})
        assert response.status_code == 502
        assert secret_fragment not in response.text


# ============================================================
# 6. Response 字段映射
# ============================================================

class TestResponseMapping:
    def test_all_fields_mapped_correctly(self, client) -> None:
        """RagResponse → RagAnswerResponse：全部字段一一对应。"""
        result = RagResponse(
            answer="完整回答",
            sources=(
                RagSource(
                    chunk_id=101,
                    document_id=12,
                    chunk_index=3,
                    content="片段甲",
                    similarity=0.95,
                    metadata={"heading_path": ["A", "B"], "source_type": "md"},
                ),
                RagSource(
                    chunk_id=102,
                    document_id=13,
                    chunk_index=0,
                    content="片段乙",
                    similarity=0.80,
                    metadata={},
                ),
            ),
            used_chunks_count=2,
        )
        with client(response=result) as (c, _fake):
            payload = c.post(
                "/api/rag/answer", json={"query": "q", "top_k": 5}
            ).json()

        assert set(payload.keys()) == {"answer", "sources", "used_chunks_count"}
        assert payload["answer"] == "完整回答"
        assert payload["used_chunks_count"] == 2
        assert len(payload["sources"]) == 2
        for actual, expected in zip(payload["sources"], result.sources):
            assert set(actual.keys()) == {
                "chunk_id",
                "document_id",
                "chunk_index",
                "content",
                "similarity",
                "metadata",
            }
            assert actual["chunk_id"] == expected.chunk_id
            assert actual["document_id"] == expected.document_id
            assert actual["chunk_index"] == expected.chunk_index
            assert actual["content"] == expected.content
            assert actual["similarity"] == pytest.approx(expected.similarity)
            assert actual["metadata"] == expected.metadata

    def test_response_omits_internal_fields(self, client) -> None:
        """API 响应不包含 distance / prompt / context 等内部字段。"""
        with client() as (c, _fake):
            payload = c.post("/api/rag/answer", json={"query": "q"}).json()
        assert "distance" not in payload
        assert "context" not in payload
        assert "prompt" not in payload
        assert "messages" not in payload
        for src in payload["sources"]:
            assert set(src.keys()) == {
                "chunk_id",
                "document_id",
                "chunk_index",
                "content",
                "similarity",
                "metadata",
            }


# ============================================================
# 7. /api/chat 未受影响
# ============================================================

class TestChatUnchanged:
    def test_chat_endpoint_still_works(self, monkeypatch) -> None:
        """新增 RAG 路由不影响既有 /api/chat（回归保护）。

        Phase 3.5.6 起 /api/chat 经由 ChatService → RagService；
        此处用 Fake RagService 验证链路仍然打通。
        """
        from backend.app.api import chat as chat_module
        from backend.app.services.chat_service import ChatService

        class _FakeRag:
            def __init__(self) -> None:
                self.calls: list[str] = []

            async def answer(self, query: str, *, top_k: int | None = None):
                self.calls.append(query)
                return RagResponse(
                    answer="chat-via-rag",
                    sources=(),
                    used_chunks_count=0,
                )

        fake = _FakeRag()
        monkeypatch.setattr(
            chat_module, "_chat_service", ChatService(rag_service=fake)
        )
        with TestClient(app) as c:
            response = c.post("/api/chat", json={"message": "hi"})
        assert response.status_code == 200
        assert response.json()["answer"] == "chat-via-rag"
        assert fake.calls == ["hi"]


__all__ = [
    "TestNormalRequest",
    "TestTopK",
    "TestValidation",
    "TestEmptyKnowledgeBase",
    "TestErrorMapping",
    "TestResponseMapping",
    "TestChatUnchanged",
]
