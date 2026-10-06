"""Phase 4.2 Step 6 —— Message Idempotency 契约测试（**纯离线**）。

隔离策略（沿用 ``tests/test_chat_application_service.py``）：

    * Fake Repository（内存）+ **真实 ConversationService**；
    * Fake Orchestrator（duck-type ``execute(question, *, context=None)``）；
    * Fake ContextBuilder（记录收到的 turns）—— 用于 §23 Context Retry 断言；
    * DB = 0 · Network = 0 · LLM = 0。

覆盖（Step 6 §二十二～§二十七）：

    Header（无 / 合法 / 空白 / 超长）
    首次请求 · duplicate replay · 同 key 异 payload 409
    retry（EMPTY / FAILED / TX2 失败）· retry context 不重复
    no-key 回归 · 跨会话隔离 · ASSISTANT key = NULL
    fingerprint / key 校验 · replay response 形态
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api import conversations as conversations_module
from backend.app.db.conversation_repository import (
    CONVERSATION_STATUS_ACTIVE,
    CONVERSATION_STATUS_ARCHIVED,
    TURN_ROLE_ASSISTANT,
    TURN_ROLE_USER,
    ConversationArchivedRepositoryError,
    ConversationNotFoundRepositoryError,
    ConversationRepositoryError,
    ConversationRow,
    ConversationTurnRow,
    validate_idempotency_key,
)
from backend.app.dto.conversation_api import ConversationMessageResponse
from backend.app.main import app
from backend.app.services.ai_orchestrator_service import (
    AIOrchestrationResult,
    AIOrchestratorExecutionError,
    RouteType,
)
from backend.app.services.chat_application_service import (
    REQUEST_ID_METADATA_KEY,
    ChatApplicationService,
    MessageReplay,
)
from backend.app.services.conversation_context_builder import (
    ConversationContextBuilder,
)
from backend.app.services.conversation_service import (
    IDEMPOTENCY_KEY_IN_FLIGHT,
    IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD,
    ConversationService,
    IdempotencyKeyInFlightError,
    IdempotencyKeyReusedWithDifferentPayloadError,
    message_fingerprint,
)

_DEFAULT_PROJECT_ID = "proj-alpha"
_DEFAULT_REQUEST_ID = "req-0001"
_MAX_KEY_LENGTH = 128


# ============================================================
# Fakes（无 DB / 无网络）
# ============================================================


class FakeConversationRepository:
    """内存版 ConversationRepository（含幂等查询；可注入 append 失败）。"""

    def __init__(self) -> None:
        self._conversations: dict[str, ConversationRow] = {}
        self._turns: dict[str, list[ConversationTurnRow]] = {}
        self._next_turn_id = 1
        self._append_count = 0
        #: 1-based append 序号 → 注入失败（模拟 TX2 写失败）
        self.fail_append_on: set[int] = set()

    # ---------- 测试辅助 ----------

    def seed_conversation(
        self,
        *,
        conversation_id: str = "conv-1",
        project_id: str = _DEFAULT_PROJECT_ID,
        status: str = CONVERSATION_STATUS_ACTIVE,
    ) -> ConversationRow:
        now = datetime.now(timezone.utc)
        row = ConversationRow(
            conversation_id=conversation_id,
            project_id=project_id,
            created_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=10),
            status=status,
        )
        self._conversations[conversation_id] = row
        self._turns.setdefault(conversation_id, [])
        return row

    def turns(self, conversation_id: str = "conv-1") -> tuple[ConversationTurnRow, ...]:
        return tuple(self._turns.get(conversation_id, ()))

    def count(self, role: str, conversation_id: str = "conv-1") -> int:
        return sum(
            1 for turn in self._turns.get(conversation_id, ()) if turn.role == role
        )

    # ---------- ConversationRepository 契约 ----------

    def create(
        self, *, conversation_id: str, project_id: str, status: str
    ) -> ConversationRow:
        now = datetime.now(timezone.utc)
        row = ConversationRow(
            conversation_id=conversation_id,
            project_id=project_id,
            created_at=now,
            updated_at=now,
            status=status,
        )
        self._conversations[conversation_id] = row
        self._turns.setdefault(conversation_id, [])
        return row

    def get_by_conversation_id(
        self, conversation_id: str
    ) -> ConversationRow | None:
        return self._conversations.get(conversation_id)

    def update_status(self, conversation_id: str, status: str) -> ConversationRow | None:
        current = self._conversations.get(conversation_id)
        if current is None:
            return None
        updated = replace(current, status=status, updated_at=datetime.now(timezone.utc))
        self._conversations[conversation_id] = updated
        return updated

    def append_turn(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        assistant_request_id: str | None,
        idempotency_key: str | None = None,
    ) -> ConversationTurnRow:
        self._append_count += 1
        if self._append_count in self.fail_append_on:
            raise ConversationRepositoryError(
                f"injected append failure (call #{self._append_count})"
            )
        current = self._conversations.get(conversation_id)
        if current is None:
            raise ConversationNotFoundRepositoryError("conversation 不存在")
        if current.status == CONVERSATION_STATUS_ARCHIVED:
            raise ConversationArchivedRepositoryError("conversation 已归档")

        now = datetime.now(timezone.utc)
        turn = ConversationTurnRow(
            turn_id=self._next_turn_id,
            conversation_id=conversation_id,
            role=role,
            content=content,
            assistant_request_id=assistant_request_id,
            created_at=now,
            idempotency_key=idempotency_key,
        )
        self._next_turn_id += 1
        self._turns.setdefault(conversation_id, []).append(turn)
        self._conversations[conversation_id] = replace(current, updated_at=now)
        return turn

    def list_turns_by_conversation_id(
        self, conversation_id: str
    ) -> tuple[ConversationTurnRow, ...]:
        if conversation_id not in self._conversations:
            raise ConversationNotFoundRepositoryError("conversation 不存在")
        return tuple(self._turns.get(conversation_id, ()))

    def find_turn_by_idempotency_key(
        self,
        *,
        conversation_id: str,
        idempotency_key: str | None,
        role: str = TURN_ROLE_USER,
    ) -> ConversationTurnRow | None:
        if idempotency_key is None or not idempotency_key.strip():
            return None
        for turn in self._turns.get(conversation_id, ()):
            if turn.idempotency_key == idempotency_key and turn.role == role:
                return turn
        return None

    def find_next_assistant_turn(
        self,
        *,
        conversation_id: str,
        after_turn_id: int,
    ) -> ConversationTurnRow | None:
        for turn in sorted(
            self._turns.get(conversation_id, ()), key=lambda item: item.turn_id
        ):
            if turn.turn_id > after_turn_id and turn.role == TURN_ROLE_ASSISTANT:
                return turn
        return None


class FakeOrchestrator:
    """duck-type AIOrchestrator；记录调用次数（AI 执行次数断言用）。"""

    def __init__(self, *, result: AIOrchestrationResult | None = None) -> None:
        self.result = result
        self.error: Exception | None = None
        self.calls: list[str] = []
        self.contexts: list[str | None] = []

    async def execute(
        self, question: str, *, context: str | None = None
    ) -> AIOrchestrationResult:
        self.calls.append(question)
        self.contexts.append(context)
        if self.error is not None:
            raise self.error
        assert self.result is not None, "FakeOrchestrator 未配置 result"
        return self.result


class FakeContextBuilder:
    """记录收到的 turns；渲染 delegate 到**真实** ContextBuilder。"""

    def __init__(self) -> None:
        self.received: list[tuple[Any, ...]] = []
        self._delegate = ConversationContextBuilder()

    def build_context(self, turns: Any) -> str | None:
        self.received.append(tuple(turns))
        return self._delegate.build_context(turns)


@dataclass
class _Env:
    service: ChatApplicationService
    repository: FakeConversationRepository
    conversations: ConversationService
    orchestrator: FakeOrchestrator
    context_builder: FakeContextBuilder


@pytest.fixture()
def env() -> _Env:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    orchestrator = FakeOrchestrator()
    context_builder = FakeContextBuilder()
    service = ChatApplicationService(
        conversation_service=conversations,
        orchestrator_factory=lambda project_id: orchestrator,
        context_builder=context_builder,
    )
    return _Env(
        service=service,
        repository=repository,
        conversations=conversations,
        orchestrator=orchestrator,
        context_builder=context_builder,
    )


def _result(
    *,
    content: str | None = "回答",
    metadata: dict[str, Any] | None = None,
    route: RouteType = RouteType.TEXT_TO_SQL,
    data: Any = None,
) -> AIOrchestrationResult:
    payload: dict[str, Any] = {REQUEST_ID_METADATA_KEY: _DEFAULT_REQUEST_ID}
    if metadata:
        payload.update(metadata)
    return AIOrchestrationResult(
        route=route, content=content, data=data, metadata=payload
    )


async def _send(
    env: _Env,
    *,
    content: str = "你好",
    idempotency_key: str | None = None,
    conversation_id: str = "conv-1",
) -> Any:
    return await env.service.execute_message(
        conversation_id=conversation_id,
        content=content,
        idempotency_key=idempotency_key,
    )


# ============================================================
# 1. Idempotency-Key 校验（纯函数）
# ============================================================


class TestIdempotencyKeyValidation:
    def test_none_is_no_idempotency(self) -> None:
        assert validate_idempotency_key(None) is None

    def test_blank_is_no_idempotency(self) -> None:
        for blank in ("", "   ", "\t\n"):
            assert validate_idempotency_key(blank) is None

    def test_valid_key_is_returned_unchanged(self) -> None:
        key = "abc-123"
        assert validate_idempotency_key(key) == key

    def test_surrounding_whitespace_is_not_silently_rewritten(self) -> None:
        """不得 trim 后改变客户端 key（Step 6 §二.3）。"""
        assert validate_idempotency_key(" padded ") == " padded "

    def test_max_length_boundary(self) -> None:
        assert len(validate_idempotency_key("k" * _MAX_KEY_LENGTH) or "") == (
            _MAX_KEY_LENGTH
        )
        with pytest.raises(ValueError):
            validate_idempotency_key("k" * (_MAX_KEY_LENGTH + 1))

    def test_non_string_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_idempotency_key(123)  # type: ignore[arg-type]


class TestMessageFingerprint:
    def test_deterministic(self) -> None:
        first = message_fingerprint(conversation_id="c1", content="你好")
        second = message_fingerprint(conversation_id="c1", content="你好")
        assert first == second
        assert len(first) == 64  # sha256 hexdigest

    def test_different_content_differs(self) -> None:
        assert message_fingerprint(
            conversation_id="c1", content="A"
        ) != message_fingerprint(conversation_id="c1", content="B")

    def test_different_conversation_differs(self) -> None:
        assert message_fingerprint(
            conversation_id="c1", content="A"
        ) != message_fingerprint(conversation_id="c2", content="A")


# ============================================================
# 2. 首次请求 / duplicate / conflict
# ============================================================


class TestFirstRequestAndDuplicate:
    async def test_first_request_creates_user_and_assistant(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="最终回答")

        result = await _send(env, content="你好", idempotency_key="key-1")

        assert env.orchestrator.calls == ["你好"]
        assert env.repository.count(TURN_ROLE_USER) == 1
        assert env.repository.count(TURN_ROLE_ASSISTANT) == 1
        assert result.content == "最终回答"

        user_turn = env.repository.turns()[0]
        assistant_turn = env.repository.turns()[1]
        assert user_turn.idempotency_key == "key-1"
        # §十一 硬规则：ASSISTANT Turn 的 idempotency_key 恒为 NULL
        assert assistant_turn.idempotency_key is None

    async def test_duplicate_replays_without_ai(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="最终回答")

        first = await _send(env, content="你好", idempotency_key="key-1")
        second = await _send(env, content="你好", idempotency_key="key-1")

        assert isinstance(first, AIOrchestrationResult)
        # §二十二：AI 调用 = 0 增量 / USER·ASSISTANT 数量不变
        assert env.orchestrator.calls == ["你好"]
        assert env.repository.count(TURN_ROLE_USER) == 1
        assert env.repository.count(TURN_ROLE_ASSISTANT) == 1
        # OD-34：MessageReplay（content + 已持久化 request_id）
        assert isinstance(second, MessageReplay)
        assert second.content == "最终回答"
        assert second.assistant_request_id == _DEFAULT_REQUEST_ID

    async def test_duplicate_ignores_second_orchestrator_result(self, env: _Env) -> None:
        """重放不得受"第二次 result"影响（AI 根本没执行）。"""
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="第一次")

        await _send(env, content="你好", idempotency_key="key-1")
        env.orchestrator.result = _result(content="第二次")
        second = await _send(env, content="你好", idempotency_key="key-1")

        assert isinstance(second, MessageReplay)
        assert second.content == "第一次"

    async def test_same_key_different_payload_is_conflict(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="第一次")

        await _send(env, content="你好", idempotency_key="key-1")
        with pytest.raises(IdempotencyKeyReusedWithDifferentPayloadError) as excinfo:
            await _send(env, content="换个问题", idempotency_key="key-1")

        assert IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD in str(excinfo.value)
        # 冲突：AI = 0 增量 · 不创建第二条 USER
        assert env.orchestrator.calls == ["你好"]
        assert env.repository.count(TURN_ROLE_USER) == 1

    async def test_same_content_different_key_is_independent(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="回答")

        await _send(env, content="你好", idempotency_key="key-1")
        second = await _send(env, content="你好", idempotency_key="key-2")

        assert isinstance(second, AIOrchestrationResult)
        assert env.orchestrator.calls == ["你好", "你好"]
        assert env.repository.count(TURN_ROLE_USER) == 2

    async def test_no_key_behaviour_is_unchanged(self, env: _Env) -> None:
        """§二十五：无 Idempotency-Key → 历史行为（两次独立 USER Turn）。"""
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="回答")

        first = await _send(env, content="你好")
        second = await _send(env, content="你好")

        assert isinstance(first, AIOrchestrationResult)
        assert isinstance(second, AIOrchestrationResult)
        assert env.repository.count(TURN_ROLE_USER) == 2
        assert env.repository.count(TURN_ROLE_ASSISTANT) == 2
        assert all(turn.idempotency_key is None for turn in env.repository.turns())

    async def test_blank_key_is_treated_as_no_key(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="回答")

        await _send(env, content="你好", idempotency_key="   ")
        await _send(env, content="你好", idempotency_key="   ")

        assert env.repository.count(TURN_ROLE_USER) == 2
        assert all(turn.idempotency_key is None for turn in env.repository.turns())

    async def test_cross_conversation_isolation(self, env: _Env) -> None:
        """§二十六：UNIQUE(conversation_id, idempotency_key)，非 UNIQUE(key)。"""
        env.repository.seed_conversation(conversation_id="conv-a")
        env.repository.seed_conversation(conversation_id="conv-b")
        env.orchestrator.result = _result(content="回答")

        result_a = await _send(env, content="你好", idempotency_key="same-key", conversation_id="conv-a")
        result_b = await _send(env, content="你好", idempotency_key="same-key", conversation_id="conv-b")

        assert isinstance(result_a, AIOrchestrationResult)
        assert isinstance(result_b, AIOrchestrationResult)
        assert env.repository.count(TURN_ROLE_USER, "conv-a") == 1
        assert env.repository.count(TURN_ROLE_USER, "conv-b") == 1

    async def test_archived_conversation_still_rejected_before_write(
        self, env: _Env
    ) -> None:
        from backend.app.services.conversation_service import (
            ConversationArchivedError,
        )

        env.repository.seed_conversation(status=CONVERSATION_STATUS_ARCHIVED)
        env.orchestrator.result = _result()

        with pytest.raises(ConversationArchivedError):
            await _send(env, content="你好", idempotency_key="key-1")
        assert env.orchestrator.calls == []


# ============================================================
# 3. Retry（EMPTY / FAILED / TX2 失败）—— OD-26
# ============================================================


class TestRetrySemantics:
    async def test_empty_then_retry_reuses_user_turn(self, env: _Env) -> None:
        """EMPTY = NOT completed（OD-26）⇒ 同 key 可重试。"""
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content=None)  # EMPTY

        first = await _send(env, content="你好", idempotency_key="key-1")
        assert isinstance(first, AIOrchestrationResult)
        assert env.repository.count(TURN_ROLE_ASSISTANT) == 0

        env.orchestrator.result = _result(content="这次有了")
        second = await _send(env, content="你好", idempotency_key="key-1")

        assert isinstance(second, AIOrchestrationResult)
        assert env.orchestrator.calls == ["你好", "你好"]
        # 复用既有 USER Turn（不创建第二条）
        assert env.repository.count(TURN_ROLE_USER) == 1
        assert env.repository.count(TURN_ROLE_ASSISTANT) == 1
        assert env.repository.turns()[-1].content == "这次有了"

    async def test_failed_then_retry_reuses_user_turn(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.error = AIOrchestratorExecutionError("provider down")

        with pytest.raises(AIOrchestratorExecutionError):
            await _send(env, content="你好", idempotency_key="key-1")

        env.orchestrator.error = None
        env.orchestrator.result = _result(content="恢复")
        await _send(env, content="你好", idempotency_key="key-1")

        assert env.repository.count(TURN_ROLE_USER) == 1
        assert env.repository.count(TURN_ROLE_ASSISTANT) == 1

    async def test_tx2_failure_then_retry(self, env: _Env) -> None:
        """ASSISTANT Turn 写失败（TX2）⇒ NOT completed ⇒ 可重试。"""
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="回答")
        env.repository.fail_append_on = {2}  # 第 2 次 append = ASSISTANT

        with pytest.raises(ConversationRepositoryError):
            await _send(env, content="你好", idempotency_key="key-1")

        assert env.repository.count(TURN_ROLE_USER) == 1
        assert env.repository.count(TURN_ROLE_ASSISTANT) == 0

        env.repository.fail_append_on = set()
        await _send(env, content="你好", idempotency_key="key-1")

        assert env.repository.count(TURN_ROLE_USER) == 1
        assert env.repository.count(TURN_ROLE_ASSISTANT) == 1
        assert env.orchestrator.calls == ["你好", "你好"]

    async def test_retry_context_does_not_duplicate_user_turn(self, env: _Env) -> None:
        """§二十三（强制）：retry 不得把既有 USER Turn 再注入 context。"""
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content=None)  # 首次 EMPTY

        await _send(env, content="你好", idempotency_key="key-1")
        env.orchestrator.result = _result(content="回答")
        await _send(env, content="你好", idempotency_key="key-1")

        # 两次请求的 previous turns 都不包含当前 USER Turn（首次也为 ()）
        assert env.context_builder.received == [(), ()]

    async def test_retry_after_second_turn_keeps_prior_history(self, env: _Env) -> None:
        """多轮下 retry：历史（上一轮 USER/ASSISTANT）仍在，当前 USER 不重复。"""
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="A1")
        await _send(env, content="U1", idempotency_key="k1")

        env.orchestrator.result = _result(content=None)  # U2 EMPTY
        await _send(env, content="U2", idempotency_key="k2")

        env.orchestrator.result = _result(content="A2")
        await _send(env, content="U2", idempotency_key="k2")

        received = env.context_builder.received
        # 第 3 次（retry）的 history = [U1, A1]（U2 被精确排除）
        assert [turn.content for turn in received[-1]] == ["U1", "A1"]
        assert env.repository.count(TURN_ROLE_USER) == 2


# ============================================================
# 4. Duplicate Response Contract（OD-34）
# ============================================================


class TestDuplicateResponseContract:
    def test_replay_maps_to_null_route_and_null_data(self) -> None:
        replay = MessageReplay(content="已持久化回答", assistant_request_id="req-1")

        response = conversations_module._to_message_response(replay)

        assert isinstance(response, ConversationMessageResponse)
        assert response.route is None  # 禁止 "replay" / "unknown" / 推断值
        assert response.data is None
        assert response.content == "已持久化回答"

    def test_replay_metadata_only_request_id_and_marker(self) -> None:
        replay = MessageReplay(content="回答", assistant_request_id="req-1")

        response = conversations_module._to_message_response(replay)

        assert response.metadata == {
            "idempotent_replay": True,
            "request_id": "req-1",
        }
        for forbidden in ("turn_id", "conversation_id", "idempotency_key", "outcome"):
            assert forbidden not in response.metadata, forbidden

    def test_replay_without_request_id_omits_it(self) -> None:
        replay = MessageReplay(content="回答", assistant_request_id=None)
        response = conversations_module._to_message_response(replay)
        assert response.metadata == {"idempotent_replay": True}

    def test_response_field_names_unchanged(self) -> None:
        assert list(ConversationMessageResponse.model_fields) == [
            "route",
            "content",
            "data",
            "metadata",
        ]

    def test_route_is_optional_for_replay(self) -> None:
        route_field = ConversationMessageResponse.model_fields["route"]
        assert route_field.is_required() is False


# ============================================================
# 5. API Header Contract（离线 TestClient；Fake Application Service）
# ============================================================


class FakeChatApplicationService:
    """ChatApplicationService 替身：只记录接收到的 idempotency_key。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = _result(content="回答")

    async def execute_message(
        self,
        *,
        conversation_id: str,
        content: str,
        idempotency_key: str | None = None,
    ) -> AIOrchestrationResult:
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "content": content,
                "idempotency_key": idempotency_key,
            }
        )
        return self.result


@pytest.fixture()
def fake_service(monkeypatch: pytest.MonkeyPatch) -> FakeChatApplicationService:
    service = FakeChatApplicationService()
    monkeypatch.setattr(
        conversations_module,
        "_chat_application_service",
        service,  # type: ignore[arg-type]
    )
    return service


@pytest.fixture()
def api_client(fake_service: FakeChatApplicationService) -> TestClient:
    return TestClient(app)


def _post(
    client: TestClient,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    conversation_id: str = "conv-1",
) -> Any:
    return client.post(
        f"/api/conversations/{conversation_id}/messages",
        json=body if body is not None else {"content": "你好"},
        headers=headers,
    )


class TestApiHeaderContract:
    def test_no_header(self, api_client: TestClient, fake_service: FakeChatApplicationService) -> None:
        response = _post(api_client)

        assert response.status_code == 200
        assert fake_service.calls[0]["idempotency_key"] is None

    def test_valid_header(self, api_client: TestClient, fake_service: FakeChatApplicationService) -> None:
        response = _post(api_client, headers={"Idempotency-Key": "client-key-1"})

        assert response.status_code == 200
        assert fake_service.calls[0]["idempotency_key"] == "client-key-1"

    def test_blank_header_is_no_idempotency(
        self, api_client: TestClient, fake_service: FakeChatApplicationService
    ) -> None:
        response = _post(api_client, headers={"Idempotency-Key": "   "})

        assert response.status_code == 200
        assert fake_service.calls[0]["idempotency_key"] == "   "

    def test_header_over_max_length_is_422(self, api_client: TestClient) -> None:
        response = _post(
            api_client, headers={"Idempotency-Key": "k" * (_MAX_KEY_LENGTH + 1)}
        )

        assert response.status_code == 422

    def test_body_never_carries_idempotency_key(
        self, api_client: TestClient, fake_service: FakeChatApplicationService
    ) -> None:
        response = _post(
            api_client,
            body={"content": "你好", "idempotency_key": "evil"},
            headers={"Idempotency-Key": "real-key"},
        )

        assert response.status_code == 200
        # DTO 不含该字段 ⇒ Body 值被忽略，只取 Header
        assert fake_service.calls[0]["idempotency_key"] == "real-key"


class TestApiConflictMapping:
    def test_payload_conflict_is_409(
        self, api_client: TestClient, fake_service: FakeChatApplicationService
    ) -> None:
        from backend.app.services.conversation_service import (
            IdempotencyKeyReusedWithDifferentPayloadError,
        )

        async def _raise(**kwargs: Any) -> Any:
            raise IdempotencyKeyReusedWithDifferentPayloadError(
                IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD
            )

        fake_service.execute_message = _raise  # type: ignore[assignment]
        response = _post(api_client, headers={"Idempotency-Key": "k"})

        assert response.status_code == 409
        assert IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD in response.json()["detail"]

    def test_in_flight_conflict_is_409(
        self, api_client: TestClient, fake_service: FakeChatApplicationService
    ) -> None:
        async def _raise(**kwargs: Any) -> Any:
            raise IdempotencyKeyInFlightError(IDEMPOTENCY_KEY_IN_FLIGHT)

        fake_service.execute_message = _raise  # type: ignore[assignment]
        response = _post(api_client, headers={"Idempotency-Key": "k"})

        assert response.status_code == 409
        assert IDEMPOTENCY_KEY_IN_FLIGHT in response.json()["detail"]


__all__ = [
    "TestIdempotencyKeyValidation",
    "TestMessageFingerprint",
    "TestFirstRequestAndDuplicate",
    "TestRetrySemantics",
    "TestDuplicateResponseContract",
    "TestApiHeaderContract",
    "TestApiConflictMapping",
]
