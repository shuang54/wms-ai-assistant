"""Annotation Evidence Finalization & Integrity Gate（Phase 4.1 Step 30 —— **Contract only**）。

本文件冻结：什么样的 AnnotatedEvidence 才能被视为"完整、版本一致、Review 状态合法、
可以进入未来 G3 Evidence Gate"的候选证据（test-local；**非 production**）：

```text
Reviewed Annotation
        ↓
Finalization / Integrity Check
        ↓
AnnotationEvidenceIntegrityResult
        ↓
G3 Evidence Input Contract（未来）
```

复用 Step 24 / 28 / 29 契约（AnnotatedEvidence / CaseAnnotation / ReviewStatus /
ComparisonOutcome / dataset_version / annotation_version / source_type）—— **不重新定义 DTO**。

核心原则：

```text
* 只有 REVIEWED 的 case 可能进入 Finalized Evidence（DRAFT / DISPUTED 一律 BLOCKED）；
* dataset_version 必须全 case 一致（不自动 convert / coerce / upgrade / downgrade）；
* annotation_version 允许每 case 不同，但同 case 只允许一个有效 REVIEWED 版本
  （禁止 max(annotation_version) 选 winner）；
* 缺任何一项 → BLOCKED；不自动填 unknown / false / None；
* impact unknown 保持 unknown（不代表 missing，也不归一化为 false）；
* Finalized Evidence 不复制 raw dataset（仅 dataset_version + case_id 引用）。
```

边界：DB = 0 · Network = 0 · LLM = 0 · 不实现真实 G3 Evidence ·
Synthetic fixtures only（synthetic procedure validation ≠ real evidence readiness）。
"""
from __future__ import annotations

import ast
import dataclasses
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tests.test_conversation_context_annotation_workflow import (
    REFERENCE_TYPES,
    AnnotatedEvidence,
    AnnotationWorkflowError,
    AnnotatorIdentity,
    CaseAnnotation,
    ReferenceAnnotation,
    evaluate_g3_evidence,
    validate_no_forbidden_annotation_fields,
)
from tests.test_conversation_context_evaluation_rubric import (
    IMPACT_FIELDS,
    IMPACT_VALUES,
    OUTCOME_TYPES,
    ContextDependencyAnnotation,
    ContextImpactAnnotation,
    EvaluationSchemaError,
    ReferenceRequirement,
    validate_annotation,
    validate_impact,
    validate_reference,
)
from tests.test_conversation_context_evidence_collection_procedure import (
    run_g1_procedure,
)
from tests.test_conversation_selection_evidence_data_audit import (
    audit_current_readiness,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_annotation_finalization.py"
_AUDIT_DOC = (
    "docs/evaluation/Phase 4.1 Step 30 — Annotation Evidence Finalization.md"
)
_FIXTURE = (
    "tests/fixtures/conversation_context/annotation_finalization_cases.yaml"
)

# ============================================================
# 常量（test-local）
# ============================================================

#: 失败代码（少量结构化；不建立复杂 error taxonomy）。
FAILURE_CODES: tuple[str, ...] = (
    "DATASET_VERSION_MISMATCH",
    "UNREVIEWED_CASE",
    "DUPLICATE_REVIEWED_VERSION",
    "INCOMPLETE_CASE",
    "INVALID_REFERENCE",
    "INVALID_DEPENDENCY",
    "INVALID_IMPACT",
    "INVALID_BUSINESS_OUTCOME",
    "REVIEW_STATE_MISMATCH",
    "ANNOTATOR_NOT_INDEPENDENT",
    "FORBIDDEN_FIELD",
)

INTEGRITY_RESULT_FIELDS: tuple[str, ...] = (
    "valid",
    "dataset_version",
    "source_type",
    "cases_checked",
    "cases_finalized",
    "blocked_cases",
    "failure_codes",
)

#: 禁止进入 dependency / annotation payload 的统计字段（§九）。
FORBIDDEN_PROBABILITY_FIELDS: tuple[str, ...] = (
    "score",
    "confidence",
    "weight",
    "probability",
)

#: §十八：integrity statistics 允许的字段（不是 quality score）。
INTEGRITY_STATISTIC_FIELDS: tuple[str, ...] = (
    "cases_checked",
    "cases_finalized",
    "cases_blocked",
)

FORBIDDEN_STATISTIC_NAMES: tuple[str, ...] = (
    "accuracy",
    "score",
    "quality",
    "winner",
    "ranking",
)

FINALIZATION_SCENARIOS: tuple[str, ...] = (
    "pass",
    "draft_blocked",
    "disputed_blocked",
    "dataset_mismatch",
    "duplicate_reviewed_version",
    "missing_dependency",
    "missing_impact",
    "invalid_reference",
    "invalid_outcome",
    "domain_review_required",
    "domain_review_completed",
    "forbidden_field",
)

EVIDENCE_DATASET_VERSION = "wms-conversation-v1"
EVIDENCE_SOURCE_TYPE = "SYNTHETIC_ONLY"  # fixture 为 synthetic procedure validation

_DEFAULT_RECORD_VALUES: dict[str, str] = {
    "disagreement": "false",
    "domain_review_completed": "false",
    "annotator_1": "annotator-a",
    "annotator_2": "annotator-b",
    "forbidden_field": "none",
}


# ============================================================
# Finalization 输入 / 结果契约（test-local）
# ============================================================


@dataclasses.dataclass(frozen=True)
class CaseFinalizationMetadata:
    """case 级 finalization 元数据（Step 28 DTO 不承载的维度）。"""

    case_id: str
    dataset_version: str
    annotation_version: str
    disagreement: bool = False
    domain_review_completed: bool = False
    annotator_1: str | None = None
    annotator_2: str | None = None


@dataclasses.dataclass(frozen=True)
class AnnotationEvidenceIntegrityResult:
    """Finalization / Integrity 结果（**不含 raw dataset / conversation content**）。"""

    valid: bool
    dataset_version: str
    source_type: str
    cases_checked: int
    cases_finalized: int
    blocked_cases: tuple[str, ...]
    failure_codes: tuple[str, ...]


# ============================================================
# Integrity check（§四 ~ §十四）
# ============================================================


def _case_payload(case: CaseAnnotation) -> dict[str, Any]:
    payload: dict[str, Any] = {"case_id": case.case_id}
    dependency = case.dependency_annotation
    if dependency is not None:
        for name in (
            "early_constraint",
            "middle_decision",
            "recent_context",
            "old_topic",
            "standalone",
        ):
            payload[name] = getattr(dependency, name)
    reference = case.reference_annotation
    if reference is not None:
        payload["reference_type"] = reference.reference_type
        payload["business_outcome"] = reference.outcome_type
    impact = case.impact_annotation
    if impact is not None:
        for name in IMPACT_FIELDS:
            payload[name] = getattr(impact, name)
    if case.review_status is not None:
        payload["review_status"] = case.review_status
    return payload


def _metadata_payload(metadata: CaseFinalizationMetadata) -> dict[str, Any]:
    return {
        "case_id": metadata.case_id,
        "dataset_version": metadata.dataset_version,
        "annotation_version": metadata.annotation_version,
    }


def _check_no_probability_fields(payload: Mapping[str, Any]) -> None:
    for key in payload:
        if str(key) in FORBIDDEN_PROBABILITY_FIELDS:
            raise AnnotationWorkflowError(
                f"probability/score field forbidden: {key}"
            )


def finalize_annotated_evidence(
    evidence: AnnotatedEvidence,
    *,
    metadata: tuple[CaseFinalizationMetadata, ...],
    extra_payload: Mapping[str, Any] | None = None,
    raw_artifact: Mapping[str, Any] | None = None,
) -> AnnotationEvidenceIntegrityResult:
    """Finalization / Integrity Check（不抛异常；失败进入 failure_codes）。"""
    blocked: list[str] = []
    failures: list[str] = []

    def block(case_id: str, code: str) -> None:
        assert code in FAILURE_CODES, code
        if case_id not in blocked:
            blocked.append(case_id)
        if code not in failures:
            failures.append(code)

    evidence_case_ids = {case.case_id for case in evidence.cases}
    metadata_by_case: dict[str, list[CaseFinalizationMetadata]] = {}
    for entry in metadata:
        metadata_by_case.setdefault(entry.case_id, []).append(entry)

    # ---- 4. duplicate reviewed version（同 case 多条记录 → 多版本 REVIEWED）----
    for case_id, entries in metadata_by_case.items():
        if len(entries) > 1:
            block(case_id, "DUPLICATE_REVIEWED_VERSION")

    # ---- metadata 空引用（case 不存在于 evidence）----
    for entry in metadata:
        if entry.case_id not in evidence_case_ids:
            block(entry.case_id, "INCOMPLETE_CASE")

    # ---- 2. dataset version integrity ----
    for case in evidence.cases:
        entries = metadata_by_case.get(case.case_id)
        if not entries:
            block(case.case_id, "INCOMPLETE_CASE")
            continue
        for entry in entries:
            if entry.dataset_version != evidence.dataset_version:
                block(case.case_id, "DATASET_VERSION_MISMATCH")
    if raw_artifact is not None:
        if str(raw_artifact.get("dataset_version")) != evidence.dataset_version:
            block("<evidence>", "DATASET_VERSION_MISMATCH")

    # ---- forbidden field（复用 Step 28 scanner；不实现第二套）----
    for case in evidence.cases:
        try:
            validate_no_forbidden_annotation_fields(_case_payload(case))
            _check_no_probability_fields(_case_payload(case))
        except AnnotationWorkflowError:
            block(case.case_id, "FORBIDDEN_FIELD")
    for entry in metadata:
        try:
            validate_no_forbidden_annotation_fields(_metadata_payload(entry))
        except AnnotationWorkflowError:
            block(entry.case_id, "FORBIDDEN_FIELD")
    if extra_payload:
        target = str(extra_payload.get("case_id") or "<extra>")
        try:
            validate_no_forbidden_annotation_fields(extra_payload)
            _check_no_probability_fields(extra_payload)
        except AnnotationWorkflowError:
            block(target, "FORBIDDEN_FIELD")

    # ---- 3. per-case integrity ----
    for case in evidence.cases:
        entries = metadata_by_case.get(case.case_id) or []
        entry = entries[0] if entries else None

        # review status（DRAFT / DISPUTED → UNREVIEWED_CASE）
        if case.review_status != "REVIEWED":
            block(case.case_id, "UNREVIEWED_CASE")

        # completeness（缺 dependency / reference / impact / review）
        if case.missing_fields():
            block(case.case_id, "INCOMPLETE_CASE")

        # dependency integrity
        dependency = case.dependency_annotation
        if dependency is not None:
            try:
                validate_annotation(dependency)
            except EvaluationSchemaError:
                block(case.case_id, "INVALID_DEPENDENCY")
            else:
                if not (
                    dependency.has_dependency() or dependency.standalone
                ):
                    block(case.case_id, "INVALID_DEPENDENCY")

        # impact integrity（unknown 保持 unknown，不归一化）
        impact = case.impact_annotation
        if impact is not None:
            try:
                validate_impact(impact)
            except EvaluationSchemaError:
                block(case.case_id, "INVALID_IMPACT")

        # reference / business outcome integrity
        reference = case.reference_annotation
        if reference is not None:
            if reference.reference_type not in REFERENCE_TYPES:
                block(case.case_id, "INVALID_REFERENCE")
            if reference.outcome_type not in OUTCOME_TYPES:
                block(case.case_id, "INVALID_BUSINESS_OUTCOME")
            else:
                try:
                    validate_reference(
                        ReferenceRequirement(
                            outcome_type=reference.outcome_type,
                            expected_behavior=reference.expected_behavior,
                            reference_created_by_human=(
                                reference.reference_created_by_human
                            ),
                            reference_validated_by_domain_expert=(
                                reference.reference_validated_by_domain_expert
                            ),
                        )
                    )
                except EvaluationSchemaError:
                    block(case.case_id, "INVALID_REFERENCE")

        # review state / domain review / annotator independence
        if entry is not None:
            if entry.disagreement and not entry.domain_review_completed:
                block(case.case_id, "REVIEW_STATE_MISMATCH")
            if entry.disagreement and entry.domain_review_completed:
                first, second = entry.annotator_1, entry.annotator_2
                if not first or not second or first == second:
                    block(case.case_id, "ANNOTATOR_NOT_INDEPENDENT")

    blocked_evidence_cases = {
        case.case_id for case in evidence.cases if case.case_id in blocked
    }
    cases_checked = len(evidence_case_ids | set(metadata_by_case))
    return AnnotationEvidenceIntegrityResult(
        valid=not failures,
        dataset_version=evidence.dataset_version,
        source_type=evidence.source_type,
        cases_checked=cases_checked,
        cases_finalized=len(evidence.cases) - len(blocked_evidence_cases),
        blocked_cases=tuple(blocked),
        failure_codes=tuple(failures),
    )


# ============================================================
# Fixture 解析（flat records；文本解析，不依赖 yaml 库）
# ============================================================


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8-sig")


def fixture_records() -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in _source(_FIXTURE).splitlines():
        stripped = line.strip()
        if stripped.startswith("- scenario:"):
            current = {"scenario": stripped.split("- scenario:", 1)[1].strip()}
            records.append(current)
            continue
        if current is None or not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("records:") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        current[key.strip()] = value.strip()
    return records


def _record_value(record: Mapping[str, str], key: str) -> str:
    return record.get(key, _DEFAULT_RECORD_VALUES.get(key, ""))


def _dependency_from(value: str) -> ContextDependencyAnnotation | None:
    if value == "none":
        return None
    flags = {
        "early_constraint": value == "early_constraint",
        "middle_decision": value == "middle_decision",
        "recent_context": value == "recent_context",
        "old_topic": value == "old_topic",
        "standalone": value == "standalone",
    }
    return ContextDependencyAnnotation(
        case_id="sim", current_turn_id="t3", **flags
    )


def _impact_from(value: str) -> ContextImpactAnnotation | None:
    if value == "none":
        return None
    parts = value.split("_")
    assert len(parts) == 4, value
    return ContextImpactAnnotation(
        case_id="sim",
        full_history_result="A01 库存 320",
        selected_history_result="全部仓库库存 1000",
        business_outcome_changed=parts[0],
        critical_constraint_lost=parts[1],
        entity_changed=parts[2],
        intent_changed=parts[3],
    )


def build_from_records(
    records: list[Mapping[str, str]],
    *,
    dataset_version: str = EVIDENCE_DATASET_VERSION,
) -> tuple[AnnotatedEvidence, tuple[CaseFinalizationMetadata, ...], dict[str, Any]]:
    cases: list[CaseAnnotation] = []
    metadata: list[CaseFinalizationMetadata] = []
    extra: dict[str, Any] = {}
    for record in records:
        case_id = record["case_id"]
        cases.append(
            CaseAnnotation(
                case_id=case_id,
                dependency_annotation=_dependency_from(record["dependency"]),
                reference_annotation=ReferenceAnnotation(
                    reference_type=record["reference_type"],
                    outcome_type=record["business_outcome"],
                    expected_behavior="仅统计 A01 仓库库存",
                    reference_created_by_human=True,
                ),
                impact_annotation=_impact_from(record["impact"]),
                review_status=record["review_status"],
            )
        )
        metadata.append(
            CaseFinalizationMetadata(
                case_id=case_id,
                dataset_version=record["dataset_version"],
                annotation_version=record["annotation_version"],
                disagreement=_record_value(record, "disagreement") == "true",
                domain_review_completed=(
                    _record_value(record, "domain_review_completed") == "true"
                ),
                annotator_1=_record_value(record, "annotator_1") or None,
                annotator_2=_record_value(record, "annotator_2") or None,
            )
        )
        if _record_value(record, "forbidden_field") != "none":
            extra = {"case_id": case_id, "note": "SELECT * FROM inventory"}
    evidence = AnnotatedEvidence(
        dataset_version=dataset_version,
        annotation_version="context-annotation-final",
        source_type=EVIDENCE_SOURCE_TYPE,
        cases=tuple(cases),
        annotator=AnnotatorIdentity("domain-reviewer-01"),
    )
    return evidence, tuple(metadata), extra


def records_for(scenario: str) -> list[dict[str, str]]:
    return [record for record in fixture_records() if record["scenario"] == scenario]


def finalize_scenario(scenario: str) -> AnnotationEvidenceIntegrityResult:
    records = records_for(scenario)
    assert records, f"fixture scenario missing: {scenario}"
    evidence, metadata, extra = build_from_records(records)
    return finalize_annotated_evidence(
        evidence, metadata=metadata, extra_payload=extra or None
    )


def _module_imports(relative: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(_source(relative))):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _non_test_function_names(relative: str) -> set[str]:
    return {
        node.name
        for node in ast.walk(ast.parse(_source(relative)))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("test_")
    }


# ============================================================
# 1. PASS / Fixture 覆盖（§十七）
# ============================================================


class TestFinalizationPass:
    def test_01_pass_scenario(self) -> None:
        result = finalize_scenario("pass")
        assert result.valid is True
        assert result.failure_codes == ()
        assert result.blocked_cases == ()
        assert result.cases_checked == 1
        assert result.cases_finalized == 1

    def test_02_result_contract_fields(self) -> None:
        fields = {
            field.name
            for field in dataclasses.fields(AnnotationEvidenceIntegrityResult)
        }
        assert fields == set(INTEGRITY_RESULT_FIELDS)
        for forbidden in ("content", "turns", "conversations", "answer", "sql"):
            assert forbidden not in fields, forbidden

    def test_03_fixture_covers_all_scenarios(self) -> None:
        scenarios = [record["scenario"] for record in fixture_records()]
        assert sorted(set(scenarios)) == sorted(FINALIZATION_SCENARIOS)
        assert len(scenarios) == 14  # 12 场景 + mismatch/duplicate 各多 1 条记录


# ============================================================
# 2. Review Status Integrity（§四 / §十二）
# ============================================================


class TestReviewStatusIntegrity:
    def test_04_draft_blocked(self) -> None:
        result = finalize_scenario("draft_blocked")
        assert result.valid is False
        assert "UNREVIEWED_CASE" in result.failure_codes
        assert result.cases_finalized == 0

    def test_05_disputed_blocked(self) -> None:
        result = finalize_scenario("disputed_blocked")
        assert result.valid is False
        assert "UNREVIEWED_CASE" in result.failure_codes

    def test_06_domain_review_required_blocked(self) -> None:
        """§十二：DISAGREEMENT 但没有最终 Domain Review → BLOCKED。"""
        result = finalize_scenario("domain_review_required")
        assert result.valid is False
        assert "REVIEW_STATE_MISMATCH" in result.failure_codes

    def test_07_domain_review_completed_passes(self) -> None:
        result = finalize_scenario("domain_review_completed")
        assert result.valid is True
        assert result.failure_codes == ()

    def test_08_annotator_not_independent_blocked(self) -> None:
        """§十三：DISAGREEMENT + domain review 但 annotator 相同 → BLOCKED。"""
        records = records_for("domain_review_completed")
        records = [dict(record) for record in records]
        records[0]["annotator_2"] = records[0]["annotator_1"]
        evidence, metadata, _ = build_from_records(records)
        result = finalize_annotated_evidence(evidence, metadata=metadata)
        assert result.valid is False
        assert "ANNOTATOR_NOT_INDEPENDENT" in result.failure_codes


# ============================================================
# 3. Version Integrity（§五 / §六）
# ============================================================


class TestVersionIntegrity:
    def test_09_dataset_version_mismatch_blocked(self) -> None:
        result = finalize_scenario("dataset_mismatch")
        assert result.valid is False
        assert "DATASET_VERSION_MISMATCH" in result.failure_codes
        assert "fin_mismatch_b" in result.blocked_cases

    def test_10_duplicate_reviewed_version_blocked(self) -> None:
        """§六：同 case 两个 REVIEWED 版本 → BLOCKED（禁止 max() 选 winner）。"""
        result = finalize_scenario("duplicate_reviewed_version")
        assert result.valid is False
        assert "DUPLICATE_REVIEWED_VERSION" in result.failure_codes
        assert "fin_dup_a" in result.blocked_cases

    def test_11_per_case_annotation_versions_may_differ(self) -> None:
        """§六：annotation_version 是 revision identity，允许每 case 不同。"""
        records = records_for("pass")
        records = [dict(record) for record in records]
        records.append(
            dict(records[0], case_id="fin_pass_b", annotation_version="context-annotation-v9")
        )
        evidence, metadata, _ = build_from_records(records)
        versions = {entry.annotation_version for entry in metadata}
        assert len(versions) == 2
        result = finalize_annotated_evidence(evidence, metadata=metadata)
        assert result.valid is True

    def test_12_raw_artifact_version_mismatch_blocked(self) -> None:
        records = records_for("pass")
        evidence, metadata, _ = build_from_records(records)
        result = finalize_annotated_evidence(
            evidence,
            metadata=metadata,
            raw_artifact={
                "dataset_version": "wms-conversation-v9",
                "source_type": EVIDENCE_SOURCE_TYPE,
                "conversations": [],
            },
        )
        assert result.valid is False
        assert "DATASET_VERSION_MISMATCH" in result.failure_codes


# ============================================================
# 4. Case Completeness / 字段 Integrity（§七 ~ §十一）
# ============================================================


class TestFieldIntegrity:
    def test_13_missing_dependency_blocked(self) -> None:
        result = finalize_scenario("missing_dependency")
        assert result.valid is False
        assert "INCOMPLETE_CASE" in result.failure_codes

    def test_14_missing_impact_blocked(self) -> None:
        result = finalize_scenario("missing_impact")
        assert result.valid is False
        assert "INCOMPLETE_CASE" in result.failure_codes

    def test_15_invalid_reference_blocked(self) -> None:
        result = finalize_scenario("invalid_reference")
        assert result.valid is False
        assert "INVALID_REFERENCE" in result.failure_codes

    def test_16_invalid_outcome_blocked(self) -> None:
        result = finalize_scenario("invalid_outcome")
        assert result.valid is False
        assert "INVALID_BUSINESS_OUTCOME" in result.failure_codes

    def test_17_all_false_dependency_blocked(self) -> None:
        """§九：五维全 False 非法（必须至少一个 True）。"""
        records = records_for("pass")
        records = [dict(record) for record in records]
        records[0]["dependency"] = "all_false"
        evidence, metadata, _ = build_from_records(records)
        dependency = evidence.cases[0].dependency_annotation
        assert dependency is not None
        assert dependency.has_dependency() is False
        assert dependency.standalone is False
        result = finalize_annotated_evidence(evidence, metadata=metadata)
        assert result.valid is False
        assert "INVALID_DEPENDENCY" in result.failure_codes

    def test_18_impact_unknown_stays_unknown(self) -> None:
        """§十：unknown 保持 unknown（不等于 missing，也不归一化为 false）。"""
        result = finalize_scenario("domain_review_completed")
        assert result.valid is True
        records = records_for("domain_review_completed")
        evidence, metadata, _ = build_from_records(records)
        impact = evidence.cases[0].impact_annotation
        assert impact is not None
        assert impact.entity_changed == "unknown"
        assert impact.business_outcome_changed == "true"
        assert IMPACT_VALUES == ("true", "false", "unknown")

    def test_19_probability_fields_forbidden(self) -> None:
        """§九：score / confidence / weight / probability 禁止进入 annotation。"""
        records = records_for("pass")
        evidence, metadata, _ = build_from_records(records)
        for field in FORBIDDEN_PROBABILITY_FIELDS:
            result = finalize_annotated_evidence(
                evidence,
                metadata=metadata,
                extra_payload={"case_id": "fin_pass_a", field: "0.9"},
            )
            assert result.valid is False, field
            assert "FORBIDDEN_FIELD" in result.failure_codes, field


# ============================================================
# 5. Security / No raw dataset（§十四 / §十五）
# ============================================================


class TestSecurityAndNoRawDataset:
    def test_20_forbidden_field_blocked(self) -> None:
        result = finalize_scenario("forbidden_field")
        assert result.valid is False
        assert "FORBIDDEN_FIELD" in result.failure_codes

    def test_21_finalized_result_holds_no_raw_dataset(self) -> None:
        result = finalize_scenario("pass")
        fields = {
            field.name
            for field in dataclasses.fields(AnnotationEvidenceIntegrityResult)
        }
        for forbidden in (
            "content",
            "turns",
            "conversations",
            "full_history",
            "candidate_history",
            "messages",
        ):
            assert forbidden not in fields, forbidden
        rendered = repr(result)
        for fragment in ("只看 A01 仓库", "查询库存", "已限定"):
            assert fragment not in rendered, fragment

    def test_22_reuses_step28_security_scanner(self) -> None:
        """§十四：复用 validate_no_forbidden_annotation_fields（不实现第二套）。"""
        modules = _module_imports(_SELF)
        assert (
            "tests.test_conversation_context_annotation_workflow" in modules
        ), sorted(modules)
        function_names = _non_test_function_names(_SELF)
        for forbidden in ("security_scanner", "scan_secrets", "redact"):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{forbidden}"


# ============================================================
# 6. Statistics / Determinism / G3（§十八 / §十九）
# ============================================================


class TestStatisticsDeterminismAndG3:
    def test_23_integrity_statistics_only(self) -> None:
        fields = {
            field.name
            for field in dataclasses.fields(AnnotationEvidenceIntegrityResult)
        }
        for forbidden in FORBIDDEN_STATISTIC_NAMES:
            for name in fields:
                assert forbidden not in name.lower(), name
        function_names = _non_test_function_names(_SELF)
        for forbidden in ("accuracy", "quality_score", "winner", "ranking"):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{forbidden}"
        assert INTEGRITY_STATISTIC_FIELDS == (
            "cases_checked",
            "cases_finalized",
            "cases_blocked",
        )

    def test_24_deterministic(self) -> None:
        assert finalize_scenario("pass") == finalize_scenario("pass")
        assert finalize_scenario("draft_blocked") == finalize_scenario("draft_blocked")
        assert fixture_records() == fixture_records()

    def test_25_synthetic_finalization_never_upgrades_g3(self) -> None:
        """§十九：Finalization PASS ≠ G3 Evidence READY（G1 BLOCKED）。"""
        result = finalize_scenario("pass")
        assert result.valid is True

        g1_status = run_g1_procedure(audit_current_readiness())
        assert g1_status == "BLOCKED"

        records = records_for("pass")
        evidence, _, _ = build_from_records(records)
        assert evidence.source_type == EVIDENCE_SOURCE_TYPE
        assert (
            evaluate_g3_evidence(
                g1_status=g1_status,
                evidence=evidence,
                artifact={
                    "dataset_version": EVIDENCE_DATASET_VERSION,
                    "source_type": EVIDENCE_SOURCE_TYPE,
                    "conversations": [
                        {
                            "case_id": "fin_pass_a",
                            "project_id": "vietnam-wms",
                            "turns": [
                                {"role": "user", "content": "只看 A01 仓库"},
                                {"role": "assistant", "content": "已限定（示例）。"},
                                {"role": "user", "content": "查询库存"},
                            ],
                        }
                    ],
                },
            )
            == "BLOCKED"
        )

    def test_26_document_sections_and_statements(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in (
            "## 1. Purpose",
            "## 2. Integrity Rules",
            "## 3. Block Conditions",
            "## 4. G3 Relationship",
            "## 5. Synthetic Limitation",
            "## 6. Production Isolation",
        ):
            assert section in doc, section
        for statement in (
            "Finalization PASS",
            "G3 Evidence READY",
            "G1 BLOCKED",
            "Synthetic fixtures only",
            "No real WMS annotation",
            "Production Code = 0",
            "DUPLICATE_REVIEWED_VERSION",
            "REVIEW_STATE_MISMATCH",
        ):
            assert statement in doc, statement

    def test_27_offline(self) -> None:
        modules = _module_imports(_SELF)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for forbidden in (
                "sqlalchemy",
                "psycopg",
                "httpx",
                "requests",
                "openai",
                "yaml",
                "backend.app.db",
                "backend.app.services",
            ):
                assert not module.startswith(forbidden), module


__all__ = [
    "FAILURE_CODES",
    "INTEGRITY_RESULT_FIELDS",
    "FORBIDDEN_PROBABILITY_FIELDS",
    "FINALIZATION_SCENARIOS",
    "EVIDENCE_DATASET_VERSION",
    "EVIDENCE_SOURCE_TYPE",
    "CaseFinalizationMetadata",
    "AnnotationEvidenceIntegrityResult",
    "finalize_annotated_evidence",
    "fixture_records",
    "build_from_records",
    "finalize_scenario",
]
