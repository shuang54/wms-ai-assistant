"""Tool Execution Record（Phase 3.11 Step 14）—— 内部 Execution Record 数据契约。

一次 Tool 执行的**不可变元数据事件**（Audit / Observability / Metrics /
Tracing 的稳定数据模型基础）：

    ToolExecutionContext（Step 13：谁 / 哪个 request / 哪个 project /
                          哪个 tool call / 第几轮）
            +
    ToolResult（Step 12：成功 / 失败）
            +
    Timing（started_at / finished_at / duration_ms）
            ↓
    ToolExecutionRecord            （frozen DTO；**只描述执行事实**）

记录什么（白名单字段）：

    request_id / project_id / tool_call_id / round      ← 来自 Execution Context
    tool_name                                           ← 来自 execute() 实参
    started_at / finished_at / duration_ms               ← 计时
    success                                             ← 来自 ToolResult
    error_code / error_type                             ← 安全错误分类槽位

**不**记录（安全边界，§九）：

    Tool arguments / ToolResult.data / SQL / LLM prompt / LLM response /
    API Key / password / DATABASE_URL / connection string /
    Authorization header / 完整 traceback / 原始 exception message

设计纪律（本阶段严格范围）：

* **零持久化**：不落库、无 Repository / Migration / audit table /
  dashboard / metrics 聚合 / tracing backend；Record 目前**不被写往任何地方**；
* **不修改既有 Contract**：``ToolResult`` 字段不变；
  ``ToolExecutionService.execute()`` 仍返回 ``ToolResult``（不是 Record、
  不是 tuple）；``ToolRegistry`` / Tool Handler / API / LLM messages 均不变；
* **不新增 Observer Framework**：当前执行边界没有合适的（既有）扩展点 →
  本阶段只提供 DTO + ``from_execution()`` + 测试 + 架构契约 C16；
  生产接线（在边界内采集 timing 并构建 Record）= Deferred；
* 纯函数式：``from_execution()`` 无 IO / 无日志 / 无 DB / 无 LLM / 无 Tool 调用；
  计时由调用方完成（wall clock 用 :func:`now_utc`，时长用
  :func:`elapsed_ms` = ``time.perf_counter()`` 差值；**不**使用
  ``datetime.now() - datetime.now()`` 作为唯一精度来源）。

错误字段说明（§八）：当前 ``ToolResult`` 只有 ``error: str | None``
（无结构化 ``error_code`` / ``cause_type`` 字段），因此：

* ``error_code``：**不发明**错误码体系 → 恒为 ``None``（槽位保留，
  未来 ``ToolResult`` 提供结构化 code 后再映射；构造时可显式传入）；
* ``error_type``：复用项目既有 ``ToolError`` 家族**类名**（allow-list：
  ToolValidationError / ToolExecutionError / ToolNotFoundError /
  ToolAlreadyRegisteredError）——仅当 ``ToolResult.error`` 以该前缀开头时
  直接映射，无法确定 → ``None``（绝不解析 / 推断 message 文本）。
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from typing import Final

from backend.app.services.tool_execution_context import ToolExecutionContext
from backend.app.tools.base import ToolResult

__all__ = [
    "ToolExecutionRecord",
    "now_utc",
    "elapsed_ms",
]

#: Record 字段白名单（防未来意外引入业务数据 / 敏感字段）
_ALLOWED_FIELDS: Final[frozenset[str]] = frozenset({
    "request_id",
    "project_id",
    "tool_call_id",
    "round",
    "tool_name",
    "started_at",
    "finished_at",
    "duration_ms",
    "success",
    "error_code",
    "error_type",
})

#: error_code 允许形态（snake_case；未来结构化错误码）
_ERROR_CODE_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

#: error_type 允许形态（异常类名；不允许 message / traceback 文本）
_ERROR_TYPE_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

#: 可从 ``ToolResult.error`` 前缀直接映射的项目既有 ToolError 家族类名
_TOOL_ERROR_TYPES: Final[frozenset[str]] = frozenset({
    "ToolValidationError",
    "ToolExecutionError",
    "ToolNotFoundError",
    "ToolAlreadyRegisteredError",
})


def now_utc() -> datetime:
    """统一 wall-clock 时间戳约定（timezone-aware UTC）。

    与项目既有约定一致（``datetime.now(timezone.utc)``）；**不**使用
    naive datetime（Record 校验会拒绝 naive 时间戳）。
    """
    return datetime.now(timezone.utc)


def elapsed_ms(started_perf: float) -> float:
    """由 ``time.perf_counter()`` 起点计算时长（毫秒）。

    Args:
        started_perf: 执行前记录的 ``time.perf_counter()`` 值。

    Returns:
        毫秒（float，>= 0；单调时钟差值，不受系统时钟调整影响）。
    """
    return (time.perf_counter() - started_perf) * 1000.0


def _error_type_from_tool_result(result: ToolResult) -> str | None:
    """从 ``ToolResult.error`` 前缀复用既有 ToolError 类名（不推断）。

    仅当 error 以白名单中的**类名 + ":"** 开头时返回该类名；
    其它情况（Registry 级 ``Tool 未注册`` / ``参数校验失败`` 等无类名前缀的
    消息）→ ``None``（绝不解析 message 正文、绝不猜测）。
    """
    if result.success or not result.error:
        return None
    head = result.error.split(":", 1)[0].strip()
    return head if head in _TOOL_ERROR_TYPES else None


@dataclass(frozen=True)
class ToolExecutionRecord:
    """一次 Tool 执行的元数据记录（frozen；不可变）。

    Attributes:
        request_id:   一次 chat 的关联 ID（来自 Execution Context；不重新生成）。
        round:        Tool 执行轮次（来自 Execution Context；>= 1）。
        tool_name:    实际执行的 Tool 名称（来自 ``execute()`` 实参；
                      **不**从 question / LLM message / arguments 推断）。
        started_at:   执行开始时间（timezone-aware UTC）。
        finished_at:  执行结束时间（timezone-aware UTC；>= started_at）。
        duration_ms:  实际耗时毫秒（来自 ``time.perf_counter()`` 差值；>= 0）。
        success:      是否成功（**直接来自** ``ToolResult.success``；不推断）。
        project_id:   授权作用域（来自 Execution Context；None = 未绑定）。
        tool_call_id: LLM ToolCall id 原值（来自 Execution Context）。
        error_code:   结构化错误码槽位（当前 ToolResult 无该信息 → None；
                      不发明错误码体系）。
        error_type:   安全错误分类（既有 ToolError 类名或显式传入；无 → None；
                      **不含** exception message / traceback）。

    Raises:
        ValueError: 字段非法（空 request_id / round < 1 / naive 时间戳 /
                    duration < 0 / success 与 error 字段矛盾 / 形态不合法等）。

    Example:
        >>> started = time.perf_counter()
        >>> t0 = now_utc()
        >>> record = ToolExecutionRecord.from_execution(
        ...     context=ctx, tool_name="get_inventory", result=result,
        ...     started_at=t0, finished_at=now_utc(),
        ...     duration_ms=elapsed_ms(started),
        ... )
        >>> record.success
        True
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

    # ------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError("request_id 必须是非空 str")
        if isinstance(self.round, bool) or not isinstance(self.round, int):
            raise ValueError(
                f"round 必须是 int（got {type(self.round).__name__}）"
            )
        if self.round < 1:
            raise ValueError(f"round 必须 >= 1（got {self.round}）")
        if not isinstance(self.tool_name, str) or not self.tool_name.strip():
            raise ValueError("tool_name 必须是非空 str")
        if not isinstance(self.success, bool):
            raise ValueError(
                f"success 必须是 bool（got {type(self.success).__name__}）"
            )
        for field_name in ("project_id", "tool_call_id"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"{field_name} 必须是非空 str 或 None"
                    f"（got {type(value).__name__}）"
                )
        self._validate_timing()
        self._validate_error_fields()

    def _validate_timing(self) -> None:
        for field_name in ("started_at", "finished_at"):
            value = getattr(self, field_name)
            if not isinstance(value, datetime):
                raise ValueError(
                    f"{field_name} 必须是 datetime"
                    f"（got {type(value).__name__}）"
                )
            if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
                raise ValueError(
                    f"{field_name} 必须是 timezone-aware（建议 now_utc()）"
                )
        if self.finished_at < self.started_at:
            raise ValueError(
                "finished_at 不能早于 started_at"
                f"（{self.finished_at} < {self.started_at}）"
            )
        duration = self.duration_ms
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise ValueError(
                f"duration_ms 必须是数值（got {type(duration).__name__}）"
            )
        if math.isnan(duration) or math.isinf(duration):
            raise ValueError("duration_ms 不允许 NaN / Infinity")
        if duration < 0:
            raise ValueError(f"duration_ms 不允许负数（got {duration}）")

    def _validate_error_fields(self) -> None:
        if self.error_code is not None and (
            not isinstance(self.error_code, str)
            or not _ERROR_CODE_RE.match(self.error_code)
        ):
            raise ValueError(
                "error_code 必须是 snake_case 标识符或 None"
                f"（got {self.error_code!r}）"
            )
        if self.error_type is not None and (
            not isinstance(self.error_type, str)
            or not _ERROR_TYPE_RE.match(self.error_type)
        ):
            raise ValueError(
                "error_type 必须是异常类名（标识符）或 None"
                f"（got {self.error_type!r}）"
            )
        if self.success and (self.error_code or self.error_type):
            raise ValueError(
                "success=True 时 error_code / error_type 必须为 None"
                "（不伪造失败分类）"
            )

    # ------------------------------------------------------------
    # 自检（防未来意外扩展字段 / 引入业务数据）
    # ------------------------------------------------------------

    def assert_field_whitelist(self) -> None:
        """字段白名单自检（测试调用；不得引入 arguments / data / SQL 等）。"""
        actual = {f.name for f in fields(self)}
        if actual != _ALLOWED_FIELDS:
            raise ValueError(
                f"ToolExecutionRecord 字段白名单被破坏: {sorted(actual)}"
                f"（允许: {sorted(_ALLOWED_FIELDS)}）"
            )

    # ------------------------------------------------------------
    # 纯构造（§十一）
    # ------------------------------------------------------------

    @classmethod
    def from_execution(
        cls,
        *,
        context: ToolExecutionContext,
        tool_name: str,
        result: ToolResult,
        started_at: datetime,
        finished_at: datetime,
        duration_ms: float,
        error_code: str | None = None,
        error_type: str | None = None,
    ) -> "ToolExecutionRecord":
        """由「Context + ToolResult + Timing」纯构造 Record。

        Pure / Deterministic：无 IO、无日志、无 DB、无 LLM、无 Tool 调用；
        不修改 ``context`` / ``result``；不生成 request_id；
        不把 arguments / ToolResult.data / error message 写入 Record。

        Args:
            context:     本次执行的 Execution Context（request_id /
                         project_id / tool_call_id / round 原值映射）。
            tool_name:   实际执行的 Tool 名称（``execute()`` 实参；
                         必须与 ``result.tool_name`` 一致）。
            result:      Tool 执行结果（``success`` 直接映射；
                         ``error`` 正文**不**写入 Record）。
            started_at:  执行开始时间（timezone-aware）。
            finished_at: 执行结束时间（timezone-aware）。
            duration_ms: ``time.perf_counter()`` 差值（毫秒）。
            error_code:  显式结构化错误码（默认 None；不发明错误码）。
            error_type:  显式错误类名（默认由 ``result.error`` 前缀的既有
                         ToolError 类名映射；不解析 message 正文）。

        Returns:
            ``ToolExecutionRecord``（不可变）。

        Raises:
            TypeError:  context / result 类型不符。
            ValueError: 字段校验失败（含 tool_name 与 result.tool_name 不一致）。
        """
        if not isinstance(context, ToolExecutionContext):
            raise TypeError(
                "context 必须是 ToolExecutionContext"
                f"（got {type(context).__name__}）"
            )
        if not isinstance(result, ToolResult):
            raise TypeError(
                f"result 必须是 ToolResult（got {type(result).__name__}）"
            )
        if result.tool_name != tool_name:
            raise ValueError(
                "tool_name 必须来自实际执行的 Tool"
                f"（execute={tool_name!r}, result={result.tool_name!r}）"
            )
        return cls(
            request_id=context.request_id,
            round=context.round,
            tool_name=tool_name,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            success=result.success,
            project_id=context.project_id,
            tool_call_id=context.tool_call_id,
            error_code=error_code,
            error_type=(
                error_type
                if error_type is not None
                else _error_type_from_tool_result(result)
            ),
        )
