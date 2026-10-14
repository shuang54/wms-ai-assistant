"""UserService 单元测试（Phase 7 Recovery M1；离线 Fake Repository）。

覆盖任务书测试清单：
    用户创建与唯一性 / 密码哈希与验证 / 错误密码 / 停用用户 /
    登录成功 / 失败（统一语义）/ 登录尝试限制（锁定）/ 伪造 user_id 不可信
    （身份只能由服务端产生——见 test_auth_api.py 的 HTTP 层断言）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.db.user_repository import (
    UsernameAlreadyExistsError,
    UserRow,
)
from backend.app.services.user_service import (
    InvalidCredentialsError,
    UserAlreadyExistsError,
    UserService,
)

__all__ = [
    "FakeUserRepository",
    "test_create_user_issues_unpredictable_id",
    "test_create_user_duplicate_username_rejected",
    "test_create_user_password_policy_enforced",
    "test_authenticate_success_returns_row",
    "test_authenticate_wrong_password_uniform_failure",
    "test_authenticate_unknown_username_uniform_failure",
    "test_authenticate_disabled_user_rejected",
    "test_failed_logins_accumulate_and_lock",
    "test_locked_user_rejected_even_with_correct_password",
    "test_successful_login_resets_failure_counter",
    "test_record_failure_ignores_stale_row_count",
]


class FakeUserRepository:
    """进程内 Fake（**不**触 DB；模拟唯一约束与状态持久化）。"""

    def __init__(self) -> None:
        self.rows: dict[str, UserRow] = {}
        self._counter = 0

    # ---- 写 ----
    def create(
        self,
        *,
        user_id: str,
        username: str,
        password_hash: str,
        display_name: str = "",
        status: str = "ACTIVE",
    ) -> UserRow:
        if any(row.username == username for row in self.rows.values()):
            raise UsernameAlreadyExistsError(username)
        now = datetime.now(timezone.utc)
        row = UserRow(
            user_id=user_id,
            username=username,
            password_hash=password_hash,
            display_name=display_name,
            status=status,
            failed_login_count=0,
            locked_until=None,
            created_at=now,
            updated_at=now,
        )
        self.rows[user_id] = row
        self._counter += 1
        return row

    def apply_login_success(self, user_id: str) -> None:
        row = self.rows.get(user_id)
        if row is None:
            return
        self.rows[user_id] = UserRow(
            **{**row.__dict__, "failed_login_count": 0, "locked_until": None}
        )

    def apply_login_failure(
        self,
        user_id: str,
        *,
        max_failed_logins: int,
        lockout_minutes: int,
    ) -> None:
        """模拟**原子自增**（真实实现是单条 UPDATE；Fake 用 +1 表达语义）。"""
        row = self.rows.get(user_id)
        if row is None:
            return
        count = int(row.failed_login_count) + 1
        locked_until = None
        if count >= max_failed_logins:
            locked_until = datetime.now(timezone.utc) + timedelta(
                minutes=lockout_minutes
            )
        self.rows[user_id] = UserRow(
            **{
                **row.__dict__,
                "failed_login_count": count,
                "locked_until": locked_until,
            }
        )

    # ---- 读 ----
    def get_by_user_id(self, user_id: str) -> UserRow | None:
        return self.rows.get(user_id)

    def get_by_username(self, username: str) -> UserRow | None:
        for row in self.rows.values():
            if row.username == username:
                return row
        return None


def _service() -> tuple[UserService, FakeUserRepository]:
    repo = FakeUserRepository()
    return UserService(user_repository=repo), repo  # type: ignore[arg-type]


def test_create_user_issues_unpredictable_id() -> None:
    service, repo = _service()
    first = service.create_user(username="alice", password="password-123")
    second = service.create_user(username="bob", password="password-123")
    assert first.user_id != second.user_id
    assert len(first.user_id) >= 32  # uuid4 文本
    assert first.status == "ACTIVE"
    # 视图绝不暴露哈希
    assert not hasattr(first, "password_hash")
    assert repo.rows[first.user_id].password_hash.startswith("scrypt$")


def test_create_user_duplicate_username_rejected() -> None:
    service, _ = _service()
    service.create_user(username="alice", password="password-123")
    with pytest.raises(UserAlreadyExistsError):
        service.create_user(username="alice", password="another-password-1")


def test_create_user_password_policy_enforced() -> None:
    service, _ = _service()
    with pytest.raises(ValueError):
        service.create_user(username="alice", password="short")


def test_authenticate_success_returns_row() -> None:
    service, _ = _service()
    service.create_user(username="alice", password="password-123")
    row = service.authenticate(username="alice", password="password-123")
    assert row.username == "alice"


def test_authenticate_wrong_password_uniform_failure() -> None:
    service, repo = _service()
    service.create_user(username="alice", password="password-123")
    with pytest.raises(InvalidCredentialsError):
        service.authenticate(username="alice", password="wrong-password-1")
    # 失败被累计（持久化）
    assert repo.rows[repo.rows.keys().__iter__().__next__()].failed_login_count == 1


def test_authenticate_unknown_username_uniform_failure() -> None:
    service, repo = _service()
    with pytest.raises(InvalidCredentialsError):
        service.authenticate(username="ghost", password="password-123")
    # 未知用户不写任何状态（无可累计行）
    assert repo.rows == {}


def test_authenticate_disabled_user_rejected() -> None:
    service, repo = _service()
    view = service.create_user(username="alice", password="password-123")
    row = repo.rows[view.user_id]
    repo.rows[view.user_id] = UserRow(**{**row.__dict__, "status": "DISABLED"})
    with pytest.raises(InvalidCredentialsError):
        service.authenticate(username="alice", password="password-123")


def test_failed_logins_accumulate_and_lock() -> None:
    service, repo = _service()
    view = service.create_user(username="alice", password="password-123")
    for _ in range(5):
        with pytest.raises(InvalidCredentialsError):
            service.authenticate(username="alice", password="wrong-password-1")
    row = repo.rows[view.user_id]
    assert row.failed_login_count == 5
    assert row.locked_until is not None
    assert row.locked_until > datetime.now(timezone.utc)


def test_locked_user_rejected_even_with_correct_password() -> None:
    service, repo = _service()
    view = service.create_user(username="alice", password="password-123")
    for _ in range(5):
        with pytest.raises(InvalidCredentialsError):
            service.authenticate(username="alice", password="wrong-password-1")
    with pytest.raises(InvalidCredentialsError):
        service.authenticate(username="alice", password="password-123")


def test_successful_login_resets_failure_counter() -> None:
    service, repo = _service()
    view = service.create_user(username="alice", password="password-123")
    for _ in range(3):
        with pytest.raises(InvalidCredentialsError):
            service.authenticate(username="alice", password="wrong-password-1")
    service.authenticate(username="alice", password="password-123")
    row = repo.rows[view.user_id]
    assert row.failed_login_count == 0
    assert row.locked_until is None


def test_record_failure_ignores_stale_row_count() -> None:
    """M1 Security Review：失败计数由**数据库原子自增**，不信赖过期行副本。

    模拟并发：另一个请求已把计数推到 3；服务端持有的 row 副本仍是 0。
    记录失败必须得到 4（而非用陈旧计数覆盖回 1）——否则并发失败可绕过锁定。
    """
    service, repo = _service()
    view = service.create_user(username="alice", password="password-123")
    for _ in range(3):
        repo.apply_login_failure(
            view.user_id, max_failed_logins=5, lockout_minutes=15
        )
    current = repo.get_by_user_id(view.user_id)
    assert current is not None and current.failed_login_count == 3
    stale = UserRow(**{**current.__dict__, "failed_login_count": 0})
    service._record_failure(stale)
    after = repo.get_by_user_id(view.user_id)
    assert after is not None and after.failed_login_count == 4
