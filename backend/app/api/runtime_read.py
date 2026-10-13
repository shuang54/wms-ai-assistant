"""Phase 7 Step 2 — 统一 Runtime 读取 API（Agent + Workflow；**只读**）。

```text
HTTP GET /api/runtime-runs/agent/{run_id}      → AgentReadService.get_run
HTTP GET /api/runtime-runs/workflow/{run_id}   → WorkflowReadService.get_run_detail
        ↓  X-API-Key（按域解析——复用 agent_read_auth / workflow_read_auth）
        ↓  授权 project_id（**仅**来自服务端配置）
        ↓  逐字段映射（复用域端点的 to_api_run / to_api_run_detail）
RuntimeAgentRunDetailResponse / RuntimeWorkflowRunDetailResponse
```

边界（**只读**；不新增任何执行能力）：

* 不调用 Agent Runtime 的 execute / resume / approve / cancel；
  不调用 Workflow Runtime 的 start / advance / request_cancel /
  submit_approval_decision；不调用 Tool / LLM / Orchestrator；
  无任何写入路径（OpenAPI 面形仅 GET——测试锁定）；
* **不重写**两域 ReadService / Repository / DTO——本模块只做
  "按域分发 + 公共字段包装"：详情数据 = 域端点同一映射函数产物
  （``to_api_run`` / ``to_api_run_detail``）**原样内嵌**；
* 资源不存在 **或** 不属于授权项目 ⇒ 404 固定文案（**沿用对应域
  文案**；不泄露存在性——与域端点同码同文案）；
* 不吞异常：持久化故障 ⇒ 502（不是 200 + 空数据）；未预期 ⇒ 500；
* 不暴露 ORM / Session / Connection / 凭据 / 连接串 / 异常正文；
* 响应只由 ``backend.app.dto.runtime_read_api`` 的 Pydantic DTO 组成
  （``response_model`` + discriminator 同时充当字段过滤与类型路由边界）；
* Workflow 详情经 ``asyncio.to_thread`` 调用同步 Read Service
  （S1 · D-F 冻结惯例——不在事件循环中直接阻塞）。

与两域端点的一致性（测试锁定）：

* 认证 / 授权 / 分页（详情端点均无分页参数）/ 错误码语义**逐项沿用**；
* 统一 DTO 只含两域**真实共有**字段（Agent 无 ``created_at`` /
  ``updated_at`` ⇒ 不引入、不伪造）；``status`` 原样透传（两域状态
  词汇不同——**不**强行映射）。

已知限制（如实记录；任务书 §三"不为统一而大规模扩展"）：

* **无统一列表端点**：两域 ReadService / Repository 当前均无"按项目
  范围列表"能力（Workflow Read Layer 在 S6 明确声明不实现全局 Run
  列表；Agent Run Repository 同样无列表方法）⇒ 本阶段只提供统一
  **详情**入口；列表需求属后续阶段（需先经治理评估扩仓储）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Security, status
from pydantic import Field

from backend.app.api.agent_read import get_agent_read_service, to_api_run
from backend.app.api.agent_read_auth import api_key_header
from backend.app.api.runtime_read_auth import (
    RUNTIME_TYPES,
    resolve_runtime_read_scope,
)
from backend.app.api.workflow_read import get_workflow_read_service, to_api_run_detail
from backend.app.db.agent_approval_repository import AgentApprovalRepositoryError
from backend.app.db.agent_checkpoint_repository import (
    AgentCheckpointRepositoryError,
)
from backend.app.db.agent_decision_repository import (
    AgentDecisionRepositoryError,
)
from backend.app.db.agent_run_repository import AgentRunRepositoryError
from backend.app.db.agent_step_repository import AgentStepRepositoryError
from backend.app.db.agent_tool_result_repository import (
    AgentToolResultRepositoryError,
)
from backend.app.dto.runtime_read_api import (
    RuntimeAgentRunDetailResponse,
    RuntimeWorkflowRunDetailResponse,
)
from backend.app.services.agent_read_service import AgentRunNotFoundError
from backend.app.workflow_runtime.read_service import (
    WorkflowReadError,
    WorkflowReadNotFoundError,
)

logger = logging.getLogger(__name__)
router = APIRouter()

#: 路径参数长度边界（与两域一致：agent 64 / workflow 64 ⇒ 统一 64）。
RUNTIME_RUN_ID_MAX_LENGTH: int = 64

#: 固定错误文案（**不**回传 DB 异常文本 / SQL / 路径 / 凭据）。
_AGENT_NOT_FOUND_DETAIL: str = "Agent Run 不存在"
_WORKFLOW_NOT_FOUND_DETAIL: str = "Workflow Run 不存在"
_AGENT_UNAVAILABLE_DETAIL: str = "Agent Run 读取数据源不可用"
_WORKFLOW_UNAVAILABLE_DETAIL: str = "Workflow 读取数据源不可用"
_BAD_RUNTIME_TYPE_DETAIL: str = "非法输入: runtime_type 必须为 agent 或 workflow"

#: 两个域共享的 responses 片段（认证 / 授权错误声明——文案与域端点一致）。
_AUTH_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {
        "description": (
            "缺少或无效的 X-API-Key（固定文案；不泄露目标资源是否存在）"
        )
    },
    503: {"description": "服务端认证 / 作用域未配置（fail closed）"},
}


@router.get(
    "/runtime-runs/{runtime_type}/{run_id}",
    response_model=Annotated[
        RuntimeAgentRunDetailResponse | RuntimeWorkflowRunDetailResponse,
        Field(discriminator="runtime_type"),
    ],
    responses={
        200: {
            "description": (
                "运行只读详情（公共字段 + 类型专属 detail；"
                "discriminator = runtime_type）"
            )
        },
        400: {"description": "runtime_type 非法（必须为 agent 或 workflow）"},
        404: {
            "description": (
                "运行不存在**或**不属于授权项目（沿用域文案；不泄露存在性）"
            )
        },
        422: {"description": "路径参数校验失败（长度越界）"},
        502: {"description": "持久化读边界不可用（DB 未配置 / 查询失败）"},
        **_AUTH_RESPONSES,
    },
)
async def get_runtime_run(
    runtime_type: Annotated[
        str,
        Path(
            min_length=1,
            max_length=16,
            description="运行时域：agent / workflow（小写枚举）",
        ),
    ],
    run_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=RUNTIME_RUN_ID_MAX_LENGTH,
            description="运行身份（域内 run_id；不得为纯空白）",
        ),
    ],
    api_key: Annotated[str | None, Security(api_key_header)] = None,
) -> RuntimeAgentRunDetailResponse | RuntimeWorkflowRunDetailResponse:
    """按运行时域读取一次运行的**只读**详情（Query only；不推进 / 不取消）。

    路由语义：``runtime_type`` 决定**认证域**与**数据域**——agent 请求
    只要求 Agent 读取认证配置、只读 ``ai_ops.agent_*`` 视图；workflow
    请求只要求 Workflow 读取认证配置、只读 ``ai_ops.workflow_*`` 视图。
    未涉及的域不要求配置（§五.4 按需 fail-closed）。

    授权范围仅来自服务端的已认证作用域（``scope.project_id``）——客户端
    Query / 请求头不能指定或扩宽项目。
    """
    if runtime_type not in RUNTIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_BAD_RUNTIME_TYPE_DETAIL,
        )
    scope = await resolve_runtime_read_scope(
        runtime_type=runtime_type, api_key=api_key
    )

    if scope.runtime_type == "agent":
        return _read_agent_run(run_id=run_id, project_id=scope.project_id)
    return await _read_workflow_run(run_id=run_id, project_id=scope.project_id)


def _read_agent_run(*, run_id: str, project_id: str) -> RuntimeAgentRunDetailResponse:
    """Agent 域详情（同步 Read Service——与域端点一致；复用同一映射）。"""
    service = get_agent_read_service()
    try:
        view = service.get_run(run_id, project_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"非法输入: {exc}",
        ) from None
    except AgentRunNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_AGENT_NOT_FOUND_DETAIL,
        ) from None
    except (
        AgentRunRepositoryError,
        AgentStepRepositoryError,
        AgentDecisionRepositoryError,
        AgentToolResultRepositoryError,
        AgentCheckpointRepositoryError,
        AgentApprovalRepositoryError,
    ) as exc:
        logger.error(
            "runtime read agent data source unavailable",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_AGENT_UNAVAILABLE_DETAIL,
        ) from None
    except Exception:  # noqa: BLE001 —— 不暴露 traceback / 内部模块路径
        logger.error("runtime read agent unavailable", extra={"error_type": "unknown"})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_AGENT_UNAVAILABLE_DETAIL,
        ) from None
    detail = to_api_run(view, run_id=run_id)
    return RuntimeAgentRunDetailResponse(
        runtime_type="agent",
        run_id=detail.run_id,
        project_id=project_id,
        status=detail.status,
        started_at=detail.started_at,
        detail=detail,
    )


async def _read_workflow_run(
    *, run_id: str, project_id: str
) -> RuntimeWorkflowRunDetailResponse:
    """Workflow 域详情（to_thread 调用同步 Read Service——与域端点一致）。"""
    service = get_workflow_read_service()
    try:
        detail_record = await asyncio.to_thread(
            service.get_run_detail,
            workflow_run_id=run_id,
            project_id=project_id,
        )
    except WorkflowReadNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_WORKFLOW_NOT_FOUND_DETAIL,
        ) from None
    except WorkflowReadError as exc:
        logger.error(
            "runtime read workflow data source unavailable",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_WORKFLOW_UNAVAILABLE_DETAIL,
        ) from None
    except Exception:  # noqa: BLE001 —— 不暴露 traceback / 内部路径
        logger.error(
            "runtime read workflow unavailable", extra={"error_type": "unknown"}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_WORKFLOW_UNAVAILABLE_DETAIL,
        ) from None
    detail = to_api_run_detail(detail_record)
    return RuntimeWorkflowRunDetailResponse(
        runtime_type="workflow",
        run_id=detail.workflow_run_id,
        project_id=project_id,
        status=detail.status,
        started_at=detail.started_at,
        detail=detail,
    )


__all__ = [
    "router",
    "get_agent_read_service",
    "get_workflow_read_service",
    "RUNTIME_RUN_ID_MAX_LENGTH",
]
