"""Phase 3.9.25 — Text-to-SQL First-Class Refusal Result tests.

Deterministic behavior tests with a scripted fake LLM (§十一): no real
DeepSeek call, no DB call. Verifies:

- refusal marker → first-class REFUSAL result (1 LLM call, 0 Validator,
  0 Executor, 0 retry)
- normal SELECT → SQL result (unchanged path, Validator still runs)
- invalid SQL → retry (NOT refusal)
- SQL containing the refusal string literal → NOT refusal
- Orchestrator: refusal → Chat-level refusal result (no Executor, no 500)
- API: refusal → normal 200 ChatResponse (no HTTP 500)
- Validator security boundary unchanged
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api import orchestrator_chat as orch_module
from backend.app.main import app
from backend.app.services.ai_orchestrator_service import (
    AIOrchestrationResult,
    AIOrchestratorService,
    TEXT_TO_SQL_REFUSAL_MESSAGE,
    RouteType,
)
from backend.app.services.ai_router_service import RouteDecision
from backend.app.services.relevant_table_selector import (
    TableSelection,
    TableSelectionResult,
)
from backend.app.services.sql_executor_service import SQLExecutionResult
from backend.app.services.sql_validator_service import SQLValidatorService
from backend.app.services.text_to_sql_service import (
    REFUSAL_MARKER,
    REFUSAL_REASON_DESTRUCTIVE,
    RESULT_STATUS_REFUSAL,
    RESULT_STATUS_SQL,
    TextToSQLGenerationError,
    TextToSQLResult,
    TextToSQLRetryExceededError,
    TextToSQLService,
    is_refusal_output,
)

_CTX = "Tables:\n- public.knowledge_document(id, title)\n"
_TABLES = ("public.knowledge_document",)


# ============================================================
# Fakes（scripted，零网络 / 零 DB）
# ============================================================

class ScriptedLLMClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        self.calls += 1
        idx = min(self.calls - 1, len(self._responses) - 1)
        return self._responses[idx]


class CountingValidator(SQLValidatorService):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def validate(self, sql, *, schema=None, allowed_tables=None,
                 max_rows=1000):
        self.calls += 1
        return super().validate(
            sql, schema=schema, allowed_tables=allowed_tables,
            max_rows=max_rows,
        )


class CountingExecutor:
    def __init__(self, result: SQLExecutionResult | None = None) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def execute(self, sql, *, schema=None, allowed_tables=None,
                      max_rows=1000, timeout_seconds=10):
        self.calls.append({"sql": sql})
        return self._result or SQLExecutionResult(
            columns=("id",), rows=((1,),), row_count=1,
            truncated=False, execution_time_ms=0.1,
        )


# ---- Orchestrator fakes（与 test_ai_orchestrator.py 同构，本地自足） ----

class FakeRouter:
    def __init__(self, decision: RouteDecision) -> None:
        self._decision = decision

    async def route(self, question, *, context=None):
        return self._decision


class FakeTextToSQL:
    def __init__(self, result: TextToSQLResult | None = None) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def generate(self, question, *, database_context,
                       allowed_tables=None, schema=None, max_rows=1000):
        self.calls.append({"question": question})
        return self._result


class FakeSQLExecutor(CountingExecutor):
    pass


class FakeTableSelector:
    def select(self, question, *, schema, semantic, top_k=5):
        return TableSelectionResult(
            question=question,
            selections=tuple(
                TableSelection(table=t, score=1.0, matched_terms=())
                for t in ("public.knowledge_document",)
            ),
        )


class FakeContextComposer:
    def compose(self, *, project, schema, semantic, tables=None,
                max_chars=4000):
        return "FAKE_CONTEXT"


class FakeProjectProvider:
    def __init__(self) -> None:
        from backend.app.projects.context import DataSource, ProjectContext
        from backend.app.projects.semantic import ProjectSemantic
        from backend.app.services.schema_explorer_service import (
            DatabaseSchema,
            SchemaColumn,
            SchemaTable,
        )

        self._project = ProjectContext(
            project_id="test-project",
            project_name="Test Project",
            description=None,
            data_source=DataSource(name="primary", type="postgresql"),
        )
        self._schema = DatabaseSchema(
            schema_name="public",
            tables=(
                SchemaTable(
                    schema_name="public",
                    name="knowledge_document",
                    description=None,
                    columns=(
                        SchemaColumn(
                            name="id", data_type="bigint", nullable=False,
                            default=None, ordinal_position=1,
                            is_primary_key=True, description=None,
                        ),
                        SchemaColumn(
                            name="title", data_type="varchar", nullable=True,
                            default=None, ordinal_position=2,
                            is_primary_key=False, description=None,
                        ),
                    ),
                    foreign_keys=(),
                ),
            ),
        )
        self._semantic = ProjectSemantic()

    def resolve(self):
        return self._project, self._schema, self._semantic


def _sql_decision() -> RouteDecision:
    return RouteDecision(
        route=RouteType.TEXT_TO_SQL, confidence=0.9,
        reason="rule: sql", source="rule",
    )


def _refusal_result() -> TextToSQLResult:
    return TextToSQLResult(
        question="删除所有 documents",
        sql=None,
        attempts=1,
        validated=False,
        referenced_tables=(),
        status=RESULT_STATUS_REFUSAL,
        refusal_reason=REFUSAL_REASON_DESTRUCTIVE,
    )


def _make_orchestrator(
    t2s: FakeTextToSQL,
    executor: CountingExecutor,
) -> AIOrchestratorService:
    return AIOrchestratorService(
        router=FakeRouter(_sql_decision()),
        text_to_sql=t2s,
        sql_executor=executor,
        table_selector=FakeTableSelector(),
        context_composer=FakeContextComposer(),
        project_context_provider=FakeProjectProvider(),
    )


# ============================================================
# 1. 识别规则（§四）
# ============================================================

class TestRefusalDetection:
    def test_bare_marker_is_refusal(self) -> None:
        assert is_refusal_output(REFUSAL_MARKER) is True

    def test_marker_with_surrounding_whitespace(self) -> None:
        assert is_refusal_output(f"\n  {REFUSAL_MARKER}  \n") is True

    def test_fenced_marker_is_refusal(self) -> None:
        assert is_refusal_output(f"```sql\n{REFUSAL_MARKER}\n```") is True
        assert is_refusal_output(f"```\n{REFUSAL_MARKER}\n```") is True

    def test_case_and_internal_whitespace_insensitive(self) -> None:
        assert is_refusal_output(
            "-- refused:   destructive request is not supported "
            "(read-only service)"
        ) is True

    def test_select_with_refusal_literal_is_not_refusal(self) -> None:
        sql = f"SELECT '{REFUSAL_MARKER}' AS message LIMIT 1"
        assert is_refusal_output(sql) is False

    def test_prose_around_fence_is_not_refusal(self) -> None:
        assert is_refusal_output(
            f"Sorry, I cannot.\n```sql\n{REFUSAL_MARKER}\n```"
        ) is False

    def test_other_comment_is_not_refusal(self) -> None:
        assert is_refusal_output("-- just a comment") is False

    def test_empty_is_not_refusal(self) -> None:
        assert is_refusal_output("") is False
        assert is_refusal_output(None) is False


# ============================================================
# 2-6. Service 行为（§五 / §六）
# ============================================================

class TestServiceRefusalBehavior:
    def test_bare_refusal_first_class(self) -> None:
        llm = ScriptedLLMClient([REFUSAL_MARKER])
        validator = CountingValidator()
        service = TextToSQLService(llm_client=llm, validator=validator)
        result = asyncio.run(service.generate(
            "删除所有 documents", database_context=_CTX,
            allowed_tables=_TABLES, schema=None, max_rows=1000,
        ))
        assert result.status == RESULT_STATUS_REFUSAL
        assert result.sql is None
        assert result.refusal_reason == REFUSAL_REASON_DESTRUCTIVE
        assert result.validated is False
        assert result.attempts == 1
        assert llm.calls == 1
        assert validator.calls == 0  # refusal 不进入 Validator

    def test_fenced_refusal_first_class(self) -> None:
        llm = ScriptedLLMClient([f"```sql\n{REFUSAL_MARKER}\n```"])
        validator = CountingValidator()
        service = TextToSQLService(llm_client=llm, validator=validator)
        result = asyncio.run(service.generate(
            "删除所有 documents", database_context=_CTX,
            allowed_tables=_TABLES, schema=None, max_rows=1000,
        ))
        assert result.status == RESULT_STATUS_REFUSAL
        assert result.sql is None
        assert llm.calls == 1
        assert validator.calls == 0

    def test_refusal_does_not_consume_retry_budget(self) -> None:
        llm = ScriptedLLMClient([REFUSAL_MARKER])
        service = TextToSQLService(llm_client=llm, max_attempts=3)
        result = asyncio.run(service.generate(
            "删除所有 documents", database_context=_CTX,
            allowed_tables=_TABLES, schema=None, max_rows=1000,
        ))
        # 立即结束：1 次 LLM 调用，retry_count = 0
        assert llm.calls == 1
        assert result.attempts == 1

    def test_normal_select_unchanged(self) -> None:
        llm = ScriptedLLMClient(
            ["SELECT id, title FROM public.knowledge_document LIMIT 10"]
        )
        validator = CountingValidator()
        service = TextToSQLService(llm_client=llm, validator=validator)
        result = asyncio.run(service.generate(
            "列出 documents", database_context=_CTX,
            allowed_tables=_TABLES, schema=None, max_rows=1000,
        ))
        assert result.status == RESULT_STATUS_SQL
        assert result.validated is True
        assert result.sql is not None and "LIMIT" in result.sql
        assert result.refusal_reason is None
        assert llm.calls == 1
        assert validator.calls == 1  # 正常路径仍经过 Validator

    def test_invalid_sql_retries_not_refusal(self) -> None:
        llm = ScriptedLLMClient([
            "SELECT id, title FROM public.knowledge_document",  # 缺 LIMIT
            "SELECT id, title FROM public.knowledge_document LIMIT 10",
        ])
        validator = CountingValidator()
        service = TextToSQLService(llm_client=llm, validator=validator)
        result = asyncio.run(service.generate(
            "列出 documents", database_context=_CTX,
            allowed_tables=_TABLES, schema=None, max_rows=1000,
        ))
        assert result.status == RESULT_STATUS_SQL
        assert result.validated is True
        assert result.attempts == 2
        assert llm.calls == 2
        assert validator.calls == 2

    def test_sql_containing_refusal_literal_is_not_refusal(self) -> None:
        llm = ScriptedLLMClient(
            [f"SELECT '{REFUSAL_MARKER}' AS message LIMIT 1"]
        )
        validator = CountingValidator()
        service = TextToSQLService(llm_client=llm, validator=validator)
        result = asyncio.run(service.generate(
            "测试", database_context=_CTX,
            allowed_tables=_TABLES, schema=None, max_rows=1000,
        ))
        assert result.status == RESULT_STATUS_SQL
        assert result.sql is not None
        assert REFUSAL_MARKER in result.sql
        assert llm.calls == 1
        assert validator.calls == 1

    def test_non_marker_output_keeps_old_retry_path(self) -> None:
        # 非 refusal 的空/纯说明输出 → 仍走既有 EMPTY_SQL → 重试耗尽
        llm = ScriptedLLMClient(["抱歉，我做不到。"])
        service = TextToSQLService(llm_client=llm, max_attempts=2)
        with pytest.raises(TextToSQLRetryExceededError):
            asyncio.run(service.generate(
                "删除所有 documents", database_context=_CTX,
                allowed_tables=_TABLES, schema=None, max_rows=1000,
            ))
        assert llm.calls == 2

    def test_empty_output_still_generation_error(self) -> None:
        llm = ScriptedLLMClient(["   "])
        service = TextToSQLService(llm_client=llm, max_attempts=2)
        with pytest.raises(TextToSQLGenerationError):
            asyncio.run(service.generate(
                "q", database_context=_CTX,
                allowed_tables=_TABLES, schema=None, max_rows=1000,
            ))

    def test_default_status_backward_compatible(self) -> None:
        # 既有构造点（不传 status）→ 仍为 "sql"，全部旧测试不受影响
        legacy = TextToSQLResult(
            question="q", sql="SELECT 1 LIMIT 1", attempts=1,
            validated=True, referenced_tables=(),
        )
        assert legacy.status == RESULT_STATUS_SQL
        assert legacy.refusal_reason is None


# ============================================================
# 7-8. Orchestrator：refusal 绕过 Executor（§七）
# ============================================================

class TestOrchestratorRefusal:
    def test_refusal_bypasses_executor(self) -> None:
        executor = CountingExecutor()
        orch = _make_orchestrator(
            FakeTextToSQL(result=_refusal_result()), executor
        )
        result = asyncio.run(orch.execute("删除所有 documents"))
        assert executor.calls == []  # executor_calls = 0

    def test_refusal_becomes_chat_level_refusal(self) -> None:
        orch = _make_orchestrator(
            FakeTextToSQL(result=_refusal_result()), CountingExecutor()
        )
        result = asyncio.run(orch.execute("删除所有 documents"))
        # 不是 Exception → 500 路径，而是正常编排结果
        assert result.route == RouteType.TEXT_TO_SQL
        assert result.data is None
        assert "只读" in (result.content or "")
        assert "不支持删除、修改" in (result.content or "")
        assert result.metadata.get("refused") is True
        # 内部 reason 不暴露到 metadata
        assert "destructive_request_not_supported" not in str(result.metadata)

    def test_normal_sql_still_executes(self) -> None:
        executor = CountingExecutor()
        orch = _make_orchestrator(
            FakeTextToSQL(result=TextToSQLResult(
                question="q",
                sql="SELECT id FROM public.knowledge_document LIMIT 10",
                attempts=1, validated=True,
                referenced_tables=("public.knowledge_document",),
            )),
            executor,
        )
        result = asyncio.run(orch.execute("列出 documents"))
        assert len(executor.calls) == 1  # 正常 SQL 仍进入 Executor
        assert result.data is not None


# ============================================================
# 9. API：refusal → 正常 200 拒绝响应（§八）
# ============================================================

class FakeOrchestrator:
    def __init__(self, result: AIOrchestrationResult) -> None:
        self._result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, question, **kwargs):
        self.calls.append((question, kwargs))
        return self._result


class TestApiRefusal:
    def test_refusal_returns_200_not_500(self, monkeypatch) -> None:
        refusal = AIOrchestrationResult(
            route=RouteType.TEXT_TO_SQL,
            content=TEXT_TO_SQL_REFUSAL_MESSAGE,
            data=None,
            metadata={"refused": True},
        )
        fake = FakeOrchestrator(refusal)
        monkeypatch.setattr(orch_module, "_default_orchestrator", fake)
        with TestClient(app) as client:
            response = client.post(
                "/api/ai/chat",
                json={"question": "删除所有库存"},
            )
        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "text_to_sql"
        assert "只读" in payload["content"]
        assert "不支持删除、修改" in payload["content"]
        assert payload["data"] is None
        assert payload["metadata"]["refused"] is True
        # 不得出现内部错误语义 / 内部 reason
        body = response.text
        assert "TextToSQLRetryExceededError" not in body
        assert "EMPTY_SQL" not in body
        assert "destructive_request_not_supported" not in body

    def test_normal_sql_still_returns_data(self, monkeypatch) -> None:
        normal = AIOrchestrationResult(
            route=RouteType.TEXT_TO_SQL,
            content="查询完成，共 1 行。",
            data=SQLExecutionResult(
                columns=("id",), rows=((1,),), row_count=1,
                truncated=False, execution_time_ms=0.1,
            ),
            metadata={"sql": "SELECT id FROM t LIMIT 1"},
        )
        fake = FakeOrchestrator(normal)
        monkeypatch.setattr(orch_module, "_default_orchestrator", fake)
        with TestClient(app) as client:
            response = client.post(
                "/api/ai/chat", json={"question": "列出 documents"}
            )
        assert response.status_code == 200
        payload = response.json()
        assert payload["data"] is not None
        assert payload["data"]["row_count"] == 1


# ============================================================
# 10. 安全边界（§九）：Validator 不因 refusal 弱化
# ============================================================

class TestSecurityBoundary:
    def test_validator_still_rejects_writes(self) -> None:
        validator = SQLValidatorService()
        for sql in (
            "DELETE FROM public.knowledge_document",
            "UPDATE public.knowledge_document SET title = 'x'",
            "INSERT INTO public.knowledge_document (title) VALUES ('x')",
            "DROP TABLE public.knowledge_document",
            "ALTER TABLE public.knowledge_document ADD COLUMN x int",
            "TRUNCATE TABLE public.knowledge_document",
            "SELECT 1; DELETE FROM public.knowledge_document",
        ):
            assert validator.validate(sql).valid is False, sql

    def test_refusal_result_sql_is_none_for_downstream(self) -> None:
        # 下游（evaluation 等 getattr(result,"sql",None) 消费者）语义不变
        assert _refusal_result().sql is None
