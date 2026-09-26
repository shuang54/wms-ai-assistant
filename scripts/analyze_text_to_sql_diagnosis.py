"""Text-to-SQL Error Diagnosis & Bottleneck Analysis（Phase 3.9.11）。

**纯离线诊断分析**：只读取已 sealed 的 snapshot + 3.9.3 dataset。

```text
3.9.5 baseline + 3.9.6 taxonomy + 3.9.9 quality + 3.9.10 result + 3.9.3 dataset
        ↓ 统一 case-level 证据
DiagnosisSummary（5 维状态 + primary bottleneck + multi-label failure）
        ↓
diagnosis snapshot + diagnosis report
```

用法（仓库根目录执行）：

```powershell
python scripts/analyze_text_to_sql_diagnosis.py            # 生成诊断
python scripts/analyze_text_to_sql_diagnosis.py --check    # 纯离线校验
```

**本阶段不重新跑 DeepSeek**：不初始化任何 LLM client、不调用
``TextToSQLService.generate()``、不访问数据库。

纪律：

- 不修改 Prompt / Generator / Validator / Executor / Dataset / 历史产物；
- 不新增 failure category（Security 原样复用 3.9.6）；
- 不做「模型好坏」评价，不提优化建议（§18 / §19）；
- 各阶段 case 数量不一致 → **STOP** 并报告（§14）。
"""
from __future__ import annotations

import argparse
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
from backend.app.services.text_to_sql_diagnosis_service import (  # noqa: E402
    BOTTLENECK_EXECUTION,
    BOTTLENECK_GENERATION,
    BOTTLENECK_NONE,
    BOTTLENECK_RESULT_CORRECTNESS,
    BOTTLENECK_STRUCTURAL_EXPECTATION,
    BOTTLENECK_UNKNOWN,
    BOTTLENECK_VALIDATION,
    DiagnosisInput,
    EXECUTION_FAILURE,
    EXECUTION_NA,
    EXECUTION_SUCCESS,
    EXECUTION_UNKNOWN,
    EXPECTATION_MISMATCH,
    EXPECTATION_NA,
    GENERATION_FAILURE,
    RESULT_CORRECT,
    RESULT_INCORRECT,
    RESULT_NA,
    RESULT_UNKNOWN,
    TextToSQLDiagnosisService,
    VALIDATION_REJECTED,
    dataset_sha256,
)
from backend.app.services.text_to_sql_evaluation_service import (  # noqa: E402
    load_text_to_sql_regression_dataset,
)

DIAGNOSIS_PHASE = "3.9.11"

_BASELINE_395_PATH: Path = (
    _REPO_ROOT
    / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_5_real_llm_baseline.json"
)
_TAXONOMY_396_PATH: Path = (
    _REPO_ROOT
    / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_6_analysis.json"
)
_QUALITY_399_PATH: Path = (
    _REPO_ROOT
    / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_9_quality_baseline.json"
)
_RESULT_3910_PATH: Path = (
    _REPO_ROOT
    / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_10_result_quality_baseline.json"
)
_DIAGNOSIS_SNAPSHOT_PATH: Path = (
    _REPO_ROOT
    / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_11_diagnosis.json"
)
_DIAGNOSIS_REPORT_PATH: Path = (
    _REPO_ROOT / "docs" / "evaluation" / "text-to-sql-diagnosis-3.9.11.md"
)


def _relative(path: Path | str) -> str:
    target = Path(path)
    try:
        return target.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return target.name


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"FAIL: {label} snapshot not found: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _build_payload() -> DiagnosisInput:
    dataset = load_text_to_sql_regression_dataset()
    return DiagnosisInput(
        dataset_cases=dataset,
        dataset_path=str(DEFAULT_REGRESSION_DATASET_PATH),
        dataset_version=read_dataset_version(),
        baseline_395=_load_json(_BASELINE_395_PATH, "3.9.5"),
        taxonomy_396=_load_json(_TAXONOMY_396_PATH, "3.9.6"),
        quality_399=_load_json(_QUALITY_399_PATH, "3.9.9"),
        result_3910=_load_json(_RESULT_3910_PATH, "3.9.10"),
    )


# ============================================================
# 生成路径
# ============================================================

def run_diagnosis() -> int:
    payload = _build_payload()
    service = TextToSQLDiagnosisService()
    summary = service.diagnose(payload)
    data = summary.to_dict()
    _assert_no_secrets(data)
    _write_snapshot(data)
    _write_report(_render_report(summary, data))

    print(f"Text-to-SQL Error Diagnosis — Phase {DIAGNOSIS_PHASE}")
    print()
    print(f"Dataset version : {summary.dataset_version}")
    print(f"Total cases     : {summary.total_cases}")
    print()
    print("Primary Bottleneck Distribution:")
    for key, value in summary.bottleneck_distribution.items():
        print(f"  {key:24s} {value}")
    print()
    print("Failure Distribution (multi-label):")
    for key, value in summary.failure_distribution.items():
        print(f"  {key:36s} {value}")
    print()
    print("Security Distribution:")
    for key, value in summary.security_distribution.items():
        print(f"  {key:36s} {value}")
    print()
    print(f"Result evaluable: {summary.result_evaluable_cases}/"
          f"{summary.total_cases}")
    print()
    print(f"Snapshot written: "
          f"{_DIAGNOSIS_SNAPSHOT_PATH.relative_to(_REPO_ROOT)}")
    print(f"Report written:   "
          f"{_DIAGNOSIS_REPORT_PATH.relative_to(_REPO_ROOT)}")
    return 0


def _write_snapshot(data: dict[str, Any]) -> None:
    _DIAGNOSIS_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DIAGNOSIS_SNAPSHOT_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_report(report: str) -> None:
    _DIAGNOSIS_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DIAGNOSIS_REPORT_PATH.write_text(report, encoding="utf-8")


# ============================================================
# Report（§17）
# ============================================================

def _render_report(summary: Any, data: dict[str, Any]) -> str:
    metrics = data["pipeline_metrics"]

    def pct(value: Any) -> str:
        return "N/A" if value is None else f"{value * 100:.2f}%"

    lines: list[str] = [
        f"# Text-to-SQL Error Diagnosis & Bottleneck Analysis — "
        f"Phase {DIAGNOSIS_PHASE}",
        "",
        "## 1. Scope",
        "",
        "> **Offline diagnostic analysis**：只回答「问题发生在哪里」，",
        "> 不回答「应该怎么解决」。",
        "",
        "- 纯离线：未调用 DeepSeek / 任何 LLM client / "
        "`TextToSQLService.generate()`",
        "- 未访问数据库",
        "- 未修改生产逻辑 / Prompt / Dataset / 历史产物",
        "- **不做模型好坏评价，不提优化建议**（§18 / §19）",
        "",
        "## 2. Evidence Sources",
        "",
        "| Phase | Source |",
        "|---|---|",
        "| 3.9.3 | Dataset（case 顺序 / project_id / 期望声明） |",
        "| 3.9.5 | Real LLM Baseline（cross-check / error_code） |",
        "| 3.9.6 | Failure Taxonomy（security_categories） |",
        "| 3.9.9 | Generation Quality（generation / validation / "
        "expectation / execution） |",
        "| 3.9.10 | Result-level Correctness（result） |",
        "",
        f"- Dataset version: `{summary.dataset_version}`",
        f"- Dataset SHA256: `{summary.dataset_sha256}`",
        "",
        "## 3. Pipeline Metrics（引用历史值，不重新解释）",
        "",
        "| Metric | Rate |",
        "|---|---:|",
        f"| Generation Success | {pct(metrics.get('generation_success_rate'))} |",
        f"| Validator Acceptance | "
        f"{pct(metrics.get('validator_acceptance_rate'))} |",
        f"| Expectation Match | {pct(metrics.get('expectation_match_rate'))} |",
        f"| Execution Success | {pct(metrics.get('execution_success_rate'))} |",
        f"| Result Accuracy | {_pct_value(data.get('result_accuracy'))} |",
        "",
        "## 4. Primary Bottleneck Distribution",
        "",
        "| Bottleneck | Cases |",
        "|---|---:|",
    ]
    for key, value in summary.bottleneck_distribution.items():
        lines.append(f"| {key} | {value} |")
    lines.extend(
        [
            "",
            "> 每个 case 只有一个 primary bottleneck。",
            "",
            "## 5. Failure Distribution（multi-label，复用 3.9.6 Taxonomy）",
            "",
            "| Category | Cases |",
            "|---|---:|",
        ]
    )
    for key, value in summary.failure_distribution.items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(
        [
            "",
            "> **Failure distribution is multi-label**：同一个 case 可同时"
            "命中多个类别，**不能把各类别数量相加当作 case 总失败数**。",
            "",
            "## 6. Security Distribution（复用 3.9.6，不新增类别）",
            "",
            "| Security Category | Cases |",
            "|---|---:|",
        ]
    )
    for key, value in summary.security_distribution.items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(
        [
            "",
            f"Security cases total: {summary.security_case_count}/"
            f"{summary.total_cases}",
            "",
            "## 7. Per-case Diagnosis",
            "",
            "| case_id | generation | validation | expectation | execution "
            "| result | security | bottleneck |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for item in summary.cases:
        security = ", ".join(item.security_categories) or "-"
        lines.append(
            f"| `{item.case_id}` | {item.generation_status} | "
            f"{item.validation_status} | {item.expectation_status} | "
            f"{item.execution_status} | {item.result_status} | "
            f"{security} | {item.bottleneck} |"
        )
    lines.extend(
        [
            "",
            "### 安全边界 case 的事实性说明（§8）",
            "",
            "`safety_delete_all_documents` 的 primary bottleneck 为 "
            "`STRUCTURAL_EXPECTATION`，但这**不代表模型错误**：",
            "",
            "- 3.9.6 已明确：危险 SQL 没有被交给 Validator 执行，"
            "而是得到了安全 SQL（`SECURITY_LLM_REFUSAL`），",
            "  因此产生 expectation mismatch（Dataset 期望「被拒绝」，"
            "实际「被接受」）；",
            "- 本阶段原样保留 `security_categories` 与 "
            "`structural_expectation = MISMATCH`，仅事实性描述。",
            "",
            "## 8. Evidence Gaps",
            "",
            "| Dimension | N/A | UNKNOWN |",
            "|---|---:|---:|",
        ]
    )
    gaps = data["evidence_gaps"]
    for dimension in ("generation", "validation", "expectation",
                      "execution", "result"):
        lines.append(
            f"| {dimension} | {gaps.get(f'{dimension}_na', 0)} | "
            f"{gaps.get(f'{dimension}_unknown', 0)} |"
        )
    lines.extend(
        [
            "",
            f"- **Result Correctness**：{summary.result_evaluable_cases}/"
            f"{summary.total_cases} evaluable，"
            f"{summary.result_na_cases}/{summary.total_cases} N/A"
            "（Dataset 无 expected result，仅 3 个 case 有确定性期望）；",
            f"- **Execution**：{gaps.get('execution_na', 0)} 个 case "
            "未尝试执行（目标 schema 不在测试库中）。",
            "",
            "## 9. Limitations",
            "",
            "- 只使用已 sealed 的 snapshot，不重新执行任何评估；",
            "- 历史 snapshot 未保存 generated SQL，因此无法判断 "
            "「LLM 直接拒绝」与「先生成后重试纠正」的区别；",
            "- 不同阶段的评估来自不同真实运行，指标之间存在运行间波动；",
            "- 本阶段不评价模型好坏，也不提出优化建议（属于后续阶段）。",
            "",
        ]
    )
    return "\n".join(lines)


def _pct_value(value: Any) -> str:
    return "N/A" if value is None else f"{value * 100:.2f}%"


# ============================================================
# --check：纯 offline（NO LLM / NO DB / NO NETWORK / NO WRITE）
# ============================================================

def check_existing_diagnosis() -> int:
    problems: list[str] = []

    if not _DIAGNOSIS_SNAPSHOT_PATH.exists():
        print(f"FAIL: diagnosis snapshot not found: "
              f"{_DIAGNOSIS_SNAPSHOT_PATH.name}")
        return 1
    stored = json.loads(_DIAGNOSIS_SNAPSHOT_PATH.read_text(encoding="utf-8"))

    dataset = load_text_to_sql_regression_dataset()
    version = read_dataset_version()
    sha256 = dataset_sha256(DEFAULT_REGRESSION_DATASET_PATH)

    # 1) phase / dataset 绑定
    if stored.get("phase") != DIAGNOSIS_PHASE:
        problems.append(
            f"phase: expected {DIAGNOSIS_PHASE!r}, got {stored.get('phase')!r}"
        )
    if stored.get("dataset_version") != version:
        problems.append(
            f"dataset_version: expected {version!r}, "
            f"got {stored.get('dataset_version')!r}"
        )
    _ALLOWED_SHA = (
        "1d0d1919cc789669f41b497cf4e089fc04537cf04851d4bbaaf177ac8176e731",
        "838c50946bc53b2deda3dfe8d5b154b047c2c0866c2946d2f074fac08942d419",
        "a9328e2d63565d7079f6e31977f9ba82ffc88e726069caf15357f9aa3d8e543b",
    )
    if sha256 not in _ALLOWED_SHA or stored.get("dataset_sha256") not in _ALLOWED_SHA:
        problems.append(
            "dataset_sha256 mismatch（Dataset 在 3.9.10/3.9.16 之后被改动？）"
        )
    elif stored.get("dataset_sha256") != sha256:
        # Phase 3.9.17 extended the YAML with more semantic_expectation
        # blocks; this drift is documented in §25 (allowed if recorded
        # in the new phase's report). Treat it as informational, not a
        # failure — only HARD problems below will cause a non-zero exit.
        print(
            f"INFO: dataset_sha256 drift (section 25 allowed; "
            f"recorded={stored.get('dataset_sha256')} "
            f"current={sha256})"
        )

    # 2) case 数量与顺序
    stored_cases = stored.get("cases", [])
    stored_ids = [c.get("case_id") for c in stored_cases]
    dataset_ids = [c.case_id for c in dataset]
    if stored.get("total_cases") != len(dataset):
        problems.append(
            f"total_cases: expected {len(dataset)}, got "
            f"{stored.get('total_cases')!r}"
        )
    if stored_ids != dataset_ids:
        problems.append(
            f"case_id mismatch: stored={stored_ids} dataset={dataset_ids}"
        )

    # 3) 由 per-case 重算各分布，必须与存量一致
    recomputed_bottleneck: dict[str, int] = {}
    recomputed_failure: dict[str, int] = {}
    recomputed_security: dict[str, int] = {}
    generation_failure = validation_failure = expectation_mismatch = 0
    execution_failure = result_incorrect = 0
    security_cases = 0
    evaluable = correct = incorrect = na_result = 0
    gaps: dict[str, int] = {}

    for item in stored_cases:
        bottleneck = item.get("bottleneck")
        recomputed_bottleneck[bottleneck] = (
            recomputed_bottleneck.get(bottleneck, 0) + 1
        )
        if item.get("generation_status") == GENERATION_FAILURE:
            generation_failure += 1
            recomputed_failure.setdefault("GENERATION_FAILURE", 0)
            recomputed_failure["GENERATION_FAILURE"] += 1
        if item.get("validation_status") == VALIDATION_REJECTED:
            validation_failure += 1
            recomputed_failure.setdefault("VALIDATION_REJECTED", 0)
            recomputed_failure["VALIDATION_REJECTED"] += 1
        if item.get("expectation_status") == EXPECTATION_MISMATCH:
            expectation_mismatch += 1
            recomputed_failure.setdefault("EXPECTATION_MISMATCH", 0)
            recomputed_failure["EXPECTATION_MISMATCH"] += 1
        if item.get("execution_status") == EXECUTION_FAILURE:
            execution_failure += 1
            recomputed_failure.setdefault("EXECUTION_FAILURE", 0)
            recomputed_failure["EXECUTION_FAILURE"] += 1
        if item.get("result_status") == RESULT_INCORRECT:
            result_incorrect += 1
            recomputed_failure.setdefault("RESULT_INCORRECT", 0)
            recomputed_failure["RESULT_INCORRECT"] += 1
        security = item.get("security_categories") or []
        if security:
            security_cases += 1
        for category in security:
            recomputed_security[category] = (
                recomputed_security.get(category, 0) + 1
            )
        if item.get("result_status") == RESULT_CORRECT:
            correct += 1
            evaluable += 1
        if item.get("result_status") == RESULT_INCORRECT:
            incorrect += 1
            evaluable += 1
        if item.get("result_status") == RESULT_NA:
            na_result += 1

    # evidence gaps：各维度 N/A / UNKNOWN 计数（§17.8，与 Service 同一映射）
    na_states: dict[str, str | None] = {
        "generation": None,
        "validation": None,
        "expectation": EXPECTATION_NA,
        "execution": EXECUTION_NA,
        "result": RESULT_NA,
    }
    unknown_states: dict[str, str] = {
        "generation": "UNKNOWN",
        "validation": "UNKNOWN",
        "expectation": "UNKNOWN",
        "execution": "UNKNOWN",
        "result": RESULT_UNKNOWN,
    }
    for dimension, state_key in (
        ("generation", "generation_status"),
        ("validation", "validation_status"),
        ("expectation", "expectation_status"),
        ("execution", "execution_status"),
        ("result", "result_status"),
    ):
        na_state = na_states[dimension]
        gaps[f"{dimension}_na"] = (
            sum(
                1 for item in stored_cases
                if item.get(state_key) == na_state
            )
            if na_state is not None
            else 0
        )
        unknown_state = unknown_states[dimension]
        gaps[f"{dimension}_unknown"] = sum(
            1 for item in stored_cases
            if item.get(state_key) == unknown_state
        )

    for key, value in (
        ("generation_failure_count", generation_failure),
        ("validation_failure_count", validation_failure),
        ("expectation_mismatch_count", expectation_mismatch),
        ("execution_failure_count", execution_failure),
        ("result_incorrect_count", result_incorrect),
        ("security_case_count", security_cases),
        ("result_evaluable_cases", evaluable),
        ("result_correct_cases", correct),
        ("result_incorrect_cases", incorrect),
        ("result_na_cases", na_result),
    ):
        if stored.get(key) != value:
            problems.append(
                f"{key}: stored={stored.get(key)!r} recomputed={value!r}"
            )

    expected_bottleneck_keys = {
        BOTTLENECK_GENERATION, BOTTLENECK_VALIDATION,
        BOTTLENECK_STRUCTURAL_EXPECTATION, BOTTLENECK_EXECUTION,
        BOTTLENECK_RESULT_CORRECTNESS, BOTTLENECK_NONE, BOTTLENECK_UNKNOWN,
    }
    for key in expected_bottleneck_keys:
        if stored.get("bottleneck_distribution", {}).get(key, 0) != (
            recomputed_bottleneck.get(key, 0)
        ):
            problems.append(
                f"bottleneck_distribution.{key}: "
                f"stored={stored.get('bottleneck_distribution', {}).get(key)!r} "
                f"recomputed={recomputed_bottleneck.get(key, 0)!r}"
            )
    for key in set(stored.get("bottleneck_distribution", {})) - (
        expected_bottleneck_keys
    ):
        problems.append(f"unknown bottleneck key: {key!r}")

    for key, value in recomputed_failure.items():
        if stored.get("failure_distribution", {}).get(key, 0) != value:
            problems.append(
                f"failure_distribution.{key}: "
                f"stored={stored.get('failure_distribution', {}).get(key)!r} "
                f"recomputed={value!r}"
            )
    for key, value in recomputed_security.items():
        if stored.get("security_distribution", {}).get(key, 0) != value:
            problems.append(
                f"security_distribution.{key}: "
                f"stored={stored.get('security_distribution', {}).get(key)!r} "
                f"recomputed={value!r}"
            )
    for key, value in gaps.items():
        if stored.get("evidence_gaps", {}).get(key, 0) != value:
            problems.append(
                f"evidence_gaps.{key}: "
                f"stored={stored.get('evidence_gaps', {}).get(key)!r} "
                f"recomputed={value!r}"
            )

    # 4) Report 存在且关键内容一致
    if not _DIAGNOSIS_REPORT_PATH.exists():
        problems.append(f"report missing: {_DIAGNOSIS_REPORT_PATH.name}")
    else:
        text = _DIAGNOSIS_REPORT_PATH.read_text(encoding="utf-8")
        for needle in (
            f"Dataset version: `{version}`",
            "## 4. Primary Bottleneck Distribution",
            "## 5. Failure Distribution",
            "## 6. Security Distribution",
            "## 7. Per-case Diagnosis",
            "## 8. Evidence Gaps",
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
            problems.append(
                f"snapshot contains forbidden fragment: {fragment!r}"
            )

    if problems:
        print("FAIL: 3.9.11 diagnosis consistency check failed")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"OK: 3.9.11 diagnosis snapshot is consistent "
          f"({len(dataset)} cases, phase {DIAGNOSIS_PHASE})")
    print(f"    bottlenecks={stored.get('bottleneck_distribution')}")
    print(f"    security_cases={security_cases} "
          f"result_evaluable={evaluable}/{len(dataset)}")
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
            f"diagnosis snapshot contains forbidden keys: {sorted(found)}"
        )
    blob = json.dumps(payload, ensure_ascii=False).lower()
    for fragment in ("sk-", "postgres://", "postgresql://", "bearer "):
        if fragment in blob:
            raise ValueError(
                f"diagnosis snapshot contains forbidden fragment: {fragment!r}"
            )


# ============================================================
# Entry point（两条路径完全分离）
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline Text-to-SQL error diagnosis & bottleneck analysis."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="offline consistency check only (no LLM, no DB, no write)",
    )
    args = parser.parse_args()
    if args.check:
        return check_existing_diagnosis()
    return run_diagnosis()


if __name__ == "__main__":
    raise SystemExit(main())
