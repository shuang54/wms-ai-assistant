"""Phase 4.2 Step 6 —— Message Idempotency DB 集成测试（真实 PostgreSQL）。

隔离策略（沿用 ``tests/test_conversation_persistence_db.py``）：

    * 建表：module fixture 调用 ``init_db()``（幂等；列 + 唯一索引由显式 DDL 补齐）；
    * 残留：每个测试创建的 conversation 在结束时按 conversation_id 精确删除
      （FK CASCADE 连带删除 turns）；**不使用** TRUNCATE；
    * 不删除 / 不修改任何历史数据；不触碰观测表；
    * Fake 只替换 **AIOrchestrator**（持久化 / Service / Repository 全部真实）。

覆盖（Step 6 §二十四～§三十二）：

    schema（列 + UNIQUE 索引）· 历史 NULL 兼容 · 跨会话隔离
    唯一冲突 → 幂等冲突异常（不是 500）· 并发同 key（USER = 1）
    端到端 duplicate replay / conflict / retry · ASSISTANT key = NULL
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import text

from backend.app.db.conversation_repository import (
    TURN_ROLE_ASSISTANT,
    TURN_ROLE_USER,
    ConversationRepository,
    ConversationTurnIdempotencyConflictRepositoryError,
)
from backend.app.db.session import get_session_factory
from backend.app.services.ai_orchestrator_service import (
    AIOrchestrationResult,
    RouteType,
)
from backend.app.services.chat_application_service import (
    ChatApplicationService,
    MessageReplay,
)
from backend.app.services.conversation_service import (
    IdempotencyKeyInFlightError,
    IdempotencyKeyReusedWithDifferentPayloadError,
    ConversationMessageIdempotencyConflictError,
    ConversationService,
)

_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {"1", "true", "yes", "on"}

_DB_GATED = pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable PostgreSQL integration tests",
)

_CONVERSATION_TABLE = "ai_ops.conversation"
_TURN_TABLE = "ai_ops.conversation_turn"

_DEFAULT_PROJECT_ID = "vietnam-wms"
_DEFAULT_REQUEST_ID = "step6-req-0001"


# ============================================================
# Fixtures / 辅助
# ============================================================


@pytest.fixture(scope="module", autouse=True)
def _ensure_tables() -> None:
    if not _ENV_FLAG:
        return
    from backend.app.db import init_db as init_db_module

    init_db_module.init_db()
    # 幂等性：第二次执行必须 no-op（不得报错 / 不得改数据）
    init_db_module.init_db()


@pytest.fixture()
def created() -> Iterator[list[str]]:
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


def _query_scalar(sql: str, params: dict[str, Any] | None = None) -> Any:
    factory = get_session_factory()
    assert factory is not None
    with factory() as session:
        return session.execute(text(sql), params or {}).scalar()


def _query_all(sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    factory = get_session_factory()
    assert factory is not None
    with factory() as session:
        return [row[0] for row in session.execute(text(sql), params or {}).all()]


def _new_conversation(service: ConversationService, created: list[str]) -> Any:
    view = service.create_conversation(project_id=_DEFAULT_PROJECT_ID)
    created.append(view.conversation_id)
    return view


def _count_turns(conversation_id: str, role: str | None = None) -> int:
    sql = f"SELECT COUNT(*) FROM {_TURN_TABLE} WHERE conversation_id = :cid"
    params: dict[str, Any] = {"cid": conversation_id}
    if role is not None:
        sql += " AND role = :role"
        params["role"] = role
    return int(_query_scalar(sql, params))


def _null_key_count(conversation_id: str) -> int:
    return int(
        _query_scalar(
            f"SELECT COUNT(*) FROM {_TURN_TABLE} "
            "WHERE conversation_id = :cid AND idempotency_key IS NULL",
            {"cid": conversation_id},
        )
    )


class FakeOrchestrator:
    """duck-type AIOrchestrator（**唯一**被替换的依赖）。"""

    def __init__(self, *, content: str | None = "已持久化回答") -> None:
        self.content = content
        self.calls: list[str] = []

    async def execute(
        self, question: str, *, context: str | None = None
    ) -> AIOrchestrationResult:
        self.calls.append(question)
        return AIOrchestrationResult(
            route=RouteType.TEXT_TO_SQL,
            content=self.content,
            data=None,
            metadata={"request_id": _DEFAULT_REQUEST_ID},
        )


def _service(orchestrator: FakeOrchestrator) -> ChatApplicationService:
    return ChatApplicationService(
        conversation_service=ConversationService(),
        orchestrator_factory=lambda project_id: orchestrator,
    )


# ============================================================
# 1. Schema（真实库）
# ============================================================


@_DB_GATED
class TestIdempotencySchema:
    def test_idempotency_key_column_exists_and_nullable(self) -> None:
        data_type = _query_scalar(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema='ai_ops' AND table_name='conversation_turn' "
            "AND column_name='idempotency_key'"
        )
        assert data_type == "character varying"
        is_nullable = _query_scalar(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema='ai_ops' AND table_name='conversation_turn' "
            "AND column_name='idempotency_key'"
        )
        assert is_nullable == "YES"

    def test_unique_index_exists_on_conversation_and_key(self) -> None:
        indexdef = _query_scalar(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname='ai_ops' AND tablename='conversation_turn' "
            "AND indexname='uq_conversation_turn_conversation_id_idempotency_key'"
        )
        assert indexdef is not None
        assert "UNIQUE" in indexdef.upper()
        assert "(conversation_id, idempotency_key)" in indexdef

    def test_no_single_column_unique_on_key(self) -> None:
        """scope = conversation：**不是** UNIQUE(idempotency_key)。"""
        names = _query_all(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname='ai_ops' AND tablename='conversation_turn'"
        )
        assert "uq_conversation_turn_conversation_id_idempotency_key" in names


# ============================================================
# 2. 历史 NULL 兼容 / 隔离 / 冲突
# ============================================================


@_DB_GATED
class TestHistoricalNullCompatibility:
    def test_multiple_null_key_rows_coexist(self, created: list[str]) -> None:
        """标准 UNIQUE 下 NULL 互不冲突（历史行 / 无幂等请求完全兼容）。"""
        service = ConversationService()
        view = _new_conversation(service, created)

        for index in range(3):
            service.append_turn(
                conversation_id=view.conversation_id,
                role=TURN_ROLE_USER,
                content=f"无幂等消息-{index}",
                assistant_request_id=None,
            )

        assert _count_turns(view.conversation_id) == 3
        assert _null_key_count(view.conversation_id) == 3

    def test_key_and_null_rows_mixed(self, created: list[str]) -> None:
        service = ConversationService()
        view = _new_conversation(service, created)

        service.append_turn(
            conversation_id=view.conversation_id,
            role=TURN_ROLE_USER,
            content="历史消息",
            assistant_request_id=None,
        )
        service.append_turn(
            conversation_id=view.conversation_id,
            role=TURN_ROLE_USER,
            content="带幂等键消息",
            assistant_request_id=None,
            idempotency_key="step6-key-1",
        )

        assert _null_key_count(view.conversation_id) == 1
        assert _count_turns(view.conversation_id) == 2


@_DB_GATED
class TestUniqueConstraint:
    def test_duplicate_key_raises_idempotency_conflict(
        self, created: list[str]
    ) -> None:
        """唯一冲突 → 幂等冲突（Service 层 IdempotencyKeyInFlightError → 409，非 500）。

        Repository 层原始异常 = ``ConversationTurnIdempotencyConflictRepositoryError``
        （见 :class:`ConversationRepository.append_turn`）。
        """
        service = ConversationService()
        view = _new_conversation(service, created)
        service.append_turn(
            conversation_id=view.conversation_id,
            role=TURN_ROLE_USER,
            content="第一次",
            assistant_request_id=None,
            idempotency_key="step6-dup",
        )

        with pytest.raises(ConversationMessageIdempotencyConflictError) as excinfo:
            service.append_turn(
                conversation_id=view.conversation_id,
                role=TURN_ROLE_USER,
                content="第二次",
                assistant_request_id=None,
                idempotency_key="step6-dup",
            )

        assert type(excinfo.value) is IdempotencyKeyInFlightError
        assert _count_turns(view.conversation_id, TURN_ROLE_USER) == 1

    def test_repository_raises_raw_conflict_error(self, created: list[str]) -> None:
        service = ConversationService()
        view = _new_conversation(service, created)
        repository = ConversationRepository()
        repository.append_turn(
            conversation_id=view.conversation_id,
            role=TURN_ROLE_USER,
            content="第一次",
            assistant_request_id=None,
            idempotency_key="step6-dup-raw",
        )

        with pytest.raises(ConversationTurnIdempotencyConflictRepositoryError):
            repository.append_turn(
                conversation_id=view.conversation_id,
                role=TURN_ROLE_USER,
                content="第二次",
                assistant_request_id=None,
                idempotency_key="step6-dup-raw",
            )

        assert _count_turns(view.conversation_id, TURN_ROLE_USER) == 1

    def test_same_key_across_conversations_is_independent(
        self, created: list[str]
    ) -> None:
        service = ConversationService()
        first = _new_conversation(service, created)
        second = _new_conversation(service, created)

        for view in (first, second):
            service.append_turn(
                conversation_id=view.conversation_id,
                role=TURN_ROLE_USER,
                content="同 key 不同会话",
                assistant_request_id=None,
                idempotency_key="step6-shared",
            )

        assert _count_turns(first.conversation_id) == 1
        assert _count_turns(second.conversation_id) == 1


@_DB_GATED
class TestConcurrency:
    async def test_concurrent_same_key_creates_exactly_one_user_turn(
        self, created: list[str]
    ) -> None:
        """§二十四：并发同 key ⇒ USER Turn = 1（另一个捕获唯一冲突 → 409）。

        **本测试不声称** AI exactly-once（KL-1 Crash B 仍存在）；
        只证明 DB 幂等身份 + USER Turn 持久化不会产生 duplicate USER Turn。
        """
        service = ConversationService()
        view = _new_conversation(service, created)
        repository = ConversationRepository()

        def _append(index: int) -> str:
            try:
                repository.append_turn(
                    conversation_id=view.conversation_id,
                    role=TURN_ROLE_USER,
                    content="并发同 key 同 content",
                    assistant_request_id=None,
                    idempotency_key="step6-concurrent",
                )
            except ConversationTurnIdempotencyConflictRepositoryError:
                return "conflict"
            return "created"

        results = await asyncio.gather(
            asyncio.to_thread(_append, 1),
            asyncio.to_thread(_append, 2),
        )

        assert sorted(results) == ["conflict", "created"]
        assert _count_turns(view.conversation_id, TURN_ROLE_USER) == 1
        assert _count_turns(view.conversation_id, TURN_ROLE_ASSISTANT) <= 1


# ============================================================
# 3. 端到端（真实 Service + Repository + Fake Orchestrator）
# ============================================================


@_DB_GATED
class TestIdempotencyRuntime:
    async def test_first_request_persists_key_and_assistant(
        self, created: list[str]
    ) -> None:
        service = ConversationService()
        view = _new_conversation(service, created)
        orchestrator = FakeOrchestrator(content="第一次回答")

        result = await _service(orchestrator).execute_message(
            conversation_id=view.conversation_id,
            content="你好",
            idempotency_key="step6-e2e-1",
        )

        assert result.content == "第一次回答"
        assert _count_turns(view.conversation_id, TURN_ROLE_USER) == 1
        assert _count_turns(view.conversation_id, TURN_ROLE_ASSISTANT) == 1
        # ASSISTANT Turn 的 idempotency_key 恒为 NULL
        null_keys = int(
            _query_scalar(
                f"SELECT COUNT(*) FROM {_TURN_TABLE} "
                "WHERE conversation_id = :cid AND role = 'ASSISTANT' "
                "AND idempotency_key IS NULL",
                {"cid": view.conversation_id},
            )
        )
        assert null_keys == 1

    async def test_duplicate_replays_persisted_message(
        self, created: list[str]
    ) -> None:
        service = ConversationService()
        view = _new_conversation(service, created)
        orchestrator = FakeOrchestrator(content="第一次回答")
        application = _service(orchestrator)

        await application.execute_message(
            conversation_id=view.conversation_id,
            content="你好",
            idempotency_key="step6-e2e-2",
        )
        orchestrator.content = "第二次（不应发生）"
        second = await application.execute_message(
            conversation_id=view.conversation_id,
            content="你好",
            idempotency_key="step6-e2e-2",
        )

        assert isinstance(second, MessageReplay)
        assert second.content == "第一次回答"
        assert second.assistant_request_id == _DEFAULT_REQUEST_ID
        assert orchestrator.calls == ["你好"]  # AI = 1 次
        assert _count_turns(view.conversation_id, TURN_ROLE_USER) == 1
        assert _count_turns(view.conversation_id, TURN_ROLE_ASSISTANT) == 1

    async def test_same_key_different_payload_is_conflict(
        self, created: list[str]
    ) -> None:
        service = ConversationService()
        view = _new_conversation(service, created)
        orchestrator = FakeOrchestrator()
        application = _service(orchestrator)

        await application.execute_message(
            conversation_id=view.conversation_id,
            content="你好",
            idempotency_key="step6-e2e-3",
        )
        with pytest.raises(IdempotencyKeyReusedWithDifferentPayloadError):
            await application.execute_message(
                conversation_id=view.conversation_id,
                content="完全不同的问题",
                idempotency_key="step6-e2e-3",
            )

        assert orchestrator.calls == ["你好"]
        assert _count_turns(view.conversation_id, TURN_ROLE_USER) == 1

    async def test_empty_then_retry_reuses_user_turn(
        self, created: list[str]
    ) -> None:
        """OD-26：EMPTY = NOT completed ⇒ 同 key 可重试，且 USER Turn 不重复。"""
        service = ConversationService()
        view = _new_conversation(service, created)
        orchestrator = FakeOrchestrator(content=None)
        application = _service(orchestrator)

        await application.execute_message(
            conversation_id=view.conversation_id,
            content="你好",
            idempotency_key="step6-e2e-4",
        )
        assert _count_turns(view.conversation_id, TURN_ROLE_ASSISTANT) == 0

        orchestrator.content = "这次有内容"
        await application.execute_message(
            conversation_id=view.conversation_id,
            content="你好",
            idempotency_key="step6-e2e-4",
        )

        assert orchestrator.calls == ["你好", "你好"]
        assert _count_turns(view.conversation_id, TURN_ROLE_USER) == 1
        assert _count_turns(view.conversation_id, TURN_ROLE_ASSISTANT) == 1

    async def test_no_key_requests_are_independent(self, created: list[str]) -> None:
        service = ConversationService()
        view = _new_conversation(service, created)
        orchestrator = FakeOrchestrator()
        application = _service(orchestrator)

        await application.execute_message(
            conversation_id=view.conversation_id, content="你好"
        )
        await application.execute_message(
            conversation_id=view.conversation_id, content="你好"
        )

        assert _count_turns(view.conversation_id, TURN_ROLE_USER) == 2
        assert _null_key_count(view.conversation_id) == 4  # 2 USER + 2 ASSISTANT


__all__ = [
    "TestIdempotencySchema",
    "TestHistoricalNullCompatibility",
    "TestUniqueConstraint",
    "TestConcurrency",
    "TestIdempotencyRuntime",
]
