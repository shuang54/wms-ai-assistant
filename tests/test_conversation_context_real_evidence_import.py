"""Real Evidence Import Contract（Phase 4.1 Step 31 —— **Contract only**）。

本文件冻结未来真实 Evidence 安全、可追溯进入 Evaluation Pipeline 的 Import 契约
（test-local；**非 production**）：

```text
Future Real Evidence
        ↓
Import Contract（本文件）
        ↓
Provenance / De-identification / Schema Validation
        ↓
G1 Candidate Input（未来；本阶段不执行 G1）
```

核心区分（§四 / §二十二）：

```text
Import  ≠  G1 Evaluation

Import PASS 只表示：Real Evidence Candidate structurally valid
Import PASS ≠ G1 READY；≠ G3 READY。
```

复用既有契约（不重新设计）：Step 26 artifact schema / production correlation IDs、
Step 27 EvidenceProvenance、Step 28 forbidden-field scanner。

边界：DB = 0 · Network = 0 · LLM = 0 · 不导入真实 WMS 数据 · 不执行真实 G1 ·
不自动修复 / 去重 / 脱敏 / 改 role（发现问题一律 BLOCKED）。
"""
from __future__ import annotations

import ast
import copy
import dataclasses
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tests.test_conversation_context_annotation_workflow import (
    AnnotationWorkflowError,
    validate_no_forbidden_annotation_fields,
)
from tests.test_conversation_context_evidence_collection_procedure import (
    run_g1_procedure,
)
from tests.test_conversation_context_evidence_provenance import EvidenceProvenance
from tests.test_conversation_context_real_evidence_boundary import (
    PRODUCTION_CORRELATION_IDS,
    SOURCE_TYPE_REAL,
    SOURCE_TYPE_SYNTHETIC,
)
from tests.test_conversation_selection_evidence_data_audit import (
    audit_current_readiness,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_real_evidence_import.py"
_AUDIT_DOC = "docs/evaluation/Phase 4.1 Step 31 — Real Evidence Import Contract.md"
_FIXTURE = "tests/fixtures/conversation_context/real_evidence_import_cases.yaml"

# ============================================================
# 常量（test-local）
# ============================================================

#: Import failure codes（少量结构化；不建立几十个错误类型）。
FAILURE_CODES: tuple[str, ...] = (
    "INVALID_SOURCE_TYPE",
    "MISSING_DATASET_VERSION",
    "DATASET_VERSION_MISMATCH",
    "MISSING_DEIDENTIFICATION_ATTESTATION",
    "DUPLICATE_CASE_ID",
    "INVALID_ROLE",
    "INVALID_TURN",
    "INVALID_CONVERSATION",
    "FORBIDDEN_FIELD",
    "PRODUCTION_CORRELATION_ID",
)

IMPORT_RESULT_FIELDS: tuple[str, ...] = (
    "accepted",
    "dataset_version",
    "source_type",
    "cases_imported",
    "blocked_cases",
    "failure_codes",
    "provenance",
)

REQUIRED_ARTIFACT_FIELDS: tuple[str, ...] = (
    "dataset_version",
    "source_type",
    "de_identification_attestation",
    "conversations",
)

#: §七：允许字段（除此之外一律拒绝 —— 不 silently ignore unknown fields）。
ALLOWED_ARTIFACT_FIELDS: tuple[str, ...] = REQUIRED_ARTIFACT_FIELDS
ALLOWED_CONVERSATION_FIELDS: tuple[str, ...] = ("case_id", "project_id", "turns")
ALLOWED_TURN_FIELDS: tuple[str, ...] = ("role", "content")

#: §十三：Role Contract（规范枚举 USER / ASSISTANT；Step 26 承载形小写等价）。
ALLOWED_ROLES: tuple[str, ...] = ("USER", "ASSISTANT")
REJECTED_ROLES: tuple[str, ...] = ("SYSTEM", "TOOL", "FUNCTION", "DEVELOPER")
_ROLE_EQUIVALENTS: dict[str, str] = {
    "user": "USER",
    "assistant": "ASSISTANT",
}

#: 复用 Step 28 scanner 之外，Import 层附加禁止字段（§二十：prompt / raw response 等）。
IMPORT_EXTRA_FORBIDDEN_FIELDS: tuple[str, ...] = (
    "prompt",
    "messages",
    "tool_payload",
    "tool_raw_payload",
    "sql_result",
    "raw_response",
    "raw_llm_response",
    "orm_object",
)

#: §七：明确禁止的派生 / 生产字段（用于快速抽查）。
FORBIDDEN_DERIVED_FIELDS: tuple[str, ...] = (
    "annotation",
    "reference",
    "impact",
    "strategy",
    "strategy_id",
    "selection",
    "selection_result",
    "score",
    "ranking",
    "winner",
    "token_estimate",
    "tokens",
)

IMPORT_SCENARIOS: tuple[str, ...] = (
    "valid_real",
    "synthetic_only",
    "missing_dataset_version",
    "missing_attestation",
    "duplicate_case_id",
    "system_role",
    "production_correlation_id",
    "forbidden_payload",
    "mixed_dataset_version",
    "user_only",
    "assistant_only",
    "unknown_field",
)

_ATTESTATION_METHOD = "external_process"
_DEFAULT_DATASET_VERSION = "wms-v1"
_DEFAULT_CASE_ID = "import_case_001"
_DEFAULT_ROLES = "USER,ASSISTANT,USER"
_PROJECT_ID = "vietnam-wms"


# ============================================================
# Import 契约（test-local）
# ============================================================


@dataclasses.dataclass(frozen=True)
class DeIdentificationAttestation:
    """脱敏声明（外部流程产物；本阶段不实现真实脱敏系统）。"""

    attested: bool
    method: str = _ATTESTATION_METHOD

    def validate(self) -> None:
        if self.attested is not True:
            raise ImportContractError("attestation must be true")
        if not str(self.method).strip():
            raise ImportContractError("attestation method must be non-empty")


@dataclasses.dataclass(frozen=True)
class RealEvidenceImportResult:
    """Import 结果（**不携带任何 conversation content**）。"""

    accepted: bool
    dataset_version: str
    source_type: str
    cases_imported: int
    blocked_cases: tuple[str, ...]
    failure_codes: tuple[str, ...]
    provenance: EvidenceProvenance | None


class ImportContractError(ValueError):
    """Import 契约违规（test-local；非 production 异常）。"""


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


def _check_import_extra_forbidden(payload: Any) -> None:
    for key in _iter_keys(payload):
        if key in IMPORT_EXTRA_FORBIDDEN_FIELDS:
            raise ImportContractError(f"import-forbidden field: {key}")
        if key in FORBIDDEN_DERIVED_FIELDS:
            raise ImportContractError(f"derived field forbidden: {key}")


def import_real_evidence(
    artifact: Mapping[str, Any],
    *,
    expected_dataset_version: str | None = None,
) -> RealEvidenceImportResult:
    """Real Evidence Import（**read-only**；发现问题一律 BLOCKED，不自动修复）。"""
    blocked: list[str] = []
    failures: list[str] = []

    def fail(code: str, case_id: str | None = None) -> None:
        assert code in FAILURE_CODES, code
        if case_id and case_id not in blocked:
            blocked.append(case_id)
        if code not in failures:
            failures.append(code)

    if not isinstance(artifact, Mapping):
        fail("INVALID_CONVERSATION")
        return _result(artifact, accepted=False, blocked=blocked, failures=failures)

    # ---- 顶层字段（§七：白名单；不 silently ignore）----
    for key in artifact:
        name = str(key)
        if name in PRODUCTION_CORRELATION_IDS:
            fail("PRODUCTION_CORRELATION_ID")
        elif name not in ALLOWED_ARTIFACT_FIELDS:
            fail("FORBIDDEN_FIELD")

    # ---- source_type（§五：Real Import 只接受 REAL_DEIDENTIFIED）----
    source_type = str(artifact.get("source_type", ""))
    if source_type != SOURCE_TYPE_REAL:
        fail("INVALID_SOURCE_TYPE")

    # ---- dataset_version（§六：non-empty / stable / explicit）----
    version = artifact.get("dataset_version")
    valid_version = (
        isinstance(version, str) and bool(version.strip()) and version == version.strip()
    )
    if not valid_version:
        fail("MISSING_DATASET_VERSION")
    elif expected_dataset_version is not None and version != expected_dataset_version:
        fail("DATASET_VERSION_MISMATCH")

    # ---- de-identification attestation（§八）----
    attestation = artifact.get("de_identification_attestation")
    if not isinstance(attestation, Mapping):
        fail("MISSING_DEIDENTIFICATION_ATTESTATION")
    else:
        try:
            DeIdentificationAttestation(
                attested=attestation.get("attested") is True,
                method=str(attestation.get("method", "")),
            ).validate()
        except ImportContractError:
            fail("MISSING_DEIDENTIFICATION_ATTESTATION")

    # ---- security（复用 Step 28 scanner + import 附加字段；不实现第二套）----
    try:
        validate_no_forbidden_annotation_fields(artifact)
        _check_import_extra_forbidden(artifact)
    except (AnnotationWorkflowError, ImportContractError):
        fail("FORBIDDEN_FIELD")

    # ---- conversations（§十四 ~ §十六）----
    conversations = artifact.get("conversations")
    if not isinstance(conversations, list) or not conversations:
        fail("INVALID_CONVERSATION")
        return _result(artifact, accepted=False, blocked=blocked, failures=failures)

    seen_case_ids: set[str] = set()
    for conversation in conversations:
        if not isinstance(conversation, Mapping):
            fail("INVALID_CONVERSATION")
            continue

        case_id = str(conversation.get("case_id", "")).strip()
        if not case_id:
            fail("INVALID_CONVERSATION")
        elif case_id in seen_case_ids:
            fail("DUPLICATE_CASE_ID", case_id)  # 不自动去重 / 不 first-wins
        else:
            seen_case_ids.add(case_id)

        for key in conversation:
            name = str(key)
            if name in PRODUCTION_CORRELATION_IDS:
                fail("PRODUCTION_CORRELATION_ID", case_id)
            elif name not in ALLOWED_CONVERSATION_FIELDS:
                fail("FORBIDDEN_FIELD", case_id)

        if not str(conversation.get("project_id", "")).strip():
            fail("INVALID_CONVERSATION", case_id)

        turns = conversation.get("turns")
        if not isinstance(turns, list) or not turns:
            fail("INVALID_CONVERSATION", case_id)
            continue

        has_user = False
        for turn in turns:
            if not isinstance(turn, Mapping):
                fail("INVALID_TURN", case_id)
                continue
            for key in turn:
                name = str(key)
                if name in PRODUCTION_CORRELATION_IDS:
                    fail("PRODUCTION_CORRELATION_ID", case_id)
                elif name not in ALLOWED_TURN_FIELDS:
                    fail("FORBIDDEN_FIELD", case_id)
            role = str(turn.get("role", ""))
            normalized = _ROLE_EQUIVALENTS.get(role.lower())
            if normalized is None:
                fail("INVALID_ROLE", case_id)
            elif normalized == "USER":
                has_user = True
            content = turn.get("content")
            if not isinstance(content, str) or not content.strip():
                fail("INVALID_TURN", case_id)

        if not has_user:
            fail("INVALID_CONVERSATION", case_id)  # assistant-only 不允许

    return _result(
        artifact,
        accepted=not failures,
        blocked=blocked,
        failures=failures,
        case_count=len(seen_case_ids),
    )


def _result(
    artifact: Mapping[str, Any],
    *,
    accepted: bool,
    blocked: list[str],
    failures: list[str],
    case_count: int = 0,
) -> RealEvidenceImportResult:
    version = artifact.get("dataset_version") if isinstance(artifact, Mapping) else None
    source_type = (
        artifact.get("source_type") if isinstance(artifact, Mapping) else None
    )
    provenance = (
        EvidenceProvenance(
            dataset_version=str(version), source_type=str(source_type)
        )
        if accepted
        else None
    )
    return RealEvidenceImportResult(
        accepted=accepted,
        dataset_version=str(version) if isinstance(version, str) else "",
        source_type=str(source_type) if isinstance(source_type, str) else "",
        cases_imported=case_count if accepted else 0,
        blocked_cases=tuple(blocked),
        failure_codes=tuple(failures),
        provenance=provenance,
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


def records_for(scenario: str) -> list[dict[str, str]]:
    return [record for record in fixture_records() if record["scenario"] == scenario]


def _conversation_from(record: Mapping[str, str]) -> dict[str, Any]:
    roles = record.get("turns", _DEFAULT_ROLES).split(",")
    turns: list[dict[str, str]] = []
    for role in roles:
        turn: dict[str, str] = {"role": role.strip(), "content": "示例内容（契约模拟）"}
        turn_extra = record.get("turn_extra")
        if turn_extra:
            turn[str(turn_extra)] = "turn-x"
        turns.append(turn)
    conversation: dict[str, Any] = {
        "case_id": record.get("case_id", _DEFAULT_CASE_ID),
        "project_id": _PROJECT_ID,
        "turns": turns,
    }
    conversation_extra = record.get("conversation_extra")
    if conversation_extra:
        conversation[str(conversation_extra)] = "corr-x"
    payload_field = record.get("payload_field")
    if payload_field:
        secret_like = "sk-" + "abcdef123456"
        payload_values = {
            "sql": "SELECT * FROM inventory",
            "prompt": "raw prompt text with hidden information",
            "api_key": secret_like,
        }
        conversation[str(payload_field)] = payload_values[str(payload_field)]
    return conversation


def artifact_from_records(records: list[Mapping[str, str]]) -> dict[str, Any]:
    base = records[0]
    artifact: dict[str, Any] = {}
    if base.get("dataset_version_marker") != "missing":
        artifact["dataset_version"] = base.get("dataset_version", _DEFAULT_DATASET_VERSION)
    artifact["source_type"] = base.get("source_type", SOURCE_TYPE_REAL)
    attestation = base.get("attestation", "true")
    if attestation != "missing":
        artifact["de_identification_attestation"] = {
            "attested": attestation == "true",
            "method": _ATTESTATION_METHOD,
        }
    artifact["conversations"] = [
        _conversation_from(record) for record in records
    ]
    return artifact


def import_scenario(scenario: str) -> tuple[RealEvidenceImportResult, dict[str, Any]]:
    records = records_for(scenario)
    assert records, f"fixture scenario missing: {scenario}"
    artifact = artifact_from_records(records)
    return import_real_evidence(artifact), artifact


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
# 1. Valid import / Result 契约（§四 / §十八）
# ============================================================


class TestValidImport:
    def test_01_valid_import_accepted(self) -> None:
        result, _ = import_scenario("valid_real")
        assert result.accepted is True
        assert result.failure_codes == ()
        assert result.cases_imported == 1
        assert result.blocked_cases == ()

    def test_02_provenance_follows_step27_contract(self) -> None:
        result, _ = import_scenario("valid_real")
        assert isinstance(result.provenance, EvidenceProvenance)
        assert result.provenance.dataset_version == result.dataset_version
        assert result.provenance.source_type == SOURCE_TYPE_REAL
        assert set(result.provenance.as_dict()) == {"dataset_version", "source_type"}

    def test_03_result_contract_fields(self) -> None:
        fields = {
            field.name
            for field in dataclasses.fields(RealEvidenceImportResult)
        }
        assert fields == set(IMPORT_RESULT_FIELDS)
        for forbidden in ("content", "turns", "conversations", "messages"):
            assert forbidden not in fields, forbidden

    def test_04_result_holds_no_conversation_content(self) -> None:
        result, _ = import_scenario("valid_real")
        rendered = repr(result)
        assert "示例内容" not in rendered
        assert "只看 A01" not in rendered

    def test_05_fixture_covers_declared_scenarios(self) -> None:
        scenarios = [record["scenario"] for record in fixture_records()]
        assert sorted(set(scenarios)) == sorted(IMPORT_SCENARIOS)
        assert len(scenarios) == 16  # 12 场景 + duplicate/mixed/forbidden 多记录


# ============================================================
# 2. Source Type / Dataset Version / Attestation（§五 / §六 / §八）
# ============================================================


class TestSourceVersionAttestation:
    def test_06_synthetic_only_rejected(self) -> None:
        result, _ = import_scenario("synthetic_only")
        assert result.accepted is False
        assert "INVALID_SOURCE_TYPE" in result.failure_codes
        assert result.provenance is None
        # synthetic fixture 仍可继续用于测试（不删除）
        assert SOURCE_TYPE_SYNTHETIC == "SYNTHETIC_ONLY"

    def test_07_missing_dataset_version_rejected(self) -> None:
        result, _ = import_scenario("missing_dataset_version")
        assert result.accepted is False
        assert "MISSING_DATASET_VERSION" in result.failure_codes

    def test_08_unstable_dataset_version_rejected(self) -> None:
        artifact = artifact_from_records(records_for("valid_real"))
        artifact["dataset_version"] = "  wms-v1  "
        result = import_real_evidence(artifact)
        assert result.accepted is False
        assert "MISSING_DATASET_VERSION" in result.failure_codes

    def test_09_missing_attestation_rejected(self) -> None:
        result, _ = import_scenario("missing_attestation")
        assert result.accepted is False
        assert "MISSING_DEIDENTIFICATION_ATTESTATION" in result.failure_codes

    def test_10_attestation_false_is_rejected(self) -> None:
        artifact = artifact_from_records(records_for("valid_real"))
        artifact["de_identification_attestation"] = {
            "attested": False,
            "method": _ATTESTATION_METHOD,
        }
        result = import_real_evidence(artifact)
        assert result.accepted is False
        assert "MISSING_DEIDENTIFICATION_ATTESTATION" in result.failure_codes

    def test_11_mixed_dataset_versions_rejected(self) -> None:
        records = records_for("mixed_dataset_version")
        first = import_real_evidence(artifact_from_records([records[0]]))
        assert first.accepted is True
        second = import_real_evidence(
            artifact_from_records([records[1]]),
            expected_dataset_version=first.dataset_version,
        )
        assert second.accepted is False
        assert "DATASET_VERSION_MISMATCH" in second.failure_codes


# ============================================================
# 3. Case / Turn / Conversation Integrity（§十四 ~ §十六）
# ============================================================


class TestCaseTurnIntegrity:
    def test_12_duplicate_case_id_rejected(self) -> None:
        result, _ = import_scenario("duplicate_case_id")
        assert result.accepted is False
        assert "DUPLICATE_CASE_ID" in result.failure_codes
        assert "import_dup_001" in result.blocked_cases

    def test_13_system_role_rejected(self) -> None:
        result, _ = import_scenario("system_role")
        assert result.accepted is False
        assert "INVALID_ROLE" in result.failure_codes
        assert ALLOWED_ROLES == ("USER", "ASSISTANT")
        assert set(REJECTED_ROLES) == {"SYSTEM", "TOOL", "FUNCTION", "DEVELOPER"}

    def test_14_lowercase_roles_are_equivalent_carrier_forms(self) -> None:
        """§十三：规范枚举 USER / ASSISTANT（Step 26 承载形小写等价）。"""
        artifact = artifact_from_records(records_for("valid_real"))
        artifact["conversations"][0]["turns"] = [
            {"role": "user", "content": "只看 A01 仓库"},
            {"role": "assistant", "content": "已限定（示例）。"},
            {"role": "user", "content": "查询库存"},
        ]
        assert import_real_evidence(artifact).accepted is True

    def test_15_user_only_conversation_passes(self) -> None:
        result, _ = import_scenario("user_only")
        assert result.accepted is True

    def test_16_assistant_only_conversation_rejected(self) -> None:
        result, _ = import_scenario("assistant_only")
        assert result.accepted is False
        assert "INVALID_CONVERSATION" in result.failure_codes

    def test_17_empty_turns_rejected(self) -> None:
        artifact = artifact_from_records(records_for("valid_real"))
        artifact["conversations"][0]["turns"] = []
        result = import_real_evidence(artifact)
        assert result.accepted is False
        assert "INVALID_CONVERSATION" in result.failure_codes

    def test_18_empty_content_rejected(self) -> None:
        artifact = artifact_from_records(records_for("valid_real"))
        artifact["conversations"][0]["turns"][0]["content"] = "   "
        result = import_real_evidence(artifact)
        assert result.accepted is False
        assert "INVALID_TURN" in result.failure_codes

    def test_19_extra_turn_field_rejected(self) -> None:
        artifact = artifact_from_records(records_for("valid_real"))
        artifact["conversations"][0]["turns"][0]["extra_note"] = "x"
        result = import_real_evidence(artifact)
        assert result.accepted is False
        assert "FORBIDDEN_FIELD" in result.failure_codes


# ============================================================
# 4. Production Correlation / Unknown Field（§七 / §十）
# ============================================================


class TestCorrelationAndUnknownFields:
    def test_20_production_correlation_id_rejected(self) -> None:
        result, _ = import_scenario("production_correlation_id")
        assert result.accepted is False
        assert "PRODUCTION_CORRELATION_ID" in result.failure_codes
        assert set(PRODUCTION_CORRELATION_IDS) == {
            "conversation_id",
            "assistant_request_id",
            "turn_id",
            "provider_request_id",
        }

    def test_21_turn_level_correlation_id_rejected(self) -> None:
        artifact = artifact_from_records(records_for("valid_real"))
        artifact["conversations"][0]["turns"][0]["turn_id"] = "turn-1"
        result = import_real_evidence(artifact)
        assert result.accepted is False
        assert "PRODUCTION_CORRELATION_ID" in result.failure_codes

    def test_22_unknown_conversation_field_rejected(self) -> None:
        result, _ = import_scenario("unknown_field")
        assert result.accepted is False
        assert "FORBIDDEN_FIELD" in result.failure_codes

    def test_23_derived_fields_rejected(self) -> None:
        """§七：annotation / strategy / score / token_estimate 等派生字段禁止。"""
        for field in FORBIDDEN_DERIVED_FIELDS:
            artifact = artifact_from_records(records_for("valid_real"))
            artifact["conversations"][0][field] = "x"
            result = import_real_evidence(artifact)
            assert result.accepted is False, field
            assert "FORBIDDEN_FIELD" in result.failure_codes, field

    def test_24_top_level_extra_field_rejected(self) -> None:
        artifact = artifact_from_records(records_for("valid_real"))
        artifact["ingested_by"] = "x"
        result = import_real_evidence(artifact)
        assert result.accepted is False
        assert "FORBIDDEN_FIELD" in result.failure_codes


# ============================================================
# 5. Security（§二十）
# ============================================================


class TestSecurity:
    def test_25_forbidden_payloads_blocked(self) -> None:
        results = [
            import_real_evidence(artifact_from_records([record]))
            for record in records_for("forbidden_payload")
        ]
        assert len(results) == 3
        for result in results:
            assert result.accepted is False
            assert "FORBIDDEN_FIELD" in result.failure_codes

    def test_26_reuses_step28_scanner(self) -> None:
        """§二十：复用 Step 28 scanner（不实现第二套 forbidden-field scanner）。"""
        modules = _module_imports(_SELF)
        assert (
            "tests.test_conversation_context_annotation_workflow" in modules
        ), sorted(modules)
        # 复用调用存在
        source = _source(_SELF)
        assert "validate_no_forbidden_annotation_fields(" in source
        function_names = _non_test_function_names(_SELF)
        for forbidden in ("scanner", "scan_secrets", "redact", "sanitize"):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{forbidden}"

    def test_27_no_production_id_generation(self) -> None:
        """§十六：Import 不生成新的生产 ID。"""
        function_names = _non_test_function_names(_SELF)
        for forbidden in ("generate_id", "new_request_id", "uuid", "mint_id"):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{forbidden}"
        modules = _module_imports(_SELF)
        for module in modules:
            for forbidden in ("uuid", "secrets", "random"):
                assert not module.startswith(forbidden), module


# ============================================================
# 6. Immutability / Determinism（§十九）
# ============================================================


class TestImmutabilityAndDeterminism:
    def test_28_import_does_not_modify_input(self) -> None:
        artifact = artifact_from_records(records_for("valid_real"))
        snapshot = copy.deepcopy(artifact)
        import_real_evidence(artifact)
        assert artifact == snapshot

    def test_29_import_does_not_modify_invalid_input(self) -> None:
        for scenario in IMPORT_SCENARIOS:
            records = records_for(scenario)
            artifact = artifact_from_records(records)
            snapshot = copy.deepcopy(artifact)
            import_real_evidence(artifact)
            assert artifact == snapshot, scenario

    def test_30_deterministic(self) -> None:
        first, _ = import_scenario("valid_real")
        second, _ = import_scenario("valid_real")
        assert first == second
        assert fixture_records() == fixture_records()
        bad_first, _ = import_scenario("duplicate_case_id")
        bad_second, _ = import_scenario("duplicate_case_id")
        assert bad_first == bad_second


# ============================================================
# 7. G1 / G3 Relationship + 文档（§二十二 / §二十三 / §二十六）
# ============================================================


class TestGateRelationshipAndDocument:
    def test_31_import_pass_does_not_change_g1(self) -> None:
        """§二十二：Import PASS ≠ G1 READY（G1 仍 BLOCKED）。"""
        result, _ = import_scenario("valid_real")
        assert result.accepted is True

        g1_status = run_g1_procedure(audit_current_readiness())
        assert g1_status == "BLOCKED"
        # Import 不产生 G1 评估产物；当前真实分类仍为 SYNTHETIC_ONLY
        assert audit_current_readiness().source_type == "SYNTHETIC_ONLY"

    def test_32_g2_g3_g4_unchanged(self) -> None:
        """§二十三：即使 import PASS，G2/G3/G4 全部保持。"""
        from tests.test_conversation_context_evidence_collection_procedure import (
            run_g2_procedure,
            run_g3_procedure,
            run_g4_procedure,
        )

        g1_status = run_g1_procedure(audit_current_readiness())
        assert run_g2_procedure((), g1_status=g1_status)["status"] == "INSUFFICIENT"
        assert run_g3_procedure((), g1_status=g1_status)["status"] == "BLOCKED"
        assert run_g4_procedure((), g1_status=g1_status)["status"] == "BLOCKED"
        doc = _source(_AUDIT_DOC)
        for statement in (
            "G1 = BLOCKED",
            "G2 = INSUFFICIENT",
            "G3 Evidence = BLOCKED",
            "G4 = BLOCKED",
            "Selection Strategy = BLOCKED",
        ):
            assert statement in doc, statement

    def test_33_document_sections_and_statements(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in (
            "## 1. Purpose",
            "## 2. Required Fields",
            "## 3. Forbidden Fields",
            "## 4. Provenance",
            "## 5. Import vs G1",
            "## 6. Synthetic Limitation",
            "## 7. Deferred",
        ):
            assert section in doc, section
        for statement in (
            "Import PASS",
            "G1 READY",
            "Synthetic only",
            "No real WMS import",
            "INVALID_SOURCE_TYPE",
            "MISSING_DEIDENTIFICATION_ATTESTATION",
        ):
            assert statement in doc, statement

    def test_34_offline(self) -> None:
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
    "IMPORT_RESULT_FIELDS",
    "IMPORT_SCENARIOS",
    "REQUIRED_ARTIFACT_FIELDS",
    "ALLOWED_ARTIFACT_FIELDS",
    "ALLOWED_CONVERSATION_FIELDS",
    "ALLOWED_TURN_FIELDS",
    "ALLOWED_ROLES",
    "IMPORT_EXTRA_FORBIDDEN_FIELDS",
    "DeIdentificationAttestation",
    "RealEvidenceImportResult",
    "ImportContractError",
    "import_real_evidence",
    "fixture_records",
    "records_for",
    "artifact_from_records",
    "import_scenario",
]
