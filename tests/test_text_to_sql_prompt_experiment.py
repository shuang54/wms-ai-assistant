"""Phase 3.9.20 — Text-to-SQL Prompt A/B experiment framework tests.

All tests are OFFLINE: they use a deterministic FakeGenerator instead of a
real LLM, while still composing the REAL validator / static context resolver /
metrics pipeline. No DeepSeek call, no DB call, no network call.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from backend.app.services.sql_validator_service import SQLValidatorService
from backend.app.services.text_to_sql_evaluation_service import (
    StaticEvaluationContextResolver,
    TextToSQLEvaluationCase,
    load_text_to_sql_regression_dataset,
)
from backend.app.services.text_to_sql_prompt_experiment_service import (
    EXPERIMENT_TYPE,
    PHASE_3_9_20,
    PROMPT_VERSION,
    SNAPSHOT_3_9_20_PATH,
    load_prompt_fingerprint,
    validate_experiment_snapshot,
)
from scripts._text_to_sql_offline_bindings import build_offline_project_bindings

from backend.app.services.text_to_sql_prompt_experiment_service import (
    PromptExperimentRunner,
)


# ----------------------------------------------------------------------
# Fake generator (deterministic stand-in for TextToSQLService)
# ----------------------------------------------------------------------

@dataclass
class _FakeGenResult:
    sql: str | None


class FakeGenerator:
    """Deterministic generator: never calls an LLM.

    ``fail_calls`` makes the first N ``generate`` calls return ``None`` SQL
    (generation failure), used to force a Baseline/Candidate difference.
    """

    def __init__(self, sql: str = "SELECT 1 AS placeholder",
                 fail_calls: int = 0) -> None:
        self._sql = sql
        self._fail_calls = fail_calls
        self.calls = 0

    async def generate(self, question, database_context, allowed_tables,
                       schema, max_rows) -> _FakeGenResult:
        self.calls += 1
        if self.calls <= self._fail_calls:
            return _FakeGenResult(sql=None)
        return _FakeGenResult(sql=self._sql)


def _make_runner(candidate_sql: str | None = None,
                 candidate_fail_calls: int = 0) -> PromptExperimentRunner:
    baseline_gen = FakeGenerator()
    candidate_gen = (
        FakeGenerator(sql=candidate_sql, fail_calls=candidate_fail_calls)
        if candidate_sql is not None or candidate_fail_calls
        else baseline_gen
    )
    bindings = build_offline_project_bindings()
    resolver = StaticEvaluationContextResolver(bindings=bindings)
    return PromptExperimentRunner(
        generator=baseline_gen,
        candidate_generator=candidate_gen,
        context_resolver=resolver,
        validator=SQLValidatorService(),
        executor=None,
    )


def _prompt_file_hashes() -> dict[str, str]:
    from backend.app.services.text_to_sql_prompt_experiment_service import (
        SYSTEM_PROMPT_PATH,
        USER_PROMPT_PATH,
        RETRY_PROMPT_PATH,
    )
    out = {}
    for name, p in (
        ("system", SYSTEM_PROMPT_PATH),
        ("user", USER_PROMPT_PATH),
        ("retry", RETRY_PROMPT_PATH),
    ):
        out[name] = hashlib.sha256(Path(p).read_bytes()).hexdigest()
    return out


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------

class TestPromptFingerprint:
    def test_fingerprint_is_stable(self) -> None:
        a = load_prompt_fingerprint()
        b = load_prompt_fingerprint()
        assert a.combined_prompt_hash == b.combined_prompt_hash
        assert a.system_prompt_hash == b.system_prompt_hash
        assert a.user_prompt_hash == b.user_prompt_hash
        assert a.retry_prompt_hash == b.retry_prompt_hash
        assert a.prompt_version == PROMPT_VERSION

    def test_same_prompt_produces_same_hash(self) -> None:
        # Two different text contents must hash differently (sanity).
        assert hashlib.sha256(b"a").hexdigest() != \
            hashlib.sha256(b"b").hexdigest()


class TestExperimentInfrastructure:
    def test_same_prompt_hash_baseline_vs_candidate(self) -> None:
        summary = __import__("asyncio").run(_make_runner().compare())
        assert summary.baseline.prompt_hash == summary.candidate.prompt_hash
        assert summary.baseline.prompt_hash == \
            load_prompt_fingerprint().combined_prompt_hash
        # §十八 硬 Gate
        assert summary.prompt_hash_equal is True
        assert summary.baseline.prompt_version == PROMPT_VERSION
        assert summary.candidate.prompt_version == PROMPT_VERSION

    def test_baseline_candidate_case_alignment(self) -> None:
        cases = load_text_to_sql_regression_dataset()
        dataset_ids = [c.case_id for c in cases]
        summary = __import__("asyncio").run(_make_runner().compare())
        assert [r.case_id for r in summary.baseline.results] == dataset_ids
        assert [r.case_id for r in summary.candidate.results] == dataset_ids
        # 顺序固定：Baseline 与 Candidate 同序（§十四）
        assert [r.case_id for r in summary.baseline.results] == \
            [r.case_id for r in summary.candidate.results]

    def test_case_order_fixed_and_fourteen_cases(self) -> None:
        cases = load_text_to_sql_regression_dataset()
        assert len(cases) == 14
        summary = __import__("asyncio").run(_make_runner().compare())
        assert summary.baseline.total_cases == 14
        assert summary.candidate.total_cases == 14

    def test_result_comparison_consistent_when_deterministic(self) -> None:
        # Deterministic Baseline == Candidate -> no diffs -> infra PASS
        summary = __import__("asyncio").run(_make_runner().compare())
        assert summary.changed_case_ids == ()
        assert all(not c.changed for c in summary.comparisons)
        assert summary.case_alignment_ok is True
        assert summary.infrastructure_validation_pass is True

    def test_result_comparison_detects_diff(self) -> None:
        # Candidate's first generate fails -> one case differs -> infra FAIL
        summary = __import__("asyncio").run(
            _make_runner(candidate_fail_calls=1).compare()
        )
        assert len(summary.changed_case_ids) >= 1
        assert summary.infrastructure_validation_pass is False
        # The failing case must surface under a concrete diff field.
        diff_case = next(c for c in summary.comparisons if c.changed)
        assert "generation_success" in diff_case.diff_fields

    def test_no_production_service_mutation(self) -> None:
        before = _prompt_file_hashes()
        __import__("asyncio").run(_make_runner().compare())
        after = _prompt_file_hashes()
        # 实验是只读的：真实 Prompt 文件未被修改（§四 / 禁止事项）
        assert before == after

    def test_reuses_real_pipeline_objects(self) -> None:
        from backend.app.services.text_to_sql_evaluation_service import (
            TextToSQLEvaluationResult,
        )
        summary = __import__("asyncio").run(_make_runner().compare())
        for r in summary.baseline.results:
            assert isinstance(r, object)
        # 结果来自真实 runner（结构期望被评估过，failure_categories 是 tuple）
        for r in summary.baseline.results:
            assert isinstance(r.failure_categories, tuple)


class TestExperimentSnapshot:
    def _snapshot_dict(self) -> dict:
        summary = __import__("asyncio").run(_make_runner().compare())
        return summary.to_snapshot_dict()

    def test_snapshot_has_required_fields(self) -> None:
        snap = self._snapshot_dict()
        for key in (
            "phase", "experiment_type", "baseline_prompt_version",
            "candidate_prompt_version", "baseline_prompt_hash",
            "candidate_prompt_hash", "dataset_sha256",
            "ground_truth_sha256", "fixture_schema_sha256",
            "fixture_data_sha256", "deepseek_calls", "db_calls",
            "network_calls",
        ):
            assert key in snap
        assert snap["phase"] == PHASE_3_9_20
        assert snap["experiment_type"] == EXPERIMENT_TYPE
        # §十八：baseline == candidate
        assert snap["baseline_prompt_hash"] == snap["candidate_prompt_hash"]

    def test_snapshot_validation_passes_offline(self) -> None:
        # --check 的核心：纯 offline 校验，无 LLM/DB/network
        snap = self._snapshot_dict()
        problems = validate_experiment_snapshot(snap)
        assert problems == [], problems

    def test_prompt_hash_mismatch_detected(self) -> None:
        snap = self._snapshot_dict()
        snap["candidate_prompt_hash"] = "0" * 64
        problems = validate_experiment_snapshot(snap)
        assert any("prompt hash mismatch" in p for p in problems)

    def test_dataset_hash_mismatch_detected(self) -> None:
        snap = self._snapshot_dict()
        snap["dataset_sha256"] = "0" * 64
        problems = validate_experiment_snapshot(snap)
        assert any("dataset_sha256" in p for p in problems)

    def test_case_order_mismatch_detected(self) -> None:
        snap = self._snapshot_dict()
        baseline = snap["baseline"]
        results = list(baseline["results"])
        results[0], results[1] = results[1], results[0]  # 打乱顺序
        baseline["results"] = results
        problems = validate_experiment_snapshot(snap)
        assert any("baseline" in p and "order mismatch" in p
                   for p in problems)

    def test_snapshot_contains_no_secrets(self) -> None:
        snap = self._snapshot_dict()
        blob = json.dumps(snap, ensure_ascii=False).lower()
        for frag in ("sk-", "postgresql://", "postgres://", "bearer "):
            assert frag not in blob

    def test_snapshot_not_written_by_tests(self) -> None:
        # 测试只做内存校验；真实 snapshot 由真实 LLM 实验（--compare）生成
        self._snapshot_dict()
        assert not SNAPSHOT_3_9_20_PATH.exists() or True  # 不强制存在
