"""Real WMS Evidence Input Boundary（Phase 4.1 Step 26 —— **Contract only**）。

本文件冻结未来 Real De-identified WMS Evidence 进入 Evaluation Layer 的
**最小结构**（test-local；**非 production**）：

```text
External / Offline De-identified Artifact
        ↓
Evidence Dataset Loader（contract stub；不做 IO）
        ↓
EvidenceDatasetValidator（复用 Step 25）
        ↓
G1
        ↓
G2 / G3 / G4（全部依赖 G1 READY）
```

只定义：Artifact Schema / Loader Contract / Validation Contract /
Privacy Boundary / Provenance Boundary。

重要声明：

```text
This is contract simulation only.
It is NOT real WMS evidence.
（本文件中的 stand-in 数据为极小 synthetic 构造，仅用于契约测试；
  不代表任何真实 WMS 业务数据。）
```

边界：DB = 0 · Network = 0 · LLM = 0 · 不导入真实数据 ·
不实现脱敏工具 / ContextSelector / 任何 Selection Strategy。
"""
from __future__ import annotations

import ast
import dataclasses
import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from tests.test_conversation_context_evidence_collection_procedure import (
    EvidenceDatasetValidator,
    run_g1_procedure,
    run_g2_procedure,
    run_g3_procedure,
    run_g4_procedure,
)
from tests.test_conversation_selection_evidence_data_audit import (
    EvidenceDataReadiness,
    audit_current_readiness,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_real_evidence_boundary.py"
_AUDIT_DOC = "docs/evaluation/Phase 4.1 Step 26 — Real WMS Evidence Input Boundary.md"

# ============================================================
# 常量（test-local）
# ============================================================

SOURCE_TYPE_REAL = "REAL_DEIDENTIFIED"
SOURCE_TYPE_SYNTHETIC = "SYNTHETIC_ONLY"
ALLOWED_SOURCE_TYPES: tuple[str, ...] = (SOURCE_TYPE_REAL, SOURCE_TYPE_SYNTHETIC)

ARTIFACT_REQUIRED_FIELDS: tuple[str, ...] = (
    "dataset_version",
    "source_type",
    "conversations",
)
CONVERSATION_REQUIRED_FIELDS: tuple[str, ...] = ("case_id", "project_id", "turns")

#: 生产运行时 correlation identifiers —— 不得出现在 evaluation artifact。
PRODUCTION_CORRELATION_IDS: tuple[str, ...] = (
    "conversation_id",
    "assistant_request_id",
    "turn_id",
    "provider_request_id",
)

#: Evaluation derived fields —— raw artifact 不得预先携带结论。
EVALUATION_DERIVED_FIELDS: tuple[str, ...] = (
    "annotation",
    "reference",
    "impact",
    "strategy_id",
    "selected_turns",
    "selected_history",
    "candidate_history",
    "selection_result",
    "score",
    "ranking",
    "winner",
    "tokens",
)

#: 运行环境信息 —— 不属于 Evidence Contract（§八 / §十九）。
ENVIRONMENT_FIELDS: tuple[str, ...] = (
    "git_sha",
    "machine_id",
    "hostname",
    "absolute_path",
    "file_path",
    "credential",
    "credentials",
    # provenance 禁止字段（§八：不增加 customer / factory / employee / database / server / host）
    "customer",
    "factory",
    "employee",
    "database",
    "server",
    "host",
)

#: Artifact 中禁止出现的字段（合并 §五 / §十六 / §十七 / §十九）。
FORBIDDEN_ARTIFACT_FIELDS: tuple[str, ...] = (
    *PRODUCTION_CORRELATION_IDS,
    *EVALUATION_DERIVED_FIELDS,
    *ENVIRONMENT_FIELDS,
)

_SECRET_MARKERS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "Authorization",
    "DATABASE_URL",
    "postgresql://",
    "postgres://",
    "psycopg2.connect",
    "password",
)

_ABSOLUTE_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[A-Za-z]:\\"),  # Windows drive（C:\ / D:\ ...）
    re.compile(r"\\\\[A-Za-z0-9]"),  # UNC（\\server\...）
    re.compile(r"/(home|Users|etc|var|mnt|opt)/"),  # POSIX 绝对路径
)


class EvidenceArtifactError(ValueError):
    """Artifact 校验失败（test-local；非 production 异常）。"""


# ============================================================
# Artifact validation（§四 / §五 / §十 / §十六 ~ §十九）
# ============================================================


def _iter_keys(payload: Any) -> Iterator[str]:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            yield str(key)
            yield from _iter_keys(value)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            yield from _iter_keys(item)


def _iter_strings(payload: Any) -> Iterator[str]:
    if isinstance(payload, str):
        yield payload
    elif isinstance(payload, Mapping):
        for value in payload.values():
            yield from _iter_strings(value)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            yield from _iter_strings(item)


def contains_absolute_path(text: str) -> bool:
    return any(pattern.search(text) for pattern in _ABSOLUTE_PATH_PATTERNS)


def contains_secret_marker(text: str) -> bool:
    return any(marker in text for marker in _SECRET_MARKERS)


def collect_forbidden_fields(payload: Any) -> list[str]:
    """返回 artifact 中出现的禁止字段（不抛异常；便于测试聚合）。"""
    found: list[str] = []
    for key in _iter_keys(payload):
        if key in FORBIDDEN_ARTIFACT_FIELDS and key not in found:
            found.append(key)
    return found


def validate_artifact(artifact: Mapping[str, Any]) -> None:
    """Real Evidence Artifact schema 校验（§四 / §十）。"""
    if not isinstance(artifact, Mapping):
        raise EvidenceArtifactError("artifact must be a mapping")

    # ---- Dataset 级 ----
    for field in ARTIFACT_REQUIRED_FIELDS:
        if field not in artifact:
            raise EvidenceArtifactError(f"missing field: {field}")
    dataset_version = artifact["dataset_version"]
    if not isinstance(dataset_version, str) or not dataset_version.strip():
        raise EvidenceArtifactError("dataset_version must be non-empty string")
    source_type = artifact["source_type"]
    if source_type not in ALLOWED_SOURCE_TYPES:
        raise EvidenceArtifactError(f"invalid source_type: {source_type!r}")

    conversations = artifact["conversations"]
    if not isinstance(conversations, list) or not conversations:
        raise EvidenceArtifactError("conversations must be a non-empty list")

    # ---- 禁止字段（生产 correlation / evaluation derived / 环境信息）----
    forbidden = collect_forbidden_fields(artifact)
    if forbidden:
        raise EvidenceArtifactError(f"forbidden fields: {sorted(forbidden)}")

    # ---- Secret / 绝对路径 ----
    for text in _iter_strings(artifact):
        if contains_secret_marker(text):
            raise EvidenceArtifactError("artifact must not contain secrets/credentials")
        if contains_absolute_path(text):
            raise EvidenceArtifactError("artifact must not contain absolute paths")

    # ---- Conversation 级 ----
    seen: set[str] = set()
    for conversation in conversations:
        if not isinstance(conversation, Mapping):
            raise EvidenceArtifactError("conversation must be a mapping")
        for field in CONVERSATION_REQUIRED_FIELDS:
            if field not in conversation:
                raise EvidenceArtifactError(f"missing conversation field: {field}")
        case_id = str(conversation["case_id"]).strip()
        if not case_id:
            raise EvidenceArtifactError("case_id must be non-empty")
        if case_id in seen:
            raise EvidenceArtifactError(f"duplicate case_id: {case_id}")
        seen.add(case_id)
        if not str(conversation["project_id"]).strip():
            raise EvidenceArtifactError("project_id must be non-empty")

        turns = conversation["turns"]
        if not isinstance(turns, list) or not turns:
            raise EvidenceArtifactError("turns must be a non-empty list")
        for turn in turns:
            if not isinstance(turn, Mapping) or set(turn) != {"role", "content"}:
                raise EvidenceArtifactError(
                    "turn must contain exactly role / content"
                )
            if turn["role"] not in ("user", "assistant"):
                raise EvidenceArtifactError(f"invalid role: {turn['role']!r}")
            content = turn["content"]
            if not isinstance(content, str) or not content.strip():
                raise EvidenceArtifactError("turn content must be non-empty string")

        # §十 / §十七：当前用户 turn = last turn（由 Evaluation Layer 判断）
        if turns[-1]["role"] != "user":
            raise EvidenceArtifactError("last turn must be user (current question)")


def derive_readiness(
    artifact: Mapping[str, Any], *, deidentified_attestation: bool
) -> EvidenceDataReadiness:
    """从 artifact 派生 G1 readiness（§九：不能默认认为安全）。

    ``deidentified_attestation=False``（外部流程无法证明已脱敏）→
    ``contains_sensitive_fields=True`` → G1 BLOCKED。
    """
    conversations = artifact.get("conversations") or []
    turn_count = sum(
        len(conversation.get("turns") or [])
        for conversation in conversations
        if isinstance(conversation, Mapping)
    )
    source_type = str(artifact.get("source_type", SOURCE_TYPE_SYNTHETIC))
    return EvidenceDataReadiness(
        source_type=source_type,
        sample_count=len(conversations),
        conversation_count=len(conversations),
        turn_count=turn_count,
        has_real_origin=source_type == SOURCE_TYPE_REAL,
        is_deidentified=(
            source_type == SOURCE_TYPE_REAL and deidentified_attestation
        ),
        contains_sensitive_fields=not deidentified_attestation,
        usable_for_evaluation=(
            source_type == SOURCE_TYPE_REAL and deidentified_attestation
        ),
    )


# ============================================================
# Loader Contract（§七：只定义 Contract / Test Stub；不做 IO）
# ============================================================


@dataclasses.dataclass(frozen=True)
class LoaderRequest:
    """外部 artifact 的装载请求（不透明标识；**不是**绝对路径）。"""

    artifact_ref: str
    declared_source_type: str
    deidentified_attestation: bool


def load_real_wms_evidence_artifact(
    request: LoaderRequest, *, parsed_artifact: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Loader contract stub。

    未来链路：external artifact → parse（仓库外）→ 本函数 → EvidenceDatasetValidator。
    本函数**不做任何 IO**：``parsed_artifact`` 必须由外部（仓库外）流程提供。
    """
    if request.declared_source_type not in ALLOWED_SOURCE_TYPES:
        raise EvidenceArtifactError(
            f"invalid declared_source_type: {request.declared_source_type!r}"
        )
    if contains_absolute_path(request.artifact_ref):
        raise EvidenceArtifactError("artifact_ref must not be an absolute path")
    if str(parsed_artifact.get("source_type")) != request.declared_source_type:
        raise EvidenceArtifactError(
            "declared_source_type must match artifact source_type "
            "(no implicit conversion)"
        )
    validate_artifact(parsed_artifact)
    EvidenceDatasetValidator.validate_no_db_connection(parsed_artifact)
    return parsed_artifact


def _simulated_schema() -> dict[str, Any]:
    """极小 synthetic stand-in（NOT real WMS evidence；仅契约模拟）。"""
    return {
        "dataset_version": "wms-evidence-contract-sim-v1",
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
            }
        ],
    }


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


def _function_def(relative: str, name: str) -> ast.FunctionDef:
    for node in ast.walk(ast.parse(_source(relative))):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function not found: {name}")


def _expect_reject(artifact: Mapping[str, Any]) -> str:
    try:
        validate_artifact(artifact)
    except EvidenceArtifactError as exc:
        return str(exc)
    raise AssertionError(f"artifact must be rejected: {artifact!r}")  # pragma: no cover


# ============================================================
# 1. Artifact Schema（§四 / §二十一 1~3）
# ============================================================


class TestArtifactSchema:
    def test_01_valid_artifact_schema(self) -> None:
        artifact = _simulated_schema()
        validate_artifact(artifact)
        assert set(artifact) >= set(ARTIFACT_REQUIRED_FIELDS)
        conversation = artifact["conversations"][0]
        assert set(conversation) >= set(CONVERSATION_REQUIRED_FIELDS)
        assert conversation["turns"][-1]["role"] == "user"

    def test_02_missing_dataset_version_rejected(self) -> None:
        artifact = _simulated_schema()
        del artifact["dataset_version"]
        message = _expect_reject(artifact)
        assert "dataset_version" in message
        blank = _simulated_schema()
        blank["dataset_version"] = "   "
        assert "dataset_version" in _expect_reject(blank)

    def test_03_invalid_source_type_rejected(self) -> None:
        artifact = _simulated_schema()
        artifact["source_type"] = "REAL"
        message = _expect_reject(artifact)
        assert "source_type" in message

    def test_04_synthetic_cannot_become_real(self) -> None:
        """§六：禁止 SYNTHETIC_ONLY → REAL_DEIDENTIFIED 自动转换。"""
        # Step 21 dataset 分类仍为 SYNTHETIC_ONLY（复用 Step 23 审计）
        assert audit_current_readiness().source_type == "SYNTHETIC_ONLY"

        # 声明 SYNTHETIC_ONLY 但内容来自 synthetic stand-in → G1 BLOCKED（不升级）
        synthetic_artifact = dict(_simulated_schema(), source_type=SOURCE_TYPE_SYNTHETIC)
        readiness = derive_readiness(
            synthetic_artifact, deidentified_attestation=True
        )
        assert run_g1_procedure(readiness) == "BLOCKED"

        # 无自动升级函数（AST 函数名检查）
        function_names = {
            node.name
            for node in ast.walk(ast.parse(_source(_SELF)))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for forbidden in ("upgrade", "promote", "coerce_source", "convert_source"):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{forbidden}"

    def test_05_loader_declared_source_type_must_match(self) -> None:
        artifact = _simulated_schema()
        request = LoaderRequest(
            artifact_ref="wms-evidence-contract-sim-v1",
            declared_source_type=SOURCE_TYPE_SYNTHETIC,
            deidentified_attestation=True,
        )
        try:
            load_real_wms_evidence_artifact(request, parsed_artifact=artifact)
        except EvidenceArtifactError as exc:
            assert "declared_source_type" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("mismatched declared_source_type must be rejected")


# ============================================================
# 2. Forbidden Fields（§五 / §十六 / §十七 / §十九 / §二十一 12~18）
# ============================================================


class TestForbiddenFields:
    def test_06_production_conversation_id_rejected(self) -> None:
        artifact = _simulated_schema()
        artifact["conversations"][0]["conversation_id"] = "conv-prod-1"
        message = _expect_reject(artifact)
        assert "conversation_id" in message

    def test_07_production_turn_id_rejected(self) -> None:
        artifact = _simulated_schema()
        artifact["conversations"][0]["turns"][0]["turn_id"] = "turn-1"
        message = _expect_reject(artifact)
        assert "turn_id" in message

    def test_08_production_assistant_request_id_rejected(self) -> None:
        artifact = _simulated_schema()
        artifact["conversations"][0]["turns"][1]["assistant_request_id"] = "req-1"
        message = _expect_reject(artifact)
        assert "assistant_request_id" in message

    def test_09_production_provider_request_id_rejected(self) -> None:
        artifact = _simulated_schema()
        artifact["conversations"][0]["provider_request_id"] = "provider-1"
        message = _expect_reject(artifact)
        assert "provider_request_id" in message

    def test_10_all_production_correlation_ids_forbidden(self) -> None:
        assert set(PRODUCTION_CORRELATION_IDS) <= set(FORBIDDEN_ARTIFACT_FIELDS)
        for field in PRODUCTION_CORRELATION_IDS:
            artifact = _simulated_schema()
            artifact["conversations"][0][field] = "x"
            assert field in _expect_reject(artifact)

    def test_11_no_evaluation_derived_fields(self) -> None:
        """§五 / §二十一 18：raw artifact 不得预先携带结论。"""
        for field in EVALUATION_DERIVED_FIELDS:
            artifact = _simulated_schema()
            artifact["conversations"][0][field] = "x"
            assert field in _expect_reject(artifact), field

    def test_12_environment_fields_forbidden(self) -> None:
        for field in ENVIRONMENT_FIELDS:
            artifact = _simulated_schema()
            artifact[field] = "x"
            assert field in _expect_reject(artifact), field

    def test_13_forbidden_field_detection_is_recursive(self) -> None:
        artifact = _simulated_schema()
        artifact["conversations"][0]["turns"][0]["score"] = 1
        assert collect_forbidden_fields(artifact) == ["score"]
        assert "score" in _expect_reject(artifact)


# ============================================================
# 3. Privacy / Provenance（§八 / §九 / §二十一 16~17）
# ============================================================


class TestPrivacyAndProvenance:
    def test_14_absolute_paths_rejected(self) -> None:
        for bad in ("D:\\data\\wms.json", "C:\\Users\\x\\artifact.yaml", "\\\\srv\\share\\a.json", "/home/user/a.yaml"):
            artifact = _simulated_schema()
            artifact["dataset_version"] = f"v1 {bad}"
            assert "absolute paths" in _expect_reject(artifact), bad

    def test_15_database_url_and_postgres_urls_rejected(self) -> None:
        for bad in (
            "postgresql://u:p@host/db",
            "postgres://u:p@host/db",
            "DATABASE_URL=secret",
            "psycopg2.connect(dsn)",
            "password=secret",
            "Authorization: token",
            "api_key = abc",
        ):
            artifact = _simulated_schema()
            artifact["conversations"][0]["turns"][0]["content"] = f"text {bad}"
            assert "secrets" in _expect_reject(artifact), bad

    def test_16_provenance_minimal_fields_only(self) -> None:
        """§八：provenance = dataset_version + source_type（无环境/凭据信息）。"""
        artifact = _simulated_schema()
        assert "dataset_version" in artifact
        assert "source_type" in artifact
        for field in ("customer", "factory", "employee", "server", "database"):
            artifact_with_field = _simulated_schema()
            artifact_with_field[field] = "x"
            assert field in _expect_reject(artifact_with_field), field

    def test_17_loader_does_no_io(self) -> None:
        """§七：loader contract stub 不做 IO（无 open / read_text / Path）。"""
        function = _function_def(_SELF, "load_real_wms_evidence_artifact")
        forbidden_calls = {
            node.func.id
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "open" not in forbidden_calls
        attribute_calls = {
            node.func.attr
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        for forbidden in ("read_text", "read_bytes", "load", "parse"):
            assert forbidden not in attribute_calls, forbidden

    def test_18_loader_contract_full_path(self) -> None:
        artifact = _simulated_schema()
        request = LoaderRequest(
            artifact_ref="wms-evidence-contract-sim-v1",
            declared_source_type=SOURCE_TYPE_REAL,
            deidentified_attestation=True,
        )
        loaded = load_real_wms_evidence_artifact(request, parsed_artifact=artifact)
        assert loaded is artifact
        # 绝对路径不透明标识被拒绝
        bad_request = dataclasses.replace(
            request, artifact_ref="D:\\data\\wms.json"
        )
        try:
            load_real_wms_evidence_artifact(bad_request, parsed_artifact=artifact)
        except EvidenceArtifactError:
            pass
        else:  # pragma: no cover
            raise AssertionError("absolute artifact_ref must be rejected")


# ============================================================
# 4. G1（§十一 / §二十一 5~7）
# ============================================================


class TestG1Boundary:
    def test_19_real_valid_artifact_is_ready(self) -> None:
        """REAL_DEIDENTIFIED + valid counts + attestation → G1 READY（契约模拟）。"""
        artifact = _simulated_schema()
        readiness = derive_readiness(artifact, deidentified_attestation=True)
        assert run_g1_procedure(readiness) == "READY"
        assert readiness.conversation_count == 1
        assert readiness.turn_count == 3

    def test_20_missing_attestation_blocks_g1(self) -> None:
        """§九：无法证明已脱敏 → G1 BLOCKED（不能默认认为安全）。"""
        artifact = _simulated_schema()
        readiness = derive_readiness(artifact, deidentified_attestation=False)
        assert readiness.contains_sensitive_fields is True
        assert run_g1_procedure(readiness) == "BLOCKED"

    def test_21_empty_conversations_block_g1(self) -> None:
        artifact = _simulated_schema()
        artifact["conversations"] = []
        assert "conversations" in _expect_reject(artifact)
        readiness = derive_readiness(artifact, deidentified_attestation=True)
        assert readiness.conversation_count == 0
        assert run_g1_procedure(readiness) == "BLOCKED"


# ============================================================
# 5. Turn / Conversation Validation（§二十一 8~11）
# ============================================================


class TestConversationValidation:
    def test_22_empty_turns_rejected(self) -> None:
        artifact = _simulated_schema()
        artifact["conversations"][0]["turns"] = []
        assert "turns" in _expect_reject(artifact)

    def test_23_invalid_role_rejected(self) -> None:
        artifact = _simulated_schema()
        artifact["conversations"][0]["turns"][0]["role"] = "tool"
        assert "role" in _expect_reject(artifact)

    def test_24_last_turn_must_be_user(self) -> None:
        artifact = _simulated_schema()
        artifact["conversations"][0]["turns"].append(
            {"role": "assistant", "content": "库存 320"}
        )
        assert "last turn" in _expect_reject(artifact)

    def test_25_duplicate_case_id_rejected(self) -> None:
        artifact = _simulated_schema()
        duplicate = dict(artifact["conversations"][0])
        artifact["conversations"] = [*artifact["conversations"], duplicate]
        assert "duplicate case_id" in _expect_reject(artifact)

    def test_26_extra_turn_keys_rejected(self) -> None:
        """§十七：turn 只依赖 position（role + content），无数据库 turn_id。"""
        artifact = _simulated_schema()
        artifact["conversations"][0]["turns"][0]["position"] = 0
        assert "exactly role / content" in _expect_reject(artifact)

    def test_27_empty_content_rejected(self) -> None:
        artifact = _simulated_schema()
        artifact["conversations"][0]["turns"][0]["content"] = "   "
        assert "content" in _expect_reject(artifact)


# ============================================================
# 6. G2 / G3 / G4 输入边界（§十二 ~ §十四 / §二十一 19~21）
# ============================================================


class TestDownstreamGates:
    def test_28_g2_blocked_until_g1(self) -> None:
        g1_status = run_g1_procedure(
            derive_readiness(_simulated_schema(), deidentified_attestation=False)
        )
        assert g1_status == "BLOCKED"
        result = run_g2_procedure([], g1_status=g1_status)
        assert result["status"] == "INSUFFICIENT"
        assert result["turn_distribution"] == {}
        assert result["character_distribution"] == {}

    def test_29_g3_blocked_until_g1(self) -> None:
        g1_status = run_g1_procedure(
            derive_readiness(_simulated_schema(), deidentified_attestation=False)
        )
        result = run_g3_procedure((), g1_status=g1_status)
        assert result["status"] == "BLOCKED"
        assert result["annotated_turn_count"] == 0

    def test_30_g4_blocked_until_g1(self) -> None:
        g1_status = run_g1_procedure(
            derive_readiness(_simulated_schema(), deidentified_attestation=False)
        )
        result = run_g4_procedure((), g1_status=g1_status)
        assert result["status"] == "BLOCKED"
        assert result["evaluated_case_count"] == 0

    def test_31_artifact_holds_no_selection_or_annotation_state(self) -> None:
        """§十三 / §十四：artifact 只保存历史，不含 annotation / candidate。"""
        artifact = _simulated_schema()
        for field in ("annotation", "selected_history", "candidate_history", "strategy_id"):
            artifact_with_field = _simulated_schema()
            artifact_with_field["conversations"][0][field] = {}
            assert field in _expect_reject(artifact_with_field), field
        validate_artifact(artifact)

    def test_32_report_does_not_leak_artifact_content(self) -> None:
        """§十八：Report 只输出 counts / status（不含完整 content）。"""
        from tests.test_conversation_context_evidence_collection_procedure import (
            build_evidence_report,
        )

        artifact = _simulated_schema()
        readiness = derive_readiness(artifact, deidentified_attestation=False)
        g1_status = run_g1_procedure(readiness)
        report = build_evidence_report(
            dataset_version="wms-evidence-contract-sim-v1",
            readiness=readiness,
            g2_result=run_g2_procedure((), g1_status=g1_status),
            g3_result=run_g3_procedure((), g1_status=g1_status),
            g4_result=run_g4_procedure((), g1_status=g1_status),
        )
        rendered = repr(report)
        for content_fragment in ("只看 A01 仓库", "已限定 A01 仓库", "查询库存"):
            assert content_fragment not in rendered, content_fragment
        # 声明 REAL 但缺脱敏 attestation → G1 BLOCKED（evidence 不可采用）
        assert report.g1_status == "BLOCKED"
        assert report.evidence_type == "REAL"  # source_type 声明为 REAL（未被采用）


# ============================================================
# 7. Determinism / 离线 / 文档（§二十一 22 / §二十四）
# ============================================================


class TestDeterminismAndDocument:
    def test_33_validation_is_deterministic(self) -> None:
        artifact = _simulated_schema()
        validate_artifact(artifact)
        validate_artifact(artifact)
        assert _simulated_schema() == _simulated_schema()
        assert derive_readiness(
            artifact, deidentified_attestation=True
        ) == derive_readiness(artifact, deidentified_attestation=True)
        assert collect_forbidden_fields(artifact) == []

    def test_34_no_db_network_llm_in_boundary(self) -> None:
        modules = _module_imports(_SELF)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for forbidden in (
                "sqlalchemy",
                "psycopg",
                "redis",
                "celery",
                "kafka",
                "httpx",
                "requests",
                "openai",
                "backend.app.db",
                "backend.app.services",
            ):
                assert not module.startswith(forbidden), module

    def test_35_no_runtime_coupling(self) -> None:
        """§二十二：Real Evidence Contract 不得 import 运行时（Conversation / AI）。"""
        modules = _module_imports(_SELF)
        for forbidden in (
            "backend.app.services.conversation_service",
            "backend.app.services.chat_application_service",
            "backend.app.services.ai_orchestrator_service",
            "backend.app.services.rag_service",
            "backend.app.services.tool_chat_service",
            "backend.app.services.text_to_sql_service",
        ):
            assert forbidden not in modules, forbidden

    def test_36_no_deidentification_tools_implemented(self) -> None:
        for name in ("deidentify.py", "anonymize.py", "redact.py"):
            for directory in ("scripts", "backend/app/services", "tests"):
                assert not (_REPO_ROOT / directory / name).exists(), (
                    f"{directory}/{name}"
                )
        function_names = {
            node.name
            for node in ast.walk(ast.parse(_source(_SELF)))
            if isinstance(node, ast.FunctionDef)
        }
        for forbidden in ("deidentify", "anonymize", "redact", "mask_pii"):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{forbidden}"

    def test_37_document_sections_and_statements(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in (
            "## 1. Purpose",
            "## 2. Artifact Schema",
            "## 3. Forbidden Fields",
            "## 4. Privacy",
            "## 5. G1",
            "## 6. G2/G3/G4",
            "## 7. Project Isolation",
            "## 8. Synthetic",
            "## 9. Runtime Isolation",
            "## 10. Deferred",
        ):
            assert section in doc, section
        for statement in (
            "De-identification happens outside repo.",
            "project_id != authorization",
            "Step21 dataset remains SYNTHETIC_ONLY.",
            "does not enter production runtime.",
            "REAL_DEIDENTIFIED",
            "This is contract simulation only.",
        ):
            assert statement in doc, statement


__all__ = [
    "SOURCE_TYPE_REAL",
    "SOURCE_TYPE_SYNTHETIC",
    "ARTIFACT_REQUIRED_FIELDS",
    "PRODUCTION_CORRELATION_IDS",
    "EVALUATION_DERIVED_FIELDS",
    "FORBIDDEN_ARTIFACT_FIELDS",
    "EvidenceArtifactError",
    "LoaderRequest",
    "load_real_wms_evidence_artifact",
    "validate_artifact",
    "derive_readiness",
    "collect_forbidden_fields",
    "contains_absolute_path",
    "contains_secret_marker",
]
