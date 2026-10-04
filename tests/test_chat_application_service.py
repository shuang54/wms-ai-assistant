"""ChatApplicationService 契约测试（Phase 4.1 Step 11 —— 最小 workflow）。

本文件性质：

    * **纯离线**：全部使用 Fake / Stub —— DB = 0 · Network = 0 · LLM = 0；
    * Fake Repository + **真实 ConversationService**（验证真实业务规则）；
      Fake Orchestrator（duck-type ``execute(question, *, context=None)``）；
    * 冻结来源：``docs/evaluation/Phase 4.1 Step 10 — Chat Application Boundary Audit.md``
      （方案 B：Application Service 组合 Conversation + AI，互不依赖）。
"""
from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

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
)
from backend.app.dto.assistant_outcome import (
    AssistantOutcome,
    determine_assistant_outcome,
)
from backend.app.services.ai_orchestrator_service import (
    AIOrchestrationResult,
    AIOrchestratorExecutionError,
    RouteType,
    TEXT_TO_SQL_REFUSAL_MESSAGE,
)
from backend.app.services.chat_application_service import (
    ASSISTANT_ROLE,
    CONVERSATION_STATUS_ARCHIVED as SERVICE_ARCHIVED_STATUS,
    REQUEST_ID_METADATA_KEY,
    USER_ROLE,
    ChatApplicationService,
    _default_orchestrator_factory,
)
from backend.app.services.conversation_context_builder import (
    ConversationContextBuilder,
    build_context,
)
from backend.app.services.conversation_service import (
    ConversationArchivedError,
    ConversationNotFoundError,
    ConversationService,
    ConversationTurnView,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_chat_application_service.py"
_SERVICE = "backend/app/services/chat_application_service.py"
_CONVERSATION_SERVICE = "backend/app/services/conversation_service.py"

_DEFAULT_PROJECT_ID = "proj-alpha"
_DEFAULT_REQUEST_ID = "req-0001"


# ============================================================
# Fakes（无 DB / 无网络）
# ============================================================


class FakeConversationRepository:
    """内存版 ConversationRepository（只实现 Service 用到的契约；含事件记录）。"""

    def __init__(self, events: list[str] | None = None) -> None:
        self._conversations: dict[str, ConversationRow] = {}
        self._turns: dict[str, list[ConversationTurnRow]] = {}
        self._next_turn_id = 1
        self._append_count = 0
        self._list_calls = 0
        self.events = events if events is not None else []
        #: 1-based append 调用序号 → 注入失败（模拟 Repository / DB 写失败）
        self.fail_append_on: set[int] = set()
        #: True → 注入历史读失败（模拟 list_turns 的 DB failure）
        self.fail_list = False
        #: 对外可见的 append 入参（断言契约用）
        self.append_calls: list[dict[str, Any]] = []

    # ---------- 测试辅助 ----------

    def seed_conversation(
        self,
        *,
        conversation_id: str = "conv-1",
        project_id: str = _DEFAULT_PROJECT_ID,
        status: str = CONVERSATION_STATUS_ACTIVE,
    ) -> ConversationRow:
        now = datetime.now(timezone.utc)
        created_at = now - timedelta(minutes=10)
        row = ConversationRow(
            conversation_id=conversation_id,
            project_id=project_id,
            created_at=created_at,
            updated_at=created_at,
            status=status,
        )
        self._conversations[conversation_id] = row
        self._turns.setdefault(conversation_id, [])
        return row

    def turns(self, conversation_id: str = "conv-1") -> tuple[ConversationTurnRow, ...]:
        return tuple(self._turns.get(conversation_id, ()))

    @property
    def list_calls(self) -> int:
        return self._list_calls

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
    ) -> ConversationTurnRow:
        self._append_count += 1
        self.append_calls.append(
            {
                "conversation_id": conversation_id,
                "role": role,
                "content": content,
                "assistant_request_id": assistant_request_id,
            }
        )
        self.events.append(f"append:{role}")

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
        )
        self._next_turn_id += 1
        self._turns.setdefault(conversation_id, []).append(turn)
        self._conversations[conversation_id] = replace(current, updated_at=now)
        return turn

    def list_turns_by_conversation_id(
        self, conversation_id: str
    ) -> tuple[ConversationTurnRow, ...]:
        self._list_calls += 1
        if self.fail_list:
            raise ConversationRepositoryError("injected history read failure")
        if conversation_id not in self._conversations:
            raise ConversationNotFoundRepositoryError("conversation 不存在")
        return tuple(self._turns.get(conversation_id, ()))


class FakeOrchestrator:
    """duck-type AIOrchestrator（``execute(question, *, context=None)``）。"""

    def __init__(
        self,
        *,
        result: AIOrchestrationResult | None = None,
        error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.events = events if events is not None else []
        self.calls: list[str] = []
        self.contexts: list[str | None] = []

    async def execute(
        self, question: str, *, context: str | None = None
    ) -> AIOrchestrationResult:
        self.calls.append(question)
        self.contexts.append(context)
        self.events.append(f"execute:{question}")
        if self.error is not None:
            raise self.error
        assert self.result is not None, "FakeOrchestrator 未配置 result"
        return self.result


class FakeContextBuilder:
    """duck-type ConversationContextBuilder（记录入参；可注入失败）。

    默认 delegate 到 **真实** Step 13 ``ConversationContextBuilder`` ——
    测试不复制渲染逻辑，只观察"收到了什么 turns / 何时失败"。
    """

    def __init__(self) -> None:
        self.received: list[tuple[ConversationTurnView, ...]] = []
        self.error: Exception | None = None
        self._delegate = ConversationContextBuilder()

    def build_context(self, turns: Any) -> str | None:
        self.received.append(tuple(turns))
        if self.error is not None:
            raise self.error
        return self._delegate.build_context(turns)


class _FactoryRecorder:
    """记录 factory 收到的 project_id（断言 project binding）。"""

    def __init__(self, orchestrator: FakeOrchestrator) -> None:
        self.orchestrator = orchestrator
        self.project_ids: list[str] = []

    def __call__(self, project_id: str) -> FakeOrchestrator:
        self.project_ids.append(project_id)
        return self.orchestrator


@dataclass
class _Env:
    service: ChatApplicationService
    repository: FakeConversationRepository
    conversations: ConversationService
    orchestrator: FakeOrchestrator
    factory: _FactoryRecorder
    context_builder: FakeContextBuilder
    events: list[str] = field(default_factory=list)


@pytest.fixture()
def env() -> _Env:
    events: list[str] = []
    repository = FakeConversationRepository(events=events)
    conversations = ConversationService(repository=repository)
    orchestrator = FakeOrchestrator(events=events)
    factory = _FactoryRecorder(orchestrator)
    context_builder = FakeContextBuilder()
    service = ChatApplicationService(
        conversation_service=conversations,
        orchestrator_factory=factory,
        context_builder=context_builder,
    )
    return _Env(
        service=service,
        repository=repository,
        conversations=conversations,
        orchestrator=orchestrator,
        factory=factory,
        context_builder=context_builder,
        events=events,
    )


def _result(
    *,
    content: str | None,
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


# ============================================================
# 1. Normal success
# ============================================================


class TestNormalSuccess:
    async def test_workflow_order_user_then_ai_then_assistant(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="最终回答")

        result = await env.service.execute_message(
            conversation_id="conv-1", content="你好"
        )

        assert env.events == ["append:USER", "execute:你好", "append:ASSISTANT"]
        assert [t.role for t in env.repository.turns()] == [
            TURN_ROLE_USER,
            TURN_ROLE_ASSISTANT,
        ]
        assert result.content == "最终回答"

    async def test_user_turn_has_no_assistant_request_id(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="回答")

        await env.service.execute_message(conversation_id="conv-1", content="你好")

        user_turn, _ = env.repository.turns()
        assert user_turn.role == TURN_ROLE_USER
        assert user_turn.assistant_request_id is None
        assert env.repository.append_calls[0]["assistant_request_id"] is None

    async def test_assistant_turn_uses_ai_request_id(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="回答")

        await env.service.execute_message(conversation_id="conv-1", content="你好")

        _, assistant_turn = env.repository.turns()
        assert assistant_turn.role == TURN_ROLE_ASSISTANT
        assert assistant_turn.assistant_request_id == _DEFAULT_REQUEST_ID

    async def test_result_returned_unchanged(self, env: _Env) -> None:
        env.repository.seed_conversation()
        ai_result = _result(content="回答")
        env.orchestrator.result = ai_result

        result = await env.service.execute_message(
            conversation_id="conv-1", content="你好"
        )

        assert result is ai_result  # 原样返回，不包装 / 不重建

    async def test_assistant_content_equals_ai_content_metadata_ignored(
        self, env: _Env
    ) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(
            content="最终回答",
            metadata={
                "sql": "SELECT secret FROM t",
                "prompt": "system prompt 全文",
                "api_key": "sk-secret",
            },
        )

        await env.service.execute_message(conversation_id="conv-1", content="你好")

        _, assistant_turn = env.repository.turns()
        assert assistant_turn.content == "最终回答"
        for forbidden_value in (
            "SELECT secret FROM t",
            "system prompt 全文",
            "sk-secret",
        ):
            assert forbidden_value not in assistant_turn.content


# ============================================================
# 2. Project binding
# ============================================================


class TestProjectBinding:
    async def test_factory_receives_conversation_project_id(self, env: _Env) -> None:
        env.repository.seed_conversation(project_id="proj-beta")
        env.orchestrator.result = _result(content="回答")

        await env.service.execute_message(conversation_id="conv-1", content="你好")

        assert env.factory.project_ids == ["proj-beta"]

    async def test_no_hardcoded_project_name(self) -> None:
        source = (_REPO_ROOT / _SERVICE).read_text(encoding="utf-8")
        assert "vietnam-wms" not in source
        assert "vietnam_wms" not in source

    async def test_first_turn_context_none_and_history_read_once(
        self, env: _Env
    ) -> None:
        """Step 14：每个 turn 都读历史；首个 turn → previous = () → context = None。"""
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="回答")

        await env.service.execute_message(conversation_id="conv-1", content="你好")

        assert env.orchestrator.contexts == [None]
        assert env.repository.list_calls == 1
        assert env.context_builder.received == [()]  # 只收到历史（current 已排除）


# ============================================================
# 3. Archived / NotFound / 非法输入（AI = 0）
# ============================================================


class TestRejectedBeforeAi:
    async def test_archived_conversation_rejected(self, env: _Env) -> None:
        env.repository.seed_conversation(status=CONVERSATION_STATUS_ARCHIVED)

        with pytest.raises(ConversationArchivedError):
            await env.service.execute_message(conversation_id="conv-1", content="你好")

        assert env.orchestrator.calls == []
        assert env.repository.append_calls == []

    async def test_archived_uses_existing_error_type(self, env: _Env) -> None:
        env.repository.seed_conversation(status=CONVERSATION_STATUS_ARCHIVED)
        with pytest.raises(ConversationArchivedError) as excinfo:
            await env.service.execute_message(conversation_id="conv-1", content="你好")
        assert type(excinfo.value) is ConversationArchivedError

    async def test_not_found_rejected(self, env: _Env) -> None:
        with pytest.raises(ConversationNotFoundError):
            await env.service.execute_message(conversation_id="conv-missing", content="你好")

        assert env.orchestrator.calls == []
        assert env.repository.append_calls == []

    async def test_invalid_conversation_id_rejected(self, env: _Env) -> None:
        with pytest.raises(ValueError):
            await env.service.execute_message(conversation_id="   ", content="你好")

        assert env.orchestrator.calls == []

    async def test_blank_content_rejected_before_any_write(self, env: _Env) -> None:
        env.repository.seed_conversation()

        with pytest.raises(ValueError):
            await env.service.execute_message(conversation_id="conv-1", content="   ")

        assert env.orchestrator.calls == []
        assert env.repository.append_calls == []


# ============================================================
# 4. AI failure
# ============================================================


class TestAiFailure:
    async def test_user_turn_kept_and_original_error_raised(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.error = AIOrchestratorExecutionError("boom")

        with pytest.raises(AIOrchestratorExecutionError) as excinfo:
            await env.service.execute_message(conversation_id="conv-1", content="你好")

        assert type(excinfo.value) is AIOrchestratorExecutionError  # 不包装
        assert env.orchestrator.calls == ["你好"]
        assert [t.role for t in env.repository.turns()] == [TURN_ROLE_USER]

    async def test_no_assistant_turn_created_on_failure(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.error = AIOrchestratorExecutionError("boom")

        with pytest.raises(AIOrchestratorExecutionError):
            await env.service.execute_message(conversation_id="conv-1", content="你好")

        assert [c["role"] for c in env.repository.append_calls] == [TURN_ROLE_USER]


# ============================================================
# 5. EMPTY / REFUSED / T2SQL row_count=0
# ============================================================


class TestResultSemantics:
    async def test_empty_result_creates_no_assistant_turn(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(
            content=None,
            route=RouteType.RAG,
            metadata={
                "outcome": determine_assistant_outcome(
                    route=RouteType.RAG.value, rag_used_chunks=0
                )
            },
        )

        result = await env.service.execute_message(
            conversation_id="conv-1", content="你好"
        )

        assert result.metadata["outcome"] is AssistantOutcome.EMPTY
        assert [t.role for t in env.repository.turns()] == [TURN_ROLE_USER]

    async def test_blank_content_creates_no_assistant_turn(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="   ")

        await env.service.execute_message(conversation_id="conv-1", content="你好")

        assert [t.role for t in env.repository.turns()] == [TURN_ROLE_USER]

    async def test_no_fabricated_content(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content=None)

        await env.service.execute_message(conversation_id="conv-1", content="你好")

        stored = [t.content for t in env.repository.turns()]
        assert stored == ["你好"]
        for marker in ("[EMPTY]", "EMPTY", "占位", "无法回答"):
            assert all(marker not in content for content in stored)

    async def test_refused_with_content_creates_assistant_turn(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(
            content=TEXT_TO_SQL_REFUSAL_MESSAGE,
            route=RouteType.TEXT_TO_SQL,
            metadata={
                "refused": True,
                "outcome": determine_assistant_outcome(
                    route=RouteType.TEXT_TO_SQL.value, refused=True
                ),
            },
        )

        await env.service.execute_message(conversation_id="conv-1", content="删掉所有订单")

        assert env.orchestrator.calls == ["删掉所有订单"]  # 无 retry
        _, assistant_turn = env.repository.turns()
        assert assistant_turn.content == TEXT_TO_SQL_REFUSAL_MESSAGE
        assert assistant_turn.assistant_request_id == _DEFAULT_REQUEST_ID

    async def test_t2sql_zero_rows_success_creates_assistant_turn(
        self, env: _Env
    ) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(
            content="查询完成：0 行结果",
            route=RouteType.TEXT_TO_SQL,
            metadata={
                "row_count": 0,
                "outcome": determine_assistant_outcome(
                    route=RouteType.TEXT_TO_SQL.value
                ),
            },
        )

        await env.service.execute_message(conversation_id="conv-1", content="查没有的订单")

        _, assistant_turn = env.repository.turns()
        assert assistant_turn.content == "查询完成：0 行结果"
        assert (
            env.orchestrator.result.metadata["outcome"] is AssistantOutcome.SUCCESS
        )

    async def test_missing_request_id_creates_no_assistant_turn(
        self, env: _Env
    ) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = AIOrchestrationResult(
            route=RouteType.RAG, content="回答", data=None, metadata={}
        )

        result = await env.service.execute_message(
            conversation_id="conv-1", content="你好"
        )

        assert result.content == "回答"
        assert [t.role for t in env.repository.turns()] == [TURN_ROLE_USER]

    async def test_non_string_request_id_creates_no_assistant_turn(
        self, env: _Env
    ) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(
            content="回答", metadata={REQUEST_ID_METADATA_KEY: 12345}
        )

        await env.service.execute_message(conversation_id="conv-1", content="你好")

        assert [t.role for t in env.repository.turns()] == [TURN_ROLE_USER]


# ============================================================
# 6. Transaction failure（append 失败）
# ============================================================


class TestAppendFailure:
    async def test_user_append_failure_skips_ai(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.repository.fail_append_on = {1}

        with pytest.raises(ConversationRepositoryError):
            await env.service.execute_message(conversation_id="conv-1", content="你好")

        assert env.orchestrator.calls == []  # AI 未被调用
        assert env.repository.turns() == ()

    async def test_assistant_append_failure_does_not_retry(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="回答")
        env.repository.fail_append_on = {2}

        with pytest.raises(ConversationRepositoryError):
            await env.service.execute_message(conversation_id="conv-1", content="你好")

        assert env.orchestrator.calls == ["你好"]  # AI 只执行 1 次，不重试
        assert [t.role for t in env.repository.turns()] == [TURN_ROLE_USER]


# ============================================================
# 7. 边界审计（AST / 常量 / 签名）
# ============================================================


def _module_imports(relative: str) -> set[str]:
    tree = ast.parse((_REPO_ROOT / relative).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _identifiers(relative: str) -> set[str]:
    tree = ast.parse((_REPO_ROOT / relative).read_text(encoding="utf-8"))
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }


def _called_names(relative: str) -> set[str]:
    """模块中所有被调用函数/方法名（AST Call 节点；docstring 不参与）。"""
    tree = ast.parse((_REPO_ROOT / relative).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _call_lines(relative: str, name: str) -> list[int]:
    """指定调用名的源码行号（AST；用于断言代码执行顺序，非 docstring 文本顺序）。"""
    tree = ast.parse((_REPO_ROOT / relative).read_text(encoding="utf-8"))
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            if isinstance(node.func, ast.Attribute)
            else None
        )
        if func_name == name:
            lines.append(node.lineno)
    return lines


class TestBoundaryAudit:
    def test_service_has_no_db_imports(self) -> None:
        modules = _module_imports(_SERVICE)
        for module in modules:
            assert not module.startswith("sqlalchemy"), module
            assert not module.startswith("psycopg"), module
            assert not module.startswith("backend.app.db"), module

    def test_service_does_not_generate_ids(self) -> None:
        identifiers = _identifiers(_SERVICE)
        for forbidden in ("new_request_id", "uuid", "uuid4", "uuid1"):
            assert forbidden not in identifiers, forbidden

    def test_conversation_service_still_has_no_ai_imports(self) -> None:
        modules = _module_imports(_CONVERSATION_SERVICE)
        for forbidden in (
            "backend.app.services.ai_orchestrator_service",
            "backend.app.services.chat_application_service",
            "backend.app.services.rag_service",
            "backend.app.services.tool_chat_service",
            "backend.app.services.text_to_sql_service",
        ):
            assert forbidden not in modules, forbidden

    def test_role_and_status_literals_match_persistence(self) -> None:
        assert USER_ROLE == TURN_ROLE_USER
        assert ASSISTANT_ROLE == TURN_ROLE_ASSISTANT
        assert SERVICE_ARCHIVED_STATUS == CONVERSATION_STATUS_ARCHIVED

    def test_execute_message_signature_is_keyword_only(self) -> None:
        signature = inspect.signature(ChatApplicationService.execute_message)
        params = {
            name: parameter.kind
            for name, parameter in signature.parameters.items()
            if name != "self"
        }
        assert params == {
            "conversation_id": inspect.Parameter.KEYWORD_ONLY,
            "content": inspect.Parameter.KEYWORD_ONLY,
        }
        assert inspect.iscoroutinefunction(ChatApplicationService.execute_message)

    def test_default_construction_is_offline(self) -> None:
        service = ChatApplicationService()
        assert isinstance(service.conversation_service, ConversationService)
        assert callable(service.orchestrator_factory)

    def test_default_factory_is_the_lazy_module_factory(self) -> None:
        assert (
            ChatApplicationService().orchestrator_factory
            is _default_orchestrator_factory
        )

    def test_self_audit_offline(self) -> None:
        modules = _module_imports(_SELF)
        for module in modules:
            assert not module.startswith("sqlalchemy"), module
            assert not module.startswith("httpx"), module
            assert not module.startswith("openai"), module


# ============================================================
# 9. History → Context（Step 14）
# ============================================================


class TestHistoryContext:
    async def test_first_turn_context_is_none(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="回答")

        await env.service.execute_message(conversation_id="conv-1", content="你好")

        assert env.orchestrator.contexts == [None]
        assert env.context_builder.received == [()]

    async def test_second_turn_passes_previous_history_to_orchestrator(
        self, env: _Env
    ) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="当前库存为 100 件")
        await env.service.execute_message(conversation_id="conv-1", content="查询库存")

        env.orchestrator.result = _result(content="最低的 10 个是 A/B/C")
        await env.service.execute_message(
            conversation_id="conv-1", content="查询其中库存最低的10个"
        )

        assert env.orchestrator.calls == [
            "查询库存",
            "查询其中库存最低的10个",
        ]
        assert env.orchestrator.contexts[0] is None
        assert env.orchestrator.contexts[1] == (
            "user: 查询库存\nassistant: 当前库存为 100 件"
        )
        # 交叉验证：与 Step 13 真实 build_context 输出一致
        assert env.orchestrator.contexts[1] == build_context(
            list(env.context_builder.received[1])
        )

    async def test_current_user_turn_excluded_from_context(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="A1")
        await env.service.execute_message(conversation_id="conv-1", content="U1")

        env.orchestrator.result = _result(content="A2")
        await env.service.execute_message(
            conversation_id="conv-1", content="U2-current"
        )

        latest = env.context_builder.received[-1]
        assert [turn.role for turn in latest] == [
            TURN_ROLE_USER,
            TURN_ROLE_ASSISTANT,
        ]
        assert [turn.content for turn in latest] == ["U1", "A1"]
        assert "U2-current" not in (env.orchestrator.contexts[1] or "")

    async def test_multiple_history_ordering_preserved(self, env: _Env) -> None:
        env.repository.seed_conversation()
        script = [("U1", "A1"), ("U2", "A2"), ("U3", "A3"), ("U4", "A4")]
        for question, answer in script:
            env.orchestrator.result = _result(content=answer)
            await env.service.execute_message(
                conversation_id="conv-1", content=question
            )

        # 第 4 次执行时 current = U4 → context 只含 U1/A1/U2/A2/U3/A3（顺序保持）
        assert env.orchestrator.contexts[3] == (
            "user: U1\nassistant: A1\n"
            "user: U2\nassistant: A2\n"
            "user: U3\nassistant: A3"
        )
        assert len(env.context_builder.received) == 4

    async def test_history_metadata_not_in_context(self, env: _Env) -> None:
        env.repository.seed_conversation(conversation_id="conv-secret-id")
        env.orchestrator.result = _result(
            content="库存 100", metadata={"request_id": "req-secret-1"}
        )
        await env.service.execute_message(
            conversation_id="conv-secret-id", content="查询库存"
        )

        env.orchestrator.result = _result(content="好的")
        await env.service.execute_message(
            conversation_id="conv-secret-id", content="继续"
        )

        context = env.orchestrator.contexts[1] or ""
        assert context == "user: 查询库存\nassistant: 库存 100"
        assert "req-secret-1" not in context
        assert "conv-secret-id" not in context

    async def test_history_read_failure_skips_ai(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.repository.fail_list = True

        with pytest.raises(ConversationRepositoryError):
            await env.service.execute_message(conversation_id="conv-1", content="你好")

        assert env.orchestrator.calls == []  # AI = 0
        assert [t.role for t in env.repository.turns()] == [TURN_ROLE_USER]
        assert env.context_builder.received == []  # Context 未构建

    async def test_context_build_failure_skips_ai(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.context_builder.error = ValueError("不支持的 turn role: 'SYSTEM'")

        with pytest.raises(ValueError) as excinfo:
            await env.service.execute_message(conversation_id="conv-1", content="你好")

        assert type(excinfo.value) is ValueError  # 不包装
        assert env.orchestrator.calls == []
        assert [t.role for t in env.repository.turns()] == [TURN_ROLE_USER]

    async def test_ai_failure_after_context_keeps_user_turn(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="A1")
        await env.service.execute_message(conversation_id="conv-1", content="U1")

        env.orchestrator.error = AIOrchestratorExecutionError("boom")
        with pytest.raises(AIOrchestratorExecutionError):
            await env.service.execute_message(conversation_id="conv-1", content="U2")

        assert [t.role for t in env.repository.turns()] == [
            TURN_ROLE_USER,
            TURN_ROLE_ASSISTANT,
            TURN_ROLE_USER,
        ]
        assert len(env.context_builder.received) == 2
        assert env.context_builder.received[1][0].content == "U1"

    async def test_project_binding_unchanged_with_history(self, env: _Env) -> None:
        env.repository.seed_conversation(project_id="project-A")
        env.orchestrator.result = _result(content="A1")
        await env.service.execute_message(conversation_id="conv-1", content="U1")

        env.orchestrator.result = _result(content="A2")
        await env.service.execute_message(
            conversation_id="conv-1", content="我想切到 project-B"
        )

        assert env.factory.project_ids == ["project-A", "project-A"]
        assert env.orchestrator.contexts[1] is not None

    async def test_refusal_regression_with_context(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="A1")
        await env.service.execute_message(conversation_id="conv-1", content="U1")

        env.orchestrator.result = _result(
            content=TEXT_TO_SQL_REFUSAL_MESSAGE,
            route=RouteType.TEXT_TO_SQL,
            metadata={
                "refused": True,
                "outcome": determine_assistant_outcome(
                    route=RouteType.TEXT_TO_SQL.value, refused=True
                ),
            },
        )
        await env.service.execute_message(
            conversation_id="conv-1", content="删掉所有订单"
        )

        assert env.orchestrator.calls == ["U1", "删掉所有订单"]  # 无 retry
        last_turn = env.repository.turns()[-1]
        assert last_turn.role == TURN_ROLE_ASSISTANT
        assert last_turn.content == TEXT_TO_SQL_REFUSAL_MESSAGE
        assert last_turn.assistant_request_id == _DEFAULT_REQUEST_ID
        assert env.orchestrator.contexts[1] is not None

    async def test_empty_regression_with_context(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="A1")
        await env.service.execute_message(conversation_id="conv-1", content="U1")

        env.orchestrator.result = _result(
            route=RouteType.RAG,
            content=None,
            metadata={
                "outcome": determine_assistant_outcome(
                    route=RouteType.RAG.value, rag_used_chunks=0
                )
            },
        )
        result = await env.service.execute_message(
            conversation_id="conv-1", content="空结果问题"
        )

        assert env.orchestrator.contexts[1] is not None
        assert result.metadata["outcome"] is AssistantOutcome.EMPTY
        assert [t.role for t in env.repository.turns()] == [
            TURN_ROLE_USER,
            TURN_ROLE_ASSISTANT,
            TURN_ROLE_USER,
        ]

    async def test_t2sql_zero_rows_regression_with_context(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="A1")
        await env.service.execute_message(conversation_id="conv-1", content="U1")

        env.orchestrator.result = _result(
            content="查询完成：0 行结果",
            route=RouteType.TEXT_TO_SQL,
            metadata={
                "row_count": 0,
                "outcome": determine_assistant_outcome(
                    route=RouteType.TEXT_TO_SQL.value
                ),
            },
        )
        await env.service.execute_message(
            conversation_id="conv-1", content="查没有的订单"
        )

        last_turn = env.repository.turns()[-1]
        assert last_turn.role == TURN_ROLE_ASSISTANT
        assert last_turn.content == "查询完成：0 行结果"
        assert env.orchestrator.contexts[1] is not None

    async def test_assistant_turn_regression_with_context(self, env: _Env) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="A1")
        await env.service.execute_message(conversation_id="conv-1", content="U1")

        env.orchestrator.result = _result(
            content="A2", metadata={"request_id": "req-2"}
        )
        await env.service.execute_message(conversation_id="conv-1", content="U2")

        _, a1, u2, a2 = env.repository.turns()
        assert a1.assistant_request_id == _DEFAULT_REQUEST_ID
        assert u2.assistant_request_id is None
        assert a2.assistant_request_id == "req-2"
        assert a2.content == "A2"

    async def test_user_append_failure_still_skips_history_read(
        self, env: _Env
    ) -> None:
        env.repository.seed_conversation()
        env.repository.fail_append_on = {1}

        with pytest.raises(ConversationRepositoryError):
            await env.service.execute_message(conversation_id="conv-1", content="你好")

        assert env.orchestrator.calls == []
        assert env.repository.list_calls == 0  # USER 写失败 → 未读历史
        assert env.context_builder.received == []

    async def test_assistant_append_failure_still_does_not_retry(
        self, env: _Env
    ) -> None:
        env.repository.seed_conversation()
        env.orchestrator.result = _result(content="A1")
        env.repository.fail_append_on = {2}

        with pytest.raises(ConversationRepositoryError):
            await env.service.execute_message(conversation_id="conv-1", content="U1")

        assert env.orchestrator.calls == ["U1"]
        assert len(env.context_builder.received) == 1
        assert [t.role for t in env.repository.turns()] == [TURN_ROLE_USER]


class TestContextBuilderBoundary:
    """§十：build_context 是唯一入口；Service 不自己拼字符串。"""

    def test_service_uses_build_context_as_only_entry(self) -> None:
        """AST Call 断言（docstring 提及不算调用）：只用 build_context，无手工拼接。"""
        calls = _called_names(_SERVICE)
        assert "build_context" in calls
        assert "build_messages" not in calls
        assert "join" not in calls

    def test_service_imports_context_builder_module(self) -> None:
        modules = _module_imports(_SERVICE)
        assert "backend.app.services.conversation_context_builder" in modules

    def test_current_turn_excluded_by_turn_id_not_last_index(self) -> None:
        tree = ast.parse((_REPO_ROOT / _SERVICE).read_text(encoding="utf-8"))
        comparisons = [
            ast.unparse(node)
            for node in ast.walk(tree)
            if isinstance(node, ast.Compare)
        ]
        assert any("turn.turn_id" in text for text in comparisons), comparisons
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(
                node.slice, ast.UnaryOp
            ):
                assert not isinstance(node.slice.op, ast.USub), ast.unparse(node)

    def test_history_read_happens_before_ai_execution(self) -> None:
        list_lines = _call_lines(_SERVICE, "list_turns")
        execute_lines = _call_lines(_SERVICE, "execute")
        assert list_lines and execute_lines
        assert min(list_lines) < min(execute_lines)


__all__ = [
    "FakeConversationRepository",
    "FakeOrchestrator",
    "FakeContextBuilder",
    "TestNormalSuccess",
    "TestProjectBinding",
    "TestRejectedBeforeAi",
    "TestAiFailure",
    "TestResultSemantics",
    "TestAppendFailure",
    "TestHistoryContext",
    "TestContextBuilderBoundary",
    "TestBoundaryAudit",
]
