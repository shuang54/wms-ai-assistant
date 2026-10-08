"""Agent Read Service（Phase 5 P-8 —— Agent Runtime 的**只读**查询服务）。

定位（Command / Query 分离）：

```text
Command（既有内部 Application Service；本阶段**不**改动）
    execute · resume · resolve_agent_approval · cancel_agent_run

Query（本服务）
    get_run()        → AgentRunView（Run 摘要 + Checkpoint / Approval / Step /
                       Decision / ToolResult 视图）
    get_checkpoint() → AgentCheckpointView | None
    get_approval()   → AgentApprovalView | None
    get_timeline()   → AgentTimelineEvent[]（deterministic 排序 + 分页）
```

硬约束（§六 / §二十七）：

    * **Query only**：不调用 LLM / Tool / Resume / Approve / Cancel；
      不写库（本服务**只**调用 Repository 的只读方法）；
    * 组合 6 个既有数据源（AgentRun / AgentStep / AgentDecision /
      AgentToolResult / AgentCheckpoint / AgentApproval）；
    * **不新增表 / 不改 schema / 不引入 event sourcing**：Timeline 是
      Read Model，由既有行组合而来；
    * **不修正时钟**：``agent_run.started_at / finished_at`` 是**应用时钟**，
      ``agent_step / agent_decision / agent_tool_result / agent_approval`` 的
      ``created_at`` 是**DB 时钟**（写入时刻）—— 二者可能交叉，因此终态事件
      **不保证**排在最后；Timeline 只按 ``occurred_at`` 排序，**不**重排 /
      **不**修正（与既有 Assistant Timeline 的"跨钟域不比较"原则一致）；
    * **不伪造事实**：某类事件若当前数据不足以可靠生成 ⇒ 不返回该事件
      （``STEP_STARTED`` 目前不产生：``agent_step`` 只持久化写入时刻
      ``created_at`` 与 ``elapsed_ms``，没有可证明的"开始时刻"）；
    * **顺序 deterministic**：``occurred_at`` + 稳定 tie-breaker
      （event rank → step_index → event_type）⇒ 与输入顺序 / dict / set /
      DB 自然顺序无关；
    * **性能**：每个数据源**恰好一次**查询（6 次读 + 内存 merge），无 N+1。

项目隔离（§二十一 / §二十二）：本服务的每个公开方法都要求 ``project_id``：
归属只能由**带 project 的持久化行**证明（``agent_run.project_id`` 或
``agent_tool_result.project_id``）；无法证明 ⇒ :class:`AgentRunNotFoundError`
（与"不存在"**同码**，不泄露存在性）。**绝不在 API 层猜 project**。

Timeline 事件与来源（§十四 / §十五；只列**可证明**的事实）：

```text
AgentRun.started_at                → RUN_STARTED
AgentRun.finished_at + status      → RUN_COMPLETED / RUN_FAILED /
                                     RUN_CANCELLED / RUN_LIMIT_REACHED
AgentDecision.created_at           → DECISION_RECORDED
AgentApproval.created_at           → APPROVAL_REQUIRED（请求时刻状态 = PENDING）
AgentApproval.resolved_at          → APPROVAL_RESOLVED（仅已裁决时）
AgentToolResult.created_at         → TOOL_EXECUTED
AgentStep.created_at + status      → STEP_COMPLETED（记录时刻 = 完成事实落盘时刻；
                                     duration_ms 来自同一行）
AgentCheckpoint                    → 不产生事件（无独立时间事实）；它是
                                     AgentRunView.checkpoint 的来源
```

边界（**明确不做**）：

    * 不返回 ``claim_token`` / tool arguments / Decision content /
      Tool Result payload / prompt / SQL / 凭据 / LLM raw response；
    * 不做 Artifact / Result Detail（完整 Tool Result 属未来阶段）；
    * 不做 cursor pagination / streaming / 缓存；
    * 不做 RBAC（project-scoped query boundary 是 P-8 唯一授权语义）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Sequence

from backend.app.agent.approval import APPROVAL_STATUS_PENDING
from backend.app.agent.checkpoint import AgentCheckpoint
from backend.app.agent.decision_store import AgentDecisionRecord
from backend.app.agent.recorder import AgentRunRecord
from backend.app.db.agent_approval_repository import AgentApprovalRead
from backend.app.db.agent_step_repository import AgentStepRead
from backend.app.db.agent_tool_result_repository import AgentToolResultRead
from backend.app.dto.agent_read import (
    AGENT_READ_DEFAULT_LIMIT,
    AGENT_READ_MAX_LIMIT,
    AGENT_READ_PROJECT_ID_MAX_LENGTH,
    AGENT_READ_RUN_ID_MAX_LENGTH,
    TERMINAL_EVENT_TYPE_BY_STATUS,
    TIMELINE_EVENT_RANK,
    AgentApprovalView,
    AgentCheckpointView,
    AgentDecisionView,
    AgentRunSummary,
    AgentRunView,
    AgentStepView,
    AgentTimelineEvent,
    AgentTimelineEventType,
    AgentToolResultView,
)

__all__ = [
    "AgentReadService",
    "AgentRunNotFoundError",
    "TOOL_EVENT_STATUS_OK",
    "TOOL_EVENT_STATUS_FAILED",
]

#: TOOL_EXECUTED 事件的 ``status``（与 AgentStep 的 OK / FAILED 同一词汇；§十四）。
TOOL_EVENT_STATUS_OK: Final[str] = "OK"
TOOL_EVENT_STATUS_FAILED: Final[str] = "FAILED"


class AgentRunNotFoundError(Exception):
    """Run 不存在**或**不属于请求的 project（**故意同码**；§二十二 / §二十三）。

    为什么合并：跨 project 若返回 403 / 404 之外的码，会泄露"该 run_id 存在"。
    本阶段唯一授权语义 = project-scoped query boundary ⇒ 统一"不可见"。

    文案**固定**（不回显 run_id、不区分"不存在"与"跨 project"）⇒ 无法通过
    错误信息做存在性探测 / 枚举。
    """

    def __init__(self, message: str = "Agent Run 不存在或不可见") -> None:
        super().__init__(message)


@dataclass(frozen=True)
class _LoadedRun:
    """一次 Read Model 组合所需的**全部**既有行（每源一次查询）。"""

    run: AgentRunRecord | None
    steps: tuple[AgentStepRead, ...]
    decisions: tuple[AgentDecisionRecord, ...]
    tool_results: tuple[AgentToolResultRead, ...]
    approvals: tuple[AgentApprovalRead, ...]
    checkpoint: AgentCheckpoint | None


@dataclass(frozen=True)
class _EventDraft:
    """排序前的候选事件（排序后再赋 ``sequence``）。"""

    event_type: str
    occurred_at: datetime
    step_index: int | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    status: str | None = None
    error_code: str | None = None
    duration_ms: float | None = None

    def sort_key(self) -> tuple[Any, ...]:
        """``(occurred_at, event_rank, step_index, event_type)``（全序、可复现）。"""
        return (
            self.occurred_at,
            TIMELINE_EVENT_RANK.get(self.event_type, 99),
            -1 if self.step_index is None else self.step_index,
            self.event_type,
        )


class AgentReadService:
    """Agent Runtime 只读查询服务（Query Only；Repository 必须注入）。"""

    def __init__(
        self,
        *,
        run_repository: Any,
        step_repository: Any,
        decision_repository: Any,
        tool_result_repository: Any,
        checkpoint_repository: Any,
        approval_repository: Any,
    ) -> None:
        """构造只读服务。

        Args:
            run_repository: ``AgentRunRepository``（只使用 ``get``）。
            step_repository: ``AgentStepRepository``（只使用 ``list_for_run``）。
            decision_repository: ``AgentDecisionRepository``（``list_for_run``）。
            tool_result_repository: ``AgentToolResultRepository``（``list_for_run``）。
            checkpoint_repository: ``AgentCheckpointRepository``（``get``）。
            approval_repository: ``AgentApprovalRepository``（``list_for_run``）。

        Raises:
            ValueError: 任一依赖缺少所需**只读**方法（装配期 fail-fast）。
        """
        self._run_repository = _require(run_repository, "run_repository", ("get",))
        self._step_repository = _require(
            step_repository, "step_repository", ("list_for_run",)
        )
        self._decision_repository = _require(
            decision_repository, "decision_repository", ("list_for_run",)
        )
        self._tool_result_repository = _require(
            tool_result_repository, "tool_result_repository", ("list_for_run",)
        )
        self._checkpoint_repository = _require(
            checkpoint_repository, "checkpoint_repository", ("get",)
        )
        self._approval_repository = _require(
            approval_repository, "approval_repository", ("list_for_run",)
        )

    # ---------- 公开 Query（§二十四） ----------

    def get_run(self, run_id: str, project_id: str) -> AgentRunView:
        """读取一次 Agent Run 的组合只读视图。

        Args:
            run_id:      Agent Run 身份（``agent_run.run_id``）。
            project_id:  当前 project（**必须**由调用方提供；不猜）。

        Returns:
            :class:`AgentRunView`（``run`` 为 ``None`` ⇒ 尚无终态执行记录，
            例如 HITL 暂停 / 崩溃窗口；此时状态只在 ``checkpoint`` 中表达）。

        Raises:
            ValueError:            参数非法（400）。
            AgentRunNotFoundError: 不存在 / 不属于该 project（404）。
        """
        loaded = self._load(run_id, project_id)
        return AgentRunView(
            run=self._run_summary(loaded.run) if loaded.run is not None else None,
            checkpoint=(
                self._checkpoint_view(loaded.checkpoint)
                if loaded.checkpoint is not None
                else None
            ),
            approval=(
                self._approval_view(loaded.approvals[-1])
                if loaded.approvals
                else None
            ),
            steps=tuple(self._step_view(step) for step in loaded.steps),
            decisions=tuple(
                self._decision_view(decision) for decision in loaded.decisions
            ),
            tool_results=tuple(
                self._tool_result_view(item) for item in loaded.tool_results
            ),
        )

    def get_checkpoint(self, run_id: str, project_id: str) -> AgentCheckpointView | None:
        """读取 Control State 视图（无 ⇒ ``None``；**不含** ``claim_token``）。"""
        loaded = self._load(run_id, project_id)
        if loaded.checkpoint is None:
            return None
        return self._checkpoint_view(loaded.checkpoint)

    def get_approval(self, run_id: str, project_id: str) -> AgentApprovalView | None:
        """读取该 Run 的**最后一条**审批视图（无 ⇒ ``None``）。"""
        loaded = self._load(run_id, project_id)
        if not loaded.approvals:
            return None
        return self._approval_view(loaded.approvals[-1])

    def get_timeline(
        self,
        run_id: str,
        project_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[AgentTimelineEvent, ...]:
        """读取 Timeline（deterministic 排序的**只读**事件序列）。

        Args:
            run_id:     Agent Run 身份。
            project_id: 当前 project。
            limit:      每页条数（``None`` ⇒ 默认 50；有效范围 1..100）。
            offset:     跳过条数（``>= 0``）。

        Returns:
            分页后的 :class:`AgentTimelineEvent` 元组（``sequence`` 是**投影下标**，
            分页不会重新编号 —— 与全量排序一致，便于客户端拼接）。

        Raises:
            ValueError:            参数非法（400：limit ≤ 0 / > 100 / offset < 0）。
            AgentRunNotFoundError: 不存在 / 不属于该 project（404）。
        """
        validated_limit, validated_offset = self._validated_pagination(limit, offset)
        validated_run_id = self._validated_run_id(run_id)
        loaded = self._load(validated_run_id, project_id)
        events = self._build_timeline(loaded, run_id=validated_run_id)
        return tuple(events[validated_offset : validated_offset + validated_limit])

    # ---------- 输入校验（触达 DB 之前） ----------

    @staticmethod
    def _validated_run_id(run_id: object) -> str:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id 必须是非空 str")
        if len(run_id) > AGENT_READ_RUN_ID_MAX_LENGTH:
            raise ValueError("run_id 超出长度上限")
        return run_id

    @staticmethod
    def _validated_project_id(project_id: object) -> str:
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("project_id 必须是非空 str")
        if len(project_id) > AGENT_READ_PROJECT_ID_MAX_LENGTH:
            raise ValueError("project_id 超出长度上限")
        return project_id

    @staticmethod
    def _validated_pagination(
        limit: object, offset: object
    ) -> tuple[int, int]:
        if offset is None:
            offset = 0
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset 必须是 >= 0 的 int")
        if limit is None:
            limit = AGENT_READ_DEFAULT_LIMIT
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit 必须是 int")
        if limit <= 0:
            raise ValueError("limit 必须 >= 1")
        if limit > AGENT_READ_MAX_LIMIT:
            # **server-side cap**（§二十）：绝不把巨大 limit 透传给 DB
            raise ValueError(f"limit 不能超过 {AGENT_READ_MAX_LIMIT}")
        return limit, offset

    # ---------- 读取（每源一次；唯一 DB 访问点） ----------

    def _load(self, run_id: str, project_id: str) -> _LoadedRun:
        """组合 6 个数据源（每源一次查询）+ 归属校验（fail-closed）。"""
        validated_run_id = self._validated_run_id(run_id)
        validated_project_id = self._validated_project_id(project_id)

        # ① 归属判定只需 Run + Tool Result（先读这两者；跨 project 探测因此
        #    不会触发其余 4 次查询）。
        run = self._run_repository.get(validated_run_id)
        tool_results = tuple(
            self._tool_result_repository.list_for_run(validated_run_id)
        )
        if not _owns(run, tool_results, validated_project_id):
            # 不区分"不存在"与"不属于本 project"（不泄露存在性；固定文案）
            raise AgentRunNotFoundError()

        steps = tuple(self._step_repository.list_for_run(validated_run_id))
        decisions = tuple(
            self._decision_repository.list_for_run(validated_run_id)
        )
        approvals = tuple(self._approval_repository.list_for_run(validated_run_id))
        checkpoint = self._checkpoint_repository.get(validated_run_id)
        return _LoadedRun(
            run=run,
            steps=steps,
            decisions=decisions,
            tool_results=tool_results,
            approvals=approvals,
            checkpoint=checkpoint,
        )

    # ---------- 纯映射（既有行 → 只读视图；显式逐字段） ----------

    @staticmethod
    def _run_summary(record: AgentRunRecord) -> AgentRunSummary:
        return AgentRunSummary(
            run_id=record.run_id,
            request_id=record.request_id,
            project_id=record.project_id,
            status=record.status,
            error_code=record.error_code,
            limit_reason=record.limit_reason,
            step_count=record.step_count,
            tool_call_count=record.tool_call_count,
            llm_call_count=record.llm_call_count,
            token_usage_unverifiable=record.token_usage_unverifiable,
            started_at=record.started_at,
            finished_at=record.finished_at,
            duration_ms=record.duration_ms,
        )

    @staticmethod
    def _step_view(record: AgentStepRead) -> AgentStepView:
        return AgentStepView(
            step_index=record.step_index,
            step_type=record.step_type,
            status=record.status,
            tool_name=record.tool_name,
            error_code=record.error_code,
            duration_ms=record.elapsed_ms,
            created_at=record.created_at,
        )

    @staticmethod
    def _decision_view(record: AgentDecisionRecord) -> AgentDecisionView:
        """Decision → 视图（**只**元数据；``arguments`` / ``content`` 绝不进入）。"""
        return AgentDecisionView(
            step_index=record.step_index,
            decision_type=record.decision_type,
            tool_call_id=record.tool_call_id,
            tool_name=record.tool_name,
            finish_reason=record.finish_reason,
            decision_unavailable=record.decision_unavailable,
            created_at=record.created_at,
        )

    @staticmethod
    def _tool_result_view(record: AgentToolResultRead) -> AgentToolResultView:
        """Tool Result → 视图（**只**元数据；``payload`` / ``ref`` 绝不进入）。"""
        return AgentToolResultView(
            step_index=record.step_index,
            tool_name=record.tool_name,
            success=record.success,
            error_code=record.error_code,
            result_hash=record.result_hash,
            result_size_bytes=record.result_size_bytes,
            result_truncated=record.result_truncated,
            result_unavailable=record.result_unavailable,
            tool_call_id=record.tool_call_id,
            created_at=record.created_at,
        )

    @staticmethod
    def _checkpoint_view(checkpoint: AgentCheckpoint) -> AgentCheckpointView:
        """Checkpoint → 视图（**绝不**暴露 ``claim_token``；§九）。"""
        return AgentCheckpointView(
            run_id=checkpoint.run_id,
            state_version=checkpoint.state_version,
            status=checkpoint.status,
            next_action=checkpoint.next_action,
            pending_step_index=checkpoint.pending_step_index,
            pending_tool_call_id=checkpoint.pending_tool_call_id,
            pending_decision_ref=checkpoint.pending_decision_ref,
            pending_tool_result_ref=checkpoint.pending_tool_result_ref,
            cancel_requested=bool(checkpoint.cancel_requested),
            cancel_requested_at=checkpoint.cancel_requested_at,
            updated_at=checkpoint.updated_at,
        )

    @staticmethod
    def _approval_view(record: AgentApprovalRead) -> AgentApprovalView:
        return AgentApprovalView(
            approval_id=record.approval_id,
            run_id=record.run_id,
            step_index=record.step_index,
            tool_call_id=record.tool_call_id,
            tool_name=record.tool_name,
            status=record.status,
            decision=record.decision,
            reason_code=record.reason_code,
            created_at=record.created_at,
            resolved_at=record.resolved_at,
        )

    # ---------- Timeline 组合（§十三 – §十八） ----------

    def _build_timeline(
        self, loaded: _LoadedRun, *, run_id: str
    ) -> list[AgentTimelineEvent]:
        """既有行 → deterministic 事件序列（先收集 → 全序排序 → 编号）。"""
        drafts: list[_EventDraft] = []

        # ① AgentRun：开始 + 终态（终态事件**逐字**映射；CANCELLED ≠ FAILED）
        if loaded.run is not None:
            drafts.append(
                _EventDraft(
                    event_type=AgentTimelineEventType.RUN_STARTED.value,
                    occurred_at=loaded.run.started_at,
                )
            )
            terminal_type = TERMINAL_EVENT_TYPE_BY_STATUS.get(loaded.run.status)
            if terminal_type is not None:
                drafts.append(
                    _EventDraft(
                        event_type=terminal_type,
                        occurred_at=loaded.run.finished_at,
                        status=loaded.run.status,
                        error_code=loaded.run.error_code,
                        duration_ms=loaded.run.duration_ms,
                    )
                )

        # ② AgentDecision：决策落盘（step_index 可靠）
        for decision in loaded.decisions:
            drafts.append(
                _EventDraft(
                    event_type=AgentTimelineEventType.DECISION_RECORDED.value,
                    occurred_at=decision.created_at,
                    step_index=decision.step_index,
                    tool_name=decision.tool_name,
                    tool_call_id=decision.tool_call_id,
                    status=decision.decision_type,
                )
            )

        # ③ AgentApproval：请求（当时必然是 PENDING）+ 裁决（仅已裁决时）
        for approval in loaded.approvals:
            drafts.append(
                _EventDraft(
                    event_type=AgentTimelineEventType.APPROVAL_REQUIRED.value,
                    occurred_at=approval.created_at,
                    step_index=approval.step_index,
                    tool_name=approval.tool_name,
                    tool_call_id=approval.tool_call_id,
                    status=APPROVAL_STATUS_PENDING,
                )
            )
            if approval.resolved_at is not None:
                drafts.append(
                    _EventDraft(
                        event_type=AgentTimelineEventType.APPROVAL_RESOLVED.value,
                        occurred_at=approval.resolved_at,
                        step_index=approval.step_index,
                        tool_name=approval.tool_name,
                        tool_call_id=approval.tool_call_id,
                        status=approval.status,
                    )
                )

        # ④ AgentToolResult：执行事实（结果正文**不**进入事件）
        for tool_result in loaded.tool_results:
            drafts.append(
                _EventDraft(
                    event_type=AgentTimelineEventType.TOOL_EXECUTED.value,
                    occurred_at=tool_result.created_at,
                    step_index=tool_result.step_index,
                    tool_name=tool_result.tool_name,
                    tool_call_id=tool_result.tool_call_id,
                    status=(
                        TOOL_EVENT_STATUS_OK
                        if tool_result.success
                        else TOOL_EVENT_STATUS_FAILED
                    ),
                    error_code=tool_result.error_code,
                )
            )

        # ⑤ AgentStep：Step 事实落盘（= 完成事实；**不**伪造 STEP_STARTED）
        for step in loaded.steps:
            drafts.append(
                _EventDraft(
                    event_type=AgentTimelineEventType.STEP_COMPLETED.value,
                    occurred_at=step.created_at,
                    step_index=step.step_index,
                    tool_name=step.tool_name,
                    status=step.status,
                    error_code=step.error_code,
                    duration_ms=step.elapsed_ms,
                )
            )

        # 全序排序（deterministic；与输入顺序无关）→ 赋投影下标
        drafts.sort(key=_EventDraft.sort_key)
        return [
            AgentTimelineEvent(
                sequence=index,
                event_type=draft.event_type,
                occurred_at=draft.occurred_at,
                run_id=run_id,
                step_index=draft.step_index,
                tool_name=draft.tool_name,
                tool_call_id=draft.tool_call_id,
                status=draft.status,
                error_code=draft.error_code,
                duration_ms=draft.duration_ms,
            )
            for index, draft in enumerate(drafts)
        ]


def _require(repository: Any, field: str, methods: Sequence[str]) -> Any:
    """装配期校验：依赖必须存在且提供全部**只读**方法（fail-fast）。"""
    for method in methods:
        if not callable(getattr(repository, method, None)):
            raise ValueError(
                f"{field} 必须提供可调用的 {method}()"
                f"（got {type(repository).__name__}）"
            )
    return repository


def _owns(
    run: AgentRunRecord | None,
    tool_results: Sequence[AgentToolResultRead],
    project_id: str,
) -> bool:
    """归属判定（fail-closed）：只信**带 project 的持久化行**。

    规则：

        ① ``agent_run.project_id`` 非空 ⇒ 必须等于请求 ``project_id``；
        ② 否则用 ``agent_tool_result.project_id``：
           必须**恰好**是请求的 project（多个不同 project ⇒ fail-closed）；
        ③ 两者都无法证明 ⇒ **不可见**（不泄露存在性）。
    """
    if run is not None and run.project_id is not None:
        return run.project_id == project_id
    projects = {
        item.project_id for item in tool_results if item.project_id is not None
    }
    if not projects:
        return False
    return projects == {project_id}
