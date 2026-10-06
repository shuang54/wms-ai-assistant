"""Conversation Management HTTP API（Phase 4.1 Step 8）。

边界（API 层只做 HTTP 适配）：

```text
HTTP Client
    ↓
Conversation API（校验 + 异常映射 + DTO 转换）
    ↓
ConversationService（业务生命周期）
    ↓
ConversationRepository（持久化 / 事务）
```

Step 7 冻结的 4 个端点 + Step 12 新增 1 个消息端点：

    POST /api/conversations                              创建会话
    GET  /api/conversations/{conversation_id}            会话 metadata
    GET  /api/conversations/{conversation_id}/messages   历史消息列表
    POST /api/conversations/{conversation_id}/archive    归档
    POST /api/conversations/{conversation_id}/messages   发送消息（Step 12）

消息端点链路（Step 12 唯一新增接线；API 只做适配）：

```text
HTTP
  ↓
Conversation API（校验 + 异常映射 + DTO 转换）
  ↓
ChatApplicationService.execute_message()（唯一 Application 入口）
  ↓
ConversationService（USER / ASSISTANT turn）
+ AIOrchestrator（AI execution；由 Application Service 调用）
  ↓
AIOrchestrationResult
  ↓
ConversationMessageResponse（envelope 与 /api/ai/chat 一致）
```

明确不做：

    * API **不**直接调用 AIOrchestrator / AIRouter / RAG / Tool / Text-to-SQL
      （AI 执行只能经 ChatApplicationService）
    * 不提供 DELETE / PATCH / PUT / switch-project / 物理删除
    * 不提供 Pagination
    * 不实现 Authentication / Authorization
    * API 不直接访问 SQLAlchemy / Repository / DB（只调用 ConversationService）

错误映射（Step 7 冻结）：

    ConversationNotFoundError    → 404
    ConversationArchivedError    → 409（当前 4 个端点不触发；契约保留）
    ValueError                   → 422（优先 Pydantic 校验拦截）
    ConversationRepositoryError  → 500（不降级为 404 / 空列表）
    未知异常                      → 500（不暴露 traceback / SQL / 凭据）

安全：响应只由 `backend.app.dto.conversation_api` 的 Pydantic DTO 组成
（`response_model` 同时充当字段过滤边界）；error detail 使用固定文案。
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Annotated, Any, Final

from fastapi import APIRouter, HTTPException, Header, Path, status

from backend.app.db.conversation_repository import ConversationRepositoryError
from backend.app.dto.conversation_api import (
    ConversationMessageRequest,
    ConversationMessageResponse,
    ConversationMessagesResponse,
    ConversationResponse,
    ConversationTurnResponse,
    CreateConversationRequest,
)
from backend.app.services.chat_application_service import (
    ChatApplicationService,
    MessageReplay,
)
from backend.app.services.conversation_service import (
    ConversationArchivedError,
    ConversationMessageIdempotencyConflictError,
    ConversationNotFoundError,
    ConversationService,
    ConversationTurnView,
    ConversationView,
)



logger = logging.getLogger(__name__)
router = APIRouter()

#: conversation_id 路径参数边界（与既有 Assistant Trace API 一致）。
MIN_CONVERSATION_ID_LENGTH: int = 1
MAX_CONVERSATION_ID_LENGTH: int = 128

#: ``Idempotency-Key`` Header 长度上限（Phase 4.2 Step 6；
#: 与 ``conversation_turn.idempotency_key`` 列宽一致；超长 → 422）。
IDEMPOTENCY_KEY_MAX_LENGTH: int = 128

#: 模块级 Service（Composition Root；测试可 monkeypatch 替换）。
#: 与既有 api 模块风格一致（``api/chat.py`` 的 ``_chat_service``、
#: ``api/orchestrator_chat.py`` 的 ``_default_orchestrator``）。
_conversation_service: ConversationService = ConversationService()


def get_conversation_service() -> ConversationService:
    """返回 Conversation Service（装配 accessor；便于测试注入 Fake）。"""
    return _conversation_service


#: 模块级 Chat Application Service（Step 12 Composition Root；测试可 monkeypatch）。
#: 只组合 ConversationService + AIOrchestrator（构造期不连接数据库）。
_chat_application_service: ChatApplicationService = ChatApplicationService()


def get_chat_application_service() -> ChatApplicationService:
    """返回 Chat Application Service（装配 accessor；便于测试注入 Fake）。"""
    return _chat_application_service


#: data 保守透传允许的 JSON 基本类型（防泄露：富对象不透传）。
_JSON_PRIMITIVES: Final[tuple[type, ...]] = (str, int, float, bool, type(None))


def _safe_message_data(data: object) -> dict[str, Any] | None:
    """data 安全投影：只透传"值均为 JSON 基本类型"的 Mapping。

    RagResponse / ToolResult / SQLExecutionResult 等富对象（可能含 chunk 正文 /
    Tool 内部 payload / SQL）在本阶段**不透传**（None）—— 深度映射统一属后续 Step。
    """
    if not isinstance(data, Mapping):
        return None
    if not all(isinstance(value, _JSON_PRIMITIVES) for value in data.values()):
        return None
    return {str(key): value for key, value in data.items()}


def _to_message_response(result: Any) -> ConversationMessageResponse:
    """执行结果 → HTTP DTO（**不修改** route / content / metadata）。

    ``result`` 使用 ``Any``：API 层**不 import** AI Core 模块（连类型标注也不），
    实际形状由 ChatApplicationService 契约保证：

        * ``AIOrchestrationResult`` → 本次真实执行了 AI；
        * ``MessageReplay``         → **duplicate 重放**（Phase 4.2 Step 6 /
          OD-34 Message Replay）：AI = 0 次，只重放已持久化 assistant message。
    """
    if isinstance(result, MessageReplay):
        # route / data **未持久化** ⇒ 一律 null（**禁止**伪造 route）。
        # metadata 只允许：request_id（来自 ASSISTANT Turn）+ idempotent_replay；
        # **不含** turn_id / conversation_id / idempotency_key / outcome。
        replay_metadata: dict[str, Any] = {"idempotent_replay": True}
        if result.assistant_request_id is not None:
            replay_metadata["request_id"] = result.assistant_request_id
        return ConversationMessageResponse(
            route=None,
            content=result.content,
            data=None,
            metadata=replay_metadata,
        )

    route = getattr(result.route, "value", result.route)
    return ConversationMessageResponse(
        route=str(route),
        content=result.content,
        data=_safe_message_data(result.data),
        metadata=dict(result.metadata),
    )


def _to_conversation_response(view: ConversationView) -> ConversationResponse:
    """Service View → API DTO（显式逐字段映射；不返回 ORM / Row）。"""
    return ConversationResponse(
        conversation_id=view.conversation_id,
        project_id=view.project_id,
        status=view.status,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


def _to_turn_response(view: ConversationTurnView) -> ConversationTurnResponse:
    """Service View → API DTO（顺序由 Service / Repository 保证，本层不重排）。"""
    return ConversationTurnResponse(
        turn_id=view.turn_id,
        role=view.role,
        content=view.content,
        assistant_request_id=view.assistant_request_id,
        created_at=view.created_at,
    )


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    responses={
        200: {"description": "会话已创建（conversation_id 由服务端生成）"},
        422: {"description": "请求体校验失败（project_id 非法）"},
        500: {"description": "会话服务不可用（内部错误）"},
    },
)
async def create_conversation(
    request: CreateConversationRequest,
) -> ConversationResponse:
    """创建会话（conversation_id 由服务端生成；status = ACTIVE）。

    客户端**只能**提供 project_id；不得提供 conversation_id / status /
    created_at / updated_at。
    """
    service = get_conversation_service()
    try:
        view = service.create_conversation(project_id=request.project_id)
    except ValueError as exc:
        # Pydantic 已拦截大部分；Service 层兜底（非空 / 长度）
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"非法输入: {exc}",
        )
    except ConversationRepositoryError as exc:
        logger.error(
            "conversation create failed (repository)",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="会话服务不可用",
        )
    except Exception as exc:  # noqa: BLE001 —— 不暴露 traceback / SQL / 凭据
        logger.exception(
            "conversation create failed",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="会话服务内部错误",
        )
    return _to_conversation_response(view)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    responses={
        200: {"description": "会话 metadata（不含消息）"},
        404: {"description": "会话不存在"},
        422: {"description": "路径参数校验失败（长度越界）"},
        500: {"description": "会话服务不可用（内部错误）"},
    },
)
async def get_conversation(
    conversation_id: Annotated[
        str,
        Path(
            min_length=MIN_CONVERSATION_ID_LENGTH,
            max_length=MAX_CONVERSATION_ID_LENGTH,
            description="会话 ID（服务端生成；不得为纯空白）",
        ),
    ],
) -> ConversationResponse:
    """读取会话 metadata（**不自动查询 turns**）。"""
    service = get_conversation_service()
    try:
        view = service.get_conversation(conversation_id)
    except ConversationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    except ConversationArchivedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"非法输入: {exc}",
        )
    except ConversationRepositoryError as exc:
        logger.error(
            "conversation get failed (repository)",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="会话服务不可用",
        )
    except Exception as exc:  # noqa: BLE001 —— 不暴露 traceback / SQL / 凭据
        logger.exception(
            "conversation get failed", extra={"error_type": type(exc).__name__}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="会话服务内部错误",
        )
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"会话不存在: {conversation_id}",
        )
    return _to_conversation_response(view)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=ConversationMessagesResponse,
    responses={
        200: {"description": "消息列表（无消息 → []）"},
        404: {"description": "会话不存在"},
        422: {"description": "路径参数校验失败（长度越界）"},
        500: {"description": "会话服务不可用（内部错误）"},
    },
)
async def list_conversation_messages(
    conversation_id: Annotated[
        str,
        Path(
            min_length=MIN_CONVERSATION_ID_LENGTH,
            max_length=MAX_CONVERSATION_ID_LENGTH,
            description="会话 ID（服务端生成；不得为纯空白）",
        ),
    ],
) -> ConversationMessagesResponse:
    """读取会话消息列表（顺序 = created_at ASC, turn_id ASC；API 层不重排）。

    * 会话不存在 → 404（**不**与"存在但无消息"混同）；
    * 存在但无消息 → 200 + `messages: []`。
    """
    service = get_conversation_service()
    try:
        turns = service.list_turns(conversation_id)
    except ConversationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    except ConversationArchivedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"非法输入: {exc}",
        )
    except ConversationRepositoryError as exc:
        logger.error(
            "conversation messages failed (repository)",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="会话服务不可用",
        )
    except Exception as exc:  # noqa: BLE001 —— 不暴露 traceback / SQL / 凭据
        logger.exception(
            "conversation messages failed",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="会话服务内部错误",
        )
    return ConversationMessagesResponse(
        conversation_id=conversation_id,
        messages=[_to_turn_response(turn) for turn in turns],
    )


@router.post(
    "/conversations/{conversation_id}/archive",
    response_model=ConversationResponse,
    responses={
        200: {"description": "会话已归档（幂等）"},
        404: {"description": "会话不存在"},
        409: {"description": "会话状态冲突（已归档）"},
        422: {"description": "路径参数校验失败（长度越界）"},
        500: {"description": "会话服务不可用（内部错误）"},
    },
)
async def archive_conversation(
    conversation_id: Annotated[
        str,
        Path(
            min_length=MIN_CONVERSATION_ID_LENGTH,
            max_length=MAX_CONVERSATION_ID_LENGTH,
            description="会话 ID（服务端生成；不得为纯空白）",
        ),
    ],
) -> ConversationResponse:
    """归档会话：ACTIVE → ARCHIVED；已 ARCHIVED → ARCHIVED（**幂等，无错误**）。"""
    service = get_conversation_service()
    try:
        view = service.archive_conversation(conversation_id)
    except ConversationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    except ConversationArchivedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"非法输入: {exc}",
        )
    except ConversationRepositoryError as exc:
        logger.error(
            "conversation archive failed (repository)",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="会话服务不可用",
        )
    except Exception as exc:  # noqa: BLE001 —— 不暴露 traceback / SQL / 凭据
        logger.exception(
            "conversation archive failed",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="会话服务内部错误",
        )
    return _to_conversation_response(view)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ConversationMessageResponse,
    responses={
        200: {
            "description": (
                "消息已执行（SUCCESS / EMPTY / REFUSED / FAILED 均为 200）"
            )
        },
        404: {"description": "会话不存在"},
        409: {
            "description": (
                "会话已归档（禁止追加 Turn）；或幂等冲突"
                "（IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD / "
                "IDEMPOTENCY_KEY_IN_FLIGHT）"
            )
        },
        422: {
            "description": (
                "请求体 / 路径参数 / Idempotency-Key 校验失败"
                "（content 非法 · key 超长）"
            )
        },
        500: {"description": "AI 执行失败或服务不可用（不暴露内部细节）"},
    },
)
async def execute_conversation_message(
    conversation_id: Annotated[
        str,
        Path(
            min_length=MIN_CONVERSATION_ID_LENGTH,
            max_length=MAX_CONVERSATION_ID_LENGTH,
            description="会话 ID（服务端生成；不得为纯空白）",
        ),
    ],
    request: ConversationMessageRequest,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            max_length=IDEMPOTENCY_KEY_MAX_LENGTH,
            description=(
                "客户端请求幂等键（**可选**；≤128 字符）。"
                "同一会话内同 key + 同 content 且已完成 → 200 duplicate replay"
                "（不重新执行 AI）；同 key 不同 content → 409；"
                "未提供 / 纯空白 → 不参与幂等"
            ),
        ),
    ] = None,
) -> ConversationMessageResponse:
    """执行一条会话消息（USER turn → AI → 条件式 ASSISTANT turn）。

    边界（Step 12）：

    * project 只能来自 ``conversation.project_id``（客户端**不可**覆盖）；
    * ``assistant_request_id`` 由 AI 执行生成（响应 ``metadata.request_id``），
      客户端**不可**指定；
    * 幂等键只经 **HTTP Header** 传递（**不在** JSON Body；
      ``ConversationMessageRequest`` 仍只有 ``content``）；
    * API 只做适配：校验 + 调用 ChatApplicationService + DTO 转换 + 异常映射，
      **不**访问 Repository / DB / AIOrchestrator；
    * Outcome（SUCCESS / EMPTY / REFUSED / FAILED）不是 HTTP 状态 ——
      AI 返回结果（含 EMPTY / REFUSED / Tool 业务失败）统一为 HTTP 200；
      仅当执行抛异常（AI 失败 / 服务不可用）才映射 500。

    Phase 4.2 Step 6（幂等）：

    * ``Idempotency-Key`` 缺失 / 纯空白 → 历史行为（每次独立，服务端**不生成** key）；
    * 已完成 + 同 key 同 content → **200 + duplicate replay**
      （``route=null · data=null · metadata.idempotent_replay=true``）；
    * 同 key 不同 content → **409** ``IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD``；
    * 并发同 key（唯一约束冲突）→ **409** ``IDEMPOTENCY_KEY_IN_FLIGHT``。
    """
    service = get_chat_application_service()
    try:
        result = await service.execute_message(
            conversation_id=conversation_id,
            content=request.content,
            idempotency_key=idempotency_key,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    except ConversationArchivedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )
    except ConversationMessageIdempotencyConflictError as exc:
        # 幂等冲突 = 409（**不是** 500）：请求未被静默执行，客户端需处理。
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"非法输入: {exc}",
        )
    except ConversationRepositoryError as exc:
        logger.error(
            "conversation message failed (repository)",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="会话服务不可用",
        )
    except Exception as exc:  # noqa: BLE001 —— 不暴露 traceback / SQL / prompt / 凭据
        logger.exception(
            "conversation message failed",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="消息执行失败",
        )
    return _to_message_response(result)


__all__ = [
    "router",
    "get_conversation_service",
    "get_chat_application_service",
    "MIN_CONVERSATION_ID_LENGTH",
    "MAX_CONVERSATION_ID_LENGTH",
]
