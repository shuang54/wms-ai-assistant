# -*- coding: utf-8 -*-
"""残留归属时间簇核验（只读）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text  # noqa: E402

from backend.app.db.session import get_engine  # noqa: E402


def main() -> int:
    engine = get_engine()
    assert engine.url.database == "wms_ai_test", engine.url.database
    assert engine.url.port == 5433, engine.url.port

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, request_id, started_at "
                "FROM ai_ops.tool_execution_record ORDER BY id"
            )
        ).fetchall()

        print("=== time clusters (started_at -> ids) ===")
        clusters: dict[str, list[int]] = {}
        for row in rows:
            clusters.setdefault(row[2].strftime("%H:%M:%S"), []).append(row[0])
        for key, ids in clusters.items():
            print(f"  {key}  -> ids {ids}  (n={len(ids)})")
        print()

        print("sequence last_value =",
              conn.execute(text(
                  "SELECT last_value FROM ai_ops.tool_execution_record_id_seq"
              )).scalar())
        print("max(id) in table    =", max(r[0] for r in rows))
        print()

        print("=== four observability tables (absolute) ===")
        for table in (
            "ai_ops.llm_usage_record",
            "ai_ops.tool_execution_record",
            "ai_ops.rag_execution_record",
            "ai_ops.assistant_outcome_record",
        ):
            print(f"  {table:44s} = "
                  f"{conn.execute(text('SELECT COUNT(*) FROM ' + table)).scalar_one()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
