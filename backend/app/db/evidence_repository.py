"""Evidence / Annotation Repository（Phase 4.1 Step 37 —— 真实持久化实现）。

最小数据访问层（与 ``conversation_repository`` / ``assistant_outcome_repository``
同风格、同基础设施）：

    create_evidence(...)              插入 Evidence（**幂等**：同一 (source_type,
                                      dataset_version) 重复 import → 返回既有记录）
    get_evidence(...)                 只读：按 evidence_id（无记录 → None）
    find_by_dataset(...)              只读：按 (dataset_version, source_type)
    update_status(...)                状态迁移（**禁止跳级**）+ updated_at = now()
    create_annotation(...)            **原子**：插入 Annotation + 推进 Evidence 状态
    list_annotations(...)             按 evidence_id 列出 Annotation（case_id, version）

复用基础设施（不新增第二套机制）：

    * Session 工厂：``backend.app.db.session.get_session_factory()``
    * 事务：``with factory() as session, session.begin():``（写）·
      ``with factory() as session:``（读）
    * ORM：``EvidenceRecord`` / ``EvidenceAnnotationRecord``

事务归属（沿用 Step 4 §4 冻结的**方案 A**）：

    事务由 **Repository** 持有（``session.begin()`` 正常退出提交 / 异常回滚）；
    调用方不接触 Session / 不 commit。

失败语义：
    ``SQLAlchemyError`` → ``EvidenceRepositoryError``（事务已回滚）；
    "Evidence 不存在" → ``EvidenceNotFoundError``；
    "非法状态迁移" → ``InvalidEvidenceStatusTransitionError``。

安全边界（Step 36 §9）：本层不接受也不保存
api_key / password / authorization / database_url / connection_string /
llm_secret / token 等字段（ORM 中不存在这些列）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models.evidence_annotation_record import (
    ANNOTATION_REVIEW_DRAFT,
    ANNOTATION_REVIEW_TRANSITIONS,
    ANNOTATION_REVIEW_VALUES,
    EvidenceAnnotationRecord,
)
from backend.app.db.models.evidence_record import (
    EVIDENCE_STATUS_ANNOTATED,
    EVIDENCE_STATUS_TRANSITIONS,
    EVIDENCE_STATUS_VALUES,
    SOURCE_TYPE_VALUES,
    EvidenceRecord,
)
from backend.app.db.session import get_session_factory

__all__ = [
    "EvidenceRepository",
    "EvidenceRepositoryError",
    "EvidenceNotFoundError",
    "InvalidEvidenceStatusTransitionError",
    "AnnotationNotFoundError",
    "InvalidAnnotationReviewTransitionError",
    "EvidenceRow",
    "EvidenceProvenanceRow",
    "EvidenceWithProvenance",
    "AnnotationRow",
    "validate_provenance",
    "EVIDENCE_READ_COLUMNS",
    "ANNOTATION_READ_COLUMNS",
    "validate_evidence_id",
    "validate_dataset_version",
    "validate_source_type",
    "validate_evidence_status",
    "validate_case_id",
    "validate_annotation_version",
    "validate_annotator_id",
    "validate_review_status",
]

#: Evidence 只读查询允许返回的字段（显式列，**不用** SELECT *）。
EVIDENCE_READ_COLUMNS: Final[tuple[str, ...]] = (
    "evidence_id",
    "dataset_version",
    "source_type",
    "de_identification_attested",
    "de_identification_method",
    "status",
    "created_at",
    "updated_at",
)

#: Annotation 只读查询允许返回的字段。
ANNOTATION_READ_COLUMNS: Final[tuple[str, ...]] = (
    "annotation_id",
    "evidence_id",
    "case_id",
    "annotation_version",
    "annotator_id",
    "review_status",
    "created_at",
    "updated_at",
)

_MAX_ID_LENGTH: Final[int] = 128
_MAX_VERSION_LENGTH: Final[int] = 64


@dataclass(frozen=True)
class EvidenceRow:
    """Evidence 行（**不是** ORM 对象；字段 = :data:`EVIDENCE_READ_COLUMNS`）。"""

    evidence_id: str
    dataset_version: str
    source_type: str
    de_identification_attested: bool
    de_identification_method: str | None
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AnnotationRow:
    """Annotation 行（**不是** ORM 对象）。"""

    annotation_id: str
    evidence_id: str
    case_id: str
    annotation_version: str
    annotator_id: str
    review_status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class EvidenceProvenanceRow:
    """EvidenceProvenance 读取模型（**严格复用 Step 27 冻结的两个字段**）。

    Step 27 契约：``EvidenceProvenance`` 只含 ``dataset_version`` + ``source_type``；
    不得新增 source_ref / content_ref / locator / file_path / raw_content / url。
    """

    dataset_version: str
    source_type: str

    def as_dict(self) -> dict[str, str]:
        return {
            "dataset_version": self.dataset_version,
            "source_type": self.source_type,
        }


@dataclass(frozen=True)
class EvidenceWithProvenance:
    """Evidence + Provenance 读取模型（不含 Session / ORM / SQL 对象）。"""

    evidence_id: str
    dataset_version: str
    source_type: str
    de_identification_attested: bool
    de_identification_method: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    provenance: EvidenceProvenanceRow


class EvidenceRepositoryError(Exception):
    """Evidence 持久化 / 读取失败（事务已回滚）。"""


class EvidenceNotFoundError(EvidenceRepositoryError):
    """Evidence 不存在（Annotation 不得成为孤儿）。"""


class InvalidEvidenceStatusTransitionError(EvidenceRepositoryError):
    """非法状态迁移（禁止跳级）。"""


class AnnotationNotFoundError(EvidenceRepositoryError):
    """Annotation 不存在（评审迁移不得凭空创建 Annotation）。"""


class InvalidAnnotationReviewTransitionError(EvidenceRepositoryError):
    """非法 Annotation 评审迁移（Step 41：DRAFT → REVIEWED 单向；REVIEWED 为终态）。"""


def _require_text(value: object, name: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} 必须是 str（当前: {type(value).__name__}）")
    if not value.strip():
        raise ValueError(f"{name} 不能为空或纯空白")
    if len(value) > max_length:
        raise ValueError(f"{name} 超出长度上限（{len(value)} > {max_length}）")
    return value


def validate_evidence_id(value: object) -> str:
    return _require_text(value, "evidence_id", _MAX_ID_LENGTH)


def validate_dataset_version(value: object) -> str:
    return _require_text(value, "dataset_version", _MAX_ID_LENGTH)


def validate_source_type(value: object) -> str:
    text = _require_text(value, "source_type", 32)
    if text not in SOURCE_TYPE_VALUES:
        raise ValueError(f"source_type 非法: {text!r}（允许: {SOURCE_TYPE_VALUES}）")
    return text


def validate_evidence_status(value: object) -> str:
    text = _require_text(value, "status", 32)
    if text not in EVIDENCE_STATUS_VALUES:
        raise ValueError(f"status 非法: {text!r}（允许: {EVIDENCE_STATUS_VALUES}）")
    return text


def validate_case_id(value: object) -> str:
    return _require_text(value, "case_id", _MAX_ID_LENGTH)


def validate_annotation_version(value: object) -> str:
    return _require_text(value, "annotation_version", _MAX_VERSION_LENGTH)


def validate_annotator_id(value: object) -> str:
    return _require_text(value, "annotator_id", _MAX_ID_LENGTH)


def validate_review_status(value: object) -> str:
    text = _require_text(value, "review_status", 32)
    if text not in ANNOTATION_REVIEW_VALUES:
        raise ValueError(
            f"review_status 非法: {text!r}（允许: {ANNOTATION_REVIEW_VALUES}）"
        )
    return text


class EvidenceRepository:
    """``ai_ops.evidence_record`` / ``ai_ops.evidence_annotation_record`` 的最小仓储。"""

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._session_factory = session_factory

    # ---------- 依赖解析 ----------

    def _factory(self) -> sessionmaker[Session]:
        if self._session_factory is not None:
            return self._session_factory
        factory = get_session_factory()
        if factory is None:
            raise EvidenceRepositoryError(
                "DATABASE_URL 未配置，无法访问 Evidence 持久化层"
            )
        return factory

    # ---------- SQL 构造点 ----------

    @staticmethod
    def _evidence_columns() -> tuple[Any, ...]:
        return tuple(getattr(EvidenceRecord, name) for name in EVIDENCE_READ_COLUMNS)

    @staticmethod
    def _annotation_columns() -> tuple[Any, ...]:
        return tuple(
            getattr(EvidenceAnnotationRecord, name)
            for name in ANNOTATION_READ_COLUMNS
        )

    def build_evidence_select(self, evidence_id: str) -> Any:
        return select(*self._evidence_columns()).where(
            EvidenceRecord.evidence_id == evidence_id
        )

    def build_evidence_insert(
        self,
        *,
        evidence_id: str,
        dataset_version: str,
        source_type: str,
        de_identification_attested: bool,
        de_identification_method: str | None,
    ) -> Any:
        return (
            insert(EvidenceRecord)
            .values(
                evidence_id=evidence_id,
                dataset_version=dataset_version,
                source_type=source_type,
                de_identification_attested=de_identification_attested,
                de_identification_method=de_identification_method,
            )
            .returning(*self._evidence_columns())
        )

    def build_status_update(self, evidence_id: str, status: str) -> Any:
        return (
            update(EvidenceRecord)
            .where(EvidenceRecord.evidence_id == evidence_id)
            .values(status=status, updated_at=func.now())
            .returning(*self._evidence_columns())
        )

    def build_annotation_insert(
        self,
        *,
        annotation_id: str,
        evidence_id: str,
        case_id: str,
        annotation_version: str,
        annotator_id: str,
        review_status: str,
    ) -> Any:
        return (
            insert(EvidenceAnnotationRecord)
            .values(
                annotation_id=annotation_id,
                evidence_id=evidence_id,
                case_id=case_id,
                annotation_version=annotation_version,
                annotator_id=annotator_id,
                review_status=review_status,
            )
            .returning(*self._annotation_columns())
        )

    def build_annotation_select(self, evidence_id: str) -> Any:
        return (
            select(*self._annotation_columns())
            .where(EvidenceAnnotationRecord.evidence_id == evidence_id)
            .order_by(
                EvidenceAnnotationRecord.case_id,
                EvidenceAnnotationRecord.annotation_version,
            )
        )

    def build_annotation_select_by_id(self, annotation_id: str) -> Any:
        """按主键读取单条 Annotation（Step 41：评审前加载 + 读回校验）。"""
        return (
            select(*self._annotation_columns()).where(
                EvidenceAnnotationRecord.annotation_id == annotation_id
            )
        )

    def build_annotation_review_update(
        self, annotation_id: str, review_status: str
    ) -> Any:
        """只更新 ``review_status`` + ``updated_at``（**不触碰**其余字段）。"""
        return (
            update(EvidenceAnnotationRecord)
            .where(EvidenceAnnotationRecord.annotation_id == annotation_id)
            .values(review_status=review_status, updated_at=func.now())
            .returning(*self._annotation_columns())
        )

    # ---------- 读 ----------

    def get_evidence(self, evidence_id: str) -> EvidenceRow | None:
        validate_evidence_id(evidence_id)
        factory = self._factory()
        try:
            with factory() as session:
                row = session.execute(self.build_evidence_select(evidence_id)).first()
        except SQLAlchemyError as exc:
            raise EvidenceRepositoryError(f"读取 Evidence 失败: {exc}") from exc
        return EvidenceRow(*row) if row is not None else None

    def find_by_dataset(
        self, dataset_version: str, source_type: str
    ) -> EvidenceRow | None:
        validate_dataset_version(dataset_version)
        validate_source_type(source_type)
        factory = self._factory()
        try:
            with factory() as session:
                row = session.execute(
                    select(*self._evidence_columns()).where(
                        EvidenceRecord.dataset_version == dataset_version,
                        EvidenceRecord.source_type == source_type,
                    )
                ).first()
        except SQLAlchemyError as exc:
            raise EvidenceRepositoryError(f"按 dataset 查询失败: {exc}") from exc
        return EvidenceRow(*row) if row is not None else None

    def list_annotations(self, evidence_id: str) -> tuple[AnnotationRow, ...]:
        validate_evidence_id(evidence_id)
        factory = self._factory()
        try:
            with factory() as session:
                rows = session.execute(
                    self.build_annotation_select(evidence_id)
                ).all()
        except SQLAlchemyError as exc:
            raise EvidenceRepositoryError(f"列出 Annotation 失败: {exc}") from exc
        return tuple(AnnotationRow(*row) for row in rows)

    # ---------- 写（事务由 Repository 持有）----------

    def create_evidence(
        self,
        *,
        evidence_id: str,
        dataset_version: str,
        source_type: str,
        de_identification_attested: bool,
        de_identification_method: str | None = None,
        status: str | None = None,
    ) -> EvidenceRow:
        """插入 Evidence；**幂等**：同 (source_type, dataset_version) 返回既有记录。"""
        validate_evidence_id(evidence_id)
        # Step 38：provenance（dataset_version + source_type）必填且合法；缺失 → 拒绝
        self.validate_provenance(dataset_version, source_type)
        target_status = validate_evidence_status(
            status or EVIDENCE_STATUS_VALUES[0]
        )
        factory = self._factory()
        try:
            with factory() as session, session.begin():
                existing = session.execute(
                    select(*self._evidence_columns()).where(
                        EvidenceRecord.source_type == source_type,
                        EvidenceRecord.dataset_version == dataset_version,
                    )
                ).first()
                if existing is not None:  # first-write-wins（DB 唯一约束兜底）
                    return EvidenceRow(*existing)
                row = session.execute(
                    self.build_evidence_insert(
                        evidence_id=evidence_id,
                        dataset_version=dataset_version,
                        source_type=source_type,
                        de_identification_attested=bool(
                            de_identification_attested
                        ),
                        de_identification_method=de_identification_method,
                    )
                ).first()
                if row is None:  # pragma: no cover
                    raise EvidenceRepositoryError("Evidence 插入未返回结果")
                inserted = EvidenceRow(*row)
            if inserted.status != target_status:
                return self.update_status(inserted.evidence_id, target_status)
            return inserted
        except SQLAlchemyError as exc:
            raise EvidenceRepositoryError(f"写入 Evidence 失败: {exc}") from exc

    def update_status(self, evidence_id: str, target_status: str) -> EvidenceRow:
        """状态迁移（禁止跳级）；无记录 → EvidenceNotFoundError。"""
        validate_evidence_id(evidence_id)
        validate_evidence_status(target_status)
        factory = self._factory()
        try:
            with factory() as session, session.begin():
                current = session.execute(
                    self.build_evidence_select(evidence_id)
                ).first()
                if current is None:
                    raise EvidenceNotFoundError(f"Evidence 不存在: {evidence_id}")
                current_status = str(current[5])
                if target_status not in EVIDENCE_STATUS_TRANSITIONS[current_status]:
                    raise InvalidEvidenceStatusTransitionError(
                        f"非法状态迁移: {current_status} -> {target_status}"
                    )
                row = session.execute(
                    self.build_status_update(evidence_id, target_status)
                ).first()
                if row is None:  # pragma: no cover
                    raise EvidenceRepositoryError("状态迁移未返回结果")
                return EvidenceRow(*row)
        except SQLAlchemyError as exc:
            raise EvidenceRepositoryError(f"状态迁移失败: {exc}") from exc

    def create_annotation(
        self,
        *,
        annotation_id: str,
        evidence_id: str,
        case_id: str,
        annotation_version: str,
        annotator_id: str,
        review_status: str = ANNOTATION_REVIEW_DRAFT,
    ) -> AnnotationRow:
        """**原子**：插入 Annotation + 推进 Evidence 状态（PERSISTED → ANNOTATED）。

        任一环节失败 → 事务回滚：**不产生孤儿 Annotation**，Evidence 状态不推进。
        """
        validate_evidence_id(annotation_id)
        validate_evidence_id(evidence_id)
        validate_case_id(case_id)
        validate_annotation_version(annotation_version)
        validate_annotator_id(annotator_id)
        validate_review_status(review_status)
        factory = self._factory()
        try:
            with factory() as session, session.begin():
                evidence = session.execute(
                    self.build_evidence_select(evidence_id)
                ).first()
                if evidence is None:
                    raise EvidenceNotFoundError(f"Evidence 不存在: {evidence_id}")
                dataset_version = str(evidence[1])
                if annotation_version == dataset_version:
                    raise EvidenceRepositoryError(
                        "annotation_version 必须与 dataset_version 不同"
                    )
                row = session.execute(
                    self.build_annotation_insert(
                        annotation_id=annotation_id,
                        evidence_id=evidence_id,
                        case_id=case_id,
                        annotation_version=annotation_version,
                        annotator_id=annotator_id,
                        review_status=review_status,
                    )
                ).first()
                if row is None:  # pragma: no cover
                    raise EvidenceRepositoryError("Annotation 插入未返回结果")
                # 状态推进：PERSISTED → ANNOTATED（已是 ANNOTATED 之后的状态则跳过）
                current_status = str(evidence[5])
                if (
                    EVIDENCE_STATUS_ANNOTATED
                    in EVIDENCE_STATUS_TRANSITIONS[current_status]
                ):
                    session.execute(
                        self.build_status_update(
                            evidence_id, EVIDENCE_STATUS_ANNOTATED
                        )
                    )
                return AnnotationRow(*row)
        except SQLAlchemyError as exc:
            raise EvidenceRepositoryError(f"写入 Annotation 失败: {exc}") from exc

    # ---------- Annotation Review Transition（Step 41）----------

    def get_annotation(self, annotation_id: str) -> AnnotationRow | None:
        """按主键读取 Annotation（不存在 → ``None``；与 ``get_evidence`` 同风格）。"""
        validate_evidence_id(annotation_id)
        factory = self._factory()
        try:
            with factory() as session:
                row = session.execute(
                    self.build_annotation_select_by_id(annotation_id)
                ).first()
        except SQLAlchemyError as exc:
            raise EvidenceRepositoryError(f"读取 Annotation 失败: {exc}") from exc
        return AnnotationRow(*row) if row is not None else None

    def update_annotation_review_status(
        self, annotation_id: str, review_status: str
    ) -> AnnotationRow:
        """**Annotation-only** 评审迁移：``DRAFT → REVIEWED``。

        Step 41 边界（不得越界）：

        * 只变更 ``review_status`` + ``updated_at``；
          ``evidence_id`` / ``case_id`` / ``annotation_version`` / ``annotator_id``
          / ``created_at`` 一律保持不变；
        * **不联动** Evidence 状态 ——
          ``PERSISTED → ANNOTATED`` 与 ``ANNOTATED → REVIEWED`` 均属 Step 43，
          本方法绝不推进 Evidence；
        * **先验证、后写入**：非法迁移 / 未知状态 / Annotation 不存在
          → 抛错且数据库状态不变（禁止"先 UPDATE 再验证"）。

        事务边界沿用 Step 37/40：Repository owns transaction
        （``with factory() as session, session.begin():``）。
        """
        validate_evidence_id(annotation_id)
        validate_review_status(review_status)
        factory = self._factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(
                    self.build_annotation_select_by_id(annotation_id)
                ).first()
                if row is None:
                    raise AnnotationNotFoundError(
                        f"Annotation 不存在: {annotation_id}"
                    )
                current_status = str(row[5])
                if review_status not in ANNOTATION_REVIEW_TRANSITIONS[current_status]:
                    raise InvalidAnnotationReviewTransitionError(
                        f"非法 Annotation 评审迁移: {current_status} -> {review_status}"
                    )
                updated = session.execute(
                    self.build_annotation_review_update(annotation_id, review_status)
                ).first()
                if updated is None:  # pragma: no cover
                    raise EvidenceRepositoryError("Annotation 评审更新未返回结果")
                return AnnotationRow(*updated)
        except SQLAlchemyError as exc:
            raise EvidenceRepositoryError(
                f"更新 Annotation 评审状态失败: {exc}"
            ) from exc

    # ---------- Provenance（Step 38：复用 Step 27 契约；与 Evidence 同行持久化）----------

    @staticmethod
    def validate_provenance(dataset_version: str, source_type: str) -> None:
        """校验 Provenance（Step 27 规则；**不自动补值**）。

        * dataset_version：非空 且 stable（无前后空白）；
        * source_type：必须属于 ``SOURCE_TYPE_VALUES``；
        * 缺失 / 非法 → 拒绝持久化（禁止用 unknown / default / fake 替代）。
        """
        version = validate_dataset_version(dataset_version)
        if version != version.strip():
            raise ValueError("dataset_version must be stable (no padding)")
        validate_source_type(source_type)

    def get_by_id(self, evidence_id: str) -> EvidenceWithProvenance | None:
        """按 evidence_id 读取 Evidence **及其 Provenance**（不暴露 Session / ORM）。"""
        validate_evidence_id(evidence_id)
        factory = self._factory()
        try:
            with factory() as session:
                row = session.execute(self.build_evidence_select(evidence_id)).first()
        except SQLAlchemyError as exc:
            raise EvidenceRepositoryError(f"读取 Evidence 失败: {exc}") from exc
        if row is None:
            return None
        evidence = EvidenceRow(*row)
        return EvidenceWithProvenance(
            evidence_id=evidence.evidence_id,
            dataset_version=evidence.dataset_version,
            source_type=evidence.source_type,
            de_identification_attested=evidence.de_identification_attested,
            de_identification_method=evidence.de_identification_method,
            status=evidence.status,
            created_at=evidence.created_at,
            updated_at=evidence.updated_at,
            provenance=EvidenceProvenanceRow(
                dataset_version=evidence.dataset_version,
                source_type=evidence.source_type,
            ),
        )

    def get_provenance(self, evidence_id: str) -> EvidenceProvenanceRow | None:
        """只读取 Provenance（dataset_version + source_type）。"""
        found = self.get_by_id(evidence_id)
        return None if found is None else found.provenance
