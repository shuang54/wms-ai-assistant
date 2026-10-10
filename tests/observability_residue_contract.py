"""观测四表"会话收尾零残留"共享契约（Phase 6 Step 8B-1）。

**为什么需要本模块**（无法通过现有结构避免新增）：

    * 全库零残留检查已**迁移**到 ``tests/conftest.py`` 的会话收尾
      （必须发生在三个清理守卫之后——见 conftest 依赖图说明）；
    * ``tests/conftest.py`` **不得**反向 import 测试模块
      （``test_assistant_trace_timeline_regression.py`` 是测试模块——
      反向依赖会形成循环 / 违反导入方向）；
    * 四张表名与"清理后期望值"必须**单一来源**（不得在 conftest 与
      regression 文件各定义一份）。

职责（**纯测试基础设施**；模块顶层零 DB / 零 pytest 依赖）：

    ``OBSERVABILITY_RESIDUE_TABLES``
        四张观测表（表名， 关联列）——与 regression 文件的 ``_RESIDUE_TABLES``
        是**同一对象**（该文件改为引用本常量，不再自行定义副本）。
    ``EXPECTED_RESIDUE_AFTER_CLEANUP``
        会话清理完成后的期望全库残留（0；与 Step 89
        ``MATRIX_EXECUTION_BASELINE.db_residue`` 记录值一致）。
    ``query_residue_total(engine)``
        逐表 ``COUNT(*)``（**无 WHERE 过滤**）求和——**无任何缓存**。
    ``cached_residue_total()``
        会话内缓存版（保留原 ``_db_residue_total`` 的 ``lru_cache`` 语义；
        供 matrix baseline 复用）；测试可 ``cache_clear()``。
    ``fresh_residue_total()``
        **每次重新查询**版——会话收尾检查使用（保证不读陈旧缓存）。
    ``capture_session_start_residue(engine=None)``
        记录**会话开始**（收尾检查 fixture setup 时）的全库残留快照。
    ``mark_guard_cleaned(name)`` / ``cleaned_guards()`` / ``reset_guard_marks()``
        三个清理守卫的"已完成清理"标记（收尾检查的顺序证据通道）。
    ``assert_session_end_residue_cleared(engine=None, check_guards=True)``
        会话收尾检查本体（测试可注入 fake engine）。

**会话收尾语义（含嵌套子进程，Phase 6 Step 8B-1 实测修正）**：

    · 根会话（会话开始快照 == 0，常态干净库）⇒ 收尾断言
      "全库 == 0"——与迁移前断言**等价**、不弱化；
    · **嵌套子进程会话**（由治理容器测试嵌套运行 pytest——`_run_pytest`）
      ⇒ 收尾时**父会话**未清理的行仍在库，"全库 == 0"物理上不成立；
      正确语义 = "**回到会话开始水平**"（本会话零残留——
      即断言 ``fresh == capture_session_start_residue``）。
      根会话下两者**完全一致**（start == 0）。
    · 断言**永远**是全库逐表 ``COUNT(*)``（**无过滤**、无白名单），
      并保留"本会话新增行已被清理"的完整强度。

缓存语义（§4.3）：收尾检查**绝不**复用会话中途的缓存值——
``fresh_residue_total`` 不做缓存；验收测试以注入 fake engine 的查询计数
证明"每次检查都真实重新查询数据库"。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

__all__ = [
    "OBSERVABILITY_RESIDUE_TABLES",
    "EXPECTED_RESIDUE_AFTER_CLEANUP",
    "GUARD_ASSISTANT_OUTCOME",
    "GUARD_AGENT_RUN",
    "GUARD_AGENT_STEP",
    "query_residue_total",
    "cached_residue_total",
    "fresh_residue_total",
    "capture_session_start_residue",
    "session_start_residue",
    "reset_session_snapshot",
    "mark_guard_cleaned",
    "cleaned_guards",
    "reset_guard_marks",
    "assert_session_end_residue_cleared",
]

#: 四张观测表（表名， 关联列）——**单一来源**（conftest 收尾检查与
#: regression 文件的 _RESIDUE_TABLES 共同引用本常量）。
OBSERVABILITY_RESIDUE_TABLES: tuple[tuple[str, str], ...] = (
    ("llm_usage_record", "assistant_request_id"),
    ("tool_execution_record", "request_id"),
    ("rag_execution_record", "request_id"),
    ("assistant_outcome_record", "assistant_request_id"),
)

#: 会话清理完成后的期望全库残留（**严格 0**；不添加任何过滤 / 白名单。
#: 嵌套子进程会话下属性地与"会话开始快照"比较——见模块 docstring）。
EXPECTED_RESIDUE_AFTER_CLEANUP: int = 0

#: 三个会话级清理守卫的名称（收尾检查的顺序证据）。
GUARD_ASSISTANT_OUTCOME: str = "assistant_outcome"
GUARD_AGENT_RUN: str = "agent_run"
GUARD_AGENT_STEP: str = "agent_step"

_GUARD_NAMES: tuple[str, ...] = (
    GUARD_ASSISTANT_OUTCOME,
    GUARD_AGENT_RUN,
    GUARD_AGENT_STEP,
)

#: 已"完成清理"的守卫集合（会话进程内；由 conftest 守卫在 DELETE 成功后标记）。
_cleaned_guards: set[str] = set()

#: 会话开始时的全库残留快照（由 conftest 收尾检查 fixture 的 setup 捕获；
#: ``None`` = 未捕获 —— 收尾检查退化为"必须为 0"的根会话语义）。
_session_start_residue: int | None = None


def mark_guard_cleaned(name: str) -> None:
    """标记某个清理守卫**已完成清理**（在其 DELETE 成功提交之后调用）。"""
    _cleaned_guards.add(name)


def cleaned_guards() -> frozenset[str]:
    """当前已完成的守卫集合（只读）。"""
    return frozenset(_cleaned_guards)


def reset_guard_marks() -> None:
    """清空标记（**仅供测试**隔离用例状态；不参与生产/会话逻辑）。"""
    _cleaned_guards.clear()


def _default_engine() -> Any | None:
    """默认引擎（延迟 import——保持模块顶层零 DB 依赖）。"""
    try:
        from backend.app.db.session import get_engine

        return get_engine()
    except Exception:  # noqa: BLE001 —— 与守卫同语义：不可用视同未配置
        return None


def query_residue_total(engine: Any) -> int | None:
    """四张观测表总行数（逐表 ``COUNT(*)``，**无过滤**，**无缓存**）。

    ``engine`` 为 ``None`` ⇒ ``None``（不猜测）。
    表不存在 / DB 不可达 ⇒ 异常向上抛出（由调用方决定语义：守卫静默、
    收尾检查静默——两者均不得伪造 0）。
    """
    if engine is None:
        return None
    from sqlalchemy import text

    with engine.connect() as connection:
        return sum(
            int(
                connection.execute(
                    text(f"SELECT COUNT(*) FROM ai_ops.{table}")
                ).scalar_one()
            )
            for table, _ in OBSERVABILITY_RESIDUE_TABLES
        )


@lru_cache(maxsize=1)
def cached_residue_total() -> int | None:
    """会话内缓存版（保留原 ``_db_residue_total`` 语义）。

    DB 不可用 / 查询失败 ⇒ ``None``（不猜测；不缓存异常——异常路径不进入
    lru_cache 结果）。需要新鲜值时使用 :func:`fresh_residue_total`
    或先调用 ``cached_residue_total.cache_clear()``。
    """
    engine = _default_engine()
    if engine is None:
        return None
    try:
        return query_residue_total(engine)
    except Exception:  # noqa: BLE001 —— 与原实现一致：不可用 → None
        return None


def fresh_residue_total() -> int | None:
    """**每次重新查询**（无缓存）——会话收尾检查专用（§4.3）。

    DB 不可用 / 查询失败 ⇒ ``None``（收尾检查据此静默跳过——不伪造 0）。
    """
    engine = _default_engine()
    if engine is None:
        return None
    try:
        return query_residue_total(engine)
    except Exception:  # noqa: BLE001 —— 与守卫同语义：表缺失 / 不可达 → 跳过
        return None


def capture_session_start_residue(*, engine: Any = None) -> int | None:
    """记录**会话开始**的全库残留快照（conftest 收尾检查 fixture setup 时调用）。

    返回值同时写入模块状态（``session_start_residue()`` 可读）。
    DB 不可用 / 查询失败 ⇒ ``None``（不设置快照 ⇒ 收尾退化为根会话语义）。
    """
    global _session_start_residue
    resolved = _default_engine() if engine is None else engine
    try:
        total = query_residue_total(resolved)
    except Exception:  # noqa: BLE001 —— 表缺失 / 不可达：不设快照
        total = None
    _session_start_residue = total
    return total


def session_start_residue() -> int | None:
    """当前会话开始快照（``None`` = 未捕获 / DB 不可用）。"""
    return _session_start_residue


def reset_session_snapshot() -> None:
    """清空会话开始快照（**仅供测试**隔离用例状态）。"""
    global _session_start_residue
    _session_start_residue = None


def assert_session_end_residue_cleared(
    *,
    engine: Any = None,
    check_guards: bool = True,
) -> None:
    """会话收尾零残留检查（**必须在三个清理守卫完成之后调用**）。

    参数：
        engine:      注入用（测试）；``None`` ⇒ 使用默认引擎。
        check_guards: 是否断言三个清理守卫均已标记完成（顺序证据）。

    语义（严格；与迁移前断言在根会话下**等价**）：
        · 全库逐表 ``COUNT(*)`` 求和（**无过滤**）；
        · 期望值 = 会话开始快照（未捕获 / 根会话 ⇒ ``0``）——
          即"**本会话未留下任何新增残留**"；根会话（干净库）下
          与"全库 == 0"完全一致（§4.2 原文语义）；
        · **上界语义**（Phase 6 Step 8B-1 实测修正）：断言
          ``total <= 快照``——会话内的（自清）测试可能把**会话前**的既有
          残留一并清掉（``total < 快照``，库更干净）——不构成残留违规；
          唯一违规 = "本会话新增未清理"（``total > 快照``）。
          根会话（快照 == 0）下退化为**严格 ``total == 0``**（原文语义保留）。
        · 每次调用**重新查询**（``fresh_residue_total``）——不复用任何会话缓存；
        · DB 不可用 / 表不存在 ⇒ 静默返回（与守卫同语义——不阻断无 DB 会话，
          也不伪造通过）。
    """
    resolved = _default_engine() if engine is None else engine
    try:
        total = query_residue_total(resolved)
    except Exception:  # noqa: BLE001 —— 表缺失 / 不可达：与守卫同语义静默跳过
        return
    if total is None:
        return
    if check_guards:
        missing = sorted(set(_GUARD_NAMES) - _cleaned_guards)
        assert not missing, (
            "会话收尾检查早于清理守卫完成："
            f"未标记完成的守卫 = {missing}（期望先完成清理的守卫："
            f"{list(_GUARD_NAMES)}）"
        )
    expected = (
        _session_start_residue
        if _session_start_residue is not None
        else EXPECTED_RESIDUE_AFTER_CLEANUP
    )
    # 上界语义：**本会话未新增任何残留**。
    #   · 根会话（快照 == 0）⇒ total <= 0 ⇒ **严格 == 0**（§4.2 原文语义）；
    #   · 嵌套 / 分片链：会话内的（自清）测试可能把**会话前**的既有残留
    #     一并清掉（total < 快照）——库更干净不构成残留违规（下界不设）。
    assert total <= expected, (
        "会话收尾观测表残留异常（本会话新增未清理）："
        f"total={total}（上界 {expected}"
        + (
            "= 会话开始快照"
            if _session_start_residue is not None
            else "= 全库零残留"
        )
        + f"；表 = {[t for t, _ in OBSERVABILITY_RESIDUE_TABLES]}）"
    )
