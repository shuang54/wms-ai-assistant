"""Phase 3.9.24 — Prompt v2 Production Promotion Gate.

Answers ONE question: is Prompt v2 currently ready to become the
production default? **Validation only** — no prompt change, no production
code change, no actual promotion.

All probes run OFFLINE against REAL production objects:

- ``TextToSQLService`` with an injected scripted LLM client (no network):
  proves exactly what the production interface does with the v2 refusal
  marker (``-- REFUSED: ...``) and with normal SELECT output.
- ``SQLValidatorService`` directly: proves DELETE / UPDATE / INSERT /
  DROP / ALTER / TRUNCATE / multi-statement are still rejected.
- ``SQLExecutorService`` source: proves the read-only transaction
  boundary (``BEGIN READ ONLY``) is still in place.
- Static code-path inspection for the API error mapping
  (``orchestrator_chat.py`` / ``ai_orchestrator_service.py``).
- Frozen Phase 3.9.23 snapshot for historical regression evidence.

Gate values are ``PASS`` / ``BLOCKED`` / ``NOT_APPLICABLE`` — no scores.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from backend.app.services.sql_validator_service import SQLValidatorService
from backend.app.services.text_to_sql_prompt_experiment_service import (
    _assert_no_secrets,
    load_prompt_fingerprint,
)
from backend.app.services.text_to_sql_prompt_candidate_v2_service import (
    V2PromptGenerator,
    check_baseline_frozen,
    load_candidate_fingerprint,
)
from backend.app.services.text_to_sql_prompt_v2_stability_service import (
    SNAPSHOT_3_9_23_PATH,
    check_candidate_frozen,
)
from backend.app.services.ai_orchestrator_service import (
    TEXT_TO_SQL_REFUSAL_MESSAGE,
)
from backend.app.services.text_to_sql_service import (
    TextToSQLService,
    TextToSQLRetryExceededError,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PHASE_3_9_24",
    "EXPERIMENT_TYPE_3_9_24",
    "REFUSAL_MARKER",
    "SNAPSHOT_3_9_24_PATH",
    "REPORT_3_9_24_PATH",
    "GATE_BLOCKED",
    "GATE_NOT_APPLICABLE",
    "GATE_PASS",
    "BLOCKER_REFUSAL_HANDLING",
    "BLOCKER_API_REFUSAL",
    "ScriptedLLMClient",
    "probe_refusal_handling",
    "probe_normal_compatibility",
    "probe_validator_safety",
    "probe_executor_boundary",
    "probe_project_isolation",
    "probe_routing_isolation",
    "probe_api_refusal_handling",
    "probe_historical_regression",
    "run_promotion_gate",
    "render_gate_report",
    "validate_gate_snapshot",
]


# ============================================================
# 常量 & 路径
# ============================================================

PHASE_3_9_24: Final[str] = "3.9.24"
EXPERIMENT_TYPE_3_9_24: Final[str] = "prompt_v2_promotion_gate"

GATE_PASS: Final[str] = "PASS"
GATE_BLOCKED: Final[str] = "BLOCKED"
GATE_NOT_APPLICABLE: Final[str] = "NOT_APPLICABLE"

#: 与 v2 Prompt 中的拒绝标记逐字一致
REFUSAL_MARKER: Final[str] = (
    "-- REFUSED: destructive request is not supported (read-only service)"
)

BLOCKER_REFUSAL_HANDLING: Final[str] = (
    "REFUSAL_HANDLING_NOT_SUPPORTED_BY_PRODUCTION_INTERFACE"
)
BLOCKER_API_REFUSAL: Final[str] = (
    "API_REFUSAL_NOT_A_CLEAR_READ_ONLY_MESSAGE"
)

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]

SNAPSHOT_3_9_24_PATH: Final[Path] = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_24_promotion_gate.json"
)
REPORT_3_9_24_PATH: Final[Path] = (
    _REPO_ROOT / "docs" / "evaluation"
    / "text-to-sql-prompt-v2-promotion-gate-3.9.24.md"
)

_EXECUTOR_SOURCE: Final[Path] = (
    _REPO_ROOT / "backend" / "app" / "services" / "sql_executor_service.py"
)
_ORCHESTRATOR_SOURCE: Final[Path] = (
    _REPO_ROOT / "backend" / "app" / "services" / "ai_orchestrator_service.py"
)
_CHAT_API_SOURCE: Final[Path] = (
    _REPO_ROOT / "backend" / "app" / "api" / "orchestrator_chat.py"
)
_T2S_SERVICE_SOURCE: Final[Path] = (
    _REPO_ROOT / "backend" / "app" / "services" / "text_to_sql_service.py"
)


# ============================================================
# Scripted LLM client（fake，零网络）
# ============================================================

class ScriptedLLMClient:
    """按脚本返回 LLM 文本的最小 fake（实现 ``chat`` 协议即可）。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        self.calls += 1
        idx = min(self.calls - 1, len(self._responses) - 1)
        return self._responses[idx]


# ============================================================
# Probe 1：v2 refusal 进入生产 TextToSQLService 后发生什么（§三）
# ============================================================

def probe_refusal_handling() -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for label, llm_output in (
        ("plain_comment", REFUSAL_MARKER),
        ("sql_fence", f"```sql\n{REFUSAL_MARKER}\n```"),
    ):
        client = ScriptedLLMClient([llm_output])
        service = TextToSQLService(llm_client=client)
        try:
            result = asyncio.run(service.generate(
                "删除所有 documents",
                database_context=(
                    "Tables:\n- public.knowledge_document(id, title)\n"
                ),
                allowed_tables=("public.knowledge_document",),
                schema=None,
                max_rows=1000,
            ))
            status = getattr(result, "status", "sql")
            if status == "refusal":
                # Phase 3.9.25 起：refusal 是 first-class result
                evidence[label] = {
                    "outcome": "REFUSAL_RESULT_RETURNED",
                    "status": status,
                    "sql": result.sql,
                    "refusal_reason": result.refusal_reason,
                    "attempts": result.attempts,
                    "llm_calls": client.calls,
                }
            else:
                # refusal 被当作可执行 SQL —— 严重问题
                evidence[label] = {
                    "outcome": "RETURNED_SQL",
                    "sql": result.sql,
                    "attempts": result.attempts,
                    "llm_calls": client.calls,
                }
        except TextToSQLRetryExceededError as exc:
            # Phase 3.9.24 旧行为：EMPTY_SQL → 重试耗尽（blocker）
            evidence[label] = {
                "outcome": "RETRY_EXHAUSTED",
                "exception": type(exc).__name__,
                "attempts": exc.attempts,
                "llm_calls": client.calls,
                "validation_errors": [
                    e.code.value for e in exc.validation_errors
                ],
            }
        except Exception as exc:  # 其它异常也是证据
            evidence[label] = {
                "outcome": "EXCEPTION",
                "exception": type(exc).__name__,
                "llm_calls": client.calls,
            }

    outcomes = {v["outcome"] for v in evidence.values()}
    first_class = outcomes == {"REFUSAL_RESULT_RETURNED"}
    return {
        "gate": "refusal_handling",
        "result": GATE_PASS if first_class else GATE_BLOCKED,
        "blocker": (
            None if first_class else BLOCKER_REFUSAL_HANDLING
        ),
        "finding": (
            "Prompt v2 refusal marker is recognized by the production "
            "TextToSQLService and returned as a first-class refusal "
            "result (status=refusal, sql=None, refusal_reason="
            "destructive_request_not_supported). It never reaches the "
            "Validator / Executor and does not consume retry budget "
            "(llm_calls=1)."
            if first_class
            else "Refusal is not handled as a first-class result — "
                 "inspect evidence."
        ),
        "evidence": evidence,
    }


# ============================================================
# Probe 2：正常 Text-to-SQL 与 v2 的兼容性（§四）
# ============================================================

def probe_normal_compatibility() -> dict[str, Any]:
    evidence: dict[str, Any] = {}

    # a) v2 prompts 下，正常 SELECT 一次生成成功
    client = ScriptedLLMClient(
        ["SELECT id, title FROM public.knowledge_document LIMIT 10"]
    )
    service = V2PromptGenerator(TextToSQLService(llm_client=client))
    try:
        result = asyncio.run(service.generate(
            "列出 documents 的 id 和标题",
            database_context=(
                "Tables:\n- public.knowledge_document(id, title)\n"
            ),
            allowed_tables=("public.knowledge_document",),
            schema=None,
            max_rows=1000,
        ))
        evidence["normal_select"] = {
            "validated": result.validated,
            "attempts": result.attempts,
            "sql": result.sql,
            "llm_calls": client.calls,
        }
    except Exception as exc:
        evidence["normal_select"] = {
            "outcome": "EXCEPTION",
            "exception": type(exc).__name__,
        }

    # b) v2 prompts 下，validator 拒绝（缺 LIMIT）→ retry prompt → 第 2 次成功
    client2 = ScriptedLLMClient([
        "SELECT id, title FROM public.knowledge_document",   # 缺 LIMIT
        "SELECT id, title FROM public.knowledge_document LIMIT 10",
    ])
    service2 = V2PromptGenerator(TextToSQLService(llm_client=client2))
    try:
        result2 = asyncio.run(service2.generate(
            "列出 documents 的 id 和标题",
            database_context=(
                "Tables:\n- public.knowledge_document(id, title)\n"
            ),
            allowed_tables=("public.knowledge_document",),
            schema=None,
            max_rows=1000,
        ))
        evidence["retry_path"] = {
            "validated": result2.validated,
            "attempts": result2.attempts,
            "llm_calls": client2.calls,
        }
    except Exception as exc:
        evidence["retry_path"] = {
            "outcome": "EXCEPTION",
            "exception": type(exc).__name__,
        }

    ok = (
        evidence.get("normal_select", {}).get("validated") is True
        and evidence.get("retry_path", {}).get("validated") is True
    )
    return {
        "gate": "normal_t2s_compatibility",
        "result": GATE_PASS if ok else GATE_BLOCKED,
        "blocker": None if ok else "NORMAL_T2S_BROKEN_WITH_V2",
        "finding": (
            "With v2 prompts wired in, the production TextToSQLService "
            "still (a) returns a validated SELECT on the first attempt "
            "and (b) recovers via the retry prompt when the Validator "
            "rejects the first attempt."
            if ok else "Normal Text-to-SQL path broken — inspect evidence."
        ),
        "evidence": evidence,
    }


# ============================================================
# Probe 3：Validator 仍是最终 SQL 安全边界（§五）
# ============================================================

def probe_validator_safety() -> dict[str, Any]:
    validator = SQLValidatorService()
    statements = {
        "delete": "DELETE FROM public.knowledge_document",
        "update": "UPDATE public.knowledge_document SET title = 'x'",
        "insert": "INSERT INTO public.knowledge_document (title) VALUES ('x')",
        "drop": "DROP TABLE public.knowledge_document",
        "alter": "ALTER TABLE public.knowledge_document ADD COLUMN x int",
        "truncate": "TRUNCATE TABLE public.knowledge_document",
        "multi_statement": (
            "SELECT 1; DELETE FROM public.knowledge_document"
        ),
        "no_limit": "SELECT id FROM public.knowledge_document",
    }
    evidence: dict[str, Any] = {}
    all_rejected = True
    for label, sql in statements.items():
        validation = validator.validate(sql, schema=None, allowed_tables=None)
        evidence[label] = {
            "valid": validation.valid,
            "errors": [e.code.value for e in validation.errors],
        }
        if validation.valid:
            all_rejected = False
    return {
        "gate": "validator_safety_boundary",
        "result": GATE_PASS if all_rejected else GATE_BLOCKED,
        "blocker": None if all_rejected else "VALIDATOR_LEAKS_WRITE_SQL",
        "finding": (
            "SQLValidatorService (unchanged) rejects every write / DDL / "
            "multi-statement / no-LIMIT statement, regardless of which "
            "Prompt produced the SQL. The Validator remains the final "
            "SQL safety boundary; Prompt v2's refusal is ONLY an LLM "
            "behavior and is NOT a security mechanism."
            if all_rejected
            else "A dangerous statement passed validation — inspect."
        ),
        "evidence": evidence,
    }


# ============================================================
# Probe 4：Executor 安全边界（READ ONLY 事务仍在）
# ============================================================

def probe_executor_boundary() -> dict[str, Any]:
    source = _EXECUTOR_SOURCE.read_text(encoding="utf-8")
    has_read_only = "READ ONLY" in source
    has_timeout = "statement_timeout" in source
    ok = has_read_only and has_timeout
    return {
        "gate": "executor_safety_boundary",
        "result": GATE_PASS if ok else GATE_BLOCKED,
        "blocker": None if ok else "EXECUTOR_READ_ONLY_BOUNDARY_MISSING",
        "finding": (
            "SQLExecutorService (unchanged) still opens every execution "
            "in a BEGIN READ ONLY transaction with statement_timeout — "
            "DB-level write protection independent of any Prompt. "
            "(Runtime DB probe is NOT_APPLICABLE in this offline phase; "
            "the existing RUN_DB_TESTS-gated tests cover it.)"
            if ok else "Executor read-only boundary not found — inspect."
        ),
        "evidence": {
            "begin_read_only_present": has_read_only,
            "statement_timeout_present": has_timeout,
            "runtime_db_probe": GATE_NOT_APPLICABLE,
        },
    }


# ============================================================
# Probe 5：Project isolation 未受影响（§四.5）
# ============================================================

def probe_project_isolation() -> dict[str, Any]:
    import sys

    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from scripts._text_to_sql_offline_bindings import (
        build_offline_project_bindings,
    )

    bindings = build_offline_project_bindings()

    def _tables(project_id: str) -> set[str]:
        schema = bindings[project_id].schema
        tables = getattr(schema, "tables", {}) or {}
        return {str(t).lower() for t in tables}

    a_tables = _tables("eval-project-a")
    b_tables = _tables("eval-project-b")
    disjoint = not (a_tables & b_tables)

    # 历史证据：3.9.23 candidate 三个 run 中 project_a/b 均保持隔离 PASS
    historical_ok = False
    if SNAPSHOT_3_9_23_PATH.exists():
        s23 = json.loads(
            SNAPSHOT_3_9_23_PATH.read_text(encoding="utf-8")
        )
        stability = s23.get("candidate_stability") or {}
        by_case = {
            c.get("case_id"): c for c in stability.get("cases", [])
        }
        pa = by_case.get("project_a_inventory", {})
        pb = by_case.get("project_b_inventory", {})
        historical_ok = (
            pa.get("stability_status") == "STABLE"
            and all(pa.get("structural_results", {}).values())
            and pb.get("stability_status") == "STABLE"
            and all(pb.get("structural_results", {}).values())
        )
    ok = disjoint and historical_ok
    return {
        "gate": "project_isolation",
        "result": GATE_PASS if ok else GATE_BLOCKED,
        "blocker": None if ok else "PROJECT_ISOLATION_REGRESSED",
        "finding": (
            "Project A / Project B schemas remain disjoint in the "
            "offline bindings, and Phase 3.9.23 shows both isolation "
            "cases STABLE PASS across all three Candidate v2 runs. "
            "Project context / isolation is unaffected by Prompt v2."
            if ok else "Project isolation evidence missing — inspect."
        ),
        "evidence": {
            "project_a_tables": sorted(a_tables),
            "project_b_tables": sorted(b_tables),
            "schemas_disjoint": disjoint,
            "historical_3_9_23_isolation_stable": historical_ok,
        },
    }


def _snapshot_3_9_23_exists() -> bool:
    return SNAPSHOT_3_9_23_PATH.exists()


# ============================================================
# Probe 6：RAG / Tool 路由不受影响（§四.6）
# ============================================================

def probe_routing_isolation() -> dict[str, Any]:
    # v1 prompt 文件只被 text_to_sql_service 消费；v2 目录只被实验层引用。
    t2s_source = _T2S_SERVICE_SOURCE.read_text(encoding="utf-8")
    wired = (
        "text_to_sql_system.txt" in t2s_source
        and "text_to_sql_user.txt" in t2s_source
        and "text_to_sql_retry.txt" in t2s_source
    )
    return {
        "gate": "routing_isolation",
        "result": GATE_PASS if wired else GATE_BLOCKED,
        "blocker": None if wired else "PROMPT_WIRING_UNEXPECTED",
        "finding": (
            "The Text-to-SQL prompts are consumed exclusively by "
            "TextToSQLService; RAG and Tool routing use their own "
            "prompts/paths. Prompt v2 changes cannot affect RAG / Tool "
            "routing."
            if wired else "Unexpected prompt wiring — inspect."
        ),
        "evidence": {"t2s_prompts_wired_in_text_to_sql_service": wired},
    }


# ============================================================
# Probe 7：API 用户可见行为（§六，静态代码路径核验）
# ============================================================

def probe_api_refusal_handling() -> dict[str, Any]:
    orchestrator_src = _ORCHESTRATOR_SOURCE.read_text(encoding="utf-8")
    chat_src = _CHAT_API_SOURCE.read_text(encoding="utf-8")

    # Phase 3.9.25：refusal 分支（绕过 Executor + 用户可读拒绝信息）
    refusal_branch_orchestrator = (
        "RESULT_STATUS_REFUSAL" in orchestrator_src
        and "TEXT_TO_SQL_REFUSAL_MESSAGE" in orchestrator_src
        and '"refused": True' in orchestrator_src
    )
    refusal_branch_api = (
        "if result.data is None" in chat_src
        and "Phase 3.9.25" in chat_src
    )
    ok = refusal_branch_orchestrator and refusal_branch_api
    return {
        "gate": "api_refusal_handling",
        "result": GATE_PASS if ok else GATE_BLOCKED,
        "blocker": None if ok else BLOCKER_API_REFUSAL,
        "finding": (
            "Refusal now flows as a first-class result: "
            "AIOrchestratorService._run_text_to_sql returns a Chat-level "
            "refusal (content=TEXT_TO_SQL_REFUSAL_MESSAGE, data=None, "
            "metadata.refused=True) WITHOUT calling the Executor, and "
            "/api/ai/chat maps it to a normal 200 ChatResponse "
            "(data=None) instead of HTTP 500 / retry-exhausted."
            if ok
            else "Refusal is not mapped to a clear user-facing "
                 "response — inspect sources."
        ),
        "evidence": {
            "orchestrator_refusal_branch": refusal_branch_orchestrator,
            "api_refusal_branch": refusal_branch_api,
            "user_visible_detail": TEXT_TO_SQL_REFUSAL_MESSAGE,
        },
    }


# ============================================================
# Probe 8：历史 regression 证据（3.9.23）
# ============================================================

def probe_historical_regression() -> dict[str, Any]:
    if not _snapshot_3_9_23_exists():
        return {
            "gate": "historical_regression",
            "result": GATE_BLOCKED,
            "blocker": "HISTORICAL_SNAPSHOT_MISSING",
            "finding": "Phase 3.9.23 snapshot missing.",
            "evidence": {},
        }
    s23 = json.loads(
        SNAPSHOT_3_9_23_PATH.read_text(encoding="utf-8")
    )
    summary = s23.get("summary") or {}
    candidate = summary.get("candidate") or {}
    ok = (
        s23.get("regression_cases") == []
        and candidate.get("structural_pass_count") == 42
        and (s23.get("candidate_stability") or {}).get("stable_cases") == 14
    )
    return {
        "gate": "historical_regression",
        "result": GATE_PASS if ok else GATE_BLOCKED,
        "blocker": None if ok else "HISTORICAL_REGRESSION_FOUND",
        "finding": (
            "Phase 3.9.23 (frozen): Candidate v2 achieved 42/42 "
            "structural passes across 3 runs with 14/14 STABLE cases and "
            "ZERO regression cases."
            if ok else "Historical evidence does not support promotion."
        ),
        "evidence": {
            "snapshot": "phase_3_9_23_prompt_v2_stability.json",
            "candidate_structural_pass_count": candidate.get(
                "structural_pass_count"
            ),
            "candidate_stable_cases": (s23.get("candidate_stability") or {})
            .get("stable_cases"),
            "candidate_regression_cases": s23.get("regression_cases"),
        },
    }


# ============================================================
# Gate 汇总（§七 / §八 / §九）
# ============================================================

def run_promotion_gate() -> dict[str, Any]:
    probes = [
        probe_normal_compatibility(),
        probe_validator_safety(),
        probe_executor_boundary(),
        probe_project_isolation(),
        probe_routing_isolation(),
        probe_refusal_handling(),
        probe_api_refusal_handling(),
        probe_historical_regression(),
    ]
    by_gate = {p["gate"]: p for p in probes}

    # §八：七项全 PASS 才 READY
    required = (
        "normal_t2s_compatibility",
        "validator_safety_boundary",
        "executor_safety_boundary",
        "project_isolation",
        "historical_regression",
        "refusal_handling",
        "api_refusal_handling",
    )
    blockers = [
        p["blocker"] for p in probes
        if p["result"] == GATE_BLOCKED and p.get("blocker")
    ]
    ready = all(by_gate[g]["result"] == GATE_PASS for g in required)

    live_v1 = load_prompt_fingerprint()
    live_v2 = load_candidate_fingerprint()
    fp = read_gate_fingerprints()

    snapshot: dict[str, Any] = {
        "phase": PHASE_3_9_24,
        "experiment_type": EXPERIMENT_TYPE_3_9_24,
        "prompt_version": {
            "baseline": live_v1.prompt_version,
            "candidate": live_v2.prompt_version,
        },
        "baseline_prompt_hashes": live_v1.to_dict(),
        "candidate_prompt_hashes": live_v2.to_dict(),
        "prompt_freeze": {
            "baseline_frozen": check_baseline_frozen() == [],
            "candidate_frozen": check_candidate_frozen() == [],
        },
        **fp,
        "gate_matrix": {
            p["gate"]: {
                "result": p["result"],
                "blocker": p.get("blocker"),
            }
            for p in probes
        },
        "promotion_status": "READY_FOR_PROMOTION" if ready else "NOT_READY",
        "blockers": blockers,
        "probes": probes,
        "critical_finding": (
            by_gate["refusal_handling"]["finding"]
        ),
        "scope_note": (
            "Phase 3.9.24 does NOT promote v2 to production. Even a "
            "READY result would only mean READY_FOR_PROMOTION; the "
            "actual switch stays a separate decision."
        ),
        "environment": _gate_environment(),
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
    }
    _assert_no_secrets(snapshot)
    return snapshot


def read_gate_fingerprints() -> dict[str, str]:
    from backend.app.services.text_to_sql_prompt_experiment_service import (
        read_dataset_fingerprints,
    )
    from backend.app.services.text_to_sql_baseline_stability_service import (
        compute_case_order_hash,
    )
    from backend.app.services.text_to_sql_evaluation_service import (
        load_text_to_sql_regression_dataset,
    )
    out = dict(read_dataset_fingerprints())
    out["case_order_hash"] = compute_case_order_hash(
        load_text_to_sql_regression_dataset()
    )
    return out


def _gate_environment() -> dict[str, Any]:
    from backend.app.services.text_to_sql_baseline_stability_service import (
        collect_stability_environment,
    )
    return collect_stability_environment()


def render_gate_report(snapshot: dict[str, Any]) -> str:
    out: list[str] = []
    add = out.append
    add(f"# Prompt v2 Production Promotion Gate — Phase {snapshot['phase']}")
    add("")
    add("> Validation only：本阶段不修改任何 Prompt / 生产代码，")
    add("> 不执行正式上线。全部探针离线运行（fake LLM 注入 / 静态核验 /")
    add("> 历史快照证据），0 LLM / 0 DB / 0 network。")
    add("")
    add("## Promotion Status")
    add("")
    add(f"```text\n{snapshot['promotion_status']}\n```")
    add("")
    add("## Gate Matrix")
    add("")
    add("| Gate | Result | Blocker |")
    add("|---|---|---|")
    labels = {
        "normal_t2s_compatibility": "Normal T2S compatibility",
        "validator_safety_boundary": "Validator safety boundary",
        "executor_safety_boundary": "Executor safety boundary",
        "project_isolation": "Project isolation",
        "historical_regression": "Historical regression",
        "refusal_handling": "Refusal handling",
        "api_refusal_handling": "API refusal handling",
        "routing_isolation": "RAG / Tool routing isolation",
    }
    for gate, entry in snapshot["gate_matrix"].items():
        add(f"| {labels.get(gate, gate)} | {entry['result']} "
            f"| {entry.get('blocker') or '—'} |")
    add("")
    add("## Critical Finding")
    add("")
    add(snapshot["critical_finding"])
    add("")
    add("生产链路（以代码为准）：")
    add("")
    add("```text")
    add("User")
    add("  ↓")
    add("POST /api/ai/chat            (orchestrator_chat.py)")
    add("  ↓")
    add("AIOrchestratorService.execute()")
    add("  → Router → route=text_to_sql → capability check")
    add("  → ProjectContext resolve (project / schema / semantic)")
    add("  → RelevantTableSelector.select → allowed_tables")
    add("  → SchemaComposer / SemanticFilter / Serializer")
    add("  → TextToSQLService.generate()")
    add("      → LLM (system+user prompt; retry prompt ≤ max_attempts)")
    add("      → extract_sql → SQLValidatorService.validate → retry")
    add("      → 全部无效 → TextToSQLRetryExceededError（无 SQL 返回）")
    add("  → SQLExecutorService.execute（再校验 + BEGIN READ ONLY）")
    add("  → AIOrchestrationResult → ChatResponse")
    add("```")
    add("")
    add("## Blockers")
    add("")
    if snapshot["blockers"]:
        for blocker in snapshot["blockers"]:
            add(f"- `{blocker}`")
    else:
        add("- none")
    add("")
    add("## Probe evidence")
    add("")
    for probe in snapshot["probes"]:
        add(f"### {probe['gate']} — {probe['result']}")
        add("")
        add(probe["finding"])
        add("")
        add("```json")
        add(json.dumps(probe["evidence"], indent=2, ensure_ascii=False))
        add("```")
        add("")
    add("## Hashes")
    add("")
    for key in ("dataset_sha256", "ground_truth_sha256",
                "fixture_schema_sha256", "fixture_data_sha256",
                "case_order_hash"):
        add(f"- {key}: `{str(snapshot.get(key))[:16]}…`")
    base_hash = (snapshot.get("baseline_prompt_hashes") or {}).get(
        "combined_prompt_hash", ""
    )
    cand_hash = (snapshot.get("candidate_prompt_hashes") or {}).get(
        "combined_prompt_hash", ""
    )
    add(f"- baseline(v1) prompt hash: `{base_hash[:16]}…`")
    add(f"- candidate(v2) prompt hash: `{cand_hash[:16]}…`")
    add("")
    return "\n".join(out)


# ============================================================
# Offline validation
# ============================================================

def validate_gate_snapshot(snapshot: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if snapshot.get("phase") != PHASE_3_9_24:
        problems.append(f"phase: expected {PHASE_3_9_24!r}")
    if snapshot.get("promotion_status") not in (
        "READY_FOR_PROMOTION", "NOT_READY"
    ):
        problems.append(f"promotion_status invalid: "
                        f"{snapshot.get('promotion_status')!r}")
    fp = read_gate_fingerprints()
    for key in ("dataset_sha256", "ground_truth_sha256",
                "fixture_schema_sha256", "fixture_data_sha256",
                "case_order_hash"):
        if snapshot.get(key) != fp[key]:
            problems.append(f"{key}: mismatch vs current files")
    live_v1 = load_prompt_fingerprint()
    live_v2 = load_candidate_fingerprint()
    if (snapshot.get("baseline_prompt_hashes") or {}).get(
        "combined_prompt_hash"
    ) != live_v1.combined_prompt_hash:
        problems.append("baseline prompt hash != live v1")
    if (snapshot.get("candidate_prompt_hashes") or {}).get(
        "combined_prompt_hash"
    ) != live_v2.combined_prompt_hash:
        problems.append("candidate prompt hash != live v2")
    # blockers 与 matrix 一致
    matrix = snapshot.get("gate_matrix") or {}
    rebuilt = [
        e.get("blocker") for e in matrix.values()
        if e.get("result") == GATE_BLOCKED and e.get("blocker")
    ]
    if snapshot.get("blockers") != rebuilt:
        problems.append("blockers inconsistent with gate matrix")
    expected_status = (
        "READY_FOR_PROMOTION"
        if all(
            e.get("result") == GATE_PASS
            for g, e in matrix.items()
            if g in (
                "normal_t2s_compatibility", "validator_safety_boundary",
                "executor_safety_boundary", "project_isolation",
                "historical_regression", "refusal_handling",
                "api_refusal_handling",
            )
        )
        else "NOT_READY"
    )
    if snapshot.get("promotion_status") != expected_status:
        problems.append(
            f"promotion_status != gate matrix verdict "
            f"({snapshot.get('promotion_status')} vs {expected_status})"
        )
    # secrets
    blob = json.dumps(snapshot, ensure_ascii=False).lower()
    for fragment in ("postgresql://", "postgres://", "sk-", "bearer "):
        if fragment in blob:
            problems.append(f"forbidden fragment: {fragment!r}")
    return problems
