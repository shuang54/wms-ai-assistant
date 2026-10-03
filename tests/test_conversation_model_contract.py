"""Conversation Data Model Contract（Phase 4.1 Step 2 — Design / Freeze Only）。

固化来源：
    `docs/evaluation/Phase 4.1 Step 2 — Conversation Data Model Audit.md`

本文件性质：

    * **纯离线**：DB reads = 0 · DB writes = 0 · network = 0 · LLM = 0
      （不连接 PostgreSQL / 不启动 ASGI app / 不调用任何真实服务）；
    * **Freeze Only**：本 Step **不创建**表 / ORM / Repository / Service / API
      —— 本文件用测试内设计 DTO（``_`` 前缀）固化 Conversation /
      ConversationTurn 的字段、职责、关系与生命周期结论；
    * **复用现有字段风格**：ID 类型（BIGINT 自增主键 / String(128) 业务键）、
      timestamp（DateTime(timezone=True) + server_default）、
      status（String(32) + 大写枚举）、content（Text）均对齐既有实现
      （db/models 中已有先例，测试用源码证据锁定）；
    * 任何"进入 production 的 conversation 代码 / 修改现有 Trace · Timeline ·
      Outcome contract / 敏感字段进入模型"的漂移都会使本文件失败
      —— 这是**有意的漂移报警**。
"""
from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, dataclass, fields
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest

from backend.app.api.assistant_timeline import router as timeline_router
from backend.app.api.assistant_trace import (
    AssistantTraceResponse,
    router as trace_router,
)
from backend.app.dto.assistant_outcome import AssistantOutcome
from backend.app.projects.models import DataSource, ProjectContext

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_model_contract.py"
_AUDIT_DOC = "docs/evaluation/Phase 4.1 Step 2 — Conversation Data Model Audit.md"
_API_DIR = "backend/app/api"
_DB_MODELS_DIR = "backend/app/db/models"
_SERVICES_DIR = "backend/app/services"
_TRACE_API = "backend/app/api/assistant_trace.py"
_TIMELINE_API = "backend/app/api/assistant_timeline.py"
_OUTCOME_MODEL = "backend/app/db/models/assistant_outcome_record.py"
_KNOWLEDGE_DOCUMENT = "backend/app/db/models/knowledge_document.py"
_KNOWLEDGE_CHUNK = "backend/app/db/models/knowledge_chunk.py"
_LLM_USAGE_MODEL = "backend/app/db/models/llm_usage_record.py"
_OUTCOME_DTO = "backend/app/dto/assistant_outcome.py"

_TRACE_PATH = "/observability/assistant-trace/{assistant_request_id}"
_TIMELINE_PATH = "/observability/assistant-timeline/{assistant_request_id}"

#: Turn model 中**禁止**出现的字段名（Security / 职责边界）。
FORBIDDEN_TURN_FIELDS: tuple[str, ...] = (
    "prompt",
    "messages",
    "system_prompt",
    "sql",
    "arguments",
    "tool_result",
    "chunks",
    "embedding",
    "similarity",
    "api_key",
    "password",
    "database_url",
    "authorization",
    "raw_headers",
    "connection_string",
    "session",
    "outcome",
    "success",
    "error_code",
    "attempt",
    "llm_usage_id",
)

#: metadata 守卫禁止键（与 Step 1 同口径 + Step 2 强化）。
FORBIDDEN_METADATA_KEYS: tuple[str, ...] = (
    "api_key",
    "password",
    "database_url",
    "authorization",
    "connection_string",
    "token",
    "secret",
)

_FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "sqlalchemy",
    "psycopg",
    "psycopg2",
    "alembic",
    "redis",
    "celery",
    "kafka",
    "backend.app.db",
    "httpx",
    "requests",
    "aiohttp",
    "openai",
    "socket",
    "urllib",
)

#: Step 2 文档必须包含的章节标题（17 节）。
_REQUIRED_DOC_SECTIONS: tuple[str, ...] = (
    "## 1. Conversation Model",
    "## 2. ConversationTurn Model",
    "## 3. Turn / Request Relationship",
    "## 4. User / Assistant Role",
    "## 5. Project Binding",
    "## 6. History Boundary",
    "## 7. Trace Boundary",
    "## 8. Timeline Boundary",
    "## 9. Outcome Boundary",
    "## 10. Retry / Regenerate",
    "## 11. Delete / Archive",
    "## 12. Retention",
    "## 13. Security",
    "## 14. Index Strategy",
    "## 15. Concurrency",
    "## 16. Idempotency",
    "## 17. Deferred Implementation",
)


# ============================================================
# 设计 DTO / 设计枚举（design-only；本文件私有）
# ============================================================


class _ConversationStatus(StrEnum):
    """Conversation 状态（仅两态；冻结）。"""

    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class _TurnRole(StrEnum):
    """Turn 角色（第一版仅两态；冻结）。"""

    USER = "USER"
    ASSISTANT = "ASSISTANT"


@dataclass(frozen=True)
class _ConversationModel:
    """Conversation 最小模型（5 字段；design-only）。"""

    conversation_id: str
    project_id: str
    created_at: datetime
    updated_at: datetime
    status: _ConversationStatus = _ConversationStatus.ACTIVE

    def __post_init__(self) -> None:
        if not isinstance(self.conversation_id, str) or not self.conversation_id.strip():
            raise ValueError("conversation_id 必须是非空 str（唯一 / 不可变）")
        if not isinstance(self.project_id, str) or not self.project_id.strip():
            raise ValueError("project_id 必须是非空 str（NOT NULL；创建时绑定）")
        for name in ("created_at", "updated_at"):
            if not isinstance(getattr(self, name), datetime):
                raise ValueError(f"{name} 必须是 datetime")
        if not isinstance(self.status, _ConversationStatus):
            raise ValueError(f"status 必须是 _ConversationStatus（当前: {type(self.status).__name__}）")


@dataclass(frozen=True)
class _TurnModel:
    """ConversationTurn 最小模型（6 字段；一行 = 一条消息；design-only）。

    规则（冻结）：

        * USER      → ``assistant_request_id`` 必须为 ``None``；
        * ASSISTANT → ``assistant_request_id`` 必须非空。
    """

    turn_id: int
    conversation_id: str
    role: _TurnRole
    content: str
    assistant_request_id: str | None
    created_at: datetime

    def __post_init__(self) -> None:
        if isinstance(self.turn_id, bool) or not isinstance(self.turn_id, int):
            raise ValueError(f"turn_id 必须是 int（当前: {type(self.turn_id).__name__}）")
        if self.turn_id < 1:
            raise ValueError(f"turn_id 必须 >= 1（当前: {self.turn_id}）")
        if not isinstance(self.conversation_id, str) or not self.conversation_id.strip():
            raise ValueError("conversation_id 必须是非空 str")
        if not isinstance(self.role, _TurnRole):
            raise ValueError(f"role 必须是 _TurnRole（当前: {type(self.role).__name__}）")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("content 必须是非空 str")
        if not isinstance(self.created_at, datetime):
            raise ValueError("created_at 必须是 datetime")
        if self.role is _TurnRole.USER:
            if self.assistant_request_id is not None:
                raise ValueError("USER turn 的 assistant_request_id 必须为 None")
        else:  # ASSISTANT
            if (
                not isinstance(self.assistant_request_id, str)
                or not self.assistant_request_id.strip()
            ):
                raise ValueError("ASSISTANT turn 的 assistant_request_id 必须非空")


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _find_route(router: Any, path: str) -> Any:
    for route in router.routes:
        if getattr(route, "path", None) == path:
            return route
    raise AssertionError(f"route not found: {path}")


def _conversation(**overrides: Any) -> _ConversationModel:
    payload: dict[str, Any] = {
        "conversation_id": "conv-001",
        "project_id": "vietnam-wms",
        "created_at": datetime(2026, 10, 3, 8, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 10, 3, 8, 0, tzinfo=timezone.utc),
    }
    payload.update(overrides)
    return _ConversationModel(**payload)


def _turn(**overrides: Any) -> _TurnModel:
    payload: dict[str, Any] = {
        "turn_id": 1,
        "conversation_id": "conv-001",
        "role": _TurnRole.USER,
        "content": "查询 A001 库存",
        "assistant_request_id": None,
        "created_at": datetime(2026, 10, 3, 8, 0, tzinfo=timezone.utc),
    }
    payload.update(overrides)
    return _TurnModel(**payload)


def _assistant_turn(**overrides: Any) -> _TurnModel:
    payload: dict[str, Any] = {
        "turn_id": 2,
        "role": _TurnRole.ASSISTANT,
        "content": "A001 当前库存 1250 个。",
        "assistant_request_id": "req-A",
    }
    payload.update(overrides)
    return _turn(**payload)


def _assert_metadata_safe(metadata: dict[str, Any]) -> None:
    for key, value in metadata.items():
        assert key.lower() not in FORBIDDEN_METADATA_KEYS, key
        blob = str(value)
        for sentinel in ("postgresql://", "postgres://", "sk-", "Bearer "):
            assert sentinel not in blob, (key, sentinel)


# ============================================================
# 1：Conversation Model
# ============================================================


class TestConversationModel:
    def test_1a_fields_exact_five(self) -> None:
        assert [f.name for f in fields(_ConversationModel)] == [
            "conversation_id",
            "project_id",
            "created_at",
            "updated_at",
            "status",
        ]

    def test_1b_conversation_id_required_unique_immutable(self) -> None:
        with pytest.raises(ValueError):
            _conversation(conversation_id="")
        with pytest.raises(ValueError):
            _conversation(conversation_id="   ")
        conversation = _conversation()
        with pytest.raises(FrozenInstanceError):
            conversation.conversation_id = "conv-002"  # type: ignore[misc]
        assert conversation.conversation_id == "conv-001"

    def test_1c_project_id_not_null_binding(self) -> None:
        with pytest.raises(ValueError):
            _conversation(project_id="")
        with pytest.raises(ValueError):
            _conversation(project_id=None)
        # 与现有 ProjectContext.project_id（非空 str）一致
        context = ProjectContext(
            project_id="vietnam-wms",
            project_name="Vietnam WMS",
            description=None,
            data_source=DataSource(name="primary", type="postgresql"),
        )
        assert context.project_id == _conversation().project_id
        with pytest.raises(FrozenInstanceError):
            _conversation().project_id = "other-project"  # type: ignore[misc]

    def test_1d_status_only_active_archived(self) -> None:
        assert [member.value for member in _ConversationStatus] == [
            "ACTIVE",
            "ARCHIVED",
        ]
        for forbidden in (
            "DELETED",
            "CLOSED",
            "EXPIRED",
            "LOCKED",
            "PROCESSING",
            "FAILED",
        ):
            assert forbidden not in _ConversationStatus.__members__, forbidden
        # 存储风格先例：knowledge_document.status（String(32) + server_default）
        source = _source(_KNOWLEDGE_DOCUMENT)
        assert "String(32)" in source
        assert 'server_default="pending"' in source
        assert "status" in source
        # 枚举风格先例：AssistantOutcome = StrEnum，大写值即对外契约
        assert "StrEnum" in _source(_OUTCOME_DTO)

    def test_1e_created_at_and_updated_at_semantics(self) -> None:
        names = {f.name for f in fields(_ConversationModel)}
        assert {"created_at", "updated_at"} <= names
        doc = _source(_AUDIT_DOC)
        assert "不是第一条消息时间" in doc
        assert "最近一次有效变更时间" in doc

    def test_1f_updated_at_update_triggers_recorded(self) -> None:
        doc = _source(_AUDIT_DOC)
        for marker in (
            "新增 Turn",
            "归档（ACTIVE → ARCHIVED）",
            "读取不更新 updated_at",
        ):
            assert marker in doc, marker


# ============================================================
# 2：ConversationTurn Model
# ============================================================


class TestConversationTurnModel:
    def test_2a_fields_exact_six(self) -> None:
        assert [f.name for f in fields(_TurnModel)] == [
            "turn_id",
            "conversation_id",
            "role",
            "content",
            "assistant_request_id",
            "created_at",
        ]

    def test_2b_turn_id_bigint_style_not_uuid(self) -> None:
        assert isinstance(_turn().turn_id, int)
        with pytest.raises(ValueError):
            _turn(turn_id="1")
        with pytest.raises(ValueError):
            _turn(turn_id=True)
        with pytest.raises(ValueError):
            _turn(turn_id=0)
        # 项目规范证据：主键沿用 BIGINT 自增，明确不引入 UUID / ULID / Snowflake
        source = _source(_LLM_USAGE_MODEL)
        assert "主键沿用项目既有 BIGINT 自增规范" in source
        assert "不引入 UUID / ULID /" in source

    def test_2c_role_only_user_assistant(self) -> None:
        assert [member.value for member in _TurnRole] == ["USER", "ASSISTANT"]
        for forbidden in ("SYSTEM", "TOOL", "FUNCTION", "DEVELOPER"):
            assert forbidden not in _TurnRole.__members__, forbidden
        doc = _source(_AUDIT_DOC)
        assert "Conversation History 不应等同于 LLM raw messages" in doc

    def test_2d_content_is_final_text_only(self) -> None:
        with pytest.raises(ValueError):
            _turn(content="   ")
        with pytest.raises(ValueError):
            _turn(content=None)
        doc = _source(_AUDIT_DOC)
        for marker in (
            "不得保存完整 LLM prompt",
            "RAG chunks",
            "Tool arguments",
            "System Prompt",
        ):
            assert marker in doc, marker

    def test_2e_assistant_request_id_optional_by_role(self) -> None:
        user = _turn(role=_TurnRole.USER, assistant_request_id=None)
        assert user.assistant_request_id is None
        assistant = _assistant_turn()
        assert assistant.assistant_request_id == "req-A"
        with pytest.raises(ValueError):
            _turn(role=_TurnRole.USER, assistant_request_id="req-A")
        with pytest.raises(ValueError):
            _assistant_turn(assistant_request_id=None)

    def test_2f_string_id_width_reuses_existing_style(self) -> None:
        # 现有业务键列宽先例：String(128)（request_id / assistant_request_id / project_id）
        source = _source(_OUTCOME_MODEL)
        assert "String(128)" in source
        # 内容列先例：Text（knowledge_chunk.content）
        chunk = _source(_KNOWLEDGE_CHUNK)
        assert "Text," in chunk


# ============================================================
# 3：Turn / Request Relationship
# ============================================================


class TestTurnRequestRelationship:
    def test_3a_one_conversation_many_turns(self) -> None:
        conversation = _conversation()
        turns = (
            _turn(turn_id=1, role=_TurnRole.USER),
            _assistant_turn(turn_id=2, assistant_request_id="req-A"),
            _turn(turn_id=3, role=_TurnRole.USER, content="那 B 仓呢？"),
            _assistant_turn(turn_id=4, assistant_request_id="req-B"),
        )
        assert {t.conversation_id for t in turns} == {conversation.conversation_id}
        assert [t.turn_id for t in turns] == [1, 2, 3, 4]

    def test_3b_user_turn_no_request_assistant_turn_has_request(self) -> None:
        user = _turn(role=_TurnRole.USER)
        assistant = _assistant_turn()
        assert user.assistant_request_id is None
        assert assistant.assistant_request_id is not None

    def test_3c_message_row_model_not_merged_row(self) -> None:
        """选定：一条消息一行（USER / ASSISTANT 各占一行），非合并行。"""
        user = _turn(turn_id=1, role=_TurnRole.USER)
        assistant = _assistant_turn(turn_id=2)
        assert user.role is not assistant.role
        assert user.turn_id != assistant.turn_id
        names = {f.name for f in fields(_TurnModel)}
        assert "user_content" not in names
        assert "assistant_content" not in names
        doc = _source(_AUDIT_DOC)
        assert "一条消息一行" in doc


# ============================================================
# 4：Retry / Regenerate / T2SQL retry
# ============================================================


class TestRetryAndRegenerate:
    def test_4a_multiple_assistant_turns_per_user_turn_allowed(self) -> None:
        user = _turn(turn_id=1, role=_TurnRole.USER)
        failed = _assistant_turn(turn_id=2, assistant_request_id="req-A")
        regenerated = _assistant_turn(turn_id=3, assistant_request_id="req-B")
        assert len({failed.assistant_request_id, regenerated.assistant_request_id}) == 2
        assert failed.conversation_id == regenerated.conversation_id == user.conversation_id
        doc = _source(_AUDIT_DOC)
        assert "多个 Assistant Turn" in doc

    def test_4b_design_has_no_attempt_or_outcome_fields(self) -> None:
        names = {f.name for f in fields(_TurnModel)}
        for forbidden in (
            "attempt",
            "llm_usage_id",
            "retry_count",
            "outcome",
            "success",
            "error_code",
            "model",
        ):
            assert forbidden not in names, forbidden

    def test_4c_t2sql_retry_is_request_internal(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "一个 LLM attempt = 一个 Conversation Turn" in doc
        assert "多个 LLM usage" in doc


# ============================================================
# 5：Security / Forbidden fields
# ============================================================


class TestSecurityBoundary:
    def test_5a_turn_and_conversation_have_no_forbidden_fields(self) -> None:
        names = {f.name for f in fields(_TurnModel)} | {
            f.name for f in fields(_ConversationModel)
        }
        for forbidden in FORBIDDEN_TURN_FIELDS:
            assert forbidden not in names, forbidden

    def test_5b_metadata_guard_rejects_sensitive_fields(self) -> None:
        _assert_metadata_safe(
            {
                "conversation_id": "conv-001",
                "assistant_request_id": "req-A",
                "role": "ASSISTANT",
            }
        )
        for key in FORBIDDEN_METADATA_KEYS:
            with pytest.raises(AssertionError):
                _assert_metadata_safe({key: "x"})
        with pytest.raises(AssertionError):
            _assert_metadata_safe({"endpoint": "postgresql://user:pw@host/db"})

    def test_5c_design_doc_declares_forbidden_content(self) -> None:
        doc = _source(_AUDIT_DOC)
        for marker in (
            "api_key",
            "password",
            "database_url",
            "Authorization",
            "raw headers",
            "embedding",
            "system prompt",
        ):
            assert marker in doc, marker

    def test_5d_history_is_more_sensitive_than_trace(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "Conversation History 未来可能比 Trace 更敏感" in doc


# ============================================================
# 6：Index / Concurrency / Idempotency 设计结论
# ============================================================


class TestDesignDecisions:
    def test_6a_index_strategy_minimal(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "(conversation_id, created_at)" in doc
        assert "不为 assistant_request_id 建索引" in doc

    def test_6b_unique_constraints(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "conversation_id UNIQUE" in doc
        assert "turn_id UNIQUE" in doc
        assert "不加全局 UNIQUE" in doc

    def test_6c_lifecycle_separation(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "Conversation lifecycle ≠ Observability lifecycle" in doc
        assert "Conversation 不负责删除 Observability 历史记录" in doc

    def test_6d_not_in_public_schema(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "不得放入 public" in doc

    def test_6e_id_boundary_frozen(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "conversation_id ≠ assistant_request_id" in doc
        assert "project_id NOT NULL" in doc
        assert "network = 0" in doc


# ============================================================
# 7：Existing boundary 不发生变化（Trace / Timeline / Outcome）
# ============================================================


class TestExistingBoundaryUnchanged:
    def test_7a_trace_api_unchanged(self) -> None:
        route = _find_route(trace_router, _TRACE_PATH)
        assert set(route.methods) == {"GET"}
        assert route.dependant.path_params[0].name == "assistant_request_id"
        assert list(AssistantTraceResponse.model_fields) == [
            "assistant_request_id",
            "outcome",
            "llm_usage",
            "tool_executions",
            "rag_executions",
        ]
        assert "conversation_id" not in _source(_TRACE_API)

    def test_7b_timeline_api_unchanged(self) -> None:
        route = _find_route(timeline_router, _TIMELINE_PATH)
        assert set(route.methods) == {"GET"}
        assert route.dependant.path_params[0].name == "assistant_request_id"
        assert "conversation_id" not in _source(_TIMELINE_API)

    def test_7c_outcome_contract_unchanged(self) -> None:
        assert [member.value for member in AssistantOutcome] == [
            "SUCCESS",
            "EMPTY",
            "REFUSED",
            "FAILED",
        ]
        source = _source(_OUTCOME_MODEL)
        assert "assistant_request_id" in source
        assert "conversation_id" not in source


# ============================================================
# 8：No production implementation
# ============================================================


class TestProductionImplementationScope:
    """Step 5 起 Conversation 持久化层已实现：守卫改为**范围**校验。

    * 只允许 Step 5 冻结的文件 / 类存在（多出的 conversation 代码 = 漂移）；
    * API 层仍然不允许出现 conversation 模块（Conversation API = NOT IMPLEMENTED）。
    """

    #: Step 5 允许存在的生产文件（相对仓库根）。
    ALLOWED_CONVERSATION_MODULES: tuple[str, ...] = (
        "backend/app/db/models/conversation.py",
        "backend/app/db/models/conversation_turn.py",
        "backend/app/db/conversation_repository.py",
        "backend/app/services/conversation_service.py",
    )

    #: 只允许在这些（Step 5 冻结的）文件中定义 Conversation* 类。
    ALLOWED_CONVERSATION_CLASS_FILES: frozenset[str] = frozenset(
        {
            "conversation.py",
            "conversation_turn.py",
            "conversation_repository.py",
            "conversation_service.py",
        }
    )

    def test_8a_conversation_files_are_within_allowed_scope(self) -> None:
        found: list[str] = []
        for sub in ("db", "api", "services"):
            root = _REPO_ROOT / "backend" / "app" / sub
            found.extend(
                str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
                for path in root.rglob("conversation*.py")
            )
        assert sorted(found) == sorted(self.ALLOWED_CONVERSATION_MODULES)

    def test_8b_no_conversation_api_module(self) -> None:
        root = _REPO_ROOT / _API_DIR
        assert [p.name for p in root.rglob("conversation*.py")] == []

    def test_8c_conversation_classes_only_in_allowed_files(self) -> None:
        """Conversation* 类只能定义于 Step 5 冻结的文件（其余文件 = 漂移）。"""
        for sub in (_DB_MODELS_DIR, _SERVICES_DIR, _API_DIR):
            root = _REPO_ROOT / sub
            for path in sorted(root.rglob("*.py")):
                # utf-8-sig：兼容个别带 BOM 的既有源文件（不跳过任何文件）
                tree = ast.parse(path.read_text(encoding="utf-8-sig"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef) and node.name.startswith(
                        "Conversation"
                    ):
                        assert path.name in self.ALLOWED_CONVERSATION_CLASS_FILES, (
                            f"{path.name}:{node.name}"
                        )

    def test_8c_no_conversation_routes_registered(self) -> None:
        api_root = _REPO_ROOT / _API_DIR
        for path in sorted(api_root.glob("*.py")):
            assert "/conversations" not in path.read_text(encoding="utf-8"), path.name

    def test_8d_design_doc_records_unchanged_invariants(self) -> None:
        doc = _source(_AUDIT_DOC)
        for marker in (
            "DB = unchanged",
            "API = unchanged",
            "Orchestrator = unchanged",
            "RAG = unchanged",
            "Tool = unchanged",
            "LLM = unchanged",
        ):
            assert marker in doc, marker


# ============================================================
# 9：Design doc 完整性（17 个必需章节）
# ============================================================


class TestDesignDocument:
    def test_9a_required_sections_present(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in _REQUIRED_DOC_SECTIONS:
            assert section in doc, section

    def test_9b_references_step_one(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "Phase 4.1 Step 1" in doc


# ============================================================
# 10：自身 import 的 AST 审计（纯离线守卫）
# ============================================================


class TestSelfAudit:
    def test_10a_self_import_is_offline(self) -> None:
        tree = ast.parse(_source(_SELF))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for prefix in _FORBIDDEN_IMPORT_PREFIXES:
                assert not module.startswith(prefix), module

    def test_10b_no_db_engine_identifiers(self) -> None:
        tree = ast.parse(_source(_SELF))
        identifiers = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        for forbidden in (
            "get_" + "engine",
            "create_" + "engine",
            "get_session_" + "factory",
            "session" + "maker",
        ):
            assert forbidden not in identifiers, forbidden


__all__ = [
    "FORBIDDEN_TURN_FIELDS",
    "TestConversationModel",
    "TestConversationTurnModel",
    "TestTurnRequestRelationship",
    "TestRetryAndRegenerate",
    "TestSecurityBoundary",
    "TestDesignDecisions",
    "TestExistingBoundaryUnchanged",
    "TestProductionImplementationScope",
    "TestDesignDocument",
    "TestSelfAudit",
]
