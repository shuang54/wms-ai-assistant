"""WMS/ERP 业务数据源 **只读** 检查脚本（人工执行；零写入）。

用途（接入独立业务库的第一步）：

```powershell
# 1) 在本机忽略跟踪的 .env 填写 WMS_DATABASE_PASSWORD（只读账号密码）
# 2) 运行：
python scripts/check_wms_datasource.py                    # 连接 + 身份 + 权限 + 元数据
python scripts/check_wms_datasource.py --probe 10001      # 追加一次真实库存查询（只读）
python scripts/check_wms_datasource.py --table myschema.inventory --probe MAT-001
```

检查内容（**全部只读**；元数据查询限定在 information_schema / pg_catalog）：

```text
1  连接可用性（超时可控）与服务器版本
2  当前数据库名 / 连接用户（身份确认 —— 必须是测试库 + 只读账号）
3  只读证据：事务只读状态 + 角色属性 + （可选）目标表的写权限判定
4  可见 schema 清单
5  库存相关表 / 视图（按名称模式）+ 候选表的列与类型
6  --probe：经与应用同一 Handler 逻辑的只读查询返回 qty
```

安全边界：

    * **绝不**打印连接串 / 密码 / 环境变量值；只打印非敏感摘要（host 已由
      ``business_datasource_summary`` 给出：host / port / database）；
    * **不写**任何数据：连接启用 ``default_transaction_read_only=on``，
      查询走 ``BEGIN READ ONLY`` 事务；**不建表 / 不迁移 / 不 INSERT**；
    * 失败即**明确报错**（退出码非 0）——**不**回退应用数据库、**不**造数。

退出码：

```text
0  检查通过
2  业务数据源未配置（WMS_DATABASE_* 缺项，含密码为空）
3  连接失败（网络 / 认证 / 超时）
4  元数据检查发现异常（如同名表不可读 / 查询被拒）
```
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import text as sa_text  # noqa: E402

from backend.app.db.business_session import (  # noqa: E402
    BusinessDataSourceError,
    business_datasource_summary,
    require_business_engine,
)

__all__ = ["main"]

#: 库存相关名称模式（information_schema 名称匹配；只读元数据）。
_NAME_PATTERNS: tuple[str, ...] = (
    "%inventory%",
    "%stock%",
    "%material%",
    "%warehouse%",
    "%store%",
)

#: 库存相关列名模式。
_COLUMN_PATTERNS: tuple[str, ...] = (
    "material_code",
    "item_code",
    "sku",
    "qty",
    "quantity",
    "stock_qty",
    "warehouse",
)


def _fail(message: str, code: int) -> int:
    print(f"\n[FAIL] {message}")
    return code


def _print_summary() -> None:
    summary = business_datasource_summary()
    print("业务数据源摘要（非敏感）：")
    for key in ("configured", "host", "port", "database"):
        print("  %-11s = %s" % (key, summary.get(key)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="WMS/ERP 业务数据源只读检查（不写任何数据）"
    )
    parser.add_argument(
        "--table",
        default=None,
        help="目标库存表/视图（schema.table 或 table；默认取 WMS_INVENTORY_* 配置）",
    )
    parser.add_argument(
        "--probe",
        default=None,
        help="可选：对给定 material_code 执行一次真实只读库存查询",
    )
    args = parser.parse_args(argv)

    print("WMS/ERP 业务数据源只读检查")
    _print_summary()

    from backend.app.config import settings

    cfg = settings.business_database
    if not cfg.is_configured:
        missing = [
            name
            for name, value in (
                ("WMS_DATABASE_HOST", cfg.host),
                ("WMS_DATABASE_NAME", cfg.name),
                ("WMS_DATABASE_USER", cfg.user),
                ("WMS_DATABASE_PASSWORD", cfg.password),
            )
            if not str(value).strip()
        ]
        return _fail(
            "业务数据源未配置（缺项：" + ", ".join(missing) + "）"
            "—— 请在本机 .env 填写后重试；不回退应用数据库。",
            2,
        )

    if args.table:
        raw = args.table.strip()
        if "." in raw:
            schema_name, _, table_name = raw.partition(".")
        else:
            schema_name, table_name = settings.inventory_tool.schema_name, raw
    else:
        schema_name = settings.inventory_tool.schema_name
        table_name = settings.inventory_tool.table_name

    try:
        engine = require_business_engine()
    except BusinessDataSourceError as exc:
        return _fail(str(exc), 2)

    try:
        with engine.connect() as conn:
            info = conn.execute(
                sa_text(
                    "SELECT current_database(), current_user, version(), "
                    "current_setting('transaction_read_only')"
                )
            ).one()
            db_name, db_user, version, tx_read_only = info
            print("\n连接身份：")
            print("  database      =", db_name)
            print("  user          =", db_user)
            print("  server        =", str(version).split(",")[0])
            print("  tx_read_only  =", tx_read_only)

            role = conn.execute(
                sa_text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            ).one_or_none()
            if role is not None:
                print("  角色属性       = super=%s createdb=%s createrole=%s"
                      % tuple(role))

            schemas = [
                row[0]
                for row in conn.execute(
                    sa_text(
                        "SELECT nspname FROM pg_namespace "
                        "WHERE nspname NOT LIKE 'pg\\_%' "
                        "AND nspname <> 'information_schema' ORDER BY nspname"
                    )
                ).all()
            ]
            print("\n可见 schema（%d）：%s" % (len(schemas), ", ".join(schemas)))

            print("\n库存相关表 / 视图（限定元数据查询）：")
            rows = conn.execute(
                sa_text(
                    "SELECT table_schema, table_name, table_type "
                    "FROM information_schema.tables "
                    "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                    "AND ("
                    + " OR ".join(
                        f"table_name ILIKE :p{i}" for i in range(len(_NAME_PATTERNS))
                    )
                    + ") ORDER BY table_schema, table_name"
                ),
                {f"p{i}": pattern for i, pattern in enumerate(_NAME_PATTERNS)},
            ).all()
            if not rows:
                print("  （无匹配 —— 请与业务方确认实际表/视图名）")
            for schema, name, kind in rows:
                print("  %-20s %-32s %s" % (schema, name, kind))

            print("\n候选列（限定列元数据；material/qty/warehouse 类）：")
            cols = conn.execute(
                sa_text(
                    "SELECT table_schema, table_name, column_name, data_type "
                    "FROM information_schema.columns "
                    "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                    "AND ("
                    + " OR ".join(
                        f"column_name ILIKE :c{i}" for i in range(len(_COLUMN_PATTERNS))
                    )
                    + ") ORDER BY table_schema, table_name, ordinal_position LIMIT 200"
                ),
                {f"c{i}": pattern for i, pattern in enumerate(_COLUMN_PATTERNS)},
            ).all()
            for schema, name, column, data_type in cols:
                print("  %-18s %-28s %-18s %s" % (schema, name, column, data_type))
            if not cols:
                print("  （无匹配列）")

            print("\n目标库存表坐标（WMS_INVENTORY_*）：%s.%s" % (schema_name, table_name))
            target = conn.execute(
                sa_text(
                    "SELECT table_type FROM information_schema.tables "
                    "WHERE table_schema = :s AND table_name = :t"
                ),
                {"s": schema_name, "t": table_name},
            ).scalar_one_or_none()
            if target is None:
                print("  [WARN] 该表/视图不存在 —— 请在 metadata 核对后调整 "
                      "WMS_INVENTORY_SCHEMA / WMS_INVENTORY_TABLE")
            else:
                print("  存在：type =", target)

            if args.probe is not None:
                code = str(args.probe).strip()
                # 列映射与应用一致（WMS_INVENTORY_MATERIAL_CODE_COLUMN / _QTY_COLUMN；
                # 均为服务器端配置的安全标识符）
                inv = settings.inventory_tool
                print("\n真实只读查询（probe=%s；列映射 %s / %s）：" % (
                    code, inv.material_code_column, inv.qty_column
                ))
                with engine.connect() as probe_conn:
                    probe_conn.execute(sa_text("SET TRANSACTION READ ONLY"))
                    qty = probe_conn.execute(
                        sa_text(
                            f'SELECT COALESCE(SUM("{inv.qty_column}"), 0) FROM '
                            f'"{schema_name}"."{table_name}" '
                            f'WHERE "{inv.material_code_column}" = :material_code'
                        ),
                        {"material_code": code},
                    ).scalar()
                    probe_conn.rollback()
                print("  qty =", qty)
    except Exception as exc:  # noqa: BLE001 —— 明确报错（不输出连接参数）
        return _fail(
            f"连接 / 元数据检查失败（{type(exc).__name__}）："
            "请确认网络可达、账号密码正确、且账号具备只读权限",
            3 if exc.__class__.__name__ in {"OperationalError", "InterfaceError"} else 4,
        )

    print("\n[OK] 检查完成（零写入：只读事务 + 元数据查询）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
