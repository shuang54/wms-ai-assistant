"""Agent HITL Approval —— Tool 执行前的**人工审批门**（Phase 5 P-7）。

职责（单一）：

```text
LLM Decision（Tool Call 提案）
        │
        ▼
Tool Authorization（Runtime 第一道 + 执行边界第二道 capability 校验）
        │
        ▼
Approval Required?（RuntimePolicy / Runtime 装配期声明的 approval_required_tools）
        │ YES
        ▼
create Approval（PENDING）+ Checkpoint（PAUSED / WAITING_APPROVAL）
        │
        ▼
WAITING_APPROVAL（Tool Handler calls = 0）
        │
   Human Decision（APPROVED / REJECTED）
        │
        ▼
Resume（重新 Authorization → Tool / FAILED）
```

三类对象**不得混淆**（§八 / §十二）：

```text
AgentDecision      = LLM 提出的 Tool Call 提案（arguments 属 Decision Store）
AgentApproval      = 人类是否允许执行（本模块；**不**保存 arguments）
AgentToolResult    = Tool 实际执行结果（P-4；审批**绝不**写入结果表）
```

身份（§十二）：``approval_id`` 是审批身份（本模块签发）；
``run_id`` / ``step_index`` 是 Agent Run / Step 身份；
``tool_call_id`` 是 Provider protocol correlation。
**不允许** ``approval_id == tool_call_id`` / ``run_id`` / ``request_id``。

状态机（§五十一，严格）：

```text
PENDING ──► APPROVED
        └─► REJECTED
```

禁止：``APPROVED → PENDING`` / ``REJECTED → PENDING`` /
``APPROVED → REJECTED`` / ``REJECTED → APPROVED``（终态不可逆；重复同一
decision ⇒ **幂等**，不同 decision ⇒ ``AGENT_APPROVAL_CONFLICT`` fail-closed）。

安全边界（§十一 / §四十二）：

    * 只保存**身份 + 状态 + 时间**：approval_id / run_id / step_index /
      tool_call_id / tool_name / status / decision / created_at / resolved_at；
    * **绝不**保存：tool arguments（连 hash 都不需要 —— 执行参数由 Decision
      Store 提供，审批只需要身份）、prompt / messages / conversation context /
      RAG chunks / tool definitions / SQL / API key / password / Authorization /
      database URL / Tool Result / LLM raw response / exception body / stack trace；
    * 本模块**不改变** Tool Result 的信任等级；**不**把审批写进 AgentToolResult；
    * ``AgentApprovalRequest`` 是**对外可见投影**（§四十二）：不含 arguments /
      prompt / context / SQL / claim_token。

边界（严格）：

    * **不是 Resume**：本模块只**写/读/裁决**审批状态；无 resume / replay /
      LLM 调用 / Tool 执行（§二十三：Approval 只改变持久化控制状态，
      Resume 才执行 Agent）；
    * **不是 Authorization**：``APPROVED`` **不等于**授权 —— Execution 前
      Runtime 必须**重新**做 Tool Authorization（§二十 / §四十一）；
    * 本阶段**无** TTL / 过期 / 定时升级 / 通知 / HTTP API（§四 / §三十九：
      ``AGENT_APPROVAL_EXPIRED`` **不实现、不注册**）。

依赖方向（禁止反向）：本模块可 import agent 内核（errors / ids）；Repository /
Session **只在调用期延迟 import**（模块加载不引入 DB 基础设施）。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Protocol

from backend.app.agent.errors import (
    AGENT_ERROR_APPROVAL_CONFLICT,
    AGENT_ERROR_APPROVAL_NOT_FOUND,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AgentApprovalRequest",
    "AgentApprovalResolution",
    "AgentApprovalStore",
    "AgentApprovalRecorder",
    "approval_request_of",
    "approval_conflict_code",
    "is_valid_approval_decision",
    "new_approval_id",
    "APPROVAL_STATUS_PENDING",
    "APPROVAL_STATUS_APPROVED",
    "APPROVAL_STATUS_REJECTED",
    "APPROVAL_STATUSES",
    "APPROVAL_DECISIONS",
    "APPROVAL_ID_MAX_LENGTH",
    "APPROVAL_RUN_ID_MAX_LENGTH",
    "APPROVAL_STEP_INDEX_MAX",
    "APPROVAL_TOOL_NAME_MAX_LENGTH",
    "APPROVAL_TOOL_CALL_ID_MAX_LENGTH",
    "APPROVAL_STATUS_MAX_LENGTH",
    "APPROVAL_DECISION_MAX_LENGTH",
]

#: 审批状态（§九 / §五十一）。
APPROVAL_STATUS_PENDING: Final[str] = "PENDING"
APPROVAL_STATUS_APPROVED: Final[str] = "APPROVED"
APPROVAL_STATUS_REJECTED: Final[str] = "REJECTED"

#: 允许的状态集合（白名单；**不新增** EXPIRED / CANCELLED —— 取消是 Run 级控制状态，
#: 不写进 Approval：审批只回答"人类是否允许"）。
APPROVAL_STATUSES: Final[frozenset[str]] = frozenset(
    {
        APPROVAL_STATUS_PENDING,
        APPROVAL_STATUS_APPROVED,
        APPROVAL_STATUS_REJECTED,
    }
)

#: 外部裁决**只**接受两种（§十三）：不允许 retry / modify / edit_tool /
#: change_arguments / change_tool（本阶段不允许人工修改 LLM Tool Call）。
APPROVAL_DECISIONS: Final[frozenset[str]] = frozenset(
    {
        APPROVAL_STATUS_APPROVED,
        APPROVAL_STATUS_REJECTED,
    }
)

# ---- 列长度上限（§十）----
APPROVAL_ID_MAX_LENGTH: Final[int] = 128
APPROVAL_RUN_ID_MAX_LENGTH: Final[int] = 64
APPROVAL_STEP_INDEX_MAX: Final[int] = 2_147_483_647
APPROVAL_TOOL_NAME_MAX_LENGTH: Final[int] = 128
APPROVAL_TOOL_CALL_ID_MAX_LENGTH: Final[int] = 128
APPROVAL_STATUS_MAX_LENGTH: Final[int] = 32
APPROVAL_DECISION_MAX_LENGTH: Final[int] = 32


def new_approval_id() -> str:
    """签发一个审批身份（``uuid4`` 文本；与 run_id 同 ID 风格）。

    Note:
        **不**复用 ``run_id`` / ``request_id`` / ``tool_call_id``（§十二）；
        不含语义信息、不含 prompt / 参数（非敏感）。
    """
    return str(uuid.uuid4())


def is_valid_approval_decision(decision: Any) -> bool:
    """裁决值是否在白名单（``APPROVED`` / ``REJECTED``）。"""
    return isinstance(decision, str) and decision in APPROVAL_DECISIONS


def approval_conflict_code(existing_status: Any, decision: str) -> str | None:
    """纯函数：对已有审批裁决新 decision 的结果码（§十八 / §十九 / §五十一）。

    Returns:
        ``None``   ⇒ **幂等**（已有状态 == 本次 decision；不产生第二次转换）
        ``AGENT_APPROVAL_CONFLICT`` ⇒ 冲突（终态不可逆 / 已有不同裁决 ⇒ fail closed）
    """
    if existing_status == decision:
        return None
    return AGENT_ERROR_APPROVAL_CONFLICT


@dataclass(frozen=True)
class AgentApprovalResolution:
    """一次审批裁决的结果（frozen；只含非敏感事实；§十七–§十九）。

    Attributes:
        approval_id: 审批身份。
        run_id:      所属 Agent Run（``None`` ⇒ 审批不存在）。
        status:      裁决后的状态（APPROVED / REJECTED；不存在 ⇒ ``None``）。
        decision:    生效的裁决值（幂等重复 ⇒ 与既有相同）。
        error_code:  ``None``（成功）· ``AGENT_APPROVAL_NOT_FOUND``（不存在）·
                     ``AGENT_APPROVAL_CONFLICT``（不同裁决 / 已终态 Run ⇒ fail-closed）。

    Note:
        **不含** arguments / prompt / claim_token / 自由文本；
        ``error_code`` 非 ``None`` 时**没有**发生任何状态转换。
    """

    approval_id: str
    run_id: str | None
    status: str | None
    decision: str | None
    error_code: str | None


@dataclass(frozen=True)
class AgentApprovalRequest:
    """审批请求的**对外可见投影**（§四十二；frozen）。

    Attributes:
        approval_id:  审批身份（本模块签发；≠ run_id / request_id / tool_call_id）。
        run_id:       Agent Run 身份（correlation，**无外键**）。
        step_index:   Agent step（0 起；= 该 Tool Call 的 execution identity）。
        tool_name:    待审批的 Tool 名（非敏感）。
        tool_call_id: Provider protocol correlation（**不是**审批身份）。
        status:       PENDING / APPROVED / REJECTED。
        created_at:   审批创建时间（UTC）。

    Note:
        **无** arguments / prompt / context / SQL / 密钥 / Tool Result；
        UI 若需展示参数，应另立 Read API（本阶段禁止，§四十三）。
    """

    approval_id: str
    run_id: str
    step_index: int
    tool_name: str
    tool_call_id: str | None
    status: str
    created_at: datetime


def approval_request_of(record: Any) -> AgentApprovalRequest:
    """持久化记录 → 对外投影（**不**透传 arguments / 内部字段）。"""
    return AgentApprovalRequest(
        approval_id=getattr(record, "approval_id"),
        run_id=getattr(record, "run_id"),
        step_index=int(getattr(record, "step_index")),
        tool_name=getattr(record, "tool_name"),
        tool_call_id=getattr(record, "tool_call_id", None),
        status=getattr(record, "status"),
        created_at=getattr(record, "created_at"),
    )


class AgentApprovalRecorder(Protocol):
    """审批持久化端口（Runtime 依赖；由 composition root 注入实现）。

    契约：

        await arequest_approval(run_id=…, step_index=…, tool_name=…, tool_call_id=…)
            → 审批行（新建或**已存在**的同一行；重复请求幂等）
            → ``None`` ⇒ **写失败**（调用方必须 fail-closed：不暂停、不执行 Tool）
        aget_for_step(*, run_id, step_index)
            → 审批行 | ``None``（读取失败**抛异常** ⇒ 调用方 fail-closed）
        aresolve(*, approval_id, decision)
            → 裁决后的审批行 | ``None``（无转换：状态已是终态/其它 → 由调用方判幂等/冲突）

    实现**必须**保证：审批行不保存 arguments（§十一）；
    ``(run_id, step_index)`` 唯一（一个 Tool 执行身份最多一个审批）。
    """

    async def arequest_approval(
        self,
        *,
        run_id: str,
        step_index: int,
        tool_name: str,
        tool_call_id: str | None = None,
    ) -> Any | None: ...

    async def aget_for_step(self, *, run_id: str, step_index: int) -> Any | None: ...

    async def aresolve(self, *, approval_id: str, decision: str) -> Any | None: ...


class AgentApprovalStore:
    """审批持久化的**唯一生产实现**（Repository 注入；fail-closed 语义）。

    与 ``AgentDecisionStore`` 同风格：

        * Repository 由 composition root 注入（``None`` → 默认仓储，调用期延迟 import）；
        * ``request_approval`` 返回 ``None`` ⇒ 写失败（**不**吞成"已创建"）；
        * ``get_for_step`` 读取失败**抛异常**（Resume 必须 fail closed，
          而不是"当作不存在"）；
        * ``resolve`` 只做**条件 UPDATE**（``WHERE approval_id=? AND status='PENDING'``）：
          无转换 ⇒ ``None``（调用方决定幂等 / 冲突 —— 绝不覆盖已终态审批）。

    Note:
        本类**不**调用 LLM / Tool 执行服务 / Resume（§二十三：审批只改控制状态）。
    """

    def __init__(self, repository: Any) -> None:
        """构造审批存储（**repository 必须注入**：本模块是纯域层，不 import DB）。

        Args:
            repository: ``AgentApprovalRepository``（composition root 注入；
                生产装配见同包 recorder 模块的 ``build_agent_approval_store``）。

        Raises:
            ValueError: repository 缺少 create / get / get_for_step / resolve。
        """
        for method in ("create", "get", "get_for_step", "resolve"):
            if not callable(getattr(repository, method, None)):
                raise ValueError(
                    f"repository 必须提供可调用的 {method}()"
                    f"（got {type(repository).__name__}）"
                )
        self._repository = repository

    @property
    def repository(self) -> Any:
        """底层仓储（只读暴露，便于测试断言注入关系）。"""
        return self._repository

    # ---------- 写（Tool 执行前；fail-closed） ----------

    def request_approval(
        self,
        *,
        run_id: str,
        step_index: int,
        tool_name: str,
        tool_call_id: str | None = None,
    ) -> Any | None:
        """创建（或返回已存在的）审批行；写失败 ⇒ ``None``（调用方 fail-closed）。"""
        try:
            return self._repository.create(
                approval_id=new_approval_id(),
                run_id=run_id,
                step_index=step_index,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
            )
        except Exception as exc:  # noqa: BLE001 —— fail-closed（调用方不再暂停/执行）
            logger.warning(
                "Approval 请求持久化失败（fail-closed；不暂停、不执行 Tool）: %s",
                type(exc).__name__,
                extra={
                    "error_type": type(exc).__name__,
                    "run_id": run_id,
                    "step_index": step_index,
                },
            )
            return None

    async def arequest_approval(
        self,
        *,
        run_id: str,
        step_index: int,
        tool_name: str,
        tool_call_id: str | None = None,
    ) -> Any | None:
        """异步入口（同步持久化放进 worker 线程；``CancelledError`` 不吞）。"""
        try:
            return await asyncio.to_thread(
                self.request_approval,
                run_id=run_id,
                step_index=step_index,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
            )
        except Exception as exc:  # noqa: BLE001 —— 兜底（request 本身已 fail-closed）
            logger.warning(
                "Approval 请求持久化失败（fail-closed）: %s",
                type(exc).__name__,
                extra={"error_type": type(exc).__name__, "run_id": run_id},
            )
            return None

    # ---------- 读（Resume；**读失败抛异常** = fail-closed） ----------

    def get_for_step(self, *, run_id: str, step_index: int) -> Any | None:
        """按执行身份 ``(run_id, step_index)`` 读取审批行（读取失败抛异常）。"""
        return self._repository.get_for_step(
            run_id=run_id, step_index=step_index
        )

    async def aget_for_step(self, *, run_id: str, step_index: int) -> Any | None:
        """异步入口（读失败抛异常 ⇒ 调用方 fail-closed）。"""
        return await asyncio.to_thread(
            self.get_for_step, run_id=run_id, step_index=step_index
        )

    def get(self, approval_id: str) -> Any | None:
        """按审批身份读取（读取失败抛异常）。"""
        return self._repository.get(approval_id)

    async def aget(self, approval_id: str) -> Any | None:
        """异步入口（读失败抛异常）。"""
        return await asyncio.to_thread(self.get, approval_id)

    # ---------- 裁决（条件 UPDATE；终态不可逆） ----------

    def resolve(self, *, approval_id: str, decision: str) -> Any | None:
        """条件裁决：``PENDING → decision``；无转换 ⇒ ``None``（不覆盖）。

        Raises:
            ValueError: decision 非法（**只**接受 APPROVED / REJECTED；§十三）。
        """
        if not is_valid_approval_decision(decision):
            raise ValueError(
                "approval decision 只接受 APPROVED / REJECTED"
                f"（当前: {decision!r}）"
            )
        return self._repository.resolve(
            approval_id=approval_id, decision=decision
        )

    async def aresolve(self, *, approval_id: str, decision: str) -> Any | None:
        """异步入口（无转换 ⇒ ``None``；非法 decision ⇒ ValueError）。"""
        return await asyncio.to_thread(
            self.resolve, approval_id=approval_id, decision=decision
        )

    # ---------- 对外投影 ----------

    def request_projection_of(self, record: Any) -> AgentApprovalRequest:
        """持久化记录 → 对外投影（**无** arguments / 内部字段；§四十二）。"""
        return approval_request_of(record)

    @staticmethod
    def not_found_code() -> str:
        """审批缺失 / 身份不一致的稳定码（fail-closed）。"""
        return AGENT_ERROR_APPROVAL_NOT_FOUND
