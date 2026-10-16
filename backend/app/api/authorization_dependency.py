"""授权依赖（Phase 7 Recovery M2.4 — 项目级 / 系统级授权判定）。

职责（**只提供 FastAPI 依赖**；不声明任何 HTTP 路由）：

    require_system_ops(request)                系统级运维（全局视图端点）
    require_project_member(request)            项目成员（项目级端点）
    require_project_ops(request)               项目运维（更严的项目级端点）

边界（任务书 §五）：

```text
M1 会话 Cookie
    ↓  auth_dependency.require_session         （401 / 503 fail closed）
Principal（user_id 服务端签发；客户端不可主张）
    ↓  AuthorizationService（ai_ops.authorization_grant）
项目成员 / 项目运维 / 系统运维                       403 / 503
```

铁律（逐条对应任务书 §五 1–8）：

    1. **不接受**客户端传入的 role 作为授权依据（本模块无 role 形参）；
    2. **不接受**客户端声明的 membership（授权关系只从持久化表读取）；
    3. 每个受保护请求都执行授权判定（装饰器级依赖先于端点体）；
    4. 项目级授权只对**指定项目**生效（``project_id`` 来自服务端配置，
       不来自客户端 Query / Header / Body）；
    5. 系统级授权只通过 ``scope_type = 'SYSTEM'`` 的显式行获得；
    6. 授权存储不可用 / 配置损坏 ⇒ **fail closed**（503）；
    7. 更换客户端 ``project_id`` 不能扩大权限（客户端值不参与判定）；
    8. 错误契约与项目既有约定一致，且不泄漏其他用户 / 项目的存在性
       （固定文案；不区分"无权"与"不存在"）。

系统级端点的**过渡期并集**（M2.4 D3 决策）：

    显式授予 = (授权表含 SYSTEM_OPS)  OR  (user_id ∈ 旧白名单)
    旧白名单不可用 ⇒ **503**（与 M2.3 A2 行为逐字一致，**不削弱** fail-closed）

    旧白名单的解析与 503 语义由 ``observability_read_auth`` 持有（单一事实
    来源）；本模块只提供授权表一侧的判定。

本模块**不声明任何 HTTP 路由** —— C25 架构闸门
（``tests/test_tool_observability_architecture_audit.py``）锁定
``backend/app/api`` 下只允许已批准的观测路由。
"""
from __future__ import annotations

import logging
from typing import Final

from fastapi import HTTPException, Request, status

from backend.app.api.auth_dependency import Principal, require_session
from backend.app.config import settings
from backend.app.services.authorization_service import (
    AuthorizationService,
    AuthorizationStoreUnavailableError,
)

logger = logging.getLogger(__name__)

__all__ = [
    "get_authorization_service",
    "get_authorization_project_id",
    "enforce_system_ops_grant",
    "enforce_project_access",
    "require_system_ops",
    "require_project_member",
    "require_project_ops",
    "PROJECT_SCOPE_STATE_KEY",
]

#: 固定错误文案（**不**回传 user_id / project_id / 目标资源信息）。
_NOT_AUTHORIZED_DETAIL: Final[str] = "权限不足：需要项目访问授权"
_NOT_SYSTEM_OPS_DETAIL: Final[str] = "权限不足：需要系统运维授权"
_AUTHORIZATION_UNAVAILABLE_DETAIL: Final[str] = "授权服务不可用"
_PROJECT_NOT_CONFIGURED_DETAIL: Final[str] = "部署项目作用域未配置（服务不可用）"

#: ``request.state`` 上承载服务端项目作用域的键名（与 A2 同一来源语义）。
PROJECT_SCOPE_STATE_KEY: Final[str] = "observability_project_scope"


def get_authorization_service() -> AuthorizationService:
    """组合根：授权服务（测试以 monkeypatch 替换本函数）。

    **单一补丁点**：``observability_read_auth`` 也经本函数取服务，
    因此测试只需替换本函数即可同时覆盖两条依赖路径。
    """
    return AuthorizationService()


def get_authorization_project_id() -> str:
    """组合根：服务端项目作用域（与 M2.2 D1 / M2.3 A2 同一 ``PROJECT_ID``）。"""
    return settings.project.project_id


def _resolve_project_scope(request: Request) -> str:
    """解析服务端项目作用域（缺失 / 空白 ⇒ 503 fail closed）。

    作用域**只**来自服务端配置；通过后写入 ``request.state``，供后续依赖 /
    端点体复用（避免同一请求重复解析）。
    """
    raw = get_authorization_project_id()
    if raw is None or not raw.strip():
        logger.error("authorization project scope is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_PROJECT_NOT_CONFIGURED_DETAIL,
        )
    scope = raw.strip()
    request.state.observability_project_scope = scope
    return scope


def enforce_system_ops_grant(user_id: str) -> bool:
    """**授权表一侧**的系统级判定（决策单一事实来源）。

    本函数是"用户是否持有显式 ``SYSTEM_OPS`` 授权行"的**唯一**实现；
    ``observability_read_auth`` 的过渡期并集也复用它（避免同一授权决策
    在两处各写一遍而产生分歧）。

    Args:
        user_id: 已验证的 ``Principal.user_id``（绝不来自客户端入参）。

    Returns:
        True: 持有 ``scope_type='SYSTEM'`` 的 ``SYSTEM_OPS`` 授权行。
        False: 未持有（**不**构成拒绝——调用方可能还有其它显式来源）。

    Raises:
        HTTPException 503: 授权存储不可用（fail closed）。
    """
    try:
        return get_authorization_service().is_system_ops(user_id)
    except AuthorizationStoreUnavailableError:
        logger.error("authorization store unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_AUTHORIZATION_UNAVAILABLE_DETAIL,
        ) from None


def enforce_project_access(
    user_id: str, project_id: str, *, ops_only: bool
) -> None:
    """项目级授权判定（决策单一事实来源；无授权 ⇒ 403）。

    Args:
        user_id: 已验证的 ``Principal.user_id``。
        project_id: **服务端**项目作用域（绝不来自客户端入参）。
        ops_only: ``True`` ⇒ 要求 ``PROJECT_OPS``；``False`` ⇒ 要求至少为成员
            （``MEMBER`` 或 ``PROJECT_OPS``）。

    Raises:
        HTTPException 403: 无该项目授权（固定文案；不泄漏项目 / 用户存在性）。
        HTTPException 503: 授权存储不可用（fail closed）。
    """
    try:
        service = get_authorization_service()
        allowed = (
            service.is_project_ops(user_id, project_id)
            if ops_only
            else service.is_project_member(user_id, project_id)
        )
    except AuthorizationStoreUnavailableError:
        logger.error("authorization store unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_AUTHORIZATION_UNAVAILABLE_DETAIL,
        ) from None

    if not allowed:
        # 只记录"拒绝"事实——**不**记录 user_id / project_id
        logger.warning("project authorization rejected")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_NOT_AUTHORIZED_DETAIL,
        )


async def _require_system_ops_principal(request: Request) -> Principal:
    """系统级授权：会话 + 授权表 ``SYSTEM_OPS``。

    Note:
        系统级端点的**完整**判定（含旧白名单并集与旧配置 503 语义）由
        ``observability_read_auth.require_observability_ops`` 负责 ——
        那里是本部署系统级授权判定的**单一事实来源**。本依赖提供授权表
        一侧的可复用判定（供未来其它系统级端点使用）。
    """
    principal = require_session(request)
    if not enforce_system_ops_grant(principal.user_id):
        logger.warning("system ops authorization rejected")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_NOT_SYSTEM_OPS_DETAIL,
        )
    return principal


async def require_system_ops(request: Request) -> None:
    """系统级运维依赖（装饰器级；无返回值）。

    Note:
        本依赖**只**依据授权表 ``SYSTEM_OPS``（**不**含旧白名单并集）。
        过渡期系统级端点的权威判定是
        ``observability_read_auth.require_observability_ops``；本依赖供
        **未来**不受旧配置约束的系统级端点使用。
    """
    await _require_system_ops_principal(request)


async def require_project_member(request: Request) -> None:
    """项目成员依赖：会话 + 服务端项目作用域 + 该项目 ``MEMBER``/``PROJECT_OPS``。

    Raises:
        HTTPException 401: 会话缺失 / 无效 / 过期 / 已撤销。
        HTTPException 403: 已认证但**不是**该项目成员。
        HTTPException 503: 会话服务不可用 / 项目作用域未配置 / 授权存储不可用。
    """
    principal = require_session(request)
    project_id = _resolve_project_scope(request)
    enforce_project_access(principal.user_id, project_id, ops_only=False)


async def require_project_ops(request: Request) -> None:
    """项目运维依赖：会话 + 服务端项目作用域 + 该项目 ``PROJECT_OPS``。

    Raises:
        HTTPException 401 / 403 / 503: 同 :func:`require_project_member`，
            但项目级判定要求 ``PROJECT_OPS``（``MEMBER`` 不足）。
    """
    principal = require_session(request)
    project_id = _resolve_project_scope(request)
    enforce_project_access(principal.user_id, project_id, ops_only=True)
