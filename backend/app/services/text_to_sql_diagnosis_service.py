"""Text-to-SQL Error Diagnosis & Bottleneck Analysis（Phase 3.9.11）。

统一 3.9.5~3.9.10 各阶段的 case-level 证据，回答**一个问题**：

> 当前 Text-to-SQL Pipeline 的失败，到底发生在 Generation、Validation、
> Structural Expectation、Execution、Result Correctness 还是 Security Boundary？

## 纪律

- **纯离线分析**：只读取已 sealed 的 snapshot + 3.9.3 dataset，
  不调用 DeepSeek / 任何 LLM client / ``TextToSQLService.generate()``；
- **不优化**：只回答「问题发生在哪里」，不回答「应该怎么解决」；
- **不评价模型好坏**：只报告 observed metrics / failures / evidence gaps；
- **不新增 failure category**：Security 复用 3.9.6 的 ``security_categories``；
- **不确定就标 UNKNOWN / N/A**，不猜测。

## 数据来源（§6）

| 维度 | 来源 |
| --- | --- |
| Generation | 3.9.9（primary）／ 3.9.5 |
| Validation | 3.9.9（primary）／ 3.9.5 |
| Structural Expectation | 3.9.9（primary）；未声明期望 → N/A |
| Execution | 3.9.9 |
| Result Correctness | 3.9.10 |
| Security | 3.9.6（原样保留，不新增类别） |
| project_id | 3.9.3 dataset |

## Bottleneck（§7，确定性优先级）

只在对应阶段**明确失败**时判定：

    GENERATION → VALIDATION → STRUCTURAL_EXPECTATION → EXECUTION → RESULT_CORRECTNESS

证据不足 → ``UNKNOWN``；全部明确成功/不适用 → ``NONE``。

## Multi-label（§11 / §12）

Failure distribution 是 **multi-label**：同一个 case 可同时命中多个类别
（如安全 case 同时有 EXPECTATION_MISMATCH + SECURITY_LLM_REFUSAL +
SECURITY_EXPECTATION_MISMATCH），**不能把各类别数量相加当作 case 总失败数**。
每个 case 另有一个且只有一个 primary ``bottleneck``（§13）。
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from backend.app.services.text_to_sql_evaluation_service import (
    TextToSQLEvaluationCase,
)

__all__ = [
    # 维度状态
    "GENERATION_SUCCESS",
    "GENERATION_FAILURE",
    "GENERATION_UNKNOWN",
    "VALIDATION_ACCEPTED",
    "VALIDATION_REJECTED",
    "VALIDATION_UNKNOWN",
    "EXPECTATION_MATCH",
    "EXPECTATION_MISMATCH",
    "EXPECTATION_NA",
    "EXPECTATION_UNKNOWN",
    "EXECUTION_SUCCESS",
    "EXECUTION_FAILURE",
    "EXECUTION_NA",
    "EXECUTION_UNKNOWN",
    "RESULT_CORRECT",
    "RESULT_INCORRECT",
    "RESULT_NA",
    "RESULT_UNKNOWN",
    # Bottleneck
    "BOTTLENECK_GENERATION",
    "BOTTLENECK_VALIDATION",
    "BOTTLENECK_STRUCTURAL_EXPECTATION",
    "BOTTLENECK_EXECUTION",
    "BOTTLENECK_RESULT_CORRECTNESS",
    "BOTTLENECK_NONE",
    "BOTTLENECK_UNKNOWN",
    # DTO / Service
    "DiagnosisInput",
    "DiagnosisCase",
    "DiagnosisSummary",
    "TextToSQLDiagnosisService",
    "dataset_sha256",
]


# ============================================================
# 维度状态常量（§6）
# ============================================================

GENERATION_SUCCESS: Final[str] = "SUCCESS"
GENERATION_FAILURE: Final[str] = "FAILURE"
GENERATION_UNKNOWN: Final[str] = "UNKNOWN"

VALIDATION_ACCEPTED: Final[str] = "ACCEPTED"
VALIDATION_REJECTED: Final[str] = "REJECTED"
VALIDATION_UNKNOWN: Final[str] = "UNKNOWN"

EXPECTATION_MATCH: Final[str] = "MATCH"
EXPECTATION_MISMATCH: Final[str] = "MISMATCH"
EXPECTATION_NA: Final[str] = "N/A"
EXPECTATION_UNKNOWN: Final[str] = "UNKNOWN"

EXECUTION_SUCCESS: Final[str] = "SUCCESS"
EXECUTION_FAILURE: Final[str] = "FAILURE"
EXECUTION_NA: Final[str] = "N/A"
EXECUTION_UNKNOWN: Final[str] = "UNKNOWN"

RESULT_CORRECT: Final[str] = "CORRECT"
RESULT_INCORRECT: Final[str] = "INCORRECT"
RESULT_NA: Final[str] = "N/A"
RESULT_UNKNOWN: Final[str] = "UNKNOWN"

# ============================================================
# Bottleneck 常量（§7 / §13）
# ============================================================

BOTTLENECK_GENERATION: Final[str] = "GENERATION"
BOTTLENECK_VALIDATION: Final[str] = "VALIDATION"
BOTTLENECK_STRUCTURAL_EXPECTATION: Final[str] = "STRUCTURAL_EXPECTATION"
BOTTLENECK_EXECUTION: Final[str] = "EXECUTION"
BOTTLENECK_RESULT_CORRECTNESS: Final[str] = "RESULT_CORRECTNESS"
BOTTLENECK_NONE: Final[str] = "NONE"
BOTTLENECK_UNKNOWN: Final[str] = "UNKNOWN"

_BOTTLENECK_ORDER: Final[tuple[str, ...]] = (
    BOTTLENECK_GENERATION,
    BOTTLENECK_VALIDATION,
    BOTTLENECK_STRUCTURAL_EXPECTATION,
    BOTTLENECK_EXECUTION,
    BOTTLENECK_RESULT_CORRECTNESS,
)

_EVALUABLE_RESULT_STATES: Final[frozenset[str]] = frozenset(
    {RESULT_CORRECT, RESULT_INCORRECT}
)


def dataset_sha256(path: str | Path) -> str:
    """计算 dataset SHA256（用于跨阶段完整性核对，§23）。"""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ============================================================
# DTO
# ============================================================

@dataclass(frozen=True)
class DiagnosisInput:
    """统一诊断的输入（全部为已 sealed 的只读数据）。"""

    dataset_cases: tuple[TextToSQLEvaluationCase, ...]
    dataset_path: str
    dataset_version: str
    #: 3.9.5 Real LLM Baseline snapshot（cross-check / project_id）
    baseline_395: Mapping[str, Any]
    #: 3.9.6 Taxonomy snapshot（security_categories 来源）
    taxonomy_396: Mapping[str, Any]
    #: 3.9.9 Quality snapshot（generation / validation / expectation / execution）
    quality_399: Mapping[str, Any]
    #: 3.9.10 Result quality snapshot（result correctness）
    result_3910: Mapping[str, Any]


@dataclass(frozen=True)
class DiagnosisCase:
    """单个 case 的多维度诊断（§9）。"""

    case_id: str
    project_id: str | None
    generation_status: str
    validation_status: str
    expectation_status: str
    execution_status: str
    result_status: str
    security_categories: tuple[str, ...]
    bottleneck: str
    error_code: str | None

    @property
    def evaluable_result(self) -> bool:
        """§10：只有 CORRECT / INCORRECT 才进入 result accuracy 分母。"""
        return self.result_status in _EVALUABLE_RESULT_STATES

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "project_id": self.project_id,
            "generation_status": self.generation_status,
            "validation_status": self.validation_status,
            "expectation_status": self.expectation_status,
            "execution_status": self.execution_status,
            "result_status": self.result_status,
            "security_categories": list(self.security_categories),
            "bottleneck": self.bottleneck,
            "evaluable_result": self.evaluable_result,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class DiagnosisSummary:
    """统一诊断汇总。"""

    phase: str
    dataset_version: str
    dataset_sha256: str
    total_cases: int

    generation_failure_count: int
    validation_failure_count: int
    expectation_mismatch_count: int
    execution_failure_count: int
    result_incorrect_count: int
    security_case_count: int

    bottleneck_distribution: dict[str, int]
    failure_distribution: dict[str, int]
    security_distribution: dict[str, int]
    evidence_gaps: dict[str, int]

    result_evaluable_cases: int
    result_correct_cases: int
    result_incorrect_cases: int
    result_na_cases: int

    cases: tuple[DiagnosisCase, ...] = ()
    pipeline_metrics: dict[str, Any] = field(default_factory=dict)

    # ---------- 派生 ----------

    def case_ids(self) -> tuple[str, ...]:
        return tuple(item.case_id for item in self.cases)

    def failed_cases(self) -> tuple[DiagnosisCase, ...]:
        return tuple(
            item for item in self.cases
            if item.bottleneck != BOTTLENECK_NONE
        )

    def security_cases(self) -> tuple[DiagnosisCase, ...]:
        return tuple(item for item in self.cases if item.security_categories)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "dataset_version": self.dataset_version,
            "dataset_sha256": self.dataset_sha256,
            "total_cases": self.total_cases,
            "generation_failure_count": self.generation_failure_count,
            "validation_failure_count": self.validation_failure_count,
            "expectation_mismatch_count": self.expectation_mismatch_count,
            "execution_failure_count": self.execution_failure_count,
            "result_incorrect_count": self.result_incorrect_count,
            "security_case_count": self.security_case_count,
            "bottleneck_distribution": dict(self.bottleneck_distribution),
            "failure_distribution": dict(self.failure_distribution),
            "security_distribution": dict(self.security_distribution),
            "evidence_gaps": dict(self.evidence_gaps),
            "result_evaluable_cases": self.result_evaluable_cases,
            "result_correct_cases": self.result_correct_cases,
            "result_incorrect_cases": self.result_incorrect_cases,
            "result_na_cases": self.result_na_cases,
            "pipeline_metrics": dict(self.pipeline_metrics),
            "cases": [item.to_dict() for item in self.cases],
        }


# ============================================================
# 抽取辅助（容错：某个 snapshot 缺信息 → UNKNOWN，不猜）
# ============================================================

def _by_case_id(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    entries = payload.get("cases")
    if not isinstance(entries, list):
        return {}
    return {
        str(entry.get("case_id")): dict(entry)
        for entry in entries
        if isinstance(entry, dict) and entry.get("case_id") is not None
    }


def _map_generation(value: Any) -> str:
    normalized = str(value).strip().lower()
    if normalized == "success" or normalized == "generation_success":
        return GENERATION_SUCCESS
    if normalized == "failure" or normalized == "generation_failure":
        return GENERATION_FAILURE
    return GENERATION_UNKNOWN


def _map_validation(value: Any) -> str:
    normalized = str(value).strip().lower()
    if normalized in {"accepted", "validation_accepted"}:
        return VALIDATION_ACCEPTED
    if normalized in {"rejected", "validation_rejected"}:
        return VALIDATION_REJECTED
    return VALIDATION_UNKNOWN


def _map_execution(value: Any) -> str:
    normalized = str(value).strip().lower()
    if normalized in {"success", "execution_success"}:
        return EXECUTION_SUCCESS
    if normalized in {"failure", "execution_failure"}:
        return EXECUTION_FAILURE
    if normalized in {"n/a", "not attempted", "execution_na"}:
        return EXECUTION_NA
    return EXECUTION_UNKNOWN


def _result_status(entry: Mapping[str, Any] | None) -> str:
    if entry is None:
        return RESULT_NA
    if entry.get("applicable") is not True:
        return RESULT_NA
    passed = entry.get("passed")
    if passed is True:
        return RESULT_CORRECT
    if passed is False:
        return RESULT_INCORRECT
    # applicable 但没有可判定结果（如执行失败）→ 无证据
    return RESULT_NA


def _bottleneck(item: "DiagnosisCase") -> str:
    """§7：只在对应阶段**明确失败**时判定；证据不足 → UNKNOWN。"""
    if item.generation_status == GENERATION_FAILURE:
        return BOTTLENECK_GENERATION
    if item.validation_status == VALIDATION_REJECTED:
        return BOTTLENECK_VALIDATION
    if item.expectation_status == EXPECTATION_MISMATCH:
        return BOTTLENECK_STRUCTURAL_EXPECTATION
    if item.execution_status == EXECUTION_FAILURE:
        return BOTTLENECK_EXECUTION
    if item.result_status == RESULT_INCORRECT:
        return BOTTLENECK_RESULT_CORRECTNESS
    insufficient = {
        item.generation_status: GENERATION_UNKNOWN,
        item.validation_status: VALIDATION_UNKNOWN,
        item.expectation_status: EXPECTATION_UNKNOWN,
        item.execution_status: EXECUTION_UNKNOWN,
        item.result_status: RESULT_UNKNOWN,
    }
    for status, unknown in insufficient.items():
        if status == unknown:
            return BOTTLENECK_UNKNOWN
    return BOTTLENECK_NONE


# ============================================================
# Service
# ============================================================

class TextToSQLDiagnosisService:
    """统一诊断（纯离线，只读 sealed snapshot + dataset）。"""

    def consistency_problems(self, payload: DiagnosisInput) -> list[str]:
        """§14：各阶段 case 数量 / case_id 必须一致，否则 STOP。"""
        problems: list[str] = []
        dataset_ids = [case.case_id for case in payload.dataset_cases]
        expected_count = len(payload.dataset_cases)

        sources = {
            "3.9.5 baseline": _by_case_id(payload.baseline_395),
            "3.9.6 taxonomy": _by_case_id(payload.taxonomy_396),
            "3.9.9 quality": _by_case_id(payload.quality_399),
            "3.9.10 result": _by_case_id(payload.result_3910),
        }
        for name, mapping in sources.items():
            if len(mapping) != expected_count:
                problems.append(
                    f"{name} has {len(mapping)} cases, expected "
                    f"{expected_count}"
                )
                continue
            for case_id in dataset_ids:
                if case_id not in mapping:
                    problems.append(
                        f"{name} is missing case_id {case_id!r}"
                    )
        return problems

    def diagnose(self, payload: DiagnosisInput) -> DiagnosisSummary:
        """统一诊断（纯函数组合，无副作用）。"""
        problems = self.consistency_problems(payload)
        if problems:
            raise ValueError(
                "diagnosis aborted: cross-phase case inconsistency: "
                + "; ".join(problems)
            )

        baseline = _by_case_id(payload.baseline_395)
        taxonomy = _by_case_id(payload.taxonomy_396)
        quality = _by_case_id(payload.quality_399)
        result_snapshot = _by_case_id(payload.result_3910)

        cases: list[DiagnosisCase] = []
        for case in payload.dataset_cases:
            quality_entry = quality.get(case.case_id, {})
            taxonomy_entry = taxonomy.get(case.case_id, {})
            baseline_entry = baseline.get(case.case_id, {})
            result_entry = result_snapshot.get(case.case_id)

            # structural expectation：未声明期望 → N/A（§6）
            declared = bool(
                baseline_entry.get("matched_expectations")
                or baseline_entry.get("failed_expectations")
            )
            raw_expectation = quality_entry.get("expectation")
            if not declared:
                expectation_status = EXPECTATION_NA
            elif raw_expectation == "matched":
                expectation_status = EXPECTATION_MATCH
            elif raw_expectation == "mismatched":
                expectation_status = EXPECTATION_MISMATCH
            else:
                expectation_status = EXPECTATION_UNKNOWN

            item = DiagnosisCase(
                case_id=case.case_id,
                project_id=case.project_id,
                generation_status=_map_generation(
                    quality_entry.get("generation")
                ),
                validation_status=_map_validation(
                    quality_entry.get("validation")
                ),
                expectation_status=expectation_status,
                execution_status=_map_execution(
                    quality_entry.get("execution")
                ),
                result_status=_result_status(result_entry),
                security_categories=tuple(
                    taxonomy_entry.get("security_categories") or ()
                ),
                bottleneck="",
                error_code=baseline_entry.get("error_code"),
            )
            cases.append(
                DiagnosisCase(
                    case_id=item.case_id,
                    project_id=item.project_id,
                    generation_status=item.generation_status,
                    validation_status=item.validation_status,
                    expectation_status=item.expectation_status,
                    execution_status=item.execution_status,
                    result_status=item.result_status,
                    security_categories=item.security_categories,
                    bottleneck=_bottleneck(item),
                    error_code=item.error_code,
                )
            )

        bottleneck_distribution = self._distribution(
            cases, lambda c: c.bottleneck, _BOTTLENECK_ORDER
        )
        failure_distribution = self._failure_distribution(cases)
        security_distribution = self._security_distribution(cases)
        evidence_gaps = self._evidence_gaps(cases)

        result_correct = sum(
            1 for item in cases if item.result_status == RESULT_CORRECT
        )
        result_incorrect = sum(
            1 for item in cases if item.result_status == RESULT_INCORRECT
        )
        result_na = sum(
            1 for item in cases if item.result_status == RESULT_NA
        )

        return DiagnosisSummary(
            phase="3.9.11",
            dataset_version=payload.dataset_version,
            dataset_sha256=dataset_sha256(payload.dataset_path),
            total_cases=len(cases),
            generation_failure_count=sum(
                1 for c in cases if c.generation_status == GENERATION_FAILURE
            ),
            validation_failure_count=sum(
                1 for c in cases if c.validation_status == VALIDATION_REJECTED
            ),
            expectation_mismatch_count=sum(
                1 for c in cases
                if c.expectation_status == EXPECTATION_MISMATCH
            ),
            execution_failure_count=sum(
                1 for c in cases if c.execution_status == EXECUTION_FAILURE
            ),
            result_incorrect_count=result_incorrect,
            security_case_count=sum(1 for c in cases if c.security_categories),
            bottleneck_distribution=bottleneck_distribution,
            failure_distribution=failure_distribution,
            security_distribution=security_distribution,
            evidence_gaps=evidence_gaps,
            result_evaluable_cases=sum(
                1 for c in cases if c.evaluable_result
            ),
            result_correct_cases=result_correct,
            result_incorrect_cases=result_incorrect,
            result_na_cases=result_na,
            cases=tuple(cases),
            pipeline_metrics=dict(payload.quality_399.get("metrics") or {}),
        )

    @staticmethod
    def _distribution(
        cases: Sequence[DiagnosisCase], key, order: Sequence[str]
    ) -> dict[str, int]:
        counts = {label: 0 for label in order}
        for item in cases:
            value = key(item)
            counts[value] = counts.get(value, 0) + 1
        return dict(counts)

    @staticmethod
    def _failure_distribution(
        cases: Sequence[DiagnosisCase],
    ) -> dict[str, int]:
        """Multi-label：沿用 3.9.6 Taxonomy，不新增类别（§12）。"""
        distribution: dict[str, int] = {}
        for item in cases:
            labels: list[str] = []
            if item.generation_status == GENERATION_FAILURE:
                labels.append("GENERATION_FAILURE")
            if item.validation_status == VALIDATION_REJECTED:
                labels.append("VALIDATION_REJECTED")
            if item.expectation_status == EXPECTATION_MISMATCH:
                labels.append("EXPECTATION_MISMATCH")
            if item.execution_status == EXECUTION_FAILURE:
                labels.append("EXECUTION_FAILURE")
            if item.result_status == RESULT_INCORRECT:
                labels.append("RESULT_INCORRECT")
            labels.extend(item.security_categories)
            if item.project_id is not None and not labels:
                continue
            for label in labels:
                distribution[label] = distribution.get(label, 0) + 1
        return dict(sorted(distribution.items()))

    @staticmethod
    def _security_distribution(
        cases: Sequence[DiagnosisCase],
    ) -> dict[str, int]:
        distribution: dict[str, int] = {}
        for item in cases:
            for category in item.security_categories:
                distribution[category] = distribution.get(category, 0) + 1
        return dict(sorted(distribution.items()))

    @staticmethod
    def _evidence_gaps(cases: Sequence[DiagnosisCase]) -> dict[str, int]:
        """各维度 N/A / UNKNOWN 数量（§17.8）。

        Generation / Validation 维度**没有** N/A 状态（要么成功要么失败，
        证据不足记 UNKNOWN），因此其 N/A 计数恒为 0。
        """
        na_states: dict[str, str | None] = {
            "generation": None,          # 该维度无 N/A 状态
            "validation": None,          # 同上
            "expectation": EXPECTATION_NA,
            "execution": EXECUTION_NA,
            "result": RESULT_NA,
        }
        unknown_states: dict[str, str] = {
            "generation": GENERATION_UNKNOWN,
            "validation": VALIDATION_UNKNOWN,
            "expectation": EXPECTATION_UNKNOWN,
            "execution": EXECUTION_UNKNOWN,
            "result": RESULT_UNKNOWN,
        }
        gaps: dict[str, int] = {}
        for dimension, na_state in na_states.items():
            status_key = f"{dimension}_status"
            gaps[f"{dimension}_na"] = (
                sum(
                    1 for item in cases
                    if getattr(item, status_key) == na_state
                )
                if na_state is not None
                else 0
            )
            unknown_state = unknown_states[dimension]
            gaps[f"{dimension}_unknown"] = sum(
                1 for item in cases
                if getattr(item, status_key) == unknown_state
            )
        return gaps
