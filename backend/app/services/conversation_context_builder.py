"""Conversation Context Builder（Phase 4.1 Step 13 —— 最小纯函数实现）。

唯一职责（Step 13 §5 冻结）：

```text
ConversationTurn[]（业务层已按 created_at ASC, turn_id ASC 提供）
        ↓
LLM Context（AIOrchestrator.execute 的 context 参数）
```

真实 contract（阅读代码确认，**未修改**任何既有接口）：

```text
AIOrchestratorService.execute(question, *, context: str | None = None)
AIRouterService.route(question, *, context: str | None = None)
```

因此：

    * ``build_context(turns)`` 返回 ``str | None`` —— 可直接传给
      ``execute(..., context=...)``（真实 contract）；
    * ``build_messages(turns)`` 提供结构化一对一映射
      （``{"role": "user" | "assistant", "content": ...}``），
      供未来 messages 形态的 LLM 调用使用 —— **不新增** ConversationContext
      大型 DTO（§6）。

边界（Step 13 §4 / §5 / §12 / §15 / §16 冻结）：

    * 输入已经是 ConversationTurn DTO / duck-type（只需 ``role`` / ``content``）
      —— **不**接收 conversation_id，**不**调用 ConversationService /
      Repository / SQLAlchemy / DB；
    * 一对一转换：**不**判断哪条是 current turn（由未来 Application Layer
      决定传入 history 还是 history + current），**不**排序；
    * 只复制 ``role`` / ``content`` —— **不**读取 turn_id / conversation_id /
      assistant_request_id / created_at；
    * ``content`` **原样**保留（不 strip / truncate / normalize / escape）；
    * 纯函数：deterministic / stateless / 无 IO / 无环境依赖 /
      无全局可变状态；
    * 未知 role（SYSTEM / TOOL / UNKNOWN 等）→ ``ValueError``
      （**不**静默转成 user）。

与既有 ``services/context_builder.py``（RAG Context Builder）的区别：

```text
RAG ContextBuilder      : VectorSearchResult[] → ContextBuildResult（chunk 序列化/截断）
Conversation Builder    : ConversationTurn[]   → str | None（对话历史 → context）
```

用途不同、输入不同 —— 本模块**不是** RAG Builder 的复制（审计断言无 chunk 相关符号）。

不在本层（Deferred）：Memory / Summary / Token 预算 / Token counting /
Embedding / Reranker / Prompt 模板 / DB 读取 / Context 缓存 /
ChatApplicationService 接线（Step 14+）。
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Protocol, TypeAlias

__all__ = [
    "ConversationContextBuilder",
    "ConversationTurnLike",
    "ContextMessage",
    "build_context",
    "build_messages",
    "USER_TURN_ROLE",
    "ASSISTANT_TURN_ROLE",
    "USER_CONTEXT_ROLE",
    "ASSISTANT_CONTEXT_ROLE",
    "MESSAGE_LINE_FMT",
    "LINE_SEPARATOR",
]

#: Turn role（= 持久化层 ``TURN_ROLE_USER`` / ``TURN_ROLE_ASSISTANT``；
#: 本模块**不 import** ``backend.app.db`` —— 一致性由契约测试锁定）。
USER_TURN_ROLE: Final[str] = "USER"
ASSISTANT_TURN_ROLE: Final[str] = "ASSISTANT"

#: LLM context role（映射结果；§7 冻结：USER → user / ASSISTANT → assistant）。
USER_CONTEXT_ROLE: Final[str] = "user"
ASSISTANT_CONTEXT_ROLE: Final[str] = "assistant"

#: 渲染格式：每行 ``"role: content"``（分隔符 = 单个换行）。
MESSAGE_LINE_FMT: Final[str] = "{role}: {content}"
LINE_SEPARATOR: Final[str] = "\n"

#: 结构化消息（只含 role / content；轻量 TypeAlias，不新增 DTO 类）。
ContextMessage: TypeAlias = dict[str, str]


class ConversationTurnLike(Protocol):
    """输入契约：Builder **只**读取 ``role`` / ``content`` 两个属性。"""

    @property
    def role(self) -> str: ...

    @property
    def content(self) -> str: ...


def _context_role(turn_role: str) -> str:
    """Turn role → context role；未知 role → ``ValueError``（不静默转换）。"""
    if turn_role == USER_TURN_ROLE:
        return USER_CONTEXT_ROLE
    if turn_role == ASSISTANT_TURN_ROLE:
        return ASSISTANT_CONTEXT_ROLE
    raise ValueError(
        f"不支持的 turn role: {turn_role!r}"
        f"（允许: {USER_TURN_ROLE} / {ASSISTANT_TURN_ROLE}）"
    )


def build_messages(
    turns: Sequence[ConversationTurnLike],
) -> tuple[ContextMessage, ...]:
    """Turn[] → 结构化消息（一对一；顺序保持；content 原样）。

    Args:
        turns: 已排序（created_at ASC, turn_id ASC）的会话历史；本函数
            **不排序**、**不裁剪**、**不判断 current turn**。

    Returns:
        ``tuple[{"role": ..., "content": ...}, ...]``；空输入 → ``()``。

    Raises:
        ValueError: ``role`` 非 str / 未知 role；``content`` 非 str。
    """
    messages: list[ContextMessage] = []
    for turn in turns:
        role = getattr(turn, "role", None)
        if not isinstance(role, str):
            raise ValueError(
                f"turn.role 必须是 str（当前: {type(role).__name__}）"
            )
        content = getattr(turn, "content", None)
        if not isinstance(content, str):
            raise ValueError(
                f"turn.content 必须是 str（当前: {type(content).__name__}）"
            )
        messages.append({"role": _context_role(role), "content": content})
    return tuple(messages)


def build_context(turns: Sequence[ConversationTurnLike]) -> str | None:
    """Turn[] → AIOrchestrator context（``str | None``，真实 contract）。

    空输入 → ``None``（真实 contract 支持 None；**不**伪造 ``{"messages": []}``）；
    非空 → 每行 ``"role: content"``，以 ``"\\n"`` 连接（顺序 = 输入顺序）。

    Raises:
        同 :func:`build_messages`。
    """
    messages = build_messages(turns)
    if not messages:
        return None
    return LINE_SEPARATOR.join(
        MESSAGE_LINE_FMT.format(role=message["role"], content=message["content"])
        for message in messages
    )


class ConversationContextBuilder:
    """无状态包装（便于未来 DI 注入；行为与模块级函数完全一致）。"""

    def build_messages(
        self, turns: Sequence[ConversationTurnLike]
    ) -> tuple[ContextMessage, ...]:
        """见 :func:`build_messages`。"""
        return build_messages(turns)

    def build_context(self, turns: Sequence[ConversationTurnLike]) -> str | None:
        """见 :func:`build_context`。"""
        return build_context(turns)
