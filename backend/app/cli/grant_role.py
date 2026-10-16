"""授权授予 CLI（Phase 7 Recovery M2.4 — 受控管理员入口）。

**没有公开授权接口**：授权关系只能由管理员在本机（或受控初始化流程）经本
CLI 显式写入 ``ai_ops.authorization_grant``。M2.4 **不**为任何既有用户自动
授予角色（包括旧白名单用户）—— 未知映射必须保留为"待配置项"。

用法：

```bash
# 项目成员（MEMBER）
python -m backend.app.cli.grant_role --user-id <uuid> \
    --project vietnam-wms --role MEMBER

# 项目运维（PROJECT_OPS）
python -m backend.app.cli.grant_role --user-id <uuid> \
    --project vietnam-wms --role PROJECT_OPS

# 系统级运维（SYSTEM_OPS；不得携带 --project）
python -m backend.app.cli.grant_role --user-id <uuid> --role SYSTEM_OPS
```

退出码：

```text
0  成功（新增或**已存在**——幂等；打印授权摘要，**不**打印其它用户数据）
1  失败（参数错误 / 角色与作用域不匹配 / DB 未配置或不可用）
```

安全纪律：

    * 只接受 ``--user-id``（``ai_ops.app_user.user_id``，管理员从
      ``create_user`` 输出获得）；**不**按用户名模糊匹配、不批量授予；
    * ``--project`` 与 ``--role`` 必须匹配：``PROJECT`` 角色要求 ``--project``，
      ``SYSTEM_OPS`` **禁止**携带 ``--project``（防止 NULL 歧义）；
    * 幂等：重复授予同一组合不报错、**不**覆盖、不新增第二行；
    * 输出只有 user_id / scope / project（若有）/ role / 是否新增。
"""
from __future__ import annotations

import argparse
import logging
import sys

from backend.app.db.authorization_grant_repository import (
    AuthorizationGrantRepository,
    AuthorizationGrantRepositoryError,
    AuthorizationGrantStoreNotConfiguredError,
)
from backend.app.db.models.authorization_grant import (
    PROJECT_ROLES,
    SYSTEM_ROLE_SYSTEM_OPS,
    SYSTEM_ROLES,
)

__all__ = ["main"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.app.cli.grant_role",
        description=(
            "授予项目级 / 系统级授权（受控管理员入口；无公开授权接口）"
        ),
    )
    parser.add_argument(
        "--user-id",
        required=True,
        help="被授权用户 ID（ai_ops.app_user.user_id；由 create_user 输出）",
    )
    parser.add_argument(
        "--role",
        required=True,
        choices=sorted(PROJECT_ROLES | SYSTEM_ROLES),
        help="角色：MEMBER / PROJECT_OPS（需 --project）或 SYSTEM_OPS（禁止 --project）",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="项目 ID（项目级角色必填；SYSTEM_OPS 必须省略）",
    )
    return parser


def _validate_combination(role: str, project: str | None) -> None:
    """scope ↔ role ↔ project 一致性前置校验（数据库 CHECK 是最终防线）。

    Raises:
        SystemExit: 组合非法（打印固定文案，**不**回显内部细节）。
    """
    if role in PROJECT_ROLES:
        if project is None or not project.strip():
            raise SystemExit(
                f"[grant_role] FAIL: 角色 {role} 需要 --project（项目 ID）"
            )
        return

    if role in SYSTEM_ROLES:
        if project is not None:
            raise SystemExit(
                f"[grant_role] FAIL: 角色 {role} 不得携带 --project"
                "（系统级作用域的 project_id 必须为空）"
            )
        return

    raise SystemExit(f"[grant_role] FAIL: 未知角色 {role}")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    args = _build_parser().parse_args(argv)

    try:
        _validate_combination(args.role, args.project)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1

    repository = AuthorizationGrantRepository()

    try:
        if args.role in PROJECT_ROLES:
            created = repository.grant_project_role(
                user_id=args.user_id,
                project_id=args.project,
                role=args.role,
            )
            scope_line = f"  scope   = PROJECT (project_id={args.project.strip()})"
        else:
            created = repository.grant_system_role(
                user_id=args.user_id,
                role=SYSTEM_ROLE_SYSTEM_OPS,
            )
            scope_line = "  scope   = SYSTEM (project_id=NULL)"
    except ValueError as exc:
        # 参数校验失败（不含敏感内容）
        print(f"[grant_role] FAIL: {exc}", file=sys.stderr)
        return 1
    except AuthorizationGrantStoreNotConfiguredError:
        print("[grant_role] FAIL: DATABASE_URL 未配置，授权存储不可用", file=sys.stderr)
        return 1
    except AuthorizationGrantRepositoryError as exc:
        print(
            f"[grant_role] FAIL: 授权写入失败（{type(exc).__name__}）",
            file=sys.stderr,
        )
        return 1

    print("[grant_role] OK" + ("" if created else "（已存在，未变更）"))
    print(f"  user_id = {args.user_id.strip()}")
    print(scope_line)
    print(f"  role    = {args.role}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
