"""Tool Execution Persistence Adapter / Composite Observer（Phase 3.11 Step 28）。

把 ``ToolExecutionObserver`` 端口接到 Persistence 链路：

    ToolExecutionService.execute()
        ↓ ToolExecutionRecord（frozen）
    ToolExecutionObserver（Step 15 端口，**未修改**）
        ├── InMemoryToolExecutionCollector      （runtime store，不变）
        └── ToolExecutionPersistenceAdapter     （本模块）
                ↓
            ToolExecutionPersistenceService     （application operation）
                ↓
            ToolExecutionRepository             （database access）
                ↓
            ai_ops.tool_execution_record

Adapter 职责（**只做一件事**）：

    record → persistence_service.persist(record)

    * 接口就是 Observer 协议：``on_execution(record) -> None``
      （不新增 save / persist / write 等第二套接口）；
    * **不**创建 Session / **不** import SQLAlchemy / **不** import ORM
      Model / **不**调用 Repository / **不**做 SQL / **不**做 JSON /
      **不**做业务校验 / **不**做 aggregation / metrics；
    * 失败隔离（Step 15 契约的延伸，本模块**必须**保证）：

          persist 抛出的任何异常 → warning 日志 → **静默丢弃**
          ❌ 不传播（绝不变成 ToolResult(success=False)）
          ❌ 不 retry / sleep / backoff / fallback / queue / 重跑 Tool
          ❌ 不把 exception message / traceback 写入 Record

    * 日志白名单（§十一）：tool_name / request_id / round / project_id /
      error_type（**异常类名**）；不使用 ``logger.exception``（不打印
      traceback），不输出 arguments / result / SQL / 连接串 / 凭据。

CompositeToolExecutionObserver（最小 fan-out）：

    on_execution(record)
        ├── 依次调用每个子 observer
        └── **单个子 observer 失败不阻断后续**（隔离在本层完成）

    * 与 Adapter 一样是 Observer 协议实现（可再嵌套 / 单独注入）；
    * 不引入 Observer Framework / 事件总线 / 中间件体系。
"""
from __future__ import annotations

import logging

from backend.app.services.tool_execution_observer import ToolExecutionObserver
from backend.app.services.tool_execution_persistence_service import (
    ToolExecutionPersistenceService,
)
from backend.app.services.tool_execution_record import ToolExecutionRecord

logger = logging.getLogger(__name__)

__all__ = [
    "ToolExecutionPersistenceAdapter",
    "CompositeToolExecutionObserver",
]


def _safe_log_context(record: object) -> dict[str, object]:
    """日志上下文白名单（只取允许字段；对象异常也不影响日志）。"""
    context: dict[str, object] = {}
    for name in ("tool_name", "request_id", "round", "project_id"):
        try:
            value = getattr(record, name, None)
        except Exception:  # noqa: BLE001 —— 取值失败不得引发新异常
            value = None
        if value is not None:
            context[name] = value
    return context


class ToolExecutionPersistenceAdapter:
    """``ToolExecutionObserver`` 实现：把 Record 交给 Persistence Service。"""

    def __init__(
        self,
        persistence_service: ToolExecutionPersistenceService | None = None,
        *,
        repository: object | None = None,
    ) -> None:
        """构造 Adapter。

        Args:
            persistence_service: 持久化服务；None 时按 ``repository`` 构造
                （默认 ``ToolExecutionPersistenceService()``）。
            repository: 可选仓储（透传给默认 service；便于测试 / 装配）。
        """
        self._service = (
            persistence_service
            if persistence_service is not None
            else ToolExecutionPersistenceService(repository=repository)  # type: ignore[arg-type]
        )

    # ---------- 只读暴露（便于装配断言） ----------

    @property
    def persistence_service(self) -> ToolExecutionPersistenceService:
        """底层持久化服务（application persistence operation）。"""
        return self._service

    # ---------- Observer Contract ----------

    def on_execution(self, record: ToolExecutionRecord) -> None:
        """接收一条执行记录并尝试持久化（失败**绝不**传播）。

        Args:
            record: ``ToolExecutionRecord``（frozen；observability 只读）。

        Note:
            本方法**不抛出**（除 BaseException）：写入失败收敛为
            warning 日志；Tool 执行结果不受影响（Step 15 契约）。
        """
        try:
            self._service.persist(record)
        except Exception as exc:  # noqa: BLE001 —— 持久化失败绝不穿透
            logger.warning(
                "Tool execution 持久化失败（不影响 Tool 执行结果）",
                extra={
                    **_safe_log_context(record),
                    "error_type": type(exc).__name__,
                },
            )


class CompositeToolExecutionObserver:
    """最小 fan-out：把同一条 Record 依次交给多个 Observer（互相独立）。

    契约：

        * 实现 ``ToolExecutionObserver``（``on_execution(record)``）；
        * **子 observer 之间互相隔离**：任一个抛出异常只记 warning，
          后续 observer **仍会被调用**（不阻断 fan-out）；
        * 不重排 / 不去重 / 不缓存 / 不改写 record；
        * 不创建 / 不持有 Collector 或 Adapter（由 Composition Root 装配）。
    """

    def __init__(self, *observers: ToolExecutionObserver) -> None:
        """构造 composite。

        Args:
            *observers: 子 observer（各自需提供可调用的 ``on_execution``）。

        Raises:
            TypeError: 任一 observer 缺少可调用的 ``on_execution()``。
        """
        for observer in observers:
            if not callable(getattr(observer, "on_execution", None)):
                raise TypeError(
                    "observer 必须提供可调用的 on_execution()"
                    f"（got {type(observer).__name__}）"
                )
        self._observers: tuple[ToolExecutionObserver, ...] = tuple(observers)

    # ---------- 只读暴露 ----------

    @property
    def observers(self) -> tuple[ToolExecutionObserver, ...]:
        """子 observer（装配顺序；只读 tuple）。"""
        return self._observers

    # ---------- Observer Contract ----------

    def on_execution(self, record: ToolExecutionRecord) -> None:
        """依次调用每个子 observer；单个失败不阻断后续（已隔离）。"""
        for observer in self._observers:
            try:
                observer.on_execution(record)
            except Exception as exc:  # noqa: BLE001 —— 子 observer 失败隔离
                logger.warning(
                    "Tool execution observer failed（已被隔离）",
                    extra={
                        **_safe_log_context(record),
                        "observer_type": type(observer).__name__,
                        "error_type": type(exc).__name__,
                    },
                )
