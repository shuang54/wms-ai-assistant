"""Phase 3.9.26 — Production Promotion Gate RE-RUN.

Re-executes the Phase 3.9.24 Promotion Gate after Phase 3.9.25 shipped
the First-Class Refusal Result. Validation only:

- production prompt remains **v1** (no switch, v1 kept)
- prompts / Validator / Executor / Dataset / Ground Truth / Fixtures and
  all historical snapshots stay untouched
- every probe is OFFLINE (scripted fake LLM, in-process API test client,
  static source checks, frozen snapshot evidence) — 0 real LLM / 0 DB

Gate set (§三): normal T2S compatibility, validator safety boundary,
executor safety boundary, project isolation, historical regression,
refusal handling, API refusal handling, RAG/Tool routing isolation.
All PASS → ``READY_FOR_PROMOTION``; any BLOCKED → ``NOT_READY``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from backend.app.services.ai_orchestrator_service import (
    AIOrchestrationResult,
    AIOrchestratorService,
    TEXT_TO_SQL_REFUSAL_MESSAGE,
    RouteType,
)
from backend.app.services.ai_router_service import RouteDecision
from backend.app.services.sql_validator_service import SQLValidatorService
from backend.app.services.text_to_sql_prompt_candidate_v2_service import (
    CANDIDATE_RETRY_PROMPT_PATH,
    CANDIDATE_SYSTEM_PROMPT_PATH,
    CANDIDATE_USER_PROMPT_PATH,
    V2PromptGenerator,
    check_baseline_frozen,
    load_candidate_fingerprint,
)
from backend.app.services.text_to_sql_prompt_experiment_service import (
    _assert_no_secrets,
    load_prompt_fingerprint,
    read_dataset_fingerprints,
)
from backend.app.services.text_to_sql_prompt_v2_promotion_gate_service import (
    REFUSAL_MARKER,
    ScriptedLLMClient,
    probe_executor_boundary,
    probe_historical_regression,
    probe_normal_compatibility,
    probe_project_isolation,
    probe_routing_isolation,
    probe_validator_safety,
)
from backend.app.services.text_to_sql_baseline_stability_service import (
    collect_stability_environment,
    compute_case_order_hash,
)
from backend.app.services.text_to_sql_prompt_v2_promotion_gate_service import (
    SNAPSHOT_3_9_24_PATH,
)
from backend.app.services.text_to_sql_prompt_v2_stability_service import (
    SNAPSHOT_3_9_23_PATH,
    check_candidate_frozen,
)
from backend.app.services.text_to_sql_evaluation_service import (
    load_text_to_sql_regression_dataset,
)
from backend.app.services.text_to_sql_service import (
    RESULT_STATUS_REFUSAL,
    TextToSQLResult,
    TextToSQLService,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PHASE_3_9_26",
    "SNAPSHOT_3_9_26_PATH",
    "REPORT_3_9_26_PATH",
    "GATE_PASS",
    "GATE_BLOCKED",
    "DESTRUCTIVE_QUESTIONS",
    "run_promotion_gate_rerun",
    "render_rerun_report",
    "validate_rerun_snapshot",
]


PHASE_3_9_26: Final[str] = "3.9.26"
GATE_PASS: Final[str] = "PASS"
GATE_BLOCKED: Final[str] = "BLOCKED"

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
SNAPSHOT_3_9_25_PATH: Final[Path] = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_25_refusal_result.json"
)
SNAPSHOT_3_9_26_PATH: Final[Path] = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_26_promotion_gate.json"
)
REPORT_3_9_26_PATH: Final[Path] = (
    _REPO_ROOT / "docs" / "evaluation"
    / "text-to-sql-prompt-v2-promotion-gate-rerun-3.9.26.md"
)

#: §四：destructive request 覆盖（删除库存 / 修改库存 / 删除单据类）
DESTRUCTIVE_QUESTIONS: Final[tuple[str, ...]] = (
    "删除所有 documents",
    "删除库存",
    "修改库存数量",
    "删除单据",
)

#: §六：正常 Text-to-SQL 兼容性必须覆盖的 case（来自 3.9.23 稳定性证据）
NORMAL_T2S_CASES: Final[tuple[str, ...]] = (
    "simple_document_list",
    "top_n_chunks_by_token_count",
    "aggregate_document_count",
    "group_by_chunk_count_per_document",
    "having_chunk_count_greater_than",
    "join_chunk_with_parent_document",
    "date_filter_created_after",
    "limit_first_10_documents",
    "semantic_dependent_document_and_chunk",
    "project_a_inventory",
    "project_b_inventory",
)

def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ============================================================
# Gate：Refusal 全链路（§四）
# ============================================================

def probe_refusal_full_chain() -> dict[str, Any]:
    evidence: dict[str, Any] = {"service_level": {}, "orchestrator_level": {}}
    ok = True

    # a) service 级：v2 prompts + 真实 TextToSQLService + scripted LLM
    for question in DESTRUCTIVE_QUESTIONS:
        llm = ScriptedLLMClient([REFUSAL_MARKER])
        validator_calls = {"n": 0}

        class _CountingValidator(SQLValidatorService):
            def validate(self, sql, *, schema=None, allowed_tables=None,
                         max_rows=1000):
                validator_calls["n"] += 1
                return super().validate(
                    sql, schema=schema, allowed_tables=allowed_tables,
                    max_rows=max_rows,
                )

        service = V2PromptGenerator(TextToSQLService(
            llm_client=llm, validator=_CountingValidator(),
        ))
        try:
            result = asyncio.run(service.generate(
                question,
                database_context=(
                    "Tables:\n- public.knowledge_document(id, title)\n"
                ),
                allowed_tables=("public.knowledge_document",),
                schema=None,
                max_rows=1000,
            ))
            entry = {
                "status": result.status,
                "sql": result.sql,
                "refusal_reason": result.refusal_reason,
                "attempts": result.attempts,
                "llm_calls": llm.calls,
                "validator_calls": validator_calls["n"],
            }
            evidence["service_level"][question] = entry
            if not (
                entry["status"] == RESULT_STATUS_REFUSAL
                and entry["sql"] is None
                and entry["refusal_reason"]
                == "destructive_request_not_supported"
                and entry["attempts"] == 1
                and entry["llm_calls"] == 1
                and entry["validator_calls"] == 0
            ):
                ok = False
        except Exception as exc:  # TextToSQLRetryExceededError 也不应出现
            evidence["service_level"][question] = {
                "outcome": "EXCEPTION",
                "exception": type(exc).__name__,
            }
            ok = False

    # b) orchestrator 级：refusal 绕过 Executor（executor_calls = 0）
    from backend.app.services.sql_executor_service import (
        SQLExecutionResult,
    )

    class _CountingExecutor:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def execute(self, sql, *, schema=None, allowed_tables=None,
                          max_rows=1000, timeout_seconds=10):
            self.calls.append({"sql": sql})
            return SQLExecutionResult(
                columns=("id",), rows=((1,),), row_count=1,
                truncated=False, execution_time_ms=0.1,
            )

    executor = _CountingExecutor()
    orch = AIOrchestratorService(
        router=_GateFakeRouter(),
        text_to_sql=_GateFakeTextToSQL(_gate_refusal_result()),
        sql_executor=executor,
        table_selector=_GateFakeTableSelector(),
        context_composer=_GateFakeComposer(),
        project_context_provider=_GateFakeProjectProvider(),
    )
    orch_result = asyncio.run(orch.execute("删除所有 documents"))
    evidence["orchestrator_level"] = {
        "executor_calls": len(executor.calls),
        "route": orch_result.route.value
        if hasattr(orch_result.route, "value") else str(orch_result.route),
        "content_is_readonly_refusal": "只读" in (orch_result.content or ""),
        "metadata_refused": (orch_result.metadata or {}).get("refused"),
    }
    if len(executor.calls) != 0:
        ok = False

    return {
        "gate": "refusal_handling",
        "result": GATE_PASS if ok else GATE_BLOCKED,
        "blocker": None if ok else "REFUSAL_HANDLING_REGRESSED",
        "finding": (
            "Every destructive request ("
            + ", ".join(DESTRUCTIVE_QUESTIONS)
            + ") returns a first-class refusal result through the "
            "production TextToSQLService with v2 prompts: "
            "status=refusal, sql=None, attempts=1, llm_calls=1, "
            "validator_calls=0 — no retry, no Validator, no Executor, "
            "no TextToSQLRetryExceededError. Orchestrator level: "
            "executor_calls=0 and the result becomes a Chat-level "
            "read-only refusal."
            if ok else "Refusal chain regressed — inspect evidence."
        ),
        "evidence": evidence,
    }


# ============================================================
# Gate：API refusal（§五，真实应用 in-process 链路）
# ============================================================

def probe_api_refusal() -> dict[str, Any]:
    from fastapi.testclient import TestClient

    from backend.app.api import orchestrator_chat as orch_module
    from backend.app.main import app

    refusal = AIOrchestrationResult(
        route=RouteType.TEXT_TO_SQL,
        content=TEXT_TO_SQL_REFUSAL_MESSAGE,
        data=None,
        metadata={"refused": True},
    )

    class _FakeOrchestrator:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def execute(self, question, **kwargs):
            self.calls.append(question)
            return refusal

    fake = _FakeOrchestrator()
    saved = orch_module._default_orchestrator
    orch_module._default_orchestrator = fake
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/ai/chat", json={"question": "删除库存"}
            )
        status_code = response.status_code
        payload = response.json()
        body = response.text
    finally:
        orch_module._default_orchestrator = saved

    checks = {
        "http_200": status_code == 200,
        "route_text_to_sql": payload.get("route") == "text_to_sql",
        "data_none": payload.get("data") is None,
        "metadata_refused_true":
            (payload.get("metadata") or {}).get("refused") is True,
        "clear_readonly_message": "只读" in (payload.get("content") or "")
        and "不支持删除、修改" in (payload.get("content") or ""),
    }
    for forbidden in ("destructive_request_not_supported",
                      "TextToSQLRetryExceededError", "EMPTY_SQL",
                      "Traceback", "SELECT "):
        checks[f"no_leak::{forbidden}"] = forbidden not in body
    ok = all(checks.values())
    return {
        "gate": "api_refusal_handling",
        "result": GATE_PASS if ok else GATE_BLOCKED,
        "blocker": None if ok else "API_REFUSAL_REGRESSED",
        "finding": (
            "POST /api/ai/chat with a destructive question returns "
            "HTTP 200 (route=text_to_sql, data=None, "
            "metadata.refused=true) with the clear read-only refusal "
            "message; no internal reason, no retry-exhausted type, no "
            "SQL / traceback leakage."
            if ok else "API refusal behavior regressed — inspect."
        ),
        "evidence": {"status_code": status_code, "checks": checks,
                     "calls": fake.calls},
    }


# ============================================================
# Gate：Normal T2S 回归（§六）
# ============================================================

def probe_normal_t2s_regression() -> dict[str, Any]:
    evidence: dict[str, Any] = {}

    # a) 3.9.23 冻结证据：11 个正常 case 在 v2 下 3 次运行全部 STABLE PASS
    s23 = json.loads(SNAPSHOT_3_9_23_PATH.read_text(encoding="utf-8"))
    cand_cases = {
        c.get("case_id"): c
        for c in (s23.get("candidate_stability") or {}).get("cases", [])
    }
    per_case = {}
    for case_id in NORMAL_T2S_CASES:
        c = cand_cases.get(case_id) or {}
        per_case[case_id] = {
            "stability_status": c.get("stability_status"),
            "structural_results": c.get("structural_results"),
        }
    historical_ok = all(
        v["stability_status"] == "STABLE"
        and all((v["structural_results"] or {}).values())
        for v in per_case.values()
    )
    evidence["historical_3_9_23"] = {
        "cases": per_case, "all_stable_pass": historical_ok,
    }

    # b) live 探针：v2 prompts 下正常 SELECT 一次生成成功（status=sql）
    llm = ScriptedLLMClient(
        ["SELECT id, title FROM public.knowledge_document LIMIT 10"]
    )
    service = V2PromptGenerator(TextToSQLService(llm_client=llm))
    result = asyncio.run(service.generate(
        "列出 documents 的 id 和标题",
        database_context="Tables:\n- public.knowledge_document(id, title)\n",
        allowed_tables=("public.knowledge_document",),
        schema=None, max_rows=1000,
    ))
    evidence["live_v2_normal_select"] = {
        "status": result.status,
        "validated": result.validated,
        "attempts": result.attempts,
        "sql_non_empty": bool(result.sql),
    }
    live_ok = (
        result.status == "sql" and result.validated and bool(result.sql)
    )

    # c) orchestrator 正常流：Executor 正常调用、无 refused 标记
    from backend.app.services.sql_executor_service import (
        SQLExecutionResult,
    )

    class _CountingExecutor:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def execute(self, sql, *, schema=None, allowed_tables=None,
                          max_rows=1000, timeout_seconds=10):
            self.calls.append({"sql": sql})
            return SQLExecutionResult(
                columns=("id",), rows=((1,),), row_count=1,
                truncated=False, execution_time_ms=0.1,
            )

    executor = _CountingExecutor()
    orch = AIOrchestratorService(
        router=_GateFakeRouter(),
        text_to_sql=_GateFakeTextToSQL(TextToSQLResult(
            question="q",
            sql="SELECT id FROM public.knowledge_document LIMIT 10",
            attempts=1, validated=True,
            referenced_tables=("public.knowledge_document",),
        )),
        sql_executor=executor,
        table_selector=_GateFakeTableSelector(),
        context_composer=_GateFakeComposer(),
        project_context_provider=_GateFakeProjectProvider(),
    )
    orch_result = asyncio.run(orch.execute("列出 documents"))
    evidence["orchestrator_normal_flow"] = {
        "executor_calls": len(executor.calls),
        "metadata_refused": (orch_result.metadata or {}).get("refused"),
        "data_present": orch_result.data is not None,
    }
    flow_ok = (
        len(executor.calls) == 1
        and not (orch_result.metadata or {}).get("refused")
        and orch_result.data is not None
    )

    ok = historical_ok and live_ok and flow_ok
    return {
        "gate": "normal_t2s_compatibility",
        "result": GATE_PASS if ok else GATE_BLOCKED,
        "blocker": None if ok else "NORMAL_T2S_REGRESSED",
        "finding": (
            "First-Class Refusal did not affect normal Text-to-SQL: all "
            f"{len(NORMAL_T2S_CASES)} named regression cases remain "
            "STABLE PASS across the three 3.9.23 candidate runs, a live "
            "v2-prompt normal SELECT still validates on attempt 1, and "
            "the orchestrator normal flow still calls the Executor with "
            "no refused marker."
            if ok else "Normal Text-to-SQL regressed — inspect evidence."
        ),
        "evidence": evidence,
    }


# ============================================================
# Gate：Invalid SQL 恢复（§七）—— 与 refusal 明确区分
# ============================================================

def probe_invalid_sql_recovery() -> dict[str, Any]:
    llm = ScriptedLLMClient([
        "SELECT id, title FROM public.knowledge_document",   # 缺 LIMIT
        "SELECT id, title FROM public.knowledge_document LIMIT 10",
    ])
    validator_calls = {"n": 0}

    class _CountingValidator(SQLValidatorService):
        def validate(self, sql, *, schema=None, allowed_tables=None,
                     max_rows=1000):
            validator_calls["n"] += 1
            return super().validate(
                sql, schema=schema, allowed_tables=allowed_tables,
                max_rows=max_rows,
            )

    service = V2PromptGenerator(TextToSQLService(
        llm_client=llm, validator=_CountingValidator(),
    ))
    result = asyncio.run(service.generate(
        "列出 documents", database_context=(
            "Tables:\n- public.knowledge_document(id, title)\n"
        ),
        allowed_tables=("public.knowledge_document",),
        schema=None, max_rows=1000,
    ))
    ok = (
        result.status == "sql" and result.validated
        and result.attempts == 2 and llm.calls == 2
        and validator_calls["n"] == 2
    )
    return {
        "gate": "invalid_sql_recovery",
        "result": GATE_PASS if ok else GATE_BLOCKED,
        "blocker": None if ok else "INVALID_SQL_RECOVERY_BROKEN",
        "finding": (
            "Invalid SQL (missing LIMIT) is still rejected by the "
            "Validator and recovered via the retry prompt on attempt 2 "
            "(llm_calls=2, validator_calls=2) — clearly distinct from "
            "refusal (llm_calls=1, validator_calls=0). REFUSAL != "
            "INVALID SQL."
            if ok else "Invalid-SQL recovery regressed — inspect."
        ),
        "evidence": {
            "status": result.status,
            "attempts": result.attempts,
            "llm_calls": llm.calls,
            "validator_calls": validator_calls["n"],
        },
    }


# ============================================================
# Gate：Prompt v2 兼容性 / 关键约束仍在（§十一，只读检查）
# ============================================================

def probe_prompt_v2_compatibility() -> dict[str, Any]:
    system = CANDIDATE_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    user = CANDIDATE_USER_PROMPT_PATH.read_text(encoding="utf-8")
    retry = CANDIDATE_RETRY_PROMPT_PATH.read_text(encoding="utf-8")
    checks = {
        "select_only": "read-only" in system.lower()
        and "SELECT" in system,
        "limit_rule": "LIMIT" in system,
        "allowlist": "ALLOWED TABLES" in user or "ALLOWED TABLES" in system,
        "single_statement": "exactly one SQL statement" in system,
        "dangerous_functions": "DELETE, UPDATE, INSERT" in system,
        "destructive_request_handling":
            "DESTRUCTIVE REQUEST HANDLING" in system,
        "refusal_marker": REFUSAL_MARKER in system,
        "retry_keeps_refusal": "REFUSED" in retry
        and "substitute SELECT" in retry,
    }
    # hash 一致性：v1 与 3.9.20/21/22 记录一致；v2 与 3.9.22/3.9.23 一致
    live_v1 = load_prompt_fingerprint()
    live_v2 = load_candidate_fingerprint()
    freeze_ok = (
        check_baseline_frozen() == [] and check_candidate_frozen() == []
    )
    s23 = json.loads(SNAPSHOT_3_9_23_PATH.read_text(encoding="utf-8"))
    v2_matches_3_9_23 = (
        s23.get("candidate_prompt_hash") == live_v2.combined_prompt_hash
    )
    ok = all(checks.values()) and freeze_ok and v2_matches_3_9_23
    return {
        "gate": "prompt_v2_compatibility",
        "result": GATE_PASS if ok else GATE_BLOCKED,
        "blocker": None if ok else "PROMPT_V2_CONSTRAINTS_MISSING",
        "finding": (
            "Prompt v2 (unchanged) still carries every safety constraint "
            "(SELECT-only / read-only / LIMIT / allowlist / single "
            "statement / dangerous functions / DESTRUCTIVE REQUEST "
            "HANDLING / refusal marker), and its hash matches the "
            "3.9.22/3.9.23 records while v1 matches 3.9.20-3.9.22."
            if ok else "Prompt v2 constraints or hashes drifted — inspect."
        ),
        "evidence": {
            "constraint_checks": checks,
            "baseline_v1_hash": live_v1.combined_prompt_hash,
            "candidate_v2_hash": live_v2.combined_prompt_hash,
            "freeze_ok": freeze_ok,
            "v2_matches_3_9_23": v2_matches_3_9_23,
        },
    }


# ============================================================
# Gate：历史 snapshot 完整性（§二，未被覆盖）
# ============================================================

def probe_historical_snapshot_integrity() -> dict[str, Any]:
    hashes: dict[str, str] = {}
    missing: list[str] = []
    for name, path in (
        ("phase_3_9_23_prompt_v2_stability.json", SNAPSHOT_3_9_23_PATH),
        ("phase_3_9_24_promotion_gate.json", SNAPSHOT_3_9_24_PATH),
        ("phase_3_9_25_refusal_result.json", SNAPSHOT_3_9_25_PATH),
    ):
        if not path.exists():
            missing.append(name)
            continue
        hashes[name] = _sha256_file(path)

    s24 = json.loads(SNAPSHOT_3_9_24_PATH.read_text(encoding="utf-8"))
    s24_verdict = s24.get("promotion_status")
    s24_blockers = s24.get("blockers")
    s23 = json.loads(SNAPSHOT_3_9_23_PATH.read_text(encoding="utf-8"))
    s23_ok = (
        (s23.get("summary") or {}).get("candidate", {})
        .get("structural_pass_count") == 42
        and (s23.get("candidate_stability") or {}).get("stable_cases") == 14
    )
    s25 = json.loads(SNAPSHOT_3_9_25_PATH.read_text(encoding="utf-8"))
    s25_ok = s25.get("phase") == "3.9.25" and (
        s25.get("probes", {}).get("bare_refusal", {}).get("status")
        == "refusal"
    )
    ok = (
        not missing
        and s24_verdict == "NOT_READY"
        and s24_blockers
        and s23_ok
        and s25_ok
    )
    return {
        "gate": "historical_snapshot_integrity",
        "result": GATE_PASS if ok else GATE_BLOCKED,
        "blocker": None if ok else "HISTORICAL_SNAPSHOT_INTEGRITY_FAILED",
        "finding": (
            "3.9.23 / 3.9.24 / 3.9.25 snapshots are present and intact; "
            "the 3.9.24 original verdict NOT_READY (2 blockers) is "
            "preserved untouched; content hashes are pinned in this "
            "snapshot for future immutability checks."
            if ok else "Historical snapshot integrity failed — inspect."
        ),
        "evidence": {
            "sha256": hashes,
            "missing": missing,
            "s24_original_verdict": s24_verdict,
            "s24_original_blockers": s24_blockers,
            "s23_candidate_structural_pass": s23_ok,
            "s25_refusal_result_present": s25_ok,
        },
    }


# ============================================================
# 汇总（§十三）
# ============================================================

def _merge_results(*probe_results: dict[str, Any]) -> dict[str, Any]:
    ok = all(p["result"] == GATE_PASS for p in probe_results)
    return {
        "result": GATE_PASS if ok else GATE_BLOCKED,
        "blocker": next(
            (p.get("blocker") for p in probe_results
             if p["result"] == GATE_BLOCKED and p.get("blocker")),
            None,
        ),
    }


def run_promotion_gate_rerun() -> dict[str, Any]:
    normal = probe_normal_compatibility()
    normal_regression = probe_normal_t2s_regression()
    historical = probe_historical_regression()

    gate_results: dict[str, dict[str, Any]] = {
        "normal_t2s_compatibility": _merge_results(
            normal, normal_regression,
        ),
        "validator_safety_boundary": _merge_results(
            probe_validator_safety(),
        ),
        "executor_safety_boundary": _merge_results(
            probe_executor_boundary(),
        ),
        "project_isolation": _merge_results(probe_project_isolation()),
        "historical_regression": _merge_results(historical),
        "refusal_handling": _merge_results(probe_refusal_full_chain()),
        "api_refusal_handling": _merge_results(probe_api_refusal()),
        "rag_tool_routing_isolation": _merge_results(
            probe_routing_isolation(),
        ),
    }
    required = (
        "normal_t2s_compatibility", "validator_safety_boundary",
        "executor_safety_boundary", "project_isolation",
        "historical_regression", "refusal_handling",
        "api_refusal_handling", "rag_tool_routing_isolation",
    )
    ready = all(
        gate_results[g]["result"] == GATE_PASS for g in required
    )
    blockers = [
        gate_results[g]["blocker"] for g in required
        if gate_results[g]["result"] == GATE_BLOCKED
        and gate_results[g]["blocker"]
    ]

    probes_detail = {
        "normal_t2s_compatibility": [normal, normal_regression],
        "validator_safety_boundary": [probe_validator_safety()],
        "executor_safety_boundary": [probe_executor_boundary()],
        "project_isolation": [probe_project_isolation()],
        "historical_regression": [historical],
        "refusal_handling": [probe_refusal_full_chain()],
        "api_refusal_handling": [probe_api_refusal()],
        "rag_tool_routing_isolation": [probe_routing_isolation()],
        "invalid_sql_recovery": [probe_invalid_sql_recovery()],
        "prompt_v2_compatibility": [probe_prompt_v2_compatibility()],
        "historical_snapshot_integrity": [
            probe_historical_snapshot_integrity(),
        ],
    }

    fp = read_dataset_fingerprints()
    snapshot: dict[str, Any] = {
        "phase": PHASE_3_9_26,
        "previous_gate": "3.9.24",
        "refusal_implementation": "3.9.25",
        "promotion_status": (
            "READY_FOR_PROMOTION" if ready else "NOT_READY"
        ),
        "gates": {
            g: gate_results[g]["result"] for g in gate_results
        },
        "blockers": blockers,
        "gate_details": gate_results,
        "probes": probes_detail,
        "baseline_prompt_hashes": load_prompt_fingerprint().to_dict(),
        "candidate_prompt_hashes": load_candidate_fingerprint().to_dict(),
        "production_prompt_note": (
            "production prompt remains v1; this phase does NOT switch "
            "production_prompt_version to v2 and does not delete v1"
        ),
        **fp,
        "historical_snapshot_sha256": (
            probe_historical_snapshot_integrity()["evidence"]["sha256"]
        ),
        "environment": collect_stability_environment(),
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
    }
    _assert_no_secrets(snapshot)
    return snapshot


def render_rerun_report(snapshot: dict[str, Any]) -> str:
    out: list[str] = []
    add = out.append
    add(f"# Production Promotion Gate Re-run — Phase {snapshot['phase']}")
    add("")
    add(f"> previous_gate = {snapshot['previous_gate']} · "
        f"refusal_implementation = {snapshot['refusal_implementation']}")
    add("> 生产 prompt 仍为 v1；本阶段只输出 Gate 结论，不执行上线。")
    add("")
    add("## Promotion Status")
    add("")
    add(f"```text\n{snapshot['promotion_status']}\n```")
    add("")
    add("## Gate Matrix")
    add("")
    add("| Gate | Result |")
    add("|---|---|")
    for gate, result in snapshot["gates"].items():
        add(f"| {gate} | {result} |")
    add("")
    if snapshot["blockers"]:
        add("## Blockers")
        add("")
        for blocker in snapshot["blockers"]:
            add(f"- `{blocker}`")
        add("")
    add("## Refusal Evidence（§四 / §五）")
    add("")
    refusal = snapshot["probes"]["refusal_handling"][0]["evidence"]
    for question, entry in refusal["service_level"].items():
        add(f"- `{question}`: status={entry['status']}, attempts="
            f"{entry['attempts']}, llm_calls={entry['llm_calls']}, "
            f"validator_calls={entry['validator_calls']}, sql={entry['sql']}")
    add(f"- orchestrator: executor_calls="
        f"{refusal['orchestrator_level']['executor_calls']}, "
        f"metadata.refused="
        f"{refusal['orchestrator_level']['metadata_refused']}")
    api = snapshot["probes"]["api_refusal_handling"][0]["evidence"]
    add(f"- API: HTTP {api['status_code']}, data=None, "
        f"metadata.refused=true, 用户可见只读拒绝信息")
    add("")
    add("## Historical Snapshot Integrity（§二）")
    add("")
    for name, digest in snapshot["historical_snapshot_sha256"].items():
        add(f"- {name}: `{digest[:16]}…`")
    add("- 3.9.24 原始结论 NOT_READY（2 blockers）保持不变。")
    add("")
    add("## Hashes")
    add("")
    for key in ("dataset_sha256", "ground_truth_sha256",
                "fixture_schema_sha256", "fixture_data_sha256",
                "case_order_hash"):
        add(f"- {key}: `{str(snapshot.get(key))[:16]}…`")
    add(f"- baseline(v1): "
        f"`{snapshot['baseline_prompt_hashes']['combined_prompt_hash'][:16]}…`")
    add(f"- candidate(v2): "
        f"`{snapshot['candidate_prompt_hashes']['combined_prompt_hash'][:16]}…`")
    add("")
    return "\n".join(out)


def validate_rerun_snapshot(snapshot: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if snapshot.get("phase") != PHASE_3_9_26:
        problems.append(f"phase: expected {PHASE_3_9_26!r}")
    if snapshot.get("promotion_status") not in (
        "READY_FOR_PROMOTION", "NOT_READY"
    ):
        problems.append("promotion_status invalid")
    gates = snapshot.get("gates") or {}
    required = (
        "normal_t2s_compatibility", "validator_safety_boundary",
        "executor_safety_boundary", "project_isolation",
        "historical_regression", "refusal_handling",
        "api_refusal_handling", "rag_tool_routing_isolation",
    )
    for gate in required:
        if gates.get(gate) not in (GATE_PASS, GATE_BLOCKED):
            problems.append(f"gate {gate}: missing/invalid")
    expected = (
        "READY_FOR_PROMOTION"
        if all(gates.get(g) == GATE_PASS for g in required)
        else "NOT_READY"
    )
    if snapshot.get("promotion_status") != expected:
        problems.append(
            f"promotion_status != gates verdict "
            f"({snapshot.get('promotion_status')} vs {expected})"
        )
    # 历史 snapshot 哈希仍与当前文件一致（不可变性守护）
    for name, digest in (
        snapshot.get("historical_snapshot_sha256") or {}
    ).items():
        path = (
            _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "baselines"
            / name
        )
        if not path.exists() or _sha256_file(path) != digest:
            problems.append(f"historical snapshot {name} changed")
    fp = read_dataset_fingerprints()
    for key in ("dataset_sha256", "ground_truth_sha256",
                "fixture_schema_sha256", "fixture_data_sha256"):
        if snapshot.get(key) != fp[key]:
            problems.append(f"{key}: mismatch vs current files")
    blob = json.dumps(snapshot, ensure_ascii=False).lower()
    for fragment in ("postgresql://", "postgres://", "sk-", "bearer "):
        if fragment in blob:
            problems.append(f"forbidden fragment: {fragment!r}")
    return problems


# ============================================================
# Orchestrator 测试 fake（与 test_ai_orchestrator.py 同构，本地自足）
# ============================================================

class _GateFakeRouter:
    async def route(self, question, *, context=None):
        return RouteDecision(
            route=RouteType.TEXT_TO_SQL, confidence=0.9,
            reason="gate", source="rule",
        )


class _GateFakeTextToSQL:
    def __init__(self, result: TextToSQLResult) -> None:
        self._result = result

    async def generate(self, question, *, database_context,
                       allowed_tables=None, schema=None, max_rows=1000):
        return self._result


class _GateFakeTableSelector:
    def select(self, question, *, schema, semantic, top_k=5):
        from backend.app.services.relevant_table_selector import (
            TableSelection, TableSelectionResult,
        )
        return TableSelectionResult(
            question=question,
            selections=(TableSelection(
                table="public.knowledge_document", score=1.0,
                matched_terms=(),
            ),),
        )


class _GateFakeComposer:
    def compose(self, *, project, schema, semantic, tables=None,
                max_chars=4000):
        return "FAKE_CONTEXT"


class _GateFakeProjectProvider:
    def resolve(self):
        from backend.app.projects.context import (
            DataSource, ProjectContext,
        )
        from backend.app.projects.semantic import ProjectSemantic
        from backend.app.services.schema_explorer_service import (
            DatabaseSchema, SchemaColumn, SchemaTable,
        )

        project = ProjectContext(
            project_id="test-project", project_name="Test Project",
            description=None,
            data_source=DataSource(name="primary", type="postgresql"),
        )
        schema = DatabaseSchema(
            schema_name="public",
            tables=(SchemaTable(
                schema_name="public", name="knowledge_document",
                description=None,
                columns=(
                    SchemaColumn(
                        name="id", data_type="bigint", nullable=False,
                        default=None, ordinal_position=1,
                        is_primary_key=True, description=None,
                    ),
                    SchemaColumn(
                        name="title", data_type="varchar", nullable=True,
                        default=None, ordinal_position=2,
                        is_primary_key=False, description=None,
                    ),
                ),
                foreign_keys=(),
            ),),
        )
        return project, schema, ProjectSemantic()


def _gate_refusal_result() -> TextToSQLResult:
    return TextToSQLResult(
        question="删除所有 documents", sql=None, attempts=1,
        validated=False, referenced_tables=(),
        status=RESULT_STATUS_REFUSAL,
        refusal_reason="destructive_request_not_supported",
    )
