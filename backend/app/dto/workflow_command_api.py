"""Workflow **命令** API DTO（Phase 7 Step 1）。

纪律（沿 ``workflow_read_api`` / ``agent_read_api`` 先例）：
    * Pydantic v2 显式逐字段声明（**不**用 vars / asdict / model_dump）；
      ``response_model`` 同时充当字段过滤边界；
    * **不**含 Workflow 启动输入 ``input`` / ``request_id`` / claim_token /
      ORM / Session / Connection / 未筛选 metadata / 异常正文；
    * ``*_FIELDS`` 常量（顺序 ≡ 声明）供 API 测试做**精确字段集**断言；
    * 作用域（project_id）**不**出现在请求 DTO——客户端**不能**指定；
      响应中 run 视图的 ``project_id`` 来自服务端授权作用域下的真实行。
"""
from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, Field

from backend.app.dto.workflow_read_api import (
    WORKFLOW_RUN_DETAIL_RESPONSE_FIELDS,
    WorkflowRunDetailResponse,
)

__all__ = [
    "WORKFLOW_COMMAND_RESULT_RESPONSE_FIELDS",
    "WorkflowStartRequest",
    "ApprovalDecisionRequest",
    "WorkflowCommandResultResponse",
]

#: ``POST`` 命令响应的顶层字段（顺序 ≡ 声明）。
WORKFLOW_COMMAND_RESULT_RESPONSE_FIELDS: Final[tuple[str, ...]] = (
    "workflow_run_id",
    "outcome",
    "error_code",
    "approval_id",
    "approval_status",
    "run",
)

#: run 视图复用只读 DTO 的字段边界（**无** input / request_id）。
_RUN_VIEW_FIELDS: Final[tuple[str, ...]] = WORKFLOW_RUN_DETAIL_RESPONSE_FIELDS


class WorkflowStartRequest(BaseModel):
    """``POST /api/workflow-runs`` 请求体。

    注意：**没有** project_id 字段——作用域只能来自服务端认证配置。
    """

    workflow_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="已注册的 Workflow 定义 name（服务器端 Registry 解析）",
    )
    input: str | None = Field(
        default=None,
        max_length=8192,
        description="启动输入（JSON 文本；None = 无输入）",
    )
    workflow_run_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="可选的显式 Run 身份（None = 服务端生成）",
    )


class ApprovalDecisionRequest(BaseModel):
    """``POST /api/workflow-approvals/{approval_id}/decision`` 请求体。"""

    decision: Literal["APPROVED", "REJECTED"] = Field(
        ..., description="裁决：APPROVED / REJECTED"
    )


class WorkflowCommandResultResponse(BaseModel):
    """命令结果（runtime 事实 + 授权作用域内的 run 只读视图）。

    * ``outcome`` 为 Runtime 白名单结果（COMPLETED / FAILED / CANCELLED /
      WAITING_APPROVAL / RUNNING / CANCEL_REQUESTED / NOT_CANCELLABLE /
      UNKNOWN / CONFLICT / LIMIT_REACHED / NOT_FOUND / DEFINITION_MISMATCH）；
    * ``run`` 为**读回**的只读视图（命令后真实状态；不存在 ⇒ null，
      **不伪造**）。
    """

    workflow_run_id: str = Field(description="Workflow Run 身份")
    outcome: str = Field(description="Runtime 结果码（稳定白名单）")
    error_code: str | None = Field(
        default=None, description="WF_ERROR_* 稳定码（无 ⇒ null）"
    )
    approval_id: str | None = Field(default=None)
    approval_status: str | None = Field(default=None)
    run: WorkflowRunDetailResponse | None = Field(
        default=None, description="命令后的 Run 只读视图（无 input / request_id）"
    )
