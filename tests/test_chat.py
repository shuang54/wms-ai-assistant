"""Chat API 测试（Phase 3.5.6：Chat + RAG）。

通过 monkeypatch 替换 api.chat._chat_service，
验证：

    - 正常路径：200 + answer / sources / used_chunks_count
    - message 原样传递到 ChatService → RagService
    - 入参校验：空 / 缺失 / 非法类型 → 422；纯空白 → 400（服务层校验）
    - RAG 错误 → 相应 HTTP 状态码（不伪装成功、不泄露敏感信息）
    - sources 仅元数据（不含 chunk content）
    - OpenAPI 正常
    - /api/chat 只调用一次 RagService（单 LLM 链路）

全部 Mock ChatService / RagService，不发起真实 DB / Embedding / LLM 调用。
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from backend.app.api import chat as chat_module
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
from backend.app.services.chat_service import ChatService
from backend.app.services.rag_service import (
    DEFAULT_EMPTY_ANSWER,
    RagError,
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

class FakeChatService:
    """记录调用并可注入响应 / 异常的 ChatService 替身。

    行为与真实 ChatService 一致：空 / 纯空白 message 抛 ValueError，
    其余委托注入的响应或异常。
    """

    def __init__(
        self,
        *,
        response: RagResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        if response is None:
            response = RagResponse(
                answer="chat-rag-answer",
                sources=(
                    RagSource(
                        chunk_id=101,
                        document_id=12,
                        chunk_index=3,
                        content="chunk-内容（不应出现在响应中）",
                        similarity=0.95,
                        metadata={"heading_path": ["采购入库", "操作步骤"]},
                    ),
                ),
                used_chunks_count=1,
            )
        self._response = response
        self._error = error
        self.calls: list[str] = []

    async def chat(self, message: str) -> RagResponse:
        if not message or not message.strip():
            raise ValueError("message 不能为空")
        self.calls.append(message)
        if self._error is not None:
            raise self._error
        return self._response


@pytest.fixture()
def client(monkeypatch):
    """构造测试客户端；返回 context manager，yield (TestClient, FakeChatService)。"""

    @contextmanager
    def _make(
        *,
        response: RagResponse | None = None,
        error: Exception | None = None,
        raise_server_exceptions: bool = True,
    ):
        fake = FakeChatService(response=response, error=error)
        monkeypatch.setattr(chat_module, "_chat_service", fake)
        with TestClient(
            app, raise_server_exceptions=raise_server_exceptions
        ) as c:
            yield c, fake

    return _make


# ============================================================
# 1. 正常请求（协议兼容：answer 语义不变 + 新增 sources）
# ============================================================

class TestNormalRequest:
    def test_returns_200_with_answer(self, client) -> None:
        """POST /api/chat → 200 + answer（保持 Phase 2 协议字段）。"""
        with client() as (c, _fake):
            response = c.post("/api/chat", json={"message": "采购入库怎么操作？"})
        assert response.status_code == 200
        assert response.json()["answer"] == "chat-rag-answer"

    def test_returns_sources_and_used_chunks_count(self, client) -> None:
        """Phase 3.5.6 新增字段：sources / used_chunks_count（向后兼容）。"""
        with client() as (c, _fake):
            payload = c.post("/api/chat", json={"message": "q"}).json()
        assert payload["used_chunks_count"] == 1
        assert len(payload["sources"]) == 1
        src = payload["sources"][0]
        assert src["chunk_id"] == 101
        assert src["document_id"] == 12
        assert src["chunk_index"] == 3
        assert src["similarity"] == pytest.approx(0.95)
        assert src["metadata"] == {"heading_path": ["采购入库", "操作步骤"]}

    def test_sources_omit_chunk_content(self, client) -> None:
        """sources 仅元数据：不返回 chunk content（避免泄露大段知识库原文）。"""
        with client() as (c, _fake):
            payload = c.post("/api/chat", json={"message": "q"}).json()
        for src in payload["sources"]:
            assert set(src.keys()) == {
                "chunk_id",
                "document_id",
                "chunk_index",
                "similarity",
                "metadata",
            }

    def test_message_passed_to_service(self, client) -> None:
        with client() as (c, fake):
            c.post("/api/chat", json={"message": "  测试一下  "})
        assert fake.calls == ["  测试一下  "]

    def test_chat_called_exactly_once(self, client) -> None:
        """一次请求只调用一次 ChatService（单 LLM 链路）。"""
        with client() as (c, fake):
            c.post("/api/chat", json={"message": "q"})
        assert len(fake.calls) == 1

    def test_openapi_contains_chat_endpoint(self, client) -> None:
        with client() as (c, _fake):
            spec = c.get("/openapi.json").json()
        assert "/api/chat" in spec["paths"]
        post = spec["paths"]["/api/chat"]["post"]
        assert "200" in post["responses"]

    def test_empty_knowledge_base_returns_200(self, client) -> None:
        """空知识库 → 200 + 默认提示语 + sources=[]。"""
        empty = RagResponse(
            answer=DEFAULT_EMPTY_ANSWER,
            sources=(),
            used_chunks_count=0,
        )
        with client(response=empty) as (c, _fake):
            response = c.post("/api/chat", json={"message": "任何问题"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["answer"] == DEFAULT_EMPTY_ANSWER
        assert payload["sources"] == []
        assert payload["used_chunks_count"] == 0


# ============================================================
# 2. 入参校验
# ============================================================

class TestValidation:
    @pytest.mark.parametrize("message", ["", "   ", "\n\t "])
    def test_blank_message_rejected(self, client, message) -> None:
        """""（Pydantic）与纯空白（服务层）分别拒绝：422 / 400。"""
        with client() as (c, fake):
            response = c.post("/api/chat", json={"message": message})
        if message == "":
            assert response.status_code == 422
        else:
            # 纯空白通过 Pydantic min_length=1，由 ChatService 抛 ValueError → 400
            assert response.status_code == 400
            assert "不能为空" in response.json()["detail"]
        assert fake.calls == []

    def test_missing_message_rejected_422(self, client) -> None:
        with client() as (c, fake):
            response = c.post("/api/chat", json={})
        assert response.status_code == 422
        assert fake.calls == []

    def test_non_string_message_rejected_422(self, client) -> None:
        with client() as (c, fake):
            response = c.post("/api/chat", json={"message": 123})
        assert response.status_code == 422
        assert fake.calls == []


# ============================================================
# 3. RAG 异常 → HTTP 状态码映射（与 /api/rag/answer 一致）
# ============================================================

class TestRagErrorMapping:
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
            (RagError("prompt missing"), 500, "RAG 服务内部错误"),
            (LLMError("llm boom"), 500, "LLM 调用异常"),
        ],
    )
    def test_known_rag_errors_map_to_status(
        self, client, exc, expected_status, expected_fragment
    ) -> None:
        """已知 RAG 异常 → 明确状态码，不伪装成功。"""
        with client(error=exc) as (c, _fake):
            response = c.post("/api/chat", json={"message": "q"})
        assert response.status_code == expected_status
        detail = response.json()["detail"]
        assert expected_fragment in detail

    def test_unknown_error_returns_500_without_leaking_traceback(self, client) -> None:
        """未知异常 → 500，且响应不包含 traceback / 异常消息。"""
        with client(error=RuntimeError("internal boom"), raise_server_exceptions=False) as (c, _fake):
            response = c.post("/api/chat", json={"message": "q"})
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
        with client(error=VectorSearchInputError("检索失败")) as (c, _fake):
            response = c.post("/api/chat", json={"message": "q"})
        assert response.status_code == 400
        assert secret_fragment not in response.text


# ============================================================
# 4. 真实 ChatService（校验保留）经 FakeRagService 的接线测试
# ============================================================

class TestRealChatServiceWiring:
    def test_whitespace_message_maps_to_400_via_real_service(self, monkeypatch) -> None:
        """真实 ChatService（注入 Fake RagService）：纯空白 → ValueError → 400。"""
        from backend.app.services.vector_search_service import (
            VectorSearchInputError as _VSIE,
        )

        class _FakeRag:
            def __init__(self) -> None:
                self.calls: list[tuple[str, int | None]] = []

            async def answer(self, query: str, *, top_k: int | None = None):
                self.calls.append((query, top_k))
                raise _VSIE("query 为空或纯空白")

        fake = _FakeRag()
        monkeypatch.setattr(
            chat_module, "_chat_service", ChatService(rag_service=fake)
        )
        with TestClient(app) as c:
            response = c.post("/api/chat", json={"message": "   "})
        assert response.status_code == 400
        assert "不能为空" in response.json()["detail"]
        assert fake.calls == []  # ChatService 在委托前已拒绝

    def test_service_answer_flows_through_endpoint(self, monkeypatch) -> None:
        """真实 ChatService + Fake RagService：answer 正确映射为响应体。"""
        rag = RagResponse(
            answer="真实链路回答",
            sources=(
                RagSource(
                    chunk_id=7,
                    document_id=3,
                    chunk_index=1,
                    content="c",
                    similarity=0.5,
                    metadata={},
                ),
            ),
            used_chunks_count=1,
        )

        class _FakeRag:
            async def answer(self, query: str, *, top_k: int | None = None):
                return rag

        monkeypatch.setattr(
            chat_module, "_chat_service", ChatService(rag_service=_FakeRag())
        )
        with TestClient(app) as c:
            payload = c.post("/api/chat", json={"message": "q"}).json()
        assert payload["answer"] == "真实链路回答"
        assert payload["used_chunks_count"] == 1
        assert len(payload["sources"]) == 1


__all__ = [
    "FakeChatService",
    "TestNormalRequest",
    "TestValidation",
    "TestRagErrorMapping",
    "TestRealChatServiceWiring",
]
