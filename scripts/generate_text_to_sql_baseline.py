"""Generate the Text-to-SQL Baseline snapshot + report（Phase 3.9.4）。

用法（仓库根目录执行）：

```powershell
python scripts/generate_text_to_sql_baseline.py           # 生成 / 覆盖产物
python scripts/generate_text_to_sql_baseline.py --check   # 只比对，不写文件
```

流程严格遵循任务书 §二十四：

```text
load dataset（Phase 3.9.3，不修改 case）
    ↓
deterministic Fake Generator（LLM 层被替换为固定 SQL）
    ↓
Phase 3.9.3 TextToSQLEvaluationRunner（真实 Selector / Composer / Filter / Validator）
    ↓
calculate_baseline_metrics()
    ↓
snapshot JSON + markdown report
```

纪律：

- **不修改 Prompt / Generator / Validator / Executor / Selector / Semantic**；
- 默认不需要网络、不需要真实 LLM、不写业务库（仅写两个产物文件）；
- `--check` 模式下连产物文件也不写。
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
    BASELINE_REPORT_PATH,
    BASELINE_SNAPSHOT_PATH,
    PHASE,
    collect_environment_info,
    run_baseline,
)
from backend.app.services.text_to_sql_evaluation_service import (  # noqa: E402
    StaticEvaluationContextResolver,
    load_text_to_sql_regression_dataset,
)
from backend.app.services.text_to_sql_service import TextToSQLResult  # noqa: E402
from scripts._text_to_sql_offline_bindings import (  # noqa: E402
    OFFLINE_SCHEMA_BY_PROJECT,
    build_offline_project_bindings,
)

RESPONSES_PATH = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "text_to_sql"
    / "baselines"
    / "deterministic_generator_responses.json"
)

#: Baseline 对比时忽略的字段（非确定性 / 环境相关）
_IGNORED_COMPARE_KEYS: tuple[str, ...] = ("generated_at", "environment")


# ============================================================
# Deterministic Generator（LLM 层替身）
# ============================================================

class DeterministicGenerator:
    """确定性 Fake Generator：按 (question, schema_name) 返回固定 SQL。

    查找键带 schema_name，是因为 Project A/B 隔离 case 共用同一个问句。
    """

    def __init__(self, responses: dict[tuple[str, str], str]) -> None:
        self._responses = dict(responses)
        self.calls: int = 0

    async def generate(
        self, question, *, database_context, allowed_tables=None,
        schema=None, max_rows=1000,
    ):
        self.calls += 1
        schema_name = getattr(schema, "schema_name", "") or ""
        sql = self._responses.get((question, schema_name))
        if sql is None:  # dataset 变更 ⇢ 立刻暴露，而不是静默回退
            raise KeyError(f"no deterministic response for ({question!r}, "
                           f"{schema_name!r})")
        return TextToSQLResult(
            question=question, sql=sql, attempts=1, validated=True,
            referenced_tables=tuple(allowed_tables or ()),
        )


def load_responses(cases) -> dict[tuple[str, str], str]:
    """Responses fixture（case_id → sql）转换为 Generator 查找表。"""
    raw = json.loads(RESPONSES_PATH.read_text(encoding="utf-8"))
    by_case_id = {
        entry["case_id"]: entry["sql"]
        for entry in raw.get("responses", [])
        if isinstance(entry, dict)
    }
    missing = [c.case_id for c in cases if c.case_id not in by_case_id]
    if missing:
        raise KeyError(
            f"deterministic responses missing for cases: {missing}"
        )
    responses: dict[tuple[str, str], str] = {}
    for case in cases:
        schema_name = OFFLINE_SCHEMA_BY_PROJECT.get(case.project_id or "", "")
        responses[(case.question, schema_name)] = by_case_id[case.case_id]
    return responses


# ============================================================
# Environment（安全读取 PostgreSQL 版本；失败一律 unavailable）
# ============================================================

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
    except Exception:  # 不可达 → 不阻塞 Baseline
        return "unavailable"


# ============================================================
# Main
# ============================================================

async def _run(*, check: bool) -> int:
    cases = load_text_to_sql_regression_dataset()
    generator = DeterministicGenerator(load_responses(cases))
    environment = collect_environment_info(database=_detect_database_version())

    baseline = await run_baseline(
        generator=generator,
        context_resolver=StaticEvaluationContextResolver(
            bindings=build_offline_project_bindings()
        ),
        cases=cases,
        phase=PHASE,
        environment=environment,
    )

    print(baseline.render_summary())
    print()

    if check:
        return _compare(baseline.to_snapshot_dict())

    _write_snapshot(baseline.to_snapshot_dict())
    _write_report(baseline.render_report())
    print(f"Snapshot written: {BASELINE_SNAPSHOT_PATH.relative_to(_REPO_ROOT)}")
    print(f"Report written:   {BASELINE_REPORT_PATH.relative_to(_REPO_ROOT)}")
    return 0


def _compare(snapshot: dict[str, Any]) -> int:
    if not BASELINE_SNAPSHOT_PATH.exists():
        print(f"FAIL: snapshot not found: {BASELINE_SNAPSHOT_PATH}")
        return 1
    existing = json.loads(BASELINE_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    diffs = [
        key
        for key in ("metrics", "cases", "phase", "dataset")
        if existing.get(key) != snapshot.get(key)
    ]
    ignored = [
        key for key in _IGNORED_COMPARE_KEYS if existing.get(key) != snapshot.get(key)
    ]
    if diffs:
        print("FAIL: baseline drift detected")
        for key in diffs:
            print(f"  - {key} differs")
        return 1
    print("OK: baseline snapshot is up to date")
    for key in ignored:
        print(f"  - ignored field differs: {key}")
    return 0


def _write_snapshot(snapshot: dict[str, Any]) -> None:
    BASELINE_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_SNAPSHOT_PATH.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_report(report: str) -> None:
    BASELINE_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_REPORT_PATH.write_text(report, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate Text-to-SQL baseline snapshot + report."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare metrics against the committed snapshot without writing",
    )
    args = parser.parse_args()
    return asyncio.run(_run(check=args.check))


if __name__ == "__main__":
    raise SystemExit(main())
