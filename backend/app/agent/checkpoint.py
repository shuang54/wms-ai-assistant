"""Agent Checkpoint —— 恢复控制状态（Phase 5 P-5 Checkpoint Persistence）。

职责（单一）：

```text
Agent 当前执行到了哪里？
下一步应该做什么？
当前状态版本是多少？
谁拥有继续执行这个 Checkpoint 的 claim？
```

```text
AgentRun          = 已经发生了什么（terminal execution record）
AgentStep         = 每一步发生了什么（安全投影）
AgentToolResult   = Tool 的历史结果是什么（可 Replay 结果）
AgentCheckpoint   = 下一次恢复应该从哪里继续（recovery control state）
Conversation      = 用户对话是什么
Evidence          = 信息来源是什么
```

边界（严格）：

    * **不是 Resume**：本模块只**写/读**控制状态；无 resume() / restore() /
      replay() / retry / HITL / cancel / approval，绝不自动恢复 Agent、
      自动重调 LLM、自动重跑 Tool（P-6 起才使用这些字段）；
    * **只保存控制状态 + 引用 + 标识**（§二十五）：**禁止** prompt / messages /
      conversation context / RAG chunks / tool definitions / tool arguments /
      tool result body / SQL / LLM response body / token 计量 / provider / model；
      Tool 结果与 LLM 观测分别属 ``agent_tool_result`` / ``llm_usage_record``；
    * **无 Conversation / Evidence 耦合**（§四十八 / §四十九）：不出现
      conversation_id / turn_id / idempotency_key / evidence_id；
    * **claim_token = 内部并发/所有权令牌**（§十四–§十六）：cryptographically
      random（``secrets.token_urlsafe``）；**绝不**进入 API / Conversation /
      AIOrchestrationResult / 任何用户可见输出；
    * **每次成功 update 不重新生成 claim_token**（§十八）；不做自动 takeover /
      重新生成 / force update（§四十五；lease/ownership 属后续阶段）；
    * 乐观并发（§十二）：``run_id + expected_state_version + claim_token`` 三者
      同时进入 UPDATE 条件（Repository 层，单条 SQL；绝不 SELECT-then-UPDATE）。

依赖方向（禁止反向）：本模块可 import agent 内核（ids / models）；
Repository / Session **只在调用期延迟 import**（模块加载不引入 DB 基础设施）。
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Protocol

from backend.app.agent.ids import current_agent_run_id
from backend.app.agent.models import (
    AGENT_STATUS_CANCELLED,
    AGENT_STATUS_COMPLETED,
    AGENT_STATUS_FAILED,
    AGENT_STATUS_LIMIT_REACHED,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AgentCheckpoint",
    "AgentCheckpointRecorder",
    "AgentCheckpointStoreError",
    "BestEffortAgentCheckpointRecorder",
    "new_claim_token",
    "tool_result_ref",
    "parse_tool_result_ref",
    "assert_tool_result_ref_matches",
    "checkpoint_terminal_state_of",
    "CHECKPOINT_STATUS_ACTIVE",
    "CHECKPOINT_STATUS_PAUSED",
    "CHECKPOINT_STATUS_TERMINAL",
    "CHECKPOINT_STATUSES",
    "CHECKPOINT_NEXT_ACTION_NONE",
    "CHECKPOINT_NEXT_ACTION_LLM_DECISION",
    "CHECKPOINT_NEXT_ACTION_TOOL_EXECUTION",
    "CHECKPOINT_NEXT_ACTION_WAITING_APPROVAL",
    "CHECKPOINT_NEXT_ACTION_COMPLETED",
    "CHECKPOINT_NEXT_ACTION_FAILED",
    "CHECKPOINT_NEXT_ACTION_CANCELLED",
    "CHECKPOINT_NEXT_ACTIONS",
    "CHECKPOINT_RUN_ID_MAX_LENGTH",
    "CHECKPOINT_CLAIM_TOKEN_MAX_LENGTH",
    "CHECKPOINT_STATUS_MAX_LENGTH",
    "CHECKPOINT_NEXT_ACTION_MAX_LENGTH",
    "CHECKPOINT_PENDING_TOOL_CALL_ID_MAX_LENGTH",
    "CHECKPOINT_PENDING_REF_MAX_LENGTH",
    "CHECKPOINT_INITIAL_STATE_VERSION",
]

#: Checkpoint 状态（§二十；``PAUSED`` 为 HITL 预留，本阶段**不产生**）。
CHECKPOINT_STATUS_ACTIVE: Final[str] = "ACTIVE"
CHECKPOINT_STATUS_PAUSED: Final[str] = "PAUSED"
CHECKPOINT_STATUS_TERMINAL: Final[str] = "TERMINAL"

#: 允许的状态集合（白名单；不新增 APPROVED / REJECTED / CANCELLED）。
CHECKPOINT_STATUSES: Final[frozenset[str]] = frozenset(
    {
        CHECKPOINT_STATUS_ACTIVE,
        CHECKPOINT_STATUS_PAUSED,
        CHECKPOINT_STATUS_TERMINAL,
    }
)

#: ``next_action``（§十九；P-7 §三十五 最小扩展）：
#: NONE / LLM_DECISION / TOOL_EXECUTION / WAITING_APPROVAL / COMPLETED /
#: FAILED / **CANCELLED**。
#: **禁止**新增 APPROVED / REJECTED / LIMIT_REACHED（HITL 裁决不写 Checkpoint；
#: LIMIT_REACHED 归入 FAILED；§十九 / §三十六 —— CANCELLED 由 P-7 §三十五 明确要求，
#: 不得用 FAILED 冒充取消）。
CHECKPOINT_NEXT_ACTION_NONE: Final[str] = "NONE"
CHECKPOINT_NEXT_ACTION_LLM_DECISION: Final[str] = "LLM_DECISION"
CHECKPOINT_NEXT_ACTION_TOOL_EXECUTION: Final[str] = "TOOL_EXECUTION"
CHECKPOINT_NEXT_ACTION_WAITING_APPROVAL: Final[str] = "WAITING_APPROVAL"
CHECKPOINT_NEXT_ACTION_COMPLETED: Final[str] = "COMPLETED"
CHECKPOINT_NEXT_ACTION_FAILED: Final[str] = "FAILED"
CHECKPOINT_NEXT_ACTION_CANCELLED: Final[str] = "CANCELLED"

CHECKPOINT_NEXT_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        CHECKPOINT_NEXT_ACTION_NONE,
        CHECKPOINT_NEXT_ACTION_LLM_DECISION,
        CHECKPOINT_NEXT_ACTION_TOOL_EXECUTION,
        CHECKPOINT_NEXT_ACTION_WAITING_APPROVAL,
        CHECKPOINT_NEXT_ACTION_COMPLETED,
        CHECKPOINT_NEXT_ACTION_FAILED,
        CHECKPOINT_NEXT_ACTION_CANCELLED,
    }
)

# ---- 列长度上限（§八 / §二十七）----
CHECKPOINT_RUN_ID_MAX_LENGTH: Final[int] = 64
CHECKPOINT_CLAIM_TOKEN_MAX_LENGTH: Final[int] = 128
CHECKPOINT_STATUS_MAX_LENGTH: Final[int] = 32
CHECKPOINT_NEXT_ACTION_MAX_LENGTH: Final[int] = 32
CHECKPOINT_PENDING_TOOL_CALL_ID_MAX_LENGTH: Final[int] = 128
CHECKPOINT_PENDING_REF_MAX_LENGTH: Final[int] = 128

#: 新建 Checkpoint 的初始版本（§十一：创建 = 1；每次成功更新 +1）。
CHECKPOINT_INITIAL_STATE_VERSION: Final[int] = 1

#: claim_token 随机字节数（``secrets.token_urlsafe(32)`` ⇒ 43 字符 ≤ 128）。
_CLAIM_TOKEN_BYTES: Final[int] = 32


def new_claim_token() -> str:
    """签发一个 Checkpoint claim token（cryptographically random）。

    Returns:
        ``secrets.token_urlsafe(32)``（URL-safe base64；不含语义信息）。

    Note:
        **不是** request_id / run_id / tool_call_id / Conversation Idempotency-Key /
        LLM request id（§十四）；**绝不**用 uuid4 / hash(run_id) / 时间戳替代（§十五）。
        该 token 只用于**内部**并发/所有权判定（§十六：不返回给用户）。
    """
    return secrets.token_urlsafe(_CLAIM_TOKEN_BYTES)


def tool_result_ref(run_id: str, step_index: int) -> str:
    """Tool Result 的**逻辑引用**（§二十四）：``"{run_id}:{step_index}"``。

    该引用可逻辑定位 ``agent_tool_result`` 行（执行身份 = (run_id, step_index)）；
    **不**新增 result_ref API、**不**修改 P-4 schema。
    长度 ≤ 64 + 1 + 10 = 75 ≤ 128 ✓。
    """
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id 必须是非空 str")
    if isinstance(step_index, bool) or not isinstance(step_index, int):
        raise ValueError("step_index 必须是 int")
    if step_index < 0:
        raise ValueError("step_index 必须 >= 0")
    return f"{run_id}:{step_index}"


def parse_tool_result_ref(ref: str) -> tuple[str, int]:
    """解析 Tool Result 引用 → ``(run_id, step_index)``。

    Raises:
        ValueError: 非法格式 / 空 run_id / 非整数或负 step_index。
    """
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("pending_tool_result_ref 必须是非空 str")
    run_id, separator, raw_index = ref.rpartition(":")
    if not separator or not run_id.strip():
        raise ValueError(f"pending_tool_result_ref 格式非法: {ref!r}")
    try:
        step_index = int(raw_index)
    except ValueError as exc:
        raise ValueError(
            f"pending_tool_result_ref 的 step_index 非法: {ref!r}"
        ) from exc
    if step_index < 0:
        raise ValueError(f"pending_tool_result_ref 的 step_index 不能为负: {ref!r}")
    return run_id, step_index


def assert_tool_result_ref_matches(
    ref: str, *, run_id: str, step_index: int
) -> None:
    """纯函数校验（§四十）：引用必须指向**同一个 run、同一个 step**。

    Raises:
        ValueError: 引用指向其它 run / 其它 step（或格式非法）。
    """
    ref_run_id, ref_step_index = parse_tool_result_ref(ref)
    if ref_run_id != run_id:
        raise ValueError(
            "pending_tool_result_ref 指向其它 run"
            f"（ref={ref_run_id!r}, run_id={run_id!r}）"
        )
    if ref_step_index != step_index:
        raise ValueError(
            "pending_tool_result_ref 指向其它 step"
            f"（ref={ref_step_index}, step_index={step_index}）"
        )


def checkpoint_terminal_state_of(agent_status: str) -> tuple[str, str] | None:
    """``AgentRuntimeResult.status`` → ``(checkpoint_status, next_action)``（§三十五 / §三十六）。

        COMPLETED      → TERMINAL / COMPLETED
        FAILED         → TERMINAL / FAILED
        LIMIT_REACHED  → TERMINAL / FAILED（**不**新增 LIMIT_REACHED 作为 next_action）
        CANCELLED      → TERMINAL / CANCELLED（P-7 §三十五；**不**用 FAILED 冒充取消）

    Returns:
        ``(status, next_action)``；``WAITING_APPROVAL`` → ``None``
        （**暂停不是终态**：Run 尚未有终态记录，Checkpoint 为 PAUSED /
        WAITING_APPROVAL —— 由 :meth:`record_waiting_approval_checkpoint` 写入）。

    Note:
        本函数**不修改** AgentRun 语义（AgentRun 仍是最终 execution record）。
    """
    if agent_status == AGENT_STATUS_COMPLETED:
        return (CHECKPOINT_STATUS_TERMINAL, CHECKPOINT_NEXT_ACTION_COMPLETED)
    if agent_status in (AGENT_STATUS_FAILED, AGENT_STATUS_LIMIT_REACHED):
        return (CHECKPOINT_STATUS_TERMINAL, CHECKPOINT_NEXT_ACTION_FAILED)
    if agent_status == AGENT_STATUS_CANCELLED:
        return (CHECKPOINT_STATUS_TERMINAL, CHECKPOINT_NEXT_ACTION_CANCELLED)
    return None


@dataclass(frozen=True)
class AgentCheckpoint:
    """Checkpoint 的内部 DTO（frozen；§三十）。

    ⚠️ 含 ``claim_token``（内部并发/所有权令牌）：**任何** API / 用户输出 DTO /
    Conversation metadata / AIOrchestrationResult **都不得**包含它（§十六 / §三十）。
    """

    run_id: str
    state_version: int
    claim_token: str
    status: str
    next_action: str
    pending_step_index: int | None
    pending_tool_call_id: str | None
    pending_decision_ref: str | None
    pending_tool_result_ref: str | None
    created_at: datetime
    updated_at: datetime
    # ---- Phase 5 P-7：协作式取消信号（**信号**，不是 status 枚举成员；§二十八）----
    cancel_requested: bool = False
    cancel_requested_at: datetime | None = None


class AgentCheckpointStoreError(Exception):
    """Checkpoint 读取 / 声明失败（Resume 的 fail-closed 信号）。

    与写路径（best-effort 吞异常）不同：读/声明失败**必须**让 Resume 失败，
    而不是"当作没有 Checkpoint"继续。
    """


class AgentCheckpointRecorder(Protocol):
    """Checkpoint 写入端口（实现必须 **best-effort**：永不抛出）。"""

    def record_tool_result_checkpoint(
        self, *, step_index: int, tool_result_persisted: bool
    ) -> AgentCheckpoint | None:
        """Tool Result 已 durable 后的控制状态（ACTIVE / LLM_DECISION）。"""
        ...

    async def arecord_tool_result_checkpoint(
        self, *, step_index: int, tool_result_persisted: bool
    ) -> AgentCheckpoint | None:
        """异步入口。"""
        ...

    def record_decision_checkpoint(
        self,
        *,
        step_index: int,
        decision_ref: str,
        next_action: str,
        tool_call_id: str | None = None,
    ) -> AgentCheckpoint | None:
        """Decision 已持久化后的控制状态（Phase 5 P-6；ACTIVE + pending_decision_ref）。"""
        ...

    async def arecord_decision_checkpoint(
        self,
        *,
        step_index: int,
        decision_ref: str,
        next_action: str,
        tool_call_id: str | None = None,
    ) -> AgentCheckpoint | None:
        """异步入口。"""
        ...

    def record_waiting_approval_checkpoint(
        self,
        *,
        step_index: int,
        decision_ref: str,
        tool_call_id: str | None = None,
    ) -> AgentCheckpoint | None:
        """HITL 暂停写点（Phase 5 P-7）：PAUSED / WAITING_APPROVAL（§十四）。

        字段契约（已冻结）：``pending_step_index = step_index`` ·
        ``pending_decision_ref = decision_ref`` · ``pending_tool_call_id = tool_call_id`` ·
        ``pending_tool_result_ref = NULL``（Tool **尚未执行**）。
        """
        ...

    async def arecord_waiting_approval_checkpoint(
        self,
        *,
        step_index: int,
        decision_ref: str,
        tool_call_id: str | None = None,
    ) -> AgentCheckpoint | None:
        """异步入口。"""
        ...

    def get(self, run_id: str) -> AgentCheckpoint | None:
        """读取 current checkpoint（Resume 专用；**读失败抛异常**）。"""
        ...

    def request_cancel(self, run_id: str) -> AgentCheckpoint:
        """协作式取消信号 CAS（Phase 5 P-7；**fail-closed**，与 get / claim 同类）。

        ``UPDATE … WHERE run_id = ? AND cancel_requested = false AND status != TERMINAL
          SET cancel_requested = true, cancel_requested_at = now(),
              state_version = state_version + 1``

        与所有控制状态转换共用 ``state_version`` 域（HITL / Resume / 终态写入
        天然互斥；§三十六 / §五十四）。
        """
        ...

    def claim_for_resume(
        self,
        *,
        run_id: str,
        expected_state_version: int,
        claim_token: str,
    ) -> AgentCheckpoint:
        """Resume 的乐观并发声明（版本 +1；冲突抛异常；§三十五）。"""
        ...

    def record_terminal(
        self,
        *,
        run_id: str,
        agent_status: str,
        agent_error: str | None = None,
    ) -> AgentCheckpoint | None:
        """Agent 终态（TERMINAL / COMPLETED|FAILED；run_id 由调用方只读传入）。"""
        ...

    async def arecord_terminal(
        self,
        *,
        run_id: str,
        agent_status: str,
        agent_error: str | None = None,
    ) -> AgentCheckpoint | None:
        """异步入口。"""
        ...


class BestEffortAgentCheckpointRecorder:
    """Best-effort Checkpoint 记录器（与既有 recorder 同风格）。

    契约（§三十一 / §三十二 / §四十六）：

        * run_id 来源 = ``current_agent_run_id()``（Agent Run Scope）；
          无 scope ⇒ **不写**（best-effort；绝不猜 request_id → run_id）；
        * Repository 注入（``None`` → 默认仓储，延迟 import）：Runtime **不**接触 DB；
        * 乐观并发：先 ``get`` 读当前 version，再以
          ``run_id + expected_state_version + claim_token`` 执行**单条** UPDATE；
          版本 / claim 不匹配 ⇒ ``CHECKPOINT_CONFLICT``（**不**自动接管、**不**重试、
          **不**重新生成 claim）⇒ warning + ``None``；
        * 任何 ``Exception`` 只记 warning（``error_type``）：不改变 Tool / Agent
          业务结果，不 retry LLM / Tool；``CancelledError`` 不吞（取消优先）。
    """

    def __init__(
        self,
        repository: Any,
        *,
        claim_token_factory: Any = new_claim_token,
    ) -> None:
        """构造记录器（**repository 必须注入**：本模块是纯域层，不 import DB）。

        Args:
            repository: ``AgentCheckpointRepository``（composition root 注入；
                生产装配见同包 recorder 模块的 ``build_agent_checkpoint_recorder``）。
            claim_token_factory: 新 Checkpoint 的 claim token 生成器
                （默认 :func:`new_claim_token`；测试可注入确定性实现）。
        """
        for method in ("get", "create", "update"):
            if not callable(getattr(repository, method, None)):
                raise ValueError(
                    f"repository 必须提供可调用的 {method}()"
                    f"（got {type(repository).__name__}）"
                )
        if not callable(claim_token_factory):
            raise ValueError("claim_token_factory 必须是可调用对象")
        self._repository = repository
        self._claim_token_factory = claim_token_factory

    @property
    def repository(self) -> Any:
        """底层仓储（只读暴露，便于测试断言注入关系）。"""
        return self._repository

    # ---------- 入口 ----------

    def record_tool_result_checkpoint(
        self, *, step_index: int, tool_result_persisted: bool
    ) -> AgentCheckpoint | None:
        """Tool Result 之后的 ACTIVE checkpoint（同步入口；永不抛出 ``Exception``）。"""
        run_id = current_agent_run_id()
        if not isinstance(run_id, str) or not run_id.strip():
            logger.debug(
                "Checkpoint 未写入：无 Agent Run Scope（best-effort；绝不猜测 run_id）",
                extra={"step_index": step_index},
            )
            return None
        try:
            return self._save(
                run_id=run_id,
                status=CHECKPOINT_STATUS_ACTIVE,
                # Tool 已完成 ⇒ 下一步重新进入 LLM Decision（§三十三）
                next_action=CHECKPOINT_NEXT_ACTION_LLM_DECISION,
                pending_step_index=step_index + 1,
                # next_action != TOOL_EXECUTION ⇒ 必须为 NULL（§二十二）
                pending_tool_call_id=None,
                # 尚无独立 Decision Store ⇒ NULL（§二十三）
                pending_decision_ref=None,
                # 仅当 Tool Result **已经 durable** 才引用（§二十四 / §三十八 / §四十）
                pending_tool_result_ref=(
                    tool_result_ref(run_id, step_index)
                    if tool_result_persisted
                    else None
                ),
            )
        except Exception as exc:  # noqa: BLE001 —— best-effort：绝不穿透为业务失败
            self._log_failure(exc, run_id=run_id)
            return None

    async def arecord_tool_result_checkpoint(
        self, *, step_index: int, tool_result_persisted: bool
    ) -> AgentCheckpoint | None:
        """异步入口（同步持久化放进 worker 线程；``CancelledError`` 不吞）。"""
        try:
            return await asyncio.to_thread(
                self.record_tool_result_checkpoint,
                step_index=step_index,
                tool_result_persisted=tool_result_persisted,
            )
        except Exception as exc:  # noqa: BLE001 —— 兜底（record 本身已 best-effort）
            self._log_failure(exc, run_id=current_agent_run_id())
            return None

    def record_decision_checkpoint(
        self,
        *,
        step_index: int,
        decision_ref: str,
        next_action: str,
        tool_call_id: str | None = None,
    ) -> AgentCheckpoint | None:
        """Decision 已持久化后的 ACTIVE checkpoint（P-6；同步入口；永不抛出 ``Exception``）。

        * ``pending_step_index`` = 该 Decision 的 step（与 ``decision_ref`` 同 step，
          满足 §十八 一致性校验）；
        * ``pending_decision_ref`` = ``"{run_id}:{step_index}"``（§十五）；
        * ``pending_tool_call_id`` 仅在 ``next_action=TOOL_EXECUTION`` 时非 NULL（§二十二）。
        """
        run_id = current_agent_run_id()
        if not isinstance(run_id, str) or not run_id.strip():
            logger.debug(
                "Decision Checkpoint 未写入：无 Agent Run Scope（best-effort）",
                extra={"step_index": step_index},
            )
            return None
        try:
            return self._save(
                run_id=run_id,
                status=CHECKPOINT_STATUS_ACTIVE,
                next_action=next_action,
                pending_step_index=step_index,
                pending_tool_call_id=tool_call_id,
                pending_decision_ref=decision_ref,
                pending_tool_result_ref=None,
            )
        except Exception as exc:  # noqa: BLE001 —— best-effort
            self._log_failure(exc, run_id=run_id)
            return None

    async def arecord_decision_checkpoint(
        self,
        *,
        step_index: int,
        decision_ref: str,
        next_action: str,
        tool_call_id: str | None = None,
    ) -> AgentCheckpoint | None:
        """异步入口（``CancelledError`` 不吞）。"""
        try:
            return await asyncio.to_thread(
                self.record_decision_checkpoint,
                step_index=step_index,
                decision_ref=decision_ref,
                next_action=next_action,
                tool_call_id=tool_call_id,
            )
        except Exception as exc:  # noqa: BLE001 —— 兜底
            self._log_failure(exc, run_id=current_agent_run_id())
            return None

    def record_waiting_approval_checkpoint(
        self,
        *,
        step_index: int,
        decision_ref: str,
        tool_call_id: str | None = None,
    ) -> AgentCheckpoint | None:
        """HITL 暂停写点（P-7；同步入口；永不抛出 ``Exception``）。

        * ``status = PAUSED``（**不是** TERMINAL：Run 尚未终态，§十六）；
        * ``next_action = WAITING_APPROVAL``；
        * ``pending_step_index = step_index``（该 Tool Call 的 execution identity）；
        * ``pending_decision_ref`` = 已持久化的 TOOL_CALL Decision（Resume 依据）；
        * ``pending_tool_result_ref = NULL``（Tool **未**执行 ⇒ 无结果，§十五）。
        """
        run_id = current_agent_run_id()
        if not isinstance(run_id, str) or not run_id.strip():
            logger.debug(
                "WAITING_APPROVAL Checkpoint 未写入：无 Agent Run Scope（best-effort）",
                extra={"step_index": step_index},
            )
            return None
        try:
            return self._save(
                run_id=run_id,
                status=CHECKPOINT_STATUS_PAUSED,
                next_action=CHECKPOINT_NEXT_ACTION_WAITING_APPROVAL,
                pending_step_index=step_index,
                pending_tool_call_id=tool_call_id,
                pending_decision_ref=decision_ref,
                pending_tool_result_ref=None,
            )
        except Exception as exc:  # noqa: BLE001 —— best-effort（审批行已 durable，可重算）
            self._log_failure(exc, run_id=run_id)
            return None

    async def arecord_waiting_approval_checkpoint(
        self,
        *,
        step_index: int,
        decision_ref: str,
        tool_call_id: str | None = None,
    ) -> AgentCheckpoint | None:
        """异步入口（``CancelledError`` 不吞）。"""
        try:
            return await asyncio.to_thread(
                self.record_waiting_approval_checkpoint,
                step_index=step_index,
                decision_ref=decision_ref,
                tool_call_id=tool_call_id,
            )
        except Exception as exc:  # noqa: BLE001 —— 兜底
            self._log_failure(exc, run_id=current_agent_run_id())
            return None

    # ---------- 读取 / Resume 声明（P-6；**读失败抛异常** = fail-closed） ----------

    def get(self, run_id: str) -> AgentCheckpoint | None:
        """读取 current checkpoint（Resume 专用）。

        Note:
            与写路径（best-effort 吞异常）不同：**读失败向调用方抛出**
            （``AgentCheckpointRepositoryError``）——Resume 必须 fail closed，
            而不是"当作不存在"。
        """
        return self._repository.get(run_id)

    def claim_for_resume(
        self,
        *,
        run_id: str,
        expected_state_version: int,
        claim_token: str,
    ) -> AgentCheckpoint:
        """Resume 的乐观并发声明（§三十五）：版本 +1、其余状态不变。

        Raises:
            AgentCheckpointConflictError: 版本 / claim 不匹配（并发 Resume 失败者）。
        """
        current = self._repository.get(run_id)
        if current is None:
            raise AgentCheckpointStoreError(
                f"run_id 没有 current checkpoint: {run_id!r}"
            )
        return self._repository.update(
            run_id=run_id,
            expected_state_version=expected_state_version,
            claim_token=claim_token,
            status=current.status,
            next_action=current.next_action,
            pending_step_index=current.pending_step_index,
            pending_tool_call_id=current.pending_tool_call_id,
            pending_decision_ref=current.pending_decision_ref,
            pending_tool_result_ref=current.pending_tool_result_ref,
        )

    def request_cancel(self, run_id: str) -> AgentCheckpoint:
        """协作式取消信号 CAS（P-7；**fail-closed**：冲突 / 失败抛异常）。

        Note:
            与写路径（best-effort）不同：取消是**控制状态转换**，调用方
            （``AgentCancellationCoordinator``）必须能区分"已置位 / 竞争 /
            不可取消"，因此本方法**不**吞异常（与 ``get`` / ``claim_for_resume``
            同类）。

        Raises:
            ValueError: run_id 非法。
            AgentCheckpointNotFoundError / AgentCheckpointConflictError /
            AgentCheckpointRepositoryError: 见 Repository（未找到 / 已终态 /
            其它转换先赢）。
        """
        return self._repository.request_cancel(run_id=run_id)

    def record_terminal(
        self,
        *,
        run_id: str,
        agent_status: str,
        agent_error: str | None = None,
    ) -> AgentCheckpoint | None:
        """Agent 终态 checkpoint（同步入口；永不抛出 ``Exception``）。

        ``run_id`` 由调用方**只读**传入（= ``AgentRuntimeResult.run_id``）——
        终态写点由 Orchestrator 触发（运行在 Agent Run Scope **之外**），
        因此不能依赖 ContextVar；持久化层**绝不**重新生成 / 推导 run_id
        （Phase 5 §30.7：只读取 AgentRuntimeResult 的身份）。
        """
        if not isinstance(run_id, str) or not run_id.strip():
            logger.debug(
                "Terminal Checkpoint 未写入：run_id 缺失（best-effort）",
                extra={"agent_status": agent_status},
            )
            return None
        mapped = checkpoint_terminal_state_of(agent_status)
        if mapped is None:
            logger.debug(
                "Terminal Checkpoint 未写入：状态无 P-5 映射（预留不可达）",
                extra={"agent_status": agent_status},
            )
            return None
        status, next_action = mapped
        try:
            return self._save(
                run_id=run_id,
                status=status,
                next_action=next_action,
                # 终态：没有 pending（§三十五）
                pending_step_index=None,
                pending_tool_call_id=None,
                pending_decision_ref=None,
                pending_tool_result_ref=None,
                # Phase 5 P-7：CANCELLED 是**不可逆**终态（§三十三 / §三十四）——
                # 取消之后到达的失败写入（例如并行 Resume 竞争失败）**不得**把它
                # 改写成 FAILED / COMPLETED（"最后写入者获胜"在取消语义下是错的）。
                terminal_agent_status=agent_status,
            )
        except Exception as exc:  # noqa: BLE001 —— best-effort
            self._log_failure(exc, run_id=run_id)
            return None

    async def arecord_terminal(
        self,
        *,
        run_id: str,
        agent_status: str,
        agent_error: str | None = None,
    ) -> AgentCheckpoint | None:
        """异步入口（``agent_error`` 只作诊断提示，**不**落盘自由文本）。"""
        try:
            return await asyncio.to_thread(
                self.record_terminal,
                run_id=run_id,
                agent_status=agent_status,
                agent_error=agent_error,
            )
        except Exception as exc:  # noqa: BLE001 —— 兜底
            self._log_failure(exc, run_id=run_id)
            return None

    # ---------- 内部 ----------

    def _save(
        self,
        *,
        run_id: str,
        status: str,
        next_action: str,
        pending_step_index: int | None,
        pending_tool_call_id: str | None,
        pending_decision_ref: str | None,
        pending_tool_result_ref: str | None,
        terminal_agent_status: str | None = None,
    ) -> AgentCheckpoint:
        """读取当前 Checkpoint → create（首次）或 update（乐观并发）。

        ``terminal_agent_status``（P-7，可选）：仅 :meth:`record_terminal` 传入 ——
        若当前 Checkpoint 已是 ``TERMINAL / CANCELLED`` 而本次终态**不是**取消，
        则**跳过**写入（取消终态不可逆；复用本次读取，不增加额外 IO）。

        注意：``get`` 只用于**选择 create / update 并取得 expected version**；
        版本判定本身在 UPDATE 的 WHERE 条件里（§二十八：绝不 SELECT-then-compare）。
        """
        repository = self._repository
        current = repository.get(run_id)
        if (
            current is not None
            and terminal_agent_status is not None
            and terminal_agent_status != AGENT_STATUS_CANCELLED
            and getattr(current, "status", None) == CHECKPOINT_STATUS_TERMINAL
            and getattr(current, "next_action", None)
            == CHECKPOINT_NEXT_ACTION_CANCELLED
        ):
            logger.info(
                "取消终态不可逆：跳过非 CANCELLED 的终态写入",
                extra={"run_id": run_id, "agent_status": terminal_agent_status},
            )
            return current
        if current is None:
            return repository.create(
                run_id=run_id,
                state_version=CHECKPOINT_INITIAL_STATE_VERSION,
                claim_token=self._claim_token_factory(),
                status=status,
                next_action=next_action,
                pending_step_index=pending_step_index,
                pending_tool_call_id=pending_tool_call_id,
                pending_decision_ref=pending_decision_ref,
                pending_tool_result_ref=pending_tool_result_ref,
            )
        # 乐观并发：expected_state_version + 同一 claim_token（成功更新**不**换 claim，§十八）
        return repository.update(
            run_id=run_id,
            expected_state_version=current.state_version,
            claim_token=current.claim_token,
            status=status,
            next_action=next_action,
            pending_step_index=pending_step_index,
            pending_tool_call_id=pending_tool_call_id,
            pending_decision_ref=pending_decision_ref,
            pending_tool_result_ref=pending_tool_result_ref,
        )

    @staticmethod
    def _log_failure(exc: BaseException, *, run_id: str | None) -> None:
        """失败只记录**诊断信息**（error_type + run_id；不含 prompt / 结果 / 密钥）。"""
        logger.warning(
            "Checkpoint 写入失败（best-effort；不影响 Tool / Agent 结果）: %s",
            type(exc).__name__,
            extra={"error_type": type(exc).__name__, "run_id": run_id},
        )


# 说明：生产装配（``build_agent_checkpoint_recorder``）位于同包内的 recorder
# 模块（**持久化边界**，与 agent_run / agent_step / tool_result 的 builder 同风格）
# —— 本模块（域层 + 端口 + best-effort 实现）保持 **0 DB import**，
# 因此 `agent` 包的"执行内核 0 DB"架构审计**无需放宽**。
