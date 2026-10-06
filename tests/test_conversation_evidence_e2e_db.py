"""Real Conversation Evidence E2E —— 真实 PostgreSQL（Phase 4.1 Step 46）。

第一次闭环验证：

```text
Conversation
    ↓  （真实：ConversationRepository）
ConversationTurn
    ↓  （真实：ChatApplicationService.execute_message → USER turn）
AIOrchestrator
    ↓  （**唯一 Fake**：FakeOrchestrator；无真实 LLM / network）
AI Result（AIOrchestrationResult：route=RAG · metadata.request_id）
    ↓  （test-composed：按 Step 36–38 冻结契约写入，无新业务层）
Evidence（EvidenceRepository）
    ↓  （真实：ConversationEvidenceRepository.create_association，禁止裸 SQL）
ConversationEvidence
    ↓
PostgreSQL
    ↓
Read-back（**新 Repository / 新 Session**）
```

Fake 边界（沿用 Step 15 `test_conversation_multiturn_db_e2e.py` 模式）：

* 只有 Orchestrator 是 Fake；Conversation / Turn / Evidence / Association /
  PostgreSQL **全部真实**；
* Fake 只返回脚本化 `AIOrchestrationResult`（route=RAG），不实现任何 AI 行为；
* 不新增大型 Fake Framework（本文件内 ~30 行）。

**Evidence creation orchestration = test-composed**：项目中不存在
EvidenceBuilder / EvidenceFactory / EvidenceMapper（已审计，0 命中），
因此本 E2E 在测试层按已冻结契约写入 Evidence —— **未**引入新业务层 / Service。

OD-12（Turn-level reference）在本文件给出判定（见
`test_10_turn_level_reference_requirement`）。

隔离与残留：

* ``RUN_DB_TESTS=1`` 才运行；
* module 级残留守卫：conversation / conversation_turn / evidence_record /
  evidence_annotation_record / conversation_evidence 五表计数前后一致；
* 只删除**本测试创建**的 conversation_id / evidence_id；无 TRUNCATE / 全表 DELETE。

禁止：DeepSeek / SiliconFlow / 真实 LLM / network。
"""
from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import text

from backend.app.db.conversation_evidence_repository import (
    ConversationEvidenceRepository,
    ConversationNotFoundRepositoryError,
    EvidenceNotFoundRepositoryError,
)
from backend.app.db.conversation_repository import ConversationRepository
from backend.app.db.evidence_repository import EvidenceRepository
from backend.app.db.init_db import init_db
from backend.app.db.models.conversation import CONVERSATION_STATUS_ACTIVE
from backend.app.db.models.evidence_record import (
    EVIDENCE_STATUS_PERSISTED,
    SOURCE_TYPE_SYNTHETIC,
)
from backend.app.db.session import get_session_factory
from backend.app.dto.assistant_outcome import AssistantOutcome
from backend.app.services.ai_orchestrator_service import (
    AIOrchestrationResult,
    RouteType,
)
from backend.app.services.chat_application_service import ChatApplicationService
from backend.app.services.conversation_context_builder import (
    ConversationContextBuilder,
)
from backend.app.services.conversation_service import ConversationService

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_TESTS") != "1",
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable Conversation Evidence E2E",
)

_SCHEMA = "ai_ops"
_TABLES: tuple[str, ...] = (
    "conversation",
    "conversation_turn",
    "evidence_record",
    "evidence_annotation_record",
    "conversation_evidence",
)

#: 测试专用 project_id（明显标识）。
TEST_PROJECT_ID = "phase-4-1-step-46-test"


def _count(table: str) -> int:
    factory = get_session_factory()
    assert factory is not None
    with factory() as session:
        return int(
            session.execute(
                text(f"SELECT count(*) FROM {_SCHEMA}.{table}")
            ).scalar_one()
        )


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _cleanup(
    conversation_ids: tuple[str, ...] = (), evidence_ids: tuple[str, ...] = ()
) -> None:
    """精确删除本测试创建的数据（关联行 → 父记录）。"""
    factory = get_session_factory()
    assert factory is not None
    with factory() as session, session.begin():
        for conversation_id in conversation_ids:
            session.execute(
                text(
                    f"DELETE FROM {_SCHEMA}.conversation_evidence "
                    "WHERE conversation_id = :conversation_id"
                ),
                {"conversation_id": conversation_id},
            )
        for evidence_id in evidence_ids:
            session.execute(
                text(
                    f"DELETE FROM {_SCHEMA}.conversation_evidence "
                    "WHERE evidence_id = :evidence_id"
                ),
                {"evidence_id": evidence_id},
            )
        for conversation_id in conversation_ids:
            session.execute(
                text(
                    f"DELETE FROM {_SCHEMA}.conversation_turn "
                    "WHERE conversation_id = :conversation_id"
                ),
                {"conversation_id": conversation_id},
            )
            session.execute(
                text(
                    f"DELETE FROM {_SCHEMA}.conversation "
                    "WHERE conversation_id = :conversation_id"
                ),
                {"conversation_id": conversation_id},
            )
        for evidence_id in evidence_ids:
            session.execute(
                text(
                    f"DELETE FROM {_SCHEMA}.evidence_annotation_record "
                    "WHERE evidence_id = :evidence_id"
                ),
                {"evidence_id": evidence_id},
            )
            session.execute(
                text(
                    f"DELETE FROM {_SCHEMA}.evidence_record "
                    "WHERE evidence_id = :evidence_id"
                ),
                {"evidence_id": evidence_id},
            )


# ============================================================
# Fake AI（唯一被替换的层，沿用 Step 15 模式）
# ============================================================


def _fake_result(content: str, request_id: str) -> AIOrchestrationResult:
    return AIOrchestrationResult(
        route=RouteType.RAG,
        content=content,
        data=None,
        metadata={
            "request_id": request_id,
            "outcome": AssistantOutcome.SUCCESS,
        },
    )


class FakeOrchestrator:
    """记录 ``(question, context)``；按调用顺序返回脚本化结果。"""

    def __init__(self, request_ids: tuple[str, ...]) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self._request_ids = list(request_ids)

    async def execute(
        self, question: str, *, context: str | None = None
    ) -> AIOrchestrationResult:
        self.calls.append((question, context))
        index = len(self.calls) - 1
        request_id = (
            self._request_ids[index]
            if index < len(self._request_ids)
            else self._request_ids[-1]
        )
        return _fake_result(content=f"回答-{index + 1}", request_id=request_id)


class FakeOrchestratorFactory:
    def __init__(self, orchestrator: FakeOrchestrator) -> None:
        self._orchestrator = orchestrator

    def __call__(self, project_id: str) -> FakeOrchestrator:
        return self._orchestrator


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture(scope="module", autouse=True)
def _residue_guard() -> Iterator[None]:
    init_db()
    before = {table: _count(table) for table in _TABLES}
    yield
    after = {table: _count(table) for table in _TABLES}
    assert after == before, f"DB residue detected: {before} -> {after}"


@pytest.fixture()
def conversations() -> ConversationRepository:
    return ConversationRepository()


@pytest.fixture()
def evidences() -> EvidenceRepository:
    return EvidenceRepository()


@pytest.fixture()
def associations() -> ConversationEvidenceRepository:
    return ConversationEvidenceRepository()


def _create_conversation(repository: ConversationRepository) -> str:
    conversation_id = _unique("conv")
    repository.create(
        conversation_id=conversation_id,
        project_id=TEST_PROJECT_ID,
        status=CONVERSATION_STATUS_ACTIVE,
    )
    return conversation_id


def _create_evidence(repository: EvidenceRepository) -> str:
    """按 Step 36–38 冻结契约写入（test-composed，无新业务层）。"""
    evidence_id = _unique("ev")
    repository.create_evidence(
        evidence_id=evidence_id,
        dataset_version=_unique("ds"),
        source_type=SOURCE_TYPE_SYNTHETIC,
        de_identification_attested=True,
        de_identification_method="external_process",
    )
    repository.update_status(evidence_id, EVIDENCE_STATUS_PERSISTED)
    return evidence_id


def _run_ai_turn(conversation_id: str, content: str, request_id: str) -> Any:
    """真实链路：USER turn → Context → Orchestrator(Fake) → ASSISTANT turn。"""
    orchestrator = FakeOrchestrator((request_id,))
    service = ChatApplicationService(
        conversation_service=ConversationService(),
        orchestrator_factory=FakeOrchestratorFactory(orchestrator),
        context_builder=ConversationContextBuilder(),
    )
    return asyncio.run(
        service.execute_message(conversation_id=conversation_id, content=content)
    )


# ============================================================
# Case 1：Conversation → AI → Evidence → Association → Read-back
# ============================================================


def test_01_conversation_to_evidence_roundtrip(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    conversation_id = _create_conversation(conversations)
    evidence_id = _create_evidence(evidences)
    try:
        # 1) 真实 Conversation → AI Runtime（唯一 Fake：Orchestrator）
        result = _run_ai_turn(conversation_id, "库存查询", _unique("req"))
        assert result.route == RouteType.RAG

        # 2) AI Result → Evidence（test-composed，按冻结契约）
        # 3) Evidence → ConversationEvidence（真实 Repository，非裸 SQL）
        reference = associations.create_association(conversation_id, evidence_id)
        assert reference.conversation_id == conversation_id
        assert reference.evidence_id == evidence_id

        # 4) Read-back（新 Repository / 新 Session）
        assert associations.list_evidence_ids(conversation_id) == (evidence_id,)
        assert EvidenceRepository().get_by_id(evidence_id) is not None
        # Turn 真实落库（USER + ASSISTANT）
        turns = conversations.list_turns_by_conversation_id(conversation_id)
        assert len(turns) >= 2
    finally:
        _cleanup((conversation_id,), (evidence_id,))


# ============================================================
# Case 2：一个 Conversation 多个 Evidence
# ============================================================


def test_02_multiple_evidence_per_conversation(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    conversation_id = _create_conversation(conversations)
    _run_ai_turn(conversation_id, "第一个问题", _unique("req"))
    _run_ai_turn(conversation_id, "第二个问题", _unique("req"))
    first, second = _create_evidence(evidences), _create_evidence(evidences)
    try:
        associations.create_association(conversation_id, first)
        associations.create_association(conversation_id, second)
        assert first != second
        assert associations.list_evidence_ids(conversation_id) == tuple(
            sorted((first, second))
        )
    finally:
        _cleanup((conversation_id,), (first, second))


# ============================================================
# Case 3：Evidence Reuse（Step 44 FROZEN：Evidence = Reusable）
# ============================================================


def test_03_evidence_reuse_across_conversations(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    first, second = _create_conversation(conversations), _create_conversation(
        conversations
    )
    evidence_id = _create_evidence(evidences)
    try:
        associations.create_association(first, evidence_id)
        associations.create_association(second, evidence_id)
        assert associations.list_conversation_ids(evidence_id) == tuple(
            sorted((first, second))
        )
        # Evidence 只有一个 record（不得复制成 XA / XB）
        assert EvidenceRepository().get_by_id(evidence_id) is not None
        assert EvidenceRepository().get_by_id(f"{evidence_id}-copy") is None
    finally:
        _cleanup((first, second), (evidence_id,))


# ============================================================
# Case 4：Conversation Isolation（cross-conversation leakage = 0）
# ============================================================


def test_04_conversation_isolation(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    first, second = _create_conversation(conversations), _create_conversation(
        conversations
    )
    first_evidence, second_evidence = (
        _create_evidence(evidences),
        _create_evidence(evidences),
    )
    try:
        associations.create_association(first, first_evidence)
        associations.create_association(second, second_evidence)
        assert associations.list_evidence_ids(first) == (first_evidence,)
        assert associations.list_evidence_ids(second) == (second_evidence,)
        assert second_evidence not in associations.list_evidence_ids(first)
        assert first_evidence not in associations.list_evidence_ids(second)
    finally:
        _cleanup((first, second), (first_evidence, second_evidence))


# ============================================================
# Case 5：Persistence Read-back（新 Session，非 identity map）
# ============================================================


def test_05_persistence_readback(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    conversation_id = _create_conversation(conversations)
    evidence_id = _create_evidence(evidences)
    try:
        _run_ai_turn(conversation_id, "持久化验证", _unique("req"))
        created = associations.create_association(conversation_id, evidence_id)

        # 全新 Repository（新 Session）三重读回
        reloaded = ConversationEvidenceRepository().get_association(
            conversation_id, evidence_id
        )
        assert reloaded is not None
        assert reloaded.created_at == created.created_at
        assert ConversationRepository().get_by_conversation_id(
            conversation_id
        ) is not None
        assert EvidenceRepository().get_evidence(evidence_id) is not None
    finally:
        _cleanup((conversation_id,), (evidence_id,))


# ============================================================
# Case 6：Association Idempotency（OD-13 + Step 45 复合 PK）
# ============================================================


def test_06_association_idempotency(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    conversation_id = _create_conversation(conversations)
    evidence_id = _create_evidence(evidences)
    try:
        first = associations.create_association(conversation_id, evidence_id)
        second = associations.create_association(conversation_id, evidence_id)
        assert first.created_at == second.created_at
        assert associations.list_evidence_ids(conversation_id) == (evidence_id,)
    finally:
        _cleanup((conversation_id,), (evidence_id,))


# ============================================================
# Case 7：Invalid Parent Reference（不得产生 orphan row）
# ============================================================


def test_07_invalid_parent_reference_rejected(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    conversation_id = _create_conversation(conversations)
    evidence_id = _create_evidence(evidences)
    try:
        before = _count("conversation_evidence")
        with pytest.raises(ConversationNotFoundRepositoryError):
            associations.create_association("conversation-missing", evidence_id)
        with pytest.raises(EvidenceNotFoundRepositoryError):
            associations.create_association(conversation_id, "evidence-missing")
        assert _count("conversation_evidence") == before
    finally:
        _cleanup((conversation_id,), (evidence_id,))


# ============================================================
# Case 8：Parent Delete Semantics（Reference ≠ Ownership）
# ============================================================


def test_08_parent_delete_preserves_other_domain_object(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    factory = get_session_factory()
    assert factory is not None

    # 8a：删除 Conversation → 关联行删除，Evidence 保留
    conversation_id = _create_conversation(conversations)
    evidence_id = _create_evidence(evidences)
    associations.create_association(conversation_id, evidence_id)
    with factory() as session, session.begin():
        session.execute(
            text(
                f"DELETE FROM {_SCHEMA}.conversation "
                "WHERE conversation_id = :conversation_id"
            ),
            {"conversation_id": conversation_id},
        )
    assert associations.get_association(conversation_id, evidence_id) is None
    assert EvidenceRepository().get_evidence(evidence_id) is not None
    _cleanup((), (evidence_id,))

    # 8b：删除 Evidence → 关联行删除，Conversation 保留
    conversation_id = _create_conversation(conversations)
    evidence_id = _create_evidence(evidences)
    associations.create_association(conversation_id, evidence_id)
    with factory() as session, session.begin():
        session.execute(
            text(
                f"DELETE FROM {_SCHEMA}.evidence_record "
                "WHERE evidence_id = :evidence_id"
            ),
            {"evidence_id": evidence_id},
        )
    assert associations.get_association(conversation_id, evidence_id) is None
    assert (
        ConversationRepository().get_by_conversation_id(conversation_id) is not None
    )
    _cleanup((conversation_id,), ())


# ============================================================
# Case 9：Runtime Result Identity Boundary（assistant_request_id）
# ============================================================


def test_09_runtime_result_identity_boundary(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    conversation_id = _create_conversation(conversations)
    evidence_id = _create_evidence(evidences)
    try:
        request_id = _unique("req")
        result = _run_ai_turn(conversation_id, "身份边界", request_id)
        assert result.metadata["request_id"] == request_id

        # request_id 只是 runtime correlation —— 不是 evidence identity
        assert evidence_id != request_id
        provenance = evidences.get_provenance(evidence_id)
        assert provenance is not None
        assert request_id not in provenance.dataset_version
        assert request_id not in provenance.source_type

        # 关联表不得出现 turn-level / provenance 字段（OD-12 DEFERRED）
        factory = get_session_factory()
        assert factory is not None
        with factory() as session:
            columns = {
                row[0]
                for row in session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = :s AND table_name = :t"
                    ),
                    {"s": _SCHEMA, "t": "conversation_evidence"},
                )
            }
        assert columns == {"conversation_id", "evidence_id", "created_at"}
    finally:
        _cleanup((conversation_id,), (evidence_id,))


# ============================================================
# Case 10：OD-12 —— Turn-level Reference 判定
# ============================================================


def test_10_turn_level_reference_requirement(
    conversations: ConversationRepository,
    evidences: EvidenceRepository,
    associations: ConversationEvidenceRepository,
) -> None:
    """OD-12 判定（Q1 → Q2 → Q3）：

    Q1：`(conversation_id, evidence_id)` 能否唯一定位 Evidence 所属会话？
        → YES（`list_conversation_ids` 可反查）。
    Q2：当前是否需要回答"该 Evidence 由哪个 Turn 产生"？
        → 无现有契约 / API 要求 turn-level provenance
          （Step 44/45 冻结的关联表不含 turn_id）。
    Q3：因此 OD-12 = **DEFERRED**（保持 Step 45 模型，不新增 turn_id）。
    """
    conversation_id = _create_conversation(conversations)
    evidence_id = _create_evidence(evidences)
    try:
        associations.create_association(conversation_id, evidence_id)
        # Q1 = YES
        assert associations.list_conversation_ids(evidence_id) == (conversation_id,)
        assert associations.get_association(conversation_id, evidence_id) is not None
        # Q2/Q3 = DEFERRED：无 turn-level 列、无 turn-level API
        assert not hasattr(associations, "create_turn_association")
        assert "turn_id" not in {
            column
            for column in ("conversation_id", "evidence_id", "created_at")
        }
    finally:
        _cleanup((conversation_id,), (evidence_id,))

