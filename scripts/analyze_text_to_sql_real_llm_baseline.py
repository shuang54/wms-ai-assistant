"""Analyze the Phase 3.9.5 Real LLM Baseline（Phase 3.9.6）。

纯离线失败分析分类，**不调用 DeepSeek、不访问数据库、不联网**。

用法（仓库根目录执行）：

```powershell
python scripts/analyze_text_to_sql_real_llm_baseline.py            # 生成分析报告
python scripts/analyze_text_to_sql_real_llm_baseline.py --check    # 只校验一致性
```

## 两种模式严格分离（吸取 3.9.5 初版 --check 的教训）

```text
--check → check_existing_analysis()   只 load / recompute / compare，不写任何文件
默认   → generate_analysis()          读 3.9.5 Snapshot → 分析 → 写 Report + Analysis Snapshot
```

本脚本**刻意不 import** ``backend.app.db`` / ``TextToSQLService`` /
任何 LLM client —— 从代码结构上就不存在调用路径。

纪律：

- 不修改 3.9.5 Snapshot / 3.9.4 Snapshot / Dataset / Prompt；
- 不保存 generated SQL、不保存 API Key；
- Analysis Snapshot 的 case_id 必须与 3.9.5 完全一致；
- 分类只基于现有字段，不臆测 LLM 实际生成内容。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 允许 `python scripts/xxx.py` 直接执行（把仓库根加入 sys.path）。
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.app.services.text_to_sql_baseline_service import (  # noqa: E402
    DEFAULT_REGRESSION_DATASET_PATH,
    read_dataset_version,
)
from backend.app.services.text_to_sql_evaluation_service import (  # noqa: E402
    load_text_to_sql_regression_dataset,
)
from backend.app.services.text_to_sql_evaluation_taxonomy import (  # noqa: E402
    analyze_text_to_sql_real_llm_baseline,
    render_analysis_report,
    snapshot_case_to_result,
)
from backend.app.services.text_to_sql_real_llm_baseline_service import (  # noqa: E402
    REAL_LLM_SNAPSHOT_PATH,
)

ANALYSIS_REPORT_PATH: Path = (
    _REPO_ROOT / "docs" / "evaluation" / "text-to-sql-real-llm-analysis-3.9.6.md"
)
ANALYSIS_SNAPSHOT_PATH: Path = (
    _REPO_ROOT
    / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_6_analysis.json"
)

ANALYSIS_PHASE: str = "3.9.6"


# ============================================================
# 公共：加载 + 重算（两条路径共用，均离线）
# ============================================================

def _relative(path: Path | str) -> str:
    """仓库相对路径（POSIX 风格），避免产物里出现本地绝对路径。"""
    target = Path(path)
    try:
        return target.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return target.name


def _load_395_snapshot() -> dict[str, Any]:
    """读取 3.9.5 Snapshot（只读）。不存在 → 明确失败，不伪造。"""
    if not REAL_LLM_SNAPSHOT_PATH.exists():
        raise SystemExit(
            f"FAIL: 3.9.5 snapshot not found: {REAL_LLM_SNAPSHOT_PATH.name}"
        )
    return json.loads(REAL_LLM_SNAPSHOT_PATH.read_text(encoding="utf-8"))


def _recompute_analysis(snapshot: dict[str, Any]) -> Any:
    """由 3.9.5 Snapshot + Dataset 重算分析（纯函数组合，无副作用）。"""
    entries = snapshot.get("cases")
    if not isinstance(entries, list):
        raise SystemExit("FAIL: 3.9.5 snapshot has no valid cases[]")
    results = tuple(snapshot_case_to_result(e) for e in entries)
    cases = load_text_to_sql_regression_dataset()
    return analyze_text_to_sql_real_llm_baseline(results, cases), cases


def _analysis_payload(analysis: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "phase": ANALYSIS_PHASE,
        "source": {
            "snapshot": _relative(REAL_LLM_SNAPSHOT_PATH),
            "dataset": _relative(DEFAULT_REGRESSION_DATASET_PATH),
            "dataset_version": read_dataset_version(),
            "total_cases": len(analysis.case_taxonomies),
        },
        **analysis.to_dict(),
    }
    _assert_no_secrets(payload)
    return payload


# ============================================================
# 生成路径
# ============================================================

def generate_analysis() -> int:
    """读 3.9.5 Snapshot → 分析 → 写 Report + Analysis Snapshot。"""
    snapshot = _load_395_snapshot()
    analysis, _cases = _recompute_analysis(snapshot)
    payload = _analysis_payload(analysis, snapshot)

    report = render_analysis_report(
        analysis,
        source_snapshot=payload["source"]["snapshot"],
        source_dataset=payload["source"]["dataset"],
        dataset_version=payload["source"]["dataset_version"],
    )

    _write_report(report)
    _write_analysis_snapshot(payload)

    print(f"Real LLM Failure Analysis — Phase {ANALYSIS_PHASE}")
    print()
    print(f"Source Snapshot: {payload['source']['snapshot']}")
    print(f"Dataset Version: {payload['source']['dataset_version']}")
    print(f"Total Cases:     {analysis.total_cases}")
    print()
    print(f"Generation Success : {analysis.generation_success_cases}")
    print(f"Generation Failure : {analysis.generation_failure_cases}")
    print(f"Generation Unknown : {analysis.generation_unknown_cases}")
    print(f"Validation Accepted: {analysis.validation_accepted_cases}")
    print(f"Validation Rejected: {analysis.validation_rejected_cases}")
    print(f"Expectation Match  : {analysis.expectation_match_cases}")
    print(f"Expectation Mismatch: {analysis.expectation_mismatch_cases}")
    print()
    print(f"Security Cases              : {analysis.security_case_count}")
    print(f"  LLM Refusal               : {analysis.security_llm_refusal_cases}")
    print(f"  Validator Rejection       : "
          f"{analysis.security_validator_rejection_cases}")
    print(f"  Expectation Mismatch      : "
          f"{analysis.security_expectation_mismatch_cases}")
    print()
    print(f"Project Isolation Pass: {analysis.project_isolation_pass_cases}/"
          f"{analysis.project_isolation_case_count}")
    print()
    print(f"Report written:   {ANALYSIS_REPORT_PATH.relative_to(_REPO_ROOT)}")
    print(f"Analysis written: {ANALYSIS_SNAPSHOT_PATH.relative_to(_REPO_ROOT)}")
    return 0


def _write_report(report: str) -> None:
    ANALYSIS_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ANALYSIS_REPORT_PATH.write_text(report, encoding="utf-8")


def _write_analysis_snapshot(payload: dict[str, Any]) -> None:
    ANALYSIS_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ANALYSIS_SNAPSHOT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ============================================================
# --check：纯 offline 一致性校验（不写任何文件）
# ============================================================

def check_existing_analysis() -> int:
    """校验已有分析结果与 3.9.5 Snapshot 一致（NO LLM / NO DB / NO WRITE）。"""
    problems: list[str] = []

    if not ANALYSIS_SNAPSHOT_PATH.exists():
        print(f"FAIL: analysis snapshot not found: "
              f"{ANALYSIS_SNAPSHOT_PATH.name}")
        return 1

    snapshot = _load_395_snapshot()
    analysis, _cases = _recompute_analysis(snapshot)
    recomputed = _analysis_payload(analysis, snapshot)

    stored = json.loads(ANALYSIS_SNAPSHOT_PATH.read_text(encoding="utf-8"))

    # 1) case_id 必须与 3.9.5 完全一致（无丢失 / 无多余）
    stored_ids = [c.get("case_id") for c in stored.get("cases", [])]
    snapshot_ids = [c.get("case_id") for c in snapshot.get("cases", [])]
    if stored_ids != snapshot_ids:
        problems.append(
            f"case_id mismatch with 3.9.5 snapshot: "
            f"stored={stored_ids} snapshot={snapshot_ids}"
        )

    # 2) 汇总 + 逐 case 分类必须可重算复现
    for key in (
        "total_cases", "generation_success_cases", "generation_failure_cases",
        "generation_unknown_cases", "validation_accepted_cases",
        "validation_rejected_cases", "expectation_match_cases",
        "expectation_mismatch_cases", "security_case_count",
        "security_llm_refusal_cases", "security_validator_rejection_cases",
        "security_expectation_mismatch_cases",
        "project_isolation_case_count", "project_isolation_pass_cases",
        "project_isolation_fail_cases",
    ):
        if stored.get(key) != recomputed.get(key):
            problems.append(
                f"{key}: stored={stored.get(key)!r} "
                f"recomputed={recomputed.get(key)!r}"
            )
    if stored.get("cases") != recomputed.get("cases"):
        problems.append("per-case taxonomy differs from recomputed")

    # 3) Report 存在且关键数字一致
    if not ANALYSIS_REPORT_PATH.exists():
        problems.append(f"report missing: {ANALYSIS_REPORT_PATH.name}")
    else:
        text = ANALYSIS_REPORT_PATH.read_text(encoding="utf-8")
        for needle in (
            f"| Total Cases | {analysis.total_cases} |",
            f"| Passed | {analysis.expectation_match_cases} |",
            f"| Failed | {analysis.expectation_mismatch_cases} |",
        ):
            if needle not in text:
                problems.append(f"report missing row: {needle}")
        if "Security Boundary Matrix" not in text:
            problems.append("report missing security boundary matrix")

    if problems:
        print("FAIL: 3.9.6 analysis consistency check failed")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("OK: 3.9.6 analysis is consistent with 3.9.5 snapshot "
          f"({analysis.total_cases} cases, phase {ANALYSIS_PHASE})")
    print(f"    expectation mismatch={analysis.expectation_mismatch_cases} "
          f"security={analysis.security_case_count} "
          f"isolation={analysis.project_isolation_pass_cases}"
          f"/{analysis.project_isolation_case_count}")
    return 0


def _assert_no_secrets(payload: Any) -> None:
    """Analysis Snapshot 不得含敏感信息（只报类别，不输出值）。"""
    forbidden_keys = {
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
                if str(k).lower() in forbidden_keys
            }
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    if found:
        raise ValueError(
            f"analysis snapshot contains forbidden keys: {sorted(found)}"
        )
    blob = json.dumps(payload, ensure_ascii=False).lower()
    for fragment in ("sk-", "postgres://", "postgresql://", "bearer "):
        if fragment in blob:
            raise ValueError(
                f"analysis snapshot contains forbidden fragment: {fragment!r}"
            )


# ============================================================
# Entry point（两条路径完全分离）
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze Text-to-SQL Real LLM baseline (offline)."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="offline consistency check only (no LLM, no DB, no write)",
    )
    args = parser.parse_args()
    if args.check:
        return check_existing_analysis()
    return generate_analysis()


if __name__ == "__main__":
    raise SystemExit(main())
