"""LLM Usage Aggregation Contract（Phase 3.10.19）。

把 `LLMUsageRecordView` 列表聚合成稳定的统计 DTO——**纯内存、纯 Python**：

    Usage Query
        ↓
    list[LLMUsageRecordView]
        ↓
    LLMUsageAggregationService（本模块，纯函数式聚合）
        ↓
    LLMUsageAggregate / 分组结果

边界（务必阅读）：

    * **Pure / Deterministic / In-memory**：无 IO / 无网络 / 无 DB /
      无 SQL / 无 Session / 无 Repository——本模块**不知道**
      PostgreSQL 的存在（§四）；
    * **不知道 Cost**：无 price / currency / cost 字段，不做
      `Usage × Price`（Billing 属于后续独立阶段，§十四）；
    * **不修改 Usage Contract**：`prompt_tokens / completion_tokens /
      total_tokens` 是**三个独立观测值**——聚合分别求和，
      绝不用 `prompt + completion` 重算 `total`（§十三）；
    * **NULL ≠ 0**：token 为 `None` 表示"未知 / 不可用"（Phase 3.10.14
      Persistence Contract），聚合时**不计入求和**、也绝不当作 0；
      未知信息通过 `*_known` 标志显式保留（§七）；
    * **不去重**：两条完全相同的记录就是两次 Usage（§十五.9）；
    * **输出稳定**：分组结果按 key 升序排序（`None` 分组排在最后），
      与输入顺序无关（§十一）。

明确不做（§二十一）：

    GenericAggregationEngine / GenericGroupByFramework /
    SQLAggregationBuilder / AnalyticsFramework / MetricsFramework /
    ReportFramework / HTTP API / Dashboard。
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Iterable

from backend.app.services.llm_usage_query_service import LLMUsageRecordView

__all__ = [
    "LLMUsageAggregate",
    "ProviderUsageAggregate",
    "ModelUsageAggregate",
    "ProviderModelUsageAggregate",
    "LLMUsageAggregationService",
    "LLMUsageAggregationError",
    "LLMUsageAggregationInputError",
    "LLM_USAGE_AGGREGATE_FIELDS",
]


# ============================================================
# 异常（沿用项目 XxxError + InputError 模式）
# ============================================================

class LLMUsageAggregationError(Exception):
    """Usage 聚合失败的根异常。"""


class LLMUsageAggregationInputError(LLMUsageAggregationError):
    """聚合输入非法（不是 LLMUsageRecordView）。"""


# ============================================================
# DTO（frozen —— 项目 DTO 通用约定）
# ============================================================

@dataclass(frozen=True)
class LLMUsageAggregate:
    """一次聚合的 Usage 统计（Phase 3.10.19）。

    NULL 语义（§七 —— 关键 Contract）：

        * `prompt_tokens` 等 token 字段 = **已知值的和**
          （`None` 不计入，也绝不当作 0）；
        * `*_known = True`  → 所有记录该列均已知，求和覆盖全部记录；
        * `*_known = False` → 至少一条记录该列未知（NULL），
          求和只是"已知部分"——未知信息没有被丢失；
        * 空输入：`total_requests = 0`、token 和为 0、`*_known = True`
          （没有任何未知值，空集上的求和合法）。

    三个 token 字段是**独立观测值**（§十三）：`total_tokens` 直接来自
    Provider 原值求和，绝不重算为 `prompt + completion`。

    不包含：cost / price / currency（§十四）/ user / project /
    tenant / day / month 等分组维度（表里没有这些字段）。
    """

    total_requests: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    prompt_tokens_known: bool
    completion_tokens_known: bool
    total_tokens_known: bool


@dataclass(frozen=True)
class ProviderUsageAggregate:
    """按 provider 分组的聚合结果（`provider=None` 分组原样保留）。"""

    provider: str | None
    aggregate: LLMUsageAggregate


@dataclass(frozen=True)
class ModelUsageAggregate:
    """按 model 分组的聚合结果（`model=None` 分组原样保留）。"""

    model: str | None
    aggregate: LLMUsageAggregate


@dataclass(frozen=True)
class ProviderModelUsageAggregate:
    """按 (provider, model) 组合键分组的聚合结果。"""

    provider: str | None
    model: str | None
    aggregate: LLMUsageAggregate


#: DTO 字段集合（测试 / 接入点校验用）。
LLM_USAGE_AGGREGATE_FIELDS: frozenset[str] = frozenset(
    field.name for field in fields(LLMUsageAggregate)
)


# ============================================================
# 内部 helpers（纯函数）
# ============================================================

def _as_views(
    records: Iterable[LLMUsageRecordView],
) -> tuple[LLMUsageRecordView, ...]:
    """收集并校验输入（聚合只接受 LLMUsageRecordView）。"""
    views: list[LLMUsageRecordView] = []
    for record in records:
        if not isinstance(record, LLMUsageRecordView):
            raise LLMUsageAggregationInputError(
                "records 必须是 LLMUsageRecordView"
                f"（got {type(record).__name__}）"
            )
        views.append(record)
    return tuple(views)


def _sum_column(
    views: tuple[LLMUsageRecordView, ...],
    column: str,
) -> tuple[int, bool]:
    """对一列求和：None 不计入也不当 0；返回 (已知值之和, 是否全部已知)。"""
    total = 0
    all_known = True
    for view in views:
        value = getattr(view, column)
        if value is None:
            all_known = False
            continue
        total += value
    return total, all_known


def _aggregate_views(
    views: tuple[LLMUsageRecordView, ...],
) -> LLMUsageAggregate:
    """对一组 view 做基础聚合（三个 token 列独立求和）。"""
    prompt, prompt_known = _sum_column(views, "prompt_tokens")
    completion, completion_known = _sum_column(views, "completion_tokens")
    total, total_known = _sum_column(views, "total_tokens")
    return LLMUsageAggregate(
        total_requests=len(views),
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        prompt_tokens_known=prompt_known,
        completion_tokens_known=completion_known,
        total_tokens_known=total_known,
    )


def _group_views(
    views: tuple[LLMUsageRecordView, ...],
    key_fields: tuple[str, ...],
) -> dict[tuple[str | None, ...], tuple[LLMUsageRecordView, ...]]:
    """按一个或多个字段分组（内部 dict；NULL 作为独立分组保留）。"""
    groups: dict[tuple[str | None, ...], list[LLMUsageRecordView]] = {}
    for view in views:
        key = tuple(getattr(view, name) for name in key_fields)
        groups.setdefault(key, []).append(view)
    return {key: tuple(items) for key, items in groups.items()}


def _sort_key(key: tuple[str | None, ...]) -> tuple[tuple[bool, str], ...]:
    """确定性排序键：每个维度"值升序 + `None` 排最后"（§十一）。

    注意必须同时携带值本身作为 tiebreaker——否则全部非 NULL 的 key
    排序键相同，顺序会退化为 dict insertion order（依赖输入顺序）。
    """
    return tuple(
        (value is None, "" if value is None else value)
        for value in key
    )


# ============================================================
# Aggregation Service（无状态，纯计算）
# ============================================================

class LLMUsageAggregationService:
    """`list[LLMUsageRecordView]` → 聚合 DTO（纯内存，只读）。

    * 不创建 Session / 不调用 Repository / 不执行 SQL /
      不知道 PostgreSQL（§四）；
    * 不计算 Cost（§十四）；
    * 同一输入恒得同一输出（输入顺序无关，§十一）。

    用法：

        service = LLMUsageAggregationService()
        total = service.aggregate(records)
        by_provider = service.aggregate_by_provider(records)
    """

    def aggregate(
        self,
        records: Iterable[LLMUsageRecordView],
    ) -> LLMUsageAggregate:
        """基础聚合：total_requests + 三个独立 token 列求和（§六）。

        Args:
            records: `Iterable[LLMUsageRecordView]`（list / tuple /
                generator 均可）。

        Returns:
            `LLMUsageAggregate`（空输入 → 零值合法结果，§十二）。

        Raises:
            LLMUsageAggregationInputError: 含非 View 元素。
        """
        return _aggregate_views(_as_views(records))

    def aggregate_by_provider(
        self,
        records: Iterable[LLMUsageRecordView],
    ) -> tuple[ProviderUsageAggregate, ...]:
        """按 provider 分组（§八）。

        Returns:
            按 `provider` 升序的 tuple；`provider=None` 的记录构成
            独立分组并排在最后。
        """
        views = _as_views(records)
        groups = _group_views(views, ("provider",))
        return tuple(
            ProviderUsageAggregate(
                provider=key[0],
                aggregate=_aggregate_views(items),
            )
            for key, items in sorted(
                groups.items(), key=lambda item: _sort_key(item[0])
            )
        )

    def aggregate_by_model(
        self,
        records: Iterable[LLMUsageRecordView],
    ) -> tuple[ModelUsageAggregate, ...]:
        """按 model 分组（§九）。

        Returns:
            按 `model` 升序的 tuple；`model=None` 的记录构成独立分组
            并排在最后。
        """
        views = _as_views(records)
        groups = _group_views(views, ("model",))
        return tuple(
            ModelUsageAggregate(
                model=key[0],
                aggregate=_aggregate_views(items),
            )
            for key, items in sorted(
                groups.items(), key=lambda item: _sort_key(item[0])
            )
        )

    def aggregate_by_provider_model(
        self,
        records: Iterable[LLMUsageRecordView],
    ) -> tuple[ProviderModelUsageAggregate, ...]:
        """按 (provider, model) 组合键分组（§十）。

        Returns:
            按 `(provider, model)` 升序的 tuple；任一维度为 None 的
            分组在该维度上排在最后（None 只影响自己所在的维度）。
        """
        views = _as_views(records)
        groups = _group_views(views, ("provider", "model"))
        return tuple(
            ProviderModelUsageAggregate(
                provider=key[0],
                model=key[1],
                aggregate=_aggregate_views(items),
            )
            for key, items in sorted(
                groups.items(), key=lambda item: _sort_key(item[0])
            )
        )
