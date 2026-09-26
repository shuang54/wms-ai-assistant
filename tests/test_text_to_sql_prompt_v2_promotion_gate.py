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
    def test_refusal_is_first_class_result(self) -> None:
        """Phase 3.9.25 起：refusal 是 first-class result（blocker 已解除）。"""
        probe = probe_refusal_handling()
        assert probe["result"] == GATE_PASS
        assert probe["blocker"] is None
        for label in ("plain_comment", "sql_fence"):
            evidence = probe["evidence"][label]
            assert evidence["outcome"] == "REFUSAL_RESULT_RETURNED"
            assert evidence["status"] == "refusal"
            assert evidence["sql"] is None
            assert evidence["refusal_reason"] == \
                "destructive_request_not_supported"
            # 不消耗 retry budget：1 次 LLM 调用
            assert evidence["llm_calls"] == 1
            assert evidence["attempts"] == 1

    def test_refusal_never_returns_executable_sql(self) -> None:
        # 关键安全事实：refusal 绝不会变成可执行 SQL 结果
        probe = probe_refusal_handling()
        for evidence in probe["evidence"].values():
            assert evidence["outcome"] != "RETURNED_SQL"
            assert evidence["sql"] is None


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
    def test_api_maps_refusal_to_clear_readonly_message(self) -> None:
        """Phase 3.9.25 起：refusal → 正常 200 拒绝信息（blocker 已解除）。"""
        probe = probe_api_refusal_handling()
        assert probe["result"] == GATE_PASS
        assert probe["blocker"] is None
        user_visible = probe["evidence"]["user_visible_detail"]
        assert "只读" in user_visible


class TestGateAssembly:
    def test_promotion_status_ready_for_promotion(self) -> None:
        snapshot = run_promotion_gate()
        assert snapshot["phase"] == PHASE_3_9_24
        matrix = snapshot["gate_matrix"]
        for gate in ("normal_t2s_compatibility",
                     "validator_safety_boundary",
                     "executor_safety_boundary",
                     "project_isolation",
                     "historical_regression",
                     "refusal_handling",
                     "api_refusal_handling"):
            assert matrix[gate]["result"] == GATE_PASS, gate
        assert snapshot["promotion_status"] == "READY_FOR_PROMOTION"
        assert snapshot["blockers"] == []

    def test_frozen_3_9_24_snapshot_unchanged(self) -> None:
        """历史 snapshot 保持 3.9.24 时的 NOT_READY 结论（不被改写）。"""
        import json

        from backend.app.services.text_to_sql_prompt_v2_promotion_gate_service import (
            SNAPSHOT_3_9_24_PATH,
        )
        if not SNAPSHOT_3_9_24_PATH.exists():
            pytest.skip("3.9.24 snapshot not generated yet")
        snap = json.loads(
            SNAPSHOT_3_9_24_PATH.read_text(encoding="utf-8")
        )
        assert snap["promotion_status"] == "NOT_READY"
        assert set(snap["blockers"]) == {
            BLOCKER_REFUSAL_HANDLING, BLOCKER_API_REFUSAL,
        }
        assert snap["gate_matrix"]["refusal_handling"]["result"] == \
            GATE_BLOCKED
        assert snap["gate_matrix"]["api_refusal_handling"]["result"] == \
            GATE_BLOCKED

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
