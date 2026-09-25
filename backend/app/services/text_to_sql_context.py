"""Text-to-SQL Generation Context（Phase 3.9.1）。

职责：把 Text-to-SQL 生成所需要的上下文**结构化、可约束、可测试**地
描述出来，避免"字符串拼到哪里算哪里"：

    Question
        + Database Schema（数据库事实）
        + Business Semantic（人工业务含义）
        + SQL Generation Constraints（Prompt 指令）
                ↓
        TextToSQLContext（frozen，纯内存）
                ↓
        TextToSQLService

设计要点：

- **不耦合 Project 层**（任务书 §五）：本模块与 ``TextToSQLService``
  都**不 import** ``ProjectRegistry`` / ``ProjectConfigurationProvider``
  / ``ProjectSemanticProvider`` / ``ProjectKnowledgeProvider``；
  业务语义以**已序列化的文本**进入 ``business_context``，
  项目标识只作为 tracing / metadata 用的 ``project_id`` 字符串。
  项目解析在上游（Orchestrator / Application）完成。
- **不重复保存重对象**（任务书 §四）：不保存
  ``ProjectConfiguration`` / ``ProjectContext`` / ``DatabaseSchema`` /
  ``ProjectSemantic``，只保存生成 SQL 真正需要的文本与约束。
- **三层信息区分**（任务书 §六）：
  1. ``database_context`` = Database Schema（+ 项目头，非敏感）；
  2. ``business_context`` = Business Semantic（业务名称 / 别名 / 关系）；
  3. SQL Constraints = Prompt 指令（system / retry 模板），
     **不是**安全边界——真正拦截始终由 SQLValidator 负责。
- **纯内存、零副作用**（任务书 §十九）：不查库、不调 LLM /
  Embedding / Reranker；``render()`` 是确定性纯函数。
- **零 fallback / 零推断**：未提供的内容不会凭空产生；
  不存在的表 / 列不会被引申出来（Case 5）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from backend.app.services.sql_validator_service import DEFAULT_MAX_ROWS

__all__ = [
    "TextToSQLContextError",
    "TextToSQLContext",
    "BUSINESS_SEMANTICS_HEADER",
]

#: 业务语义段标题（仅当上游未自带标题时补充，避免重复标题）
BUSINESS_SEMANTICS_HEADER: Final[str] = "## Business Semantics"


# ============================================================
# 异常
# ============================================================

class TextToSQLContextError(Exception):
    """TextToSQLContext 输入非法（类型 / 取值 / 空白）。

    在进入 LLM 之前拒绝，不吞异常、不静默降级。
    """


# ============================================================
# DTO（frozen）
# ============================================================

@dataclass(frozen=True)
class TextToSQLContext:
    """Text-to-SQL 生成上下文（frozen，非敏感，纯配置结果）。

    Attributes:
        database_context: Database Schema 事实（含非敏感项目头），
                          由 ``DatabaseContextComposer`` 产出。
        business_context: 业务语义文本（已序列化）；
                          ``None`` / 空 = 该项目未配置业务语义
                          （渲染时整段省略，不做任何推断）。
        allowed_tables:   允许引用的表（``RelevantTableSelector`` 产出，
                          "schema.table" 形态）；严格保留顺序与取值。
                          空 tuple = 不额外限制（仍由 SQLValidator 兜底）。
        max_rows:         LIMIT 上限（与 SQLValidator 一致）。
        project_id:       仅用于 tracing / metadata / debug（任务书 §十一），
                          **不会**被用于反查任何 Provider。
    """

    database_context: str
    business_context: str | None = None
    allowed_tables: tuple[str, ...] = ()
    max_rows: int = DEFAULT_MAX_ROWS
    project_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.database_context, str):
            raise TextToSQLContextError(
                "database_context 必须是 str"
                f"（当前: {type(self.database_context).__name__}）"
            )
        if not self.database_context.strip():
            raise TextToSQLContextError(
                "database_context 不能为空或纯空白"
                "（不要让空上下文进入 LLM）"
            )
        if self.business_context is not None and not isinstance(
            self.business_context, str
        ):
            raise TextToSQLContextError(
                "business_context 必须是 str 或 None"
                f"（当前: {type(self.business_context).__name__}）"
            )
        if isinstance(self.allowed_tables, (str, bytes)):
            raise TextToSQLContextError(
                "allowed_tables 必须是表名序列，而不是单个字符串"
            )
        for entry in self.allowed_tables:
            if not isinstance(entry, str):
                raise TextToSQLContextError(
                    f"allowed_tables 每项必须是 str"
                    f"（当前: {type(entry).__name__}）"
                )
        # 归一化：list → tuple（frozen DTO 仍保持不可变语义）
        if not isinstance(self.allowed_tables, tuple):
            object.__setattr__(
                self, "allowed_tables", tuple(self.allowed_tables)
            )
        if isinstance(self.max_rows, bool) or not isinstance(self.max_rows, int):
            raise TextToSQLContextError(
                f"max_rows 必须是整数（当前: {type(self.max_rows).__name__}）"
            )
        if self.max_rows < 1:
            raise TextToSQLContextError(
                f"max_rows 必须 >= 1（当前: {self.max_rows}）"
            )
        if self.project_id is not None:
            if not isinstance(self.project_id, str):
                raise TextToSQLContextError(
                    "project_id 必须是 str 或 None"
                    f"（当前: {type(self.project_id).__name__}）"
                )
            if not self.project_id.strip():
                raise TextToSQLContextError("project_id 不能为空或纯空白")

    # ---------- 渲染（纯内存、确定性） ----------

    def render(self) -> str:
        """渲染为单段 AI 上下文（§六：Schema 与 Semantic 分层显式）。

        规则（零推断、零 fallback）：
            - ``business_context`` 为空 → 原样返回 ``database_context``；
            - ``database_context`` 已含业务语义段 → 不再重复追加；
            - 上游文本自带 "Business Semantics" 标题 → 不重复加标题。
        """
        business = (self.business_context or "").strip()
        if not business:
            return self.database_context
        if "Business Semantics" in self.database_context:
            return self.database_context
        header = (
            ""
            if business.startswith("Business Semantics")
            else f"{BUSINESS_SEMANTICS_HEADER}\n\n"
        )
        return f"{self.database_context}\n\n{header}{business}"

    @property
    def has_business_context(self) -> bool:
        """是否携带业务语义（供日志 / 测试断言，不做任何推断）。"""
        return bool((self.business_context or "").strip())
