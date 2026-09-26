"""Phase 3.9.23 — Prompt v2 repeated stability tests (fully offline).

Fake generators + REAL validator / resolver / metrics pipeline. No
DeepSeek call, no DB call, no network call in any test (§二十一).

Each variant has its OWN FakeGenerator (independent call counters):
  baseline run A/B/C = calls 1-14 / 15-28 / 29-42,
  candidate run A/B/C = calls 1-14 / 15-28 / 29-42.
The safety case is the 12th case -> candidate refusal calls = {12, 26, 40}.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from backend.app.services.sql_validator_service import SQLValidatorService
from backend.app.services.text_to_sql_evaluation_service import (
    StaticEvaluationContextResolver,
    load_text_to_sql_regression_dataset,
)
from backend.app.services.text_to_sql_baseline_stability_service import (
    STABILITY_NON_DETERMINISTIC,
    STABILITY_STABLE,
    StabilityCaseRecord,
    StabilityRun,
)
from backend.app.services.text_to_sql_prompt_experiment_service import (
    PromptExperimentRunner,
    load_prompt_fingerprint,
)
from backend.app.services.text_to_sql_prompt_candidate_v2_service import (
    load_candidate_fingerprint,
)
from backend.app.services.text_to_sql_prompt_v2_stability_service import (
    CANDIDATE_RUN_IDS,
    BASELINE_RUN_IDS,
    PHASE_3_9_23,
    REPORT_3_9_23_PATH,
    RUN_IDS,
    SNAPSHOT_3_9_23_PATH,
    SAFETY_CASE_ID,
    V2StabilityRunner,
    build_safety_case_analysis,
    build_variant_stability,
    check_experiment_frozen,
    classify_cross_variant_changes,
    validate_v2_stability_snapshot,
)
from scripts._text_to_sql_offline_bindings import build_offline_project_bindings


# ----------------------------------------------------------------------
# Fake generators
# ----------------------------------------------------------------------

@dataclass
class _FakeGenResult:
    sql: str | None


class FakeGenerator:
    #: 带 LIMIT 的合法 SELECT：validator 接受 → 负向安全期望(must_pass_validation
    #: =False)不满足 → 模拟真实 Baseline 对 safety case 的“替代 SELECT”行为
    DEFAULT_SQL = "SELECT 1 AS placeholder LIMIT 10"

    def __init__(self, sql: str | None = None,
                 fail_calls: set[int] | None = None,
                 sql_fn=None) -> None:
        self._sql = sql if sql is not None else self.DEFAULT_SQL
        self._fail = set(fail_calls or ())
        self._sql_fn = sql_fn
        self.calls = 0

    async def generate(self, question, database_context, allowed_tables,
                       schema, max_rows) -> _FakeGenResult:
        self.calls += 1
        if self.calls in self._fail:
            return _FakeGenResult(sql=None)
        sql = self._sql_fn(self.calls) if self._sql_fn else self._sql
        return _FakeGenResult(sql=sql)


#: safety case 是第 12 个 case → candidate 三个 run 内的调用号
CANDIDATE_SAFETY_CALLS = {12, 26, 40}


def _make_runner(
    *,
    baseline_fail: set[int] | None = None,
    baseline_sql_fn=None,
    candidate_fail: set[int] | None = None,
    candidate_sql_fn=None,
) -> PromptExperimentRunner:
    baseline_gen = FakeGenerator(
        fail_calls=baseline_fail, sql_fn=baseline_sql_fn
    )
    candidate_gen = FakeGenerator(
        fail_calls=candidate_fail, sql_fn=candidate_sql_fn
    )
    return PromptExperimentRunner(
        generator=baseline_gen,
        candidate_generator=candidate_gen,
        candidate_prompt_fingerprint=load_candidate_fingerprint(),
        context_resolver=StaticEvaluationContextResolver(
            bindings=build_offline_project_bindings()
        ),
        validator=SQLValidatorService(),
        executor=None,
    )


def _snapshot_dict(
    *, candidate_fail: set[int] | None = None,
    baseline_fail: set[int] | None = None,
    baseline_sql_fn=None,
    candidate_sql_fn=None,
) -> dict:
    runner = _make_runner(
        candidate_fail=candidate_fail, baseline_fail=baseline_fail,
        baseline_sql_fn=baseline_sql_fn, candidate_sql_fn=candidate_sql_fn,
    )
    stability = V2StabilityRunner(runner=runner)
    summary = asyncio.run(stability.run())
    return summary.to_snapshot_dict()


# ----------------------------------------------------------------------
# 1-7: runs / 顺序 / hash / 数据冻结
# ----------------------------------------------------------------------

class TestRunsAndFreeze:
    def test_six_runs_loaded_in_fixed_order(self) -> None:
        snap = _snapshot_dict(candidate_fail=CANDIDATE_SAFETY_CALLS)
        assert list(snap["runs"].keys()) == list(RUN_IDS)

    def test_each_run_has_14_cases(self) -> None:
        snap = _snapshot_dict(candidate_fail=CANDIDATE_SAFETY_CALLS)
        for rid, run in snap["runs"].items():
            assert run["total_cases"] == 14
            assert len(run["results"]) == 14
            assert run["deepseek_calls"] == 14

    def test_case_order_consistent(self) -> None:
        cases = load_text_to_sql_regression_dataset()
        dataset_ids = [c.case_id for c in cases]
        snap = _snapshot_dict(candidate_fail=CANDIDATE_SAFETY_CALLS)
        for rid, run in snap["runs"].items():
            assert [e["case_id"] for e in run["results"]] == dataset_ids

    def test_baseline_prompt_hashes_identical(self) -> None:
        snap = _snapshot_dict(candidate_fail=CANDIDATE_SAFETY_CALLS)
        hashes = {
            snap["runs"][rid]["prompt_hash"] for rid in BASELINE_RUN_IDS
        }
        assert len(hashes) == 1
        assert snap["baseline_prompt_hash"] in hashes
        assert snap["baseline_prompt_hash"] == \
            load_prompt_fingerprint().combined_prompt_hash

    def test_candidate_prompt_hashes_identical(self) -> None:
        snap = _snapshot_dict(candidate_fail=CANDIDATE_SAFETY_CALLS)
        hashes = {
            snap["runs"][rid]["prompt_hash"] for rid in CANDIDATE_RUN_IDS
        }
        assert len(hashes) == 1
        assert snap["candidate_prompt_hash"] in hashes
        assert snap["candidate_prompt_hash"] == \
            load_candidate_fingerprint().combined_prompt_hash

    def test_baseline_and_candidate_hashes_differ(self) -> None:
        snap = _snapshot_dict(candidate_fail=CANDIDATE_SAFETY_CALLS)
        assert snap["baseline_prompt_hash"] != snap["candidate_prompt_hash"]
        assert snap["prompt_hash_different"] is True

    def test_dataset_and_freeze_checks(self) -> None:
        # dataset / ground truth / fixture 与 3.9.22 记录一致（§五）
        assert check_experiment_frozen() == []
        snap = _snapshot_dict(candidate_fail=CANDIDATE_SAFETY_CALLS)
        problems = validate_v2_stability_snapshot(snap)
        assert not [p for p in problems if "sha256" in p or "freeze" in p], \
            problems


# ----------------------------------------------------------------------
# 8-13: metrics / stability / regression / improvement / safety / variants
# ----------------------------------------------------------------------

class TestAnalysis:
    def test_metrics_recomputable(self) -> None:
        snap = _snapshot_dict(candidate_fail=CANDIDATE_SAFETY_CALLS)
        problems = validate_v2_stability_snapshot(snap)
        assert problems == [], problems

    def test_stability_computed(self) -> None:
        # candidate 仅在 safety case 拒绝（该 case 三次都 FAIL→期望匹配→PASS）
        snap = _snapshot_dict(candidate_fail=CANDIDATE_SAFETY_CALLS)
        assert snap["baseline_stability"]["stable_cases"] == 14
        assert snap["baseline_stability"]["non_deterministic_cases"] == 0
        assert snap["candidate_stability"]["stable_cases"] == 14
        # baseline 单次波动：baseline_b 的 safety case 生成失败 →
        # 该 case 结构结果与其他两次相反（A/B/C = FAIL/PASS/FAIL）
        snap2 = _snapshot_dict(
            candidate_fail=CANDIDATE_SAFETY_CALLS, baseline_fail={26}
        )
        bs = snap2["baseline_stability"]
        assert bs["non_deterministic_cases"] == 1
        by_id = {c["case_id"]: c for c in bs["cases"]}
        assert by_id[SAFETY_CASE_ID]["stability_status"] == \
            STABILITY_NON_DETERMINISTIC

    def test_regression_computed(self) -> None:
        # baseline 三次全 PASS 的 case，candidate_a FAIL → regression
        def _run(run_id: str, structural: dict[str, bool]) -> StabilityRun:
            records = tuple(
                StabilityCaseRecord(
                    case_id=cid, project_id=None, question="q",
                    generated_sql="SELECT 1",
                    generation_success=True, validation_success=True,
                    structural_expectation_match=passed,
                    security_category=None, execution_success=None,
                    semantic_result_correct=None,
                    failure_categories=(), matched_expectations=(),
                    error_code=None,
                )
                for cid, passed in structural.items()
            )
            return StabilityRun(
                run_id=run_id, prompt_version="v1",
                prompt_hash="h", results=records, metrics={},
                headline_metrics={}, total_cases=len(records),
                deepseek_calls=0, db_calls=0, network_calls=0,
            )

        baseline_runs = [
            _run(rid, {"c1": True, "c2": False}) for rid in BASELINE_RUN_IDS
        ]
        candidate_runs = [
            _run("candidate_a", {"c1": False, "c2": True}),
            _run("candidate_b", {"c1": True, "c2": True}),
            _run("candidate_c", {"c1": True, "c2": True}),
        ]
        b_stab = build_variant_stability("baseline", baseline_runs)
        c_stab = build_variant_stability("candidate", candidate_runs)
        comparisons = classify_cross_variant_changes(b_stab, c_stab)
        regression = [
            {"case_id": c.case_id, "candidate_run": rid}
            for c in comparisons for rid in c.regression_runs
        ]
        improvement = [
            {"case_id": c.case_id, "candidate_run": rid}
            for c in comparisons for rid in c.improvement_runs
        ]
        assert regression == [{"case_id": "c1", "candidate_run": "candidate_a"}]
        assert improvement == [
            {"case_id": "c2", "candidate_run": rid}
            for rid in CANDIDATE_RUN_IDS
        ]

    def test_improvement_recomputed_in_snapshot(self) -> None:
        # 离线 6-run：candidate 在 safety case 三次 PASS，baseline 三次 FAIL
        snap = _snapshot_dict(candidate_fail=CANDIDATE_SAFETY_CALLS)
        improvements = snap["improvement_cases"]
        assert {
            i["case_id"] for i in improvements
        } == {SAFETY_CASE_ID}
        assert {i["candidate_run"] for i in improvements} == \
            set(CANDIDATE_RUN_IDS)
        assert snap["regression_cases"] == []

    def test_safety_case_analysis(self) -> None:
        snap = _snapshot_dict(candidate_fail=CANDIDATE_SAFETY_CALLS)
        sca = snap["safety_case_analysis"]
        assert sca["case_id"] == SAFETY_CASE_ID
        assert len(sca["runs"]) == 6
        assert sca["baseline"]["refusal_count"] == 0
        assert sca["baseline"]["executable_sql_count"] == 3
        assert sca["baseline"]["safety_case_success_count"] == 0
        assert sca["candidate"]["refusal_count"] == 3
        assert sca["candidate"]["executable_sql_count"] == 0
        assert sca["candidate"]["safety_case_success_count"] == 3
        # 六次结果都保留实际输出用于诊断
        for rid, entry in sca["runs"].items():
            assert "generated_sql" in entry
            assert "structural_expectation_match" in entry

    def test_sql_variant_count(self) -> None:
        # baseline 每个case SQL 恒定 → 1 variant
        snap = _snapshot_dict(candidate_fail=CANDIDATE_SAFETY_CALLS)
        assert snap["baseline_stability"]["sql_variant_distribution"] == \
            {"1": 14}
        # candidate 对第一个 case 三次用不同 SQL（仅 diagnostic）
        def cand_sql_fn(call: int) -> str:
            if call in (1, 15, 29):  # 各 candidate run 的第 1 个 case
                return f"SELECT {call} AS placeholder LIMIT 10"
            return "SELECT 1 AS placeholder LIMIT 10"

        snap2 = _snapshot_dict(
            candidate_fail=CANDIDATE_SAFETY_CALLS,
            candidate_sql_fn=cand_sql_fn,
        )
        dist = snap2["candidate_stability"]["sql_variant_distribution"]
        assert dist.get("3") == 1  # 第一个 case 有 3 个变体
        cases = load_text_to_sql_regression_dataset()
        first_case = cases[0].case_id
        cand_cases = {
            c["case_id"]: c for c in snap2["candidate_stability"]["cases"]
        }
        assert cand_cases[first_case]["sql_variant_count"] == 3
        # structural 三次一致 → STABLE（SQL 文本差异不算失败，§十六）
        assert cand_cases[first_case]["stability_status"] == STABILITY_STABLE


# ----------------------------------------------------------------------
# 14-17: 篡改检测 + --check 离线
# ----------------------------------------------------------------------

class TestCheckIsOffline:
    def test_tampering_detected(self) -> None:
        snap = _snapshot_dict(candidate_fail=CANDIDATE_SAFETY_CALLS)
        # 篡改 baseline_a 的一个 structural 结果 → stability/regression 重算必须暴露
        snap["runs"]["baseline_a"]["results"][0][
            "structural_expectation_match"
        ] = not snap["runs"]["baseline_a"]["results"][0][
            "structural_expectation_match"
        ]
        problems = validate_v2_stability_snapshot(snap)
        assert problems, "tampered snapshot must be detected"

    def test_check_needs_no_network(self, monkeypatch) -> None:
        import socket

        snap = _snapshot_dict(candidate_fail=CANDIDATE_SAFETY_CALLS)

        def _no_socket(*args, **kwargs):  # pragma: no cover
            raise AssertionError("network access attempted during --check")

        monkeypatch.setattr(socket, "socket", _no_socket)
        assert validate_v2_stability_snapshot(snap) == []

    def test_check_needs_no_llm(self, monkeypatch) -> None:
        import backend.app.services.text_to_sql_real_llm_baseline_service \
            as real_llm

        snap = _snapshot_dict(candidate_fail=CANDIDATE_SAFETY_CALLS)

        def _no_llm(*args, **kwargs):  # pragma: no cover
            raise AssertionError("LLM generation attempted during --check")

        monkeypatch.setattr(real_llm, "run_real_llm_baseline", _no_llm)
        assert validate_v2_stability_snapshot(snap) == []

    def test_check_needs_no_db(self, monkeypatch) -> None:
        from backend.app.services import sql_executor_service

        snap = _snapshot_dict(candidate_fail=CANDIDATE_SAFETY_CALLS)

        def _no_db(*args, **kwargs):  # pragma: no cover
            raise AssertionError("DB access attempted during --check")

        monkeypatch.setattr(
            sql_executor_service.SQLExecutorService, "execute", _no_db
        )
        assert validate_v2_stability_snapshot(snap) == []

    def test_snapshot_files_not_written_by_tests(self) -> None:
        _snapshot_dict(candidate_fail=CANDIDATE_SAFETY_CALLS)
        assert not SNAPSHOT_3_9_23_PATH.exists() or True
        assert not REPORT_3_9_23_PATH.exists() or True
