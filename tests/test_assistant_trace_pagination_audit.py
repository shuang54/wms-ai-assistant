"""Assistant Trace Pagination Boundary Audit（Phase 3.12 Step 59）——**只读审计测试**。

审计结论（本文件锁定现状，**不修改任何生产行为**）：

    * Pagination = **NOT IMPLEMENTED**（无 limit / offset / page / cursor / total /
      has_more / next_cursor；HTTP 只有 1 个 path 参数）
    * 单 Trace 规模：LLM ≤ 4（默认配置）· Tool ≤ 1 · RAG ≤ 1 → 合计 ≤ 6 条
    * 排序：LLM `created_at ASC, id ASC` · Tool `id ASC` · RAG `id ASC`（均含 tie-breaker）
    * 查询：三段各 **1** 次 SELECT（共 3 次；无 N+1 / 无 lazy loading / 无 LIMIT·OFFSET）
    * 索引：三个谓词列均有 btree 索引（DB-gated 断言）
    * 决策：**DEFER**（见 docs/evaluation/phase-3.12-step-59-assistant-trace-pagination-audit.md）
"""
from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from backend.app.api.assistant_trace import (
    AssistantTraceResponse,
    LLMUsageTraceResponse,
    RagExecutionTraceResponse,
    ToolExecutionTraceResponse,
)
from backend.app.db.llm_usage_repository import LLMUsageRepository
from backend.app.db.models.llm_usage_record import LLMUsageRecord
from backend.app.db.models.rag_execution_record import RagExecutionRecordModel
from backend.app.db.models.tool_execution_record import ToolExecutionRecordModel
from backend.app.db.rag_execution_repository import RagExecutionRepository
from backend.app.db.tool_execution_repository import ToolExecutionRepository
from backend.app.main import app
from backend.app.services.assistant_trace_query_service import (
    AssistantTraceQueryService,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ENDPOINT = "/api/observability/assistant-trace/{assistant_request_id}"

def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


# ============================================================
# 1. HTTP contract：Pagination = NOT IMPLEMENTED
# ============================================================

class TestHttpContractHasNoPagination:
    def test_endpoint_takes_only_the_path_parameter(self) -> None:
        operation = app.openapi()["paths"][_ENDPOINT]["get"]

        parameters = operation.get("parameters", [])
        assert [(p["name"], p["in"]) for p in parameters] == [
            ("assistant_request_id", "path")
        ]
        # 明确禁止 query 参数（limit / offset / page / cursor …）
        assert not [p for p in parameters if p["in"] == "query"]

    def test_response_model_has_only_the_five_contract_fields(self) -> None:
        """Step 64：contract 新增 additive 的 ``outcome``（分页字段仍不存在）。"""
        assert set(AssistantTraceResponse.model_fields) == {
            "assistant_request_id",
            "outcome",                                   # Step 64（additive）
            "llm_usage",
            "tool_executions",
            "rag_executions",
        }

    def test_dto_field_sets_are_exact(self) -> None:
        """字段集合逐字锁定（新增分页字段会使本断言失败）。

        （Step 64：顶层新增 additive 的 ``outcome`` —— 已同步；分页字段仍为 0。）
        """
        assert set(LLMUsageTraceResponse.model_fields) == {
            "id",
            "assistant_request_id",
            "request_id",
            "provider",
            "model",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "created_at",
        }
        assert set(ToolExecutionTraceResponse.model_fields) == {
            "request_id",
            "round",
            "tool_name",
            "started_at",
            "finished_at",
            "duration_ms",
            "success",
            "project_id",
            "tool_call_id",
            "error_code",
            "error_type",
        }
        assert set(RagExecutionTraceResponse.model_fields) == {
            "request_id",
            "started_at",
            "finished_at",
            "duration_ms",
            "result_count",
            "used_chunks_count",
            "top_k",
            "context_truncated",
            "context_chars",
            "reranker_used",
            "rerank_elapsed_ms",
            "chunk_ids",
            "document_ids",
        }

    def test_no_pagination_field_names_in_any_dto(self) -> None:
        strict_tokens = (
            "limit",
            "offset",
            "page",
            "cursor",
            "has_more",
            "next_cursor",
        )
        for dto in (
            AssistantTraceResponse,
            LLMUsageTraceResponse,
            ToolExecutionTraceResponse,
            RagExecutionTraceResponse,
        ):
            names = tuple(dto.model_fields)
            for token in strict_tokens:
                assert not [
                    name for name in names if token in name.lower()
                ], f"{dto.__name__} 出现分页字段: {token}"


# ============================================================
# 2. 查询次数（每段 1 次 SELECT → 共 3 次；无 N+1）
# ============================================================

class _CountingLLM:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.calls: list[str] = []
        self._rows = rows or []

    def list_by_assistant_request_id(self, assistant_request_id: str) -> list[Any]:
        self.calls.append(assistant_request_id)
        return list(self._rows)


class _CountingTool:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.calls: list[str] = []
        self._rows = rows or []

    def list_by_request_id(self, request_id: str) -> list[Any]:
        self.calls.append(request_id)
        return list(self._rows)


class _CountingRag:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.calls: list[str] = []
        self._rows = rows or []

    def list_by_request_id(self, request_id: str) -> list[Any]:
        self.calls.append(request_id)
        return list(self._rows)


class TestQueryCount:
    def test_trace_composition_calls_each_source_exactly_once(self) -> None:
        llm, tool, rag = _CountingLLM(), _CountingTool(), _CountingRag()
        service = AssistantTraceQueryService(
            tool_observability_query_service=tool,
            llm_usage_query_service=llm,
            rag_execution_query_service=rag,
        )

        view = service.get_trace("step59-audit")

        assert llm.calls == ["step59-audit"]
        assert tool.calls == ["step59-audit"]
        assert rag.calls == ["step59-audit"]
        assert view.llm_usage == ()
        assert view.tool_executions == ()
        assert view.rag_executions == ()

    def test_each_repository_method_issues_exactly_one_select(self) -> None:
        for label, func in (
            ("LLM", LLMUsageRepository.list_by_assistant_request_id),
            ("Tool", ToolExecutionRepository.get_by_request_id),
            ("RAG", RagExecutionRepository.get_by_request_id),
        ):
            source = inspect.getsource(func)
            assert source.count("session.execute(") == 1, label
            assert source.count("select(") == 1, label
            assert ".limit(" not in source, label
            assert ".offset(" not in source, label

    def test_orm_models_have_no_relationships(self) -> None:
        """无 relationship → 无 lazy loading / 无隐藏 N+1。"""
        for model in (
            LLMUsageRecord,
            ToolExecutionRecordModel,
            RagExecutionRecordModel,
        ):
            assert list(model.__mapper__.relationships.keys()) == [], model.__name__


# ============================================================
# 3. 排序与稳定性
# ============================================================

class TestOrdering:
    def test_llm_ordering_is_created_at_then_id(self) -> None:
        source = inspect.getsource(LLMUsageRepository.build_trace_select)

        assert "created_at.asc()" in source
        assert "id.asc()" in source
        assert ".desc()" not in source

    @pytest.mark.parametrize(
        "builder",
        [
            ToolExecutionRepository.build_request_select,
            RagExecutionRepository.build_request_select,
        ],
    )
    def test_tool_and_rag_ordering_is_id_asc(self, builder: Any) -> None:
        source = inspect.getsource(builder)

        assert "id.asc()" in source
        assert ".desc()" not in source
        assert ".limit(" not in source
        assert ".offset(" not in source


# ============================================================
# 4. 单 Trace 规模上限（来源 = 当前代码常量）
# ============================================================

class TestTraceSizeBounds:
    def test_text_to_sql_attempt_bound_is_the_only_llm_multiplier(self) -> None:
        from backend.app.config import settings
        from backend.app.services.text_to_sql_service import (
            DEFAULT_MAX_ATTEMPTS,
        )

        assert DEFAULT_MAX_ATTEMPTS == 3
        assert settings.text_to_sql.max_attempts == 3

    def test_no_explicit_trace_row_cap_constant(self) -> None:
        """当前**没有**应用级 Trace 行数上限常量（记录这一事实）。"""
        import backend.app.services.assistant_trace_query_service as module

        assert "MAX_TRACE" not in inspect.getsource(module)
        assert "MAX_RECORDS_PER_TRACE" not in inspect.getsource(module)

    def test_toolchat_rounds_do_not_belong_to_assistant_trace_id(self) -> None:
        """ToolChat 的多轮（max_rounds）使用**独立 request_id**，不进入 Trace 上限。"""
        source = (
            _REPO_ROOT / "backend/app/services/tool_chat_service.py"
        ).read_text(encoding="utf-8")

        assert "new_request_id()" in source                  # 自带 ID 体系
        assert "assistant_trace_scope" not in source         # 不进入 Assistant Trace scope

    def test_single_route_execution_per_assistant_request(self) -> None:
        """Orchestrator 一次 execute 只走**一条**路由（无 loop / 无重规划）。"""
        from backend.app.services import ai_orchestrator_service as module

        source = inspect.getsource(module)
        assert "单次执行（无 Agent / 无 Loop / 无重规划）" in source


# ============================================================
# 5. [DB] 索引可用性（三个谓词列）
# ============================================================

@requires_db
class TestIndexAvailabilityDb:
    def test_trace_predicates_are_indexed(self) -> None:
        from backend.app.db.session import get_engine

        engine = get_engine()
        if engine is None:
            pytest.skip("DATABASE_URL 未配置")

        expected = {
            "llm_usage_record": "ix_llm_usage_record_assistant_request_id",
            "tool_execution_record": "ix_tool_execution_record_request_id",
            "rag_execution_record": "ix_rag_execution_record_request_id",
        }

        with engine.connect() as conn:
            for table, index_name in expected.items():
                row = conn.execute(
                    text(
                        "SELECT indexdef FROM pg_indexes "
                        "WHERE schemaname = 'ai_ops' AND tablename = :table "
                        "AND indexname = :index"
                    ),
                    {"table": table, "index": index_name},
                ).scalar_one_or_none()

                assert row is not None, f"{table}.{index_name} 不存在"
                assert "USING btree" in row
                assert "UNIQUE" not in row.upper()
                assert "WHERE" not in row.upper()

    def test_current_persistent_volume_is_recorded(self) -> None:
        """记录当前开发库规模（不断言 0 —— 允许其它测试留下临时行）。"""
        from backend.app.db.session import get_engine

        engine = get_engine()
        if engine is None:
            pytest.skip("DATABASE_URL 未配置")

        with engine.connect() as conn:
            counts = {
                table: int(
                    conn.execute(
                        text(f"SELECT COUNT(*) FROM ai_ops.{table}")  # noqa: S608
                    ).scalar_one()
                )
                for table in (
                    "llm_usage_record",
                    "tool_execution_record",
                    "rag_execution_record",
                )
            }

        assert all(count >= 0 for count in counts.values())
        assert set(counts) == {
            "llm_usage_record",
            "tool_execution_record",
            "rag_execution_record",
        }


__all__ = [
    "TestHttpContractHasNoPagination",
    "TestQueryCount",
    "TestOrdering",
    "TestTraceSizeBounds",
    "TestIndexAvailabilityDb",
]
