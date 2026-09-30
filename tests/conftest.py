"""pytest 共享配置（Phase 3.12 Step 64 首次引入 —— **测试专用**，无生产代码）。

本文件只做一件事：**Assistant Outcome 残留守卫**。

为什么需要：

    Step 64 起，生产 Composition Root（``api/orchestrator_chat.py``）在
    ``DATABASE_URL`` 已配置时默认注入 ``BestEffortAssistantOutcomeRecorder``
    （生产接线必须真实），因此**任何**驱动真实 ``POST /api/ai/chat``
    的测试（离线 E2E 与 DB-gated 都包括）都会写一行 request-level 终态到
    ``ai_ops.assistant_outcome_record``。
    既有测试各自定向清理 llm_usage / tool_execution / rag_execution 行，
    但不感知本阶段新增的表 → 需要会话级兜底清理。

守卫语义（保守，**绝不触碰既有数据**）：

    * 只要 ``DATABASE_URL`` 已配置（即 recorder 真的会写入）就生效，
      与 ``RUN_DB_TESTS`` 无关（离线 E2E 同样会写）；
    * 会话开始时记录 ``MAX(id)`` 水位 → 会话结束时只删除
      ``id > 水位`` 的行（= 本次会话新增的测试残留）；
    * 不使用 TRUNCATE；不修改表结构；不新增 / 不删除索引；
    * 无 DB / 未配置 / 表不存在 → 静默跳过（守卫本身不影响任何断言）。

注意：本守卫**不改变**任何测试语义 —— 它只清理测试自己产生的合成残留；
生产装配 / 业务行为 / API contract 均不受影响。
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

__all__ = ["_assistant_outcome_residue_guard"]

#: Step 64 新增的 request-level 终态表（只在本守卫中使用）。
_OUTCOME_TABLE: str = "ai_ops.assistant_outcome_record"


@pytest.fixture(scope="session", autouse=True)
def _assistant_outcome_residue_guard() -> Iterator[None]:
    """会话级残留守卫：删除**本次会话期间新增**的 outcome 行。"""
    from sqlalchemy import text

    from backend.app.db.session import get_engine

    engine = get_engine()
    if engine is None:                       # DATABASE_URL 未配置 → 无写入
        yield
        return

    try:
        with engine.connect() as conn:
            watermark = int(
                conn.execute(
                    text(
                        f"SELECT COALESCE(MAX(id), 0) FROM {_OUTCOME_TABLE}"
                    )
                ).scalar_one()
            )
    except Exception:  # noqa: BLE001 —— 表不存在 / DB 不可达 → 不阻断测试
        yield
        return

    try:
        yield
    finally:
        with engine.begin() as conn:
            conn.execute(
                text(f"DELETE FROM {_OUTCOME_TABLE} WHERE id > :watermark"),
                {"watermark": watermark},
            )
