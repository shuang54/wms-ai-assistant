"""LLM Usage Analytics Contract（Phase 3.10.20）。

在 Query + Aggregation 之上组合出 **Usage 统计能力**——仍然是
**纯内存、纯 Python**，不是新的数据库层：

    Query Service
        ↓
    list[LLMUsageRecordView]
        ↓
    LLMUsageAnalyticsService（本模块，组合层）
        ↓
    LLMUsageAnalyticsSnapshot
        ├── total            （LLMUsageAggregate —— 复用 3.10.19 DTO）
        ├── by_provider      （tuple[ProviderUsageAggregate, ...]）
        ├── by_model         （tuple[ModelUsageAggregate, ...]）
        └── by_provider_model（tuple[ProviderModelUsageAggregate, ...]）

职责边界（务必阅读）：

    * **Analytics 不重复实现聚合算法**：sum / group-by / 排序 /
      NULL 处理全部复用 `LLMUsageAggregationService`（§五 / §七 ~ §九）；
    * **Analytics 不接触数据库**：无 Session / SQL / Repository / ORM /
      PostgreSQL（§四）；
    * **不重复定义 DTO**：summary 就是 `LLMUsageAggregate`
      （字段完全一致，不为名字复制 DTO，§六）；本模块唯一新增的
      `LLMUsageAnalyticsSnapshot` 承载"一次快照 → 四个视图"的
      一致性语义（§十一）；
    * **Snapshot Contract（§十 / §十一）**：输入 `Iterable` 先物化为
      不可变 tuple，再基于**同一份快照**生成全部视图——generator
      只被消费一次，各视图之间不会因 iterable 二次消费而不一致；
    * **无 Cost**：只回答"用了多少"，不回答"花了多少钱"（§十五）；
    * **无时间聚合**：不做 hour / day / week / month（§十四）；
    * **错误 Contract（§十六）**：Analytics 自身输入非法
      （None / 不可迭代）→ `LLMUsageAnalyticsInputError`；
      Aggregation 已定义的异常**原样传播**，不做无意义包装；
    * **Deterministic / Immutable / 无全局状态**（§十七 / §十八）。

明确不做（§三）：

    HTTP API / FastAPI Router / Dashboard / Frontend / Billing /
    Cost Calculation / Pricing / 数据库 GROUP BY / Materialized View /
    Redis / Cache / Queue / Worker / Outbox / Kafka / Celery。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from backend.app.services.llm_usage_aggregation_service import (
    LLMUsageAggregate,
    LLMUsageAggregationService,
    ModelUsageAggregate,
    ProviderModelUsageAggregate,
    ProviderUsageAggregate,
)
from backend.app.services.llm_usage_query_service import LLMUsageRecordView

__all__ = [
    "LLMUsageAnalyticsSnapshot",
    "LLMUsageAnalyticsService",
    "LLMUsageAnalyticsError",
    "LLMUsageAnalyticsInputError",
]


# ============================================================
# 异常（§十六：不过度封装；Aggregation 异常原样传播）
# ============================================================

class LLMUsageAnalyticsError(Exception):
    """LLM Usage Analytics 失败的根异常。"""


class LLMUsageAnalyticsInputError(LLMUsageAnalyticsError):
    """Analytics 输入非法（None / 不可迭代 / 字符串）。"""


# ============================================================
# DTO（§六 / §十一：只新增"快照"语义，不复制 Aggregate 字段）
# ============================================================

@dataclass(frozen=True)
class LLMUsageAnalyticsSnapshot:
    """一次 Analytics 计算的完整结果（基于**同一份** Usage 快照）。

    一致性 Contract（§十二 / §十三）——对同一 snapshot 恒成立：

        Σ by_provider.total_requests
        = Σ by_model.total_requests
        = Σ by_provider_model.total_requests
        = total.total_requests

        Σ by_provider.prompt_tokens（已知值）
        = total.prompt_tokens
        （completion / total 同理）

    `*_known` 语义与 Phase 3.10.19 完全一致：`False` 表示该视图内
    至少一条记录该列未知——分组视图的 known 标志由该分组自己的记录
    决定，不会被其它分组污染，也不会被当成 0 解释。

    Attributes:
        total:             全量聚合（`LLMUsageAggregate`，复用 DTO）。
        by_provider:       按 provider 升序（None 分组最后）。
        by_model:          按 model 升序（None 分组最后）。
        by_provider_model: 按 (provider, model) 升序。
    """

    total: LLMUsageAggregate
    by_provider: tuple[ProviderUsageAggregate, ...] = ()
    by_model: tuple[ModelUsageAggregate, ...] = ()
    by_provider_model: tuple[ProviderModelUsageAggregate, ...] = ()


# ============================================================
# Analytics Service（组合层，无状态，纯计算）
# ============================================================

class LLMUsageAnalyticsService:
    """Usage 统计组合层：`Iterable[LLMUsageRecordView]` → Analytics。

    * 复用 `LLMUsageAggregationService`（不重复实现 sum / group-by /
      排序 / NULL 处理，§五 / §七 ~ §九）；
    * 不创建 Session / 不执行 SQL / 不访问 Repository / ORM /
      PostgreSQL（§四）；
    * 不计算 Cost（§十五）、不做时间聚合（§十四）；
    * 无状态：无缓存 / 无全局可变结果（§十七）。

    用法：

        analytics = LLMUsageAnalyticsService()
        snapshot = analytics.snapshot(records)     # 一次快照 → 四个视图
        total = analytics.summary(records)         # 或只取 summary
    """

    def __init__(
        self,
        aggregation_service: LLMUsageAggregationService | None = None,
    ) -> None:
        """构造 Analytics 服务。

        Args:
            aggregation_service: 聚合服务；None 时构造默认实例
                （复用 3.10.19 Contract）。测试可注入 Fake 以验证组合关系。
        """
        self._aggregation = (
            aggregation_service
            if aggregation_service is not None
            else LLMUsageAggregationService()
        )

    @property
    def aggregation_service(self) -> LLMUsageAggregationService:
        """底层聚合服务（只读暴露，便于测试断言组合关系）。"""
        return self._aggregation

    # ---------- 对外 Contract ----------

    def snapshot(
        self,
        records: Iterable[LLMUsageRecordView],
    ) -> LLMUsageAnalyticsSnapshot:
        """基于**同一份不可变快照**计算全部统计视图（§十 / §十一）。

        generator 只被消费一次；四个视图共享同一份 `tuple` 快照，
        因此各视图之间天然一致（§十二 / §十三）。

        Args:
            records: `Iterable[LLMUsageRecordView]`
                （list / tuple / generator / iterator 均可）。

        Returns:
            `LLMUsageAnalyticsSnapshot`（空输入 → 合法零值结果）。

        Raises:
            LLMUsageAnalyticsInputError:  records 为 None / 不可迭代 / 字符串。
            LLMUsageAggregationInputError: 含非 View 元素（原样传播，§十六）。
        """
        views = self._materialize(records)
        return LLMUsageAnalyticsSnapshot(
            total=self._aggregation.aggregate(views),
            by_provider=self._aggregation.aggregate_by_provider(views),
            by_model=self._aggregation.aggregate_by_model(views),
            by_provider_model=self._aggregation.aggregate_by_provider_model(
                views
            ),
        )

    def summary(
        self,
        records: Iterable[LLMUsageRecordView],
    ) -> LLMUsageAggregate:
        """全量 Summary（复用 `LLMUsageAggregate`，§六）。"""
        return self._aggregation.aggregate(self._materialize(records))

    def by_provider(
        self,
        records: Iterable[LLMUsageRecordView],
    ) -> tuple[ProviderUsageAggregate, ...]:
        """按 provider 分组（复用 `aggregate_by_provider`，§七）。"""
        return self._aggregation.aggregate_by_provider(
            self._materialize(records)
        )

    def by_model(
        self,
        records: Iterable[LLMUsageRecordView],
    ) -> tuple[ModelUsageAggregate, ...]:
        """按 model 分组（复用 `aggregate_by_model`，§八）。"""
        return self._aggregation.aggregate_by_model(
            self._materialize(records)
        )

    def by_provider_model(
        self,
        records: Iterable[LLMUsageRecordView],
    ) -> tuple[ProviderModelUsageAggregate, ...]:
        """按 (provider, model) 分组（复用 `aggregate_by_provider_model`，§九）。"""
        return self._aggregation.aggregate_by_provider_model(
            self._materialize(records)
        )

    # ---------- 内部 helpers ----------

    @staticmethod
    def _materialize(
        records: Iterable[LLMUsageRecordView],
    ) -> tuple[LLMUsageRecordView, ...]:
        """Iterable → 不可变快照（generator 只消费一次，§十）。

        元素级校验交给 Aggregation（保持原始异常类型，§十六）。
        """
        if records is None:
            raise LLMUsageAnalyticsInputError(
                "records 不能为 None（应为 Iterable[LLMUsageRecordView]）"
            )
        if isinstance(records, (str, bytes)):
            raise LLMUsageAnalyticsInputError(
                "records 不能是 str / bytes"
                "（应为 Iterable[LLMUsageRecordView]）"
            )
        try:
            return tuple(records)
        except TypeError as exc:
            raise LLMUsageAnalyticsInputError(
                "records 必须是 Iterable[LLMUsageRecordView]"
                f"（got {type(records).__name__}）"
            ) from exc
