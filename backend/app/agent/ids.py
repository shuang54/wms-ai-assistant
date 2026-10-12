"""Agent Run 身份生成 + Agent Run Scope（Phase 5 — AgentRun Persistence, P-2；
Phase 5 P-4 Integration — Agent Run Scope）。

职责（两个，均极小）：

```text
new_agent_run_id()      一次 AgentRuntime.run() 的**执行身份**（run_id）
agent_run_scope(run_id) 把该身份绑定到**当前任务**的上下文（ContextVar）
current_agent_run_id()  读取当前 Agent Run Scope 内的 run_id（不在 scope ⇒ None）
```

边界（严格）：

    * 身份由 **Agent Runtime 层**签发（``AgentRuntime.run()`` 内调用一次）；
      **Recorder / Repository / Orchestrator / 写入端口不得生成 run_id**
      （Phase 5 §30.7 / §十七：run_id = Agent 执行 identity）；
    * ``agent_run_scope`` **不接受缺省 run_id**（绝不内部生成）—— 只做绑定/恢复；
    * 与 `assistant_request_id`（AI 请求 correlation）、
      `turn_id`（Conversation 消息）、`tool_call_id`（协议关联）**互不相同**；
    * Scope 只保存 **run_id 一个字符串**：不含 prompt / context / tool result /
      credentials / 任何业务数据（§三十三 安全边界）；
    * 使用 ``ContextVar``（per-task 隔离）：嵌套（内层覆盖、退出恢复外层）、
      并发（asyncio task / 线程各自隔离）安全；**禁止** global mutable state。

本模块零依赖（std lib only；不 import DB / LLM / Tool / Conversation）。
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

__all__ = [
    "new_agent_run_id",
    "agent_run_scope",
    "current_agent_run_id",
]

#: 当前 Agent Run 身份（per-task 上下文；**只存 run_id**）。
#: 由 :func:`agent_run_scope` 写入 / 恢复；不在 scope 内 ⇒ ``None``。
_AGENT_RUN_ID: ContextVar[str | None] = ContextVar("agent_run_id", default=None)


def new_agent_run_id() -> str:
    """签发一个新的 Agent Run 身份（`uuid4` 字符串；与既有 ID 风格一致）。

    Returns:
        ``str``（标准 uuid4 文本；不截断、不含语义信息）。

    Note:
        调用点唯一 = ``AgentRuntime.run()``（每次执行签发一个）；
        持久化层**只读取** ``AgentRuntimeResult.run_id``，不重新生成。
    """
    return str(uuid.uuid4())


def current_agent_run_id() -> str | None:
    """读取当前 Agent Run Scope 内的 ``run_id``。

    Returns:
        scope 内 → ``run_id``（非空 str）；不在 scope 内 → ``None``。

    Note:
        **绝不**回退/推断（不得用 ``request_id`` / ``tool_call_id`` /
        Conversation ``Idempotency-Key`` 充当 run_id）：无法确定身份时，
        调用方必须 **fail-closed**（不写 / 不继续）。
    """
    return _AGENT_RUN_ID.get()


@contextmanager
def agent_run_scope(run_id: str) -> Iterator[str]:
    """在 ``with`` 块内绑定 Agent Run 身份（退出时**必定**恢复原值）。

    用法：

        with agent_run_scope(run_id):
            ...  # 该 Run 内任意深度的 Tool 执行都能读到 current_agent_run_id()

    Args:
        run_id: 非空字符串（由 ``AgentRuntime.run()`` 的
            ``new_agent_run_id()`` 生成；**本函数不生成**）。

    Yields:
        run_id（原样返回，便于调用方直接使用）。

    Raises:
        ValueError: run_id 非 str / 空。

    Note:
        实现 = ``ContextVar.set`` + ``try/finally`` + ``Token.reset``；
        支持嵌套（内层覆盖，退出逐层恢复）与并发（per-task 上下文隔离）。
        禁止 ``global`` / 模块级可变变量（避免 async / 嵌套请求污染）。
    """
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError(
            "run_id 必须是非空 str（Agent Run Scope 只绑定已有身份，不生成）"
            f"（当前: {type(run_id).__name__}）"
        )
    token: Token[str | None] = _AGENT_RUN_ID.set(run_id)
    try:
        yield run_id
    finally:
        _AGENT_RUN_ID.reset(token)
