"""用户会话认证依赖（Phase 7 Recovery M1 — HttpOnly Cookie + 服务端会话）。

与 5 个域 X-API-Key 认证（``agent_command_auth`` 等）的**边界**：

    * 本模块 = **Human User Authentication**（浏览器用户；Cookie 会话）；
    * ``*_auth.py``（Agent / Workflow / Runtime Read）= **Service-to-Service
      API Key**（服务端共享凭据 + 服务端项目作用域）；
    * 两者**并存不替代**：服务调用方继续用 ``X-API-Key``；浏览器用户用会话
      Cookie。本模块**不**接受 API Key，``*_auth`` 模块**不**接受会话 Cookie。

fail-closed 语义（与既有认证模块一致）：

    1. 服务端认证不可用（DB 未配置 / 会话服务错误）⇒ **503**（固定文案）；
    2. 缺少 / 无效 / 过期 / 已吊销会话 ⇒ **401**（固定文案；与资源是否存在无关）；
    3. 身份**只**能来自服务端会话——**绝不**接受客户端传入的 user_id /
       X-User-Id / X-Project-Id / Query / Body 身份字段。

CSRF（M1 Security Review 修复——**签名双提交，绑定会话**）：

    朴素 Double-Submit（header == cookie）比较的是两个客户端可控的值，
    可被 Cookie 注入绕过（OWASP）。现改为服务端签名：

        csrf = HMAC-SHA256(key=token_hash, msg=session_id)

    * 登录时由 ``SessionService.login`` 签发（``token_hash`` 仅存服务端 DB）；
    * 不安全方法必须同时满足：Cookie 中有 CSRF 值、请求头有 CSRF 值、
      两者相等、且**等于用当前会话重算的签名**（恒定时间比较）；
    * 会话无效 / 吊销 / 过期 ⇒ 无期望签名 ⇒ **403**（旧 CSRF 值随之失效）；
    * 服务端故障 ⇒ **503**（fail-closed）；
    * 无会话 Cookie 的匿名请求不强制（端点自身认证负责；此类请求无会话
      可滥用）。
"""
from __future__ import annotations

import hmac
import logging
from typing import Annotated, Final

from fastapi import HTTPException, Request, Security, status

from backend.app.config import settings
from backend.app.services.session_service import (
    Principal,
    SessionService,
    SessionServiceError,
    get_session_service,
)

logger = logging.getLogger(__name__)

__all__ = [
    "require_session",
    "require_csrf",
    "get_session_service",
    "CurrentPrincipal",
    "SESSION_COOKIE_NAME",
    "CSRF_COOKIE_NAME",
    "CSRF_HEADER_NAME",
]

#: Cookie / Header 名（来自 ``AuthSettings``；部署可改，代码不散落字符串）。
SESSION_COOKIE_NAME: Final[str] = settings.auth.session_cookie_name
CSRF_COOKIE_NAME: Final[str] = settings.auth.csrf_cookie_name
CSRF_HEADER_NAME: Final[str] = settings.auth.csrf_header_name

#: 固定错误文案（不泄露会话是否存在 / 用户是否存在 / 配置细节）。
_NOT_AUTHENTICATED_DETAIL: Final[str] = "认证失败：缺少或无效的会话"
_AUTH_NOT_CONFIGURED_DETAIL: Final[str] = "认证服务不可用"
_CSRF_FAILED_DETAIL: Final[str] = "CSRF 校验失败"


def _read_session_token(request: Request) -> str | None:
    """从 Cookie 读会话令牌（**不**从 Query / Header / Body 读）。"""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token is None or not token.strip():
        return None
    return token


def require_session(request: Request) -> Principal:
    """FastAPI 依赖：解析并校验会话 ⇒ :class:`Principal`。

    会话服务经组合根 ``get_session_service()`` 获取（测试以 monkeypatch
    替换该函数，**不**在本依赖签名中注入服务类型——避免 FastAPI 把服务
    类型当作请求字段解析）。

    Raises:
        HTTPException: 401（无会话 / 会话无效）/ 503（认证服务不可用）。
    """
    token = _read_session_token(request)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_NOT_AUTHENTICATED_DETAIL,
        )
    try:
        principal = get_session_service().validate(token)
    except SessionServiceError:
        logger.warning(
            "session validation unavailable",
            extra={"error_type": "SessionServiceError"},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_AUTH_NOT_CONFIGURED_DETAIL,
        )
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_NOT_AUTHENTICATED_DETAIL,
        )
    return principal


def require_csrf(request: Request) -> None:
    """FastAPI 依赖：不安全方法的 CSRF **会话绑定签名**校验（fail-closed）。

    通过条件（全部满足，恒定时间比较）：

        1. 会话 Cookie 存在且对应**有效**会话（否则无期望签名 ⇒ 拒绝）；
        2. CSRF Cookie 存在（前端回显通道）；
        3. ``X-CSRF-Token`` 请求头存在；
        4. Cookie 值 == 请求头值（双提交）；
        5. 请求头值 == 服务端用**当前会话**的 ``token_hash`` 对
           ``session_id`` 重算的 HMAC（会话绑定 ⇒ 伪造 / 换会话 / 旧会话
           全部失效）。

    Raises:
        HTTPException: 403（CSRF 校验失败）/ 503（会话服务不可用）。
    """
    token = _read_session_token(request)
    if token is None:
        # 无会话 Cookie：不存在可被滥用的已认证会话（端点自身认证负责）
        return
    try:
        expected = get_session_service().expected_csrf_token(token)
    except SessionServiceError:
        logger.warning(
            "csrf validation unavailable",
            extra={"error_type": "SessionServiceError"},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_AUTH_NOT_CONFIGURED_DETAIL,
        )
    cookie_value = request.cookies.get(CSRF_COOKIE_NAME)
    header_value = request.headers.get(CSRF_HEADER_NAME)
    if (
        expected is None
        or cookie_value is None
        or header_value is None
        or not hmac.compare_digest(cookie_value, header_value)
        or not hmac.compare_digest(header_value, expected)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_CSRF_FAILED_DETAIL,
        )


#: 路由签名用的依赖别名（``principal: CurrentPrincipal``）。
CurrentPrincipal = Annotated[
    Principal,
    Security(require_session),
]
