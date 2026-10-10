"""Agent Read API（Phase 5 P-8；Phase 6 Step 8A 加认证）—— **只读** HTTP 边界。

```text
HTTP GET /api/agent/runs/{run_id}
HTTP GET /api/agent/runs/{run_id}/timeline
        ↓  X-API-Key（服务端配置比对；恒定时间）
        ↓  授权 project_id（**仅**来自服务端配置——S8A）
AgentReadService.get_run / get_timeline（run_id, project_id, …）
        ↓
AgentRunView（内部 frozen 读模型）
        ↓ 显式逐字段映射（to_api_run / to_api_event）
AgentRunResponse / AgentTimelineEventResponse（Pydantic response_model）
        ↓
JSON
```

边界（**只读**；§五 / §六 / §二十七）：

* **Query only**：不调用 LLM / Tool / Resume / Approve / Cancel；
  不写任何表（不触碰 Checkpoint / Approval / Cancellation 状态机）；
* 不接触 ORM 会话对象：API 层经 ``AgentReadService`` 调用仓储
  （§二十四）——不向调用方暴露 Session / Connection；
* 不推断状态：``status`` 只来自 ``ai_ops.agent_run``；Checkpoint 的
  ``next_action`` **不**冒充 Run status（§八）；
* 不吞异常：下游持久化故障呈现为 502（不是 200 + 空视图）；
* 不缓存、不 streaming（§二十二）。

认证与授权（Phase 6 Step 8A —— 修复 Recovery Audit R1 缺口）：
* 两个端点**全部**挂载
  :func:`backend.app.api.agent_read_auth.require_agent_read_scope`
  （FastAPI ``Security``）——认证发生在任何数据读取**之前**；
* 授权 ``project_id`` **仅**来自服务端配置（``AGENT_READ_API_PROJECT_ID``）
  ——本 API **不再接受**客户端 ``project_id`` 查询参数（未知名被忽略）；
* fail closed：未配置 key / 未配置 project ⇒ 503（绝不匿名放行、绝不
  回退项目默认）；缺失 / 错误凭据 ⇒ 401（固定文案）；
* 本次为**安全例外解冻**：仅访问控制变化；Agent 其余契约（DTO 字段集、
  只读面形、无写路径、无缓存的语义）**零修改**（测试双证）。

错误契约（沿用既有 Trace / Timeline API 风格；§二十三）：

```text
400  run_id 非法（服务层校验）/ limit / offset 非法
401  缺少或无效的 X-API-Key（固定文案；不泄露目标资源是否存在）
404  Run 不存在 **或** 不属于授权项目（**故意同码**，不泄露存在性）
422  路径参数长度越界（FastAPI Path 校验）
502  持久化读边界不可用（DATABASE_URL 未配置 / 查询失败）
503  服务端认证 / 作用域未配置（fail closed）
500  其它未预期错误（不暴露 traceback / SQL / 凭据 / 内部模块路径）
```

分页（§二十）：``limit``（默认 50；**1..100**，超过上限 ⇒ **400**）+
``offset``（``>= 0``）。**server-side cap** 在服务层强制执行 ⇒ 绝不把
``limit=1000000`` 透传给数据库。

部署状态（如实记录）：应用层认证与项目作用域授权已实现（本模块 +
``agent_read_auth``）；HTTPS = NOT VERIFIED · 网络边界 = NOT VERIFIED ⇒
``PRODUCTION_DEPLOYMENT`` 仍为 **BLOCKED**（与 Workflow Read API 一致）。
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Security, status

from backend.app.api.agent_read_auth import (
    AgentReadScope,
    require_agent_read_scope,
)
from backend.app.db.agent_approval_repository import (
    AgentApprovalRepository,
    AgentApprovalRepositoryError,
)
from backend.app.db.agent_checkpoint_repository import (
    AgentCheckpointRepository,
    AgentCheckpointRepositoryError,
)
from backend.app.db.agent_decision_repository import (
    AgentDecisionRepository,
    AgentDecisionRepositoryError,
)
from backend.app.db.agent_run_repository import (
    AgentRunRepository,
    AgentRunRepositoryError,
)
from backend.app.db.agent_step_repository import (
    AgentStepRepository,
    AgentStepRepositoryError,
)
from backend.app.db.agent_tool_result_repository import (
    AgentToolResultRepository,
    AgentToolResultRepositoryError,
)
from backend.app.dto.agent_read import (
    AGENT_READ_RUN_ID_MAX_LENGTH,
    AgentApprovalView,
    AgentCheckpointView,
    AgentDecisionView,
    AgentRunView,
    AgentStepView,
    AgentTimelineEvent,
    AgentToolResultView,
)
from backend.app.dto.agent_read_api import (
    AgentApprovalViewResponse,
    AgentCheckpointViewResponse,
    AgentDecisionViewResponse,
    AgentRunResponse,
    AgentStepViewResponse,
    AgentTimelineEventResponse,
    AgentToolResultViewResponse,
)
from backend.app.services.agent_read_service import (
    AgentReadService,
    AgentRunNotFoundError,
)

logger = logging.getLogger(__name__)
router = APIRouter()

#: 固定错误文案（**不**回传 DB 异常文本 / SQL / 路径 / 凭据）。
_UNAVAILABLE_DETAIL: str = "Agent Run 读取数据源不可用"
_NOT_FOUND_DETAIL: str = "Agent Run 不存在"

#: 两个端点共享的 responses 片段（认证 / 授权错误声明）。
_AUTH_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {
        "description": (
            "缺少或无效的 X-API-Key（固定文案；不泄露目标资源是否存在）"
        )
    },
    503: {"description": "服务端认证 / 作用域未配置（fail closed）"},
}


def get_agent_read_service() -> AgentReadService:
    """Composition Root：装配只读服务（测试以 monkeypatch 替换本函数）。"""
    return AgentReadService(
        run_repository=AgentRunRepository(),
        step_repository=AgentStepRepository(),
        decision_repository=AgentDecisionRepository(),
        tool_result_repository=AgentToolResultRepository(),
        checkpoint_repository=AgentCheckpointRepository(),
        approval_repository=AgentApprovalRepository(),
    )


# ============================================================
# 显式逐字段映射（内部读模型 → API DTO）
# ============================================================


def to_api_checkpoint(view: AgentCheckpointView) -> AgentCheckpointViewResponse:
    """``AgentCheckpointView`` → API DTO（**无** claim_token）。"""
    return AgentCheckpointViewResponse(
        run_id=view.run_id,
        state_version=view.state_version,
        status=view.status,
        next_action=view.next_action,
        pending_step_index=view.pending_step_index,
        pending_tool_call_id=view.pending_tool_call_id,
        pending_decision_ref=view.pending_decision_ref,
        pending_tool_result_ref=view.pending_tool_result_ref,
        cancel_requested=view.cancel_requested,
        cancel_requested_at=view.cancel_requested_at,
        updated_at=view.updated_at,
    )


def to_api_approval(view: AgentApprovalView) -> AgentApprovalViewResponse:
    """``AgentApprovalView`` → API DTO（**无** arguments）。"""
    return AgentApprovalViewResponse(
        approval_id=view.approval_id,
        run_id=view.run_id,
        step_index=view.step_index,
        tool_call_id=view.tool_call_id,
        tool_name=view.tool_name,
        status=view.status,
        decision=view.decision,
        reason_code=view.reason_code,
        created_at=view.created_at,
        resolved_at=view.resolved_at,
    )


def to_api_step(view: AgentStepView) -> AgentStepViewResponse:
    """``AgentStepView`` → API DTO（**无** arguments / output）。"""
    return AgentStepViewResponse(
        step_index=view.step_index,
        step_type=view.step_type,
        status=view.status,
        tool_name=view.tool_name,
        error_code=view.error_code,
        duration_ms=view.duration_ms,
        created_at=view.created_at,
    )


def to_api_decision(view: AgentDecisionView) -> AgentDecisionViewResponse:
    """``AgentDecisionView`` → API DTO（**无** arguments / content）。"""
    return AgentDecisionViewResponse(
        step_index=view.step_index,
        decision_type=view.decision_type,
        tool_call_id=view.tool_call_id,
        tool_name=view.tool_name,
        finish_reason=view.finish_reason,
        decision_unavailable=view.decision_unavailable,
        created_at=view.created_at,
    )


def to_api_tool_result(view: AgentToolResultView) -> AgentToolResultViewResponse:
    """``AgentToolResultView`` → API DTO（**无** payload / ref）。"""
    return AgentToolResultViewResponse(
        step_index=view.step_index,
        tool_name=view.tool_name,
        success=view.success,
        error_code=view.error_code,
        result_hash=view.result_hash,
        result_size_bytes=view.result_size_bytes,
        result_truncated=view.result_truncated,
        result_unavailable=view.result_unavailable,
        tool_call_id=view.tool_call_id,
        created_at=view.created_at,
    )


def to_api_run(view: AgentRunView, *, run_id: str) -> AgentRunResponse:
    """``AgentRunView`` → API DTO（Run 摘要 + 组合视图；显式映射）。"""
    summary = view.run
    return AgentRunResponse(
        run_id=summary.run_id if summary is not None else run_id,
        request_id=summary.request_id if summary is not None else None,
        project_id=summary.project_id if summary is not None else None,
        status=summary.status if summary is not None else None,
        error_code=summary.error_code if summary is not None else None,
        limit_reason=summary.limit_reason if summary is not None else None,
        step_count=summary.step_count if summary is not None else None,
        tool_call_count=summary.tool_call_count if summary is not None else None,
        llm_call_count=summary.llm_call_count if summary is not None else None,
        token_usage_unverifiable=(
            summary.token_usage_unverifiable if summary is not None else None
        ),
        started_at=summary.started_at if summary is not None else None,
        finished_at=summary.finished_at if summary is not None else None,
        duration_ms=summary.duration_ms if summary is not None else None,
        checkpoint=(
            to_api_checkpoint(view.checkpoint)
            if view.checkpoint is not None
            else None
        ),
        approval=(
            to_api_approval(view.approval) if view.approval is not None else None
        ),
        steps=[to_api_step(step) for step in view.steps],
        decisions=[to_api_decision(decision) for decision in view.decisions],
        tool_results=[
            to_api_tool_result(item) for item in view.tool_results
        ],
    )


def to_api_event(event: AgentTimelineEvent) -> AgentTimelineEventResponse:
    """``AgentTimelineEvent`` → API DTO（顺序 / sequence 原样，不重排）。"""
    return AgentTimelineEventResponse(
        sequence=event.sequence,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        run_id=event.run_id,
        step_index=event.step_index,
        tool_name=event.tool_name,
        tool_call_id=event.tool_call_id,
        status=event.status,
        error_code=event.error_code,
        duration_ms=event.duration_ms,
    )


# ============================================================
# 端点（**只读 + 认证**）
# ============================================================


@router.get(
    "/agent/runs/{run_id}",
    response_model=AgentRunResponse,
    responses={
        200: {"description": "Agent Run 只读视图（Run 摘要 + Checkpoint / Approval / Step / Decision / ToolResult）"},
        400: {"description": "run_id 非法"},
        404: {"description": "Run 不存在**或**不属于授权项目（不泄露存在性）"},
        422: {"description": "路径参数校验失败（长度越界）"},
        502: {"description": "持久化读边界不可用（DB 未配置 / 查询失败）"},
        **_AUTH_RESPONSES,
    },
)
async def get_agent_run(
    run_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=AGENT_READ_RUN_ID_MAX_LENGTH,
            description="Agent Run 身份（ai_ops.agent_run.run_id；不得为纯空白）",
        ),
    ],
    scope: Annotated[AgentReadScope, Security(require_agent_read_scope)],
) -> AgentRunResponse:
    """读取一次 Agent Run 的**只读**视图（Query only；认证作用域内）。

    语义：``status`` 只来自 ``ai_ops.agent_run``（尚无终态记录 ⇒ ``null``）；
    ``checkpoint`` 表达"从哪恢复"（**不含** claim_token）；``approval`` 是该 Run
    的最后一条审批；``decisions`` / ``tool_results`` 只含**元数据**
    （**无** arguments / content / payload）。**不**触发 Resume / Approve / Cancel。
    授权范围仅来自服务端的已认证作用域（``scope.project_id``）。
    """
    service = get_agent_read_service()
    try:
        view = service.get_run(run_id, scope.project_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"非法输入: {exc}",
        )
    except AgentRunNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_NOT_FOUND_DETAIL,
        )
    except (
        AgentRunRepositoryError,
        AgentStepRepositoryError,
        AgentDecisionRepositoryError,
        AgentToolResultRepositoryError,
        AgentCheckpointRepositoryError,
        AgentApprovalRepositoryError,
    ) as exc:
        logger.error(
            "agent run read data source unavailable",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_UNAVAILABLE_DETAIL,
        )
    except Exception:  # noqa: BLE001 —— 不暴露 traceback / 内部模块路径
        logger.error("agent run read unavailable", extra={"error_type": "unknown"})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_UNAVAILABLE_DETAIL,
        )
    return to_api_run(view, run_id=run_id)


@router.get(
    "/agent/runs/{run_id}/timeline",
    response_model=list[AgentTimelineEventResponse],
    responses={
        200: {"description": "该 Run 的 Timeline（deterministic 排序；可分页）"},
        400: {"description": "run_id / limit / offset 非法"},
        404: {"description": "Run 不存在**或**不属于授权项目（不泄露存在性）"},
        422: {"description": "路径参数校验失败（长度越界）"},
        502: {"description": "持久化读边界不可用（DB 未配置 / 查询失败）"},
        **_AUTH_RESPONSES,
    },
)
async def get_agent_run_timeline(
    run_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=AGENT_READ_RUN_ID_MAX_LENGTH,
            description="Agent Run 身份（ai_ops.agent_run.run_id；不得为纯空白）",
        ),
    ],
    scope: Annotated[AgentReadScope, Security(require_agent_read_scope)],
    limit: Annotated[
        int | None,
        Query(description="每页条数（默认 50；**1..100**；超过 100 ⇒ 400）"),
    ] = None,
    offset: Annotated[
        int,
        Query(description="跳过条数（>= 0；负数 ⇒ 400）"),
    ] = 0,
) -> list[AgentTimelineEventResponse]:
    """读取该 Run 的 Timeline（**只读**展示模型；不是新的事件系统）。

    事件只表达当前持久化数据能**证明**的事实（RUN_STARTED · DECISION_RECORDED ·
    APPROVAL_REQUIRED / APPROVAL_RESOLVED · TOOL_EXECUTED · STEP_COMPLETED ·
    RUN_COMPLETED / RUN_FAILED / RUN_CANCELLED / RUN_LIMIT_REACHED）；
    排序 = ``occurred_at`` + 稳定 tie-breaker（同一 Run 顺序 deterministic）；
    ``sequence`` 是**投影下标**（分页不重新编号）。
    """
    service = get_agent_read_service()
    try:
        events = service.get_timeline(
            run_id,
            scope.project_id,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"非法输入: {exc}",
        )
    except AgentRunNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_NOT_FOUND_DETAIL,
        )
    except (
        AgentRunRepositoryError,
        AgentStepRepositoryError,
        AgentDecisionRepositoryError,
        AgentToolResultRepositoryError,
        AgentCheckpointRepositoryError,
        AgentApprovalRepositoryError,
    ) as exc:
        logger.error(
            "agent timeline read data source unavailable",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_UNAVAILABLE_DETAIL,
        )
    except Exception:  # noqa: BLE001 —— 不暴露 traceback / 内部模块路径
        logger.error(
            "agent timeline read unavailable", extra={"error_type": "unknown"}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_UNAVAILABLE_DETAIL,
        )
    return [to_api_event(event) for event in events]


__all__ = [
    "router",
    "get_agent_read_service",
    "to_api_approval",
    "to_api_checkpoint",
    "to_api_decision",
    "to_api_event",
    "to_api_run",
    "to_api_step",
    "to_api_tool_result",
    "AgentRunResponse",
    "AgentTimelineEventResponse",
    "AGENT_READ_RUN_ID_MAX_LENGTH",
]
