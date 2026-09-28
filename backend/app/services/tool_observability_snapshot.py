"""Tool Observability Snapshot Read Model（Phase 3.11 Step 22）。

给未来 HTTP API / Dashboard 一个**稳定的对外读模型**，使上层不需要直接暴露
内部执行事件对象：

    ToolExecutionRecord        = 内部 Execution Event（Step 14；可演进）
            ↓ from_record()（显式逐字段映射）
    ToolExecutionSnapshot      = 对外 Read Model（本模块；字段固定）

为什么必须是**独立 DTO**（§五）：

    * Record 是内部执行事实，未来可能增加字段（tracing / retry / 维度…）；
    * Snapshot 是对外契约：**不应该**因为 Record 内部结构变化而自动改变；
    * 因此禁止 `vars(record)` / `asdict(record)` / `**record.__dict__`
      这类"自动把所有字段带过来"的写法（§十）—— 必须逐字段显式映射。

设计约束：

    * `@dataclass(frozen=True)`（§七）：不可变；不是 dict / 普通 class /
      可变 dataclass / Pydantic Model；
    * **不持有原始 Record**（§八）：只复制值（`from_record()` 后两者互不
      引用）；也不持有 Collector / Metrics Service / DB / LLM / Tool 对象；
    * `from_record()` 是**纯函数**（§九）：无 IO / 无 DB / 无 LLM /
      无 Collector / 无 Registry / 无 HTTP；deterministic；
    * 字段与 Step 14 Record 一致（11 项），但**不等于** Record：
      Record 已经不含 Tool arguments / SQL / 凭据 / traceback，Snapshot
      也不新增任何敏感信息（§十一）；
    * **不含任何 Metrics 字段**（§六）：Record Snapshot 与 Metrics
      Snapshot 是两个层级，不合并（metrics 仍由
      ``ToolExecutionMetricsService`` 单独计算）。

明确不做（§十八）：

    page / page_size / offset / cursor / sort / order_by / filter DSL /
    时间范围 / 聚合维度 —— 这些属未来 HTTP Read Layer。
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from typing import Final

from backend.app.services.tool_execution_record import ToolExecutionRecord

__all__ = [
    "ToolExecutionSnapshot",
]


#: Snapshot 字段白名单（防未来意外扩展 / 引入敏感字段）
_ALLOWED_FIELDS: Final[frozenset[str]] = frozenset({
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
})


@dataclass(frozen=True)
class ToolExecutionSnapshot:
    """一次 Tool 执行的**对外只读**快照（Read Model；frozen）。

    Attributes:
        request_id:   一次 execute / chat 的关联 ID（request lifetime）。
        round:        Tool 执行轮次（从 1 开始）。
        tool_name:    实际执行的 Tool 名称。
        started_at:   执行开始时间（timezone-aware UTC）。
        finished_at:  执行结束时间（>= started_at）。
        duration_ms:  耗时毫秒。
        success:      是否成功（来自 ToolResult；不推断）。
        project_id:   授权作用域（``None`` = 未绑定）。
        tool_call_id: Function Calling 的 ToolCall id（上游未提供 → ``None``）。
        error_code:   结构化错误码槽位（当前 ``None``）。
        error_type:   安全错误分类（既有 ToolError 类名或 ``None``）。

    Note:
        本 DTO **不**做业务校验（校验权威在 ``ToolExecutionRecord``）；
        也**不**持有 ``ToolExecutionRecord``（只复制字段值）。
    """

    request_id: str
    round: int
    tool_name: str
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    success: bool
    project_id: str | None = None
    tool_call_id: str | None = None
    error_code: str | None = None
    error_type: str | None = None

    @classmethod
    def from_record(
        cls, record: ToolExecutionRecord
    ) -> "ToolExecutionSnapshot":
        """``ToolExecutionRecord`` → ``ToolExecutionSnapshot``（纯转换）。

        逐字段**显式**映射（不使用 ``vars`` / ``asdict`` / ``__dict__``）：
        Record 未来新增的字段不会自动进入对外 Read Model。

        Args:
            record: 内部执行事件（Step 14 frozen DTO）。

        Returns:
            ``ToolExecutionSnapshot``（新对象；不引用 ``record``）。

        Raises:
            TypeError: ``record`` 不是 ``ToolExecutionRecord``。
        """
        if not isinstance(record, ToolExecutionRecord):
            raise TypeError(
                "record 必须是 ToolExecutionRecord"
                f"（got {type(record).__name__}）"
            )
        return cls(
            request_id=record.request_id,
            round=record.round,
            tool_name=record.tool_name,
            started_at=record.started_at,
            finished_at=record.finished_at,
            duration_ms=record.duration_ms,
            success=record.success,
            project_id=record.project_id,
            tool_call_id=record.tool_call_id,
            error_code=record.error_code,
            error_type=record.error_type,
        )

    # ---------- 自检（防未来意外扩展字段 / 引入敏感信息） ----------

    def assert_field_whitelist(self) -> None:
        """字段白名单自检（测试调用；对外 Read Model 字段固定 11 项）。"""
        actual = {f.name for f in fields(self)}
        if actual != _ALLOWED_FIELDS:
            raise ValueError(
                f"ToolExecutionSnapshot 字段白名单被破坏: {sorted(actual)}"
                f"（允许: {sorted(_ALLOWED_FIELDS)}）"
            )
