"""LLM Client 抽象层与实现（Phase 2）。

抽象边界：

    ChatService
        ↓
    LLMClient（Protocol）
        ↓
    ┌─────────────────────┐
    │                     │
    MockLLMClient       OpenAICompatibleClient
    （无 Key 回退）            │
                               ↓
                            httpx (HTTP)
                               ↓
                       OpenAI Chat Completions API
                          （OpenAI / DeepSeek /
                            Qwen (compatible-mode) /
                            Ollama / 自部署）

异常层级：

    LLMError
        ├── LLMConfigError      配置缺失 / 非法
        ├── LLMRequestError     网络 / 超时 / 非 2xx 响应
        └── LLMResponseError    响应解析失败 / 结构异常

详见 docs/architecture.md §8、AGENTS.md §9。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

from backend.app.config import LLMSettings, settings

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
    """LLM 请求失败（连接失败、超时、HTTP 非 2xx 等）。"""


class LLMResponseError(LLMError):
    """LLM 响应解析失败（JSON 非法、结构不符合预期）。"""


# ============================================================
# 抽象接口
# ============================================================

class LLMClient(Protocol):
    """LLM Client 抽象接口。

    ChatService 仅依赖此接口，不直接引用具体实现。
    """

    async def generate(self, prompt: str) -> str:
        """单轮文本生成（无 system prompt）。"""
        ...

    async def chat(self, messages: list[dict[str, str]]) -> str:
        """多轮对话生成。

        messages 每项形如 {"role": "system|user|assistant", "content": "..."}。
        返回 assistant 的 content。
        """
        ...


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

    async def chat(self, messages: list[dict[str, str]]) -> str:
        last_user = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user = msg.get("content", "")
                break
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

    def _build_payload(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        return {
            "model": self._model,
            "messages": messages,
        }

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

    def _log_common(self) -> dict[str, Any]:
        return {
            "llm_provider": self._provider,
            "llm_model": self._model,
        }

    # ---------- public API ----------

    async def chat(self, messages: list[dict[str, str]]) -> str:
        url = self._endpoint_url()
        headers = self._headers()
        payload = self._build_payload(messages)
        client_kwargs: dict[str, Any] = {"timeout": self._timeout}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        extra_base = self._log_common()
        logger.info(
            "LLM request start: messages=%d",
            len(messages),
            extra={**extra_base, "message_count": len(messages)},
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
                f"LLM API 返回 HTTP {response.status_code}: {body_preview[:200]}"
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

        answer = self._extract_answer(data)

        logger.info(
            "LLM request success: elapsed=%.1fms answer_len=%d",
            elapsed_ms,
            len(answer),
            extra={
                **extra_base,
                "elapsed_ms": elapsed_ms,
                "answer_length": len(answer),
            },
        )
        return answer

    async def generate(self, prompt: str) -> str:
        messages = [{"role": "user", "content": prompt}]
        return await self.chat(messages)


# ============================================================
# 工厂 + 单例
# ============================================================

def create_llm_client(llm_settings: LLMSettings | None = None) -> LLMClient:
    """根据配置创建 LLM Client。

    规则：
        - api_key 非空  → OpenAICompatibleClient
        - api_key 为空  → MockLLMClient（带 warning 日志）

    业务代码应调用本工厂，而不是直接 new 具体实现，
    便于未来切换不同协议。
    """
    s = llm_settings if llm_settings is not None else settings.llm
    if not s.api_key:
        logger.warning(
            "LLM_API_KEY 未配置，回退到 MockLLMClient（不会调用真实 LLM）",
            extra={"llm_provider": s.provider or "mock"},
        )
        return MockLLMClient(system_prompt=load_system_prompt())

    return OpenAICompatibleClient(
        api_key=s.api_key,
        base_url=s.base_url,
        model=s.model,
        provider=s.provider,
        timeout_connect=s.timeout_connect,
        timeout_read=s.timeout_read,
        timeout_write=s.timeout_write,
        timeout_pool=s.timeout_pool,
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
    "LLMError",
    "LLMConfigError",
    "LLMRequestError",
    "LLMResponseError",
    "MockLLMClient",
    "OpenAICompatibleClient",
    "create_llm_client",
    "get_default_llm_client",
    "reset_default_llm_client",
    "load_system_prompt",
]