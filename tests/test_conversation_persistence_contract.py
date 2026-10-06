"""Conversation Persistence Contract（Phase 4.1 Step 4 — Design / Audit Only）。

固化来源：
    `docs/evaluation/Phase 4.1 Step 4 — Conversation Persistence Audit.md`

本文件性质：

    * **纯离线**：DB writes = 0 · DB reads = 0 · network = 0 · LLM = 0
      （不连接 PostgreSQL / 不执行 init_db / 不运行 alembic / 不建表）；
    * **Audit Only**：本 Step **不创建** ORM / Repository / migration / 表 / FK /
      索引 —— 本文件用测试内设计常量与骨架（``_`` 前缀）冻结
      ORM 设计、FK / 索引 / 排序契约、Repository 5 方法、
      Transaction Ownership 与 Delete 语义；
    * **严格复用现有 DB 风格**：Base（`DeclarativeBase`）、BigInteger 自增主键、
      `String(128)` 业务键、`DateTime(timezone=True)` + `server_default`、
      string status（`String(32)`）、`ix_/uq_` 索引命名、Repository 内部持有
      Session 与事务（`session.begin()`）——全部用真实源码证据锁定；
    * 任何"进入 production 的 conversation 持久化代码 / 建 FK 到观测表 /
      额外索引 / 修改现有 DB"的漂移都会使本文件失败 —— 这是有意的漂移报警。
"""
from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass, fields
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
_SELF = "tests/test_conversation_persistence_contract.py"
_AUDIT_DOC = "docs/evaluation/Phase 4.1 Step 4 — Conversation Persistence Audit.md"
_APP_DIR = "backend/app"
_API_DIR = "backend/app/api"
_DB_DIR = "backend/app/db"
_DB_MODELS_DIR = "backend/app/db/models"
_BASE = "backend/app/db/base.py"
_INIT_DB = "backend/app/db/init_db.py"
_MODELS_INIT = "backend/app/db/models/__init__.py"
_SESSION = "backend/app/db/session.py"
_OUTCOME_REPOSITORY = "backend/app/db/assistant_outcome_repository.py"
_KNOWLEDGE_DOCUMENT = "backend/app/db/models/knowledge_document.py"
_KNOWLEDGE_CHUNK = "backend/app/db/models/knowledge_chunk.py"
_LLM_USAGE_MODEL = "backend/app/db/models/llm_usage_record.py"
_TRACE_API = "backend/app/api/assistant_trace.py"
_TIMELINE_API = "backend/app/api/assistant_timeline.py"
_OUTCOME_MODEL = "backend/app/db/models/assistant_outcome_record.py"
_CONFTEST = "tests/conftest.py"
_DB_GATED_TEST = "tests/test_assistant_outcome_contract_audit.py"

_TRACE_PATH = "/observability/assistant-trace/{assistant_request_id}"
_TIMELINE_PATH = "/observability/assistant-timeline/{assistant_request_id}"

# ============================================================
# 设计冻结常量（design-only）
# ============================================================

#: Conversation ORM 列（严格对应 Step 2 的 5 字段）。
CONVERSATION_ORM_FIELDS: tuple[str, ...] = (
    "conversation_id",
    "project_id",
    "created_at",
    "updated_at",
    "status",
)

#: Conversation 列类型（设计；复用现有 DB 风格）。
CONVERSATION_ORM_COLUMN_TYPES: dict[str, str] = {
    "conversation_id": "String(128) PRIMARY KEY NOT NULL",
    "project_id": "String(128) NOT NULL",
    "created_at": "DateTime(timezone=True) NOT NULL server_default=func.now()",
    "updated_at": (
        "DateTime(timezone=True) NOT NULL server_default=func.now() onupdate=func.now()"
    ),
    "status": "String(32) NOT NULL default='ACTIVE'",
}

#: ConversationTurn ORM 列（严格对应 Step 2 的 6 字段）。
TURN_ORM_FIELDS: tuple[str, ...] = (
    "turn_id",
    "conversation_id",
    "role",
    "content",
    "assistant_request_id",
    "created_at",
)

TURN_ORM_COLUMN_TYPES: dict[str, str] = {
    "turn_id": "BigInteger PRIMARY KEY autoincrement",
    "conversation_id": (
        "String(128) NOT NULL FOREIGN KEY -> conversation.conversation_id "
        "ON DELETE CASCADE"
    ),
    "role": "String(32) NOT NULL",
    "content": "Text NOT NULL",
    "assistant_request_id": "String(128) NULL（correlation，不是 FK）",
    "created_at": "DateTime(timezone=True) NOT NULL server_default=func.now()",
}

#: Conversation ORM **禁止**字段（本阶段全部延期）。
FORBIDDEN_CONVERSATION_FIELDS: tuple[str, ...] = (
    "title",
    "user_id",
    "tenant_id",
    "metadata",
    "last_message",
    "message_count",
)

#: 索引（Step 2 冻结的最小集合）。
CONVERSATION_TURN_INDEX_COLUMNS: tuple[str, ...] = ("conversation_id", "created_at")

#: 明确不建的索引（除非未来架构明确要求）。
FORBIDDEN_INDEX_COLUMNS: tuple[str, ...] = (
    "project_id",
    "status",
    "assistant_request_id",
    "role",
)

#: Turn 读取排序（必须 created_at + turn_id 组合）。
TURN_ORDERING_COLUMNS: tuple[str, ...] = ("created_at ASC", "turn_id ASC")

#: append_turn 原子操作序列（同一事务）。
APPEND_TURN_ATOMIC_OPERATIONS: tuple[str, ...] = (
    "INSERT conversation_turn",
    "UPDATE conversation.updated_at",
)

#: Transaction Ownership（方案 A：Repository 持有事务；Service 无 Session）。
TRANSACTION_OWNERSHIP: str = "repository"

#: Repository 方法（Step 3 冻结；5 个）。
REPOSITORY_METHODS: tuple[str, ...] = (
    "create",
    "get_by_conversation_id",
    "update_status",
    "append_turn",
    "list_turns_by_conversation_id",
)

#: Service 方法（Step 3 冻结；5 个）。
SERVICE_METHODS: tuple[str, ...] = (
    "create_conversation",
    "get_conversation",
    "archive_conversation",
    "append_turn",
    "list_turns",
)

#: 物理删除的级联目标 / 必须保留的观测表。
DELETE_CASCADE_TARGETS: tuple[str, ...] = ("conversation_turn",)
DELETE_PRESERVED_TABLES: tuple[str, ...] = (
    "llm_usage_record",
    "tool_execution_record",
    "rag_execution_record",
    "assistant_outcome_record",
)

#: 持久化层禁止字段（Security）。
FORBIDDEN_SENSITIVE_FIELDS: tuple[str, ...] = (
    "api_key",
    "password",
    "authorization",
    "headers",
    "database_url",
    "prompt",
    "system_prompt",
    "tool_definitions",
    "sql",
    "chunk_content",
    "embedding",
    "session",
    "provider_response",
)

#: Step 5 起允许存在的生产文件（相对仓库根；多出的 conversation 代码 = 漂移）。
#: Step 45 追加：Conversation ↔ Evidence 关联持久化（association，非新 Entity）。
ALLOWED_PRODUCTION_MODULES: tuple[str, ...] = (
    "backend/app/api/conversations.py",
    "backend/app/db/conversation_evidence_repository.py",
    "backend/app/db/conversation_repository.py",
    "backend/app/db/models/conversation.py",
    "backend/app/db/models/conversation_evidence.py",
    "backend/app/db/models/conversation_turn.py",
    "backend/app/dto/conversation_api.py",
    "backend/app/services/conversation_context_builder.py",
    # Phase 4.2 Step 7E：Selection 层（Step 7C 契约实现）
    "backend/app/services/conversation_context_selection_service.py",
    "backend/app/services/conversation_service.py",
)

_REQUIRED_DOC_SECTIONS: tuple[str, ...] = (
    "## 1. Current DB Architecture",
    "## 2. ORM Conventions",
    "## 3. Repository Conventions",
    "## 4. Transaction Ownership",
    "## 5. Conversation ORM Design",
    "## 6. Turn ORM Design",
    "## 7. FK Design",
    "## 8. Index Strategy",
    "## 9. Ordering",
    "## 10. Status / Role",
    "## 11. Delete Semantics",
    "## 12. Project Binding",
    "## 13. Security",
    "## 14. Migration Strategy",
    "## 15. Test DB Strategy",
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
# 设计 DTO（design-only；本文件私有）
# ============================================================


@dataclass(frozen=True)
class _ConversationRowDesign:
    """Repository 返回的 Conversation Row（**不是** ORM 对象）。"""

    conversation_id: str
    project_id: str
    created_at: str
    updated_at: str
    status: str

    def __post_init__(self) -> None:
        if not isinstance(self.conversation_id, str) or not self.conversation_id.strip():
            raise ValueError("conversation_id 必须是非空 str")
        if not isinstance(self.project_id, str) or not self.project_id.strip():
            raise ValueError("project_id 必须是非空 str")
        if self.status not in ("ACTIVE", "ARCHIVED"):
            raise ValueError(f"status 非法: {self.status!r}")


@dataclass(frozen=True)
class _ConversationTurnRowDesign:
    """Repository 返回的 Turn Row（**不是** ORM 对象）。"""

    turn_id: int
    conversation_id: str
    role: str
    content: str
    assistant_request_id: str | None
    created_at: str

    def __post_init__(self) -> None:
        if isinstance(self.turn_id, bool) or not isinstance(self.turn_id, int):
            raise ValueError("turn_id 必须是 int")
        if self.turn_id < 1:
            raise ValueError("turn_id 必须 >= 1")
        if self.role not in ("USER", "ASSISTANT"):
            raise ValueError(f"role 非法: {self.role!r}")
        if self.role == "USER" and self.assistant_request_id is not None:
            raise ValueError("USER turn 的 assistant_request_id 必须为 None")
        if self.role == "ASSISTANT" and not self.assistant_request_id:
            raise ValueError("ASSISTANT turn 的 assistant_request_id 必须非空")


class _ConversationRepositoryDesign:
    """ConversationRepository 设计骨架（仅签名；不实现）。"""

    def __init__(self, session_factory: Any = None) -> None:
        self._session_factory = session_factory

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


class _ConversationServiceDesign:
    """ConversationService 设计骨架（仅签名；不实现）。"""

    def __init__(self, repository: _ConversationRepositoryDesign | None = None) -> None:
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


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _signature_params(func: Any) -> dict[str, Any]:
    return {
        name: parameter.kind
        for name, parameter in inspect.signature(func).parameters.items()
        if name not in {"self", "cls"}
    }


def _find_route(router: Any, path: str) -> Any:
    for route in router.routes:
        if getattr(route, "path", None) == path:
            return route
    raise AssertionError(f"route not found: {path}")


def _conversation_production_modules() -> list[Path]:
    """AST 守卫目标（Step 5~8 起 = 冻结的已实现 Conversation 生产模块）。"""
    return sorted((_REPO_ROOT / _APP_DIR).rglob("conversation*.py"))


def _module_identifiers(tree: ast.AST) -> set[str]:
    return {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }


def _module_imports(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


# ============================================================
# 1：Conversation ORM Design
# ============================================================


class TestConversationOrmDesign:
    def test_1a_fields_exact_five(self) -> None:
        assert CONVERSATION_ORM_FIELDS == (
            "conversation_id",
            "project_id",
            "created_at",
            "updated_at",
            "status",
        )
        assert set(CONVERSATION_ORM_COLUMN_TYPES) == set(CONVERSATION_ORM_FIELDS)

    def test_1b_no_extra_fields(self) -> None:
        for forbidden in FORBIDDEN_CONVERSATION_FIELDS:
            assert forbidden not in CONVERSATION_ORM_FIELDS, forbidden

    def test_1c_conversation_id_is_string_pk(self) -> None:
        declared = CONVERSATION_ORM_COLUMN_TYPES["conversation_id"]
        assert "String(128)" in declared
        assert "PRIMARY KEY" in declared
        assert "NOT NULL" in declared

    def test_1d_project_id_not_null_string(self) -> None:
        declared = CONVERSATION_ORM_COLUMN_TYPES["project_id"]
        assert "String(128)" in declared
        assert "NOT NULL" in declared

    def test_1e_timestamps_match_project_style(self) -> None:
        for name in ("created_at", "updated_at"):
            declared = CONVERSATION_ORM_COLUMN_TYPES[name]
            assert "DateTime(timezone=True)" in declared
            assert "server_default=func.now()" in declared
        # 风格证据：现有表统一使用 DateTime(timezone=True) + server_default
        assert "DateTime(timezone=True)" in _source(_KNOWLEDGE_CHUNK)
        assert "server_default=func.now()" in _source(_KNOWLEDGE_DOCUMENT)

    def test_1f_status_is_string_not_pg_enum(self) -> None:
        declared = CONVERSATION_ORM_COLUMN_TYPES["status"]
        assert "String(32)" in declared
        assert "Enum" not in declared and "Enum(" not in declared
        # 风格证据：现有 status 列 = String(32) + server_default（无 PG ENUM）
        assert "String(32)" in _source(_KNOWLEDGE_DOCUMENT)
        assert 'server_default="pending"' in _source(_KNOWLEDGE_DOCUMENT)


# ============================================================
# 2：ConversationTurn ORM Design
# ============================================================


class TestTurnOrmDesign:
    def test_2a_fields_exact_six(self) -> None:
        assert TURN_ORM_FIELDS == (
            "turn_id",
            "conversation_id",
            "role",
            "content",
            "assistant_request_id",
            "created_at",
        )
        assert set(TURN_ORM_COLUMN_TYPES) == set(TURN_ORM_FIELDS)

    def test_2b_turn_id_bigint_autoincrement(self) -> None:
        declared = TURN_ORM_COLUMN_TYPES["turn_id"]
        assert "BigInteger" in declared
        assert "PRIMARY KEY" in declared
        for forbidden in ("UUID", "String", "assistant_request_id"):
            assert forbidden not in declared, forbidden
        # 风格证据：项目主键规范 = BIGINT 自增，明确不引入 UUID / ULID / Snowflake
        source = _source(_LLM_USAGE_MODEL)
        assert "主键沿用项目既有 BIGINT 自增规范" in source
        assert "不引入 UUID / ULID /" in source

    def test_2c_content_is_text(self) -> None:
        assert TURN_ORM_COLUMN_TYPES["content"].startswith("Text")
        assert "Text," in _source(_KNOWLEDGE_CHUNK)

    def test_2d_assistant_request_id_nullable_string(self) -> None:
        declared = TURN_ORM_COLUMN_TYPES["assistant_request_id"]
        assert "String(128)" in declared
        assert "NULL" in declared
        assert "ForeignKey" not in declared
        assert "FOREIGN KEY" not in declared

    def test_2e_row_dto_contract(self) -> None:
        assert [f.name for f in fields(_ConversationTurnRowDesign)] == list(
            TURN_ORM_FIELDS
        )
        assert [f.name for f in fields(_ConversationRowDesign)] == list(
            CONVERSATION_ORM_FIELDS
        )


# ============================================================
# 3：FK Design
# ============================================================


class TestForeignKeyDesign:
    def test_3a_turn_has_fk_to_conversation_with_cascade(self) -> None:
        declared = TURN_ORM_COLUMN_TYPES["conversation_id"]
        assert "FOREIGN KEY" in declared
        assert "conversation.conversation_id" in declared
        assert "ON DELETE CASCADE" in declared
        assert DELETE_CASCADE_TARGETS == ("conversation_turn",)

    def test_3b_fk_precedent_exists(self) -> None:
        # 现有 FK 先例：knowledge_chunk → knowledge_document（ON DELETE CASCADE）
        assert 'ForeignKey("knowledge_document.id", ondelete="CASCADE")' in _source(
            _KNOWLEDGE_CHUNK
        )

    def test_3c_no_fk_to_observability(self) -> None:
        for table in ("llm_usage_record", "assistant_outcome_record"):
            source = _source(f"{_DB_MODELS_DIR}/{table}.py")
            assert "ForeignKey" not in source, table
        assert "assistant_request_id 不是 ForeignKey" in _source(_AUDIT_DOC)


# ============================================================
# 4：Index Strategy
# ============================================================


class TestIndexStrategy:
    def test_4a_minimal_composite_index(self) -> None:
        assert CONVERSATION_TURN_INDEX_COLUMNS == ("conversation_id", "created_at")

    def test_4b_no_extra_indexes(self) -> None:
        for forbidden in FORBIDDEN_INDEX_COLUMNS:
            assert forbidden not in CONVERSATION_TURN_INDEX_COLUMNS, forbidden

    def test_4c_design_doc_declares_index_boundary(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "(conversation_id, created_at)" in doc
        assert "ix_conversation_turn" in doc


# ============================================================
# 5：Ordering
# ============================================================


class TestOrdering:
    def test_5a_ordering_is_created_at_then_turn_id(self) -> None:
        assert TURN_ORDERING_COLUMNS == ("created_at ASC", "turn_id ASC")

    def test_5b_ordering_not_single_column(self) -> None:
        assert len(TURN_ORDERING_COLUMNS) == 2
        assert "created_at ASC" in TURN_ORDERING_COLUMNS
        assert "turn_id ASC" in TURN_ORDERING_COLUMNS

    def test_5c_design_doc_declares_ordering(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "created_at ASC" in doc
        assert "turn_id ASC" in doc


# ============================================================
# 6：Status / Role
# ============================================================


class TestStatusAndRole:
    def test_6a_status_values(self) -> None:
        with pytest.raises(ValueError):
            _ConversationRowDesign(
                conversation_id="c",
                project_id="p",
                created_at="t",
                updated_at="t",
                status="DELETED",
            )
        for forbidden in ("CLOSED", "CANCELLED", "SUSPENDED", "DELETED"):
            with pytest.raises(ValueError):
                _ConversationRowDesign(
                    conversation_id="c",
                    project_id="p",
                    created_at="t",
                    updated_at="t",
                    status=forbidden,
                )

    def test_6b_role_values(self) -> None:
        with pytest.raises(ValueError):
            _ConversationTurnRowDesign(
                turn_id=1,
                conversation_id="c",
                role="SYSTEM",
                content="x",
                assistant_request_id=None,
                created_at="t",
            )
        for forbidden in ("SYSTEM", "TOOL", "FUNCTION"):
            with pytest.raises(ValueError):
                _ConversationTurnRowDesign(
                    turn_id=1,
                    conversation_id="c",
                    role=forbidden,
                    content="x",
                    assistant_request_id=None,
                    created_at="t",
                )

    def test_6c_design_doc_declares_status_and_role(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "ACTIVE" in doc and "ARCHIVED" in doc
        assert "USER" in doc and "ASSISTANT" in doc


# ============================================================
# 7：Repository Contract + Transaction Ownership
# ============================================================


class TestRepositoryContract:
    def test_7a_repository_methods_frozen(self) -> None:
        actual = {
            name
            for name in vars(_ConversationRepositoryDesign)
            if not name.startswith("_")
        }
        assert actual == set(REPOSITORY_METHODS)
        assert "delete" not in actual  # 删除语义另议；本阶段不提供 delete 方法

    def test_7b_repository_does_not_receive_session_from_service(self) -> None:
        # Repository 构造只接收 session_factory（可选），Service 不传 Session
        params = _signature_params(_ConversationRepositoryDesign.__init__)
        assert list(params) == ["session_factory"]
        parameter = inspect.signature(
            _ConversationRepositoryDesign.__init__
        ).parameters["session_factory"]
        assert parameter.default is None

    def test_7c_transaction_ownership_is_repository(self) -> None:
        assert TRANSACTION_OWNERSHIP == "repository"
        doc = _source(_AUDIT_DOC)
        assert "方案 A" in doc
        assert "Transaction ownership = Repository" in doc

    def test_7d_existing_repository_style_evidence(self) -> None:
        source = _source(_OUTCOME_REPOSITORY)
        # 写：事务在 Repository 内部（session.begin()）；读：无写事务
        assert "with factory() as session, session.begin():" in source
        assert "with factory() as session:" in source
        # Repository 不显式 commit / flush / refresh / add
        for forbidden in ("session.commit", "session.flush", "session.refresh"):
            assert forbidden not in source, forbidden
        # Repository 构造注入 session_factory（None → 全局工厂）
        assert "session_factory: sessionmaker[Session] | None = None" in source
        assert "get_session_factory()" in _source(_SESSION)


# ============================================================
# 8：Service Contract（无 Session / 无 SQL）
# ============================================================


class TestServiceContract:
    def test_8a_service_methods_frozen(self) -> None:
        actual = {
            name
            for name in vars(_ConversationServiceDesign)
            if not name.startswith("_")
        }
        assert actual == set(SERVICE_METHODS)

    def test_8b_service_has_no_session_or_sql_parameters(self) -> None:
        forbidden = {"session", "session_factory", "engine", "connection", "select"}
        for name in SERVICE_METHODS:
            params = set(_signature_params(getattr(_ConversationServiceDesign, name)))
            assert not (params & forbidden), name
        init_params = set(_signature_params(_ConversationServiceDesign.__init__))
        assert init_params == {"repository"}

    def test_8c_design_doc_declares_service_boundary(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "Service = business lifecycle" in doc
        assert "Repository = persistence" in doc


# ============================================================
# 9：append_turn / archive 原子性
# ============================================================


class TestAtomicityContract:
    def test_9a_append_turn_is_single_transaction(self) -> None:
        assert APPEND_TURN_ATOMIC_OPERATIONS == (
            "INSERT conversation_turn",
            "UPDATE conversation.updated_at",
        )

    def test_9b_design_doc_declares_append_atomicity(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "INSERT conversation_turn" in doc
        assert "UPDATE conversation.updated_at" in doc
        assert "ROLLBACK" in doc

    def test_9c_archived_append_has_zero_db_writes(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "DB write = 0" in doc
        assert "ConversationArchivedError" in doc

    def test_9d_design_doc_declares_archive_atomicity(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "ACTIVE → ARCHIVED" in doc
        assert "updated_at = now" in doc


# ============================================================
# 10：Delete Semantics
# ============================================================


class TestDeleteSemantics:
    def test_10a_cascade_targets_and_preserved_tables(self) -> None:
        assert DELETE_CASCADE_TARGETS == ("conversation_turn",)
        assert DELETE_PRESERVED_TABLES == (
            "llm_usage_record",
            "tool_execution_record",
            "rag_execution_record",
            "assistant_outcome_record",
        )
        assert not set(DELETE_CASCADE_TARGETS) & set(DELETE_PRESERVED_TABLES)

    def test_10b_design_doc_declares_delete_separation(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "Conversation delete ≠ Observability delete" in doc
        assert "assistant_request_id 只是 correlation" in doc


# ============================================================
# 11：Project Binding（持久化层）
# ============================================================


class TestProjectBindingPersistence:
    def test_11a_create_writes_project_id(self) -> None:
        assert "project_id" in CONVERSATION_ORM_FIELDS
        params = _signature_params(_ConversationRepositoryDesign.create)
        assert set(params) == {"conversation_id", "project_id", "status"}

    def test_11b_subsequent_lookups_use_conversation_id_only(self) -> None:
        for name in (
            "get_by_conversation_id",
            "list_turns_by_conversation_id",
        ):
            params = set(_signature_params(getattr(_ConversationRepositoryDesign, name)))
            assert params == {"conversation_id"}, name

    def test_11c_design_doc_declares_project_binding(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "by conversation_id" in doc
        assert "不得覆盖" in doc or "不允许" in doc


# ============================================================
# 12：ORM / DTO 分离 + Security
# ============================================================


class TestOrmAndDtoSeparation:
    def test_12a_row_dtos_are_frozen_dataclasses(self) -> None:
        for model in (_ConversationRowDesign, _ConversationTurnRowDesign):
            assert model.__dataclass_params__.frozen is True

    def test_12b_no_sensitive_fields_in_row_dtos(self) -> None:
        names = {f.name for f in fields(_ConversationRowDesign)} | {
            f.name for f in fields(_ConversationTurnRowDesign)
        }
        for forbidden in FORBIDDEN_SENSITIVE_FIELDS:
            assert forbidden not in names, forbidden

    def test_12c_design_doc_declares_orm_dto_separation(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "不返回 ORM 对象" in doc
        assert "Session 不得逃逸" in doc or "不返回 Session" in doc

    def test_12d_design_doc_declares_security_fields(self) -> None:
        doc = _source(_AUDIT_DOC)
        for marker in (
            "api_key",
            "password",
            "Authorization",
            "database URL",
            "LLM prompt",
            "system prompt",
            "tool definitions",
            "raw SQL",
            "RAG chunk content",
            "embedding",
            "DB Session",
            "ORM Session",
            "raw provider response",
        ):
            assert marker in doc, marker


# ============================================================
# 13：Migration Strategy（只审计，不执行）
# ============================================================


class TestMigrationStrategy:
    def test_13a_no_alembic_artifacts(self) -> None:
        for relative in ("alembic.ini", "alembic", "migrations"):
            assert not (_REPO_ROOT / relative).exists(), relative

    def test_13b_current_strategy_is_create_all(self) -> None:
        source = _source(_INIT_DB)
        assert "Base.metadata.create_all(bind=conn)" in source
        assert "CREATE SCHEMA IF NOT EXISTS" in source
        models_init = _source(_MODELS_INIT)
        assert "_MODELS" in models_init
        assert "Alembic migration" in models_init  # 注明为未来（Phase 3.5+）

    def test_13c_design_doc_records_migration_strategy(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "Step 4 does not execute migration." in doc
        assert "Future migration strategy" in doc


# ============================================================
# 14：Test DB Strategy
# ============================================================


class TestTestDbStrategy:
    def test_14a_db_tests_are_env_gated(self) -> None:
        assert "RUN_DB_TESTS" in _source(_DB_GATED_TEST)

    def test_14b_residue_guard_exists_in_conftest(self) -> None:
        conftest = _source(_CONFTEST)
        assert "_assistant_outcome_residue_guard" in conftest
        assert "DATABASE_URL" in conftest

    def test_14c_design_doc_declares_test_db_strategy(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "RUN_DB_TESTS" in doc
        assert "init_db" in doc


# ============================================================
# 15：AST 守卫（条件式；当前不存在 production 文件）
# ============================================================


class TestAstGuard:
    def test_15a_conversation_production_modules_are_declared(self) -> None:
        """Step 5~8 起 Conversation 各层已实现：只允许冻结的已声明文件。"""
        modules = [
            str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
            for path in _conversation_production_modules()
        ]
        assert sorted(modules) == sorted(ALLOWED_PRODUCTION_MODULES)

    def test_15b_conditional_service_ast_rules(self) -> None:
        """若未来新增 conversation service：不得出现 Session / select / engine。"""
        forbidden = {
            "Session",
            "session",
            "create_engine",
            "get_engine",
            "select",
            "insert",
            "update",
            "delete",
        }
        for path in _conversation_production_modules():
            if "service" not in path.name:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            identifiers = _module_identifiers(tree)
            for name in forbidden:
                assert name not in identifiers, f"{path.name}:{name}"

    def test_15c_conditional_repository_ast_rules(self) -> None:
        """若未来新增 conversation repository：不得出现 AI / FastAPI 依赖。"""
        forbidden = {
            "AIOrchestratorService",
            "RagService",
            "ToolChatService",
            "LLMClient",
            "HTTPException",
            "APIRouter",
        }
        imports_forbidden = ("fastapi", "backend.app.llm", "backend.app.rag")
        for path in _conversation_production_modules():
            if "repository" not in path.name:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            identifiers = _module_identifiers(tree)
            for name in forbidden:
                assert name not in identifiers, f"{path.name}:{name}"
            for module in _module_imports(tree):
                for prefix in imports_forbidden:
                    assert not module.startswith(prefix), f"{path.name}:{module}"

    def test_15d_conditional_orm_ast_rules(self) -> None:
        """若未来新增 conversation ORM model：不得 import FastAPI / AI 组件。"""
        forbidden_prefixes = (
            "fastapi",
            "backend.app.llm",
            "backend.app.rag",
            "backend.app.tools",
            "backend.app.services",
        )
        for path in _conversation_production_modules():
            if _DB_MODELS_DIR not in str(path).replace("\\", "/"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for module in _module_imports(tree):
                for prefix in forbidden_prefixes:
                    assert not module.startswith(prefix), f"{path.name}:{module}"


# ============================================================
# 16：Existing boundary 不发生变化
# ============================================================


class TestExistingBoundaryUnchanged:
    def test_16a_trace_api_unchanged(self) -> None:
        route = _find_route(trace_router, _TRACE_PATH)
        assert set(route.methods) == {"GET"}
        assert list(AssistantTraceResponse.model_fields) == [
            "assistant_request_id",
            "outcome",
            "llm_usage",
            "tool_executions",
            "rag_executions",
        ]
        assert "conversation_id" not in _source(_TRACE_API)

    def test_16b_timeline_api_unchanged(self) -> None:
        route = _find_route(timeline_router, _TIMELINE_PATH)
        assert set(route.methods) == {"GET"}
        assert "conversation_id" not in _source(_TIMELINE_API)

    def test_16c_outcome_contract_unchanged(self) -> None:
        assert [member.value for member in AssistantOutcome] == [
            "SUCCESS",
            "EMPTY",
            "REFUSED",
            "FAILED",
        ]
        assert "conversation_id" not in _source(_OUTCOME_MODEL)

    def test_16d_conversation_schema_is_defined_in_db_layer_only(self) -> None:
        """Conversation ORM / 仓储只允许出现在 db 层；API 层只允许 HTTP 边界。"""
        # Step 45：conversation_evidence.py = 关联表 ORM（仍在 db/models 层）。
        assert sorted(
            path.name
            for path in (_REPO_ROOT / _DB_MODELS_DIR).glob("conversation*.py")
        ) == ["conversation.py", "conversation_evidence.py", "conversation_turn.py"]
        # Step 45：conversation_evidence_repository.py = 关联表仓储（仍在 db 层）。
        assert sorted(
            path.name
            for path in (_REPO_ROOT / _DB_DIR).glob("conversation*.py")
        ) == ["conversation_evidence_repository.py", "conversation_repository.py"]
        # Step 8 起 Conversation HTTP 边界（conversations.py）已实现：
        # API 层只允许该文件，且其中不得定义 ORM / 触碰 db.models。
        assert [
            path.name for path in (_REPO_ROOT / _API_DIR).glob("conversation*.py")
        ] == ["conversations.py"]
        api_source = (_REPO_ROOT / _API_DIR / "conversations.py").read_text(
            encoding="utf-8"
        )
        assert "DeclarativeBase" not in api_source
        assert "backend.app.db.models" not in api_source


# ============================================================
# 17：Design doc 完整性 + 不变式
# ============================================================


class TestDesignDocument:
    def test_17a_required_sections_present(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in _REQUIRED_DOC_SECTIONS:
            assert section in doc, section

    def test_17b_declares_production_zero(self) -> None:
        doc = _source(_AUDIT_DOC)
        for marker in (
            "Production Code = 0",
            "DB Schema = unchanged",
            "Migration = 0",
            "network = 0",
        ):
            assert marker in doc, marker

    def test_17c_references_previous_steps(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "Phase 4.1 Step 1" in doc
        assert "Phase 4.1 Step 3" in doc


# ============================================================
# 18：自身 import 的 AST 审计（纯离线守卫）
# ============================================================


class TestSelfAudit:
    def test_18a_self_import_is_offline(self) -> None:
        modules = _module_imports(ast.parse(_source(_SELF)))
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for prefix in _FORBIDDEN_IMPORT_PREFIXES:
                assert not module.startswith(prefix), module

    def test_18b_no_db_engine_identifiers(self) -> None:
        identifiers = _module_identifiers(ast.parse(_source(_SELF)))
        for forbidden in (
            "get_" + "engine",
            "create_" + "engine",
            "get_session_" + "factory",
            "session" + "maker",
        ):
            assert forbidden not in identifiers, forbidden


__all__ = [
    "APPEND_TURN_ATOMIC_OPERATIONS",
    "CONVERSATION_ORM_FIELDS",
    "CONVERSATION_TURN_INDEX_COLUMNS",
    "DELETE_PRESERVED_TABLES",
    "REPOSITORY_METHODS",
    "SERVICE_METHODS",
    "TRANSACTION_OWNERSHIP",
    "TURN_ORDERING_COLUMNS",
    "TURN_ORM_FIELDS",
    "TestConversationOrmDesign",
    "TestTurnOrmDesign",
    "TestForeignKeyDesign",
    "TestIndexStrategy",
    "TestOrdering",
    "TestStatusAndRole",
    "TestRepositoryContract",
    "TestServiceContract",
    "TestAtomicityContract",
    "TestDeleteSemantics",
    "TestProjectBindingPersistence",
    "TestOrmAndDtoSeparation",
    "TestMigrationStrategy",
    "TestTestDbStrategy",
    "TestAstGuard",
    "TestExistingBoundaryUnchanged",
    "TestDesignDocument",
    "TestSelfAudit",
]
