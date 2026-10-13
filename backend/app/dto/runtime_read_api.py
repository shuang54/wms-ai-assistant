"""Phase 7 Step 2 — 统一 Runtime 读取 DTO（Agent + Workflow 公共查询面）。

定位：让调用方经**一个**入口查询 Agent Run 与 Workflow Run，而不必分别
理解两套内部数据结构。本模块只定义**公共字段**与**类型专属详情包装**——
底层 Agent / Workflow 的完整响应 DTO **原样内嵌**（零重映射、零字段丢失）。

纪律（沿用 S6 / S7.2 / S8A / Phase 7 Step 1 冻结惯例）：

* Pydantic v2 **显式逐字段声明**；``response_model`` 同时充当字段过滤边界；
* **不伪造缺失字段**：公共字段只包含 Agent 与 Workflow **都真实存在**的
  字段（``run_id`` / ``project_id`` / ``status`` / ``started_at``）；
  Agent 无 ``created_at`` / ``updated_at`` ⇒ 统一 DTO **不引入**这两个键
  （需要时读域专属端点——不伪造、不猜）；
* **不强行映射状态**：``status`` 原样透传（两域状态词汇不同——Agent 域
  RUNNING/COMPLETED/FAILED/CANCELLED/LIMIT_REACHED/WAITING_APPROVAL，
  Workflow 域 PENDING/RUNNING/WAITING/…终态；同一字符串在两域含义可能
  不同，统一 DTO 拒绝任何语义归一化）；
* **不暴露** ORM / Session / Connection / 凭据 / 连接串 / 异常正文；
* ``*_FIELDS`` 常量（顺序 ≡ 声明）供 API 测试做**精确字段集**断言
  （沿 ``agent_read_api`` / ``workflow_read_api`` 先例）；
* ``runtime_type`` 作为 Pydantic **discriminator**（``Literal`` 保证
  类型路由在序列化边界确定——不依赖运行时猜判）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, Field

from backend.app.dto.agent_read_api import AgentRunResponse
from backend.app.dto.workflow_read_api import WorkflowRunDetailResponse

__all__ = [
    "RUNTIME_TYPES",
    "RUNTIME_RUN_SUMMARY_FIELDS",
    "RUNTIME_AGENT_RUN_DETAIL_FIELDS",
    "RUNTIME_WORKFLOW_RUN_DETAIL_FIELDS",
    "RuntimeRunSummaryResponse",
    "RuntimeAgentRunDetailResponse",
    "RuntimeWorkflowRunDetailResponse",
]

#: 受支持的运行时域（路径参数白名单；**小写**枚举——不引入别名）。
RUNTIME_TYPES: Final[tuple[str, ...]] = ("agent", "workflow")

#: 公共字段（两域都真实存在；顺序 ≡ 声明）。
RUNTIME_RUN_SUMMARY_FIELDS: Final[tuple[str, ...]] = (
    "runtime_type",
    "run_id",
    "project_id",
    "status",
    "started_at",
)

#: Agent 详情响应顶层字段（公共 + ``detail``；顺序 ≡ 声明）。
RUNTIME_AGENT_RUN_DETAIL_FIELDS: Final[tuple[str, ...]] = (
    *RUNTIME_RUN_SUMMARY_FIELDS,
    "detail",
)

#: Workflow 详情响应顶层字段（公共 + ``detail``；顺序 ≡ 声明）。
RUNTIME_WORKFLOW_RUN_DETAIL_FIELDS: Final[tuple[str, ...]] = (
    *RUNTIME_RUN_SUMMARY_FIELDS,
    "detail",
)


class RuntimeRunSummaryResponse(BaseModel):
    """Agent / Workflow 运行记录的**公共字段**（只含两域真实共有项）。

    Attributes:
        runtime_type: 运行时域标识（``agent`` / ``workflow``）——**不是**
            状态、不参与任何状态映射。
        run_id: 运行身份（域内 ``run_id``；由详情数据原样投影，不伪造）。
        project_id: 授权项目作用域（**服务端**认证配置解析结果——绝不
            来自客户端参数）。
        status: 域状态**原值**（可 ``null``：Agent 无终态记录时即无状态；
            两域词汇不混用、不归一化）。
        started_at: 开始时刻（两域均有该字段；无 ⇒ ``null``——不伪造）。
    """

    runtime_type: str = Field(description="运行时域：agent / workflow")
    run_id: str = Field(description="运行身份（域内 run_id）")
    project_id: str = Field(description="授权项目作用域（服务端解析结果）")
    status: str | None = Field(
        default=None, description="域状态原值（两域词汇不同；不强行映射）"
    )
    started_at: datetime | None = Field(
        default=None, description="开始时刻（两域共有字段；无 ⇒ null）"
    )


class RuntimeAgentRunDetailResponse(RuntimeRunSummaryResponse):
    """Agent 运行的统一详情（公共字段 + Agent 专属完整详情）。

    ``detail`` = :class:`backend.app.dto.agent_read_api.AgentRunResponse`
    **原样**（Run 摘要 + Checkpoint / Approval / Step / Decision /
    ToolResult 元数据——无 arguments / content / payload / claim_token）。
    """

    runtime_type: Literal["agent"] = "agent"
    detail: AgentRunResponse = Field(description="Agent Run 只读详情（原样内嵌）")


class RuntimeWorkflowRunDetailResponse(RuntimeRunSummaryResponse):
    """Workflow 运行的统一详情（公共字段 + Workflow 专属完整详情）。

    ``detail`` = :class:`backend.app.dto.workflow_read_api.WorkflowRunDetailResponse`
    **原样**（无 ``input`` / ``request_id``）。
    """

    runtime_type: Literal["workflow"] = "workflow"
    detail: WorkflowRunDetailResponse = Field(
        description="Workflow Run 只读详情（原样内嵌）"
    )
