"""用户认证 HTTP API（Phase 7 Recovery M1 — 登录 / 登出 / 当前用户）。

端点（最小认证接口；**不**提供注册——用户由受控管理员 / CLI 创建）：

    POST /api/auth/login    登录（成功 ⇒ Set-Cookie：HttpOnly 会话 Cookie
                             + 非 HttpOnly CSRF Cookie）
    POST /api/auth/logout   登出（吊销会话 + 清理 Cookie；幂等）
    GET  /api/auth/me       当前登录用户（会话无效 ⇒ 401）

安全边界：

    * 登录失败**统一** 401 固定文案（用户名不存在 / 密码错误 / 停用 / 锁定
      不可区分；登录尝试限制由 ``UserService`` 持久化实现）；
    * 密码 / 令牌 / Cookie 值 / password_hash **不**进入日志、异常、响应体；
    * 身份只来自服务端会话；客户端**不能**指定 user_id / role / project；
    * 会话 Cookie：HttpOnly + Secure（``AUTH_COOKIE_SECURE``，默认 True）
      + SameSite=Lax（默认）+ Path=/；
    * 登出（不安全方法）要求 CSRF 双提交头（见 ``auth_dependency.require_csrf``）；
    * DB / 会话服务不可用 ⇒ 503 fail-closed（**绝不**匿名放行）。

错误契约：

    200  登录成功 / 登出完成 / 当前用户
    401  认证失败（固定文案）/ 会话缺失或无效
    403  CSRF 校验失败（登出）
    422  请求体校验失败（Pydantic）
    503  认证服务不可用（DB 未配置 / 会话服务错误）
"""
from __future__ import annotations

import logging
from typing import Annotated, Final

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from backend.app.api.auth_dependency import (
    CurrentPrincipal,
    require_csrf,
)
from backend.app.config import settings
from backend.app.dto.auth_api import (
    CURRENT_USER_RESPONSE_FIELDS,
    LOGIN_REQUEST_FIELDS,
    LOGOUT_RESPONSE_FIELDS,
    CurrentUserResponse,
    LoginRequest,
    LogoutResponse,
)
from backend.app.services.session_service import (
    LoginResult,
    SessionService,
    SessionServiceError,
    get_session_service,
)
from backend.app.services.user_service import (
    InvalidCredentialsError,
    UserServiceError,
)

logger = logging.getLogger(__name__)

router = APIRouter()

__all__ = [
    "router",
    "get_session_service",
    "LOGIN_REQUEST_FIELDS",
    "CURRENT_USER_RESPONSE_FIELDS",
    "LOGOUT_RESPONSE_FIELDS",
]

#: Cookie / Header 名（单一来源 = AuthSettings）。
_SESSION_COOKIE: Final[str] = settings.auth.session_cookie_name
_CSRF_COOKIE: Final[str] = settings.auth.csrf_cookie_name
_CSRF_HEADER: Final[str] = settings.auth.csrf_header_name

#: 会话 TTL（秒；与 UserSession.expires_at 同一口径）。
_SESSION_MAX_AGE: Final[int] = settings.auth.session_ttl_hours * 3600

#: 固定错误文案（不泄露用户名是否存在 / 账户是否锁定）。
_LOGIN_FAILED_DETAIL: Final[str] = "认证失败：用户名或密码错误"
_AUTH_UNAVAILABLE_DETAIL: Final[str] = "认证服务不可用"


def _set_session_cookies(response: Response, login: LoginResult) -> None:
    """下发会话 Cookie（HttpOnly）+ CSRF Cookie（非 HttpOnly；**会话绑定签名**）。

    CSRF 值 = ``SessionService.login`` 签发的
    ``HMAC(token_hash, session_id)``——密钥 ``token_hash`` 仅存服务端 DB，
    浏览器拿不到 ⇒ 无法伪造；校验逻辑见 ``auth_dependency.require_csrf``。
    """
    common = {
        "path": "/",
        "secure": settings.auth.cookie_secure,
        "samesite": settings.auth.cookie_samesite,
        "max_age": _SESSION_MAX_AGE,
    }
    response.set_cookie(_SESSION_COOKIE, login.token, httponly=True, **common)
    response.set_cookie(
        _CSRF_COOKIE,
        login.csrf_token,
        httponly=False,  # 双提交模式要求 JS 可读（值本身不可伪造）
        **common,
    )


def _clear_auth_cookies(response: Response) -> None:
    """清理认证 Cookie（max_age=0；Path / 属性与下发时一致）。"""
    response.delete_cookie(
        _SESSION_COOKIE,
        path="/",
        secure=settings.auth.cookie_secure,
        samesite=settings.auth.cookie_samesite,
    )
    response.delete_cookie(
        _CSRF_COOKIE,
        path="/",
        secure=settings.auth.cookie_secure,
        samesite=settings.auth.cookie_samesite,
    )


async def _run_login(service: SessionService, payload: LoginRequest) -> LoginResult:
    """同步认证 + 会话签发（经线程池：KDF / DB 往返不阻塞事件循环）。"""
    return await anyio.to_thread.run_sync(
        lambda: service.login(
            username=payload.username, password=payload.password
        )
    )


@router.post(
    "/auth/login",
    response_model=CurrentUserResponse,
    responses={
        200: {"description": "登录成功（Set-Cookie：会话 + CSRF）"},
        401: {"description": "认证失败（固定文案；不泄露用户名是否存在）"},
        422: {"description": "请求体校验失败"},
        503: {"description": "认证服务不可用（DB 未配置 / 会话服务错误）"},
    },
)
async def login(
    payload: LoginRequest,
    response: Response,
) -> CurrentUserResponse:
    """登录（成功 ⇒ 服务端会话 + HttpOnly Cookie；失败 ⇒ 统一 401）。"""
    try:
        service = get_session_service()
        login_result = await _run_login(service, payload)
    except InvalidCredentialsError:
        # 统一失败：不区分用户名不存在 / 密码错误 / 停用 / 锁定
        logger.info("login rejected", extra={"error_type": "InvalidCredentialsError"})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_LOGIN_FAILED_DETAIL,
        )
    except (UserServiceError, SessionServiceError) as exc:
        logger.warning("login failed (service)", extra={"error_type": type(exc).__name__})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_AUTH_UNAVAILABLE_DETAIL,
        )
    _set_session_cookies(response, login_result)
    return CurrentUserResponse(
        user_id=login_result.principal.user_id,
        username=login_result.principal.username,
        display_name=login_result.principal.display_name,
    )


@router.post(
    "/auth/logout",
    response_model=LogoutResponse,
    responses={
        200: {"description": "登出完成（幂等；Cookie 已清理）"},
        403: {"description": "CSRF 校验失败"},
    },
)
async def logout(
    request: Request,
    response: Response,
    _csrf: Annotated[None, Depends(require_csrf)],
) -> LogoutResponse:
    """登出（吊销当前会话 + 清理 Cookie；无会话也返回 200——幂等）。"""
    token = request.cookies.get(_SESSION_COOKIE)
    if token:
        service = get_session_service()
        try:
            await anyio.to_thread.run_sync(lambda: service.logout(token))
        except SessionServiceError:  # 吊销失败不阻塞登出（Cookie 已清理）
            logger.warning(
                "logout revoke failed",
                extra={"error_type": "SessionServiceError"},
            )
    _clear_auth_cookies(response)
    return LogoutResponse(ok=True)


@router.get(
    "/auth/me",
    response_model=CurrentUserResponse,
    responses={
        200: {"description": "当前登录用户"},
        401: {"description": "会话缺失或无效"},
        503: {"description": "认证服务不可用"},
    },
)
async def me(principal: CurrentPrincipal) -> CurrentUserResponse:
    """当前登录用户（身份来自服务端会话；客户端不能指定）。"""
    return CurrentUserResponse(
        user_id=principal.user_id,
        username=principal.username,
        display_name=principal.display_name,
    )
