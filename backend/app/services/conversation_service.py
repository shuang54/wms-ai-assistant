"""Conversation Service（Phase 4.1 Step 5 —— 最小生命周期实现）。

职责（Step 3 冻结；**只有** Conversation 生命周期）：

    create_conversation(*, project_id)                    创建会话（服务端生成 ID）
    get_conversation(conversation_id)                     读取会话（无记录 → None）
    archive_conversation(conversation_id)                 归档（幂等；ACTIVE → ARCHIVED）
    append_turn(*, conversation_id, role, content,
                assistant_request_id, idempotency_key=None)   追加一条消息
    list_turns(conversation_id)                           消息列表（created_at, turn_id）
    find_turn_by_idempotency_key(*)                       按 (conversation_id,
                                                          idempotency_key) 查 USER Turn
    find_assistant_turn_after(*)                          指定 USER Turn 之后的
                                                          第一个 ASSISTANT Turn
    message_fingerprint(*)                                请求 payload 指纹
                                                          （SHA-256；不落库）

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

import hashlib
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
    ConversationTurnIdempotencyConflictRepositoryError,
    ConversationTurnRow,
    validate_assistant_request_id,
    validate_content,
    validate_conversation_id,
    validate_idempotency_key,
    validate_project_id,
    validate_role,
)

__all__ = [
    "ConversationService",
    "ConversationServiceError",
    "ConversationNotFoundError",
    "ConversationArchivedError",
    "ConversationMessageIdempotencyConflictError",
    "IdempotencyKeyReusedWithDifferentPayloadError",
    "IdempotencyKeyInFlightError",
    "ConversationView",
    "ConversationTurnView",
    "new_conversation_id",
    "message_fingerprint",
    "CONVERSATION_ID_PREFIX",
    "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD",
    "IDEMPOTENCY_KEY_IN_FLIGHT",
]

#: 错误码（Phase 4.2 Step 6；固定文案，供 API 层 409 detail 使用）。
IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD: Final[str] = (
    "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD"
)
IDEMPOTENCY_KEY_IN_FLIGHT: Final[str] = "IDEMPOTENCY_KEY_IN_FLIGHT"

#: 指纹分隔符（避免 ``conversation_id + content`` 拼接歧义；不参与任何 ID 体系）。
_FINGERPRINT_SEPARATOR: Final[str] = "\x1f"

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


class ConversationMessageIdempotencyConflictError(ConversationServiceError):
    """消息幂等冲突基类（Phase 4.2 Step 6）→ HTTP **409**。

    语义：请求**未被静默执行**（AI = 0 次），客户端需按 409 语义处理
    （换 key 重试 / 稍后重试）。**不是** 500（不是基础设施失败）。
    """


class IdempotencyKeyReusedWithDifferentPayloadError(
    ConversationMessageIdempotencyConflictError
):
    """同一 (conversation_id, idempotency_key) 但 payload 不同（同 key 异内容）。"""


class IdempotencyKeyInFlightError(ConversationMessageIdempotencyConflictError):
    """同一 (conversation_id, idempotency_key) 已被占用（并发 / 未决请求）。

    由 ``UNIQUE(conversation_id, idempotency_key)`` 冲突映射而来
    （Step 6 §13；并发最终保证层是数据库，不是应用层 check-then-act）。
    """


def message_fingerprint(*, conversation_id: str, content: str) -> str:
    """请求 payload 指纹（Phase 4.2 Step 1A §8 / Step 6 §7）。

    ```text
    SHA-256(conversation_id + content)
    ```

    * **不落库**（不需要 fingerprint 列）：已持久化的 USER Turn 保存了
      ``content``，请求进入时用同一函数现算再比对；
    * 幂等键本身**不进入**指纹（指纹判断"同 key 是否为同请求"）；
    * 用途唯一：同 key 不同 payload → 冲突（**不**静默执行第二条）。
    """
    validated_id = validate_conversation_id(conversation_id)
    validated_content = validate_content(content)
    payload = f"{validated_id}{_FINGERPRINT_SEPARATOR}{validated_content}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
        idempotency_key: str | None = None,
    ) -> ConversationTurnView:
        """追加一条消息（业务规则 owner；ARCHIVED 在写入前拒绝）。

        规则（Step 2 / Step 3 冻结）：

            USER      → assistant_request_id 必须为 None
            ASSISTANT → assistant_request_id 必须非空

        Phase 4.2 Step 6（§11 硬规则）：

            idempotency_key **只写 USER Turn**
            ASSISTANT → idempotency_key 必须为 None

        Raises:
            ValueError:                   字段非法 / role 与 request 组合非法。
            ConversationNotFoundError:    会话不存在。
            ConversationArchivedError:    会话已归档（**DB write = 0**）。
            IdempotencyKeyInFlightError:  幂等键已被同一会话占用（并发）。
            ConversationRepositoryError:   DB 未配置或写入失败。
        """
        validated_id = validate_conversation_id(conversation_id)
        validated_role = validate_role(role)
        validated_content = validate_content(content)
        validated_request_id = validate_assistant_request_id(assistant_request_id)
        validated_key = validate_idempotency_key(idempotency_key)

        if validated_role == TURN_ROLE_USER and validated_request_id is not None:
            raise ValueError("USER turn 的 assistant_request_id 必须为 None")
        if validated_role == TURN_ROLE_ASSISTANT and validated_request_id is None:
            raise ValueError("ASSISTANT turn 的 assistant_request_id 必须非空")
        if validated_role == TURN_ROLE_ASSISTANT and validated_key is not None:
            raise ValueError(
                "ASSISTANT turn 的 idempotency_key 必须为 None"
                "（幂等键只写 USER Turn）"
            )

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
                idempotency_key=validated_key,
            )
        except ConversationNotFoundRepositoryError as exc:
            raise ConversationNotFoundError(str(exc)) from exc
        except ConversationArchivedRepositoryError as exc:
            raise ConversationArchivedError(str(exc)) from exc
        except ConversationTurnIdempotencyConflictRepositoryError as exc:
            raise IdempotencyKeyInFlightError(
                f"{IDEMPOTENCY_KEY_IN_FLIGHT}: 相同 Idempotency-Key 的请求"
                "正在处理或已提交，请稍后重试或使用新的 Idempotency-Key"
            ) from exc
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

    def find_turn_by_idempotency_key(
        self,
        *,
        conversation_id: str,
        idempotency_key: str | None,
    ) -> ConversationTurnView | None:
        """按 ``(conversation_id, idempotency_key)`` 读 USER Turn；无记录 → ``None``。

        Phase 4.2 Step 6（§15）：请求进入时**先查**幂等键（避免"先创建 USER
        再发现 duplicate"）。``idempotency_key`` 为 None / 空白 → 返回 ``None``
        （= 本请求不参与幂等，走历史行为）。

        Raises:
            ValueError:                   conversation_id 非法 / key 超长。
            ConversationRepositoryError:  DB 未配置或读取失败。
        """
        validated_id = validate_conversation_id(conversation_id)
        validated_key = validate_idempotency_key(idempotency_key)
        if validated_key is None:
            return None
        row = self._repository.find_turn_by_idempotency_key(
            conversation_id=validated_id, idempotency_key=validated_key
        )
        if row is None:
            return None
        return _to_turn_view(row)

    def find_assistant_turn_after(
        self,
        *,
        conversation_id: str,
        after_turn_id: int,
    ) -> ConversationTurnView | None:
        """读取 ``after_turn_id`` 之后的第一个 ASSISTANT Turn；无记录 → ``None``。

        Phase 4.2 Step 6（§16）：``completed = USER Turn + 其后 ASSISTANT Turn``；
        归属只依赖 ``conversation_id`` + ``turn_id`` 顺序，**不**通过
        ``assistant_request_id`` 反查（correlation only）。

        Raises:
            ValueError:                   参数非法。
            ConversationRepositoryError:  DB 未配置或读取失败。
        """
        validated_id = validate_conversation_id(conversation_id)
        row = self._repository.find_next_assistant_turn(
            conversation_id=validated_id, after_turn_id=after_turn_id
        )
        if row is None:
            return None
        return _to_turn_view(row)
