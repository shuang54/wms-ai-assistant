"""ORM Models 包（Phase 3.2）。

本包**只**承载 SQLAlchemy ORM Model 的定义与导出，不放业务逻辑。
所有 Model 继承 `backend.app.db.base.Base`，由 `Base.metadata` 统一管理。

新增 Model 流程：
    1. 在本目录新建 `xxx.py`，定义 `class Xxx(Base)`，设置 `__tablename__`
    2. 在本文件 `__init__.py` 的 `_MODELS` 列表中添加该类
    3. `Base.metadata.create_all()` 会自动发现并创建对应表

暂未实现（按 Phase 计划）：
    - 业务表（conversation / user / role 等）— 后续 Phase
    - Embedding / RAG Service — Phase 3.3+
    - Alembic migration — Phase 3.5+
"""
from __future__ import annotations

# 顺序很关键：KnowledgeDocument 必须先于 KnowledgeChunk（虽然 SQLAlchemy 不强制，
# 但 IDE / 类型检查友好）
from backend.app.db.models.agent_checkpoint import AgentCheckpoint
from backend.app.db.models.agent_decision import AgentDecision
from backend.app.db.models.agent_run import AgentRun
from backend.app.db.models.agent_step import AgentStep
from backend.app.db.models.agent_approval import AgentApproval
from backend.app.db.models.agent_tool_result import AgentToolResult
from backend.app.db.models.assistant_outcome_record import AssistantOutcomeRecord
from backend.app.db.models.conversation import Conversation
from backend.app.db.models.conversation_evidence import ConversationEvidenceRecord
from backend.app.db.models.conversation_turn import ConversationTurn
from backend.app.db.models.evidence_annotation_record import EvidenceAnnotationRecord
from backend.app.db.models.evidence_record import EvidenceRecord
from backend.app.db.models.knowledge_chunk import KnowledgeChunk
from backend.app.db.models.knowledge_document import KnowledgeDocument
from backend.app.db.models.llm_usage_record import LLMUsageRecord
from backend.app.db.models.rag_execution_record import RagExecutionRecordModel
from backend.app.db.models.tool_execution_record import ToolExecutionRecordModel
from backend.app.db.models.workflow_approval import WorkflowApproval
from backend.app.db.models.workflow_node_run import WorkflowNodeRun
from backend.app.db.models.workflow_run import WorkflowRun

_MODELS: tuple[type, ...] = (
    KnowledgeDocument,
    KnowledgeChunk,
    LLMUsageRecord,
    ToolExecutionRecordModel,
    RagExecutionRecordModel,
    # Phase 3.12 Step 64：Assistant request-level terminal outcome
    # （全新表 → 随 create_all 自动创建；既有库首次 init_db() 即补建）
    AssistantOutcomeRecord,
    # Phase 4.1 Step 5：Conversation 持久化层（ai_ops schema；非 public）。
    # Conversation 必须先于 ConversationTurn（后者有指向前者的 FK）。
    Conversation,
    ConversationTurn,
    # Phase 4.1 Step 37：Evidence / Annotation 持久化（ai_ops schema；非 public）。
    # EvidenceRecord 必须先于 EvidenceAnnotationRecord（后者有指向前者的 FK）。
    EvidenceRecord,
    EvidenceAnnotationRecord,
    # Phase 4.1 Step 45：Conversation ↔ Evidence 关联（ai_ops schema）。
    # 必须晚于 Conversation 与 EvidenceRecord（本表对两者都有 FK）。
    ConversationEvidenceRecord,
    # Phase 5 P-2：Agent Run（run-level 持久化；ai_ops schema；**无外键**）。
    # 全新表 → 随 create_all 自动创建；既有库首次 init_db() 即补建。
    AgentRun,
    # Phase 5 AgentStep Persistence：Agent Step（step projection；ai_ops；
    # **无外键**；UNIQUE(run_id, step_index)）。
    AgentStep,
    # Phase 5 P-4 Tool Result Store：Agent Tool Result（replayable result；ai_ops；
    # **无外键**；UNIQUE(run_id, step_index) = Tool Execution Identity）。
    AgentToolResult,
    # Phase 5 P-5 Checkpoint Persistence：Agent Checkpoint（recovery control state；
    # ai_ops；**无外键**；UNIQUE(run_id)；**可 UPDATE**）。
    AgentCheckpoint,
    # Phase 5 P-6 Decision Store：Agent Decision（LLM decision replay data；ai_ops；
    # **无外键**；UNIQUE(run_id, step_index) + UNIQUE(decision_ref)）。
    AgentDecision,
    # Phase 5 P-7 HITL: Agent Approval (human approval request; ai_ops; no FK;
    # UNIQUE(approval_id) + UNIQUE(run_id, step_index)).
    AgentApproval,
    # Phase 6 Step 2：Workflow Persistence（ai_ops；独立 3 表 —— Discovery §10）。
    # WorkflowRun **必须**先于 WorkflowNodeRun / WorkflowApproval
    # （后两者有指向它的域内 FK；create_all 亦会自动拓扑排序）。
    WorkflowRun,
    WorkflowNodeRun,
    WorkflowApproval,
)


__all__ = [
    "AgentCheckpoint",
    "AgentDecision",
    "AgentRun",
    "AgentStep",
    "AgentToolResult",
    "AgentApproval",
    "AssistantOutcomeRecord",
    "Conversation",
    "ConversationEvidenceRecord",
    "ConversationTurn",
    "EvidenceRecord",
    "EvidenceAnnotationRecord",
    "KnowledgeDocument",
    "KnowledgeChunk",
    "LLMUsageRecord",
    "RagExecutionRecordModel",
    "ToolExecutionRecordModel",
    "WorkflowRun",
    "WorkflowNodeRun",
    "WorkflowApproval",
]


def get_all_models() -> tuple[type, ...]:
    """返回所有已注册的 ORM Model 类（用于 `Base.metadata.create_all` 自动发现）。"""
    return _MODELS