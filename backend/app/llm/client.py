"""LLM Client 实现层（Phase 2；Phase 3.6.2 扩展 Tool Calling；
Phase 3.10.1 引入 LLMProvider 抽象 + DeepSeekProvider）。

抽象边界（Phase 3.10.1 起）：

    AI Core
        ↓
    LLMProvider（provider.py，Protocol —— AI Core 依赖的抽象）
        ↓
    DeepSeekProvider（deepseek_provider.py，delegation）
        ↓
    ┌─────────────────────────┐
    │ MockLLMClient           │ （无 Key 回退）
    │ OpenAICompatibleClient  │ （本模块，httpx HTTP）
    └─────────────────────────┘
                               ↓
                        OpenAI Chat Completions API
                          （OpenAI / DeepSeek /
                            Qwen (compatible-mode) /
                            Ollama / 自部署）

向后兼容：本模块 ``LLMClient`` 现为 ``provider.LLMProvider`` 的
别名（同一对象）。Phase 2 ~ 3.9 的 ``from backend.app.llm.client
import LLMClient`` 继续有效。

异常层级：

    LLMError
        ├── LLMConfigError      配置缺失 / 非法
        ├── LLMRequestError     网络 / 超时 / 非 2xx 响应
        └── LLMResponseError    响应解析失败 / 结构异常
                └── LLMToolCallFormatError   tool_calls 结构非法
                                              （Phase 3.6.2）

Phase 3.6.2 Tool Calling：

    chat(messages)                 → str          （行为与 Phase 2 完全一致）
    chat(messages, tools=[...])    → LLMResponse  （content + tool_calls）

    * tools schema 由 backend/app/llm/tool_schema.py 从 ToolDefinition 转换；
    * 本阶段协议约束：单次响应最多 1 个 tool call（超过 → LLMToolCallFormatError，
      不静默截断 / 不并行执行）。

详见 docs/architecture.md §8、AGENTS.md §9。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from backend.app.config import LLMSettings, settings
from backend.app.llm.deepseek_provider import DeepSeekProvider
from backend.app.llm.provider import LLMProvider

logger = logging.getLogger(__name__)

_PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "system.txt"


# ============================================================
# System Prompt
# ============================================================

def load_system_prompt() -> str:
    """读取系统 Prompt；缺失返回空串。

    Phase 2 由 ChatService 在构造 messages 时调用，
    不在 Client 层硬编码任何 Prompt 字符串。
    """
    try:
        return _PROMPT_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        logger.warning("system prompt file not found: %s", _PROMPT_FILE)
        return ""


# ============================================================
# 异常体系
# ============================================================

class LLMError(Exception):
    """LLM 相关错误的基类。"""


class LLMConfigError(LLMError):
    """LLM 配置错误（API Key 缺失、Base URL 缺失、Model 缺失）。"""


class LLMRequestError(LLMError):
    """LLM 请求失败（连接失败、超时、HTTP 非 2xx 等）。

    Phase 3.10.2：增加结构化 ``status_code``（retry 分类的依据，
    见 ``llm.retry.is_retryable_llm_error``）。

    Attributes:
        status_code: HTTP 状态码；网络级失败（连接失败 / 超时 /
                     其它 httpx 错误）为 ``None``。
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMResponseError(LLMError):
    """LLM 响应解析失败（JSON 非法、结构不符合预期）。"""


class LLMToolCallFormatError(LLMResponseError):
    """tool_calls 结构非法（Phase 3.6.2）。

    触发条件：
        * tool_call 缺 id / function.name
        * arguments 不是合法 JSON object
        * 单次响应包含多个 tool call（本阶段仅支持单个）
    """


# ============================================================
# Tool Calling DTO（Phase 3.6.2）
# ============================================================

@dataclass(frozen=True)
class ToolCall:
    """LLM 请求调用某个 Tool 的结构化表达。

    Attributes:
        id:        tool call 唯一标识（OpenAI 协议要求，用于 role=tool 回传）。
        name:      Tool 名称（对应 ToolDefinition.name）。
        arguments: 已解析为 dict 的调用参数。
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("ToolCall.id 不能为空")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("ToolCall.name 不能为空")
        if not isinstance(self.arguments, dict):
            raise ValueError(
                f"ToolCall.arguments 必须为 dict（got {type(self.arguments).__name__}）"
            )


@dataclass(frozen=True)
class LLMResponse:
    """``chat(messages, tools=...)`` 的统一返回 DTO（Phase 3.6.2）。

    Attributes:
        content:    LLM 文本回答；请求 Tool 时通常为 None。
        tool_calls: LLM 请求的 Tool 调用列表（本阶段最多 1 个，协议保证）。
    """

    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()


# ============================================================
# 抽象接口（Phase 3.10.1：定义移至 provider.py，此处保留别名）
# ============================================================

LLMClient = LLMProvider
"""LLM 抽象接口（向后兼容别名）。

Phase 3.10.1 起抽象正式定义于 ``backend/app.llm.provider.LLMProvider``；
本名字保留为同一对象的别名，Phase 2 ~ 3.9 的旧 import 路径
``from backend.app.llm.client import LLMClient`` 继续有效。

语义（Phase 3.6.2 起支持 Tool Calling）：

    chat(messages)              → str          （向后兼容，Phase 2 行为）
    chat(messages, tools=...)   → LLMResponse  （content + tool_calls）
"""


# ============================================================
# Mock 实现
# ============================================================

class MockLLMClient:
    """返回 Mock 响应，不发起任何网络请求。

    当 LLM_API_KEY 未配置时由 create_llm_client 自动选用，
    保证本地无 Key 也能演示 / 运行测试。
    """

    def __init__(self, system_prompt: str = "") -> None:
        self._system_prompt = system_prompt

    async def generate(self, prompt: str) -> str:
        return f"[mock] 已收到消息：{prompt}"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> str | LLMResponse:
        last_user = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user = msg.get("content", "")
                break
        if tools is not None:
            # Mock 不伪造 tool call：无 Key 场景下返回普通回答
            return LLMResponse(
                content=f"[mock] 已收到消息：{last_user}",
                tool_calls=(),
            )
        return f"[mock] 已收到消息：{last_user}"


# ============================================================
# OpenAI 兼容实现
# ============================================================

class OpenAICompatibleClient:
    """OpenAI Chat Completions API 兼容协议的 LLM Client。

    通过环境变量 LLM_BASE_URL + LLM_MODEL + LLM_API_KEY，
    可对接以下任一服务（都遵循相同的 Chat Completions 协议）：

        - OpenAI:        https://api.openai.com/v1
        - DeepSeek:      https://api.deepseek.com/v1
        - Qwen:          https://dashscope.aliyuncs.com/compatible-mode/v1
        - 硅基流动:       https://api.siliconflow.cn/v1
        - Ollama:        http://localhost:11434/v1
        - 其它自部署

    请求协议：

        POST {base_url}/chat/completions
        Authorization: Bearer {api_key}
        Content-Type: application/json
        {
            "model": "...",
            "messages": [{"role": "system|user|assistant", "content": "..."}]
        }

    响应结构（兼容）：

        {
            "choices": [
                {"message": {"role": "assistant", "content": "..."}}
            ]
        }
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        provider: str = "openai_compatible",
        timeout_connect: float = 10.0,
        timeout_read: float = 60.0,
        timeout_write: float = 10.0,
        timeout_pool: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # 配置校验：缺失即抛 LLMConfigError，由 API 层捕获 → 503
        if not api_key:
            raise LLMConfigError("LLM_API_KEY 未配置")
        if not base_url:
            raise LLMConfigError("LLM_BASE_URL 未配置")
        if not model:
            raise LLMConfigError("LLM_MODEL 未配置")

        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._provider = provider or "openai_compatible"
        self._timeout = httpx.Timeout(
            connect=timeout_connect,
            read=timeout_read,
            write=timeout_write,
            pool=timeout_pool,
        )
        # 仅测试使用：传入 httpx.MockTransport 以避免真实网络
        self._transport = transport

    # ---------- internal helpers ----------

    def _endpoint_url(self) -> str:
        return f"{self._base_url.rstrip('/')}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
        return payload

    def _extract_answer(self, data: dict[str, Any]) -> str:
        try:
            choices = data["choices"]
            first = choices[0]
            message = first["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError(
                f"LLM 响应结构不符合预期（缺 choices[0].message.content）: {exc}"
            ) from exc
        if not isinstance(content, str):
            raise LLMResponseError(
                f"LLM 响应 content 类型不是 string：{type(content).__name__}"
            )
        return content

    def _extract_assistant_message(self, data: dict[str, Any]) -> dict[str, Any]:
        """提取 choices[0].message 原始 dict（Tool Calling 路径）。"""
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError(
                f"LLM 响应结构不符合预期（缺 choices[0].message）: {exc}"
            ) from exc
        if not isinstance(message, dict):
            raise LLMResponseError(
                f"LLM 响应 message 类型不是 object：{type(message).__name__}"
            )
        return message

    def _parse_tool_arguments(self, call_id: str, raw: Any) -> dict[str, Any]:
        """解析 tool_call 的 arguments（JSON 字符串 → dict）。"""
        if raw is None or raw == "":
            return {}
        if isinstance(raw, dict):
            # 部分兼容实现直接返回 dict
            return dict(raw)
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except ValueError as exc:
                raise LLMToolCallFormatError(
                    f"tool_call {call_id!r} arguments 不是合法 JSON: {exc}"
                ) from exc
            if not isinstance(parsed, dict):
                raise LLMToolCallFormatError(
                    f"tool_call {call_id!r} arguments 必须为 JSON object"
                    f"（got {type(parsed).__name__}）"
                )
            return parsed
        raise LLMToolCallFormatError(
            f"tool_call {call_id!r} arguments 类型非法: {type(raw).__name__}"
        )

    def _parse_tool_calls(self, raw_tool_calls: Any) -> tuple[ToolCall, ...]:
        """解析 message.tool_calls → ToolCall 元组。

        Phase 3.6.2 协议约束：单次响应最多 1 个 tool call；
        超过 → LLMToolCallFormatError（不静默截断、不并行执行）。
        """
        if not isinstance(raw_tool_calls, list):
            raise LLMToolCallFormatError(
                f"tool_calls 必须为 list（got {type(raw_tool_calls).__name__}）"
            )
        if len(raw_tool_calls) > 1:
            raise LLMToolCallFormatError(
                f"LLM 返回 {len(raw_tool_calls)} 个 tool call，"
                "当前阶段仅支持单个 tool call"
            )
        calls: list[ToolCall] = []
        for item in raw_tool_calls:
            if not isinstance(item, dict):
                raise LLMToolCallFormatError(
                    f"tool_call 必须为 object（got {type(item).__name__}）"
                )
            call_id = item.get("id")
            if not isinstance(call_id, str) or not call_id:
                raise LLMToolCallFormatError("tool_call 缺少 id")
            function = item.get("function")
            if not isinstance(function, dict):
                raise LLMToolCallFormatError(
                    f"tool_call {call_id!r} 缺少 function 对象"
                )
            name = function.get("name")
            if not isinstance(name, str) or not name:
                raise LLMToolCallFormatError(
                    f"tool_call {call_id!r} 缺少 function.name"
                )
            arguments = self._parse_tool_arguments(call_id, function.get("arguments"))
            calls.append(
                ToolCall(id=call_id, name=name, arguments=arguments)
            )
        return tuple(calls)

    def _extract_llm_response(self, data: dict[str, Any]) -> LLMResponse:
        """Tool Calling 路径：解析完整 assistant message（content + tool_calls）。"""
        message = self._extract_assistant_message(data)
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise LLMResponseError(
                f"LLM 响应 content 类型不是 string：{type(content).__name__}"
            )
        tool_calls = self._parse_tool_calls(message.get("tool_calls") or [])
        return LLMResponse(content=content, tool_calls=tool_calls)

    def _log_common(self) -> dict[str, Any]:
        return {
            "llm_provider": self._provider,
            "llm_model": self._model,
        }

    # ---------- public API ----------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> str | LLMResponse:
        """多轮对话生成（Phase 3.6.2 起支持 Tool Calling）。

        Args:
            messages: 对话消息（system / user / assistant / tool）。
            tools:    OpenAI-compatible tool schema 列表；
                      None / 空 → 不携带 tools（Phase 2 行为）。

        Returns:
            tools 为空时：str（assistant content，与 Phase 2 完全一致）。
            tools 非空时：LLMResponse（content 可能为 None + tool_calls）。
        """
        url = self._endpoint_url()
        headers = self._headers()
        payload = self._build_payload(messages, tools)
        client_kwargs: dict[str, Any] = {"timeout": self._timeout}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        extra_base = self._log_common()
        logger.info(
            "LLM request start: messages=%d",
            len(messages),
            extra={
                **extra_base,
                "message_count": len(messages),
                "tool_calling": bool(tools),
                "tool_count": len(tools) if tools else 0,
            },
        )
        start_time = time.perf_counter()

        # ---- HTTP 调用 ----
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.ConnectError as exc:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                "LLM connection failed",
                extra={
                    **extra_base,
                    "elapsed_ms": elapsed_ms,
                    "error_type": "ConnectError",
                },
            )
            raise LLMRequestError(
                f"无法连接 LLM 服务 {self._base_url}: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                "LLM request timeout",
                extra={
                    **extra_base,
                    "elapsed_ms": elapsed_ms,
                    "error_type": "Timeout",
                },
            )
            raise LLMRequestError(f"LLM 请求超时: {exc}") from exc
        except httpx.HTTPError as exc:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                "LLM http error",
                extra={
                    **extra_base,
                    "elapsed_ms": elapsed_ms,
                    "error_type": type(exc).__name__,
                },
            )
            raise LLMRequestError(f"LLM 请求异常: {exc}") from exc

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        # ---- 非 2xx ----
        if response.status_code != 200:
            # 截断响应体以避免日志/异常信息过长
            body_preview = response.text[:500]
            logger.error(
                "LLM non-2xx response: status=%d elapsed=%.1fms",
                response.status_code,
                elapsed_ms,
                extra={
                    **extra_base,
                    "status_code": response.status_code,
                    "elapsed_ms": elapsed_ms,
                    "response_body_preview": body_preview,
                },
            )
            raise LLMRequestError(
                f"LLM API 返回 HTTP {response.status_code}: {body_preview[:200]}",
                status_code=response.status_code,
            )

        # ---- 响应解析 ----
        try:
            data = response.json()
        except ValueError as exc:
            logger.error(
                "LLM response not JSON",
                extra={**extra_base, "elapsed_ms": elapsed_ms},
            )
            raise LLMResponseError(f"LLM 响应不是合法 JSON: {exc}") from exc

        if tools:
            result: str | LLMResponse = self._extract_llm_response(data)
            answer_length = len(result.content or "")
        else:
            result = self._extract_answer(data)
            answer_length = len(result)

        logger.info(
            "LLM request success: elapsed=%.1fms answer_len=%d",
            elapsed_ms,
            answer_length,
            extra={
                **extra_base,
                "elapsed_ms": elapsed_ms,
                "answer_length": answer_length,
                "tool_calling": bool(tools),
            },
        )
        return result

    async def generate(self, prompt: str) -> str:
        messages = [{"role": "user", "content": prompt}]
        result = await self.chat(messages)
        if isinstance(result, LLMResponse):
            # generate 不传 tools，正常不会走到这里；防御性兜底
            return result.content or ""
        return result


# ============================================================
# 工厂 + 单例
# ============================================================

def create_llm_client(llm_settings: LLMSettings | None = None) -> LLMProvider:
    """根据配置创建 LLM Provider（composition/root 工厂）。

    规则（Phase 3.10.1 起）：
        - api_key 非空  → DeepSeekProvider(OpenAICompatibleClient)
                          （现有 OpenAI-compatible Client 原样保留在
                           delegation 内层，生产行为不变）
        - api_key 为空  → MockLLMClient（带 warning 日志）

    业务代码应调用本工厂，而不是直接 new 具体实现，
    便于未来切换不同 Provider / 协议。
    """
    s = llm_settings if llm_settings is not None else settings.llm
    if not s.api_key:
        logger.warning(
            "LLM_API_KEY 未配置，回退到 MockLLMClient（不会调用真实 LLM）",
            extra={"llm_provider": s.provider or "mock"},
        )
        return MockLLMClient(system_prompt=load_system_prompt())

    return DeepSeekProvider(
        OpenAICompatibleClient(
            api_key=s.api_key,
            base_url=s.base_url,
            model=s.model,
            provider=s.provider,
            timeout_connect=s.timeout_connect,
            timeout_read=s.timeout_read,
            timeout_write=s.timeout_write,
            timeout_pool=s.timeout_pool,
        )
    )


_default_client: LLMClient | None = None


def get_default_llm_client() -> LLMClient:
    """获取默认 LLM Client（模块级单例）。

    测试时可调用 reset_default_llm_client() 重新构造。
    """
    global _default_client
    if _default_client is None:
        _default_client = create_llm_client(settings.llm)
    return _default_client


def reset_default_llm_client() -> None:
    """测试辅助：重置默认客户端缓存。"""
    global _default_client
    _default_client = None


__all__ = [
    "LLMClient",
    "LLMProvider",
    "LLMError",
    "LLMConfigError",
    "LLMRequestError",
    "LLMResponseError",
    "LLMToolCallFormatError",
    "LLMResponse",
    "ToolCall",
    "MockLLMClient",
    "OpenAICompatibleClient",
    "create_llm_client",
    "get_default_llm_client",
    "reset_default_llm_client",
    "load_system_prompt",
]