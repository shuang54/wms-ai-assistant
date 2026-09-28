"""Assistant Trace LLM Usage 只读边界单元测试（Phase 3.12 Step 37）。

层次（全部 0 DB / 0 Network / 0 LLM）：

    LLMUsageQueryService.list_by_assistant_request_id()
        ↓ validate（DB 之前）
    LLMUsageRepository.list_by_assistant_request_id()
        ↓ build_trace_select()（唯一 trace SQL 构造点）
    [LLMUsageTraceRow] → [LLMUsageTraceRecordView]
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from backend.app.db.llm_usage_repository import (
    ASSISTANT_REQUEST_ID_MAX_LENGTH,
    LLM_USAGE_TRACE_READ_COLUMNS,
    LLMUsageRepository,
    LLMUsageTraceRow,
)
from backend.app.services.llm_usage_query_service import (
    LLM_USAGE_TRACE_VIEW_FIELDS,
    LLMUsageQueryInputError,
    LLMUsageQueryService,
    LLMUsageTraceRecordView,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REPOSITORY_MODULE = "backend/app/db/llm_usage_repository.py"
_QUERY_MODULE = "backend/app/services/llm_usage_query_service.py"
_API_MODULES = (
    "backend/app/api/usage.py",
    "backend/app/api/orchestrator_chat.py",
    "backend/app/api/tool_observability.py",
)


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _tree(relative: str) -> ast.Module:
    return ast.parse(_source(relative))


def _identifiers(relative: str) -> set[str]:
    tree = _tree(relative)
    return {
        node.id.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    } | {
        node.attr.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }


def _trace_row(**overrides: Any) -> LLMUsageTraceRow:
    values: dict[str, Any] = {
        "id": 10,
        "assistant_request_id": "A",
        "request_id": "chatcmpl-P1",
        "provider": "deepseek-test",
        "model": "deepseek-chat",
        "prompt_tokens": 11,
        "completion_tokens": 22,
        "total_tokens": 33,
        "created_at": datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return LLMUsageTraceRow(**values)


class _FakeRepository:
    """记录入参的 Fake Repository（duck-typed；0 DB）。"""

    def __init__(
        self,
        rows: list[LLMUsageTraceRow] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.rows = rows if rows is not None else []
        self.calls: list[str] = []
        self._error = error

    def list_by_assistant_request_id(
        self, assistant_request_id: str
    ) -> list[LLMUsageTraceRow]:
        self.calls.append(assistant_request_id)
        if self._error is not None:
            raise self._error
        return list(self.rows)


def _boom_session_factory() -> Any:
    raise AssertionError("DB 不应被访问（输入校验必须先于数据库访问）")


# ============================================================
# 1. Read Service（validate → Repository → View）
# ============================================================

class TestTraceReadService:
    def test_returns_views_in_repository_order(self) -> None:
        rows = [
            _trace_row(id=10, created_at=datetime(
                2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)),
            _trace_row(id=11, request_id="chatcmpl-P2", created_at=datetime(
                2026, 9, 28, 12, 0, 1, tzinfo=timezone.utc)),
        ]
        repository = _FakeRepository(rows)
        service = LLMUsageQueryService(repository=repository)  # type: ignore[arg-type]

        views = service.list_by_assistant_request_id("A")

        assert repository.calls == ["A"]              # 精确透传（不改写）
        assert [type(v) for v in views] == [LLMUsageTraceRecordView] * 2
        assert [v.id for v in views] == [10, 11]      # 不在 Service 层重排序
        assert all(v.assistant_request_id == "A" for v in views)
        assert views[0].request_id == "chatcmpl-P1"   # Provider ID 保持原值

    def test_empty_result_is_not_an_error(self) -> None:
        service = LLMUsageQueryService(repository=_FakeRepository())  # type: ignore[arg-type]

        assert service.list_by_assistant_request_id("not-exist") == []

    @pytest.mark.parametrize(
        "bad", [None, "", "   ", "\t\n", 123, b"A", ["A"], "x" * 129]
    )
    def test_invalid_input_rejected_before_repository(self, bad) -> None:
        repository = _FakeRepository()
        service = LLMUsageQueryService(repository=repository)  # type: ignore[arg-type]

        with pytest.raises(LLMUsageQueryInputError):
            service.list_by_assistant_request_id(bad)  # type: ignore[arg-type]

        assert repository.calls == []                 # 未触达 Repository

    def test_max_length_boundary_is_accepted(self) -> None:
        repository = _FakeRepository()
        service = LLMUsageQueryService(repository=repository)  # type: ignore[arg-type]
        request_id = "x" * ASSISTANT_REQUEST_ID_MAX_LENGTH

        assert service.list_by_assistant_request_id(request_id) == []
        assert repository.calls == [request_id]       # 不 truncate / 不改写

    def test_repository_error_propagates(self) -> None:
        from backend.app.db.llm_usage_repository import (
            LLMUsageRepositoryError,
        )

        service = LLMUsageQueryService(  # type: ignore[arg-type]
            repository=_FakeRepository(
                error=LLMUsageRepositoryError("db down")
            )
        )

        with pytest.raises(LLMUsageRepositoryError):
            service.list_by_assistant_request_id("A")

    def test_view_mapping_is_explicit_and_whitelisted(self) -> None:
        view = LLMUsageTraceRecordView.from_row(_trace_row())

        assert set(LLM_USAGE_TRACE_VIEW_FIELDS) == {
            "id", "assistant_request_id", "request_id", "provider", "model",
            "prompt_tokens", "completion_tokens", "total_tokens", "created_at",
        }
        assert view.assistant_request_id == "A"
        assert view.request_id == "chatcmpl-P1"
        # 冻结 + 无敏感字段
        with pytest.raises(Exception):
            view.id = 999  # type: ignore[misc]

    def test_view_rejects_blank_assistant_request_id(self) -> None:
        """View（公开读 DTO）校验；Repository Row 是内部纯数据记录（不校验）。"""
        with pytest.raises(ValueError):
            LLMUsageTraceRecordView(
                id=1,
                assistant_request_id="",
                request_id=None,
                provider=None,
                model=None,
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
                created_at=datetime.now(timezone.utc),
            )


# ============================================================
# 2. Repository（SQL 形态 / bound parameter / 校验先于 DB）
# ============================================================

class TestTraceRepository:
    def test_trace_columns_are_explicit(self) -> None:
        assert LLM_USAGE_TRACE_READ_COLUMNS == (
            "id", "assistant_request_id", "request_id", "provider", "model",
            "prompt_tokens", "completion_tokens", "total_tokens", "created_at",
        )
        # analytics 读白名单未被修改（8 列，不含 correlation）
        from backend.app.db.llm_usage_repository import (
            LLM_USAGE_READ_COLUMNS,
        )

        assert "assistant_request_id" not in LLM_USAGE_READ_COLUMNS
        assert len(LLM_USAGE_READ_COLUMNS) == 8

    def test_select_is_exact_match_with_deterministic_order(self) -> None:
        sql = str(
            LLMUsageRepository()
            .build_trace_select(assistant_request_id="A")
            .compile(compile_kwargs={"literal_binds": True})
        )

        assert "SELECT *" not in sql.upper()
        for column in LLM_USAGE_TRACE_READ_COLUMNS:
            assert f".{column}" in sql, column
        # 精确匹配（无 NULL 分支 / 无 OR / 无 LIKE）
        assert "assistant_request_id = 'A'" in sql
        assert " IS NULL" not in sql.upper()
        assert " OR " not in sql.upper()
        assert "LIKE" not in sql.upper()
        # 确定性排序
        assert "ORDER BY" in sql.upper()
        assert "created_at ASC" in sql
        assert "id ASC" in sql
        # 无分页（Step 37 不引入 limit / offset）
        assert "LIMIT" not in sql.upper()
        assert "OFFSET" not in sql.upper()

    def test_filter_value_is_bound_parameter(self) -> None:
        statement = LLMUsageRepository().build_trace_select(
            assistant_request_id="A"
        )

        assert statement.compile().params == {"assistant_request_id_1": "A"}

    def test_injection_input_stays_literal(self) -> None:
        statement = LLMUsageRepository().build_trace_select(
            assistant_request_id="A' OR '1'='1"
        )
        sql = str(statement)

        assert statement.compile().params == {
            "assistant_request_id_1": "A' OR '1'='1"
        }
        assert "OR '1'='1" not in sql
        assert ";" not in sql

    def test_second_injection_input_stays_literal(self) -> None:
        payload = "A'; DROP TABLE ai_ops.llm_usage_record; --"
        statement = LLMUsageRepository().build_trace_select(
            assistant_request_id=payload
        )

        assert statement.compile().params == {"assistant_request_id_1": payload}
        assert "DROP TABLE" not in str(statement)
        assert ";" not in str(statement)

    @pytest.mark.parametrize("bad", [None, "", "   ", 123, "x" * 129])
    def test_repository_validates_before_database(self, bad) -> None:
        repository = LLMUsageRepository(session_factory=_boom_session_factory)  # type: ignore[arg-type]

        with pytest.raises(ValueError):
            repository.list_by_assistant_request_id(bad)  # type: ignore[arg-type]


# ============================================================
# C42. Assistant Trace LLM Usage Read Boundary
# ============================================================

class TestC42AssistantTraceReadBoundary:
    """C42：Assistant Trace 的 LLM Usage 只读边界。

        C42.1  assistant_request_id 精确匹配
        C42.2  NULL 不参与匹配
        C42.3  Repository 使用 bound parameter
        C42.4  Repository 不 SELECT *
        C42.5  Repository 返回 frozen Row DTO（不是 ORM）
        C42.6  Read Service 不访问 ORM / Session / SQLAlchemy
        C42.7  不新增 HTTP endpoint
        C42.8  provider_request_id（request_id）语义保持不变
        C42.9  历史 NULL 数据兼容（读模型未变）
        C42.10 不读取 prompt / messages / secrets
    """

    def test_c42_1_exact_match_only(self) -> None:
        sql = str(
            LLMUsageRepository()
            .build_trace_select(assistant_request_id="A")
            .compile(compile_kwargs={"literal_binds": True})
        )
        assert "assistant_request_id = 'A'" in sql
        assert " LIKE " not in sql.upper()
        assert ">= " not in sql and "<= " not in sql

    def test_c42_2_null_never_matches(self) -> None:
        """NULL 分支不存在：SQL 无 IS NULL / OR；AST 无 or_() / is_()。"""
        sql = str(
            LLMUsageRepository().build_trace_select(assistant_request_id="A")
        ).upper()
        assert "IS NULL" not in sql
        assert " OR " not in sql

        identifiers = _identifiers(_REPOSITORY_MODULE)
        for forbidden in ("or_", "is_null", "nulls_last"):
            assert forbidden not in identifiers, forbidden

    def test_c42_3_bound_parameter(self) -> None:
        statement = LLMUsageRepository().build_trace_select(
            assistant_request_id="A"
        )
        assert ":assistant_request_id" in str(statement)
        assert statement.compile().params == {"assistant_request_id_1": "A"}

    def test_c42_4_no_select_star(self) -> None:
        sql = str(
            LLMUsageRepository().build_trace_select(assistant_request_id="A")
        )
        assert "SELECT *" not in sql.upper()
        # SELECT 列表恰为白名单的 9 个显式列（无 * / 无额外列）
        statement = LLMUsageRepository().build_trace_select(
            assistant_request_id="A"
        )
        assert list(statement.selected_columns.keys()) == list(
            LLM_USAGE_TRACE_READ_COLUMNS
        )
        # trace 读路径（两个方法）内部不使用 text() / 字符串拼接
        module_tree = _tree(_REPOSITORY_MODULE)
        for function_name in (
            "build_trace_select", "list_by_assistant_request_id",
        ):
            function = next(
                node
                for node in ast.walk(module_tree)
                if isinstance(node, ast.FunctionDef)
                and node.name == function_name
            )
            calls = {
                node.func.id
                for node in ast.walk(function)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
            }
            assert "text" not in calls, (function_name, calls)

    def test_c42_5_returns_frozen_row_dto(self) -> None:
        row = _trace_row()
        with pytest.raises(Exception):
            row.id = 999  # type: ignore[misc]
        source = _source(_REPOSITORY_MODULE)
        # trace 读路径显式映射为内部 Row（不把 ORM 对象交给上层）
        assert "_to_trace_row" in source
        assert "LLMUsageTraceRow" in source

    def test_c42_6_read_service_has_no_orm_or_session_access(self) -> None:
        identifiers = _identifiers(_QUERY_MODULE)
        for forbidden in (
            "session", "engine", "commit", "rollback", "sqlalchemy",
            "select", "execute",
        ):
            assert forbidden not in identifiers, forbidden
        imports = {
            node.module or ""
            for node in ast.walk(_tree(_QUERY_MODULE))
            if isinstance(node, ast.ImportFrom)
        }
        assert not any("sqlalchemy" in name for name in imports), imports

    def test_c42_7_no_new_http_endpoint(self) -> None:
        from fastapi.testclient import TestClient

        from backend.app.main import app

        paths = set(app.openapi()["paths"])
        for forbidden in ("by-request", "trace", "assistant/trace"):
            assert not any(forbidden in path for path in paths), forbidden
        for module in _API_MODULES:
            source = _source(module)
            assert "list_by_assistant_request_id" not in source, module
            assert "trace_read" not in source, module
        with TestClient(app) as client:
            assert client.get("/api/usage/by-request").status_code == 404
            assert client.get("/api/trace").status_code == 404

    def test_c42_8_provider_request_id_semantics_unchanged(self) -> None:
        source = _source(_REPOSITORY_MODULE)
        assert "trace_id" not in _identifiers(_REPOSITORY_MODULE)
        assert "provider_request_id" not in _identifiers(_REPOSITORY_MODULE)
        # 既有 request_id 列语义（Provider 请求 ID）保持：写入路径未修改
        assert 'request_id=data["request_id"]' in source
        row = _trace_row(request_id="chatcmpl-P1")
        assert row.request_id == "chatcmpl-P1"

    def test_c42_9_historical_null_compatible(self) -> None:
        from backend.app.db.llm_usage_repository import (
            LLM_USAGE_READ_COLUMNS,
        )
        from backend.app.services.llm_usage_query_service import (
            LLM_USAGE_VIEW_FIELDS,
        )

        # 历史行（assistant_request_id = NULL）仍可通过 analytics 读路径读取
        assert LLM_USAGE_READ_COLUMNS == (
            "id", "request_id", "provider", "model", "prompt_tokens",
            "completion_tokens", "total_tokens", "created_at",
        )
        assert LLM_USAGE_VIEW_FIELDS == frozenset({
            "id", "request_id", "provider", "model", "prompt_tokens",
            "completion_tokens", "total_tokens", "created_at",
        })

    def test_c42_10_no_prompt_or_secrets(self) -> None:
        for module in (_REPOSITORY_MODULE, _QUERY_MODULE):
            source = _source(module)
            for forbidden in (
                '"prompt"', '"messages"', '"raw_response"', '"api_key"',
                '"authorization"', '"password"', '"database_url"',
                '"tool_args"', '"tool_result"', '"sql"', '"rag_content"',
                '"chunk_content"',
            ):
                assert forbidden not in source, (module, forbidden)
        for field_name in LLM_USAGE_TRACE_VIEW_FIELDS:
            assert field_name in {
                "id", "assistant_request_id", "request_id", "provider",
                "model", "prompt_tokens", "completion_tokens", "total_tokens",
                "created_at",
            }


__all__ = [
    "TestTraceReadService",
    "TestTraceRepository",
    "TestC42AssistantTraceReadBoundary",
]
