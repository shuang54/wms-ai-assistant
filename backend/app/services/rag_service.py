"""RAG Service（Phase 3.5.4：最小可用 RAG；Phase 3.7.14 接入 Reranker）。

Pipeline：
    validate query (non-empty / non-whitespace)
                ↓
VectorSearchService.search(query, top_k=top_k)
                ↓
VectorSearchResult[]  （按 similarity 降序）
        ↓
[Phase 3.7.14] RerankerClient.rerank(query, contents)
                ↓（仅 RERANKER_ENABLED=true 时；重排序并截断至 top_k）
        ↓
ContextBuilder.build(results)
                ↓
LLMClient.chat([system, user])
                ↓
RagResponse(answer, sources)

设计要点：

- 职责清晰：仅做检索 + 重排 + 拼装 + 调用 LLM；不实现多 Agent / 工具调用 / 对话管理。
- 不做 Function Calling、不实现 OpenAI / DeepSeek 协议；全部委托 LLMClient。
- 复用既有：
        * VectorSearchService         （Phase 3.5.3）
        * RerankerClient (抽象)       （Phase 3.5.12，BGERerankerClient 实现）
        * LLMClient (Protocol)       （Phase 2+）
        * EmbeddingClient            （通过 VectorSearchService 间接使用）
        * KnowledgeChunk / Database  （Phase 3.1+）
- Reranker 接入位置（Phase 3.7.14）：**Vector Search 之后、ContextBuilder 之前**。
  RERANKER_ENABLED=false（默认）时链路与 Phase 3.5.4 完全一致。
- 异常透传：Vector Search / Embedding / Reranker / LLM 任何失败，原样向上传播，
  **绝不**吞掉或返回"系统暂时正常"等掩盖真实错误的字符串。
- 空检索：不调 LLM 也不调 Reranker，直接返回"知识库中没有找到与该问题相关的信息" +
  sources=[]。理由：无 Context 时调用 LLM 极易产生幻觉。
- Prompt：
        * System prompt：从 `backend/app/prompts/rag_system.txt` 加载（外置）。
        * User prompt 模板：从 `backend/app/prompts/rag_user.txt` 加载，
          仅替换 `{context}` 与 `{question}`，不再泄露 database / embedding /
          distance / similarity 等内部字段。
- 日志：
        * 只记录 query_length / top_k / result_count / elapsed_ms /
          reranker_used / rerank_elapsed_ms
        * 不记录完整 prompt / 完整 answer / 任何 embedding vector / API Key。
- 安全：
        * API Key / Authorization Header 一律由 LLMClient / EmbeddingClient
          内部处理；本 Service 不读、不写、不打印。
        * Reranker 不执行数据库操作、不调用 LLM、不修改知识库数据。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from backend.app.config import settings
from backend.app.llm.client import get_default_llm_client
from backend.app.llm.provider import LLMProvider
from backend.app.projects.knowledge_provider import ProjectKnowledgeScope
from backend.app.reranker.client import (
    RerankerClient,
    get_default_reranker_client,
)
from backend.app.services.context_builder import ContextBuilder
from backend.app.services.vector_search_service import (
    VectorSearchResult,
    VectorSearchService,
)

logger = logging.getLogger(__name__)

__all__ = [
    "RagService",
    "RagResponse",
    "RagSource",
    "RagError",
]


# ============================================================
# 默认常量
# ============================================================

DEFAULT_EMPTY_ANSWER: Final[str] = "知识库中没有找到与该问题相关的信息。"

PROMPTS_DIR: Final[Path] = Path(__file__).resolve().parents[1] / "prompts"
SYSTEM_PROMPT_PATH: Final[Path] = PROMPTS_DIR / "rag_system.txt"
USER_PROMPT_PATH: Final[Path] = PROMPTS_DIR / "rag_user.txt"


# ============================================================
# 异常体系
# ============================================================

class RagError(Exception):
    """RAG Service 通用异常基类。

    正常 RAG 流程中的错误（Vector Search / LLM / Embedding）原样透传，
    调用方按原类型区分；本类承载 RAG 自身的、不可恢复的错误
    （如 Prompt 模板缺失、占位符未填齐等）。
    """


# ============================================================
# 结果结构（DTO）
# ============================================================

@dataclass(frozen=True)
class RagSource:
    """RAG 命中来源（DTO，不暴露 ORM Model）。

    Attributes:
        chunk_id:     KnowledgeChunk 主键。
        document_id:  所属文档主键。
        chunk_index:  Chunk 在原文中的顺序。
        content:      Chunk 文本内容。
        similarity:   cosine similarity（与 distance 互为 1 - x；保留 similarity 便于前端展示）。
        metadata:     KnowledgeChunk meta_data 字典（None 时为空 dict）。
    """

    chunk_id: int
    document_id: int
    chunk_index: int
    content: str
    similarity: float
    metadata: dict


@dataclass(frozen=True)
class RagResponse:
    """RAG 回答结果（DTO）。

    Attributes:
        answer:  LLM 生成的最终回答；空检索时为内置常量 DEFAULT_EMPTY_ANSWER。
        sources: 实际纳入回答的来源（与喂给 LLM 的 Context 一致）；
                 空检索时为 ()。
        used_chunks_count: 实际纳入 Context 的片段数（≤ top_k，
                           因 Context 长度限制可能进一步裁剪）。
    """

    answer: str
    sources: tuple[RagSource, ...]
    used_chunks_count: int


# ============================================================
# Service
# ============================================================

class RagService:
    """RAG Service（Phase 3.5.4；Phase 3.7.14 接入 Reranker）。

    依赖：
        - VectorSearchService（Phase 3.5.3）—— 默认通过懒加载构造
        - RerankerClient（Phase 3.5.12 抽象）—— 仅 RERANKER_ENABLED=true 时参与；
          默认通过 get_default_reranker_client() 懒加载（进程内单例）
        - LLMProvider（Phase 3.10.1 抽象；Phase 2+ 语义不变）
          —— 默认通过 get_default_llm_client() 懒加载
        - ContextBuilder（Phase 3.5.4 本模块）—— 默认 max_context_chars 来自 settings.rag

    可注入项（用于测试）：
        - vector_search_service: None 时懒加载默认
        - reranker_client:       Reranker 抽象（RerankerClient 子类 / Fake）；
                                 **仅当 settings.reranker.enabled=true 时参与链路**
                                 （配置开关是链路形态的唯一权威）
        - llm_client: None 时懒加载默认
        - context_builder: None 时按 max_context_chars 构造
        - system_prompt: None 时从文件加载
        - user_prompt_template: None 时从文件加载
        - empty_answer_text: 默认 DEFAULT_EMPTY_ANSWER
    """

    def __init__(
        self,
        *,
        vector_search_service: VectorSearchService | None = None,
        reranker_client: RerankerClient | None = None,
        llm_client: LLMProvider | None = None,
        context_builder: ContextBuilder | None = None,
        system_prompt: str | None = None,
        user_prompt_template: str | None = None,
        empty_answer_text: str = DEFAULT_EMPTY_ANSWER,
    ) -> None:
        self._vector_search_service = vector_search_service
        self._reranker_client = reranker_client
        self._llm_client = llm_client
        self._context_builder = context_builder
        self._system_prompt = system_prompt
        self._user_prompt_template = user_prompt_template
        self._empty_answer_text = empty_answer_text

    # ---------- 依赖解析（懒加载） ----------

    def _get_vector_search_service(self) -> VectorSearchService:
        if self._vector_search_service is None:
            # VectorSearchService 内部已懒加载 EmbeddingClient / SessionFactory
            self._vector_search_service = VectorSearchService()
        return self._vector_search_service

    def _get_reranker(self) -> RerankerClient | None:
        """解析 Reranker（Phase 3.7.14）。

        返回 None 的两种情况：
            1. settings.reranker.enabled=False（默认）——链路保持原样
            2. （不会发生）enabled=True 且注入了 client / 默认单例

        配置开关是链路形态的唯一权威：即使注入了 reranker_client，
        enabled=False 时也不会参与链路（保证"关闭=完全旁路"的可测试性）。
        """
        if not settings.reranker.enabled:
            return None
        if self._reranker_client is not None:
            return self._reranker_client
        # 进程内单例（BGERerankerClient 内部线程锁保证只加载一次模型）
        return get_default_reranker_client()

    def _get_llm_client(self) -> LLMProvider:
        if self._llm_client is None:
            self._llm_client = get_default_llm_client()
        return self._llm_client

    def _get_context_builder(self) -> ContextBuilder:
        if self._context_builder is None:
            self._context_builder = ContextBuilder(
                max_context_chars=settings.rag.max_context_chars
            )
        return self._context_builder

    def _get_system_prompt(self) -> str:
        if self._system_prompt is None:
            self._system_prompt = _load_prompt_file(SYSTEM_PROMPT_PATH)
        return self._system_prompt

    def _get_user_prompt_template(self) -> str:
        if self._user_prompt_template is None:
            self._user_prompt_template = _load_prompt_file(USER_PROMPT_PATH)
        return self._user_prompt_template

    # ---------- 主流程 ----------

    async def answer(
        self,
        query: str,
        *,
        top_k: int | None = None,
        knowledge_scope: ProjectKnowledgeScope | None = None,
    ) -> RagResponse:
        """对 query 执行 RAG，返回 RagResponse。

        Pipeline:
            1. Vector Search（top_k 默认 settings.rag.default_top_k；
               knowledge_scope 非 None 时检索被显式限制在该项目
               知识范围内，Phase 3.8.4）
            2. [Phase 3.7.14] Reranker（仅 enabled=true：candidate_k 召回 →
               重排序 → 截断至 top_k；默认 top_k 取 settings.reranker.top_k）
            3. 空结果 → 直接返回 canned answer（**不调 LLM / 不调 Reranker**）
            4. Context Builder → 拼装结构化 Context
            5. LLM.chat([system, user]) → 生成回答
            6. 映射 VectorSearchResult[] → RagSource[]
            7. 返回 RagResponse

        Args:
            query:  用户问题（任意由调用方传入；VectorSearchService 会校验非空）。
            top_k:  **最终**纳入回答的片段数；
                    None 时：Reranker 关闭 → settings.rag.default_top_k；
                             Reranker 开启 → settings.reranker.top_k。
            knowledge_scope: Phase 3.8.4 —— 项目知识检索范围（frozen DTO），
                    由上层（Factory → Orchestrator）通过服务器端
                    Provider 解析后传入；本 Service **不读取**
                    ProjectRegistry（依赖倒置）。None = 旧行为
                    （不带 scope 的全局检索，历史端点兼容）。

        Returns:
            RagResponse(answer, sources, used_chunks_count)。

        Raises:
            VectorSearchInputError / VectorSearchParameterError:  query / top_k 非法
            EmbeddingError 家族 / VectorSearchError / LLMError 家族:
                任一阶段错误**原样透传**，不掩盖。
            RerankerError 家族（RerankerInputError / RerankerModelError /
            RerankerConfigurationError）: Reranker 阶段错误**原样透传**，不掩盖、
            不降级为未排序结果。
            RuntimeError: 必要依赖未配置（如 DATABASE_URL 空）。
            RagError:      Prompt 模板读取失败 / 缺失占位符 /
                           Reranker 返回分数数量与候选数不一致。
        """
        started = time.perf_counter()
        reranker = self._get_reranker()

        if top_k is None:
            top_k = (
                settings.reranker.top_k
                if reranker is not None
                else settings.rag.default_top_k
            )

        # ---- 1. Vector Search（Reranker 开启时扩大召回窗口）----
        # Phase 3.8.4：knowledge_scope 非 None 时显式传递（项目内检索）；
        # None → 旧调用形态（兼容既有 Fake / Mock 的
        # search(query, *, top_k) 签名与历史端点全局检索行为）。
        vector_search_service = self._get_vector_search_service()
        if reranker is not None:
            # 召回条数：max(top_k, candidate_top_k)（钳制到 [1, 50]，
            # 与 VectorSearchService.MAX_TOP_K 一致，防止异常配置）
            candidate_k = max(
                top_k,
                settings.reranker.candidate_top_k,
            )
            if knowledge_scope is None:
                results = await vector_search_service.search(
                    query, top_k=candidate_k
                )
            else:
                results = await vector_search_service.search(
                    query, top_k=candidate_k, knowledge_scope=knowledge_scope
                )
        else:
            if knowledge_scope is None:
                results = await vector_search_service.search(
                    query, top_k=top_k
                )
            else:
                results = await vector_search_service.search(
                    query, top_k=top_k, knowledge_scope=knowledge_scope
                )

        # ---- 2. 空检索 → 不调 Reranker、不调 LLM ----
        if not results:
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.info(
                "RAG empty result",
                extra={
                    "query_length": len(query or ""),
                    "top_k": top_k,
                    "result_count": 0,
                    "reranker_used": reranker is not None,
                    "elapsed_ms": elapsed_ms,
                },
            )
            return RagResponse(
                answer=self._empty_answer_text,
                sources=(),
                used_chunks_count=0,
            )

        # ---- 3. Reranker（仅 enabled=true；位于 Vector Search 之后、Context 之前）----
        rerank_elapsed_ms: float | None = None
        if reranker is not None:
            rerank_started = time.perf_counter()
            results = await _rerank_chunks(
                reranker, query, results, top_k=top_k
            )
            rerank_elapsed_ms = (time.perf_counter() - rerank_started) * 1000

        # ---- 4. Context ----
        context_builder = self._get_context_builder()
        context_result = context_builder.build(results)

        # ---- 5. LLM ----
        system_prompt = self._get_system_prompt()
        user_prompt_template = self._get_user_prompt_template()
        user_prompt = _format_user_prompt(user_prompt_template, context_result.text, query)

        llm_client = self._get_llm_client()
        answer = await llm_client.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        # ---- 6. 映射 sources（与 Context 一致：可能少于 results，因 Context 截断） ----
        sources = tuple(
            RagSource(
                chunk_id=r.chunk_id,
                document_id=r.document_id,
                chunk_index=r.chunk_index,
                content=r.content,
                similarity=r.similarity,
                metadata=dict(r.metadata) if r.metadata else {},
            )
            for r in context_result.used_chunks
        )

        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "RAG answered",
            extra={
                "query_length": len(query),
                "top_k": top_k,
                "result_count": len(results),
                "used_chunks_count": len(sources),
                "context_truncated": context_result.truncated,
                "context_chars": context_result.total_chars,
                "reranker_used": reranker is not None,
                "rerank_elapsed_ms": rerank_elapsed_ms,
                "elapsed_ms": elapsed_ms,
            },
        )

        return RagResponse(
            answer=answer,
            sources=sources,
            used_chunks_count=len(sources),
        )


# ============================================================
# Helpers（私有）
# ============================================================

async def _rerank_chunks(
    reranker: RerankerClient,
    query: str,
    results: list[VectorSearchResult],
    *,
    top_k: int,
) -> list[VectorSearchResult]:
    """对 Vector Search 候选执行 Rerank 并截断至 top_k（Phase 3.7.14）。

    位置：Vector Search **之后**、ContextBuilder **之前**。

    行为：
        1. 空候选 → 直接返回 []（不调 Reranker，由调用方短路，此处防御）
        2. reranker.rerank(query, [r.content ...]) → scores
           （RerankerError 家族**原样透传**，绝不吞掉 / 降级为未排序结果）
        3. 分数数量与候选数不一致 → RagError（防御 Fake / 自定义实现）
        4. 按 score 降序稳定排序（同分保持 Vector Search 原顺序）
        5. 截断至 top_k

    不做的事情：
        - 不修改 VectorSearchResult 内容（similarity 等字段保持原值）
        - 不执行数据库操作、不调用 LLM、不修改知识库数据
    """
    if not results:
        return list(results)

    scores = await reranker.rerank(query, [r.content for r in results])

    if len(scores) != len(results):
        raise RagError(
            f"Reranker 返回 score 数量与候选数不一致"
            f"（{len(scores)} vs {len(results)}）；已阻止未排序结果进入 Context。"
        )

    # 稳定排序：score 降序；同分保持 Vector Search 原顺序
    order = sorted(
        range(len(results)),
        key=lambda i: -float(scores[i]),
    )
    reranked = [results[i] for i in order[:top_k]]

    logger.info(
        "RAG rerank applied",
        extra={
            "candidate_count": len(results),
            "kept_count": len(reranked),
            "top_k": top_k,
        },
    )
    return reranked


def _load_prompt_file(path: Path) -> str:
    """读取 Prompt 模板文件；缺失抛 RagError。"""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RagError(f"RAG Prompt 模板缺失: {path}") from exc


def _format_user_prompt(template: str, context: str, question: str) -> str:
    """填入 {context} / {question} 占位符；缺失占位符抛 RagError。"""
    try:
        return template.format(context=context, question=question)
    except KeyError as exc:
        raise RagError(f"RAG User Prompt 模板包含未识别占位符: {exc.args}") from exc