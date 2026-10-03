"""Conversation HTTP API DTO（Phase 4.1 Step 8）。

本模块只描述 **HTTP 边界的数据形状**（Pydantic v2），不放业务逻辑、不依赖
Service / DB / LLM / ORM。对应 Step 7 冻结的 Contract：

    CreateConversationRequest     —— 创建请求（只有 project_id）
    ConversationResponse          —— 会话 metadata（不含 turns / messages）
    ConversationTurnResponse      —— 一条消息（含 assistant_request_id correlation）
    ConversationMessagesResponse  —— 消息列表（空 = []；无分页字段）
    ConversationMessageRequest    —— 发送消息请求（只有 content；Step 12）
    ConversationMessageResponse   —— 一次消息执行结果（route / content / data /
                                     metadata；与 /api/ai/chat 的 ChatResponse
                                     envelope 一致；Step 12）

字段边界（冻结，不得扩展）：

    * CreateConversationRequest 不得出现 conversation_id / status /
      created_at / updated_at / user_id / tenant_id / title / name /
      metadata / context / system_prompt；
    * ConversationMessageRequest（Step 12）不得出现 project_id /
      assistant_request_id / request_id / route / metadata / context /
      model / provider —— 项目来自 ``conversation.project_id``，
      assistant_request_id 来自
      ``AIOrchestrationResult.metadata["request_id"]``；
    * 响应不得出现 api_key / password / authorization / database_url / SQL /
      prompt / system_prompt / raw LLM response / RAG chunk / embedding /
      headers / traceback / SQLAlchemy Session / Engine / Provider request_id；
    * `content` 与 `assistant_request_id` 允许暴露（用户保存的会话正文与
      Turn → Trace → Timeline 的正式 correlation 键）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "CreateConversationRequest",
    "ConversationResponse",
    "ConversationTurnResponse",
    "ConversationMessagesResponse",
    "ConversationMessageRequest",
    "ConversationMessageResponse",
    "PROJECT_ID_MAX_LENGTH",
    "MESSAGE_CONTENT_MAX_LENGTH",
]

#: project_id 长度上限（与 ORM 列 VARCHAR(128) 一致）。
PROJECT_ID_MAX_LENGTH: int = 128

#: 消息正文长度上限（HTTP 边界；Step 12 冻结：1 ≤ len ≤ 10000）。
MESSAGE_CONTENT_MAX_LENGTH: int = 10000


class CreateConversationRequest(BaseModel):
    """创建会话请求（**客户端只能提供 project_id**）。

    conversation_id 由服务端生成（UUID4），客户端不得指定。
    """

    project_id: str = Field(
        ...,
        min_length=1,
        max_length=PROJECT_ID_MAX_LENGTH,
        description="项目 ID（必填 / 非空 / ≤128 字符）",
    )

    @field_validator("project_id")
    @classmethod
    def _reject_blank_project_id(cls, value: str) -> str:
        """纯空白在 API 层直接拒绝（422）。"""
        if not value.strip():
            raise ValueError("project_id 不能为空或纯空白")
        return value


class ConversationResponse(BaseModel):
    """会话 metadata 响应（**不含** turns / messages / 正文）。"""

    conversation_id: str = Field(
        ..., description="会话 ID（服务端生成；唯一 / 不可变）"
    )
    project_id: str = Field(..., description="项目 ID（创建时绑定；不可切换）")
    status: str = Field(..., description="会话状态：ACTIVE / ARCHIVED")
    created_at: datetime = Field(..., description="会话创建时间")
    updated_at: datetime = Field(..., description="最近一次有效变更时间")


class ConversationTurnResponse(BaseModel):
    """消息响应（一行 = 一条消息）。

    `assistant_request_id` 是 `Turn → Assistant Trace → Timeline` 的 correlation 键；
    USER 消息为 `null`。**不暴露** LLM Provider request_id。
    """

    turn_id: int = Field(..., description="消息主键（BIGINT 自增）")
    role: str = Field(..., description="消息角色：USER / ASSISTANT")
    content: str = Field(..., description="消息正文（用户可见文本）")
    assistant_request_id: str | None = Field(
        default=None,
        description="Assistant Trace ID（correlation；USER 为 null）",
    )
    created_at: datetime = Field(..., description="消息写入时间")


class ConversationMessagesResponse(BaseModel):
    """消息列表响应（顺序 = created_at ASC, turn_id ASC；**无分页**）。"""

    conversation_id: str = Field(..., description="会话 ID")
    messages: list[ConversationTurnResponse] = Field(
        default_factory=list,
        description="消息列表（无消息 → []）",
    )


class ConversationMessageRequest(BaseModel):
    """发送消息请求（**客户端只能提供 content**；Step 12）。

    * 项目来自 `conversation.project_id`（本请求**不得**携带 project_id）；
    * `assistant_request_id` 由 AI 执行生成并只出现在响应
      `metadata.request_id`（本请求不得指定）；
    * 格式校验在 HTTP DTO 层完成（1 ≤ len ≤ 10000；纯空白 → 422）；
      Step 11 的业务校验（ConversationService / Application Service）
      继续保留，不在本层复制。
    """

    content: str = Field(
        ...,
        min_length=1,
        max_length=MESSAGE_CONTENT_MAX_LENGTH,
        description="用户消息正文（1 ≤ len ≤ 10000；不得为纯空白）",
    )

    @field_validator("content")
    @classmethod
    def _reject_blank_content(cls, value: str) -> str:
        """纯空白在 API 层直接拒绝（422）。"""
        if not value.strip():
            raise ValueError("content 不能为空或纯空白")
        return value


class ConversationMessageResponse(BaseModel):
    """一次消息执行结果（envelope 与 `/api/ai/chat` 的 ChatResponse 一致）。

    * `route` / `content` / `data` / `metadata` 语义与 `/api/ai/chat` 相同；
    * `metadata.request_id` = Assistant Trace ID（AI 执行生成；API 层不生成）；
    * `data` 只透传"值均为 JSON 基本类型"的 Mapping（防泄露）；
      RagResponse / ToolResult / SQLExecutionResult 等富对象在当前阶段
      **不透传**（None）—— 深度映射统一属后续 Step；
    * Outcome（SUCCESS / EMPTY / REFUSED / FAILED）不是 HTTP 状态：
      AI 返回结果（含 EMPTY / Tool 业务失败）统一为 HTTP 200。
    """

    route: str = Field(..., description="实际执行的路由（rag / tool / text_to_sql）")
    content: str | None = Field(
        default=None, description="自然语言内容 / 摘要（RAG 空结果可为 null）"
    )
    data: dict[str, Any] | None = Field(
        default=None, description="结构化数据（保守透传；富对象不透传）"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="路由 + 执行统计（含 request_id；不含敏感信息）",
    )
