"""Tool Execution Repository（Phase 3.11 Step 27）。

Tool Observability 持久化的**最小数据访问层**（严格复用
``LLMUsageRepository`` 的模式）：

    create(record)             —— 写入一条 Tool Execution 事实（内部事务）
    get_by_request_id(...)     —— 只读：按 request_id 取该次请求的全部执行

复用现有数据库基础设施：

    - Session 工厂：`backend.app.db.session.get_session_factory()`
    - 事务：`with factory() as session, session.begin():`
      （与 LLMUsageRepository.create() 完全一致；调用方**不**负责
      commit / rollback）
    - ORM Model：`backend.app.db.models.ToolExecutionRecordModel`
    - 表：`ai_ops.tool_execution_record`（与业务 schema public 隔离）

事务与失败语义（§十二）：

    session_factory → Session → session.begin() → INSERT → COMMIT
    INSERT failure → ROLLBACK → ToolExecutionRepositoryError
    （SQLAlchemyError 被收敛为仓储异常；调用方未来转成 warning，
      绝不影响 Tool 执行结果）

Repository 职责边界（§十三 ~ §十五）：

    * **不**做 ToolExecutionRecord 校验（校验权威在 Record 自身）；
    * **不**做 aggregation / metrics / 分页 / 过滤 DSL / 排序策略
      （除本文件为 `get_by_request_id` 所需的稳定排序 `id ASC`）；
    * **不**做安全策略判断（字段白名单在 Model 层已锁定）；
    * **不**做 JSON serialization（序列化属 Step 24 的
      ``tool_observability_serialization``，与本层无关）；
    * **不**做 retry / fallback / outbox / 后台任务；
    * 返回内部 record ``ToolExecutionRecordRow``——**不返回 ORM 对象**；
    * 读：显式列（禁用 `SELECT *`），不开启写事务。

第一版不提供（属后续 Query / Retention 能力）：

    list_by_project / list_by_tool / list_by_time / metrics /
    aggregation / pagination / search / delete / cleanup
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import func, insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models.tool_execution_record import (
    TOOL_EXECUTION_PERSISTED_FIELDS,
    ToolExecutionRecordModel,
)
from backend.app.db.session import get_session_factory
from backend.app.services.tool_execution_record import ToolExecutionRecord

__all__ = [
    "ToolExecutionRepository",
    "ToolExecutionRepositoryError",
    "ToolExecutionRecordRow",
    "ToolExecutionMetricsRow",
    "TOOL_EXECUTION_READ_COLUMNS",
    "MIN_RECENT_LIMIT",
    "MAX_RECENT_LIMIT",
]

#: ``list_recent()`` 的硬上限（**模块级常量**；不新增全局配置）。
#:
#: 说明：项目既有分页上限 ``MAX_QUERY_LIMIT = 100`` 属于 LLM Usage 的
#: **分页查询**契约；本方法不是分页 API（无 offset / cursor），返回的是
#: 「最近 N 条」窗口，因此使用独立常量并给出更大的安全边界（1000）——
#: 仍远小于"整表读取"，防止误用造成的无界查询。
MIN_RECENT_LIMIT: Final[int] = 1
MAX_RECENT_LIMIT: Final[int] = 1000

#: 只读查询允许返回的字段（显式列；不用 SELECT *）。
#: = 主键 + ToolExecutionRecord 的 11 个字段。
TOOL_EXECUTION_READ_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    *TOOL_EXECUTION_PERSISTED_FIELDS,
)


@dataclass(frozen=True)
class ToolExecutionRecordRow:
    """Repository 层的内部只读记录（**不是 ORM 对象**）。

    ORM Model 不得离开 db 层：上层只拿到这个 frozen record，
    后续如需对外 DTO 由 Service / Snapshot 层转换。
    字段严格等于 ``TOOL_EXECUTION_READ_COLUMNS``。
    """

    id: int
    request_id: str
    round: int
    tool_name: str
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    success: bool
    project_id: str | None
    tool_call_id: str | None
    error_code: str | None
    error_type: str | None


@dataclass(frozen=True)
class ToolExecutionMetricsRow:
    """Repository 层的**原始聚合行**（Step 33；不是 ORM / 不是 API DTO）。

    只承载 SQL 聚合结果（``COUNT`` / ``COUNT FILTER`` / ``SUM`` / ``AVG`` /
    ``MAX``）；**不做**比率计算（``success_rate`` / ``failure_rate`` 属应用层
    —— 见 ``ToolExecutionPersistentQueryService.metrics()``）。

    空数据集（无匹配行）：``total_count == success_count == failure_count == 0``；
    ``SUM`` / ``AVG`` / ``MAX`` 在 SQL 中天然为 ``NULL`` → 这里保持 ``None``
    （**不**伪装成 ``0``）。
    """

    total_count: int
    success_count: int
    failure_count: int
    total_duration_ms: float | None
    average_duration_ms: float | None
    max_duration_ms: float | None


class ToolExecutionRepositoryError(Exception):
    """Tool Execution 持久化失败（事务已回滚）。

    调用方（未来的 Persistence Adapter）捕获后仅记录 warning：
    Tool 执行结果不因观测持久化失败而改变（Step 26 ADR：Failure Isolation）。
    """


def _validate_optional_text_filter(name: str, value: object) -> str | None:
    """可选文本过滤值（``str | None``）。

    * ``None`` = 不过滤；
    * ``""`` **是普通字符串值**（``WHERE col = ''``），不自动转 None
      （不做 trim / lower / fuzzy —— 精确匹配语义，§五 / §十三）。
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"{name} 必须是 str 或 None（got {type(value).__name__}）"
        )
    return value


def _validate_optional_bool_filter(name: str, value: object) -> bool | None:
    """可选布尔过滤值（``bool | None``）。

    * ``None`` = 不过滤（**与 ``False`` 不同**：``False`` 是过滤条件
      ``success = FALSE``）；
    * 只接受 bool（拒绝 0 / 1 等 int，避免语义歧义）。
    """
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(
            f"{name} 必须是 bool 或 None（got {type(value).__name__}）"
        )
    return value


def _validate_recent_offset(offset: object) -> int:
    """``list_recent`` 的 offset 校验（§五 / §十二）。

    * 必须是 int（``bool`` 排除）；
    * 必须 >= 0（**无** MAX_OFFSET：不人为引入新上限 / 新全局配置）。

    非法 → ``ValueError``（参数错误，与 DB 故障语义不同）。
    """
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise ValueError(
            f"offset 必须是 int（got {type(offset).__name__}）"
        )
    if offset < 0:
        raise ValueError(f"offset 不允许负数（got {offset}）")
    return offset


def _validate_recent_limit(limit: object) -> int:
    """``list_recent`` 的 limit 边界校验（§七；不新增全局配置）。

    * 必须是 int（``bool`` 排除，避免 ``True`` 被当成 1）；
    * 必须落在 ``[MIN_RECENT_LIMIT, MAX_RECENT_LIMIT]``。

    非法 → ``ValueError``（参数错误，与 DB 故障语义不同）。
    """
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError(
            f"limit 必须是 int（got {type(limit).__name__}）"
        )
    if limit < MIN_RECENT_LIMIT or limit > MAX_RECENT_LIMIT:
        raise ValueError(
            f"limit 超出合法范围 [{MIN_RECENT_LIMIT}, {MAX_RECENT_LIMIT}]"
            f"（got {limit}）"
        )
    return limit


class ToolExecutionRepository:
    """`ai_ops.tool_execution_record` 的最小读写仓储。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        """构造仓储。

        Args:
            session_factory: Session 工厂；None 时用全局
                ``get_session_factory()``（DATABASE_URL 为空 → 抛
                ``ToolExecutionRepositoryError``）。测试可注入 Fake。
        """
        self._session_factory = session_factory

    # ---------- 依赖解析 ----------

    def _get_session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is not None:
            return self._session_factory
        factory = get_session_factory()
        if factory is None:
            raise ToolExecutionRepositoryError(
                "DATABASE_URL 未配置，无法持久化 Tool Execution"
            )
        return factory

    # ---------- 写入 ----------

    @staticmethod
    def _values_from(record: ToolExecutionRecord) -> dict[str, Any]:
        """Record → 写入值（逐字段显式映射；字段严格等于白名单）。"""
        return {
            "request_id": record.request_id,
            "round": record.round,
            "tool_name": record.tool_name,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "duration_ms": record.duration_ms,
            "success": record.success,
            "project_id": record.project_id,
            "tool_call_id": record.tool_call_id,
            "error_code": record.error_code,
            "error_type": record.error_type,
        }

    def build_insert(self, record: ToolExecutionRecord) -> Any:
        """构造 INSERT（本仓储唯一一处写入 SQL 构造点）。

        使用 ``RETURNING`` 一次往返取回落库后的行（含主键 id）；
        不做 ON CONFLICT —— **request_id 不是唯一键**：一次 request
        允许包含多条 Tool 执行（Step 27 §七）。
        """
        return (
            insert(ToolExecutionRecordModel)
            .values(**self._values_from(record))
            .returning(*self._read_columns())
        )

    def create(self, record: ToolExecutionRecord) -> ToolExecutionRecordRow:
        """写入一条 Tool Execution 事实（内部事务；失败已回滚）。

        Args:
            record: ``ToolExecutionRecord``（frozen；校验已完成）。

        Returns:
            ``ToolExecutionRecordRow``（含数据库主键 id）。

        Raises:
            ToolExecutionRepositoryError: DB 未配置或写入失败（事务已回滚）。
        """
        factory = self._get_session_factory()
        statement = self.build_insert(record)
        try:
            with factory() as session, session.begin():
                raw = session.execute(statement).one()
        except SQLAlchemyError as exc:
            raise ToolExecutionRepositoryError(
                f"Tool Execution 写入失败: {type(exc).__name__}"
            ) from exc
        return self._to_row(raw)

    # ---------- 只读查询 ----------

    @staticmethod
    def _read_columns() -> tuple[Any, ...]:
        """显式 SELECT 列（不允许 `SELECT *`）。"""
        return tuple(
            getattr(ToolExecutionRecordModel, name)
            for name in TOOL_EXECUTION_READ_COLUMNS
        )

    def build_request_select(self, request_id: str) -> Any:
        """构造按 request_id 的只读 SELECT（唯一一处读 SQL 构造点）。

        排序固定 ``id ASC``（同一 request 内按落库顺序；带过滤的查询
        必须有稳定排序 —— 沿用 LLMUsageRepository 的稳定排序约定）。
        过滤下推 PostgreSQL，不在 Python 里二次过滤。
        """
        return (
            select(*self._read_columns())
            .where(ToolExecutionRecordModel.request_id == request_id)
            .order_by(ToolExecutionRecordModel.id.asc())
        )

    @staticmethod
    def _to_row(raw: Any) -> ToolExecutionRecordRow:
        """Core Row → 内部 record（ORM 不外泄）。"""
        data = raw._mapping  # noqa: SLF001 —— SQLAlchemy Row 的读取约定
        return ToolExecutionRecordRow(
            id=data["id"],
            request_id=data["request_id"],
            round=data["round"],
            tool_name=data["tool_name"],
            started_at=data["started_at"],
            finished_at=data["finished_at"],
            duration_ms=data["duration_ms"],
            success=data["success"],
            project_id=data["project_id"],
            tool_call_id=data["tool_call_id"],
            error_code=data["error_code"],
            error_type=data["error_type"],
        )

    def build_recent_select(
        self,
        *,
        limit: int,
        offset: int = 0,
        project_id: str | None = None,
        tool_name: str | None = None,
        success: bool | None = None,
    ) -> Any:
        """构造「最近 N 条」只读 SELECT（唯一一处 recent SQL 构造点）。

        * **精确过滤**（Step 32）：``project_id`` / ``tool_name`` / ``success``
          非 ``None`` 时各追加一个 ``WHERE col = :param``，条件之间 **AND**；
          ``None`` → 不追加任何条件（无空 ``WHERE``）；
          过滤全部下推 SQL，**不在 Python 层过滤**；
        * SQL 注入安全：三个值一律走 **bound parameters**（SQLAlchemy
          ``where(Model.col == value)``），**不做**字符串拼接 / ``text()``；
        * 排序固定 ``started_at DESC, id DESC``：
          ``started_at`` 可能相同（同毫秒批量执行）→ 主键 ``id`` 提供
          **deterministic tie-breaker**（不暴露给上层 Read Model）；
          **所有分页必须共用这一套排序**（换掉任一项都会导致跨页
          重复 / 遗漏）；过滤**不改变**排序；
        * 顺序：``WHERE`` → ``ORDER BY`` → ``LIMIT`` → ``OFFSET``
          （过滤发生在分页之前）；
        * ``LIMIT`` / ``OFFSET`` / 过滤值由调用方校验后传入
          （本方法只构造，不校验）；
        * 只有 LIMIT + OFFSET（**无** total_count / COUNT(*) —— 属后续
          Metrics / Query metadata 范围）。
        """
        statement = self._apply_exact_filters(
            select(*self._read_columns()),
            project_id=project_id,
            tool_name=tool_name,
            success=success,
        )
        return (
            statement.order_by(
                ToolExecutionRecordModel.started_at.desc(),
                ToolExecutionRecordModel.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )

    @staticmethod
    def _apply_exact_filters(
        statement: Any,
        *,
        project_id: str | None = None,
        tool_name: str | None = None,
        success: bool | None = None,
    ) -> Any:
        """追加**精确过滤**（唯一一处过滤构造点；recent 与 metrics 共用）。

        * ``None`` → 不追加条件 → **不生成空 ``WHERE``**（无 ``WHERE 1=1``）；
        * 条件之间 **AND**；
        * 全部走 **bound parameters**（``col == value``），**不做**字符串
          拼接 / ``text()`` 拼接用户输入（SQL 注入安全）；
        * 无 LIKE / ILIKE / regex / fuzzy / 时间范围。
        """
        if project_id is not None:
            statement = statement.where(
                ToolExecutionRecordModel.project_id == project_id
            )
        if tool_name is not None:
            statement = statement.where(
                ToolExecutionRecordModel.tool_name == tool_name
            )
        if success is not None:
            statement = statement.where(
                ToolExecutionRecordModel.success == success
            )
        return statement

    def build_metrics_select(
        self,
        *,
        project_id: str | None = None,
        tool_name: str | None = None,
        success: bool | None = None,
    ) -> Any:
        """构造 Metrics 聚合 SELECT（唯一一处 metrics SQL 构造点；Step 33）。

        SQL 形态（全部在**数据库内**完成，无 Python 全量扫描）：::

            SELECT COUNT(*)                                        AS total_count,
                   COUNT(*) FILTER (WHERE success = TRUE)          AS success_count,
                   COUNT(*) FILTER (WHERE success = FALSE)         AS failure_count,
                   SUM(duration_ms)                                AS total_duration_ms,
                   AVG(duration_ms)                                AS average_duration_ms,
                   MAX(duration_ms)                                AS max_duration_ms
            FROM ai_ops.tool_execution_record
            [WHERE project_id = :p] [AND tool_name = :t] [AND success = :s]

        * 精确过滤（与 History 完全一致；复用 ``_apply_exact_filters``）；
        * 无过滤 → 不生成 ``WHERE``；
        * 空数据集 → ``SUM`` / ``AVG`` / ``MAX`` 天然为 ``NULL``（不伪装 0）；
        * **不返回 ORM 对象**：由 ``get_metrics()`` 映射为
          ``ToolExecutionMetricsRow``；
        * 不计算 ``success_rate`` / ``failure_rate``（应用层职责）；
        * 无 ``ORDER BY`` / ``LIMIT`` / ``OFFSET``（聚合单行）。
        """
        statement = self._apply_exact_filters(
            select(
                func.count().label("total_count"),
                func.count()
                .filter(ToolExecutionRecordModel.success.is_(True))
                .label("success_count"),
                func.count()
                .filter(ToolExecutionRecordModel.success.is_(False))
                .label("failure_count"),
                func.sum(ToolExecutionRecordModel.duration_ms).label(
                    "total_duration_ms"
                ),
                func.avg(ToolExecutionRecordModel.duration_ms).label(
                    "average_duration_ms"
                ),
                func.max(ToolExecutionRecordModel.duration_ms).label(
                    "max_duration_ms"
                ),
            ).select_from(ToolExecutionRecordModel),
            project_id=project_id,
            tool_name=tool_name,
            success=success,
        )
        return statement

    @staticmethod
    def _to_metrics(raw: Any) -> ToolExecutionMetricsRow:
        """Core Row → 内部聚合行（ORM / SQLAlchemy Result 不外泄）。"""
        data = raw._mapping  # noqa: SLF001 —— SQLAlchemy Row 的读取约定
        return ToolExecutionMetricsRow(
            total_count=int(data["total_count"] or 0),
            success_count=int(data["success_count"] or 0),
            failure_count=int(data["failure_count"] or 0),
            total_duration_ms=(
                None
                if data["total_duration_ms"] is None
                else float(data["total_duration_ms"])
            ),
            average_duration_ms=(
                None
                if data["average_duration_ms"] is None
                else float(data["average_duration_ms"])
            ),
            max_duration_ms=(
                None
                if data["max_duration_ms"] is None
                else float(data["max_duration_ms"])
            ),
        )

    def get_metrics(
        self,
        *,
        project_id: str | None = None,
        tool_name: str | None = None,
        success: bool | None = None,
    ) -> ToolExecutionMetricsRow:
        """只读：聚合指标原始行（SQL 内聚合；**不**逐行扫描）。

        Args:
            project_id: 精确匹配（``None`` = 不过滤；``""`` 是普通字符串值）。
            tool_name:  精确匹配（``None`` = 不过滤）。
            success:    ``True`` / ``False`` 精确匹配（``None`` = 不过滤）。

        Returns:
            ``ToolExecutionMetricsRow``（单行；空数据集 → 计数 0 +
            时长聚合 ``None``）。

        Raises:
            ValueError: 过滤值类型非法（触达数据库**之前**校验）。
            ToolExecutionRepositoryError: DB 未配置或查询失败
                （**不返回 0 掩盖故障**：DB failure ≠ empty database）。
        """
        validated_project = _validate_optional_text_filter(
            "project_id", project_id
        )
        validated_tool = _validate_optional_text_filter(
            "tool_name", tool_name
        )
        validated_success = _validate_optional_bool_filter(
            "success", success
        )
        factory = self._get_session_factory()
        statement = self.build_metrics_select(
            project_id=validated_project,
            tool_name=validated_tool,
            success=validated_success,
        )
        try:
            with factory() as session:  # 读路径不开启写事务
                raw = session.execute(statement).one()
        except SQLAlchemyError as exc:
            raise ToolExecutionRepositoryError(
                f"Tool Execution 指标查询失败: {type(exc).__name__}"
            ) from exc
        return self._to_metrics(raw)

    def list_recent(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        project_id: str | None = None,
        tool_name: str | None = None,
        success: bool | None = None,
    ) -> list[ToolExecutionRecordRow]:
        """只读：精确过滤 + 时间倒序的一页（``limit`` + ``offset``）。

        Args:
            limit:      1 ~ ``MAX_RECENT_LIMIT``（默认 100）。
            offset:     >= 0（默认 0；无上限 —— 超出记录数即空页，不是错误）。
            project_id: 精确匹配授权作用域（``None`` = 不过滤；
                        ``""`` 是普通字符串值）。
            tool_name:  精确匹配 Tool 名（``None`` = 不过滤）。
            success:    ``True`` / ``False`` 精确匹配（``None`` = 不过滤；
                        **``False`` 与 ``None`` 语义不同**）。

        Returns:
            ``list[ToolExecutionRecordRow]``（**可能为空列表**：
            无匹配 / 数据库无数据 / offset 超界 → ``[]``，不是 ``None``）。

        Raises:
            ValueError: ``limit`` / ``offset`` / 过滤值类型非法
                （全部在触达数据库**之前**校验）。
            ToolExecutionRepositoryError: DB 未配置或查询失败
                （**不返回 [] 掩盖故障**：DB failure ≠ empty database）。
        """
        validated = _validate_recent_limit(limit)
        validated_offset = _validate_recent_offset(offset)
        validated_project = _validate_optional_text_filter(
            "project_id", project_id
        )
        validated_tool = _validate_optional_text_filter(
            "tool_name", tool_name
        )
        validated_success = _validate_optional_bool_filter(
            "success", success
        )
        factory = self._get_session_factory()
        statement = self.build_recent_select(
            limit=validated,
            offset=validated_offset,
            project_id=validated_project,
            tool_name=validated_tool,
            success=validated_success,
        )
        try:
            with factory() as session:  # 读路径不开启写事务
                rows = list(session.execute(statement).all())
        except SQLAlchemyError as exc:
            raise ToolExecutionRepositoryError(
                f"Tool Execution 查询失败: {type(exc).__name__}"
            ) from exc
        return [self._to_row(raw) for raw in rows]

    def get_by_request_id(
        self, request_id: str
    ) -> list[ToolExecutionRecordRow]:
        """只读：取同一 request 的全部 Tool Execution（按 id 升序）。

        Args:
            request_id: 关联 ID（精确匹配）。

        Returns:
            ``list[ToolExecutionRecordRow]``（无匹配 → 空列表）。

        Raises:
            ToolExecutionRepositoryError: DB 未配置或查询失败。
        """
        factory = self._get_session_factory()
        statement = self.build_request_select(request_id)
        try:
            with factory() as session:  # 读路径不开启写事务
                rows = list(session.execute(statement).all())
        except SQLAlchemyError as exc:
            raise ToolExecutionRepositoryError(
                f"Tool Execution 查询失败: {type(exc).__name__}"
            ) from exc
        return [self._to_row(raw) for raw in rows]
