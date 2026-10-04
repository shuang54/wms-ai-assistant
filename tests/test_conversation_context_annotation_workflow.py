"""Real Evidence Annotation Workflow Contract（Phase 4.1 Step 28 —— **Contract only**）。

本文件冻结未来真实 WMS Evidence 进入 G3 Annotation Workflow 的契约，
以及 Annotation 结果与原始 Dataset Version 的可追溯关系（test-local；**非 production**）：

```text
REAL_DEIDENTIFIED Artifact
        ↓
Dataset Validation（Step 26）
        ↓
Provenance Validation（Step 27）
        ↓
G1 READY
        ↓
Annotation Workflow
        ↓
Annotated Evidence
        ↓
G3 Evidence READY
```

核心分离：

```text
Raw Evidence Artifact  ≠  Annotated Evidence
（Annotated Evidence 只通过 dataset_version + case_id 引用原始证据，
  不复制 conversation content。）
```

复用 Step 24 已冻结契约：ContextDependencyAnnotation / ContextImpactAnnotation /
Business Outcome taxonomy / Reference quality / Disagreement —— **不重新设计**。

边界：DB = 0 · Network = 0 · LLM = 0 · 不改 Step 25 Report Schema ·
不实现 Annotation UI / Storage / API / 生产 Service。
"""
from __future__ import annotations

import ast
import dataclasses
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tests.test_conversation_context_evidence_collection_procedure import (
    run_g3_procedure,
    classify_g4_impact,
)
from tests.test_conversation_context_evaluation_rubric import (
    ANNOTATION_TYPES,
    DISAGREEMENT,
    IMPACT_FIELDS,
    IMPACT_VALUES,
    OUTCOME_TYPES,
    RESOLVED,
    ContextDependencyAnnotation,
    ContextImpactAnnotation,
    EvaluationSchemaError,
    ReferenceRequirement,
    requires_domain_review,
    resolve_annotation_status,
    validate_annotation,
    validate_impact,
    validate_reference,
)
from tests.test_conversation_context_real_evidence_boundary import (
    PRODUCTION_CORRELATION_IDS,
    SOURCE_TYPE_REAL,
    SOURCE_TYPE_SYNTHETIC,
    derive_readiness,
    validate_artifact,
)
from tests.test_conversation_context_evidence_collection_procedure import (
    run_g1_procedure,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_annotation_workflow.py"
_AUDIT_DOC = (
    "docs/evaluation/Phase 4.1 Step 28 — Real Evidence Annotation Workflow Contract.md"
)

# ============================================================
# 常量（test-local）
# ============================================================

ALLOWED_SOURCE_TYPES: tuple[str, ...] = (SOURCE_TYPE_REAL, SOURCE_TYPE_SYNTHETIC)

#: Review Status 最小状态（§十四）；禁止 APPROVED / REJECTED / AUTO。
REVIEW_STATUSES: tuple[str, ...] = ("DRAFT", "REVIEWED", "DISPUTED")
FORBIDDEN_REVIEW_STATUSES: tuple[str, ...] = ("APPROVED", "REJECTED", "AUTO")

#: Reference Contract（§十一）。
REFERENCE_TYPES: tuple[str, ...] = ("FULL_HISTORY", "DOMAIN_EXPERT_VALIDATED")

#: Annotated Evidence 最小字段（§六）。
ANNOTATED_EVIDENCE_FIELDS: tuple[str, ...] = (
    "dataset_version",
    "annotation_version",
    "source_type",
    "cases",
    "annotator",
)

CASE_ANNOTATION_FIELDS: tuple[str, ...] = (
    "case_id",
    "dependency_annotation",
    "reference_annotation",
    "impact_annotation",
    "review_status",
)

#: Mismatch / 缺失状态（§十七 / §十八）。
DATASET_VERSION_MISMATCH = "DATASET_VERSION_MISMATCH"
CASE_NOT_FOUND = "CASE_NOT_FOUND"
G3_EVIDENCE_STATES: tuple[str, ...] = ("READY", "BLOCKED")

#: §二十一：禁止保存的字段名。
FORBIDDEN_ANNOTATION_FIELDS: tuple[str, ...] = (
    "api_key",
    "Authorization",
    "password",
    "DATABASE_URL",
    "sql",
    "stack_trace",
    "traceback",
    "raw_llm_response",
    "raw_response",
    "tool_raw_payload",
    "embedding",
    "vector",
)

#: §十二：不得新增的 business outcome 类型。
FORBIDDEN_OUTCOME_TYPES: tuple[str, ...] = (
    "SUCCESS",
    "FAILED",
    "EMPTY",
    "LATENCY",
    "TOKEN_COST",
)

_ALLOWED_ANNOTATOR_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz0123456789-"
)
_MIN_ANNOTATOR_ID_LENGTH = 3
_MAX_ANNOTATOR_ID_LENGTH = 64
_MAX_CONSECUTIVE_DIGITS = 8

_SECRET_MARKERS: tuple[str, ...] = (
    "api_key",
    "Authorization",
    "DATABASE_URL",
    "postgresql://",
    "postgres://",
    "psycopg2.connect",
    "password",
)
_SQL_MARKERS: tuple[str, ...] = ("SELECT ", "INSERT ", "UPDATE ", "DELETE FROM ")
_TRACE_MARKERS: tuple[str, ...] = ("Traceback (most recent call last)", "  File \"")


class AnnotationWorkflowError(ValueError):
    """Annotation workflow 契约违规（test-local；非 production 异常）。"""


class DatasetVersionMismatchError(AnnotationWorkflowError):
    """Annotated Evidence 引用的 dataset_version 与实际 artifact 不一致。"""

    state = DATASET_VERSION_MISMATCH


class CaseNotFoundError(AnnotationWorkflowError):
    """Annotated Evidence 的 case_id 不存在于引用 artifact。"""

    state = CASE_NOT_FOUND


class AnnotationCompletenessError(AnnotationWorkflowError):
    """标注不完整（缺 dependency / reference / impact / review）。"""


# ============================================================
# Annotator Identity（§十三）
# ============================================================


def is_valid_annotator_id(value: str) -> bool:
    """annotator_id：非空 / 稳定 / 无邮箱 / 无姓名 / 无手机号形态。"""
    if not isinstance(value, str):
        return False
    if not value or value != value.strip():
        return False
    if not (_MIN_ANNOTATOR_ID_LENGTH <= len(value) <= _MAX_ANNOTATOR_ID_LENGTH):
        return False
    if not set(value) <= _ALLOWED_ANNOTATOR_ID_CHARS:
        return False  # 排除邮箱（@）、大写姓名、空格等
    if value.startswith("-") or value.endswith("-"):
        return False
    consecutive_digits = 0
    for char in value:
        consecutive_digits = consecutive_digits + 1 if char.isdigit() else 0
        if consecutive_digits >= _MAX_CONSECUTIVE_DIGITS:
            return False  # 手机号 / 长数字串形态
    return True


@dataclasses.dataclass(frozen=True)
class AnnotatorIdentity:
    """标注者身份（**不使用真实个人姓名**；只使用 anonymized id）。"""

    annotator_id: str

    def validate(self) -> None:
        if not is_valid_annotator_id(self.annotator_id):
            raise AnnotationWorkflowError(
                f"invalid annotator_id: {self.annotator_id!r}"
            )


# ============================================================
# Annotated Evidence（§六 ~ §十一）
# ============================================================


@dataclasses.dataclass(frozen=True)
class ReferenceAnnotation:
    """Reference Contract：reference_type ∈ {FULL_HISTORY, DOMAIN_EXPERT_VALIDATED}。"""

    reference_type: str
    outcome_type: str
    expected_behavior: str
    reference_created_by_human: bool = False
    reference_validated_by_domain_expert: bool = False

    def validate(self) -> None:
        if self.reference_type not in REFERENCE_TYPES:
            raise AnnotationWorkflowError(
                f"invalid reference_type: {self.reference_type!r}"
            )
        validate_reference(
            ReferenceRequirement(
                outcome_type=self.outcome_type,
                expected_behavior=self.expected_behavior,
                reference_created_by_human=self.reference_created_by_human,
                reference_validated_by_domain_expert=(
                    self.reference_validated_by_domain_expert
                ),
            )
        )


@dataclasses.dataclass(frozen=True)
class CaseAnnotation:
    """单 case 标注（字段可为 None 表示**未标注**；不自动填充默认值）。"""

    case_id: str
    dependency_annotation: ContextDependencyAnnotation | None = None
    reference_annotation: ReferenceAnnotation | None = None
    impact_annotation: ContextImpactAnnotation | None = None
    review_status: str | None = None

    def missing_fields(self) -> tuple[str, ...]:
        missing: list[str] = []
        if self.dependency_annotation is None:
            missing.append("dependency_annotation")
        if self.reference_annotation is None:
            missing.append("reference_annotation")
        if self.impact_annotation is None:
            missing.append("impact_annotation")
        if self.review_status is None:
            missing.append("review_status")
        return tuple(missing)

    def is_complete(self) -> bool:
        return not self.missing_fields()

    def validate(self) -> None:
        if not str(self.case_id).strip():
            raise AnnotationWorkflowError("case_id must be non-empty")
        if self.review_status is not None and (
            self.review_status not in REVIEW_STATUSES
        ):
            raise AnnotationWorkflowError(
                f"invalid review_status: {self.review_status!r}"
            )
        if self.dependency_annotation is not None:
            validate_annotation(self.dependency_annotation)
            if not (
                self.dependency_annotation.has_dependency()
                or self.dependency_annotation.standalone
            ):
                raise AnnotationWorkflowError(
                    "all-false dependency annotation is invalid "
                    "(must be standalone or have at least one dependency dimension)"
                )
        if self.reference_annotation is not None:
            self.reference_annotation.validate()
        if self.impact_annotation is not None:
            validate_impact(self.impact_annotation)


@dataclasses.dataclass(frozen=True)
class AnnotatedEvidence:
    """未来 Annotated Evidence 结构（**不复制原始 conversation content**）。"""

    dataset_version: str
    annotation_version: str
    source_type: str
    cases: tuple[CaseAnnotation, ...]
    annotator: AnnotatorIdentity

    def validate(self, *, require_complete: bool = False) -> None:
        if not self.dataset_version.strip() or self.dataset_version != (
            self.dataset_version.strip()
        ):
            raise AnnotationWorkflowError("invalid dataset_version")
        if not self.annotation_version.strip() or self.annotation_version != (
            self.annotation_version.strip()
        ):
            raise AnnotationWorkflowError("invalid annotation_version")
        if self.source_type not in ALLOWED_SOURCE_TYPES:
            raise AnnotationWorkflowError(f"invalid source_type: {self.source_type!r}")
        if not self.cases:
            raise AnnotationWorkflowError("cases must be non-empty")
        self.annotator.validate()

        seen: set[str] = set()
        for case in self.cases:
            case.validate()
            if case.case_id in seen:
                raise AnnotationWorkflowError(
                    f"duplicate annotation for case_id: {case.case_id}"
                )
            seen.add(case.case_id)

        if require_complete:
            missing = self.completeness()
            if missing:
                raise AnnotationCompletenessError(
                    f"annotation incomplete: {missing}"
                )

    def completeness(self) -> dict[str, tuple[str, ...]]:
        """返回 {case_id: 缺失字段}（只含不完整 case）。"""
        return {
            case.case_id: case.missing_fields()
            for case in self.cases
            if not case.is_complete()
        }

    @property
    def review_status(self) -> str:
        """整体状态推导：任一 DISPUTED → DISPUTED；全部 REVIEWED → REVIEWED；否则 DRAFT。"""
        statuses = [case.review_status for case in self.cases]
        if any(status == "DISPUTED" for status in statuses):
            return "DISPUTED"
        if statuses and all(status == "REVIEWED" for status in statuses):
            return "REVIEWED"
        return "DRAFT"

    def revise(
        self,
        *,
        new_annotation_version: str,
        cases: tuple[CaseAnnotation, ...] | None = None,
        annotator: AnnotatorIdentity | None = None,
    ) -> "AnnotatedEvidence":
        """修正必须创建**新 annotation_version**（REVIEWED 不可原地覆盖）。"""
        if new_annotation_version == self.annotation_version:
            raise AnnotationWorkflowError(
                "revision requires a new annotation_version "
                "(REVIEWED annotation is immutable)"
            )
        return AnnotatedEvidence(
            dataset_version=self.dataset_version,
            annotation_version=new_annotation_version,
            source_type=self.source_type,
            cases=cases if cases is not None else self.cases,
            annotator=annotator if annotator is not None else self.annotator,
        )


# ============================================================
# 校验 / 追溯 / 完整性 / G3（§十七 ~ §二十）
# ============================================================


def _iter_keys(payload: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            keys.append(str(key))
            keys.extend(_iter_keys(value))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            keys.extend(_iter_keys(item))
    return keys


def _iter_strings(payload: Any) -> list[str]:
    values: list[str] = []
    if isinstance(payload, str):
        values.append(payload)
    elif isinstance(payload, Mapping):
        for value in payload.values():
            values.extend(_iter_strings(value))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            values.extend(_iter_strings(item))
    return values


def validate_no_forbidden_annotation_fields(payload: Any) -> None:
    """§二十一：annotation payload 禁止 secret / SQL / stack trace / raw payload 等。"""
    for key in _iter_keys(payload):
        if key in FORBIDDEN_ANNOTATION_FIELDS:
            raise AnnotationWorkflowError(f"forbidden annotation field: {key}")
        if key in PRODUCTION_CORRELATION_IDS:
            raise AnnotationWorkflowError(f"production id forbidden: {key}")
    for text in _iter_strings(payload):
        for marker in _SECRET_MARKERS:
            if marker in text:
                raise AnnotationWorkflowError("secret-like value in annotation")
        for marker in _SQL_MARKERS:
            if marker in text:
                raise AnnotationWorkflowError("SQL-like value in annotation")
        for marker in _TRACE_MARKERS:
            if marker in text:
                raise AnnotationWorkflowError("stack-trace-like value in annotation")


def check_dataset_version(
    evidence: AnnotatedEvidence, artifact: Mapping[str, Any]
) -> None:
    """§十七：AnnotatedEvidence.dataset_version 必须与引用 Artifact 一致。"""
    if evidence.dataset_version != str(artifact.get("dataset_version")):
        raise DatasetVersionMismatchError(
            f"{DATASET_VERSION_MISMATCH}: annotated={evidence.dataset_version!r} "
            f"artifact={artifact.get('dataset_version')!r}"
        )


def check_case_ids(evidence: AnnotatedEvidence, artifact: Mapping[str, Any]) -> None:
    """§十八：annotation 的 case_id 必须存在于 artifact（不自动创建 case）。"""
    artifact_case_ids = {
        str(conversation.get("case_id"))
        for conversation in artifact.get("conversations", [])
        if isinstance(conversation, Mapping)
    }
    for case in evidence.cases:
        if case.case_id not in artifact_case_ids:
            raise CaseNotFoundError(f"{CASE_NOT_FOUND}: {case.case_id}")
    # annotation 不携带 conversation 内容：仅通过 case_id 引用原始证据


def evaluate_g3_evidence(
    *,
    g1_status: str,
    evidence: AnnotatedEvidence | None,
    artifact: Mapping[str, Any] | None = None,
) -> str:
    """G3 Evidence 门控（§二十）。

    READY 需要：G1 READY + Annotated Evidence 存在 + dataset_version match +
    case IDs valid + annotation complete + review status valid。
    """
    if g1_status != "READY" or evidence is None:
        return "BLOCKED"
    try:
        evidence.validate(require_complete=True)
        if artifact is None:
            return "BLOCKED"
        validate_artifact(artifact)
        check_dataset_version(evidence, artifact)
        check_case_ids(evidence, artifact)
    except AnnotationWorkflowError:
        return "BLOCKED"
    except EvaluationSchemaError:
        return "BLOCKED"
    return "READY"


def aggregate_dependency_for_g3(
    evidence: AnnotatedEvidence, *, g1_status: str
) -> dict[str, Any]:
    """把 annotated dependency 交给 Step 25 的 G3 procedure（计数）。"""
    annotations = [
        case.dependency_annotation
        for case in evidence.cases
        if case.dependency_annotation is not None
    ]
    return run_g3_procedure(annotations, g1_status=g1_status)


# ============================================================
# 模拟数据（NOT real WMS evidence；仅契约模拟）
# ============================================================


def _simulated_artifact(
    *, dataset_version: str = "wms-conversation-v1"
) -> dict[str, Any]:
    return {
        "dataset_version": dataset_version,
        "source_type": SOURCE_TYPE_REAL,
        "conversations": [
            {
                "case_id": "contract_sim_001",
                "project_id": "vietnam-wms",
                "turns": [
                    {"role": "user", "content": "只看 A01 仓库"},
                    {"role": "assistant", "content": "已限定 A01 仓库。"},
                    {"role": "user", "content": "查询库存"},
                ],
            },
            {
                "case_id": "contract_sim_002",
                "project_id": "vietnam-wms",
                "turns": [
                    {"role": "user", "content": "查询入库单"},
                    {"role": "assistant", "content": "共 12 张（示例）。"},
                    {"role": "user", "content": "其中今天的呢"},
                ],
            },
        ],
    }


def _dependency(**overrides: bool) -> ContextDependencyAnnotation:
    values = {"early_constraint": True}
    values.update(overrides)
    return ContextDependencyAnnotation(
        case_id="contract_sim_001", current_turn_id="t3", **values
    )


def _impact(**overrides: str) -> ContextImpactAnnotation:
    values = {
        "business_outcome_changed": "false",
        "critical_constraint_lost": "false",
        "entity_changed": "false",
        "intent_changed": "false",
    }
    values.update(overrides)
    return ContextImpactAnnotation(
        case_id="contract_sim_001",
        full_history_result="A01 库存 320",
        selected_history_result="全部仓库库存 1000",
        **values,
    )


def _reference(
    *, reference_type: str = "FULL_HISTORY", outcome_type: str = "SQL_SEMANTICS"
) -> ReferenceAnnotation:
    return ReferenceAnnotation(
        reference_type=reference_type,
        outcome_type=outcome_type,
        expected_behavior="仅统计 A01 仓库库存",
        reference_created_by_human=True,
    )


def _case(
    case_id: str = "contract_sim_001",
    *,
    complete: bool = True,
    review_status: str | None = "REVIEWED",
    dependency: ContextDependencyAnnotation | None = None,
    reference: ReferenceAnnotation | None = None,
    impact: ContextImpactAnnotation | None = None,
) -> CaseAnnotation:
    if not complete:
        return CaseAnnotation(case_id=case_id, review_status=review_status)
    return CaseAnnotation(
        case_id=case_id,
        dependency_annotation=dependency or _dependency(),
        reference_annotation=reference or _reference(),
        impact_annotation=impact or _impact(),
        review_status=review_status,
    )


def _evidence(
    *,
    dataset_version: str = "wms-conversation-v1",
    annotation_version: str = "context-annotation-v1",
    source_type: str = SOURCE_TYPE_REAL,
    cases: tuple[CaseAnnotation, ...] | None = None,
) -> AnnotatedEvidence:
    return AnnotatedEvidence(
        dataset_version=dataset_version,
        annotation_version=annotation_version,
        source_type=source_type,
        cases=cases if cases is not None else (_case(),),
        annotator=AnnotatorIdentity("domain-reviewer-01"),
    )


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8-sig")


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
# 1. Schema（§六 / §二十三 Schema）
# ============================================================


class TestAnnotatedEvidenceSchema:
    def test_01_valid_annotated_evidence(self) -> None:
        evidence = _evidence()
        evidence.validate(require_complete=True)
        fields = {field.name for field in dataclasses.fields(AnnotatedEvidence)}
        assert fields == set(ANNOTATED_EVIDENCE_FIELDS)
        assert evidence.completeness() == {}

    def test_02_missing_dataset_version_rejected(self) -> None:
        for bad in ("", "  "):
            try:
                _evidence(dataset_version=bad).validate()
            except AnnotationWorkflowError:
                continue
            raise AssertionError("invalid dataset_version must be rejected")  # pragma: no cover

    def test_03_missing_annotation_version_rejected(self) -> None:
        try:
            _evidence(annotation_version="").validate()
        except AnnotationWorkflowError:
            pass
        else:  # pragma: no cover
            raise AssertionError("invalid annotation_version must be rejected")

    def test_04_invalid_source_type_rejected(self) -> None:
        for bad in ("REAL", "synthetic_only", ""):
            try:
                _evidence(source_type=bad).validate()
            except AnnotationWorkflowError:
                continue
            raise AssertionError("invalid source_type must be rejected")  # pragma: no cover

    def test_05_missing_cases_rejected(self) -> None:
        try:
            _evidence(cases=()).validate()
        except AnnotationWorkflowError:
            pass
        else:  # pragma: no cover
            raise AssertionError("empty cases must be rejected")

    def test_06_annotated_evidence_holds_no_conversation_content(self) -> None:
        """§六：不复制原始 conversation content（字段集合锁定）。"""
        fields = {field.name for field in dataclasses.fields(AnnotatedEvidence)}
        for forbidden in ("content", "turns", "conversations", "messages"):
            assert forbidden not in fields, forbidden
        case_fields = {field.name for field in dataclasses.fields(CaseAnnotation)}
        assert case_fields == set(CASE_ANNOTATION_FIELDS)
        for forbidden in ("content", "turns", "answer", "sql", "prompt"):
            assert forbidden not in case_fields, forbidden


# ============================================================
# 2. Version（§七 / §十七 / §二十三 Version）
# ============================================================


class TestVersioning:
    def test_07_dataset_version_match(self) -> None:
        artifact = _simulated_artifact()
        evidence = _evidence()
        check_dataset_version(evidence, artifact)

    def test_08_dataset_version_mismatch(self) -> None:
        artifact = _simulated_artifact(dataset_version="wms-conversation-v2")
        evidence = _evidence(dataset_version="wms-conversation-v1")
        try:
            check_dataset_version(evidence, artifact)
        except DatasetVersionMismatchError as exc:
            assert exc.state == DATASET_VERSION_MISMATCH
        else:  # pragma: no cover
            raise AssertionError("dataset version mismatch must be raised")
        assert DATASET_VERSION_MISMATCH == "DATASET_VERSION_MISMATCH"

    def test_09_annotation_version_is_independent(self) -> None:
        """§七：同一 dataset 可以有多个 annotation_version（修正标注）。"""
        artifact = _simulated_artifact()
        v1 = _evidence(annotation_version="context-annotation-v1")
        v2 = v1.revise(new_annotation_version="context-annotation-v2")
        assert v2.annotation_version != v1.annotation_version
        assert v2.dataset_version == v1.dataset_version  # 原始 dataset 不变
        check_dataset_version(v2, artifact)

    def test_10_annotation_version_does_not_replace_dataset_version(self) -> None:
        evidence = _evidence()
        assert evidence.dataset_version != evidence.annotation_version
        assert evidence.dataset_version == "wms-conversation-v1"
        assert evidence.annotation_version == "context-annotation-v1"


# ============================================================
# 3. Case（§十八 / §二十三 Case）
# ============================================================


class TestCaseCoverage:
    def test_11_valid_case_ids(self) -> None:
        artifact = _simulated_artifact()
        evidence = _evidence(
            cases=(_case("contract_sim_001"), _case("contract_sim_002"))
        )
        evidence.validate(require_complete=True)
        check_case_ids(evidence, artifact)

    def test_12_unknown_case_id_not_found(self) -> None:
        artifact = _simulated_artifact()
        evidence = _evidence(cases=(_case("unknown_case_999"),))
        try:
            check_case_ids(evidence, artifact)
        except CaseNotFoundError as exc:
            assert exc.state == CASE_NOT_FOUND
        else:  # pragma: no cover
            raise AssertionError("unknown case_id must raise CASE_NOT_FOUND")
        assert CASE_NOT_FOUND == "CASE_NOT_FOUND"

    def test_13_duplicate_annotation_case_rejected(self) -> None:
        try:
            _evidence(cases=(_case("contract_sim_001"), _case("contract_sim_001"))).validate()
        except AnnotationWorkflowError as exc:
            assert "duplicate annotation" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("duplicate annotation case must be rejected")

    def test_14_no_auto_case_creation(self) -> None:
        """§十八：不能自动创建 Case。"""
        function_names = _non_test_function_names(_SELF)
        for forbidden in ("create_case", "add_case", "auto_create"):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{forbidden}"


# ============================================================
# 4. Dependency Annotation（§八 / §二十三 Dependency）
# ============================================================


class TestDependencyAnnotation:
    def test_15_single_label(self) -> None:
        case = _case(dependency=_dependency(early_constraint=True, recent_context=False))
        case.validate()
        assert case.dependency_annotation is not None
        assert case.dependency_annotation.dependency_dimensions() == ("early_constraint",)

    def test_16_multi_label_allowed(self) -> None:
        case = _case(
            dependency=_dependency(early_constraint=True, middle_decision=True)
        )
        case.validate()
        assert len(case.dependency_annotation.dependency_dimensions()) == 2
        assert set(ANNOTATION_TYPES) == {
            "early_constraint",
            "middle_decision",
            "recent_context",
            "old_topic",
            "standalone",
        }

    def test_17_all_false_invalid(self) -> None:
        """§二十三：all false invalid（既无依赖也不 standalone）。"""
        case = _case(dependency=_dependency(early_constraint=False))
        try:
            case.validate()
        except AnnotationWorkflowError as exc:
            assert "all-false" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("all-false dependency must be rejected")

    def test_18_standalone_only_is_valid(self) -> None:
        case = _case(
            dependency=ContextDependencyAnnotation(
                case_id="contract_sim_001", current_turn_id="t3", standalone=True
            )
        )
        case.validate()
        assert case.dependency_annotation.has_dependency() is False
        assert case.dependency_annotation.standalone is True


# ============================================================
# 5. Impact Annotation（§九 / §二十三 Impact）
# ============================================================


class TestImpactAnnotation:
    def test_19_true_false_unknown_supported(self) -> None:
        for value in IMPACT_VALUES:
            case = _case(impact=_impact(business_outcome_changed=value))
            case.validate()
            assert getattr(case.impact_annotation, "business_outcome_changed") == value

    def test_20_unknown_is_not_false(self) -> None:
        """§九：unknown 不能自动变成 false。"""
        assert IMPACT_VALUES == ("true", "false", "unknown")
        unknown_all = _impact(**{field: "unknown" for field in IMPACT_FIELDS})
        false_all = _impact()
        assert classify_g4_impact(unknown_all) == "UNKNOWN"
        assert classify_g4_impact(false_all) == "NO_IMPACT"
        assert classify_g4_impact(unknown_all) != classify_g4_impact(false_all)

    def test_21_invalid_impact_value_rejected(self) -> None:
        try:
            _case(impact=_impact(intent_changed="maybe")).validate()
        except EvaluationSchemaError:
            pass
        else:  # pragma: no cover
            raise AssertionError("invalid impact value must be rejected")

    def test_22_no_automatic_default_filling(self) -> None:
        """§十九：禁止 missing → false / missing → unknown 自动填充。"""
        function_names = _non_test_function_names(_SELF)
        for forbidden in (
            "default_annotation",
            "fill_missing",
            "coerce_unknown",
            "default_impact",
        ):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{forbidden}"
        doc = _source(_AUDIT_DOC)
        assert "missing annotation → false" in doc
        assert "missing impact → unknown" in doc


# ============================================================
# 6. Business Outcome / Reference（§十 / §十一）
# ============================================================


class TestOutcomeAndReference:
    def test_23_business_outcome_taxonomy_closed(self) -> None:
        assert OUTCOME_TYPES == (
            "ANSWER_CONTENT",
            "TOOL_SELECTION",
            "TOOL_ARGUMENTS",
            "SQL_SEMANTICS",
            "ROUTE",
            "REFUSAL",
        )
        for forbidden in FORBIDDEN_OUTCOME_TYPES:
            assert forbidden not in OUTCOME_TYPES, forbidden
            case = _case(reference=_reference(outcome_type=forbidden))
            try:
                case.validate()
            except EvaluationSchemaError:
                continue
            raise AssertionError(  # pragma: no cover
                f"outcome type {forbidden} must be rejected"
            )

    def test_24_reference_types(self) -> None:
        assert REFERENCE_TYPES == ("FULL_HISTORY", "DOMAIN_EXPERT_VALIDATED")
        for reference_type in REFERENCE_TYPES:
            _case(reference=_reference(reference_type=reference_type)).validate()

    def test_25_invalid_reference_type_rejected(self) -> None:
        try:
            _case(reference=_reference(reference_type="LLM_GENERATED")).validate()
        except AnnotationWorkflowError as exc:
            assert "reference_type" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("invalid reference_type must be rejected")

    def test_26_full_history_is_reference_condition_not_proof(self) -> None:
        """§十一：Full history 只是 reference condition，不等于业务正确性绝对证明。"""
        doc = _source(_AUDIT_DOC)
        assert "reference condition" in doc
        assert "绝对证明" in doc or "absolute proof" in doc


# ============================================================
# 7. Review / Disagreement（§十三 ~ §十五）
# ============================================================


class TestReviewAndDisagreement:
    def test_27_review_statuses_are_closed(self) -> None:
        assert REVIEW_STATUSES == ("DRAFT", "REVIEWED", "DISPUTED")
        for status in REVIEW_STATUSES:
            _case(review_status=status).validate()
        for forbidden in FORBIDDEN_REVIEW_STATUSES:
            assert forbidden not in REVIEW_STATUSES, forbidden
            try:
                _case(review_status=forbidden).validate()
            except AnnotationWorkflowError:
                continue
            raise AssertionError(  # pragma: no cover
                f"review status {forbidden} must be rejected"
            )

    def test_28_overall_status_derivation(self) -> None:
        draft = _evidence(cases=(_case(review_status="DRAFT"),))
        reviewed = _evidence(cases=(_case(review_status="REVIEWED"),))
        disputed = _evidence(
            cases=(
                _case("contract_sim_001", review_status="REVIEWED"),
                _case("contract_sim_002", review_status="DISPUTED"),
            )
        )
        assert draft.review_status == "DRAFT"
        assert reviewed.review_status == "REVIEWED"
        assert disputed.review_status == "DISPUTED"

    def test_29_annotator_identity_constraints(self) -> None:
        AnnotatorIdentity("domain-reviewer-01").validate()
        # 邮箱 / 手机号 / 大写姓名 / 空格 / 过短 / 过长形态一律拒绝
        for bad in (
            "Alice",
            "a@example.com",
            "13800138000",
            "Alice Smith",
            "  reviewer-01",
            "",
            "ab",
            "domain-reviewer-01-extra-very-long-identifier-exceeding-limit-aaaa",
        ):
            assert is_valid_annotator_id(bad) is False, bad
            try:
                AnnotatorIdentity(bad).validate()
            except AnnotationWorkflowError:
                continue
            raise AssertionError(f"annotator_id {bad!r} must be rejected")  # pragma: no cover
        # 匿名小写 id 有效（含连字符与短数字后缀）
        for good in ("domain-reviewer-01", "annotator-7", "qa-reviewer-12"):
            assert is_valid_annotator_id(good) is True, good
            AnnotatorIdentity(good).validate()

    def test_30_disagreement_without_majority_vote(self) -> None:
        """§十五：不一致 → DISAGREEMENT；禁止 majority vote / average / 自动选择。"""
        assert resolve_annotation_status("true", "true") == RESOLVED
        status = resolve_annotation_status("true", "unknown")
        assert status == DISAGREEMENT
        assert requires_domain_review(status) is True
        function_names = _non_test_function_names(_SELF)
        for forbidden in ("majority", "average", "vote", "auto_select", "pick_winner"):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{forbidden}"


# ============================================================
# 8. Immutability（§十六）
# ============================================================


class TestImmutability:
    def test_31_reviewed_evidence_is_frozen(self) -> None:
        evidence = _evidence()
        assert evidence.review_status == "REVIEWED"
        for field in ("dataset_version", "annotation_version", "source_type"):
            try:
                setattr(evidence, field, "mutated")
            except dataclasses.FrozenInstanceError:
                continue
            raise AssertionError(f"{field} must be frozen")  # pragma: no cover

    def test_32_revision_requires_new_annotation_version(self) -> None:
        evidence = _evidence()
        try:
            evidence.revise(new_annotation_version=evidence.annotation_version)
        except AnnotationWorkflowError as exc:
            assert "new annotation_version" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("same annotation_version revision must be rejected")

    def test_33_revision_creates_new_object_original_unchanged(self) -> None:
        original = _evidence()
        revised = original.revise(
            new_annotation_version="context-annotation-v2",
            cases=(
                _case(
                    review_status="DRAFT",
                    impact=_impact(entity_changed="unknown"),
                ),
            ),
        )
        assert revised.annotation_version == "context-annotation-v2"
        assert revised.dataset_version == original.dataset_version
        assert original.annotation_version == "context-annotation-v1"
        assert original.review_status == "REVIEWED"
        assert revised is not original

    def test_34_case_annotation_is_frozen(self) -> None:
        case = _case()
        try:
            case.review_status = "DRAFT"  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            pass
        else:  # pragma: no cover
            raise AssertionError("CaseAnnotation must be frozen")


# ============================================================
# 9. Completeness / G3（§十九 / §二十）
# ============================================================


class TestCompletenessAndG3:
    def test_35_missing_dependency_blocks_g3(self) -> None:
        artifact = _simulated_artifact()
        evidence = _evidence(
            cases=(CaseAnnotation(case_id="contract_sim_001",
                                  reference_annotation=_reference(),
                                  impact_annotation=_impact(),
                                  review_status="REVIEWED"),)
        )
        assert evidence.completeness() == {
            "contract_sim_001": ("dependency_annotation",)
        }
        assert (
            evaluate_g3_evidence(
                g1_status="READY", evidence=evidence, artifact=artifact
            )
            == "BLOCKED"
        )

    def test_36_missing_reference_blocks_g3(self) -> None:
        artifact = _simulated_artifact()
        evidence = _evidence(
            cases=(CaseAnnotation(case_id="contract_sim_001",
                                  dependency_annotation=_dependency(),
                                  impact_annotation=_impact(),
                                  review_status="REVIEWED"),)
        )
        assert "reference_annotation" in evidence.completeness()["contract_sim_001"]
        assert (
            evaluate_g3_evidence(
                g1_status="READY", evidence=evidence, artifact=artifact
            )
            == "BLOCKED"
        )

    def test_37_missing_impact_blocks_g3(self) -> None:
        artifact = _simulated_artifact()
        evidence = _evidence(
            cases=(CaseAnnotation(case_id="contract_sim_001",
                                  dependency_annotation=_dependency(),
                                  reference_annotation=_reference(),
                                  review_status="REVIEWED"),)
        )
        assert "impact_annotation" in evidence.completeness()["contract_sim_001"]
        assert (
            evaluate_g3_evidence(
                g1_status="READY", evidence=evidence, artifact=artifact
            )
            == "BLOCKED"
        )

    def test_38_missing_review_blocks_g3(self) -> None:
        artifact = _simulated_artifact()
        evidence = _evidence(
            cases=(CaseAnnotation(case_id="contract_sim_001",
                                  dependency_annotation=_dependency(),
                                  reference_annotation=_reference(),
                                  impact_annotation=_impact()),)
        )
        assert "review_status" in evidence.completeness()["contract_sim_001"]
        assert (
            evaluate_g3_evidence(
                g1_status="READY", evidence=evidence, artifact=artifact
            )
            == "BLOCKED"
        )

    def test_39_complete_evidence_ready_when_g1_ready(self) -> None:
        artifact = _simulated_artifact()
        evidence = _evidence()
        assert evidence.completeness() == {}
        assert (
            evaluate_g3_evidence(
                g1_status="READY", evidence=evidence, artifact=artifact
            )
            == "READY"
        )

    def test_40_g3_blocked_when_g1_blocked(self) -> None:
        artifact = _simulated_artifact()
        evidence = _evidence()
        assert (
            evaluate_g3_evidence(
                g1_status="BLOCKED", evidence=evidence, artifact=artifact
            )
            == "BLOCKED"
        )
        assert (
            evaluate_g3_evidence(g1_status="BLOCKED", evidence=None) == "BLOCKED"
        )

    def test_41_g3_blocked_on_version_or_case_mismatch(self) -> None:
        evidence = _evidence()
        wrong_version = _simulated_artifact(dataset_version="wms-conversation-v9")
        assert (
            evaluate_g3_evidence(
                g1_status="READY", evidence=evidence, artifact=wrong_version
            )
            == "BLOCKED"
        )
        wrong_case = _simulated_artifact()
        assert (
            evaluate_g3_evidence(
                g1_status="READY",
                evidence=_evidence(cases=(_case("unknown_case_999"),)),
                artifact=wrong_case,
            )
            == "BLOCKED"
        )

    def test_42_current_state_is_blocked(self) -> None:
        """当前真实状态：G1 BLOCKED → G3 Evidence BLOCKED。"""
        from tests.test_conversation_selection_evidence_data_audit import (
            audit_current_readiness,
        )

        g1_status = run_g1_procedure(audit_current_readiness())
        assert g1_status == "BLOCKED"
        assert (
            evaluate_g3_evidence(g1_status=g1_status, evidence=_evidence()) == "BLOCKED"
        )
        assert G3_EVIDENCE_STATES == ("READY", "BLOCKED")

    def test_43_dependency_aggregation_uses_step25_procedure(self) -> None:
        evidence = _evidence(
            cases=(
                _case("contract_sim_001", dependency=_dependency()),
                _case(
                    "contract_sim_002",
                    dependency=ContextDependencyAnnotation(
                        case_id="contract_sim_002",
                        current_turn_id="t3",
                        standalone=True,
                    ),
                ),
            )
        )
        result = aggregate_dependency_for_g3(evidence, g1_status="READY")
        assert result["status"] == "READY"
        assert result["annotation_counts"]["early_constraint"] == 1
        assert result["annotation_counts"]["standalone"] == 1


# ============================================================
# 10. Security / LLM 禁止 / 离线 / 文档（§十二 / §二十一 / §二十五）
# ============================================================


class TestSecurityAndDocument:
    def test_44_forbidden_fields_rejected(self) -> None:
        for field in FORBIDDEN_ANNOTATION_FIELDS:
            payload: dict[str, Any] = {field: "x"}
            try:
                validate_no_forbidden_annotation_fields(payload)
            except AnnotationWorkflowError:
                continue
            raise AssertionError(f"{field} must be rejected")  # pragma: no cover

    def test_45_secret_sql_and_trace_values_rejected(self) -> None:
        for payload in (
            {"note": "api_key=secret"},
            {"note": "SELECT * FROM inventory"},
            {"note": "Traceback (most recent call last)"},
        ):
            try:
                validate_no_forbidden_annotation_fields(payload)
            except AnnotationWorkflowError:
                continue
            raise AssertionError(f"payload must be rejected: {payload}")  # pragma: no cover
        # 允许字段
        validate_no_forbidden_annotation_fields(
            {"case_id": "c1", "project_id": "vietnam-wms", "review_status": "REVIEWED"}
        )

    def test_46_production_ids_rejected_in_annotation(self) -> None:
        for field in PRODUCTION_CORRELATION_IDS:
            try:
                validate_no_forbidden_annotation_fields({field: "x"})
            except AnnotationWorkflowError:
                continue
            raise AssertionError(f"{field} must be rejected")  # pragma: no cover

    def test_47_no_llm_as_judge(self) -> None:
        """§十二：禁止 LLM 自动生成 annotation / 决定 impact / 决定 winner。"""
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
        function_names = _non_test_function_names(_SELF)
        for forbidden in ("llm", "judge", "ai_suggest", "auto_annotate"):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{forbidden}"

    def test_48_document_sections_and_statements(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in (
            "## 1. Workflow",
            "## 2. Raw vs Annotated",
            "## 3. Versioning",
            "## 4. Dependency Annotation",
            "## 5. Impact Annotation",
            "## 6. Business Outcome",
            "## 7. Reference",
            "## 8. Review",
            "## 9. Disagreement",
            "## 10. Security",
            "## 11. Production Isolation",
            "## 12. G3 Gate",
            "## 13. Deferred",
        ):
            assert section in doc, section
        for statement in (
            "DATASET_VERSION_MISMATCH",
            "CASE_NOT_FOUND",
            "DRAFT",
            "REVIEWED",
            "DISPUTED",
            "FULL_HISTORY",
            "DOMAIN_EXPERT_VALIDATED",
            "project_id != authorization",
            "LLM suggestion",
            "Human / Domain Expert Review",
            "Final Annotation",
            "G1 = BLOCKED",
            "G3 Evidence = BLOCKED",
            "Selection Strategy = BLOCKED",
        ):
            assert statement in doc, statement

    def test_49_deterministic(self) -> None:
        assert _evidence() == _evidence()
        validate_no_forbidden_annotation_fields({"case_id": "c1"})
        validate_no_forbidden_annotation_fields({"case_id": "c1"})
        assert is_valid_annotator_id("domain-reviewer-01") is True


__all__ = [
    "REVIEW_STATUSES",
    "REFERENCE_TYPES",
    "ANNOTATED_EVIDENCE_FIELDS",
    "DATASET_VERSION_MISMATCH",
    "CASE_NOT_FOUND",
    "G3_EVIDENCE_STATES",
    "FORBIDDEN_ANNOTATION_FIELDS",
    "AnnotationWorkflowError",
    "DatasetVersionMismatchError",
    "CaseNotFoundError",
    "AnnotationCompletenessError",
    "AnnotatorIdentity",
    "ReferenceAnnotation",
    "CaseAnnotation",
    "AnnotatedEvidence",
    "is_valid_annotator_id",
    "validate_no_forbidden_annotation_fields",
    "check_dataset_version",
    "check_case_ids",
    "evaluate_g3_evidence",
    "aggregate_dependency_for_g3",
]
