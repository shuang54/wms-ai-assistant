"""M2.4 授权服务单元测试（进程内 Fake 仓储；**零 DB / 零残留**）。

覆盖任务书 §六「单元测试」条目：

    * 有效项目成员授权（MEMBER / PROJECT_OPS）
    * 不同项目之间的权限隔离
    * 无 membership 时拒绝访问
    * PROJECT_OPS 不能访问系统级端点（服务层语义）
    * SYSTEM_OPS 的授权范围符合定义
    * 客户端伪造角色无效（服务层**没有** role 入参 ⇒ 结构性保证）
    * 客户端更换项目 ID 不会扩大权限
    * 授权数据缺失或读取失败时 fail closed
    * 重复授权记录被约束或明确拒绝（仓储层）
    * 角色与 scope 不匹配时拒绝访问

本文件**不**使用真实 PostgreSQL（DB 级断言见
``tests/test_authorization_grant_db.py``，那里硬断言 5433/wms_ai_test）。
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.app.db.authorization_grant_repository import (
    AuthorizationGrantRepositoryError,
    AuthorizationGrantStoreNotConfiguredError,
    validate_project_id,
    validate_project_role,
    validate_system_role,
    validate_user_id,
)
from backend.app.db.models.authorization_grant import (
    ALL_ROLES,
    AUTHORIZATION_GRANT_TABLE,
    AUTHORIZATION_PROJECT_UNIQUE_INDEX,
    AUTHORIZATION_SCOPE_ROLE_CHECK,
    AUTHORIZATION_SCOPE_ROLE_PREDICATE,
    AUTHORIZATION_SYSTEM_UNIQUE_INDEX,
    PROJECT_ROLE_MEMBER,
    PROJECT_ROLE_PROJECT_OPS,
    PROJECT_ROLES,
    SCOPE_PROJECT,
    SCOPE_SYSTEM,
    SYSTEM_ROLE_SYSTEM_OPS,
    SYSTEM_ROLES,
    AuthorizationGrant,
)
from backend.app.services.authorization_service import (
    AuthorizationService,
    AuthorizationServiceError,
    AuthorizationStoreUnavailableError,
    get_authorization_service,
)

_ALICE = "11111111-1111-4111-8111-111111111111"
_BOB = "22222222-2222-4222-8222-222222222222"
_PROJECT_A = "vietnam-wms"
_PROJECT_B = "other-project"


# ============================================================
# Fake 仓储（只实现 Service 依赖的最小接口）
# ============================================================

class _FakeGrantRepository:
    """授权仓储替身：显式行 + 可注入故障。"""

    def __init__(
        self,
        *,
        project_rows: dict[tuple[str, str], set[str]] | None = None,
        system_rows: dict[str, set[str]] | None = None,
        failure: str | None = None,
    ) -> None:
        self.project_rows = {
            key: set(value) for key, value in (project_rows or {}).items()
        }
        self.system_rows = {
            key: set(value) for key, value in (system_rows or {}).items()
        }
        self.failure = failure

    def _guard(self) -> None:
        if self.failure == "unavailable":
            raise AuthorizationGrantRepositoryError("boom")
        if self.failure == "not_configured":
            raise AuthorizationGrantStoreNotConfiguredError("no DATABASE_URL")
        if self.failure == "corrupt":
            raise AuthorizationGrantRepositoryError("illegal row combination")

    def get_project_roles(self, *, user_id: str, project_id: str) -> frozenset[str]:
        self._guard()
        return frozenset(self.project_rows.get((user_id, project_id), set()))

    def has_system_role(self, *, user_id: str, role: str) -> bool:
        self._guard()
        return role in self.system_rows.get(user_id, set())


def _service(**kwargs: Any) -> AuthorizationService:
    return AuthorizationService(repository=_FakeGrantRepository(**kwargs))


# ============================================================
# 1. 正向授权
# ============================================================

class TestPositiveGrants:
    def test_project_member_is_member_but_not_ops(self) -> None:
        service = _service(
            project_rows={(_ALICE, _PROJECT_A): {PROJECT_ROLE_MEMBER}}
        )

        assert service.project_role(_ALICE, _PROJECT_A) == PROJECT_ROLE_MEMBER
        assert service.is_project_member(_ALICE, _PROJECT_A) is True
        assert service.is_project_ops(_ALICE, _PROJECT_A) is False

    def test_project_ops_is_member_and_ops(self) -> None:
        service = _service(
            project_rows={(_ALICE, _PROJECT_A): {PROJECT_ROLE_PROJECT_OPS}}
        )

        assert service.project_role(_ALICE, _PROJECT_A) == PROJECT_ROLE_PROJECT_OPS
        assert service.is_project_member(_ALICE, _PROJECT_A) is True
        assert service.is_project_ops(_ALICE, _PROJECT_A) is True

    def test_project_ops_wins_when_both_roles_present(self) -> None:
        service = _service(
            project_rows={
                (_ALICE, _PROJECT_A): {
                    PROJECT_ROLE_MEMBER, PROJECT_ROLE_PROJECT_OPS
                }
            }
        )

        assert service.project_role(_ALICE, _PROJECT_A) == PROJECT_ROLE_PROJECT_OPS

    def test_system_ops_is_granted(self) -> None:
        service = _service(system_rows={_ALICE: {SYSTEM_ROLE_SYSTEM_OPS}})

        assert service.is_system_ops(_ALICE) is True
        assert service.is_system_ops(_BOB) is False

    def test_default_composition_root_returns_service(self) -> None:
        service = get_authorization_service()

        assert isinstance(service, AuthorizationService)


# ============================================================
# 2. 无授权 ⇒ 拒绝（绝不回退放行）
# ============================================================

class TestNoGrantDenied:
    def test_no_rows_is_denied_everywhere(self) -> None:
        service = _service()

        assert service.project_role(_ALICE, _PROJECT_A) is None
        assert service.is_project_member(_ALICE, _PROJECT_A) is False
        assert service.is_project_ops(_ALICE, _PROJECT_A) is False
        assert service.is_system_ops(_ALICE) is False

    def test_missing_project_membership_is_not_a_fallback(self) -> None:
        """另一项目的成员身份**不**构成本项目成员。"""
        service = _service(
            project_rows={(_ALICE, _PROJECT_B): {PROJECT_ROLE_PROJECT_OPS}}
        )

        assert service.is_project_member(_ALICE, _PROJECT_A) is False
        assert service.is_project_ops(_ALICE, _PROJECT_A) is False

    def test_unknown_user_is_denied(self) -> None:
        service = _service(
            project_rows={(_ALICE, _PROJECT_A): {PROJECT_ROLE_MEMBER}},
            system_rows={_ALICE: {SYSTEM_ROLE_SYSTEM_OPS}},
        )

        assert service.is_project_member(_BOB, _PROJECT_A) is False
        assert service.is_system_ops(_BOB) is False


# ============================================================
# 3. 项目级 ≠ 系统级（两个方向都不得推导）
# ============================================================

class TestProjectSystemSeparation:
    def test_project_ops_does_not_grant_system_ops(self) -> None:
        """``PROJECT_OPS``（任意项目、任意数量）**绝不**等于 ``SYSTEM_OPS``。"""
        service = _service(
            project_rows={
                (_ALICE, _PROJECT_A): {PROJECT_ROLE_PROJECT_OPS},
                (_ALICE, _PROJECT_B): {PROJECT_ROLE_PROJECT_OPS},
                (_ALICE, ""): {PROJECT_ROLE_PROJECT_OPS},
            }
        )

        assert service.is_project_ops(_ALICE, _PROJECT_A) is True
        assert service.is_system_ops(_ALICE) is False

    def test_system_ops_does_not_grant_any_project_role(self) -> None:
        """``SYSTEM_OPS`` **不**等于任意项目成员（系统级行没有 project_id）。"""
        service = _service(system_rows={_ALICE: {SYSTEM_ROLE_SYSTEM_OPS}})

        assert service.is_system_ops(_ALICE) is True
        assert service.is_project_member(_ALICE, _PROJECT_A) is False
        assert service.is_project_ops(_ALICE, _PROJECT_A) is False
        assert service.project_role(_ALICE, _PROJECT_A) is None

    def test_member_only_does_not_grant_ops_levels(self) -> None:
        service = _service(
            project_rows={(_ALICE, _PROJECT_A): {PROJECT_ROLE_MEMBER}}
        )

        assert service.is_project_ops(_ALICE, _PROJECT_A) is False
        assert service.is_system_ops(_ALICE) is False


# ============================================================
# 4. 跨项目隔离与"换 project_id 不能扩权"
# ============================================================

class TestCrossProjectIsolation:
    def test_roles_are_isolated_between_projects(self) -> None:
        service = _service(
            project_rows={
                (_ALICE, _PROJECT_A): {PROJECT_ROLE_PROJECT_OPS},
                (_BOB, _PROJECT_B): {PROJECT_ROLE_MEMBER},
            }
        )

        assert service.is_project_ops(_ALICE, _PROJECT_A) is True
        assert service.is_project_member(_ALICE, _PROJECT_B) is False
        assert service.is_project_member(_BOB, _PROJECT_B) is True
        assert service.is_project_member(_BOB, _PROJECT_A) is False

    @pytest.mark.parametrize(
        "claimed",
        ("", "   ", "other-project", "vietnam-wms ", "vietnam-wms-2", "*"),
    )
    def test_claiming_another_project_id_does_not_widen_scope(
        self, claimed: str
    ) -> None:
        """客户端声称的任何 project_id 都不是授权依据（服务层按参数精确匹配）。

        ``"vietnam-wms "``（尾空格）与 ``"*"`` 尤为重要：前者属规范化差异、
        后者是典型的通配符尝试。
        """
        service = _service(
            project_rows={(_ALICE, _PROJECT_A): {PROJECT_ROLE_PROJECT_OPS}}
        )

        assert service.is_project_member(_ALICE, claimed) is False

    def test_client_forged_role_is_structurally_impossible(self) -> None:
        """服务接口**没有** role / membership 入参 ⇒ 客户端无法主张角色。

        这是一条**结构性**断言：伪造者唯一能影响的是 project_id 字符串，
        而它必须与服务端作用域完全一致才可能命中授权行。
        """
        import inspect

        for name in ("project_role", "is_project_member", "is_project_ops"):
            parameters = inspect.signature(
                getattr(AuthorizationService, name)
            ).parameters
            assert list(parameters) == ["self", "user_id", "project_id"], name

        system_params = inspect.signature(
            AuthorizationService.is_system_ops
        ).parameters
        assert list(system_params) == ["self", "user_id"]

    def test_repository_queries_have_no_role_from_caller(self) -> None:
        """读路径的 role 只作为**查询条件**（系统级），不作为授予凭据。"""
        import inspect

        project_params = inspect.signature(
            _FakeGrantRepository.get_project_roles
        ).parameters
        assert "role" not in project_params


# ============================================================
# 5. fail closed（存储故障 / 数据损坏）
# ============================================================

class TestFailClosed:
    @pytest.mark.parametrize("failure", ("unavailable", "corrupt"))
    def test_store_failure_raises_unavailable(self, failure: str) -> None:
        """存储不可用 / 数据损坏 ⇒ ``AuthorizationStoreUnavailableError``。"""
        service = _service(failure=failure)

        with pytest.raises(AuthorizationStoreUnavailableError):
            service.is_project_member(_ALICE, _PROJECT_A)

        with pytest.raises(AuthorizationStoreUnavailableError):
            service.is_project_ops(_ALICE, _PROJECT_A)

        with pytest.raises(AuthorizationStoreUnavailableError):
            service.is_system_ops(_ALICE)

        with pytest.raises(AuthorizationStoreUnavailableError):
            service.project_role(_ALICE, _PROJECT_A)

    def test_store_failure_is_not_silently_treated_as_no_grant(self) -> None:
        """故障**不得**降级为"无授权后继续"——必须抛出，由调用方 fail closed。"""
        service = _service(failure="unavailable")

        with pytest.raises(AuthorizationStoreUnavailableError):
            service.is_system_ops(_ALICE)

    def test_not_configured_is_not_a_failure(self) -> None:
        """存储**未配置**（无 DATABASE_URL）⇒ 无授权，**不**抛 503 级异常。

        这是无 DB 部署（及离线测试）的既有形态：调用方仍有显式配置来源
        （如旧白名单）可用；缺少存储不等于故障。
        """
        service = _service(failure="not_configured")

        assert service.is_project_member(_ALICE, _PROJECT_A) is False
        assert service.is_project_ops(_ALICE, _PROJECT_A) is False
        assert service.is_system_ops(_ALICE) is False
        assert service.project_role(_ALICE, _PROJECT_A) is None

    def test_unavailable_error_is_service_error(self) -> None:
        assert issubclass(
            AuthorizationStoreUnavailableError, AuthorizationServiceError
        )


# ============================================================
# 6. 参数校验（非法输入不进入查询）
# ============================================================

class TestValidation:
    def test_user_id_validation(self) -> None:
        assert validate_user_id("  u1  ") == "u1"
        for bad in ("", "   ", None, 123):
            with pytest.raises(ValueError):
                validate_user_id(bad)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            validate_user_id("x" * 65)

    def test_project_id_validation(self) -> None:
        assert validate_project_id(" p1 ") == "p1"
        for bad in ("", "   ", None):
            with pytest.raises(ValueError):
                validate_project_id(bad)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            validate_project_id("x" * 129)

    def test_role_validation_is_scope_aware(self) -> None:
        assert validate_project_role(PROJECT_ROLE_MEMBER) == PROJECT_ROLE_MEMBER
        assert validate_project_role(
            PROJECT_ROLE_PROJECT_OPS
        ) == PROJECT_ROLE_PROJECT_OPS
        assert validate_system_role(SYSTEM_ROLE_SYSTEM_OPS) == (
            SYSTEM_ROLE_SYSTEM_OPS
        )

        # scope ↔ role 不匹配必须被拒绝
        with pytest.raises(ValueError):
            validate_project_role(SYSTEM_ROLE_SYSTEM_OPS)
        with pytest.raises(ValueError):
            validate_system_role(PROJECT_ROLE_MEMBER)
        with pytest.raises(ValueError):
            validate_system_role(PROJECT_ROLE_PROJECT_OPS)
        with pytest.raises(ValueError):
            validate_project_role("ADMIN")

    def test_service_rejects_illegal_arguments(self) -> None:
        """非法参数（服务端 bug）**不**被静默吞掉。

        参数校验发生在**真实仓储**边界（``validate_*``）；服务经
        ``AuthorizationService(repository=真实仓储)`` 注入以验证异常透传。
        校验先于 Session 工厂获取 ⇒ 本用例不需要数据库。
        """
        from backend.app.db.authorization_grant_repository import (
            AuthorizationGrantRepository,
        )

        service = AuthorizationService(repository=AuthorizationGrantRepository())

        with pytest.raises(ValueError):
            service.is_project_member(_ALICE, "   ")
        with pytest.raises(ValueError):
            service.is_project_member("", _PROJECT_A)
        with pytest.raises(ValueError):
            service.is_system_ops("")

    def test_repository_validates_before_touching_session_factory(self) -> None:
        """参数校验必须先于任何存储访问（无 DB 时也不应触达连接）。"""
        from backend.app.db.authorization_grant_repository import (
            AuthorizationGrantRepository,
        )

        repository = AuthorizationGrantRepository()

        with pytest.raises(ValueError):
            repository.get_project_roles(user_id="", project_id=_PROJECT_A)
        with pytest.raises(ValueError):
            repository.get_project_roles(user_id=_ALICE, project_id="  ")
        with pytest.raises(ValueError):
            repository.has_system_role(user_id=_ALICE, role="ADMIN")
        with pytest.raises(ValueError):
            repository.grant_project_role(
                user_id=_ALICE,
                project_id=_PROJECT_A,
                role=SYSTEM_ROLE_SYSTEM_OPS,
            )
        with pytest.raises(ValueError):
            repository.grant_system_role(
                user_id=_ALICE, role=PROJECT_ROLE_MEMBER
            )


# ============================================================
# 7. 模型层约束定义（结构性断言；DB 级行为见 *_db 测试）
# ============================================================

class TestModelContract:
    def test_scope_and_role_vocabulary_is_minimal(self) -> None:
        assert PROJECT_ROLES == {
            PROJECT_ROLE_MEMBER, PROJECT_ROLE_PROJECT_OPS
        }
        assert SYSTEM_ROLES == {SYSTEM_ROLE_SYSTEM_OPS}
        assert ALL_ROLES == PROJECT_ROLES | SYSTEM_ROLES
        assert len(ALL_ROLES) == 3
        assert SCOPE_PROJECT == "PROJECT"
        assert SCOPE_SYSTEM == "SYSTEM"

    def test_table_lives_in_ai_ops_schema(self) -> None:
        """授权表位于 ``ai_ops``（**不**进入业务 schema public）。"""
        assert AuthorizationGrant.__table__.schema == "ai_ops"
        assert AUTHORIZATION_GRANT_TABLE == "authorization_grant"

    def test_scope_role_check_constraint_exists(self) -> None:
        names = {
            c.name for c in AuthorizationGrant.__table__.constraints
            if c.name
        }
        assert AUTHORIZATION_SCOPE_ROLE_CHECK in names

    def test_check_predicate_rejects_null_project_for_project_scope(self) -> None:
        """复合 CHECK 谓词必须**显式**要求 PROJECT 行 project_id 非空、SYSTEM 行为 NULL。"""
        predicate = AUTHORIZATION_SCOPE_ROLE_PREDICATE

        assert "(scope_type = 'PROJECT'" in predicate
        assert "project_id IS NOT NULL" in predicate
        assert "length(btrim(project_id)) > 0" in predicate
        assert "role IN ('MEMBER', 'PROJECT_OPS')" in predicate
        assert "(scope_type = 'SYSTEM'" in predicate
        assert "project_id IS NULL" in predicate
        assert "role = 'SYSTEM_OPS'" in predicate

    def test_partial_unique_indexes_exist(self) -> None:
        """两个 partial unique index（PG 视 NULL 互不相等 ⇒ SYSTEM 必须 partial）。"""
        indexes = {
            idx.name: idx for idx in AuthorizationGrant.__table__.indexes
        }

        project_index = indexes[AUTHORIZATION_PROJECT_UNIQUE_INDEX]
        system_index = indexes[AUTHORIZATION_SYSTEM_UNIQUE_INDEX]

        assert project_index.unique is True
        assert system_index.unique is True
        assert {c.name for c in project_index.columns} == {
            "user_id", "project_id", "role"
        }
        assert {c.name for c in system_index.columns} == {"user_id", "role"}
        # 两个索引都必须是 partial（否则无法同时容纳项目级与系统级行）
        assert project_index.dialect_options["postgresql"]["where"] is not None
        assert system_index.dialect_options["postgresql"]["where"] is not None
