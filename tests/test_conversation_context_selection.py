"""Phase 4.2 Step 7E —— Conversation Context Selection 测试（离线 + DB-gated）。

覆盖 Step 7C 冻结的回归契约 T-7C-1 … T-7C-12 与 Step 7E 附加用例：

    T-7C-1  current turn 永不进入 window
    T-7C-2  retry 不重复 current turn
    T-7C-3  duplicate replay 不执行 selection / builder / AI
    T-7C-4  历史排序稳定（Selection 不重排）
    T-7C-5  window 确定性
    T-7C-6  turn 上限 20 / 字符上限 12000（最新 turn 豁免）
    T-7C-7  EMPTY（无 ASSISTANT 的 USER）保留
    T-7C-8  异常 / FAILED 语义：USER 保留，context 不含任何内部状态
    T-7C-9  oversized：最新 turn 完整保留；更旧 oversized → 停止（连续后缀）
    T-7C-10 安全边界与内部标识符不入 context
    T-7C-11 USER-anchored window
    T-7C-12 空历史 ⇒ context = None

纯离线部分：DB = 0 · Network = 0 · LLM = 0（Fake Repository + Fake Orchestrator）。
DB-gated 部分：真实 PostgreSQL（RUN_DB_TESTS=1），自建数据 + 按 conversation_id 精确清理。
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import text

from backend.app.db.conversation_repository import (
    CONVERSATION_STATUS_ACTIVE,
    TURN_ROLE_ASSISTANT,
    TURN_ROLE_USER,
    ConversationArchivedRepositoryError,
    ConversationNotFoundRepositoryError,
    ConversationRow,
    ConversationTurnRow,
)
from backend.app.db.session import get_session_factory
from backend.app.services import chat_application_service as chat_module
from backend.app.services.ai_orchestrator_service import (
    AIOrchestrationResult,
    AIOrchestratorExecutionError,
    RouteType,
)
from backend.app.services.chat_application_service import (
    ChatApplicationService,
    MessageReplay,
)
from backend.app.services.conversation_context_builder import build_context
from backend.app.services.conversation_context_selection_service import (
    ASSISTANT_TURN_ROLE,
    DEFAULT_HISTORY_CHARS,
    DEFAULT_HISTORY_TURNS,
    USER_TURN_ROLE,
    ConversationContextSelectionService,
    select_history,
)
from backend.app.services.conversation_service import (
    ConversationService,
    ConversationTurnView,
)

_BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {"1", "true", "yes", "on"}
_DB_GATED = pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable PostgreSQL integration tests",
)


# ============================================================
# 构造器（离线）
# ============================================================


def _turn(
    index: int,
    role: str,
    content: str,
    *,
    conversation_id: str = "conv-1",
    request_id: str | None = None,
) -> ConversationTurnView:
    return ConversationTurnView(
        turn_id=index,
        conversation_id=conversation_id,
        role=role,
        content=content,
        assistant_request_id=None if role == USER_TURN_ROLE else request_id,
        created_at=_BASE_TIME + timedelta(seconds=index),
    )


def _user(index: int, content: str = "u") -> ConversationTurnView:
    return _turn(index, USER_TURN_ROLE, content)


def _assistant(index: int, content: str = "a") -> ConversationTurnView:
    return _turn(index, ASSISTANT_TURN_ROLE, content, request_id=f"req-{index}")


def _chars(turns: tuple[ConversationTurnView, ...]) -> int:
    return sum(len(turn.content) for turn in turns)


def _contents(turns: tuple[ConversationTurnView, ...]) -> list[str]:
    return [turn.content for turn in turns]


class _PoisonedTurn:
    """只暴露 turn_id / role / content；其余属性访问即失败（证明不读其它字段）。"""

    def __init__(self, turn_id: int, role: str, content: str) -> None:
        self._values = {"turn_id": turn_id, "role": role, "content": content}

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - 仅在违规读取时触发
        if name in self._values:
            return self._values[name]
        raise AssertionError(f"Selection 不得读取 turn.{name}")


# ============================================================
# 1. 窗口上限（T-7C-6 / T-7C-7）
# ============================================================


class TestWindowLimits:
    def test_defaults_are_frozen(self) -> None:
        assert DEFAULT_HISTORY_TURNS == 20
        assert DEFAULT_HISTORY_CHARS == 12000

    def test_exactly_20_turns_all_fit(self) -> None:
        turns = tuple(
            _user(i + 1, "x") if i % 2 == 0 else _assistant(i + 1, "y")
            for i in range(20)
        )

        selected = select_history(turns)

        assert len(selected) == 20
        assert selected == turns

    def test_21_turns_keeps_latest_20(self) -> None:
        turns = tuple(
            _user(i + 1, f"m{i + 1}") if i % 2 == 0 else _assistant(i + 1, "y")
            for i in range(21)
        )

        selected = select_history(turns)

        # 先取最新 20（turn 2..21），其首条为 ASSISTANT 且窗口内仍有 USER
        # ⇒ USER-anchor 丢弃它（19 条，均 ≤ 20）
        assert len(selected) == 19
        assert selected == turns[2:]
        assert selected[0].content == "m3"

    def test_exactly_12000_chars_fit(self) -> None:
        turns = (_user(1, "u" * 6000), _assistant(2, "a" * 6000))

        selected = select_history(turns)

        assert len(selected) == 2
        assert _chars(selected) == 12000

    def test_12001_chars_stops(self) -> None:
        turns = (_user(1, "u" * 6001), _assistant(2, "a" * 6000))

        selected = select_history(turns)

        # 最新 A2(6000) 豁免入选；再加 U1(6001) = 12001 > 12000 ⇒ 停止
        assert _contents(selected) == ["a" * 6000]
        assert "u" * 6001 not in _contents(selected)  # 更旧超限 turn 被排除（未截断、未跳过）

    def test_char_cap_applies_to_content_not_formatted(self) -> None:
        turns = (_user(1, "u" * 12000),)

        selected = select_history(turns)
        context = build_context(selected)

        assert len(selected[0].content) == 12000
        # 格式化含 "user: " 前缀 ⇒ 长度 > 12000（上限口径 = content 字符）
        assert len(context or "") > 12000
        assert (context or "") == f"user: {'u' * 12000}"

    def test_turn_cap_does_not_use_pairs(self) -> None:
        """单位 = turn（不是 pair / message pair）。"""
        turns = tuple(
            _user(i + 1, "x") if i % 2 == 0 else _assistant(i + 1, "y")
            for i in range(21)
        )

        selected = select_history(turns)

        # 若按 pair 选择，结果必然是偶数条 —— 19（奇数）证明不是
        assert len(selected) == 19
        assert selected[0].role == USER_TURN_ROLE
        assert selected[-1].role == USER_TURN_ROLE


# ============================================================
# 2. 连续后缀 / oversized（T-7C-9）
# ============================================================


class TestContiguousSuffixAndOversized:
    def test_older_oversized_turn_stops_selection(self) -> None:
        turns = (
            _user(1, "u" * 13000),
            _assistant(2, "a" * 10),
            _user(3, "u2"),
            _assistant(4, "a2"),
        )

        selected = select_history(turns)

        # 最新→最旧：A2(2) → U2(2) → A1(10)【都在预算内】→ U1(13000) 超预算 ⇒ 停止
        # ⇒ 连续后缀 = [A1, U2, A2] → USER-anchor 丢弃 A1 ⇒ [U2, A2]
        assert _contents(selected) == ["u2", "a2"]
        assert _contents(selected) == _contents(turns[2:])

    def test_no_holes_in_selected_suffix(self) -> None:
        turns = (
            _user(1, "u" * 9000),
            _assistant(2, "a" * 9000),
            _user(3, "u3"),
            _assistant(4, "a4"),
        )

        selected = select_history(turns)

        indices = [turn.turn_id for turn in selected]
        assert indices == [3, 4]  # 连续（不得出现 1/3/4 这类空洞）

    def test_newest_oversized_turn_is_kept_fully(self) -> None:
        turns = (_user(1, "u" * 13000),)

        selected = select_history(turns)

        assert len(selected) == 1
        assert selected[0].content == "u" * 13000  # 未截断、未摘要
        assert selected[0].content is turns[0].content  # 同一对象（未复制/未改写）

    def test_newest_assistant_oversized_is_kept(self) -> None:
        turns = (_user(1, "u"), _assistant(2, "a" * 13000))

        selected = select_history(turns)

        assert _contents(selected) == ["a" * 13000]


# ============================================================
# 3. USER-anchored（T-7C-11）
# ============================================================


class TestUserAnchored:
    def test_drops_leading_assistant_when_user_remains(self) -> None:
        turns = (_user(1, "u1"), _assistant(2, "a1"), _user(3, "u2"), _assistant(4, "a2"))

        selected = select_history(turns, turn_cap=3)

        # turn_cap=3 ⇒ [A1, U2, A2] → 丢弃前导 A1 ⇒ [U2, A2]
        assert _contents(selected) == ["u2", "a2"]

    def test_no_alignment_when_window_has_no_user(self) -> None:
        turns = (_assistant(1, "a1"),)

        selected = select_history(turns)

        assert _contents(selected) == ["a1"]  # 丢弃会清空窗口 ⇒ 不对齐

    def test_does_not_align_when_first_is_user(self) -> None:
        turns = (_user(1, "u1"), _assistant(2, "a1"))

        selected = select_history(turns)

        assert _contents(selected) == ["u1", "a1"]


# ============================================================
# 4. current turn / ordering / determinism（T-7C-1 / 4 / 5）
# ============================================================


class TestCurrentTurnOrderingDeterminism:
    def test_current_turn_is_excluded(self) -> None:
        turns = (_user(1, "u1"), _assistant(2, "a1"), _user(3, "u2"))

        selected = select_history(turns, current_turn_id=3)

        assert _contents(selected) == ["u1", "a1"]

    def test_exclusion_is_idempotent_when_absent(self) -> None:
        turns = (_user(1, "u1"), _assistant(2, "a1"))

        assert select_history(turns, current_turn_id=99) == select_history(turns)

    def test_selection_does_not_reorder(self) -> None:
        """输入顺序即输出顺序（Repository 保证 created_at ASC, turn_id ASC）。"""
        turns = (_user(5, "z"), _assistant(6, "y"), _user(7, "x"))

        selected = select_history(turns)

        assert _contents(selected) == ["z", "y", "x"]

    def test_same_timestamp_order_is_input_order(self) -> None:
        same_time = _BASE_TIME
        first = replace(_user(1, "first"), created_at=same_time)
        second = replace(_user(2, "second"), created_at=same_time)

        selected = select_history((first, second))

        assert _contents(selected) == ["first", "second"]

    def test_determinism(self) -> None:
        turns = tuple(
            _user(i + 1, f"m{i}") if i % 2 == 0 else _assistant(i + 1, f"r{i}")
            for i in range(25)
        )

        first = select_history(turns)
        second = select_history(turns)

        assert first == second
        assert build_context(first) == build_context(second)

    def test_service_wrapper_matches_function(self) -> None:
        turns = (_user(1, "u1"), _assistant(2, "a1"))

        assert ConversationContextSelectionService().select(turns) == select_history(
            turns
        )


# ============================================================
# 5. 输入校验 / 隔离（不读其它字段 / 不改写内容）
# ============================================================


class TestInputBoundary:
    def test_only_three_fields_are_read(self) -> None:
        turns = (
            _PoisonedTurn(1, USER_TURN_ROLE, "u1"),
            _PoisonedTurn(2, ASSISTANT_TURN_ROLE, "a1"),
        )

        selected = select_history(turns)

        assert [turn.content for turn in selected] == ["u1", "a1"]

    def test_content_is_not_rewritten(self) -> None:
        raw = "  保留 空白\n与换行  📦  "
        selected = select_history((_user(1, raw),))

        assert selected[0].content == raw
        assert build_context(selected) == f"user: {raw}"

    def test_unicode_counts_python_characters(self) -> None:
        # 6000 个 emoji（UTF-8 下 4 bytes 每个）+ 6000 个汉字 = 12000 字符 ⇒ 恰好可容纳
        turns = (_user(1, "📦" * 6000), _assistant(2, "仓" * 6000))

        selected = select_history(turns)

        assert len(selected) == 2
        assert _chars(selected) == 12000
        assert len(selected[0].content.encode("utf-8")) == 24000  # 字节数 ≠ 口径

    def test_empty_content_is_preserved(self) -> None:
        turns = (_user(1, ""), _assistant(2, "a1"))

        selected = select_history(turns)

        assert _contents(selected) == ["", "a1"]

    def test_empty_history_returns_empty_tuple(self) -> None:
        assert select_history(()) == ()

    def test_invalid_caps_rejected(self) -> None:
        turns = (_user(1, "u1"),)
        for bad in (0, -1, "20", 1.5, True):
            with pytest.raises(ValueError):
                select_history(turns, turn_cap=bad)  # type: ignore[arg-type]

    def test_unknown_role_rejected(self) -> None:
        with pytest.raises(ValueError):
            select_history((_turn(1, "SYSTEM", "x"),))


# ============================================================
# 6. 内部标识符不入 context（T-7C-10 + Step 6 边界）
# ============================================================


class TestNoInternalLeak:
    def test_identifiers_never_enter_context(self) -> None:
        turns = (
            _turn(
                7,
                USER_TURN_ROLE,
                "查询库存",
                conversation_id="conv-secret-001",
                request_id=None,
            ),
            _turn(
                8,
                ASSISTANT_TURN_ROLE,
                "库存 10",
                conversation_id="conv-secret-001",
                request_id="req-secret-002",
            ),
        )

        context = build_context(select_history(turns)) or ""

        for forbidden in (
            "conv-secret-001",
            "req-secret-002",
            "turn_id",
            "created_at",
            "idempotency_key",
        ):
            assert forbidden not in context, forbidden

    def test_context_only_contains_role_prefixed_lines(self) -> None:
        turns = (_user(1, "u1"), _assistant(2, "a1"))

        context = build_context(select_history(turns))

        assert context == "user: u1\nassistant: a1"


# ============================================================
# 7. Pipeline 集成（离线；Fake Repository + Fake Orchestrator）
# ============================================================


class FakeConversationRepository:
    """内存版 ConversationRepository（只实现 Service 使用的契约）。"""

    def __init__(self) -> None:
        self._conversations: dict[str, ConversationRow] = {}
        self._turns: dict[str, list[ConversationTurnRow]] = {}
        self._next_turn_id = 1

    def seed_conversation(self, conversation_id: str = "conv-1") -> ConversationRow:
        now = datetime.now(timezone.utc)
        row = ConversationRow(
            conversation_id=conversation_id,
            project_id="vietnam-wms",
            created_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=10),
            status=CONVERSATION_STATUS_ACTIVE,
        )
        self._conversations[conversation_id] = row
        self._turns.setdefault(conversation_id, [])
        return row

    def seed_turn(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        assistant_request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> ConversationTurnRow:
        turn = ConversationTurnRow(
            turn_id=self._next_turn_id,
            conversation_id=conversation_id,
            role=role,
            content=content,
            assistant_request_id=assistant_request_id,
            created_at=datetime.now(timezone.utc) + timedelta(seconds=self._next_turn_id),
            idempotency_key=idempotency_key,
        )
        self._next_turn_id += 1
        self._turns.setdefault(conversation_id, []).append(turn)
        return turn

    def create(
        self, *, conversation_id: str, project_id: str, status: str
    ) -> ConversationRow:
        return self.seed_conversation(conversation_id)

    def get_by_conversation_id(
        self, conversation_id: str
    ) -> ConversationRow | None:
        return self._conversations.get(conversation_id)

    def update_status(self, conversation_id: str, status: str) -> ConversationRow | None:
        current = self._conversations.get(conversation_id)
        if current is None:
            return None
        updated = replace(current, status=status)
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
        current = self._conversations.get(conversation_id)
        if current is None:
            raise ConversationNotFoundRepositoryError("conversation 不存在")
        if current.status != CONVERSATION_STATUS_ACTIVE:
            raise ConversationArchivedRepositoryError("conversation 已归档")
        return self.seed_turn(
            conversation_id=conversation_id,
            role=role,
            content=content,
            assistant_request_id=assistant_request_id,
            idempotency_key=idempotency_key,
        )

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
        self, *, conversation_id: str, after_turn_id: int
    ) -> ConversationTurnRow | None:
        for turn in sorted(
            self._turns.get(conversation_id, ()), key=lambda item: item.turn_id
        ):
            if turn.turn_id > after_turn_id and turn.role == TURN_ROLE_ASSISTANT:
                return turn
        return None


class FakeOrchestrator:
    def __init__(self) -> None:
        self.result: AIOrchestrationResult | None = AIOrchestrationResult(
            route=RouteType.RAG,
            content="回答",
            data=None,
            metadata={"request_id": "req-1"},
        )
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
        assert self.result is not None
        return self.result


@dataclass
class _Env:
    service: ChatApplicationService
    repository: FakeConversationRepository
    orchestrator: FakeOrchestrator


@pytest.fixture()
def env() -> _Env:
    repository = FakeConversationRepository()
    orchestrator = FakeOrchestrator()
    service = ChatApplicationService(
        conversation_service=ConversationService(repository=repository),
        orchestrator_factory=lambda project_id: orchestrator,
    )
    return _Env(service=service, repository=repository, orchestrator=orchestrator)


def _seed_history(
    env: _Env,
    *,
    conversation_id: str = "conv-1",
    turns: int = 25,
) -> None:
    for index in range(turns):
        env.repository.seed_turn(
            conversation_id=conversation_id,
            role=TURN_ROLE_USER if index % 2 == 0 else TURN_ROLE_ASSISTANT,
            content=f"m{index + 1}",
            assistant_request_id=None if index % 2 == 0 else f"req-{index + 1}",
        )


class TestPipelineIntegration:
    async def test_window_applied_in_pipeline(self, env: _Env) -> None:
        env.repository.seed_conversation()
        _seed_history(env, turns=25)

        await env.service.execute_message(conversation_id="conv-1", content="current")

        context = env.orchestrator.contexts[0] or ""
        lines = context.split("\n")
        # 最新 20 条（m6..m25）→ 首条为 ASSISTANT(m6) ⇒ USER-anchor 丢弃 ⇒ 19 行
        assert len(lines) == DEFAULT_HISTORY_TURNS - 1
        expected = [
            f"{'user' if index % 2 == 0 else 'assistant'}: m{index + 1}"
            for index in range(6, 25)
        ]
        assert lines == expected  # m1..m6 被裁掉（窗口 + 对齐）；顺序为 oldest → newest
        assert "current" not in context  # T-7C-1

    async def test_small_history_is_untouched(self, env: _Env) -> None:
        env.repository.seed_conversation()
        _seed_history(env, turns=4)

        await env.service.execute_message(conversation_id="conv-1", content="current")

        assert env.orchestrator.contexts[0] == (
            "user: m1\nassistant: m2\nuser: m3\nassistant: m4"
        )

    async def test_empty_history_context_is_none(self, env: _Env) -> None:
        env.repository.seed_conversation()

        await env.service.execute_message(conversation_id="conv-1", content="first")

        assert env.orchestrator.contexts[0] is None

    async def test_retry_excludes_reused_user_turn(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.repository.seed_turn(
            conversation_id="conv-1", role=TURN_ROLE_USER, content="U1"
        )
        env.repository.seed_turn(
            conversation_id="conv-1",
            role=TURN_ROLE_ASSISTANT,
            content="A1",
            assistant_request_id="req-1",
        )
        # 首次 U2：EMPTY（无 ASSISTANT）→ 保留为已有 USER Turn
        env.orchestrator.result = AIOrchestrationResult(
            route=RouteType.RAG, content=None, data=None, metadata={"request_id": "r"}
        )
        await env.service.execute_message(
            conversation_id="conv-1", content="U2", idempotency_key="k1"
        )
        assert env.orchestrator.contexts[-1] == "user: U1\nassistant: A1"

        # retry（同 key 同 content）⇒ 复用既有 U2，仍不得进入 context
        env.orchestrator.result = AIOrchestrationResult(
            route=RouteType.RAG, content="A2", data=None, metadata={"request_id": "r2"}
        )
        await env.service.execute_message(
            conversation_id="conv-1", content="U2", idempotency_key="k1"
        )

        assert env.orchestrator.contexts[-1] == "user: U1\nassistant: A1"
        assert env.orchestrator.calls == ["U2", "U2"]

    async def test_duplicate_skips_selection(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env.repository.seed_conversation()
        env.repository.seed_turn(
            conversation_id="conv-1",
            role=TURN_ROLE_USER,
            content="U1",
            idempotency_key="k1",
        )
        env.repository.seed_turn(
            conversation_id="conv-1",
            role=TURN_ROLE_ASSISTANT,
            content="A1",
            assistant_request_id="req-1",
        )

        calls: list[tuple[Any, ...]] = []

        def _spy(turns: Any, **kwargs: Any) -> Any:
            calls.append(tuple(turns))
            return select_history(turns, **kwargs)

        monkeypatch.setattr(chat_module, "select_history", _spy)

        result = await env.service.execute_message(
            conversation_id="conv-1", content="U1", idempotency_key="k1"
        )

        assert isinstance(result, MessageReplay)
        assert calls == []  # duplicate ⇒ 不执行 selection
        assert env.orchestrator.calls == []  # AI = 0

    async def test_failed_turn_kept_and_no_internal_state(
        self, env: _Env
    ) -> None:
        env.repository.seed_conversation()
        env.orchestrator.error = AIOrchestratorExecutionError(
            "boom sk-secret postgresql://u:p@h/db"
        )

        with pytest.raises(AIOrchestratorExecutionError):
            await env.service.execute_message(conversation_id="conv-1", content="U1")

        env.orchestrator.error = None
        await env.service.execute_message(conversation_id="conv-1", content="U2")

        context = env.orchestrator.contexts[-1] or ""
        assert context == "user: U1"  # FAILED 的 USER 保留（内容原样）
        for forbidden in ("FAILED", "exception", "sk-secret", "postgresql"):
            assert forbidden not in context

    async def test_empty_user_turn_kept(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = AIOrchestrationResult(
            route=RouteType.RAG, content=None, data=None, metadata={"request_id": "r"}
        )
        await env.service.execute_message(conversation_id="conv-1", content="U1")

        env.orchestrator.result = AIOrchestrationResult(
            route=RouteType.RAG, content="A2", data=None, metadata={"request_id": "r2"}
        )
        await env.service.execute_message(conversation_id="conv-1", content="U2")

        assert env.orchestrator.contexts[-1] == "user: U1"

    async def test_oversized_newest_history_turn_exempt(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.repository.seed_turn(
            conversation_id="conv-1", role=TURN_ROLE_USER, content="u" * 13000
        )

        await env.service.execute_message(conversation_id="conv-1", content="current")

        context = env.orchestrator.contexts[0] or ""
        assert context == f"user: {'u' * 13000}"
        assert len(context) > DEFAULT_HISTORY_CHARS  # 豁免（不截断）
        assert u"u" * 13000 not in context.replace(f"user: {'u' * 13000}", "")  # 原样

    async def test_archived_conversation_not_selected(self, env: _Env) -> None:
        from backend.app.services.conversation_service import ConversationArchivedError

        env.repository.seed_conversation()
        row = env.repository.get_by_conversation_id("conv-1")
        assert row is not None
        env.repository.update_status("conv-1", "ARCHIVED")

        with pytest.raises(ConversationArchivedError):
            await env.service.execute_message(conversation_id="conv-1", content="x")

        assert env.orchestrator.calls == []


# ============================================================
# 8. DB-gated（真实 PostgreSQL；Selection × Repository 顺序/上限）
# ============================================================


def _delete_conversations(conversation_ids: list[str]) -> None:
    if not conversation_ids:
        return
    factory = get_session_factory()
    assert factory is not None
    with factory() as session, session.begin():
        for conversation_id in conversation_ids:
            session.execute(
                text(
                    "DELETE FROM ai_ops.conversation WHERE conversation_id = :cid"
                ),
                {"cid": conversation_id},
            )


@pytest.fixture()
def created() -> Iterator[list[str]]:
    ids: list[str] = []
    yield ids
    _delete_conversations(ids)


@_DB_GATED
class TestSelectionWithRealRepository:
    @pytest.fixture(scope="class", autouse=True)
    def _ensure_tables(self) -> None:
        from backend.app.db import init_db as init_db_module

        init_db_module.init_db()

    async def _run_pipeline(
        self,
        *,
        conversation_service: ConversationService,
        conversation_id: str,
        content: str = "current",
    ) -> list[str | None]:
        orchestrator = FakeOrchestrator()
        service = ChatApplicationService(
            conversation_service=conversation_service,
            orchestrator_factory=lambda project_id: orchestrator,
        )
        await service.execute_message(conversation_id=conversation_id, content=content)
        return list(orchestrator.contexts)

    async def test_window_uses_latest_20_turns_in_repository_order(
        self, created: list[str]
    ) -> None:
        service = ConversationService()
        view = service.create_conversation(project_id="vietnam-wms")
        created.append(view.conversation_id)

        for index in range(25):
            role = TURN_ROLE_USER if index % 2 == 0 else TURN_ROLE_ASSISTANT
            service.append_turn(
                conversation_id=view.conversation_id,
                role=role,
                content=f"m{index + 1}",
                assistant_request_id=None if role == TURN_ROLE_USER else f"req-{index}",
            )

        contexts = await self._run_pipeline(
            conversation_service=service, conversation_id=view.conversation_id
        )

        context = contexts[0] or ""
        lines = context.split("\n")
        # 最新 20 条（m6..m25）→ 首条 ASSISTANT(m6) ⇒ USER-anchor 丢弃 ⇒ 19 行
        assert len(lines) == DEFAULT_HISTORY_TURNS - 1
        expected = [
            f"{'user' if index % 2 == 0 else 'assistant'}: m{index + 1}"
            for index in range(6, 25)
        ]
        assert lines == expected
        assert "current" not in context

    async def test_oversized_older_turn_stops_with_real_data(
        self, created: list[str]
    ) -> None:
        service = ConversationService()
        view = service.create_conversation(project_id="vietnam-wms")
        created.append(view.conversation_id)

        service.append_turn(
            conversation_id=view.conversation_id,
            role=TURN_ROLE_USER,
            content="u" * 13000,
            assistant_request_id=None,
        )
        service.append_turn(
            conversation_id=view.conversation_id,
            role=TURN_ROLE_ASSISTANT,
            content="a1",
            assistant_request_id="req-1",
        )
        service.append_turn(
            conversation_id=view.conversation_id,
            role=TURN_ROLE_USER,
            content="u2",
            assistant_request_id=None,
        )
        service.append_turn(
            conversation_id=view.conversation_id,
            role=TURN_ROLE_ASSISTANT,
            content="a2",
            assistant_request_id="req-2",
        )

        contexts = await self._run_pipeline(
            conversation_service=service, conversation_id=view.conversation_id
        )

        # 连续后缀 [A1, U2, A2] → USER-anchor 丢弃 A1 ⇒ [U2, A2]
        assert contexts[0] == "user: u2\nassistant: a2"

    async def test_no_selection_residue(self, created: list[str]) -> None:
        service = ConversationService()
        view = service.create_conversation(project_id="vietnam-wms")
        created.append(view.conversation_id)
        service.append_turn(
            conversation_id=view.conversation_id,
            role=TURN_ROLE_USER,
            content="u1",
            assistant_request_id=None,
        )

        await self._run_pipeline(
            conversation_service=service, conversation_id=view.conversation_id
        )

        factory = get_session_factory()
        assert factory is not None
        with factory() as session:
            count = session.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.conversation_turn "
                    "WHERE conversation_id = :cid"
                ),
                {"cid": view.conversation_id},
            ).scalar_one()
        # 历史 1（u1）+ 本次 USER 1 + 本次 ASSISTANT 1 = 3；Selection 自身不写任何行
        assert int(count) == 3


__all__ = [
    "TestWindowLimits",
    "TestContiguousSuffixAndOversized",
    "TestUserAnchored",
    "TestCurrentTurnOrderingDeterminism",
    "TestInputBoundary",
    "TestNoInternalLeak",
    "TestPipelineIntegration",
    "TestSelectionWithRealRepository",
]
