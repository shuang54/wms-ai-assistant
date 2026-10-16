"""M2.4 授权关系 **专用 PostgreSQL** 集成测试（真实表 / 真实 Repository）。

运行前置（**强制**）：

    * ``DATABASE_URL`` 必须显式指向 ``localhost:5433/wms_ai_test``
      （专用测试库，``docker-compose.test.yml``）；
    * ``RUN_DB_TESTS=1``。

**隔离硬断言**（任务书 §七：不得只依赖 ``RUN_DB_TESTS`` 判断安全）：

    每个用例的 ``db_repository`` 夹具在**任何 SQL 之前**断言
    ``host == localhost`` / ``port == 5433`` / ``database == wms_ai_test``；
    不满足 ⇒ ``pytest.fail``（**绝不**降级到其它数据库，尤其不碰应用库
    ``wms_ai``）。

覆盖任务书 §六「数据库集成测试」条目：

    * 授权记录写入、读取和唯一性（含幂等与重复授权约束）
    * 不同用户、不同项目的授权隔离
    * 系统级授权与项目级授权相互独立（含数据库 CHECK 拒绝非法组合）
    * 数据库初始化可重复执行
    * 授权查询不修改既有观测表
    * 测试结束无新增观测表残留

残留治理：

    * 本文件只写 ``ai_ops.authorization_grant``（**不**写 4 张观测表）；
      ``tests/conftest.py`` 的会话级残留守卫仍会复核观测表零残留；
    * 夹具按 user_id 前缀（``m24-``）精确清理自己写入的授权行。
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app.db.authorization_grant_repository import (
    AuthorizationGrantRepository,
    AuthorizationGrantRepositoryError,
    validate_project_id,
    validate_user_id,
)
from backend.app.db.models.authorization_grant import (
    AUTHORIZATION_GRANT_TABLE,
    PROJECT_ROLE_MEMBER,
    PROJECT_ROLE_PROJECT_OPS,
    SCOPE_PROJECT,
    SCOPE_SYSTEM,
    SYSTEM_ROLE_SYSTEM_OPS,
)
from backend.app.db.session import get_engine, get_session_factory
from backend.app.services.authorization_service import (
    AuthorizationService,
    AuthorizationStoreUnavailableError,
)

#: 本文件写入的授权行统一前缀（精确清理，不误删他人数据）。
_USER_PREFIX = "m24-"

#: 授权表**全限定**名。
#:
#: 原生 SQL **必须**带 ``ai_ops.`` 前缀：``ai_ops`` 不在默认 ``search_path`` 中，
#: 裸表名会报 ``relation "authorization_grant" does not exist``。
#: （ORM 侧由 ``AuthorizationGrant.__table__.schema`` 处理，无需限定。）
_GRANT_TABLE = "ai_ops.authorization_grant"
_PROJECT_A = "m24-project-a"
_PROJECT_B = "m24-project-b"

#: 受本文件保护的观测表（**只读**校验；本文件绝不写入）。
_OBSERVABILITY_TABLES = (
    "ai_ops.llm_usage_record",
    "ai_ops.tool_execution_record",
    "ai_ops.rag_execution_record",
    "ai_ops.assistant_outcome_record",
)

requires_db = pytest.mark.skipif(
    os.environ.get("RUN_DB_TESTS", "").strip().lower()
    not in {"1", "true", "yes", "on"},
    reason="DB 集成测试：需设置 RUN_DB_TESTS=1（并使用专用测试库）",
)


def _assert_test_database() -> None:
    """**硬断言**当前引擎指向专用测试库（任何 SQL 之前）。

    这是 M2.4 任务书 §七 的强制要求：不得只依赖 ``RUN_DB_TESTS`` 就认为安全。
    失败时直接 fail（**不** skip）——跳过会掩盖"跑错库"这一严重配置错误。
    """
    engine = get_engine()
    if engine is None:
        pytest.fail(
            "DATABASE_URL 未配置：授权 DB 集成测试必须显式指向 "
            "localhost:5433/wms_ai_test"
        )

    host = engine.url.host
    port = engine.url.port
    database = engine.url.database

    if not (
        host in {"localhost", "127.0.0.1"}
        and port == 5433
        and database == "wms_ai_test"
    ):
        pytest.fail(
            "拒绝执行：数据库隔离断言失败"
            f"（host={host} port={port} database={database}）——"
            "M2.4 DB 集成测试只允许 localhost:5433/wms_ai_test"
        )


def _user(tag: str) -> str:
    return f"{_USER_PREFIX}{tag}-{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def db_repository() -> AuthorizationGrantRepository:
    """真实 Repository；夹具前后都断言库身份，并精确清理本文件写入的行。"""
    _assert_test_database()
    repository = AuthorizationGrantRepository()
    _cleanup()
    try:
        yield repository
    finally:
        _cleanup()
        _assert_test_database()


def _cleanup() -> None:
    """只删除本文件前缀的授权行（不触碰其它数据、不触碰观测表）。"""
    factory = get_session_factory()
    assert factory is not None, "授权 DB 集成测试要求 DATABASE_URL 已配置"
    with factory() as session, session.begin():
        session.execute(
            text(
                f"DELETE FROM {_GRANT_TABLE} "
                "WHERE user_id LIKE :prefix"
            ),
            {"prefix": f"{_USER_PREFIX}%"},
        )


def _observability_counts() -> dict[str, int]:
    engine = get_engine()
    assert engine is not None
    with engine.connect() as conn:
        return {
            table: conn.execute(
                text(f"SELECT COUNT(*) FROM {table}")
            ).scalar_one()
            for table in _OBSERVABILITY_TABLES
        }


def _raw_count(*, user_id: str) -> int:
    engine = get_engine()
    assert engine is not None
    with engine.connect() as conn:
        return conn.execute(
            text(
                f"SELECT COUNT(*) FROM {_GRANT_TABLE} "
                "WHERE user_id = :uid"
            ),
            {"uid": user_id},
        ).scalar_one()


# ============================================================
# 1. 写入 / 读取 / 幂等 / 唯一性
# ============================================================

@requires_db
class TestGrantPersistence:
    def test_project_grant_round_trip(self, db_repository) -> None:
        user_id = _user("member")

        created = db_repository.grant_project_role(
            user_id=user_id, project_id=_PROJECT_A, role=PROJECT_ROLE_MEMBER
        )

        assert created is True
        assert db_repository.get_project_roles(
            user_id=user_id, project_id=_PROJECT_A
        ) == frozenset({PROJECT_ROLE_MEMBER})

    def test_system_grant_round_trip(self, db_repository) -> None:
        user_id = _user("sysops")

        created = db_repository.grant_system_role(
            user_id=user_id, role=SYSTEM_ROLE_SYSTEM_OPS
        )

        assert created is True
        assert db_repository.has_system_role(
            user_id=user_id, role=SYSTEM_ROLE_SYSTEM_OPS
        ) is True

    def test_project_grant_is_idempotent(self, db_repository) -> None:
        """重复授予同一组合 ⇒ 不报错、**不**新增第二行。"""
        user_id = _user("idem")

        first = db_repository.grant_project_role(
            user_id=user_id, project_id=_PROJECT_A, role=PROJECT_ROLE_MEMBER
        )
        second = db_repository.grant_project_role(
            user_id=user_id, project_id=_PROJECT_A, role=PROJECT_ROLE_MEMBER
        )

        assert first is True
        assert second is False
        assert _raw_count(user_id=user_id) == 1

    def test_system_grant_is_idempotent_despite_null_project(
        self, db_repository
    ) -> None:
        """SYSTEM 行 ``project_id`` 全为 NULL ⇒ 必须靠 **partial** unique 防重。"""
        user_id = _user("sysidem")

        first = db_repository.grant_system_role(
            user_id=user_id, role=SYSTEM_ROLE_SYSTEM_OPS
        )
        second = db_repository.grant_system_role(
            user_id=user_id, role=SYSTEM_ROLE_SYSTEM_OPS
        )

        assert first is True
        assert second is False
        assert _raw_count(user_id=user_id) == 1

    def test_multiple_roles_in_one_project_are_allowed(self, db_repository) -> None:
        """同一用户在同一项目可同时持 ``MEMBER`` 与 ``PROJECT_OPS``。"""
        user_id = _user("multi")

        assert db_repository.grant_project_role(
            user_id=user_id, project_id=_PROJECT_A, role=PROJECT_ROLE_MEMBER
        ) is True
        assert db_repository.grant_project_role(
            user_id=user_id, project_id=_PROJECT_A, role=PROJECT_ROLE_PROJECT_OPS
        ) is True

        assert db_repository.get_project_roles(
            user_id=user_id, project_id=_PROJECT_A
        ) == frozenset({PROJECT_ROLE_MEMBER, PROJECT_ROLE_PROJECT_OPS})

    def test_unknown_user_has_no_grants(self, db_repository) -> None:
        assert db_repository.get_project_roles(
            user_id=_user("ghost"), project_id=_PROJECT_A
        ) == frozenset()
        assert db_repository.has_system_role(
            user_id=_user("ghost"), role=SYSTEM_ROLE_SYSTEM_OPS
        ) is False

    def test_grant_is_committed_and_visible_in_new_session(self, db_repository) -> None:
        """写入已提交（不是未提交事务内的可见性假象）。"""
        user_id = _user("commit")
        db_repository.grant_project_role(
            user_id=user_id, project_id=_PROJECT_A, role=PROJECT_ROLE_MEMBER
        )

        fresh = AuthorizationGrantRepository()   # 全新仓储 / 全新 Session
        assert fresh.get_project_roles(
            user_id=user_id, project_id=_PROJECT_A
        ) == frozenset({PROJECT_ROLE_MEMBER})
        assert _raw_count(user_id=user_id) == 1


# ============================================================
# 2. 数据库级约束（绕过 Repository 直接插入，证明约束真的在）
# ============================================================

@requires_db
class TestDatabaseLevelConstraints:
    def _insert_raw(
        self, *, user_id: str, scope_type: str, project_id: str | None, role: str
    ) -> None:
        factory = get_session_factory()
        assert factory is not None
        with factory() as session, session.begin():
            session.execute(
                text(
                    f"INSERT INTO {_GRANT_TABLE} "
                    "(user_id, scope_type, project_id, role) "
                    "VALUES (:uid, :scope, :pid, :role)"
                ),
                {
                    "uid": user_id,
                    "scope": scope_type,
                    "pid": project_id,
                    "role": role,
                },
            )

    def test_project_scope_with_null_project_is_rejected(self, db_repository) -> None:
        """**核心安全约束**：PROJECT + NULL project_id 必须被数据库拒绝。"""
        with pytest.raises(IntegrityError):
            self._insert_raw(
                user_id=_user("nullproj"),
                scope_type=SCOPE_PROJECT,
                project_id=None,
                role=PROJECT_ROLE_MEMBER,
            )

    def test_project_scope_with_blank_project_is_rejected(self, db_repository) -> None:
        """PROJECT + 空白 project_id（不 trim）也必须被拒绝。"""
        with pytest.raises(IntegrityError):
            self._insert_raw(
                user_id=_user("blankproj"),
                scope_type=SCOPE_PROJECT,
                project_id="   ",
                role=PROJECT_ROLE_MEMBER,
            )

    def test_system_scope_with_project_is_rejected(self, db_repository) -> None:
        with pytest.raises(IntegrityError):
            self._insert_raw(
                user_id=_user("sysproj"),
                scope_type=SCOPE_SYSTEM,
                project_id=_PROJECT_A,
                role=SYSTEM_ROLE_SYSTEM_OPS,
            )

    def test_system_scope_with_project_role_is_rejected(self, db_repository) -> None:
        with pytest.raises(IntegrityError):
            self._insert_raw(
                user_id=_user("sysmember"),
                scope_type=SCOPE_SYSTEM,
                project_id=None,
                role=PROJECT_ROLE_MEMBER,
            )

    def test_project_scope_with_system_role_is_rejected(self, db_repository) -> None:
        with pytest.raises(IntegrityError):
            self._insert_raw(
                user_id=_user("projsys"),
                scope_type=SCOPE_PROJECT,
                project_id=_PROJECT_A,
                role=SYSTEM_ROLE_SYSTEM_OPS,
            )

    def test_unknown_scope_type_is_rejected(self, db_repository) -> None:
        with pytest.raises(IntegrityError):
            self._insert_raw(
                user_id=_user("badscope"),
                scope_type="GLOBAL",
                project_id=None,
                role=SYSTEM_ROLE_SYSTEM_OPS,
            )

    def test_duplicate_project_grant_is_rejected_at_db_level(
        self, db_repository
    ) -> None:
        user_id = _user("dup")
        db_repository.grant_project_role(
            user_id=user_id, project_id=_PROJECT_A, role=PROJECT_ROLE_MEMBER
        )

        with pytest.raises(IntegrityError):
            self._insert_raw(
                user_id=user_id,
                scope_type=SCOPE_PROJECT,
                project_id=_PROJECT_A,
                role=PROJECT_ROLE_MEMBER,
            )

    def test_duplicate_system_grant_is_rejected_at_db_level(
        self, db_repository
    ) -> None:
        user_id = _user("dupsys")
        db_repository.grant_system_role(
            user_id=user_id, role=SYSTEM_ROLE_SYSTEM_OPS
        )

        with pytest.raises(IntegrityError):
            self._insert_raw(
                user_id=user_id,
                scope_type=SCOPE_SYSTEM,
                project_id=None,
                role=SYSTEM_ROLE_SYSTEM_OPS,
            )


# ============================================================
# 3. 隔离：不同用户 / 不同项目 / 系统级 vs 项目级
# ============================================================

@requires_db
class TestGrantIsolation:
    def test_users_are_isolated(self, db_repository) -> None:
        alice, bob = _user("iso-a"), _user("iso-b")
        db_repository.grant_project_role(
            user_id=alice, project_id=_PROJECT_A, role=PROJECT_ROLE_PROJECT_OPS
        )
        db_repository.grant_project_role(
            user_id=bob, project_id=_PROJECT_A, role=PROJECT_ROLE_MEMBER
        )

        service = AuthorizationService(repository=db_repository)

        assert service.is_project_ops(alice, _PROJECT_A) is True
        assert service.is_project_ops(bob, _PROJECT_A) is False
        assert service.is_project_member(bob, _PROJECT_A) is True

    def test_projects_are_isolated(self, db_repository) -> None:
        user_id = _user("iso-p")
        db_repository.grant_project_role(
            user_id=user_id, project_id=_PROJECT_A, role=PROJECT_ROLE_PROJECT_OPS
        )

        service = AuthorizationService(repository=db_repository)

        assert service.is_project_ops(user_id, _PROJECT_A) is True
        assert service.is_project_member(user_id, _PROJECT_B) is False
        assert service.project_role(user_id, _PROJECT_B) is None

    def test_system_grant_is_independent_from_project_grants(
        self, db_repository
    ) -> None:
        """**两个方向都不得推导**（真实 DB 上的端到端验证）。"""
        project_user, system_user = _user("sep-p"), _user("sep-s")
        db_repository.grant_project_role(
            user_id=project_user,
            project_id=_PROJECT_A,
            role=PROJECT_ROLE_PROJECT_OPS,
        )
        db_repository.grant_system_role(
            user_id=system_user, role=SYSTEM_ROLE_SYSTEM_OPS
        )

        service = AuthorizationService(repository=db_repository)

        # 项目运维 ≠ 系统运维
        assert service.is_project_ops(project_user, _PROJECT_A) is True
        assert service.is_system_ops(project_user) is False

        # 系统运维 ≠ 任意项目成员
        assert service.is_system_ops(system_user) is True
        assert service.is_project_member(system_user, _PROJECT_A) is False
        assert service.is_project_member(system_user, _PROJECT_B) is False

    def test_project_grant_is_not_visible_to_system_lookup(
        self, db_repository
    ) -> None:
        """``PROJECT`` 作用域的行**不参与**系统级判定（反之亦然）。"""
        user_id = _user("scope-iso")
        db_repository.grant_project_role(
            user_id=user_id, project_id=_PROJECT_A, role=PROJECT_ROLE_MEMBER
        )

        assert db_repository.has_system_role(
            user_id=user_id, role=SYSTEM_ROLE_SYSTEM_OPS
        ) is False

        db_repository.grant_system_role(
            user_id=user_id, role=SYSTEM_ROLE_SYSTEM_OPS
        )

        # 系统级行（project_id IS NULL）不出现在项目级查询结果里
        assert db_repository.get_project_roles(
            user_id=user_id, project_id=_PROJECT_A
        ) == frozenset({PROJECT_ROLE_MEMBER})


# ============================================================
# 4. 初始化幂等 / 观测表零影响
# ============================================================

@requires_db
class TestInitializationAndResidue:
    def test_init_db_is_repeatable(self, db_repository) -> None:
        """``init_db()`` 可重复执行（不改既有结构，不报错）。"""
        from backend.app.db.init_db import init_db

        _assert_test_database()
        init_db()
        init_db()

    def test_table_exists_in_ai_ops_schema(self, db_repository) -> None:
        engine = get_engine()
        assert engine is not None
        with engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = 'ai_ops' AND table_name = :name"
                ),
                {"name": AUTHORIZATION_GRANT_TABLE},
            ).scalar_one()

        assert count == 1

    def test_grant_operations_do_not_touch_observability_tables(
        self, db_repository
    ) -> None:
        """授权读写**不**影响 4 张观测表（本文件自身的前后对比）。"""
        before = _observability_counts()
        user_id = _user("observ")

        db_repository.grant_project_role(
            user_id=user_id, project_id=_PROJECT_A, role=PROJECT_ROLE_MEMBER
        )
        db_repository.grant_system_role(
            user_id=user_id, role=SYSTEM_ROLE_SYSTEM_OPS
        )
        db_repository.get_project_roles(user_id=user_id, project_id=_PROJECT_A)
        db_repository.has_system_role(
            user_id=user_id, role=SYSTEM_ROLE_SYSTEM_OPS
        )

        assert _observability_counts() == before

    def test_cleanup_leaves_no_rows_for_prefix(self, db_repository) -> None:
        """夹具清理后，本前缀**无**残留（按 user_id 精确清理）。"""
        engine = get_engine()
        assert engine is not None
        with engine.connect() as conn:
            remaining = conn.execute(
                text(
                    f"SELECT COUNT(*) FROM {_GRANT_TABLE} "
                    "WHERE user_id LIKE :prefix"
                ),
                {"prefix": f"{_USER_PREFIX}isolation-probe%"},
            ).scalar_one()

        # 到这里夹具已清理过一次（前置 _cleanup），不应有本探针前缀的行
        assert remaining == 0


# ============================================================
# 5. 真实存储故障 ⇒ fail closed（非法/不可用引擎）
# ============================================================

@requires_db
class TestRealStoreFailure:
    def test_unreachable_store_raises_unavailable(self) -> None:
        """把 Repository 指向不可达端口 ⇒ ``AuthorizationStoreUnavailableError``。"""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        _assert_test_database()
        # 保留凭据来源：复用当前 URL 的用户信息，仅改端口（不打印任何凭证）
        engine = get_engine()
        assert engine is not None
        # connect_timeout：避免依赖 OS 级 TCP 超时（否则本用例会占用数分钟）
        bad_url = engine.url.set(port=5999, query={"connect_timeout": "2"})
        bad_engine = create_engine(bad_url, pool_pre_ping=False)
        bad_factory = sessionmaker(bind=bad_engine)

        repository = AuthorizationGrantRepository(session_factory=bad_factory)
        service = AuthorizationService(repository=repository)

        try:
            with pytest.raises(AuthorizationStoreUnavailableError):
                service.is_system_ops(_user("down"))

            with pytest.raises(AuthorizationStoreUnavailableError):
                service.is_project_member(_user("down"), _PROJECT_A)
        finally:
            bad_engine.dispose()

    def test_corrupt_row_is_refused_on_read(self, db_repository) -> None:
        """读侧防御：绕过 CHECK 的非法行（禁用约束后写入）⇒ 拒绝读取。

        植入的行：``scope_type='PROJECT' + project_id=<项目> + role='SYSTEM_OPS'``
        —— 这是**最危险的损坏形态**：一个项目作用域的行携带系统级角色，
        若读侧不做一致性防御，就会被 ``get_project_roles`` 当作合法角色返回，
        进而可能在应用层被误判为更高的权限。仓储必须**拒绝读取**（不猜测、
        不静默丢弃、不降级为"无授权"）。

        Note:
            不选择 ``project_id IS NULL`` 的损坏行：带 ``project_id`` 过滤的
            项目级查询不会命中它（NULL 永不等于查询值），因此它不可见于任何
            读路径，无法用于验证读侧防御。
        """
        user_id = _user("corrupt")
        factory = get_session_factory()
        assert factory is not None

        # 临时禁用 CHECK 以植入非法行（模拟"约束被删 / 被绕过"）
        with factory() as session, session.begin():
            session.execute(
                text(
                    f"ALTER TABLE {_GRANT_TABLE} "
                    "DROP CONSTRAINT ck_authorization_grant_scope_role"
                )
            )
        try:
            with factory() as session, session.begin():
                session.execute(
                    text(
                        f"INSERT INTO {_GRANT_TABLE} "
                        "(user_id, scope_type, project_id, role) "
                        "VALUES (:uid, 'PROJECT', :pid, 'SYSTEM_OPS')"
                    ),
                    {"uid": user_id, "pid": _PROJECT_A},
                )

            with pytest.raises(AuthorizationGrantRepositoryError):
                db_repository.get_project_roles(
                    user_id=user_id, project_id=_PROJECT_A
                )

            # 同一损坏行也不得被系统级查询"顺带"接受
            assert db_repository.has_system_role(
                user_id=user_id, role=SYSTEM_ROLE_SYSTEM_OPS
            ) is False
        finally:
            # 清理非法行并恢复约束（保持 schema 与 init_db 一致）
            with factory() as session, session.begin():
                session.execute(
                    text(
                        f"DELETE FROM {_GRANT_TABLE} "
                        "WHERE user_id = :uid"
                    ),
                    {"uid": user_id},
                )
                session.execute(
                    text(
                        f"ALTER TABLE {_GRANT_TABLE} "
                        "ADD CONSTRAINT ck_authorization_grant_scope_role "
                        "CHECK ("
                        "(scope_type = 'PROJECT'"
                        " AND project_id IS NOT NULL"
                        " AND length(btrim(project_id)) > 0"
                        " AND role IN ('MEMBER', 'PROJECT_OPS'))"
                        " OR "
                        "(scope_type = 'SYSTEM'"
                        " AND project_id IS NULL"
                        " AND role = 'SYSTEM_OPS')"
                        ")"
                    )
                )


# ============================================================
# 6. 授权校验（真实仓储边界；不需要 DB 也可跑）
# ============================================================

class TestRepositoryValidationWithoutDb:
    def test_validation_happens_before_store_access(self) -> None:
        repository = AuthorizationGrantRepository()

        with pytest.raises(ValueError):
            repository.get_project_roles(user_id="", project_id=_PROJECT_A)
        with pytest.raises(ValueError):
            repository.has_system_role(user_id=_user("v"), role="ADMIN")
        with pytest.raises(ValueError):
            validate_user_id("x" * 65)
        with pytest.raises(ValueError):
            validate_project_id("x" * 129)
