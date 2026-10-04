"""Real Annotation Execution Contract（Phase 3.12 Step 32 —— **Contract only**）。

本文件冻结未来"真实脱敏数据人工标注"如何执行（test-local；**非 production**），
但**不执行真实人工标注**：

```text
REAL_DEIDENTIFIED Evidence Candidate
        ↓
Case Sampling（explicit / deterministic / auditable）
        ↓
Annotator 1 Input ─┐
Annotator 2 Input ─┴─ Independent Annotation（human；本阶段不产生）
        ↓
Step 29 Compare（复用，不重实现）
        ↓
Agreement / Disagreement → Domain Review（复用）
        ↓
Step 30 Finalization（复用）
        ↓
Future G3 Evidence（本阶段不产生）
```

复用既有契约（不重新设计）：Step 24 annotation schema、Step 28 AnnotatedEvidence /
validate_no_forbidden_annotation_fields、Step 29 compare/review、
Step 30 finalization、Step 31 import contract / role 枚举。

边界：DB = 0 · Network = 0 · LLM = 0 · 不导入真实 WMS 数据 · 不执行真实人工标注 ·
不自动生成 annotation · 不自动产生 AGREEMENT / DISAGREEMENT / REVIEWED。
"""
from __future__ import annotations

import ast
import copy
import dataclasses
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tests.test_conversation_context_annotation_review import (
    AnnotationDraft,
    compare_annotations,
)
from tests.test_conversation_context_annotation_workflow import (
    AnnotatedEvidence,
    AnnotationWorkflowError,
    AnnotatorIdentity,
    CaseAnnotation,
    ContextDependencyAnnotation,
    ContextImpactAnnotation,
    ReferenceAnnotation,
    evaluate_g3_evidence,
    validate_no_forbidden_annotation_fields,
)
from tests.test_conversation_context_real_evidence_import import (
    ALLOWED_ROLES,
)
from tests.test_conversation_context_evidence_collection_procedure import (
    run_g1_procedure,
    run_g2_procedure,
    run_g3_procedure,
    run_g4_procedure,
)
from tests.test_conversation_context_real_evidence_boundary import (
    SOURCE_TYPE_REAL,
    SOURCE_TYPE_SYNTHETIC,
)
from tests.test_conversation_context_real_evidence_import import (
    IMPORT_EXTRA_FORBIDDEN_FIELDS,
    import_real_evidence,
)
from tests.test_conversation_selection_evidence_data_audit import (
    audit_current_readiness,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_real_annotation_execution_contract.py"
_AUDIT_DOC = (
    "docs/evaluation/Phase 3.12 Step 32 — Real Annotation Execution Contract.md"
)
_FIXTURE = (
    "tests/fixtures/conversation_context/real_annotation_execution_cases.yaml"
)

# ============================================================
# 常量（test-local）
# ============================================================

#: Execution failure codes（少量结构化；不建立几十个 taxonomy）。
FAILURE_CODES: tuple[str, ...] = (
    "DATASET_VERSION_MISMATCH",
    "CASE_NOT_FOUND",
    "DUPLICATE_CASE_ID",
    "ANNOTATOR_NOT_INDEPENDENT",
    "ANNOTATOR_INPUT_LEAK",
    "INVALID_CASE",
)

EXECUTION_RESULT_FIELDS: tuple[str, ...] = (
    "dataset_version",
    "cases_presented",
    "annotator_1",
    "annotator_2",
    "independence_valid",
    "isolation_valid",
    "blocked_cases",
    "failure_codes",
)

#: 结果中禁止出现的分析类字段（属后续 Evidence Analysis）。
FORBIDDEN_RESULT_FIELDS: tuple[str, ...] = (
    "score",
    "accuracy",
    "agreement_rate",
    "winner",
    "ranking",
    "quality_score",
)

#: Annotator 输入白名单（§十 / §十二）。
ALLOWED_INPUT_KEYS: tuple[str, ...] = (
    "dataset_version",
    "case_id",
    "project_id",
    "turns",
)
ALLOWED_INPUT_TURN_KEYS: tuple[str, ...] = ("role", "content")

#: 禁止进入 annotator 输入的字段（输入隔离）。
FORBIDDEN_INPUT_KEYS: tuple[str, ...] = (
    "annotator_id",
    "annotator_1",
    "annotator_2",
    "annotator_1_annotation",
    "annotator_2_annotation",
    "previous_annotation",
    "review_hint",
    "agreement_hint",
    "disagreement_hint",
    "annotation",
    "annotation_version",
    "review_status",
    "comparison_outcome",
)

EXECUTION_SCENARIOS: tuple[str, ...] = (
    "normal_independent",
    "duplicate_case",
    "missing_case",
    "dataset_version_mismatch",
    "same_annotator",
    "input_leakage",
)

EVIDENCE_DATASET_VERSION = "wms-v1"
EVIDENCE_SOURCE_TYPE = SOURCE_TYPE_SYNTHETIC  # fixture 为 SYNTHETIC_ONLY（不伪装真实数据）
EXAMPLE_ANNOTATION_VERSION = "annotation-v1"

_DEFAULT_ANNOTATOR_1 = "annotator-a"
_DEFAULT_ANNOTATOR_2 = "annotator-b"
_DEFAULT_REQUESTED_VERSION = EVIDENCE_DATASET_VERSION


class AnnotationExecutionError(ValueError):
    """Annotation execution 契约违规（test-local；非 production 异常）。"""

    def __init__(self, code: str, message: str = "", case_id: str = "") -> None:
        assert code in FAILURE_CODES, code
        self.code = code
        self.case_id = case_id
        detail = f" ({case_id})" if case_id else ""
        super().__init__(f"{code}{detail}: {message}" if message else f"{code}{detail}")


# ============================================================
# DTO（test-local；frozen）
# ============================================================


@dataclasses.dataclass(frozen=True)
class AnnotationSamplingResult:
    """Case 抽样结果（只 select，不修改 evidence）。"""

    dataset_version: str
    case_ids: tuple[str, ...]
    sample_count: int


@dataclasses.dataclass(frozen=True)
class AnnotatorCaseInput:
    """单个 annotator 的 case 输入（**不含**他人标注 / review 状态 / annotator_id）。"""

    dataset_version: str
    case_id: str
    project_id: str
    turns: tuple[dict[str, str], ...]


@dataclasses.dataclass(frozen=True)
class RealAnnotationExecutionResult:
    """标注执行结果（**不含**任何 annotation 生成物与分析指标）。"""

    dataset_version: str
    cases_presented: tuple[str, ...]
    annotator_1: str
    annotator_2: str
    independence_valid: bool
    isolation_valid: bool
    blocked_cases: tuple[str, ...]
    failure_codes: tuple[str, ...]


# ============================================================
# Sampling（§五 ~ §八：deterministic / explicit / auditable）
# ============================================================


def _conversations(evidence: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    conversations = evidence.get("conversations")
    return list(conversations) if isinstance(conversations, list) else []


def _case_ids_of(evidence: Mapping[str, Any]) -> list[str]:
    return [str(conversation.get("case_id", "")) for conversation in _conversations(evidence)]


def sample_cases(
    evidence: Mapping[str, Any],
    *,
    case_ids: Sequence[str] | None = None,
    start: int = 0,
    limit: int | None = None,
    requested_dataset_version: str | None = None,
) -> AnnotationSamplingResult:
    """Case 抽样（deterministic；只 select，不改 case）。

    * 显式 `case_ids` → 按传入顺序（auditable）；
    * 未给出 → ``sorted(all_case_ids)[start : start+limit]``；
    * 禁止 random / time-based / LLM sampling。
    """
    dataset_version = str(evidence.get("dataset_version", ""))
    if not dataset_version.strip():
        raise AnnotationExecutionError("INVALID_CASE", "dataset_version must be non-empty")

    if requested_dataset_version is not None and requested_dataset_version != dataset_version:
        raise AnnotationExecutionError(
            "DATASET_VERSION_MISMATCH",
            f"requested={requested_dataset_version!r} evidence={dataset_version!r}",
        )

    available = _case_ids_of(evidence)
    available_set = set(available)

    if case_ids is not None:
        selected: list[str] = []
        seen: set[str] = set()
        for raw in case_ids:
            case_id = str(raw).strip()
            if not case_id:
                raise AnnotationExecutionError("INVALID_CASE", "case_id must be non-empty")
            if case_id in seen:
                raise AnnotationExecutionError(
                    "DUPLICATE_CASE_ID", "duplicate in requested list", case_id
                )
            if case_id not in available_set:
                raise AnnotationExecutionError("CASE_NOT_FOUND", "not in evidence", case_id)
            seen.add(case_id)
            selected.append(case_id)
        return AnnotationSamplingResult(
            dataset_version=dataset_version,
            case_ids=tuple(selected),
            sample_count=len(selected),
        )

    ordered = sorted(available)
    if start < 0:
        raise AnnotationExecutionError("INVALID_CASE", "start must be >= 0")
    window = ordered[start:] if limit is None else ordered[start : start + limit]
    return AnnotationSamplingResult(
        dataset_version=dataset_version,
        case_ids=tuple(window),
        sample_count=len(window),
    )


def build_case_input(evidence: Mapping[str, Any], case_id: str) -> AnnotatorCaseInput:
    """构造单个 annotator 的只读 case 输入（不修改原 evidence）。"""
    for conversation in _conversations(evidence):
        if str(conversation.get("case_id", "")) != case_id:
            continue
        turns_raw = conversation.get("turns")
        if not isinstance(turns_raw, list) or not turns_raw:
            raise AnnotationExecutionError("INVALID_CASE", "turns empty", case_id)
        turns: list[dict[str, str]] = []
        for turn in turns_raw:
            if not isinstance(turn, Mapping):
                raise AnnotationExecutionError("INVALID_CASE", "turn invalid", case_id)
            role = str(turn.get("role", ""))
            if role not in ALLOWED_ROLES:
                raise AnnotationExecutionError("INVALID_CASE", f"role {role!r}", case_id)
            content = turn.get("content")
            if not isinstance(content, str) or not content.strip():
                raise AnnotationExecutionError("INVALID_CASE", "content empty", case_id)
            turns.append({"role": role, "content": content})
        return AnnotatorCaseInput(
            dataset_version=str(evidence.get("dataset_version", "")),
            case_id=case_id,
            project_id=str(conversation.get("project_id", "")),
            turns=tuple(turns),
        )
    raise AnnotationExecutionError("CASE_NOT_FOUND", "not in evidence", case_id)


# ============================================================
# Independence / Isolation（§九 / §十 / §十二）
# ============================================================


def check_independence(annotator_1: str, annotator_2: str) -> bool:
    return bool(annotator_1) and bool(annotator_2) and annotator_1 != annotator_2


def check_input_isolation(payload: Mapping[str, Any]) -> bool:
    """Annotator 输入只允许白名单字段（不含他人标注 / 提示 / annotator_id）。"""
    for key in payload:
        name = str(key)
        if name in FORBIDDEN_INPUT_KEYS:
            return False
        if name not in ALLOWED_INPUT_KEYS:
            return False
    turns = payload.get("turns")
    if isinstance(turns, (list, tuple)):
        for turn in turns:
            if not isinstance(turn, Mapping):
                return False
            for key in turn:
                if str(key) not in ALLOWED_INPUT_TURN_KEYS:
                    return False
    return True


def check_cross_isolation(
    input_a: AnnotatorCaseInput,
    input_b: AnnotatorCaseInput,
    *,
    annotation_a: Mapping[str, Any] | None = None,
    annotation_b: Mapping[str, Any] | None = None,
) -> bool:
    """A 的输入不得含 B 的标注（与反向）—— 输入与标注 payload 结构上互不可见。

    语义：标注 payload 中**不属于输入白名单**的键（如 early_constraint /
    recent_context / previous_annotation）不得出现在另一 annotator 的输入中。
    """
    def _keys(payload: Mapping[str, Any]) -> set[str]:
        keys: set[str] = set()
        for key, value in payload.items():
            keys.add(str(key))
            if isinstance(value, Mapping):
                keys |= _keys(value)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, Mapping):
                        keys |= _keys(item)
        return keys

    a_payload = dataclasses.asdict(input_a)
    b_payload = dataclasses.asdict(input_b)
    a_keys = _keys(a_payload)
    b_keys = _keys(b_payload)

    def _annotation_only_keys(annotation: Mapping[str, Any]) -> set[str]:
        return {key for key in _keys(annotation) if key not in ALLOWED_INPUT_KEYS}

    if annotation_b is not None and _annotation_only_keys(annotation_b) & a_keys:
        return False
    if annotation_a is not None and _annotation_only_keys(annotation_a) & b_keys:
        return False
    return check_input_isolation(a_payload) and check_input_isolation(b_payload)


# ============================================================
# Execution（§十六）
# ============================================================


def execute_annotation_assignment(
    evidence: Mapping[str, Any],
    *,
    annotator_1: str,
    annotator_2: str,
    case_ids: Sequence[str] | None = None,
    start: int = 0,
    limit: int | None = None,
    requested_dataset_version: str | None = None,
    annotator_payload_extra: Mapping[str, Any] | None = None,
) -> tuple[RealAnnotationExecutionResult, tuple[AnnotatorCaseInput, ...]]:
    """执行标注**分配**（不下发真实标注任务、不产生 annotation）。"""
    blocked: list[str] = []
    failures: list[str] = []

    def fail(code: str, case_id: str = "") -> None:
        if case_id and case_id not in blocked:
            blocked.append(case_id)
        if code not in failures:
            failures.append(code)

    independence_valid = check_independence(annotator_1, annotator_2)
    if not independence_valid:
        fail("ANNOTATOR_NOT_INDEPENDENT")

    dataset_version = str(evidence.get("dataset_version", ""))
    try:
        sampling = sample_cases(
            evidence,
            case_ids=case_ids,
            start=start,
            limit=limit,
            requested_dataset_version=requested_dataset_version,
        )
    except AnnotationExecutionError as exc:
        fail(exc.code, exc.case_id)
        sampling = AnnotationSamplingResult(
            dataset_version=dataset_version, case_ids=(), sample_count=0
        )

    inputs: list[AnnotatorCaseInput] = []
    for case_id in sampling.case_ids:
        try:
            inputs.append(build_case_input(evidence, case_id))
        except AnnotationExecutionError as exc:
            fail(exc.code, case_id)

    isolation_valid = True
    for input_dto in inputs:
        if not check_input_isolation(dataclasses.asdict(input_dto)):
            isolation_valid = False
    if annotator_payload_extra:
        # 执行层若试图把额外信息塞进 annotator 输入 → 输入隔离违规
        if not check_input_isolation(dict(annotator_payload_extra)):
            isolation_valid = False
    if not isolation_valid:
        fail("ANNOTATOR_INPUT_LEAK")

    try:
        validate_no_forbidden_annotation_fields(evidence)
        for field in IMPORT_EXTRA_FORBIDDEN_FIELDS:
            for conversation in _conversations(evidence):
                if field in conversation:
                    raise AnnotationWorkflowError(f"forbidden: {field}")
    except AnnotationWorkflowError:
        fail("FORBIDDEN_FIELD")

    result = RealAnnotationExecutionResult(
        dataset_version=dataset_version,
        cases_presented=sampling.case_ids,
        annotator_1=annotator_1,
        annotator_2=annotator_2,
        independence_valid=independence_valid,
        isolation_valid=isolation_valid,
        blocked_cases=tuple(blocked),
        failure_codes=tuple(failures),
    )
    return result, tuple(inputs)


# ============================================================
# Fixture（SYNTHETIC_ONLY；文本解析，不依赖 yaml 库）
# ============================================================


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8-sig")


def fixture_records() -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in _source(_FIXTURE).splitlines():
        stripped = line.strip()
        if stripped.startswith("- kind:"):
            current = {"kind": stripped.split("- kind:", 1)[1].strip()}
            records.append(current)
            continue
        if current is None or not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("records:") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        current[key.strip()] = value.strip()
    return records


def fixture_cases() -> list[dict[str, str]]:
    return [record for record in fixture_records() if record["kind"] == "case"]


def fixture_scenarios() -> list[dict[str, str]]:
    return [record for record in fixture_records() if record["kind"] == "scenario"]


def build_evidence() -> dict[str, Any]:
    """构造 synthetic evidence（Step 26 结构；source_type = SYNTHETIC_ONLY）。"""
    conversations: list[dict[str, Any]] = []
    for case in fixture_cases():
        roles = case["roles"].split(",")
        conversations.append(
            {
                "case_id": case["case_id"],
                "project_id": case["project_id"],
                "turns": [
                    {"role": role.strip(), "content": f"示例内容（{case['case_id']}）"}
                    for role in roles
                ],
            }
        )
    return {
        "dataset_version": EVIDENCE_DATASET_VERSION,
        "source_type": EVIDENCE_SOURCE_TYPE,
        "conversations": conversations,
    }


def scenario_records(scenario: str) -> dict[str, str]:
    for record in fixture_scenarios():
        if record["scenario"] == scenario:
            return record
    raise AssertionError(f"fixture scenario missing: {scenario}")


def run_scenario(
    scenario: str,
) -> tuple[RealAnnotationExecutionResult, tuple[AnnotatorCaseInput, ...], dict[str, Any]]:
    record = scenario_records(scenario)
    evidence = build_evidence()
    requested = [
        case_id.strip() for case_id in record["requested"].split(",")
    ]
    leak_field = record.get("leak_field", "none")
    extra = {leak_field: {"early_constraint": True}} if leak_field != "none" else None
    result, inputs = execute_annotation_assignment(
        evidence,
        annotator_1=record.get("annotator_1", _DEFAULT_ANNOTATOR_1),
        annotator_2=record.get("annotator_2", _DEFAULT_ANNOTATOR_2),
        case_ids=requested,
        requested_dataset_version=record.get(
            "requested_dataset_version", _DEFAULT_REQUESTED_VERSION
        ),
        annotator_payload_extra=extra,
    )
    return result, inputs, evidence


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
# 1. Sampling（§五 ~ §八）
# ============================================================


class TestSamplingContract:
    def test_01_explicit_case_ids_preserve_order(self) -> None:
        evidence = build_evidence()
        sampling = sample_cases(evidence, case_ids=["exec_case_002", "exec_case_001"])
        assert sampling.case_ids == ("exec_case_002", "exec_case_001")
        assert sampling.sample_count == 2
        assert sampling.dataset_version == EVIDENCE_DATASET_VERSION

    def test_02_derived_sampling_is_sorted_and_windowed(self) -> None:
        evidence = build_evidence()
        full = sample_cases(evidence)
        assert full.case_ids == (
            "exec_case_001",
            "exec_case_002",
            "exec_case_003",
            "exec_case_004",
        )
        window = sample_cases(evidence, start=1, limit=2)
        assert window.case_ids == ("exec_case_002", "exec_case_003")

    def test_03_duplicate_case_rejected(self) -> None:
        result, _, _ = run_scenario("duplicate_case")
        assert "DUPLICATE_CASE_ID" in result.failure_codes
        assert result.cases_presented == ()

    def test_04_missing_case_rejected(self) -> None:
        result, _, _ = run_scenario("missing_case")
        assert "CASE_NOT_FOUND" in result.failure_codes
        assert "exec_case_999" in result.blocked_cases

    def test_05_dataset_version_mismatch_rejected(self) -> None:
        result, _, _ = run_scenario("dataset_version_mismatch")
        assert "DATASET_VERSION_MISMATCH" in result.failure_codes

    def test_06_empty_case_id_rejected(self) -> None:
        try:
            sample_cases(build_evidence(), case_ids=["  "])
        except AnnotationExecutionError as exc:
            assert exc.code == "INVALID_CASE"
        else:  # pragma: no cover
            raise AssertionError("empty case_id must be rejected")

    def test_07_empty_dataset_version_rejected(self) -> None:
        try:
            sample_cases({"dataset_version": "  ", "conversations": []})
        except AnnotationExecutionError as exc:
            assert exc.code == "INVALID_CASE"
        else:  # pragma: no cover
            raise AssertionError("empty dataset_version must be rejected")

    def test_08_sampling_does_not_modify_evidence(self) -> None:
        evidence = build_evidence()
        snapshot = copy.deepcopy(evidence)
        sample_cases(evidence, case_ids=["exec_case_001"])
        sample_cases(evidence, start=0, limit=2)
        assert evidence == snapshot

    def test_09_no_random_or_time_based_sampling(self) -> None:
        modules = _module_imports(_SELF)
        for module in modules:
            for forbidden in ("random", "time", "secrets", "uuid"):
                assert not module.startswith(forbidden), module
        function_names = _non_test_function_names(_SELF)
        for forbidden in ("random", "shuffle", "seed"):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{forbidden}"


# ============================================================
# 2. Independence / Isolation（§九 / §十 / §十二）
# ============================================================


class TestIndependenceAndIsolation:
    def test_10_normal_execution_passes(self) -> None:
        result, inputs, _ = run_scenario("normal_independent")
        assert result.failure_codes == ()
        assert result.independence_valid is True
        assert result.isolation_valid is True
        assert result.cases_presented == ("exec_case_001", "exec_case_002")
        assert len(inputs) == 2
        assert result.annotator_1 != result.annotator_2

    def test_11_same_annotator_rejected(self) -> None:
        result, _, _ = run_scenario("same_annotator")
        assert result.independence_valid is False
        assert "ANNOTATOR_NOT_INDEPENDENT" in result.failure_codes

    def test_12_input_leakage_rejected(self) -> None:
        result, _, _ = run_scenario("input_leakage")
        assert result.isolation_valid is False
        assert "ANNOTATOR_INPUT_LEAK" in result.failure_codes

    def test_13_input_keys_are_whitelisted(self) -> None:
        _, inputs, _ = run_scenario("normal_independent")
        for input_dto in inputs:
            payload = dataclasses.asdict(input_dto)
            assert set(payload) == set(ALLOWED_INPUT_KEYS)
            for turn in payload["turns"]:
                assert set(turn) == set(ALLOWED_INPUT_TURN_KEYS)
            for forbidden in FORBIDDEN_INPUT_KEYS:
                assert forbidden not in payload, forbidden

    def test_14_cross_isolation_both_directions(self) -> None:
        """A 的输入不含 B 的输出；B 的输入不含 A 的输出。"""
        _, inputs, _ = run_scenario("normal_independent")
        input_a, input_b = inputs[0], inputs[1]
        annotation_a = {"case_id": "exec_case_001", "early_constraint": True}
        annotation_b = {"case_id": "exec_case_001", "recent_context": True}
        assert (
            check_cross_isolation(
                input_a, input_b, annotation_a=annotation_a, annotation_b=annotation_b
            )
            is True
        )
        # 反例：把 B 的标注 key 混入 A 的输入 → 隔离失败
        leaked = dict(dataclasses.asdict(input_a), previous_annotation=annotation_b)
        assert check_input_isolation(leaked) is False

    def test_15_no_annotation_or_review_in_input(self) -> None:
        """§十二：annotator_id / previous_annotation / review_status 不得进入待标注内容。"""
        _, inputs, _ = run_scenario("normal_independent")
        rendered = repr(inputs)
        for forbidden in ("review_status", "previous_annotation", "annotator"):
            assert forbidden not in rendered, forbidden

    def test_16_execution_does_not_modify_evidence(self) -> None:
        evidence = build_evidence()
        snapshot = copy.deepcopy(evidence)
        execute_annotation_assignment(
            evidence,
            annotator_1=_DEFAULT_ANNOTATOR_1,
            annotator_2=_DEFAULT_ANNOTATOR_2,
            case_ids=["exec_case_001"],
        )
        assert evidence == snapshot


# ============================================================
# 3. Version / Revision（§十四 / §十五）
# ============================================================


class TestVersionAndRevision:
    def test_17_dataset_version_differs_from_annotation_version(self) -> None:
        assert EVIDENCE_DATASET_VERSION != EXAMPLE_ANNOTATION_VERSION
        result, _, _ = run_scenario("normal_independent")
        assert result.dataset_version == EVIDENCE_DATASET_VERSION
        # Execution Result 不含 annotation_version（标注版本属 annotation 产物）
        fields = {
            field.name
            for field in dataclasses.fields(RealAnnotationExecutionResult)
        }
        assert "annotation_version" not in fields

    def test_18_revision_requires_new_annotation_version(self) -> None:
        """§十五：复用 Step 28 revise（REVIEWED 不可覆盖；必须新 annotation_version）。"""
        evidence = AnnotatedEvidence(
            dataset_version=EVIDENCE_DATASET_VERSION,
            annotation_version=EXAMPLE_ANNOTATION_VERSION,
            source_type=EVIDENCE_SOURCE_TYPE,
            cases=(
                CaseAnnotation(
                    case_id="exec_case_001",
                    dependency_annotation=ContextDependencyAnnotation(
                        case_id="exec_case_001",
                        current_turn_id="t3",
                        early_constraint=True,
                    ),
                    reference_annotation=ReferenceAnnotation(
                        reference_type="FULL_HISTORY",
                        outcome_type="SQL_SEMANTICS",
                        expected_behavior="仅统计 A01 仓库库存",
                        reference_created_by_human=True,
                    ),
                    impact_annotation=ContextImpactAnnotation(
                        case_id="exec_case_001",
                        full_history_result="A01 库存 320",
                        selected_history_result="全部仓库库存 1000",
                        business_outcome_changed="false",
                        critical_constraint_lost="false",
                        entity_changed="false",
                        intent_changed="false",
                    ),
                    review_status="REVIEWED",
                ),
            ),
            annotator=AnnotatorIdentity("domain-reviewer-01"),
        )
        assert evidence.review_status == "REVIEWED"
        try:
            evidence.revise(new_annotation_version=EXAMPLE_ANNOTATION_VERSION)
        except AnnotationWorkflowError:
            pass
        else:  # pragma: no cover
            raise AssertionError("same-version revision must be rejected")
        revised = evidence.revise(new_annotation_version="annotation-v2")
        assert revised.annotation_version == "annotation-v2"
        assert evidence.annotation_version == EXAMPLE_ANNOTATION_VERSION  # 原版不变
        assert revised.dataset_version == EVIDENCE_DATASET_VERSION


# ============================================================
# 4. No Auto Annotation / No Auto Review（§十一 / §二十四）
# ============================================================


class TestNoAutoAnnotationOrReview:
    def test_19_no_annotation_generation(self) -> None:
        function_names = _non_test_function_names(_SELF)
        for forbidden in (
            "generate_annotation",
            "auto_label",
            "llm",
            "judge",
            "heuristic",
            "keyword",
            "majority",
            "vote",
        ):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{forbidden}"
        modules = _module_imports(_SELF)
        for module in modules:
            for forbidden in ("openai", "anthropic", "transformers"):
                assert not module.startswith(forbidden), module

    def test_20_no_auto_review_states(self) -> None:
        function_names = _non_test_function_names(_SELF)
        for forbidden in (
            "auto_agreement",
            "auto_review",
            "auto_dispute",
            "classify_review",
            "decide_review",
        ):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{name}"
        # Execution Result 不含 review 状态字段
        fields = {
            field.name
            for field in dataclasses.fields(RealAnnotationExecutionResult)
        }
        for forbidden in ("review_status", "agreement", "disagreement", "outcome"):
            assert forbidden not in fields, forbidden

    def test_21_result_holds_no_analysis_metrics(self) -> None:
        fields = {
            field.name
            for field in dataclasses.fields(RealAnnotationExecutionResult)
        }
        assert fields == set(EXECUTION_RESULT_FIELDS)
        for forbidden in FORBIDDEN_RESULT_FIELDS:
            for name in fields:
                assert forbidden not in name.lower(), name


# ============================================================
# 5. Security（§十八）
# ============================================================


class TestSecurity:
    def test_22_forbidden_fields_rejected(self) -> None:
        evidence = build_evidence()
        for field in ("api_key", "prompt", "sql", "embedding"):
            payload = copy.deepcopy(evidence)
            payload["conversations"][0][field] = "x"
            result, _ = execute_annotation_assignment(
                payload,
                annotator_1=_DEFAULT_ANNOTATOR_1,
                annotator_2=_DEFAULT_ANNOTATOR_2,
                case_ids=["exec_case_001"],
            )
            assert "FORBIDDEN_FIELD" in result.failure_codes, field

    def test_23_reuses_step28_and_step31_scanners(self) -> None:
        modules = _module_imports(_SELF)
        assert (
            "tests.test_conversation_context_annotation_workflow" in modules
        ), sorted(modules)
        assert (
            "tests.test_conversation_context_real_evidence_import" in modules
        ), sorted(modules)
        source = _source(_SELF)
        assert "validate_no_forbidden_annotation_fields(" in source

    def test_24_project_id_is_context_not_authorization(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "project_id" in doc
        assert "authorization" in doc
        function_names = _non_test_function_names(_SELF)
        for forbidden in ("authorize", "acl", "tenant", "permission"):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{forbidden}"


# ============================================================
# 6. Step 29 / 30 Compatibility（§二十三）
# ============================================================


class TestStep29And30Compatibility:
    def test_25_execution_output_feeds_step29_compare(self) -> None:
        """Execution 输出可作为 Step 29 输入（复用 compare_annotations，不重实现）。"""
        _, inputs, _ = run_scenario("normal_independent")
        first_input = inputs[0]
        assert first_input.case_id == "exec_case_001"
        draft_a = AnnotationDraft(
            case_id=first_input.case_id,
            annotator=AnnotatorIdentity("annotator-a"),
            dependency_annotation=ContextDependencyAnnotation(
                case_id=first_input.case_id,
                current_turn_id="t3",
                early_constraint=True,
            ),
            reference_annotation=ReferenceAnnotation(
                reference_type="FULL_HISTORY",
                outcome_type="SQL_SEMANTICS",
                expected_behavior="仅统计 A01 仓库库存",
                reference_created_by_human=True,
            ),
            impact_annotation=ContextImpactAnnotation(
                case_id=first_input.case_id,
                full_history_result="A01 库存 320",
                selected_history_result="全部仓库库存 1000",
                business_outcome_changed="false",
                critical_constraint_lost="false",
                entity_changed="false",
                intent_changed="false",
            ),
            business_outcome="SQL_SEMANTICS",
        )
        outcome = compare_annotations(draft_a, dataclasses.replace(draft_a, annotator=AnnotatorIdentity("annotator-b")))
        assert outcome.result == "AGREEMENT"

    def test_26_step29_30_functions_not_reimplemented(self) -> None:
        """不重实现 compare_annotations / resolve_annotation_status / finalize。"""
        function_names = _non_test_function_names(_SELF)
        for forbidden in (
            "compare_annotation",
            "resolve_annotation_status",
            "finalize_annotated",
        ):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{forbidden}"
        modules = _module_imports(_SELF)
        assert "tests.test_conversation_context_annotation_review" in modules

    def test_27_execution_pass_does_not_upgrade_g3(self) -> None:
        """§二十五：Execution PASS 不改变 G1~G4。"""
        result, _, evidence = run_scenario("normal_independent")
        assert result.failure_codes == ()

        g1_status = run_g1_procedure(audit_current_readiness())
        assert g1_status == "BLOCKED"
        assert run_g2_procedure((), g1_status=g1_status)["status"] == "INSUFFICIENT"
        assert run_g3_procedure((), g1_status=g1_status)["status"] == "BLOCKED"
        assert run_g4_procedure((), g1_status=g1_status)["status"] == "BLOCKED"

        # synthetic evidence 不能晋升为 REAL_DEIDENTIFIED
        assert evidence["source_type"] == SOURCE_TYPE_SYNTHETIC
        import_result = import_real_evidence(
            dict(
                evidence,
                source_type=SOURCE_TYPE_REAL,
                de_identification_attestation={"attested": True, "method": "external_process"},
            )
        )
        assert import_result.accepted is True  # 结构可作 import 契约模拟
        # 但真实 import 契约不接受 synthetic 声明
        synthetic_import = import_real_evidence(evidence)
        assert synthetic_import.accepted is False
        assert "INVALID_SOURCE_TYPE" in synthetic_import.failure_codes

    def test_28_synthetic_evidence_keeps_g3_blocked_via_step28(self) -> None:
        from tests.test_conversation_context_annotation_finalization import (
            EVIDENCE_DATASET_VERSION as STEP30_VERSION_STR,
        )
        from tests.test_conversation_context_annotation_finalization import (
            EVIDENCE_SOURCE_TYPE as STEP30_SOURCE,
        )

        # Step 30 与 Step 32 使用各自 fixture 的版本常量（均为 synthetic procedure 验证）
        assert isinstance(STEP30_VERSION_STR, str) and STEP30_VERSION_STR
        assert STEP30_SOURCE == EVIDENCE_SOURCE_TYPE == SOURCE_TYPE_SYNTHETIC
        assert (
            evaluate_g3_evidence(g1_status="BLOCKED", evidence=None) == "BLOCKED"
        )


# ============================================================
# 7. Fixture / Production Boundary / 文档
# ============================================================


class TestFixtureBoundaryAndDocument:
    def test_29_fixture_covers_declared_scenarios(self) -> None:
        scenarios = [record["scenario"] for record in fixture_scenarios()]
        assert sorted(set(scenarios)) == sorted(EXECUTION_SCENARIOS)
        assert len(fixture_cases()) == 4
        assert len(set(case["case_id"] for case in fixture_cases())) == 4

    def test_30_fixture_is_synthetic_only(self) -> None:
        text = _source(_FIXTURE)
        assert "SYNTHETIC_ONLY" in text
        assert "非生产数据" in text
        assert "不**伪装" in text or "不伪装" in text or "**不**伪装" in text
        for forbidden in (
            "conversation_id",
            "assistant_request_id",
            "turn_id",
            "provider_request_id",
        ):
            assert forbidden not in text, forbidden

    def test_31_production_boundary_unchanged(self) -> None:
        """backend/app 不得出现 execution contract 标识符（Production Code = 0）。"""
        backend = sorted((_REPO_ROOT / "backend/app").rglob("*.py"))
        assert backend, "backend/app 为空（审计失效）"
        forbidden_identifiers = (
            "RealAnnotationExecutionResult",
            "AnnotationSamplingResult",
            "AnnotatorCaseInput",
            "execute_annotation_assignment",
            "sample_cases",
            "real_annotation_execution",
        )
        offenders: list[str] = []
        for path in backend:
            tokens: set[str] = set()
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    tokens.add(node.id)
                elif isinstance(node, ast.Attribute):
                    tokens.add(node.attr)
                elif isinstance(node, ast.arg):
                    tokens.add(node.arg)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    tokens.add(node.name)
            for forbidden in forbidden_identifiers:
                if forbidden in tokens:
                    offenders.append(f"{path.name}:{forbidden}")
        assert offenders == [], offenders

    def test_32_document_sections_and_statements(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in (
            "## 1. Purpose",
            "## 2. Sampling",
            "## 3. Annotator Independence",
            "## 4. Input Isolation",
            "## 5. Versioning",
            "## 6. Revision",
            "## 7. Security",
            "## 8. Current State",
            "## 9. Deferred",
        ):
            assert section in doc, section
        for statement in (
            "Synthetic only",
            "No real annotation",
            "G1 blocked",
            "G3 blocked",
            "annotator_1 != annotator_2",
            "previous_annotation",
            "new annotation_version",
        ):
            assert statement in doc, statement

    def test_33_offline_and_deterministic(self) -> None:
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
        first, _, _ = run_scenario("normal_independent")
        second, _, _ = run_scenario("normal_independent")
        assert first == second
        assert fixture_records() == fixture_records()


__all__ = [
    "FAILURE_CODES",
    "EXECUTION_RESULT_FIELDS",
    "ALLOWED_INPUT_KEYS",
    "FORBIDDEN_INPUT_KEYS",
    "EXECUTION_SCENARIOS",
    "EVIDENCE_DATASET_VERSION",
    "EVIDENCE_SOURCE_TYPE",
    "AnnotationExecutionError",
    "AnnotationSamplingResult",
    "AnnotatorCaseInput",
    "RealAnnotationExecutionResult",
    "sample_cases",
    "build_case_input",
    "check_independence",
    "check_input_isolation",
    "check_cross_isolation",
    "execute_annotation_assignment",
    "fixture_records",
    "fixture_cases",
    "fixture_scenarios",
    "build_evidence",
    "run_scenario",
]
