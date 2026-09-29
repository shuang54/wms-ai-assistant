"""Composite RAG Execution Observer（Phase 3.12 Step 46）。

最小 fan-out：把同一条 ``RagExecutionObservation`` 依次交给多个
``RagExecutionObserver``（互相独立）—— 严格复刻
``CompositeToolExecutionObserver`` 的语义：

    RagService._finish_observation()
        ↓ observer.record(observation)
    CompositeRagExecutionObserver
        ├── InMemoryRagExecutionCollector（Runtime 视图）
        └── RagExecutionPersistenceAdapter（PostgreSQL）
            （两个 observer 都会被执行；顺序 = 装配顺序）

契约：

    * 实现 ``RagExecutionObserver``（``record(observation)``）；
    * **子 observer 之间互相隔离**：任一个抛出异常只记 warning，
      后续 observer **仍会被调用**（不阻断 fan-out）——
      即"Persistence 失败不影响 Memory 观测 / Memory 失败不跳过持久化"；
    * 不重排 / 不去重 / 不缓存 / 不改写 observation；
    * 不创建 / 不持有 Collector 或 Adapter（由 Composition Root 装配）；
    * 无 retry / queue / 后台线程。
"""
from __future__ import annotations

import logging

from backend.app.services.rag_execution_observation import (
    RagExecutionObservation,
    RagExecutionObserver,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CompositeRagExecutionObserver",
]


def _safe_log_context(observation: object) -> dict[str, object]:
    """日志上下文白名单（只取允许字段；取值失败不影响日志）。"""
    context: dict[str, object] = {}
    for name in ("request_id",):
        try:
            value = getattr(observation, name, None)
        except Exception:  # noqa: BLE001 —— 取值失败不得引发新异常
            value = None
        if value is not None:
            context[name] = value
    return context


class CompositeRagExecutionObserver:
    """把同一条 Observation 交给多个子 observer（互相隔离）。"""

    def __init__(self, *observers: RagExecutionObserver) -> None:
        """构造 composite。

        Args:
            *observers: 子 observer（各自需提供可调用的 ``record()``）。

        Raises:
            TypeError: 任一 observer 缺少可调用的 ``record()``。
        """
        for observer in observers:
            if not callable(getattr(observer, "record", None)):
                raise TypeError(
                    "observer 必须提供可调用的 record(observation)"
                    f"（got {type(observer).__name__}）"
                )
        self._observers: tuple[RagExecutionObserver, ...] = tuple(observers)

    # ---------- 只读暴露 ----------

    @property
    def observers(self) -> tuple[RagExecutionObserver, ...]:
        """子 observer（装配顺序；只读 tuple）。"""
        return self._observers

    # ---------- Observer Contract ----------

    def record(self, observation: RagExecutionObservation) -> None:
        """依次调用每个子 observer；单个失败不阻断后续（已隔离）。"""
        for observer in self._observers:
            try:
                observer.record(observation)
            except Exception as exc:  # noqa: BLE001 —— 子 observer 失败隔离
                logger.warning(
                    "RAG execution observer failed（已被隔离）",
                    extra={
                        **_safe_log_context(observation),
                        "observer_type": type(observer).__name__,
                        "error_type": type(exc).__name__,
                    },
                )
