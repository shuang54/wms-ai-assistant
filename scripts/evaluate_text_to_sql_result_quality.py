"""Evaluate Text-to-SQL Result-level Correctness（Phase 3.9.10）。

在 3.9.9 之上增加**结果级正确性**评估：

```text
Question
  → 既有 Text-to-SQL Pipeline（真实 DeepSeek）
  → SQL
  → 既有 SQLValidator / SQLExecutor
  → rows
  → ResultEvaluationService（deterministic checker，非 LLM judge）
```

用法（仓库根目录执行）：

```powershell
python scripts/evaluate_text_to_sql_result_quality.py            # 真实评估
python scripts/evaluate_text_to_sql_result_quality.py --check    # 纯离线校验
```

## 两种模式严格分离

```text
--check → check_existing_result_quality()   只 load / recompute / compare，不写文件
默认   → run_result_evaluation()            真实 DeepSeek + 真实 Validator / Executor
```

``--check`` 路径**不初始化 LLM client、不调用 get_engine()、不写任何文件**。

纪律：

- 不修改 Prompt / Generator / Validator / Executor / Runner / Dataset 既有字段；
- 不修改 3.9.4 / 3.9.5 / 3.9.6 / 3.9.9 任何产物；
- 只执行只读 SQL（被评估 SQL 全部先过既有 Validator）；
- 不输出、不落盘 API Key / DATABASE_URL；
- 没有 ``result_expectation`` 的 case → N/A，**不**算失败。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.app.services.text_to_sql_baseline_service import (  # noqa: E402
    DEFAULT_REGRESSION_DATASET_PATH,
    read_dataset_version,
)
from backend.app.services.text_to_sql_evaluation_service import (  # noqa: E402
    load_text_to_sql_regression_dataset,
    with_execution_required,
)
from backend.app.services.text_to_sql_quality_evaluation_service import (  # noqa: E402
    evaluate_text_to_sql_quality,
)
from backend.app.services.text_to_sql_result_evaluation_service import (  # noqa: E402
    ResultCheckInput,
    TextToSQLResultEvaluationService,
    load_result_expectations,
)

RESULT_PHASE = "3.9.10"

RESULT_SNAPSHOT_PATH: Path = (
    _REPO_ROOT
    / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_10_result_quality_baseline.json"
)
RESULT_REPORT_PATH: Path = (
    _REPO_ROOT / "docs" / "evaluation" / "text-to-sql-result-quality-3.9.10.md"
)

#: 目标 schema 在真实测试库中存在的 project（可开启执行）
EXECUTABLE_PROJECT_IDS: frozenset[str] = frozenset({"vietnam-wms"})


def _relative(path: Path | str) -> str:
    target = Path(path)
    try:
        return target.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return target.name


class RowCapturingExecutor:
    """包装**真实** SQLExecutorService，并记录最近一次执行的 rows/columns。

    Runner 只保留 ``execution_passed``，会丢弃 rows；本包装器在不修改
    Runner / Executor 的前提下，把真实执行结果留给结果正确性校验。
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.last_columns: tuple[str, ...] = ()
        self.last_rows: tuple[tuple[Any, ...], ...] = ()
        self.executed = False

    async def execute(
        self,
        sql: str,
        *,
        schema: Any = None,
        allowed_tables: Any = None,
        max_rows: int | None = None,
        **kwargs: Any,
    ) -> Any:
        result = await self._inner.execute(
            sql,
            schema=schema,
            allowed_tables=allowed_tables,
            max_rows=max_rows if max_rows is not None else 1000,
        )
        self.last_columns = tuple(result.columns)
        self.last_rows = tuple(result.rows)
        self.executed = True
        return result


# ============================================================
# 生成路径（真实 DeepSeek）
# ============================================================

async def run_result_evaluation() -> int:
    from backend.app.services.sql_executor_service import SQLExecutorService
    from backend.app.services.text_to_sql_evaluation_service import (
        StaticEvaluationContextResolver,
        TextToSQLEvaluationRunner,
    )
    from backend.app.services.text_to_sql_service import TextToSQLService
    from scripts._text_to_sql_offline_bindings import (
        build_offline_project_bindings,
    )

    dataset = load_text_to_sql_regression_dataset()
    expectations = load_result_expectations()

    prepared = tuple(
        with_execution_required(case)
        if case.project_id in EXECUTABLE_PROJECT_IDS
        else case
        for case in dataset
    )

    capturing = RowCapturingExecutor(SQLExecutorService())
    runner = TextToSQLEvaluationRunner(
        generator=TextToSQLService(),
        context_resolver=StaticEvaluationContextResolver(
            bindings=build_offline_project_bindings()
        ),
        executor=capturing,
    )

    service = TextToSQLResultEvaluationService()
    results: list[Any] = []
    checks: list[Any] = []

    for case in prepared:
        capturing.last_columns = ()
        capturing.last_rows = ()
        capturing.executed = False

        result = await runner.run_case(case)
        results.append(result)

        expectation = expectations.get(case.case_id)
        checks.append(
            service.check(
                ResultCheckInput(
                    case_id=case.case_id,
                    columns=capturing.last_columns,
                    rows=capturing.last_rows,
                    expectation=expectation,
                    executed=capturing.executed,
                )
            )
        )

    summary = service.summarize(checks)

    # 复用 3.9.9：把结果正确性作为 result_outcomes 注入，得到 5 层指标
    result_outcomes = {
        item.case_id: item.passed
        for item in checks
        if item.passed is not None
    }
    quality = evaluate_text_to_sql_quality(
        results,
        dataset,
        result_outcomes=result_outcomes,
        phase=RESULT_PHASE,
        dataset_path=_relative(DEFAULT_REGRESSION_DATASET_PATH),
        dataset_version=read_dataset_version(),
    )

    payload = {
        "phase": RESULT_PHASE,
        "dataset_version": read_dataset_version(),
        "total_cases": summary.total_cases,
        "applicable_result_cases": summary.applicable_result_cases,
        "correct_result_cases": summary.correct_result_cases,
        "incorrect_result_cases": summary.incorrect_result_cases,
        "not_evaluable_cases": summary.not_evaluable_cases,
        "result_accuracy": summary.result_accuracy,
        "quality_metrics": quality.metrics.as_dict(),
        "expectation_types": {
            case_id: expectation.type
            for case_id, expectation in expectations.items()
        },
        "cases": [item.to_dict() for item in checks],
    }
    _assert_no_secrets(payload)
    _write_snapshot(payload)
    _write_report(_render_report(payload, quality))

    print(f"Text-to-SQL Result-level Correctness — Phase {RESULT_PHASE}")
    print()
    print(f"Dataset version : {payload['dataset_version']}")
    print(f"Total cases     : {summary.total_cases}")
    print(f"Applicable      : {summary.applicable_result_cases}")
    print(f"Correct         : {summary.correct_result_cases}")
    print(f"Incorrect       : {summary.incorrect_result_cases}")
    print(f"Not evaluable   : {summary.not_evaluable_cases}")
    print(f"Result accuracy : {summary.result_accuracy}")
    print()
    print(f"Snapshot written: {RESULT_SNAPSHOT_PATH.relative_to(_REPO_ROOT)}")
    print(f"Report written:   {RESULT_REPORT_PATH.relative_to(_REPO_ROOT)}")
    return 0


def _write_snapshot(payload: dict[str, Any]) -> None:
    RESULT_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_SNAPSHOT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_report(report: str) -> None:
    RESULT_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_REPORT_PATH.write_text(report, encoding="utf-8")


def _render_report(payload: dict[str, Any], quality: Any) -> str:
    accuracy = payload["result_accuracy"]
    accuracy_text = (
        "N/A" if accuracy is None else f"{accuracy * 100:.2f}%"
    )
    metrics = payload["quality_metrics"]
    lines = [
        "# Text-to-SQL Result-level Correctness — Phase 3.9.10",
        "",
        "## Scope",
        "",
        "> 本阶段评估 SQL **执行结果**是否与 deterministic expected result 一致。",
        "",
        "- 评估方式：deterministic checker（exact_rows / unordered_rows / "
        "scalar / column_values）",
        "- **不使用 LLM-as-a-Judge**，不让模型判断结果正确性",
        "- 复用既有 Text-to-SQL Pipeline / Validator / Executor，不修改生产逻辑",
        "- 只读取测试数据库，被评估 SQL 全部先过既有 Validator（只读）",
        "",
        "## Dataset",
        "",
        f"- File: `{_relative(DEFAULT_REGRESSION_DATASET_PATH)}`",
        f"- Version: `{payload['dataset_version']}`",
        f"- Cases: {payload['total_cases']}",
        f"- Result expectation cases: "
        f"{payload['applicable_result_cases']}",
        "",
        "## Metrics",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Applicable Result Cases | {payload['applicable_result_cases']} |",
        f"| Correct Result Cases | {payload['correct_result_cases']} |",
        f"| Incorrect Result Cases | {payload['incorrect_result_cases']} |",
        f"| Not Evaluable Cases | {payload['not_evaluable_cases']} |",
        f"| **Result Accuracy** | **{accuracy_text}** |",
        "",
        "### 与 3.9.9 其它层级保持独立（§15）",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Generation Success Rate | "
        f"{_pct(metrics.get('generation_success_rate'))} |",
        f"| Validator Acceptance Rate | "
        f"{_pct(metrics.get('validator_acceptance_rate'))} |",
        f"| Expectation Match Rate | "
        f"{_pct(metrics.get('expectation_match_rate'))} |",
        f"| Execution Success Rate | "
        f"{_pct(metrics.get('execution_success_rate'))} |",
        f"| Result Accuracy | {accuracy_text} |",
        "",
        "> **Result Accuracy ≠ Expectation Match Rate**：SQL 结构符合预期，",
        "> 不代表查询结果正确。",
        "",
        "## Per-case Results",
        "",
        "| case_id | type | applicable | passed | reason |",
        "|---|---|---|---|---|",
    ]
    expectation_types: dict[str, str] = payload.get("expectation_types", {})
    for item in payload["cases"]:
        passed = "-" if item["passed"] is None else str(item["passed"])
        lines.append(
            f"| `{item['case_id']}` | "
            f"{expectation_types.get(item['case_id'], '-')} | "
            f"{item['applicable']} | {passed} | {item['reason']} |"
        )
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- **不是所有 14 个 case 都具备 deterministic expected result**；",
            f"  仅 {payload['applicable_result_cases']}/{payload['total_cases']} "
            "个 case 定义了 result_expectation。",
            "- **N/A 不代表失败**：没有 result_expectation 的 case 记 "
            "`applicable=false / passed=null`，不进入准确率分母。",
            "- 当前只验证**测试数据库**中的稳定结果（测试库业务表为空，"
            "期望值据此确定）。",
            "- 不代表生产数据库上的全面正确性，也不代表所有 SQL 语义都正确。",
            "- 空表场景下「0 行 / COUNT=0」类期望的区分度有限："
            "能证明执行与结果形状正确，不能区分不同但同样返回空结果的 SQL。",
            "- DeepSeek 输出不稳定，重复运行指标可能变化。",
            "",
        ]
    )
    return "\n".join(lines)


def _pct(rate: float | None) -> str:
    return "N/A" if rate is None else f"{rate * 100:.2f}%"


# ============================================================
# --check：纯 offline（NO LLM / NO DB / NO NETWORK / NO WRITE）
# ============================================================

def check_existing_result_quality() -> int:
    problems: list[str] = []

    if not RESULT_SNAPSHOT_PATH.exists():
        print(f"FAIL: result snapshot not found: "
              f"{RESULT_SNAPSHOT_PATH.name}")
        return 1
    stored = json.loads(RESULT_SNAPSHOT_PATH.read_text(encoding="utf-8"))

    # 1) phase / dataset 绑定
    if stored.get("phase") != RESULT_PHASE:
        problems.append(
            f"phase: expected {RESULT_PHASE!r}, got {stored.get('phase')!r}"
        )
    version = read_dataset_version()
    if stored.get("dataset_version") != version:
        problems.append(
            f"dataset_version: expected {version!r}, "
            f"got {stored.get('dataset_version')!r}"
        )

    dataset = load_text_to_sql_regression_dataset()
    if stored.get("total_cases") != len(dataset):
        problems.append(
            f"total_cases: expected {len(dataset)}, "
            f"got {stored.get('total_cases')!r}"
        )

    # 2) case_id 与 Dataset 完全一致
    stored_ids = [c.get("case_id") for c in stored.get("cases", [])]
    dataset_ids = [c.case_id for c in dataset]
    if stored_ids != dataset_ids:
        problems.append(
            f"case_id mismatch: stored={stored_ids} dataset={dataset_ids}"
        )

    # 3) 由 Snapshot 内 per-case 结果重算指标，必须与存量一致
    checks = stored.get("cases", [])
    correct = sum(1 for c in checks if c.get("passed") is True)
    incorrect = sum(1 for c in checks if c.get("passed") is False)
    applicable = sum(1 for c in checks if c.get("applicable"))
    not_evaluable = sum(
        1 for c in checks if c.get("applicable") and c.get("passed") is None
    )
    denominator = correct + incorrect
    recomputed_accuracy = (
        round(correct / denominator, 4) if denominator else None
    )
    for key, value in (
        ("applicable_result_cases", applicable),
        ("correct_result_cases", correct),
        ("incorrect_result_cases", incorrect),
        ("not_evaluable_cases", not_evaluable),
        ("result_accuracy", recomputed_accuracy),
    ):
        if stored.get(key) != value:
            problems.append(
                f"{key}: stored={stored.get(key)!r} recomputed={value!r}"
            )

    # 4) result_expectation 覆盖数与 Dataset 一致
    expectations = load_result_expectations()
    if applicable != len(expectations):
        problems.append(
            f"applicable_result_cases={applicable} does not match "
            f"dataset result_expectation count={len(expectations)}"
        )

    # 5) expectation_types 必须与 Dataset 一致
    expectations = load_result_expectations()
    recomputed_types = {
        case_id: expectation.type
        for case_id, expectation in expectations.items()
    }
    if stored.get("expectation_types") != recomputed_types:
        problems.append(
            f"expectation_types: stored={stored.get('expectation_types')!r} "
            f"recomputed={recomputed_types!r}"
        )

    # 6) Report 存在且关键数字一致
    if not RESULT_REPORT_PATH.exists():
        problems.append(f"report missing: {RESULT_REPORT_PATH.name}")
    else:
        text = RESULT_REPORT_PATH.read_text(encoding="utf-8")
        accuracy_text = (
            "N/A" if recomputed_accuracy is None
            else f"{recomputed_accuracy * 100:.2f}%"
        )
        for needle in (
            f"- Version: `{version}`",
            f"- Cases: {len(dataset)}",
            f"| Correct Result Cases | {correct} |",
            f"| Incorrect Result Cases | {incorrect} |",
            f"| **Result Accuracy** | **{accuracy_text}** |",
            "## Limitations",
        ):
            if needle not in text:
                problems.append(f"report missing: {needle}")

    # 6) 敏感信息自检
    blob = json.dumps(stored, ensure_ascii=False).lower()
    for fragment in ("sk-", "postgres://", "postgresql://", "bearer "):
        if fragment in blob:
            problems.append(
                f"snapshot contains forbidden fragment: {fragment!r}"
            )

    if problems:
        print("FAIL: 3.9.10 result quality consistency check failed")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"OK: 3.9.10 result snapshot is consistent "
          f"({len(dataset)} cases, phase {RESULT_PHASE})")
    print(f"    applicable={applicable} correct={correct} "
          f"incorrect={incorrect} "
          f"result_accuracy={recomputed_accuracy}")
    return 0


def _assert_no_secrets(payload: Any) -> None:
    forbidden = {
        "api_key", "llm_api_key", "password", "database_url", "dsn",
        "connection_string", "secret", "token", "authorization",
    }
    found: set[str] = set()
    stack: list[Any] = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            found |= {
                str(k).lower() for k in node
                if str(k).lower() in forbidden
            }
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    if found:
        raise ValueError(
            f"result snapshot contains forbidden keys: {sorted(found)}"
        )
    blob = json.dumps(payload, ensure_ascii=False).lower()
    for fragment in ("sk-", "postgres://", "postgresql://", "bearer "):
        if fragment in blob:
            raise ValueError(
                f"result snapshot contains forbidden fragment: {fragment!r}"
            )


# ============================================================
# Entry point（两条路径完全分离）
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate Text-to-SQL result-level correctness."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="offline consistency check only (no LLM, no DB, no write)",
    )
    args = parser.parse_args()
    if args.check:
        return check_existing_result_quality()
    return asyncio.run(run_result_evaluation())


if __name__ == "__main__":
    raise SystemExit(main())
