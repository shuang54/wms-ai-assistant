"""Multi-turn Context Evaluation Rubric（Phase 4.1 Step 24 —— **Contract only**）。

本文件定义未来 G3 / G4 所需的 **Evaluation Rubric 与 Annotation Contract**
（test-local；**非 production DTO**）：

```text
Real Conversation
      ↓
Annotation Schema（ContextDependencyAnnotation）
      ↓
Context Dependency Label
      ↓
Full vs Selected Evaluation
      ↓
Business Impact Label（ContextImpactAnnotation）
```

核心原则（§三）：

    评价的不是"这段历史看起来重要不重要"，而是：
    **如果历史上下文被移除，是否会影响当前业务请求的正确完成。**

Gate 状态（契约维度 vs 证据维度）：

```text
G3 Contract = READY        （Annotation Contract 已定义，可接受未来真实数据）
G3 Evidence = BLOCKED      （真实 G3 evidence 尚未存在）
G4 = BLOCKED
Selection Strategy = BLOCKED（保持不变）
```

边界：DB = 0 · Network = 0 · LLM = 0 · 不修改 Step 21 synthetic dataset ·
不实现 ContextSelector / Tokenizer / judge / grader / scoring。
"""
from __future__ import annotations

import ast
import dataclasses
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from tests.test_conversation_selection_evidence_data_audit import (
    audit_current_readiness,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_evaluation_rubric.py"
_AUDIT_DOC = "docs/evaluation/Phase 4.1 Step 24 — Multi-turn Context Evaluation Rubric.md"
_SYNTHETIC_DATASET = (
    "tests/fixtures/conversation_context/wms_multiturn_conversations.yaml"
)

#: G3：Context Dependency 维度（**不是** 5 选 1 分类）。
ANNOTATION_TYPES: tuple[str, ...] = (
    "early_constraint",
    "middle_decision",
    "recent_context",
    "old_topic",
    "standalone",
)

#: G4：Business Impact 字段（值域 true / false / unknown）。
IMPACT_FIELDS: tuple[str, ...] = (
    "business_outcome_changed",
    "critical_constraint_lost",
    "entity_changed",
    "intent_changed",
)

IMPACT_VALUES: tuple[str, ...] = ("true", "false", "unknown")

#: Business Outcome Taxonomy（§十）。
OUTCOME_TYPES: tuple[str, ...] = (
    "ANSWER_CONTENT",
    "TOOL_SELECTION",
    "TOOL_ARGUMENTS",
    "SQL_SEMANTICS",
    "ROUTE",
    "REFUSAL",
)

TURN_ROLES: tuple[str, ...] = ("user", "assistant")

#: 标注一致性状态（§十七：不一致 → DISAGREEMENT，需 domain review；禁止自动多数表决）。
RESOLVED = "RESOLVED"
DISAGREEMENT = "DISAGREEMENT"
ANNOTATION_STATUSES: tuple[str, ...] = (RESOLVED, DISAGREEMENT)

#: 未来真实 Evaluation Dataset 最小字段（§十八；**不是** Conversation ORM）。
CASE_REQUIRED_FIELDS: tuple[str, ...] = (
    "case_id",
    "project_id",
    "turns",
    "annotation",
    "reference",
    "impact",
)

#: WMS 场景 → Primary Outcome（§十一；只做 taxonomy 映射，不评分）。
WMS_SCENARIO_OUTCOMES: dict[str, tuple[str, ...]] = {
    "库存查询": ("SQL_SEMANTICS", "ANSWER_CONTENT"),
    "入库查询": ("SQL_SEMANTICS",),
    "出库查询": ("SQL_SEMANTICS",),
    "指定仓库查询": ("SQL_SEMANTICS",),
    "指定物料查询": ("SQL_SEMANTICS",),
    "Tool 查询": ("TOOL_SELECTION", "TOOL_ARGUMENTS"),
    "业务知识问答": ("ANSWER_CONTENT",),
    "路由判断": ("ROUTE",),
}

#: 最小 secret pattern（只检查明确凭据形态）。
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"postgresql://\S+:\S+@"),
    re.compile(r"password\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"Authorization:\s*\S+", re.IGNORECASE),
    re.compile(r"api[_-]?key\s*[:=]\s*[A-Za-z0-9]{8,}", re.IGNORECASE),
    re.compile(r"DATABASE_URL\s*=\s*\S+"),
)

_REQUIRED_DOC_SECTIONS: tuple[str, ...] = (
    "## 1. Objective",
    "## 2. G3 Annotation Contract",
    "## 3. G4 Impact Contract",
    "## 4. Business Outcome Taxonomy",
    "## 5. WMS Scenario Mapping",
    "## 6. Reference Quality",
    "## 7. Annotation Quality",
    "## 8. Disagreement",
    "## 9. Dataset Schema",
    "## 10. Security",
    "## 11. Production Boundary",
    "## 12. Current Gate State",
    "## 13. Deferred",
)

_REQUIRED_DOC_STATEMENTS: tuple[str, ...] = (
    "G3 Contract = READY",
    "G3 Evidence = BLOCKED",
    "G4 = BLOCKED",
    "Selection Strategy = BLOCKED",
    "ContextDependencyAnnotation",
    "ContextImpactAnnotation",
    "如果历史上下文被移除",
    "不是五选一",
    "string equality",
    "domain review",
    "human validation",
)


class EvaluationSchemaError(ValueError):
    """Evaluation schema 校验失败（test-local；非 production 异常）。"""


# ============================================================
# Test-local contracts（**非 production DTO**）
# ============================================================


@dataclasses.dataclass(frozen=True)
class ContextDependencyAnnotation:
    """G3：当前 user turn 对历史的依赖维度（5 个 bool；允许同时为真）。"""

    case_id: str
    current_turn_id: str
    early_constraint: bool = False
    middle_decision: bool = False
    recent_context: bool = False
    old_topic: bool = False
    standalone: bool = False

    def dependency_dimensions(self) -> tuple[str, ...]:
        """返回为真的依赖维度（不含 standalone）。"""
        names = ("early_constraint", "middle_decision", "recent_context", "old_topic")
        return tuple(name for name in names if getattr(self, name))

    def has_dependency(self) -> bool:
        return bool(self.dependency_dimensions())


@dataclasses.dataclass(frozen=True)
class ContextImpactAnnotation:
    """G4：Full vs Selected 的 Business Impact 标注（true / false / unknown）。"""

    case_id: str
    full_history_result: str
    selected_history_result: str
    business_outcome_changed: str
    critical_constraint_lost: str
    entity_changed: str
    intent_changed: str


@dataclasses.dataclass(frozen=True)
class ReferenceRequirement:
    """Reference Quality（§十五）：reference 必须经人工创建或领域专家校验。"""

    outcome_type: str
    expected_behavior: str
    reference_created_by_human: bool = False
    reference_validated_by_domain_expert: bool = False


@dataclasses.dataclass(frozen=True)
class AnnotationQuality:
    """Annotation Quality（§十六）：未来真实标注至少记录三者。"""

    annotator: str
    annotation_version: str
    reviewed: bool


# ============================================================
# Validation（schema validation；未来真实数据必须通过）
# ============================================================


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvaluationSchemaError(message)


def validate_annotation(annotation: ContextDependencyAnnotation) -> None:
    _require(bool(annotation.case_id.strip()), "case_id required")
    _require(bool(annotation.current_turn_id.strip()), "current_turn_id required")
    for name in ANNOTATION_TYPES:
        value = getattr(annotation, name)
        _require(isinstance(value, bool), f"{name} must be bool")


def validate_impact(impact: ContextImpactAnnotation) -> None:
    _require(bool(impact.case_id.strip()), "case_id required")
    _require(
        bool(impact.full_history_result.strip()), "full_history_result required"
    )
    _require(
        bool(impact.selected_history_result.strip()),
        "selected_history_result required",
    )
    for name in IMPACT_FIELDS:
        value = getattr(impact, name)
        _require(
            isinstance(value, str) and value in IMPACT_VALUES,
            f"{name} must be one of {IMPACT_VALUES}",
        )


def validate_reference(reference: ReferenceRequirement) -> None:
    _require(reference.outcome_type in OUTCOME_TYPES, "unknown outcome_type")
    _require(bool(reference.expected_behavior.strip()), "expected_behavior required")
    _require(
        reference.reference_created_by_human
        or reference.reference_validated_by_domain_expert,
        "reference must be human-created or domain-expert-validated "
        "(LLM output is not ground truth)",
    )


def validate_turn(turn: Mapping[str, Any]) -> None:
    _require(set(turn) >= {"role", "content"}, "turn requires role / content")
    _require(turn["role"] in TURN_ROLES, f"invalid role: {turn['role']!r}")
    content = turn["content"]
    _require(
        isinstance(content, str) and bool(content.strip()),
        "turn content must be non-empty string",
    )


def validate_dataset_case(case: Mapping[str, Any]) -> None:
    """未来真实 Evaluation Dataset 的 schema 校验（§十八 / §二十 Validation）。"""
    for field in CASE_REQUIRED_FIELDS:
        _require(field in case, f"missing field: {field}")

    _require(bool(str(case["case_id"]).strip()), "case_id required")
    _require(bool(str(case["project_id"]).strip()), "project_id required")

    turns = case["turns"]
    _require(isinstance(turns, list) and len(turns) > 0, "turns non-empty")
    for turn in turns:
        validate_turn(turn)
    _require(turns[-1]["role"] == "user", "last turn must be user (current question)")

    annotation = case["annotation"]
    _require(set(annotation) == set(ANNOTATION_TYPES), "annotation types invalid")
    for name in ANNOTATION_TYPES:
        _require(isinstance(annotation[name], bool), f"annotation {name} must be bool")

    reference = case["reference"]
    _require(
        set(reference) >= {"outcome_type", "expected_behavior"},
        "reference fields invalid",
    )
    validate_reference(
        ReferenceRequirement(
            outcome_type=reference["outcome_type"],
            expected_behavior=reference["expected_behavior"],
            reference_created_by_human=reference.get(
                "reference_created_by_human", False
            ),
            reference_validated_by_domain_expert=reference.get(
                "reference_validated_by_domain_expert", False
            ),
        )
    )

    impact = case["impact"]
    _require(set(impact) == set(IMPACT_FIELDS), "impact fields invalid")
    for name in IMPACT_FIELDS:
        _require(impact[name] in IMPACT_VALUES, f"impact {name} invalid value")


def validate_no_secret(payload: Any) -> None:
    """annotation / reference 值中禁止出现 secret 形态（§二十 Security）。"""
    if isinstance(payload, str):
        for pattern in _SECRET_PATTERNS:
            _require(not pattern.search(payload), f"secret-like value: {pattern.pattern}")
        return
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            validate_no_secret(key)
            validate_no_secret(value)
        return
    if isinstance(payload, (list, tuple, set)):
        for item in payload:
            validate_no_secret(item)


def resolve_annotation_status(first: str, second: str) -> str:
    """两名标注者不一致 → DISAGREEMENT（§十七：禁止自动 majority vote）。"""
    if first == second:
        return RESOLVED
    return DISAGREEMENT


def requires_domain_review(status: str) -> bool:
    return status == DISAGREEMENT


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8-sig")


def _valid_case() -> dict[str, Any]:
    """一个通过全量 schema 校验的样例（离线；不含真实业务数据）。"""
    return {
        "case_id": "case_001",
        "project_id": "vietnam-wms",
        "turns": [
            {"role": "user", "content": "只看 A01 仓库"},
            {"role": "assistant", "content": "已限定 A01 仓库。"},
            {"role": "user", "content": "查询库存"},
        ],
        "annotation": {
            "early_constraint": True,
            "middle_decision": False,
            "recent_context": False,
            "old_topic": False,
            "standalone": False,
        },
        "reference": {
            "outcome_type": "SQL_SEMANTICS",
            "expected_behavior": "仅统计 A01 仓库库存",
            "reference_created_by_human": True,
        },
        "impact": {
            "business_outcome_changed": "true",
            "critical_constraint_lost": "true",
            "entity_changed": "unknown",
            "intent_changed": "false",
        },
    }


def _valid_impact() -> ContextImpactAnnotation:
    return ContextImpactAnnotation(
        case_id="case_001",
        full_history_result="A01 库存 320",
        selected_history_result="全部仓库库存 1000",
        business_outcome_changed="true",
        critical_constraint_lost="true",
        entity_changed="unknown",
        intent_changed="false",
    )


def _module_imports(relative: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(_source(relative))):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


# ============================================================
# 1. G3 Annotation Contract（§四 / §五 / §六）
# ============================================================


class TestG3AnnotationContract:
    def test_01_five_dependency_dimensions_exist(self) -> None:
        fields = {field.name for field in dataclasses.fields(ContextDependencyAnnotation)}
        assert fields == {"case_id", "current_turn_id", *ANNOTATION_TYPES}

    def test_02_all_dimensions_are_bool(self) -> None:
        for field in dataclasses.fields(ContextDependencyAnnotation):
            if field.name in ANNOTATION_TYPES:
                assert field.type == "bool", field.name

    def test_03_labels_are_not_one_hot(self) -> None:
        """§五：early_constraint + old_topic 可以同时存在。"""
        annotation = ContextDependencyAnnotation(
            case_id="c1",
            current_turn_id="t3",
            early_constraint=True,
            old_topic=True,
        )
        assert len(annotation.dependency_dimensions()) == 2
        assert annotation.has_dependency() is True

    def test_04_standalone_is_independent_dimension(self) -> None:
        standalone_only = ContextDependencyAnnotation(
            case_id="c1", current_turn_id="t3", standalone=True
        )
        assert standalone_only.has_dependency() is False

    def test_05_validation_requires_ids(self) -> None:
        ok = ContextDependencyAnnotation(case_id="c1", current_turn_id="t1")
        validate_annotation(ok)
        with pytest.raises(EvaluationSchemaError):
            validate_annotation(
                ContextDependencyAnnotation(case_id="  ", current_turn_id="t1")
            )
        with pytest.raises(EvaluationSchemaError):
            validate_annotation(
                ContextDependencyAnnotation(case_id="c1", current_turn_id="")
            )

    def test_06_validation_rejects_non_bool(self) -> None:
        bad = ContextDependencyAnnotation(case_id="c1", current_turn_id="t1")
        object.__setattr__(bad, "early_constraint", "yes")  # type: ignore[arg-type]
        with pytest.raises(EvaluationSchemaError):
            validate_annotation(bad)

    def test_07_annotation_definitions_documented(self) -> None:
        doc = _source(_AUDIT_DOC)
        for name in ANNOTATION_TYPES:
            assert name in doc, name
        assert "只看 A01 仓库" in doc
        assert "回到刚才那个采购单" in doc


# ============================================================
# 2. G4 Impact Contract（§七 / §十二 / §十三 / §十四）
# ============================================================


class TestG4ImpactContract:
    def test_08_impact_fields_and_value_domain(self) -> None:
        fields = {field.name for field in dataclasses.fields(ContextImpactAnnotation)}
        assert fields == {
            "case_id",
            "full_history_result",
            "selected_history_result",
            *IMPACT_FIELDS,
        }
        assert IMPACT_VALUES == ("true", "false", "unknown")

    def test_09_unknown_is_representable(self) -> None:
        impact = dataclasses.replace(_valid_impact(), entity_changed="unknown")
        validate_impact(impact)
        assert impact.entity_changed == "unknown"

    def test_10_critical_constraint_lost_scenario(self) -> None:
        """§十二：A01 约束丢失 → critical_constraint_lost = true。"""
        impact = _valid_impact()
        assert impact.critical_constraint_lost == "true"
        assert "A01" in impact.full_history_result
        assert "全部仓库" in impact.selected_history_result

    def test_11_entity_and_intent_change_are_separate_dimensions(self) -> None:
        impact = dataclasses.replace(
            _valid_impact(),
            business_outcome_changed="false",
            critical_constraint_lost="false",
            entity_changed="true",
            intent_changed="true",
        )
        validate_impact(impact)

    def test_12_validation_rejects_non_domain_values(self) -> None:
        with pytest.raises(EvaluationSchemaError):
            validate_impact(
                dataclasses.replace(_valid_impact(), business_outcome_changed="maybe")
            )
        with pytest.raises(EvaluationSchemaError):
            validate_impact(dataclasses.replace(_valid_impact(), entity_changed=True))  # type: ignore[arg-type]
        with pytest.raises(EvaluationSchemaError):
            validate_impact(dataclasses.replace(_valid_impact(), intent_changed=""))

    def test_13_validation_requires_both_results(self) -> None:
        with pytest.raises(EvaluationSchemaError):
            validate_impact(dataclasses.replace(_valid_impact(), full_history_result=""))
        with pytest.raises(EvaluationSchemaError):
            validate_impact(
                dataclasses.replace(_valid_impact(), selected_history_result="  ")
            )


# ============================================================
# 3. Business Outcome Taxonomy + WMS 映射（§十 / §十一）
# ============================================================


class TestBusinessOutcomeTaxonomy:
    def test_14_taxonomy_is_closed(self) -> None:
        assert OUTCOME_TYPES == (
            "ANSWER_CONTENT",
            "TOOL_SELECTION",
            "TOOL_ARGUMENTS",
            "SQL_SEMANTICS",
            "ROUTE",
            "REFUSAL",
        )

    def test_15_every_wms_scenario_maps_into_taxonomy(self) -> None:
        assert len(WMS_SCENARIO_OUTCOMES) == 8
        for scenario, outcomes in WMS_SCENARIO_OUTCOMES.items():
            assert outcomes, scenario
            for outcome in outcomes:
                assert outcome in OUTCOME_TYPES, f"{scenario}:{outcome}"

    def test_16_specific_scenarios_match_spec(self) -> None:
        assert WMS_SCENARIO_OUTCOMES["指定仓库查询"] == ("SQL_SEMANTICS",)
        assert WMS_SCENARIO_OUTCOMES["Tool 查询"] == (
            "TOOL_SELECTION",
            "TOOL_ARGUMENTS",
        )
        assert WMS_SCENARIO_OUTCOMES["业务知识问答"] == ("ANSWER_CONTENT",)
        assert WMS_SCENARIO_OUTCOMES["路由判断"] == ("ROUTE",)

    def test_17_no_scoring_in_rubric(self) -> None:
        """§十一：不要给场景打分（无 score / rank / weight 字段）。"""
        names = set(WMS_SCENARIO_OUTCOMES)
        for field in dataclasses.fields(ContextDependencyAnnotation):
            names.add(field.name)
        for field in dataclasses.fields(ContextImpactAnnotation):
            names.add(field.name)
        for name in names:
            assert "score" not in name.lower(), name
            assert "rank" not in name.lower(), name
            assert "weight" not in name.lower(), name


# ============================================================
# 4. Reference Quality（§八 / §九 / §十五）
# ============================================================


class TestReferenceQuality:
    def test_18_reference_requires_outcome_type_and_behavior(self) -> None:
        good = ReferenceRequirement(
            outcome_type="SQL_SEMANTICS",
            expected_behavior="仅统计 A01 仓库库存",
            reference_created_by_human=True,
        )
        validate_reference(good)
        with pytest.raises(EvaluationSchemaError):
            validate_reference(
                dataclasses.replace(good, outcome_type="NATURAL_LANGUAGE")
            )
        with pytest.raises(EvaluationSchemaError):
            validate_reference(dataclasses.replace(good, expected_behavior=""))

    def test_19_llm_output_is_not_ground_truth(self) -> None:
        llm_only = ReferenceRequirement(
            outcome_type="ANSWER_CONTENT",
            expected_behavior="LLM 生成答案",
            reference_created_by_human=False,
            reference_validated_by_domain_expert=False,
        )
        with pytest.raises(EvaluationSchemaError):
            validate_reference(llm_only)

    def test_20_human_or_domain_expert_paths_allowed(self) -> None:
        validate_reference(
            ReferenceRequirement(
                outcome_type="ANSWER_CONTENT",
                expected_behavior="人工参考答案",
                reference_created_by_human=True,
            )
        )
        validate_reference(
            ReferenceRequirement(
                outcome_type="ANSWER_CONTENT",
                expected_behavior="领域专家校验后的答案",
                reference_validated_by_domain_expert=True,
            )
        )

    def test_21_no_string_equality_semantics(self) -> None:
        """§九：candidate vs reference 禁止简单 string equality（文档冻结）。"""
        doc = _source(_AUDIT_DOC)
        assert "string equality" in doc
        assert "多个合法表达" in doc or "多种合法表达" in doc or "自然语言" in doc


# ============================================================
# 5. Annotation Quality + Disagreement（§十六 / §十七）
# ============================================================


class TestAnnotationQualityAndDisagreement:
    def test_22_quality_fields_exist(self) -> None:
        fields = {field.name for field in dataclasses.fields(AnnotationQuality)}
        assert fields == {"annotator", "annotation_version", "reviewed"}

    def test_23_agreement_resolves(self) -> None:
        assert resolve_annotation_status("true", "true") == RESOLVED
        assert requires_domain_review(RESOLVED) is False

    def test_24_disagreement_requires_domain_review(self) -> None:
        status = resolve_annotation_status("true", "unknown")
        assert status == DISAGREEMENT
        assert status in ANNOTATION_STATUSES
        assert requires_domain_review(status) is True

    def test_25_no_auto_majority_vote(self) -> None:
        """§十七：不一致时不得自动多数表决（只标记 DISAGREEMENT）。"""
        assert resolve_annotation_status("true", "false") == DISAGREEMENT
        doc = _source(_AUDIT_DOC)
        assert "DISAGREEMENT" in doc
        assert "domain review" in doc
        assert "majority" in doc

    def test_26_no_user_system_or_database_created(self) -> None:
        """§十六：本阶段不建立用户系统或数据库（无 production 模块）。"""
        for directory in ("backend/app/services", "backend/app/db", "backend/app/dto"):
            for path in (_REPO_ROOT / directory).rglob("*.py"):
                name = path.name
                for keyword in ("annotator", "annotation_quality", "rubric"):
                    assert keyword not in name, str(path)


# ============================================================
# 6. Dataset Schema（§十八 / §十九 / §二十 Validation）
# ============================================================


class TestDatasetSchema:
    def test_27_minimal_fields_frozen(self) -> None:
        assert CASE_REQUIRED_FIELDS == (
            "case_id",
            "project_id",
            "turns",
            "annotation",
            "reference",
            "impact",
        )

    def test_28_valid_case_passes(self) -> None:
        validate_dataset_case(_valid_case())

    def test_29_case_id_and_project_id_required(self) -> None:
        case = _valid_case()
        del case["case_id"]
        with pytest.raises(EvaluationSchemaError):
            validate_dataset_case(case)
        case = _valid_case()
        case["project_id"] = "  "
        with pytest.raises(EvaluationSchemaError):
            validate_dataset_case(case)

    def test_30_turns_validation(self) -> None:
        empty = _valid_case()
        empty["turns"] = []
        with pytest.raises(EvaluationSchemaError):
            validate_dataset_case(empty)

        last_assistant = _valid_case()
        last_assistant["turns"][-1] = {"role": "assistant", "content": "库存 320"}
        with pytest.raises(EvaluationSchemaError):
            validate_dataset_case(last_assistant)

        bad_role = _valid_case()
        bad_role["turns"][0] = {"role": "system", "content": "x"}
        with pytest.raises(EvaluationSchemaError):
            validate_dataset_case(bad_role)

        blank_content = _valid_case()
        blank_content["turns"][0] = {"role": "user", "content": "   "}
        with pytest.raises(EvaluationSchemaError):
            validate_dataset_case(blank_content)

    def test_31_annotation_and_impact_validation(self) -> None:
        bad_annotation = _valid_case()
        bad_annotation["annotation"].pop("old_topic")
        with pytest.raises(EvaluationSchemaError):
            validate_dataset_case(bad_annotation)

        bad_value = _valid_case()
        bad_value["annotation"]["standalone"] = "false"
        with pytest.raises(EvaluationSchemaError):
            validate_dataset_case(bad_value)

        bad_impact = _valid_case()
        bad_impact["impact"]["intent_changed"] = "maybe"
        with pytest.raises(EvaluationSchemaError):
            validate_dataset_case(bad_impact)

    def test_32_reference_validation_in_case(self) -> None:
        llm_only = _valid_case()
        llm_only["reference"].pop("reference_created_by_human", None)
        with pytest.raises(EvaluationSchemaError):
            validate_dataset_case(llm_only)

    def test_33_dataset_schema_is_not_conversation_orm(self) -> None:
        """§十八：evaluation dataset schema ≠ Conversation ORM（不混入持久化）。"""
        assert "conversation_id" not in CASE_REQUIRED_FIELDS
        assert "turn_id" not in CASE_REQUIRED_FIELDS
        assert "assistant_request_id" not in CASE_REQUIRED_FIELDS
        assert "created_at" not in CASE_REQUIRED_FIELDS
        assert "status" not in CASE_REQUIRED_FIELDS

    def test_34_synthetic_dataset_unchanged_and_still_synthetic_only(self) -> None:
        text = _source(_SYNTHETIC_DATASET)
        for field in ("annotation:", "reference:", "impact:", "outcome_type"):
            assert field not in text, field
        assert audit_current_readiness().source_type == "SYNTHETIC_ONLY"

    def test_35_schema_is_offline_and_importable(self) -> None:
        """schema validation 不依赖 DB / 网络 / LLM（纯函数式）。"""
        for module in _module_imports(_SELF):
            for forbidden in (
                "sqlalchemy",
                "psycopg",
                "httpx",
                "requests",
                "openai",
                "backend.app.db",
                "backend.app.services",
            ):
                assert not module.startswith(forbidden), module


# ============================================================
# 7. Security / Gate / Document（§二十 / §二十二 / §二十四）
# ============================================================


class TestSecurityGateAndDocument:
    def test_36_secret_values_rejected_in_annotation_payload(self) -> None:
        secret_like = "sk-" + "abcdef123456"
        with pytest.raises(EvaluationSchemaError):
            validate_no_secret({"expected_behavior": secret_like})
        with pytest.raises(EvaluationSchemaError):
            validate_no_secret({"note": "password = " + "hunter2secret"})
        with pytest.raises(EvaluationSchemaError):
            validate_no_secret(
                {"dsn": "postgresql://" + "user:pass@" + "localhost/db"}
            )
        # 合法文本通过
        validate_no_secret({"expected_behavior": "仅统计 A01 仓库库存"})

    def test_37_document_has_no_secret_values(self) -> None:
        doc = _source(_AUDIT_DOC)
        for pattern in _SECRET_PATTERNS:
            assert not pattern.search(doc), pattern.pattern

    def test_38_gate_state_contract_vs_evidence(self) -> None:
        """§二十二：G3 Contract = READY ≠ G3 Evidence 已存在；策略保持 BLOCKED。"""
        doc = _source(_AUDIT_DOC)
        assert "G3 Contract = READY" in doc
        assert "G3 Evidence = BLOCKED" in doc
        assert "G4 = BLOCKED" in doc
        assert "Selection Strategy = BLOCKED" in doc

        from tests.test_conversation_context_selection_decision_gate import (
            decide_strategy_state,
        )

        assert decide_strategy_state() == "BLOCKED"

    def test_39_no_llm_judge_or_scoring_implemented(self) -> None:
        source = _source(_SELF)
        tree = ast.parse(source)
        function_names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not node.name.startswith("test_")  # 测试方法名描述该约束，不参与检查
        }
        for forbidden in ("judge", "grade", "score", "rank"):
            for name in function_names:
                assert forbidden not in name.lower(), name

    def test_40_document_sections_and_statements(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in _REQUIRED_DOC_SECTIONS:
            assert section in doc, section
        for statement in _REQUIRED_DOC_STATEMENTS:
            assert statement in doc, statement

    def test_41_rubric_contracts_are_test_local(self) -> None:
        """§二十：DTO / schema 只存在于 tests/（production 目录无对应模块）。"""
        for name in (
            "context_dependency_annotation.py",
            "context_impact_annotation.py",
            "evaluation_rubric.py",
            "annotation_contract.py",
        ):
            for directory in (
                "backend/app/dto",
                "backend/app/services",
                "backend/app/db",
            ):
                assert not (_REPO_ROOT / directory / name).exists(), (
                    f"{directory}/{name}"
                )

    def test_42_contracts_are_deterministic(self) -> None:
        assert _valid_case() == _valid_case()
        validate_dataset_case(_valid_case())
        validate_dataset_case(_valid_case())
        assert resolve_annotation_status("false", "false") == RESOLVED
        assert resolve_annotation_status("false", "false") == RESOLVED


__all__ = [
    "ANNOTATION_TYPES",
    "IMPACT_FIELDS",
    "IMPACT_VALUES",
    "OUTCOME_TYPES",
    "WMS_SCENARIO_OUTCOMES",
    "ContextDependencyAnnotation",
    "ContextImpactAnnotation",
    "ReferenceRequirement",
    "AnnotationQuality",
    "EvaluationSchemaError",
    "validate_annotation",
    "validate_impact",
    "validate_reference",
    "validate_dataset_case",
    "validate_no_secret",
    "resolve_annotation_status",
    "requires_domain_review",
]
