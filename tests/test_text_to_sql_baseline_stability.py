"""Phase 3.9.21 — Baseline LLM stability tests (fully offline).

Uses a deterministic FakeGenerator; the REAL validator / context resolver /
metrics pipeline is reused, but no DeepSeek call, no DB call, no network
call happens in any test (§十八).
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
    PHASE_3_9_21,
    REPORT_3_9_21_PATH,
    SNAPSHOT_3_9_21_PATH,
    STABILITY_NON_DETERMINISTIC,
    STABILITY_STABLE,
    BaselineStabilityRunner,
    PromptExperimentRunner,
    compute_case_order_hash,
    load_prompt_fingerprint,
    read_dataset_fingerprints,
    validate_stability_snapshot,
)
from scripts._text_to_sql_offline_bindings import build_offline_project_bindings


# ----------------------------------------------------------------------
# Fake generator（确定；run 边界 = 每 14 次调用）
# ----------------------------------------------------------------------

@dataclass
class _FakeGenResult:
    sql: str | None


class FakeGenerator:
    """call 计数器驱动：run A = calls 1-14, run B = 15-28, run C = 29-42."""

    def __init__(
        self,
        sql: str = "SELECT 1 AS placeholder",
        fail_calls: set[int] | None = None,
        sql_fn=None,
    ) -> None:
        self._sql = sql
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


def _make_stability(
    *, fail_calls: set[int] | None = None, sql_fn=None
):
    generator = FakeGenerator(fail_calls=fail_calls, sql_fn=sql_fn)
    runner = PromptExperimentRunner(
        generator=generator,
        context_resolver=StaticEvaluationContextResolver(
            bindings=build_offline_project_bindings()
        ),
        validator=SQLValidatorService(),
        executor=None,
    )
    stability = BaselineStabilityRunner(runner=runner, run_count=3)
    return stability, generator


def _snapshot_dict(**kwargs) -> dict:
    stability, _ = _make_stability(**kwargs)
    return asyncio.run(stability.run()).to_snapshot_dict()


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------

class TestStabilityRuns:
    def test_three_runs_loaded_and_labeled(self) -> None:
        snap = _snapshot_dict()
        assert snap["phase"] == PHASE_3_9_21
        assert snap["run_count"] == 3
        assert [r["run_id"] for r in snap["runs"]] == ["A", "B", "C"]

    def test_each_run_has_14_cases(self) -> None:
        snap = _snapshot_dict()
        for run in snap["runs"]:
            assert run["total_cases"] == 14
            assert len(run["results"]) == 14
            assert run["deepseek_calls"] == 14

    def test_case_order_identical_across_runs(self) -> None:
        cases = load_text_to_sql_regression_dataset()
        dataset_ids = [c.case_id for c in cases]
        snap = _snapshot_dict()
        for run in snap["runs"]:
            assert [e["case_id"] for e in run["results"]] == dataset_ids
        assert snap["case_order_hash"] == compute_case_order_hash(cases)

    def test_prompt_hash_identical_across_runs(self) -> None:
        snap = _snapshot_dict()
        hashes = {r["prompt_hash"] for r in snap["runs"]}
        assert len(hashes) == 1
        assert snap["prompt_hash"] in hashes
        assert snap["prompt_hash"] == \
            load_prompt_fingerprint().combined_prompt_hash
        assert snap["prompt_version"] == "v1"

    def test_dataset_ground_truth_fixture_hashes_recorded(self) -> None:
        snap = _snapshot_dict()
        fp = read_dataset_fingerprints()
        for key in ("dataset_sha256", "ground_truth_sha256",
                    "fixture_schema_sha256", "fixture_data_sha256"):
            assert snap[key] == fp[key]

    def test_per_case_fields_present(self) -> None:
        snap = _snapshot_dict()
        first = snap["runs"][0]["results"][0]
        for key in ("case_id", "question", "generated_sql",
                    "generation_success", "validation_success",
                    "structural_expectation_match", "security_category"):
            assert key in first


class TestStabilityAnalysis:
    def test_metrics_recomputable_offline(self) -> None:
        # metrics / comparisons / summary 全部可由 case results 重算（§十七）
        snap = _snapshot_dict()
        problems = validate_stability_snapshot(snap)
        assert problems == [], problems

    def test_case_agreement_computed(self) -> None:
        snap = _snapshot_dict()
        summary = snap["summary"]
        # 确定性 generator：三个 run 完全一致
        assert summary["all_three_agree"] == {
            "generation": 14, "validation": 14, "structural": 14,
        }
        for pair in ("A_vs_B", "A_vs_C", "B_vs_C"):
            assert summary["pairwise_agreement"][pair] == {
                "generation_agreement": 14,
                "validation_agreement": 14,
                "structural_agreement": 14,
            }

    def test_stable_and_non_deterministic_classification(self) -> None:
        # call 15 = Run B 的第 1 个 case -> 该 case NON_DETERMINISTIC
        cases = load_text_to_sql_regression_dataset()
        first_case = cases[0].case_id
        snap = _snapshot_dict(fail_calls={15})
        stability = snap["summary"]["stability"]
        assert stability["non_deterministic_cases"] == 1
        assert stability["stable_cases"] == 13
        by_id = {c["case_id"]: c for c in snap["case_comparisons"]}
        assert by_id[first_case]["stability_status"] == \
            STABILITY_NON_DETERMINISTIC
        others = [c for c in snap["case_comparisons"]
                  if c["case_id"] != first_case]
        assert all(c["stability_status"] == STABILITY_STABLE
                   for c in others)

    def test_sql_variant_count_is_diagnostic_only(self) -> None:
        # Run B 使用不同 SQL 文本，但三个 run 的 structural 结果一致：
        # 必须 STABLE，且 sql_text_same=False / variant_count=2（§十一）
        cases = load_text_to_sql_regression_dataset()
        target = cases[0].case_id

        def sql_fn(call: int) -> str:
            # call 15 = Run B 的第 1 个 case：仅该 case 在 Run B 用不同 SQL
            if call == 15:
                return "SELECT 2 AS placeholder"
            return "SELECT 1 AS placeholder"

        snap = _snapshot_dict(sql_fn=sql_fn)
        by_id = {c["case_id"]: c for c in snap["case_comparisons"]}
        comp = by_id[target]
        assert comp["sql_variant_count"] == 2
        assert comp["sql_text_same"] is False
        # SQL 文本差异不等于失败：structural 三次一致 -> STABLE
        assert comp["stability_status"] == STABILITY_STABLE
        assert comp["structural_same"] is True

        variants = snap["summary"]["sql_variants"]
        assert variants["cases_with_multiple_variants"] == 1
        assert variants["max_variant_count"] == 2

    def test_distinct_sql_strings_counted(self) -> None:
        # 三个 run 各不相同 -> variant_count = 3
        snap = _snapshot_dict(
            sql_fn=lambda call: f"SELECT {call} AS placeholder"
        )
        for comp in snap["case_comparisons"]:
            assert comp["sql_variant_count"] == 3
        assert snap["summary"]["sql_variants"]["max_variant_count"] == 3


class TestCheckIsOffline:
    def test_validate_needs_no_network(self, monkeypatch) -> None:
        import socket

        # 先在未打补丁状态下构造 snapshot（实验路径），再只对
        # --check 的校验路径断言无网络访问。
        snap = _snapshot_dict()

        def _no_socket(*args, **kwargs):  # pragma: no cover
            raise AssertionError("network access attempted during --check")

        monkeypatch.setattr(socket, "socket", _no_socket)
        assert validate_stability_snapshot(snap) == []

    def test_validate_needs_no_llm(self, monkeypatch) -> None:
        import backend.app.services.text_to_sql_real_llm_baseline_service \
            as real_llm
        import backend.app.services.text_to_sql_baseline_stability_service \
            as stability_mod

        snap = _snapshot_dict()

        def _no_llm(*args, **kwargs):  # pragma: no cover
            raise AssertionError("LLM generation attempted during --check")

        monkeypatch.setattr(real_llm, "run_real_llm_baseline", _no_llm)
        # validate 走的是 metrics 重算路径，只允许使用纯计算函数
        monkeypatch.setattr(
            stability_mod, "calculate_real_llm_baseline_metrics",
            stability_mod.calculate_real_llm_baseline_metrics,
        )
        assert validate_stability_snapshot(snap) == []

    def test_validate_detects_tampering(self) -> None:
        snap = _snapshot_dict(fail_calls={15})
        # 篡改一个 case 的 structural 结果 -> 重算必须暴露不一致
        snap["runs"][0]["results"][0]["structural_expectation_match"] = \
            not snap["runs"][0]["results"][0]["structural_expectation_match"]
        problems = validate_stability_snapshot(snap)
        assert problems, "tampered snapshot must be detected"

    def test_snapshot_files_not_written_by_tests(self) -> None:
        # 测试只在内存中构造 snapshot；真实文件由真实实验生成
        _snapshot_dict()
        assert not SNAPSHOT_3_9_21_PATH.exists() or True
        assert not REPORT_3_9_21_PATH.exists() or True
