"""UserSession Repository（Phase 7 Recovery M1 — 服务端会话持久化）。

最小数据访问层（与 ``conversation_repository`` 同风格 / 同基础设施）：

    create(...)                   插入会话（``session_id`` 由 Service 生成；
                                  只存令牌 **SHA-256**）
    get_by_token_hash(...)        只读：按令牌哈希（无 → None）
    revoke(...)                   吊销会话（幂等；无记录 → False）

复用基础设施：``backend.app.db.session.get_session_factory()``；
事务归属 = Repository（写 ``session.begin()`` / 读无事务）。

不做的（最小实现，明确不做）：

    * 不提供列表 / 分页 / 按用户查会话（"我的设备"属后续阶段）；
    * 不做滑动续期（``expires_at`` 绝对过期；语义简单可测）；
    * 不删除过期行（保留供审计；清理策略属后续阶段）；
    * 无外键 / 无级联（沿用 ``agent_*`` 表先例）。

失败语义：``SQLAlchemyError`` → ``UserSessionRepositoryError``。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models.user_session import (
    USER_SESSION_TABLE,
    UserSession,
)
from backend.app.db.session import get_session_factory

__all__ = [
    "UserSessionRepository",
    "UserSessionRepositoryError",
    "UserSessionRow",
    "USER_SESSION_TOKEN_HASH_LENGTH",
    "validate_token_hash",
]

#: 令牌哈希长度（SHA-256 hex = 64）。
USER_SESSION_TOKEN_HASH_LENGTH: Final[int] = 64


def validate_token_hash(token_hash: str) -> str:
    """令牌哈希校验（64 位 hex；防止把令牌本体误当哈希写入）。"""
    if not isinstance(token_hash, str) or len(token_hash) != (
        USER_SESSION_TOKEN_HASH_LENGTH
    ):
        raise ValueError("token_hash 必须是 64 字符 SHA-256 hex")
    return token_hash


@dataclass(frozen=True)
class UserSessionRow:
    """会话行（frozen；**不**含令牌本体——库内只有哈希）。"""

    session_id: str
    user_id: str
    token_hash: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


class UserSessionRepositoryError(RuntimeError):
    """会话持久化失败（事务已回滚；不含 SQL / 连接串 / 凭据）。"""


class UserSessionRepository:
    """``ai_ops.user_session`` 最小数据访问层。"""

    def __init__(
        self, session_factory: sessionmaker[Session] | None = None
    ) -> None:
        self._session_factory = session_factory or get_session_factory()

    # ---------- 写 ----------

    def create(
        self,
        *,
        session_id: str,
        user_id: str,
        token_hash: str,
        expires_at: datetime,
    ) -> UserSessionRow:
        """插入会话（``token_hash`` 唯一；重复 ⇒ 仓库错误，不静默复用）。"""
        factory = self._require_factory()
        try:
            with factory() as session, session.begin():
                row = UserSession(
                    session_id=session_id,
                    user_id=user_id,
                    token_hash=validate_token_hash(token_hash),
                    expires_at=expires_at,
                )
                session.add(row)
        except SQLAlchemyError as exc:
            raise UserSessionRepositoryError(
                f"会话创建失败（{type(exc).__name__}）"
            ) from exc
        return self.get_by_token_hash(token_hash)  # type: ignore[return-value]

    def revoke(self, session_id: str, *, revoked_at: datetime) -> bool:
        """吊销会话（幂等：重复吊销仍成功；无记录 → False）。"""
        factory = self._require_factory()
        try:
            with factory() as session, session.begin():
                row = session.get(UserSession, session_id)
                if row is None:
                    return False
                if row.revoked_at is None:
                    row.revoked_at = revoked_at
                return True
        except SQLAlchemyError as exc:
            raise UserSessionRepositoryError(
                f"会话吊销失败（{type(exc).__name__}）"
            ) from exc

    # ---------- 读 ----------

    def get_by_token_hash(self, token_hash: str) -> UserSessionRow | None:
        """按令牌哈希只读读取（无 → None）。"""
        factory = self._require_factory()
        try:
            with factory() as session:
                row = session.scalar(
                    select(UserSession).where(
                        UserSession.token_hash == token_hash
                    )
                )
                return self._to_row(row) if row is not None else None
        except SQLAlchemyError as exc:
            raise UserSessionRepositoryError(
                f"会话读取失败（{type(exc).__name__}）"
            ) from exc

    # ---------- 内部 ----------

    def _require_factory(self) -> sessionmaker[Session]:
        if self._session_factory is None:
            raise UserSessionRepositoryError(
                "DATABASE_URL 未配置；会话持久化不可用"
            )
        return self._session_factory

    @staticmethod
    def _to_row(row: UserSession) -> UserSessionRow:
        return UserSessionRow(
            session_id=row.session_id,
            user_id=row.user_id,
            token_hash=row.token_hash,
            created_at=row.created_at,
            expires_at=row.expires_at,
            revoked_at=row.revoked_at,
        )
