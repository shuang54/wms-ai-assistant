"""Agent Tool Result ORM Model（Phase 5 P-4 — Replayable Tool Result Store）。

```text
ai_ops.agent_tool_result      一行 = 一次 Agent Tool 执行的**可安全 Replay 的结果**
```

设计依据（已冻结，不得就地重设计）：
    docs/decisions/Phase/Phase 5 — Tool Replay & Idempotency Decision.md
        §3.3 Tool Execution Identity = **(run_id, step_index)**
        §5   Replay 语义（SUCCEEDED ⇒ replay payload / FAILED ⇒ replay failure /
             UNKNOWN ⇒ fail-closed）
        §6   Tool Result Store（保存什么 / 不保存什么 / 大小边界 / 敏感边界）
        §7   Checkpoint Boundary（本表只承载"恢复后需要重放什么"）

职责边界（严格）：

    * 只承载 **执行身份 + 结果安全体（或结果不可用标记）+ 完整性 hash + 尺寸**；
    * **禁止**：system prompt / user prompt / conversation context / RAG chunks /
      messages / tool definitions / SQL 原文 / headers / API key / authorization /
      password / database URL / credentials / exception message / stack trace /
      raw provider response / AgentStep 的 arguments / output；
    * **不是** observation（``tool_execution_record`` 仍为执行事实观测；
      本表**不**复制 request_id / round / duration / started_at）；
    * **不是** checkpoint（不承载 next_action / pending ids / 恢复控制信息）；
    * **无外键**：``run_id`` 为 correlation 字符串（沿用 ``agent_run`` /
      ``agent_step`` "correlation 不用 FK" 的先例；OD-P5-25）。

状态语义（本表四态；写入侧 = ``agent.recorder.agent_tool_result_record_of``）：

| success | result_unavailable | payload | 语义 | 未来 Resume 行为 |
| ------- | ------------------ | ------- | ---- | ---------------- |
| True    | False              | 已保存 | SUCCEEDED（小结果内联） | **Replay payload**（handler 0 次） |
| True    | True               | NULL    | SUCCEEDED 但 payload 不可保存（超限 / 敏感 / 不可序列化） | **FAIL CLOSED** |
| False   | False              | NULL    | FAILED（Handler 已执行并返回失败；稳定码已保存） | **Replay failure**（handler 0 次） |
| False   | True               | NULL    | 执行结果**不可判定**（边界异常 / 能力拒绝 / 未知；§23） | **FAIL CLOSED** |

写入时机（已冻结，§25）：**Tool handler 成功/失败之后立即写入** ——
**不是** AgentRun 完成之后；本表用于覆盖 "Tool 已执行、Checkpoint 未落盘、进程崩溃"
的恢复窗口（本阶段仅落地存储层；生产写入点接线见 P-4 报告）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base

#: AI 内部运维 schema（与业务 schema `public` 物理隔离；与其它 ai_ops 表同 schema）。
AGENT_TOOL_RESULT_SCHEMA: Final[str] = "ai_ops"

#: 表名常量（DB 测试 / 残留守卫共用）。
AGENT_TOOL_RESULT_TABLE: Final[str] = "agent_tool_result"

#: 执行身份唯一索引：**(run_id, step_index)**（不同 Run 可各有 step_index=0）。
#: 这是 Tool Execution Identity 的数据库约束；**不是** tool_call_id 唯一。
AGENT_TOOL_RESULT_IDENTITY_INDEX: Final[str] = "uq_agent_tool_result_run_id_step_index"

# ---- 列长度上限（与任务书 §十一 输入验证一致）----
AGENT_TOOL_RESULT_RUN_ID_MAX_LENGTH: Final[int] = 64
AGENT_TOOL_RESULT_STEP_TOOL_NAME_MAX_LENGTH: Final[int] = 128
AGENT_TOOL_RESULT_PROJECT_ID_MAX_LENGTH: Final[int] = 128
AGENT_TOOL_RESULT_ERROR_CODE_MAX_LENGTH: Final[int] = 64
AGENT_TOOL_RESULT_TOOL_CALL_ID_MAX_LENGTH: Final[int] = 128
AGENT_TOOL_RESULT_RESULT_REF_MAX_LENGTH: Final[int] = 512

#: SHA-256 十六进制长度（64 hex characters；**不是**加密，只做完整性校验）。
AGENT_TOOL_RESULT_RESULT_HASH_LENGTH: Final[int] = 64

#: 内联上限：规范化 JSON-safe 结果 ≤ 32 KiB ⇒ 保存完整 payload（可 Replay）。
AGENT_TOOL_RESULT_INLINE_MAX_BYTES: Final[int] = 32 * 1024

#: 硬上限：> 1 MiB ⇒ 不保存 payload（只保留 hash / size / 身份；不可 Replay）。
AGENT_TOOL_RESULT_HARD_MAX_BYTES: Final[int] = 1024 * 1024


class AgentToolResult(Base):
    """一次 Agent Tool 执行的可重放结果（append-only；first-write-wins）。

    写入：``UNIQUE(run_id, step_index)`` + ``ON CONFLICT DO NOTHING``
    （重复写入不产生第二行，也不 UPDATE —— 冻结的 execution identity）。
    删除：本阶段无 delete / TTL / 级联（retention = OQ-P5-23）。
    """

    __tablename__ = AGENT_TOOL_RESULT_TABLE

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="主键（BIGINT 自增；项目既有规范）",
    )
    run_id: Mapped[str] = mapped_column(
        String(AGENT_TOOL_RESULT_RUN_ID_MAX_LENGTH),
        nullable=False,
        comment="所属 Agent Run 身份（= agent_run.run_id；correlation，**无外键**）",
    )
    step_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Run 内 Tool 执行步序（0 起；与 run_id 组成唯一身份）",
    )
    tool_name: Mapped[str] = mapped_column(
        String(AGENT_TOOL_RESULT_STEP_TOOL_NAME_MAX_LENGTH),
        nullable=False,
        comment="Tool 名称（白名单内已授权 Tool；**禁止** arguments）",
    )
    project_id: Mapped[str | None] = mapped_column(
        String(AGENT_TOOL_RESULT_PROJECT_ID_MAX_LENGTH),
        nullable=True,
        comment="项目作用域（执行边界 project_id；缺失 ⇒ NULL）",
    )
    success: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        comment="Tool 执行是否成功（False ⇒ result_payload 必为 NULL）",
    )
    error_code: Mapped[str | None] = mapped_column(
        String(AGENT_TOOL_RESULT_ERROR_CODE_MAX_LENGTH),
        nullable=True,
        comment=(
            "稳定错误码（Agent 错误码白名单）—— "
            "**禁止** exception message / stack trace / ToolResult.error 自由文本"
        ),
    )
    result_payload: Mapped[dict | list | str | int | float | bool | None] = (
        mapped_column(
            JSONB,
            nullable=True,
            comment=(
                "JSON-safe 安全序列化结果（仅 ≤ 32 KiB 且无敏感标记时非 NULL）；"
                "**禁止** ORM model / Session / Connection / exception / 原始对象"
            ),
        )
    )
    result_hash: Mapped[str | None] = mapped_column(
        String(AGENT_TOOL_RESULT_RESULT_HASH_LENGTH),
        nullable=True,
        comment=(
            "规范化 JSON 序列化后的 SHA-256（64 hex）—— **只做完整性校验**，"
            "不是加密、不是认证；不可序列化 / 敏感 ⇒ NULL"
        ),
    )
    result_size_bytes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="规范化 JSON（UTF-8）字节数（>= 0；不可度量 ⇒ 0）",
    )
    result_truncated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        comment="是否发生过截断（本阶段恒为 False：超限**不截断**，直接置不可用）",
    )
    result_unavailable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        comment=(
            "payload 不可用于 Replay（超限 / 敏感 / 不可序列化 / 结果不可判定）"
            " ⇒ Resume 必须 fail-closed"
        ),
    )
    result_ref: Mapped[str | None] = mapped_column(
        String(AGENT_TOOL_RESULT_RESULT_REF_MAX_LENGTH),
        nullable=True,
        comment="外部 artifact 引用（**本阶段恒为 NULL**：artifact store 未实现）",
    )
    tool_call_id: Mapped[str | None] = mapped_column(
        String(AGENT_TOOL_RESULT_TOOL_CALL_ID_MAX_LENGTH),
        nullable=True,
        comment=(
            "Provider/LLM 单轮协议关联（**非唯一键**、**不作为执行身份**；"
            "执行身份 = (run_id, step_index)）"
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="本条记录的创建时间（不是 Tool 开始/结束时间）",
    )

    __table_args__ = (
        # Tool Execution Identity：同一 Run 内 step_index 唯一（不同 Run 可重复）
        Index(
            AGENT_TOOL_RESULT_IDENTITY_INDEX,
            "run_id",
            "step_index",
            unique=True,
        ),
        {"schema": AGENT_TOOL_RESULT_SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AgentToolResult id={self.id} run_id={self.run_id} "
            f"step_index={self.step_index} tool_name={self.tool_name} "
            f"success={self.success} unavailable={self.result_unavailable}>"
        )
