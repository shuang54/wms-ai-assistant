"""Tool Execution Observer（Phase 3.11 Step 15）——最小 Observability 出口。

一次 Tool 执行的观测出口（**单向 Record 消费者**）：

    ToolExecutionService.execute()
        ├── ToolRegistry.execute(...) → ToolResult
        ├── Timing（started_at / finished_at / duration_ms）
        ↓
    ToolExecutionRecord（frozen；只描述执行事实）
        ↓
    ToolExecutionObserver.on_execution(record)      ← 可选（observer=None → 不产生）
        ↓
    return ToolResult（**原样**；观测失败绝不影响执行结果）

契约（本模块只定义 Protocol；**不是** Audit / Metrics / Tracing 系统）：

* **只接收** ``ToolExecutionRecord``——不接收 Tool arguments、
  ToolResult.data、SQL、exception object、LLM message、
  DB session、Engine、HTTP client；
* **一次执行最多一个事件**：成功与 ToolResult 失败都会产生；
  未发生执行（capability 拒绝 / Registry 抛异常 / 无 Execution Context）
  → **0 个事件**；
* **不得抛出依赖**：observer 抛出的任何异常都被执行边界隔离
  （warning 记录 + 静默丢弃），绝不会变成 ``ToolResult(success=False)``、
  不会触发 retry / fallback / Tool 重新执行；
* **不得改写**：``ToolExecutionRecord`` 是 frozen DTO，observer 无法修改
  （``FrozenInstanceError``）；
* **单向**：observer 不持有 Registry / Handler / Engine / Session / LLM /
  API 能力，也不参与 Tool 执行决策；
* **零持久化**：不落库、不外发、无 EventBus / MessageBus / Kafka /
  Redis Stream / OpenTelemetry / Plugin System / DI Framework ——
  复刻项目既有 sink 风格（``llm/observability.py::LLMObservationSink``），
  只是进程内单次同步回调。

方法名说明：项目 LLM 侧 sink 使用 ``record(observation)``；此处 payload 本身
就叫 ``ToolExecutionRecord``（``record(record)`` 语义含混），故按执行观测语义
取名为 ``on_execution(record)``。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.app.services.tool_execution_record import ToolExecutionRecord

__all__ = [
    "ToolExecutionObserver",
]


@runtime_checkable
class ToolExecutionObserver(Protocol):
    """接收 ``ToolExecutionRecord`` 的最小接口（Phase 3.11 Step 15）。

    实现方（测试 / 未来 sink）自行决定并发安全与去向；本阶段：
    进程内、不持久化、不外发。

    实现约束：
        * 只读取 ``record`` 字段（request_id / project_id / tool_call_id /
          round / tool_name / started_at / finished_at / duration_ms /
          success / error_code / error_type）；
        * 不得尝试修改 record（frozen）；
        * 抛出异常将被执行边界隔离（不影响 ToolResult）。

    Phase 3.11 Step 16：`@runtime_checkable` —— 允许用 ``isinstance`` 做
    结构化一致性检查（例如 `InMemoryToolExecutionCollector` 是否满足本
    Protocol）；执行边界仍按 duck-typing（``on_execution`` 可调用）校验。
    """

    def on_execution(self, record: ToolExecutionRecord) -> None:
        """接收一条执行记录（实现方不得依赖抛出）。"""
        ...
