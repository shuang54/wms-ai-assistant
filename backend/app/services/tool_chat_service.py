"""Tool Chat Service（Phase 3.6.2：LLM Function Calling 接入 Tool Framework）。

链路（任务书 §一）：

    用户问题
        ↓ LLM #1（携带 registry 中全部 Tool Schema）
    判断是否需要 Tool
        ↓ 否 → 直接返回回答（1 轮 LLM / 0 次 Tool）
        ↓ 是 → ToolRegistry.execute() → ToolResult
    ToolResult 序列化为 role=tool 消息
        ↓ LLM #2（**不**携带 tools，结构性保证最多 2 轮）
    最终自然语言回答

阶段约束（任务书 §二 / §十）：

    * max_llm_rounds = 2（第二次调用不传 tools，LLM 结构上无法再发起 tool call）
    * max_tool_calls = 1（LLM 返回多个 tool call → MultipleToolCallsError，
      不并行执行、不静默截断）
    * Tool 执行失败（参数错误 / 未注册）**不**打断请求：
      ToolResult(success=False) 转为 tool message 回传 LLM，由 LLM 生成
      自然语言错误说明（任务书 §十二）

依赖方向（任务书 §二十）：

    ToolChatService
        ↓
    LLMClient
        ↓
    ToolRegistry
        ↓
    Tool

    不允许出现 RagService → ToolRegistry / ToolRegistry → LLMClient / Tool → DB。

不在本层做：

    * 多轮循环 Agent / 自动规划
    * RAG 检索（RagService 保持独立）
    * Tool 权限 / 持久化 / 审计
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from backend.app.llm.client import (
    LLMClient,
    LLMResponse,
    ToolCall,
    get_default_llm_client,
)
from backend.app.llm.tool_schema import definitions_to_openai_tools
from backend.app.tools.base import ToolResult
from backend.app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

__all__ = [
    "ToolChatCallInfo",
    "ToolChatResponse",
    "ToolChatService",
    "ToolChatError",
    "MultipleToolCallsError",
]


# ============================================================
# 异常
# ============================================================

class ToolChatError(Exception):
    """Tool Chat Service 通用异常基类。"""


class MultipleToolCallsError(ToolChatError):
    """LLM 单次响应返回多个 tool call（本阶段仅支持单个）。

    Attributes:
        count: LLM 返回的 tool call 数量。
    """

    def __init__(self, count: int) -> None:
        super().__init__(
            f"LLM 返回 {count} 个 tool call，当前阶段仅支持单个"
        )
        self.count = count


# ============================================================
# DTO
# ============================================================

@dataclass(frozen=True)
class ToolChatCallInfo:
    """对外暴露的单次 Tool 调用信息（**只**含 Tool 名称）。

    不包含：arguments、执行结果 data、错误堆栈、handler 等内部细节。
    """

    tool_name: str


@dataclass(frozen=True)
class ToolChatResponse:
    """Tool Chat 回答结果（Service 层 DTO）。

    Attributes:
        answer:     最终自然语言回答（无 Tool 时直接来自 LLM #1）。
        tool_calls: 本次请求实际发生的 Tool 调用（本阶段最多 1 个；
                    仅含 tool_name，不含内部执行细节）。
    """

    answer: str
    tool_calls: tuple[ToolChatCallInfo, ...] = ()


# ============================================================
# Helpers（私有）
# ============================================================

def _serialize_tool_result(result: ToolResult) -> str:
    """把 ToolResult 序列化为 tool message 的 content（JSON 字符串）。

    只序列化 success / data / error 三个安全字段；
    **不**包含 traceback / API Key / DATABASE_URL / SQL / embedding。
    """
    if result.success:
        payload: dict[str, Any] = {"success": True, "data": result.data}
    else:
        payload = {"success": False, "error": result.error}
    return json.dumps(payload, ensure_ascii=False, default=str)


def _build_tool_messages(
    call: ToolCall, result: ToolResult
) -> list[dict[str, Any]]:
    """构造回传 LLM 的消息对（assistant tool_call + role=tool 结果）。

    OpenAI 协议要求：tool message 之前必须有携带相同 tool_calls 的
    assistant message，且 tool_call_id 一致。
    """
    assistant_message: dict[str, Any] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(
                        call.arguments, ensure_ascii=False, default=str
                    ),
                },
            }
        ],
    }
    tool_message: dict[str, Any] = {
        "role": "tool",
        "tool_call_id": call.id,
        "content": _serialize_tool_result(result),
    }
    return [assistant_message, tool_message]


# ============================================================
# Service
# ============================================================

class ToolChatService:
    """LLM Function Calling 编排（Phase 3.6.2）。

    单轮、单个 Tool Call 的最小闭环；不做循环 Agent。
    """

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        """构造 ToolChatService。

        Args:
            llm_client: LLM Client；为 None 时使用模块默认实例
                        （主要用于测试注入 Scripted Fake）。
        """
        self._llm_client = (
            llm_client if llm_client is not None else get_default_llm_client()
        )

    async def chat(
        self,
        message: str,
        *,
        registry: ToolRegistry,
    ) -> ToolChatResponse:
        """处理单轮用户消息（最多 1 次 Tool Call + 2 轮 LLM）。

        Args:
            message:  用户自然语言消息。
            registry: Tool 注册中心（只读取 list_definitions / execute）。

        Returns:
            ToolChatResponse（answer + tool_calls 元信息）。

        Raises:
            ValueError: message 为空或纯空白。
            MultipleToolCallsError: LLM 返回多个 tool call。
            LLMError 家族: LLM 配置 / 请求 / 响应异常（原样透传，
                           由 API 层映射为 HTTP 状态码）。
        """
        if not message or not message.strip():
            raise ValueError("message 不能为空")

        start_time = time.perf_counter()
        definitions = registry.list_definitions()
        tools = definitions_to_openai_tools(definitions)
        messages: list[dict[str, Any]] = [{"role": "user", "content": message}]

        # ---- LLM #1（携带 tools；空 registry 时不携带） ----
        if tools:
            first = await self._llm_client.chat(messages, tools=tools)
        else:
            first = await self._llm_client.chat(messages)
        if not isinstance(first, LLMResponse):
            # 防御：tools 路径实现方必须返回 LLMResponse（Protocol 约定）
            first = LLMResponse(content=first, tool_calls=())
        llm_rounds = 1

        # ---- 无 Tool Call：直接返回 ----
        if not first.tool_calls:
            response = ToolChatResponse(
                answer=first.content or "",
                tool_calls=(),
            )
            logger.info(
                "tool chat completed without tool call",
                extra={
                    "llm_rounds": llm_rounds,
                    "tool_call_count": 0,
                    "elapsed_ms": (time.perf_counter() - start_time) * 1000,
                },
            )
            return response

        # ---- 单 Tool Call 校验（客户端已拒绝多个，此处防御性兜底） ----
        if len(first.tool_calls) > 1:
            raise MultipleToolCallsError(len(first.tool_calls))
        call = first.tool_calls[0]

        # ---- 执行 Tool（失败也归一为 ToolResult，不打断链路） ----
        result = await registry.execute(call.name, call.arguments)
        messages.extend(_build_tool_messages(call, result))

        # ---- LLM #2（不携带 tools → 结构上保证最多 2 轮） ----
        second = await self._llm_client.chat(messages)
        llm_rounds = 2
        if isinstance(second, LLMResponse):
            # 防御：不传 tools 时实现方按 Phase 2 行为返回 str
            answer = second.content or ""
        else:
            answer = second

        logger.info(
            "tool chat completed with tool call",
            extra={
                "tool_name": call.name,
                "tool_success": result.success,
                "llm_rounds": llm_rounds,
                "tool_call_count": 1,
                "elapsed_ms": (time.perf_counter() - start_time) * 1000,
            },
        )
        return ToolChatResponse(
            answer=answer,
            tool_calls=(ToolChatCallInfo(tool_name=call.name),),
        )
