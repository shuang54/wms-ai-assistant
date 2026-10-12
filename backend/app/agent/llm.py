"""AgentLLM Adapter —— Provider-native Function Calling（Phase 5 / OQ-P5-1 Option B）。

```text
AgentRuntime
      ↓  messages + tools（**已授权子集** 的 ToolDefinition）
ProviderAgentLLM（本模块；agent 侧适配器）
      ↓  definitions_to_openai_tools() → tools=[...]
LLMProvider.chat(messages, tools=...)
      ↓
LLMResponse（content / tool_calls / usage / finish_reason / metadata）
      ↓  严格映射（不猜测 / 不修复 / 不重试）
AgentLLMCompletion{decision, total_tokens, finish_reason}
```

职责（**只有这些**）：

    * Tool Schema 注入：``ToolDefinition`` → ``definitions_to_openai_tools()``
      （复用既有转换器，不重新实现 OpenAI schema）；
    * 决策映射：``LLMResponse`` → ``AgentDecision``（FINAL / TOOL_CALL）；
    * usage / finish_reason 透传（``total_tokens`` 不估算 / 不伪造）。

明确不负责（仍属 Agent Runtime / 其它层）：
    RuntimePolicy · Tool Authorization · Project Permission · Tool 执行 ·
    Conversation · 持久化 · 观测 · Streaming。

映射规则（OQ-P5-1 §28.4 / §28.5 冻结）：

```text
tool_calls 恰好 1 个（Provider 已强制 ≤1；本层再防御）
      → TOOL_CALL{tool_name=name, arguments=arguments, tool_call_id=id}
        伴随 content **显式丢弃**（不用于 FINAL、不进入历史）
tool_calls 为空
      ├── finish_reason == "length" → AgentDecisionParseError（截断不可信）
      ├── content 非空 str          → FINAL{content}
      └── content None / 空白       → AgentDecisionParseError
```

错误边界（与 Runtime 既有错误码一致）：

    * Provider / 传输 / 响应结构异常（含裸 ``str`` 返回、>1 tool call、
      ``arguments`` 非 dict）→ :class:`AgentLLMFailureError`（``LLM_FAILURE``）；
    * 决策本身非法（空 content / 截断）→ :class:`AgentDecisionParseError`
      （``INVALID_DECISION``）；
    * 两者都**不**触发任何自动重试。

边界（依赖方向）：

```text
agent.llm → backend.app.llm.tool_schema（schema 转换，唯一允许的 llm.* 依赖）
agent.llm → LLMProvider（structural typing，**不 import** client / deepseek_provider）
禁止：Agent → OpenAIClient / httpx / 数据库；禁止 Adapter 执行 Tool。
```
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from backend.app.agent.decision import (
    DECISION_FINAL,
    DECISION_TOOL_CALL,
    AgentDecision,
)
from backend.app.agent.errors import (
    AgentDecisionParseError,
    AgentLLMFailureError,
    AgentPolicyError,
)
from backend.app.agent.models import AgentLLMCompletion
from backend.app.llm.tool_schema import definitions_to_openai_tools
from backend.app.tools.base import ToolDefinition

logger = logging.getLogger(__name__)

__all__ = ["ProviderAgentLLM", "FINISH_REASON_LENGTH"]

#: 截断结束原因（Provider 原值；出现即视为不可信决策）。
FINISH_REASON_LENGTH: str = "length"


class ProviderAgentLLM:
    """把通用 ``LLMProvider`` 适配成 Agent 端口（provider-native function calling）。

    用法：

        adapter = ProviderAgentLLM(llm_provider)      # DeepSeekProvider 等
        completion = await adapter.complete(
            messages=messages,
            tools=authorized_definitions,             # Runtime 授权子集
        )
        completion.decision      # AgentDecision
        completion.total_tokens  # usage 透传（缺失 → None）

    Raises:
        AgentPolicyError: provider 未提供可调用的 ``chat()``（装配期 fail-closed）。
    """

    def __init__(self, provider: Any) -> None:
        if not callable(getattr(provider, "chat", None)):
            raise AgentPolicyError(
                "provider 必须提供可调用的 chat()"
                f"（got {type(provider).__name__}）—— "
                "generate() 无法提供 tool_calls / usage"
            )
        self._provider = provider

    @property
    def provider(self) -> Any:
        """底层通用 LLM Provider（只读；本层不修改其 contract）。"""
        return self._provider

    # ---------- Agent 端口 ----------

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: Sequence[ToolDefinition],
    ) -> AgentLLMCompletion:
        """调用 Provider（provider-native function calling）并映射为 AgentDecision。

        Args:
            messages: OpenAI 风格消息（system / user / assistant.tool_calls / tool）。
            tools:    **已授权** Tool 的 ``ToolDefinition``（Runtime 决定子集；
                      本层不做授权判断，也不会扩大范围）。

        Returns:
            :class:`AgentLLMCompletion`（decision + total_tokens + finish_reason）。

        Raises:
            AgentLLMFailureError:      Provider 异常 / 响应结构非法（``LLM_FAILURE``）。
            AgentDecisionParseError:   响应结构合法但决策非法（``INVALID_DECISION``）。
            AgentPolicyError:          messages / tools 形态非法（装配期错误）。
        """
        validated_messages = self._validate_messages(messages)
        validated_tools = self._validate_tools(tools)

        payload_tools = (
            definitions_to_openai_tools(validated_tools)
            if validated_tools
            else None
        )
        try:
            response = await self._provider.chat(
                validated_messages, tools=payload_tools
            )
        except Exception as exc:  # noqa: BLE001 —— Provider 失败归类，不重试
            failure = AgentLLMFailureError(
                "LLM_FAILURE: LLM Provider 调用失败"
                f"（{type(exc).__name__}）"
            )
            failure.__cause__ = exc
            logger.warning("Agent LLM Provider 调用失败: %s", failure)
            raise failure from exc

        return self._to_completion(response)

    # ---------- 内部：输入校验 ----------

    @staticmethod
    def _validate_messages(messages: Any) -> list[dict[str, Any]]:
        if not isinstance(messages, list) or not messages:
            raise AgentPolicyError("messages 必须是非空 list[dict]")
        for message in messages:
            if not isinstance(message, dict):
                raise AgentPolicyError("messages 每项必须是 dict")
            role = message.get("role")
            if not isinstance(role, str) or not role.strip():
                raise AgentPolicyError("messages 每项必须包含非空 role")
        return messages

    @staticmethod
    def _validate_tools(tools: Any) -> tuple[ToolDefinition, ...]:
        if isinstance(tools, str) or not isinstance(tools, (tuple, list)):
            raise AgentPolicyError(
                "tools 必须是 tuple/list[ToolDefinition]"
                f"（got {type(tools).__name__}）"
            )
        for definition in tools:
            if not isinstance(definition, ToolDefinition):
                raise AgentPolicyError(
                    "tools 每项必须是 ToolDefinition"
                    f"（got {type(definition).__name__}）"
                )
        return tuple(tools)

    # ---------- 内部：响应 → AgentDecision（严格映射） ----------

    def _to_completion(self, response: Any) -> AgentLLMCompletion:
        """``LLMResponse`` → ``AgentLLMCompletion``（结构非法 ⇒ LLM_FAILURE）。"""
        if isinstance(response, str):
            # tools= 已发送但 Provider 返回裸 str ⇒ 未走 tool calling 通道
            raise AgentLLMFailureError(
                "LLM_FAILURE: Provider 返回裸 str"
                "（tools= 已发送，期望 LLMResponse：content + tool_calls + usage）"
            )

        tool_calls = getattr(response, "tool_calls", None)
        if not isinstance(tool_calls, (tuple, list)):
            raise AgentLLMFailureError(
                "LLM_FAILURE: LLMResponse.tool_calls 缺失或类型非法"
                f"（got {type(tool_calls).__name__}）"
            )
        finish_reason = getattr(response, "finish_reason", None)
        total_tokens = self._extract_total_tokens(response)

        if len(tool_calls) > 1:
            # Provider 已强制单 Tool Call；此处为第二道防御（**不**取第一个）
            raise AgentLLMFailureError(
                f"LLM_FAILURE: 单次响应返回 {len(tool_calls)} 个 tool call"
                "（本阶段仅支持单个）"
            )

        if len(tool_calls) == 1:
            decision = self._map_tool_call(tool_calls[0])
            # content（若同时返回）显式丢弃：不用于 FINAL、不进入历史
            return AgentLLMCompletion(
                decision=decision,
                total_tokens=total_tokens,
                finish_reason=self._validate_finish_reason(finish_reason),
            )

        decision = self._map_final(response, finish_reason)
        return AgentLLMCompletion(
            decision=decision,
            total_tokens=total_tokens,
            finish_reason=self._validate_finish_reason(finish_reason),
        )

    @staticmethod
    def _extract_total_tokens(response: Any) -> int | None:
        """usage.total_tokens 透传（缺失 / 形态非法 → ``None``，**不估算**）。

        说明：usage 是观测元数据，其缺失按既有 Core Loop 语义处理
        （``token_budget_unverifiable=True``）；**不**为凑数字而失败整个 Run。
        """
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        value = getattr(usage, "total_tokens", None)
        if value is None or isinstance(value, bool) or not isinstance(value, int):
            return None
        if value < 0:
            return None
        return value

    @staticmethod
    def _validate_finish_reason(value: Any) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _map_tool_call(tool_call: Any) -> AgentDecision:
        """单个 ToolCall → ``AgentDecision(TOOL_CALL)``（id / name / arguments 不猜测）。"""
        call_id = getattr(tool_call, "id", None)
        name = getattr(tool_call, "name", None)
        arguments = getattr(tool_call, "arguments", None)

        if not isinstance(call_id, str) or not call_id.strip():
            raise AgentLLMFailureError(
                "LLM_FAILURE: ToolCall.id 缺失或非法（Provider 契约要求非空）"
            )
        if not isinstance(name, str) or not name.strip():
            raise AgentLLMFailureError(
                "LLM_FAILURE: ToolCall.name 缺失或非法（Provider 契约要求非空）"
            )
        if not isinstance(arguments, dict):
            # Provider 已把 arguments 解析为 dict；此处不重新 json.loads、不修复
            raise AgentLLMFailureError(
                "LLM_FAILURE: ToolCall.arguments 必须是 dict"
                f"（got {type(arguments).__name__}）"
            )

        try:
            return AgentDecision(
                type=DECISION_TOOL_CALL,
                tool_name=name,
                arguments=arguments,
                tool_call_id=call_id,
            )
        except AgentDecisionParseError as exc:  # 防御：DTO 契约不一致
            raise AgentLLMFailureError(
                f"LLM_FAILURE: ToolCall → AgentDecision 映射失败（{exc}）"
            ) from exc

    @staticmethod
    def _map_final(response: Any, finish_reason: Any) -> AgentDecision:
        """无 tool_calls ⇒ FINAL（截断 / 空 content 一律 INVALID_DECISION）。"""
        if finish_reason == FINISH_REASON_LENGTH:
            raise AgentDecisionParseError(
                "INVALID_DECISION: 响应被截断（finish_reason=length），不可作为决策"
            )
        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise AgentDecisionParseError(
                "INVALID_DECISION: 无 tool_calls 且 content 为空"
                "（不把 None / 空串 / 纯空白当作成功）"
            )
        return AgentDecision(type=DECISION_FINAL, content=content)
