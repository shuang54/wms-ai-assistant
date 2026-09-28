"""Assistant Trace Scope（Phase 3.12 Step 36）。

用途：把**一次 Assistant 请求**的 trace id（``request_id``，Phase 3.12
Step 35 的 Assistant Trace ID）在调用栈内**隐式**传播，使 LLM Usage
持久化边界在不修改 LLM 领域模块（client / observability）与不修改 RAG /
Text-to-SQL 核心逻辑的前提下，仍能把 usage 事实关联到这次 Assistant 请求。

    AIOrchestratorService.execute()
        request_id = new_request_id()                 ← 唯一起点（Step 35）
            ↓ with assistant_trace_scope(request_id)
        Router / RAG / Tool / Text-to-SQL（任意深度）
            ↓ LLM 调用（Client → Accounting Sink）
        LLMUsagePersistenceService.persist()
            ↓ current_assistant_request_id()
        ai_ops.llm_usage_record.assistant_request_id = request_id

为什么用 contextvar（而不是新增函数参数）：

    * **零侵入**：不需要给 LLM Client / RagService / TextToSQLService /
      ToolExecutionService 增加参数（这些模块在本阶段**禁止修改**）；
    * 并发安全：``contextvars`` 是 per-task 上下文，并发请求天然隔离
      （不使用全局变量 / 不共享可变状态）；
    * 线程边界可用：``asyncio.to_thread``（Phase 3.10.15 Runtime Bridge）
      会把当前 context 复制进 worker 线程，因此 ``persist()`` 在
      worker 线程内仍能读到同一个 request_id。

边界与限制：

    * 本模块**只**保存 / 读取一个不可变字符串；不保存 question / prompt /
      messages / tool arguments / SQL / 凭据；
    * 未绑定（旧链路：``/api/chat`` / ``/api/rag/answer`` /
      ``/api/chat/with-tools``，或直接调用 Service 的脚本 / 测试）
      → ``current_assistant_request_id()`` 返回 ``None``
      → Usage Record 的 ``assistant_request_id`` 保持 NULL（向后兼容）；
    * 本模块不生成 ID（唯一生成点仍是 Orchestrator 的
      ``new_request_id()``）；不读取 / 不修改 Usage Record；不做聚合 / 持久化。
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator

logger = logging.getLogger(__name__)

__all__ = [
    "assistant_trace_scope",
    "current_assistant_request_id",
    "ASSISTANT_REQUEST_ID_MAX_LENGTH",
]

#: 与 ``ai_ops.llm_usage_record.assistant_request_id``（VARCHAR(128)）保持一致。
ASSISTANT_REQUEST_ID_MAX_LENGTH: int = 128

#: 当前执行上下文的 Assistant Trace ID（默认 None = 未绑定 / 旧链路）。
_ASSISTANT_REQUEST_ID: ContextVar[str | None] = ContextVar(
    "assistant_request_id", default=None
)


def _validate_request_id(request_id: object) -> str:
    """校验 Assistant Trace ID（非空 str；不生成、不推断、不截断）。"""
    if not isinstance(request_id, str):
        raise ValueError(
            "assistant request_id 必须是 str"
            f"（当前: {type(request_id).__name__}）"
        )
    if not request_id:
        raise ValueError("assistant request_id 不能为空字符串")
    if len(request_id) > ASSISTANT_REQUEST_ID_MAX_LENGTH:
        raise ValueError(
            "assistant request_id 超出长度上限 "
            f"（{len(request_id)} > {ASSISTANT_REQUEST_ID_MAX_LENGTH}）"
        )
    return request_id


@contextmanager
def assistant_trace_scope(request_id: str) -> Iterator[str]:
    """在 ``with`` 块内绑定 Assistant Trace ID（退出时**必定**恢复原值）。

    用法：

        with assistant_trace_scope(request_id):
            ...  # 任意深度的 LLM 调用都会关联到 request_id

    Args:
        request_id: 非空字符串（由 Orchestrator 的 ``new_request_id()``
            生成；本函数不生成 ID）。

    Yields:
        request_id（原样返回，便于调用方直接使用）。

    Raises:
        ValueError: request_id 非 str / 空 / 超长。

    Note:
        支持嵌套（内层覆盖外层，退出时逐层恢复）；未捕获异常同样恢复
        （``finally``），因此不会把 trace id 泄漏到后续请求。
    """
    validated = _validate_request_id(request_id)
    token: Token[str | None] = _ASSISTANT_REQUEST_ID.set(validated)
    try:
        yield validated
    finally:
        _ASSISTANT_REQUEST_ID.reset(token)


def current_assistant_request_id() -> str | None:
    """返回当前上下文的 Assistant Trace ID；未绑定 → ``None``。

    调用方（LLM Usage Persistence Boundary）必须把 ``None`` 原样写入
    （NULL），**不得**生成 / 推断 / 回填任何替代值。
    """
    return _ASSISTANT_REQUEST_ID.get()
