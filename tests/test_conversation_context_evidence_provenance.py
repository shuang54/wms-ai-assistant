"""Evidence Provenance & Version Freeze（Phase 4.1 Step 27 —— **Contract only**）。

本文件冻结：未来第一份 REAL_DEIDENTIFIED WMS Evidence Artifact 进入
Evaluation Layer 时，如何证明它是"哪一版、是否被替换、是否与报告对应"
（test-local；**非 production**）。

```text
Artifact Schema（Step 26）
    ≠
Provenance

Artifact 描述：有什么数据
Provenance 描述：这份数据是哪一版
```

冻结的 Provenance Contract：

```text
EvidenceProvenance
    dataset_version（非空 / 稳定 / 可比较）
    source_type（REAL_DEIDENTIFIED / SYNTHETIC_ONLY）
```

明确不做：semantic versioning / Git SHA / timestamp / machine id / hostname /
absolute path / database id；不实现内容 hash（Artifact Integrity Hash = DEFERRED）；
不创建真实 artifact（G1 = BLOCKED 保持不变）。

边界：DB = 0 · Network = 0 · LLM = 0 · 不改 Step 25 Report Schema ·
不做 runtime / environment / CLI / project config override。
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tests.test_conversation_context_evidence_collection_procedure import (
    EvidenceCollectionReport,
    run_g1_procedure,
    run_g2_procedure,
    run_g3_procedure,
    run_g4_procedure,
)
from tests.test_conversation_context_real_evidence_boundary import (
    FORBIDDEN_ARTIFACT_FIELDS,
    PRODUCTION_CORRELATION_IDS,
    SOURCE_TYPE_REAL,
    SOURCE_TYPE_SYNTHETIC,
    derive_readiness,
    validate_artifact,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_evidence_provenance.py"
_AUDIT_DOC = "docs/evaluation/Phase 4.1 Step 27 — Evidence Provenance & Version Freeze Audit.md"

# ============================================================
# 常量（test-local）
# ============================================================

PROVENANCE_FIELDS: tuple[str, ...] = ("dataset_version", "source_type")

ALLOWED_SOURCE_TYPES: tuple[str, ...] = (SOURCE_TYPE_REAL, SOURCE_TYPE_SYNTHETIC)

#: 比较结果值域（§十 / §十一）。
VERSION_SAME = "SAME"
VERSION_MISMATCH = "VERSION_MISMATCH"
SOURCE_TYPE_MISMATCH = "SOURCE_TYPE_MISMATCH"

COMPARISON_STATES: tuple[str, ...] = (VERSION_SAME, VERSION_MISMATCH)

#: 禁止进入 Provenance 的运行环境 / 部署信息（§六）。
FORBIDDEN_ENVIRONMENT_FIELDS: tuple[str, ...] = (
    "hostname",
    "machine_id",
    "username",
    "absolute_path",
    "DATABASE_URL",
    "database_host",
    "server",
    "container_id",
    "git_sha",
    # 额外环境/部署面
    "app_version",
    "package_version",
    "git_tag",
    "commit_sha",
)

#: 自动生成 version 的形态（§十四：timestamp / UUID / random hash 禁止）。
_FORBIDDEN_VERSION_GENERATORS: tuple[str, ...] = (
    "uuid",
    "uuid4",
    "uuid1",
    "random",
    "timestamp",
    "now",
    "time_ns",
    "monotonic",
)

#: Proposed Report Provenance Contract（§十六；**不修改** Step 25 Report Schema）。
PROPOSED_REPORT_PROVENANCE_FIELDS: tuple[str, ...] = (
    "dataset_version",
    "source_type",
    "g1_status",
    "g2_status",
    "g3_status",
    "g4_status",
)

#: Step 25 EvidenceCollectionReport 字段（冻结：本阶段不得修改）。
FROZEN_STEP25_REPORT_FIELDS: tuple[str, ...] = (
    "dataset_version",
    "source_type",
    "evidence_type",
    "g1_status",
    "g2_status",
    "g2_turn_distribution",
    "g2_character_distribution",
    "g3_status",
    "g3_annotation_counts",
    "g3_disagreement_count",
    "g4_status",
    "g4_impact_counts",
    "g4_unknown_count",
    "selection_strategy_decision",
)


class ProvenanceError(ValueError):
    """Provenance 校验失败（test-local；非 production 异常）。"""


class VersionMismatchError(ProvenanceError):
    """Artifact 与 Evaluation 声明的 dataset_version 不一致。"""

    state = VERSION_MISMATCH


class SourceTypeMismatchError(ProvenanceError):
    """Artifact 与 Evaluation 声明的 source_type 不一致。"""

    state = SOURCE_TYPE_MISMATCH


# ============================================================
# EvidenceProvenance（§五：最小字段 + 非空/稳定/可比较）
# ============================================================


@dataclasses.dataclass(frozen=True)
class EvidenceProvenance:
    """Evaluation Dataset 身份（**只含 dataset_version + source_type**）。"""

    dataset_version: str
    source_type: str

    def validate(self) -> None:
        version = self.dataset_version
        if not isinstance(version, str) or not version.strip():
            raise ProvenanceError("dataset_version must be non-empty string")
        if version != version.strip():
            raise ProvenanceError("dataset_version must be stable (no padding)")
        if self.source_type not in ALLOWED_SOURCE_TYPES:
            raise ProvenanceError(f"invalid source_type: {self.source_type!r}")

    def as_dict(self) -> dict[str, str]:
        return {
            "dataset_version": self.dataset_version,
            "source_type": self.source_type,
        }


def derive_provenance(artifact: Mapping[str, Any]) -> EvidenceProvenance:
    """从 artifact **声明**派生 provenance（不生成、不推断、不改写）。

    Dataset version 必须由 Evidence Dataset 本身声明（§七）：
    不从 package version / git tag / commit SHA / 时间 / UUID 生成。
    """
    provenance = EvidenceProvenance(
        dataset_version=str(artifact.get("dataset_version", "")),
        source_type=str(artifact.get("source_type", "")),
    )
    provenance.validate()
    return provenance


def validate_provenance_payload(payload: Mapping[str, Any]) -> None:
    """Provenance payload 只允许 PROVENANCE_FIELDS；环境字段/生产 ID 一律拒绝。"""
    for key in payload:
        name = str(key)
        if name in FORBIDDEN_ENVIRONMENT_FIELDS:
            raise ProvenanceError(f"environment field forbidden in provenance: {name}")
        if name in PRODUCTION_CORRELATION_IDS:
            raise ProvenanceError(f"production id forbidden in provenance: {name}")
        if name not in PROVENANCE_FIELDS:
            raise ProvenanceError(f"unknown provenance field: {name}")


def compare_versions(left: str, right: str) -> str:
    """§十 / §十四：V1 == V1 → SAME；V1 != V2 → VERSION_MISMATCH。"""
    return VERSION_SAME if left == right else VERSION_MISMATCH


def reconcile_provenance(
    artifact_provenance: EvidenceProvenance,
    declared_provenance: EvidenceProvenance,
) -> EvidenceProvenance:
    """Artifact 与 Evaluation 声明对账（§十 / §十一）。

    不一致 → VERSION_MISMATCH / SOURCE_TYPE_MISMATCH（**不能继续**）。
    一致 → 返回 artifact provenance（provenance 不可被声明覆盖）。
    """
    artifact_provenance.validate()
    declared_provenance.validate()
    if (
        artifact_provenance.source_type != declared_provenance.source_type
    ):
        raise SourceTypeMismatchError(
            f"{SOURCE_TYPE_MISMATCH}: artifact={artifact_provenance.source_type!r} "
            f"declared={declared_provenance.source_type!r}"
        )
    if artifact_provenance.dataset_version != declared_provenance.dataset_version:
        raise VersionMismatchError(
            f"{VERSION_MISMATCH}: artifact={artifact_provenance.dataset_version!r} "
            f"declared={declared_provenance.dataset_version!r}"
        )
    return artifact_provenance


def build_proposed_report_binding(
    provenance: EvidenceProvenance,
    *,
    g1_status: str,
    g2_status: str,
    g3_status: str,
    g4_status: str,
) -> dict[str, str]:
    """Proposed Report Provenance Contract（§十六；**不修改** Step 25 schema）。"""
    provenance.validate()
    return {
        "dataset_version": provenance.dataset_version,
        "source_type": provenance.source_type,
        "g1_status": g1_status,
        "g2_status": g2_status,
        "g3_status": g3_status,
        "g4_status": g4_status,
    }


@dataclasses.dataclass(frozen=True)
class AnnotatedEvidenceIdentity:
    """§十七：dataset_version 与 annotation_version 必须同时保留、互不覆盖。"""

    dataset_version: str
    annotation_version: str


def _simulated_artifact(
    *,
    dataset_version: str = "wms-conversation-v1",
    source_type: str = SOURCE_TYPE_REAL,
) -> dict[str, Any]:
    """极小 synthetic stand-in（NOT real WMS evidence；仅契约模拟）。"""
    return {
        "dataset_version": dataset_version,
        "source_type": source_type,
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


def _function_param_names(relative: str, name: str) -> set[str]:
    function = _function_def(relative, name)
    arguments = function.args
    names: set[str] = set()
    for group in (arguments.posonlyargs, arguments.args, arguments.kwonlyargs):
        names |= {arg.arg for arg in group}
    return names


# ============================================================
# 1. Provenance（§五 / §十九 Provenance）
# ============================================================


class TestProvenanceContract:
    def test_01_valid_provenance(self) -> None:
        provenance = EvidenceProvenance(
            dataset_version="wms-conversation-v1", source_type=SOURCE_TYPE_REAL
        )
        provenance.validate()
        assert provenance.as_dict() == {
            "dataset_version": "wms-conversation-v1",
            "source_type": SOURCE_TYPE_REAL,
        }
        assert set(provenance.as_dict()) == set(PROVENANCE_FIELDS)

    def test_02_empty_dataset_version_rejected(self) -> None:
        for bad in ("", "   ", "\t"):
            try:
                EvidenceProvenance(
                    dataset_version=bad, source_type=SOURCE_TYPE_REAL
                ).validate()
            except ProvenanceError:
                continue
            raise AssertionError(f"dataset_version {bad!r} must be rejected")  # pragma: no cover

    def test_03_unstable_padded_version_rejected(self) -> None:
        """§五：version 必须稳定（不允许前后空白导致的"同物不同串"）。"""
        try:
            EvidenceProvenance(
                dataset_version=" wms-conversation-v1 ", source_type=SOURCE_TYPE_REAL
            ).validate()
        except ProvenanceError:
            pass
        else:  # pragma: no cover
            raise AssertionError("padded dataset_version must be rejected")

    def test_04_source_type_validation(self) -> None:
        for good in ALLOWED_SOURCE_TYPES:
            EvidenceProvenance(
                dataset_version="wms-conversation-v1", source_type=good
            ).validate()
        for bad in ("REAL", "real_deidentified", "", "DEIDENTIFIED"):
            try:
                EvidenceProvenance(
                    dataset_version="wms-conversation-v1", source_type=bad
                ).validate()
            except ProvenanceError:
                continue
            raise AssertionError(f"source_type {bad!r} must be rejected")  # pragma: no cover

    def test_05_provenance_payload_keys_are_closed(self) -> None:
        validate_provenance_payload(
            {"dataset_version": "v1", "source_type": SOURCE_TYPE_REAL}
        )
        for bad_key in ("conversations", "turns", "case_id", "dataset_name"):
            try:
                validate_provenance_payload({bad_key: "x"})
            except ProvenanceError:
                continue
            raise AssertionError(f"unknown key {bad_key} must be rejected")  # pragma: no cover

    def test_06_derive_provenance_reads_declared_version_only(self) -> None:
        artifact = _simulated_artifact()
        provenance = derive_provenance(artifact)
        assert provenance.dataset_version == artifact["dataset_version"]
        assert provenance.source_type == artifact["source_type"]
        # 缺失声明 → reject（不生成默认值）
        broken = dict(artifact)
        del broken["dataset_version"]
        try:
            derive_provenance(broken)
        except ProvenanceError:
            pass
        else:  # pragma: no cover
            raise AssertionError("missing dataset_version must be rejected")


# ============================================================
# 2. Version Semantics（§七 / §十四 / §十九 Version）
# ============================================================


class TestVersionSemantics:
    def test_07_same_version_compares_equal(self) -> None:
        assert compare_versions("wms-conversation-v1", "wms-conversation-v1") == VERSION_SAME
        assert VERSION_SAME in COMPARISON_STATES

    def test_08_different_version_is_mismatch(self) -> None:
        assert (
            compare_versions("wms-conversation-v1", "wms-conversation-v2")
            == VERSION_MISMATCH
        )

    def test_09_dataset_version_is_not_app_version(self) -> None:
        """§七：dataset_version != app_version，且不得从包版本 / git 生成。"""
        doc = _source(_AUDIT_DOC)
        assert "dataset_version != app_version" in doc
        # app_version / package_version / git_tag / commit_sha 属环境字段（禁止）
        for field_name in ("app_version", "package_version", "git_tag", "commit_sha"):
            assert field_name in FORBIDDEN_ENVIRONMENT_FIELDS, field_name
            try:
                validate_provenance_payload({field_name: "x"})
            except ProvenanceError:
                continue
            raise AssertionError(f"{field_name} must be rejected")  # pragma: no cover

    def test_10_no_auto_generated_version(self) -> None:
        """§十四：不得用 timestamp / UUID / random hash 生成 dataset_version。"""
        derive = _function_def(_SELF, "derive_provenance")
        called_names = {
            node.func.id
            for node in ast.walk(derive)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        called_attrs = {
            node.func.attr
            for node in ast.walk(derive)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        for forbidden in _FORBIDDEN_VERSION_GENERATORS:
            assert forbidden not in called_names, forbidden
            assert forbidden not in called_attrs, forbidden
        for module in _module_imports(_SELF):
            for forbidden in ("uuid", "random", "time", "datetime"):
                assert not module.startswith(forbidden), module

    def test_11_repeated_load_yields_identical_provenance(self) -> None:
        artifact = _simulated_artifact()
        first = derive_provenance(artifact)
        second = derive_provenance(artifact)
        assert first == second
        assert first.as_dict() == second.as_dict()


# ============================================================
# 3. Source Type（§八 / §十九 Source Type）
# ============================================================


class TestSourceType:
    def test_12_same_source_type_matches(self) -> None:
        left = EvidenceProvenance("v1", SOURCE_TYPE_REAL)
        right = EvidenceProvenance("v1", SOURCE_TYPE_REAL)
        assert reconcile_provenance(left, right) == left

    def test_13_synthetic_never_becomes_real(self) -> None:
        """§八：SYNTHETIC_ONLY 永远不能成为 REAL_DEIDENTIFIED。"""
        synthetic = EvidenceProvenance("v1", SOURCE_TYPE_SYNTHETIC)
        declared_real = EvidenceProvenance("v1", SOURCE_TYPE_REAL)
        try:
            reconcile_provenance(synthetic, declared_real)
        except SourceTypeMismatchError as exc:
            assert SOURCE_TYPE_MISMATCH in str(exc)
        else:  # pragma: no cover
            raise AssertionError("synthetic → real must be SOURCE_TYPE_MISMATCH")
        # 无 promote / coerce / convert / auto_upgrade 函数
        function_names = {
            node.name
            for node in ast.walk(ast.parse(_source(_SELF)))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for forbidden in ("promote", "coerce", "convert", "auto_upgrade"):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{forbidden}"

    def test_14_reverse_direction_is_also_mismatch(self) -> None:
        """§十一：反过来也一样（REAL 声明 vs SYNTHETIC artifact）。"""
        try:
            reconcile_provenance(
                EvidenceProvenance("v1", SOURCE_TYPE_REAL),
                EvidenceProvenance("v1", SOURCE_TYPE_SYNTHETIC),
            )
        except SourceTypeMismatchError:
            pass
        else:  # pragma: no cover
            raise AssertionError("real → synthetic must be SOURCE_TYPE_MISMATCH")


# ============================================================
# 4. Mismatch（§十 / §十一 / §十九 Mismatch）
# ============================================================


class TestMismatch:
    def test_15_version_mismatch_raises_and_blocks(self) -> None:
        artifact = EvidenceProvenance("wms-conversation-v1", SOURCE_TYPE_REAL)
        declared = EvidenceProvenance("wms-conversation-v2", SOURCE_TYPE_REAL)
        try:
            reconcile_provenance(artifact, declared)
        except VersionMismatchError as exc:
            assert exc.state == VERSION_MISMATCH
        else:  # pragma: no cover
            raise AssertionError("version mismatch must be raised")

    def test_16_mismatch_stops_downstream_results(self) -> None:
        """§十：mismatch 时不能继续生成真实 G2/G3/G4 结果。"""
        g1_status = run_g1_procedure(
            derive_readiness(_simulated_artifact(), deidentified_attestation=False)
        )
        assert g1_status == "BLOCKED"
        # 下游仍只能是 INSUFFICIENT / BLOCKED（不产生统计）
        assert run_g2_procedure((), g1_status=g1_status)["status"] == "INSUFFICIENT"
        assert run_g3_procedure((), g1_status=g1_status)["status"] == "BLOCKED"
        assert run_g4_procedure((), g1_status=g1_status)["status"] == "BLOCKED"

    def test_17_mismatch_states_are_closed(self) -> None:
        assert VERSION_MISMATCH == "VERSION_MISMATCH"
        assert SOURCE_TYPE_MISMATCH == "SOURCE_TYPE_MISMATCH"
        assert issubclass(VersionMismatchError, ProvenanceError)
        assert issubclass(SourceTypeMismatchError, ProvenanceError)
        assert VersionMismatchError.state != SourceTypeMismatchError.state


# ============================================================
# 5. Identity（§十二 / §十三 / §十九 Identity）
# ============================================================


class TestIdentity:
    def test_18_case_id_is_stable_identity(self) -> None:
        artifact = _simulated_artifact()
        validate_artifact(artifact)
        case_ids = [c["case_id"] for c in artifact["conversations"]]
        assert case_ids == ["contract_sim_001"]
        # 同一 artifact 重复校验 → 相同 identity 集合（稳定）
        validate_artifact(artifact)
        assert [c["case_id"] for c in artifact["conversations"]] == case_ids

    def test_19_duplicate_case_id_rejected(self) -> None:
        artifact = _simulated_artifact()
        artifact["conversations"] = [
            *artifact["conversations"],
            dict(artifact["conversations"][0]),
        ]
        try:
            validate_artifact(artifact)
        except ValueError as exc:
            assert "duplicate case_id" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("duplicate case_id must be rejected")

    def test_20_case_id_may_reappear_across_versions(self) -> None:
        """§十三：同一 case_id 可出现在 V1 / V2（不同快照），不要求全局永久唯一。"""
        v1 = _simulated_artifact(dataset_version="wms-conversation-v1")
        v2 = _simulated_artifact(dataset_version="wms-conversation-v2")
        validate_artifact(v1)
        validate_artifact(v2)
        assert v1["conversations"][0]["case_id"] == v2["conversations"][0]["case_id"]
        assert compare_versions(
            derive_provenance(v1).dataset_version,
            derive_provenance(v2).dataset_version,
        ) == VERSION_MISMATCH

    def test_21_production_ids_not_used_as_identity(self) -> None:
        """§十二：生产 correlation IDs 不得作为 Evidence Artifact Identity。"""
        assert set(PRODUCTION_CORRELATION_IDS) <= set(FORBIDDEN_ARTIFACT_FIELDS)
        for field_name in PRODUCTION_CORRELATION_IDS:
            artifact = _simulated_artifact()
            artifact["conversations"][0][field_name] = "prod-id"
            try:
                validate_artifact(artifact)
            except ValueError:
                continue
            raise AssertionError(f"{field_name} must be rejected")  # pragma: no cover


# ============================================================
# 6. Immutability（§九 / §十九 Immutability）
# ============================================================


class TestImmutability:
    def test_22_provenance_is_frozen(self) -> None:
        provenance = derive_provenance(_simulated_artifact())
        try:
            provenance.dataset_version = "overridden"  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            pass
        else:  # pragma: no cover
            raise AssertionError("provenance must be frozen")
        try:
            provenance.source_type = SOURCE_TYPE_SYNTHETIC  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            pass
        else:  # pragma: no cover
            raise AssertionError("source_type must be frozen")

    def test_23_no_override_entry_points(self) -> None:
        """§九：无 runtime / environment / CLI / project config override 入口。"""
        for function_name in (
            "derive_provenance",
            "reconcile_provenance",
            "compare_versions",
        ):
            params = _function_param_names(_SELF, function_name)
            for forbidden in (
                "override",
                "env",
                "environ",
                "cli",
                "config",
                "runtime",
                "force",
            ):
                assert not any(
                    forbidden in param.lower() for param in params
                ), f"{function_name}:{forbidden}"

    def test_24_artifact_provenance_wins_over_declared(self) -> None:
        """对账一致时返回 artifact provenance（声明不覆盖 artifact）。"""
        artifact = derive_provenance(_simulated_artifact())
        declared = EvidenceProvenance(artifact.dataset_version, artifact.source_type)
        assert reconcile_provenance(artifact, declared) is artifact

    def test_25_module_has_no_environment_reads(self) -> None:
        modules = _module_imports(_SELF)
        for module in modules:
            for forbidden in ("os", "dotenv", "getpass", "socket", "platform"):
                assert not module.startswith(forbidden), module


# ============================================================
# 7. Environment Isolation（§六 / §十九 Environment Isolation）
# ============================================================


class TestEnvironmentIsolation:
    def test_26_environment_fields_rejected_in_provenance(self) -> None:
        for field_name in FORBIDDEN_ENVIRONMENT_FIELDS:
            try:
                validate_provenance_payload({field_name: "x"})
            except ProvenanceError:
                continue
            raise AssertionError(f"{field_name} must be rejected")  # pragma: no cover

    def test_27_documented_environment_isolation(self) -> None:
        doc = _source(_AUDIT_DOC)
        for field_name in (
            "hostname",
            "machine_id",
            "username",
            "absolute_path",
            "DATABASE_URL",
            "database_host",
            "server",
            "container_id",
            "git_sha",
        ):
            assert field_name in doc, field_name

    def test_28_execution_metadata_is_separate_contract(self) -> None:
        """§六：未来如需审计运行环境，另建独立 Execution Metadata Contract（本阶段不做）。"""
        doc = _source(_AUDIT_DOC)
        assert "Execution Metadata Contract" in doc
        function_names = {
            node.name
            for node in ast.walk(ast.parse(_source(_SELF)))
            if isinstance(node, ast.FunctionDef)
            and not node.name.startswith("test_")  # 测试方法名描述该约束，不参与检查
        }
        for forbidden in ("execution_metadata", "collect_env", "capture_host"):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{forbidden}"


# ============================================================
# 8. Hash / Annotation Separation / Report Binding
# ============================================================


class TestHashAnnotationAndReport:
    def test_29_artifact_hash_is_deferred(self) -> None:
        """§十五：本阶段不实现内容 hash（canonical serialization 未解决）。"""
        doc = _source(_AUDIT_DOC)
        assert "Artifact Integrity Hash = DEFERRED" in doc
        for module in _module_imports(_SELF):
            for forbidden in ("hashlib", "zlib", "hmac", "blake3", "xxhash"):
                assert not module.startswith(forbidden), module
        function_names = {
            node.name
            for node in ast.walk(ast.parse(_source(_SELF)))
            if isinstance(node, ast.FunctionDef)
            and not node.name.startswith("test_")  # 测试方法名描述该约束，不参与检查
        }
        for forbidden in ("hash", "checksum", "digest", "fingerprint"):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{forbidden}"

    def test_30_annotation_separation(self) -> None:
        """§十七：dataset_version 与 annotation_version 分离，互不覆盖。"""
        identity = AnnotatedEvidenceIdentity(
            dataset_version="wms-conversation-v1",
            annotation_version="annotation-v1",
        )
        assert identity.dataset_version != identity.annotation_version
        doc = _source(_AUDIT_DOC)
        assert "annotation_version" in doc
        assert "不覆盖" in doc or "never overrides" in doc or "互不覆盖" in doc
        # 无任何赋值把 annotation_version 写入 dataset_version（AST 精确检查）
        tree = ast.parse(_source(_SELF))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not (
                    isinstance(target, ast.Attribute)
                    and target.attr == "dataset_version"
                ):
                    continue
                value = node.value
                if isinstance(value, ast.Attribute) and value.attr == (
                    "annotation_version"
                ):
                    raise AssertionError(
                        "annotation_version must not be assigned to dataset_version"
                    )

    def test_31_proposed_report_binding_contract(self) -> None:
        provenance = EvidenceProvenance("wms-conversation-v1", SOURCE_TYPE_REAL)
        binding = build_proposed_report_binding(
            provenance,
            g1_status="BLOCKED",
            g2_status="INSUFFICIENT",
            g3_status="BLOCKED",
            g4_status="BLOCKED",
        )
        assert set(binding) == set(PROPOSED_REPORT_PROVENANCE_FIELDS)
        assert binding["dataset_version"] == "wms-conversation-v1"
        assert binding["source_type"] == SOURCE_TYPE_REAL

    def test_32_step25_report_schema_unchanged(self) -> None:
        """§十六：不修改 Step 25 Report Schema（字段集合冻结）。"""
        fields = tuple(
            field.name for field in dataclasses.fields(EvidenceCollectionReport)
        )
        assert fields == FROZEN_STEP25_REPORT_FIELDS

    def test_33_blocked_g1_has_no_fabricated_statistics(self) -> None:
        """§十六：G1 BLOCKED → G2/G3/G4 仍不得出现伪造统计。"""
        readiness = derive_readiness(
            _simulated_artifact(), deidentified_attestation=False
        )
        g1_status = run_g1_procedure(readiness)
        assert g1_status == "BLOCKED"
        g2 = run_g2_procedure((), g1_status=g1_status)
        g3 = run_g3_procedure((), g1_status=g1_status)
        g4 = run_g4_procedure((), g1_status=g1_status)
        assert g2["turn_distribution"] == {}
        assert g2["character_distribution"] == {}
        assert all(value == 0 for value in g3["annotation_counts"].values())
        assert all(value == 0 for value in g4["impact_counts"].values())


# ============================================================
# 9. G1 Gating（§十八 / §十九 Gating）
# ============================================================


class TestGating:
    def test_34_g1_requires_provenance_and_attestation(self) -> None:
        """§十八：REAL_DEIDENTIFIED 需要 dataset_version + source_type + attestation + 计数。"""
        provenance = derive_provenance(_simulated_artifact())
        readiness = derive_readiness(
            _simulated_artifact(), deidentified_attestation=True
        )
        assert provenance.dataset_version
        assert provenance.source_type == SOURCE_TYPE_REAL
        assert run_g1_procedure(readiness) == "READY"  # 契约模拟（无真实数据）

        # 去 attestation → BLOCKED
        assert (
            run_g1_procedure(
                derive_readiness(
                    _simulated_artifact(), deidentified_attestation=False
                )
            )
            == "BLOCKED"
        )

    def test_35_synthetic_provenance_blocks_g1(self) -> None:
        synthetic = _simulated_artifact(source_type=SOURCE_TYPE_SYNTHETIC)
        provenance = derive_provenance(synthetic)
        assert provenance.source_type == SOURCE_TYPE_SYNTHETIC
        readiness = derive_readiness(synthetic, deidentified_attestation=True)
        assert run_g1_procedure(readiness) == "BLOCKED"

    def test_36_current_gate_state_is_blocked(self) -> None:
        """当前真实状态（无真实 artifact）→ 全链路 BLOCKED（§十八）。"""
        from tests.test_conversation_selection_evidence_data_audit import (
            audit_current_readiness,
        )

        assert audit_current_readiness().source_type == "SYNTHETIC_ONLY"
        g1_status = run_g1_procedure(audit_current_readiness())
        assert g1_status == "BLOCKED"
        assert run_g2_procedure((), g1_status=g1_status)["status"] == "INSUFFICIENT"
        assert run_g3_procedure((), g1_status=g1_status)["status"] == "BLOCKED"
        assert run_g4_procedure((), g1_status=g1_status)["status"] == "BLOCKED"
        doc = _source(_AUDIT_DOC)
        for statement in (
            "G1 = BLOCKED",
            "G2 = INSUFFICIENT",
            "G3 Contract = READY",
            "G3 Evidence = BLOCKED",
            "G4 = BLOCKED",
            "Selection Strategy = BLOCKED",
        ):
            assert statement in doc, statement

    def test_37_offline_and_deterministic(self) -> None:
        modules = _module_imports(_SELF)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
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
        artifact = _simulated_artifact()
        assert derive_provenance(artifact) == derive_provenance(artifact)

    def test_38_document_sections(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in (
            "## 1. Current State",
            "## 2. Provenance",
            "## 3. Version Semantics",
            "## 4. Mismatch",
            "## 5. Identity",
            "## 6. Production ID Isolation",
            "## 7. Environment Isolation",
            "## 8. Hash",
            "## 9. Annotation Separation",
            "## 10. G1/G2/G3/G4",
            "## 11. Deferred",
        ):
            assert section in doc, section

    def test_39_signatures_are_keyword_or_plain_only(self) -> None:
        """契约函数不引入 override / resolver 式签名。"""
        params = _function_param_names(_SELF, "reconcile_provenance")
        assert params == {"artifact_provenance", "declared_provenance"}
        assert list(inspect.signature(derive_provenance).parameters) == ["artifact"]


__all__ = [
    "PROVENANCE_FIELDS",
    "ALLOWED_SOURCE_TYPES",
    "VERSION_SAME",
    "VERSION_MISMATCH",
    "SOURCE_TYPE_MISMATCH",
    "FORBIDDEN_ENVIRONMENT_FIELDS",
    "PROPOSED_REPORT_PROVENANCE_FIELDS",
    "ProvenanceError",
    "VersionMismatchError",
    "SourceTypeMismatchError",
    "EvidenceProvenance",
    "AnnotatedEvidenceIdentity",
    "derive_provenance",
    "validate_provenance_payload",
    "compare_versions",
    "reconcile_provenance",
    "build_proposed_report_binding",
]
