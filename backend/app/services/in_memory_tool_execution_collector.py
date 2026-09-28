"""In-Memory Tool Execution Collector（Phase 3.11 Step 16）。

进程内、**只读查询**的 ``ToolExecutionRecord`` 收集器 ——
``ToolExecutionObserver``（Step 15）的第一个生产可用实现：

    ToolExecutionService（Step 15：可选 observer）
        ↓ ToolExecutionRecord（frozen；Step 14）
    InMemoryToolExecutionCollector.on_execution(record)
        ↓ 原样 append（O(1)；不复制 / 不修改 / 不排序 / 不去重 / 不聚合）
    records() / records_by_request_id() / records_by_project_id() /
    records_by_tool_name()
        ↓
    tuple[ToolExecutionRecord, ...]（不可变快照；无匹配 → ()）

使用方式（**显式实例**；无模块级单例 / 无全局隐藏状态）：

    collector = InMemoryToolExecutionCollector()            # 默认 1000 条
    collector = InMemoryToolExecutionCollector(max_records=100)   # 显式窗口
    boundary = ToolExecutionService(registry=..., observer=collector)
    ...  # 执行（ToolChatService / 任意调用方注入该边界）
    collector.records_by_request_id("req-1")

Retention / Capacity（Phase 3.11 Step 20）—— **有限内存窗口**：

    * ``max_records``：最多保留的 Record 条数（int >= 1；bool 明确拒绝）；
      默认 ``DEFAULT_MAX_RECORDS = 1000``；
    * 策略 = **FIFO**：写入第 N+1 条 → 淘汰**最旧**一条，保留最新 N 条
      （append / eviction 均为 O(1)；不做 LRU / 不做按 project·tool 分桶）；
    * 淘汰事实：被淘汰的 Record **无法再通过任何查询 API 取回**
      （``records()`` / ``records_by_request_id()`` /
      ``records_by_project_id()`` / ``records_by_tool_name()`` 只作用于
      当前 retention window）；Metrics 也只看到当前窗口；
    * **Retention ≠ Persistence**：淘汰 = 内存释放（不可恢复），
      不是删除持久化数据（本模块无任何持久化）；
    * **无 TTL**：不做时间 / 时间窗口 / ``threading.Timer`` /
      ``asyncio.create_task`` / 后台清理；淘汰只由**条数**触发；
    * **不自动 clear**：``clear()`` 仍是唯一显式清空入口。

安全边界（只保存 Record）：

    * 只接受 / 只保存 ``ToolExecutionRecord``（非 Record → TypeError，不落库）；
    * **不**保存 arguments / ToolResult / ToolResult.data / SQL / LLM prompt /
      LLM response / API Key / password / DATABASE_URL / connection string /
      Authorization / Exception / traceback —— 这些值既不进入 Record
      （Step 14 契约），也不会被本 Collector 接收；
    * 不持久化：无 DB / Repository / Migration / 文件 / Redis / Kafka /
      消息队列 / metrics backend / dashboard / tracing backend。

职责边界：

    * **不执行 Tool**：无 Registry / Handler / Engine / Session / LLM 能力，
      不提供 ``execute_and_collect()`` 或任何第二条执行路径；
    * **不做统计**：无 count / success_rate / average_duration / p95 / p99
      （属 Metrics / Analytics，后续阶段）；
    * **不做索引 / LRU / cache**：append = O(1)，eviction = O(1)，
      查询 = O(n)（small in-memory；有界 retention 窗口）；
    * **不自动清理**：无 TTL / 后台线程 / 定时任务；``clear()`` 是唯一显式清空；
    * **无单例**：不提供 ``get_default_*`` / 模块级实例 —— 避免 test / request /
      project 间污染与内存泄漏，由上层显式创建并注入。

线程模型：与项目既有 In-Memory 组件（``InMemoryProjectRegistry`` /
``InMemoryToolCapabilityRegistry``）一致，用**单个** ``threading.Lock`` 保护
内部 deque（模块级单例边界可能被多个线程共享）；append 与 FIFO 淘汰在**同一
临界区**内完成（不会出现半完成状态）；不引入 asyncio.Queue / 线程池 /
event loop。
"""
from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable

from backend.app.services.tool_execution_record import ToolExecutionRecord

__all__ = [
    "DEFAULT_MAX_RECORDS",
    "InMemoryToolExecutionCollector",
]

#: 默认 retention 窗口（Phase 3.11 Step 20）：最多保留的 Record 条数。
#: 选择 1000 的依据：既有测试 / 调用方全部按默认构造（每次 append 数量 <= 200），
#: 1000 足够且把"无界增长"变成"有限窗口"；不引入全局配置层（显式构造参数优先）。
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


class InMemoryToolExecutionCollector:
    """收集 ToolExecutionRecord 并提供最小只读查询（实现 ToolExecutionObserver）。

    契约：
        * ``on_execution(record)`` 原样保存（``records()[0] is record``）；
        * **Retention（Step 20）**：最多保留 ``max_records`` 条；超限按 FIFO
          淘汰最旧（保留最新 N 条）；淘汰后无法通过任何查询取回；
        * 所有查询返回 ``tuple``（不可变快照；调用方无法 append / clear / pop）；
        * 顺序 = 写入顺序（不 sort / 不 deduplicate / 不 aggregate）；
        * ``records_by_project_id(None)`` 只匹配 ``record.project_id is None``
          （**不**表示"全部 project"）；
        * ``records_by_tool_name`` 严格相等（无大小写折叠 / 无前缀 / 无别名 /
          无模糊匹配）；
        * 无匹配 → ``()``（绝不返回 None）；
        * 无 count / 成功率 / 分位数等统计；
        * 无持久化、无 TTL、无后台任务、无单例。

    Args:
        max_records: retention 窗口大小（最多保留的 Record 条数；默认
            ``DEFAULT_MAX_RECORDS``）。必须为 ``int >= 1``；
            ``bool`` **明确拒绝**（``isinstance(True, int)`` 为 True）。

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
        #: retention 窗口（只读；构造后不可变）
        self._max_records = max_records
        #: 内部可变集合（仅本类访问；对外一律返回 tuple 快照）。
        #: ``deque(maxlen=N)``：append O(1)，超限自动从左侧淘汰最旧（FIFO，O(1)）。
        self._records: deque[ToolExecutionRecord] = deque(maxlen=max_records)
        #: 与项目既有 In-Memory 组件一致的单一锁（无复杂锁体系）
        self._lock = threading.Lock()

    # ---------- 只读暴露（retention 参数） ----------

    @property
    def max_records(self) -> int:
        """retention 窗口大小（最多保留的 Record 条数；构造后不可变）。"""
        return self._max_records

    # ------------------------------------------------------------
    # ToolExecutionObserver（唯一写入点）
    # ------------------------------------------------------------

    def on_execution(self, record: ToolExecutionRecord) -> None:
        """接收一条执行记录并**原样**保存（O(1)；超限 FIFO 淘汰最旧）。

        retention（Step 20）：写入后若超出 ``max_records``，同一临界区内
        淘汰最旧一条（``deque(maxlen)`` 语义：保留最新 N 条）；被淘汰的
        Record 只从本 Collector 释放，**不**修改 Record 本身、不做持久化。

        Args:
            record: 执行记录（Step 14 frozen DTO；本方法不复制、不修改）。

        Raises:
            TypeError: ``record`` 不是 ``ToolExecutionRecord``
                （Collector 只保存 Record —— 其它对象一律拒绝）。
        """
        if not isinstance(record, ToolExecutionRecord):
            raise TypeError(
                "InMemoryToolExecutionCollector 只接受 ToolExecutionRecord"
                f"（got {type(record).__name__}）"
            )
        # append + eviction 在同一临界区（不会出现半完成状态）
        with self._lock:
            self._records.append(record)

    # ------------------------------------------------------------
    # 只读查询（全部返回不可变快照 tuple）
    # ------------------------------------------------------------

    def records(self) -> tuple[ToolExecutionRecord, ...]:
        """全部记录（按写入顺序；空 → ``()``）。"""
        with self._lock:
            return tuple(self._records)

    def records_by_request_id(
        self, request_id: str
    ) -> tuple[ToolExecutionRecord, ...]:
        """按 request_id 过滤（一次 chat 的全部 Tool 执行；保持写入顺序）。"""
        _require_non_empty_str("request_id", request_id)
        return self._filtered(lambda record: record.request_id == request_id)

    def records_by_project_id(
        self, project_id: str | None
    ) -> tuple[ToolExecutionRecord, ...]:
        """按 project_id 过滤。

        Args:
            project_id: 项目 ID；``None`` **只**匹配 ``record.project_id is None``
                （未绑定作用域的执行），**不**表示"全部 project"。
        """
        if project_id is not None:
            _require_non_empty_str("project_id", project_id)
        return self._filtered(lambda record: record.project_id == project_id)

    def records_by_tool_name(
        self, tool_name: str
    ) -> tuple[ToolExecutionRecord, ...]:
        """按 tool_name 过滤（严格相等；无大小写折叠 / 无模糊匹配）。"""
        _require_non_empty_str("tool_name", tool_name)
        return self._filtered(lambda record: record.tool_name == tool_name)

    # ------------------------------------------------------------
    # 生命周期（显式清空；无 TTL / 无后台清理）
    # ------------------------------------------------------------

    def clear(self) -> None:
        """清空当前 retention window（显式调用；清空后 ``records() == ()``）。

        只清空内存中的 Record（不涉及任何持久化）；之后可继续 append，
        并仍受 ``max_records`` 约束；**不**自动调用（无 TTL / 无后台清理）。
        """
        with self._lock:
            self._records.clear()

    # ------------------------------------------------------------
    # 内部（短临界区：先取快照，再在锁外过滤）
    # ------------------------------------------------------------

    def _filtered(
        self, predicate: Callable[[ToolExecutionRecord], bool]
    ) -> tuple[ToolExecutionRecord, ...]:
        with self._lock:
            snapshot = tuple(self._records)
        return tuple(record for record in snapshot if predicate(record))
