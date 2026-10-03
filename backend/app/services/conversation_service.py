"""Conversation Service（Phase 4.1 Step 5 —— 最小生命周期实现）。

职责（Step 3 冻结；**只有** Conversation 生命周期）：

    create_conversation(*, project_id)                    创建会话（服务端生成 ID）
    get_conversation(conversation_id)                     读取会话（无记录 → None）
    archive_conversation(conversation_id)                 归档（幂等；ACTIVE → ARCHIVED）
    append_turn(*, conversation_id, role, content,
                assistant_request_id)                     追加一条消息
    list_turns(conversation_id)                           消息列表（created_at, turn_id）

边界（严格）：

* **不调用** AIOrchestrator / Router / RAG / Tool / Text-to-SQL / LLM ——
  本层不负责任何 AI Execution（Step 3 §1.4）；
* **不依赖** FastAPI（无 Request / Response / HTTPException / Depends）；
* **不接触** SQLAlchemy Session / select / insert / update / engine ——
  持久化全部委托 ``ConversationRepository``（事务由 Repository 持有，Step 4 §4）；
* **不查询** Trace / Timeline / Outcome（观测组合属 Application Layer）；
* **不生成** assistant_request_id（由 AIOrchestrator 生成并传入）；
* **生成** conversation_id（服务端签发；客户端不可提供）。

错误边界：

    对外：ConversationServiceError / ConversationNotFoundError /
          ConversationArchivedError
    对内：ConversationRepositoryError（Repository 错误不吞；
          NotFound / Archived 子类映射为对应 Service 异常）
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from backend.app.db.conversation_repository import (
    CONVERSATION_STATUS_ACTIVE,
    CONVERSATION_STATUS_ARCHIVED,
    TURN_ROLE_ASSISTANT,
    TURN_ROLE_USER,
    ConversationArchivedRepositoryError,
    ConversationNotFoundRepositoryError,
    ConversationRepository,
    ConversationRow,
    ConversationTurnRow,
    validate_assistant_request_id,
    validate_content,
    validate_conversation_id,
    validate_project_id,
    validate_role,
)

__all__ = [
    "ConversationService",
    "ConversationServiceError",
    "ConversationNotFoundError",
    "ConversationArchivedError",
    "ConversationView",
    "ConversationTurnView",
    "new_conversation_id",
    "CONVERSATION_ID_PREFIX",
]

#: 仅用于日志 / 错误文案标识（conversation_id 本身无前缀）。
CONVERSATION_ID_PREFIX: Final[str] = "conv"


def new_conversation_id() -> str:
    """生成 conversation_id（**服务端**签发；uuid4，与 new_request_id 同风格）。

    客户端不得提供 conversation_id / status / created_at / updated_at。
    """
    return str(uuid.uuid4())


@dataclass(frozen=True)
class ConversationView:
    """Conversation 对外视图（字段 = Step 2 冻结的 5 字段）。"""

    conversation_id: str
    project_id: str
    created_at: datetime
    updated_at: datetime
    status: str


@dataclass(frozen=True)
class ConversationTurnView:
    """ConversationTurn 对外视图（字段 = Step 2 冻结的 6 字段）。"""

    turn_id: int
    conversation_id: str
    role: str
    content: str
    assistant_request_id: str | None
    created_at: datetime


class ConversationServiceError(Exception):
    """Conversation Service 边界错误基类。"""


class ConversationNotFoundError(ConversationServiceError):
    """会话不存在（命令路径；``get_conversation`` 仍返回 None）。"""


class ConversationArchivedError(ConversationServiceError):
    """ARCHIVED 会话不允许追加 Turn。"""


def _to_conversation_view(row: ConversationRow) -> ConversationView:
    return ConversationView(
        conversation_id=row.conversation_id,
        project_id=row.project_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        status=row.status,
    )


def _to_turn_view(row: ConversationTurnRow) -> ConversationTurnView:
    return ConversationTurnView(
        turn_id=row.turn_id,
        conversation_id=row.conversation_id,
        role=row.role,
        content=row.content,
        assistant_request_id=row.assistant_request_id,
        created_at=row.created_at,
    )


class ConversationService:
    """Conversation 生命周期（业务规则 owner；持久化委托 Repository）。"""

    def __init__(
        self,
        repository: ConversationRepository | None = None,
    ) -> None:
        """构造 Service。

        Args:
            repository: Conversation 仓储；None 时构造默认
                ``ConversationRepository()``（复用全局 session factory；
                **构造期不连接数据库**）。测试可注入 Fake。
        """
        self._repository = (
            repository if repository is not None else ConversationRepository()
        )

    # ---------- 只读暴露（便于测试 / 装配断言） ----------

    @property
    def repository(self) -> ConversationRepository:
        """底层仓储（持久化权威）。"""
        return self._repository

    # ---------- 对外 Contract ----------

    def create_conversation(self, *, project_id: str) -> ConversationView:
        """创建会话（服务端生成 conversation_id；status = ACTIVE）。

        Raises:
            ValueError:                   project_id 非法（空 / 非 str / 超长）。
            ConversationRepositoryError:  DB 未配置或写入失败。
        """
        validated_project_id = validate_project_id(project_id)
        conversation_id = new_conversation_id()
        row = self._repository.create(
            conversation_id=conversation_id,
            project_id=validated_project_id,
            status=CONVERSATION_STATUS_ACTIVE,
        )
        return _to_conversation_view(row)

    def get_conversation(self, conversation_id: str) -> ConversationView | None:
        """读取会话；无记录 → ``None``（不猜；None 只表示确实没有记录）。

        Raises:
            ValueError:                   conversation_id 非法。
            ConversationRepositoryError:  DB 未配置或读取失败。
        """
        validated_id = validate_conversation_id(conversation_id)
        row = self._repository.get_by_conversation_id(validated_id)
        if row is None:
            return None
        return _to_conversation_view(row)

    def archive_conversation(self, conversation_id: str) -> ConversationView:
        """归档会话（ACTIVE → ARCHIVED；**幂等**，第二次不报错）。

        Raises:
            ValueError:                    conversation_id 非法。
            ConversationNotFoundError:    会话不存在。
            ConversationRepositoryError:   DB 未配置或写入失败。
        """
        validated_id = validate_conversation_id(conversation_id)
        current = self._repository.get_by_conversation_id(validated_id)
        if current is None:
            raise ConversationNotFoundError(f"conversation 不存在: {validated_id}")
        if current.status == CONVERSATION_STATUS_ARCHIVED:
            return _to_conversation_view(current)  # 幂等：无 DB 写入
        updated = self._repository.update_status(
            validated_id, CONVERSATION_STATUS_ARCHIVED
        )
        if updated is None:  # 并发下会话已被删除
            raise ConversationNotFoundError(f"conversation 不存在: {validated_id}")
        return _to_conversation_view(updated)

    def append_turn(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        assistant_request_id: str | None,
    ) -> ConversationTurnView:
        """追加一条消息（业务规则 owner；ARCHIVED 在写入前拒绝）。

        规则（Step 2 / Step 3 冻结）：

            USER      → assistant_request_id 必须为 None
            ASSISTANT → assistant_request_id 必须非空

        Raises:
            ValueError:                   字段非法 / role 与 request 组合非法。
            ConversationNotFoundError:    会话不存在。
            ConversationArchivedError:    会话已归档（**DB write = 0**）。
            ConversationRepositoryError:   DB 未配置或写入失败。
        """
        validated_id = validate_conversation_id(conversation_id)
        validated_role = validate_role(role)
        validated_content = validate_content(content)
        validated_request_id = validate_assistant_request_id(assistant_request_id)

        if validated_role == TURN_ROLE_USER and validated_request_id is not None:
            raise ValueError("USER turn 的 assistant_request_id 必须为 None")
        if validated_role == TURN_ROLE_ASSISTANT and validated_request_id is None:
            raise ValueError("ASSISTANT turn 的 assistant_request_id 必须非空")

        # 状态守卫（写入前判定：write before reject = 0）
        current = self._repository.get_by_conversation_id(validated_id)
        if current is None:
            raise ConversationNotFoundError(f"conversation 不存在: {validated_id}")
        if current.status == CONVERSATION_STATUS_ARCHIVED:
            raise ConversationArchivedError(f"conversation 已归档: {validated_id}")

        try:
            row = self._repository.append_turn(
                conversation_id=validated_id,
                role=validated_role,
                content=validated_content,
                assistant_request_id=validated_request_id,
            )
        except ConversationNotFoundRepositoryError as exc:
            raise ConversationNotFoundError(str(exc)) from exc
        except ConversationArchivedRepositoryError as exc:
            raise ConversationArchivedError(str(exc)) from exc
        return _to_turn_view(row)

    def list_turns(self, conversation_id: str) -> tuple[ConversationTurnView, ...]:
        """消息列表（``created_at ASC, turn_id ASC``）。

        * 会话不存在 → ``ConversationNotFoundError``
          （不与"存在但无消息"混为一谈）；
        * 存在但无消息 → 空元组 ``()``。
        """
        validated_id = validate_conversation_id(conversation_id)
        try:
            rows = self._repository.list_turns_by_conversation_id(validated_id)
        except ConversationNotFoundRepositoryError as exc:
            raise ConversationNotFoundError(str(exc)) from exc
        return tuple(_to_turn_view(row) for row in rows)
