"""RAG Observability Query Service（Phase 3.12 Step 43）——只读查询边界。

为上层（内部测试 / 后续组合；本阶段**不接 HTTP API**）提供一个稳定、只读的
应用层读入口，避免调用方直接依赖 ``InMemoryRagExecutionCollector`` 的内部
查询方法：

    RagExecutionObservation（frozen；Runtime Only）
            ↓
    InMemoryRagExecutionCollector（存储 + Capacity + 查询原语）
            ↓ 有限 retention window
    RagObservabilityQueryService（本模块：**Read Facade**）
            ├── observations()                        （全部，写入顺序）
            ├── observations_by_request_id()          （严格相等）
            └── latest_observation_by_request_id()     （单条 | None）

依赖方向：

    （未来）API / Assistant Trace / Dashboard
        ↓
    RagObservabilityQueryService
        ↓
    InMemoryRagExecutionCollector

职责边界：

* **不持有存储**：无 list / deque / dict / 缓存 / 索引；只有 collector 引用；
* **不实现 Capacity / 锁 / 过滤 / 排序 / 去重**：全部委托 Collector；
* **只读**：所有方法都不 append / clear / 淘汰；
* **不提供 ``clear()``**：避免未来 HTTP 层意外暴露破坏性能力；
* **校验先于查询**：``request_id`` 的合法性在**触达 Collector 之前**判定，
  非法值（``None`` / ``""`` / 纯空白 / > 128）直接拒绝且**不访问 Collector**；
* **不新增持久化 / 不聚合**：无 metrics / 无 count / 无平均值（后续阶段）。

明确不做：

    HTTP API / Dashboard / Database / Redis / Kafka / Prometheus /
    OpenTelemetry / 后台线程 / TTL / settings / 环境变量。
"""
from __future__ import annotations

from backend.app.services.assistant_trace import (
    ASSISTANT_REQUEST_ID_MAX_LENGTH,
)
from backend.app.services.in_memory_rag_execution_collector import (
    InMemoryRagExecutionCollector,
)
from backend.app.services.rag_execution_observation import (
    RagExecutionObservation,
)

__all__ = [
    "RagObservabilityQueryService",
]


def _validate_request_id(request_id: object) -> str:
    """Assistant Trace ID 查询校验（**不生成 / 不修改 / 不截断**）。

    * 非 ``str``（含 ``None``）→ ``TypeError``；
    * 空 / 纯空白 → ``ValueError``；
    * 长度 > 128（与 ``ASSISTANT_REQUEST_ID_MAX_LENGTH`` 一致）→ ``ValueError``；
    * 校验通过后使用**原值**查询（不隐式 strip / 不做大小写折叠）。
    """
    if not isinstance(request_id, str):
        raise TypeError(
            "request_id 必须是 str"
            f"（当前: {type(request_id).__name__}）"
        )
    if not request_id.strip():
        raise ValueError("request_id 不能为空或纯空白")
    if len(request_id) > ASSISTANT_REQUEST_ID_MAX_LENGTH:
        raise ValueError(
            "request_id 超出长度上限 "
            f"（{len(request_id)} > {ASSISTANT_REQUEST_ID_MAX_LENGTH}）"
        )
    return request_id


class RagObservabilityQueryService:
    """RAG 运行时观测数据的**只读**查询边界（Read Facade）。

    每个方法都只是 Collector 的只读委托：

        observations()                       → collector.records()
        observations_by_request_id(id)       → collector.records_by_request_id(id)
        latest_observation_by_request_id(id) → 上述结果的最后一条 | None

    Args:
        collector: 数据来源（必须提供可调用的 ``records()`` 与
            ``records_by_request_id()``）。

    Raises:
        TypeError: collector 为 None / 缺少所需查询方法。

    Note:
        本类**故意不提供** ``record()`` / ``clear()`` 等写能力（Read-only
        Boundary）：写入只能经 ``RagExecutionObserver`` 接口由 Collector 完成。
    """

    def __init__(self, collector: InMemoryRagExecutionCollector) -> None:
        if collector is None:
            raise TypeError("collector 不能为 None")
        for method in ("records", "records_by_request_id"):
            if not callable(getattr(collector, method, None)):
                raise TypeError(
                    f"collector 必须提供可调用的 {method}()"
                    f"（got {type(collector).__name__}）"
                )
        #: 只读引用（不复制、不持有 Observation 集合）
        self._collector = collector

    # ---------- 只读暴露（便于测试断言装配关系） ----------

    @property
    def collector(self) -> InMemoryRagExecutionCollector:
        """数据来源（Collector；查询语义由它决定）。"""
        return self._collector

    # ---------- 对外只读查询 ----------

    def observations(self) -> tuple[RagExecutionObservation, ...]:
        """当前 retention window 内的全部观测（写入顺序；tuple 快照）。"""
        return self._collector.records()

    def observations_by_request_id(
        self, request_id: str
    ) -> tuple[RagExecutionObservation, ...]:
        """按 Assistant Trace ID 过滤（严格相等；无匹配 → ``()``）。

        Raises:
            TypeError:  ``request_id`` 非 str。
            ValueError: ``request_id`` 空 / 纯空白 / 超长。
        """
        validated = _validate_request_id(request_id)
        return self._collector.records_by_request_id(validated)

    def latest_observation_by_request_id(
        self, request_id: str
    ) -> RagExecutionObservation | None:
        """按 Assistant Trace ID 取**最后一条**观测（无匹配 → ``None``）。

        一次 ``/api/ai/chat`` 请求当前只产生一条 RAG 观测（单路由、无重试）；
        ``latest`` 语义为未来可能的重复执行保留确定性（取最新）。
        """
        matched = self.observations_by_request_id(request_id)
        return matched[-1] if matched else None
