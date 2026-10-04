"""Multi-turn Conversation DB E2E（Phase 4.1 Step 15）。

真实链路（**只有 AI execution 是 Fake**）：

```text
POST /api/conversations
        ↓
POST /api/conversations/{id}/messages
        ↓
Conversation API（真实）
        ↓
ChatApplicationService（真实；context 接线 = Step 14）
        ↓
ConversationService → ConversationRepository → PostgreSQL（真实）
        ↓
ConversationContextBuilder（真实）
        ↓
Fake Orchestrator（**仅此一层**：记录 question / context，脚本化返回）
        ↓
ASSISTANT Turn 持久化（真实）
```

隔离与残留（与 ``tests/test_conversation_api_db_e2e.py`` 同策略）：

    * ``RUN_DB_TESTS=1`` 才运行；
    * module 级残留守卫：conversation / conversation_turn 计数前后一致，
      4 张 observability 表计数**不变**；
    * 每个用例只删除**本测试创建**的 conversation_id（绝不 TRUNCATE / 全表 DELETE）。

禁止：DeepSeek / SiliconFlow / 真实 LLM / network（AI 全部为 Fake）。
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
from backend.app.db.conversation_repository import ConversationRepositoryError
from backend.app.db.session import get_session_factory
from backend.app.dto.assistant_outcome import AssistantOutcome
from backend.app.main import app
from backend.app.services.ai_orchestrator_service import (
    AIOrchestrationResult,
    AIOrchestratorExecutionError,
    RouteType,
)
from backend.app.services.chat_application_service import ChatApplicationService
from backend.app.services.conversation_context_builder import (
    ConversationContextBuilder,
)
from backend.app.services.conversation_service import ConversationService

_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

pytestmark = pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable multi-turn PostgreSQL E2E tests",
)

#: 测试专用 project_id（明显标识，便于人工识别）。
TEST_PROJECT_ID = "phase-4-1-step-15-test"
PROJECT_A = "project-a"
PROJECT_B = "project-b"

_CONVERSATION_TABLE = "ai_ops.conversation"
_TURN_TABLE = "ai_ops.conversation_turn"

_OBSERVABILITY_TABLES: tuple[str, ...] = (
    "ai_ops.llm_usage_record",
    "ai_ops.tool_execution_record",
    "ai_ops.rag_execution_record",
    "ai_ops.assistant_outcome_record",
)

_TURN_COLUMNS = (
    "turn_id, conversation_id, role, content, assistant_request_id, created_at"
)


# ============================================================
# Fake AI（唯一被替换的层）
# ============================================================


def _result(
    *,
    content: str | None,
    request_id: str,
    route: RouteType = RouteType.RAG,
    metadata: dict[str, Any] | None = None,
) -> AIOrchestrationResult:
    payload: dict[str, Any] = {
        "request_id": request_id,
        "outcome": AssistantOutcome.SUCCESS,
    }
    if metadata:
        payload.update(metadata)
    return AIOrchestrationResult(
        route=route, content=content, data=None, metadata=payload
    )


class FakeOrchestrator:
    """记录 ``(question, context)``；按 question 返回脚本化结果；可注入异常。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.scripted: dict[str, AIOrchestrationResult] = {}
        self.error: Exception | None = None
        self.default_result = _result(content="默认回答", request_id="assistant-req-x")

    async def execute(
        self, question: str, *, context: str | None = None
    ) -> AIOrchestrationResult:
        self.calls.append((question, context))
        if self.error is not None:
            raise self.error
        return self.scripted.get(question, self.default_result)

    # ---------- 断言辅助 ----------

    @property
    def questions(self) -> list[str]:
        return [question for question, _ in self.calls]

    @property
    def contexts(self) -> list[str | None]:
        return [context for _, context in self.calls]


class FakeOrchestratorFactory:
    """记录 project_id → 返回同一 FakeOrchestrator。"""

    def __init__(self, orchestrator: FakeOrchestrator) -> None:
        self.project_ids: list[str] = []
        self._orchestrator = orchestrator

    def __call__(self, project_id: str) -> FakeOrchestrator:
        self.project_ids.append(project_id)
        return self._orchestrator


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture(scope="module", autouse=True)
def _ensure_tables() -> None:
    """幂等建表（create_all；本 Step 无 migration）。"""
    from backend.app.db import init_db as init_db_module

    init_db_module.init_db()


@pytest.fixture(scope="module", autouse=True)
def _residue_guard() -> Iterator[None]:
    """残留守卫：会话级计数前后一致（含 observability 隔离）。"""
    before_conversations = _count(_CONVERSATION_TABLE)
    before_turns = _count(_TURN_TABLE)
    before_observability = {t: _count(t) for t in _OBSERVABILITY_TABLES}

    yield

    assert _count(_CONVERSATION_TABLE) == before_conversations, "conversation 残留"
    assert _count(_TURN_TABLE) == before_turns, "conversation_turn 残留"
    after_observability = {t: _count(t) for t in _OBSERVABILITY_TABLES}
    assert after_observability == before_observability, "observability 不得被清理"


@pytest.fixture(scope="module")
def observability_before(_residue_guard: None) -> dict[str, int]:
    """依赖 module 级 guard → 在模块开始（guard setup 之后）立即记录 before。"""
    return {table: _count(table) for table in _OBSERVABILITY_TABLES}


@pytest.fixture()
def created() -> Iterator[list[str]]:
    """收集本用例创建的 conversation_id；结束时精确删除（级联删 turns）。"""
    ids: list[str] = []
    yield ids
    _delete_conversations(ids)


@dataclass
class Env:
    client: TestClient
    service: ChatApplicationService
    orchestrator: FakeOrchestrator
    factory: FakeOrchestratorFactory


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    """真实 Application Service + 真实 Context Builder + Fake AI。"""
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
        yield Env(
            client=test_client,
            service=service,
            orchestrator=orchestrator,
            factory=factory,
        )


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


def _db_turns(conversation_id: str) -> list[dict[str, Any]]:
    factory = _session_factory()
    with factory() as session:
        rows = session.execute(
            text(
                f"SELECT {_TURN_COLUMNS} FROM {_TURN_TABLE} "
                "WHERE conversation_id = :cid ORDER BY created_at ASC, turn_id ASC"
            ),
            {"cid": conversation_id},
        ).all()
    return [dict(row._mapping) for row in rows]


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


def _create(
    env: Env, created: list[str], project_id: str = TEST_PROJECT_ID
) -> dict[str, Any]:
    response = env.client.post(
        "/api/conversations", json={"project_id": project_id}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    created.append(payload["conversation_id"])
    return payload


def _send(env: Env, conversation_id: str, content: str):
    return env.client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": content},
    )


# ============================================================
# 1：Create E2E
# ============================================================


class TestCreateE2E:
    def test_create_conversation_persists_row(
        self, env: Env, created: list[str]
    ) -> None:
        response = env.client.post(
            "/api/conversations", json={"project_id": TEST_PROJECT_ID}
        )

        assert response.status_code == 200
        payload = response.json()
        created.append(payload["conversation_id"])

        assert payload["conversation_id"]
        assert payload["project_id"] == TEST_PROJECT_ID
        assert payload["status"] == "ACTIVE"
        assert _count_turns(payload["conversation_id"]) == 0


# ============================================================
# 2：Multi-turn E2E（first / second / ordering）
# ============================================================


class TestMultiTurnE2E:
    def test_first_turn_persists_user_and_assistant(
        self, env: Env, created: list[str]
    ) -> None:
        conversation_id = _create(env, created)["conversation_id"]
        env.orchestrator.scripted["查询当前库存"] = _result(
            content="当前库存共有 100 件", request_id="assistant-req-001"
        )

        response = _send(env, conversation_id, "查询当前库存")

        assert response.status_code == 200
        assert response.json()["route"] == "rag"
        assert response.json()["content"] == "当前库存共有 100 件"

        turns = _db_turns(conversation_id)
        assert [t["role"] for t in turns] == ["USER", "ASSISTANT"]
        assert turns[0]["content"] == "查询当前库存"
        assert turns[0]["assistant_request_id"] is None
        assert turns[1]["content"] == "当前库存共有 100 件"
        assert turns[1]["assistant_request_id"] == "assistant-req-001"

    def test_first_turn_context_is_none(
        self, env: Env, created: list[str]
    ) -> None:
        conversation_id = _create(env, created)["conversation_id"]
        env.orchestrator.scripted["查询当前库存"] = _result(
            content="当前库存共有 100 件", request_id="assistant-req-001"
        )

        _send(env, conversation_id, "查询当前库存")

        assert env.orchestrator.calls[0][0] == "查询当前库存"
        assert env.orchestrator.contexts[0] is None

    def test_second_turn_receives_previous_history_context(
        self, env: Env, created: list[str]
    ) -> None:
        conversation_id = _create(env, created)["conversation_id"]
        env.orchestrator.scripted["查询当前库存"] = _result(
            content="当前库存共有 100 件", request_id="assistant-req-001"
        )
        env.orchestrator.scripted["其中库存最低的 10 个是什么？"] = _result(
            content="最低库存的10个物料如下...", request_id="assistant-req-002"
        )

        _send(env, conversation_id, "查询当前库存")
        second = _send(env, conversation_id, "其中库存最低的 10 个是什么？")

        assert second.status_code == 200
        assert env.orchestrator.questions == [
            "查询当前库存",
            "其中库存最低的 10 个是什么？",
        ]
        context = env.orchestrator.contexts[1]
        assert context == "user: 查询当前库存\nassistant: 当前库存共有 100 件"
        # current USER 不得进入 context
        assert "其中库存最低的 10 个是什么？" not in (context or "")

    def test_final_turn_ordering_via_get_messages(
        self, env: Env, created: list[str]
    ) -> None:
        conversation_id = _create(env, created)["conversation_id"]
        env.orchestrator.scripted["查询当前库存"] = _result(
            content="当前库存共有 100 件", request_id="assistant-req-001"
        )
        env.orchestrator.scripted["其中库存最低的 10 个是什么？"] = _result(
            content="最低库存的10个物料如下...", request_id="assistant-req-002"
        )

        _send(env, conversation_id, "查询当前库存")
        _send(env, conversation_id, "其中库存最低的 10 个是什么？")

        response = env.client.get(
            f"/api/conversations/{conversation_id}/messages"
        )

        assert response.status_code == 200
        messages = response.json()["messages"]
        assert [m["role"] for m in messages] == [
            "USER",
            "ASSISTANT",
            "USER",
            "ASSISTANT",
        ]
        assert [m["content"] for m in messages] == [
            "查询当前库存",
            "当前库存共有 100 件",
            "其中库存最低的 10 个是什么？",
            "最低库存的10个物料如下...",
        ]
        assert [m["turn_id"] for m in messages] == sorted(
            m["turn_id"] for m in messages
        )
        assert [m["created_at"] for m in messages] == sorted(
            m["created_at"] for m in messages
        )

    def test_request_id_isolation(
        self, env: Env, created: list[str]
    ) -> None:
        conversation_id = _create(env, created)["conversation_id"]
        env.orchestrator.scripted["查询当前库存"] = _result(
            content="当前库存共有 100 件", request_id="assistant-req-001"
        )
        env.orchestrator.scripted["其中库存最低的 10 个是什么？"] = _result(
            content="最低库存的10个物料如下...", request_id="assistant-req-002"
        )

        _send(env, conversation_id, "查询当前库存")
        _send(env, conversation_id, "其中库存最低的 10 个是什么？")

        turns = _db_turns(conversation_id)
        assert [t["assistant_request_id"] for t in turns] == [
            None,
            "assistant-req-001",
            None,
            "assistant-req-002",
        ]
        # 四个 ID 维度互不等价
        assert turns[1]["assistant_request_id"] != turns[1]["conversation_id"]
        assert turns[1]["assistant_request_id"] != str(turns[1]["turn_id"])


# ============================================================
# 3：Isolation（conversation / cross-request / project）
# ============================================================


class TestIsolationE2E:
    def test_conversation_history_is_isolated(
        self, env: Env, created: list[str]
    ) -> None:
        conversation_a = _create(env, created)["conversation_id"]
        conversation_b = _create(env, created)["conversation_id"]

        env.orchestrator.scripted["用户 A 的问题"] = _result(
            content="A 的回答", request_id="req-a1"
        )
        env.orchestrator.scripted["A 的第二问"] = _result(
            content="A2 的回答", request_id="req-a2"
        )
        env.orchestrator.scripted["用户 B 的问题"] = _result(
            content="B 的回答", request_id="req-b1"
        )
        env.orchestrator.scripted["B 的第二问"] = _result(
            content="B2 的回答", request_id="req-b2"
        )

        _send(env, conversation_a, "用户 A 的问题")
        _send(env, conversation_a, "A 的第二问")
        _send(env, conversation_b, "用户 B 的问题")
        _send(env, conversation_b, "B 的第二问")

        # A2 的 context 只含 A1（不含 B 任何内容）
        context_a2 = env.orchestrator.contexts[1] or ""
        assert context_a2 == "user: 用户 A 的问题\nassistant: A 的回答"
        assert "B 的回答" not in context_a2
        assert "用户 B 的问题" not in context_a2

        # B2 的 context 只含 B1（不含 A 任何内容）
        context_b2 = env.orchestrator.contexts[3] or ""
        assert context_b2 == "user: 用户 B 的问题\nassistant: B 的回答"
        assert "A 的回答" not in context_b2
        assert "用户 A 的问题" not in context_b2

        # DB 也完全隔离
        assert [t["content"] for t in _db_turns(conversation_a)] == [
            "用户 A 的问题",
            "A 的回答",
            "A 的第二问",
            "A2 的回答",
        ]
        assert [t["content"] for t in _db_turns(conversation_b)] == [
            "用户 B 的问题",
            "B 的回答",
            "B 的第二问",
            "B2 的回答",
        ]

    def test_new_conversation_has_no_cross_request_history(
        self, env: Env, created: list[str]
    ) -> None:
        conversation_a = _create(env, created)["conversation_id"]
        env.orchestrator.scripted["A1"] = _result(content="A1 答", request_id="req-a1")
        env.orchestrator.scripted["A2"] = _result(content="A2 答", request_id="req-a2")
        _send(env, conversation_a, "A1")
        _send(env, conversation_a, "A2")

        conversation_b = _create(env, created)["conversation_id"]
        env.orchestrator.scripted["B1"] = _result(content="B1 答", request_id="req-b1")
        _send(env, conversation_b, "B1")

        assert env.orchestrator.contexts[-1] is None  # 新会话：无历史
        assert "A1 答" not in (_db_turns(conversation_b)[0]["content"] or "")

    def test_project_binding_per_conversation(
        self, env: Env, created: list[str]
    ) -> None:
        conversation_a = _create(env, created, project_id=PROJECT_A)[
            "conversation_id"
        ]
        conversation_b = _create(env, created, project_id=PROJECT_B)[
            "conversation_id"
        ]

        env.orchestrator.scripted["A 的问题（提到 project-b）"] = _result(
            content="A 答", request_id="req-a1"
        )
        env.orchestrator.scripted["B 的问题"] = _result(
            content="B 答", request_id="req-b1"
        )

        _send(env, conversation_a, "A 的问题（提到 project-b）")
        _send(env, conversation_b, "B 的问题")

        assert env.factory.project_ids == [PROJECT_A, PROJECT_B]

        # conversation-A 的 project_id 不被消息文本/任何输入改变
        a_row = env.client.get(
            f"/api/conversations/{conversation_a}"
        ).json()
        assert a_row["project_id"] == PROJECT_A


# ============================================================
# 4：Failure semantics E2E
# ============================================================


class TestFailureE2E:
    def test_archived_conversation_rejected_409(
        self, env: Env, created: list[str]
    ) -> None:
        conversation_id = _create(env, created)["conversation_id"]
        archived = env.client.post(
            f"/api/conversations/{conversation_id}/archive"
        )
        assert archived.status_code == 200

        turns_before = _count_turns(conversation_id)
        response = _send(env, conversation_id, "归档后不应执行")

        assert response.status_code == 409
        assert env.orchestrator.calls == []  # AI = 0
        assert _count_turns(conversation_id) == turns_before  # DB 不增加

    def test_ai_failure_keeps_user_turn_and_next_turn_sees_it(
        self, env: Env, created: list[str]
    ) -> None:
        conversation_id = _create(env, created)["conversation_id"]
        env.orchestrator.error = AIOrchestratorExecutionError(
            "boom sk-secret postgresql://user:pw@host/db"
        )

        failed = _send(env, conversation_id, "会失败的问题")

        assert failed.status_code == 500
        assert "sk-secret" not in failed.text
        assert "postgresql://" not in failed.text
        turns = _db_turns(conversation_id)
        assert [t["role"] for t in turns] == ["USER"]
        assert turns[0]["assistant_request_id"] is None

        # 失败 USER 被保留 → 下一次正常请求的 context 能看到它
        env.orchestrator.error = None
        env.orchestrator.scripted["正常问题"] = _result(
            content="正常回答", request_id="req-after-failure"
        )
        ok = _send(env, conversation_id, "正常问题")

        assert ok.status_code == 200
        assert env.orchestrator.contexts[-1] == "user: 会失败的问题"

    def test_history_read_failure_skips_ai_and_keeps_user_turn(
        self, env: Env, created: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conversation_id = _create(env, created)["conversation_id"]

        def _boom(conversation_id: str) -> None:  # pragma: no cover - 注入
            raise ConversationRepositoryError("injected history read failure")

        monkeypatch.setattr(env.service.conversation_service, "list_turns", _boom)

        response = _send(env, conversation_id, "历史读会失败")

        assert response.status_code == 500
        turns = _db_turns(conversation_id)
        assert [t["role"] for t in turns] == ["USER"]  # USER 保留
        assert env.orchestrator.calls == []  # AI = 0


# ============================================================
# 5：Residue / Observability / Security
# ============================================================


class TestResidueAndSecurity:
    def test_cleanup_is_scoped_to_created_ids(
        self, env: Env, created: list[str]
    ) -> None:
        conversation_id = _create(env, created)["conversation_id"]
        env.orchestrator.scripted["清理用问题"] = _result(
            content="清理用回答", request_id="req-cleanup"
        )
        _send(env, conversation_id, "清理用问题")
        assert _count_turns(conversation_id) == 2

        before = _count(_CONVERSATION_TABLE)
        _delete_conversations([conversation_id])
        created.remove(conversation_id)  # 已删除，避免二次删除

        assert _count(_CONVERSATION_TABLE) == before - 1
        assert _count_turns(conversation_id) == 0  # FK CASCADE 删除 turns

    def test_observability_tables_unchanged(
        self, env: Env, created: list[str], observability_before: dict[str, int]
    ) -> None:
        conversation_id = _create(env, created)["conversation_id"]
        env.orchestrator.scripted["观测隔离问题"] = _result(
            content="观测隔离回答", request_id="req-obs"
        )
        _send(env, conversation_id, "观测隔离问题")

        after = {table: _count(table) for table in _OBSERVABILITY_TABLES}
        assert after == observability_before

    def test_responses_do_not_leak_internals(
        self, env: Env, created: list[str]
    ) -> None:
        conversation_id = _create(env, created)["conversation_id"]
        env.orchestrator.scripted["安全检查问题"] = _result(
            content="安全检查回答", request_id="req-sec"
        )

        response = _send(env, conversation_id, "安全检查问题")
        listed = env.client.get(
            f"/api/conversations/{conversation_id}/messages"
        )

        assert response.status_code == 200
        for body in (response.text, listed.text):
            for sentinel in (
                "postgresql://",
                "DATABASE_URL",
                "password",
                "Authorization",
                "Traceback",
                "sqlalchemy",
                "Session",
                "sk-",
            ):
                assert sentinel not in body, sentinel


__all__ = [
    "FakeOrchestrator",
    "FakeOrchestratorFactory",
    "TestCreateE2E",
    "TestMultiTurnE2E",
    "TestIsolationE2E",
    "TestFailureE2E",
    "TestResidueAndSecurity",
]
