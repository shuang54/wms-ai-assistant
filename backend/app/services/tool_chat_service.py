"""Tool Chat Service（Phase 3.6.2 引入；Phase 3.6.3 升级为 Multi-Step）。

链路（Phase 3.6.3 任务书 §一）：

    用户问题
        ↓ LLM #1（携带 registry 中全部 Tool Schema）
    判断是否需要 Tool
        ↓ 否 → 直接返回回答（1 轮 LLM / 0 次 Tool）
        ↓ 是 → ToolRegistry.execute() → ToolResult
              → 序列化为 role=tool 消息（append 到消息历史）
        ↓ LLM #2（携带 tools，可继续请求 Tool）
    ... 循环 ...
        ↓ LLM 最终轮（不再请求 Tool）
    最终自然语言回答

阶段约束（Phase 3.6.3 任务书 §三 / §四 / §九）：

    * MAX_TOOL_ROUNDS = settings.tool.max_rounds（默认 5，钳制 [1, 20]）
      —— 每轮 LLM 请求的 Tool 执行完毕后，**下一轮 LLM 若仍请求 Tool 且
      预算已耗尽 → ToolCallingBudgetExceededError**（不执行、不再请求 LLM）
    * 每个 LLM response 最多 1 个 tool call：
      多个 → MultipleToolCallsError（多轮 ≠ 并行，仅 sequential）
    * Tool 执行失败（参数错误 / 未注册）不打断链路：
      ToolResult(success=False) 转 tool message 回传 LLM，允许继续下一轮
    * 最坏情况有硬上限：max_rounds 次 Tool 执行 + max_rounds+1 次 LLM 调用

消息历史（任务书 §七 / §八）：

    * 每一轮 LLM 都收到**完整**消息历史（user + 历次 assistant tool_call
      + 历次 tool result），不是只发最新 ToolResult；
    * messages 由 Service 内部独立构建（不接收 / 不修改调用方传入的列表），
      只 append、不改写历史消息。

依赖方向（Phase 3.6.2 任务书 §二十，保持不变）：

    ToolChatService
        ↓
    LLMClient
        ↓
    ToolRegistry
        ↓
    Tool

不在本层做：

    * 并行 Tool / Tool 依赖图 / Retry / Cache / Timeout
    * 对话历史（多轮用户 session）
    * RAG 检索（RagService 保持独立）
    * Tool 权限 / 持久化 / 审计
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from backend.app.config import settings
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
    "ToolCallingBudgetExceededError",
]


# ============================================================
# 异常
# ============================================================

class ToolChatError(Exception):
    """Tool Chat Service 通用异常基类。"""


class MultipleToolCallsError(ToolChatError):
    """LLM 单次响应返回多个 tool call（仅支持 sequential 单 Tool）。

    Attributes:
        count: LLM 返回的 tool call 数量。
    """

    def __init__(self, count: int) -> None:
        super().__init__(
            f"LLM 返回 {count} 个 tool call，当前仅支持单轮单个 tool call"
        )
        self.count = count


class ToolCallingBudgetExceededError(ToolChatError):
    """Tool Calling 预算耗尽（Phase 3.6.3）。

    含义：已执行 max_rounds 个 Tool 后，LLM 仍返回 tool call。
    行为：不执行该 Tool、不再请求 LLM，直接抛出本异常（由 API 层
    映射为 5xx），绝不静默截断。

    Attributes:
        max_rounds:       配置的最大 Tool 轮数。
        requested_tool:   预算耗尽后 LLM 仍请求的 Tool 名称（公开信息）。
    """

    def __init__(self, max_rounds: int, requested_tool: str) -> None:
        super().__init__(
            f"Tool Calling 预算耗尽（max_rounds={max_rounds}），"
            f"LLM 仍请求调用 Tool {requested_tool!r}"
        )
        self.max_rounds = max_rounds
        self.requested_tool = requested_tool


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
        tool_calls: 本次请求**实际执行过**的 Tool（按执行顺序；
                    上限 = settings.tool.max_rounds；
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
    """LLM Function Calling 编排（Phase 3.6.3：Multi-Step，顺序、有限预算）。

    每一轮：LLM（携带 tools）→ 无 tool call 则返回最终回答；
    有则执行单个 Tool 并 append 消息历史，进入下一轮。
    不是 Agent / 不是无限循环：`max_tool_rounds` 是硬上限。
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        *,
        max_tool_rounds: int | None = None,
    ) -> None:
        """构造 ToolChatService。

        Args:
            llm_client:      LLM Client；为 None 时使用模块默认实例
                             （主要用于测试注入 Scripted Fake）。
            max_tool_rounds: 最大 Tool Calling 轮数（>=1）；
                             为 None 时读取 settings.tool.max_rounds
                             （环境变量 TOOL_MAX_ROUNDS，默认 5）。

        Raises:
            ValueError: max_tool_rounds < 1。
        """
        if max_tool_rounds is None:
            max_tool_rounds = settings.tool.max_rounds
        if max_tool_rounds < 1:
            raise ValueError(
                f"max_tool_rounds 必须 >= 1（got {max_tool_rounds}）"
            )
        self._max_tool_rounds = max_tool_rounds
        self._llm_client = (
            llm_client if llm_client is not None else get_default_llm_client()
        )

    @property
    def max_tool_rounds(self) -> int:
        """最大 Tool Calling 轮数（只读）。"""
        return self._max_tool_rounds

    async def chat(
        self,
        message: str,
        *,
        registry: ToolRegistry,
    ) -> ToolChatResponse:
        """处理单轮用户消息（顺序多步 Tool Calling，预算内循环）。

        每轮 LLM 调用都携带完整消息历史 + 全部 Tool Schema；
        LLM 不再请求 Tool 时返回最终回答。

        Args:
            message:  用户自然语言消息。
            registry: Tool 注册中心（只读取 list_definitions / execute）。

        Returns:
            ToolChatResponse（answer + 按执行顺序的 tool_calls 元信息）。

        Raises:
            ValueError:                       message 为空或纯空白。
            ToolCallingBudgetExceededError:   预算耗尽后 LLM 仍请求 Tool。
            MultipleToolCallsError:           LLM 单次返回多个 tool call。
            LLMError 家族:                    LLM 配置 / 请求 / 响应异常
                                              （原样透传，API 层映射 HTTP）。
        """
        if not message or not message.strip():
            raise ValueError("message 不能为空")

        start_time = time.perf_counter()
        definitions = registry.list_definitions()
        tools = definitions_to_openai_tools(definitions)
        # Service 内部独立构建消息历史：只 append，不改写历史消息，
        # 不接收调用方传入的 list（避免意外污染）。
        messages: list[dict[str, Any]] = [{"role": "user", "content": message}]

        executed_calls: list[ToolChatCallInfo] = []
        tool_round = 0
        llm_rounds = 0

        while True:
            # ---- LLM（每轮都携带 tools，使 LLM 可以继续请求下一个 Tool） ----
            if tools:
                raw = await self._llm_client.chat(messages, tools=tools)
            else:
                raw = await self._llm_client.chat(messages)
            llm_rounds += 1
            if not isinstance(raw, LLMResponse):
                # 防御：tools 路径实现方必须返回 LLMResponse（Protocol 约定）
                response = LLMResponse(content=raw, tool_calls=())
            else:
                response = raw

            # ---- 1) 无 Tool Call：最终回答 ----
            if not response.tool_calls:
                answer = response.content or ""
                logger.info(
                    "tool chat completed",
                    extra={
                        "llm_rounds": llm_rounds,
                        "tool_call_count": tool_round,
                        "elapsed_ms": (time.perf_counter() - start_time)
                        * 1000,
                    },
                )
                return ToolChatResponse(
                    answer=answer,
                    tool_calls=tuple(executed_calls),
                )

            # ---- 2) 预算检查：先于一切执行（任务书 §九） ----
            if tool_round >= self._max_tool_rounds:
                logger.warning(
                    "tool calling budget exceeded",
                    extra={
                        "max_rounds": self._max_tool_rounds,
                        "requested_tool": response.tool_calls[0].name,
                        "llm_rounds": llm_rounds,
                        "tool_call_count": tool_round,
                    },
                )
                raise ToolCallingBudgetExceededError(
                    self._max_tool_rounds, response.tool_calls[0].name
                )

            # ---- 3) 单 Tool Call 校验（多轮 ≠ 并行） ----
            if len(response.tool_calls) > 1:
                raise MultipleToolCallsError(len(response.tool_calls))
            call = response.tool_calls[0]

            # ---- 4) 执行 Tool（失败也归一为 ToolResult，允许继续下一轮） ----
            tool_round += 1
            result = await registry.execute(call.name, call.arguments)

            # ---- 5) append 完整消息对，保留历史（任务书 §七） ----
            messages.extend(_build_tool_messages(call, result))
            executed_calls.append(ToolChatCallInfo(tool_name=call.name))

            logger.info(
                "tool round executed",
                extra={
                    "tool_round": tool_round,
                    "tool_name": call.name,
                    "tool_success": result.success,
                    "llm_rounds": llm_rounds,
                    "tool_call_count": tool_round,
                    "elapsed_ms": (time.perf_counter() - start_time) * 1000,
                },
            )
