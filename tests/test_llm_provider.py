"""LLMProvider 抽象 / DeepSeekProvider delegation / Provider 注入测试（Phase 3.10.1）。

覆盖（任务书 §八）：

    1. Provider interface  —— 具体 Provider 满足 ``LLMProvider`` 抽象；
    2. Delegation          —— DeepSeekProvider → Fake DeepSeek Client
                              （参数透传 / 结果转发 / 零网络请求）；
    3. Provider injection  —— 核心服务可注入 ``LLMProvider``，
                              不强绑定具体 DeepSeek Client；
    4. Fake Provider       —— ``FakeLLMProvider``（仅测试使用，
                              不进 production runtime）；
    5. Composition         —— ``create_llm_client`` 装配规则：
                              有 Key → DeepSeekProvider(OpenAICompatibleClient)，
                              无 Key → MockLLMClient。

全部使用 Fake / 内存对象，不发生真实网络请求。
"""
from __future__ import annotations

import copy
from typing import Any

from backend.app.config import LLMSettings
from backend.app.llm.client import (
    LLMClient,
    LLMResponse,
    MockLLMClient,
    OpenAICompatibleClient,
    ToolCall,
    create_llm_client,
)
from backend.app.llm.deepseek_provider import DeepSeekProvider
from backend.app.llm.provider import LLMProvider
from backend.app.services.ai_router_service import AIRouterService
from backend.app.services.rag_service import RagService
from backend.app.services.text_to_sql_service import TextToSQLService
from backend.app.services.tool_chat_service import ToolChatService


# ============================================================
# Fake DeepSeek Client（模拟现有 OpenAICompatibleClient 调用面）
# ============================================================

class FakeDeepSeekClient:
    """模拟现有 DeepSeek OpenAI-compatible Client 的最小调用面。

    记录全部调用参数供断言；返回预置结果；零网络请求。
    """

    def __init__(self) -> None:
        self.generate_prompts: list[str] = []
        self.chat_calls: list[dict[str, Any]] = []

    async def generate(self, prompt: str) -> str:
        self.generate_prompts.append(prompt)
        return "SELECT 1"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> str | LLMResponse:
        self.chat_calls.append(
            {
                "messages": copy.deepcopy(messages),
                "tools": copy.deepcopy(tools),
            }
        )
        if tools:
            return LLMResponse(
                content=None,
                tool_calls=(
                    ToolCall(
                        id="call_1",
                        name="get_inventory",
                        arguments={"material_code": "MAT001"},
                    ),
                ),
            )
        return "plain answer"


# ============================================================
# Fake LLM Provider（任务书 §8.5：仅测试使用）
# ============================================================

class FakeLLMProvider:
    """极小 Fake ``LLMProvider``（仅测试使用，不进 production runtime）。"""

    def __init__(self, answer: str = "fake answer") -> None:
        self.answer = answer
        self.generate_calls: list[str] = []
        self.chat_calls: list[dict[str, Any]] = []

    async def generate(self, prompt: str) -> str:
        self.generate_calls.append(prompt)
        return self.answer

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> str | LLMResponse:
        self.chat_calls.append(
            {"messages": messages, "tools": tools}
        )
        return self.answer


def _as_provider(provider: Any) -> LLMProvider:
    """结构化验证：实现可赋值给 ``LLMProvider`` 抽象注解。

    Protocol 为 structural typing（非 runtime_checkable），
    赋值给抽象注解即静态类型层面的满足性检查。
    """
    checked: LLMProvider = provider
    return checked


# ============================================================
# 1. Provider interface
# ============================================================

def test_llm_client_alias_is_llm_provider() -> None:
    """client.LLMClient 是 provider.LLMProvider 的向后兼容别名（同一对象）。"""
    assert LLMClient is LLMProvider


def test_deepseek_provider_satisfies_llm_provider_protocol() -> None:
    """DeepSeekProvider 满足 LLMProvider 抽象接口。"""
    provider = DeepSeekProvider(FakeDeepSeekClient())
    _as_provider(provider)  # 静态结构检查
    # 运行时 duck-typing：抽象要求的两个方法均存在且可调用
    assert callable(provider.generate)
    assert callable(provider.chat)


def test_mock_llm_client_satisfies_llm_provider_protocol() -> None:
    """MockLLMClient（无 Key 回退实现）满足 LLMProvider 抽象接口。"""
    provider = MockLLMClient(system_prompt="")
    _as_provider(provider)
    assert callable(provider.generate)
    assert callable(provider.chat)


def test_fake_llm_provider_satisfies_llm_provider_protocol() -> None:
    """FakeLLMProvider 满足 LLMProvider 抽象接口。"""
    provider = FakeLLMProvider()
    _as_provider(provider)
    assert callable(provider.generate)
    assert callable(provider.chat)


def test_fake_provider_not_in_production_runtime() -> None:
    """FakeLLMProvider 只存在于测试模块，production llm 包不导出 / 不含 Fake。"""
    import backend.app.llm.deepseek_provider as deepseek_module
    import backend.app.llm.provider as provider_module

    assert "FakeLLMProvider" not in deepseek_module.__all__
    assert "FakeLLMProvider" not in provider_module.__all__
    assert not hasattr(provider_module, "FakeLLMProvider")
    assert not hasattr(deepseek_module, "FakeLLMProvider")


# ============================================================
# 2. DeepSeek Provider delegation（零网络）
# ============================================================

async def test_deepseek_provider_delegates_generate() -> None:
    """generate：参数原样透传，返回原样转发。"""
    client = FakeDeepSeekClient()
    provider = DeepSeekProvider(client)

    result = await provider.generate("列出所有仓库")

    assert result == "SELECT 1"
    assert client.generate_prompts == ["列出所有仓库"]
    assert client.chat_calls == []  # 未发生其它调用


async def test_deepseek_provider_delegates_chat_without_tools() -> None:
    """chat（无 tools）：messages 原样透传，str 返回原样转发。"""
    client = FakeDeepSeekClient()
    provider = DeepSeekProvider(client)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "你好"},
    ]

    result = await provider.chat(messages)

    assert result == "plain answer"
    assert isinstance(result, str)
    assert client.chat_calls == [{"messages": messages, "tools": None}]


async def test_deepseek_provider_delegates_chat_with_tools() -> None:
    """chat（tools 非空）：tools 原样透传，LLMResponse（含 tool_calls）
    原样转发——不丢失、不转换、不发起网络请求。"""
    client = FakeDeepSeekClient()
    provider = DeepSeekProvider(client)
    messages = [{"role": "user", "content": "查询 MAT001 库存"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_inventory",
                "description": "查询库存",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    result = await provider.chat(messages, tools=tools)

    assert isinstance(result, LLMResponse)
    assert result.content is None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[0].name == "get_inventory"
    assert result.tool_calls[0].arguments == {"material_code": "MAT001"}
    # 参数原样到达内层 Client
    assert client.chat_calls[0]["messages"] == messages
    assert client.chat_calls[0]["tools"] == tools


# ============================================================
# 3. Provider injection（核心服务依赖抽象，不强绑定具体 Client）
# ============================================================

def test_text_to_sql_service_accepts_llm_provider() -> None:
    """TextToSQLService 接收并使用注入的 LLMProvider。"""
    provider = FakeLLMProvider()
    service = TextToSQLService(llm_client=provider)

    assert service._get_llm_client() is provider


async def test_text_to_sql_service_end_to_end_with_fake_provider() -> None:
    """行为回归：Question → TextToSQLService → LLMProvider → SQL
    → Validator 链路在 Provider 抽象下保持不变。"""
    provider = FakeLLMProvider(answer="```sql\nSELECT 1 LIMIT 1\n```")
    service = TextToSQLService(llm_client=provider, max_attempts=1)

    result = await service.generate("知识文档有哪些？", database_context="ctx")

    assert result.validated is True
    assert result.sql == "SELECT 1 LIMIT 1"
    assert len(provider.chat_calls) == 1  # LLM 调用点走 Provider


def test_rag_service_accepts_llm_provider() -> None:
    """RagService（RAG LLM 调用点）接收并使用注入的 LLMProvider。"""
    provider = FakeLLMProvider()
    service = RagService(llm_client=provider)

    assert service._get_llm_client() is provider


def test_tool_chat_service_accepts_llm_provider() -> None:
    """ToolChatService（Tool Calling LLM 调用点）接收 LLMProvider。"""
    provider = FakeLLMProvider()
    service = ToolChatService(provider)

    assert service._llm_client is provider


def test_ai_router_service_accepts_llm_provider() -> None:
    """AIRouterService（Router LLM fallback 调用点）接收 LLMProvider。"""
    provider = FakeLLMProvider()
    service = AIRouterService(llm_client=provider)

    assert service._llm is provider


# ============================================================
# 4. Composition（application/root 层装配规则，不触网）
# ============================================================

def test_create_llm_client_with_key_wraps_existing_client() -> None:
    """有 Key：DeepSeekProvider 包装**现有** OpenAICompatibleClient
    （不重新实现 HTTP Client；配置语义不变）。"""
    llm_settings = LLMSettings(
        api_key="test-key",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        provider="deepseek",
    )

    provider = create_llm_client(llm_settings)

    assert isinstance(provider, DeepSeekProvider)
    # 现有 DeepSeek Client 原样作为 delegation 内层
    assert isinstance(provider._client, OpenAICompatibleClient)
    # Provider 接口不持有 / 不暴露 API Key
    assert not hasattr(provider, "api_key")


def test_create_llm_client_without_key_returns_mock_provider() -> None:
    """无 Key：回退 MockLLMClient（同样满足 LLMProvider 抽象）。"""
    llm_settings = LLMSettings(api_key="", base_url="", model="")

    provider = create_llm_client(llm_settings)

    assert isinstance(provider, MockLLMClient)
    _as_provider(provider)
