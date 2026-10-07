"""Agent Run 身份生成（Phase 5 — AgentRun Persistence, P-2）。

职责（单一）：

```text
new_agent_run_id()  一次 AgentRuntime.run() 的**执行身份**（run_id）
```

边界（严格）：

    * 身份由 **Agent Runtime 层**签发（``AgentRuntime.run()`` 内调用一次）；
      **Recorder / Repository / Orchestrator 不得重新生成第二个 run_id**
      （Phase 5 §30.7 / §十七：run_id = Agent 执行 identity）；
    * 与 `assistant_request_id`（AI 请求 correlation）、
      `turn_id`（Conversation 消息）、`tool_call_id`（协议关联）**互不相同**；
    * 风格与项目既有 ID 生成器一致（`new_request_id()` /
      ``new_conversation_id()`` 均使用标准库 ``uuid.uuid4()``）。

本模块零依赖（不 import DB / LLM / Tool / Conversation）。
"""
from __future__ import annotations

import uuid

__all__ = ["new_agent_run_id"]


def new_agent_run_id() -> str:
    """签发一个新的 Agent Run 身份（`uuid4` 字符串；与既有 ID 风格一致）。

    Returns:
        ``str``（标准 uuid4 文本；不截断、不含语义信息）。

    Note:
        调用点唯一 = ``AgentRuntime.run()``（每次执行签发一个）；
        持久化层**只读取** ``AgentRuntimeResult.run_id``，不重新生成。
    """
    return str(uuid.uuid4())
