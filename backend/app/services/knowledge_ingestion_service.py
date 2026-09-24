"""Knowledge Ingestion Service（Phase 3.5.2；Phase 3.8.5 扩展 Project Context）。

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

Phase 3.8.5 —— Project Knowledge Ingestion Context：

    project_id（服务端调用上下文，非 HTTP 可注入）
        ↓ ProjectKnowledgeProvider.get_scope()（Phase 3.8.4 复用，DI 注入）
    ProjectKnowledgeScope.namespace
        ↓ 强制写入
    knowledge_document.meta_data.project_id
        ↓ （检索侧由 Phase 3.8.4 VectorSearch scope 过滤消费）
    Project-scoped Retrieval

写入规则：

- **只写 scope.namespace**；`includes_global` / `includes_legacy` 是检索
  策略，绝不写入 metadata（任务书 §四）；
- 调用方 metadata 中的 `project_id` 一律剥离后由服务器端 Provider 结果
  **强制覆盖**（任务书 §五：不信任调用方 metadata）；
- `project_id=None`（旧调用形态）保持旧行为：meta_data 无 project_id
  → 按 Phase 3.8.4 规则属于 legacy（仅 vietnam-wms 可检索）；
  **绝不**自动伪装成 `__global__` / `vietnam-wms`（任务书 §六）；
- 文档 `content_hash` 参与项目身份：namespace 有值时
  `sha256(namespace + "\\0" + content)`，None 时保持纯内容 SHA-256
  （旧行完全兼容；满足 content_hash UNIQUE 约束的同时实现
  per-project 去重——project-a 与 project-b 的相同内容是两份独立知识，
  任务书 §十）。chunk 级 hash 不变。

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

- 项目 scope 解析：`KnowledgeIngestionProjectScopeError`（Phase 3.8.5；
  Provider 未注册 / 输入非法，**绝不 fallback 到其他项目 / global**）
- Parser 阶段：`DocumentNotFoundError` / `UnsupportedDocumentTypeError` /
  `DocumentParseError`（原样透传）
- 空文档：`EmptyDocumentError`（不调 Embedding、不写库）
- Embedding 阶段：`EmbeddingAPIError` / `EmbeddingDimensionError` 等（原样透传）
- 写库阶段：`KnowledgeIngestionDatabaseError`（已回滚）

日志只记录 file_name / chunk_count / duration / status / namespace，
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
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.db.models import KnowledgeChunk, KnowledgeDocument
from backend.app.db.session import get_session_factory
from backend.app.embedding.client import EmbeddingClient, get_default_embedding_client
from backend.app.embedding.exceptions import EmbeddingDimensionError
from backend.app.projects.knowledge_provider import (
    ProjectKnowledgeProvider,
    ProjectKnowledgeProviderError,
    get_default_project_knowledge_provider,
)
from backend.app.rag.chunking.text_chunker import TextChunker, default_chunker

logger = logging.getLogger(__name__)

__all__ = [
    "IngestionResult",
    "KnowledgeDocumentInfo",
    "KnowledgeDocumentNotFoundError",
    "KnowledgeIngestionError",
    "EmptyDocumentError",
    "KnowledgeIngestionDatabaseError",
    "KnowledgeIngestionProjectScopeError",
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


class KnowledgeIngestionProjectScopeError(KnowledgeIngestionError):
    """项目知识 scope 解析失败（Phase 3.8.5）。

    Provider 未注册该 project_id / 输入非法时抛出；
    **绝不** fallback 到其他项目 / vietnam-wms / __global__。
    """


class KnowledgeDocumentNotFoundError(KnowledgeIngestionError):
    """按 document_id 未找到对应知识文档（update / delete / get 调用时使用）。"""


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


def _document_content_hash(content: str, namespace: str | None) -> str:
    """文档级 content_hash（Phase 3.8.5：参与项目身份判断）。

    - ``namespace=None``（旧调用形态）→ 纯内容 SHA-256，
      与历史存量行、既有测试**逐字节兼容**；
    - ``namespace="project-a"`` → ``sha256(b"project-a\\0" + content)``：
      满足 ``content_hash`` UNIQUE 约束的同时实现 per-project 去重
      （project-a 与 project-b 的相同内容是两份独立知识）。

    注意：chunk 级 hash 仍用 ``_sha256_hex``（chunk 身份与项目无关）。
    """
    if namespace is None:
        return _sha256_hex(content)
    h = hashlib.sha256()
    h.update(namespace.encode("utf-8"))
    h.update(b"\x00")
    h.update(content.encode("utf-8"))
    return h.hexdigest()


@dataclass(frozen=True)
class KnowledgeDocumentInfo:
    """知识文档只读摘要 DTO（frozen dataclass，Phase 3.5.10）。

    字段：
        id, title, file_name, file_type, source, status,
        created_at, updated_at, chunk_count

    明确**不**暴露：embedding 向量、数据库连接、API Key、完整 chunk content。
    """

    id: int
    title: str
    file_name: str | None
    file_type: str | None
    source: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    chunk_count: int


def _to_document_info(doc: KnowledgeDocument) -> "KnowledgeDocumentInfo":
    """ORM 行 → 只读 DTO（不暴露 ORM 对象）。"""
    return KnowledgeDocumentInfo(
        id=doc.id,
        title=doc.title,
        file_name=doc.file_name,
        file_type=doc.file_type,
        source=doc.source,
        status=doc.status,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        chunk_count=len(doc.chunks or []),
    )


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
        knowledge_provider: ProjectKnowledgeProvider | None = None,
    ) -> None:
        """
        Args:
            embedding_client: Embedding 客户端；None 时懒加载默认单例
                （首次 ingest 时构造，配置缺失抛 EmbeddingConfigurationError）。
            chunker: Chunking 策略；None 时用 default_chunker()。
            session_factory: Session 工厂；None 时用全局
                get_session_factory()（DATABASE_URL 为空则 ingest 时抛
                KnowledgeIngestionDatabaseError）。测试可注入 Mock。
            knowledge_provider: Phase 3.8.5 —— project_id → KnowledgeScope
                的服务器端解析器（复用 Phase 3.8.4 协议）；None 时懒加载
                默认 Provider。仅在 ``ingest_one(project_id=...)`` 有值时
                被调用（旧调用形态 project_id=None 不触碰 Provider）。
        """
        self._embedding_client = embedding_client
        self._chunker = chunker if chunker is not None else default_chunker()
        self._session_factory = session_factory
        self._knowledge_provider = knowledge_provider

    # ---------- 依赖解析（懒加载） ----------

    def _get_embedding_client(self) -> EmbeddingClient:
        if self._embedding_client is None:
            self._embedding_client = get_default_embedding_client()
        return self._embedding_client

    def _get_knowledge_provider(self) -> ProjectKnowledgeProvider:
        """解析 Knowledge Provider（惰性；测试可注入 InMemory/Fake）。"""
        if self._knowledge_provider is None:
            self._knowledge_provider = get_default_project_knowledge_provider()
        return self._knowledge_provider

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

    # ---------- Project Knowledge Scope（Phase 3.8.5） ----------

    def _resolve_knowledge_namespace(self, project_id: str | None) -> str | None:
        """project_id → scope.namespace（服务器端解析；None → None）。

        Raises:
            KnowledgeIngestionProjectScopeError: Provider 未注册该 project_id
                或输入非法（**绝不 fallback** 到其他项目 / global / legacy）。
        """
        if project_id is None:
            return None
        provider = self._get_knowledge_provider()
        try:
            scope = provider.get_scope(project_id)
        except ProjectKnowledgeProviderError as exc:
            raise KnowledgeIngestionProjectScopeError(
                f"项目 {project_id!r} 的知识 scope 解析失败"
                f"（不会回退到其他项目 / global / legacy）: {exc}"
            ) from exc
        # 只取 namespace；includes_global / includes_legacy 是检索策略，
        # 不写入 metadata（任务书 §四）
        return scope.namespace

    @staticmethod
    def _build_document_metadata(
        metadata: dict | None,
        namespace: str | None,
        source_type: str | None,
    ) -> dict:
        """合并文档 metadata（Phase 3.8.5，任务书 §五 / §八）。

        规则：

        - ``metadata=None`` → 空 dict 起步；非 dict → clear error
          （现有数据契约：此前调用方根本没有 metadata 入参）；
        - 调用方 metadata 中的 ``project_id`` 一律**先剥离**——
          该键只能来自服务器端 Provider 解析，调用方无法注入归属；
        - ``namespace`` 非 None → 强制写 ``project_id=namespace``
          （覆盖一切调用方值）；None → 不写该键（legacy 旧行为，
          绝不伪装成 __global__ / vietnam-wms）；
        - ``source_type`` 保持既有行为（parser 权威）。
        """
        if metadata is None:
            meta: dict = {}
        elif isinstance(metadata, dict):
            meta = dict(metadata)
        else:
            raise KnowledgeIngestionError(
                "metadata 必须是 dict 或 None"
                f"（当前: {type(metadata).__name__}）"
            )
        # project_id 只能来自服务器端 Provider；调用方传入的一律剥离
        meta.pop("project_id", None)
        if namespace is not None:
            meta["project_id"] = namespace
        meta["source_type"] = source_type
        return meta

    # ---------- 主流程 ----------

    async def ingest_one(
        self,
        file_path: str | Path,
        *,
        project_id: str | None = None,
        metadata: dict | None = None,
    ) -> IngestionResult:
        """导入单个知识文档（TXT / Markdown）。

        流程：输入校验 → scope 解析（Phase 3.8.5）→ parse → 空检查 →
        content_hash（namespace 参与，Phase 3.8.5）→ 重复检测（跳过
        Embedding）→ chunk → embed（逐个，维度校验）→ 单事务写库 → commit。

        Args:
            file_path: 文档路径（.md / .txt）。
            project_id: Phase 3.8.5 —— 服务端调用上下文的项目 ID。
                有值时通过注入的 ``ProjectKnowledgeProvider`` 解析
                namespace，并**强制**写入 ``meta_data.project_id``
                （调用方 metadata 中的同名键被剥离 / 覆盖）；
                ``None`` = 旧调用形态（旧行为完全不变：meta_data 不含
                project_id，属于 legacy，仅 vietnam-wms 可检索）。
            metadata: Phase 3.8.5 —— 调用方业务元数据（可选）；与
                ``source_type`` 合并；其中 ``project_id`` 一律被服务器端
                解析结果覆盖（不可注入归属）。

        Raises:
            KnowledgeIngestionProjectScopeError: project_id 的知识 scope
                未注册 / 非法（不 fallback 到其他项目）。
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

        # ---- 0. Phase 3.8.5：输入校验 + 项目 scope 解析（未触碰 DB） ----
        # metadata 类型校验（clear error，早失败）
        self._build_document_metadata(metadata, None, None)
        namespace = self._resolve_knowledge_namespace(project_id)

        # ---- 1. Parser（异常在此抛出，未触碰 DB） ----
        from backend.app.rag.parsers.factory import get_parser

        parser = get_parser(path)
        document_text = parser.parse(path)

        # ---- 2. 空文档：拒绝，不调用 Embedding、不写库 ----
        if not document_text.strip():
            raise EmptyDocumentError(
                f"文档内容为空或纯空白，已拒绝导入: {file_name}"
            )

        # ---- 3. content_hash（Phase 3.8.5：namespace 参与文档身份，
        #      实现同内容跨项目共存 + 项目内去重） ----
        content_hash = _document_content_hash(document_text, namespace)

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
                    # Phase 3.8.5：project_id 由服务器端 Provider 结果
                    # 强制写入（调用方 metadata 中的同名键已被剥离/覆盖）
                    meta_data=self._build_document_metadata(
                        metadata, namespace, parser.file_type
                    ),
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
                "knowledge_namespace": namespace,
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

    # ============================================================
    # Lifecycle: update / delete / get / list（Phase 3.5.10）
    # ============================================================

    async def update_document(
        self,
        document_id: int,
        file_path: str | Path,
    ) -> "IngestionResult":
        """原地更新指定 document_id 的内容（保留主键）。

        阶段式安全策略（任务书 §七/§八/§十一）：

            Phase A — 只读加载文档 + 解析新文件 + content_hash 对比
                       （同 → status="unchanged"，零 Embedding 调用）
            Phase B — 重新 Chunk + 重新 Embedding（全部完成前不触碰 DB）
                       （Embedding 失败 → 异常抛出，旧 doc/chunks 不动）
            Phase C — 单事务：删除旧 chunks → 写入新 chunks → 更新
                       document.content_hash/status（任一步失败 → ROLLBACK）

        Args:
            document_id: 已存在的 KnowledgeDocument 主键。
            file_path:   新版本的文件路径（.md / .txt）。

        Returns:
            IngestionResult：status 为 "ready"/"already_exists"（同 hash 走 unchanged）
            或 "updated"。

        Raises:
            KnowledgeDocumentNotFoundError: document_id 不存在。
            DocumentNotFoundError / UnsupportedDocumentTypeError /
            DocumentParseError: Parser 阶段失败。
            EmptyDocumentError: 新内容为空。
            EmbeddingConfigurationError / EmbeddingAPIError /
            EmbeddingResponseError / EmbeddingDimensionError: Embedding 阶段失败。
            KnowledgeIngestionDatabaseError: 写库失败（已回滚）。
        """
        started = time.perf_counter()
        path = Path(file_path)
        file_name = path.name

        # ---- 1. Parser（异常在此抛出，未触碰 DB） ----
        from backend.app.rag.parsers.factory import get_parser

        parser = get_parser(path)
        new_text = parser.parse(path)

        if not new_text.strip():
            raise EmptyDocumentError(
                f"文档内容为空或纯空白，已拒绝更新: {file_name}"
            )

        factory = self._get_session_factory()

        # ---- Phase A: 加载旧文档 + 对比 content_hash ----
        # Phase 3.8.5：hash 以文档自身的 meta_data.project_id（归属）
        # 计算——与创建时一致，保证 unchanged 检测不回归；文档归属
        # 在 update 过程中保持不变（meta_data 不被触碰）。
        old_chunk_count = 0
        old_hash: str | None = None
        with factory() as session:
            existing = session.get(KnowledgeDocument, document_id)
            if existing is None:
                raise KnowledgeDocumentNotFoundError(
                    f"知识文档不存在: document_id={document_id}"
                )
            old_hash = existing.content_hash
            old_chunk_count = len(existing.chunks)
            existing_meta = existing.meta_data
            existing_namespace = (
                existing_meta.get("project_id")
                if isinstance(existing_meta, dict)
                else None
            )

        new_hash = _document_content_hash(new_text, existing_namespace)

        if old_hash == new_hash:
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.info(
                "Knowledge document update skipped (unchanged content)",
                extra={
                    "document_id": document_id,
                    "file_name": file_name,
                    "content_hash": new_hash,
                    "status": "unchanged",
                    "elapsed_ms": elapsed_ms,
                },
            )
            return IngestionResult(
                document_id=document_id,
                file_name=file_name,
                status="unchanged",
                chunk_count=old_chunk_count,
                embedded_chunk_count=0,
                content_hash=new_hash,
            )

        # ---- Phase B: 重新 Chunk + 重新 Embedding（不触碰 DB） ----
        chunks = self._chunker.chunk(new_text)
        if not chunks:
            raise EmptyDocumentError(
                f"文档切分结果为空，已拒绝更新: {file_name}"
            )

        embedding_client = self._get_embedding_client()
        dimension = settings.embedding.dimension
        vectors: list[list[float]] = []
        for chunk in chunks:
            vector = await embedding_client.embed(chunk.content)
            if len(vector) != dimension:
                raise EmbeddingDimensionError(
                    f"Embedding 向量维度不匹配：得到 {len(vector)} 维，"
                    f"EMBEDDING_DIMENSION 配置为 {dimension} 维；"
                    "已阻止数据库写入（不截断、不补零）。"
                )
            vectors.append(vector)

        # ---- Phase C: 单事务 — 删旧 chunks + 写新 chunks + 更新 document ----
        try:
            with factory() as session, session.begin():
                doc = session.get(KnowledgeDocument, document_id)
                if doc is None:
                    raise KnowledgeDocumentNotFoundError(
                        f"知识文档在更新过程中消失: document_id={document_id}"
                    )

                # 删除旧 chunks（DB CASCADE 会自动处理；此处显式 SQL 更可控）
                session.execute(
                    delete(KnowledgeChunk).where(
                        KnowledgeChunk.document_id == document_id
                    )
                )

                # 写入新 chunks
                for chunk, vector in zip(chunks, vectors):
                    session.add(
                        KnowledgeChunk(
                            document_id=document_id,
                            chunk_index=chunk.chunk_index,
                            content=chunk.content,
                            content_hash=_sha256_hex(chunk.content),
                            token_count=None,
                            embedding=vector,
                            meta_data=dict(chunk.metadata),
                        )
                    )

                # 更新 document
                doc.content_hash = new_hash
                doc.status = "ready"
        except SQLAlchemyError as exc:
            raise KnowledgeIngestionDatabaseError(
                f"知识文档更新失败（事务已回滚）: {file_name}: {exc}"
            ) from exc

        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "Knowledge document updated",
            extra={
                "document_id": document_id,
                "file_name": file_name,
                "old_chunk_count": old_chunk_count,
                "new_chunk_count": len(chunks),
                "embedded_chunk_count": len(vectors),
                "status": "updated",
                "elapsed_ms": elapsed_ms,
            },
        )
        return IngestionResult(
            document_id=document_id,
            file_name=file_name,
            status="updated",
            chunk_count=len(chunks),
            embedded_chunk_count=len(vectors),
            content_hash=new_hash,
        )

    # ---------- delete / get / list ----------

    def delete_document(self, document_id: int) -> int:
        """删除指定 document 及其所有 chunks（依赖 FK CASCADE）。

        Returns:
            删除前该文档的 chunk 数。

        Raises:
            KnowledgeDocumentNotFoundError: document_id 不存在。
            KnowledgeIngestionDatabaseError: DB 写失败（事务回滚）。
        """
        factory = self._get_session_factory()
        chunk_count = 0
        try:
            with factory() as session, session.begin():
                doc = session.get(KnowledgeDocument, document_id)
                if doc is None:
                    raise KnowledgeDocumentNotFoundError(
                        f"知识文档不存在: document_id={document_id}"
                    )
                chunk_count = len(doc.chunks or [])
                # ORM cascade + DB ondelete CASCADE 双重保障
                session.delete(doc)
        except SQLAlchemyError as exc:
            raise KnowledgeIngestionDatabaseError(
                f"知识文档删除失败（事务已回滚）: document_id={document_id}: {exc}"
            ) from exc

        logger.info(
            "Knowledge document deleted",
            extra={
                "document_id": document_id,
                "chunk_count": chunk_count,
                "status": "deleted",
            },
        )
        return chunk_count

    def get_document(self, document_id: int) -> "KnowledgeDocumentInfo":
        """按 document_id 查询文档摘要。"""
        factory = self._get_session_factory()
        with factory() as session:
            doc = session.get(KnowledgeDocument, document_id)
            if doc is None:
                raise KnowledgeDocumentNotFoundError(
                    f"知识文档不存在: document_id={document_id}"
                )
            return _to_document_info(doc)

    def list_documents(self) -> tuple["KnowledgeDocumentInfo", ...]:
        """列出所有知识文档摘要（按 id 升序）。"""
        factory = self._get_session_factory()
        with factory() as session:
            rows = session.scalars(
                select(KnowledgeDocument).order_by(KnowledgeDocument.id)
            ).all()
            return tuple(_to_document_info(r) for r in rows)
