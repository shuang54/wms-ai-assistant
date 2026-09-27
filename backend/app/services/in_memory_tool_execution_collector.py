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

    collector = InMemoryToolExecutionCollector()
    boundary = ToolExecutionService(registry=..., observer=collector)
    ...  # 执行（ToolChatService / 任意调用方注入该边界）
    collector.records_by_request_id("req-1")

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
    * **不做索引 / LRU / cache**：append = O(1)，查询 = O(n)（small in-memory）；
    * **不自动清理**：无 TTL / 后台线程 / 定时任务；``clear()`` 是唯一显式清空；
    * **无单例**：不提供 ``get_default_*`` / 模块级实例 —— 避免 test / request /
      project 间污染与内存泄漏，由上层显式创建并注入。

线程模型：与项目既有 In-Memory 组件（``InMemoryProjectRegistry`` /
``InMemoryToolCapabilityRegistry``）一致，用**单个** ``threading.Lock`` 保护
内部 list（模块级单例边界可能被多个线程共享）；不引入 asyncio.Queue /
线程池 / event loop。
"""
from __future__ import annotations

import threading
from collections.abc import Callable

from backend.app.services.tool_execution_record import ToolExecutionRecord

__all__ = [
    "InMemoryToolExecutionCollector",
]


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
        * 所有查询返回 ``tuple``（不可变快照；调用方无法 append / clear / pop）；
        * 顺序 = 写入顺序（不 sort / 不 deduplicate / 不 aggregate）；
        * ``records_by_project_id(None)`` 只匹配 ``record.project_id is None``
          （**不**表示"全部 project"）；
        * ``records_by_tool_name`` 严格相等（无大小写折叠 / 无前缀 / 无别名 /
          无模糊匹配）；
        * 无匹配 → ``()``（绝不返回 None）；
        * 无 count / 成功率 / 分位数等统计；
        * 无持久化、无 TTL、无后台任务、无单例。
    """

    def __init__(self) -> None:
        #: 内部可变集合（仅本类访问；对外一律返回 tuple 快照）
        self._records: list[ToolExecutionRecord] = []
        #: 与项目既有 In-Memory 组件一致的单一锁（无复杂锁体系）
        self._lock = threading.Lock()

    # ------------------------------------------------------------
    # ToolExecutionObserver（唯一写入点）
    # ------------------------------------------------------------

    def on_execution(self, record: ToolExecutionRecord) -> None:
        """接收一条执行记录并**原样**保存（O(1)）。

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
        """清空全部记录（显式调用；清空后 ``records() == ()``）。"""
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
