"""Tool Execution Persistent Query Service（Phase 3.11 Step 29）。

**Persistent Observability 的只读 Query Boundary**（与 Runtime Query 并列）：

    Runtime（进程内）                        Persistent（数据库）
    ─────────────────                       ─────────────────────
    ToolObservabilityQueryService           ToolExecutionPersistentQueryService
        ↓                                       ↓
    InMemoryToolExecutionCollector          ToolExecutionRepository
        ↓                                       ↓
    ToolExecutionSnapshot                   ai_ops.tool_execution_record
                                                ↓
                                            ToolExecutionSnapshot（同一 Read Model）

边界（§五）：

    Query Service = application read boundary（本模块）
    Repository    = persistence boundary（Step 27/29 的最小只读查询）

    * 本模块**不** import SQLAlchemy / Session / ORM Model；
      不建 Session、不写 SQL、不做 DDL/DML；
    * 不 import Collector / ToolExecutionService / ToolRegistry / Tool Handler；
      不修改 Collector（两者并列，**不**合并 / 不 fallback）；
    * **只读**：不 INSERT / UPDATE / DELETE / TRUNCATE。

Read Model（§十一 / §十二）：

    ToolExecutionRecordRow（Repository 的 frozen 内部 record）
        ↓ _to_snapshot()（**显式逐字段映射**）
    ToolExecutionSnapshot（Step 22 对外 Read Model；**严格 11 字段**）

    * 不使用 ``vars()`` / ``asdict()`` / ``__dict__`` / ``model_dump()``；
    * 主键 ``id`` **不**进入 Snapshot（仅供 Repository 内部排序 /
      tie-breaker）；也不新增 created_at / database_name 等字段。

排序（§十七）：``started_at DESC, id DESC``（由 Repository 保证；
    ``started_at`` 相同时由主键提供 deterministic tie-breaker）。

Limit（§七 / §二十八）：只支持 ``limit``（**无** offset / cursor / page）；
    Repository 侧硬上限 ``MAX_RECENT_LIMIT``（1000）—— Repository 仍会
    二次校验（defense in depth）。

错误语义（§十四 / §十五）：沿用项目约定（与 ``LLMUsageQueryService`` 一致）——

    成功但无数据      → ``[]``（不是 ``None``）
    DB 未配置 / 失败  → ``ToolExecutionRepositoryError`` **原样透传**
                        （DB failure ≠ empty database）
    limit 非法        → ``ValueError``（参数错误，不触达数据库）

明确不做（§二十七 / §二十八）：Persistent Metrics（COUNT / SUM / AVG /
    success_rate）、分页、过滤（project_id / tool_name / success /
    时间范围 / 关键词）、retention / TTL / delete / cleanup、
    HTTP History API（本阶段**不**接入任何 HTTP 层）。
"""
from __future__ import annotations

from typing import Final

from backend.app.db.tool_execution_repository import (
    MAX_RECENT_LIMIT,
    MIN_RECENT_LIMIT,
    ToolExecutionRecordRow,
    ToolExecutionRepository,
)
from backend.app.services.tool_execution_metrics_service import (
    ToolExecutionMetricsSnapshot,
)
from backend.app.services.tool_observability_snapshot import (
    ToolExecutionSnapshot,
)

__all__ = [
    "ToolExecutionPersistentQueryService",
    "DEFAULT_RECENT_LIMIT",
    "DEFAULT_RECENT_OFFSET",
    "MIN_RECENT_LIMIT",
    "MAX_RECENT_LIMIT",
]

#: 「最近 N 条」默认条数（无分页语义上限 —— 单次有界查询）。
DEFAULT_RECENT_LIMIT: Final[int] = 100

#: 分页默认起始偏移（offset >= 0；无 MAX_OFFSET）。
DEFAULT_RECENT_OFFSET: Final[int] = 0

#: ``request_id`` 长度上限（与 ``ai_ops.tool_execution_record.request_id``
#: 的 ``VARCHAR(128)`` 一致；Step 41）。
_TOOL_EXECUTION_REQUEST_ID_MAX_LENGTH: Final[int] = 128


def _validate_recent_limit(limit: object) -> int:
    """应用层 limit 校验（与 Repository 的硬边界一致；不触达数据库）。"""
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


def _validate_recent_offset(offset: object) -> int:
    """应用层 offset 校验（>= 0；无 MAX_OFFSET；不触达数据库）。"""
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise ValueError(
            f"offset 必须是 int（got {type(offset).__name__}）"
        )
    if offset < 0:
        raise ValueError(f"offset 不允许负数（got {offset}）")
    return offset


def _validate_required_request_id(value: object) -> str:
    """**必填** request_id（Step 41：Assistant Trace 的 Tool 读路径）。

    * 必须是 ``str`` 且 strip 后非空（``""`` / ``"   "`` → ValueError）；
    * 长度上限 = ORM 列 ``VARCHAR(128)``（超长 → ValueError）；
    * 只校验：不 truncate / 不 normalize / 不生成 / 不回填；
      返回值与入参逐字符一致。
    """
    if not isinstance(value, str):
        raise ValueError(
            f"request_id 必须是 str（got {type(value).__name__}）"
        )
    if not value.strip():
        raise ValueError("request_id 不能为空或纯空白")
    if len(value) > _TOOL_EXECUTION_REQUEST_ID_MAX_LENGTH:
        raise ValueError(
            "request_id 超出长度上限 "
            f"（{len(value)} > {_TOOL_EXECUTION_REQUEST_ID_MAX_LENGTH}）"
        )
    return value


def _validate_optional_text_filter(name: str, value: object) -> str | None:
    """可选文本过滤值（``str | None``；``""`` 是普通字符串值）。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"{name} 必须是 str 或 None（got {type(value).__name__}）"
        )
    return value


def _validate_optional_bool_filter(name: str, value: object) -> bool | None:
    """可选布尔过滤值（``bool | None``；``False`` ≠ ``None``）。"""
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(
            f"{name} 必须是 bool 或 None（got {type(value).__name__}）"
        )
    return value


class ToolExecutionPersistentQueryService:
    """数据库侧只读查询边界（Row → Snapshot；无写入能力）。"""

    def __init__(
        self,
        repository: ToolExecutionRepository | None = None,
    ) -> None:
        """构造查询服务。

        Args:
            repository: Tool Execution 仓储；None 时构造默认
                ``ToolExecutionRepository()``（复用全局 session factory；
                **构造期不连接数据库**）。测试可注入 Fake。
        """
        self._repository = (
            repository if repository is not None else ToolExecutionRepository()
        )

    # ---------- 只读暴露（便于测试 / 装配断言） ----------

    @property
    def repository(self) -> ToolExecutionRepository:
        """底层仓储（持久化边界；本服务只调用其只读方法）。"""
        return self._repository

    # ---------- 对外 Contract ----------

    def list_rows_by_request_id(
        self, request_id: str,
    ) -> list[ToolExecutionRecordRow]:
        """只读：取同一 request_id 的全部 Tool Execution **行**（含主键 ``id``）。

        Phase 3.12 Step 66（**additive**，不改变 :meth:`list_by_request_id`）：

            * 与 :meth:`list_by_request_id` **同一个数据源 / 同一排序**
              （``ai_ops.tool_execution_record`` · ``ORDER BY id ASC``）；
            * 差异仅在于：本方法返回 ``ToolExecutionRecordRow``（**含数据库主键
              ``id``**），供 Assistant Timeline 分组投影使用 ``source_id``
              （Step 66 §九：身份必须是**真实主键**，禁止生成 / 伪造）；
            * ``ToolExecutionSnapshot``（11 字段）继续**不含**主键 ——
              Assistant Trace / Runtime API 契约**完全不变**。

        Raises:
            ValueError:                   request_id 非法（触达 DB 之前）。
            ToolExecutionRepositoryError: DB 未配置 / 查询失败（**不**降级为空列表）。
        """
        validated = _validate_required_request_id(request_id)
        return list(self._repository.get_by_request_id(validated))

    @staticmethod
    def _to_snapshot(row: ToolExecutionRecordRow) -> ToolExecutionSnapshot:
        """``ToolExecutionRecordRow`` → ``ToolExecutionSnapshot``（显式映射）。

        * 逐字段显式映射（不使用 ``vars`` / ``asdict`` / ``__dict__``）；
        * 主键 ``id`` **不外泄**（Snapshot 严格 11 字段）；
        * 纯转换：无 IO / 无 DB / 无聚合 / 无序列化。
        """
        return ToolExecutionSnapshot(
            request_id=row.request_id,
            round=row.round,
            tool_name=row.tool_name,
            started_at=row.started_at,
            finished_at=row.finished_at,
            duration_ms=row.duration_ms,
            success=row.success,
            project_id=row.project_id,
            tool_call_id=row.tool_call_id,
            error_code=row.error_code,
            error_type=row.error_type,
        )

    def list_recent(
        self,
        *,
        limit: int = DEFAULT_RECENT_LIMIT,
        offset: int = DEFAULT_RECENT_OFFSET,
        project_id: str | None = None,
        tool_name: str | None = None,
        success: bool | None = None,
    ) -> list[ToolExecutionSnapshot]:
        """精确过滤 + 时间倒序的一页（``started_at DESC, id DESC``）。

        Args:
            limit:      1 ~ ``MAX_RECENT_LIMIT``，默认 ``DEFAULT_RECENT_LIMIT``。
            offset:     >= 0，默认 0（超出记录数 → 空列表，不是错误）。
            project_id: 精确匹配（``None`` = 不过滤；``""`` 是普通字符串值）。
            tool_name:  精确匹配（``None`` = 不过滤）。
            success:    ``True`` / ``False`` 精确匹配（``None`` = 不过滤 ——
                        **与 ``False`` 语义不同**）。

        Returns:
            ``list[ToolExecutionSnapshot]``（按上序；无匹配 / 超出窗口 → ``[]``，
            绝不返回 ``None``）。每项严格 11 字段。

        Raises:
            ValueError: 任何参数非法（类型 / 越界 / 负 offset；
                全部在触达数据库**之前**校验）。
            ToolExecutionRepositoryError: DB 未配置 / 查询失败（原样透传）。

        Note:
            本层**不执行 Python filtering / 不排序 / 不聚合 / 不去重**：
            过滤条件与顺序（含同 ``started_at`` 时的 ``id`` tie-breaker）
            全部下推到 Repository / SQL —— 这是「过滤先于分页」以及
            「跨页一致」的前提。
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
        rows = self._repository.list_recent(
            limit=validated,
            offset=validated_offset,
            project_id=validated_project,
            tool_name=validated_tool,
            success=validated_success,
        )
        return [self._to_snapshot(row) for row in rows]

    def list_by_request_id(
        self, request_id: str
    ) -> list[ToolExecutionSnapshot]:
        """只读：取**同一 request_id** 的全部已持久化 Tool Execution
        （Phase 3.12 Step 41；Assistant Trace 的 Tool 数据源）。

        Pipeline：

            validate request_id（DB 之前）
                ↓
            Repository.get_by_request_id()（既有只读方法；显式列 + 精确匹配）
                ↓
            [ToolExecutionSnapshot]（11 安全字段；无 id / 无 arguments / 无 result）

        顺序：复用 Repository 既有稳定排序 ``id ASC``（同一 request 内
        落库顺序 = 执行顺序；**本层不重新排序**）。

        边界：

            * **只读**：无写入 / 无事务开启 / 不修改任何 Record；
            * **不合并** Runtime Collector 数据（避免 duplicate Tool Execution）：
              本方法只读 ``ai_ops.tool_execution_record``；
            * 不做聚合 / 不做业务判断 / 不查 LLM / 不查 RAG；
            * 无分页参数：一次 Assistant 请求内的 Tool 执行数量由
              Tool Calling 预算约束（Repository 查询本身即 request 作用域）。

        Args:
            request_id: 必填、非空（strip 后非空）、≤128 字符
                （通常是 Assistant request_id）。

        Returns:
            ``list[ToolExecutionSnapshot]``；无匹配 → ``[]``（**不是错误**）。

        Raises:
            ValueError:                  request_id 非法（触达 DB 之前）。
            ToolExecutionRepositoryError: DB 未配置 / 查询失败
                （**不降级为空列表**：DB 故障 ≠ 没有 Tool Execution）。
        """
        validated = _validate_required_request_id(request_id)
        rows = self._repository.get_by_request_id(validated)
        return [self._to_snapshot(row) for row in rows]

    def metrics(
        self,
        *,
        project_id: str | None = None,
        tool_name: str | None = None,
        success: bool | None = None,
    ) -> ToolExecutionMetricsSnapshot:
        """数据库侧**持久 Metrics**（SQL 聚合 + 应用层比率计算；Step 33）。

        流水线：

            validate filters
                ↓
            Repository.get_metrics()（COUNT / COUNT FILTER / SUM / AVG / MAX）
                ↓
            raw aggregate row
                ↓
            success_rate / failure_rate（应用层）
                ↓
            ToolExecutionMetricsSnapshot（复用 Runtime Metrics 的 frozen DTO）

        语义（与 Runtime Metrics **完全一致**，但数据来源不同）：

            total_count         = COUNT(*)
            success_count       = COUNT(*) FILTER (WHERE success)
            failure_count       = COUNT(*) FILTER (WHERE NOT success)
            success_rate        = success_count / total_count（空 → None）
            failure_rate        = failure_count / total_count（空 → None）
            total_duration_ms   = SUM(duration_ms)（空 → 0.0）
            average_duration_ms = AVG(duration_ms)（空 → None）
            max_duration_ms     = MAX(duration_ms)（空 → None）

            **空数据集时比率 / 均值 / 最大值一律 None（不是 0）**；
            空 SUM 按约定为 0.0（"无耗时记录"），与 Runtime Metrics 一致。

        Args:
            project_id: 精确匹配（``None`` = 不过滤）。
            tool_name:  精确匹配（``None`` = 不过滤）。
            success:    ``True`` / ``False`` 精确匹配（``None`` = 不过滤 ——
                        **与 ``False`` 语义不同**）。

        Returns:
            ``ToolExecutionMetricsSnapshot``（8 字段；无 limit / offset ——
            聚合结果不是列表，**无分页语义**）。

        Raises:
            ValueError: 过滤值类型非法（触达数据库**之前**校验）。
            ToolExecutionRepositoryError: DB 未配置 / 查询失败（原样透传）。

        Note:
            本层**不做** Python 全量扫描 / Python 过滤 / Python 排序 /
            ORM 访问 / Session 访问：聚合与过滤全部在 SQL 内完成，
            这里只做比率除法与 DTO 组装。
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
        row = self._repository.get_metrics(
            project_id=validated_project,
            tool_name=validated_tool,
            success=validated_success,
        )
        total_count = row.total_count
        success_count = row.success_count
        failure_count = row.failure_count
        return ToolExecutionMetricsSnapshot(
            total_count=total_count,
            success_count=success_count,
            failure_count=failure_count,
            success_rate=(
                success_count / total_count if total_count else None
            ),
            failure_rate=(
                failure_count / total_count if total_count else None
            ),
            # 空数据集：SQL SUM 为 NULL → 归一为 0.0（与 Runtime Metrics 一致）
            total_duration_ms=float(row.total_duration_ms or 0.0),
            average_duration_ms=(
                row.average_duration_ms if total_count else None
            ),
            max_duration_ms=row.max_duration_ms if total_count else None,
        )
