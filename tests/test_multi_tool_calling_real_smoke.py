"""Real Multi-Step Tool Calling Smoke Test（Phase 3.6.3，真实 DeepSeek）。

**默认跳过**，不消耗任何真实 API 额度。

开启方式：

    $env:RUN_REAL_MULTI_TOOL_CALLING_TEST = "1"
    pytest tests/test_multi_tool_calling_real_smoke.py -v

跳过规则（三层防护，与 test_llm_real_smoke.py 一致）：
    1. RUN_REAL_MULTI_TOOL_CALLING_TEST 未设置 → skip
    2. 开关打开但 LLM_API_KEY 为空 → skip
    3. 开关打开但 LLM_BASE_URL / LLM_MODEL 为空 → skip

验证重点（任务书 §十八）：

    * 能解析**连续**两次 tool call
    * 第二个 Tool 真实执行
    * 消息历史正确构造（第二轮 LLM 能看到第一个 Tool 的结果）
    * 最终得到 answer

只使用 Mock Tool（get_inventory / get_work_order），
绝对不接真实 WMS / ERP，不修改任何生产数据。

注意：LLM 自然语言表达可能变化，断言只针对结构化结果
（哪些 Tool 被执行、answer 非空），不针对措辞。
"""
from __future__ import annotations

import os

import pytest


def _env_flag(name: str) -> bool:
    """读取布尔型环境变量开关。"""
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


_RUN_REAL_MULTI = _env_flag("RUN_REAL_MULTI_TOOL_CALLING_TEST")

pytestmark = pytest.mark.skipif(
    not _RUN_REAL_MULTI,
    reason=(
        "set RUN_REAL_MULTI_TOOL_CALLING_TEST=1 (or true/yes/on) "
        "to enable real multi-step tool calling smoke test"
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
async def test_real_multi_step_tool_calling() -> None:
    """完整多步闭环：DeepSeek 顺序调用两个 Mock Tool 并汇总回答。

    Prompt 明确要求"分两步执行"，尽量避免模型在单次响应中
    同时请求两个 Tool（本阶段协议仅支持每轮单个 tool call）。
    """
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
        "请先查询物料 MAT001 的库存，拿到结果之后，再查询工单 MO001 的状态，"
        "最后把两个结果都告诉我。请严格分两步依次执行，"
        "不要在同一步中同时调用两个工具。",
        registry=registry,
    )

    tool_names = [info.tool_name for info in result.tool_calls]

    # 1) 两个 Tool 都被真实执行（顺序可能因模型而异，只断言集合与数量）
    assert len(tool_names) == 2, (
        f"预期执行 2 个 Tool，实际执行了 {len(tool_names)} 个: {tool_names}"
    )
    assert set(tool_names) == {"get_inventory", "get_work_order"}, (
        f"执行的 Tool 集合不符: {tool_names}"
    )

    # 2) 最终回答非空（连续 tool call 之后 LLM 能正常收尾）
    assert result.answer.strip(), "最终回答为空"

    # 3) Tool 结果确实进入了最终回答的上下文
    #    （Mock Tool 固定返回 quantity=1000 / status=RELEASED；
    #     去除千分位后断言，容忍模型格式化）
    normalized = result.answer.replace(",", "").replace("，", "")
    assert "1000" in normalized, (
        f"最终回答未体现 get_inventory 结果（answer={result.answer[:200]!r}）"
    )
    assert "RELEASED" in result.answer.upper(), (
        f"最终回答未体现 get_work_order 结果（answer={result.answer[:200]!r}）"
    )


__all__ = ["test_real_multi_step_tool_calling"]
