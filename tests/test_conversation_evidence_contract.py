"""Conversation ↔ Evidence Integration Contract —— 边界审计（Phase 4.1 Step 44）。

**本文件不建立任何关联、不建表、不加列、不加 FK** —— Step 44 交付物是 Contract。
本测试只**锁定身份边界**，防止未来 Step 在未冻结契约前误把对方 ID 写入模型：

```text
Evidence 不得出现：conversation_id / turn_id / assistant_request_id / provider_request_id
Conversation 不得出现：evidence_id / annotation_id
ConversationTurn 不得出现：evidence_id
Evidence 状态值域 ∩ Conversation 状态值域 = ∅（两个状态机独立）
```

契约依据：

* Step 27（Real WMS Evidence Provenance & Version Freeze）§十二 Artifact Identity：
  **禁止** `conversation_id / assistant_request_id / turn_id / provider_request_id`
  作为 Evidence Artifact Identity；继续以 `case_id` 为 Evidence 内样本标识；
* Step 27 Production ID Isolation：上述生产 ID **不得进入 Provenance**；
* Step 38：Provenance = `dataset_version + source_type`（无独立表）；
* Roadmap v1.1 §七 R4：Evidence = **Reusable**、Owner = Evidence 自有、
  Conversation → Evidence = **REFERENCE（非 OWNERSHIP）**。

纯离线（无 DB / 无 LLM / 无网络）：仅读取 SQLAlchemy 模型元数据与常量。
"""
from __future__ import annotations

import pytest

from backend.app.db.models.conversation import (
    CONVERSATION_STATUS_ACTIVE,
    CONVERSATION_STATUS_ARCHIVED,
    Conversation,
)
from backend.app.db.models.conversation_turn import ConversationTurn
from backend.app.db.models.evidence_annotation_record import EvidenceAnnotationRecord
from backend.app.db.models.evidence_record import (
    EVIDENCE_STATUS_VALUES,
    EvidenceRecord,
)

#: Step 27 冻结：禁止进入 Evidence 的生产/会话标识。
_FORBIDDEN_IN_EVIDENCE: tuple[str, ...] = (
    "conversation_id",
    "turn_id",
    "assistant_request_id",
    "provider_request_id",
)

#: 禁止进入 Conversation / ConversationTurn 的 Evidence 域标识。
_FORBIDDEN_IN_CONVERSATION: tuple[str, ...] = (
    "evidence_id",
    "annotation_id",
    "dataset_version",
    "source_type",
)


def _columns(model: type) -> set[str]:
    return {column.name for column in model.__table__.columns}


def _foreign_key_targets(model: type) -> set[str]:
    return {
        foreign_key.target_fullname
        for foreign_key in model.__table__.foreign_keys
    }


# ============================================================
# Q5：Identity Boundary —— Evidence 侧
# ============================================================


def test_evidence_has_no_conversation_identity() -> None:
    columns = _columns(EvidenceRecord)
    for forbidden in _FORBIDDEN_IN_EVIDENCE:
        assert forbidden not in columns, f"evidence_record 不得包含 {forbidden}"


def test_annotation_has_no_conversation_identity() -> None:
    columns = _columns(EvidenceAnnotationRecord)
    for forbidden in _FORBIDDEN_IN_EVIDENCE:
        assert forbidden not in columns, f"evidence_annotation_record 不得包含 {forbidden}"


def test_evidence_has_single_foreign_key_only() -> None:
    """Evidence 当前**不**指向 Conversation（无任何会话侧外键）。"""
    assert _foreign_key_targets(EvidenceRecord) == set()


def test_annotation_foreign_key_points_to_evidence_only() -> None:
    """Annotation 的唯一外键指向 Evidence（Step 39 已冻结），不得指向会话侧。"""
    targets = _foreign_key_targets(EvidenceAnnotationRecord)
    assert len(targets) == 1
    assert "evidence_record" in next(iter(targets))


# ============================================================
# Q5：Identity Boundary —— Conversation 侧
# ============================================================


def test_conversation_has_no_evidence_identity() -> None:
    columns = _columns(Conversation)
    for forbidden in _FORBIDDEN_IN_CONVERSATION:
        assert forbidden not in columns, f"conversation 不得包含 {forbidden}"


def test_conversation_turn_has_no_evidence_identity() -> None:
    columns = _columns(ConversationTurn)
    for forbidden in ("evidence_id", "annotation_id", "dataset_version"):
        assert forbidden not in columns, f"conversation_turn 不得包含 {forbidden}"
    # assistant_request_id 允许存在，但它只是 correlation identifier（非 Evidence identity）
    assert "assistant_request_id" in columns
    assert _foreign_key_targets(ConversationTurn) == {"ai_ops.conversation.conversation_id"}


# ============================================================
# Q4 / Q6：生命周期互不耦合（状态机独立）
# ============================================================


def test_status_domains_are_disjoint() -> None:
    conversation_statuses = {CONVERSATION_STATUS_ACTIVE, CONVERSATION_STATUS_ARCHIVED}
    assert conversation_statuses.isdisjoint(set(EVIDENCE_STATUS_VALUES))


def test_evidence_provenance_columns_are_only_dataset_and_source() -> None:
    """Step 38：Provenance = dataset_version + source_type（无独立表、无会话字段）。"""
    columns = _columns(EvidenceRecord)
    assert {"dataset_version", "source_type"} <= columns
    assert "source_ref" not in columns
    assert "content_ref" not in columns
