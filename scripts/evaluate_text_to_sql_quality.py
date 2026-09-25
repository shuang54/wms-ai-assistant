"""Evaluate Text-to-SQL Generation Quality（Phase 3.9.9）。

在 3.9.3~3.9.8 之上建立**生成质量基线**：区分
「生成成功 / Validator 接受 / 期望匹配 / 执行成功 / 结果正确」五个层次。

用法（仓库根目录执行）：

```powershell
python scripts/evaluate_text_to_sql_quality.py            # 真实 DeepSeek 评估
python scripts/evaluate_text_to_sql_quality.py --check    # 纯离线一致性校验
```

## 两种模式严格分离（沿用 3.9.5 收尾修复的教训）

```text
--check → check_existing_quality()   只 load / recompute / compare，不写文件
默认   → run_quality_evaluation()    真实 DeepSeek + 真实 Validator / Executor
```

``--check`` 路径**不初始化 LLM client、不调用 get_engine()、不写任何文件**。

纪律：

- 不修改 Prompt / Generator / Validator / Executor / Dataset / Runner；
- 不修改 3.9.4 / 3.9.5 / 3.9.6 / 3.9.7 / 3.9.8 任何产物；
- 不输出、不落盘 API Key / DATABASE_URL；
- 只对**目标 schema 真实存在**的 case 开启执行，其余记为 N/A；
- Dataset 无 expected result → result correctness 一律 N/A（不臆造）。
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
    TextToSQLCaseQuality,
    calculate_quality_metrics,
    evaluate_text_to_sql_quality,
)

QUALITY_PHASE = "3.9.9"

QUALITY_SNAPSHOT_PATH: Path = (
    _REPO_ROOT
    / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_9_quality_baseline.json"
)
QUALITY_REPORT_PATH: Path = (
    _REPO_ROOT / "docs" / "evaluation" / "text-to-sql-quality-baseline-3.9.9.md"
)

#: 目标 schema 在真实测试库中存在的 project（可开启执行）
#: eval-project-a/b 使用离线 fixture schema（库中不存在），不执行
EXECUTABLE_PROJECT_IDS: frozenset[str] = frozenset({"vietnam-wms"})


def _relative(path: Path | str) -> str:
    target = Path(path)
    try:
        return target.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return target.name


def _dataset_path() -> str:
    return _relative(DEFAULT_REGRESSION_DATASET_PATH)


# ============================================================
# 生成路径（真实 DeepSeek）
# ============================================================

async def run_quality_evaluation() -> int:
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
    # 仅对目标 schema 真实存在的 case 开启执行；其余保持 N/A
    prepared = tuple(
        with_execution_required(case)
        if case.project_id in EXECUTABLE_PROJECT_IDS
        else case
        for case in dataset
    )

    runner = TextToSQLEvaluationRunner(
        generator=TextToSQLService(),
        context_resolver=StaticEvaluationContextResolver(
            bindings=build_offline_project_bindings()
        ),
        executor=SQLExecutorService(),
    )
    summary = await runner.run(prepared)

    quality = evaluate_text_to_sql_quality(
        summary.results,
        dataset,
        phase=QUALITY_PHASE,
        dataset_path=_dataset_path(),
        dataset_version=read_dataset_version(),
    )

    payload = quality.to_dict()
    _assert_no_secrets(payload)
    _write_snapshot(payload)
    _write_report(quality.render_report())

    metrics = quality.metrics
    print(f"Text-to-SQL Generation Quality — Phase {QUALITY_PHASE}")
    print()
    print(f"Dataset: {payload['dataset']['path']} "
          f"(version {payload['dataset']['version']})")
    print(f"Total Cases: {metrics.total_cases}")
    print()
    print(f"Generation Success : {metrics.generation_success} "
          f"(rate={metrics.generation_success_rate})")
    print(f"Validator Accepted : {metrics.validator_accepted} "
          f"(rate={metrics.validator_acceptance_rate})")
    print(f"Expectation Matched: {metrics.expectation_matched} "
          f"(rate={metrics.expectation_match_rate})")
    print(f"Execution Success  : {metrics.execution_success} "
          f"(rate={metrics.execution_success_rate}, "
          f"not attempted={metrics.execution_not_attempted})")
    print(f"Result Correct     : {metrics.result_correct} "
          f"(accuracy={metrics.result_accuracy}, "
          f"not evaluated={metrics.result_not_evaluated})")
    print()
    print(f"Snapshot written: {QUALITY_SNAPSHOT_PATH.relative_to(_REPO_ROOT)}")
    print(f"Report written:   {QUALITY_REPORT_PATH.relative_to(_REPO_ROOT)}")
    return 0


def _write_snapshot(payload: dict[str, Any]) -> None:
    QUALITY_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUALITY_SNAPSHOT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_report(report: str) -> None:
    QUALITY_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUALITY_REPORT_PATH.write_text(report, encoding="utf-8")


# ============================================================
# --check：纯 offline（NO LLM / NO DB / NO NETWORK / NO WRITE）
# ============================================================

def check_existing_quality() -> int:
    problems: list[str] = []

    if not QUALITY_SNAPSHOT_PATH.exists():
        print(f"FAIL: quality snapshot not found: "
              f"{QUALITY_SNAPSHOT_PATH.name}")
        return 1
    stored = json.loads(QUALITY_SNAPSHOT_PATH.read_text(encoding="utf-8"))

    # 1) phase / dataset 绑定
    if stored.get("phase") != QUALITY_PHASE:
        problems.append(
            f"phase: expected {QUALITY_PHASE!r}, got {stored.get('phase')!r}"
        )
    dataset_block = stored.get("dataset")
    if not isinstance(dataset_block, dict):
        problems.append("dataset block missing or malformed")
        dataset_block = {}
    if dataset_block.get("version") != read_dataset_version():
        problems.append(
            f"dataset.version: expected {read_dataset_version()!r}, "
            f"got {dataset_block.get('version')!r}"
        )

    dataset = load_text_to_sql_regression_dataset()
    if dataset_block.get("total_cases") != len(dataset):
        problems.append(
            f"dataset.total_cases: expected {len(dataset)}, "
            f"got {dataset_block.get('total_cases')!r}"
        )

    # 2) case_id 与 Dataset 完全一致
    stored_ids = [c.get("case_id") for c in stored.get("cases", [])]
    dataset_ids = [c.case_id for c in dataset]
    if stored_ids != dataset_ids:
        problems.append(
            f"case_id mismatch: stored={stored_ids} dataset={dataset_ids}"
        )

    # 3) 由 Snapshot 的 per-case 状态重算 metrics，必须与存量一致
    recomputed = calculate_quality_metrics(
        tuple(
            TextToSQLCaseQuality(
                case_id=str(item.get("case_id", "")),
                generation=str(item.get("generation", "")),
                validation=str(item.get("validation", "")),
                expectation=str(item.get("expectation", "")),
                execution=str(item.get("execution", "")),
                result=str(item.get("result", "")),
                error_code=item.get("error_code"),
            )
            for item in stored.get("cases", [])
        )
    ).as_dict()
    for key, value in recomputed.items():
        if stored.get("metrics", {}).get(key) != value:
            problems.append(
                f"metrics.{key}: stored="
                f"{stored.get('metrics', {}).get(key)!r} "
                f"recomputed={value!r}"
            )

    # 4) Report 存在且关键数字一致
    if not QUALITY_REPORT_PATH.exists():
        problems.append(f"report missing: {QUALITY_REPORT_PATH.name}")
    else:
        text = QUALITY_REPORT_PATH.read_text(encoding="utf-8")
        metrics = stored.get("metrics", {})
        for needle in (
            f"- Cases: {len(dataset)}",
            f"Version: `{dataset_block.get('version')}`",
            "## 5. Failure Distribution",
            "## 7. Limitations",
        ):
            if needle not in text:
                problems.append(f"report missing: {needle}")
        for case_id in dataset_ids:
            if f"`{case_id}`" not in text:
                problems.append(f"report missing case row: {case_id}")

    # 5) 敏感信息自检
    blob = json.dumps(stored, ensure_ascii=False).lower()
    for fragment in ("sk-", "postgres://", "postgresql://", "bearer "):
        if fragment in blob:
            problems.append(f"snapshot contains forbidden fragment: {fragment!r}")

    if problems:
        print("FAIL: 3.9.9 quality consistency check failed")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    metrics = stored.get("metrics", {})
    print(f"OK: 3.9.9 quality snapshot is consistent "
          f"({len(dataset)} cases, phase {QUALITY_PHASE})")
    print(f"    generation_success={metrics.get('generation_success')} "
          f"validator_accepted={metrics.get('validator_accepted')} "
          f"expectation_matched={metrics.get('expectation_matched')}")
    print(f"    execution_success={metrics.get('execution_success')} "
          f"(rate={metrics.get('execution_success_rate')}) "
          f"result_accuracy={metrics.get('result_accuracy')}")
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
            f"quality snapshot contains forbidden keys: {sorted(found)}"
        )
    blob = json.dumps(payload, ensure_ascii=False).lower()
    for fragment in ("sk-", "postgres://", "postgresql://", "bearer "):
        if fragment in blob:
            raise ValueError(
                f"quality snapshot contains forbidden fragment: {fragment!r}"
            )


# ============================================================
# Entry point（两条路径完全分离）
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate Text-to-SQL generation quality."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="offline consistency check only (no LLM, no DB, no write)",
    )
    args = parser.parse_args()
    if args.check:
        return check_existing_quality()
    return asyncio.run(run_quality_evaluation())


if __name__ == "__main__":
    raise SystemExit(main())
