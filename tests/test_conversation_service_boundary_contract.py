"""Conversation Service Boundary Contract（Phase 4.1 Step 3 — Design / Freeze Only）。

固化来源：
    `docs/evaluation/Phase 4.1 Step 3 — Conversation Service Boundary Audit.md`

本文件性质：

    * **纯离线**：DB reads = 0 · DB writes = 0 · network = 0 · LLM = 0
      （不连接 PostgreSQL / 不启动 ASGI app / 不调用任何真实服务）；
    * **Freeze Only**：本 Step **不创建** ConversationService / Repository /
      ORM / API —— 本文件用测试内设计骨架（``_`` 前缀）固化
      Service Contract（方法 / 参数 / 返回 / 异常）、Repository Boundary、
      Create / Archive / Append Turn 规则与失败处理语义；
    * **复用现有分层风格**：Service 构造注入 Repository（None → 默认）、
      同步 DB 方法、Query 语义 → ``None``、命令语义 → 显式异常、
      字段校验 → ``ValueError``（与 services / db 既有实现一致，测试用
      真实文件证据锁定）；
    * 任何"进入 production 的 conversation 代码 / Service 依赖 FastAPI ·
      AI Core / 复制 Outcome / 修改现有 Trace · Timeline · Outcome contract"
      的漂移都会使本文件失败 —— 这是**有意的漂移报警**。
"""
from __future__ import annotations

import ast
import inspect
import uuid
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

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_service_boundary_contract.py"
_AUDIT_DOC = "docs/evaluation/Phase 4.1 Step 3 — Conversation Service Boundary Audit.md"
_APP_DIR = "backend/app"
_API_DIR = "backend/app/api"
_SERVICES_DIR = "backend/app/services"
_DB_DIR = "backend/app/db"
_TRACE_API = "backend/app/api/assistant_trace.py"
_TIMELINE_API = "backend/app/api/assistant_timeline.py"
_OUTCOME_MODEL = "backend/app/db/models/assistant_outcome_record.py"
_OUTCOME_SERVICE = "backend/app/services/assistant_outcome_query_service.py"
_TOOL_PERSIST_SERVICE = "backend/app/services/tool_execution_persistence_service.py"
_CHAT_SERVICE = "backend/app/services/chat_service.py"

_TRACE_PATH = "/observability/assistant-trace/{assistant_request_id}"
_TIMELINE_PATH = "/observability/assistant-timeline/{assistant_request_id}"

#: Service 构造允许的依赖（唯一 = Repository；设计冻结）。
SERVICE_ALLOWED_DEPENDENCIES: tuple[str, ...] = ("repository",)

#: Service 对外方法（设计冻结；5 个，不多不少）。
SERVICE_METHODS: tuple[str, ...] = (
    "create_conversation",
    "get_conversation",
    "archive_conversation",
    "append_turn",
    "list_turns",
)

#: Repository 方法（设计冻结；5 个，不多不少）。
REPOSITORY_METHODS: tuple[str, ...] = (
    "create",
    "get_by_conversation_id",
    "update_status",
    "append_turn",
    "list_turns_by_conversation_id",
)

#: 未来 Conversation Service **禁止**依赖的既有服务（Trace Separation）。
FORBIDDEN_SERVICE_DEPENDENCIES: tuple[str, ...] = (
    "AIOrchestratorService",
    "AIRouterService",
    "RagService",
    "ToolExecutionService",
    "LLMUsageQueryService",
    "ToolObservabilityQueryService",
    "RagExecutionPersistentQueryService",
    "AssistantTraceQueryService",
    "AssistantTimelineQueryService",
    "AssistantOutcomeQueryService",
    "LLMClient",
)

#: 未来 Conversation Service **禁止**出现的 FastAPI 符号。
FORBIDDEN_FASTAPI_SYMBOLS: tuple[str, ...] = (
    "Request",
    "Response",
    "HTTPException",
    "Depends",
    "APIRouter",
)

#: Turn / Conversation 设计中禁止的敏感字段（Security，延续 Step 2）。
FORBIDDEN_SENSITIVE_FIELDS: tuple[str, ...] = (
    "api_key",
    "password",
    "database_url",
    "authorization",
    "raw_headers",
    "prompt",
    "messages",
    "system_prompt",
    "sql",
    "chunks",
    "embedding",
    "tool_payload",
    "session",
)

#: 冻结的 Create 输入字段（客户端只能给 project_id）。
CREATE_INPUT_FIELDS: tuple[str, ...] = ("project_id",)

#: 冻结的 archive 状态迁移（仅两态；幂等）。
ARCHIVE_TRANSITIONS: dict[str, str] = {
    "ACTIVE": "ARCHIVED",
    "ARCHIVED": "ARCHIVED",
}

_REQUIRED_DOC_SECTIONS: tuple[str, ...] = (
    "## 1. Service Responsibility",
    "## 2. Create Conversation",
    "## 3. Archive Conversation",
    "## 4. Project Binding",
    "## 5. Append Turn",
    "## 6. Assistant Request Correlation",
    "## 7. Outcome Boundary",
    "## 8. Failure Handling",
    "## 9. Regenerate",
    "## 10. Repository Boundary",
    "## 11. Transaction Boundary",
    "## 12. Read / Write Separation",
    "## 13. Trace Separation",
    "## 14. Security",
    "## 15. Contract Tests",
    "## 16. Deferred Implementation",
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
    "fastapi",
)


# ============================================================
# 设计枚举 / DTO / 异常（design-only；本文件私有）
# ============================================================


class _ConversationStatusDesign(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class _TurnRoleDesign(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


@dataclass(frozen=True)
class _ConversationViewDesign:
    """Service 返回的 Conversation 视图（字段 = Step 2 冻结模型）。"""

    conversation_id: str
    project_id: str
    created_at: datetime
    updated_at: datetime
    status: _ConversationStatusDesign = _ConversationStatusDesign.ACTIVE

    def __post_init__(self) -> None:
        if not isinstance(self.conversation_id, str) or not self.conversation_id.strip():
            raise ValueError("conversation_id 必须是非空 str")
        if not isinstance(self.project_id, str) or not self.project_id.strip():
            raise ValueError("project_id 必须是非空 str")


@dataclass(frozen=True)
class _TurnViewDesign:
    """Service 返回的 Turn 视图（字段 = Step 2 冻结模型）。"""

    turn_id: int
    conversation_id: str
    role: _TurnRoleDesign
    content: str
    assistant_request_id: str | None
    created_at: datetime

    def __post_init__(self) -> None:
        if isinstance(self.turn_id, bool) or not isinstance(self.turn_id, int):
            raise ValueError("turn_id 必须是 int")
        if self.turn_id < 1:
            raise ValueError("turn_id 必须 >= 1")
        if not isinstance(self.conversation_id, str) or not self.conversation_id.strip():
            raise ValueError("conversation_id 必须是非空 str")
        if not isinstance(self.role, _TurnRoleDesign):
            raise ValueError("role 必须是 _TurnRoleDesign")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("content 必须是非空 str")
        if self.role is _TurnRoleDesign.USER:
            if self.assistant_request_id is not None:
                raise ValueError("USER turn 的 assistant_request_id 必须为 None")
        else:
            if (
                not isinstance(self.assistant_request_id, str)
                or not self.assistant_request_id.strip()
            ):
                raise ValueError("ASSISTANT turn 的 assistant_request_id 必须非空")


@dataclass(frozen=True)
class _CreateConversationInputDesign:
    """Create 输入（设计冻结：客户端只允许提供 project_id）。"""

    project_id: str


class _ConversationServiceError(Exception):
    """Conversation Service 边界错误基类（Design）。"""


class _ConversationNotFoundError(_ConversationServiceError):
    """conversation 不存在（命令路径）。"""


class _ConversationArchivedError(_ConversationServiceError):
    """ARCHIVED 会话禁止追加 Turn。"""


# ============================================================
# 设计骨架（design-only；仅签名，无任何业务实现）
# ============================================================


class _ConversationRepositoryContractDesign:
    """ConversationRepository 设计契约（5 方法；仅数据库访问）。"""

    def create(
        self,
        *,
        conversation_id: str,
        project_id: str,
        status: str,
    ) -> Any: ...

    def get_by_conversation_id(self, conversation_id: str) -> Any | None: ...

    def update_status(self, conversation_id: str, status: str) -> Any | None: ...

    def append_turn(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        assistant_request_id: str | None,
    ) -> Any: ...

    def list_turns_by_conversation_id(self, conversation_id: str) -> tuple: ...


class _ConversationServiceContractDesign:
    """ConversationService 设计契约（5 方法；唯一依赖 = repository）。"""

    def __init__(
        self,
        repository: _ConversationRepositoryContractDesign | None = None,
    ) -> None:
        self._repository = repository

    def create_conversation(self, *, project_id: str) -> Any: ...

    def get_conversation(self, conversation_id: str) -> Any | None: ...

    def archive_conversation(self, conversation_id: str) -> Any: ...

    def append_turn(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        assistant_request_id: str | None,
    ) -> Any: ...

    def list_turns(self, conversation_id: str) -> tuple: ...


# ============================================================
# 设计规则（纯函数；仅用于契约验证，不是 production 实现）
# ============================================================


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _signature_params(func: Any) -> dict[str, Any]:
    return {
        name: parameter.kind
        for name, parameter in inspect.signature(func).parameters.items()
        if name != "self"
    }


def _has_required_params(func: Any) -> list[str]:
    return [
        name
        for name, parameter in inspect.signature(func).parameters.items()
        if name != "self"
        and parameter.default is inspect.Parameter.empty
    ]


def _new_conversation_id_design() -> str:
    """服务端生成 conversation_id（uuid4 风格；与 new_request_id 一致）。"""
    return str(uuid.uuid4())


def _create_conversation_design(
    project_id: str,
    *,
    now: datetime,
) -> _ConversationViewDesign:
    """Create 规则（设计验证）：服务端生成 id / ACTIVE / updated_at = created_at。"""
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("project_id 必须是非空 str")
    if len(project_id) > 128:
        raise ValueError("project_id 超出长度上限（> 128）")
    return _ConversationViewDesign(
        conversation_id=_new_conversation_id_design(),
        project_id=project_id,
        created_at=now,
        updated_at=now,
        status=_ConversationStatusDesign.ACTIVE,
    )


def _archive_status_design(current: str) -> str:
    """Archive 规则（设计验证）：ACTIVE → ARCHIVED；ARCHIVED → ARCHIVED（幂等）。"""
    try:
        return ARCHIVE_TRANSITIONS[current]
    except KeyError as exc:
        raise ValueError(f"未知 status: {current!r}") from exc


def _append_turn_design(
    conversation: _ConversationViewDesign,
    *,
    next_turn_id: int,
    role: _TurnRoleDesign,
    content: str,
    assistant_request_id: str | None,
    now: datetime,
) -> _TurnViewDesign:
    """Append 规则（设计验证）：ARCHIVED 拒绝；USER/ASSISTANT request 规则。"""
    if conversation.status is _ConversationStatusDesign.ARCHIVED:
        raise _ConversationArchivedError(
            "ARCHIVED 会话不允许追加 Turn"
        )
    return _TurnViewDesign(
        turn_id=next_turn_id,
        conversation_id=conversation.conversation_id,
        role=role,
        content=content,
        assistant_request_id=assistant_request_id,
        created_at=now,
    )


def _conversation_view(**overrides: Any) -> _ConversationViewDesign:
    payload: dict[str, Any] = {
        "conversation_id": "conv-001",
        "project_id": "vietnam-wms",
        "created_at": datetime(2026, 10, 3, 8, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 10, 3, 8, 0, tzinfo=timezone.utc),
    }
    payload.update(overrides)
    return _ConversationViewDesign(**payload)


# ============================================================
# 1：Conversation Service 只属于 Conversation Layer
# ============================================================


class TestServiceLayerBoundary:
    def test_1a_service_methods_frozen_exactly_five(self) -> None:
        actual = {
            name
            for name in vars(_ConversationServiceContractDesign)
            if not name.startswith("_")
        }
        assert actual == set(SERVICE_METHODS)

    def test_1b_service_signatures_frozen(self) -> None:
        create = _signature_params(
            _ConversationServiceContractDesign.create_conversation
        )
        assert create == {"project_id": inspect.Parameter.KEYWORD_ONLY}
        assert _has_required_params(
            _ConversationServiceContractDesign.create_conversation
        ) == ["project_id"]

        get = _signature_params(_ConversationServiceContractDesign.get_conversation)
        assert get == {"conversation_id": inspect.Parameter.POSITIONAL_OR_KEYWORD}

        archive = _signature_params(
            _ConversationServiceContractDesign.archive_conversation
        )
        assert archive == {"conversation_id": inspect.Parameter.POSITIONAL_OR_KEYWORD}

        append = _signature_params(_ConversationServiceContractDesign.append_turn)
        assert append == {
            "conversation_id": inspect.Parameter.KEYWORD_ONLY,
            "role": inspect.Parameter.KEYWORD_ONLY,
            "content": inspect.Parameter.KEYWORD_ONLY,
            "assistant_request_id": inspect.Parameter.KEYWORD_ONLY,
        }
        assert sorted(
            _has_required_params(_ConversationServiceContractDesign.append_turn)
        ) == [
            "assistant_request_id",
            "content",
            "conversation_id",
            "role",
        ]

        list_turns = _signature_params(_ConversationServiceContractDesign.list_turns)
        assert list_turns == {
            "conversation_id": inspect.Parameter.POSITIONAL_OR_KEYWORD
        }

    def test_1c_service_constructor_dependency_frozen(self) -> None:
        params = inspect.signature(
            _ConversationServiceContractDesign.__init__
        ).parameters
        assert [name for name in params if name != "self"] == list(
            SERVICE_ALLOWED_DEPENDENCIES
        )
        assert params["repository"].default is None

    def test_1d_service_methods_are_synchronous(self) -> None:
        for name in SERVICE_METHODS:
            method = getattr(_ConversationServiceContractDesign, name)
            assert not inspect.iscoroutinefunction(method), name

    def test_1e_design_doc_declares_service_lifecycle_scope(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "Conversation Service 只负责 Conversation 生命周期" in doc
        assert "ConversationService 不负责 AI Execution" in doc


# ============================================================
# 2：不依赖 LLM / RAG / Tool / Router / AIOrchestrator
# ============================================================


class TestNoAiCoreDependency:
    def test_2a_forbidden_dependencies_not_in_constructor(self) -> None:
        params = set(
            inspect.signature(_ConversationServiceContractDesign.__init__).parameters
        )
        for forbidden in FORBIDDEN_SERVICE_DEPENDENCIES:
            assert forbidden not in params, forbidden
        assert "repository" in params

    def test_2b_only_repository_dependency_allowed(self) -> None:
        assert SERVICE_ALLOWED_DEPENDENCIES == ("repository",)
        doc = _source(_AUDIT_DOC)
        assert "ConversationService 不调用 AIOrchestrator" in doc
        for marker in (
            "RagService",
            "ToolExecutionService",
            "AIRouterService",
            "LLMClient",
        ):
            assert marker in doc, marker


# ============================================================
# 3：不依赖 FastAPI
# ============================================================


class TestNoFastApiDependency:
    def test_3a_design_doc_declares_no_fastapi_dependency(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "ConversationService 不得依赖 FastAPI" in doc
        for marker in FORBIDDEN_FASTAPI_SYMBOLS:
            assert marker in doc, marker

    def test_3b_conversation_production_modules_do_not_import_fastapi(self) -> None:
        """Service / Repository / ORM / DTO 层不得 import FastAPI。

        Step 8 起 Conversation API（``api/conversations.py``）是 FastAPI
        路由模块、必然 import fastapi —— 因此本守卫范围收窄为"非 api
        目录的 conversation 生产模块"；API 层由 architecture contract 的
        "唯一属主 + 冻结路径"守卫负责。
        """
        modules = sorted(
            path
            for path in (_REPO_ROOT / _APP_DIR).rglob("conversation*.py")
            if path.parent.name != "api"
        )
        assert modules, "AST 未解析到模块（审计失效）"
        for path in modules:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("fastapi"), str(path)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("fastapi"), str(path)

    def test_3c_self_import_is_free_of_fastapi(self) -> None:
        tree = ast.parse(_source(_SELF))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        for module in modules:
            assert not module.startswith("fastapi"), module


# ============================================================
# 4：不依赖 DeepSeek / network
# ============================================================


class TestNoNetworkOrLlmDependency:
    def test_4a_self_import_is_offline(self) -> None:
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

    def test_4b_design_doc_declares_no_network_no_llm(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "network = 0" in doc
        assert "DeepSeek" in doc


# ============================================================
# 5：Create Contract
# ============================================================


class TestCreateContract:
    def test_5a_create_input_only_project_id(self) -> None:
        assert [f.name for f in fields(_CreateConversationInputDesign)] == list(
            CREATE_INPUT_FIELDS
        )
        for forbidden in (
            "conversation_id",
            "status",
            "created_at",
            "updated_at",
            "user_id",
        ):
            assert forbidden not in CREATE_INPUT_FIELDS, forbidden

    def test_5b_create_rules_server_generated(self) -> None:
        now = datetime(2026, 10, 3, 8, 0, tzinfo=timezone.utc)
        created = _create_conversation_design("vietnam-wms", now=now)
        assert created.conversation_id
        assert created.conversation_id != created.project_id
        assert created.status is _ConversationStatusDesign.ACTIVE
        assert created.updated_at == created.created_at == now
        other = _create_conversation_design("vietnam-wms", now=now)
        assert other.conversation_id != created.conversation_id

    def test_5c_create_rejects_invalid_project_id(self) -> None:
        now = datetime(2026, 10, 3, 8, 0, tzinfo=timezone.utc)
        with pytest.raises(ValueError):
            _create_conversation_design("", now=now)
        with pytest.raises(ValueError):
            _create_conversation_design("   ", now=now)
        with pytest.raises(ValueError):
            _create_conversation_design(None, now=now)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            _create_conversation_design("x" * 129, now=now)

    def test_5d_design_doc_declares_create_rules(self) -> None:
        doc = _source(_AUDIT_DOC)
        for marker in (
            "conversation_id 由服务端生成",
            "status = ACTIVE",
            "updated_at = created_at",
        ):
            assert marker in doc, marker


# ============================================================
# 6：Archive Contract（幂等）
# ============================================================


class TestArchiveContract:
    def test_6a_archive_transitions_idempotent(self) -> None:
        assert _archive_status_design("ACTIVE") == "ARCHIVED"
        assert _archive_status_design("ARCHIVED") == "ARCHIVED"  # 第二次归档不报错
        assert set(ARCHIVE_TRANSITIONS) == {"ACTIVE", "ARCHIVED"}
        assert "ARCHIVE_FAILED" not in ARCHIVE_TRANSITIONS

    def test_6b_archived_rejects_append(self) -> None:
        archived = _conversation_view(status=_ConversationStatusDesign.ARCHIVED)
        with pytest.raises(_ConversationArchivedError):
            _append_turn_design(
                archived,
                next_turn_id=1,
                role=_TurnRoleDesign.USER,
                content="再来一个问题",
                assistant_request_id=None,
                now=datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc),
            )

    def test_6c_design_doc_declares_archive_semantics(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "ARCHIVED → ARCHIVED" in doc
        assert "第二次归档不报错" in doc
        assert "禁止" in doc and "追加 Turn" in doc


# ============================================================
# 7：Project Binding（immutable）
# ============================================================


class TestProjectBinding:
    def test_7a_project_id_immutable(self) -> None:
        conversation = _conversation_view()
        with pytest.raises(FrozenInstanceError):
            conversation.project_id = "other-project"  # type: ignore[misc]

    def test_7b_lookup_by_conversation_id_only(self) -> None:
        # get_conversation 只有 conversation_id 一个参数（不接受客户端 project_id 覆盖）
        params = _signature_params(
            _ConversationServiceContractDesign.get_conversation
        )
        assert list(params) == ["conversation_id"]
        repository_params = _signature_params(
            _ConversationRepositoryContractDesign.get_by_conversation_id
        )
        assert list(repository_params) == ["conversation_id"]

    def test_7c_design_doc_declares_project_binding(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "创建后不可修改" in doc
        assert "必须根据 conversation_id 找到 project_id" in doc
        assert "不能通过客户端传入另一个" in doc


# ============================================================
# 8：Append Turn Contract（Step 2 规则延续）
# ============================================================


class TestAppendTurnContract:
    def test_8a_user_turn_has_no_assistant_request_id(self) -> None:
        conversation = _conversation_view()
        turn = _append_turn_design(
            conversation,
            next_turn_id=1,
            role=_TurnRoleDesign.USER,
            content="查询 A001 库存",
            assistant_request_id=None,
            now=datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc),
        )
        assert turn.assistant_request_id is None
        with pytest.raises(ValueError):
            _append_turn_design(
                conversation,
                next_turn_id=1,
                role=_TurnRoleDesign.USER,
                content="查询 A001 库存",
                assistant_request_id="req-A",
                now=datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc),
            )

    def test_8b_assistant_turn_requires_assistant_request_id(self) -> None:
        conversation = _conversation_view()
        turn = _append_turn_design(
            conversation,
            next_turn_id=2,
            role=_TurnRoleDesign.ASSISTANT,
            content="A001 当前库存 1250 个。",
            assistant_request_id="req-A",
            now=datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc),
        )
        assert turn.assistant_request_id == "req-A"
        with pytest.raises(ValueError):
            _append_turn_design(
                conversation,
                next_turn_id=2,
                role=_TurnRoleDesign.ASSISTANT,
                content="A001 当前库存 1250 个。",
                assistant_request_id=None,
                now=datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc),
            )

    def test_8c_design_doc_declares_append_rules(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "USER → assistant_request_id = None" in doc
        assert "ASSISTANT → assistant_request_id 必须非空" in doc


# ============================================================
# 9：Outcome separation
# ============================================================


class TestOutcomeSeparation:
    def test_9a_turn_has_no_outcome_fields(self) -> None:
        names = {f.name for f in fields(_TurnViewDesign)}
        for forbidden in (
            "outcome",
            "status",
            "success",
            "error_code",
            "failure_reason",
        ):
            assert forbidden not in names, forbidden

    def test_9b_outcome_enum_unchanged_four_states(self) -> None:
        assert [member.value for member in AssistantOutcome] == [
            "SUCCESS",
            "EMPTY",
            "REFUSED",
            "FAILED",
        ]
        assert "conversation" not in _source(_OUTCOME_MODEL)

    def test_9c_refused_content_turn_is_legal_combination(self) -> None:
        # 合法状态：Turn 有 content，Outcome = REFUSED（两者不合并）
        turn = _TurnViewDesign(
            turn_id=2,
            conversation_id="conv-001",
            role=_TurnRoleDesign.ASSISTANT,
            content="抱歉，我无法执行这个请求。",
            assistant_request_id="req-A",
            created_at=datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc),
        )
        assert turn.content
        assert AssistantOutcome.REFUSED.value == "REFUSED"
        doc = _source(_AUDIT_DOC)
        assert "不复制" in doc


# ============================================================
# 10：Trace Separation
# ============================================================


class TestTraceSeparation:
    def test_10a_forbidden_query_services_not_dependencies(self) -> None:
        params = set(
            inspect.signature(_ConversationServiceContractDesign.__init__).parameters
        )
        for forbidden in FORBIDDEN_SERVICE_DEPENDENCIES:
            assert forbidden not in params, forbidden

    def test_10b_design_doc_declares_trace_separation(self) -> None:
        doc = _source(_AUDIT_DOC)
        for marker in (
            "LLMUsageQueryService",
            "ToolObservabilityQueryService",
            "RagExecutionPersistentQueryService",
            "AssistantTraceQueryService",
            "AssistantTimelineQueryService",
            "AssistantOutcomeQueryService",
        ):
            assert marker in doc, marker
        assert "不把 Trace 查询塞进 Conversation Repository" in doc


# ============================================================
# 11：Repository Boundary
# ============================================================


class TestRepositoryBoundary:
    def test_11a_repository_methods_frozen_exactly_five(self) -> None:
        actual = {
            name
            for name in vars(_ConversationRepositoryContractDesign)
            if not name.startswith("_")
        }
        assert actual == set(REPOSITORY_METHODS)

    def test_11b_repository_signatures_frozen(self) -> None:
        create = _signature_params(_ConversationRepositoryContractDesign.create)
        assert create == {
            "conversation_id": inspect.Parameter.KEYWORD_ONLY,
            "project_id": inspect.Parameter.KEYWORD_ONLY,
            "status": inspect.Parameter.KEYWORD_ONLY,
        }
        get = _signature_params(
            _ConversationRepositoryContractDesign.get_by_conversation_id
        )
        assert get == {"conversation_id": inspect.Parameter.POSITIONAL_OR_KEYWORD}
        update = _signature_params(
            _ConversationRepositoryContractDesign.update_status
        )
        assert update == {
            "conversation_id": inspect.Parameter.POSITIONAL_OR_KEYWORD,
            "status": inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
        append = _signature_params(_ConversationRepositoryContractDesign.append_turn)
        assert set(append) == {
            "conversation_id",
            "role",
            "content",
            "assistant_request_id",
        }
        list_turns = _signature_params(
            _ConversationRepositoryContractDesign.list_turns_by_conversation_id
        )
        assert list_turns == {
            "conversation_id": inspect.Parameter.POSITIONAL_OR_KEYWORD
        }

    def test_11c_design_doc_declares_repository_boundary(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "Service 不允许直接" in doc
        assert "SQLAlchemy Session" in doc
        assert "Repository 负责" in doc


# ============================================================
# 12：Transaction Boundary / Read-Write Separation
# ============================================================


class TestTransactionAndCqrs:
    def test_12a_design_doc_declares_transaction_boundary(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "不要把整个 LLM 网络调用包在数据库 transaction 中" in doc
        assert "写 USER Turn" in doc
        assert "写 ASSISTANT Turn" in doc

    def test_12b_no_cqrs_split(self) -> None:
        # 设计结论：不拆分 CQRS，保持单一 ConversationService
        assert "ConversationQueryService" not in {
            name
            for name in vars(_ConversationServiceContractDesign)
            if not name.startswith("_")
        }
        doc = _source(_AUDIT_DOC)
        assert "不拆分 CQRS" in doc
        assert "保持单一 ConversationService" in doc


# ============================================================
# 13：Existing boundary 不发生变化（Trace / Timeline / Outcome）
# ============================================================


class TestExistingBoundaryUnchanged:
    def test_13a_trace_api_unchanged(self) -> None:
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

    def test_13b_timeline_api_unchanged(self) -> None:
        route = _find_route(timeline_router, _TIMELINE_PATH)
        assert set(route.methods) == {"GET"}
        assert route.dependant.path_params[0].name == "assistant_request_id"
        assert "conversation_id" not in _source(_TIMELINE_API)

    def test_13c_conversation_routes_only_in_declared_module(self) -> None:
        """Step 8 起路由已注册：只有 conversations.py 可以持有 /conversations。"""
        api_root = _REPO_ROOT / _API_DIR
        for path in sorted(api_root.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            if "/conversations" in source:
                assert path.name == "conversations.py", path.name

    def test_13d_no_unimplemented_conversation_artifacts(self) -> None:
        """Step 5 允许 ORM / Repository / Service；**未实现**的组件不得出现。"""
        for relative in (
            "backend/app/api/conversation.py",                      # API 未实现
            "backend/app/services/conversation_query_service.py",    # 不拆 CQRS
            "backend/app/services/conversation_command_service.py",  # 不拆 CQRS
        ):
            assert not (_REPO_ROOT / relative).exists(), relative


# ============================================================
# 14：Security（敏感字段 / Session 不跨越边界）
# ============================================================


class TestSecurityBoundary:
    def test_14a_design_views_have_no_sensitive_fields(self) -> None:
        names = {f.name for f in fields(_TurnViewDesign)} | {
            f.name for f in fields(_ConversationViewDesign)
        }
        for forbidden in FORBIDDEN_SENSITIVE_FIELDS:
            assert forbidden not in names, forbidden

    def test_14b_design_doc_declares_security_boundary(self) -> None:
        doc = _source(_AUDIT_DOC)
        for marker in (
            "api_key",
            "password",
            "database_url",
            "Authorization",
            "raw HTTP headers",
            "LLM prompt",
            "RAG chunks",
            "Tool internal payload",
            "SQL",
            "DB Session",
        ):
            assert marker in doc, marker
        assert "不返回 SQLAlchemy Session" in doc

    def test_14c_existing_service_style_evidence(self) -> None:
        # 风格证据：Query 语义 → None；命令/写 Service 构造注入 Repository
        outcome_service = _source(_OUTCOME_SERVICE)
        assert "repository: AssistantOutcomeRepository | None = None" in outcome_service
        assert "None" in outcome_service and "不猜" in outcome_service
        persistence_service = _source(_TOOL_PERSIST_SERVICE)
        assert "repository: ToolExecutionRepository | None = None" in persistence_service
        chat_service = _source(_CHAT_SERVICE)
        assert "class ChatService" in chat_service
        assert "fastapi" not in chat_service


# ============================================================
# 15：Design doc 完整性 + Deferred 不变式
# ============================================================


class TestDesignDocument:
    def test_15a_required_sections_present(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in _REQUIRED_DOC_SECTIONS:
            assert section in doc, section

    def test_15b_references_previous_steps(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "Phase 4.1 Step 1" in doc
        assert "Phase 4.1 Step 2" in doc

    def test_15c_declares_unchanged_invariants(self) -> None:
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
# 16：自身 import 的 AST 审计（纯离线守卫）
# ============================================================


class TestSelfAudit:
    def test_16a_no_db_engine_identifiers(self) -> None:
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


def _find_route(router: Any, path: str) -> Any:
    for route in router.routes:
        if getattr(route, "path", None) == path:
            return route
    raise AssertionError(f"route not found: {path}")


__all__ = [
    "ARCHIVE_TRANSITIONS",
    "CREATE_INPUT_FIELDS",
    "FORBIDDEN_SERVICE_DEPENDENCIES",
    "REPOSITORY_METHODS",
    "SERVICE_ALLOWED_DEPENDENCIES",
    "SERVICE_METHODS",
    "TestServiceLayerBoundary",
    "TestNoAiCoreDependency",
    "TestNoFastApiDependency",
    "TestNoNetworkOrLlmDependency",
    "TestCreateContract",
    "TestArchiveContract",
    "TestProjectBinding",
    "TestAppendTurnContract",
    "TestOutcomeSeparation",
    "TestTraceSeparation",
    "TestRepositoryBoundary",
    "TestTransactionAndCqrs",
    "TestExistingBoundaryUnchanged",
    "TestSecurityBoundary",
    "TestDesignDocument",
    "TestSelfAudit",
]
