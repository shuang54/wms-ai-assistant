"""Text-to-SQL Evaluation & Regression Dataset 测试（Phase 3.9.3）。

分层：

1. Dataset Loader：正常加载 / YAML 错误 / 缺 id / 空 question /
   duplicate id / 非法 expected / 确定性（§十三）
2. Expectation Checker：validation / table / column / forbidden /
   execution / 不可解析 SQL（§九，全部走 sqlglot AST）
3. Runner：全通过 / 部分失败 / **绝不绕过 Validator** / 执行失败 /
   Generator 异常 / 确定性 / 文本报告（§八、§十四、§十五）
4. Project Isolation：project-a / project-b / 跨项目污染检测（§十）
5. E2E（非 DB）：Dataset → Fake Generator → 真实 Validator → Result；
   Dataset → 真实 TextToSQLService(FakeLLMClient) → Runner → Validator
6. DB（RUN_DB_TESTS=1）：真实 Schema / Validator / Executor 集成
   （临时 schema 先 DROP IF EXISTS 再 CREATE，finally 内 DROP CASCADE）

绝大多数用例离线运行：不依赖网络、不消耗 API、不需要 LLM。
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text as sa_text

from backend.app.config import settings
from backend.app.projects.models import DataSource, ProjectContext
from backend.app.projects.semantic import (
    ColumnSemantic,
    ProjectSemantic,
    TableSemantic,
)
from backend.app.projects.semantic_loader import ProjectSemanticLoader
from backend.app.services.database_context_composer import DatabaseContextComposer
from backend.app.services.relevant_table_selector import (
    RuleBasedRelevantTableSelector,
)
from backend.app.services.schema_explorer_service import (
    DatabaseSchema,
    SchemaColumn,
    SchemaTable,
)
from backend.app.services.semantic_schema_filter import SemanticSchemaFilter
from backend.app.services.sql_validator_service import (
    SQLValidationCode,
    SQLValidationError,
    SQLValidationResult,
    SQLValidatorService,
)
from backend.app.services.text_to_sql_context import TextToSQLContext
from backend.app.services.text_to_sql_evaluation_service import (
    DEFAULT_REGRESSION_DATASET_PATH,
    EXPECTATION_COLUMNS,
    EXPECTATION_EXECUTION,
    EXPECTATION_FORBIDDEN,
    EXPECTATION_TABLES,
    EXPECTATION_VALIDATION,
    EvaluationProjectBinding,
    StaticEvaluationContextResolver,
    TextToSQLEvaluationCase,
    TextToSQLEvaluationDatasetConfigError,
    TextToSQLEvaluationDatasetNotFoundError,
    TextToSQLEvaluationInput,
    TextToSQLEvaluationRunner,
    TextToSQLEvaluationRunnerError,
    TextToSQLExecutionOutcome,
    TextToSQLExpectations,
    check_expectations,
    load_text_to_sql_regression_dataset,
    with_execution_required,
)
from backend.app.services.text_to_sql_service import (
    TextToSQLResult,
    TextToSQLService,
)


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not (_env_flag("RUN_DB_TESTS") and settings.database.url.strip()),
    reason="set RUN_DB_TESTS=1 (plus DATABASE_URL) to enable Text-to-SQL "
    "evaluation DB tests",
)

DATASET_PATH = DEFAULT_REGRESSION_DATASET_PATH


# ============================================================
# 通用 helpers
# ============================================================

def load_dataset(path: Path | None = None) -> tuple[TextToSQLEvaluationCase, ...]:
    return load_text_to_sql_regression_dataset(path)


def dataset_case(case_id: str) -> TextToSQLEvaluationCase:
    for case in load_dataset():
        if case.case_id == case_id:
            return case
    raise AssertionError(f"dataset case not found: {case_id}")


def check(
    case: TextToSQLEvaluationCase,
    sql: str | None,
    validation: SQLValidationResult | None,
    execution: TextToSQLExecutionOutcome | None = None,
    schema: DatabaseSchema | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return check_expectations(case, sql, validation, execution, schema)


def run_sync(awaitable: Any) -> Any:
    """同步等待（部分测试用同步写法，与项目既有风格一致）。"""
    return asyncio.run(awaitable)


def case_with(**expected: Any) -> TextToSQLEvaluationCase:
    return TextToSQLEvaluationCase(
        case_id="c1",
        question="查询库存明细",
        project_id="p",
        expected=TextToSQLExpectations(**expected),
    )


# ============================================================
# Schema / Semantic builders（不虚构字段，结构与真实库一致）
# ============================================================

def _column(name: str, position: int) -> SchemaColumn:
    return SchemaColumn(
        name=name, data_type="character varying", nullable=True,
        default=None, ordinal_position=position, is_primary_key=False,
        description=None,
    )


def _table(
    schema_name: str, name: str, columns: tuple[str, ...]
) -> SchemaTable:
    return SchemaTable(
        schema_name=schema_name,
        name=name,
        description=None,
        columns=tuple(_column(c, i + 1) for i, c in enumerate(columns)),
        foreign_keys=(),
    )


def knowledge_schema() -> DatabaseSchema:
    """public schema：真实知识库两张表（字段取自真实 DB）。"""
    return DatabaseSchema(
        schema_name="public",
        tables=(
            _table("public", "knowledge_document", (
                "id", "title", "file_name", "file_type", "source",
                "status", "created_at", "updated_at",
            )),
            _table("public", "knowledge_chunk", (
                "id", "document_id", "chunk_index", "content",
                "token_count", "created_at",
            )),
        ),
    )


def inventory_schema(schema_name: str) -> DatabaseSchema:
    """项目业务 schema（与既有 project_a / project_b fixture 同结构）。"""
    return DatabaseSchema(
        schema_name=schema_name,
        tables=(_table(schema_name, "inventory", ("material_code", "qty")),),
    )


def inventory_semantic(schema_name: str) -> ProjectSemantic:
    return ProjectSemantic(
        tables=(
            TableSemantic(
                table=f"{schema_name}.inventory",
                business_name="库存",
                aliases=("库存",),
            ),
        ),
        columns=(
            ColumnSemantic(
                table=f"{schema_name}.inventory", column="material_code",
                business_name="物料编码",
            ),
            ColumnSemantic(
                table=f"{schema_name}.inventory", column="qty",
                business_name="库存数量",
            ),
        ),
    )


def knowledge_semantic() -> ProjectSemantic:
    """直接加载真实项目语义 YAML（不在测试里复制业务文案）。"""
    return ProjectSemanticLoader().load("vietnam-wms")


def project(project_id: str, name: str) -> ProjectContext:
    return ProjectContext(
        project_id=project_id,
        project_name=name,
        description=None,
        data_source=DataSource(name="primary", type="postgresql"),
    )


def _bindings() -> dict[str, EvaluationProjectBinding]:
    return {
        "vietnam-wms": EvaluationProjectBinding(
            project=project("vietnam-wms", "Vietnam WMS"),
            schema=knowledge_schema(),
            semantic=knowledge_semantic(),
        ),
        "eval-project-a": EvaluationProjectBinding(
            project=project("eval-project-a", "Project A"),
            schema=inventory_schema("project_a"),
            semantic=inventory_semantic("project_a"),
        ),
        "eval-project-b": EvaluationProjectBinding(
            project=project("eval-project-b", "Project B"),
            schema=inventory_schema("project_b"),
            semantic=inventory_semantic("project_b"),
        ),
    }


def static_resolver() -> StaticEvaluationContextResolver:
    """真实链路 Resolver：Selector → SemanticFilter → Composer。"""
    return StaticEvaluationContextResolver(
        bindings=_bindings(),
        table_selector=RuleBasedRelevantTableSelector(),
        context_composer=DatabaseContextComposer(),
        semantic_filter=SemanticSchemaFilter(),
    )


def _context(
    allowed_tables: tuple[str, ...] = (), project_id: str | None = "p",
) -> TextToSQLContext:
    return TextToSQLContext(
        database_context=(
            "Project: p\nData Source: primary\nDatabase Type: postgresql\n\n"
            "## Database Schema"
        ),
        business_context=None,
        allowed_tables=allowed_tables,
        max_rows=1000,
        project_id=project_id,
    )


class DirectResolver:
    """最小 Resolver：跳过选表，直接返回固定上下文（单体验证用）。"""

    def __init__(
        self,
        *,
        schema: DatabaseSchema | None = None,
        allowed_tables: tuple[str, ...] = (),
    ) -> None:
        self._schema = schema
        self._allowed = allowed_tables

    def resolve(self, case: TextToSQLEvaluationCase) -> TextToSQLEvaluationInput:
        return TextToSQLEvaluationInput(
            context=_context(self._allowed, case.project_id),
            schema=self._schema,
        )


# ============================================================
# Fake 双件（contract 与 Phase 3.7.6 / 3.7.7 一致，未改动既有 Fake）
# ============================================================

class FakeGenerator:
    """确定性 Fake Generator。

    SQL 查找优先级：
        1) (question, schema_name) —— 同一问句按项目区分（A/B 隔离）
        2) question
        3) default_sql
    """

    def __init__(
        self,
        responses: dict[tuple[str, str] | str, str],
        *,
        raises: dict[str, Exception] | None = None,
        default_sql: str = "SELECT 1 LIMIT 1",
    ) -> None:
        self._responses = dict(responses)
        self._raises = dict(raises or {})
        self._default_sql = default_sql
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self, question, *, database_context, allowed_tables=None,
        schema=None, max_rows=1000,
    ):
        self.calls.append(
            {
                "question": question,
                "database_context": database_context,
                "allowed_tables": tuple(allowed_tables or ()),
                "schema_name": getattr(schema, "schema_name", None),
                "max_rows": max_rows,
            }
        )
        if question in self._raises:
            raise self._raises[question]
        schema_name = getattr(schema, "schema_name", "") or ""
        key: tuple[str, str] | str = (question, schema_name)
        if key in self._responses:
            sql = self._responses[key]
        elif question in self._responses:
            sql = self._responses[question]
        else:
            sql = self._default_sql
        return TextToSQLResult(
            question=question, sql=sql, attempts=1, validated=True,
            referenced_tables=tuple(allowed_tables or ()),
        )


class FakeExecutor:
    """Fake Executor（contract 与 SQLExecutor 一致）。"""

    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[str] = []

    async def execute(
        self, sql, *, schema=None, allowed_tables=None, max_rows=1000,
        timeout_seconds=10,
    ):
        self.calls.append(sql)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(columns=(), rows=(), row_count=0)


class FakeLLMClient:
    """Fake LLM Client（队列式响应，与项目既有 Fake 同款）。"""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def chat(self, messages, *, tools=None):
        self.calls.append(messages)
        if not self._responses:
            return ""
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# ============================================================
# Runner runner（统一构造 + 同步执行 helper）
# ============================================================

def make_runner(
    generator: Any,
    *,
    resolver: Any | None = None,
    executor: Any | None = None,
    validator: Any | None = None,
) -> TextToSQLEvaluationRunner:
    return TextToSQLEvaluationRunner(
        generator=generator,
        context_resolver=(
            resolver
            if resolver is not None
            else DirectResolver(schema=inventory_schema("public"))
        ),
        validator=validator if validator is not None else SQLValidatorService(),
        executor=executor,
    )


def run_case(runner: TextToSQLEvaluationRunner,
             case: TextToSQLEvaluationCase) -> Any:
    return run_sync(runner.run_case(case))


def valid_result() -> SQLValidationResult:
    return SQLValidationResult(
        valid=True, normalized_sql="SELECT 1", errors=(), referenced_tables=(),
    )


def invalid_result() -> SQLValidationResult:
    return SQLValidationResult(
        valid=False,
        normalized_sql="DELETE FROM x",
        errors=(
            SQLValidationError(
                SQLValidationCode.NON_READ_ONLY,
                "SQL contains forbidden statement: DELETE",
            ),
        ),
        referenced_tables=(),
    )


def write_dataset(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "cases.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# ============================================================
# Dataset Loader（§十三）
# ============================================================

class TestDatasetLoader:
    def test_loads_default_dataset(self) -> None:
        cases = load_dataset()
        assert len(cases) >= 12
        ids = [c.case_id for c in cases]
        assert len(ids) == len(set(ids))
        assert all(c.question.strip() for c in cases)
        assert all(c.project_id for c in cases)

    def test_dataset_covers_required_scenarios(self) -> None:
        """§五：覆盖 12 类代表性场景 (+ project A/B 隔离)。"""
        ids = {c.case_id for c in load_dataset()}
        assert {
            "simple_document_list",
            "top_n_chunks_by_token_count",
            "filtered_documents_by_file_type",
            "chunks_ordered_by_token_count",
            "aggregate_document_count",
            "group_by_chunk_count_per_document",
            "having_chunk_count_greater_than",
            "join_chunk_with_parent_document",
            "date_filter_created_after",
            "limit_first_10_documents",
            "semantic_dependent_document_and_chunk",
            "safety_delete_all_documents",
            "project_a_inventory",
            "project_b_inventory",
        } <= ids

    def test_dataset_never_requires_execution(self) -> None:
        """默认数据集必须能在无数据库环境下运行（pytest 零 DB 依赖）。"""
        assert all(not c.expected.must_execute for c in load_dataset())

    def test_missing_file_raises_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(TextToSQLEvaluationDatasetNotFoundError):
            load_dataset(tmp_path / "nope.yaml")

    def test_invalid_yaml_raises_config_error(self, tmp_path: Path) -> None:
        path = write_dataset(tmp_path, "cases: [\n  - id: x\n   bad: :::\n")
        with pytest.raises(TextToSQLEvaluationDatasetConfigError) as exc:
            load_dataset(path)
        assert "YAML 解析失败" in str(exc.value)

    def test_missing_id_raises_config_error(self, tmp_path: Path) -> None:
        path = write_dataset(tmp_path, "cases:\n  - question: 查询库存\n")
        with pytest.raises(TextToSQLEvaluationDatasetConfigError) as exc:
            load_dataset(path)
        assert "'id'" in str(exc.value)

    def test_empty_question_raises_config_error(self, tmp_path: Path) -> None:
        path = write_dataset(tmp_path, 'cases:\n  - id: x\n    question: "  "\n')
        with pytest.raises(TextToSQLEvaluationDatasetConfigError) as exc:
            load_dataset(path)
        assert "'question'" in str(exc.value)

    def test_duplicate_id_raises_config_error(self, tmp_path: Path) -> None:
        path = write_dataset(
            tmp_path,
            "cases:\n"
            "  - id: dup\n    question: 一\n"
            "  - id: dup\n    question: 二\n",
        )
        with pytest.raises(TextToSQLEvaluationDatasetConfigError) as exc:
            load_dataset(path)
        assert "重复定义 case id" in str(exc.value)

    def test_invalid_expected_raises_config_error(self, tmp_path: Path) -> None:
        unknown = write_dataset(
            tmp_path,
            "cases:\n"
            "  - id: x\n    question: q\n"
            "    expected:\n      must_be_nice: true\n",
        )
        with pytest.raises(TextToSQLEvaluationDatasetConfigError) as exc:
            load_dataset(unknown)
        assert "未知配置项" in str(exc.value)

        wrong_bool = write_dataset(
            tmp_path,
            "cases:\n"
            "  - id: y\n    question: q\n"
            "    expected:\n      must_pass_validation: 1\n",
        )
        with pytest.raises(TextToSQLEvaluationDatasetConfigError):
            load_dataset(wrong_bool)

        wrong_list = write_dataset(
            tmp_path,
            "cases:\n"
            "  - id: z\n    question: q\n"
            "    expected:\n      must_contain_tables: inventory\n",
        )
        with pytest.raises(TextToSQLEvaluationDatasetConfigError):
            load_dataset(wrong_list)

    def test_cases_section_must_be_valid(self, tmp_path: Path) -> None:
        missing = write_dataset(tmp_path, "_schema_version: '1.0'\n")
        with pytest.raises(TextToSQLEvaluationDatasetConfigError) as exc:
            load_dataset(missing)
        assert "cases 缺失" in str(exc.value)

        wrong = write_dataset(tmp_path, "cases: nope\n")
        with pytest.raises(TextToSQLEvaluationDatasetConfigError) as exc:
            load_dataset(wrong)
        assert "cases 必须是列表" in str(exc.value)

    def test_load_is_deterministic(self) -> None:
        first = load_dataset()
        second = load_dataset()
        assert isinstance(first, tuple)
        assert first == second
        with pytest.raises(Exception):
            first[0].case_id = "mutated"  # type: ignore[misc]


# ============================================================
# Expectation Checker（§九）
# ============================================================

class TestExpectationChecker:
    def test_validation_expectation_matched(self) -> None:
        case = case_with(must_pass_validation=True)
        matched, failed = check(
            case, "SELECT id FROM inventory LIMIT 10", valid_result()
        )
        assert matched == (EXPECTATION_VALIDATION,)
        assert failed == ()

    def test_validation_expectation_failed(self) -> None:
        case = case_with(must_pass_validation=True)
        matched, failed = check(
            case, "SELECT id FROM inventory LIMIT 10", invalid_result()
        )
        assert failed == (EXPECTATION_VALIDATION,)
        assert matched == ()

    def test_negative_validation_expectation_matched(self) -> None:
        """安全边界 case：SQL 必须被 Validator 拒绝。"""
        case = case_with(must_pass_validation=False)
        matched, failed = check(case, "DELETE FROM inventory", invalid_result())
        assert matched == (EXPECTATION_VALIDATION,)
        assert failed == ()

    def test_table_match_and_mismatch(self) -> None:
        case = case_with(must_contain_tables=("inventory",))
        matched, _ = check(
            case, "SELECT id FROM public.inventory LIMIT 10", None
        )
        assert EXPECTATION_TABLES in matched

        missing = case_with(must_contain_tables=("sales_order",))
        _, failed = check(
            missing, "SELECT id FROM public.inventory LIMIT 10", None
        )
        assert EXPECTATION_TABLES in failed

    def test_column_match_resolves_alias(self) -> None:
        """FROM inventory i → i.qty 必须匹配期望 inventory.qty。"""
        case = case_with(must_contain_columns=("inventory.qty",))
        matched, failed = check(
            case, "SELECT i.qty FROM public.inventory i LIMIT 10", None
        )
        assert EXPECTATION_COLUMNS in matched
        assert failed == ()

    def test_column_match_resolves_bare_column_via_schema(self) -> None:
        """裸列名（完全合法的写法）不得被误判为回归。"""
        case = case_with(must_contain_columns=("inventory.qty",))
        matched, failed = check(
            case, "SELECT qty FROM inventory LIMIT 10", None,
            schema=inventory_schema("public"),
        )
        assert EXPECTATION_COLUMNS in matched
        assert failed == ()

    def test_column_mismatch(self) -> None:
        case = case_with(must_contain_columns=("inventory.fake_column",))
        _, failed = check(
            case, "SELECT qty FROM inventory LIMIT 10", None,
            schema=inventory_schema("public"),
        )
        assert EXPECTATION_COLUMNS in failed

    def test_forbidden_identifier_present_fails(self) -> None:
        case = case_with(must_not_contain=("fake_column",))
        _, failed = check(
            case, "SELECT fake_column FROM inventory LIMIT 10", None
        )
        assert EXPECTATION_FORBIDDEN in failed

    def test_forbidden_identifier_absent_matches(self) -> None:
        case = case_with(must_not_contain=("fake_column",))
        matched, _ = check(case, "SELECT qty FROM inventory LIMIT 10", None)
        assert EXPECTATION_FORBIDDEN in matched

    def test_forbidden_check_ignores_string_literal(self) -> None:
        """AST 判定：字符串字面量中的同名文本不算命中。"""
        case = case_with(must_not_contain=("fake_column",))
        sql = "SELECT qty FROM inventory WHERE code = 'fake_column' LIMIT 10"
        matched, _ = check(case, sql, None)
        assert EXPECTATION_FORBIDDEN in matched

    def test_execution_expectation(self) -> None:
        case = case_with(must_execute=True)
        matched, _ = check(
            case, "SELECT 1 LIMIT 1", valid_result(),
            TextToSQLExecutionOutcome(attempted=True, passed=True),
        )
        assert EXPECTATION_EXECUTION in matched

        _, failed = check(
            case, "SELECT 1 LIMIT 1", valid_result(),
            TextToSQLExecutionOutcome(
                attempted=True, passed=False, error_code="FakeError"
            ),
        )
        assert EXPECTATION_EXECUTION in failed

    def test_unparseable_sql_fails_ast_expectations(self) -> None:
        case = case_with(
            must_contain_tables=("inventory",),
            must_not_contain=("fake_column",),
        )
        matched, failed = check(case, "SELECT FROM WHERE ???", None)
        assert matched == ()
        assert EXPECTATION_TABLES in failed
        assert EXPECTATION_FORBIDDEN in failed


# ============================================================
# Runner（§八 / §十四 / §十五）
# ============================================================

class TestRunner:
    def test_all_pass_summary(self) -> None:
        cases = (
            case_with(
                must_pass_validation=True, must_contain_tables=("inventory",)
            ),
            TextToSQLEvaluationCase(
                case_id="c2", question="查询库存数量 Top 10", project_id="p",
                expected=TextToSQLExpectations(
                    must_pass_validation=True,
                    must_contain_columns=("inventory.qty",),
                ),
            ),
        )
        generator = FakeGenerator(
            {
                "查询库存明细": (
                    "SELECT material_code FROM public.inventory LIMIT 10"
                ),
                "查询库存数量 Top 10": (
                    "SELECT material_code, qty FROM public.inventory "
                    "ORDER BY qty DESC LIMIT 10"
                ),
            }
        )
        summary = run_sync(make_runner(generator).run(cases))
        assert (summary.total, summary.passed, summary.failed) == (2, 2, 0)
        assert summary.validation_passed == 2
        assert summary.execution_passed == 0

    def test_partial_failure_summary(self) -> None:
        cases = (
            case_with(
                must_pass_validation=True, must_contain_tables=("inventory",)
            ),
            TextToSQLEvaluationCase(
                case_id="c2", question="查询订单", project_id="p",
                expected=TextToSQLExpectations(
                    must_contain_tables=("sales_order",)
                ),
            ),
        )
        generator = FakeGenerator(
            {
                "查询库存明细": (
                    "SELECT material_code FROM public.inventory LIMIT 10"
                ),
                "查询订单": (
                    "SELECT material_code FROM public.inventory LIMIT 10"
                ),
            }
        )
        summary = run_sync(make_runner(generator).run(cases))
        assert (summary.total, summary.passed, summary.failed) == (2, 1, 1)
        assert [r.case_id for r in summary.failed_results()] == ["c2"]

    def test_validator_cannot_be_bypassed(self) -> None:
        """Fake Generator 声称 validated=True，非法 SQL 仍必须失败。"""
        case = case_with(must_pass_validation=True)
        generator = FakeGenerator({"查询库存明细": "DELETE FROM inventory"})
        result = run_case(make_runner(generator), case)
        assert result.validation_passed is False
        assert result.error_code == SQLValidationCode.NON_READ_ONLY.value
        assert EXPECTATION_VALIDATION in result.failed_expectations

    def test_rejected_sql_skips_execution(self) -> None:
        case = with_execution_required(
            case_with(must_pass_validation=True)
        )
        generator = FakeGenerator({"查询库存明细": "DELETE FROM inventory"})
        executor = FakeExecutor()
        result = run_case(
            make_runner(generator, executor=executor), case
        )
        assert executor.calls == []
        assert result.execution_passed is False
        assert EXPECTATION_EXECUTION in result.failed_expectations

    def test_execution_failure_recorded(self) -> None:
        case = with_execution_required(
            case_with(must_pass_validation=True)
        )
        generator = FakeGenerator(
            {
                "查询库存明细": (
                    "SELECT material_code FROM public.inventory LIMIT 10"
                )
            }
        )
        executor = FakeExecutor(error=RuntimeError("boom"))
        result = run_case(make_runner(generator, executor=executor), case)
        assert result.validation_passed is True
        assert result.execution_passed is False
        assert result.error_code == "RuntimeError"
        assert EXPECTATION_EXECUTION in result.failed_expectations

    def test_executor_missing_raises_runner_error(self) -> None:
        case = with_execution_required(case_with(must_pass_validation=True))
        generator = FakeGenerator(
            {
                "查询库存明细": (
                    "SELECT material_code FROM public.inventory LIMIT 10"
                )
            }
        )
        with pytest.raises(TextToSQLEvaluationRunnerError):
            run_case(make_runner(generator), case)

    def test_generator_exception_recorded_not_raised(self) -> None:
        case = case_with(must_pass_validation=True)
        generator = FakeGenerator(
            {}, raises={"查询库存明细": RuntimeError("llm down")}
        )
        result = run_case(make_runner(generator), case)
        assert result.generated_sql is None
        assert result.validation_passed is False
        assert result.error_code == "RuntimeError"
        assert EXPECTATION_VALIDATION in result.failed_expectations

    def test_resolver_failure_recorded(self) -> None:
        """未注册的项目绑定 → 记录为失败，不中断整个数据集。"""
        case = TextToSQLEvaluationCase(
            case_id="c_unknown", question="查询库存明细",
            project_id="not-registered",
            expected=TextToSQLExpectations(must_pass_validation=True),
        )
        result = run_case(make_runner(FakeGenerator({}),
                                      resolver=static_resolver()), case)
        assert result.error_code == "TextToSQLEvaluationRunnerError"
        assert EXPECTATION_VALIDATION in result.failed_expectations

    def test_deterministic_across_runs(self) -> None:
        cases = load_dataset()[:4]
        summary_1 = run_sync(
            make_runner(
                canned_generator(), resolver=static_resolver()
            ).run(cases)
        )
        summary_2 = run_sync(
            make_runner(
                canned_generator(), resolver=static_resolver()
            ).run(cases)
        )
        assert summary_1 == summary_2
        for r1, r2 in zip(summary_1.results, summary_2.results):
            assert r1.duration_ms != r2.duration_ms  # 仅计时不同
            assert dataclasses.replace(r1, duration_ms=0.0) == r2

    def test_summary_render_text(self) -> None:
        cases = (
            case_with(
                must_pass_validation=True, must_contain_tables=("inventory",)
            ),
            TextToSQLEvaluationCase(
                case_id="c2", question="查询幽灵表", project_id="p",
                expected=TextToSQLExpectations(
                    must_contain_tables=("ghost_table",)
                ),
            ),
        )
        generator = FakeGenerator(
            {
                "查询库存明细": (
                    "SELECT material_code FROM public.inventory LIMIT 10"
                ),
                "查询幽灵表": (
                    "SELECT material_code FROM public.inventory LIMIT 10"
                ),
            }
        )
        summary = run_sync(make_runner(generator).run(cases))
        text = summary.render_text()
        assert text.startswith("Text-to-SQL Regression Evaluation")
        assert "Total: 2" in text
        assert "Passed: 1" in text
        assert "Failed: 1" in text
        assert "Validation Passed: 2" in text
        assert "- c2:" in text


# ============================================================
# Canned SQL（全数据集离线 E2E 用；字段全部真实）
# ============================================================

def canned_responses() -> dict[tuple[str, str] | str, str]:
    return {
        "查询知识文档列表": (
            "SELECT id, title FROM knowledge_document LIMIT 50"
        ),
        "查询分词数量最多的10个知识分片": (
            "SELECT id, token_count FROM knowledge_chunk "
            "ORDER BY token_count DESC LIMIT 10"
        ),
        "查询文件类型为指定类型的知识文档": (
            "SELECT id, file_name FROM knowledge_document "
            "WHERE file_type = 'pdf' LIMIT 50"
        ),
        "查询知识分片并按照分词数量从高到低排序": (
            "SELECT id, token_count FROM knowledge_chunk "
            "ORDER BY token_count DESC LIMIT 30"
        ),
        "统计知识文档总数": (
            "SELECT COUNT(*) AS total FROM knowledge_document LIMIT 1"
        ),
        "统计每个知识文档的知识分片数量": (
            "SELECT document_id, COUNT(*) AS chunk_cnt FROM knowledge_chunk "
            "GROUP BY document_id LIMIT 100"
        ),
        "查询知识分片数量超过2个的知识文档": (
            "SELECT document_id, COUNT(*) AS chunk_cnt FROM knowledge_chunk "
            "GROUP BY document_id HAVING COUNT(*) > 2 LIMIT 100"
        ),
        "查询知识分片及其所属知识文档的标题": (
            "SELECT c.content, d.title FROM knowledge_chunk c "
            "JOIN knowledge_document d ON c.document_id = d.id LIMIT 20"
        ),
        "查询2026年1月1日之后创建的知识文档": (
            "SELECT id, title FROM knowledge_document "
            "WHERE created_at >= DATE '2026-01-01' LIMIT 100"
        ),
        "查询前10条知识文档": (
            "SELECT id, title FROM knowledge_document LIMIT 10"
        ),
        "查询每个文档包含多少个分片": (
            "SELECT d.title, COUNT(c.id) AS chunks FROM knowledge_document d "
            "JOIN knowledge_chunk c ON c.document_id = d.id "
            "GROUP BY d.title LIMIT 100"
        ),
        "删除知识文档表中的所有数据": "DELETE FROM knowledge_document",
        ("查询库存明细", "project_a"): (
            "SELECT material_code, qty FROM project_a.inventory LIMIT 50"
        ),
        ("查询库存明细", "project_b"): (
            "SELECT material_code, qty FROM project_b.inventory LIMIT 50"
        ),
    }


def canned_generator(**kwargs: Any) -> FakeGenerator:
    return FakeGenerator(canned_responses(), **kwargs)


def ab_generator(contaminate_target: str | None = None) -> FakeGenerator:
    """A/B 隔离用（可污染 A 侧 SQL 的目标表）。"""
    target = contaminate_target or "project_a"
    return FakeGenerator(
        {
            ("查询库存明细", "project_a"): (
                f"SELECT material_code, qty FROM {target}.inventory LIMIT 50"
            ),
            ("查询库存明细", "project_b"): (
                "SELECT material_code, qty FROM project_b.inventory LIMIT 50"
            ),
        }
    )


# ============================================================
# Project Isolation（§十）
# ============================================================

class TestProjectIsolation:
    def test_project_a_only_uses_a_tables(self) -> None:
        case = dataset_case("project_a_inventory")
        result = run_case(
            make_runner(ab_generator(), resolver=static_resolver()), case
        )
        assert result.passed, result.failed_expectations
        assert result.generated_sql is not None
        assert "project_b" not in result.generated_sql

    def test_project_b_only_uses_b_tables(self) -> None:
        case = dataset_case("project_b_inventory")
        result = run_case(
            make_runner(ab_generator(), resolver=static_resolver()), case
        )
        assert result.passed, result.failed_expectations
        assert "project_a" not in (result.generated_sql or "")

    def test_cross_project_contamination_detected(self) -> None:
        """A 项目 SQL 引用 B 的表 → Validator 拒绝 + 期望失败。"""
        case = dataset_case("project_a_inventory")
        result = run_case(
            make_runner(
                ab_generator(contaminate_target="project_b"),
                resolver=static_resolver(),
            ),
            case,
        )
        assert result.passed is False
        assert result.validation_passed is False
        assert EXPECTATION_VALIDATION in result.failed_expectations
        assert EXPECTATION_FORBIDDEN in result.failed_expectations


# ============================================================
# E2E（非 DB）
# ============================================================

class TestEndToEndOffline:
    async def test_full_dataset_with_fake_generator_and_real_validator(self):
        """Dataset → Real Selector/Composer/Filter → Fake Generator
        → 真实 SQLValidator → 结构化 Result。"""
        cases = load_dataset()
        runner = TextToSQLEvaluationRunner(
            generator=canned_generator(),
            context_resolver=static_resolver(),
            validator=SQLValidatorService(),
        )
        summary = await runner.run(cases)
        assert summary.total == len(cases) == 14
        assert summary.failed == 0, summary.render_text()
        assert summary.validation_passed == 13  # safety case 被 Validator 拒绝
        safety = next(
            r for r in summary.results
            if r.case_id == "safety_delete_all_documents"
        )
        assert safety.passed and safety.error_code

    async def test_full_dataset_text_report_is_stable(self) -> None:
        runner = make_runner(canned_generator(), resolver=static_resolver())
        first = (await runner.run(load_dataset()[:3])).render_text()
        second = (await runner.run(load_dataset()[:3])).render_text()
        assert first == second
        assert first.startswith("Text-to-SQL Regression Evaluation")

    async def test_real_text_to_sql_service_with_fake_llm(self) -> None:
        """Dataset case → 真实 TextToSQLService(FakeLLMClient) → Runner。"""
        cases = (
            dataset_case("simple_document_list"),
            dataset_case("join_chunk_with_parent_document"),
        )
        llm = FakeLLMClient(
            [
                "```sql\nSELECT id, title FROM public.knowledge_document "
                "LIMIT 50\n```",
                "SELECT c.content, d.title FROM knowledge_chunk c "
                "JOIN knowledge_document d ON c.document_id = d.id LIMIT 20",
            ]
        )
        service = TextToSQLService(llm_client=llm, max_attempts=1)
        runner = TextToSQLEvaluationRunner(
            generator=service,
            context_resolver=DirectResolver(schema=knowledge_schema()),
            validator=SQLValidatorService(),
        )
        summary = await runner.run(cases)
        assert summary.failed == 0, summary.render_text()
        assert summary.passed == 2
        assert llm.call_count == 2

    async def test_safety_case_cannot_bypass_validator_with_real_service(self):
        """危险问句：真实服务重试预算耗尽 → 记录异常，期望仍匹配。"""
        case = dataset_case("safety_delete_all_documents")
        llm = FakeLLMClient(["DELETE FROM public.knowledge_document"])
        service = TextToSQLService(llm_client=llm, max_attempts=2)
        runner = TextToSQLEvaluationRunner(
            generator=service,
            context_resolver=DirectResolver(schema=knowledge_schema()),
            validator=SQLValidatorService(),
        )
        result = await runner.run_case(case)
        assert result.passed
        assert result.generated_sql is None  # 绝不返回可执行失败 SQL
        assert result.validation_passed is False
        assert EXPECTATION_VALIDATION in result.matched_expectations


# ============================================================
# DB 辅助（临时 schema，finally 内 DROP CASCADE）
# ============================================================

def create_project_schemas(engine: Any) -> None:
    with engine.begin() as conn:
        for schema in ("project_a", "project_b"):
            conn.execute(sa_text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            conn.execute(sa_text(f'CREATE SCHEMA "{schema}"'))
            conn.execute(
                sa_text(
                    f'CREATE TABLE "{schema}"."inventory" ('
                    f"  material_code VARCHAR(64) PRIMARY KEY,"
                    f"  qty NUMERIC NOT NULL)"
                )
            )
        conn.execute(
            sa_text(
                'INSERT INTO "project_a"."inventory" (material_code, qty) '
                "VALUES ('M-A', 100)"
            )
        )
        conn.execute(
            sa_text(
                'INSERT INTO "project_b"."inventory" (material_code, qty) '
                "VALUES ('M-B', 999)"
            )
        )


def drop_project_schemas(engine: Any) -> None:
    with engine.begin() as conn:
        for schema in ("project_a", "project_b"):
            conn.execute(sa_text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


# ============================================================
# DB（RUN_DB_TESTS=1）
# ============================================================

@requires_db
class TestRealDatabaseEvaluation:
    async def test_dataset_loads_and_validates_against_real_schema(self):
        """真实 Schema：dataset 的表 / 字段必须真实存在（无虚构字段）。"""
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )

        reset_engine_cache()
        engine = get_engine()
        assert engine is not None
        schema = await SchemaExplorerService(engine=engine).inspect(
            schema="public"
        )
        cases = (
            dataset_case("simple_document_list"),
            dataset_case("join_chunk_with_parent_document"),
            dataset_case("date_filter_created_after"),
        )
        runner = TextToSQLEvaluationRunner(
            generator=canned_generator(),
            context_resolver=StaticEvaluationContextResolver(
                bindings={
                    "vietnam-wms": EvaluationProjectBinding(
                        project=project("vietnam-wms", "Vietnam WMS"),
                        schema=schema,
                        semantic=knowledge_semantic(),
                    )
                }
            ),
            validator=SQLValidatorService(),
        )
        summary = await runner.run(cases)
        assert summary.failed == 0, summary.render_text()
        assert summary.validation_passed == 3

    async def test_legal_sql_executes_read_only(self) -> None:
        """合法 SQL 在真实 Executor（READ ONLY 事务）上执行成功。"""
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine
        from backend.app.services.sql_executor_service import SQLExecutorService

        reset_engine_cache()
        engine = get_engine()
        assert engine is not None
        create_project_schemas(engine)
        try:
            case = with_execution_required(
                dataset_case("project_a_inventory")
            )
            runner = TextToSQLEvaluationRunner(
                generator=ab_generator(),
                context_resolver=StaticEvaluationContextResolver(
                    bindings={
                        "eval-project-a": EvaluationProjectBinding(
                            project=project("eval-project-a", "Project A"),
                            schema=inventory_schema("project_a"),
                            semantic=inventory_semantic("project_a"),
                        )
                    }
                ),
                validator=SQLValidatorService(),
                executor=SQLExecutorService(engine=engine),
            )
            result = await runner.run_case(case)
            assert result.passed, result.failed_expectations
            assert result.execution_passed is True
        finally:
            drop_project_schemas(engine)

    async def test_illegal_sql_rejected_by_validator_and_executor(self) -> None:
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine
        from backend.app.services.sql_executor_service import (
            SQLExecutorService,
            SQLExecutorValidationError,
        )

        reset_engine_cache()
        engine = get_engine()
        assert engine is not None
        executor = SQLExecutorService(engine=engine)
        with pytest.raises(SQLExecutorValidationError):
            await executor.execute("DELETE FROM knowledge_document")

        case = dataset_case("safety_delete_all_documents")
        runner = TextToSQLEvaluationRunner(
            generator=canned_generator(),
            context_resolver=DirectResolver(schema=knowledge_schema()),
            validator=SQLValidatorService(),
        )
        result = await runner.run_case(case)
        assert result.passed
        assert result.validation_passed is False

    async def test_project_isolation_with_real_inspected_schemas(self) -> None:
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )

        reset_engine_cache()
        engine = get_engine()
        assert engine is not None
        create_project_schemas(engine)
        try:
            explorer = SchemaExplorerService(engine=engine)
            bindings: dict[str, EvaluationProjectBinding] = {}
            for project_id, schema_name, name in (
                ("eval-project-a", "project_a", "Project A"),
                ("eval-project-b", "project_b", "Project B"),
            ):
                bindings[project_id] = EvaluationProjectBinding(
                    project=project(project_id, name),
                    schema=await explorer.inspect(schema=schema_name),
                    semantic=inventory_semantic(schema_name),
                )
            resolver = StaticEvaluationContextResolver(bindings=bindings)

            cases = (
                dataset_case("project_a_inventory"),
                dataset_case("project_b_inventory"),
            )
            summary = await TextToSQLEvaluationRunner(
                generator=ab_generator(),
                context_resolver=resolver,
                validator=SQLValidatorService(),
            ).run(cases)
            assert summary.failed == 0, summary.render_text()

            # A 的 SQL 引用 B 的表 → 真实 Validator 拒绝
            contaminated = TextToSQLEvaluationRunner(
                generator=ab_generator(contaminate_target="project_b"),
                context_resolver=resolver,
                validator=SQLValidatorService(),
            )
            result = await contaminated.run_case(
                dataset_case("project_a_inventory")
            )
            assert result.passed is False
            assert result.validation_passed is False
        finally:
            drop_project_schemas(engine)
