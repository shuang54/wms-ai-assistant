"""Assistant Outcome Persistence Service（Phase 3.12 Step 64）。

职责（只有一件事）：

    AssistantOutcome（Step 63 判定结果）
        ↓
    persist(assistant_request_id, outcome)
        ↓
    AssistantOutcomeRepository.create()（幂等 · first-write-wins）
        ↓
    ai_ops.assistant_outcome_record

写入时机（Step 64 §十四）：

    AIOrchestratorService.execute()          ← **唯一** Outcome 判定点（Step 63）
        ├── 成功：构造 AIOrchestrationResult 后、return 之前 persist
        └── 失败（Router / RAG / Tool / Text-to-SQL / 能力禁用等异常路径）：
            persist(FAILED) 后按既有语义 raise（**错误响应结构不变**）
    API 层**不**推断 Outcome（只把 Result → HTTP）。

边界（不得违反）：

* **不生成 ID**：``assistant_request_id`` 完全由 Orchestrator 提供；
* **不判定 Outcome**：本层只存储调用方给的终态（enum → str 校验后写入）；
* **不写第二类事实**：只有 ``assistant_request_id`` / ``outcome`` /
  ``created_at``（``id`` 自增）；无 prompt / content / route / SQL /
  chunk / tool 数据 / error_class / exception message / traceback / 凭据；
* **不做 retry / sleep / backoff / queue**：失败只上抛；
* **不隔离**（本层是"纯"持久化）：隔离由 :class:`BestEffortAssistantOutcomeRecorder`
  与 Orchestrator 的 best-effort 调用完成 —— 观测/终态持久化失败
  **绝不**变成 Assistant 业务失败（Step 64 §十三）。
"""
from __future__ import annotations

import asyncio
import logging

from backend.app.db.assistant_outcome_repository import (
    AssistantOutcomeRepository,
    AssistantOutcomeRepositoryError,
    validate_assistant_request_id,
)
from backend.app.dto.assistant_outcome import AssistantOutcome

logger = logging.getLogger(__name__)

__all__ = [
    "AssistantOutcomePersistenceService",
    "BestEffortAssistantOutcomeRecorder",
    "build_assistant_outcome_recorder",
]


class AssistantOutcomePersistenceService:
    """Assistant 终态的持久化服务（同步；异常上抛给调用方）。"""

    def __init__(
        self,
        repository: AssistantOutcomeRepository | None = None,
    ) -> None:
        """构造服务。

        Args:
            repository: Outcome 仓储；None → 默认仓储（操作时才解析
                Session 工厂，构造期不连接数据库）。
        """
        self._repository = (
            repository
            if repository is not None
            else AssistantOutcomeRepository()
        )

    @property
    def repository(self) -> AssistantOutcomeRepository:
        """底层仓储（只读暴露，便于测试断言注入关系）。"""
        return self._repository

    def persist(
        self,
        assistant_request_id: str,
        outcome: AssistantOutcome,
    ) -> int | None:
        """持久化一条 request-level 终态（幂等）。

        Args:
            assistant_request_id: Assistant Trace ID（Orchestrator 生成）。
            outcome:              ``AssistantOutcome`` 四态之一
                                  （**不接受** str / None / 未知值）。

        Returns:
            新记录 id；同一 ``assistant_request_id`` 已存在 →
            ``None``（正常幂等结果：first-write-wins，旧终态不被覆盖）。

        Raises:
            ValueError:                      outcome 非法 / ID 非法。
            AssistantOutcomeRepositoryError: DB 未配置或写入失败。
        """
        if not isinstance(outcome, AssistantOutcome):
            raise ValueError(
                "outcome 必须是 AssistantOutcome"
                f"（当前: {type(outcome).__name__}）"
            )
        # 触达 DB 之前拦截（与 Repository 共用同一校验规则）
        validated_id = validate_assistant_request_id(assistant_request_id)
        return self._repository.create(
            assistant_request_id=validated_id,
            outcome=outcome.value,
        )

    async def persist_async(
        self,
        assistant_request_id: str,
        outcome: AssistantOutcome,
    ) -> int | None:
        """异步入口：把**同步持久化**放进 worker 线程后 await。

        与 :meth:`persist` 语义完全一致（同一仓储 / 同一字段 / 同一幂等），
        区别只在执行的线程（不阻塞 event loop；不创建 Session 共享）。
        """
        return await asyncio.to_thread(
            self.persist, assistant_request_id, outcome
        )


class BestEffortAssistantOutcomeRecorder:
    """Best-effort 终态记录器（**业务隔离边界**）。

    契约（与 ``DatabaseLLMAccountingSink`` 同风格）：

        record(...)  / await arecord(...)   → 永不抛出
            成功 → None（记录已写入 / 幂等跳过）
            失败 → warning（含 error_type）；业务结果不受影响

    为什么需要它：Outcome 持久化属**观测/诊断**链路；
    DB 不可用、连接超时、写入冲突、取消（CancelledError）都不得改变
    已经产生的 Assistant 业务结果，也不得触发任何 retry。
    """

    def __init__(
        self,
        persistence_service: AssistantOutcomePersistenceService | None = None,
    ) -> None:
        """构造记录器。

        Args:
            persistence_service: 持久化服务；None → 默认服务（默认仓储）。
        """
        self._service = (
            persistence_service
            if persistence_service is not None
            else AssistantOutcomePersistenceService()
        )

    @property
    def persistence_service(self) -> AssistantOutcomePersistenceService:
        """底层持久化服务（只读暴露，便于测试断言注入关系）。"""
        return self._service

    def record(
        self,
        assistant_request_id: str,
        outcome: AssistantOutcome,
    ) -> int | None:
        """同步入口（永不抛出）。"""
        try:
            return self._service.persist(assistant_request_id, outcome)
        except Exception as exc:  # noqa: BLE001 —— 终态持久化绝不穿透为业务失败
            self._log_failure(exc)
            return None

    async def arecord(
        self,
        assistant_request_id: str,
        outcome: AssistantOutcome,
    ) -> int | None:
        """异步入口（永不抛出；含取消语义）。"""
        try:
            return await self._service.persist_async(
                assistant_request_id, outcome
            )
        except asyncio.CancelledError:
            # 已完成的 Assistant 业务结果不应被可选的终态持久化取消掉；
            # worker 线程内的写入会在自己的事务里自然结束（独立 Session）。
            logger.warning(
                "Assistant Outcome 持久化被取消（不影响已完成的业务结果）",
                exc_info=False,
            )
            return None
        except Exception as exc:  # noqa: BLE001 —— 绝不穿透为业务失败
            self._log_failure(exc)
            return None

    def _log_failure(self, exc: BaseException) -> None:
        """持久化故障统一降级为 warning（两条入口共用）。"""
        if isinstance(exc, AssistantOutcomeRepositoryError):
            logger.warning(
                "Assistant Outcome 持久化失败（不影响业务结果）: %s",
                exc,
                exc_info=False,
            )
            return
        logger.warning(
            "Assistant Outcome 持久化失败（不影响业务结果）",
            exc_info=True,
        )


def build_assistant_outcome_recorder() -> (
    "BestEffortAssistantOutcomeRecorder | None"
):
    """生产装配：DB 已配置 → best-effort recorder；未配置 → ``None``。

    判定依据（**不新增配置项 / 环境变量**，复用既有语义）：

        ``get_engine() is None`` ⇔ ``DATABASE_URL`` 未配置 ⇔ DB 功能禁用
            → ``None``：Orchestrator 零开销跳过持久化；**无告警噪声**
              （无 DB 的开发 / 单元测试环境行为与 Step 63 之前完全一致）
        DB 已配置（生产）
            → ``BestEffortAssistantOutcomeRecorder``：终态写入
              ``ai_ops.assistant_outcome_record``

    为什么放在 Service 层而不是 Composition Root：组合根保持
    "只组合、不接触 DB / Engine / Session"（既有静态约束）；
    与 LLM Usage 生产接线（Step 56 把 DB 判断放在 ``llm/client.py``）
    采取同样的分层方式。
    """
    from backend.app.db.session import get_engine

    if get_engine() is None:
        logger.info(
            "assistant outcome persistence disabled"
            "（DATABASE_URL 未配置；不创建 outcome recorder）"
        )
        return None
    return BestEffortAssistantOutcomeRecorder()
