"""Agent Run ORM Model（Phase 5 P-2 — AgentRun Persistence MVP）。

```text
ai_ops.agent_run       一行 = 一次 AgentRuntime.run() 的**执行级**记录
```

设计依据（已冻结，不得就地重设计）：
    docs/decisions/Phase/Phase 5 — Scope & Architecture Decision.md §30
    （OD-P5-25 仅 request_id 关联 / OD-P5-26 run 级持久化 / OD-P5-27 step 不落库 /
     OD-P5-28 append-only · 无跨边界级联 · 无 TTL）

职责边界（严格）：

    * 只承载 **run-level state + counts + stable codes + timestamps**；
    * **不**承载 prompt / messages / conversation_context / tool_arguments /
      tool_result / SQL / headers / 密钥 / exception message / stack trace /
      最终回答正文（用户可见消息归 ``conversation_turn``）；
    * **不**复制 LLMUsageRecord（token）与 ToolExecutionRecord（Tool 明细）；
    * **无外键**：`request_id` / `project_id` 均为 correlation 字符串
      （沿用 ``conversation_turn.assistant_request_id`` 非 FK 先例）；
    * **不做** agent_run → conversation / evidence / observation 的级联删除。
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base

#: AI 内部运维 schema（与业务 schema `public` 物理隔离；与其它 ai_ops 表同 schema）。
AGENT_RUN_SCHEMA: Final[str] = "ai_ops"

#: 表名常量（DB 测试 / 残留守卫共用；避免散落字符串）。
AGENT_RUN_TABLE: Final[str] = "agent_run"

#: ``run_id`` 唯一索引（一次 Agent 执行最多 1 行；**持久化安全**，不是业务幂等）。
AGENT_RUN_RUN_ID_INDEX: Final[str] = "uq_agent_run_run_id"

#: ``request_id`` 普通索引（AI Runtime 请求 correlation；一次请求可有多行 —— 未来 resume / rerun）。
AGENT_RUN_REQUEST_ID_INDEX: Final[str] = "ix_agent_run_request_id"

#: ``started_at`` 索引（时间序；与既有观测表一致，供未来 retention / 查询评估）。
AGENT_RUN_STARTED_AT_INDEX: Final[str] = "ix_agent_run_started_at"

#: ID 长度上限（uuid4 文本 36 字符；留余量，写入前由 Repository 校验，**不截断**）。
AGENT_RUN_ID_MAX_LENGTH: Final[int] = 64

#: ``project_id`` 长度上限。
AGENT_RUN_PROJECT_ID_MAX_LENGTH: Final[int] = 128

#: status 列长度（最长取值 ``WAITING_APPROVAL`` = 16）。
AGENT_RUN_STATUS_MAX_LENGTH: Final[int] = 32

#: 稳定码列长度（``error_code`` / ``limit_reason``；只存白名单稳定码）。
AGENT_RUN_CODE_MAX_LENGTH: Final[int] = 32

#: 允许持久化的 run 状态（**不新增状态**；由 Repository 在触达 DB 前校验）。
#: ``RUNNING`` 为状态机预留（本 MVP 只在终态写入一行，故实际不出现）。
AGENT_RUN_STATUSES: Final[frozenset[str]] = frozenset(
    {
        "RUNNING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        "LIMIT_REACHED",
        "WAITING_APPROVAL",
    }
)


class AgentRun(Base):
    """一次 Agent 执行的 run-level 记录（append-only）。

    生命周期：Agent Run 结束（终态确定）时写入**一行**；不 UPDATE / 不重写。
    删除：本阶段无 delete / TTL / 级联（OD-P5-28）。
    """

    __tablename__ = AGENT_RUN_TABLE

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="Agent Run 主键（BIGINT 自增；项目既有规范）",
    )
    run_id: Mapped[str] = mapped_column(
        String(AGENT_RUN_ID_MAX_LENGTH),
        nullable=False,
        comment=(
            "Agent 执行身份（AgentRuntime.run() 签发；UNIQUE；"
            "与 assistant_request_id / turn_id / tool_call_id 互不相同）"
        ),
    )
    request_id: Mapped[str] = mapped_column(
        String(AGENT_RUN_ID_MAX_LENGTH),
        nullable=False,
        comment=(
            "AI Runtime 请求 correlation（= assistant_request_id；"
            "由 AIOrchestrator 生成，本层不生成；普通索引，非外键）"
        ),
    )
    project_id: Mapped[str | None] = mapped_column(
        String(AGENT_RUN_PROJECT_ID_MAX_LENGTH),
        nullable=True,
        comment="授权作用域（来自 ProjectContext；NULL = 未绑定项目）",
    )
    status: Mapped[str] = mapped_column(
        String(AGENT_RUN_STATUS_MAX_LENGTH),
        nullable=False,
        comment=(
            "Agent 执行终态：COMPLETED / FAILED / LIMIT_REACHED / CANCELLED / "
            "WAITING_APPROVAL（RUNNING 为预留；**原样保存**，不把 LIMIT_REACHED 改写成 FAILED）"
        ),
    )
    error_code: Mapped[str | None] = mapped_column(
        String(AGENT_RUN_CODE_MAX_LENGTH),
        nullable=True,
        comment=(
            "稳定错误码（Agent 错误码白名单；异常类名兜底）—— "
            "**禁止** exception message / stack trace"
        ),
    )
    limit_reason: Mapped[str | None] = mapped_column(
        String(AGENT_RUN_CODE_MAX_LENGTH),
        nullable=True,
        comment="仅 LIMIT_REACHED 时填写的稳定码；其它状态为 NULL",
    )
    step_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="AgentRuntimeResult.steps（LLM 决策步数；>= 0，不重新计算）",
    )
    tool_call_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="AgentRuntimeResult.tool_calls（真实 Tool 执行次数；>= 0）",
    )
    llm_call_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="AgentRuntimeResult.llm_calls（真实 LLM 调用次数；>= 0）",
    )
    token_usage_unverifiable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        comment=(
            "AgentRuntimeResult.token_budget_unverifiable（provider 未返回 usage ⇒ True；"
            "**不伪造** token；本表不存 prompt/completion/total tokens）"
        ),
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Agent 执行开始时间（TIMESTAMP WITH TIME ZONE；UTC）",
    )
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Agent 执行结束时间（>= started_at）",
    )
    duration_ms: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="端到端耗时毫秒（monotonic 差值；>= 0；与既有观测表一致）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="本条持久化记录的创建时间（不是 Agent 开始时间）",
    )

    __table_args__ = (
        # run_id UNIQUE：重复写入 ⇒ ON CONFLICT DO NOTHING（first-write-wins）
        Index(AGENT_RUN_RUN_ID_INDEX, "run_id", unique=True),
        # request_id：AI 请求 → Agent Run 关联查询
        Index(AGENT_RUN_REQUEST_ID_INDEX, "request_id"),
        # started_at：时间序（与 tool_execution / rag_execution 一致）
        Index(AGENT_RUN_STARTED_AT_INDEX, "started_at"),
        # project_id / status：**不预建**（沿用 tool_execution_record 先例：
        # 对应过滤查询尚未实现，等真正实现时再评估）
        {"schema": AGENT_RUN_SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AgentRun id={self.id} run_id={self.run_id} "
            f"request_id={self.request_id} status={self.status}>"
        )
