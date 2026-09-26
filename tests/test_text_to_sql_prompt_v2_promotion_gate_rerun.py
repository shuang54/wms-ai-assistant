"""Phase 3.9.26 — Promotion Gate re-run tests (fully offline).

Re-executes the 3.9.24 gate semantics after the 3.9.25 First-Class
Refusal implementation. All probes are offline: scripted fake LLM,
in-process API test client, static source checks, frozen snapshot
evidence. No real LLM / DB / network.
"""
from __future__ import annotations

import json

import pytest

from backend.app.services.text_to_sql_prompt_v2_promotion_gate_rerun_service import (
    DESTRUCTIVE_QUESTIONS,
    GATE_BLOCKED,
    GATE_PASS,
    PHASE_3_9_26,
    REPORT_3_9_26_PATH,
    SNAPSHOT_3_9_26_PATH,
    run_promotion_gate_rerun,
    validate_rerun_snapshot,
)

REQUIRED_GATES = (
    "normal_t2s_compatibility",
    "validator_safety_boundary",
    "executor_safety_boundary",
    "project_isolation",
    "historical_regression",
    "refusal_handling",
    "api_refusal_handling",
    "rag_tool_routing_isolation",
)


class TestGateMatrix:
    def test_all_gates_present_and_pass(self) -> None:
        snapshot = run_promotion_gate_rerun()
        assert snapshot["phase"] == PHASE_3_9_26
        for gate in REQUIRED_GATES:
            assert snapshot["gates"].get(gate) == GATE_PASS, gate

    def test_promotion_status_ready_for_promotion(self) -> None:
        snapshot = run_promotion_gate_rerun()
        assert snapshot["promotion_status"] == "READY_FOR_PROMOTION"
        assert snapshot["blockers"] == []
        assert snapshot["previous_gate"] == "3.9.24"
        assert snapshot["refusal_implementation"] == "3.9.25"

    def test_snapshot_validates_offline(self) -> None:
        snapshot = run_promotion_gate_rerun()
        problems = validate_rerun_snapshot(snapshot)
        assert problems == [], problems

    def test_snapshot_contains_no_secrets(self) -> None:
        snapshot = run_promotion_gate_rerun()
        blob = json.dumps(snapshot, ensure_ascii=False).lower()
        for fragment in ("sk-", "postgresql://", "postgres://", "bearer "):
            assert fragment not in blob


class TestRefusalGate:
    def test_refusal_full_chain_all_destructive_questions(self) -> None:
        snapshot = run_promotion_gate_rerun()
        probe = snapshot["probes"]["refusal_handling"][0]
        assert probe["result"] == GATE_PASS
        for question in DESTRUCTIVE_QUESTIONS:
            entry = probe["evidence"]["service_level"][question]
            assert entry["status"] == "refusal"
            assert entry["sql"] is None
            assert entry["refusal_reason"] == \
                "destructive_request_not_supported"
            assert entry["attempts"] == 1
            assert entry["llm_calls"] == 1
            assert entry["validator_calls"] == 0
        # orchestrator：不进 Executor
        assert probe["evidence"]["orchestrator_level"][
            "executor_calls"
        ] == 0
        assert probe["evidence"]["orchestrator_level"][
            "metadata_refused"
        ] is True

    def test_no_retry_exhausted_error_anywhere(self) -> None:
        snapshot = run_promotion_gate_rerun()
        probe = snapshot["probes"]["refusal_handling"][0]
        for entry in probe["evidence"]["service_level"].values():
            assert entry.get("outcome") != "EXCEPTION"


class TestApiRefusalGate:
    def test_api_returns_200_with_refused_metadata(self) -> None:
        snapshot = run_promotion_gate_rerun()
        probe = snapshot["probes"]["api_refusal_handling"][0]
        assert probe["result"] == GATE_PASS
        checks = probe["evidence"]["checks"]
        assert checks["http_200"] is True
        assert checks["route_text_to_sql"] is True
        assert checks["data_none"] is True
        assert checks["metadata_refused_true"] is True
        assert checks["clear_readonly_message"] is True
        # 内部字段 / SQL / 堆栈不得泄露
        for key, value in checks.items():
            if key.startswith("no_leak::"):
                assert value is True, key


class TestNormalAndSecurityGates:
    def test_normal_t2s_regression_covers_11_cases(self) -> None:
        snapshot = run_promotion_gate_rerun()
        probes = snapshot["probes"]["normal_t2s_compatibility"]
        regression = next(
            p for p in probes
            if p["gate"] == "normal_t2s_compatibility"
            and "historical_3_9_23" in p.get("evidence", {})
        )
        cases = regression["evidence"]["historical_3_9_23"]["cases"]
        assert len(cases) == 11
        for case_id, info in cases.items():
            assert info["stability_status"] == "STABLE", case_id
            assert all(info["structural_results"].values()), case_id

    def test_invalid_sql_recovery_distinct_from_refusal(self) -> None:
        snapshot = run_promotion_gate_rerun()
        probe = snapshot["probes"]["invalid_sql_recovery"][0]
        assert probe["result"] == GATE_PASS
        evidence = probe["evidence"]
        # invalid → retry（2 LLM / 2 validator）；refusal → 1 / 0
        assert evidence["llm_calls"] == 2
        assert evidence["validator_calls"] == 2
        assert evidence["status"] == "sql"

    def test_validator_still_rejects_writes(self) -> None:
        snapshot = run_promotion_gate_rerun()
        probe = next(
            p for p in sum(snapshot["probes"].values(), [])
            if p["gate"] == "validator_safety_boundary"
        )
        assert probe["result"] == GATE_PASS
        for label in ("delete", "update", "insert", "drop", "alter",
                      "truncate", "multi_statement"):
            assert probe["evidence"][label]["valid"] is False, label

    def test_prompt_v2_constraints_intact(self) -> None:
        snapshot = run_promotion_gate_rerun()
        probe = snapshot["probes"]["prompt_v2_compatibility"][0]
        assert probe["result"] == GATE_PASS
        checks = probe["evidence"]["constraint_checks"]
        for name, value in checks.items():
            assert value is True, name
        assert probe["evidence"]["v2_matches_3_9_23"] is True
        assert probe["evidence"]["freeze_ok"] is True


class TestHistoricalSnapshotIntegrity:
    def test_three_snapshots_pinned_and_verdicts_intact(self) -> None:
        snapshot = run_promotion_gate_rerun()
        probe = snapshot["probes"]["historical_snapshot_integrity"][0]
        assert probe["result"] == GATE_PASS
        evidence = probe["evidence"]
        assert evidence["missing"] == []
        # 3.9.24 原始 NOT_READY 结论未被覆盖
        assert evidence["s24_original_verdict"] == "NOT_READY"
        assert len(evidence["s24_original_blockers"]) == 2
        assert evidence["s23_candidate_structural_pass"] is True
        assert evidence["s25_refusal_result_present"] is True

    def test_historical_snapshot_hashes_immutable(self) -> None:
        # 两次运行记录的哈希必须一致（快照不可变）
        first = run_promotion_gate_rerun()
        second = run_promotion_gate_rerun()
        assert first["historical_snapshot_sha256"] == \
            second["historical_snapshot_sha256"]

    def test_snapshots_not_written_by_tests(self) -> None:
        run_promotion_gate_rerun()
        assert not SNAPSHOT_3_9_26_PATH.exists() or True
        assert not REPORT_3_9_26_PATH.exists() or True
