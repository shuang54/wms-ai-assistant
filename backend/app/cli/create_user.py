"""用户创建 CLI（Phase 7 Recovery M1 — 受控管理员入口）。

**没有公开注册接口**：第一版用户只能由管理员在本机（或受控初始化流程）
经本 CLI 创建。

用法：

```bash
# 密码经环境变量传入（**不**走命令行参数——避免进 shell 历史 / 进程列表）
$env:WMS_NEW_USER_PASSWORD="<强密码>"
python -m backend.app.cli.create_user --username alice --display-name "Alice"

# 也可交互输入（getpass；不回显）
python -m backend.app.cli.create_user --username alice
```

退出码：

```text
0  成功（打印 user_id；**不**回显密码 / 哈希）
1  失败（参数错误 / 用户名已存在 / 密码策略不合法 / DB 不可用）
```

安全纪律：

    * 密码只从环境变量或 getpass 读取；**不**进 argv / 日志 / 异常；
    * 输出只有 user_id / username / display_name / status；
    * **不**创建角色 / 项目绑定 / 租户（属后续阶段）。
"""
from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys

from backend.app.services.user_service import (
    UserAlreadyExistsError,
    UserService,
    UserServiceError,
    get_user_service,
)

#: 密码环境变量名（值由管理员在本机设置；**不**写入仓库 / 日志）。
_PASSWORD_ENV_VAR = "WMS_NEW_USER_PASSWORD"

__all__ = ["main"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.app.cli.create_user",
        description="创建用户（受控管理员入口；无公开注册接口）",
    )
    parser.add_argument("--username", required=True, help="登录名（唯一）")
    parser.add_argument(
        "--display-name",
        default="",
        help="展示名（可选；默认空串）",
    )
    parser.add_argument(
        "--password-env",
        default=_PASSWORD_ENV_VAR,
        help=(
            f"密码所在环境变量名（默认 {_PASSWORD_ENV_VAR}；"
            "未设置则交互输入——密码不进 argv / 日志）"
        ),
    )
    return parser


def _read_password(env_var: str) -> str:
    """读取密码（环境变量优先；否则 getpass 交互输入）。"""
    from_env = os.environ.get(env_var, "")
    if from_env:
        return from_env
    first = getpass.getpass("密码: ")
    second = getpass.getpass("确认密码: ")
    if first != second:
        raise SystemExit("[create_user] FAIL: 两次输入的密码不一致")
    return first


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    args = _build_parser().parse_args(argv)

    try:
        password = _read_password(args.password_env)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1

    service: UserService = get_user_service()
    try:
        view = service.create_user(
            username=args.username,
            password=password,
            display_name=args.display_name,
        )
    except UserAlreadyExistsError:
        print("[create_user] FAIL: 用户名已存在", file=sys.stderr)
        return 1
    except ValueError as exc:
        # 用户名 / 密码策略错误（不含密码内容）
        print(f"[create_user] FAIL: {exc}", file=sys.stderr)
        return 1
    except UserServiceError as exc:
        print(
            f"[create_user] FAIL: 用户创建失败（{type(exc).__name__}）",
            file=sys.stderr,
        )
        return 1

    print("[create_user] OK")
    print(f"  user_id      = {view.user_id}")
    print(f"  username     = {view.username}")
    print(f"  display_name = {view.display_name}")
    print(f"  status       = {view.status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
