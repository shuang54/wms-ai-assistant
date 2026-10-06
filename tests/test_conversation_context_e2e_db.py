"""Conversation Context Runtime E2E（Phase 4.2 Step 7G）—— 真 PostgreSQL / HTTP。

真实链路（**只有 AI 执行是 Fake**）：

```text
POST /api/conversations/{id}/messages
        ↓
Conversation API（真实）
        ↓
ChatApplicationService（真实）→ Idempotency（真实）→ Selection（真实）
        ↓
ConversationService → ConversationRepository → PostgreSQL（真实）
        ↓
ConversationContextBuilder（真实）
        ↓
Fake Orchestrator（记录 (question, context)；脚本化返回 / 注入异常）
        ↓
ASSISTANT Turn 持久化（真实）
```

隔离与残留（与 ``tests/test_conversation_multiturn_db_e2e.py`` 同策略）：

    * ``RUN_DB_TESTS=1`` 才运行；
    * module 级残留守卫：conversation / conversation_turn 计数前后一致，
      4 张 observability 表计数**不变**；
    * 每个用例只删除**本测试创建**的 conversation_id（绝不 TRUNCATE / 全表 DELETE）。

禁止：DeepSeek / 真实 LLM / network / migration / schema 变更（本文件 0 变更）。
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.api import conversations as conversations_module
from backend.app.db.session import get_session_factory
from backend.app.main import app
from backend.app.services.chat_application_service import ChatApplicationService
from backend.app.services.conversation_context_builder import (
    ConversationContextBuilder,
)
from backend.app.services.conversation_service import ConversationService
from tests.test_conversation_multiturn_db_e2e import (
    FakeOrchestrator,
    FakeOrchestratorFactory,
    _result,
)

_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {
    "1", "true", "yes", "on",
}

pytestmark = pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable conversation context PostgreSQL E2E",
)

TEST_PROJECT_ID = "phase-4-2-step-7g-test"

_CONVERSATION_TABLE = "ai_ops.conversation"
_TURN_TABLE = "ai_ops.conversation_turn"

_OBSERVABILITY_TABLES: tuple[str, ...] = (
    "ai_ops.llm_usage_record",
    "ai_ops.tool_execution_record",
    "ai_ops.rag_execution_record",
    "ai_ops.assistant_outcome_record",
)

__all__ = [
    "TestMultiTurnWindowViaHttp",
    "TestIdempotencyViaHttp",
    "TestFailureHistoryViaHttp",
]


# ============================================================
# Fixtures（reuse 4.1 Step 15 DB E2E 策略）
# ============================================================


@pytest.fixture(scope="module", autouse=True)
def _ensure_tables() -> None:
    from backend.app.db import init_db as init_db_module

    init_db_module.init_db()


@pytest.fixture(scope="module", autouse=True)
def _residue_guard() -> Iterator[None]:
    before_conversations = _count(_CONVERSATION_TABLE)
    before_turns = _count(_TURN_TABLE)
    before_observability = {t: _count(t) for t in _OBSERVABILITY_TABLES}

    yield

    assert _count(_CONVERSATION_TABLE) == before_conversations, "conversation 残留"
    assert _count(_TURN_TABLE) == before_turns, "conversation_turn 残留"
    assert {t: _count(t) for t in _OBSERVABILITY_TABLES} == before_observability


@pytest.fixture()
def created() -> Iterator[list[str]]:
    ids: list[str] = []
    yield ids
    _delete_conversations(ids)


@dataclass
class Env:
    client: TestClient
    service: ChatApplicationService
    orchestrator: FakeOrchestrator


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    orchestrator = FakeOrchestrator()
    factory = FakeOrchestratorFactory(orchestrator)
    service = ChatApplicationService(
        conversation_service=ConversationService(),
        orchestrator_factory=factory,
        context_builder=ConversationContextBuilder(),
    )
    monkeypatch.setattr(
        conversations_module,
        "_chat_application_service",
        service,  # type: ignore[arg-type]
    )
    with TestClient(app) as test_client:
        yield Env(client=test_client, service=service, orchestrator=orchestrator)


# ============================================================
# Helpers
# ============================================================


def _session_factory() -> Any:
    factory = get_session_factory()
    assert factory is not None
    return factory


def _count(table: str) -> int:
    factory = _session_factory()
    with factory() as session:
        return int(
            session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        )


def _count_turns(conversation_id: str) -> int:
    factory = _session_factory()
    with factory() as session:
        return int(
            session.execute(
                text(
                    f"SELECT COUNT(*) FROM {_TURN_TABLE} "
                    "WHERE conversation_id = :cid"
                ),
                {"cid": conversation_id},
            ).scalar_one()
        )


def _delete_conversations(conversation_ids: list[str]) -> None:
    if not conversation_ids:
        return
    factory = _session_factory()
    with factory() as session, session.begin():
        for conversation_id in conversation_ids:
            session.execute(
                text(
                    f"DELETE FROM {_CONVERSATION_TABLE} "
                    "WHERE conversation_id = :cid"
                ),
                {"cid": conversation_id},
            )


def _create(env: Env, created: list[str]) -> str:
    response = env.client.post(
        "/api/conversations", json={"project_id": TEST_PROJECT_ID}
    )
    assert response.status_code == 200, response.text
    conversation_id = response.json()["conversation_id"]
    created.append(conversation_id)
    return conversation_id


def _send(
    env: Env,
    conversation_id: str,
    content: str,
    *,
    idempotency_key: str | None = None,
):
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    return env.client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": content},
        headers=headers,
    )


# ============================================================
# 1. Multi-turn + Context Window via HTTP（真 DB 顺序 + 真 Selection）
# ============================================================


class TestMultiTurnWindowViaHttp:
    def test_window_uses_latest_20_turns_in_db_order(
        self, env: Env, created: list[str]
    ) -> None:
        conversation_id = _create(env, created)

        for index in range(1, 27):
            response = _send(env, conversation_id, f"m{index}")
            assert response.status_code == 200, response.text

        # 26 轮 × 2 = 52 行（真 DB）
        assert _count_turns(conversation_id) == 52

        context = env.orchestrator.contexts[-1] or ""
        lines = context.split("\n")
        # 最新 20 条 = U16 A16 … U25 A25（首条 USER ⇒ 无需对齐丢弃）
        assert len(lines) == 20
        assert lines[0] == "user: m16"
        assert lines[-1] == "assistant: 默认回答"
        assert "user: m15" not in lines
        # 当前消息（m26）不在自己的 context 中
        assert "m26" not in context

    def test_two_turn_context_matches_persisted_history(
        self, env: Env, created: list[str]
    ) -> None:
        conversation_id = _create(env, created)

        assert _send(env, conversation_id, "查询采购订单 A100").status_code == 200
        assert _send(env, conversation_id, "它的供应商是谁？").status_code == 200

        assert env.orchestrator.contexts[0] is None
        context = env.orchestrator.contexts[1] or ""
        assert context == "user: 查询采购订单 A100\nassistant: 默认回答"
        assert "它的供应商是谁？" not in context


# ============================================================
# 2. Idempotency via HTTP（真 DB 唯一性 + 真 Replay 语义）
# ============================================================


class TestIdempotencyViaHttp:
    def test_duplicate_replay_response_and_no_reconsumption(
        self, env: Env, created: list[str]
    ) -> None:
        conversation_id = _create(env, created)

        first = _send(env, conversation_id, "U1", idempotency_key="K1")
        assert first.status_code == 200
        assert first.json()["route"] == "rag"
        calls_after_first = len(env.orchestrator.calls)

        replay = _send(env, conversation_id, "U1", idempotency_key="K1")

        assert replay.status_code == 200
        payload = replay.json()
        assert payload["route"] is None          # 未执行 AI ⇒ 不伪造 route
        assert payload["data"] is None           # 未持久化 runtime data
        assert payload["content"] == "默认回答"   # 已持久化 assistant message
        assert payload["metadata"]["idempotent_replay"] is True
        assert payload["metadata"]["request_id"]
        # AI = 0（第二次）· 无新 Turn
        assert len(env.orchestrator.calls) == calls_after_first
        assert _count_turns(conversation_id) == 2

    def test_conflict_is_409_without_ai_or_write(
        self, env: Env, created: list[str]
    ) -> None:
        conversation_id = _create(env, created)
        assert _send(env, conversation_id, "A", idempotency_key="K1").status_code == 200
        calls_after_first = len(env.orchestrator.calls)

        conflict = _send(env, conversation_id, "B", idempotency_key="K1")

        assert conflict.status_code == 409
        assert "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD" in conflict.text
        assert len(env.orchestrator.calls) == calls_after_first
        assert _count_turns(conversation_id) == 2

    def test_retry_reuses_user_turn_and_persists_assistant(
        self, env: Env, created: list[str]
    ) -> None:
        conversation_id = _create(env, created)
        env.orchestrator.error = RuntimeError("boom sk-secret")

        failed = _send(env, conversation_id, "U1", idempotency_key="K1")

        assert failed.status_code == 500
        assert "sk-secret" not in failed.text
        assert _count_turns(conversation_id) == 1  # USER 保留

        env.orchestrator.error = None
        retried = _send(env, conversation_id, "U1", idempotency_key="K1")

        assert retried.status_code == 200
        assert _count_turns(conversation_id) == 2   # 无第二条 USER
        assert len(env.orchestrator.calls) == 2
        # retry：context 不含当前（复用）turn（无更早历史 ⇒ None）
        assert env.orchestrator.contexts[1] is None

    def test_key_over_128_is_422_without_write(
        self, env: Env, created: list[str]
    ) -> None:
        conversation_id = _create(env, created)

        response = _send(env, conversation_id, "U1", idempotency_key="k" * 129)

        assert response.status_code == 422
        assert env.orchestrator.calls == []
        assert _count_turns(conversation_id) == 0


# ============================================================
# 3. EMPTY / Oversized 历史（真 DB 内容语义）
# ============================================================


class TestFailureHistoryViaHttp:
    def test_empty_result_keeps_user_turn_as_plain_history(
        self, env: Env, created: list[str]
    ) -> None:
        conversation_id = _create(env, created)
        env.orchestrator.scripted["空回答的问题"] = _result(
            content=None, request_id="req-empty"
        )

        empty = _send(env, conversation_id, "空回答的问题")

        assert empty.status_code == 200
        assert _count_turns(conversation_id) == 1  # ASSISTANT absent

        env.orchestrator.scripted["下一个问题"] = _result(
            content="后续回答", request_id="req-next"
        )
        assert _send(env, conversation_id, "下一个问题").status_code == 200

        context = env.orchestrator.contexts[1] or ""
        assert context == "user: 空回答的问题"
        for sentinel in ("EMPTY", "FAILED", "request_id", "req-empty"):
            assert sentinel not in context

    def test_char_cap_12000_stops_cumulative_history_through_http(
        self, env: Env, created: list[str]
    ) -> None:
        conversation_id = _create(env, created)
        big = "x" * 6000  # 单条 ≤ 10000（HTTP DTO 上限）

        for label in ("u1", "u2", "u3"):
            assert _send(env, conversation_id, f"{label}:{big}").status_code == 200

        env.orchestrator.scripted["后续问题"] = _result(
            content="后续回答", request_id="req-after"
        )
        assert _send(env, conversation_id, "后续问题").status_code == 200

        context = env.orchestrator.contexts[-1] or ""
        lines = context.split("\n")
        # 反向累积：A3(4) → U3(6004) → A2(6008) → U2 超 12000 ⇒ 停止
        # ⇒ [A2, U3, A3] → USER-anchor 丢弃 A2 ⇒ [U3, A3]
        assert len(lines) == 2
        assert lines[0] == f"user: u3:{big}"
        assert lines[1] == "assistant: 默认回答"
        assert "u2:" not in context and "u1:" not in context

    def test_content_over_http_limit_is_422_without_write(
        self, env: Env, created: list[str]
    ) -> None:
        conversation_id = _create(env, created)

        response = _send(env, conversation_id, "x" * 10001)

        assert response.status_code == 422
        assert env.orchestrator.calls == []
        assert _count_turns(conversation_id) == 0
