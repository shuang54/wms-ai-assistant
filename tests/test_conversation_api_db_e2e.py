"""Conversation API 真实 PostgreSQL E2E（Phase 4.1 Step 9）。

链路（**全真实，不使用 Fake Service**）：

```text
FastAPI TestClient
    ↓
/api/conversations*
    ↓
ConversationService（真实）
    ↓
ConversationRepository（真实）
    ↓
真实 PostgreSQL（ai_ops.conversation / ai_ops.conversation_turn）
```

隔离与复用（**不新建 DB 基础设施**）：

    * 门控：``RUN_DB_TESTS=1``（项目既有机制；离线 → SKIP）；
    * 建表：module fixture 调用 ``init_db()``（幂等；``create_all``，不是 migration）；
    * Session：复用 ``backend.app.db.session.get_session_factory()``；
    * 清理：每个用例按 ``conversation_id`` 精确 DELETE（FK CASCADE 连带 turns），
      **不使用 TRUNCATE**；
    * 残留：module fixture 断言 conversation / conversation_turn 计数恢复；
    * 观测隔离：module fixture 断言 4 张 observability 表计数**不变**
      （不得因 Conversation 测试被级联删除）。

注意：本 Step 不新增 message POST API；需要真实 Turn 数据时在 **fixture/setup**
中使用既有 ``ConversationService.append_turn()``，最终验证仍走 HTTP。
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.api import conversations as conversations_module
from backend.app.db.conversation_repository import ConversationRepositoryError
from backend.app.db.session import get_session_factory
from backend.app.main import app
from backend.app.services.conversation_service import (
    ConversationArchivedError,
    ConversationService,
)

_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {"1", "true", "yes", "on"}

pytestmark = pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable PostgreSQL API E2E tests",
)

#: 测试专用 project_id（明显标识，便于人工识别）。
TEST_PROJECT_ID = "phase-4-1-step-9-test"

_CONVERSATION_TABLE = "ai_ops.conversation"
_TURN_TABLE = "ai_ops.conversation_turn"

#: 不得被 Conversation 测试影响的观测表。
_OBSERVABILITY_TABLES: tuple[str, ...] = (
    "ai_ops.llm_usage_record",
    "ai_ops.tool_execution_record",
    "ai_ops.rag_execution_record",
    "ai_ops.assistant_outcome_record",
)

_CONVERSATION_COLUMNS = "conversation_id, project_id, created_at, updated_at, status"
_TURN_COLUMNS = (
    "turn_id, conversation_id, role, content, assistant_request_id, created_at"
)


# ============================================================
# Fixtures / Helpers
# ============================================================


@pytest.fixture(scope="module", autouse=True)
def _ensure_tables() -> None:
    """幂等建表（create_all；本 Step 无 migration）。"""
    from backend.app.db import init_db as init_db_module

    init_db_module.init_db()


@pytest.fixture(scope="module", autouse=True)
def _residue_guard() -> Iterator[None]:
    """残留守卫：会话级计数前后一致（含观测隔离）。"""
    before_conversations = _count(_CONVERSATION_TABLE)
    before_turns = _count(_TURN_TABLE)
    before_observability = {t: _count(t) for t in _OBSERVABILITY_TABLES}

    yield

    assert _count(_CONVERSATION_TABLE) == before_conversations, "conversation 残留"
    assert _count(_TURN_TABLE) == before_turns, "conversation_turn 残留"
    after_observability = {t: _count(t) for t in _OBSERVABILITY_TABLES}
    assert after_observability == before_observability, "observability 不得被清理"


@pytest.fixture()
def created() -> Iterator[list[str]]:
    """收集本用例创建的 conversation_id；结束时精确删除。"""
    ids: list[str] = []
    yield ids
    _delete_conversations(ids)


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def service() -> ConversationService:
    """真实 Service（仅用于 fixture/setup 写入 Turn；验证仍走 HTTP）。"""
    return ConversationService()


def _session_factory() -> Any:
    factory = get_session_factory()
    assert factory is not None
    return factory


def _count(table: str) -> int:
    factory = _session_factory()
    with factory() as session:
        return int(session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())


def _delete_conversations(conversation_ids: list[str]) -> None:
    if not conversation_ids:
        return
    factory = _session_factory()
    with factory() as session, session.begin():
        for conversation_id in conversation_ids:
            session.execute(
                text(f"DELETE FROM {_CONVERSATION_TABLE} WHERE conversation_id = :cid"),
                {"cid": conversation_id},
            )


def _count_turns(conversation_id: str) -> int:
    factory = _session_factory()
    with factory() as session:
        return int(
            session.execute(
                text(f"SELECT COUNT(*) FROM {_TURN_TABLE} WHERE conversation_id = :cid"),
                {"cid": conversation_id},
            ).scalar_one()
        )


def _db_conversation(conversation_id: str) -> dict[str, Any] | None:
    """直接读真实库（验证 HTTP 响应与 DB 一致）。"""
    factory = _session_factory()
    with factory() as session:
        row = session.execute(
            text(
                f"SELECT {_CONVERSATION_COLUMNS} FROM {_CONVERSATION_TABLE} "
                "WHERE conversation_id = :cid"
            ),
            {"cid": conversation_id},
        ).one_or_none()
    return dict(row._mapping) if row is not None else None


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


def _insert_turn_with_fixed_timestamp(
    conversation_id: str, role: str, content: str, request_id: str | None, ts: Any
) -> None:
    """绕过 Service 直接 SQL 插入（用于制造相同 created_at 的 tie 场景）。"""
    factory = _session_factory()
    with factory() as session, session.begin():
        session.execute(
            text(
                f"INSERT INTO {_TURN_TABLE} "
                "(conversation_id, role, content, assistant_request_id, created_at) "
                "VALUES (:cid, :role, :content, :rid, :ts)"
            ),
            {
                "cid": conversation_id,
                "role": role,
                "content": content,
                "rid": request_id,
                "ts": ts,
            },
        )


def _create_via_api(client: TestClient, created: list[str], project_id: str = TEST_PROJECT_ID) -> dict:
    response = client.post("/api/conversations", json={"project_id": project_id})
    assert response.status_code == 200, response.text
    payload = response.json()
    created.append(payload["conversation_id"])
    return payload


# ============================================================
# 1：Create E2E
# ============================================================


class TestCreateE2E:
    def test_create_persists_to_database(self, client: TestClient, created: list[str]) -> None:
        response = client.post(
            "/api/conversations", json={"project_id": TEST_PROJECT_ID}
        )

        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {
            "conversation_id",
            "project_id",
            "status",
            "created_at",
            "updated_at",
        }
        assert payload["status"] == "ACTIVE"

        created.append(payload["conversation_id"])
        row = _db_conversation(payload["conversation_id"])

        assert row is not None, "conversation_id 必须真实存在于 PostgreSQL"
        assert row["project_id"] == TEST_PROJECT_ID
        assert row["status"] == "ACTIVE"
        assert row["created_at"] is not None and row["updated_at"] is not None

    def test_create_ignores_client_supplied_conversation_id(
        self, client: TestClient, created: list[str]
    ) -> None:
        """客户端提供的 conversation_id 不得成为身份（以当前 Pydantic 行为为准）。"""
        response = client.post(
            "/api/conversations",
            json={
                "project_id": TEST_PROJECT_ID,
                "conversation_id": "client-controlled-id",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        created.append(payload["conversation_id"])

        assert payload["conversation_id"] != "client-controlled-id"
        assert _db_conversation("client-controlled-id") is None

    def test_empty_project_id_is_422(self, client: TestClient) -> None:
        response = client.post("/api/conversations", json={"project_id": ""})
        assert response.status_code == 422


# ============================================================
# 2：Get E2E
# ============================================================


class TestGetE2E:
    def test_get_matches_database(self, client: TestClient, created: list[str]) -> None:
        payload = _create_via_api(client, created)

        response = client.get(f"/api/conversations/{payload['conversation_id']}")

        assert response.status_code == 200
        body = response.json()
        row = _db_conversation(payload["conversation_id"])
        assert row is not None
        assert body["conversation_id"] == row["conversation_id"]
        assert body["project_id"] == row["project_id"]
        assert body["status"] == row["status"]
        assert body["created_at"] is not None and body["updated_at"] is not None

    def test_get_is_metadata_only(self, client: TestClient, created: list[str]) -> None:
        payload = _create_via_api(client, created)

        response = client.get(f"/api/conversations/{payload['conversation_id']}")

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {
            "conversation_id",
            "project_id",
            "status",
            "created_at",
            "updated_at",
        }
        for forbidden in (
            "messages",
            "turns",
            "llm_usage",
            "tool_executions",
            "rag_executions",
            "timeline",
        ):
            assert forbidden not in body, forbidden

    def test_get_missing_is_404(self, client: TestClient) -> None:
        response = client.get("/api/conversations/does-not-exist-in-db")
        assert response.status_code == 404


# ============================================================
# 3：Messages E2E
# ============================================================


class TestMessagesE2E:
    def test_messages_empty(self, client: TestClient, created: list[str]) -> None:
        payload = _create_via_api(client, created)

        response = client.get(
            f"/api/conversations/{payload['conversation_id']}/messages"
        )

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"conversation_id", "messages"}
        assert body["messages"] == []
        assert _count_turns(payload["conversation_id"]) == 0

    def test_messages_with_real_turns(
        self, client: TestClient, created: list[str], service: ConversationService
    ) -> None:
        payload = _create_via_api(client, created)
        conversation_id = payload["conversation_id"]

        # fixture/setup：用真实 Service 写入（本 Step 不新增 message POST API）
        service.append_turn(
            conversation_id=conversation_id,
            role="USER",
            content="查询库存",
            assistant_request_id=None,
        )
        service.append_turn(
            conversation_id=conversation_id,
            role="ASSISTANT",
            content="库存查询结果",
            assistant_request_id="assistant-request-step-9",
        )

        response = client.get(f"/api/conversations/{conversation_id}/messages")

        assert response.status_code == 200
        messages = response.json()["messages"]
        assert len(messages) == 2
        assert [m["role"] for m in messages] == ["USER", "ASSISTANT"]
        assert messages[0]["assistant_request_id"] is None
        assert messages[1]["assistant_request_id"] == "assistant-request-step-9"

        db_rows = _db_turns(conversation_id)
        assert len(db_rows) == 2
        for message, row in zip(messages, db_rows):
            assert message["turn_id"] == row["turn_id"]
            assert message["role"] == row["role"]
            assert message["content"] == row["content"]
            assert message["assistant_request_id"] == row["assistant_request_id"]

    def test_messages_missing_conversation_is_404(self, client: TestClient) -> None:
        response = client.get("/api/conversations/does-not-exist-in-db/messages")
        assert response.status_code == 404

    def test_messages_ordering_four_turns(
        self, client: TestClient, created: list[str], service: ConversationService
    ) -> None:
        payload = _create_via_api(client, created)
        conversation_id = payload["conversation_id"]

        service.append_turn(
            conversation_id=conversation_id,
            role="USER",
            content="USER A",
            assistant_request_id=None,
        )
        service.append_turn(
            conversation_id=conversation_id,
            role="ASSISTANT",
            content="ASSISTANT A",
            assistant_request_id="assistant-request-step-9-a",
        )
        service.append_turn(
            conversation_id=conversation_id,
            role="USER",
            content="USER B",
            assistant_request_id=None,
        )
        service.append_turn(
            conversation_id=conversation_id,
            role="ASSISTANT",
            content="ASSISTANT B",
            assistant_request_id="assistant-request-step-9-b",
        )

        response = client.get(f"/api/conversations/{conversation_id}/messages")

        assert response.status_code == 200
        messages = response.json()["messages"]
        assert [m["content"] for m in messages] == [
            "USER A",
            "ASSISTANT A",
            "USER B",
            "ASSISTANT B",
        ]
        assert [m["turn_id"] for m in messages] == sorted(
            m["turn_id"] for m in messages
        )

    def test_messages_same_created_at_tie_break(
        self, client: TestClient, created: list[str]
    ) -> None:
        """相同 created_at 时，turn_id 必须稳定 tie-break。"""
        payload = _create_via_api(client, created)
        conversation_id = payload["conversation_id"]

        factory = _session_factory()
        with factory() as session:
            fixed_ts = session.execute(text("SELECT now()")).scalar_one()
        _insert_turn_with_fixed_timestamp(
            conversation_id, "USER", "tie-1", None, fixed_ts
        )
        _insert_turn_with_fixed_timestamp(
            conversation_id,
            "ASSISTANT",
            "tie-2",
            "assistant-request-step-9-tie",
            fixed_ts,
        )

        response = client.get(f"/api/conversations/{conversation_id}/messages")

        assert response.status_code == 200
        messages = response.json()["messages"]
        assert [m["content"] for m in messages] == ["tie-1", "tie-2"]
        assert messages[0]["turn_id"] < messages[1]["turn_id"]


# ============================================================
# 4：Archive E2E
# ============================================================


class TestArchiveE2E:
    def test_archive_active_becomes_archived(
        self, client: TestClient, created: list[str]
    ) -> None:
        payload = _create_via_api(client, created)

        response = client.post(
            f"/api/conversations/{payload['conversation_id']}/archive"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ARCHIVED"
        row = _db_conversation(payload["conversation_id"])
        assert row is not None
        assert row["status"] == "ARCHIVED"

    def test_archive_is_idempotent(self, client: TestClient, created: list[str]) -> None:
        payload = _create_via_api(client, created)
        conversation_id = payload["conversation_id"]

        first = client.post(f"/api/conversations/{conversation_id}/archive")
        second = client.post(f"/api/conversations/{conversation_id}/archive")

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["status"] == "ARCHIVED"
        # 幂等：第二次不产生不必要更新
        assert second.json()["updated_at"] == first.json()["updated_at"]

    def test_archive_missing_is_404(self, client: TestClient) -> None:
        response = client.post("/api/conversations/does-not-exist-in-db/archive")
        assert response.status_code == 404

    def test_archived_conversation_messages_still_readable(
        self, client: TestClient, created: list[str], service: ConversationService
    ) -> None:
        payload = _create_via_api(client, created)
        conversation_id = payload["conversation_id"]
        service.append_turn(
            conversation_id=conversation_id,
            role="USER",
            content="归档前的问题",
            assistant_request_id=None,
        )
        assert client.post(f"/api/conversations/{conversation_id}/archive").status_code == 200

        response = client.get(f"/api/conversations/{conversation_id}/messages")

        assert response.status_code == 200
        assert [m["content"] for m in response.json()["messages"]] == ["归档前的问题"]


# ============================================================
# 5：Archived Append（Service guard 全链路）
# ============================================================


class TestArchivedAppend:
    def test_append_to_archived_is_rejected_and_writes_nothing(
        self, client: TestClient, created: list[str], service: ConversationService
    ) -> None:
        payload = _create_via_api(client, created)
        conversation_id = payload["conversation_id"]

        # 经 HTTP 归档 → DB 状态真实变为 ARCHIVED
        assert (
            client.post(f"/api/conversations/{conversation_id}/archive").status_code
            == 200
        )
        assert _db_conversation(conversation_id)["status"] == "ARCHIVED"

        with pytest.raises(ConversationArchivedError):
            service.append_turn(
                conversation_id=conversation_id,
                role="USER",
                content="归档后不应写入",
                assistant_request_id=None,
            )

        assert _count_turns(conversation_id) == 0  # write before reject = 0


# ============================================================
# 6：404 / 422 与错误映射
# ============================================================


class TestStatusCodes:
    def test_missing_conversation_endpoints_all_404(self, client: TestClient) -> None:
        for suffix in ("", "/messages", "/archive"):
            path = f"/api/conversations/does-not-exist-in-db{suffix}"
            if suffix == "":
                assert client.get(path).status_code == 404
            elif suffix == "/messages":
                assert client.get(path).status_code == 404
            else:
                assert client.post(path).status_code == 404

    def test_too_long_conversation_id_is_422(self, client: TestClient) -> None:
        response = client.get(f"/api/conversations/{'x' * 129}")
        assert response.status_code == 422

    def test_empty_conversation_id_path_behavior(self, client: TestClient) -> None:
        """空 / 空白 conversation_id 的**真实**行为（以 FastAPI 实际结果为准）。

        * ``GET /api/conversations/`` → 405：路径退化为 ``/api/conversations``，
          该路径只注册了 POST，GET 不被允许（**不是** 404 / 422）；
        * ``GET /api/conversations/%20``（空白 id）→ 422：Path 长度校验通过，
          但 Service 的 ``validate_conversation_id`` 拒绝纯空白
          （``ValueError`` → 422，而不是 404 / 200 + []）。
        """
        assert client.get("/api/conversations/").status_code == 405
        assert client.get("/api/conversations/%20").status_code == 422

    def test_repository_error_maps_to_500_without_leakage(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _FailingService:
            def create_conversation(self, *, project_id: str) -> Any:
                raise ConversationRepositoryError(
                    "connection failed: postgresql://user:pw@host/db"
                )

        monkeypatch.setattr(
            conversations_module,
            "_conversation_service",
            _FailingService(),  # type: ignore[arg-type]
        )

        response = client.post(
            "/api/conversations", json={"project_id": TEST_PROJECT_ID}
        )

        assert response.status_code == 500
        for sentinel in ("postgresql://", "connection failed", "Traceback", "password"):
            assert sentinel not in response.text, sentinel


# ============================================================
# 7：Project Binding / Security / 观测隔离
# ============================================================


class TestBindingSecurityIsolation:
    def test_project_binding_is_immutable(
        self, client: TestClient, created: list[str]
    ) -> None:
        payload = _create_via_api(client, created, project_id="project-a")

        body = client.get(f"/api/conversations/{payload['conversation_id']}").json()

        assert body["project_id"] == "project-a"
        # 不存在 project 切换端点
        paths = app.openapi()["paths"]
        assert not [p for p in paths if "switch-project" in p]

    def test_response_shapes_are_exact_and_secure(
        self, client: TestClient, created: list[str], service: ConversationService
    ) -> None:
        payload = _create_via_api(client, created)
        conversation_id = payload["conversation_id"]
        service.append_turn(
            conversation_id=conversation_id,
            role="ASSISTANT",
            content="库存查询结果",
            assistant_request_id="assistant-request-step-9",
        )

        conversation_body = client.get(
            f"/api/conversations/{conversation_id}"
        ).json()
        messages_body = client.get(
            f"/api/conversations/{conversation_id}/messages"
        ).json()
        message = messages_body["messages"][0]

        assert set(conversation_body) == {
            "conversation_id",
            "project_id",
            "status",
            "created_at",
            "updated_at",
        }
        assert set(messages_body) == {"conversation_id", "messages"}
        assert set(message) == {
            "turn_id",
            "role",
            "content",
            "assistant_request_id",
            "created_at",
        }

        blob = f"{conversation_body}{messages_body}"
        for forbidden in (
            "provider_request_id",
            "api_key",
            "authorization",
            "password",
            "database_url",
            "DATABASE_URL",
            "session",
            "engine",
            "embedding",
        ):
            assert forbidden not in blob.lower(), forbidden

    def test_observability_tables_are_not_cascade_deleted(
        self, client: TestClient, created: list[str]
    ) -> None:
        """清理 Conversation 数据时，4 张观测表计数不得变化。"""
        payload = _create_via_api(client, created)
        before = {table: _count(table) for table in _OBSERVABILITY_TABLES}

        _delete_conversations([payload["conversation_id"]])
        created.remove(payload["conversation_id"])

        after = {table: _count(table) for table in _OBSERVABILITY_TABLES}
        assert after == before
        assert _db_conversation(payload["conversation_id"]) is None
