"""Generate the Text-to-SQL Real LLM Baseline snapshot + report（Phase 3.9.5）。

> 本脚本只测量当前真实 DeepSeek Text-to-SQL Pipeline 的表现。
> 本阶段不修改 Prompt / Dataset / Generator / Validator。

用法（仓库根目录执行）：

```powershell
# 真实运行（需要 .env 含 LLM_API_KEY / LLM_PROVIDER / LLM_MODEL）
python scripts/generate_text_to_sql_real_llm_baseline.py

# 只比对现有 Snapshot（**纯 offline**：NO LLM / NO DB / NO NETWORK / NO FILE WRITE）
python scripts/generate_text_to_sql_real_llm_baseline.py --check
```

## 两种模式严格分离

```text
--check → check_existing_baseline()   只 load / validate / recalculate / compare
默认   → generate_new_real_llm_baseline()  真实 DeepSeek → 写 Snapshot + Report
```

``check_existing_baseline()`` 路径**绝不出现** ``run_real_llm_baseline()``，
也不触碰 settings.llm / engine；因此即使没有 API Key、没有数据库、断网，
``--check`` 依然可用。

纪律（§十四 / §十七 / §十九）：

- **不覆盖 3.9.4 baseline**（路径与脚本均独立）；
- 缺 API Key / Provider / Model → 整体失败，**不**生成假 Baseline；
- 单 case 失败不污染其它 case（Runner 内部 try/except 已保证）；
- Snapshot / Report 绝不写入 API Key / DATABASE_URL / 完整环境变量；
- ``--check`` 只读 Snapshot / Dataset / Report，不写任何文件，
  并对 Snapshot 做 phase / baseline_type / execution_mode / dataset /
  case-id 集合 / **metrics 重算** / Report 数字 / 密钥泄漏 检查。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# 允许 `python scripts/xxx.py` 直接执行（把仓库根加入 sys.path）。
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import text as sa_text  # noqa: E402

from backend.app.config import settings  # noqa: E402
from backend.app.db import reset_engine_cache  # noqa: E402
from backend.app.db.session import get_engine  # noqa: E402
from backend.app.services.text_to_sql_baseline_service import (  # noqa: E402
    BASELINE_SNAPSHOT_PATH as PHASE_3_9_4_SNAPSHOT_PATH,
    RATE_PRECISION,
    read_dataset_version,
)
from backend.app.services.text_to_sql_evaluation_service import (  # noqa: E402
    StaticEvaluationContextResolver,
    TextToSQLEvaluationResult,
    load_text_to_sql_regression_dataset,
)
from backend.app.services.text_to_sql_real_llm_baseline_service import (  # noqa: E402
    BASELINE_TYPE_REAL_LLM,
    PHASE_3_9_5,
    REAL_LLM_EXECUTION_MODE,
    REAL_LLM_REPORT_PATH,
    REAL_LLM_SNAPSHOT_PATH,
    calculate_real_llm_baseline_metrics,
    collect_real_llm_environment_info,
    run_real_llm_baseline,
)
from backend.app.services.text_to_sql_service import TextToSQLService  # noqa: E402
from scripts._text_to_sql_offline_bindings import build_offline_project_bindings  # noqa: E402

# ============================================================
# Pre-flight checks（§十九）
# ============================================================

def _verify_environment() -> None:
    """执行前确认：API Key / Provider / Model / Dataset。"""
    if not settings.llm.api_key.strip():
        raise SystemExit(
            "LLM_API_KEY is empty — refusing to run real LLM baseline "
            "(set LLM_API_KEY in .env or environment; do not commit it)."
        )
    if not settings.llm.provider.strip():
        raise SystemExit("LLM_PROVIDER is empty — refusing to run.")
    if not settings.llm.model.strip():
        raise SystemExit("LLM_MODEL is empty — refusing to run.")


def _detect_database_version() -> str:
    if not settings.database.url.strip():
        return "unavailable"
    try:
        reset_engine_cache()
        engine = get_engine()
        if engine is None:
            return "unavailable"
        with engine.connect() as conn:
            return str(conn.execute(sa_text("SELECT version()")).scalar())
    except Exception:
        return "unavailable"


# ============================================================
# Fake baseline 引用（用于 §十三 对比表）
# ============================================================

def _load_fake_metrics() -> dict[str, Any] | None:
    """读取 3.9.4 Snapshot 中的 metrics（用于 Report 对比表）。"""
    if not PHASE_3_9_4_SNAPSHOT_PATH.exists():
        return None
    try:
        snapshot = json.loads(
            PHASE_3_9_4_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None
    return snapshot.get("metrics")


# ============================================================
# Generation path（真实 DeepSeek）
# ============================================================

async def generate_new_real_llm_baseline() -> int:
    """跑真实 DeepSeek 并写 Snapshot + Report（§四 默认模式，行为不变）。"""
    cases = load_text_to_sql_regression_dataset()
    environment = collect_real_llm_environment_info(
        database=_detect_database_version()
    )
    bindings = build_offline_project_bindings()

    baseline = await run_real_llm_baseline(
        generator=TextToSQLService(),
        context_resolver=StaticEvaluationContextResolver(bindings=bindings),
        cases=cases,
        phase=PHASE_3_9_5,
        environment=environment,
    )

    print(baseline.render_summary())
    print()

    fake_metrics = _load_fake_metrics()
    _write_snapshot(baseline.to_snapshot_dict())
    _write_report(baseline.render_report(fake_baseline_metrics=fake_metrics))
    print(f"Snapshot written: {REAL_LLM_SNAPSHOT_PATH.relative_to(_REPO_ROOT)}")
    print(f"Report written:   {REAL_LLM_REPORT_PATH.relative_to(_REPO_ROOT)}")
    return 0


def _write_snapshot(snapshot: dict[str, Any]) -> None:
    REAL_LLM_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REAL_LLM_SNAPSHOT_PATH.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_report(report: str) -> None:
    REAL_LLM_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REAL_LLM_REPORT_PATH.write_text(report, encoding="utf-8")


# ============================================================
# Offline consistency check（§六：NO LLM / NO DB / NO NETWORK / 不写文件）
# ============================================================

#: 可用 Snapshot cases[] 完整重算的 metrics 计数
#: （llm_generated_cases 除外——见 ``_check_llm_specific_metrics``）
_RECALCULATED_METRIC_KEYS: tuple[str, ...] = (
    "total_cases", "passed_cases", "failed_cases",
    "validation_passed_cases", "validation_expectation_cases",
    "validation_expectation_passed_cases", "execution_cases",
    "execution_passed_cases", "security_cases", "security_passed_cases",
    "project_isolation_cases", "project_isolation_passed_cases",
    "validator_accepted_cases",
)

#: 由上面计数派生的比率
#: 注 ``llm_generation_success_rate`` / ``validator_acceptance_rate`` 不在此列——
#: 两者分母都含 ``llm_generated_cases``，而该计数依赖 "是否拿到 generated_sql"，
#: SQL 按设计不进 Snapshot（§六），故改由 ``_check_llm_specific_metrics`` 校验。
_RECALCULATED_RATE_KEYS: tuple[str, ...] = (
    "expectation_pass_rate", "validation_expectation_pass_rate",
    "execution_pass_rate", "security_expectation_pass_rate",
    "project_isolation_pass_rate",
)

#: Report Metrics 表中必须出现的「标签 → snapshot metrics key」
_REPORT_RATE_ROWS: tuple[tuple[str, str], ...] = (
    ("Expectation Pass Rate", "expectation_pass_rate"),
    ("Validation Expectation Pass Rate",
     "validation_expectation_pass_rate"),
    ("LLM Generation Success Rate", "llm_generation_success_rate"),
    ("Validator Acceptance Rate", "validator_acceptance_rate"),
    ("Execution Pass Rate", "execution_pass_rate"),
    ("Security Pass Rate", "security_expectation_pass_rate"),
    ("Project Isolation Pass Rate", "project_isolation_pass_rate"),
)

#: Report 中必须出现的「计数标签 → snapshot metrics key」（Rate 列为 "-"）
_REPORT_COUNT_ROWS: tuple[tuple[str, str], ...] = (
    ("Total Cases", "total_cases"),
    ("Passed", "passed_cases"),
    ("Failed", "failed_cases"),
)

#: 敏感信息检查：禁止出现的 **键名**（递归、大小写不敏感）
_FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {"api_key", "llm_api_key", "password", "database_url", "dsn",
     "connection_string", "secret", "token", "authorization"}
)

#: 敏感信息检查：禁止出现在 **任意位置** 的明文凭证片段
#: 注意：这里刻意不检查裸 "token" / "password" 字符串，因为
#: case_id ``top_n_chunks_by_token_count`` 合法地含 "token"。
_FORBIDDEN_VALUE_FRAGMENTS: tuple[str, ...] = (
    "sk-", "postgres://", "postgresql://", "bearer ",
)


def check_existing_baseline() -> int:
    """纯 offline 一致性检查（§五 / §七 / §八）。

    只读三个文件：3.9.5 Snapshot、Regression Dataset、3.9.5 Report。
    **不调用 LLM、不连 DB、不访问网络、不写任何文件。**
    """
    problems: list[str] = []

    # ---- 1) Snapshot 存在 ----
    if not REAL_LLM_SNAPSHOT_PATH.exists():
        print(f"FAIL: snapshot not found: {REAL_LLM_SNAPSHOT_PATH.name}")
        return 1
    try:
        snapshot: dict[str, Any] = json.loads(
            REAL_LLM_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: cannot parse snapshot: {type(exc).__name__}")
        return 1

    # ---- 2/3/4) phase / baseline_type / execution_mode ----
    _expect(problems, "phase", snapshot.get("phase"), PHASE_3_9_5)
    _expect(problems, "baseline_type", snapshot.get("baseline_type"),
            BASELINE_TYPE_REAL_LLM)
    _expect(problems, "execution_mode", snapshot.get("execution_mode"),
            REAL_LLM_EXECUTION_MODE)

    # ---- 5/6/7) Dataset 绑定：version / count / case_id 集合 ----
    dataset = snapshot.get("dataset")
    if not isinstance(dataset, dict):
        problems.append("dataset block missing or malformed")
        dataset = {}
    cases = load_text_to_sql_regression_dataset()
    _expect(problems, "dataset.version", dataset.get("version"),
            read_dataset_version())

    snapshot_cases = snapshot.get("cases")
    if not isinstance(snapshot_cases, list):
        problems.append("cases must be a list")
        snapshot_cases = []
    _expect(problems, "dataset.total_cases", dataset.get("total_cases"),
            len(cases))
    _expect(problems, "len(cases)", len(snapshot_cases), len(cases))

    _check_case_ids(problems, cases, snapshot_cases)

    # ---- 8) metrics：由 cases[] 重算，不信任 Snapshot 里的存量值 ----
    _check_metrics_recalculation(problems, snapshot, cases, snapshot_cases)

    # ---- 9) Report 一致性（简单稳定的子串匹配，不做 Markdown AST）----
    _check_report(problems, snapshot)

    # ---- 10) Secret safety ----
    _check_secret_safety(problems, snapshot)

    if problems:
        print("FAIL: real LLM baseline consistency check failed")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    metrics = snapshot.get("metrics", {})
    print(f"OK: real LLM baseline snapshot is consistent "
          f"({len(cases)} cases, phase {PHASE_3_9_5})")
    print(f"    passed={metrics.get('passed_cases')} "
          f"failed={metrics.get('failed_cases')} "
          f"expectation_pass_rate={metrics.get('expectation_pass_rate')}")
    print(f"    llm_generation_success_rate="
          f"{metrics.get('llm_generation_success_rate')} "
          f"validator_acceptance_rate="
          f"{metrics.get('validator_acceptance_rate')}")
    return 0


def _expect(
    problems: list[str], label: str, actual: Any, expected: Any
) -> None:
    if actual != expected:
        problems.append(f"{label}: expected {expected!r}, got {actual!r}")


def _check_case_ids(
    problems: list[str], cases: Any, snapshot_cases: list[Any]
) -> None:
    dataset_ids = [c.case_id for c in cases]
    snapshot_ids = [
        entry.get("case_id") for entry in snapshot_cases
        if isinstance(entry, dict)
    ]
    missing = sorted(set(dataset_ids) - set(snapshot_ids))
    extra = sorted(set(snapshot_ids) - set(dataset_ids))
    duplicates = sorted({i for i in snapshot_ids if snapshot_ids.count(i) > 1})
    if missing:
        problems.append(f"missing case_id in snapshot: {missing}")
    if extra:
        problems.append(f"unexpected case_id in snapshot: {extra}")
    if duplicates:
        problems.append(f"duplicate case_id in snapshot: {duplicates}")


def _check_metrics_recalculation(
    problems: list[str],
    snapshot: dict[str, Any],
    cases: Any,
    snapshot_cases: list[Any],
) -> None:
    """由 Snapshot cases[] 反推 metrics，与 Snapshot 存量 metrics 逐字段比对。"""
    stored = snapshot.get("metrics")
    if not isinstance(stored, dict):
        problems.append("metrics block missing or malformed")
        return

    synthetic_results = tuple(
        _result_from_snapshot_case(entry)
        for entry in snapshot_cases
        if isinstance(entry, dict)
    )
    recalculated = calculate_real_llm_baseline_metrics(
        synthetic_results, cases
    ).as_dict()

    for key in (*_RECALCULATED_METRIC_KEYS, *_RECALCULATED_RATE_KEYS):
        if stored.get(key) != recalculated.get(key):
            problems.append(
                f"metrics.{key}: snapshot={stored.get(key)!r} "
                f"recalculated={recalculated.get(key)!r}"
            )

    _check_llm_specific_metrics(problems, stored, recalculated)


def _result_from_snapshot_case(entry: dict[str, Any]) -> Any:
    """把 Snapshot cases[] 条目还原为 Result，仅用于 metrics 重算。

    ``generated_sql`` / ``question`` 按设计不进 Snapshot（§六），故留空；
    被重算的全部 metrics 字段都不依赖它们——唯一依赖 SQL 的是
    ``llm_generated_cases``，它由 ``_check_llm_generated_invariants`` 单独校验。
    """
    return TextToSQLEvaluationResult(
        case_id=str(entry.get("case_id", "")),
        project_id=entry.get("project_id"),
        question="",
        generated_sql=None,
        validation_passed=bool(entry.get("validation_passed", False)),
        execution_passed=entry.get("execution_passed"),
        matched_expectations=tuple(entry.get("matched_expectations", ())),
        failed_expectations=tuple(entry.get("failed_expectations", ())),
        error_code=entry.get("error_code"),
    )


def _check_llm_specific_metrics(
    problems: list[str],
    stored: dict[str, Any],
    recalculated: dict[str, Any],
) -> None:
    """校验两个 LLM 专属指标（§五 要求纳入检查，不能跳过）。

    ``llm_generated_cases`` 表示"是否拿到了可进入 Validator 的 SQL"，依赖
    ``generated_sql``；而 SQL 按设计**不写入 Snapshot**（§六 §十六），
    因此无法像其它字段那样由 cases[] 完整重算。这里改为三重校验：

        1. ``validator_accepted_cases`` 由 cases[] 重算（已在上层比对）；
        2. ``llm_generated_cases`` 取值范围 [0, total] + 单调性
           （>= validator_accepted_cases，Validator 不可能接受不存在的 SQL）；
        3. 两个比率的算术自洽（存量计数 → 存量比率必须闭合）。
    """
    total = stored.get("total_cases")
    generated = stored.get("llm_generated_cases")
    accepted = recalculated.get("validator_accepted_cases")

    if not isinstance(total, int) or total <= 0:
        problems.append(f"metrics.total_cases invalid: {total!r}")
        return
    if not isinstance(accepted, int):
        problems.append(
            f"metrics.validator_accepted_cases invalid: {accepted!r}"
        )
        return

    if not isinstance(generated, int) or not 0 <= generated <= total:
        problems.append(
            f"metrics.llm_generated_cases out of range: {generated!r} "
            f"(total_cases={total})"
        )
        return
    if generated < accepted:
        problems.append(
            f"metrics.llm_generated_cases={generated} < "
            f"validator_accepted_cases={accepted} (monotonicity violated)"
        )
        return

    expected_generation_rate = round(generated / total, RATE_PRECISION)
    if stored.get("llm_generation_success_rate") != expected_generation_rate:
        problems.append(
            f"metrics.llm_generation_success_rate: "
            f"snapshot={stored.get('llm_generation_success_rate')!r} "
            f"expected={expected_generation_rate!r}"
        )

    expected_acceptance_rate = (
        round(accepted / generated, RATE_PRECISION) if generated else None
    )
    if stored.get("validator_acceptance_rate") != expected_acceptance_rate:
        problems.append(
            f"metrics.validator_acceptance_rate: "
            f"snapshot={stored.get('validator_acceptance_rate')!r} "
            f"expected={expected_acceptance_rate!r}"
        )


def _check_report(problems: list[str], snapshot: dict[str, Any]) -> None:
    """Report 关键数字与 Snapshot 一致（简单子串匹配，不做 Markdown AST）。"""
    if not REAL_LLM_REPORT_PATH.exists():
        problems.append(f"report missing: {REAL_LLM_REPORT_PATH.name}")
        return
    text = REAL_LLM_REPORT_PATH.read_text(encoding="utf-8")
    metrics = snapshot.get("metrics")
    if not isinstance(metrics, dict):
        return

    for label, key in _REPORT_COUNT_ROWS:
        value = metrics.get(key)
        if f"| {label} | - | {value} |" not in text:
            problems.append(f"report missing row: {label} = {value}")

    for label, key in _REPORT_RATE_ROWS:
        rate = metrics.get(key)
        cell = "N/A" if rate is None else f"{rate * 100:.2f}%"
        if f"| {label} | {cell} |" not in text:
            problems.append(f"report missing row: {label} = {cell}")

    version = snapshot.get("dataset", {}).get("version")
    if f"- Version: `{version}`" not in text:
        problems.append(f"report missing dataset version ({version})")


def _check_secret_safety(problems: list[str], snapshot: Any) -> None:
    """§八：Snapshot 不得含敏感信息；命中只报错类别，**不输出值**。"""
    forbidden_keys: set[str] = set()
    stack: list[Any] = [snapshot]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            forbidden_keys |= {
                str(k).lower() for k in node
                if str(k).lower() in _FORBIDDEN_KEYS
            }
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    for key in sorted(forbidden_keys):
        problems.append(f"snapshot contains forbidden key: {key!r}")

    blob = json.dumps(snapshot, ensure_ascii=False).lower()
    for fragment in _FORBIDDEN_VALUE_FRAGMENTS:
        if fragment in blob:
            problems.append(
                f"snapshot contains forbidden fragment: {fragment!r}"
            )


# ============================================================
# Entry point（§六：两条路径完全分离）
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate Text-to-SQL Real LLM baseline snapshot + report."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="offline consistency check only "
             "(no LLM, no DB, no write)",
    )
    args = parser.parse_args()
    if args.check:
        return check_existing_baseline()
    _verify_environment()
    return asyncio.run(generate_new_real_llm_baseline())


if __name__ == "__main__":
    raise SystemExit(main())