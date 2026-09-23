"""Real Tool Calling Smoke Test（Phase 3.6.2，真实 DeepSeek / OpenAI-compatible）。

**默认跳过**，不消耗任何真实 API 额度。

开启方式：

    $env:RUN_REAL_TOOL_CALLING_TEST = "1"
    pytest tests/test_tool_calling_real_smoke.py -v

跳过规则（三层防护，与 test_llm_real_smoke.py 一致）：
    1. RUN_REAL_TOOL_CALLING_TEST 未设置 → skip
    2. 开关打开但 LLM_API_KEY 为空 → skip
    3. 开关打开但 LLM_BASE_URL / LLM_MODEL 为空 → skip

测试内容（任务书 §十八）：

    1. 真实 LLM 收到 get_inventory tool schema 后，对
       “查询 MAT001 的库存” 是否返回 tool call；
    2. 完整闭环：ToolRegistry.execute()（Mock Tool）→ tool message
       → 第二轮 LLM → 最终自然语言回答。

注意：
    - 只使用 Mock Tool（get_inventory），绝不接真实 WMS / ERP；
    - 不修改任何生产数据。
"""
from __future__ import annotations

import os

import pytest


def _env_flag(name: str) -> bool:
    """读取布尔型环境变量开关。"""
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


_RUN_REAL = _env_flag("RUN_REAL_TOOL_CALLING_TEST")


pytestmark = pytest.mark.skipif(
    not _RUN_REAL,
    reason=(
        "set RUN_REAL_TOOL_CALLING_TEST=1 (or true/yes/on) "
        "to enable real tool calling smoke test"
    ),
)


def _require_llm_config() -> None:
    """第二 / 三层防护：无 Key / URL / Model 时 skip。"""
    from backend.app.config import settings

    if not settings.llm.api_key:
        pytest.skip("LLM_API_KEY 未配置；请在 .env 中设置后重试")
    if not settings.llm.base_url:
        pytest.skip("LLM_BASE_URL 未配置")
    if not settings.llm.model:
        pytest.skip("LLM_MODEL 未配置")


@pytest.mark.asyncio
async def test_real_llm_returns_tool_call() -> None:
    """验证真实 LLM（DeepSeek 等 OpenAI-compatible）能返回 tool call。"""
    _require_llm_config()

    from backend.app.config import settings
    from backend.app.llm.client import LLMResponse, create_llm_client
    from backend.app.llm.tool_schema import definitions_to_openai_tools
    from backend.app.tools.mock_tools import GET_INVENTORY_DEFINITION

    client = create_llm_client(settings.llm)
    tools = definitions_to_openai_tools([GET_INVENTORY_DEFINITION])

    result = await client.chat(
        [
            {
                "role": "user",
                "content": "请帮我查询物料 MAT001 的当前库存",
            }
        ],
        tools=tools,
    )

    assert isinstance(result, LLMResponse), (
        f"预期 LLMResponse，got {type(result).__name__}"
    )
    assert result.tool_calls, (
        "真实 LLM 未返回 tool call（请确认模型支持 Function Calling，"
        f"model={settings.llm.model!r}）"
    )
    call = result.tool_calls[0]
    assert call.name, "tool call 缺少 name"
    assert call.id, "tool call 缺少 id"


@pytest.mark.asyncio
async def test_real_tool_calling_full_loop() -> None:
    """完整闭环：LLM → Tool Call → Mock Tool 执行 → LLM 最终回答。"""
    _require_llm_config()

    from backend.app.config import settings
    from backend.app.llm.client import create_llm_client
    from backend.app.services.tool_chat_service import ToolChatService
    from backend.app.tools.mock_tools import register_mock_tools
    from backend.app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_mock_tools(registry)
    service = ToolChatService(llm_client=create_llm_client(settings.llm))

    result = await service.chat(
        "请帮我查询物料 MAT001 的当前库存", registry=registry
    )

    assert result.answer.strip(), "最终回答为空"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool_name == "get_inventory", (
        f"预期调用 get_inventory，got {result.tool_calls[0].tool_name!r}"
    )
    # Mock Tool 固定返回 quantity=1000；最终回答应体现该数字
    # （LLM 可能格式化为 "1,000"，故去除千分位后再断言）
    assert "1000" in result.answer.replace(",", ""), (
        f"最终回答未体现 Tool 结果（answer={result.answer[:200]!r}）"
    )


__all__ = [
    "test_real_llm_returns_tool_call",
    "test_real_tool_calling_full_loop",
]
