"""RAG Execution Repository（Phase 3.12 Step 46；Contract = Step 45）。

RAG Observability 持久化的**最小数据访问层**（严格复用
``ToolExecutionRepository`` / ``LLMUsageRepository`` 的模式）：

    create(observation)        —— 写入一条 RAG 执行观测（内部事务）
    get_by_request_id(...)     —— 只读：按 request_id 取该次请求的全部观测

复用现有数据库基础设施：

    - Session 工厂：``backend.app.db.session.get_session_factory()``
    - 事务：``with factory() as session, session.begin():``（调用方不 commit）
    - ORM Model：``backend.app.db.models.RagExecutionRecordModel``
    - 表：``ai_ops.rag_execution_record``（与业务 schema public 隔离）

事务与失败语义：

    INSERT 失败 → ROLLBACK → ``RagExecutionRepositoryError``
    （SQLAlchemyError 收敛为仓储异常；Adapter 转 warning —— 绝不影响 RAG 结果）

职责边界：

    * **不**做 Observation 校验（校验权威在 DTO 自身；
      本层只做写前类型/长度校验，避免把非法值送进 DB）；
    * **不**做 aggregation / metrics / 分页 / 过滤 DSL
      （除 ``get_by_request_id`` 所需的稳定排序 ``id ASC``）；
    * **不**做 JSON 序列化框架引入（JSONB 由 SQLAlchemy 方言处理）；
    * **不**做 retry / fallback / outbox / 后台任务；
    * 返回内部只读 ``RagExecutionRecordRow`` —— **不返回 ORM 对象**；
    * 读：显式列（禁用 `SELECT *`），不开启写事务。

第一版不提供：list_by_project / list_by_time / metrics / aggregation /
pagination / search / delete / cleanup（属后续 Query / Retention 能力）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models.rag_execution_record import (
    RAG_EXECUTION_PERSISTED_FIELDS,
    RagExecutionRecordModel,
)
from backend.app.db.session import get_session_factory
from backend.app.services.rag_execution_observation import (
    RagExecutionObservation,
)

__all__ = [
    "RagExecutionRepository",
    "RagExecutionRepositoryError",
    "RagExecutionRecordRow",
    "RAG_EXECUTION_READ_COLUMNS",
]

#: 只读查询允许返回的字段（显式列；不用 SELECT *）。
#: = 主键 + Observation 的 13 个字段（Step 45 §三）。
RAG_EXECUTION_READ_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    *RAG_EXECUTION_PERSISTED_FIELDS,
)

#: request_id 长度上限（与 ``assistant_trace.ASSISTANT_REQUEST_ID_MAX_LENGTH`` /
#: ORM 列 VARCHAR(128) 一致）。
_REQUEST_ID_MAX_LENGTH: Final[int] = 128


def _validate_required_request_id(value: object) -> str:
    """读路径 request_id 校验（**触达数据库之前**；不生成 / 不改写）。

    * 非 ``str``（含 ``None``）→ ``TypeError``；
    * 空 / 纯空白 → ``ValueError``；
    * 长度 > 128 → ``ValueError``。
    """
    if not isinstance(value, str):
        raise TypeError(
            "request_id 必须是 str"
            f"（当前: {type(value).__name__}）"
        )
    if not value.strip():
        raise ValueError("request_id 不能为空或纯空白")
    if len(value) > _REQUEST_ID_MAX_LENGTH:
        raise ValueError(
            "request_id 超出长度上限 "
            f"（{len(value)} > {_REQUEST_ID_MAX_LENGTH}）"
        )
    return value


@dataclass(frozen=True)
class RagExecutionRecordRow:
    """Repository 层的内部只读记录（**不是 ORM 对象**）。

    字段严格 = ``RAG_EXECUTION_READ_COLUMNS`` = 主键 ``id`` +
    Observation 的 13 个契约字段（Step 45：持久化 DTO 即
    ``RagExecutionPersistentRecord``；命名沿用项目既有 ``*RecordRow`` 风格）。

    安全边界：**不含** query / answer / content / similarity / embedding /
    prompt / messages / raw response / SQL / credentials / project_id；
    JSONB 数组被还原为**不可变 tuple**（去重 + 首次出现顺序由写侧保证）。
    """

    id: int
    request_id: str
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    result_count: int
    used_chunks_count: int
    top_k: int
    context_truncated: bool
    context_chars: int
    reranker_used: bool
    rerank_elapsed_ms: float | None
    chunk_ids: tuple[int, ...]
    document_ids: tuple[int, ...]


class RagExecutionRepositoryError(Exception):
    """RAG Execution 持久化失败（事务已回滚）。

    调用方（Persistence Adapter）捕获后仅记录 warning：
    RAG 业务结果不因观测持久化失败而改变（Step 43/44 Failure Isolation）。
    读路径：DB 故障**绝不**降级为空列表（空 ≠ 失败；Step 45 §六）。
    """


class RagExecutionRepository:
    """``ai_ops.rag_execution_record`` 的最小读写仓储。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        """构造仓储。

        Args:
            session_factory: Session 工厂；None 时用全局
                ``get_session_factory()``（DATABASE_URL 为空 → 抛
                ``RagExecutionRepositoryError``）。测试可注入 Fake。
        """
        self._session_factory = session_factory

    # ---------- 依赖解析 ----------

    def _get_session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is not None:
            return self._session_factory
        factory = get_session_factory()
        if factory is None:
            raise RagExecutionRepositoryError(
                "DATABASE_URL 未配置，无法持久化 RAG Execution Observation"
            )
        return factory

    # ---------- 写入 ----------

    @staticmethod
    def _values_from(observation: RagExecutionObservation) -> dict[str, Any]:
        """Observation → 写入值（**逐字段显式映射**；严格 = 白名单）。

        * ``None`` 原样保留（``rerank_elapsed_ms`` NULL ≠ 0）；
        * ``chunk_ids`` / ``document_ids``：tuple → JSON 数组（list）；
          顺序与去重已在 DTO 契约中保证（本层不排序 / 不去重）。
        """
        return {
            "request_id": observation.request_id,
            "started_at": observation.started_at,
            "finished_at": observation.finished_at,
            "duration_ms": observation.duration_ms,
            "result_count": observation.result_count,
            "used_chunks_count": observation.used_chunks_count,
            "top_k": observation.top_k,
            "context_truncated": observation.context_truncated,
            "context_chars": observation.context_chars,
            "reranker_used": observation.reranker_used,
            "rerank_elapsed_ms": observation.rerank_elapsed_ms,
            "chunk_ids": list(observation.chunk_ids),
            "document_ids": list(observation.document_ids),
        }

    def build_insert(self, observation: RagExecutionObservation) -> Any:
        """构造 INSERT（本仓储唯一一处写入 SQL 构造点）。

        使用 ``RETURNING`` 一次往返取回落库后的行（含主键 id）；
        不做 ON CONFLICT —— **request_id 不是唯一键**：一次 request
        允许包含多条 RAG 执行观测（Step 45：第一版无唯一约束）。
        """
        return (
            insert(RagExecutionRecordModel)
            .values(**self._values_from(observation))
            .returning(*self._read_columns())
        )

    def create(
        self, observation: RagExecutionObservation
    ) -> RagExecutionRecordRow:
        """写入一条 RAG 执行观测（内部事务；失败已回滚）。

        Args:
            observation: ``RagExecutionObservation``（frozen；13 字段契约）。

        Returns:
            ``RagExecutionRecordRow``（含数据库主键 id）。

        Raises:
            TypeError:  ``observation`` 类型非法（触达 DB 之前）。
            RagExecutionRepositoryError: DB 未配置或写入失败（事务已回滚）。
        """
        if not isinstance(observation, RagExecutionObservation):
            raise TypeError(
                "create 只接受 RagExecutionObservation"
                f"（got {type(observation).__name__}）"
            )
        factory = self._get_session_factory()
        statement = self.build_insert(observation)
        try:
            with factory() as session, session.begin():
                raw = session.execute(statement).one()
        except SQLAlchemyError as exc:
            raise RagExecutionRepositoryError(
                f"RAG Execution 写入失败: {type(exc).__name__}"
            ) from exc
        return self._to_row(raw)

    # ---------- 只读查询 ----------

    @staticmethod
    def _read_columns() -> tuple[Any, ...]:
        """显式 SELECT 列（不允许 `SELECT *`）。"""
        return tuple(
            getattr(RagExecutionRecordModel, name)
            for name in RAG_EXECUTION_READ_COLUMNS
        )

    def build_request_select(self, request_id: str) -> Any:
        """构造按 request_id 的只读 SELECT（唯一一处读 SQL 构造点）。

        ``WHERE request_id = :request_id``（bound parameter；精确匹配）
        + 稳定排序 ``id ASC``（同一 request 内按落库顺序 = 执行顺序）；
        过滤下推 PostgreSQL，不在 Python 里二次过滤。
        """
        return (
            select(*self._read_columns())
            .where(RagExecutionRecordModel.request_id == request_id)
            .order_by(RagExecutionRecordModel.id.asc())
        )

    @staticmethod
    def _to_row(raw: Any) -> RagExecutionRecordRow:
        """Core Row → 内部只读 record（ORM 不外泄；JSONB list → tuple）。"""
        data = raw._mapping  # noqa: SLF001 —— SQLAlchemy Row 的读取约定
        return RagExecutionRecordRow(
            id=int(data["id"]),
            request_id=data["request_id"],
            started_at=data["started_at"],
            finished_at=data["finished_at"],
            duration_ms=float(data["duration_ms"]),
            result_count=int(data["result_count"]),
            used_chunks_count=int(data["used_chunks_count"]),
            top_k=int(data["top_k"]),
            context_truncated=bool(data["context_truncated"]),
            context_chars=int(data["context_chars"]),
            reranker_used=bool(data["reranker_used"]),
            rerank_elapsed_ms=(
                None
                if data["rerank_elapsed_ms"] is None
                else float(data["rerank_elapsed_ms"])
            ),
            chunk_ids=tuple(int(value) for value in (data["chunk_ids"] or ())),
            document_ids=tuple(
                int(value) for value in (data["document_ids"] or ())
            ),
        )

    def get_by_request_id(self, request_id: str) -> list[RagExecutionRecordRow]:
        """只读：按 request_id 取全部观测（``id ASC``；无匹配 → ``[]``）。

        Args:
            request_id: 必填、非空（strip 后非空）、≤128 字符。

        Returns:
            ``list[RagExecutionRecordRow]``；无匹配 → ``[]``（**不是错误**）。

        Raises:
            TypeError:  request_id 非 str（触达 DB 之前）。
            ValueError: request_id 空 / 纯空白 / 超长（触达 DB 之前）。
            RagExecutionRepositoryError: DB 未配置 / 查询失败
                （**绝不降级为空列表**：DB 故障 ≠ 没有记录）。
        """
        validated = _validate_required_request_id(request_id)
        factory = self._get_session_factory()
        statement = self.build_request_select(validated)
        try:
            with factory() as session:  # 读路径不开启写事务
                rows = session.execute(statement).all()
        except SQLAlchemyError as exc:
            raise RagExecutionRepositoryError(
                f"RAG Execution 查询失败: {type(exc).__name__}"
            ) from exc
        return [self._to_row(raw) for raw in rows]
