"""Knowledge Ingestion Service（Phase 3.5.2）。

把已完成的模块连接为单文件导入流水线：

    file_path
        ↓ ParserFactory.get_parser()          （Phase 3.3）
    document_text
        ↓ TextChunker.chunk()                  （Phase 3.4）
    TextChunk[]
        ↓ EmbeddingClient.embed()（逐 Chunk）   （Phase 3.5.1，BAAI/bge-m3，1024 维）
    1024 维向量
        ↓ 单事务写入                           （Phase 3.2 ORM）
    KnowledgeDocument + KnowledgeChunk[]
        ↓ COMMIT
    PostgreSQL + pgvector vector(1024)

设计要点：

- 事务策略：**先完成全部 Embedding，再开启单个写事务**。
  - Embedding 失败 → 数据库零写入；
  - 写库中途失败 → `session.begin()` 自动回滚；
  - 两种情况都不会产生"有 Document 无 Chunk / 有 Chunk 无 Embedding"的半成品。
- 重复检测（Document content_hash）在 Embedding 之前执行：
  重复文档不再调用 Embedding API，避免浪费 API 成本。
- 维度校验双保险：EmbeddingClient 内部已校验；Service 在写库前再校验一次
  （防御测试 Mock / 自定义实现绕过），不一致抛 `EmbeddingDimensionError`，
  不截断、不补零、不自动转换。
- token_count：当前 Chunker 为 character-based 且项目无可靠 tokenizer，
  按约定写 NULL（不用 len(content) 冒充 token 数）。
- status 遵循 `knowledge_document` 现有约定（pending / processing / ready / failed）：
  成功 = "ready"；重复 = "already_exists"（仅结果状态，不修改库）。

异常约定（可区分）：

- Parser 阶段：`DocumentNotFoundError` / `UnsupportedDocumentTypeError` /
  `DocumentParseError`（原样透传）
- 空文档：`EmptyDocumentError`（不调 Embedding、不写库）
- Embedding 阶段：`EmbeddingAPIError` / `EmbeddingDimensionError` 等（原样透传）
- 写库阶段：`KnowledgeIngestionDatabaseError`（已回滚）

日志只记录 file_name / chunk_count / duration / status，
**绝不**记录 API Key、Authorization header 或完整 embedding 向量。

本阶段**不做**（后续 Phase）：

- ingest_directory / 批量 / 队列 / 后台任务
- Vector Search / RAG / Tool Calling / Agent
- REST API（POST /api/knowledge/ingest）
"""
from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.db.models import KnowledgeChunk, KnowledgeDocument
from backend.app.db.session import get_session_factory
from backend.app.embedding.client import EmbeddingClient, get_default_embedding_client
from backend.app.embedding.exceptions import EmbeddingDimensionError
from backend.app.rag.chunking.text_chunker import TextChunker, default_chunker

logger = logging.getLogger(__name__)

__all__ = [
    "IngestionResult",
    "KnowledgeIngestionError",
    "EmptyDocumentError",
    "KnowledgeIngestionDatabaseError",
    "KnowledgeIngestionService",
]


# ============================================================
# 异常体系
# ============================================================

class KnowledgeIngestionError(Exception):
    """Knowledge Ingestion 通用异常基类。

    Parser / Embedding 阶段的原始类型异常（DocumentParseError /
    EmbeddingError 家族）由 Service 原样透传，可直接按原类型区分；
    本类仅承载 Ingestion 自身的错误（空文档 / 数据库写入失败）。
    """


class EmptyDocumentError(KnowledgeIngestionError):
    """文档内容为空 / 纯空白：拒绝导入，不调用 Embedding、不写库。"""


class KnowledgeIngestionDatabaseError(KnowledgeIngestionError):
    """数据库写入失败（事务已回滚，无半成品残留）。"""


# ============================================================
# 结果结构
# ============================================================

@dataclass(frozen=True)
class IngestionResult:
    """单文档导入结果。

    字段：
        document_id:          KnowledgeDocument 主键（重复时为已存在文档的主键）
        file_name:            原始文件名
        status:               "ready"（成功落库）/ "already_exists"（重复，未重复导入）
        chunk_count:          Chunk 总数（重复时为已存在文档的 Chunk 数）
        embedded_chunk_count: 本次实际调用 Embedding 生成的向量数（重复时为 0）
        content_hash:         文档内容 SHA-256（hex）
    """

    document_id: int
    file_name: str
    status: str
    chunk_count: int
    embedded_chunk_count: int
    content_hash: str


def _sha256_hex(content: str) -> str:
    """计算文本的 SHA-256 hex 摘要。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ============================================================
# Service
# ============================================================

class KnowledgeIngestionService:
    """单文件知识导入 Service。

    协调 Parser → Chunker → EmbeddingClient → SQLAlchemy Session，
    业务逻辑收敛在 Service 层，不塞进 Parser / Chunker / EmbeddingClient。
    """

    def __init__(
        self,
        *,
        embedding_client: EmbeddingClient | None = None,
        chunker: TextChunker | None = None,
        session_factory: Callable[[], Session] | None = None,
    ) -> None:
        """
        Args:
            embedding_client: Embedding 客户端；None 时懒加载默认单例
                （首次 ingest 时构造，配置缺失抛 EmbeddingConfigurationError）。
            chunker: Chunking 策略；None 时用 default_chunker()。
            session_factory: Session 工厂；None 时用全局
                get_session_factory()（DATABASE_URL 为空则 ingest 时抛
                KnowledgeIngestionDatabaseError）。测试可注入 Mock。
        """
        self._embedding_client = embedding_client
        self._chunker = chunker if chunker is not None else default_chunker()
        self._session_factory = session_factory

    # ---------- 依赖解析（懒加载） ----------

    def _get_embedding_client(self) -> EmbeddingClient:
        if self._embedding_client is None:
            self._embedding_client = get_default_embedding_client()
        return self._embedding_client

    def _get_session_factory(self) -> Callable[[], Session]:
        if self._session_factory is not None:
            return self._session_factory
        factory = get_session_factory()
        if factory is None:
            raise KnowledgeIngestionDatabaseError(
                "DATABASE_URL 未配置，无法执行知识入库"
            )
        return factory

    # ---------- 重复检测 ----------

    def _find_existing_document(
        self, content_hash: str
    ) -> tuple[int, int] | None:
        """按 content_hash 查找已存在文档。

        Returns:
            (document_id, chunk_count)：已存在时返回；否则 None。
        """
        factory = self._get_session_factory()
        with factory() as session:
            existing = session.scalars(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.content_hash == content_hash
                )
            ).first()
            if existing is None:
                return None
            # lazy="selectin"：chunks 在 session 存活期间加载
            return existing.id, len(existing.chunks)

    # ---------- 主流程 ----------

    async def ingest_one(self, file_path: str | Path) -> IngestionResult:
        """导入单个知识文档（TXT / Markdown）。

        流程：parse → 空检查 → content_hash → 重复检测（跳过 Embedding）
        → chunk → embed（逐个，维度校验）→ 单事务写库 → commit。

        Raises:
            UnsupportedDocumentTypeError: 扩展名不受支持（.pdf / .docx 等）。
            DocumentNotFoundError: 文件不存在。
            DocumentParseError: 编码 / IO 等解析错误。
            EmptyDocumentError: 文档内容为空 / 纯空白。
            EmbeddingConfigurationError / EmbeddingAPIError /
                EmbeddingResponseError / EmbeddingDimensionError: 透传。
            KnowledgeIngestionDatabaseError: 写库失败（已回滚）。
        """
        started = time.perf_counter()
        path = Path(file_path)
        file_name = path.name

        # ---- 1. Parser（异常在此抛出，未触碰 DB） ----
        from backend.app.rag.parsers.factory import get_parser

        parser = get_parser(path)
        document_text = parser.parse(path)

        # ---- 2. 空文档：拒绝，不调用 Embedding、不写库 ----
        if not document_text.strip():
            raise EmptyDocumentError(
                f"文档内容为空或纯空白，已拒绝导入: {file_name}"
            )

        # ---- 3. content_hash（SHA-256，用于重复检测） ----
        content_hash = _sha256_hex(document_text)

        # ---- 4. 重复检测（在 Embedding 之前，避免重复调用 API） ----
        existing = self._find_existing_document(content_hash)
        if existing is not None:
            existing_id, existing_chunk_count = existing
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.info(
                "Knowledge ingestion skipped (duplicate document)",
                extra={
                    "file_name": file_name,
                    "document_id": existing_id,
                    "chunk_count": existing_chunk_count,
                    "status": "already_exists",
                    "elapsed_ms": elapsed_ms,
                },
            )
            return IngestionResult(
                document_id=existing_id,
                file_name=file_name,
                status="already_exists",
                chunk_count=existing_chunk_count,
                embedded_chunk_count=0,
                content_hash=content_hash,
            )

        # ---- 5. Chunking ----
        chunks = self._chunker.chunk(document_text)
        if not chunks:
            # 防御：非空文本理论上至少产生 1 个 Chunk
            raise EmptyDocumentError(
                f"文档切分结果为空，已拒绝导入: {file_name}"
            )

        # ---- 6. Embedding（逐个；先全部完成，再开写事务） ----
        embedding_client = self._get_embedding_client()
        dimension = settings.embedding.dimension
        vectors: list[list[float]] = []
        for chunk in chunks:
            vector = await embedding_client.embed(chunk.content)
            # 双保险：Mock / 自定义 Client 可能绕过内部校验
            if len(vector) != dimension:
                raise EmbeddingDimensionError(
                    f"Embedding 向量维度不匹配：得到 {len(vector)} 维，"
                    f"EMBEDDING_DIMENSION 配置为 {dimension} 维；"
                    "已阻止数据库写入（不截断、不补零）。"
                )
            vectors.append(vector)

        # ---- 7. 单事务写库（失败整体回滚） ----
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                doc = KnowledgeDocument(
                    title=path.stem or file_name,
                    file_name=file_name,
                    file_type=parser.file_type,
                    source="local",
                    content_hash=content_hash,
                    status="ready",
                    meta_data={"source_type": parser.file_type},
                )
                session.add(doc)
                session.flush()

                for chunk, vector in zip(chunks, vectors):
                    session.add(
                        KnowledgeChunk(
                            document_id=doc.id,
                            chunk_index=chunk.chunk_index,
                            content=chunk.content,
                            content_hash=_sha256_hex(chunk.content),
                            token_count=None,  # 无可靠 tokenizer，不冒充
                            embedding=vector,
                            meta_data=dict(chunk.metadata),
                        )
                    )
        except SQLAlchemyError as exc:
            raise KnowledgeIngestionDatabaseError(
                f"知识文档入库失败（事务已回滚）: {file_name}: {exc}"
            ) from exc

        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "Knowledge ingestion completed",
            extra={
                "file_name": file_name,
                "document_id": doc.id,
                "chunk_count": len(chunks),
                "embedding_count": len(vectors),
                "status": "ready",
                "elapsed_ms": elapsed_ms,
            },
        )
        return IngestionResult(
            document_id=doc.id,
            file_name=file_name,
            status="ready",
            chunk_count=len(chunks),
            embedded_chunk_count=len(vectors),
            content_hash=content_hash,
        )
