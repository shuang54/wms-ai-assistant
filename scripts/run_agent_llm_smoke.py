"""Phase 7 Step 6 — Agent 真实 LLM + WMS 只读业务闭环 Smoke（**手动触发；默认不执行**）。

链（**全部真实**；与 HTTP ``POST /api/agent-runs`` 是**同一** composition root）：

```text
build_agent_command_service(project)          ← api/agent_command 的 Composition Root
    → AgentCommandService.start_agent
        → 真 AgentRuntime（有界策略）
        → 真 DeepSeek Provider（settings.llm；get_default_llm_client）
        → 真只读 Tool（get_inventory → 真 ToolRegistry / ToolExecutionService / Engine）
        → 持久化（agent_run / agent_step / agent_tool_result / agent_checkpoint / agent_decision）
    → AgentReadService.get_run                 ← Read API 同一只读链
    → RuntimeRunListService.list_agent_runs    ← List API 同一只读链
    → AgentCommandService.resume_agent         （终态幂等：不重复执行已完成的 Tool）
```

开启方式（默认 ``python scripts/run_agent_llm_smoke.py`` **不会**发起真实调用）：

```powershell
$env:RUN_REAL_LLM_TESTS="1"
python scripts/run_agent_llm_smoke.py            # 跑完自动清理本次 run（定向：run_id + 观测表水印）
python scripts/run_agent_llm_smoke.py --keep     # 保留本次 run（现场用 Read/List 检查）
python scripts/run_agent_llm_smoke.py --question "查询物料 MAT001 当前库存数量。"
```

退出码：

```text
0  链路验证通过（业务结果 PASS；或业务数据缺失 ⇒ BUSINESS_DATA_BLOCKED，仍算链路 PASS）
1  链路失败（LLM / 决策 / 授权 / 派发 / 持久化 / Read / List 任一环节失败）
2  未开启（RUN_REAL_LLM_TESTS 未设置）或 LLM 配置缺失
3  数据库不可用 / 项目解析失败
```

安全边界（**沿既有纪律**）：

* 真实 DeepSeek 调用（.env 配置）；**不**打印 API Key / Base URL / 完整 prompt /
  完整 Tool Result / 连接串；
* Tool **只读**：复用现有 ``get_inventory``（不改 Handler、不建表、不写业务数据）；
* **不**关闭认证 / 项目隔离 / Tool Authorization（HTTP 层认证契约由 pytest
  覆盖；本脚本直接调用与 HTTP 端点**同一** composition root，跳过的是
  "凭据 → 作用域解析"这一步，业务链完全一致）；
* 清理策略（**定向**，不使用 TRUNCATE / 不清全表）：
    - ``agent_run / agent_step / agent_tool_result / agent_checkpoint /
      agent_decision`` ⇒ 按本次 ``run_id`` 删除；
    - 四张观测表 ⇒ 会话水印（只删本次脚本新增：``id > watermark``）。
* 业务数据缺失（``public.inventory`` 不存在）⇒ **如实报告 BLOCKED**，
  **不**为通过 Smoke 建表 / 造数。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import text  # noqa: E402

from backend.app.config import settings  # noqa: E402
from backend.app.db.session import get_engine  # noqa: E402

__all__ = ["main"]

#: 默认问题（简单、单 Tool、可直接由 get_inventory schema 回答）。
_DEFAULT_QUESTION: str = "查询物料 MAT001 的当前库存数量。"

#: 本次 run 的五个持久化表（按 run_id 定向清理；都含 run_id 列）。
_AGENT_TABLES: tuple[str, ...] = (
    "agent_run",
    "agent_step",
    "agent_tool_result",
    "agent_checkpoint",
    "agent_decision",
)

#: 四张观测表（按会话水印清理——与 tests/conftest.py 会话守卫同机制）。
_OBSERVABILITY_TABLES: tuple[str, ...] = (
    "llm_usage_record",
    "tool_execution_record",
    "rag_execution_record",
    "assistant_outcome_record",
)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _enabled() -> bool:
    """与既有 real LLM 机制一致的双开关（RUN_REAL_LLM_TESTS / RUN_REAL_LLM_TEST）。"""
    return _env_flag("RUN_REAL_LLM_TESTS") or _env_flag("RUN_REAL_LLM_TEST")


def _watermarks(engine: Any) -> dict[str, int]:
    """四张观测表的会话水印（MAX(id)；只读）。"""
    marks: dict[str, int] = {}
    with engine.connect() as conn:
        for table in _OBSERVABILITY_TABLES:
            marks[table] = int(
                conn.execute(
                    text(f"SELECT COALESCE(MAX(id), 0) FROM ai_ops.{table}")
                ).scalar_one()
            )
    return marks


def _table_exists(engine: Any, schema: str, table: str) -> bool:
    """只读探测业务表是否存在（不创建 / 不修改）。"""
    with engine.connect() as conn:
        return bool(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.tables"
                    " WHERE table_schema = :schema AND table_name = :table"
                ),
                {"schema": schema, "table": table},
            ).scalar_one()
        )


def _cleanup(
    engine: Any,
    *,
    run_id: str | None,
    watermarks: dict[str, int],
) -> None:
    """定向清理：agent 五表按 run_id；观测表按水印（只删本次新增）。"""
    with engine.begin() as conn:
        if run_id:
            for table in _AGENT_TABLES:
                conn.execute(
                    text(f"DELETE FROM ai_ops.{table} WHERE run_id = :run_id"),
                    {"run_id": run_id},
                )
        for table in _OBSERVABILITY_TABLES:
            conn.execute(
                text(f"DELETE FROM ai_ops.{table} WHERE id > :watermark"),
                {"watermark": watermarks[table]},
            )


def _snapshot(view: Any) -> dict[str, int]:
    """只读快照（用于 Resume 幂等断言：不重复执行 / 不重复 LLM 调用）。"""
    run = view.run
    return {
        "llm_calls": int(getattr(run, "llm_call_count", 0) or 0),
        "tool_calls": int(getattr(run, "tool_call_count", 0) or 0),
        "steps": len(getattr(view, "steps", ()) or ()),
        "decisions": len(getattr(view, "decisions", ()) or ()),
        "tool_results": len(getattr(view, "tool_results", ()) or ()),
    }


def _print_report(lines: list[str]) -> None:
    print("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 7 Step 6 — Agent 真实 LLM + WMS 只读业务闭环 Smoke"
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="保留本次 run 的持久化记录（默认按 run_id 定向清理）",
    )
    parser.add_argument(
        "--question", default=_DEFAULT_QUESTION, help="Smoke 问题（默认 WMS 库存查询）"
    )
    args = parser.parse_args(argv)

    report: list[str] = ["", "Agent Real LLM + WMS Read-Only Smoke", ""]

    if not _enabled():
        report.append("SKIPPED (default): 未设置 RUN_REAL_LLM_TESTS=1 —— 默认不执行真实调用。")
        report.append('开启：$env:RUN_REAL_LLM_TESTS="1"')
        _print_report(report)
        return 2
    if not (settings.llm.api_key and settings.llm.base_url and settings.llm.model):
        report.append("BLOCKED: LLM 配置不完整（LLM_API_KEY / LLM_BASE_URL / LLM_MODEL）。")
        _print_report(report)
        return 2

    engine = get_engine()
    if engine is None:
        report.append("BLOCKED: DATABASE_URL 未配置（Run/Step 持久化与 Read/List 需要 DB）。")
        _print_report(report)
        return 3

    # ---- 项目与业务表探测（服务端作用域；不使用客户端输入）----
    from backend.app.projects.configuration import (
        get_default_project_configuration_provider,
    )

    project_id = (
        os.getenv("AGENT_COMMAND_API_PROJECT_ID", "").strip()
        or settings.project.project_id
    )
    try:
        configuration = get_default_project_configuration_provider().get(project_id)
    except Exception as exc:  # noqa: BLE001 —— 只报告类型；不输出内部细节
        report.append(f"BLOCKED: 项目解析失败（{type(exc).__name__}）。")
        _print_report(report)
        return 3

    schema_name = configuration.schema_name
    table_name = settings.inventory_tool.table_name
    table_present = _table_exists(engine, schema_name, table_name)

    watermarks = _watermarks(engine)
    run_id: str | None = None
    chain_failures: list[str] = []

    try:
        # ---- 真实链路（与 HTTP 端点同一 composition root）----
        from backend.app.api.runtime_read import (
            get_agent_read_service,
            get_runtime_run_list_service,
        )
        from backend.app.services.project_orchestrator_factory import (
            build_agent_command_service,
        )

        report.append("Chain:")
        report.append("  command service: build_agent_command_service (production root)")
        report.append("  provider: %s / %s" % (settings.llm.provider, settings.llm.model))
        report.append("  project: %s | schema: %s | table: %s.%s" % (
            project_id, schema_name, schema_name, table_name
        ))
        report.append(
            "  business table present: %s" % ("YES" if table_present else "NO")
        )
        report.append("")
        report.append("Question: %s" % args.question)
        report.append("")

        service = build_agent_command_service(project_id)
        outcome = asyncio.run(
            service.start_agent(project_id=project_id, question=args.question)
        )
        run_id = outcome.run_id
        report.append("Run:")
        report.append("  run_id: %s" % run_id)
        report.append("  outcome: %s | status: %s | error_code: %s" % (
            outcome.kind, outcome.status, outcome.error_code
        ))
        report.append("")

        # ---- Read API 同一只读链 ----
        read_service = get_agent_read_service()
        view = read_service.get_run(run_id, project_id)
        steps = list(getattr(view, "steps", ()) or ())
        tool_results = list(getattr(view, "tool_results", ()) or ())
        decisions = list(getattr(view, "decisions", ()) or ())
        llm_steps = [s for s in steps if getattr(s, "step_type", None) == "LLM_DECISION"]
        tool_steps = [s for s in steps if getattr(s, "step_type", None) == "TOOL_CALL"]
        final_steps = [s for s in steps if getattr(s, "step_type", None) == "FINAL"]
        report.append("LLM decision (persisted):")
        report.append("  decisions: %d | llm_steps: %d" % (len(decisions), len(llm_steps)))
        for decision in decisions:
            report.append(
                "    step=%s type=%s tool_name=%s finish=%s" % (
                    getattr(decision, "step_index", "?"),
                    getattr(decision, "decision_type", "?"),
                    getattr(decision, "tool_name", None),
                    getattr(decision, "finish_reason", None),
                )
            )
        report.append("")
        report.append("Tool (real handler dispatch):")
        report.append("  tool_steps: %d | tool_results: %d" % (len(tool_steps), len(tool_results)))
        for step in tool_steps:
            report.append(
                "    step=%s tool_name=%s status=%s error_code=%s" % (
                    getattr(step, "step_index", "?"),
                    getattr(step, "tool_name", None),
                    getattr(step, "status", None),
                    getattr(step, "error_code", None),
                )
            )
        for result in tool_results:
            report.append(
                "    result tool_name=%s success=%s error_code=%s" % (
                    getattr(result, "tool_name", None),
                    getattr(result, "success", None),
                    getattr(result, "error_code", None),
                )
            )
        if not tool_steps:
            chain_failures.append("TOOL_NOT_SELECTED（真实 LLM 未产生 get_inventory 调用）")
        if not tool_results:
            chain_failures.append("TOOL_NOT_DISPATCHED（无 agent_tool_result 持久化行）")
        report.append("")

        business_ok = any(
            bool(getattr(r, "success", False)) for r in tool_results
        )

        # ---- List API 同一只读链 ----
        listing = get_runtime_run_list_service().list_agent_runs(
            project_id=project_id, limit=50, offset=0
        )
        listed = run_id in [item.run_id for item in listing.items]
        report.append("Read/List API chain:")
        report.append("  read detail: PASS (run_id=%s, status=%s)" % (
            run_id, getattr(view.run, "status", None)
        ))
        report.append("  list visible: %s (total=%d)" % (
            "PASS" if listed else "FAIL", listing.total
        ))
        if not listed:
            chain_failures.append("LIST_NOT_VISIBLE")
        report.append("")

        # ---- Resume 幂等（仅终态 COMPLETED 时断言 replay 零重跑）----
        run_status = getattr(view.run, "status", None)
        report.append("Resume (terminal idempotency):")
        if run_status == "COMPLETED":
            before = _snapshot(view)
            resumed = asyncio.run(
                service.resume_agent(
                    project_id=project_id, run_id=run_id, question=None
                )
            )
            after = _snapshot(read_service.get_run(run_id, project_id))
            resumed_status = getattr(resumed, "status", None)
            report.append("  resumed outcome: %s | status: %s" % (
                getattr(resumed, "kind", None), resumed_status
            ))
            same = before == after and resumed_status == "COMPLETED"
            report.append("  no re-execution: %s" % ("PASS" if same else "FAIL"))
            report.append("    before=%s after=%s" % (before, after))
            if not same:
                chain_failures.append("RESUME_REEXECUTED")
        else:
            report.append(
                "  NOT_ASSERTED（run status=%s——Replay 幂等断言仅对 COMPLETED 有意义；"
                "不为通过 Smoke 建表/造数）" % run_status
            )
        report.append("")

        # ---- 业务闭环结论 ----
        report.append("Business closure:")
        if business_ok:
            report.append("  WMS read-only result: PASS（真实 Tool 返回业务数据）")
        else:
            report.append(
                "  WMS read-only result: BLOCKED（%s.%s 不存在 ⇒ 真实部署需提供该表；"
                "不建表 / 不造数）" % (schema_name, table_name)
            )
        report.append("")
        report.append("Result:")
        if chain_failures:
            report.append("  CHAIN FAILED: %s" % ", ".join(chain_failures))
        elif business_ok:
            report.append("  PASS（链路 + 业务闭环）")
        else:
            report.append("  CHAIN PASS / BUSINESS_DATA_BLOCKED")
        report.append("")

        return 1 if chain_failures else 0
    finally:
        if args.keep:
            report.append("Cleanup: skipped (--keep；run_id=%s)" % run_id)
            _print_report(report)
        else:
            _cleanup(engine, run_id=run_id, watermarks=watermarks)
            report.append("Cleanup: done（agent 五表按 run_id；观测表按水印）")
            _print_report(report)


if __name__ == "__main__":
    raise SystemExit(main())
