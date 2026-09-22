"""Real LLM Smoke Test（Phase 2 Real LLM Smoke Test）。

**默认跳过**，不会消耗任何真实 API 额度，也不会无 Key 时硬跑失败。

开启方式（任一即可）：
    $env:RUN_REAL_LLM_TEST = "1"
    pytest tests/test_llm_real_smoke.py -v

    # 或
    RUN_REAL_LLM_TEST=true pytest tests/test_llm_real_smoke.py -v

跳过规则（三层防护）：
    1. RUN_REAL_LLM_TEST 未设置 → skip
    2. RUN_REAL_LLM_TEST=true 但 LLM_API_KEY 为空 → skip
    3. RUN_REAL_LLM_TEST=true 但 LLM_BASE_URL 或 LLM_MODEL 为空 → skip

注意：
    - 本测试直接调用 LLMClient.chat，不启动 uvicorn
    - 不会修改 / 删除任何生产数据
    - 失败时请检查 .env 中的 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL
    - base_url 必须包含 /v1（DeepSeek: https://api.deepseek.com/v1）
"""
from __future__ import annotations

import os

import pytest


def _env_flag(name: str) -> bool:
    """读取布尔型环境变量开关。"""
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


_RUN_REAL = _env_flag("RUN_REAL_LLM_TEST")


pytestmark = pytest.mark.skipif(
    not _RUN_REAL,
    reason="set RUN_REAL_LLM_TEST=1 (or true/yes/on) to enable real LLM smoke test",
)


@pytest.mark.asyncio
async def test_real_llm_smoke_chat() -> None:
    """对真实 LLM（OpenAI-compatible）执行一次最小调用。

    目标：验证
        - LLM_API_KEY / LLM_BASE_URL / LLM_MODEL 读取成功
        - HTTP 请求成功（不被 Mock 回退）
        - 返回非空字符串
    """
    from backend.app.config import settings
    from backend.app.llm.client import create_llm_client

    # 第二层防护：无 Key 时即使开了开关也 skip（不会硬跑失败）
    if not settings.llm.api_key:
        pytest.skip("LLM_API_KEY 未配置；请在 .env 中设置后重试")
    if not settings.llm.base_url:
        pytest.skip("LLM_BASE_URL 未配置")
    if not settings.llm.model:
        pytest.skip("LLM_MODEL 未配置")

    # 必须拿到真实 client（不是 Mock）。create_llm_client 在 api_key 非空时返回 OpenAICompatibleClient。
    client = create_llm_client(settings.llm)
    assert not isinstance(client, __import__("backend.app.llm.client", fromlist=["MockLLMClient"]).MockLLMClient), (
        "预期使用真实 LLM Client，但 create_llm_client 返回了 MockLLMClient "
        f"（provider={settings.llm.provider!r}, base_url={settings.llm.base_url!r}, "
        f"api_key_set={bool(settings.llm.api_key)}）"
    )

    # 使用短 prompt 节省 token；Phase 2 仅验证链路，不验证回答内容。
    answer = await client.chat(
        [
            {"role": "user", "content": "ping"},
        ]
    )

    assert isinstance(answer, str), f"LLM 返回类型异常：{type(answer).__name__}"
    assert answer.strip(), "LLM 返回内容为空字符串"


@pytest.mark.asyncio
async def test_real_rag_chat_smoke() -> None:
    """对 POST /api/chat 执行一次真实 RAG 全链路冒烟（Phase 3.5.6）。

    目标：验证
        - Chat API → ChatService → RagService 真实链路打通
        - HTTP 200 + answer 非空 + sources / used_chunks_count 结构正确

    注意：知识库为空时 answer 为固定提示语（非 LLM 生成），依然视为链路通过。

    额外跳过条件：
        - DATABASE_URL / EMBEDDING_API_KEY 未配置（RAG 依赖 DB + 向量化）
    """
    from backend.app.config import settings

    if not settings.llm.api_key:
        pytest.skip("LLM_API_KEY 未配置；请在 .env 中设置后重试")
    if not settings.llm.base_url:
        pytest.skip("LLM_BASE_URL 未配置")
    if not settings.llm.model:
        pytest.skip("LLM_MODEL 未配置")
    if not settings.database.url:
        pytest.skip("DATABASE_URL 未配置；无法执行真实 RAG 链路")
    if not settings.embedding.api_key:
        pytest.skip("EMBEDDING_API_KEY 未配置；无法执行真实 RAG 链路")

    from fastapi.testclient import TestClient

    from backend.app.main import app

    with TestClient(app) as c:
        response = c.post("/api/chat", json={"message": "采购入库怎么操作？"})

    assert response.status_code == 200, f"HTTP {response.status_code}: {response.text[:200]}"
    payload = response.json()
    assert isinstance(payload["answer"], str)
    assert payload["answer"].strip(), "answer 为空字符串"
    assert isinstance(payload["sources"], list)
    assert isinstance(payload["used_chunks_count"], int)
    assert payload["used_chunks_count"] >= 0
    # 安全：响应不得泄露内部敏感信息
    body = response.text
    for secret in ("sk-", "Authorization", "Bearer ", "postgresql://", "Traceback"):
        assert secret not in body, f"响应泄露敏感信息: {secret!r}"