"""Real LLM Usage Smoke（Phase 3.12 Step 58）—— 真实 DeepSeek + 生产默认 Client + DB Sink。

**默认跳过**（不消耗额度、不产生网络请求、不写库）。

开启方式（沿用项目既有机制 `RUN_REAL_LLM_TEST`；`RUN_REAL_LLM_TESTS` 作为等价别名）::

    $env:RUN_REAL_LLM_TEST = "1"
    python -m pytest -q tests/test_llm_usage_real_llm_smoke.py

验证目标（**恰好 1 次真实 LLM 请求**）：

    get_default_llm_client()            （生产默认 Client；Step 56 接线）
        ↓ 真实 DeepSeek HTTP（OpenAI-compatible）
    DatabaseLLMAccountingSink           （**不是** Noop —— 必须真实验证）
        ↓
    ai_ops.llm_usage_record             恰好 1 行（assistant_request_id = A）
        ↓
    GET /api/observability/assistant-trace/{A}    → llm_usage[] 可读

安全（不得违反）：
    * 不修改生产代码 / Prompt / API；不新增配置项；
    * 不使用真实 WMS 业务数据（只写 1 条 synthetic usage row）；
    * 测试结束**精确删除**该行（按 assistant_request_id + provider），禁用 TRUNCATE；
    * provider request_id 来自真实响应（`LLMResponse.metadata["request_id"]`），
      若 provider 未暴露则允许 None（不伪造 / 不生成 uuid 代替）。

失败语义：
    * 环境问题（无 Key / 网络不可达 / provider 不可用 / 限流 / 超时）→ **SKIPPED**
      （ENVIRONMENT UNAVAILABLE，不修改任何代码）；
    * 应用问题（LLM 成功但 usage 未持久化 / correlation 错误）→ **FAILED**。
"""
from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text

from backend.app.config import settings

_SMOKE_QUESTION = "请只回答：OK"


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _real_llm_enabled() -> bool:
    return _env_flag("RUN_REAL_LLM_TEST") or _env_flag("RUN_REAL_LLM_TESTS")


pytestmark = pytest.mark.skipif(
    not _real_llm_enabled(),
    reason=(
        "set RUN_REAL_LLM_TEST=1 (or RUN_REAL_LLM_TESTS=1) to enable the "
        "real DeepSeek usage smoke"
    ),
)


def _require_environment() -> None:
    if not settings.llm.api_key:
        pytest.skip("ENVIRONMENT UNAVAILABLE: LLM_API_KEY 未配置")
    if not settings.llm.base_url:
        pytest.skip("ENVIRONMENT UNAVAILABLE: LLM_BASE_URL 未配置")
    if not settings.llm.model:
        pytest.skip("ENVIRONMENT UNAVAILABLE: LLM_MODEL 未配置")
    if not settings.database.url.strip():
        pytest.skip("ENVIRONMENT UNAVAILABLE: DATABASE_URL 未配置（无法验证持久化）")


def _count_rows() -> int:
    from backend.app.db.session import get_engine

    engine = get_engine()
    assert engine is not None
    with engine.connect() as conn:
        return int(
            conn.execute(
                text("SELECT COUNT(*) FROM ai_ops.llm_usage_record")
            ).scalar_one()
        )


def _rows_for(assistant_request_id: str) -> list[Any]:  # noqa: ANN401
    from backend.app.db.models.llm_usage_record import LLMUsageRecord
    from backend.app.db.session import get_session_factory

    factory = get_session_factory()
    assert factory is not None
    with factory() as session:
        return list(
            session.scalars(
                select(LLMUsageRecord)
                .where(LLMUsageRecord.assistant_request_id == assistant_request_id)
                .order_by(LLMUsageRecord.id.asc())
            ).all()
        )


def _delete_rows(assistant_request_id: str) -> int:
    """精确删除本次 smoke 的行（assistant_request_id + provider 双重条件）。"""
    from backend.app.db.session import get_engine

    engine = get_engine()
    assert engine is not None
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "DELETE FROM ai_ops.llm_usage_record "
                "WHERE assistant_request_id = :assistant_request_id "
                "AND provider = :provider"
            ),
            {
                "assistant_request_id": assistant_request_id,
                "provider": settings.llm.provider,
            },
        )
    return int(result.rowcount or 0)


async def test_real_deepseek_usage_persisted_and_trace_readable() -> None:
    """1 次真实 LLM 请求 → 恰好 1 条 usage → Assistant Trace 可读 → 精确清理。"""
    from backend.app.llm.client import (
        MockLLMClient,
        get_default_accounting_sink,
        get_default_llm_client,
        reset_default_llm_client,
    )
    from backend.app.services.assistant_trace import assistant_trace_scope
    from backend.app.services.llm_usage_persistence_service import (
        DatabaseLLMAccountingSink,
    )

    _require_environment()

    reset_default_llm_client()
    client = get_default_llm_client()
    sink = get_default_accounting_sink()
    assistant_request_id = f"step58-smoke-{uuid.uuid4().hex[:8]}"
    before_count = _count_rows()

    try:
        # ① 生产默认 Client（**不是** Mock），且 accounting 为 DB sink（**不是** Noop）
        assert not isinstance(client, MockLLMClient), (
            "默认 Client 是 MockLLMClient —— 真实 LLM 环境不可用"
        )
        assert isinstance(sink, DatabaseLLMAccountingSink)
        inner = getattr(client, "_client", client)
        assert inner._accounting_sink is sink

        # ② 一次真实请求（最小问题；不触发 SQL / Tool / 检索）
        try:
            with assistant_trace_scope(assistant_request_id):
                answer = await client.generate(_SMOKE_QUESTION)
        except Exception as exc:  # noqa: BLE001 —— 环境问题不伪装成应用失败
            from backend.app.llm.client import LLMError

            if isinstance(exc, LLMError):
                pytest.skip(
                    "ENVIRONMENT UNAVAILABLE: real LLM call failed "
                    f"({type(exc).__name__})"
                )
            raise

        assert isinstance(answer, str) and answer.strip(), "真实 LLM 返回空回答"

        # ③ usage 持久化：恰好 1 行，且 correlation 正确
        rows = _rows_for(assistant_request_id)
        assert len(rows) == 1, f"期望恰好 1 条 usage，实际 {len(rows)}"
        row = rows[0]
        assert row.assistant_request_id == assistant_request_id
        assert row.provider == settings.llm.provider
        assert row.model  # 真实响应模型（不伪造）
        assert row.created_at is not None
        # provider request_id：真实响应 id；未暴露时为 None（不伪造）
        assert row.request_id != assistant_request_id
        if row.request_id is not None:
            assert isinstance(row.request_id, str) and row.request_id.strip()
        if row.total_tokens is not None:
            assert row.total_tokens >= 0

        # ④ Assistant Trace 读回（真实 HTTP 端点）
        from fastapi.testclient import TestClient

        from backend.app.main import app

        with TestClient(app) as http:
            response = http.get(
                f"/api/observability/assistant-trace/{assistant_request_id}"
            )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["assistant_request_id"] == assistant_request_id
        assert len(payload["llm_usage"]) >= 1
        item = payload["llm_usage"][0]
        assert item["id"] == row.id
        assert item["assistant_request_id"] == assistant_request_id
        assert item["request_id"] == row.request_id
        assert item["provider"] == row.provider

        # ⑤ 安全：行只含白名单列；提问原文 / 凭据不入库
        assert set(row.__table__.columns.keys()) == {
            "id",
            "request_id",
            "provider",
            "model",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "created_at",
            "assistant_request_id",
        }
        blob = repr(
            [getattr(row, column.name) for column in row.__table__.columns]
        )
        for forbidden in (_SMOKE_QUESTION, "sk-", "Bearer ", "postgresql://"):
            assert forbidden not in blob, f"usage 行泄露敏感内容: {forbidden!r}"

        # ⑥ 行数：before + 1（1 次真实 LLM 调用 → 1 条）
        assert _count_rows() == before_count + 1

        # ⑦ 证据输出（`pytest -s`；不含任何凭据）
        provider_request_id = row.request_id or ""
        print(
            "\n[step58-smoke]\n"
            f"  assistant_request_id = {assistant_request_id}\n"
            f"  provider = {row.provider} | model = {row.model}\n"
            f"  provider request_id = {provider_request_id!r} "
            f"(len={len(provider_request_id)})\n"
            f"  request_id == assistant_request_id ? "
            f"{row.request_id == assistant_request_id}\n"
            f"  tokens = prompt:{row.prompt_tokens} "
            f"completion:{row.completion_tokens} total:{row.total_tokens}\n"
            f"  row id = {row.id} | created_at = {row.created_at}\n"
            f"  answer = {answer.strip()[:24]!r}\n"
            f"  trace llm_usage = {len(payload['llm_usage'])} "
            f"| trace request_id = {item['request_id']!r}\n"
            f"  rows: before={before_count} after={_count_rows()}\n"
        )
    finally:
        # ⑦ 精确清理 + residue = 0（不使用 TRUNCATE）
        deleted = _delete_rows(assistant_request_id)
        assert deleted <= 1
        assert _rows_for(assistant_request_id) == []
        assert _count_rows() == before_count
        reset_default_llm_client()


__all__ = ["test_real_deepseek_usage_persisted_and_trace_readable"]
