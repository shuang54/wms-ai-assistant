# -*- coding: utf-8 -*-
"""定向清理 wms_ai_test 中已核验的 15 行 tool_execution_record 残留。

安全边界（硬编码，不可配置）：

* 只连接 localhost:5433/wms_ai_test；host / port / database 任一不匹配即退出；
* 只删除 TARGET_IDS 精确集合；
* 不使用 project_id IS NULL 批量删、不使用全表 DELETE / TRUNCATE；
* 单一事务，禁止自动提交；只有 RETURNING id 数量与集合全部匹配才 COMMIT，
  否则 ROLLBACK；
* 不触碰其他表、不清理 Docker 卷、不修改生产代码 / 冻结基线 / Gate。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text  # noqa: E402

from backend.app.db.session import get_engine  # noqa: E402

TARGET_IDS: list[int] = [
    94, 95, 96, 97, 98,
    147, 148, 149, 150, 151,
    200, 201, 202, 203, 204,
]

TARGET_TABLE = "ai_ops.tool_execution_record"

OBSERVABILITY_TABLES = (
    "ai_ops.llm_usage_record",
    "ai_ops.tool_execution_record",
    "ai_ops.rag_execution_record",
    "ai_ops.assistant_outcome_record",
)


def _table_counts(conn) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in OBSERVABILITY_TABLES:
        counts[table] = conn.execute(
            text(f"SELECT COUNT(*) FROM {table}")
        ).scalar_one()
    return counts


def main() -> int:
    engine = get_engine()

    # ---------- 2.1 / 2.2 数据库身份硬断言 ----------
    print("=== 1. database identity assertion ===")
    print(f"  host     = {engine.url.host}")
    print(f"  port     = {engine.url.port}")
    print(f"  database = {engine.url.database}")
    assert engine.url.host == "localhost", f"HOST MISMATCH: {engine.url.host}"
    assert engine.url.port == 5433, f"PORT MISMATCH: {engine.url.port}"
    assert engine.url.database == "wms_ai_test", \
        f"DB MISMATCH: {engine.url.database}"
    print("  ASSERT OK -> localhost:5433/wms_ai_test")
    print()

    with engine.connect() as conn:
        # ---------- 2.3 目标 ID 存在性与数量 ----------
        print("=== 2. pre-delete target ID verification ===")
        placeholders = ",".join(str(i) for i in TARGET_IDS)
        rows = conn.execute(
            text(
                f"SELECT id, request_id, tool_name, project_id, success, "
                f"error_type FROM {TARGET_TABLE} "
                f"WHERE id IN ({placeholders}) ORDER BY id"
            )
        ).fetchall()
        found_ids = sorted(r[0] for r in rows)
        print(f"  rows found        = {len(rows)}")
        print(f"  expected          = {len(TARGET_IDS)}")
        print(f"  found ID set      = {found_ids}")
        print(f"  target ID set     = {sorted(TARGET_IDS)}")
        assert len(rows) == len(TARGET_IDS), \
            f"COUNT MISMATCH: {len(rows)} != {len(TARGET_IDS)}"
        assert found_ids == sorted(TARGET_IDS), "ID SET MISMATCH"
        print("  ASSERT OK -> exactly 15 rows, ID set identical")

        # ---------- 2.4 逐行属性复核 ----------
        print()
        print("=== 2b. per-row attribute verification ===")
        for r in rows:
            print(f"  id={r[0]:>4}  tool_name={r[2]!r}  project_id={r[3]!r}  "
                  f"success={r[4]!r}  error_type={r[5]!r}")
        bad_tool = [r[0] for r in rows if r[2] != "get_inventory"]
        bad_proj = [r[0] for r in rows if r[3] is not None]
        bad_succ = [r[0] for r in rows if r[4] is not True]
        bad_err = [r[0] for r in rows if r[5] is not None]
        print(f"  tool_name != 'get_inventory' : {bad_tool}")
        print(f"  project_id IS NOT NULL       : {bad_proj}")
        print(f"  success IS NOT TRUE          : {bad_succ}")
        print(f"  error_type IS NOT NULL       : {bad_err}")
        assert not bad_tool, f"tool_name mismatch: {bad_tool}"
        assert not bad_proj, f"project_id mismatch: {bad_proj}"
        assert not bad_succ, f"success mismatch: {bad_succ}"
        assert not bad_err, f"error_type mismatch: {bad_err}"
        print("  ASSERT OK -> all 15 rows match every required attribute")

        before = _table_counts(conn)
        print()
        print("=== 2c. pre-delete absolute counts ===")
        for table, n in before.items():
            print(f"  {table:44s} = {n}")

    # ---------- 3. 事务内定向删除 ----------
    print()
    print("=== 3. transactional targeted delete ===")
    delete_sql = text(
        f"DELETE FROM {TARGET_TABLE} "
        f"WHERE id IN ({placeholders}) "
        f"AND tool_name = 'get_inventory' "
        f"AND project_id IS NULL "
        f"AND success = TRUE "
        f"AND error_type IS NULL "
        f"RETURNING id"
    )
    with engine.begin() as conn:          # 单一事务；异常即回滚
        returning = conn.execute(delete_sql).scalars().all()
        returned_ids = sorted(returning)
        print(f"  RETURNING id count = {len(returned_ids)}")
        print(f"  RETURNING id set   = {returned_ids}")
        assert len(returned_ids) == len(TARGET_IDS), \
            f"RETURNING COUNT MISMATCH: {len(returned_ids)}"
        assert returned_ids == sorted(TARGET_IDS), \
            f"RETURNING ID SET MISMATCH: {returned_ids}"
        print("  ASSERT OK -> RETURNING matches exactly -> COMMIT")
    print("  transaction COMMITTED")

    # ---------- 4. 提交后核验 ----------
    print()
    print("=== 4. post-commit verification ===")
    with engine.connect() as conn:
        after = _table_counts(conn)
        expected = {t: 0 for t in OBSERVABILITY_TABLES}
        for table, n in after.items():
            flag = "OK" if n == 0 else "NON-ZERO"
            print(f"  {table:44s} = {n}   [{flag}]")
        print(f"  expected all zero : {after == expected}")

        remaining = conn.execute(
            text(
                f"SELECT id FROM {TARGET_TABLE} "
                f"WHERE id IN ({placeholders}) ORDER BY id"
            )
        ).scalars().all()
        print(f"  target IDs still present = {list(remaining)}")
        print(f"  all target IDs gone      = {not remaining}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
