"""ChatService 单元测试（Phase 3.5.6：Chat → RAG）。

使用 FakeRagService 注入，不依赖真实 DB / Embedding / LLM。

验证：
    - chat() 委托 RagService.answer（query 原样传递、不覆盖 top_k）
    - 输入校验（空 / 纯空白 → ValueError，且不调 RagService）
    - RagService 异常原样透传（不吞、不包装）
    - ChatService 不再持有 LLMClient（杜绝 RAG 之外的第二条 LLM 调用路径）
"""
from __future__ import annotations

import pytest

from backend.app.llm.client import LLMError
from backend.app.services.chat_service import ChatService
from backend.app.services.rag_service import (
    DEFAULT_EMPTY_ANSWER,
    RagResponse,
    RagSource,
)
from backend.app.services.vector_search_service import VectorSearchInputError


class FakeRagService:
    """用于单元测试的假 RagService。

    支持：
        - 配置固定 RagResponse
        - 配置抛出异常
        - 记录 answer() 调用参数（用于断言）
    """

    def __init__(
        self,
        response: RagResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        if response is None:
            response = RagResponse(
                answer="rag-fake-answer",
                sources=(
                    RagSource(
                        chunk_id=1,
                        document_id=10,
                        chunk_index=0,
                        content="chunk-内容",
                        similarity=0.9,
                        metadata={"heading_path": ["A"]},
                    ),
                ),
                used_chunks_count=1,
            )
        self._response = response
        self._error = error
        self.calls: list[tuple[str, int | None]] = []

    async def answer(self, query: str, *, top_k: int | None = None) -> RagResponse:
        self.calls.append((query, top_k))
        if self._error is not None:
            raise self._error
        return self._response


# ============================================================
# 委托 RagService
# ============================================================

async def test_chat_delegates_to_rag_service() -> None:
    """chat() 必须调用 rag_service.answer 并原样返回 RagResponse。"""
    fake = FakeRagService()
    service = ChatService(rag_service=fake)

    result = await service.chat("采购入库怎么操作？")

    assert result is fake._response
    assert fake.calls == [("采购入库怎么操作？", None)]


async def test_chat_does_not_override_default_top_k() -> None:
    """chat() 不传 top_k（None 透传，由 RagService 决定默认值）。"""
    fake = FakeRagService()
    service = ChatService(rag_service=fake)

    await service.chat("任意问题")

    assert fake.calls == [("任意问题", None)]


async def test_chat_returns_empty_result_as_is() -> None:
    """空知识库结果（canned answer）原样返回，不当错误处理。"""
    empty = RagResponse(
        answer=DEFAULT_EMPTY_ANSWER,
        sources=(),
        used_chunks_count=0,
    )
    fake = FakeRagService(response=empty)
    service = ChatService(rag_service=fake)

    result = await service.chat("任何问题")

    assert result.answer == DEFAULT_EMPTY_ANSWER
    assert result.sources == ()
    assert result.used_chunks_count == 0


# ============================================================
# 输入校验
# ============================================================

async def test_chat_rejects_empty_message() -> None:
    fake = FakeRagService()
    service = ChatService(rag_service=fake)
    with pytest.raises(ValueError):
        await service.chat("")
    assert fake.calls == []  # RagService 不应被调用


async def test_chat_rejects_whitespace_only_message() -> None:
    fake = FakeRagService()
    service = ChatService(rag_service=fake)
    with pytest.raises(ValueError):
        await service.chat("   \t\n  ")
    assert fake.calls == []


# ============================================================
# 异常透传（不吞、不包装）
# ============================================================

async def test_chat_propagates_vector_search_error_unchanged() -> None:
    """RagService 抛出的异常必须原样透传（不包装成别的类型）。"""
    fake = FakeRagService(error=VectorSearchInputError("query 为空"))
    service = ChatService(rag_service=fake)
    with pytest.raises(VectorSearchInputError, match="query 为空"):
        await service.chat("q")


async def test_chat_propagates_llm_error_unchanged() -> None:
    fake = FakeRagService(error=LLMError("llm down"))
    service = ChatService(rag_service=fake)
    with pytest.raises(LLMError, match="llm down"):
        await service.chat("q")


# ============================================================
# 单一 LLM 调用路径
# ============================================================

async def test_chat_service_has_no_direct_llm_client() -> None:
    """ChatService 不得持有 LLMClient——RAG 之外不允许第二条 LLM 调用路径。"""
    service = ChatService(rag_service=FakeRagService())
    assert not hasattr(service, "_llm_client")
    assert not hasattr(service, "_llm")
    # 委托对象本身即唯一下游
    assert hasattr(service, "_rag_service")


__all__ = [
    "FakeRagService",
]
