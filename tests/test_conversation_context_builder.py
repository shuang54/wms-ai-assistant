"""Conversation Context Builder 契约测试（Phase 4.1 Step 13）。

冻结来源：Phase 4.1 Step 13（Conversation Context Builder 最小实现）

本文件性质：

    * **纯离线**：DB = 0 · Network = 0 · LLM = 0（直接构造 ConversationTurnView）；
    * 输入 DTO = 真实 ``ConversationTurnView``（不 mock Service / Repository）；
    * 覆盖：空历史 / 单轮 / 多轮 / 顺序保持 / 未知 role / content 原样 /
      metadata 隔离 / 输入不可变 / 输出隔离 / 确定性 / 依赖边界（AST）。
"""
from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.app.db.models.conversation_turn import (
    TURN_ROLE_ASSISTANT,
    TURN_ROLE_USER,
)
from backend.app.services.ai_orchestrator_service import AIOrchestratorService
from backend.app.services.conversation_context_builder import (
    ASSISTANT_CONTEXT_ROLE,
    ASSISTANT_TURN_ROLE,
    LINE_SEPARATOR,
    MESSAGE_LINE_FMT,
    USER_CONTEXT_ROLE,
    USER_TURN_ROLE,
    ConversationContextBuilder,
    build_context,
    build_messages,
)
from backend.app.services.conversation_service import ConversationTurnView

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_builder.py"
_BUILDER = "backend/app/services/conversation_context_builder.py"

_BASE = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def _turn(
    role: str,
    content: str,
    *,
    turn_id: int = 1,
    request_id: str | None = None,
    offset: int = 0,
    conversation_id: str = "conv-1",
) -> ConversationTurnView:
    return ConversationTurnView(
        turn_id=turn_id,
        conversation_id=conversation_id,
        role=role,
        content=content,
        assistant_request_id=request_id,
        created_at=_BASE + timedelta(seconds=offset),
    )


@dataclass(frozen=True)
class _DuckTurn:
    """duck-type 输入（只 role / content）。"""

    role: str
    content: str


class _PoisonedTurn:
    """毒化对象：除 role / content 外，任何字段访问都失败。"""

    def __init__(self, role: str, content: str) -> None:
        self._role = role
        self._content = content

    @property
    def role(self) -> str:
        return self._role

    @property
    def content(self) -> str:
        return self._content

    @property
    def turn_id(self) -> int:  # pragma: no cover —— 不应被访问
        raise AssertionError("Builder 不得读取 turn_id")

    @property
    def conversation_id(self) -> str:  # pragma: no cover
        raise AssertionError("Builder 不得读取 conversation_id")

    @property
    def assistant_request_id(self) -> str | None:  # pragma: no cover
        raise AssertionError("Builder 不得读取 assistant_request_id")

    @property
    def created_at(self) -> datetime:  # pragma: no cover
        raise AssertionError("Builder 不得读取 created_at")


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


# ============================================================
# 1. Empty / Single / Multiple
# ============================================================


class TestMappingBasics:
    def test_01_empty_turns_returns_none(self) -> None:
        assert build_context([]) is None
        assert build_messages([]) == ()

    def test_02_single_user(self) -> None:
        turns = [_turn(USER_TURN_ROLE, "查询库存")]

        assert build_messages(turns) == (
            {"role": USER_CONTEXT_ROLE, "content": "查询库存"},
        )
        assert build_context(turns) == "user: 查询库存"

    def test_03_single_assistant(self) -> None:
        turns = [_turn(ASSISTANT_TURN_ROLE, "当前库存为 10", request_id="req-1")]

        assert build_messages(turns) == (
            {"role": ASSISTANT_CONTEXT_ROLE, "content": "当前库存为 10"},
        )
        assert build_context(turns) == "assistant: 当前库存为 10"

    def test_04_user_then_assistant(self) -> None:
        turns = [
            _turn(USER_TURN_ROLE, "查询库存", turn_id=1),
            _turn(ASSISTANT_TURN_ROLE, "当前库存为 10", turn_id=2, request_id="req-1", offset=1),
        ]

        assert build_context(turns) == (
            "user: 查询库存" + LINE_SEPARATOR + "assistant: 当前库存为 10"
        )

    def test_05_multiple_turns(self) -> None:
        turns = [
            _turn(USER_TURN_ROLE, "查询库存", turn_id=1, offset=0),
            _turn(ASSISTANT_TURN_ROLE, "库存 10", turn_id=2, request_id="req-1", offset=1),
            _turn(USER_TURN_ROLE, "A 仓呢", turn_id=3, offset=2),
            _turn(ASSISTANT_TURN_ROLE, "A 仓 4", turn_id=4, request_id="req-2", offset=3),
        ]

        assert build_messages(turns) == (
            {"role": "user", "content": "查询库存"},
            {"role": "assistant", "content": "库存 10"},
            {"role": "user", "content": "A 仓呢"},
            {"role": "assistant", "content": "A 仓 4"},
        )
        assert build_context(turns) == (
            "user: 查询库存\nassistant: 库存 10\nuser: A 仓呢\nassistant: A 仓 4"
        )

    def test_06_duck_typed_input_supported(self) -> None:
        turns = [_DuckTurn(role=USER_TURN_ROLE, content="你好")]

        assert build_context(turns) == "user: 你好"

    def test_07_line_format_constant(self) -> None:
        assert MESSAGE_LINE_FMT.format(role="user", content="x") == "user: x"
        assert LINE_SEPARATOR == "\n"


# ============================================================
# 2. Ordering（Builder 不排序）
# ============================================================


class TestOrdering:
    def test_08_ordering_is_input_order(self) -> None:
        turns = [
            _turn(USER_TURN_ROLE, "第一条", turn_id=1, offset=0),
            _turn(ASSISTANT_TURN_ROLE, "第二条", turn_id=2, request_id="req-1", offset=1),
            _turn(USER_TURN_ROLE, "第三条", turn_id=3, offset=2),
        ]

        assert [m["content"] for m in build_messages(turns)] == [
            "第一条",
            "第二条",
            "第三条",
        ]

    def test_09_builder_does_not_sort_reversed_input(self) -> None:
        """输入顺序与 turn_id / created_at 相反时仍保持输入顺序（不排序）。"""
        turns = [
            _turn(ASSISTANT_TURN_ROLE, "后写入", turn_id=9, request_id="req-9", offset=5),
            _turn(USER_TURN_ROLE, "先写入", turn_id=1, offset=0),
        ]

        assert [m["content"] for m in build_messages(turns)] == ["后写入", "先写入"]

    def test_10_same_timestamp_order_preserved(self) -> None:
        """created_at 相同 → 完全按输入顺序（业务层保证 created_at, turn_id）。"""
        turns = [
            _turn(USER_TURN_ROLE, "同刻-B", turn_id=2, offset=0),
            _turn(USER_TURN_ROLE, "同刻-A", turn_id=1, offset=0),
        ]

        assert [m["content"] for m in build_messages(turns)] == ["同刻-B", "同刻-A"]


# ============================================================
# 3. Role validation
# ============================================================


class TestRoleValidation:
    @pytest.mark.parametrize(
        "bad_role",
        ["SYSTEM", "TOOL", "UNKNOWN", "user", "assistant", "", "USER "],
    )
    def test_11_unknown_role_rejected(self, bad_role: str) -> None:
        with pytest.raises(ValueError):
            build_messages([_turn(bad_role, "内容")])

    def test_12_unknown_role_not_silently_mapped_to_user(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            build_messages([_turn("SYSTEM", "系统指令")])
        assert "SYSTEM" in str(excinfo.value)

    def test_13_non_string_role_rejected(self) -> None:
        with pytest.raises(ValueError):
            build_messages([_DuckTurn(role=1, content="x")])  # type: ignore[arg-type]

    def test_14_role_literals_match_persistence(self) -> None:
        assert USER_TURN_ROLE == TURN_ROLE_USER
        assert ASSISTANT_TURN_ROLE == TURN_ROLE_ASSISTANT
        assert USER_CONTEXT_ROLE == "user"
        assert ASSISTANT_CONTEXT_ROLE == "assistant"


# ============================================================
# 4. Content semantics（原样保留）
# ============================================================


class TestContentSemantics:
    def test_15_empty_content_preserved(self) -> None:
        turns = [_turn(USER_TURN_ROLE, "")]

        assert build_messages(turns) == ({"role": "user", "content": ""},)
        assert build_context(turns) == "user: "

    def test_16_whitespace_content_preserved(self) -> None:
        assert build_context([_turn(ASSISTANT_TURN_ROLE, "   ")]) == "assistant:    "
        assert build_context([_turn(USER_TURN_ROLE, "a\tb\nc ")]) == "user: a\tb\nc "

    def test_17_content_not_truncated_or_normalized(self) -> None:
        long_content = "x" * 5000
        assert build_messages([_turn(USER_TURN_ROLE, long_content)])[0][
            "content"
        ] == long_content

    def test_18_non_string_content_rejected(self) -> None:
        with pytest.raises(ValueError):
            build_messages([_DuckTurn(role=USER_TURN_ROLE, content=123)])  # type: ignore[arg-type]


# ============================================================
# 5. Isolation（metadata / immutability / result）
# ============================================================


class TestIsolation:
    def test_19_metadata_is_not_read(self) -> None:
        """毒化 turn_id / conversation_id / assistant_request_id / created_at。"""
        turns = [_PoisonedTurn(USER_TURN_ROLE, "查询库存")]

        assert build_messages(turns) == (
            {"role": "user", "content": "查询库存"},
        )
        assert build_context(turns) == "user: 查询库存"

    def test_20_no_identifiers_in_output(self) -> None:
        turns = [
            _turn(
                USER_TURN_ROLE,
                "查询库存",
                turn_id=42,
                conversation_id="conv-secret",
                offset=3,
            ),
            _turn(
                ASSISTANT_TURN_ROLE,
                "结果",
                turn_id=43,
                request_id="req-secret",
                offset=4,
            ),
        ]

        rendered = build_context(turns) or ""
        assert "42" not in rendered
        assert "43" not in rendered
        assert "conv-secret" not in rendered
        assert "req-secret" not in rendered

    def test_21_input_immutability(self) -> None:
        turns = [
            _turn(USER_TURN_ROLE, "查询库存", turn_id=1, offset=0),
            _turn(ASSISTANT_TURN_ROLE, "答案", turn_id=2, request_id="req-1", offset=1),
        ]
        snapshot = tuple(turns)

        build_context(turns)
        build_messages(turns)

        assert tuple(turns) == snapshot
        assert turns[0].turn_id == 1
        assert turns[0].content == "查询库存"
        assert turns[1].assistant_request_id == "req-1"
        assert turns[1].created_at == _BASE + timedelta(seconds=1)

    def test_22_mutating_result_does_not_affect_input(self) -> None:
        turns = [_turn(USER_TURN_ROLE, "查询库存")]
        messages = build_messages(turns)

        assert isinstance(messages, tuple)  # 外层不可变
        mutated = dict(messages[0])
        mutated["content"] = "篡改"
        mutated["role"] = "system"
        mutated_container = list(messages) + [mutated]

        assert turns[0].content == "查询库存"
        assert turns[0].role == USER_TURN_ROLE
        assert build_messages(turns) == (
            {"role": "user", "content": "查询库存"},
        )
        assert len(mutated_container) == 2  # 仅本地副本变化

    def test_23_determinism(self) -> None:
        turns = [
            _turn(USER_TURN_ROLE, "查询库存", turn_id=1, offset=0),
            _turn(ASSISTANT_TURN_ROLE, "答案", turn_id=2, request_id="req-1", offset=1),
        ]

        assert build_context(turns) == build_context(turns)
        assert build_messages(turns) == build_messages(turns)

    def test_24_class_wrapper_matches_functions(self) -> None:
        builder = ConversationContextBuilder()
        turns = [_turn(USER_TURN_ROLE, "查询库存")]

        assert builder.build_context(turns) == build_context(turns)
        assert builder.build_messages(turns) == build_messages(turns)
        assert builder.build_context([]) is None


# ============================================================
# 6. 真实 orchestrator contract 兼容
# ============================================================


class TestOrchestratorContract:
    def test_25_context_output_matches_execute_contract(self) -> None:
        params = inspect.signature(AIOrchestratorService.execute).parameters
        assert "context" in params
        assert params["context"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["context"].default is None

        rendered = build_context([_turn(USER_TURN_ROLE, "查询库存")])
        assert isinstance(rendered, str)
        assert build_context([]) is None  # None 是合法 contract 值

    def test_26_returns_no_message_envelope(self) -> None:
        """不伪造 {"messages": []} 结构。"""
        turns = [_turn(USER_TURN_ROLE, "查询库存")]
        assert not isinstance(build_context(turns), dict)

    def test_27_builder_is_not_rag_context_builder(self) -> None:
        """与既有 RAG ContextBuilder 无耦合（输入/输出/符号均不同）。"""
        identifiers = _identifiers(_BUILDER)
        for forbidden in ("VectorSearchResult", "ContextBuildResult", "ContextBuilder"):
            assert forbidden not in identifiers, forbidden


# ============================================================
# 7. 依赖边界（AST：15~20）
# ============================================================


class TestDependencyBoundary:
    def test_28_no_db_imports(self) -> None:
        modules = _module_imports(_BUILDER)
        for module in modules:
            assert not module.startswith("sqlalchemy"), module
            assert not module.startswith("psycopg"), module
            assert not module.startswith("backend.app.db"), module

    def test_29_no_network_imports(self) -> None:
        modules = _module_imports(_BUILDER)
        for module in modules:
            for forbidden in ("httpx", "requests", "aiohttp", "socket", "urllib"):
                assert not module.startswith(forbidden), module

    def test_30_no_llm_imports(self) -> None:
        modules = _module_imports(_BUILDER)
        for module in modules:
            assert not module.startswith("openai"), module
            assert not module.startswith("backend.app.llm"), module

    def test_31_no_conversation_service_dependency(self) -> None:
        modules = _module_imports(_BUILDER)
        assert "backend.app.services.conversation_service" not in modules

    def test_32_no_repository_dependency(self) -> None:
        modules = _module_imports(_BUILDER)
        assert "backend.app.db.conversation_repository" not in modules

    def test_33_no_prompt_dependency(self) -> None:
        modules = _module_imports(_BUILDER)
        assert "backend.app.prompts" not in modules
        for module in modules:
            assert "prompt" not in module.lower(), module

    def test_34_self_audit_offline(self) -> None:
        modules = _module_imports(_SELF)
        for module in modules:
            assert not module.startswith("sqlalchemy"), module
            assert not module.startswith("httpx"), module


__all__ = [
    "TestMappingBasics",
    "TestOrdering",
    "TestRoleValidation",
    "TestContentSemantics",
    "TestIsolation",
    "TestOrchestratorContract",
    "TestDependencyBoundary",
]
