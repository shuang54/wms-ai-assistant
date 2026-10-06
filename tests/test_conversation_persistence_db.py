"""Conversation Persistence Tests（Phase 4.1 Step 5）—— 离线契约 + DB-gated 行为。

本文件分两部分：

1. **离线契约**（始终运行；不连接 PostgreSQL）：
   ``TestOrmSchemaContract`` / ``TestSqlCompilationContract`` —— 用 ORM metadata
   与 SQL 编译（不执行）锁定 schema / 列 / FK / 索引 / 排序 / 显式列。

2. **DB-gated 行为**（仅 ``RUN_DB_TESTS=1`` 时运行）：
   Conversation CRUD · Turn CRUD · 校验规则 · 归档 · 原子性 · 删除语义 ·
   Security（字段边界）。

隔离策略（与 ``test_assistant_outcome_persistence.py`` 一致）：

    * 建表：module fixture 调用 ``init_db()``（幂等；新表随 ``create_all`` 创建，
      **不是** migration）；离线时跳过（不连接数据库）；
    * 残留：每个测试创建的 conversation 在结束时按 conversation_id 精确删除
      （FK CASCADE 连带删除 turns）；不使用 TRUNCATE；
    * 不删除观测表数据（llm_usage / tool_execution / rag_execution / outcome）。
"""
from __future__ import annotations

import inspect
import os
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import BigInteger, String, Text, text
from sqlalchemy.dialects import postgresql

from backend.app.db.conversation_repository import (
    CONVERSATION_READ_COLUMNS,
    CONVERSATION_STATUS_ACTIVE,
    CONVERSATION_STATUS_ARCHIVED,
    CONVERSATION_TURN_READ_COLUMNS,
    TURN_ROLE_ASSISTANT,
    TURN_ROLE_USER,
    ConversationRepository,
    ConversationRepositoryError,
)
from backend.app.db.models.conversation import Conversation
from backend.app.db.models.conversation_turn import (
    CONVERSATION_TURN_IDEMPOTENCY_INDEX,
    CONVERSATION_TURN_INDEX,
    ConversationTurn,
)
from backend.app.db.session import get_session_factory
from backend.app.services.conversation_service import (
    ConversationArchivedError,
    ConversationNotFoundError,
    ConversationService,
    new_conversation_id,
)

_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {"1", "true", "yes", "on"}

#: 需要真实 PostgreSQL 的测试类标记（离线 → SKIP）。
_DB_GATED = pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable PostgreSQL integration tests",
)

_CONVERSATION_TABLE = "ai_ops.conversation"
_TURN_TABLE = "ai_ops.conversation_turn"
_OUTCOME_TABLE = "ai_ops.assistant_outcome_record"


@pytest.fixture(scope="module", autouse=True)
def _ensure_tables() -> None:
    """幂等建表（create_all；本 Step 无 migration）。离线时**不**连接数据库。"""
    if not _ENV_FLAG:
        return
    from backend.app.db import init_db as init_db_module

    init_db_module.init_db()


@pytest.fixture()
def created() -> Iterator[list[str]]:
    """收集本测试创建的 conversation_id，结束时精确删除（残留 = 0）。"""
    ids: list[str] = []
    yield ids
    _delete_conversations(ids)


def _delete_conversations(conversation_ids: list[str]) -> None:
    if not conversation_ids:
        return
    factory = get_session_factory()
    assert factory is not None
    with factory() as session, session.begin():
        for conversation_id in conversation_ids:
            session.execute(
                text(f"DELETE FROM {_CONVERSATION_TABLE} WHERE conversation_id = :cid"),
                {"cid": conversation_id},
            )


def _count_turns(conversation_id: str) -> int:
    factory = get_session_factory()
    assert factory is not None
    with factory() as session:
        value = session.execute(
            text(f"SELECT COUNT(*) FROM {_TURN_TABLE} WHERE conversation_id = :cid"),
            {"cid": conversation_id},
        ).scalar_one()
    return int(value)


def _count_outcomes(assistant_request_id: str) -> int:
    factory = get_session_factory()
    assert factory is not None
    with factory() as session:
        value = session.execute(
            text(
                f"SELECT COUNT(*) FROM {_OUTCOME_TABLE} "
                "WHERE assistant_request_id = :rid"
            ),
            {"rid": assistant_request_id},
        ).scalar_one()
    return int(value)


def _new_conversation(service: ConversationService, created: list[str], **kwargs: Any):
    project_id = kwargs.pop("project_id", "vietnam-wms")
    view = service.create_conversation(project_id=project_id, **kwargs)
    created.append(view.conversation_id)
    return view


def _compile(statement: Any) -> str:
    """编译为 PostgreSQL SQL（**不执行**）。"""
    return str(statement.compile(dialect=postgresql.dialect()))


# ============================================================
# 1：ORM Schema Contract（离线；不连接数据库）
# ============================================================


class TestOrmSchemaContract:
    def test_conversation_table_contract(self) -> None:
        table = Conversation.__table__

        assert table.schema == "ai_ops"  # 非 public
        assert table.name == "conversation"
        assert set(table.columns.keys()) == {
            "conversation_id",
            "project_id",
            "created_at",
            "updated_at",
            "status",
        }
        assert table.c.conversation_id.primary_key is True
        assert isinstance(table.c.conversation_id.type, String)
        assert table.c.conversation_id.type.length == 128
        assert table.c.conversation_id.nullable is False
        assert table.c.project_id.nullable is False
        assert isinstance(table.c.project_id.type, String)
        assert table.c.project_id.type.length == 128
        assert isinstance(table.c.status.type, String)
        assert table.c.status.type.length == 32
        assert table.c.status.nullable is False
        assert table.c.created_at.nullable is False
        assert table.c.updated_at.nullable is False
        # 除主键外无其它索引（Step 4 §8 最小集合）
        assert len(table.indexes) == 0

    def test_conversation_turn_table_contract(self) -> None:
        table = ConversationTurn.__table__

        assert table.schema == "ai_ops"
        assert table.name == "conversation_turn"
        # Phase 4.2 Step 6：新增 idempotency_key（7 列）
        assert set(table.columns.keys()) == {
            "turn_id",
            "conversation_id",
            "role",
            "content",
            "assistant_request_id",
            "created_at",
            "idempotency_key",
        }
        assert table.c.turn_id.primary_key is True
        assert isinstance(table.c.turn_id.type, BigInteger)
        assert isinstance(table.c.content.type, Text)
        assert table.c.content.nullable is False
        assert isinstance(table.c.role.type, String)
        assert table.c.role.type.length == 32
        assert table.c.assistant_request_id.nullable is True  # USER 为 NULL
        assert isinstance(table.c.assistant_request_id.type, String)
        assert table.c.assistant_request_id.type.length == 128
        # Phase 4.2 Step 6：幂等键列（NULL 兼容历史行；只写 USER Turn）
        assert table.c.idempotency_key.nullable is True
        assert isinstance(table.c.idempotency_key.type, String)
        assert table.c.idempotency_key.type.length == 128

    def test_idempotency_key_unique_index_contract(self) -> None:
        """Phase 4.2 Step 6：UNIQUE(conversation_id, idempotency_key)。

        * **非** UNIQUE(idempotency_key)：scope = conversation（跨会话隔离）；
        * 标准 UNIQUE（无 NULLS NOT DISTINCT）⇒ 历史 NULL 行不受约束。
        """
        table = ConversationTurn.__table__
        index = next(
            (
                candidate
                for candidate in table.indexes
                if candidate.name == CONVERSATION_TURN_IDEMPOTENCY_INDEX
            ),
            None,
        )
        assert index is not None, CONVERSATION_TURN_IDEMPOTENCY_INDEX
        assert index.unique is True
        assert [column.name for column in index.columns] == [
            "conversation_id",
            "idempotency_key",
        ]

    def test_turn_foreign_key_is_conversation_cascade(self) -> None:
        table = ConversationTurn.__table__

        assert len(table.foreign_keys) == 1
        fk = next(iter(table.foreign_keys))
        assert fk.target_fullname == "ai_ops.conversation.conversation_id"
        assert fk.ondelete == "CASCADE"

    def test_assistant_request_id_is_not_foreign_key(self) -> None:
        """correlation only：绝不对观测表建立数据库外键。"""
        table = ConversationTurn.__table__
        assert list(table.c.assistant_request_id.foreign_keys) == []
        assert len(table.foreign_keys) == 1  # 仅 conversation

    def test_only_composite_index_exists(self) -> None:
        table = ConversationTurn.__table__

        # Phase 4.2 Step 6：1（列表排序） + 1（幂等唯一）= 2
        assert len(table.indexes) == 2
        names = {index.name for index in table.indexes}
        assert names == {
            CONVERSATION_TURN_INDEX,
            CONVERSATION_TURN_IDEMPOTENCY_INDEX,
        }
        index = next(
            candidate
            for candidate in table.indexes
            if candidate.name == CONVERSATION_TURN_INDEX
        )
        assert [column.name for column in index.columns] == [
            "conversation_id",
            "created_at",
        ]

    def test_conversation_is_registered_for_create_all(self) -> None:
        from backend.app.db.models import get_all_models

        assert Conversation in get_all_models()
        assert ConversationTurn in get_all_models()


# ============================================================
# 2：SQL Compilation Contract（离线；不执行）
# ============================================================


class TestSqlCompilationContract:
    def test_conversation_select_is_explicit_columns(self) -> None:
        repository = ConversationRepository()
        sql = _compile(repository.build_conversation_select("conv-x"))

        assert "SELECT *" not in sql
        assert "ai_ops.conversation" in sql
        for column in CONVERSATION_READ_COLUMNS:
            assert column in sql, column

    def test_turn_select_ordering_is_created_at_then_turn_id(self) -> None:
        repository = ConversationRepository()
        sql = _compile(repository.build_turn_select("conv-x"))

        assert "ORDER BY" in sql
        assert "created_at ASC" in sql
        assert "turn_id ASC" in sql
        for column in CONVERSATION_TURN_READ_COLUMNS:
            assert column in sql, column

    def test_conversation_insert_targets_schema_table(self) -> None:
        repository = ConversationRepository()
        sql = _compile(
            repository.build_conversation_insert(
                conversation_id="conv-x", project_id="p", status="ACTIVE"
            )
        )
        assert "INSERT INTO ai_ops.conversation" in sql

    def test_turn_insert_targets_schema_table(self) -> None:
        repository = ConversationRepository()
        sql = _compile(
            repository.build_turn_insert(
                conversation_id="conv-x",
                role="USER",
                content="hi",
                assistant_request_id=None,
            )
        )
        assert "INSERT INTO ai_ops.conversation_turn" in sql

    def test_status_update_sets_status_and_updated_at(self) -> None:
        repository = ConversationRepository()
        sql = _compile(repository.build_status_update("conv-x", "ARCHIVED"))

        assert "UPDATE ai_ops.conversation SET" in sql
        assert "status" in sql
        assert "updated_at" in sql

    def test_touch_update_only_updates_updated_at(self) -> None:
        repository = ConversationRepository()
        sql = _compile(repository.build_conversation_touch_update("conv-x"))

        assert "UPDATE ai_ops.conversation SET" in sql
        assert "updated_at" in sql
        assert "status" not in sql

    def test_status_lookup_selects_single_column(self) -> None:
        repository = ConversationRepository()
        sql = _compile(repository.build_conversation_status_lookup("conv-x"))

        assert sql.count("ai_ops.conversation.status") >= 1


# ============================================================
# 3：Conversation CRUD（DB-gated）
# ============================================================


@_DB_GATED
class TestConversationCrud:
    def test_create_conversation(self, created: list[str]) -> None:
        service = ConversationService()
        view = _new_conversation(service, created)

        assert view.conversation_id
        assert len(view.conversation_id) == 36  # uuid4 字符串
        assert view.project_id == "vietnam-wms"
        assert view.status == CONVERSATION_STATUS_ACTIVE
        assert view.updated_at == view.created_at  # 同一事务内的 now()

    def test_get_conversation(self, created: list[str]) -> None:
        service = ConversationService()
        view = _new_conversation(service, created)
        fetched = service.get_conversation(view.conversation_id)

        assert fetched is not None
        assert fetched.conversation_id == view.conversation_id
        assert fetched.project_id == view.project_id
        assert fetched.status == CONVERSATION_STATUS_ACTIVE

    def test_get_missing_conversation_returns_none(self) -> None:
        service = ConversationService()
        assert service.get_conversation(new_conversation_id()) is None

    def test_archive_conversation(self, created: list[str]) -> None:
        service = ConversationService()
        view = _new_conversation(service, created)
        archived = service.archive_conversation(view.conversation_id)

        assert archived.status == CONVERSATION_STATUS_ARCHIVED
        assert archived.updated_at >= view.updated_at
        fetched = service.get_conversation(view.conversation_id)
        assert fetched is not None
        assert fetched.status == CONVERSATION_STATUS_ARCHIVED

    def test_archive_is_idempotent(self, created: list[str]) -> None:
        service = ConversationService()
        view = _new_conversation(service, created)

        first = service.archive_conversation(view.conversation_id)
        second = service.archive_conversation(view.conversation_id)  # 第二次不报错

        assert first.status == second.status == CONVERSATION_STATUS_ARCHIVED
        assert first.updated_at == second.updated_at  # 幂等：无 DB 写入

    def test_archive_missing_conversation(self) -> None:
        service = ConversationService()
        with pytest.raises(ConversationNotFoundError):
            service.archive_conversation(new_conversation_id())


# ============================================================
# 4：Turn CRUD（DB-gated）
# ============================================================


@_DB_GATED
class TestTurnCrud:
    def test_append_user_turn(self, created: list[str]) -> None:
        service = ConversationService()
        view = _new_conversation(service, created)

        turn = service.append_turn(
            conversation_id=view.conversation_id,
            role=TURN_ROLE_USER,
            content="查询 A001 库存",
            assistant_request_id=None,
        )

        assert turn.turn_id >= 1
        assert turn.role == TURN_ROLE_USER
        assert turn.content == "查询 A001 库存"
        assert turn.assistant_request_id is None

    def test_append_assistant_turn(self, created: list[str]) -> None:
        service = ConversationService()
        view = _new_conversation(service, created)

        turn = service.append_turn(
            conversation_id=view.conversation_id,
            role=TURN_ROLE_ASSISTANT,
            content="A001 当前库存 1250 个。",
            assistant_request_id="req-step5-001",
        )

        assert turn.role == TURN_ROLE_ASSISTANT
        assert turn.assistant_request_id == "req-step5-001"

    def test_list_turns_empty(self, created: list[str]) -> None:
        service = ConversationService()
        view = _new_conversation(service, created)
        assert service.list_turns(view.conversation_id) == ()

    def test_list_turns_missing_conversation(self) -> None:
        service = ConversationService()
        with pytest.raises(ConversationNotFoundError):
            service.list_turns(new_conversation_id())

    def test_list_turns_ordering(self, created: list[str]) -> None:
        service = ConversationService()
        view = _new_conversation(service, created)

        first = service.append_turn(
            conversation_id=view.conversation_id,
            role=TURN_ROLE_USER,
            content="第一条",
            assistant_request_id=None,
        )
        second = service.append_turn(
            conversation_id=view.conversation_id,
            role=TURN_ROLE_ASSISTANT,
            content="第二条",
            assistant_request_id="req-step5-002",
        )
        third = service.append_turn(
            conversation_id=view.conversation_id,
            role=TURN_ROLE_USER,
            content="第三条",
            assistant_request_id=None,
        )

        turns = service.list_turns(view.conversation_id)
        assert [t.turn_id for t in turns] == [
            first.turn_id,
            second.turn_id,
            third.turn_id,
        ]
        assert [t.content for t in turns] == ["第一条", "第二条", "第三条"]
        assert all(
            turns[index].created_at <= turns[index + 1].created_at
            for index in range(len(turns) - 1)
        )
        assert all(
            turns[index].turn_id < turns[index + 1].turn_id
            for index in range(len(turns) - 1)
        )


# ============================================================
# 5：Validation（Service 是业务规则 owner；DB-gated）
# ============================================================


@_DB_GATED
class TestValidation:
    def test_user_turn_with_request_id_is_rejected(self, created: list[str]) -> None:
        service = ConversationService()
        view = _new_conversation(service, created)
        with pytest.raises(ValueError):
            service.append_turn(
                conversation_id=view.conversation_id,
                role=TURN_ROLE_USER,
                content="查询 A001",
                assistant_request_id="req-step5-003",
            )

    def test_assistant_turn_without_request_id_is_rejected(
        self, created: list[str]
    ) -> None:
        service = ConversationService()
        view = _new_conversation(service, created)
        with pytest.raises(ValueError):
            service.append_turn(
                conversation_id=view.conversation_id,
                role=TURN_ROLE_ASSISTANT,
                content="A001 库存 1250",
                assistant_request_id=None,
            )

    def test_empty_content_is_rejected(self, created: list[str]) -> None:
        service = ConversationService()
        view = _new_conversation(service, created)
        with pytest.raises(ValueError):
            service.append_turn(
                conversation_id=view.conversation_id,
                role=TURN_ROLE_USER,
                content="   ",
                assistant_request_id=None,
            )

    def test_invalid_role_is_rejected(self, created: list[str]) -> None:
        service = ConversationService()
        view = _new_conversation(service, created)
        with pytest.raises(ValueError):
            service.append_turn(
                conversation_id=view.conversation_id,
                role="SYSTEM",
                content="系统消息不应进入 History",
                assistant_request_id=None,
            )


class TestValidationOffline:
    """不需要数据库即可验证的入参规则（Service 在触达 DB 之前拒绝）。"""

    def test_invalid_project_id_is_rejected(self) -> None:
        service = ConversationService()
        for bad in ("", "   ", None):
            with pytest.raises(ValueError):
                service.create_conversation(project_id=bad)  # type: ignore[arg-type]

    def test_invalid_conversation_id_is_rejected(self) -> None:
        service = ConversationService()
        with pytest.raises(ValueError):
            service.get_conversation("   ")


# ============================================================
# 6：Archive → append 拒绝（DB write = 0；DB-gated）
# ============================================================


@_DB_GATED
class TestArchivedAppend:
    def test_append_to_archived_is_rejected_with_zero_writes(
        self, created: list[str]
    ) -> None:
        service = ConversationService()
        view = _new_conversation(service, created)
        service.archive_conversation(view.conversation_id)

        with pytest.raises(ConversationArchivedError):
            service.append_turn(
                conversation_id=view.conversation_id,
                role=TURN_ROLE_USER,
                content="归档后不应写入",
                assistant_request_id=None,
            )

        assert _count_turns(view.conversation_id) == 0  # write before reject = 0


# ============================================================
# 6b：Project Binding（DB-gated + 离线签名断言）
# ============================================================


@_DB_GATED
class TestProjectBinding:
    def test_project_id_is_immutable_after_turns(self, created: list[str]) -> None:
        """创建时绑定 project-a；追加消息后仍为 project-a（不可切换）。"""
        service = ConversationService()
        view = _new_conversation(service, created, project_id="project-a")

        service.append_turn(
            conversation_id=view.conversation_id,
            role=TURN_ROLE_USER,
            content="查询 A001 库存",
            assistant_request_id=None,
        )
        service.append_turn(
            conversation_id=view.conversation_id,
            role=TURN_ROLE_ASSISTANT,
            content="A001 库存 1250",
            assistant_request_id="req-step5-005",
        )

        fetched = service.get_conversation(view.conversation_id)
        assert fetched is not None
        assert fetched.project_id == "project-a"

    def test_conversation_created_for_project_b_is_independent(
        self, created: list[str]
    ) -> None:
        """project-a 与 project-b 的会话互不干扰（只按 conversation_id 定位）。"""
        service = ConversationService()
        first = _new_conversation(service, created, project_id="project-a")
        second = _new_conversation(service, created, project_id="project-b")

        service.append_turn(
            conversation_id=first.conversation_id,
            role=TURN_ROLE_USER,
            content="A 的问题",
            assistant_request_id=None,
        )

        assert service.list_turns(second.conversation_id) == ()
        assert service.get_conversation(second.conversation_id).project_id == (
            "project-b"
        )
        assert service.get_conversation(first.conversation_id).project_id == "project-a"


class TestProjectBindingContract:
    """离线：定位参数只有 conversation_id（客户端 project_id 无法覆盖绑定）。"""

    def test_locate_methods_take_conversation_id_only(self) -> None:
        repository = ConversationRepository()
        for name in (
            "get_by_conversation_id",
            "list_turns_by_conversation_id",
        ):
            params = inspect.signature(
                getattr(repository, name)
            ).parameters
            assert [p for p in params if p != "self"] == ["conversation_id"], name

    def test_service_has_no_project_switch_method(self) -> None:
        service = ConversationService()
        for forbidden in (
            "update_project",
            "change_project",
            "set_project",
            "switch_project",
        ):
            assert not hasattr(service, forbidden), forbidden


# ============================================================
# 7：append_turn 原子性（DB-gated）
# ============================================================


@_DB_GATED
class TestAppendTurnAtomicity:
    def test_failed_update_rolls_back_inserted_turn(
        self, created: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = ConversationService()
        view = _new_conversation(service, created)

        def _broken_touch_update(self: Any, conversation_id: str) -> Any:
            """故意失败的 UPDATE（引用不存在的列）。"""
            return text(
                "UPDATE ai_ops.conversation "
                "SET updated_at = nonexistent_column "
                "WHERE conversation_id = :cid"
            )

        monkeypatch.setattr(
            ConversationRepository,
            "build_conversation_touch_update",
            _broken_touch_update,
        )

        with pytest.raises(ConversationRepositoryError):
            service.append_turn(
                conversation_id=view.conversation_id,
                role=TURN_ROLE_USER,
                content="不应留下",
                assistant_request_id=None,
            )

        # ROLLBACK：不留"Turn 已存在 / updated_at 未更新"的中间状态
        assert _count_turns(view.conversation_id) == 0
        fetched = service.get_conversation(view.conversation_id)
        assert fetched is not None
        assert fetched.updated_at == view.updated_at


# ============================================================
# 8：Delete 语义（Turn 级联 / 观测保留；DB-gated）
# ============================================================


@_DB_GATED
class TestDeleteSemantics:
    def test_delete_conversation_cascades_turns_and_keeps_observability(
        self, created: list[str]
    ) -> None:
        from backend.app.db.assistant_outcome_repository import (
            AssistantOutcomeRepository,
        )

        service = ConversationService()
        view = _new_conversation(service, created)
        request_id = "req-step5-delete-001"
        service.append_turn(
            conversation_id=view.conversation_id,
            role=TURN_ROLE_ASSISTANT,
            content="A001 库存 1250",
            assistant_request_id=request_id,
        )
        AssistantOutcomeRepository().create(
            assistant_request_id=request_id, outcome="SUCCESS"
        )
        assert _count_turns(view.conversation_id) == 1
        assert _count_outcomes(request_id) == 1

        # 物理删除会话（未来 Delete 语义；本 Step 不新增 delete API）
        factory = get_session_factory()
        assert factory is not None
        with factory() as session, session.begin():
            session.execute(
                text(f"DELETE FROM {_CONVERSATION_TABLE} WHERE conversation_id = :cid"),
                {"cid": view.conversation_id},
            )
        created.remove(view.conversation_id)  # 已删除，无需重复清理

        # FK ON DELETE CASCADE：Turn 随会话删除
        assert _count_turns(view.conversation_id) == 0
        # Observability lifecycle ≠ Conversation lifecycle：观测记录保留
        assert _count_outcomes(request_id) == 1

        # 清理本测试写入的观测行（不留残留）
        with factory() as session, session.begin():
            session.execute(
                text(
                    f"DELETE FROM {_OUTCOME_TABLE} WHERE assistant_request_id = :rid"
                ),
                {"rid": request_id},
            )
        assert _count_outcomes(request_id) == 0


# ============================================================
# 9：Security / 字段边界（离线 + DB-gated）
# ============================================================


class TestPersistenceSecurityContract:
    def test_read_column_whitelists_match_frozen_models(self) -> None:
        assert CONVERSATION_READ_COLUMNS == (
            "conversation_id",
            "project_id",
            "created_at",
            "updated_at",
            "status",
        )
        # Phase 4.2 Step 6：新增 idempotency_key（显式列；不用 SELECT *）
        assert CONVERSATION_TURN_READ_COLUMNS == (
            "turn_id",
            "conversation_id",
            "role",
            "content",
            "assistant_request_id",
            "created_at",
            "idempotency_key",
        )

    def test_no_sensitive_columns_exist(self) -> None:
        columns = set(CONVERSATION_READ_COLUMNS) | set(CONVERSATION_TURN_READ_COLUMNS)
        columns |= set(Conversation.__table__.columns.keys())
        columns |= set(ConversationTurn.__table__.columns.keys())
        for forbidden in (
            "api_key",
            "password",
            "authorization",
            "headers",
            "database_url",
            "prompt",
            "system_prompt",
            "tool_definitions",
            "sql",
            "chunk_content",
            "embedding",
            "session",
            "provider_response",
        ):
            assert forbidden not in columns, forbidden


@_DB_GATED
class TestPersistenceSecurity:
    def test_normal_content_roundtrip(self, created: list[str]) -> None:
        """普通 user / assistant 文本可正常持久化（不做 DLP / 不改写内容）。"""
        service = ConversationService()
        view = _new_conversation(service, created)
        service.append_turn(
            conversation_id=view.conversation_id,
            role=TURN_ROLE_USER,
            content="越南仓 A001 还有多少库存？",
            assistant_request_id=None,
        )
        service.append_turn(
            conversation_id=view.conversation_id,
            role=TURN_ROLE_ASSISTANT,
            content="A001 在越南仓当前库存为 1250 个。",
            assistant_request_id="req-step5-004",
        )
        turns = service.list_turns(view.conversation_id)

        assert [t.content for t in turns] == [
            "越南仓 A001 还有多少库存？",
            "A001 在越南仓当前库存为 1250 个。",
        ]
