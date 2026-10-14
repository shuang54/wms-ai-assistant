"""认证 API DTO（Phase 7 Recovery M1）。

字段纪律（与项目既有 DTO 风格一致）：

    * 响应**绝不**包含 password_hash / 令牌 / Cookie 值 / 内部计数；
    * ``CurrentUserResponse`` 只暴露 ``user_id / username / display_name``；
    * 登录**请求**只有 ``username / password``——客户端**不能**指定
      user_id / role / project（身份只能由服务端会话产生）；
    * 错误 detail 使用固定文案（不泄露用户名是否存在 / 账户是否锁定）。
"""
from __future__ import annotations

from typing import Final

from pydantic import BaseModel, Field

__all__ = [
    "LOGIN_REQUEST_FIELDS",
    "CURRENT_USER_RESPONSE_FIELDS",
    "LOGOUT_RESPONSE_FIELDS",
    "LoginRequest",
    "CurrentUserResponse",
    "LogoutResponse",
]

#: ``POST /api/auth/login`` 请求字段（顺序 ≡ 声明）。
LOGIN_REQUEST_FIELDS: Final[tuple[str, ...]] = ("username", "password")

#: 当前用户响应字段（顺序 ≡ 声明）。
CURRENT_USER_RESPONSE_FIELDS: Final[tuple[str, ...]] = (
    "user_id",
    "username",
    "display_name",
)

#: 登出响应字段（顺序 ≡ 声明）。
LOGOUT_RESPONSE_FIELDS: Final[tuple[str, ...]] = ("ok",)

#: 字段长度上限（与模型列宽对齐；超长由 Pydantic 直接 422）。
USERNAME_MAX_LENGTH: Final[int] = 64
PASSWORD_MAX_LENGTH: Final[int] = 128
DISPLAY_NAME_MAX_LENGTH: Final[int] = 128


class LoginRequest(BaseModel):
    """登录请求（**无** user_id / role / project 字段——身份由服务端签发）。"""

    username: str = Field(
        min_length=1,
        max_length=USERNAME_MAX_LENGTH,
        description="登录名（服务端精确比较；不存在时与密码错误同一响应）",
    )
    password: str = Field(
        min_length=1,
        max_length=PASSWORD_MAX_LENGTH,
        description="密码（仅传输用；不记录 / 不回显）",
    )


class CurrentUserResponse(BaseModel):
    """当前登录用户（来自服务端会话；**不含**角色 / 项目 / 内部状态）。"""

    user_id: str = Field(description="用户 ID（服务端签发；不可枚举）")
    username: str = Field(description="登录名")
    display_name: str = Field(description="展示名（可为空串）")


class LogoutResponse(BaseModel):
    """登出结果（幂等：无会话也返回 ok=true 并清理 Cookie）。"""

    ok: bool = Field(default=True, description="是否已完成登出处理")
