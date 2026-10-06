"""Conversation Repository（Phase 4.1 Step 5 —— 最小持久化层）。

最小数据访问层（与 ``assistant_outcome_repository`` / ``llm_usage_repository``
同风格、同基础设施）：

    create(...)                          插入会话（conversation_id 由 Service 生成）
    get_by_conversation_id(...)          只读：会话（无记录 → None）
    update_status(...)                   状态迁移 + updated_at = now（无记录 → None）
    append_turn(...)                     **原子**：插入 Turn + 更新会话 updated_at
    list_turns_by_conversation_id(...)   会话的 Turn 列表（created_at ASC, turn_id ASC）
    find_turn_by_idempotency_key(...)    只读：按 (conversation_id, idempotency_key)
                                         查 USER Turn（无记录 → None）
    find_next_assistant_turn(...)        只读：指定 USER Turn 之后的第一个
                                         ASSISTANT Turn（无记录 → None）

复用基础设施（不新增第二套机制）：

    * Session 工厂：``backend.app.db.session.get_session_factory()``
    * 事务：``with factory() as session, session.begin():``（写）·
      ``with factory() as session:``（读，无写事务）
    * ORM：``backend.app.db.models.Conversation`` / ``ConversationTurn``

事务归属（Step 4 §4 冻结：**方案 A**）：

    事务由 **Repository** 持有（``session.begin()`` 正常退出提交 / 异常回滚）；
    Service 不接触 Session / 不 commit。

不做的（Step 4 §11 冻结，默认不新增）：

    * 不提供 delete / find_by_project / search / count / pagination；
    * 不生成 conversation_id（ID 生成属 Service）；
    * 不返回 ORM 对象 / 不返回 Session（只返回 frozen Row）；
    * 不查询 Trace / Timeline / Outcome（观测组合属 Application Layer）；
    * 不对 assistant_request_id 建外键（correlation only）。

失败语义：``SQLAlchemyError`` → ``ConversationRepositoryError``（事务已回滚）；
"会话不存在" / "会话已归档"用**子类**表达，供 Service 精确映射为
``ConversationNotFoundError`` / ``ConversationArchivedError``。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models.conversation import (
    CONVERSATION_STATUS_ACTIVE,
    CONVERSATION_STATUS_ARCHIVED,
    Conversation,
)
from backend.app.db.models.conversation_turn import (
    CONVERSATION_TURN_IDEMPOTENCY_INDEX,
    IDEMPOTENCY_KEY_MAX_LENGTH,
    TURN_ROLE_ASSISTANT,
    TURN_ROLE_USER,
    ConversationTurn,
)
from backend.app.db.session import get_session_factory

__all__ = [
    "ConversationRepository",
    "ConversationRepositoryError",
    "ConversationNotFoundRepositoryError",
    "ConversationArchivedRepositoryError",
    "ConversationTurnIdempotencyConflictRepositoryError",
    "ConversationRow",
    "ConversationTurnRow",
    "CONVERSATION_READ_COLUMNS",
    "CONVERSATION_TURN_READ_COLUMNS",
    "CONVERSATION_STATUS_VALUES",
    "TURN_ROLE_VALUES",
    "IDEMPOTENCY_KEY_MAX_LENGTH",
    "validate_conversation_id",
    "validate_project_id",
    "validate_status",
    "validate_role",
    "validate_content",
    "validate_assistant_request_id",
    "validate_idempotency_key",
]

#: Conversation 只读查询允许返回的字段（显式列，**不用** SELECT *）。
CONVERSATION_READ_COLUMNS: Final[tuple[str, ...]] = (
    "conversation_id",
    "project_id",
    "created_at",
    "updated_at",
    "status",
)

#: ConversationTurn 只读查询允许返回的字段（显式列）。
#: Phase 4.2 Step 6：新增 ``idempotency_key``（仅 USER Turn 有值）。
CONVERSATION_TURN_READ_COLUMNS: Final[tuple[str, ...]] = (
    "turn_id",
    "conversation_id",
    "role",
    "content",
    "assistant_request_id",
    "created_at",
    "idempotency_key",
)

#: status 合法取值（Step 2 §1.4 冻结；只有两态）。
CONVERSATION_STATUS_VALUES: Final[tuple[str, ...]] = (
    CONVERSATION_STATUS_ACTIVE,
    CONVERSATION_STATUS_ARCHIVED,
)

#: role 合法取值（Step 2 §4 冻结；只有两态）。
TURN_ROLE_VALUES: Final[tuple[str, ...]] = (
    TURN_ROLE_USER,
    TURN_ROLE_ASSISTANT,
)

#: 与 ORM 列宽一致的长度上限。
_CONVERSATION_ID_MAX_LENGTH: Final[int] = 128
_PROJECT_ID_MAX_LENGTH: Final[int] = 128
_ASSISTANT_REQUEST_ID_MAX_LENGTH: Final[int] = 128


@dataclass(frozen=True)
class ConversationRow:
    """会话行（**不是** ORM 对象；字段 = :data:`CONVERSATION_READ_COLUMNS`）。"""

    conversation_id: str
    project_id: str
    created_at: datetime
    updated_at: datetime
    status: str


@dataclass(frozen=True)
class ConversationTurnRow:
    """消息行（**不是** ORM 对象；字段 = :data:`CONVERSATION_TURN_READ_COLUMNS`）。"""

    turn_id: int
    conversation_id: str
    role: str
    content: str
    assistant_request_id: str | None
    created_at: datetime
    #: Phase 4.2 Step 6：请求幂等键（仅 USER Turn 有值；ASSISTANT 恒 None）。
    #: 带默认值 —— 兼容历史行 / 无幂等写入路径。
    idempotency_key: str | None = None


class ConversationRepositoryError(Exception):
    """Conversation 持久化 / 读取失败（事务已回滚）。

    基类：DB 不可用 / SQL 失败等基础设施错误。
    """


class ConversationNotFoundRepositoryError(ConversationRepositoryError):
    """会话不存在（供 Service 映射为 ``ConversationNotFoundError``）。"""


class ConversationArchivedRepositoryError(ConversationRepositoryError):
    """会话已归档，禁止追加 Turn（供 Service 映射为
    ``ConversationArchivedError``）。"""


class ConversationTurnIdempotencyConflictRepositoryError(
    ConversationRepositoryError
):
    """``UNIQUE(conversation_id, idempotency_key)`` 冲突（Phase 4.2 Step 6）。

    含义：同一会话内该幂等键**已经**被另一个请求占用（并发 / 重复提交）。
    最终保证层 = 数据库唯一索引（应用层 check-then-act **不是**唯一手段）。

    供 Service 映射为幂等冲突 → HTTP **409**（**不得**退化为 500）。
    """


def validate_conversation_id(value: object) -> str:
    """校验 conversation_id（不生成 / 不截断 / 不改写；由 Service 生成）。"""
    if not isinstance(value, str):
        raise ValueError(
            f"conversation_id 必须是 str（当前: {type(value).__name__}）"
        )
    if not value.strip():
        raise ValueError("conversation_id 不能为空或纯空白")
    if len(value) > _CONVERSATION_ID_MAX_LENGTH:
        raise ValueError(
            f"conversation_id 超出长度上限（{len(value)} > "
            f"{_CONVERSATION_ID_MAX_LENGTH}）"
        )
    return value


def validate_project_id(value: object) -> str:
    """校验 project_id（NOT NULL；非空 / 长度上限 128）。"""
    if not isinstance(value, str):
        raise ValueError(f"project_id 必须是 str（当前: {type(value).__name__}）")
    if not value.strip():
        raise ValueError("project_id 不能为空或纯空白")
    if len(value) > _PROJECT_ID_MAX_LENGTH:
        raise ValueError(
            f"project_id 超出长度上限（{len(value)} > {_PROJECT_ID_MAX_LENGTH}）"
        )
    return value


def validate_status(value: object) -> str:
    """校验 status（仅 ACTIVE / ARCHIVED）。"""
    if not isinstance(value, str):
        raise ValueError(f"status 必须是 str（当前: {type(value).__name__}）")
    if value not in CONVERSATION_STATUS_VALUES:
        raise ValueError(f"status 非法: {value!r}（允许: {CONVERSATION_STATUS_VALUES}）")
    return value


def validate_role(value: object) -> str:
    """校验 role（仅 USER / ASSISTANT）。"""
    if not isinstance(value, str):
        raise ValueError(f"role 必须是 str（当前: {type(value).__name__}）")
    if value not in TURN_ROLE_VALUES:
        raise ValueError(f"role 非法: {value!r}（允许: {TURN_ROLE_VALUES}）")
    return value


def validate_content(value: object) -> str:
    """校验 content（非空；不校验语义 / 不做 DLP）。"""
    if not isinstance(value, str):
        raise ValueError(f"content 必须是 str（当前: {type(value).__name__}）")
    if not value.strip():
        raise ValueError("content 不能为空或纯空白")
    return value


def validate_assistant_request_id(value: object) -> str | None:
    """校验 assistant_request_id（可空；非空时长度 ≤ 128；**不是**外键）。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"assistant_request_id 必须是 str 或 None（当前: {type(value).__name__}）"
        )
    if not value.strip():
        raise ValueError("assistant_request_id 不能为空或纯空白")
    if len(value) > _ASSISTANT_REQUEST_ID_MAX_LENGTH:
        raise ValueError(
            "assistant_request_id 超出长度上限（"
            f"{len(value)} > {_ASSISTANT_REQUEST_ID_MAX_LENGTH}）"
        )
    return value


def validate_idempotency_key(value: object) -> str | None:
    """校验 idempotency_key（Phase 4.2 Step 6；可空；**不生成 / 不改写**）。

    规则：

        * ``None``                     → 无幂等（原样返回 None）
        * 纯空白（``strip()`` 后为空）  → 视为无幂等（返回 None；**不是**错误）
        * 非空且 ``len <= 128``        → 原样返回（**不 trim / 不规范化**）
        * ``len > 128``                → ``ValueError``（API 层映射 422）

    Returns:
        规范化后的幂等键（``None`` = 本请求不参与幂等）。
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"idempotency_key 必须是 str 或 None（当前: {type(value).__name__}）"
        )
    if not value.strip():
        return None
    if len(value) > IDEMPOTENCY_KEY_MAX_LENGTH:
        raise ValueError(
            f"idempotency_key 超出长度上限（{len(value)} > "
            f"{IDEMPOTENCY_KEY_MAX_LENGTH}）"
        )
    return value


#: PostgreSQL UNIQUE violation 的 SQLSTATE（psycopg2 ``pgcode`` / psycopg3 ``sqlstate``）。
_UNIQUE_VIOLATION_SQLSTATE: Final[frozenset[str]] = frozenset({"23505"})


def _to_turn_row(raw: Any) -> ConversationTurnRow:
    """Row 组装（显式逐字段；**不**返回 ORM 对象）。"""
    return ConversationTurnRow(
        turn_id=raw.turn_id,
        conversation_id=raw.conversation_id,
        role=raw.role,
        content=raw.content,
        assistant_request_id=raw.assistant_request_id,
        created_at=raw.created_at,
        idempotency_key=raw.idempotency_key,
    )


def _is_idempotency_unique_violation(exc: IntegrityError) -> bool:
    """判断 IntegrityError 是否命中 ``UNIQUE(conversation_id, idempotency_key)``。

    **只识别幂等唯一约束**（Step 6 §13）：其它 IntegrityError（PK / NOT NULL /
    FK / 其它唯一约束）**不得**被误判为幂等冲突（否则错误语义被污染）。

    判定顺序：

        1. SQLSTATE 必须是 UNIQUE violation（``23505``）；
        2. 约束名（``diag.constraint_name``）等于幂等索引名 → 命中；
        3. 约束名不可得 → 退化为消息文本匹配（仅当显式指向幂等键 / 幂等索引）。
    """
    original = getattr(exc, "orig", None)
    code = getattr(original, "pgcode", None) or getattr(original, "sqlstate", None)
    if code not in _UNIQUE_VIOLATION_SQLSTATE:
        return False

    diagnostic = getattr(original, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    if constraint_name is not None:
        return constraint_name == CONVERSATION_TURN_IDEMPOTENCY_INDEX

    message = str(exc).lower()
    return (
        "idempotency_key" in message
        or CONVERSATION_TURN_IDEMPOTENCY_INDEX.lower() in message
    )


class ConversationRepository:
    """``ai_ops.conversation`` / ``ai_ops.conversation_turn`` 的最小仓储。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        """构造仓储。

        Args:
            session_factory: Session 工厂；None 时用全局
                ``get_session_factory()``（DATABASE_URL 为空 → 操作时抛
                ``ConversationRepositoryError``）。测试可注入自定义工厂。
        """
        self._session_factory = session_factory

    # ---------- 依赖解析 ----------

    def _get_session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is not None:
            return self._session_factory
        factory = get_session_factory()
        if factory is None:
            raise ConversationRepositoryError(
                "DATABASE_URL 未配置，无法访问 Conversation 持久化层"
            )
        return factory

    # ---------- SQL 构造点（build_*；唯一构造位置） ----------

    @staticmethod
    def _conversation_columns() -> tuple[Any, ...]:
        return tuple(
            getattr(Conversation, name) for name in CONVERSATION_READ_COLUMNS
        )

    @staticmethod
    def _turn_columns() -> tuple[Any, ...]:
        return tuple(
            getattr(ConversationTurn, name) for name in CONVERSATION_TURN_READ_COLUMNS
        )

    def build_conversation_select(self, conversation_id: str) -> Any:
        """按 conversation_id 读取会话（显式列；精确匹配）。"""
        return (
            select(*self._conversation_columns())
            .where(Conversation.conversation_id == conversation_id)
        )

    def build_conversation_insert(
        self,
        *,
        conversation_id: str,
        project_id: str,
        status: str,
    ) -> Any:
        """插入会话（created_at / updated_at 由数据库 now() 生成）。"""
        return (
            insert(Conversation)
            .values(
                conversation_id=conversation_id,
                project_id=project_id,
                status=status,
            )
            .returning(*self._conversation_columns())
        )

    def build_status_update(self, conversation_id: str, status: str) -> Any:
        """状态迁移 + updated_at = now（原子；无匹配 → 无返回行）。"""
        return (
            update(Conversation)
            .where(Conversation.conversation_id == conversation_id)
            .values(status=status, updated_at=func.now())
            .returning(*self._conversation_columns())
        )

    def build_conversation_touch_update(self, conversation_id: str) -> Any:
        """仅更新 updated_at = now（append_turn 的第二个原子步骤）。"""
        return (
            update(Conversation)
            .where(Conversation.conversation_id == conversation_id)
            .values(updated_at=func.now())
        )

    def build_turn_select(self, conversation_id: str) -> Any:
        """会话的 Turn 列表（显式列；``created_at ASC, turn_id ASC``）。"""
        return (
            select(*self._turn_columns())
            .where(ConversationTurn.conversation_id == conversation_id)
            .order_by(ConversationTurn.created_at.asc(), ConversationTurn.turn_id.asc())
        )

    def build_turn_insert(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        assistant_request_id: str | None,
        idempotency_key: str | None = None,
    ) -> Any:
        """插入一条消息（created_at 由数据库 now() 生成）。

        Args:
            idempotency_key: Phase 4.2 Step 6 请求幂等键（**只写 USER Turn**；
                ASSISTANT 必须 None）。
        """
        return (
            insert(ConversationTurn)
            .values(
                conversation_id=conversation_id,
                role=role,
                content=content,
                assistant_request_id=assistant_request_id,
                idempotency_key=idempotency_key,
            )
            .returning(*self._turn_columns())
        )

    def build_turn_select_by_idempotency_key(
        self,
        *,
        conversation_id: str,
        idempotency_key: str,
        role: str = TURN_ROLE_USER,
    ) -> Any:
        """按 ``(conversation_id, idempotency_key)`` 读取 USER Turn（精确匹配）。

        并发说明：本查询**只是**前置检查；最终唯一性由
        ``UNIQUE(conversation_id, idempotency_key)`` 保证（Step 4A §12）。
        """
        return (
            select(*self._turn_columns())
            .where(ConversationTurn.conversation_id == conversation_id)
            .where(ConversationTurn.idempotency_key == idempotency_key)
            .where(ConversationTurn.role == role)
            .order_by(ConversationTurn.turn_id.asc())
        )

    def build_next_assistant_turn_select(
        self,
        *,
        conversation_id: str,
        after_turn_id: int,
    ) -> Any:
        """读取指定 USER Turn 之后的**第一个** ASSISTANT Turn。

        归属判定只依赖 ``conversation_id`` + ``turn_id`` 顺序（Step 6 §16）：
        **不**通过 ``assistant_request_id`` 反查会话（它只是 correlation ID）。
        """
        return (
            select(*self._turn_columns())
            .where(ConversationTurn.conversation_id == conversation_id)
            .where(ConversationTurn.role == TURN_ROLE_ASSISTANT)
            .where(ConversationTurn.turn_id > after_turn_id)
            .order_by(ConversationTurn.turn_id.asc())
            .limit(1)
        )

    def build_conversation_status_lookup(self, conversation_id: str) -> Any:
        """读取会话状态（append_turn / list 的存在性与状态守卫；显式单列）。"""
        return select(Conversation.status).where(
            Conversation.conversation_id == conversation_id
        )

    # ---------- 写入 ----------

    def create(
        self,
        *,
        conversation_id: str,
        project_id: str,
        status: str = CONVERSATION_STATUS_ACTIVE,
    ) -> ConversationRow:
        """插入会话（conversation_id 由 **Service** 生成，本层不生成 ID）。

        Raises:
            ValueError: 参数非法（触达 DB 之前拦截）。
            ConversationRepositoryError: DB 未配置或写入失败（事务已回滚）。
        """
        validated_id = validate_conversation_id(conversation_id)
        validated_project = validate_project_id(project_id)
        validated_status = validate_status(status)
        factory = self._get_session_factory()
        statement = self.build_conversation_insert(
            conversation_id=validated_id,
            project_id=validated_project,
            status=validated_status,
        )
        try:
            with factory() as session, session.begin():
                raw = session.execute(statement).one()
        except SQLAlchemyError as exc:
            raise ConversationRepositoryError(
                f"Conversation 写入失败: {type(exc).__name__}"
            ) from exc
        return ConversationRow(
            conversation_id=raw.conversation_id,
            project_id=raw.project_id,
            created_at=raw.created_at,
            updated_at=raw.updated_at,
            status=raw.status,
        )

    def append_turn(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        assistant_request_id: str | None,
        idempotency_key: str | None = None,
    ) -> ConversationTurnRow:
        """**原子**追加一条消息（插入 Turn + 更新会话 updated_at）。

        事务（一个 ``session.begin()``）：

            BEGIN
              1) SELECT conversation.status（存在 / 状态守卫）
              2) INSERT conversation_turn（含 idempotency_key）
              3) UPDATE conversation SET updated_at = now()
            COMMIT

        任一步失败 → ROLLBACK：绝不留"Turn 已存在但 updated_at 未更新"。
        ARCHIVED 会话在 **插入之前** 拒绝（write before reject = 0）。

        Phase 4.2 Step 6：``idempotency_key`` 非 None 且发生
        ``UNIQUE(conversation_id, idempotency_key)`` 冲突 →
        :class:`ConversationTurnIdempotencyConflictRepositoryError`
        （供上层映射 409；**不是** 500）。

        Raises:
            ValueError:                              参数非法。
            ConversationNotFoundRepositoryError:    会话不存在。
            ConversationArchivedRepositoryError:    会话已归档。
            ConversationTurnIdempotencyConflictRepositoryError:
                                                    幂等键已被占用（并发 / 重复）。
            ConversationRepositoryError:            DB 未配置 / 其它 SQL 失败。
        """
        validated_conversation_id = validate_conversation_id(conversation_id)
        validated_role = validate_role(role)
        validated_content = validate_content(content)
        validated_request_id = validate_assistant_request_id(assistant_request_id)
        validated_key = validate_idempotency_key(idempotency_key)
        factory = self._get_session_factory()

        status_statement = self.build_conversation_status_lookup(
            validated_conversation_id
        )
        insert_statement = self.build_turn_insert(
            conversation_id=validated_conversation_id,
            role=validated_role,
            content=validated_content,
            assistant_request_id=validated_request_id,
            idempotency_key=validated_key,
        )
        touch_statement = self.build_conversation_touch_update(
            validated_conversation_id
        )
        try:
            with factory() as session, session.begin():
                current_status = session.execute(status_statement).scalar_one_or_none()
                if current_status is None:
                    raise ConversationNotFoundRepositoryError(
                        f"conversation 不存在: {validated_conversation_id}"
                    )
                if current_status == CONVERSATION_STATUS_ARCHIVED:
                    raise ConversationArchivedRepositoryError(
                        f"conversation 已归档: {validated_conversation_id}"
                    )
                raw = session.execute(insert_statement).one()
                session.execute(touch_statement)
        except (
            ConversationNotFoundRepositoryError,
            ConversationArchivedRepositoryError,
        ):
            raise
        except IntegrityError as exc:
            if _is_idempotency_unique_violation(exc):
                raise ConversationTurnIdempotencyConflictRepositoryError(
                    "idempotency_key 已被同一会话内的其它请求占用: "
                    f"conversation_id={validated_conversation_id}"
                ) from exc
            raise ConversationRepositoryError(
                f"ConversationTurn 写入失败: {type(exc).__name__}"
            ) from exc
        except SQLAlchemyError as exc:
            raise ConversationRepositoryError(
                f"ConversationTurn 写入失败: {type(exc).__name__}"
            ) from exc
        return _to_turn_row(raw)

    def update_status(self, conversation_id: str, status: str) -> ConversationRow | None:
        """状态迁移（+ updated_at = now）；无匹配 → ``None``（不是错误）。

        Raises:
            ValueError:                     参数非法。
            ConversationRepositoryError:    DB 未配置或写入失败。
        """
        validated_id = validate_conversation_id(conversation_id)
        validated_status = validate_status(status)
        factory = self._get_session_factory()
        statement = self.build_status_update(validated_id, validated_status)
        try:
            with factory() as session, session.begin():
                raw = session.execute(statement).one_or_none()
        except SQLAlchemyError as exc:
            raise ConversationRepositoryError(
                f"Conversation 状态更新失败: {type(exc).__name__}"
            ) from exc
        if raw is None:
            return None
        return ConversationRow(
            conversation_id=raw.conversation_id,
            project_id=raw.project_id,
            created_at=raw.created_at,
            updated_at=raw.updated_at,
            status=raw.status,
        )

    # ---------- 只读 ----------

    def get_by_conversation_id(self, conversation_id: str) -> ConversationRow | None:
        """读取会话；无记录 → ``None``（**不猜**；None 只表示确实没有记录）。"""
        validated_id = validate_conversation_id(conversation_id)
        factory = self._get_session_factory()
        statement = self.build_conversation_select(validated_id)
        try:
            with factory() as session:
                raw = session.execute(statement).one_or_none()
        except SQLAlchemyError as exc:
            raise ConversationRepositoryError(
                f"Conversation 读取失败: {type(exc).__name__}"
            ) from exc
        if raw is None:
            return None
        return ConversationRow(
            conversation_id=raw.conversation_id,
            project_id=raw.project_id,
            created_at=raw.created_at,
            updated_at=raw.updated_at,
            status=raw.status,
        )

    def list_turns_by_conversation_id(
        self,
        conversation_id: str,
    ) -> tuple[ConversationTurnRow, ...]:
        """读取会话的 Turn 列表（``created_at ASC, turn_id ASC``）。

        * 会话不存在 → ``ConversationNotFoundRepositoryError``
          （**不**与"存在但无 Turn"混为一谈）；
        * 存在但无 Turn → 空元组 ``()``。
        """
        validated_id = validate_conversation_id(conversation_id)
        factory = self._get_session_factory()
        status_statement = self.build_conversation_status_lookup(validated_id)
        turn_statement = self.build_turn_select(validated_id)
        try:
            with factory() as session:
                exists = session.execute(status_statement).scalar_one_or_none()
                if exists is None:
                    raise ConversationNotFoundRepositoryError(
                        f"conversation 不存在: {validated_id}"
                    )
                rows = session.execute(turn_statement).all()
        except ConversationNotFoundRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise ConversationRepositoryError(
                f"ConversationTurn 读取失败: {type(exc).__name__}"
            ) from exc
        return tuple(_to_turn_row(raw) for raw in rows)

    def find_turn_by_idempotency_key(
        self,
        *,
        conversation_id: str,
        idempotency_key: str,
        role: str = TURN_ROLE_USER,
    ) -> ConversationTurnRow | None:
        """按 ``(conversation_id, idempotency_key)`` 读取 USER Turn；无记录 → ``None``。

        Phase 4.2 Step 6（Step 6 §15）：请求进入时**先查**幂等键，避免
        "先创建 USER 再发现 duplicate"。

        Raises:
            ValueError:                    参数非法（含 role 非法）。
            ConversationRepositoryError:   DB 未配置 / 读取失败。
        """
        validated_id = validate_conversation_id(conversation_id)
        validated_role = validate_role(role)
        validated_key = validate_idempotency_key(idempotency_key)
        if validated_key is None:
            raise ValueError("idempotency_key 不能为空（查询幂等键必须显式提供）")
        factory = self._get_session_factory()
        statement = self.build_turn_select_by_idempotency_key(
            conversation_id=validated_id,
            idempotency_key=validated_key,
            role=validated_role,
        )
        try:
            with factory() as session:
                rows = session.execute(statement).all()
        except SQLAlchemyError as exc:
            raise ConversationRepositoryError(
                f"ConversationTurn 读取失败: {type(exc).__name__}"
            ) from exc
        if not rows:
            return None
        return _to_turn_row(rows[0])

    def find_next_assistant_turn(
        self,
        *,
        conversation_id: str,
        after_turn_id: int,
    ) -> ConversationTurnRow | None:
        """读取 ``after_turn_id`` 之后的第一个 ASSISTANT Turn；无记录 → ``None``。

        Phase 4.2 Step 6（§16）：completed 判定 =
        ``USER Turn 存在 + 其后存在 ASSISTANT Turn``；归属只依赖
        ``conversation_id`` + ``turn_id`` 顺序，**不**用
        ``assistant_request_id`` 反查（correlation only）。

        Raises:
            ValueError:                    参数非法。
            ConversationRepositoryError:   DB 未配置 / 读取失败。
        """
        validated_id = validate_conversation_id(conversation_id)
        if not isinstance(after_turn_id, int) or isinstance(after_turn_id, bool):
            raise ValueError(
                f"after_turn_id 必须是 int（当前: {type(after_turn_id).__name__}）"
            )
        factory = self._get_session_factory()
        statement = self.build_next_assistant_turn_select(
            conversation_id=validated_id,
            after_turn_id=after_turn_id,
        )
        try:
            with factory() as session:
                raw = session.execute(statement).first()
        except SQLAlchemyError as exc:
            raise ConversationRepositoryError(
                f"ConversationTurn 读取失败: {type(exc).__name__}"
            ) from exc
        if raw is None:
            return None
        return _to_turn_row(raw)
