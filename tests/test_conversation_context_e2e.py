"""Conversation Context Runtime E2E（Phase 4.2 Step 7G）—— 离线部分。

验证 7A～7F 冻结契约在**真实装配链路**上的行为（不替换任何生产逻辑）：

```text
ChatApplicationService（真实）
        ↓  Idempotency（Step 6 真实实现）
ConversationService → Repository（内存 Fake：只替换持久化介质）
        ↓  History（Repository 顺序契约）
context_selection.select_history（**真实**；Step 7E 接线点）
        ↓  Context Builder（**真实**；Step 13）
AIOrchestrator（Fake：只记录 (question, context)）
        ↓
AI Result → 条件式 ASSISTANT Turn（真实规则）
```

被替换的**只有两层**：持久化介质（内存字典）与 AI 执行（Fake）。Selection / Builder /
幂等 / 条件式 Turn 规则全部为生产代码。

复用（不重建测试框架）：

    * ``tests.test_conversation_context_selection`` 的 ``FakeConversationRepository``
      / ``FakeOrchestrator``（Step 7E 已用于 Selection↔Pipeline 集成测试）；
    * DB 版本（真 PostgreSQL / HTTP）见 ``tests/test_conversation_context_e2e_db.py``。

禁止：DeepSeek / 真实 LLM / 网络 / DB（本文件全部离线）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

import pytest

from backend.app.db.conversation_repository import (
    CONVERSATION_STATUS_ARCHIVED,
    TURN_ROLE_ASSISTANT,
    TURN_ROLE_USER,
    ConversationRow,
)
from backend.app.services import chat_application_service as chat_app_module
from backend.app.services.ai_orchestrator_service import (
    AIOrchestrationResult,
    RouteType,
)
from backend.app.services.chat_application_service import (
    ChatApplicationService,
    MessageReplay,
)
from backend.app.services.conversation_context_builder import (
    ConversationContextBuilder,
)
from backend.app.services.conversation_service import (
    ConversationArchivedError,
    ConversationService,
    IdempotencyKeyReusedWithDifferentPayloadError,
)
from tests.test_conversation_context_selection import (
    FakeConversationRepository,
    FakeOrchestrator,
)

__all__ = [
    "TestMultiTurnE2E",
    "TestContextWindowE2E",
    "TestIdempotencyWithContextE2E",
    "TestFailureHistoryE2E",
    "TestIsolationE2E",
    "TestContextBoundary",
]

_LINE_RE = re.compile(r"^(user|assistant): ")


# ============================================================
# Env（真实 Application Service + 真实 Selection + 真实 Builder）
# ============================================================


class _RepoWithProjects(FakeConversationRepository):
    """在 7E Fake 之上补 ``project_id``（Fake 默认固定项目）。

    仅**测试**需要多 project；生产 Repository 由 SQL 行携带 project_id。
    """

    def seed_conversation(  # type: ignore[override]
        self, conversation_id: str = "conv-1", *, project_id: str = "vietnam-wms"
    ) -> ConversationRow:
        row = super().seed_conversation(conversation_id)
        updated = replace(row, project_id=project_id)
        self._conversations[conversation_id] = updated
        return updated


@dataclass
class _Counters:
    selection: int = 0
    builder: int = 0


@dataclass
class _Env:
    service: ChatApplicationService
    repository: _RepoWithProjects
    orchestrator: FakeOrchestrator
    project_ids: list[str]
    counters: _Counters


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch) -> _Env:
    """真实 ChatApplicationService（真实 Selection + 真实 Builder）+ Fake AI/介质。"""
    repository = _RepoWithProjects()
    orchestrator = FakeOrchestrator()
    project_ids: list[str] = []

    def _factory(project_id: str) -> FakeOrchestrator:
        project_ids.append(project_id)
        return orchestrator

    service = ChatApplicationService(
        conversation_service=ConversationService(repository=repository),
        orchestrator_factory=_factory,
        context_builder=ConversationContextBuilder(),
    )

    counters = _Counters()
    real_select = chat_app_module.select_history
    real_build = ConversationContextBuilder.build_context

    def _counting_select(turns: Any, **kwargs: Any) -> Any:
        counters.selection += 1
        return real_select(turns, **kwargs)

    def _counting_build(self: Any, turns: Any) -> Any:
        counters.builder += 1
        return real_build(self, turns)

    monkeypatch.setattr(chat_app_module, "select_history", _counting_select)
    monkeypatch.setattr(ConversationContextBuilder, "build_context", _counting_build)

    return _Env(
        service=service,
        repository=repository,
        orchestrator=orchestrator,
        project_ids=project_ids,
        counters=counters,
    )


def _seed_conversation(
    env: _Env,
    *,
    conversation_id: str = "conv-1",
    project_id: str = "vietnam-wms",
) -> str:
    row = env.repository.seed_conversation(
        conversation_id, project_id=project_id
    )
    return row.conversation_id


def _seed_turns(env: _Env, conversation_id: str, turns: list[str]) -> None:
    """按给定顺序（oldest → newest）写入 USER / ASSISTANT 交替历史。"""
    for index, content in enumerate(turns):
        is_user = index % 2 == 0
        env.repository.seed_turn(
            conversation_id=conversation_id,
            role=TURN_ROLE_USER if is_user else TURN_ROLE_ASSISTANT,
            content=content,
            assistant_request_id=None if is_user else f"req-{index}",
        )


def _turns(env: _Env, conversation_id: str) -> list[Any]:
    return list(env.repository.list_turns_by_conversation_id(conversation_id))


def _result(
    content: str | None, *, request_id: str = "req-ai"
) -> AIOrchestrationResult:
    return AIOrchestrationResult(
        route=RouteType.RAG, content=content, data=None,
        metadata={"request_id": request_id},
    )


def _archive(env: _Env, conversation_id: str) -> None:
    env.repository.update_status(conversation_id, CONVERSATION_STATUS_ARCHIVED)


# ============================================================
# 1. Multi-turn E2E（U1 A1 U2 A2 → U3）
# ============================================================


class TestMultiTurnE2E:
    async def test_three_turns_history_ordering_and_current_exclusion(
        self, env: _Env
    ) -> None:
        conversation_id = _seed_conversation(env)
        env.orchestrator.result = _result("采购订单 A100 已查询。", request_id="r1")
        await env.service.execute_message(
            conversation_id=conversation_id, content="查询采购订单 A100"
        )
        env.orchestrator.result = _result("供应商是华南电子。", request_id="r2")
        await env.service.execute_message(
            conversation_id=conversation_id, content="它的供应商是谁？"
        )
        env.orchestrator.result = _result("预计 10 月 12 日入库。", request_id="r3")
        await env.service.execute_message(
            conversation_id=conversation_id, content="那它什么时候入库？"
        )

        # DB（内存介质）：U1 A1 U2 A2 U3 A3（oldest → newest）
        assert [t.role for t in _turns(env, conversation_id)] == [
            "USER", "ASSISTANT", "USER", "ASSISTANT", "USER", "ASSISTANT",
        ]

        # 每轮拿到自己的窗口：首轮无历史 → U2 的 context 只含 U1 A1
        assert env.orchestrator.contexts[0] is None
        assert env.orchestrator.contexts[1] == (
            "user: 查询采购订单 A100\nassistant: 采购订单 A100 已查询。"
        )

        # U3 的 context = U1 A1 U2 A2（不含 U3 自身；oldest → newest）
        context = env.orchestrator.contexts[2]
        assert context == (
            "user: 查询采购订单 A100\n"
            "assistant: 采购订单 A100 已查询。\n"
            "user: 它的供应商是谁？\n"
            "assistant: 供应商是华南电子。"
        )
        assert "那它什么时候入库？" not in (context or "")
        # question = 当前消息（不同时进 context）
        assert env.orchestrator.calls[2] == "那它什么时候入库？"


# ============================================================
# 2. Context Window E2E（char cap / oversized）
# ============================================================


class TestContextWindowE2E:
    async def test_char_cap_12000_applied_through_pipeline(
        self, env: _Env
    ) -> None:
        conversation_id = _seed_conversation(env)
        _seed_turns(
            env, conversation_id,
            ["old-user", "old-assistant", "x" * 6000, "y" * 6000],
        )
        env.orchestrator.result = _result("ok")

        await env.service.execute_message(
            conversation_id=conversation_id, content="current"
        )

        context = env.orchestrator.contexts[0] or ""
        assert context == f"user: {'x' * 6000}\nassistant: {'y' * 6000}"
        assert "old-user" not in context  # 超出 12000 ⇒ 停止（连续后缀）
        # 入选 content 字符数 == 12000（上限精确生效）
        assert sum(len(line.split(": ", 1)[1]) for line in context.split("\n")) == 12000

    async def test_oversized_newest_turn_is_preserved_intact(
        self, env: _Env
    ) -> None:
        conversation_id = _seed_conversation(env)
        _seed_turns(env, conversation_id, ["u" * 13000, "a1", "u2", "a2"])
        env.orchestrator.result = _result("ok")

        await env.service.execute_message(
            conversation_id=conversation_id, content="current"
        )

        context = env.orchestrator.contexts[0] or ""
        # 连续后缀 [A1, U2, A2] → USER-anchor 丢弃 A1 ⇒ [U2, A2]
        assert context == "user: u2\nassistant: a2"

    async def test_older_oversized_stops_without_hole(self, env: _Env) -> None:
        conversation_id = _seed_conversation(env)
        _seed_turns(
            env, conversation_id,
            ["h" * 13000, "a0", "u1", "a1", "u2", "a2"],
        )
        env.orchestrator.result = _result("ok")

        await env.service.execute_message(
            conversation_id=conversation_id, content="current"
        )

        context = env.orchestrator.contexts[0] or ""
        assert context == "user: u1\nassistant: a1\nuser: u2\nassistant: a2"
        assert "h" * 100 not in context  # 超限 turn 及其更旧历史整体不入选（无空洞）
        assert "a0" not in context

    async def test_window_uses_latest_20_turns(self, env: _Env) -> None:
        conversation_id = _seed_conversation(env)
        _seed_turns(env, conversation_id, [f"m{i}" for i in range(1, 26)])
        env.orchestrator.result = _result("ok")

        await env.service.execute_message(
            conversation_id=conversation_id, content="current"
        )

        lines = (env.orchestrator.contexts[0] or "").split("\n")
        # 25 条（m1..m25；m1=USER）→ 最新 20 条 = m6..m25 →
        # 首条 m6 为 ASSISTANT ⇒ USER-anchor 丢弃 ⇒ 19 行（m7..m25）
        assert len(lines) == 19
        assert lines[0] == "user: m7"
        assert lines[-1] == "user: m25"
        assert "m5" not in lines


# ============================================================
# 3. Idempotency × Context E2E
# ============================================================


class TestIdempotencyWithContextE2E:
    async def test_replay_skips_selection_builder_and_ai(self, env: _Env) -> None:
        conversation_id = _seed_conversation(env)
        env.orchestrator.result = _result("第一次回答", request_id="r1")

        first = await env.service.execute_message(
            conversation_id=conversation_id, content="U1", idempotency_key="K1"
        )
        assert not isinstance(first, MessageReplay)
        assert env.counters.selection == 1
        assert env.counters.builder == 1
        assert len(env.orchestrator.calls) == 1

        replay = await env.service.execute_message(
            conversation_id=conversation_id, content="U1", idempotency_key="K1"
        )

        assert isinstance(replay, MessageReplay)
        assert replay.content == "第一次回答"
        assert replay.assistant_request_id == "r1"
        # 第二次：AI = 0 · Selection = 0 · Builder = 0 · 无新 turn
        assert len(env.orchestrator.calls) == 1
        assert env.counters.selection == 1
        assert env.counters.builder == 1
        assert [t.role for t in _turns(env, conversation_id)] == [
            "USER", "ASSISTANT",
        ]

    async def test_conflict_does_not_consume_context_or_ai(
        self, env: _Env
    ) -> None:
        conversation_id = _seed_conversation(env)
        env.orchestrator.result = _result("A")
        await env.service.execute_message(
            conversation_id=conversation_id, content="A", idempotency_key="K1"
        )
        selection_before = env.counters.selection
        builder_before = env.counters.builder

        with pytest.raises(IdempotencyKeyReusedWithDifferentPayloadError):
            await env.service.execute_message(
                conversation_id=conversation_id,
                content="B",
                idempotency_key="K1",
            )

        assert len(env.orchestrator.calls) == 1  # AI = 0（冲突前拦截）
        assert env.counters.selection == selection_before
        assert env.counters.builder == builder_before
        assert [t.role for t in _turns(env, conversation_id)] == [
            "USER", "ASSISTANT",
        ]

    async def test_retry_reuses_user_turn_and_excludes_current_from_context(
        self, env: _Env
    ) -> None:
        conversation_id = _seed_conversation(env)
        env.orchestrator.result = _result(None, request_id="r-empty")  # EMPTY

        await env.service.execute_message(
            conversation_id=conversation_id, content="U1", idempotency_key="K1"
        )
        assert [t.role for t in _turns(env, conversation_id)] == ["USER"]

        env.orchestrator.result = _result("重试成功", request_id="r-retry")
        retried = await env.service.execute_message(
            conversation_id=conversation_id, content="U1", idempotency_key="K1"
        )

        assert not isinstance(retried, MessageReplay)
        assert len(env.orchestrator.calls) == 2
        # retry：复用既有 USER Turn ⇒ 无第二条 USER Turn
        assert [t.role for t in _turns(env, conversation_id)] == [
            "USER", "ASSISTANT",
        ]
        # retry context 不含当前（复用）turn
        assert env.orchestrator.contexts[1] is None

    async def test_archived_rejected_before_any_consumption(
        self, env: _Env
    ) -> None:
        conversation_id = _seed_conversation(env)
        _archive(env, conversation_id)

        with pytest.raises(ConversationArchivedError):
            await env.service.execute_message(
                conversation_id=conversation_id, content="U1"
            )

        assert env.orchestrator.calls == []
        assert env.counters.selection == 0
        assert env.counters.builder == 0
        assert _turns(env, conversation_id) == []


# ============================================================
# 4. EMPTY / FAILED 历史语义 E2E
# ============================================================


class TestFailureHistoryE2E:
    async def test_empty_assistant_is_plain_history_without_internal_state(
        self, env: _Env
    ) -> None:
        conversation_id = _seed_conversation(env)
        env.orchestrator.result = _result(None, request_id="r-empty")

        result = await env.service.execute_message(
            conversation_id=conversation_id, content="空回答的问题"
        )

        assert result.content is None
        assert [t.role for t in _turns(env, conversation_id)] == ["USER"]

        env.orchestrator.result = _result("后续回答", request_id="r-next")
        await env.service.execute_message(
            conversation_id=conversation_id, content="下一个问题"
        )

        context = env.orchestrator.contexts[1] or ""
        assert context == "user: 空回答的问题"  # 保留为普通历史内容
        for sentinel in ("EMPTY", "FAILED", "exception", "request_id", "r-empty"):
            assert sentinel not in context

    async def test_failed_assistant_keeps_user_turn_only(
        self, env: _Env
    ) -> None:
        conversation_id = _seed_conversation(env)
        env.orchestrator.error = RuntimeError(
            "boom sk-secret postgresql://user:pw@host/db"
        )

        with pytest.raises(RuntimeError):
            await env.service.execute_message(
                conversation_id=conversation_id, content="会失败的问题"
            )

        assert [t.role for t in _turns(env, conversation_id)] == ["USER"]

        env.orchestrator.error = None
        env.orchestrator.result = _result("恢复后回答", request_id="r-ok")
        await env.service.execute_message(
            conversation_id=conversation_id, content="正常问题"
        )

        context = env.orchestrator.contexts[1] or ""
        assert context == "user: 会失败的问题"
        for sentinel in (
            "sk-secret", "postgresql://", "Traceback", "request_id", "RuntimeError",
        ):
            assert sentinel not in context


# ============================================================
# 5. Isolation E2E（conversation / project）
# ============================================================


class TestIsolationE2E:
    async def test_conversation_and_project_isolation(self, env: _Env) -> None:
        conversation_a = _seed_conversation(
            env, conversation_id="conv-a", project_id="project-a"
        )
        conversation_b = _seed_conversation(
            env, conversation_id="conv-b", project_id="project-b"
        )

        env.orchestrator.result = _result("A1 答", request_id="r-a1")
        await env.service.execute_message(conversation_id=conversation_a, content="A1")
        env.orchestrator.result = _result("A2 答", request_id="r-a2")
        await env.service.execute_message(conversation_id=conversation_a, content="A2")
        env.orchestrator.result = _result("B1 答", request_id="r-b1")
        await env.service.execute_message(conversation_id=conversation_b, content="B1")

        # B 的首轮：无任何 A 的历史
        assert env.orchestrator.contexts[2] is None

        # B 的第二轮：context 只含 B 自己的历史（A 不出现）
        env.orchestrator.result = _result("B2 答", request_id="r-b2")
        await env.service.execute_message(conversation_id=conversation_b, content="B2")
        context_b2 = env.orchestrator.contexts[3] or ""
        assert context_b2 == "user: B1\nassistant: B1 答"
        assert "A1 答" not in context_b2
        assert "A2 答" not in context_b2

        # A 的第二轮：context 只含 A 自己的历史（B 不出现）
        context_a2 = env.orchestrator.contexts[1] or ""
        assert context_a2 == "user: A1\nassistant: A1 答"
        assert "B1 答" not in context_a2

        # project 绑定来自 conversation（不是全局状态）
        assert env.project_ids == [
            "project-a", "project-a", "project-b", "project-b",
        ]


# ============================================================
# 6. Context Boundary（无内部标识 / 纯 role-content 行）
# ============================================================


class TestContextBoundary:
    async def test_context_carries_no_internal_identifiers(self, env: _Env) -> None:
        conversation_id = _seed_conversation(env)
        env.orchestrator.result = _result("A1 答", request_id="req-secret-trace")
        await env.service.execute_message(conversation_id=conversation_id, content="U1")
        env.orchestrator.result = _result("A2 答", request_id="req-x")
        await env.service.execute_message(conversation_id=conversation_id, content="U2")

        context = env.orchestrator.contexts[1] or ""

        assert context == "user: U1\nassistant: A1 答"
        for sentinel in (
            conversation_id,      # conversation_id
            "req-secret-trace",   # assistant_request_id
            "turn_id", "idempotency", "created_at", "ASSISTANT:", "USER:",
        ):
            assert sentinel not in context
        # context 只允许 "role: content" 行（无 metadata / JSON / 分隔符泄漏）
        assert all(_LINE_RE.match(line) for line in context.split("\n"))
