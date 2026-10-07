"""AgentRun Recorder（Phase 5 P-2 —— 持久化边界，**不是** Agent Runtime 的一部分）。

位置与依赖方向（已冻结，§十一 / §30.6 / §30.9）：

```text
AIOrchestrator
    ↓ agent_runtime.run()          （AgentRuntime：**纯内存执行**，0 DB）
    ↓ AgentRuntimeResult
    ↓ agent_run_recorder.record(...)（本模块：唯一持久化边界）
    ↓ AgentRunRepository
    ↓ ai_ops.agent_run
```

硬性约束：

    * ``AgentRuntime`` **不得** import 本模块（源码审计锁定：runtime 0 DB）；
    * 本模块是 agent 包中**唯一**允许触碰 DB 的模块（Repository 注入，可替换）；
    * ``run_id`` / ``request_id`` **只读取**，绝不重新生成（§六 / §十六 / §十七）；
    * best-effort：``record()`` / ``arecord()`` **永不抛出** —— 持久化失败
      只产生 warning + diagnostic，**不改变**已产生的 Agent 业务结果；
    * 只落盘 **state / counts / stable codes / timestamps / correlation ids /
      project_id**；绝不落盘 prompt / messages / context / tool_arguments /
      tool_result / SQL / headers / 密钥 / exception message / stack trace。

AgentStep（Phase 5 AgentStep Persistence）：
    * ``AgentStepRecord`` 只承载 step identity / type / status / tool_name /
      稳定错误码 / timing（**不含** arguments / output / 正文）；
    * 与 ``agent_run`` 各自独立 best-effort 写入（**不引入**跨表事务 / Unit of Work）。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from backend.app.agent.errors import (
    AGENT_ERROR_CODES,
    AGENT_ERROR_FAILED,
    AGENT_ERROR_LIMIT_REACHED,
)
from backend.app.agent.models import (
    AGENT_STATUS_LIMIT_REACHED,
    AgentRuntimeResult,
)

__all__ = [
    "AgentRunRecorder",
    "BestEffortAgentRunRecorder",
    "AgentRunRecord",
    "agent_run_record_of",
    "build_agent_run_recorder",
    "AgentStepRecorder",
    "BestEffortAgentStepRecorder",
    "AgentStepRecord",
    "agent_step_records_of",
    "build_agent_step_recorder",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentRunRecord:
    """Agent Run 的持久化投影（**安全 DTO**：字段 ≡ Decision §30.9 清单）。

    注意：不含 content / prompt / context / tool 明细 / token 计费字段。
    """

    run_id: str
    request_id: str
    project_id: str | None
    status: str
    error_code: str | None
    limit_reason: str | None
    step_count: int
    tool_call_count: int
    llm_call_count: int
    token_usage_unverifiable: bool
    started_at: datetime
    finished_at: datetime
    duration_ms: float


def _stable_error_code(error: object) -> str | None:
    """错误码归一：稳定码原样；异常取**类名**；自由文本 → ``AGENT_FAILED``。

    绝不写入 exception message / stack trace / 任何自由文本。
    """
    if error is None:
        return None
    if isinstance(error, str):
        return error if error in AGENT_ERROR_CODES else AGENT_ERROR_FAILED
    if isinstance(error, BaseException):
        return type(error).__name__
    return AGENT_ERROR_FAILED


def agent_run_record_of(
    result: AgentRuntimeResult,
    *,
    request_id: str,
    project_id: str | None,
    started_at: datetime,
    finished_at: datetime,
    duration_ms: float,
) -> AgentRunRecord:
    """``AgentRuntimeResult`` → 持久化投影（不改状态语义，不重新计算计数）。

    * ``status`` **原样**保存（LIMIT_REACHED ≠ FAILED）；
    * 计数直接取自 Result（不重新推导）；
    * ``limit_reason`` 仅 LIMIT_REACHED 时填写稳定码。
    """
    status = result.status
    limit_reason = (
        (_stable_error_code(result.error) or AGENT_ERROR_LIMIT_REACHED)
        if status == AGENT_STATUS_LIMIT_REACHED
        else None
    )
    return AgentRunRecord(
        run_id=result.run_id,
        request_id=request_id,
        project_id=project_id,
        status=status,
        error_code=_stable_error_code(result.error),
        limit_reason=limit_reason,
        step_count=result.steps,
        tool_call_count=result.tool_calls,
        llm_call_count=result.llm_calls,
        token_usage_unverifiable=bool(result.token_budget_unverifiable),
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
    )


class AgentRunRecorder(Protocol):
    """Agent Run 持久化端口（Orchestrator 只依赖本接口）。"""

    def record(
        self,
        result: AgentRuntimeResult,
        *,
        request_id: str,
        project_id: str | None,
        started_at: datetime,
        finished_at: datetime,
        duration_ms: float,
    ) -> int | None:
        """同步入口（实现必须 **best-effort**：永不抛出）。"""
        ...

    async def arecord(
        self,
        result: AgentRuntimeResult,
        *,
        request_id: str,
        project_id: str | None,
        started_at: datetime,
        finished_at: datetime,
        duration_ms: float,
    ) -> int | None:
        """异步入口（实现必须 **best-effort**：永不抛出）。"""
        ...


class BestEffortAgentRunRecorder:
    """Best-effort Agent Run 记录器（与 ``BestEffortAssistantOutcomeRecorder`` 同风格）。

    契约：

        ``record(...)`` / ``await arecord(...)`` → **永不抛出**
            成功 → ``int``（新行 id）或 ``None``（run_id 已存在 = 幂等跳过）
            失败 → ``None`` + warning（含 error_type；业务结果不受影响）

    为什么需要它：Agent Run 持久化属 **run-level state / 诊断**链路；
    DB 不可用、超时、校验失败、取消都不得改变已经产生的 Agent 结果，也不得触发 retry。
    """

    def __init__(self, repository: Any | None = None) -> None:
        """构造记录器。

        Args:
            repository: ``AgentRunRepository``；None → 默认仓储
                （延迟 import，模块加载不创建 DB 基础设施）。
        """
        self._repository = repository

    # ---------- 依赖解析 ----------

    def _get_repository(self) -> Any:
        if self._repository is not None:
            return self._repository
        from backend.app.db.agent_run_repository import AgentRunRepository

        self._repository = AgentRunRepository()
        return self._repository

    @property
    def repository(self) -> Any:
        """底层仓储（只读暴露，便于测试断言注入关系）。"""
        return self._repository

    # ---------- 入口 ----------

    def record(
        self,
        result: AgentRuntimeResult,
        *,
        request_id: str,
        project_id: str | None,
        started_at: datetime,
        finished_at: datetime,
        duration_ms: float,
    ) -> int | None:
        """同步入口（永不抛出）。"""
        try:
            record = agent_run_record_of(
                result,
                request_id=request_id,
                project_id=project_id,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
            )
            return self._get_repository().create(**_record_kwargs(record))
        except asyncio.CancelledError:
            logger.warning(
                "Agent Run 持久化被取消（不影响已完成的 Agent 结果）",
                exc_info=False,
            )
            return None
        except Exception as exc:  # noqa: BLE001 —— 绝不穿透为业务失败
            self._log_failure(exc, result)
            return None

    async def arecord(
        self,
        result: AgentRuntimeResult,
        *,
        request_id: str,
        project_id: str | None,
        started_at: datetime,
        finished_at: datetime,
        duration_ms: float,
    ) -> int | None:
        """异步入口（永不抛出；同步持久化放进 worker 线程）。"""
        try:
            return await asyncio.to_thread(
                self.record,
                result,
                request_id=request_id,
                project_id=project_id,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
            )
        except asyncio.CancelledError:
            logger.warning(
                "Agent Run 持久化被取消（不影响已完成的 Agent 结果）",
                exc_info=False,
            )
            return None
        except Exception as exc:  # noqa: BLE001 —— 兜底（record 本身已 best-effort）
            self._log_failure(exc, result)
            return None

    # ---------- 内部 ----------

    @staticmethod
    def _log_failure(exc: BaseException, result: AgentRuntimeResult) -> None:
        """失败只记录 **诊断信息**（稳定码 / 状态；不含 run 内容）。"""
        logger.warning(
            "Agent Run 持久化失败（best-effort；不影响 Agent 结果）: %s",
            type(exc).__name__,
            extra={
                "error_type": type(exc).__name__,
                "agent_status": getattr(result, "status", None),
            },
        )


# ============================================================
# AgentStep 持久化（Phase 5 AgentStep Persistence）
# ============================================================


@dataclass(frozen=True)
class AgentStepRecord:
    """Agent Step 的持久化投影（**安全 DTO**：字段 ≡ 最小字段集）。

    注意：不含 arguments / output / prompt / context / tool result /
    request_id / tool_call_id / provider / tokens
    （分别属不落盘或既有 observation 表）。
    """

    run_id: str
    step_index: int
    step_type: str
    status: str
    tool_name: str | None
    error_code: str | None
    elapsed_ms: float | None


def agent_step_records_of(result: AgentRuntimeResult) -> tuple[AgentStepRecord, ...]:
    """``AgentRuntimeResult.step_records`` → 安全持久化投影（逐 Step）。

    * ``step_index`` / ``type`` / ``status`` **原样**映射（不重排 / 不改语义）；
    * ``tool_name`` 仅 TOOL_CALL 携带（**不写** arguments / output）；
    * ``error`` → 稳定错误码（异常取类名；自由文本 → ``AGENT_FAILED``）；
    * ``elapsed_ms`` 原值（``None`` 允许；非法值由 Repository 拒绝 ⇒ best-effort 跳过整批）。
    """
    return tuple(
        AgentStepRecord(
            run_id=result.run_id,
            step_index=step.step_index,
            step_type=step.type,
            status=step.status,
            tool_name=step.tool_name,
            error_code=_stable_error_code(step.error),
            elapsed_ms=step.elapsed_ms,
        )
        for step in result.step_records
    )


class AgentStepRecorder(Protocol):
    """Agent Step 持久化端口（Orchestrator 只依赖本接口）。"""

    def record(self, result: AgentRuntimeResult) -> list[int]:
        """同步入口（实现必须 **best-effort**：永不抛出）。"""
        ...

    async def arecord(self, result: AgentRuntimeResult) -> list[int]:
        """异步入口（实现必须 **best-effort**：永不抛出）。"""
        ...


class BestEffortAgentStepRecorder:
    """Best-effort Agent Step 记录器（与 ``BestEffortAgentRunRecorder`` 同风格）。

    契约：

        ``record(result)`` / ``await arecord(result)`` → **永不抛出**
            成功 → 新建 id 列表（可能为空 = 全部幂等跳过）
            失败 → ``[]`` + warning（含 error_type）；业务结果不受影响

    为什么需要它：Step 投影属 **run-level 审计**链路；
    DB 不可用、超时、校验失败、取消都不得改变已经产生的 Agent 结果，
    也不得触发 retry / LLM 重问 / Tool 重跑。
    """

    def __init__(self, repository: Any | None = None) -> None:
        """构造记录器（repository=None → 默认仓储，延迟 import）。"""
        self._repository = repository

    def _get_repository(self) -> Any:
        if self._repository is not None:
            return self._repository
        from backend.app.db.agent_step_repository import AgentStepRepository

        self._repository = AgentStepRepository()
        return self._repository

    @property
    def repository(self) -> Any:
        """底层仓储（只读暴露，便于测试断言注入关系）。"""
        return self._repository

    def record(self, result: AgentRuntimeResult) -> list[int]:
        """同步入口（永不抛出）。"""
        try:
            rows = agent_step_records_of(result)
            if not rows:
                return []
            return self._get_repository().create_many(rows)
        except asyncio.CancelledError:
            logger.warning(
                "Agent Step 持久化被取消（不影响已完成的 Agent 结果）",
                exc_info=False,
            )
            return []
        except Exception as exc:  # noqa: BLE001 —— 绝不穿透为业务失败
            self._log_failure(exc, result)
            return []

    async def arecord(self, result: AgentRuntimeResult) -> list[int]:
        """异步入口（永不抛出；同步持久化放进 worker 线程）。"""
        try:
            return await asyncio.to_thread(self.record, result)
        except asyncio.CancelledError:
            logger.warning(
                "Agent Step 持久化被取消（不影响已完成的 Agent 结果）",
                exc_info=False,
            )
            return []
        except Exception as exc:  # noqa: BLE001 —— 兜底（record 本身已 best-effort）
            self._log_failure(exc, result)
            return []

    @staticmethod
    def _log_failure(exc: BaseException, result: AgentRuntimeResult) -> None:
        """失败只记录**诊断信息**（稳定码 / 状态；不含 run 内容）。"""
        logger.warning(
            "Agent Step 持久化失败（best-effort；不影响 Agent 结果）: %s",
            type(exc).__name__,
            extra={
                "error_type": type(exc).__name__,
                "agent_status": getattr(result, "status", None),
            },
        )


def build_agent_step_recorder() -> BestEffortAgentStepRecorder | None:
    """生产装配（与 ``build_agent_run_recorder`` 同风格）：

        ``get_engine() is None`` ⇔ DATABASE_URL 未配置 → ``None``（零开销 / 无告警噪声）
        否则 → ``BestEffortAgentStepRecorder``（写入 ``ai_ops.agent_step``）
    """
    from backend.app.db.session import get_engine

    if get_engine() is None:
        logger.info(
            "agent step persistence disabled"
            "（DATABASE_URL 未配置；不创建 agent step recorder）"
        )
        return None
    return BestEffortAgentStepRecorder()


def build_agent_run_recorder() -> BestEffortAgentRunRecorder | None:
    """生产装配（与 ``build_assistant_outcome_recorder`` 同风格）：

        ``get_engine() is None`` ⇔ DATABASE_URL 未配置
            → ``None``：Orchestrator 零开销跳过持久化；**无告警噪声**
              （无 DB 的开发 / 单元测试环境行为与 P-2 之前完全一致）
        否则 → ``BestEffortAgentRunRecorder``（写入 ``ai_ops.agent_run``）

    不新增配置项 / 环境变量；本函数只装配，不写库 / 不查询。
    """
    from backend.app.db.session import get_engine

    if get_engine() is None:
        logger.info(
            "agent run persistence disabled"
            "（DATABASE_URL 未配置；不创建 agent run recorder）"
        )
        return None
    return BestEffortAgentRunRecorder()


def _record_kwargs(record: AgentRunRecord) -> dict[str, Any]:
    """``AgentRunRecord`` → Repository ``create(**kwargs)``（显式字段，不 **dict** 透传）。"""
    return {
        "run_id": record.run_id,
        "request_id": record.request_id,
        "project_id": record.project_id,
        "status": record.status,
        "error_code": record.error_code,
        "limit_reason": record.limit_reason,
        "step_count": record.step_count,
        "tool_call_count": record.tool_call_count,
        "llm_call_count": record.llm_call_count,
        "token_usage_unverifiable": record.token_usage_unverifiable,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "duration_ms": record.duration_ms,
    }
