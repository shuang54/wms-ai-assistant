# -*- coding: utf-8 -*-
"""只读核验 wms_ai_test 的 tool_execution_record 残留（M2.3 A2 验收配套）。

本脚本**只读**：不执行 DELETE / TRUNCATE / UPDATE / INSERT。
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import text  # noqa: E402

from backend.app.db.session import get_engine  # noqa: E402

OBSERVABILITY_TABLES = (
    "ai_ops.llm_usage_record",
    "ai_ops.tool_execution_record",
    "ai_ops.rag_execution_record",
    "ai_ops.assistant_outcome_record",
)


def main() -> int:
    engine = get_engine()

    # ---- 硬断言：任何 DB 操作之前 ----
    assert engine.url.database == "wms_ai_test", f"NOT TEST DB: {engine.url.database}"
    assert engine.url.port == 5433, f"WRONG PORT: {engine.url.port}"
    print(f"engine identity OK -> db={engine.url.database} "
          f"port={engine.url.port} host={engine.url.host}")
    print()

    with engine.connect() as conn:
        print("=== 四张观测表绝对行数 ===")
        for table in OBSERVABILITY_TABLES:
            total = conn.execute(
                text(f"SELECT COUNT(*) FROM {table}")
            ).scalar_one()
            print(f"  {table:44s} = {total}")
        print()

        rows = conn.execute(
            text(
                "SELECT id, request_id, round, tool_name, project_id, "
                "success, started_at, finished_at, duration_ms, error_type "
                "FROM ai_ops.tool_execution_record ORDER BY id"
            )
        ).fetchall()

        print(f"=== ai_ops.tool_execution_record 全部行（{len(rows)}）===")
        print(f"{'id':>6}  {'request_id':38}  {'tool_name':16}  "
              f"{'project_id':13}  {'ok':5}  started_at")
        for row in rows:
            print(f"{row[0]:>6}  {row[1]:38}  {row[3]:16}  "
                  f"{str(row[4]):13}  {str(row[5]):5}  "
                  f"{row[6].isoformat() if row[6] else None}")
        print()

        ids = [row[0] for row in rows]
        print("ID 集合        =", ids)
        print("ID 数量        =", len(ids), "| 唯一 =", len(set(ids)))
        print("request_id 唯一 =", len({row[1] for row in rows}))
        print("project_id 取值 =", sorted({str(row[4]) for row in rows}))
        print("tool_name 取值  =", sorted({row[3] for row in rows}))
        stamps = [row[6] for row in rows if row[6]]
        print("started_at 范围 =",
              f"{min(stamps).isoformat()} .. {max(stamps).isoformat()}")
        print("duration_ms 取值 =", sorted({row[8] for row in rows}))
        print("error_type 取值  =", sorted({str(row[9]) for row in rows}))
        print()

        print("=== 其他三表残留（应为 0）===")
        for table in OBSERVABILITY_TABLES[0], OBSERVABILITY_TABLES[2], \
                OBSERVABILITY_TABLES[3]:
            total = conn.execute(
                text(f"SELECT COUNT(*) FROM {table}")
            ).scalar_one()
            print(f"  {table:44s} = {total}")
        print()

        print("=== 归属交叉核对（其他表 request_id 与 tool_execution 的交集）===")
        for table, column in (
            ("ai_ops.llm_usage_record", "request_id"),
            ("ai_ops.rag_execution_record", "request_id"),
            ("ai_ops.assistant_outcome_record", "assistant_request_id"),
        ):
            hits = conn.execute(
                text(
                    f"SELECT COUNT(*) FROM {table} WHERE {column} IN "
                    "(SELECT request_id FROM ai_ops.tool_execution_record)"
                )
            ).scalar_one()
            print(f"  {table}.{column} 命中数 = {hits}")
        print()

        print("=== 该 request_id 集合是否出现在 conversation / agent / workflow ===")
        rid_list = ",".join(f"'{r[1]}'" for r in rows)
        for table, column in (
            ("ai_ops.conversation_turn", "request_id"),
            ("ai_ops.agent_step", "request_id"),
            ("ai_ops.workflow_node_run", "request_id"),
        ):
            try:
                hits = conn.execute(
                    text(
                        f"SELECT COUNT(*) FROM {table} WHERE {column} IN "
                        f"({rid_list})"
                    )
                ).scalar_one()
                print(f"  {table}.{column} 命中数 = {hits}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {table}.{column} 查询失败: {type(exc).__name__}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
