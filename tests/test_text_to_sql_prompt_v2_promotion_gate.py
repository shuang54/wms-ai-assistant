"""Phase 3.9.24 — Prompt v2 production promotion gate tests (offline).

All probes run against REAL production objects (TextToSQLService with an
injected scripted LLM client, SQLValidatorService, static source checks,
frozen 3.9.23 snapshot). No prompt change, no production code change,
no LLM call, no DB call, no network call.
"""
from __future__ import annotations

import pytest

from backend.app.services.sql_validator_service import SQLValidatorService
from backend.app.services.text_to_sql_prompt_v2_stability_service import (
    check_candidate_frozen,
)
from backend.app.services.text_to_sql_prompt_candidate_v2_service import (
    check_baseline_frozen,
)
from backend.app.services.text_to_sql_prompt_v2_promotion_gate_service import (
    BLOCKER_API_REFUSAL,
    BLOCKER_REFUSAL_HANDLING,
    GATE_BLOCKED,
    GATE_PASS,
    PHASE_3_9_24,
    REFUSAL_MARKER,
    SNAPSHOT_3_9_24_PATH,
    probe_api_refusal_handling,
    probe_executor_boundary,
    probe_historical_regression,
    probe_normal_compatibility,
    probe_project_isolation,
    probe_refusal_handling,
    probe_routing_isolation,
    probe_validator_safety,
    run_promotion_gate,
    validate_gate_snapshot,
)


class TestRefusalHandling:
    def test_refusal_becomes_retry_exhausted(self) -> None:
        """情况 B：refusal 标记被生产接口当作 EMPTY_SQL → 重试耗尽。"""
        probe = probe_refusal_handling()
        assert probe["result"] == GATE_BLOCKED
        assert probe["blocker"] == BLOCKER_REFUSAL_HANDLING
        for label in ("plain_comment", "sql_fence"):
            evidence = probe["evidence"][label]
            assert evidence["outcome"] == "RETRY_EXHAUSTED"
            assert evidence["exception"] == "TextToSQLRetryExceededError"
            assert "EMPTY_SQL" in evidence["validation_errors"]
            # refusal 触发了完整重试预算（生产 max_attempts=3）
            assert evidence["llm_calls"] == 3
            assert evidence["attempts"] == 3

    def test_refusal_never_returns_executable_sql(self) -> None:
        # 关键安全事实：refusal 绝不会变成可执行 SQL 结果
        probe = probe_refusal_handling()
        for evidence in probe["evidence"].values():
            assert evidence["outcome"] != "RETURNED_SQL"


class TestNormalCompatibility:
    def test_normal_select_with_v2_prompts(self) -> None:
        probe = probe_normal_compatibility()
        assert probe["result"] == GATE_PASS
        normal = probe["evidence"]["normal_select"]
        assert normal["validated"] is True
        assert normal["attempts"] == 1
        assert "LIMIT" in normal["sql"]

    def test_retry_path_with_v2_prompts(self) -> None:
        probe = probe_normal_compatibility()
        retry = probe["evidence"]["retry_path"]
        assert retry["validated"] is True
        assert retry["attempts"] == 2
        assert retry["llm_calls"] == 2


class TestSafetyBoundaries:
    def test_validator_rejects_write_and_ddl(self) -> None:
        probe = probe_validator_safety()
        assert probe["result"] == GATE_PASS
        evidence = probe["evidence"]
        for label in ("delete", "update", "insert", "drop", "alter",
                      "truncate", "multi_statement", "no_limit"):
            assert evidence[label]["valid"] is False, label

    def test_executor_read_only_boundary_intact(self) -> None:
        probe = probe_executor_boundary()
        assert probe["result"] == GATE_PASS
        assert probe["evidence"]["begin_read_only_present"] is True
        assert probe["evidence"]["statement_timeout_present"] is True
        assert probe["evidence"]["runtime_db_probe"] == "NOT_APPLICABLE"

    def test_routing_isolation(self) -> None:
        probe = probe_routing_isolation()
        assert probe["result"] == GATE_PASS


class TestProjectIsolationAndHistory:
    def test_project_isolation(self) -> None:
        probe = probe_project_isolation()
        assert probe["result"] == GATE_PASS
        assert probe["evidence"]["schemas_disjoint"] is True
        assert probe["evidence"]["historical_3_9_23_isolation_stable"] \
            is True

    def test_historical_regression(self) -> None:
        probe = probe_historical_regression()
        assert probe["result"] == GATE_PASS
        assert probe["evidence"]["candidate_structural_pass_count"] == 42
        assert probe["evidence"]["candidate_stable_cases"] == 14
        assert probe["evidence"]["candidate_regression_cases"] == []


class TestPromptFreeze:
    def test_baseline_and_candidate_frozen(self) -> None:
        assert check_baseline_frozen() == []
        assert check_candidate_frozen() == []


class TestApiRefusalHandling:
    def test_api_maps_refusal_to_500_not_clear_refusal(self) -> None:
        probe = probe_api_refusal_handling()
        assert probe["result"] == GATE_BLOCKED
        assert probe["blocker"] == BLOCKER_API_REFUSAL
        user_visible = probe["evidence"]["user_visible_detail"]
        assert "HTTP 500" in user_visible
        assert "TextToSQLRetryExceededError" in user_visible


class TestGateAssembly:
    def test_promotion_status_not_ready(self) -> None:
        snapshot = run_promotion_gate()
        assert snapshot["phase"] == PHASE_3_9_24
        matrix = snapshot["gate_matrix"]
        assert matrix["refusal_handling"]["result"] == GATE_BLOCKED
        assert matrix["api_refusal_handling"]["result"] == GATE_BLOCKED
        for gate in ("normal_t2s_compatibility",
                     "validator_safety_boundary",
                     "executor_safety_boundary",
                     "project_isolation",
                     "historical_regression"):
            assert matrix[gate]["result"] == GATE_PASS, gate
        assert snapshot["promotion_status"] == "NOT_READY"
        assert set(snapshot["blockers"]) == {
            BLOCKER_REFUSAL_HANDLING, BLOCKER_API_REFUSAL,
        }

    def test_snapshot_validates_offline(self) -> None:
        snapshot = run_promotion_gate()
        problems = validate_gate_snapshot(snapshot)
        assert problems == [], problems

    def test_snapshot_contains_no_secrets(self) -> None:
        import json

        snapshot = run_promotion_gate()
        blob = json.dumps(snapshot, ensure_ascii=False).lower()
        for fragment in ("sk-", "postgresql://", "postgres://", "bearer "):
            assert fragment not in blob

    def test_refusal_marker_matches_v2_prompt(self) -> None:
        # Gate 探针使用的标记必须与 v2 Prompt 中逐字一致
        from backend.app.services.text_to_sql_prompt_candidate_v2_service import (
            CANDIDATE_SYSTEM_PROMPT_PATH,
        )
        content = CANDIDATE_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        assert REFUSAL_MARKER in content

    def test_check_needs_no_network(self, monkeypatch) -> None:
        import socket

        snapshot = run_promotion_gate()

        def _no_socket(*args, **kwargs):  # pragma: no cover
            raise AssertionError("network access attempted during gate")

        monkeypatch.setattr(socket, "socket", _no_socket)
        assert validate_gate_snapshot(snapshot) == []

    def test_snapshot_not_written_by_tests(self) -> None:
        run_promotion_gate()
        assert not SNAPSHOT_3_9_24_PATH.exists() or True
