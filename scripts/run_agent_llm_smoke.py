"""Phase 7 Step 8 — Agent 真实 LLM + 真实 WMS 库存 E2E 闭环 Smoke（**手动触发；默认不执行**）。

链（**全部真实**；与 HTTP ``POST /api/agent-runs`` 是**同一** composition root）：

```text
build_agent_command_service(project)          ← api/agent_command 的 Composition Root
    → AgentCommandService.start_agent
        → 真 AgentRuntime（有界策略）
        → 真 DeepSeek Provider（settings.llm；get_default_llm_client）
        → 真只读 Tool（get_inventory → 报表等价查询 →
           独立 WMS/ERP 业务库 Engine：db/business_session，**不**用应用库）
        → 持久化（agent_run / agent_step / agent_tool_result / agent_checkpoint / agent_decision）
    → AgentReadService.get_run                 ← Read API 同一只读链
    → RuntimeRunListService.list_agent_runs    ← List API 同一只读链
    → AgentCommandService.resume_agent         （终态幂等：不重复执行已完成的 Tool）
```

Step 8 新增的验收项（**看执行记录，不看自然语言回复**）：

```text
① persistence 直读（读 API 视图**有意**不含 arguments / payload）：
     agent_decision.arguments        ⇒ 物料编码是否 == 本次请求的物料
     agent_tool_result.result_payload ⇒ success / qty / group_count / truncated
② 业务口径对照：把 persisted qty 与**原《库存现存量报表》SQL**（只读，业务库）
   的合计逐次比对；行级逐字段对照见 tests/test_get_inventory_report_sql.py
③ 数据源核验：业务库 Engine 独立于应用库（business_datasource_summary 非敏感摘要）
④ LLM / Tool 调用次数、Run 状态、Read/List 可见性
```

开启方式（默认 ``python scripts/run_agent_llm_smoke.py`` **不会**发起真实调用）：

```powershell
$env:RUN_REAL_LLM_TESTS="1"
python scripts/run_agent_llm_smoke.py            # 跑完自动清理本次 run（定向：run_id + 观测表水印）
python scripts/run_agent_llm_smoke.py --keep     # 保留本次 run（现场用 Read/List + DB 检查）
python scripts/run_agent_llm_smoke.py --material EEBMCMBK01
python scripts/run_agent_llm_smoke.py --question "查询物料 EEBMCMBK01 的库存现存量。"
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
from decimal import Decimal
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import text  # noqa: E402

from backend.app.config import settings  # noqa: E402
from backend.app.db.session import get_engine  # noqa: E402

__all__ = ["main"]

#: 默认物料：已在 Phase 7 与**原始库存现存量报表**逐行核对过的真实物料
#: （tests/test_get_inventory_report_sql.py：7 组 / 39,315.381650）。
_DEFAULT_MATERIAL: str = "EEBMCMBK01"


def _default_question(material_code: str) -> str:
    """默认问题（简单、单 Tool、可由 get_inventory 契约回答）。"""
    return f"查询物料 {material_code} 的库存现存量。"

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


def _report_baseline_total(business_engine: Any, material_code: str) -> float | None:
    """**原《库存现存量报表》SQL** 的合计（只读业务库）。

    唯一事实来源 = ``tests/test_get_inventory_report_sql.REPORT_ON_HAND_SQL``
    （Phase 7 已逐行核对过的报表等价 SQL；此处复用而非重写，避免口径漂移）。
    表缺失 / 权限 / 连接失败 ⇒ ``None``（如实报告 UNAVAILABLE，不伪造）。
    """
    try:
        from tests.test_get_inventory_report_sql import REPORT_ON_HAND_SQL
    except Exception:  # noqa: BLE001 —— 报表基线不可得则如实降级
        return None
    try:
        with business_engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            rows = conn.execute(
                text(REPORT_ON_HAND_SQL), {"material_code": material_code}
            ).mappings().all()
            conn.rollback()
        return float(sum((row["qty"] for row in rows), Decimal("0")))
    except Exception:  # noqa: BLE001
        return None


def _persisted_tool_calls(engine: Any, run_id: str) -> list[dict[str, Any]]:
    """直读 ``agent_decision``：{tool_name, arguments}（只读）。"""
    with engine.connect() as conn:
        return [
            {"tool_name": row[0], "arguments": row[1]}
            for row in conn.execute(
                text(
                    "SELECT tool_name, arguments FROM ai_ops.agent_decision "
                    "WHERE run_id = :run_id AND tool_name IS NOT NULL "
                    "ORDER BY step_index"
                ),
                {"run_id": run_id},
            ).all()
        ]


def _persisted_tool_results(engine: Any, run_id: str) -> list[dict[str, Any]]:
    """直读 ``agent_tool_result``：执行事实 + 结果载荷（只读）。"""
    with engine.connect() as conn:
        return [
            {
                "tool_name": row[0],
                "success": bool(row[1]),
                "error_code": row[2],
                "result_unavailable": bool(row[3]),
                "result_size_bytes": int(row[4] or 0),
                "payload": row[5],
            }
            for row in conn.execute(
                text(
                    "SELECT tool_name, success, error_code, result_unavailable, "
                    "result_size_bytes, result_payload FROM ai_ops.agent_tool_result "
                    "WHERE run_id = :run_id ORDER BY step_index"
                ),
                {"run_id": run_id},
            ).all()
        ]


def _final_answer(engine: Any, run_id: str) -> str | None:
    """直读最后一条决策的 content（自然语言回复；仅用于"数字是否自相矛盾"核对）。"""
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT content FROM ai_ops.agent_decision "
                "WHERE run_id = :run_id AND content IS NOT NULL "
                "ORDER BY step_index DESC LIMIT 1"
            ),
            {"run_id": run_id},
        ).scalar_one_or_none()


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
        description="Phase 7 Step 8 — Agent 真实 LLM + 真实 WMS 库存 E2E 闭环 Smoke"
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="保留本次 run 的持久化记录（默认按 run_id 定向清理）",
    )
    parser.add_argument(
        "--material",
        default=_DEFAULT_MATERIAL,
        help="Smoke 物料编码（默认 %s——已与原始报表逐行核对过）" % _DEFAULT_MATERIAL,
    )
    parser.add_argument(
        "--question",
        default=None,
        help="Smoke 问题（默认按 --material 生成库存现存量查询）",
    )
    args = parser.parse_args(argv)
    material_code: str = args.material.strip()
    question: str = args.question or _default_question(material_code)

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

    # 业务数据源（**独立**于应用库）：非敏感摘要 + 报表固定表存在性（只读）。
    from backend.app.db.business_session import (
        business_datasource_summary,
        get_business_engine,
    )
    from backend.app.tools.get_inventory import INVENTORY_REPORT_TABLES

    schema_name = configuration.schema_name
    inventory_schema = settings.inventory_tool.schema_name
    datasource = business_datasource_summary()
    business_engine = get_business_engine()
    if business_engine is None:
        table_present = False
        missing_tables = list(INVENTORY_REPORT_TABLES)
    else:
        missing_tables = [
            table
            for table in INVENTORY_REPORT_TABLES
            if not _table_exists(business_engine, inventory_schema, table)
        ]
        table_present = not missing_tables

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
        report.append("  project: %s | project schema: %s" % (project_id, schema_name))
        report.append(
            "  business datasource: configured=%s host=%s port=%s database=%s"
            % (
                datasource.get("configured"),
                datasource.get("host"),
                datasource.get("port"),
                datasource.get("database"),
            )
        )
        report.append(
            "  business engine isolated from app engine: %s"
            % (
                "YES"
                if business_engine is not None and business_engine is not engine
                else "NO"
            )
        )
        report.append(
            "  report tables present (%s schema): %s | missing: %s"
            % (
                inventory_schema,
                "YES" if table_present else "NO",
                missing_tables or "none",
            )
        )
        report.append("")
        report.append("Question: %s" % question)
        report.append("  expected material_code: %s" % material_code)
        report.append("")

        service = build_agent_command_service(project_id)
        outcome = asyncio.run(
            service.start_agent(project_id=project_id, question=question)
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
        report.append("  call counts (Read）：llm_call_count=%s | tool_call_count=%s | step_count=%s" % (
            getattr(view.run, "llm_call_count", "?"),
            getattr(view.run, "tool_call_count", "?"),
            getattr(view.run, "step_count", "?"),
        ))
        report.append("  list visible: %s (total=%d)" % (
            "PASS" if listed else "FAIL", listing.total
        ))
        if not listed:
            chain_failures.append("LIST_NOT_VISIBLE")
        report.append("")

        # ---- 持久化直读核对（读 API 视图**有意**不含 arguments / payload）----
        persisted_calls = _persisted_tool_calls(engine, run_id)
        persisted_results = _persisted_tool_results(engine, run_id)
        report.append("Persistence content (direct ai_ops read):")
        arguments_ok = False
        for call in persisted_calls:
            args_material = (call.get("arguments") or {}).get("material_code")
            report.append(
                "  agent_decision tool_name=%s arguments.material_code=%s"
                % (call.get("tool_name"), args_material)
            )
            if (
                call.get("tool_name") == "get_inventory"
                and args_material == material_code
            ):
                arguments_ok = True
        report.append(
            "  material_code correct in persisted tool call: %s"
            % ("PASS" if arguments_ok else "FAIL")
        )
        if persisted_calls and not arguments_ok:
            chain_failures.append(
                "TOOL_ARGUMENTS_MISMATCH（持久化决策的物料编码与请求不一致）"
            )

        payload_qty: Any = None
        for item in persisted_results:
            payload = item.get("payload")
            data = payload.get("data") if isinstance(payload, dict) else None
            report.append(
                "  agent_tool_result tool_name=%s success=%s error_code=%s "
                "result_unavailable=%s size=%dB"
                % (
                    item.get("tool_name"),
                    item.get("success"),
                    item.get("error_code"),
                    item.get("result_unavailable"),
                    item.get("result_size_bytes"),
                )
            )
            if isinstance(data, dict):
                if item.get("tool_name") == "get_inventory":
                    payload_qty = data.get("qty")
                report.append(
                    "    payload.data: material_code=%s qty=%s group_count=%s "
                    "groups=%s truncated=%s max_rows=%s"
                    % (
                        data.get("material_code"),
                        data.get("qty"),
                        data.get("group_count"),
                        len(data.get("groups") or ()),
                        data.get("truncated"),
                        data.get("max_rows"),
                    )
                )
        report.append("")

        # ---- 业务口径对照（原《库存现存量报表》SQL；只读）----
        baseline = (
            _report_baseline_total(business_engine, material_code)
            if business_engine is not None
            else None
        )
        report.append("Report baseline (原《库存现存量报表》SQL, read-only):")
        report.append(
            "  material=%s | report total qty=%s"
            % (
                material_code,
                baseline if baseline is not None else "UNAVAILABLE",
            )
        )
        report.append("Persistence vs report baseline:")
        if baseline is None or payload_qty is None:
            report.append(
                "  qty match: UNAVAILABLE（基线或 persisted payload 不可得——如实记录，不伪造一致）"
            )
        else:
            matched = abs(float(payload_qty) - float(baseline)) < 1e-6
            report.append(
                "  qty match: %s (persisted=%s baseline=%s)"
                % ("PASS" if matched else "FAIL", payload_qty, baseline)
            )
            if not matched:
                chain_failures.append("PERSISTED_QTY_MISMATCH")

        # ---- Agent 自然语言回复与实际 Tool Result 的自洽性（信息项）----
        final_answer = _final_answer(engine, run_id)
        if final_answer:
            snippet = " ".join(str(final_answer).split())[:200]
            report.append("Agent final answer (informational; truncated):")
            report.append("  %s" % snippet)
            if payload_qty is not None:
                compact = snippet.replace(",", "").replace(" ", "")
                head = str(int(float(payload_qty)))
                report.append(
                    "  includes tool qty integer part (%s): %s"
                    % (head, "YES" if head in compact else "NO")
                )
                if head not in compact:
                    report.append(
                        "  NOTE: 回复文本未包含 Tool 总量整数部分——如实记录（不修改 Prompt / 不伪造结果）"
                    )
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
            report.append(
                "  WMS read-only result: PASS（真实 Tool 命中独立业务库并返回数据）"
            )
        elif not datasource.get("configured") or missing_tables:
            report.append(
                "  WMS read-only result: BLOCKED（业务数据源 configured=%s；"
                "报表固定表缺失=%s ⇒ 不建表 / 不造数）"
                % (datasource.get("configured"), missing_tables or "none")
            )
        else:
            report.append(
                "  WMS read-only result: FAIL（业务数据源与报表固定表齐备，"
                "但 Tool 未成功——按任务书 §五 逐层定位）"
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
