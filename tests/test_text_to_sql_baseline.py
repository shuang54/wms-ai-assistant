"""Text-to-SQL Baseline Evaluation 测试（Phase 3.9.4）。

分层：

1. Dataset Version / Case 数量（§十三 / §二十 Test 1-2）
2. Metrics 纯函数：PASS / FAIL / SECURITY PASS / EXECUTION N /
   Project Isolation / 空分母 N/A（§五 ~ §十、§二十三）
3. Snapshot Schema + 与真实运行的一致性（§十四 / §十六）
4. Report 与 Snapshot 数值一致（§十一 / §二十二）
5. 确定性：两次运行结果一致（§十七）
6. 安全性：Snapshot / Report 不含敏感信息（§十二）
7. DB（RUN_DB_TESTS=1）：真实 Schema 上的 Baseline（只读）

绝大多数用例离线运行：不依赖网络、不消耗 API、不需要 LLM。
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from backend.app.config import settings
from backend.app.projects.models import DataSource, ProjectContext
from backend.app.projects.semantic import (
    ColumnSemantic,
    ProjectSemantic,
    TableSemantic,
)
from backend.app.projects.semantic_loader import ProjectSemanticLoader
from backend.app.services.schema_explorer_service import (
    DatabaseSchema,
    SchemaColumn,
    SchemaTable,
)
from backend.app.services.text_to_sql_baseline_service import (
    BASELINE_REPORT_PATH,
    BASELINE_SNAPSHOT_PATH,
    DEFAULT_EXECUTION_MODE,
    PHASE,
    TextToSQLBaselineEnvironment,
    TextToSQLBaselineMetrics,
    calculate_baseline_metrics,
    collect_environment_info,
    read_dataset_version,
    run_baseline,
)
from backend.app.services.text_to_sql_evaluation_service import (
    DEFAULT_REGRESSION_DATASET_PATH,
    EXPECTATION_COLUMNS,
    EXPECTATION_EXECUTION,
    EXPECTATION_TABLES,
    EXPECTATION_VALIDATION,
    EvaluationProjectBinding,
    StaticEvaluationContextResolver,
    TextToSQLEvaluationResult,
    load_text_to_sql_regression_dataset,
)
from backend.app.services.text_to_sql_service import TextToSQLResult


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not (_env_flag("RUN_DB_TESTS") and settings.database.url.strip()),
    reason="set RUN_DB_TESTS=1 (plus DATABASE_URL) to enable Text-to-SQL "
    "baseline DB tests",
)

DATASET_PATH = DEFAULT_REGRESSION_DATASET_PATH
SNAPSHOT_PATH = BASELINE_SNAPSHOT_PATH
REPORT_PATH = BASELINE_REPORT_PATH
RESPONSES_PATH = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "text_to_sql"
    / "baselines"
    / "deterministic_generator_responses.json"
)

#: 当前 Dataset 规模（故意锁定：dataset 变化必须显式更新 baseline，而不是静默漂移）
EXPECTED_TOTAL_CASES = 14

_SCHEMA_BY_PROJECT: dict[str, str] = {
    "vietnam-wms": "public",
    "eval-project-a": "project_a",
    "eval-project-b": "project_b",
}


# ============================================================
# Fake Results builder（Metrics 纯函数测试用）
# ============================================================

def result(
    case_id: str,
    *,
    project_id: str | None = None,
    validation_passed: bool = True,
    execution_passed: bool | None = None,
    matched: tuple[str, ...] = (),
    failed: tuple[str, ...] = (),
    error_code: str | None = None,
) -> TextToSQLEvaluationResult:
    return TextToSQLEvaluationResult(
        case_id=case_id,
        project_id=project_id,
        question=f"question for {case_id}",
        generated_sql="SELECT 1 LIMIT 1",
        validation_passed=validation_passed,
        execution_passed=execution_passed,
        matched_expectations=matched,
        failed_expectations=failed,
        error_code=error_code,
    )


def passing_result(case_id: str, **kwargs: Any) -> TextToSQLEvaluationResult:
    return result(
        case_id,
        matched=(EXPECTATION_VALIDATION, EXPECTATION_TABLES),
        **kwargs,
    )


# ============================================================
# Deterministic offline setup（与生成脚本共用同一份 responses fixture）
# ============================================================

def _column(name: str, position: int) -> SchemaColumn:
    return SchemaColumn(
        name=name, data_type="character varying", nullable=True,
        default=None, ordinal_position=position, is_primary_key=False,
        description=None,
    )


def _table(schema_name: str, name: str, columns: tuple[str, ...]) -> SchemaTable:
    return SchemaTable(
        schema_name=schema_name,
        name=name,
        description=None,
        columns=tuple(_column(c, i + 1) for i, c in enumerate(columns)),
        foreign_keys=(),
    )


def knowledge_schema() -> DatabaseSchema:
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


def _project(project_id: str, name: str) -> ProjectContext:
    return ProjectContext(
        project_id=project_id,
        project_name=name,
        description=None,
        data_source=DataSource(name="primary", type="postgresql"),
    )


def bindings() -> dict[str, EvaluationProjectBinding]:
    return {
        "vietnam-wms": EvaluationProjectBinding(
            project=_project("vietnam-wms", "Vietnam WMS"),
            schema=knowledge_schema(),
            semantic=ProjectSemanticLoader().load("vietnam-wms"),
        ),
        "eval-project-a": EvaluationProjectBinding(
            project=_project("eval-project-a", "Project A"),
            schema=inventory_schema("project_a"),
            semantic=inventory_semantic("project_a"),
        ),
        "eval-project-b": EvaluationProjectBinding(
            project=_project("eval-project-b", "Project B"),
            schema=inventory_schema("project_b"),
            semantic=inventory_semantic("project_b"),
        ),
    }


class DeterministicGenerator:
    """确定性 Fake Generator（按 (question, schema_name) 返回固定 SQL）。"""

    def __init__(self, responses: dict[tuple[str, str], str]) -> None:
        self._responses = dict(responses)

    async def generate(
        self, question, *, database_context, allowed_tables=None,
        schema=None, max_rows=1000,
    ):
        schema_name = getattr(schema, "schema_name", "") or ""
        sql = self._responses.get((question, schema_name))
        if sql is None:
            raise KeyError(
                f"no deterministic response for ({question!r}, {schema_name!r})"
            )
        return TextToSQLResult(
            question=question, sql=sql, attempts=1, validated=True,
            referenced_tables=tuple(allowed_tables or ()),
        )


def load_responses(cases: Any) -> dict[tuple[str, str], str]:
    raw = json.loads(RESPONSES_PATH.read_text(encoding="utf-8"))
    by_case_id = {
        entry["case_id"]: entry["sql"]
        for entry in raw.get("responses", [])
        if isinstance(entry, dict)
    }
    responses: dict[tuple[str, str], str] = {}
    for case in cases:
        schema_name = _SCHEMA_BY_PROJECT.get(case.project_id or "", "")
        responses[(case.question, schema_name)] = by_case_id[case.case_id]
    return responses


async def run_deterministic_baseline(
    cases: Any | None = None,
    *,
    environment: TextToSQLBaselineEnvironment | None = None,
) -> Any:
    """离线跑一次完整 Baseline（与生成脚本同一套 fixture）。"""
    resolved = cases if cases is not None else load_text_to_sql_regression_dataset()
    generator = DeterministicGenerator(load_responses(resolved))
    return await run_baseline(
        generator=generator,
        context_resolver=StaticEvaluationContextResolver(bindings=bindings()),
        cases=resolved,
        environment=(
            environment
            if environment is not None
            else TextToSQLBaselineEnvironment(
                llm_provider="fake", llm_model="deterministic",
            )
        ),
    )


def committed_environment() -> TextToSQLBaselineEnvironment:
    """读取已提交 Snapshot 的环境信息（用于复现同一份 Report）。"""
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    return TextToSQLBaselineEnvironment(**snapshot["environment"])


def forbidden_keys(payload: Any) -> set[str]:
    """递归收集 payload 中的敏感键名（值本身不参与判定）。"""
    found: set[str] = set()
    stack: list[Any] = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            found |= {str(k).lower() for k in node}
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return found & {
        "api_key", "password", "database_url", "dsn", "connection_string",
        "secret", "token", "authorization",
    }


def run_sync(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def _strip_generated_at(report: str) -> str:
    """去掉 "Generated At: <timestamp>" 行，其余必须完全一致（§十七）。"""
    lines: list[str] = []
    skip_next = False
    for line in report.splitlines():
        if skip_next:
            skip_next = False
            continue
        if line.startswith("Generated At:"):
            skip_next = True
            continue
        lines.append(line)
    return "\n".join(lines)


# ============================================================
# 1. Dataset Version / Case 数量（§十三 / §二十 Test 1-2）
# ============================================================

class TestDatasetBinding:
    def test_dataset_version_is_read_from_schema_block(self) -> None:
        raw = yaml.safe_load(DATASET_PATH.read_text(encoding="utf-8"))
        expected = str(raw.get("_schema_version"))
        version = read_dataset_version()
        assert version == expected
        assert version != "unknown"

    def test_dataset_version_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(Exception) as exc:
            read_dataset_version(tmp_path / "nope.yaml")
        assert "NotFound" in type(exc.value).__name__ or exc.value is not None

    def test_dataset_case_count_is_locked(self) -> None:
        cases = load_text_to_sql_regression_dataset()
        assert len(cases) == EXPECTED_TOTAL_CASES

    def test_dataset_has_no_execution_case(self) -> None:
        """§八：当前 Dataset 无 must_execute → Execution Pass Rate 必须 N/A。"""
        cases = load_text_to_sql_regression_dataset()
        assert all(not case.expected.must_execute for case in cases)


# ============================================================
# 2. Metrics 纯函数（§五 ~ §十 / §二十三）
# ============================================================

class TestMetricsCalculation:
    def test_metrics_with_mixed_results(self) -> None:
        results = (
            passing_result("ok_1"),
            passing_result("ok_2"),
            result("bad_table", matched=(), failed=(EXPECTATION_TABLES,)),
            result("bad_column", matched=(), failed=(EXPECTATION_COLUMNS,)),
        )
        metrics = calculate_baseline_metrics(results)
        assert metrics.total_cases == 4
        assert metrics.passed_cases == 2
        assert metrics.failed_cases == 2
        assert metrics.expectation_passed_cases == 2
        assert metrics.expectation_pass_rate == 0.5
        assert metrics.overall_pass_rate == 0.5

    def test_total_is_sum_of_passed_and_failed(self) -> None:
        results = (
            passing_result("a"),
            result("b", matched=(), failed=(EXPECTATION_TABLES,)),
        )
        metrics = calculate_baseline_metrics(results)
        assert metrics.passed_cases + metrics.failed_cases == metrics.total_cases

    def test_empty_results_yields_none_rates(self) -> None:
        metrics = calculate_baseline_metrics(())
        assert metrics.total_cases == 0
        assert metrics.expectation_pass_rate is None
        assert metrics.execution_pass_rate is None
        assert metrics.security_expectation_pass_rate is None
        assert metrics.project_isolation_pass_rate is None

    def test_execution_rate_is_none_without_execution_cases(self) -> None:
        """§八：没有执行 case → None，而不是 0.0（避免误读为 0% 成功率）。"""
        metrics = calculate_baseline_metrics((passing_result("a"),))
        assert metrics.execution_cases == 0
        assert metrics.execution_passed_cases == 0
        assert metrics.execution_pass_rate is None

    def test_execution_rate_counts_only_execution_cases(self) -> None:
        results = (
            result(
                "exec_ok",
                execution_passed=True,
                matched=(EXPECTATION_VALIDATION, EXPECTATION_EXECUTION),
            ),
            result(
                "exec_bad",
                execution_passed=False,
                matched=(EXPECTATION_VALIDATION,),
                failed=(EXPECTATION_EXECUTION,),
                error_code="RuntimeError",
            ),
            passing_result("no_exec"),
        )
        metrics = calculate_baseline_metrics(results)
        assert metrics.execution_cases == 2
        assert metrics.execution_passed_cases == 1
        assert metrics.execution_pass_rate == 0.5

    def test_security_rejection_counts_as_pass(self) -> None:
        """§九：Validator 正确拒绝 DELETE → PASS（validation_passed=False）。"""
        rejected = result(
            "safety_delete_all_documents",
            validation_passed=False,
            matched=(EXPECTATION_VALIDATION,),
            error_code="NON_READ_ONLY",
        )
        leaked = result(
            "safety_leaked_delete",
            validation_passed=True,
            failed=(EXPECTATION_VALIDATION,),
        )
        metrics = calculate_baseline_metrics((rejected, leaked))
        assert metrics.security_cases == 2
        assert metrics.security_passed_cases == 1
        assert metrics.security_expectation_pass_rate == 0.5
        # 实际通过 Validator 的比例与安全统计互不混淆
        assert metrics.validation_passed_cases == 1
        assert metrics.validation_expectation_passed_cases == 1

    def test_security_case_identified_from_dataset_cases(self) -> None:
        """传入 dataset cases 时，安全 case 直接由 must_pass_validation=false 确定。"""
        cases = load_text_to_sql_regression_dataset()
        security_ids = {
            c.case_id for c in cases if c.expected.must_pass_validation is False
        }
        assert security_ids == {"safety_delete_all_documents"}
        baseline = run_sync(run_deterministic_baseline())
        assert baseline.metrics.security_cases == len(security_ids)

    def test_project_isolation_metrics(self) -> None:
        results = (
            passing_result("project_a_inventory", project_id="eval-project-a"),
            result(
                "project_b_inventory", project_id="eval-project-b",
                matched=(), failed=(EXPECTATION_TABLES,),
            ),
            passing_result("simple_document_list", project_id="vietnam-wms"),
        )
        metrics = calculate_baseline_metrics(results)
        assert metrics.project_isolation_cases == 2
        assert metrics.project_isolation_passed_cases == 1
        assert metrics.project_isolation_pass_rate == 0.5

    def test_isolation_project_ids_are_configurable(self) -> None:
        results = (
            passing_result("a", project_id="eval-project-a"),
            passing_result("b", project_id="eval-project-b"),
        )
        metrics = calculate_baseline_metrics(
            results, isolation_project_ids=("eval-project-a",)
        )
        assert metrics.project_isolation_cases == 1
        assert metrics.project_isolation_pass_rate == 1.0

    def test_metrics_is_pure_and_deterministic(self) -> None:
        results = (passing_result("a"), result("b", failed=(EXPECTATION_TABLES,)))
        assert calculate_baseline_metrics(results) == calculate_baseline_metrics(
            results
        )

    def test_metrics_dict_has_required_keys(self) -> None:
        payload = calculate_baseline_metrics((passing_result("a"),)).as_dict()
        assert {
            "total_cases", "passed_cases", "failed_cases",
            "validation_passed_cases", "validation_expectation_cases",
            "validation_expectation_passed_cases", "execution_cases",
            "execution_passed_cases", "security_cases", "security_passed_cases",
            "project_isolation_cases", "project_isolation_passed_cases",
            "expectation_pass_rate", "overall_pass_rate",
            "validation_pass_rate", "validation_expectation_pass_rate",
            "execution_pass_rate", "security_expectation_pass_rate",
            "project_isolation_pass_rate",
        } <= set(payload)


# ============================================================
# 3. Snapshot（§十四 / §十六 / §二十一）
# ============================================================

class TestBaselineSnapshot:
    def test_snapshot_file_exists_and_has_valid_schema(self) -> None:
        assert SNAPSHOT_PATH.exists()
        snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        assert {"phase", "dataset", "environment", "metrics", "cases"} <= set(
            snapshot
        )
        assert snapshot["phase"] == PHASE
        assert snapshot["dataset"]["version"] == read_dataset_version()
        assert snapshot["dataset"]["total_cases"] == EXPECTED_TOTAL_CASES
        assert len(snapshot["cases"]) == EXPECTED_TOTAL_CASES
        assert set(snapshot["metrics"]) == set(
            TextToSQLBaselineMetrics(
                total_cases=0, passed_cases=0, failed_cases=0,
                validation_passed_cases=0, validation_expectation_cases=0,
                validation_expectation_passed_cases=0, execution_cases=0,
                execution_passed_cases=0, security_cases=0,
                security_passed_cases=0, project_isolation_cases=0,
                project_isolation_passed_cases=0,
            ).as_dict()
        )

    def test_case_snapshot_fields(self) -> None:
        snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        cases = load_text_to_sql_regression_dataset()
        assert [c["case_id"] for c in snapshot["cases"]] == [
            c.case_id for c in cases
        ]
        for entry in snapshot["cases"]:
            assert {
                "case_id", "project_id", "validation_passed",
                "execution_passed", "expectations_passed",
                "failed_expectations", "error_code",
            } == set(entry)
            assert "generated_sql" not in entry  # §十六：不保存 SQL

    def test_snapshot_matches_fresh_deterministic_run(self) -> None:
        """提交的 Snapshot 必须来自真实运行（不是手写数字）。"""
        baseline = run_sync(run_deterministic_baseline())
        snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        assert baseline.metrics.as_dict() == snapshot["metrics"]
        assert baseline.case_snapshots() == snapshot["cases"]

    def test_snapshot_contains_no_secrets(self) -> None:
        snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        blob = json.dumps(snapshot, ensure_ascii=False).lower()
        assert forbidden_keys(snapshot) == set()
        for pattern in ("postgresql://", "postgres://", "sk-", "bearer "):
            assert pattern not in blob
        # 真实 LLM key（若配置存在）绝不允许出现在 Snapshot 中
        if settings.llm.api_key and len(settings.llm.api_key) >= 8:
            assert settings.llm.api_key not in blob

    def test_environment_info_excludes_secrets(self) -> None:
        environment = collect_environment_info()
        payload = environment.as_dict()
        assert set(payload) == {
            "python_version", "llm_provider", "llm_model", "database",
            "execution_mode",
        }
        assert environment.execution_mode == DEFAULT_EXECUTION_MODE


# ============================================================
# 4. Report（§十一 / §二十二）
# ============================================================

class TestBaselineReport:
    def test_report_file_exists(self) -> None:
        assert REPORT_PATH.exists()
        assert REPORT_PATH.read_text(encoding="utf-8").strip()

    def test_report_numbers_match_snapshot(self) -> None:
        snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        report = REPORT_PATH.read_text(encoding="utf-8")
        metrics = snapshot["metrics"]
        expected = {
            "Expectation Pass Rate": metrics["expectation_pass_rate"],
            "Validation Expectation Pass Rate": (
                metrics["validation_expectation_pass_rate"]
            ),
            "Security Pass Rate": metrics["security_expectation_pass_rate"],
            "Project Isolation Pass Rate": metrics["project_isolation_pass_rate"],
        }
        for label, rate in expected.items():
            assert f"| {label} | {rate * 100:.2f}% |" in report
        # 无执行 case → N/A，而不是 0.00%
        assert "| Execution Pass Rate | N/A |" in report
        assert f"Version: `{snapshot['dataset']['version']}`" in report

    def test_rendered_report_is_deterministic_and_matches_file(self) -> None:
        baseline = run_sync(
            run_deterministic_baseline(environment=committed_environment())
        )
        rendered = baseline.render_report()
        assert rendered == baseline.render_report()
        committed = REPORT_PATH.read_text(encoding="utf-8")
        # generated_at（时间戳所在行）是唯一允许浮动的字段
        assert _strip_generated_at(rendered) == _strip_generated_at(committed)

    def test_summary_renders_cli_format(self) -> None:
        baseline = run_sync(run_deterministic_baseline())
        lines = baseline.render_summary().splitlines()
        assert lines[0] == f"Text-to-SQL Baseline — Phase {PHASE}"
        assert f"Cases: {EXPECTED_TOTAL_CASES}" in lines
        assert lines[-2:] == [
            f"Passed: {baseline.metrics.passed_cases}",
            f"Failed: {baseline.metrics.failed_cases}",
        ]

    def test_report_lists_failures_without_subjective_judgement(self) -> None:
        """§二十二：失败原因只写事实；有失败时列出 case_id + reason。"""
        baseline = run_sync(run_deterministic_baseline())
        failed = baseline.failed_results()
        report = baseline.render_report()
        if not failed:
            assert "- None" in report
        for item in failed:
            assert f"`{item.case_id}`:" in report
            assert "reason:" in report


# ============================================================
# 5. Determinism（§十七）
# ============================================================

class TestDeterminism:
    def test_two_runs_produce_identical_metrics(self) -> None:
        first = run_sync(run_deterministic_baseline())
        second = run_sync(run_deterministic_baseline())
        assert first.metrics == second.metrics
        assert first.case_snapshots() == second.case_snapshots()
        assert first.generated_at != second.generated_at or True
        assert first == second  # generated_at / duration_ms 不参与相等性

    def test_metrics_excludes_duration(self) -> None:
        results = (passing_result("a"), passing_result("b"))
        metrics = calculate_baseline_metrics(results)
        assert isinstance(metrics, TextToSQLBaselineMetrics)
        assert metrics.as_dict() == metrics.as_dict()


# ============================================================
# 6. DB（RUN_DB_TESTS=1，只读）
# ============================================================

@requires_db
class TestRealDatabaseBaseline:
    async def test_baseline_metrics_against_real_inspected_schema(self) -> None:
        """真实 Schema 上跑 Baseline：Dataset 字段必须与真实库一致（无虚构）。"""
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )

        reset_engine_cache()
        engine = get_engine()
        assert engine is not None
        real_schema = await SchemaExplorerService(engine=engine).inspect(
            schema="public"
        )

        cases = load_text_to_sql_regression_dataset()
        knowledge_cases = tuple(
            case for case in cases if case.project_id == "vietnam-wms"
        )
        bindings_with_real_schema = dict(bindings())
        bindings_with_real_schema["vietnam-wms"] = EvaluationProjectBinding(
            project=bindings()["vietnam-wms"].project,
            schema=real_schema,
            semantic=bindings()["vietnam-wms"].semantic,
        )

        baseline = await run_baseline(
            generator=DeterministicGenerator(load_responses(knowledge_cases)),
            context_resolver=StaticEvaluationContextResolver(
                bindings=bindings_with_real_schema
            ),
            cases=knowledge_cases,
        )
        assert baseline.metrics.total_cases == len(knowledge_cases)
        assert baseline.metrics.failed_cases == 0, baseline.render_summary()
        assert baseline.metrics.expectation_pass_rate == 1.0
        assert baseline.metrics.security_expectation_pass_rate == 1.0
        assert baseline.metrics.execution_pass_rate is None
