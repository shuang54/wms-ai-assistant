"""LLM Usage Query Read Boundary（Phase 3.10.17）。

本模块只做一件事：**应用内部如何安全、稳定、可测试地读取
`ai_ops.llm_usage_record`**。

    PostgreSQL
        ↓
    LLMUsageRepository（SQL / Session / Row）
        ↓
    LLMUsageRecordRow（db 层内部记录，不是 ORM 对象）
        ↓
    LLMUsageQueryService（校验 → repository → **安全 DTO**）
        ↓
    LLMUsageRecordView（frozen DTO，对外只读边界）

职责边界：

    * Repository 负责 SQL / Session / Row；
    * Query Service 负责：过滤参数校验 → 调用 Repository → Row → DTO；
    * Service **不创建 SQLAlchemy Session**（Session 仍由 Repository
      管理，每次查询独立 Session，§二十 / §二十四）；
    * 上层业务不得直接使用 `session.query()` / ORM Model 访问 Usage
      Persistence（§二）。

明确不做（后续独立阶段）：

    * **Analytics**：无 sum_tokens() / average_tokens() / daily_usage() /
      monthly_usage() / GROUP BY 业务 API（§二十一 / §三十六）；
    * **Cost**：不接 LLMPricing / LLMCost——持久化层没有 price /
      currency / cost 字段，Usage Query 就是 Usage Query（§二十二）；
    * **Permission**：不引入 user / role / tenant / project ACL（§二十三）；
    * **HTTP / Dashboard**：无 FastAPI Router、无图表（§三十四 / §三十五）；
    * 不为 provider / model 查询新建索引（§三十七）。
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime

from backend.app.db.llm_usage_repository import (
    LLMUsageRecordRow,
    LLMUsageRepository,
    LLMUsageRepositoryError,
)

__all__ = [
    "LLMUsageRecordView",
    "LLMUsageQueryFilter",
    "LLMUsageQueryService",
    "LLMUsageQueryError",
    "LLMUsageQueryInputError",
    "LLM_USAGE_VIEW_FIELDS",
    "MIN_QUERY_LIMIT",
    "MAX_QUERY_LIMIT",
    "DEFAULT_QUERY_LIMIT",
    "DEFAULT_QUERY_OFFSET",
]


# ============================================================
# 分页常量（§十一 / §十二）
# ============================================================

MIN_QUERY_LIMIT: int = 1
MAX_QUERY_LIMIT: int = 100
DEFAULT_QUERY_LIMIT: int = 50
DEFAULT_QUERY_OFFSET: int = 0


# ============================================================
# 异常（沿用项目 XxxError + InputError 模式，不新造复杂体系）
# ============================================================

class LLMUsageQueryError(Exception):
    """LLM Usage 查询失败的根异常。

    Repository 的 `LLMUsageRepositoryError` **原样透传**给调用方
    （沿用 `VectorSearchService` 的透传约定，§二十五）。
    """


class LLMUsageQueryInputError(LLMUsageQueryError):
    """查询参数非法（在进入数据库之前被拒绝）。"""


# ============================================================
# DTO（frozen dataclass —— 项目通用 DTO 约定，不引入 Pydantic）
# ============================================================

@dataclass(frozen=True)
class LLMUsageRecordView:
    """LLM Usage Record 的只读视图 DTO（Phase 3.10.17）。

    字段严格等于安全白名单（§七）：

        id / request_id / provider / model /
        prompt_tokens / completion_tokens / total_tokens / created_at

    绝不携带 ORM Model / Session / Connection / Engine /
    prompt / messages / response / SQL / RAG context /
    tool arguments / API key / password / authorization /
    database URL / cost / price / currency。

    Attributes:
        created_at: timezone-aware datetime（ORM 列为
            `DateTime(timezone=True)`，项目统一使用 tz-aware 时间）。
    """

    id: int
    request_id: str | None
    provider: str | None
    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    created_at: datetime

    def __post_init__(self) -> None:
        if isinstance(self.id, bool) or not isinstance(self.id, int):
            raise ValueError(
                f"id 必须是 int（got {type(self.id).__name__}）"
            )
        for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(
                    f"{name} 必须是 int 或 None"
                    f"（got {type(value).__name__}）"
                )
            if value < 0:
                raise ValueError(f"{name} 不允许负数（got {value}）")
        if not isinstance(self.created_at, datetime):
            raise ValueError(
                "created_at 必须是 datetime"
                f"（got {type(self.created_at).__name__}）"
            )

    @classmethod
    def from_row(cls, row: LLMUsageRecordRow) -> "LLMUsageRecordView":
        """Repository Row → 只读 DTO（唯一转换入口）。"""
        return cls(
            id=row.id,
            request_id=row.request_id,
            provider=row.provider,
            model=row.model,
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            total_tokens=row.total_tokens,
            created_at=row.created_at,
        )


#: DTO 字段集合（测试 / 未来接入点校验用）。
LLM_USAGE_VIEW_FIELDS: frozenset[str] = frozenset(
    field.name for field in fields(LLMUsageRecordView)
)


# ============================================================
# Query Filter（§八 / §九：只有表上真实存在的字段）
# ============================================================

@dataclass(frozen=True)
class LLMUsageQueryFilter:
    """Usage 查询过滤条件（全部可选）。

    不支持 user_id / project_id / tenant_id / session_id / trace_id /
    status / cost / currency / token_range——`llm_usage_record` 本身
    没有这些字段，本阶段**不为 Query 修改数据库结构**（§九）。

    Args:
        request_id:      精确匹配（None = 不过滤）。
        provider:        精确匹配（None = 不过滤）。
        model:           精确匹配（None = 不过滤）。
        created_at_from: 下界，`created_at >=`（含边界）。
        created_at_to:   上界，`created_at <=`（含边界）。
        limit:           1 ~ 100，默认 50。
        offset:          >= 0，默认 0。

    Raises:
        LLMUsageQueryInputError: 任何参数非法。
    """

    request_id: str | None = None
    provider: str | None = None
    model: str | None = None
    created_at_from: datetime | None = None
    created_at_to: datetime | None = None
    limit: int = DEFAULT_QUERY_LIMIT
    offset: int = DEFAULT_QUERY_OFFSET

    def __post_init__(self) -> None:
        for name in ("request_id", "provider", "model"):
            _validate_optional_text(name, getattr(self, name))
        _validate_optional_datetime("created_at_from", self.created_at_from)
        _validate_optional_datetime("created_at_to", self.created_at_to)
        _validate_limit(self.limit)
        _validate_offset(self.offset)
        # §十四：非法时间范围必须 reject —— 不自动交换、不自动修正、
        # 也不静默返回空结果。
        if (
            self.created_at_from is not None
            and self.created_at_to is not None
            and self.created_at_from > self.created_at_to
        ):
            raise LLMUsageQueryInputError(
                "created_at_from 必须小于或等于 created_at_to"
                f"（got from={self.created_at_from.isoformat()}"
                f" > to={self.created_at_to.isoformat()}）"
            )


# ============================================================
# 参数校验（纯函数，deterministic，先于任何 DB 访问）
# ============================================================

def _validate_optional_text(name: str, value: object) -> None:
    """可选字符串：None OK；非空且在 `strip()` 后仍非空。

    空白串视为"未提供"的歧义输入——本阶段明确拒绝（不静默 trim）。
    """
    if value is None:
        return
    if not isinstance(value, str):
        raise LLMUsageQueryInputError(
            f"{name} 必须是 str 或 None（got {type(value).__name__}）"
        )
    if not value.strip():
        raise LLMUsageQueryInputError(f"{name} 不允许为空字符串")


def _validate_optional_datetime(name: str, value: object) -> None:
    """可选 datetime：None OK；必须是 timezone-aware datetime。

    项目统一使用 tz-aware 时间（`DateTime(timezone=True)` +
    `datetime.now(timezone.utc)`）；naive datetime 会导致跨时区比较
    语义不确定，因此明确拒绝（§十三：严格复用现有时间约定）。
    """
    if value is None:
        return
    if not isinstance(value, datetime):
        raise LLMUsageQueryInputError(
            f"{name} 必须是 datetime 或 None（got {type(value).__name__}）"
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise LLMUsageQueryInputError(
            f"{name} 必须是 timezone-aware datetime（禁止 naive datetime）"
        )


def _validate_limit(value: object) -> None:
    """limit：int（bool 排除），1 ~ 100（§十二）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise LLMUsageQueryInputError(
            f"limit 必须是 int（got {type(value).__name__}）"
        )
    if value < MIN_QUERY_LIMIT or value > MAX_QUERY_LIMIT:
        raise LLMUsageQueryInputError(
            f"limit 超出合法范围 [{MIN_QUERY_LIMIT}, {MAX_QUERY_LIMIT}]"
            f"（got {value}）"
        )


def _validate_offset(value: object) -> None:
    """offset：int（bool 排除），>= 0（§十二）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise LLMUsageQueryInputError(
            f"offset 必须是 int（got {type(value).__name__}）"
        )
    if value < 0:
        raise LLMUsageQueryInputError(
            f"offset 不允许负数（got {value}）"
        )


# ============================================================
# Query Service（§二十）
# ============================================================

class LLMUsageQueryService:
    """Usage 只读查询服务（validate → Repository → DTO）。

    用法：

        service = LLMUsageQueryService()
        rows = service.list_records(provider="deepseek")
        row = service.get_by_request_id("chatcmpl-xxx")

    或者显式传 Fitler：

        from datetime import datetime, timezone, timedelta
        rows = service.query(
            LLMUsageQueryFilter(
                created_at_from=datetime.now(timezone.utc) - timedelta(days=1),
                limit=10,
            )
        )

    约束（§二十 / §二十一 / §二十二 / §二十三 / §二十五）：

        * 不创建 Session（Session 由 Repository 管理）；
        * 不做 sum / avg / count 业务统计；
        * 不计算 cost（不接 LLMPricing / LLMCost）；
        * 不做权限过滤；
        * `LLMUsageRepositoryError` 原样透传给调用方。
    """

    def __init__(self, repository: LLMUsageRepository | None = None) -> None:
        """构造查询服务。

        Args:
            repository: Usage 仓储；None 时构造默认 `LLMUsageRepository()`
                （复用全局 session factory）。测试可注入 Fake。
        """
        self._repository = (
            repository if repository is not None else LLMUsageRepository()
        )

    def get_by_request_id(self, request_id: str) -> LLMUsageRecordView | None:
        """按 request_id 查询单条 Usage Record。

        Args:
            request_id: Provider 请求 ID（非空字符串）。

        Returns:
            命中 → `LLMUsageRecordView`；未命中 → `None`。

        Raises:
            LLMUsageQueryInputError:   request_id 非法。
            LLMUsageRepositoryError:   DB 未配置 / 查询失败（原样透传）。
        """
        _validate_optional_text("request_id", request_id)
        row = self._repository.get_by_request_id(request_id=request_id)
        if row is None:
            return None
        return LLMUsageRecordView.from_row(row)

    def list_records(
        self,
        *,
        request_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        created_at_from: datetime | None = None,
        created_at_to: datetime | None = None,
        limit: int = DEFAULT_QUERY_LIMIT,
        offset: int = DEFAULT_QUERY_OFFSET,
    ) -> list[LLMUsageRecordView]:
        """过滤 + 分页查询 Usage Records。

        不传任何过滤条件 → 查全部（仍然有稳定排序 `created_at DESC,
        id DESC` 与分页，§十 / §二十九）。

        Returns:
            `list[LLMUsageRecordView]`（可能为空列表）。

        Raises:
            LLMUsageQueryInputError:   任何参数非法。
            LLMUsageRepositoryError:   DB 未配置 / 查询失败（原样透传）。
        """
        query_filter = LLMUsageQueryFilter(
            request_id=request_id,
            provider=provider,
            model=model,
            created_at_from=created_at_from,
            created_at_to=created_at_to,
            limit=limit,
            offset=offset,
        )
        return self.query(query_filter)

    def query(
        self,
        query_filter: LLMUsageQueryFilter | None = None,
    ) -> list[LLMUsageRecordView]:
        """按已构造（并已校验）的 Filter 查询。

        Args:
            query_filter: None → 默认 Filter（limit=50, offset=0）。

        Returns:
            `list[LLMUsageRecordView]`。

        Raises:
            LLMUsageRepositoryError: DB 未配置 / 查询失败（原样透传）。
        """
        effective_filter = (
            query_filter if query_filter is not None else LLMUsageQueryFilter()
        )
        rows = self._repository.list_records(
            request_id=effective_filter.request_id,
            provider=effective_filter.provider,
            model=effective_filter.model,
            created_at_from=effective_filter.created_at_from,
            created_at_to=effective_filter.created_at_to,
            limit=effective_filter.limit,
            offset=effective_filter.offset,
        )
        return [LLMUsageRecordView.from_row(row) for row in rows]
