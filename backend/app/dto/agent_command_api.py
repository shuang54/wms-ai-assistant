"""Agent **命令** API DTO（Phase 7 Step 4 —— 与 ``workflow_command_api`` 对称）。

纪律（沿 ``workflow_command_api`` / ``agent_read_api`` 先例）：
    * Pydantic v2 显式逐字段声明（**不**用 vars / asdict / model_dump）；
      ``response_model`` 同时充当字段过滤边界；
    * **不**含 prompt / arguments / claim_token / ORM / Session /
      Connection / 未筛选 metadata / 异常正文；
    * ``*_FIELDS`` 常量（顺序 ≡ 声明）供 API 测试做**精确字段集**断言；
    * 作用域（project_id）**不**出现在请求 DTO——客户端**不能**指定；
      响应中 run 视图的 ``project_id`` 来自服务端授权作用域下的真实行。
"""
from __future__ import annotations

from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, Field

__all__ = [
    "AGENT_COMMAND_RESULT_RESPONSE_FIELDS",
    "AGENT_RUN_SUMMARY_VIEW_FIELDS",
    "AgentStartRequest",
    "AgentResumeRequest",
    "ApprovalDecisionRequest",
    "AgentRunSummaryView",
    "AgentCommandResultResponse",
]

#: ``POST`` 命令响应的顶层字段（顺序 ≡ 声明）。
AGENT_COMMAND_RESULT_RESPONSE_FIELDS: Final[tuple[str, ...]] = (
    "run_id",
    "outcome",
    "status",
    "error_code",
    "approval_id",
    "approval_status",
    "decision",
    "content",
    "run",
)

#: run 轻量视图字段（只读投影；**无** request_id / 明细）。
AGENT_RUN_SUMMARY_VIEW_FIELDS: Final[tuple[str, ...]] = (
    "run_id",
    "project_id",
    "status",
    "started_at",
    "finished_at",
)


class AgentStartRequest(BaseModel):
    """``POST /api/agent-runs`` 请求体。

    注意：**没有** project_id 字段——作用域只能来自服务端认证配置
    （经 per-project 装配的 AgentRuntime 兑现）。
    """

    question: str = Field(
        ...,
        min_length=1,
        max_length=8192,
        description="用户问题（非空；Agent 有界循环的输入）",
    )


class AgentResumeRequest(BaseModel):
    """``POST /api/agent-runs/{run_id}/resume`` 请求体。

    ``question`` 可空：TERMINAL / 纯 Replay 路径不需要新输入（§三十一：
    需要继续执行时必须提供——由 Runtime 契约判定，API 不预设）。
    """

    question: str | None = Field(
        default=None,
        max_length=8192,
        description="继续执行所需的新请求输入（可空；TERMINAL / Replay 不需要）",
    )


class ApprovalDecisionRequest(BaseModel):
    """``POST /api/agent-approvals/{approval_id}/decision`` 请求体。"""

    decision: Literal["APPROVED", "REJECTED"] = Field(
        ..., description="裁决：APPROVED / REJECTED（不接受其它值）"
    )


class AgentRunSummaryView(BaseModel):
    """命令后的 Run 轻量只读视图（授权作用域内的真实行投影）。

    **无** request_id / step / decision / tool result / approval 明细——
    详情走统一 Runtime Read API（``GET /api/runtime-runs/agent/{run_id}``）。
    """

    run_id: str = Field(description="Agent Run 身份")
    project_id: str | None = Field(
        default=None, description="项目作用域（真实行投影；历史行可空）"
    )
    status: str | None = Field(default=None, description="AgentRun.status（无记录 ⇒ null）")
    started_at: datetime | None = Field(default=None)
    finished_at: datetime | None = Field(default=None)


class AgentCommandResultResponse(BaseModel):
    """命令结果（runtime 事实 + 授权作用域内的 run 轻量视图）。

    * ``outcome`` 为命令服务稳定分类（COMPLETED / FAILED / LIMIT_REACHED /
      WAITING_APPROVAL / CANCELLED / CANCEL_REQUESTED / NOT_CANCELLABLE /
      CONFLICT / NOT_FOUND）；
    * ``status`` / ``error_code`` 为域词汇 / 稳定码**原值**（不映射）；
    * ``content`` 为 Agent 对授权项目的最终回答（失败 / 无 ⇒ null）；
    * ``run`` 为命令后轻量读回（无记录 ⇒ null，**不伪造**）。
    """

    run_id: str | None = Field(default=None, description="相关 Agent Run 身份")
    outcome: str = Field(description="命令结果分类（稳定白名单）")
    status: str | None = Field(default=None, description="Run / 审批当前状态（原值）")
    error_code: str | None = Field(
        default=None, description="AGENT_* 稳定码（无 ⇒ null）"
    )
    approval_id: str | None = Field(default=None)
    approval_status: str | None = Field(default=None)
    decision: str | None = Field(default=None, description="本次裁决（幂等回显）")
    content: str | None = Field(
        default=None, description="Agent 最终回答（失败 / 无 ⇒ null）"
    )
    run: AgentRunSummaryView | None = Field(
        default=None, description="命令后的 Run 轻量只读视图（无记录 ⇒ null）"
    )
