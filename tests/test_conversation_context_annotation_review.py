"""Annotation Review Procedure & Inter-Annotator Disagreement（Phase 4.1 Step 29 —— **Procedure only**）。

本文件冻结未来真实 Evidence 的标注复核流程（test-local；**非 production**）：

```text
Case
 ↓
Annotator 1
 ↓
Draft Annotation
 ↓
Annotator 2 / Reviewer（独立判断，不看 Annotator 1 结果）
 ↓
Compare
 ├── AGREEMENT
 └── DISAGREEMENT
        ↓
   Domain Review（DOMAIN_REVIEW_REQUIRED）
        ↓
   Final Reviewed Annotation（FINAL_REVIEW）
```

复用 Step 24 / 28 已冻结契约（ContextDependencyAnnotation / ContextImpactAnnotation /
Business Outcome taxonomy / Reference Contract / Review Status / Disagreement semantics）——
**不重新设计**。

统计口径（§十五 / §十六）：只做 descriptive statistics
（cases_reviewed / agreements / disagreements / domain_review_required / final_reviewed /
agreement_rate）；禁止 accuracy / score / weighted_score / 标注员排名。

边界：DB = 0 · Network = 0 · LLM = 0 · 不执行真实 Annotation ·
Synthetic 全部 REVIEWED 也不能升级 G3。
"""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path
from typing import Any

from tests.test_conversation_context_annotation_workflow import (
    AnnotationWorkflowError,
    AnnotatedEvidence,
    AnnotatorIdentity,
    CaseAnnotation,
    ReferenceAnnotation,
    evaluate_g3_evidence,
    validate_no_forbidden_annotation_fields,
)
from tests.test_conversation_context_evaluation_rubric import (
    DISAGREEMENT,
    IMPACT_FIELDS,
    OUTCOME_TYPES,
    RESOLVED,
    ContextDependencyAnnotation,
    ContextImpactAnnotation,
    requires_domain_review,
    resolve_annotation_status,
)
from tests.test_conversation_context_evidence_collection_procedure import (
    run_g1_procedure,
)
from tests.test_conversation_selection_evidence_data_audit import (
    audit_current_readiness,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_annotation_review.py"
_AUDIT_DOC = "docs/evaluation/Phase 4.1 Step 29 — Annotation Review Procedure.md"
_REVIEW_FIXTURE = (
    "tests/fixtures/conversation_context/annotation_review_cases.yaml"
)

# ============================================================
# 常量（test-local）
# ============================================================

AGREEMENT = "AGREEMENT"
COMPARISON_STATES: tuple[str, ...] = (AGREEMENT, DISAGREEMENT)

#: Review 阶段（§九）。
DOMAIN_REVIEW_REQUIRED = "DOMAIN_REVIEW_REQUIRED"
FINAL_REVIEW = "FINAL_REVIEW"
REVIEW_PHASES: tuple[str, ...] = (DOMAIN_REVIEW_REQUIRED, FINAL_REVIEW)

#: 必须比较的字段（§七：完整结构比较）。
COMPARED_FIELDS: tuple[str, ...] = (
    "dependency_annotation",
    "reference_type",
    "impact_annotation",
    "business_outcome",
)

#: 禁止的统计口径（§十五）。
FORBIDDEN_STATISTIC_NAMES: tuple[str, ...] = (
    "score",
    "accuracy",
    "weighted",
    "ranking",
    "rank",
    "winner",
)

_REVIEW_SCENARIOS: tuple[str, ...] = (
    "agreement",
    "dependency_disagreement",
    "impact_disagreement",
    "outcome_disagreement",
    "reference_disagreement",
    "domain_review",
)

_CASE_PATTERN_ANCHOR = "- id: "
_SCENARIO_PATTERN_ANCHOR = "review_scenario: "


class AnnotationReviewError(ValueError):
    """Annotation review 契约违规（test-local；非 production 异常）。"""


# ============================================================
# Draft / Comparison（§五 ~ §七）
# ============================================================


@dataclasses.dataclass(frozen=True)
class AnnotationDraft:
    """单个 Annotator 的独立标注结果（不含历史结论，不复制 content）。"""

    case_id: str
    annotator: AnnotatorIdentity
    dependency_annotation: ContextDependencyAnnotation
    reference_annotation: ReferenceAnnotation
    impact_annotation: ContextImpactAnnotation
    business_outcome: str

    def validate(self) -> None:
        if not str(self.case_id).strip():
            raise AnnotationReviewError("case_id must be non-empty")
        self.annotator.validate()
        from tests.test_conversation_context_evaluation_rubric import (
            validate_annotation,
            validate_impact,
        )

        validate_annotation(self.dependency_annotation)
        validate_impact(self.impact_annotation)
        self.reference_annotation.validate()
        if self.business_outcome not in OUTCOME_TYPES:
            raise AnnotationReviewError(
                f"invalid business_outcome: {self.business_outcome!r}"
            )


@dataclasses.dataclass(frozen=True)
class ComparisonOutcome:
    """两名标注者的比较结果（完整结构比较；不做部分匹配）。"""

    case_id: str
    result: str
    differing_fields: tuple[str, ...]

    def validate(self) -> None:
        if self.result not in COMPARISON_STATES:
            raise AnnotationReviewError(f"invalid comparison result: {self.result!r}")
        if (self.result == AGREEMENT) != (len(self.differing_fields) == 0):
            raise AnnotationReviewError("result and differing_fields are inconsistent")
        for field in self.differing_fields:
            if field not in COMPARED_FIELDS:
                raise AnnotationReviewError(f"unknown compared field: {field}")


def _dependency_differences(
    first: ContextDependencyAnnotation, second: ContextDependencyAnnotation
) -> tuple[str, ...]:
    """多标签完整结构比较（任一维度不同即差异；禁止部分相同判 AGREEMENT）。"""
    dimensions = (
        "early_constraint",
        "middle_decision",
        "recent_context",
        "old_topic",
        "standalone",
    )
    return tuple(
        name for name in dimensions if getattr(first, name) != getattr(second, name)
    )


def _impact_differences(
    first: ContextImpactAnnotation, second: ContextImpactAnnotation
) -> tuple[str, ...]:
    """三值比较：unknown != false（逐字段精确比较，禁止 unknown → false 归一化）。"""
    return tuple(
        name for name in IMPACT_FIELDS if getattr(first, name) != getattr(second, name)
    )


def compare_annotations(
    first: AnnotationDraft, second: AnnotationDraft
) -> ComparisonOutcome:
    """比较 Annotator 1 vs Annotator 2（4 个关键字段完整比较）。"""
    first.validate()
    second.validate()
    if first.case_id != second.case_id:
        raise AnnotationReviewError("cannot compare different case_id")
    if first.annotator.annotator_id == second.annotator.annotator_id:
        raise AnnotationReviewError(
            "comparison requires two independent annotators (distinct annotator_id)"
        )

    differing: list[str] = []
    if _dependency_differences(
        first.dependency_annotation, second.dependency_annotation
    ):
        differing.append("dependency_annotation")
    if (
        first.reference_annotation.reference_type
        != second.reference_annotation.reference_type
    ):
        differing.append("reference_type")
    if _impact_differences(first.impact_annotation, second.impact_annotation):
        differing.append("impact_annotation")
    if first.business_outcome != second.business_outcome:
        differing.append("business_outcome")

    outcome = ComparisonOutcome(
        case_id=first.case_id,
        result=AGREEMENT if not differing else DISAGREEMENT,
        differing_fields=tuple(differing),
    )
    outcome.validate()
    return outcome


# ============================================================
# Domain Review（§八 / §九）
# ============================================================


def review_phase(
    outcome: ComparisonOutcome, *, domain_review_completed: bool = False
) -> str:
    """AGREEMENT → FINAL_REVIEW；DISAGREEMENT → DOMAIN_REVIEW_REQUIRED（完成后 FINAL_REVIEW）。"""
    outcome.validate()
    if outcome.result == AGREEMENT:
        return FINAL_REVIEW
    return FINAL_REVIEW if domain_review_completed else DOMAIN_REVIEW_REQUIRED


def requires_domain_review_phase(outcome: ComparisonOutcome) -> bool:
    return review_phase(outcome) == DOMAIN_REVIEW_REQUIRED


def finalize_domain_review(
    outcome: ComparisonOutcome,
    *,
    reviewer: AnnotatorIdentity,
    final_draft: AnnotationDraft,
) -> tuple[ComparisonOutcome, AnnotationDraft, str]:
    """Domain Review 完成 → 产生 FINAL_REVIEW（人工裁决，无自动合并）。"""
    if not requires_domain_review_phase(outcome):
        raise AnnotationReviewError(
            "domain review is only required for DISAGREEMENT outcomes"
        )
    reviewer.validate()
    final_draft.validate()
    if final_draft.case_id != outcome.case_id:
        raise AnnotationReviewError("final draft case_id must match comparison")
    phase = review_phase(outcome, domain_review_completed=True)
    assert phase == FINAL_REVIEW
    return outcome, final_draft, phase


# ============================================================
# Review Status 转换（§十）
# ============================================================


def review_status_for(outcome: ComparisonOutcome, *, domain_review_completed: bool) -> str:
    """状态转换：AGREEMENT → REVIEWED；DISAGREEMENT → DISPUTED → （域评审）→ REVIEWED。"""
    if outcome.result == AGREEMENT:
        return "REVIEWED"
    return "REVIEWED" if domain_review_completed else "DISPUTED"


# ============================================================
# Statistics（§十五 / §十六）
# ============================================================


def format_agreement_rate(agreements: int, cases_reviewed: int) -> str:
    """0 case → N/A（不是 0%）；否则去尾零百分比（8/10 → 80%）。"""
    if cases_reviewed == 0:
        return "N/A"
    if agreements < 0 or agreements > cases_reviewed:
        raise AnnotationReviewError("agreements must be within [0, cases_reviewed]")
    rate = agreements * 100 / cases_reviewed
    text = f"{rate:.1f}"
    if text.endswith(".0"):
        text = text[:-2]
    return f"{text}%"


@dataclasses.dataclass(frozen=True)
class ReviewStatistics:
    """描述性统计（**不是**评分 / 排名）。"""

    cases_reviewed: int
    agreements: int
    disagreements: int
    domain_review_required: int
    final_reviewed: int
    agreement_rate: str


def summarize_reviews(
    outcomes: tuple[ComparisonOutcome, ...],
    *,
    domain_reviews_completed: tuple[str, ...] = (),
) -> ReviewStatistics:
    """统计 review 结果（deterministic；无偏差来源）。"""
    for outcome in outcomes:
        outcome.validate()
    agreements = sum(1 for outcome in outcomes if outcome.result == AGREEMENT)
    disagreements = sum(
        1 for outcome in outcomes if outcome.result == DISAGREEMENT
    )
    domain_required = sum(
        1 for outcome in outcomes if requires_domain_review_phase(outcome)
    )
    completed = set(domain_reviews_completed)
    final_reviewed = agreements + sum(1 for case_id in completed)
    return ReviewStatistics(
        cases_reviewed=len(outcomes),
        agreements=agreements,
        disagreements=disagreements,
        domain_review_required=domain_required,
        final_reviewed=final_reviewed,
        agreement_rate=format_agreement_rate(agreements, len(outcomes)),
    )


# ============================================================
# Fixture（synthetic；只读文本解析，不依赖 yaml 库）
# ============================================================


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8-sig")


def fixture_case_ids() -> list[str]:
    return [
        line.strip().split(_CASE_PATTERN_ANCHOR)[1]
        for line in _source(_REVIEW_FIXTURE).splitlines()
        if line.strip().startswith(_CASE_PATTERN_ANCHOR)
    ]


def fixture_scenarios() -> list[str]:
    return [
        line.strip().split(_SCENARIO_PATTERN_ANCHOR)[1]
        for line in _source(_REVIEW_FIXTURE).splitlines()
        if line.strip().startswith(_SCENARIO_PATTERN_ANCHOR)
    ]


def fixture_final_roles() -> list[str]:
    roles = [
        line.strip().split("- role: ")[1]
        for line in _source(_REVIEW_FIXTURE).splitlines()
        if line.strip().startswith("- role: ")
    ]
    # 约定：每个 case 的最后一个 turn 是 user；此处按 case 边界切分
    per_case: list[str] = []
    current_last = ""
    previous_was_case = True
    for line in _source(_REVIEW_FIXTURE).splitlines():
        stripped = line.strip()
        if stripped.startswith(_CASE_PATTERN_ANCHOR):
            if not previous_was_case and current_last:
                per_case.append(current_last)
            current_last = ""
            previous_was_case = True
        elif stripped.startswith("- role: "):
            current_last = stripped.split("- role: ")[1]
            previous_was_case = False
    if current_last:
        per_case.append(current_last)
    assert len(roles) == sum(
        1
        for line in _source(_REVIEW_FIXTURE).splitlines()
        if line.strip().startswith("- role: ")
    )
    return per_case


# ============================================================
# 模拟标注（NOT real WMS evidence；仅 procedure 模拟）
# ============================================================


def _dependency(
    *,
    early: bool = True,
    middle: bool = False,
    recent: bool = False,
    old: bool = False,
    standalone: bool = False,
) -> ContextDependencyAnnotation:
    return ContextDependencyAnnotation(
        case_id="sim",
        current_turn_id="t3",
        early_constraint=early,
        middle_decision=middle,
        recent_context=recent,
        old_topic=old,
        standalone=standalone,
    )


def _impact(
    *,
    business: str = "false",
    constraint: str = "false",
    entity: str = "false",
    intent: str = "false",
) -> ContextImpactAnnotation:
    return ContextImpactAnnotation(
        case_id="sim",
        full_history_result="A01 库存 320",
        selected_history_result="全部仓库库存 1000",
        business_outcome_changed=business,
        critical_constraint_lost=constraint,
        entity_changed=entity,
        intent_changed=intent,
    )


def _reference(reference_type: str = "FULL_HISTORY") -> ReferenceAnnotation:
    return ReferenceAnnotation(
        reference_type=reference_type,
        outcome_type="SQL_SEMANTICS",
        expected_behavior="仅统计 A01 仓库库存",
        reference_created_by_human=True,
    )


def _draft(
    case_id: str = "sim",
    *,
    annotator: str = "annotator-a",
    dependency: ContextDependencyAnnotation | None = None,
    impact: ContextImpactAnnotation | None = None,
    reference: ReferenceAnnotation | None = None,
    business_outcome: str = "SQL_SEMANTICS",
) -> AnnotationDraft:
    return AnnotationDraft(
        case_id=case_id,
        annotator=AnnotatorIdentity(annotator),
        dependency_annotation=dependency or _dependency(),
        reference_annotation=reference or _reference(),
        impact_annotation=impact or _impact(),
        business_outcome=business_outcome,
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
# 1. Agreement（§七 / §十八 Agreement）
# ============================================================


class TestAgreement:
    def test_01_identical_annotations_agree(self) -> None:
        first = _draft(annotator="annotator-a")
        second = _draft(annotator="annotator-b")
        outcome = compare_annotations(first, second)
        assert outcome.result == AGREEMENT
        assert outcome.differing_fields == ()
        assert review_phase(outcome) == FINAL_REVIEW
        assert requires_domain_review_phase(outcome) is False

    def test_02_agreement_requires_distinct_annotators(self) -> None:
        """§六：第二位标注员必须独立（不同 annotator_id）。"""
        first = _draft(annotator="annotator-a")
        same = _draft(annotator="annotator-a")
        try:
            compare_annotations(first, same)
        except AnnotationReviewError as exc:
            assert "independent" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("same annotator comparison must be rejected")

    def test_03_agreement_status_is_reviewed(self) -> None:
        outcome = compare_annotations(
            _draft(annotator="annotator-a"), _draft(annotator="annotator-b")
        )
        assert review_status_for(outcome, domain_review_completed=False) == "REVIEWED"
        assert resolve_annotation_status("true", "true") == RESOLVED


# ============================================================
# 2. Dependency 比较（§十一）
# ============================================================


class TestDependencyComparison:
    def test_04_different_multi_label_set_disagrees(self) -> None:
        first = _draft(
            annotator="annotator-a",
            dependency=_dependency(early=True, middle=True),
        )
        second = _draft(
            annotator="annotator-b",
            dependency=_dependency(early=True, middle=False),
        )
        outcome = compare_annotations(first, second)
        assert outcome.result == DISAGREEMENT
        assert outcome.differing_fields == ("dependency_annotation",)

    def test_05_partial_overlap_is_disagreement(self) -> None:
        """§十一：部分相同不能判 AGREEMENT（完整结构比较）。"""
        first = _draft(
            annotator="annotator-a",
            dependency=_dependency(early=True, recent=True),
        )
        second = _draft(
            annotator="annotator-b",
            dependency=_dependency(early=True, recent=False, standalone=True),
        )
        outcome = compare_annotations(first, second)
        assert outcome.result == DISAGREEMENT
        assert "dependency_annotation" in outcome.differing_fields


# ============================================================
# 3. Impact 比较（§十二）
# ============================================================


class TestImpactComparison:
    def test_06_true_vs_false_disagrees(self) -> None:
        first = _draft(
            annotator="annotator-a", impact=_impact(constraint="true")
        )
        second = _draft(
            annotator="annotator-b", impact=_impact(constraint="false")
        )
        outcome = compare_annotations(first, second)
        assert outcome.result == DISAGREEMENT
        assert outcome.differing_fields == ("impact_annotation",)

    def test_07_unknown_vs_false_disagrees(self) -> None:
        """§十二：unknown vs false 必须 DISAGREEMENT（禁止 unknown → false）。"""
        first = _draft(
            annotator="annotator-a", impact=_impact(entity="unknown")
        )
        second = _draft(
            annotator="annotator-b", impact=_impact(entity="false")
        )
        outcome = compare_annotations(first, second)
        assert outcome.result == DISAGREEMENT
        assert outcome.differing_fields == ("impact_annotation",)

    def test_08_unknown_vs_unknown_agrees(self) -> None:
        first = _draft(annotator="annotator-a", impact=_impact(entity="unknown"))
        second = _draft(annotator="annotator-b", impact=_impact(entity="unknown"))
        outcome = compare_annotations(first, second)
        assert outcome.result == AGREEMENT


# ============================================================
# 4. Business Outcome 比较（§十三）
# ============================================================


class TestOutcomeComparison:
    def test_09_sql_vs_tool_arguments_disagrees(self) -> None:
        first = _draft(annotator="annotator-a", business_outcome="SQL_SEMANTICS")
        second = _draft(annotator="annotator-b", business_outcome="TOOL_ARGUMENTS")
        outcome = compare_annotations(first, second)
        assert outcome.result == DISAGREEMENT
        assert outcome.differing_fields == ("business_outcome",)

    def test_10_sql_vs_tool_selection_disagrees(self) -> None:
        first = _draft(annotator="annotator-a", business_outcome="SQL_SEMANTICS")
        second = _draft(annotator="annotator-b", business_outcome="TOOL_SELECTION")
        outcome = compare_annotations(first, second)
        assert outcome.result == DISAGREEMENT

    def test_11_invalid_outcome_rejected(self) -> None:
        try:
            _draft(business_outcome="SUCCESS").validate()
        except AnnotationReviewError as exc:
            assert "business_outcome" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("invalid business_outcome must be rejected")


# ============================================================
# 5. Reference 比较（§十四）
# ============================================================


class TestReferenceComparison:
    def test_12_full_history_vs_domain_expert_disagrees(self) -> None:
        first = _draft(
            annotator="annotator-a", reference=_reference("FULL_HISTORY")
        )
        second = _draft(
            annotator="annotator-b",
            reference=_reference("DOMAIN_EXPERT_VALIDATED"),
        )
        outcome = compare_annotations(first, second)
        assert outcome.result == DISAGREEMENT
        assert outcome.differing_fields == ("reference_type",)

    def test_13_same_reference_type_agrees(self) -> None:
        outcome = compare_annotations(
            _draft(annotator="annotator-a", reference=_reference("FULL_HISTORY")),
            _draft(annotator="annotator-b", reference=_reference("FULL_HISTORY")),
        )
        assert outcome.result == AGREEMENT


# ============================================================
# 6. 多字段不一致 / Domain Review（§七 / §八 / §九 / §十八）
# ============================================================


class TestMultiFieldAndDomainReview:
    def test_14_multiple_differences_still_single_disagreement(self) -> None:
        first = _draft(
            annotator="annotator-a",
            dependency=_dependency(early=True, middle=True),
            impact=_impact(constraint="true"),
            reference=_reference("FULL_HISTORY"),
            business_outcome="SQL_SEMANTICS",
        )
        second = _draft(
            annotator="annotator-b",
            dependency=_dependency(early=True, recent=True),
            impact=_impact(constraint="false"),
            reference=_reference("DOMAIN_EXPERT_VALIDATED"),
            business_outcome="TOOL_SELECTION",
        )
        outcome = compare_annotations(first, second)
        assert outcome.result == DISAGREEMENT
        assert set(outcome.differing_fields) == set(COMPARED_FIELDS)
        # 仍然只产生一个 DISAGREEMENT
        assert outcome.result.count(DISAGREEMENT) == 1

    def test_15_disagreement_requires_domain_review(self) -> None:
        outcome = compare_annotations(
            _draft(annotator="annotator-a", business_outcome="SQL_SEMANTICS"),
            _draft(annotator="annotator-b", business_outcome="ROUTE"),
        )
        assert review_phase(outcome) == DOMAIN_REVIEW_REQUIRED
        assert requires_domain_review_phase(outcome) is True
        assert review_status_for(outcome, domain_review_completed=False) == "DISPUTED"
        assert requires_domain_review(DISAGREEMENT) is True

    def test_16_domain_review_produces_final_review(self) -> None:
        outcome = compare_annotations(
            _draft(annotator="annotator-a", business_outcome="SQL_SEMANTICS"),
            _draft(annotator="annotator-b", business_outcome="TOOL_SELECTION"),
        )
        final_draft = _draft(
            annotator="domain-reviewer-01", business_outcome="SQL_SEMANTICS"
        )
        result = finalize_domain_review(
            outcome,
            reviewer=AnnotatorIdentity("domain-reviewer-01"),
            final_draft=final_draft,
        )
        _, final, phase = result
        assert phase == FINAL_REVIEW
        assert final.business_outcome == "SQL_SEMANTICS"
        assert (
            review_status_for(outcome, domain_review_completed=True) == "REVIEWED"
        )

    def test_17_domain_review_not_allowed_for_agreement(self) -> None:
        outcome = compare_annotations(
            _draft(annotator="annotator-a"), _draft(annotator="annotator-b")
        )
        try:
            finalize_domain_review(
                outcome,
                reviewer=AnnotatorIdentity("domain-reviewer-01"),
                final_draft=_draft(annotator="domain-reviewer-01"),
            )
        except AnnotationReviewError:
            pass
        else:  # pragma: no cover
            raise AssertionError("agreement must not require domain review")


# ============================================================
# 7. No majority vote（§八 / §十八）
# ============================================================


class TestNoMajorityVote:
    def test_18_no_majority_average_or_auto_select(self) -> None:
        function_names = _non_test_function_names(_SELF)
        for forbidden in (
            "majority_vote",
            "majority",
            "average",
            "pick_winner",
            "auto_select",
            "auto_merge",
            "random_choice",
        ):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{forbidden}"

    def test_19_no_llm_judge(self) -> None:
        modules = _module_imports(_SELF)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for forbidden in (
                "openai",
                "anthropic",
                "transformers",
                "httpx",
                "requests",
                "backend.app.db",
                "backend.app.services",
            ):
                assert not module.startswith(forbidden), module

    def test_20_no_secret_or_sql_in_review_payload(self) -> None:
        validate_no_forbidden_annotation_fields(
            {
                "case_id": "sim",
                "annotator_id": "annotator-a",
                "review_status": "DISPUTED",
                "reference_type": "FULL_HISTORY",
            }
        )
        for payload in (
            {"note": "api_key=secret"},
            {"note": "SELECT * FROM inventory"},
            {"note": "Traceback (most recent call last)"},
            {"embedding": [0.1]},
        ):
            try:
                validate_no_forbidden_annotation_fields(payload)
            except AnnotationWorkflowError:
                continue
            raise AssertionError(f"payload must be rejected: {payload}")  # pragma: no cover


# ============================================================
# 8. Versioning（§十 / §十八）
# ============================================================


class TestVersioning:
    def test_21_revision_requires_new_annotation_version(self) -> None:
        evidence = AnnotatedEvidence(
            dataset_version="wms-conversation-v1",
            annotation_version="context-annotation-v1",
            source_type="REAL_DEIDENTIFIED",
            cases=(
                CaseAnnotation(
                    case_id="sim",
                    dependency_annotation=_dependency(),
                    reference_annotation=_reference(),
                    impact_annotation=_impact(),
                    review_status="REVIEWED",
                ),
            ),
            annotator=AnnotatorIdentity("domain-reviewer-01"),
        )
        assert evidence.review_status == "REVIEWED"
        try:
            evidence.revise(new_annotation_version="context-annotation-v1")
        except AnnotationWorkflowError as exc:
            assert "new annotation_version" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("same-version revision must be rejected")

        revised = evidence.revise(new_annotation_version="context-annotation-v2")
        assert revised.annotation_version == "context-annotation-v2"
        assert evidence.annotation_version == "context-annotation-v1"  # 原对象不变
        assert revised.dataset_version == evidence.dataset_version

    def test_22_reviewed_cannot_be_overwritten_in_place(self) -> None:
        evidence = AnnotatedEvidence(
            dataset_version="wms-conversation-v1",
            annotation_version="context-annotation-v1",
            source_type="REAL_DEIDENTIFIED",
            cases=(CaseAnnotation(case_id="sim"),),
            annotator=AnnotatorIdentity("domain-reviewer-01"),
        )
        try:
            evidence.review_status = "DRAFT"  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            pass
        else:  # pragma: no cover
            raise AssertionError("AnnotatedEvidence must be frozen")


# ============================================================
# 9. Statistics（§十五 / §十六）
# ============================================================


class TestStatistics:
    def test_23_zero_cases_is_na_not_zero_percent(self) -> None:
        assert format_agreement_rate(0, 0) == "N/A"
        stats = summarize_reviews(())
        assert stats.cases_reviewed == 0
        assert stats.agreement_rate == "N/A"
        assert stats.agreement_rate != "0%"

    def test_24_full_agreement_is_100_percent(self) -> None:
        outcomes = tuple(
            compare_annotations(
                _draft(f"case_{i:03d}", annotator="annotator-a"),
                _draft(f"case_{i:03d}", annotator="annotator-b"),
            )
            for i in range(10)
        )
        stats = summarize_reviews(outcomes)
        assert stats.cases_reviewed == 10
        assert stats.agreements == 10
        assert stats.disagreements == 0
        assert stats.agreement_rate == "100%"

    def test_25_eight_of_ten_is_80_percent(self) -> None:
        agreeing = tuple(
            compare_annotations(
                _draft(f"case_{i:03d}", annotator="annotator-a"),
                _draft(f"case_{i:03d}", annotator="annotator-b"),
            )
            for i in range(8)
        )
        disagreeing = tuple(
            compare_annotations(
                _draft(
                    f"case_{i:03d}",
                    annotator="annotator-a",
                    business_outcome="SQL_SEMANTICS",
                ),
                _draft(
                    f"case_{i:03d}",
                    annotator="annotator-b",
                    business_outcome="TOOL_SELECTION",
                ),
            )
            for i in range(8, 10)
        )
        stats = summarize_reviews(agreeing + disagreeing)
        assert stats.cases_reviewed == 10
        assert stats.agreements == 8
        assert stats.disagreements == 2
        assert stats.domain_review_required == 2
        assert stats.agreement_rate == "80%"

    def test_26_statistics_are_descriptive_only(self) -> None:
        fields = {field.name for field in dataclasses.fields(ReviewStatistics)}
        for forbidden in FORBIDDEN_STATISTIC_NAMES:
            for name in fields:
                assert forbidden not in name.lower(), name
        function_names = _non_test_function_names(_SELF)
        for forbidden in ("score", "accuracy", "weighted", "ranking"):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{forbidden}"

    def test_27_final_reviewed_count_tracks_domain_reviews(self) -> None:
        dis = compare_annotations(
            _draft("case_001", annotator="annotator-a", business_outcome="SQL_SEMANTICS"),
            _draft("case_001", annotator="annotator-b", business_outcome="ROUTE"),
        )
        agree = compare_annotations(
            _draft("case_002", annotator="annotator-a"),
            _draft("case_002", annotator="annotator-b"),
        )
        stats = summarize_reviews(
            (dis, agree), domain_reviews_completed=("case_001",)
        )
        assert stats.final_reviewed == 2
        assert stats.domain_review_required == 1


# ============================================================
# 10. Fixture / G3 / 文档（§十七 / §十九 / §二十二）
# ============================================================


class TestFixtureAndGates:
    def test_28_fixture_covers_all_scenarios(self) -> None:
        case_ids = fixture_case_ids()
        scenarios = fixture_scenarios()
        assert len(case_ids) == 6
        assert scenarios == list(_REVIEW_SCENARIOS)
        assert len(set(case_ids)) == len(case_ids)

    def test_29_fixture_last_turn_is_user(self) -> None:
        assert fixture_final_roles() == ["user"] * 6

    def test_30_fixture_is_synthetic_without_real_or_conclusion_fields(self) -> None:
        text = _source(_REVIEW_FIXTURE)
        assert "synthetic" in text
        assert "非生产数据" in text
        for forbidden in (
            "annotation:",
            "reference:",
            "impact:",
            "conversation_id",
            "turn_id",
            "assistant_request_id",
        ):
            assert forbidden not in text, forbidden

    def test_31_synthetic_review_never_upgrades_g3(self) -> None:
        """§十九：synthetic annotation 全部 REVIEWED 也不能升级 G3 READY。"""
        g1_status = run_g1_procedure(audit_current_readiness())
        assert g1_status == "BLOCKED"
        evidence = AnnotatedEvidence(
            dataset_version="wms-conversation-v1",
            annotation_version="context-annotation-v1",
            source_type="SYNTHETIC_ONLY",
            cases=(
                CaseAnnotation(
                    case_id="review_agreement_001",
                    dependency_annotation=_dependency(),
                    reference_annotation=_reference(),
                    impact_annotation=_impact(),
                    review_status="REVIEWED",
                ),
            ),
            annotator=AnnotatorIdentity("domain-reviewer-01"),
        )
        assert evidence.review_status == "REVIEWED"
        assert (
            evaluate_g3_evidence(
                g1_status=g1_status,
                evidence=evidence,
                artifact={
                    "dataset_version": "wms-conversation-v1",
                    "source_type": "SYNTHETIC_ONLY",
                    "conversations": [
                        {
                            "case_id": "review_agreement_001",
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

    def test_32_document_sections_and_statements(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in (
            "## 1. Procedure",
            "## 2. Independence",
            "## 3. Comparison",
            "## 4. Disagreement",
            "## 5. Review Status",
            "## 6. Versioning",
            "## 7. Statistics",
            "## 8. G3",
            "## 9. Security",
            "## 10. Production Isolation",
            "## 11. Deferred",
        ):
            assert section in doc, section
        for statement in (
            "AGREEMENT",
            "DISAGREEMENT",
            "DOMAIN_REVIEW_REQUIRED",
            "FINAL_REVIEW",
            "DRAFT",
            "REVIEWED",
            "DISPUTED",
            "agreement_rate",
            "G1 = BLOCKED",
            "G3 Evidence = BLOCKED",
            "Selection Strategy = BLOCKED",
        ):
            assert statement in doc, statement

    def test_33_offline_and_deterministic(self) -> None:
        modules = _module_imports(_SELF)
        for module in modules:
            for forbidden in (
                "sqlalchemy",
                "psycopg",
                "httpx",
                "openai",
                "backend.app.db",
                "backend.app.services",
            ):
                assert not module.startswith(forbidden), module
        first = compare_annotations(
            _draft(annotator="annotator-a"), _draft(annotator="annotator-b")
        )
        second = compare_annotations(
            _draft(annotator="annotator-a"), _draft(annotator="annotator-b")
        )
        assert first == second
        assert summarize_reviews((first,)) == summarize_reviews((second,))


__all__ = [
    "AGREEMENT",
    "DOMAIN_REVIEW_REQUIRED",
    "FINAL_REVIEW",
    "COMPARED_FIELDS",
    "AnnotationReviewError",
    "AnnotationDraft",
    "ComparisonOutcome",
    "ReviewStatistics",
    "compare_annotations",
    "review_phase",
    "requires_domain_review_phase",
    "finalize_domain_review",
    "review_status_for",
    "format_agreement_rate",
    "summarize_reviews",
    "fixture_case_ids",
    "fixture_scenarios",
]
