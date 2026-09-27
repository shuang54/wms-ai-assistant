"""Tool Execution Metrics Read Model（Phase 3.11 Step 17）。

在 Step 14（Record）/ Step 15（Observer）/ Step 16（Collector）之上，提供
**纯内存、只读、确定性** 的 Tool 执行统计（Read Model）：

    ToolExecutionRecord（Step 14：frozen 执行元数据）
        ↓
    InMemoryToolExecutionCollector.records()（Step 16：不可变 tuple 快照）
        ↓
    ToolExecutionMetricsService.snapshot(records)      ← 本模块（纯计算）
        ↓
    ToolExecutionMetricsSnapshot（frozen；全局单快照）

只回答 5 个问题：

    执行了多少次？成功多少次？失败多少次？平均耗时多少？最大耗时多少？

Metrics 定义（§五 ~ §八）：

    total_count           = 记录数
    success_count         = count(record.success is True)
    failure_count         = count(record.success is False)
    success_count + failure_count == total_count        （恒成立）
    success_rate          = success_count / total_count（total_count == 0 → None）
    failure_rate          = failure_count / total_count（total_count == 0 → None）
    total_duration_ms     = sum(record.duration_ms)     （空 → 0.0）
    average_duration_ms   = total_duration_ms / total_count（空 → None）
    max_duration_ms       = max(record.duration_ms)     （空 → None）

空数据语义（§十五）：``total_count == 0`` 时**比率与均值 / 最大值一律为
``None``（不是 0）** ——"0 条记录"与"100 条记录 0 成功"不是同一个含义；
且绝不产生 ``ZeroDivisionError`` / ``NaN`` / ``Infinity``。

边界（本阶段严格范围）：

* **不重新测量时间**（§九）：只读取 ``record.duration_ms``（已经完成的
  执行事实），不调用 ``time.perf_counter()`` / ``datetime.now()``；
* **不依赖 Collector**（§十一）：入参是 ``Iterable[ToolExecutionRecord]``，
  不是 ``InMemoryToolExecutionCollector`` —— 未来数据来自 DB / 文件 / 任何
  来源时本层不变（Storage-agnostic Read Model）；
* **不触达 Collector 内部**（§十二）：绝不访问 ``collector._records``；
* **纯函数**（§十）：无 IO / 无日志 / 无 DB / 无 LLM / 无 Tool 执行 /
  无网络 / 无随机 / 无当前时间 / 无缓存 / 无全局状态；
* **不可变**（§十三）：Snapshot 是 ``frozen=True`` dataclass；输入
  records 不被修改（只读取字段）；
* **输入契约**（§十四）：非 ``ToolExecutionRecord`` 元素 → ``TypeError``
  （**不静默跳过 / 不转换**，否则会隐藏数据问题）；``None`` / ``str`` /
  不可迭代 → ``TypeError``；
* **无维度**（§十七 / §十八）：只有**全局单快照**——不做 ``by_tool`` /
  ``by_project`` / ``by_request`` / ``top_slowest_tools`` /
  ``failure_by_tool`` / ``success_rate_by_project``（属 Analytics，后续阶段）；
* **不含敏感信息**（§十七）：Snapshot 字段白名单 = count / rate / duration，
  **不**包含 request_id / project_id / tool_name / arguments / SQL /
  result.data / error message / exception / traceback / credentials。

明确不做（§二）：

    Database / Repository / Migration / Redis / Kafka / Prometheus /
    OpenTelemetry / Grafana / Dashboard / HTTP API / WebSocket /
    Background Worker / Scheduler / Alert / 持久化 / 分布式 Event Bus。

读数方式（显式实例调用；**无单例**）：

    collector = InMemoryToolExecutionCollector()
    boundary = ToolExecutionService(registry=..., observer=collector)
    ...                                                  # 执行（Step 15 观测出口）
    snapshot = ToolExecutionMetricsService.snapshot(collector.records())
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, fields
from typing import Final

from backend.app.services.tool_execution_record import ToolExecutionRecord

__all__ = [
    "ToolExecutionMetricsSnapshot",
    "ToolExecutionMetricsService",
]

#: Snapshot 字段白名单（防未来意外引入 identifier / 业务数据 / 敏感字段）
_ALLOWED_FIELDS: Final[frozenset[str]] = frozenset({
    "total_count",
    "success_count",
    "failure_count",
    "success_rate",
    "failure_rate",
    "total_duration_ms",
    "average_duration_ms",
    "max_duration_ms",
})


def _as_records(
    records: Iterable[ToolExecutionRecord],
) -> tuple[ToolExecutionRecord, ...]:
    """物化为不可变 tuple + 逐元素校验（§十四：不静默跳过 / 不转换）。

    Args:
        records: ``Iterable[ToolExecutionRecord]``（tuple / list / Generator /
            Collector 的 ``records()`` 返回值均可）。

    Returns:
        不可变快照 tuple（保持输入顺序；本函数不排序 / 不去重）。

    Raises:
        TypeError: ``None`` / ``str`` / ``bytes`` / 不可迭代 /
            含非 ``ToolExecutionRecord`` 元素（错误信息只含**类型名**，
            不含元素内容 —— 不泄露 Tool 数据）。
    """
    if records is None:
        raise TypeError(
            "records 不能为 None（应为 Iterable[ToolExecutionRecord]）"
        )
    if isinstance(records, (str, bytes)):
        raise TypeError(
            "records 不能是 str / bytes（应为 Iterable[ToolExecutionRecord]）"
        )
    try:
        items = tuple(records)
    except TypeError as exc:
        raise TypeError(
            "records 必须是 Iterable[ToolExecutionRecord]"
            f"（got {type(records).__name__}）"
        ) from exc
    for index, item in enumerate(items):
        if not isinstance(item, ToolExecutionRecord):
            raise TypeError(
                f"records[{index}] 必须是 ToolExecutionRecord"
                f"（got {type(item).__name__}）—— 不静默跳过 / 不转换"
            )
    return items


@dataclass(frozen=True)
class ToolExecutionMetricsSnapshot:
    """一次 Metrics 计算的**不可变**结果（全局单快照；§四 / §十三）。

    Arithmetic Contract（§二十）——对任意由本模块产生的 snapshot 恒成立：

        success_count + failure_count == total_count
        total_count == 0  → success_rate / failure_rate / average_duration_ms /
                            max_duration_ms 均为 None（不是 0）
        total_count > 0   → 0 <= success_rate, failure_rate <= 1
                            且 success_rate + failure_rate == 1（浮点误差内）
                            average_duration_ms = total_duration_ms / total_count

    Attributes:
        total_count:        记录数（Record 数量）。
        success_count:      ``count(record.success is True)``。
        failure_count:      ``count(record.success is False)``。
        success_rate:       成功率（``success_count / total_count``）；
                            空数据集 → ``None``（**不是** 0）。
        failure_rate:       失败率（``failure_count / total_count``）；
                            空数据集 → ``None``（**不是** 0）。
        total_duration_ms:  ``sum(record.duration_ms)``（空 → ``0.0``）。
        average_duration_ms: ``total_duration_ms / total_count``（空 → ``None``）。
        max_duration_ms:    ``max(record.duration_ms)``（空 → ``None``）。

    Raises:
        ValueError: 字段非法（负数 / 非数值 / NaN / Infinity / 比率越界 /
            ``success_count + failure_count != total_count`` /
            空数据集却给出比率或均值 / 非空数据集却缺失比率或均值）。
    """

    total_count: int
    success_count: int
    failure_count: int
    success_rate: float | None
    failure_rate: float | None
    total_duration_ms: float
    average_duration_ms: float | None
    max_duration_ms: float | None

    def __post_init__(self) -> None:
        self._validate_counts()
        self._validate_arithmetic()
        self._validate_rates()
        self._validate_durations()

    # ------------------------------------------------------------
    # 校验（不发明语义：只锁 §五 ~ §八 / §十五 / §二十 的既有契约）
    # ------------------------------------------------------------

    def _validate_counts(self) -> None:
        for field_name in ("total_count", "success_count", "failure_count"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(
                    f"{field_name} 必须是 int"
                    f"（got {type(value).__name__}）"
                )
            if value < 0:
                raise ValueError(
                    f"{field_name} 不允许负数（got {value}）"
                )

    def _validate_arithmetic(self) -> None:
        if self.success_count + self.failure_count != self.total_count:
            raise ValueError(
                "success_count + failure_count 必须等于 total_count"
                f"（{self.success_count} + {self.failure_count}"
                f" != {self.total_count}）"
            )

    def _validate_rates(self) -> None:
        if self.total_count == 0:
            for field_name in ("success_rate", "failure_rate"):
                if getattr(self, field_name) is not None:
                    raise ValueError(
                        f"空数据集（total_count=0）时 {field_name} 必须为 None"
                        "（0 条记录 ≠ 0% 成功率）"
                    )
            return
        for field_name in ("success_rate", "failure_rate"):
            value = getattr(self, field_name)
            if value is None:
                raise ValueError(
                    f"total_count > 0 时 {field_name} 不能为 None"
                )
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"{field_name} 必须是数值或 None"
                    f"（got {type(value).__name__}）"
                )
            if math.isnan(value) or math.isinf(value):
                raise ValueError(f"{field_name} 不允许 NaN / Infinity")
            if not 0.0 <= value <= 1.0:
                raise ValueError(
                    f"{field_name} 必须落在 [0, 1]（got {value}）"
                )
        total_rate = self.success_rate + self.failure_rate
        if not math.isclose(total_rate, 1.0, rel_tol=1e-9, abs_tol=1e-12):
            raise ValueError(
                "success_rate + failure_rate 必须等于 1"
                f"（got {total_rate}）"
            )

    def _validate_durations(self) -> None:
        total = self.total_duration_ms
        if isinstance(total, bool) or not isinstance(total, (int, float)):
            raise ValueError(
                "total_duration_ms 必须是数值"
                f"（got {type(total).__name__}）"
            )
        if math.isnan(total) or math.isinf(total) or total < 0:
            raise ValueError(
                f"total_duration_ms 必须是有限的非负数（got {total}）"
            )
        for field_name in ("average_duration_ms", "max_duration_ms"):
            value = getattr(self, field_name)
            if self.total_count == 0:
                if value is not None:
                    raise ValueError(
                        f"空数据集（total_count=0）时 {field_name} 必须为 None"
                    )
                continue
            if value is None:
                raise ValueError(
                    f"total_count > 0 时 {field_name} 不能为 None"
                )
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"{field_name} 必须是数值或 None"
                    f"（got {type(value).__name__}）"
                )
            if math.isnan(value) or math.isinf(value) or value < 0:
                raise ValueError(
                    f"{field_name} 必须是有限的非负数（got {value}）"
                )
        if self.total_count == 0 and total != 0.0:
            raise ValueError(
                "空数据集（total_count=0）时 total_duration_ms 必须为 0"
                f"（got {total}）"
            )

    # ------------------------------------------------------------
    # 自检（防未来意外扩展维度 / 引入敏感字段）
    # ------------------------------------------------------------

    def assert_field_whitelist(self) -> None:
        """字段白名单自检（测试调用；不得引入 request_id / tool_name 等）。"""
        actual = {f.name for f in fields(self)}
        if actual != _ALLOWED_FIELDS:
            raise ValueError(
                "ToolExecutionMetricsSnapshot 字段白名单被破坏: "
                f"{sorted(actual)}（允许: {sorted(_ALLOWED_FIELDS)}）"
            )


class ToolExecutionMetricsService:
    """Tool 执行统计（Read Model）：``Iterable[ToolExecutionRecord]`` → Snapshot。

    契约（§十 ~ §十二 / §二十二）：

    * **Pure**：无 IO / 无日志 / 无 DB / 无 LLM / 无 Tool 执行 / 无网络；
    * **Deterministic**（§十九）：同一输入 → 同一 Snapshot（不依赖当前时间 /
      随机数 / set 迭代顺序 / 网络 / DB）；
    * **无状态**：``snapshot`` 是 ``@staticmethod``（无需实例状态；无进程级
      缓存 / 无全局单例）；
    * **只读**：不修改入参（Record 本身 frozen；本 Service 不调用任何写方法）；
    * **Storage-agnostic**（§十一）：只接受 Records —— 不接收 Collector /
      Session / Repository（绝不访问 ``collector._records``）；
    * **不影响执行链**（§二十五）：Metrics 计算失败不会（也不能）导致 Tool
      失败 —— 生产执行边界不依赖本模块。

    用法：

        >>> records = collector.records()                     # tuple 快照
        >>> snapshot = ToolExecutionMetricsService.snapshot(records)
        >>> snapshot.total_count
        3
    """

    @staticmethod
    def snapshot(
        records: Iterable[ToolExecutionRecord],
    ) -> ToolExecutionMetricsSnapshot:
        """基于一份 Records 快照计算全局 Metrics（纯函数）。

        Args:
            records: ``Iterable[ToolExecutionRecord]``（tuple / list /
                Generator / Collector 的 ``records()`` 返回值均可；
                Generator 只消费一次）。

        Returns:
            ``ToolExecutionMetricsSnapshot``（frozen；空输入 → 合法零值结果，
            比率 / 均值 / 最大值为 ``None``）。

        Raises:
            TypeError: records 为 ``None`` / ``str`` / ``bytes`` / 不可迭代 /
                含非 ``ToolExecutionRecord`` 元素。
        """
        items = _as_records(records)
        total_count = len(items)
        success_count = sum(1 for record in items if record.success)
        failure_count = total_count - success_count
        #: 空数据集 → 0.0（不是 None）；单条 duration_ms 允许 int（Record 契约）
        total_duration_ms = float(sum(record.duration_ms for record in items))
        return ToolExecutionMetricsSnapshot(
            total_count=total_count,
            success_count=success_count,
            failure_count=failure_count,
            # 空数据集 → None（不是 0）：0 条记录 ≠ 0% 成功率（§六）
            success_rate=(
                success_count / total_count if total_count else None
            ),
            failure_rate=(
                failure_count / total_count if total_count else None
            ),
            total_duration_ms=total_duration_ms,
            average_duration_ms=(
                total_duration_ms / total_count if total_count else None
            ),
            max_duration_ms=(
                float(max(record.duration_ms for record in items))
                if items
                else None
            ),
        )
