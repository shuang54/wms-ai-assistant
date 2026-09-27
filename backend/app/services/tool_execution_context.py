"""Tool Execution Context（Phase 3.11 Step 13）。

一次 ToolChat execution（``ToolChatService.chat()``）的**运行时**上下文：

    ToolChatService                 （Context 创建者：
                                      request_id 一次 / 每轮 tool_call_id + round）
        ↓
    ToolExecutionContext            （frozen DTO；字段白名单 4 项）
        ├── request_id              （一次 chat 一个；非 DB id / 非 secret）
        ├── project_id              （授权作用域，来自执行边界；LLM 不可控制）
        ├── tool_call_id            （LLM ToolCall.id 原值，不重新生成）
        └── round                   （Tool 执行轮次，从 1 开始）
        ↓
    ToolExecutionService            （接收并校验；不生成 request_id /
                                     不修改 Context / 不持久化 / 不 retry）
        ↓
    ToolRegistry                    （**不知道** Context：仍是
                                     Schema Validation + Handler dispatch）
        ↓
    Tool Handler                    （**不知道** Context：仍只接收 arguments）

设计边界（本阶段严格范围）：

* Context 是 **Runtime Execution Context**，不是 Prompt Context：
  不进入 Tool arguments、不进入 LLM messages（user / assistant / tool /
  system）、不进入 API 请求 / 响应 contract；
* **零持久化**：不落库、无 audit table / tool_call_log / Tool history /
  dashboard / tracing backend / request-id middleware；
* **不新增第二个 request-id 体系**：项目既有的 ``request_id`` 属于 LLM
  Provider 用量账本（``LLMResponse.metadata["request_id"]`` /
  ``llm_usage_record.request_id``），语义是 *provider 响应 ID*
  （每次 LLM 调用一个、可能为 None、只能在 LLM 调用**之后**取得）；
  本 DTO 的 ``request_id`` 是 *一次用户请求 / 一次 chat 的关联 ID*
  （覆盖该 chat 内全部 Tool 执行轮次）→ 两者生命周期不同，不复用、
  不混用（如实记录于 Step 13 evaluation 文档 §Limitations）；
* 字段仅 4 个：本阶段**不扩展**（无 user / tenant / session / trace /
  deadline / auth / permissions / retry 等字段）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, fields

__all__ = [
    "ToolExecutionContext",
    "new_request_id",
]


#: Context 字段白名单（防未来意外引入敏感 / 业务字段）
_ALLOWED_FIELDS: frozenset[str] = frozenset(
    {"request_id", "project_id", "tool_call_id", "round"}
)


def new_request_id() -> str:
    """生成一次 ToolChat execution 的 ``request_id``。

    策略（Phase 3.11 Step 13 §十七）：

        * 每次 ``ToolChatService.chat()`` 调用一次 → 同一次 chat 内的
          多轮 Tool 执行**共享**同一个 request_id；
        * 使用 ``uuid.uuid4()``（标准库）：**不是**数据库 ID、
          **不含** secret / 用户输入 / Tool 名称，
          不依赖 Tool 名称（同一次 chat 与 Tool 种类无关）；
        * 不落库、不出现在 API response、不进入 LLM messages。

    Returns:
        36 字符 UUID 字符串（非空、稳定、进程内唯一）。
    """
    return str(uuid.uuid4())


@dataclass(frozen=True)
class ToolExecutionContext:
    """Tool 执行上下文（frozen；不可变）。

    Attributes:
        request_id:    一次 chat 的关联 ID（``new_request_id()`` 生成；
                       非空；同一次 chat 内恒定）。
        round:         Tool 执行轮次（从 1 开始；每轮 +1；
                       当前 contract 为 one round = one Tool execution）。
        project_id:    授权作用域（来自 ``ToolExecutionService.project_id``，
                       即服务器端 ProjectRegistry 解析结果）；
                       ``None`` = 该边界未绑定项目作用域。
                       **不来自** LLM / Tool arguments。
        tool_call_id:  LLM Function Calling 返回的 ``ToolCall.id`` 原值
                       （不重新生成 / 不替换）；上游未提供 → ``None``。

    Raises:
        ValueError: 字段非法（空 request_id / round < 1 / 类型不符）。

    Example:
        >>> ctx = ToolExecutionContext(
        ...     request_id="3f1c…", round=1, project_id="project-a",
        ...     tool_call_id="call_001",
        ... )
        >>> ctx.round
        1
    """

    request_id: str
    round: int
    project_id: str | None = None
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError(
                "request_id 必须是非空 str"
                f"（got {type(self.request_id).__name__}）"
            )
        if isinstance(self.round, bool) or not isinstance(self.round, int):
            raise ValueError(
                f"round 必须是 int（got {type(self.round).__name__}）"
            )
        if self.round < 1:
            raise ValueError(f"round 必须 >= 1（got {self.round}）")
        for field_name in ("project_id", "tool_call_id"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"{field_name} 必须是非空 str 或 None"
                    f"（got {type(value).__name__}）"
                )

    # ---------- 自检（防未来意外扩展字段 / 引入敏感信息） ----------

    def assert_field_whitelist(self) -> None:
        """字段白名单自检（测试调用；本阶段仅 4 个字段）。"""
        actual = {f.name for f in fields(self)}
        if actual != _ALLOWED_FIELDS:
            raise ValueError(
                f"ToolExecutionContext 字段白名单被破坏: {sorted(actual)}"
                f"（允许: {sorted(_ALLOWED_FIELDS)}）"
            )
