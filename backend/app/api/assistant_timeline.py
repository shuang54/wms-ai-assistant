"""Assistant Timeline Read API（Phase 3.12 Step 68）——只读 HTTP 边界。

    HTTP GET /api/observability/assistant-timeline/{assistant_request_id}
        ↓
    get_assistant_timeline_query_service()（Composition Root 装配）
        ↓
    AssistantTimelineQueryService.get_timeline()   （Step 66 分组投影）
        ↓
    AssistantTimeline（内部 frozen 读模型）
        ↓ 显式逐字段映射（to_api_event / to_api_timeline）
    AssistantTimelineResponse（API DTO；9 字段事件）
        ↓
    JSON

边界（本模块**只**做 HTTP 适配）：

* **只读**：不写库 / 不触发 Tool / 不调用 LLM / 不重新路由 / 不做投影计算；
* **不重新排序**：四段直接使用 Service 已确定的组内顺序
  （LLM ``created_at, id``；Tool / RAG ``id``；Outcome 至多 1 条）；
  **绝不**合并四段、**绝不**生成 sequence / event_id（Step 68 §十六）；
* **不推断**：无 Outcome 记录 → ``outcome_event = null``（不猜 SUCCESS）；
  某段无记录 → 该段 ``[]``（不补造）；
* **不接触 DB**：不 import Repository / SQLAlchemy / Engine / Session；
  只调用 Query Service（§十一）；
* **不吞异常**：下游失败必须呈现为 5xx（DB 故障 ≠ 空 Timeline）；
* **不放缓存**；**无分页**（Step 54/67：Pagination = DEFER）；
* 显式映射：不使用 vars / asdict / model_dump；``response_model`` 同时
  充当字段过滤边界。

这是 **Grouped Timeline Projection**（不是 Unified Timeline）：
响应里没有全局事件流、没有 event_id、没有 sequence、没有跨组排序。

授权现状（Phase 7 Recovery M2.3 A2 —— 如实记录，不隐藏）：

    本端点已从"匿名可读"收口为 **M1 会话 + 运维白名单**（装饰器级依赖
    ``require_observability_ops``，先于任何数据读取）：

        匿名 / 无效 / 过期 / 已撤销会话        ⇒ 401
        运维白名单未配置 / 无法解析            ⇒ 503（fail closed）
        已认证但 user_id 不在白名单            ⇒ 403

    **仍然没有 request-level 项目授权**：无法从持久化数据可靠判定一条
    Timeline 的项目归属（``llm_usage_record`` / ``assistant_outcome_record``
    无 project_id 列，``resource_owner`` 也不登记 ``assistant_request_id``），
    因此只能整端点限定为运维专用——与 Assistant Trace API（A2-06）**同一
    访问策略**。该残留风险记录于 ``docs/security.md`` §A2。

    不引入 JWT / OAuth / API Key / tenant isolation；GET 不适用 CSRF。
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from backend.app.api.observability_read_auth import require_observability_ops

from backend.app.api.assistant_trace import (
    MAX_ASSISTANT_REQUEST_ID_LENGTH,
    MIN_ASSISTANT_REQUEST_ID_LENGTH,
)
from backend.app.api.orchestrator_chat import (
    get_assistant_timeline_query_service,
)
from backend.app.db.assistant_outcome_repository import (
    AssistantOutcomeRepositoryError,
)
from backend.app.db.llm_usage_repository import LLMUsageRepositoryError
from backend.app.db.rag_execution_repository import (
    RagExecutionRepositoryError,
)
from backend.app.db.tool_execution_repository import (
    ToolExecutionRepositoryError,
)
from backend.app.dto.assistant_timeline import AssistantTimelineEvent
from backend.app.dto.assistant_timeline_api import (
    AssistantTimelineEventResponse,
    AssistantTimelineResponse,
)
from backend.app.services.assistant_timeline_query_service import (
    AssistantTimelineQueryService,
)

logger = logging.getLogger(__name__)
router = APIRouter()

#: 固定错误文案（**不**回传 DB 异常文本 / SQL / 路径 / 凭据）。
_UNAVAILABLE_DETAIL: str = "助手链路观测数据不可用"


def to_api_event(event: AssistantTimelineEvent) -> AssistantTimelineEventResponse:
    """``AssistantTimelineEvent`` → API DTO（**显式逐字段映射**）。

    * 顺序 / 取值完全来自内部事件；本函数不排序、不推断、不补全；
    * 时间字段**不改名**（``created_at`` 仍叫 ``created_at``，
      绝不冒充 ``finished_at``）；
    * 枚举 → 其字符串值（``source`` / ``event_type`` / ``status``）。
    """
    return AssistantTimelineEventResponse(
        assistant_request_id=event.assistant_request_id,
        source=event.source.value,
        event_type=event.event_type.value,
        source_id=event.source_id,
        started_at=event.started_at,
        finished_at=event.finished_at,
        duration_ms=event.duration_ms,
        created_at=event.created_at,
        status=event.status.value if event.status is not None else None,
    )


def to_api_timeline(timeline: object) -> AssistantTimelineResponse:
    """``AssistantTimeline`` → API DTO（顺序保持，只做 tuple → list）。

    * 四段**独立**映射（不合并为 events）；
    * ``outcome_event`` 为 ``None`` → 响应 ``null``（历史请求 / 无终态记录）。
    """
    return AssistantTimelineResponse(
        assistant_request_id=timeline.assistant_request_id,  # type: ignore[attr-defined]
        llm_events=[to_api_event(event) for event in timeline.llm_events],  # type: ignore[attr-defined]
        tool_events=[to_api_event(event) for event in timeline.tool_events],  # type: ignore[attr-defined]
        rag_events=[to_api_event(event) for event in timeline.rag_events],  # type: ignore[attr-defined]
        outcome_event=(
            None
            if timeline.outcome_event is None  # type: ignore[attr-defined]
            else to_api_event(timeline.outcome_event)  # type: ignore[attr-defined]
        ),
    )


@router.get(
    "/observability/assistant-timeline/{assistant_request_id}",
    response_model=AssistantTimelineResponse,
    dependencies=[Depends(require_observability_ops)],
    responses={
        200: {
            "description": (
                "该 Assistant Request 的 **Grouped Timeline Projection**"
                "（四段独立；可为空）"
            )
        },
        400: {"description": "assistant_request_id 非法（如纯空白）"},
        401: {"description": "未认证 / 会话无效（固定文案；零业务执行）"},
        403: {"description": "已认证但非运维用户（固定文案；零业务执行）"},
        503: {"description": "运维白名单未配置（fail closed；零业务执行）"},
        422: {"description": "路径参数校验失败（长度越界）"},
        502: {
            "description": (
                "持久化读边界不可用（LLM Usage / Tool / RAG / Outcome "
                "数据库未配置或查询失败）"
            )
        },
        500: {"description": "Timeline 观测数据不可用"},
    },
)
async def get_assistant_timeline(
    assistant_request_id: Annotated[
        str,
        Path(
            min_length=MIN_ASSISTANT_REQUEST_ID_LENGTH,
            max_length=MAX_ASSISTANT_REQUEST_ID_LENGTH,
            description=(
                "Assistant Trace ID（即 POST /api/ai/chat 成功响应 "
                "metadata.request_id；不得为纯空白）"
            ),
        ),
    ],
) -> AssistantTimelineResponse:
    """按 Assistant request_id 读取 **Grouped Timeline Projection**（Step 68）。

    Pipeline::

        HTTP Path → AssistantTimelineQueryService.get_timeline()
            → AssistantTimeline → AssistantTimelineResponse → JSON

    语义：

        * **只读**：不写库 / 不触发 Tool / 不调用 LLM / 不重新路由；
        * **分组投影**：``llm_events`` / ``tool_events`` / ``rag_events`` /
          ``outcome_event`` 四段**独立**返回（**无** merged ``events``）；
        * **组内顺序**由 Service 决定（LLM ``created_at, id``；
          Tool / RAG ``id``；Outcome 至多 1 条），HTTP **不重排**、
          **不**跨组排序、**不**生成 sequence / event_id；
        * **空 Timeline 合法**：未知 ``assistant_request_id`` → ``200`` +
          四段空（**不是 404**）；
        * 下游失败**原样呈现**为 5xx（绝不降级成 ``200`` + 空 Timeline）。

    错误映射（沿用既有 Trace API 约定）：

        400  assistant_request_id 非法（服务层校验：非 str / 纯空白 / 超长）
        422  路径参数长度越界（FastAPI Path 校验）
        502  持久化读边界不可用（LLMUsageRepositoryError /
             ToolExecutionRepositoryError / RagExecutionRepositoryError /
             AssistantOutcomeRepositoryError）
        500  其它未预期错误（不暴露 traceback / SQL / 凭据 / 内部模块路径）

    安全：响应只含 ``assistant_request_id`` / ``source`` / ``event_type`` /
        ``source_id`` / 时间戳 / ``duration_ms`` / ``status``；
        不含 prompt / messages / SQL / chunk 正文 / embedding / similarity /
        tool arguments / tool result / 凭据 / 异常文本。
        ``source_id`` 是**内部事件标识**（来源表主键），仅用于同一次请求内
        区分事件 —— **不是** global event id、**不**表示顺序。

    授权（Phase 7 Recovery M2.3 A2）：M1 会话 + 运维白名单（装饰器级依赖，
        先于任何数据读取）——匿名 ⇒ 401；非运维用户 ⇒ 403；白名单未配置
        ⇒ 503。**"知道 ID 即可读"不再成立**。
        **仍然没有** request-level 项目授权（无法可靠判定 Timeline 的
        项目归属，见模块 docstring）⇒ 与 Assistant Trace API（A2-06）
        同一访问策略；该残留风险记录于 ``docs/security.md``。
    """
    try:
        service: AssistantTimelineQueryService = (
            get_assistant_timeline_query_service()
        )
        timeline = service.get_timeline(assistant_request_id)
    except ValueError as exc:
        # 服务层输入校验（纯空白等）—— 与项目既有 400「非法输入」语义一致
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"非法输入: {exc}",
        )
    except (
        LLMUsageRepositoryError,
        ToolExecutionRepositoryError,
        RagExecutionRepositoryError,
        AssistantOutcomeRepositoryError,
    ) as exc:
        logger.error(
            "assistant timeline data source unavailable",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_UNAVAILABLE_DETAIL,
        )
    except Exception:  # noqa: BLE001 —— 不暴露 traceback / 内部模块路径
        logger.error(
            "assistant timeline unavailable",
            extra={"error_type": "unknown"},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_UNAVAILABLE_DETAIL,
        )
    return to_api_timeline(timeline)


__all__ = [
    "router",
    "AssistantTimelineResponse",
    "AssistantTimelineEventResponse",
    "to_api_event",
    "to_api_timeline",
    "MIN_ASSISTANT_REQUEST_ID_LENGTH",
    "MAX_ASSISTANT_REQUEST_ID_LENGTH",
]
