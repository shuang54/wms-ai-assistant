"""pytest 共享配置（Phase 3.12 Step 64 首次引入 —— **测试专用**，无生产代码）。

本文件做两件事：

1. **残留守卫**（Step 64 / Phase 5 P-2）：三个会话级守卫，在会话结束时清理
   **本次会话新增**的观测/诊断行（outcome / agent_run / agent_step）；
2. **会话收尾零残留检查**（Phase 6 Step 8B-1）：在三个守卫**全部完成清理之后**，
   对四张观测表做全库零残留断言（严格：无过滤、无白名单；每次重新查询）。

为什么需要守卫（Step 64 引入）：

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

会话收尾零残留检查（Phase 6 Step 8B-1 —— 背景）：

    顶层全量顺序中，``test_assistant_trace_timeline_regression.py::
    TestMatrixExecutionBaseline::test_db_residue_is_zero`` 原先在**会话中途**
    断言"全库四表 = 0"；而 outcome 表的批量清理只发生在**会话结束**
    （本文件的守卫），且存在"写而不自清"的先序 DB 测试
    （如 ``test_assistant_trace_multi_path_e2e.py`` 的 DB 段经真实
    ``POST /api/ai/chat`` 写入 outcome，仅清理 llm_usage）——
    断言时点与清理时点错位 ⇒ 结构性顺序依赖。
    修复：断言**迁移**到会话收尾（本文件 ``_observability_residue_check``），
    通过**显式 fixture 依赖**保证执行顺序（见该 fixture 的依赖方向说明），
    并**每次重新查询**数据库（``fresh_residue_total``，不复用会话中途缓存）。
    边界（如实）：**不覆盖**进程被强制终止的情形（teardown 无机会执行）——
    该情形守卫同样无法清理，由**下一次**会话的检查兜底。

注意：守卫与收尾检查**不改变**任何测试语义 —— 只清理测试自己产生的合成
残留，并在清理完成后做零残留验证；生产装配 / 业务行为 / API contract
均不受影响。
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests import observability_residue_contract as _residue_contract

__all__ = [
    "_assistant_outcome_residue_guard",
    "_agent_run_residue_guard",
    "_agent_step_residue_guard",
    "_observability_residue_check",
]

#: Step 64 新增的 request-level 终态表（只在本守卫中使用）。
_OUTCOME_TABLE: str = "ai_ops.assistant_outcome_record"


@pytest.fixture(scope="session", autouse=True)
def _assistant_outcome_residue_guard(
    _observability_residue_check: Any,
) -> Iterator[None]:
    """会话级残留守卫：删除**本次会话期间新增**的 outcome 行。

    依赖 ``_observability_residue_check``（显式）：依赖者的 teardown **先**
    执行 ⇒ 本守卫清理完成后，收尾检查才运行（Phase 6 Step 8B-1 §4.1）。
    """
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
        # 清理已提交 → 记录顺序证据（收尾检查在 flags 不齐时失败）。
        _residue_contract.mark_guard_cleaned(
            _residue_contract.GUARD_ASSISTANT_OUTCOME
        )


#: Phase 5 P-2：Agent Run 表（只在本守卫中使用；与 outcome 守卫同机制）。
_AGENT_RUN_TABLE: str = "ai_ops.agent_run"


@pytest.fixture(scope="session", autouse=True)
def _agent_run_residue_guard(
    _observability_residue_check: Any,
) -> Iterator[None]:
    """会话级残留守卫：删除**本次会话期间新增**的 agent_run 行。

    为什么需要（与 outcome 守卫同理）：P-2 起生产 Composition Root
    （``api/orchestrator_chat.py``）在 DB 已配置时注入
    ``BestEffortAgentRunRecorder``，因此驱动真实 ``POST /api/ai/chat``
    且命中 AGENT 路由的测试会写一行 run-level 记录；既有测试不感知本表。

    语义（保守）：会话开始记录 ``MAX(id)`` 水位 → 会话结束只删除
    ``id > 水位`` 的行；不使用 TRUNCATE；不修改表结构 / 索引；
    表不存在 / DB 不可达 → 静默跳过（守卫本身不影响任何断言）。
    依赖 ``_observability_residue_check``（显式——清理先于收尾检查）。
    """
    from sqlalchemy import text

    from backend.app.db.session import get_engine

    engine = get_engine()
    if engine is None:
        yield
        return

    try:
        with engine.connect() as conn:
            watermark = int(
                conn.execute(
                    text(
                        f"SELECT COALESCE(MAX(id), 0) FROM {_AGENT_RUN_TABLE}"
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
                text(f"DELETE FROM {_AGENT_RUN_TABLE} WHERE id > :watermark"),
                {"watermark": watermark},
            )
        _residue_contract.mark_guard_cleaned(
            _residue_contract.GUARD_AGENT_RUN
        )


#: Phase 5 AgentStep Persistence：Agent Step 表（只在本守卫中使用）。
_AGENT_STEP_TABLE: str = "ai_ops.agent_step"


@pytest.fixture(scope="session", autouse=True)
def _agent_step_residue_guard(
    _observability_residue_check: Any,
) -> Iterator[None]:
    """会话级残留守卫：删除**本次会话期间新增**的 agent_step 行。

    为什么需要（与前两个守卫同理）：AgentStep Persistence 起生产
    Composition Root 注入 ``BestEffortAgentStepRecorder``；驱动真实
    ``POST /api/ai/chat`` 且命中 AGENT 路由的测试会写 Step 行。

    语义（保守）：记录 ``MAX(id)`` 水位 → 只删除 ``id > 水位`` 的行；
    不使用 TRUNCATE；不修改表结构 / 索引；表不存在 / DB 不可达 → 静默跳过。
    依赖 ``_observability_residue_check``（显式——清理先于收尾检查）。
    """
    from sqlalchemy import text

    from backend.app.db.session import get_engine

    engine = get_engine()
    if engine is None:
        yield
        return

    try:
        with engine.connect() as conn:
            watermark = int(
                conn.execute(
                    text(
                        f"SELECT COALESCE(MAX(id), 0) FROM {_AGENT_STEP_TABLE}"
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
                text(f"DELETE FROM {_AGENT_STEP_TABLE} WHERE id > :watermark"),
                {"watermark": watermark},
            )
        _residue_contract.mark_guard_cleaned(
            _residue_contract.GUARD_AGENT_STEP
        )


@pytest.fixture(scope="session", autouse=True)
def _observability_residue_check() -> Iterator[None]:
    """会话收尾：四张观测表**全库零残留**检查（Phase 6 Step 8B-1）。

    依赖方向（**显式** —— §4.1；不依赖测试文件顺序 / 函数定义顺序）::

        _assistant_outcome_residue_guard ─┐
        _agent_run_residue_guard         ─┼─→ 均依赖本 fixture
        _agent_step_residue_guard        ─┘

    三个清理守卫都是本 fixture 的**依赖方** ⇒ pytest 规则（依赖者的
    teardown 先执行）保证：三个守卫的 DELETE 全部完成后，本 fixture 的
    teardown 才执行收尾断言。依赖图在 ``tests/test_observability_residue_lifecycle.py``
    中以真实 lifecycle 与签名断言验证（含 teardown 顺序证据）。

    断言内容（**严格**，与迁移前的运行时断言等价、不弱化）：
        · 四张观测表逐表 ``COUNT(*)``（**无过滤**）求和 == 0
          （== ``EXPECTED_RESIDUE_AFTER_CLEANUP``）；
        · 三个清理守卫均已标记完成（顺序证据——若某守卫未完成清理，
          本检查失败并指出缺失者）。
    每次调用**重新查询**数据库（``fresh_residue_total``，无缓存）——
    绝不复用会话中途的缓存值。

    嵌套子进程语义（Phase 6 Step 8B-1 实测修正）：本 fixture 的 setup（会话
    最早）捕获"会话开始全库快照"——收尾断言"回到会话开始水平"：
      · 根会话（干净库，快照 == 0）⇒ 等价"全库 == 0"（§4.2 原文语义）；
      · **嵌套子进程会话**（治理容器测试嵌套运行 pytest）⇒ 父会话未清理
        的行仍在库，"全库 == 0"物理上不成立——断言"本会话零残留"
        （fresh == 快照）才是正确且不弱化的语义。

    边界（如实）：正常测试会话（含测试失败后 pytest 仍执行 teardown 的
    情形）保证"全部清理之后执行检查"；**进程被强制终止**时 teardown 无
    机会执行，本机制**不**声称覆盖该情形（由下一次会话的守卫与检查兜底）。
    """
    _residue_contract.capture_session_start_residue()
    yield
    _residue_contract.assert_session_end_residue_cleared()
