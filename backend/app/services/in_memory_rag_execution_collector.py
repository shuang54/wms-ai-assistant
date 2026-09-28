"""In-Memory RAG Execution Collector（Phase 3.12 Step 43）。

进程内、**只读查询**的 ``RagExecutionObservation`` 收集器 ——
``RagExecutionObserver``（Step 43）的第一个实现：

    RagService.answer()（observer 注入时）
        ↓ RagExecutionObservation（frozen；字段白名单）
    InMemoryRagExecutionCollector.record(observation)
        ↓ 原样 append（O(1)；不复制 / 不修改 / 不排序 / 不去重 / 不聚合）
    records() / records_by_request_id()
        ↓
    tuple[RagExecutionObservation, ...]（不可变快照；无匹配 → ()）

使用方式（**显式实例**；无模块级单例 / 无全局隐藏状态 —— 与 Tool Collector
同样的纪律）：

    collector = InMemoryRagExecutionCollector()              # 默认 1000 条
    collector = InMemoryRagExecutionCollector(max_records=10)  # 显式窗口
    service = RagService(observer=collector, vector_search_service=..., ...)
    ...  # 执行（在 assistant_trace_scope 内才会产生观察记录）
    collector.records_by_request_id("req-1")

Capacity（**有限内存窗口**）：

    * ``max_records``：最多保留条数（int >= 1；bool 明确拒绝）；
      默认 ``DEFAULT_MAX_RECORDS = 1000``（与 Tool Collector 一致）；
    * 策略 = **FIFO**：写入第 N+1 条 → 淘汰最旧一条，保留最新 N 条
      （``deque(maxlen=N)``：append / eviction 均 O(1)）；
    * 淘汰 = 内存释放（不可恢复），**不是**删除持久化数据（本模块无持久化）；
    * **无 TTL** / **无后台任务** / **不自动 clear**（``clear()`` 是唯一清空入口）。

安全边界（只保存 Observation）：

    * 只接受 ``RagExecutionObservation``（其它对象 → TypeError）；
    * Observation 本身已是字段白名单（无 query / content / prompt / SQL /
      凭据）；本 Collector **不接收**这些值，也不提供任何补写入口；
    * **不持久化**：无 DB / ORM / Repository / Migration / 文件 / Redis /
      Kafka / 消息队列 / metrics·tracing backend。

职责边界：

    * **不执行 RAG**：无 Vector Search / Reranker / LLM / ContextBuilder 能力；
    * **不做统计**：无 count / 平均耗时 / 分位数（后续阶段）；
    * **不做索引 / LRU / cache**：append O(1)，eviction O(1)，查询 O(n)；
    * **无单例**：不提供 ``get_default_*`` / 模块级实例。

线程模型：与 Tool Collector / 既有 In-Memory 组件一致，用**单个**
``threading.Lock`` 保护内部 deque；append 与 FIFO 淘汰在**同一临界区**内完成。
"""
from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable

from backend.app.services.rag_execution_observation import (
    RagExecutionObservation,
)

__all__ = [
    "DEFAULT_MAX_RECORDS",
    "InMemoryRagExecutionCollector",
]

#: 默认 retention 窗口（与 ``InMemoryToolExecutionCollector`` 一致）：1000 条。
DEFAULT_MAX_RECORDS: int = 1000


def _require_non_empty_str(field_name: str, value: object) -> str:
    """查询字段校验：非 str → TypeError；空 / 纯空白 → ValueError。"""
    if not isinstance(value, str):
        raise TypeError(
            f"{field_name} 必须是 str（got {type(value).__name__}）"
        )
    if not value.strip():
        raise ValueError(f"{field_name} 不能为空或纯空白")
    return value


class InMemoryRagExecutionCollector:
    """收集 ``RagExecutionObservation`` 并提供最小只读查询。

    契约：
        * ``record(observation)`` 原样保存（``records()[0] is observation``）；
        * **Capacity**：最多保留 ``max_records`` 条；超限按 FIFO 淘汰最旧
          （保留最新 N 条）；淘汰后无法通过任何查询取回；
        * 所有查询返回 ``tuple``（不可变快照；调用方无法 append / clear）；
        * 顺序 = 写入顺序（不 sort / 不 deduplicate / 不 aggregate）；
        * ``records_by_request_id`` 严格相等（无大小写折叠 / 无前缀 / 无模糊）；
        * 无匹配 → ``()``（绝不返回 None）；
        * 无统计 / 无持久化 / 无 TTL / 无后台任务 / 无单例。

    Args:
        max_records: retention 窗口大小（默认 ``DEFAULT_MAX_RECORDS``）；
            必须为 ``int >= 1``；``bool`` **明确拒绝**。

    Raises:
        TypeError: ``max_records`` 不是 int（含 bool / float / str / None）。
        ValueError: ``max_records < 1``。
    """

    def __init__(self, *, max_records: int = DEFAULT_MAX_RECORDS) -> None:
        if isinstance(max_records, bool) or not isinstance(max_records, int):
            raise TypeError(
                "max_records 必须是 int（bool 不被接受）"
                f"（got {type(max_records).__name__}）"
            )
        if max_records < 1:
            raise ValueError(f"max_records 必须 >= 1（got {max_records}）")
        self._max_records = max_records
        self._records: deque[RagExecutionObservation] = deque(
            maxlen=max_records
        )
        self._lock = threading.Lock()

    # ---------- 只读暴露（retention 参数） ----------

    @property
    def max_records(self) -> int:
        """retention 窗口大小（构造后不可变）。"""
        return self._max_records

    # ------------------------------------------------------------
    # RagExecutionObserver（唯一写入点）
    # ------------------------------------------------------------

    def record(self, observation: RagExecutionObservation) -> None:
        """接收一条 RAG 执行观测并**原样**保存（O(1)；超限 FIFO 淘汰最旧）。

        Args:
            observation: 观测记录（frozen DTO；本方法不复制、不修改）。

        Raises:
            TypeError: ``observation`` 不是 ``RagExecutionObservation``。
        """
        if not isinstance(observation, RagExecutionObservation):
            raise TypeError(
                "InMemoryRagExecutionCollector 只接受 "
                "RagExecutionObservation"
                f"（got {type(observation).__name__}）"
            )
        with self._lock:
            self._records.append(observation)

    # ------------------------------------------------------------
    # 只读查询（全部返回不可变快照 tuple）
    # ------------------------------------------------------------

    def records(self) -> tuple[RagExecutionObservation, ...]:
        """全部观测（按写入顺序；空 → ``()``）。"""
        with self._lock:
            return tuple(self._records)

    def records_by_request_id(
        self, request_id: str
    ) -> tuple[RagExecutionObservation, ...]:
        """按 request_id 过滤（严格相等；保持写入顺序）。"""
        _require_non_empty_str("request_id", request_id)
        return self._filtered(
            lambda observation: observation.request_id == request_id
        )

    # ------------------------------------------------------------
    # 生命周期（显式清空；无 TTL / 无后台清理）
    # ------------------------------------------------------------

    def clear(self) -> None:
        """清空当前 retention window（显式调用；清空后 ``records() == ()``）。"""
        with self._lock:
            self._records.clear()

    # ------------------------------------------------------------
    # 内部（短临界区：先取快照，再在锁外过滤）
    # ------------------------------------------------------------

    def _filtered(
        self, predicate: Callable[[RagExecutionObservation], bool]
    ) -> tuple[RagExecutionObservation, ...]:
        with self._lock:
            snapshot = tuple(self._records)
        return tuple(item for item in snapshot if predicate(item))
