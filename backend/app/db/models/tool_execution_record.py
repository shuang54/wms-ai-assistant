"""ToolExecutionRecord ORM Model（Phase 3.11 Step 27）。

Tool Observability 持久化表：**只保存一次 Tool 执行的运维事实**。

    ToolExecutionRecord（services；frozen DTO；11 字段）
        ↓ Persistence Repository（backend/app/db/tool_execution_repository.py）
    ai_ops.tool_execution_record

设计约束（Step 27 任务书 §五 ~ §九 / §十六 ~ §十八）：

* **字段严格等于** ``ToolExecutionRecord`` 的 11 个字段 + 独立主键：
  request_id / round / tool_name / started_at / finished_at / duration_ms /
  success / project_id / tool_call_id / error_code / error_type；
  可空性也一一对应（只有 project_id / tool_call_id / error_code /
  error_type 允许 NULL，其余 NOT NULL）；
* **不新增任何敏感列**：无 tool_arguments / tool_result / sql / prompt /
  llm_response / exception_message / traceback / api_key / database_url；
  （Record 本身已不含这些信息 → 落库自然不可能含）；
* **主键不是 request_id**：一个 request 未来可能包含多次 Tool 执行
  （multi-round / multi-tool），因此沿用项目既有 BIGINT 自增主键
  （与 ``LLMUsageRecord.id`` 同风格）；
* **时间戳 timezone-aware**：``DateTime(timezone=True)``
  （PostgreSQL ``TIMESTAMP WITH TIME ZONE``）；Record 校验拒绝 naive
  datetime → 不会出现 naive 值落库；
* **独立 schema ``ai_ops``**（**不是** public）：与 ``LLMUsageRecord`` 同理，
  本表是 AI 内部运维数据，不得被 Text-to-SQL 的
  ``inspect(schema="public")`` 当作业务表（避免 LLM 针对运维表生成查询）；
* **索引保持最小**：只为**当前已实现 / 明确**的查询建立
  （request_id 精确查询 + started_at 时间序）；project_id / tool_name
  的过滤查询尚未实现 → 本阶段**不**预建索引（§八）；
* **不做 retention**：无 TTL / 分区 / 自动清理（§九）；数据库存长期历史，
  内存 Collector 的 ``max_records=1000`` 仍是独立的 runtime 安全边界。

明确不做：Alembic migration（沿用 ``init_db()`` + ``create_all``）、
聚合列、JSON 列、外键、软删除。
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
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base

#: AI 内部运维 schema（与业务 schema `public` 物理隔离）。
#: 与 `llm_usage_record.LLM_USAGE_SCHEMA` 同值（有意不复用常量：
#: 两张运维表互不依赖，避免跨模块耦合）。
TOOL_EXECUTION_SCHEMA: Final[str] = "ai_ops"

#: 表名（`ai_ops.tool_execution_record`）
TOOL_EXECUTION_TABLE: Final[str] = "tool_execution_record"

#: 落库字段白名单（严格 = ToolExecutionRecord 的 11 个字段；不含主键）
TOOL_EXECUTION_PERSISTED_FIELDS: Final[tuple[str, ...]] = (
    "request_id",
    "round",
    "tool_name",
    "started_at",
    "finished_at",
    "duration_ms",
    "success",
    "project_id",
    "tool_call_id",
    "error_code",
    "error_type",
)


class ToolExecutionRecordModel(Base):
    """一次 Tool 执行的持久化事实（一行 = 一条 ``ToolExecutionRecord``）。

    位于 ``ai_ops`` schema（**不是** public）：AI 内部运维数据，
    不参与 Text-to-SQL 的业务 schema。
    """

    __tablename__ = TOOL_EXECUTION_TABLE

    # ---- 主键（独立；request_id **不是**主键）----
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="Tool Execution Record 主键（BIGINT 自增；项目既有规范）",
    )

    # ---- 关联标识 ----
    request_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment=(
            "一次 execute / chat 的关联 ID（来自 Execution Context；"
            "不重新生成；可重复：一次 request 可有多条执行）"
        ),
    )
    round: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Tool 执行轮次（来自 Execution Context；>= 1）",
    )
    tool_name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="实际执行的 Tool 名称（来自 execute() 实参；不推断）",
    )

    # ---- 时间（timezone-aware）----
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="执行开始时间（TIMESTAMP WITH TIME ZONE；UTC）",
    )
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="执行结束时间（>= started_at）",
    )
    duration_ms: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="耗时毫秒（来自 perf_counter 差值；>= 0）",
    )

    # ---- 结果 ----
    success: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        comment="是否成功（直接来自 ToolResult.success；不推断）",
    )
    project_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="授权作用域（来自 Execution Context；NULL = 未绑定）",
    )
    tool_call_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="Function Calling ToolCall id（上游未提供 → NULL）",
    )
    error_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="结构化错误码槽位（当前 ToolResult 无该信息 → NULL）",
    )
    error_type: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="安全错误分类（既有 ToolError 类名；无 → NULL）",
    )

    # ---- 索引（最小）+ 独立 schema ----
    __table_args__ = (
        # 理由：Repository 已实现 get_by_request_id()（精确查询）
        Index("ix_tool_execution_record_request_id", "request_id"),
        # 理由：时间序 / 未来时间范围查询与"最近执行"排序
        Index("ix_tool_execution_record_started_at", "started_at"),
        # 未建索引（本阶段**不**预建）：project_id / tool_name ——
        # 对应过滤查询尚未实现，等真正实现时再评估（§八）。
        {"schema": TOOL_EXECUTION_SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ToolExecutionRecordModel id={self.id} "
            f"request_id={self.request_id} round={self.round} "
            f"tool_name={self.tool_name} success={self.success}>"
        )


__all__ = [
    "ToolExecutionRecordModel",
    "TOOL_EXECUTION_SCHEMA",
    "TOOL_EXECUTION_TABLE",
    "TOOL_EXECUTION_PERSISTED_FIELDS",
]
