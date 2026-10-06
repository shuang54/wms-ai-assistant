"""Phase 4.1 Step 36 — Real Evidence Persistence & Provenance Boundary（**Contract only**）。

审计事实基线（来自真实代码检查）：production（backend/app）当前**没有**
Evidence / Annotation 持久化 —— ORM 8 张表中无 evidence / annotation 表；
6 个 repository 中无 evidence / annotation repository；无 evidence persistence
service；无 alembic（建表 = init_db() → Base.metadata.create_all()）。

因此本 Step **不重复实现**、不引入 DB schema 变更，只冻结边界：

```text
Evidence 状态机
Evidence 持久化字段最小集（以 Step 31 Contract 为准；不自造字段）
Provenance（复用 Step 27）
Evidence ↔ Annotation 稳定关联（禁止 content / source name 文本匹配）
Idempotency（dataset_version 稳定键；first-write-wins）
Transaction Boundary（Evidence + Annotation 同一事务；失败 → 无孤儿）
Security Boundary
```

本文件内的 registry 是 **test-local 契约模拟**（in-memory；**非** production
持久化、**非** DB），只用于验证上述边界语义。

边界：DB = 0 · Network = 0 · LLM = 0 · 不连接数据库 · 不写 production 代码 ·
不自行创造不属于 Step 31 Contract 的字段（如 source_ref / content_ref / locator）。
"""
from __future__ import annotations

import ast
import dataclasses
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tests.test_conversation_context_real_evidence_boundary import (
    SOURCE_TYPE_REAL,
    SOURCE_TYPE_SYNTHETIC,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_evidence_persistence_boundary.py"

# ============================================================
# 1. Evidence 状态机（§四：不得用隐式 bool / 散落字符串）
# ============================================================

EVIDENCE_STATUS_IMPORTED: str = "IMPORTED"
EVIDENCE_STATUS_PERSISTED: str = "PERSISTED"
EVIDENCE_STATUS_ANNOTATED: str = "ANNOTATED"
EVIDENCE_STATUS_REVIEWED: str = "REVIEWED"
EVIDENCE_STATUS_FINALIZED: str = "FINALIZED"

EVIDENCE_STATUS_VALUES: tuple[str, ...] = (
    EVIDENCE_STATUS_IMPORTED,
    EVIDENCE_STATUS_PERSISTED,
    EVIDENCE_STATUS_ANNOTATED,
    EVIDENCE_STATUS_REVIEWED,
    EVIDENCE_STATUS_FINALIZED,
)

#: 合法迁移（不得跳级；FINALIZED 为终态）。
EVIDENCE_STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
    EVIDENCE_STATUS_IMPORTED: (EVIDENCE_STATUS_PERSISTED,),
    EVIDENCE_STATUS_PERSISTED: (EVIDENCE_STATUS_ANNOTATED,),
    EVIDENCE_STATUS_ANNOTATED: (EVIDENCE_STATUS_REVIEWED,),
    EVIDENCE_STATUS_REVIEWED: (EVIDENCE_STATUS_FINALIZED,),
    EVIDENCE_STATUS_FINALIZED: (),
}

#: Annotation review 状态（沿用 Step 28~30 的 string 状态风格）。
ANNOTATION_REVIEW_DRAFT: str = "DRAFT"
ANNOTATION_REVIEW_REVIEWED: str = "REVIEWED"
ANNOTATION_REVIEW_VALUES: tuple[str, ...] = (
    ANNOTATION_REVIEW_DRAFT,
    ANNOTATION_REVIEW_REVIEWED,
)

# ============================================================
# 2. 持久化字段最小集（§五：只使用 Step 31 Contract 已有字段）
# ============================================================

#: Evidence 持久化允许字段（identity + source + provenance + timestamps）。
EVIDENCE_PERSISTENCE_FIELDS: tuple[str, ...] = (
    "evidence_id",
    "dataset_version",
    "source_type",
    "de_identification_attested",
    "de_identification_method",
    "status",
    "created_at",
    "updated_at",
)

#: Annotation 持久化允许字段（含指向 Evidence / case 的稳定 identity）。
ANNOTATION_PERSISTENCE_FIELDS: tuple[str, ...] = (
    "annotation_id",
    "evidence_id",
    "case_id",
    "annotation_version",
    "annotator_id",
    "review_status",
    "created_at",
    "updated_at",
)

#: §五：Step 31 Contract **不存在**的字段 → 禁止自造（不得进入持久化）。
FORBIDDEN_PERSISTENCE_FIELDS: tuple[str, ...] = (
    "source_ref",
    "source_reference",
    "content_ref",
    "content_reference",
    "locator",
    "file_path",
    "raw_content",
    "conversation_content",
)

#: §十二：持久化层禁止保存的敏感字段。
FORBIDDEN_SECRET_FIELDS: tuple[str, ...] = (
    "api_key",
    "password",
    "authorization",
    "database_url",
    "connection_string",
    "llm_secret",
    "token",
)

# ============================================================
# 3. DTO（test-local；frozen）
# ============================================================


@dataclasses.dataclass(frozen=True)
class EvidencePersistenceRecord:
    """Evidence 持久化记录（字段 = Step 31 Contract 已有字段 + identity）。"""

    evidence_id: str
    dataset_version: str
    source_type: str
    de_identification_attested: bool
    de_identification_method: str | None
    status: str
    created_at: str
    updated_at: str


@dataclasses.dataclass(frozen=True)
class AnnotationPersistenceRecord:
    """Annotation 持久化记录（通过 evidence_id + case_id 稳定关联）。"""

    annotation_id: str
    evidence_id: str
    case_id: str
    annotation_version: str
    annotator_id: str
    review_status: str
    created_at: str
    updated_at: str


class EvidencePersistenceError(ValueError):
    """持久化边界契约违规（test-local；非 production 异常）。"""


# ============================================================
# 4. 规则函数（纯函数契约；不连接 DB）
# ============================================================


def validate_status(value: str) -> str:
    if value not in EVIDENCE_STATUS_VALUES:
        raise EvidencePersistenceError(f"invalid evidence status: {value!r}")
    return value


def can_transit(current: str, target: str) -> bool:
    """状态迁移合法性（不得跳级）。"""
    validate_status(current)
    validate_status(target)
    return target in EVIDENCE_STATUS_TRANSITIONS[current]


def next_status(current: str, target: str) -> str:
    if not can_transit(current, target):
        raise EvidencePersistenceError(
            f"illegal transition: {current} -> {target}"
        )
    return target


def idempotency_key(dataset_version: str, source_type: str) -> str:
    """§八：幂等键 = dataset_version + source_type（稳定；无 random / time）。"""
    if not dataset_version.strip():
        raise EvidencePersistenceError("dataset_version must be non-empty")
    if source_type not in (SOURCE_TYPE_REAL, SOURCE_TYPE_SYNTHETIC):
        raise EvidencePersistenceError(f"invalid source_type: {source_type!r}")
    return f"{source_type}:{dataset_version}"


def provenance_of(record: EvidencePersistenceRecord) -> dict[str, str]:
    """§六：复用 Step 27 EvidenceProvenance（dataset_version + source_type）。"""
    return {
        "dataset_version": record.dataset_version,
        "source_type": record.source_type,
    }


def validate_no_forbidden_fields(payload: Mapping[str, Any]) -> None:
    """§五 / §十二：自造字段与敏感字段均不得进入持久化。"""
    for key in payload:
        name = str(key).lower()
        if name in FORBIDDEN_PERSISTENCE_FIELDS:
            raise EvidencePersistenceError(f"forbidden persistence field: {key}")
        if name in FORBIDDEN_SECRET_FIELDS:
            raise EvidencePersistenceError(f"secret field forbidden: {key}")


def associate_annotation(
    annotation: AnnotationPersistenceRecord,
    evidence: EvidencePersistenceRecord,
) -> AnnotationPersistenceRecord:
    """§七：Annotation → Evidence 的稳定关联（禁止文本匹配）。

    必须同时满足：evidence_id 相等 + case_id 非空；
    不允许以 content / source name 作为关联依据。
    """
    if not annotation.evidence_id.strip():
        raise EvidencePersistenceError("annotation.evidence_id must be non-empty")
    if annotation.evidence_id != evidence.evidence_id:
        raise EvidencePersistenceError(
            "annotation.evidence_id must match evidence.evidence_id"
        )
    if not annotation.case_id.strip():
        raise EvidencePersistenceError("annotation.case_id must be non-empty")
    if annotation.annotation_version == evidence.dataset_version:
        raise EvidencePersistenceError(
            "annotation_version must differ from dataset_version"
        )
    return annotation


# ============================================================
# 5. 契约模拟 registry（in-memory；**非** production、**非** DB）
# ============================================================


class EvidenceRegistry:
    """持久化边界的**契约模拟**（只验证幂等 / 事务 / 关联 / 隔离语义）。"""

    def __init__(self) -> None:
        self._evidence: dict[str, EvidencePersistenceRecord] = {}
        self._annotations: dict[str, AnnotationPersistenceRecord] = {}
        self._sequence = 0

    # ---- Import → Persist（幂等：同 dataset_version + source_type → 同一条）----
    def import_evidence(
        self,
        *,
        dataset_version: str,
        source_type: str,
        attested: bool,
        method: str | None,
    ) -> EvidencePersistenceRecord:
        key = idempotency_key(dataset_version, source_type)
        if not attested:
            raise EvidencePersistenceError("missing de-identification attestation")
        if key in self._evidence:  # first-write-wins：重复 import 不新增记录
            return self._evidence[key]
        self._sequence += 1
        record = EvidencePersistenceRecord(
            evidence_id=f"ev-{self._sequence:04d}",
            dataset_version=dataset_version,
            source_type=source_type,
            de_identification_attested=attested,
            de_identification_method=method,
            status=EVIDENCE_STATUS_IMPORTED,
            created_at="2026-10-04T00:00:00Z",
            updated_at="2026-10-04T00:00:00Z",
        )
        self._evidence[key] = record
        return record

    def mark_persisted(self, evidence_id: str) -> EvidencePersistenceRecord:
        record = self._by_id(evidence_id)
        updated = dataclasses.replace(
            record, status=next_status(record.status, EVIDENCE_STATUS_PERSISTED)
        )
        key = idempotency_key(record.dataset_version, record.source_type)
        self._evidence[key] = updated
        return updated

    # ---- Annotation 持久化（与 Evidence 状态推进同一事务单元）----
    def append_annotation(
        self,
        *,
        evidence_id: str,
        case_id: str,
        annotation_version: str,
        annotator_id: str,
        review_status: str = ANNOTATION_REVIEW_DRAFT,
        fail: bool = False,
    ) -> AnnotationPersistenceRecord:
        evidence = self._by_id(evidence_id)
        annotation = AnnotationPersistenceRecord(
            annotation_id=f"an-{evidence_id}-{case_id}-{annotation_version}",
            evidence_id=evidence_id,
            case_id=case_id,
            annotation_version=annotation_version,
            annotator_id=annotator_id,
            review_status=review_status,
            created_at="2026-10-04T00:00:00Z",
            updated_at="2026-10-04T00:00:00Z",
        )
        associate_annotation(annotation, evidence)
        if fail:  # 事务失败：annotation 不落库、evidence 状态不推进（无孤儿）
            raise EvidencePersistenceError("annotation persistence failed")
        self._annotations[annotation.annotation_id] = annotation
        key = idempotency_key(evidence.dataset_version, evidence.source_type)
        self._evidence[key] = dataclasses.replace(
            evidence, status=next_status(evidence.status, EVIDENCE_STATUS_ANNOTATED)
        )
        return annotation

    def read(self, evidence_id: str) -> EvidencePersistenceRecord:
        """只读读取（不改变状态）。"""
        return self._by_id(evidence_id)

    def annotations_of(self, evidence_id: str) -> tuple[AnnotationPersistenceRecord, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._annotations.values()
                    if item.evidence_id == evidence_id
                ),
                key=lambda row: (row.case_id, row.annotation_version),
            )
        )

    def evidence_count(self) -> int:
        return len(self._evidence)

    def _by_id(self, evidence_id: str) -> EvidencePersistenceRecord:
        for record in self._evidence.values():
            if record.evidence_id == evidence_id:
                return record
        raise EvidencePersistenceError(f"evidence not found: {evidence_id}")


def _registry() -> EvidenceRegistry:
    return EvidenceRegistry()


def _imported(registry: EvidenceRegistry, version: str = "wms-v1") -> EvidencePersistenceRecord:
    return registry.import_evidence(
        dataset_version=version,
        source_type=SOURCE_TYPE_REAL,
        attested=True,
        method="external_process",
    )


def _module_imports(relative: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse((_REPO_ROOT / relative).read_text(encoding="utf-8-sig"))):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


# ============================================================
# 6. Evidence Persistence / 状态机
# ============================================================


class TestEvidencePersistence:
    def test_01_status_values_are_explicit_strings(self) -> None:
        assert EVIDENCE_STATUS_VALUES == (
            "IMPORTED",
            "PERSISTED",
            "ANNOTATED",
            "REVIEWED",
            "FINALIZED",
        )
        for status in EVIDENCE_STATUS_VALUES:
            assert isinstance(status, str) and status.isupper()

    def test_02_transitions_do_not_skip(self) -> None:
        assert can_transit(EVIDENCE_STATUS_IMPORTED, EVIDENCE_STATUS_PERSISTED)
        assert not can_transit(EVIDENCE_STATUS_IMPORTED, EVIDENCE_STATUS_ANNOTATED)
        assert not can_transit(EVIDENCE_STATUS_IMPORTED, EVIDENCE_STATUS_FINALIZED)
        assert EVIDENCE_STATUS_TRANSITIONS[EVIDENCE_STATUS_FINALIZED] == ()
        try:
            next_status(EVIDENCE_STATUS_IMPORTED, EVIDENCE_STATUS_FINALIZED)
        except EvidencePersistenceError:
            pass
        else:  # pragma: no cover
            raise AssertionError("status skipping must be rejected")

    def test_03_persistence_fields_match_step31_contract(self) -> None:
        fields = {field.name for field in dataclasses.fields(EvidencePersistenceRecord)}
        assert fields == set(EVIDENCE_PERSISTENCE_FIELDS)
        annotation_fields = {
            field.name for field in dataclasses.fields(AnnotationPersistenceRecord)
        }
        assert annotation_fields == set(ANNOTATION_PERSISTENCE_FIELDS)

    def test_04_no_self_invented_fields(self) -> None:
        fields = {field.name for field in dataclasses.fields(EvidencePersistenceRecord)} | {
            field.name for field in dataclasses.fields(AnnotationPersistenceRecord)
        }
        for forbidden in FORBIDDEN_PERSISTENCE_FIELDS:
            assert forbidden not in fields, forbidden

    def test_05_import_then_persist_then_read(self) -> None:
        registry = _registry()
        record = _imported(registry)
        assert record.status == EVIDENCE_STATUS_IMPORTED
        persisted = registry.mark_persisted(record.evidence_id)
        assert persisted.status == EVIDENCE_STATUS_PERSISTED
        assert registry.evidence_count() == 1


# ============================================================
# 7. Annotation Association / Provenance
# ============================================================


class TestAnnotationAssociationAndProvenance:
    def test_06_annotation_associates_by_stable_identity(self) -> None:
        registry = _registry()
        evidence = registry.mark_persisted(_imported(registry).evidence_id)
        annotation = registry.append_annotation(
            evidence_id=evidence.evidence_id,
            case_id="case_001",
            annotation_version="annotation-v1",
            annotator_id="annotator-a",
        )
        assert annotation.evidence_id == evidence.evidence_id
        assert registry.annotations_of(evidence.evidence_id) == (annotation,)

    def test_07_cross_evidence_association_rejected(self) -> None:
        registry = _registry()
        first = registry.mark_persisted(_imported(registry, "wms-v1").evidence_id)
        second = registry.mark_persisted(_imported(registry, "wms-v2").evidence_id)
        try:
            associate_annotation(
                AnnotationPersistenceRecord(
                    annotation_id="an-x",
                    evidence_id=second.evidence_id,
                    case_id="case_001",
                    annotation_version="annotation-v1",
                    annotator_id="annotator-a",
                    review_status=ANNOTATION_REVIEW_DRAFT,
                    created_at="2026-10-04T00:00:00Z",
                    updated_at="2026-10-04T00:00:00Z",
                ),
                first,
            )
        except EvidencePersistenceError:
            pass
        else:  # pragma: no cover
            raise AssertionError("cross-evidence association must be rejected")

    def test_08_provenance_reuses_step27_fields(self) -> None:
        registry = _registry()
        record = _imported(registry)
        provenance = provenance_of(record)
        assert provenance == {
            "dataset_version": "wms-v1",
            "source_type": SOURCE_TYPE_REAL,
        }

    def test_09_no_text_or_source_name_matching(self) -> None:
        """§七：关联不得依赖 content / source name 文本匹配。"""
        payload = {"content": "x", "source_name": "y"}
        for key in payload:
            assert key not in EVIDENCE_PERSISTENCE_FIELDS
            assert key not in ANNOTATION_PERSISTENCE_FIELDS


# ============================================================
# 8. Idempotency / Transaction / Isolation
# ============================================================


class TestIdempotencyTransactionIsolation:
    def test_10_repeated_import_is_idempotent(self) -> None:
        registry = _registry()
        first = _imported(registry)
        second = _imported(registry)
        third = _imported(registry)
        assert first.evidence_id == second.evidence_id == third.evidence_id
        assert registry.evidence_count() == 1

    def test_11_idempotency_key_is_stable(self) -> None:
        assert idempotency_key("wms-v1", SOURCE_TYPE_REAL) == idempotency_key(
            "wms-v1", SOURCE_TYPE_REAL
        )
        assert idempotency_key("wms-v1", SOURCE_TYPE_REAL) != idempotency_key(
            "wms-v2", SOURCE_TYPE_REAL
        )

    def test_12_attestation_missing_blocks_import(self) -> None:
        registry = _registry()
        try:
            registry.import_evidence(
                dataset_version="wms-v1",
                source_type=SOURCE_TYPE_REAL,
                attested=False,
                method="external_process",
            )
        except EvidencePersistenceError:
            pass
        else:  # pragma: no cover
            raise AssertionError("import without attestation must be rejected")

    def test_13_transaction_failure_leaves_no_orphan(self) -> None:
        registry = _registry()
        evidence = registry.mark_persisted(_imported(registry).evidence_id)
        try:
            registry.append_annotation(
                evidence_id=evidence.evidence_id,
                case_id="case_001",
                annotation_version="annotation-v1",
                annotator_id="annotator-a",
                fail=True,
            )
        except EvidencePersistenceError:
            pass
        else:  # pragma: no cover
            raise AssertionError("annotation failure must propagate")
        assert registry.annotations_of(evidence.evidence_id) == ()
        # Evidence 已存在（PERSISTED），但状态不推进 → 无半成品 annotation
        assert registry.read(evidence.evidence_id).status == EVIDENCE_STATUS_PERSISTED

    def test_14_annotation_requires_existing_evidence(self) -> None:
        registry = _registry()
        try:
            registry.append_annotation(
                evidence_id="ev-missing",
                case_id="case_001",
                annotation_version="annotation-v1",
                annotator_id="annotator-a",
            )
        except EvidencePersistenceError:
            pass
        else:  # pragma: no cover
            raise AssertionError("annotation without evidence must be rejected")

    def test_15_evidence_isolation(self) -> None:
        registry = _registry()
        first = registry.mark_persisted(_imported(registry, "wms-v1").evidence_id)
        second = registry.mark_persisted(_imported(registry, "wms-v2").evidence_id)
        registry.append_annotation(
            evidence_id=first.evidence_id,
            case_id="case_001",
            annotation_version="annotation-v1",
            annotator_id="annotator-a",
        )
        registry.append_annotation(
            evidence_id=second.evidence_id,
            case_id="case_002",
            annotation_version="annotation-v1",
            annotator_id="annotator-b",
        )
        assert len(registry.annotations_of(first.evidence_id)) == 1
        assert len(registry.annotations_of(second.evidence_id)) == 1
        assert (
            registry.annotations_of(first.evidence_id)[0].case_id
            != registry.annotations_of(second.evidence_id)[0].case_id
        )


# ============================================================
# 9. Security / Production Boundary
# ============================================================


class TestSecurityAndProductionBoundary:
    def test_16_secret_fields_rejected(self) -> None:
        for field in FORBIDDEN_SECRET_FIELDS:
            try:
                validate_no_forbidden_fields({field: "x"})
            except EvidencePersistenceError:
                continue
            raise AssertionError(f"secret field must be rejected: {field}")  # pragma: no cover

    def test_17_self_invented_fields_rejected(self) -> None:
        for field in FORBIDDEN_PERSISTENCE_FIELDS:
            try:
                validate_no_forbidden_fields({field: "x"})
            except EvidencePersistenceError:
                continue
            raise AssertionError(f"field must be rejected: {field}")  # pragma: no cover

    def test_18_offline_and_no_db(self) -> None:
        modules = _module_imports(_SELF)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for forbidden in (
                "sqlalchemy",
                "psycopg",
                "httpx",
                "requests",
                "backend.app.db",
                "backend.app.services",
            ):
                assert not module.startswith(forbidden), module

    def test_19_production_has_no_evidence_persistence_yet(self) -> None:
        """Evidence / Annotation 持久化边界快照（未登记的新文件 = 漂移）。

        Step 45 起显式区分两类：

        * **Evidence / Annotation 核心持久化**（3 个模块，Step 37 冻结）；
        * **Conversation ↔ Evidence 关联持久化**（2 个 `conversation_evidence*` 模块，
          Step 45 新增）—— 这是 **association persistence module**，
          **不属于** Evidence / Annotation 核心 persistence boundary。

        强度保持：任何未登记的 `evidence*` / `annotation*` 文件（例如
        `evidence_fake_repository.py` / `random_evidence_service.py`）仍必须 FAIL；
        关联模块也必须**恰好**等于显式登记的两个，不得借 association 之名新增。
        """
        #: Evidence / Annotation 核心持久化模块（Step 37）。
        evidence_persistence_modules: tuple[str, ...] = (
            "backend/app/db/models/evidence_record.py",
            "backend/app/db/models/evidence_annotation_record.py",
            "backend/app/db/evidence_repository.py",
        )
        #: Step 45：Conversation ↔ Evidence **关联**持久化（不是核心持久化）。
        association_persistence_modules: tuple[str, ...] = (
            "backend/app/db/models/conversation_evidence.py",
            "backend/app/db/conversation_evidence_repository.py",
        )
        backend = sorted((_REPO_ROOT / "backend/app").rglob("*.py"))
        assert backend, "backend/app 为空（审计失效）"
        matched = sorted(
            str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
            for path in backend
            if "evidence" in path.name or "annotation" in path.name
        )
        # 关联模块必须恰好等于登记列表（防止以 association 名义新增未声明文件）
        association_matched = [
            item
            for item in matched
            if Path(item).name.startswith("conversation_evidence")
        ]
        assert sorted(association_matched) == sorted(
            association_persistence_modules
        ), association_matched
        assert matched == sorted(
            evidence_persistence_modules + association_persistence_modules
        ), matched

    def test_20_deterministic_replay(self) -> None:
        first = _registry()
        record_a = _imported(first)
        second = _registry()
        record_b = _imported(second)
        assert record_a == record_b
        assert idempotency_key("wms-v1", SOURCE_TYPE_REAL) == "REAL_DEIDENTIFIED:wms-v1"


__all__ = [
    "EVIDENCE_STATUS_VALUES",
    "EVIDENCE_STATUS_TRANSITIONS",
    "EVIDENCE_PERSISTENCE_FIELDS",
    "ANNOTATION_PERSISTENCE_FIELDS",
    "FORBIDDEN_PERSISTENCE_FIELDS",
    "FORBIDDEN_SECRET_FIELDS",
    "EvidencePersistenceRecord",
    "AnnotationPersistenceRecord",
    "EvidencePersistenceError",
    "EvidenceRegistry",
    "can_transit",
    "next_status",
    "idempotency_key",
    "provenance_of",
    "validate_no_forbidden_fields",
    "associate_annotation",
]
