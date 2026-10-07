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

AgentToolResult（Phase 5 P-4 — Replayable Tool Result Store）：
    * ``AgentToolResultRecord`` 承载 **execution identity = (run_id, step_index)**
      + Tool 结果的 **安全序列化体**（或"不可用"标记）+ SHA-256 + 尺寸；
    * 大小策略（冻结 §15）：≤ 32 KiB 内联；32 KiB–1 MiB / > 1 MiB ⇒ 不存 payload
      （只留 hash / size / 身份）⇒ 不可 Replay；**不截断**；
    * 敏感边界（§19/§20）：最小凭证 deny-list 命中 ⇒ 不存 payload（也不存 hash）；
    * 本模块定义**端口 + 纯映射**；生产写入点接线（Tool handler 之后**立即**写）
      属独立实现步骤（P-4 接线边界判定见决策 §12 / P-4 报告）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Protocol

from backend.app.agent.errors import (
    AGENT_ERROR_CODES,
    AGENT_ERROR_FAILED,
    AGENT_ERROR_LIMIT_REACHED,
    AGENT_ERROR_TOOL_EXECUTION_FAILURE,
)
from backend.app.agent.models import (
    AGENT_STATUS_LIMIT_REACHED,
    AgentRuntimeResult,
)
from backend.app.tools.base import ToolResult

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
    "AgentToolResultRecorder",
    "BestEffortAgentToolResultRecorder",
    "AgentToolResultRecord",
    "agent_tool_result_record_of",
    "build_agent_tool_result_recorder",
    "SENSITIVE_RESULT_MARKERS",
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


# ============================================================
# Agent Tool Result 持久化（Phase 5 P-4 — Replayable Tool Result Store）
# ============================================================

#: 敏感标记（**最小** deny-list；§19/§20：不是 DLP、不做脱敏、不改写内容）。
#: 命中 ⇒ 该结果**完全不落盘**（payload 与 hash 都不写）。
SENSITIVE_RESULT_MARKERS: Final[tuple[str, ...]] = (
    "api_key",
    "apikey",
    "api-key",
    "authorization",
    "bearer ",
    "password",
    "passwd",
    "secret",
    "credential",
    "database_url",
    "db_url",
    "postgres://",
    "postgresql://",
    "mysql://",
    "mongodb://",
    "sk-",
)


@dataclass(frozen=True)
class AgentToolResultRecord:
    """一次 Agent Tool 执行的持久化投影（**安全 DTO**；字段 ≡ §6.2 设计）。

    身份：**(run_id, step_index)** —— 不是 ``tool_call_id``、不是 ``request_id``、
    不是 Conversation ``Idempotency-Key``。

    状态语义（四态，与 ORM Model 一致）：

        success=True,  result_unavailable=False → SUCCEEDED，可 Replay payload
        success=True,  result_unavailable=True  → SUCCEEDED 但 payload 不可用 ⇒ fail-closed
        success=False, result_unavailable=False → FAILED（Handler 已执行并返回失败）
        success=False, result_unavailable=True  → 执行结果不可判定（§23）⇒ fail-closed
    """

    run_id: str
    step_index: int
    tool_name: str
    success: bool
    result_size_bytes: int
    result_truncated: bool
    result_unavailable: bool
    project_id: str | None = None
    error_code: str | None = None
    result_payload: Any | None = None
    result_hash: str | None = None
    result_ref: str | None = None
    tool_call_id: str | None = None


def _payload_limits() -> tuple[int, int]:
    """大小策略上限（**延迟 import**：模块加载不引入 DB / SQLAlchemy 依赖）。

    单一来源 = ORM Model 常量（``AGENT_TOOL_RESULT_INLINE_MAX_BYTES`` /
    ``AGENT_TOOL_RESULT_HARD_MAX_BYTES``）。
    """
    from backend.app.db.models.agent_tool_result import (
        AGENT_TOOL_RESULT_HARD_MAX_BYTES,
        AGENT_TOOL_RESULT_INLINE_MAX_BYTES,
    )

    return AGENT_TOOL_RESULT_INLINE_MAX_BYTES, AGENT_TOOL_RESULT_HARD_MAX_BYTES


def _canonical_json(value: Any) -> str:
    """规范化 JSON 文本（hash / 尺寸 / 落盘三者共用同一序列化）。

    * ``sort_keys=True`` + 紧凑分隔符 ⇒ **确定性**（same result → same text/hash）；
    * ``allow_nan=False`` ⇒ NaN / Infinity 视为**不可安全序列化**（不静默改写）；
    * 非 JSON 兼容对象（Session / ORM model / exception …）⇒ ``TypeError``。

    Raises:
        TypeError:  值不是 JSON 兼容结构。
        ValueError: NaN / Infinity 等非法数值。
    """
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _contains_sensitive_marker(text: str) -> bool:
    """最小凭证 deny-list 命中判定（大小写不敏感；**不**记录命中的标记）。"""
    lowered = text.lower()
    return any(marker in lowered for marker in SENSITIVE_RESULT_MARKERS)


def _stable_tool_error_code(value: object) -> str:
    """错误码归一：稳定码白名单原样；其它（自由文本 / 未知）→ ``TOOL_EXECUTION_FAILURE``。"""
    if isinstance(value, str) and value in AGENT_ERROR_CODES:
        return value
    return AGENT_ERROR_TOOL_EXECUTION_FAILURE


def agent_tool_result_record_of(
    *,
    run_id: str,
    step_index: int,
    tool_name: str,
    result: ToolResult | None = None,
    error_code: str | None = None,
    project_id: str | None = None,
    tool_call_id: str | None = None,
) -> AgentToolResultRecord:
    """``(identity, tool_name, ToolResult)`` → 持久化投影（纯函数；无 IO / 无 DB）。

    路径：

        ① ``result is None``（或显式 ``error_code``）
               → 执行结果**不可判定**（边界异常 / 能力拒绝 / 未知；§23）：
                 ``success=False`` + 稳定码 + ``result_unavailable=True``（fail-closed）
        ② ``result.success is False``（Handler 已执行并返回失败）
               → ``success=False`` + ``TOOL_EXECUTION_FAILURE`` +
                 ``result_unavailable=False``（failure 事实本身可 Replay；
                 ToolResult.error 的**自由文本一律丢弃**）
        ③ ``result.success is True``
               → 序列化 ``{"tool_name": …, "data": …}``（复用 Runtime 既有结果体；
                 不含 ``【TOOL RESULT — UNTRUSTED DATA】`` marker 包装）
               → 敏感标记命中 ⇒ payload / hash 都不落盘（unavailable=True）
               → ≤ 32 KiB ⇒ 内联 payload + SHA-256 + size（可 Replay）
               → ≤ 1 MiB / > 1 MiB ⇒ payload=None + SHA-256 + size（不可 Replay）

    恒为 ``result_truncated=False``（**不做截断**：超限直接置不可用）。
    ``result_ref`` 恒为 ``None``（无 artifact store）。
    """
    base: dict[str, Any] = {
        "run_id": run_id,
        "step_index": step_index,
        "tool_name": tool_name,
        "project_id": project_id,
        "tool_call_id": tool_call_id,
        "result_ref": None,
        "result_truncated": False,
    }

    # ① 结果不可判定（未执行 / 边界异常 / 能力拒绝 / 未知）
    if result is None or error_code is not None:
        return AgentToolResultRecord(
            **base,
            success=False,
            error_code=_stable_tool_error_code(error_code),
            result_payload=None,
            result_hash=None,
            result_size_bytes=0,
            result_unavailable=True,
        )

    # ② Handler 已执行，但返回失败
    if not bool(result.success):
        return AgentToolResultRecord(
            **base,
            success=False,
            error_code=_stable_tool_error_code(None),
            result_payload=None,
            result_hash=None,
            result_size_bytes=0,
            result_unavailable=False,
        )

    # ③ Handler 成功：安全序列化 → 敏感检查 → 尺寸策略 → hash
    inline_max, _hard_max = _payload_limits()
    try:
        text = _canonical_json({"tool_name": tool_name, "data": result.data})
    except (TypeError, ValueError):
        # 不可安全序列化（Session / 自定义对象 / NaN …）：不猜、不 str()、不 repr()
        return AgentToolResultRecord(
            **base,
            success=True,
            error_code=None,
            result_payload=None,
            result_hash=None,
            result_size_bytes=0,
            result_unavailable=True,
        )

    size = len(text.encode("utf-8"))
    if _contains_sensitive_marker(text):
        # 凭证形状内容：payload 与 hash **都不**落盘（不写入敏感原文）
        return AgentToolResultRecord(
            **base,
            success=True,
            error_code=None,
            result_payload=None,
            result_hash=None,
            result_size_bytes=size,
            result_unavailable=True,
        )

    result_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if size <= inline_max:
        return AgentToolResultRecord(
            **base,
            success=True,
            error_code=None,
            # 存 hash 的**同一**结构（保证未来 Replay 重新序列化后 hash 一致）
            result_payload=json.loads(text),
            result_hash=result_hash,
            result_size_bytes=size,
            result_unavailable=False,
        )
    # 32 KiB < size：只保留 hash / size / 身份 ⇒ 不可 Replay。
    # 中间档与 "> 1 MiB" 档当前**行为相同**（都不落 payload、都不截断）；
    # ``hard_max`` 是"超过即绝不尝试内联"的硬上界（当前实现天然满足）。
    return AgentToolResultRecord(
        **base,
        success=True,
        error_code=None,
        result_payload=None,
        result_hash=result_hash,
        result_size_bytes=size,
        result_unavailable=True,
    )


class AgentToolResultRecorder(Protocol):
    """Tool Result 持久化端口（**写入点**由上层接线决定；实现必须 best-effort）。"""

    def record(self, record: AgentToolResultRecord) -> int | None:
        """同步入口（实现必须 **best-effort**：永不抛出）。"""
        ...

    async def arecord(self, record: AgentToolResultRecord) -> int | None:
        """异步入口（实现必须 **best-effort**：永不抛出）。"""
        ...


class BestEffortAgentToolResultRecorder:
    """Best-effort Tool Result 记录器（与 ``BestEffortAgentStepRecorder`` 同风格）。

    契约：

        ``record(...)`` / ``await arecord(...)`` → **永不抛出**
            成功 → 新行 id；``(run_id, step_index)`` 已存在 → ``None``（幂等跳过）
            失败 → ``None`` + warning（含 error_type）

    为什么需要它：结果存储属**恢复支撑链路**（不是业务链路）。
    DB 不可用 / 超时 / 校验失败 / 取消都不得改变已经产生的 Tool / Agent 结果，
    也不得触发 retry、LLM 重问、Tool 重跑、fallback。
    """

    def __init__(self, repository: Any | None = None) -> None:
        """构造记录器（repository=None → 默认仓储，延迟 import）。"""
        self._repository = repository

    def _get_repository(self) -> Any:
        if self._repository is not None:
            return self._repository
        from backend.app.db.agent_tool_result_repository import (
            AgentToolResultRepository,
        )

        self._repository = AgentToolResultRepository()
        return self._repository

    @property
    def repository(self) -> Any:
        """底层仓储（只读暴露，便于测试断言注入关系）。"""
        return self._repository

    def record(self, record: AgentToolResultRecord) -> int | None:
        """同步入口（永不抛出）。"""
        created = self.record_many((record,))
        return created[0] if created else None

    def record_many(self, records: tuple[AgentToolResultRecord, ...]) -> list[int]:
        """同步批量入口（永不抛出）。"""
        try:
            if not records:
                return []
            # 直接传 agent 侧投影（与 ``BestEffortAgentStepRecorder`` 同风格：
            # Repository 按字段名校验，不要求特定 Row 类型）。
            return self._get_repository().create_many(list(records))
        except asyncio.CancelledError:
            logger.warning(
                "Agent Tool Result 持久化被取消（不影响已完成的 Tool / Agent 结果）",
                exc_info=False,
            )
            return []
        except Exception as exc:  # noqa: BLE001 —— 绝不穿透为业务失败
            self._log_failure(exc, records)
            return []

    async def arecord(self, record: AgentToolResultRecord) -> int | None:
        """异步入口（永不抛出；同步持久化放进 worker 线程）。"""
        created = await self.arecord_many((record,))
        return created[0] if created else None

    async def arecord_many(
        self, records: tuple[AgentToolResultRecord, ...]
    ) -> list[int]:
        """异步批量入口（永不抛出）。"""
        try:
            return await asyncio.to_thread(self.record_many, records)
        except asyncio.CancelledError:
            logger.warning(
                "Agent Tool Result 持久化被取消（不影响已完成的 Tool / Agent 结果）",
                exc_info=False,
            )
            return []
        except Exception as exc:  # noqa: BLE001 —— 兜底（record 本身已 best-effort）
            self._log_failure(exc, records)
            return []

    @staticmethod
    def _log_failure(
        exc: BaseException, records: tuple[AgentToolResultRecord, ...]
    ) -> None:
        """失败只记录**诊断信息**（error_type + 身份字段；不含结果内容）。"""
        logger.warning(
            "Agent Tool Result 持久化失败（best-effort；不影响 Tool / Agent 结果）: %s",
            type(exc).__name__,
            extra={
                "error_type": type(exc).__name__,
                "run_id": records[0].run_id if records else None,
                "step_index": records[0].step_index if records else None,
                "tool_name": records[0].tool_name if records else None,
            },
        )


def build_agent_tool_result_recorder() -> BestEffortAgentToolResultRecorder | None:
    """生产装配（与 ``build_agent_step_recorder`` 同风格）：

        ``get_engine() is None`` ⇔ DATABASE_URL 未配置 → ``None``（零开销 / 无告警噪声）
        否则 → ``BestEffortAgentToolResultRecorder``（写入 ``ai_ops.agent_tool_result``）

    Note:
        本函数只装配 recorder；**写入点接线**（Tool handler 之后立即写）不在本阶段
        （P-4 接线边界判定见报告）。
    """
    from backend.app.db.session import get_engine

    if get_engine() is None:
        logger.info(
            "agent tool result persistence disabled"
            "（DATABASE_URL 未配置；不创建 agent tool result recorder）"
        )
        return None
    return BestEffortAgentToolResultRecorder()
