"""Tool Observability Query Service（Phase 3.11 Step 21）——只读查询边界。

为未来 HTTP API / Dashboard / Persistence 提供一个**稳定、只读**的应用层
读入口，避免上层直接依赖 ``InMemoryToolExecutionCollector`` 的内部查询方法：

    ToolExecutionRecord
            ↓
    InMemoryToolExecutionCollector（Record 存储 + Retention + 查询原语）
            ↓
    有限 Retention Window
            ↓
    ToolObservabilityQueryService（本模块：**Read Facade**）
            ├── records()                       （内部 Execution Record 查询）
            ├── records_by_request_id() / records_by_project_id() /
            │   records_by_tool_name()
            ├── snapshots()                     （Step 22：对外 Read Model 查询）
            ├── snapshots_by_request_id() / snapshots_by_project_id() /
            │   snapshots_by_tool_name()
            └── metrics() → ToolExecutionMetricsSnapshot（聚合 Read Model）

依赖方向（§十三）：

    API / Dashboard（未来）
        ↓
    ToolObservabilityQueryService
        ↓
    InMemoryToolExecutionCollector

职责边界（务必阅读）：

* **不持有 Record storage**：无 ``list`` / ``deque`` / ``dict`` / 缓存 /
  索引；内部只有 ``collector`` 与 ``metrics_service`` 两个引用（Step 6 纪律）；
* **不实现 Retention / FIFO / 淘汰 / 锁 / 过滤 / 排序 / 去重**：
  全部委托 Collector（Collector 仍是唯一存储与 retention 权威）；
* **不实现 metrics arithmetic**：``metrics()`` 只调用
  ``ToolExecutionMetricsService.snapshot(collector.records())``（零复制算法）；
* **只读**：所有方法都不 append / clear / evict / sort / deduplicate；
* **不提供 ``clear()``**（§十二）：避免未来 HTTP 层意外暴露
  ``DELETE /observability/records`` 之类的破坏性能力；Collector 的
  ``clear()`` 仍是显式内部生命周期操作；
* **不新增抽象**（§十四）：不定义 Read Repository / Port / Store Protocol；
  未来真正需要 DB / Redis 时再抽象；
* **不暴露敏感信息**（§十一）：只返回 ``ToolExecutionRecord``（Step 14 字段
  白名单）与 ``ToolExecutionMetricsSnapshot``（Step 17 字段白名单）；
  不新增 Tool arguments / LLM prompt / SQL / 凭据 / DB 对象。

明确不做（§三）：

    HTTP API / Dashboard / WebSocket / Database / Redis / Kafka / RabbitMQ /
    Prometheus / OpenTelemetry / Audit persistence / Event Bus /
    后台线程 / 定时任务 / TTL / settings / 环境变量。

用法（测试 / 未来装配显式构造；本阶段不接 API、不做单例）：

    collector = InMemoryToolExecutionCollector()
    query = ToolObservabilityQueryService(collector)
    query.records()
    query.metrics()
"""
from __future__ import annotations

from typing import Any

from backend.app.services.in_memory_tool_execution_collector import (
    InMemoryToolExecutionCollector,
)
from backend.app.services.tool_execution_metrics_service import (
    ToolExecutionMetricsService,
    ToolExecutionMetricsSnapshot,
)
from backend.app.services.tool_execution_record import ToolExecutionRecord
from backend.app.services.tool_observability_snapshot import (
    ToolExecutionSnapshot,
)

__all__ = [
    "ToolObservabilityQueryService",
]


class ToolObservabilityQueryService:
    """Tool 观测数据的**只读**查询边界（Read Facade）。

    每个方法都只是 Collector / Metrics Service 的只读委托：

        records()                → collector.records()
        records_by_request_id()  → collector.records_by_request_id(...)
        records_by_project_id()  → collector.records_by_project_id(...)
        records_by_tool_name()   → collector.records_by_tool_name(...)
        metrics()                → metrics_service.snapshot(collector.records())

    Args:
        collector:      数据来源（当前阶段的实际 Read Model = InMemory
                        Collector；必须提供可调用的 ``records()``）。
                        查询语义（retention window / 过滤 / 校验）全部由它决定。
        metrics_service: 统计服务（默认 ``ToolExecutionMetricsService``；
                        任何提供 ``snapshot(records)`` 的对象均可注入，
                        便于测试替换为 Fake —— 见 §十五）。

    Raises:
        TypeError: collector 为 None / 缺少 ``records()``；
                   metrics_service 为 None / 缺少 ``snapshot()``。

    Note:
        本类**故意不提供** ``clear()`` / ``on_execution()`` 等写能力
        （Read-only Boundary，C23.9）。
    """

    def __init__(
        self,
        collector: InMemoryToolExecutionCollector,
        *,
        metrics_service: Any = ToolExecutionMetricsService,
    ) -> None:
        if collector is None:
            raise TypeError("collector 不能为 None")
        if not callable(getattr(collector, "records", None)):
            raise TypeError(
                "collector 必须提供可调用的 records()"
                f"（got {type(collector).__name__}）"
            )
        if metrics_service is None or not callable(
            getattr(metrics_service, "snapshot", None)
        ):
            raise TypeError(
                "metrics_service 必须提供可调用的 snapshot()"
                f"（got {type(metrics_service).__name__}）"
            )
        #: 只读引用（不复制、不持有 Record 集合）
        self._collector = collector
        self._metrics_service = metrics_service

    # ---------- 只读暴露（便于测试断言装配关系） ----------

    @property
    def collector(self) -> InMemoryToolExecutionCollector:
        """数据来源（Collector；查询语义由它决定）。"""
        return self._collector

    @property
    def metrics_service(self) -> Any:
        """统计服务（默认 ``ToolExecutionMetricsService``）。"""
        return self._metrics_service

    # ---------- 对外只读查询（§五） ----------

    def records(self) -> tuple[ToolExecutionRecord, ...]:
        """当前 retention window 内的全部 Record（写入顺序；tuple 快照）。"""
        return self._collector.records()

    def records_by_request_id(
        self, request_id: str
    ) -> tuple[ToolExecutionRecord, ...]:
        """按 request_id 过滤（只作用于当前 retention window）。"""
        return self._collector.records_by_request_id(request_id)

    def records_by_project_id(
        self, project_id: str | None
    ) -> tuple[ToolExecutionRecord, ...]:
        """按 project_id 过滤（``None`` **只**匹配 ``project_id is None``）。"""
        return self._collector.records_by_project_id(project_id)

    def records_by_tool_name(
        self, tool_name: str
    ) -> tuple[ToolExecutionRecord, ...]:
        """按 tool_name 过滤（严格相等）。"""
        return self._collector.records_by_tool_name(tool_name)

    # ---------- 对外 Read Model（Phase 3.11 Step 22） ----------

    def snapshots(self) -> tuple[ToolExecutionSnapshot, ...]:
        """当前 retention window 全部记录 → ``ToolExecutionSnapshot`` 元组。

        语义与 ``records()`` 完全一致（同一窗口、同一顺序），只是把内部
        Execution Record 转成**对外** Read Model（字段显式映射，不持有
        Record）。淘汰记录同样不可恢复（C24.11）。
        """
        return tuple(
            ToolExecutionSnapshot.from_record(record)
            for record in self._collector.records()
        )

    def snapshots_by_request_id(
        self, request_id: str
    ) -> tuple[ToolExecutionSnapshot, ...]:
        """按 request_id 过滤后转 Snapshot（只作用于当前 retention window）。"""
        return tuple(
            ToolExecutionSnapshot.from_record(record)
            for record in self._collector.records_by_request_id(request_id)
        )

    def snapshots_by_project_id(
        self, project_id: str | None
    ) -> tuple[ToolExecutionSnapshot, ...]:
        """按 project_id 过滤后转 Snapshot（``None`` 只匹配 ``None``）。"""
        return tuple(
            ToolExecutionSnapshot.from_record(record)
            for record in self._collector.records_by_project_id(project_id)
        )

    def snapshots_by_tool_name(
        self, tool_name: str
    ) -> tuple[ToolExecutionSnapshot, ...]:
        """按 tool_name 过滤后转 Snapshot（严格相等）。"""
        return tuple(
            ToolExecutionSnapshot.from_record(record)
            for record in self._collector.records_by_tool_name(tool_name)
        )

    def metrics(self) -> ToolExecutionMetricsSnapshot:
        """当前 retention window 的 Metrics 快照（复用既有 Metrics Service）。

        统计语义（空数据集 / 比率 / 时长）与
        ``ToolExecutionMetricsService.snapshot(collector.records())``
        **完全一致**：已淘汰的 Record 不参与统计（C23.7 / C23.15）。
        """
        return self._metrics_service.snapshot(self._collector.records())
