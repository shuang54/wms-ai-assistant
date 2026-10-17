"""FastAPI 应用入口（Phase 1）。

启动方式（项目根目录）：

    python -m uvicorn backend.app.main:app --reload

或在 backend/app 目录下：

    uvicorn main:app --reload
"""
from __future__ import annotations

from fastapi import FastAPI

from backend.app.api import (
    agent_command,
    agent_read,
    assistant_timeline,
    assistant_trace,
    auth,
    chat,
    conversations,
    health,
    orchestrator_chat,
    rag,
    runtime_read,
    tool_chat,
    tool_observability,
    usage,
    workflow_command,
    workflow_definitions,
    workflow_read,
    workflow_ui_read,
)
from backend.app.config import settings


def create_app() -> FastAPI:
    """应用工厂，便于测试与未来多实例启动。"""
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "WMS AI Assistant — Phase 1 Project Foundation. "
            "当前为 MVP 项目骨架，未接入真实 LLM / RAG / Tool。"
        ),
    )
    app.include_router(health.router, prefix="/api", tags=["health"])
    # Phase 7 Recovery M1：用户认证（登录 / 登出 / 当前用户）。
    # 与 5 个域 X-API-Key 服务认证**并存不替代**（人类用户会话 vs 服务凭据）。
    app.include_router(auth.router, prefix="/api", tags=["auth"])
    app.include_router(chat.router, prefix="/api", tags=["chat"])
    app.include_router(rag.router, prefix="/api", tags=["rag"])
    app.include_router(tool_chat.router, prefix="/api", tags=["chat"])
    app.include_router(
        orchestrator_chat.router, prefix="/api", tags=["ai-chat"]
    )
    app.include_router(usage.router, prefix="/api", tags=["usage"])
    app.include_router(
        tool_observability.router, prefix="/api", tags=["observability"]
    )
    app.include_router(
        assistant_trace.router, prefix="/api", tags=["observability"]
    )
    # Phase 3.12 Step 68：Assistant Timeline 只读 HTTP 边界
    # （Grouped Timeline Projection；与 Trace 端点并列，互不影响）。
    app.include_router(
        assistant_timeline.router, prefix="/api", tags=["observability"]
    )
    # Phase 4.1 Step 8：Conversation Management HTTP 边界
    # （4 个端点；不含 message POST —— Chat/Application Layer 属后续阶段）。
    app.include_router(
        conversations.router, prefix="/api", tags=["conversations"]
    )
    # Phase 5 P-8：Agent Read API（**只读** Query 边界：
    # GET /api/agent/runs/{run_id} + /timeline；不触发 Resume / Approve / Cancel）。
    app.include_router(agent_read.router, prefix="/api", tags=["agent"])
    # Phase 6 Step 7：Workflow Read API（**只读** Query 边界：
    # GET /api/workflow-runs/{run_id}(+/nodes | /timeline) +
    # GET /api/workflow-approvals/{approval_id}；
    # 不触发 start / advance / request_cancel / 审批裁决）。
    app.include_router(
        workflow_read.router, prefix="/api", tags=["workflow"]
    )
    # Phase 7 Step 1：Workflow Command API（**认证 + 项目作用域** 写边界：
    # POST /api/workflow-runs（start）+ /{run_id}/advance|cancel +
    # POST /api/workflow-approvals/{approval_id}/decision；
    # 经既有 WorkflowRuntime 命令面（start/advance/request_cancel/
    # submit_approval_decision）；不新增认证机制——与 read 面同形同语义，
    # 凭据独立（WORKFLOW_COMMAND_API_KEY）可分别轮换）。
    app.include_router(
        workflow_command.router, prefix="/api", tags=["workflow"]
    )
    # Phase 7 Step 2：统一 Runtime 读取 API（**只读** Query 边界：
    # GET /api/runtime-runs/{runtime_type}/{run_id}——runtime_type ∈
    # {agent, workflow}；按域复用两套只读面（认证 / 项目作用域 / 详情
    # 语义逐项沿用）；不新增执行能力、不重写 ReadService / Repository）。
    app.include_router(runtime_read.router, prefix="/api", tags=["runtime"])
    # Phase 7 Step 4：Agent 命令 API（**认证 + 项目作用域** 写边界：
    # POST /api/agent-runs（start）+ /{run_id}/resume|cancel +
    # POST /api/agent-approvals/{approval_id}/decision；经既有
    # AgentRuntime（run/resume）+ AgentCancellationCoordinator +
    # AgentApprovalStore——不修改 Runtime 状态机；凭据独立
    # （AGENT_COMMAND_API_KEY）可分别轮换）。
    app.include_router(agent_command.router, prefix="/api", tags=["agent"])
    # Phase 7 Step 7：Workflow 定义目录 API（**只读** Query 边界：
    # GET /api/workflow-definitions——已注册定义（name / version /
    # start_node_id / input_fields / 节点类型概要）；与命令 API 使用**同一**
    # 注册表实例（生产目录 workflow.catalog 经 Composition Root 惰性注册）；
    # 复用 Workflow 读取域认证；不暴露模板 / 参数映射 / 审批文案。
    app.include_router(
        workflow_definitions.router, prefix="/api", tags=["workflow"]
    )
    # Phase 7 Workflow UI Step 2：**浏览器**只读访问面
    # （GET /api/ui/projects/{project_id}/workflow-runs/... 等 5 个端点）——
    # 认证 = M1 会话 Cookie（require_session），授权 = M2.4 项目成员
    # （enforce_project_access）；与会话/授权机制**复用不新增**，
    # 且与既有服务间 X-API-Key 读面（workflow_read）并存不替代。
    # 纯只读：无写方法、不触 Runtime 执行入口。
    app.include_router(
        workflow_ui_read.router, prefix="/api", tags=["workflow-ui"]
    )
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
    )


__all__ = ["app", "create_app"]
