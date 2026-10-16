"""Usage / Observability Read API 认证与授权（Phase 7 Recovery M2.3 A2）。

背景：M2.3 A2 之前，7 个只读使用量／可观测性端点（``/api/usage/analytics``
与 6 个 ``/api/observability/*``）**没有任何认证依赖**——匿名调用方即可读取
全局 LLM 用量、Tool 执行历史与 Assistant Trace。A1 已收口 4 个 AI **执行**
入口；A2 收口这 7 个只读入口。

边界（**不新增认证机制**；与 ``agent_read_auth`` / ``workflow_read_auth``
同风格，但身份模型不同）：

```text
M1 会话 Cookie（wms_ai_session → ai_ops.user_session）
    ↓  auth_dependency.require_session          （401 / 503 fail closed）
Principal（user_id / username / display_name）
    ↓  运维白名单（OBSERVABILITY_READ_OPS_USER_IDS）精确匹配 user_id
运维只读（全局视图）                                    403 / 503

M1 会话 Cookie
    ↓  auth_dependency.require_session
部署项目作用域（PROJECT_ID —— 服务端配置，**唯一**来源）
    ↓  项目成员授权（ai_ops.authorization_grant：MEMBER / PROJECT_OPS）
项目只读（history / persistent metrics）               403 / 503
```

M2.4（最小 RBAC）后的**系统级**判定为**过渡期并集**：

```text
显式授予 = (授权表 ai_ops.authorization_grant 含 SYSTEM_OPS)
           OR (user_id ∈ 旧白名单 OBSERVABILITY_READ_OPS_USER_IDS)
旧白名单不可用 ⇒ 503（A2 语义逐字保留）
授权存储不可用 ⇒ 503（M2.4 新增 fail-closed）
两者皆无       ⇒ 403
```

`PROJECT_OPS`（项目级）**绝不**获得系统级访问；系统级行没有 `project_id`，
项目级判定看不到它 —— 两个方向都不存在隐式推导。

与 X-API-Key 域的**关键差别**（如实记录，不混淆）：

    * 本模块 = **Human User Authentication**（M1 会话）之上的**部署级运维授权**；
    * ``*_read_auth`` / ``*_command_auth`` = **Service-to-Service API Key**；
    * 两者**并存不替代**：A2 不为只读端点新增 API-Key 旁路（M2.3 A2 决策）。

fail-closed 语义（按判定顺序）：

    1. 会话缺失 / 无效 / 过期 / 已撤销        ⇒ **401**（auth_dependency）
    2. 会话服务不可用                          ⇒ **503**（auth_dependency）
    3. 运维白名单缺失 / 空白 / 无法解析        ⇒ **503**
    4. 已认证但 user_id 不在白名单             ⇒ **403**
    5. 部署项目作用域缺失 / 空白               ⇒ **503**
    6. 作用域未被依赖注入（极端路径）          ⇒ **503**

安全约束：

    * 身份**只**来自服务端会话（``Principal.user_id`` 服务端签发）；**绝不**
      接受客户端传入的角色 / 管理员标志 / ``is_admin`` / 用户 ID 声明；
    * **有效 Session ≠ 运维权限**（认证与授权分离）；
    * 配置值（白名单内容 / 项目 ID）**绝不**进入日志 / 异常 / 响应体——
      日志只记录"未配置"或"已拒绝"事实；
    * 认证与授权发生在任何数据读取**之前**（装饰器级依赖先于端点体）。

本模块**不声明任何 HTTP 路由**（只提供 FastAPI 依赖）——C25 架构闸门
（``tests/test_tool_observability_architecture_audit.py``）锁定
``backend/app/api`` 下只允许已批准的观测路由。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import Depends, HTTPException, Request, status

from backend.app.api import authorization_dependency as authz
from backend.app.api.auth_dependency import Principal, require_session
from backend.app.config import ObservabilityReadSettings, settings

logger = logging.getLogger(__name__)

__all__ = [
    "ObservabilityReadScope",
    "OBSERVABILITY_PROJECT_SCOPE_STATE_KEY",
    "get_observability_read_settings",
    "get_observability_project_id",
    "parse_ops_user_ids",
    "require_observability_ops",
    "require_observability_project_scope",
    "get_observability_project_scope",
    "OPS_USER_ID_MAX_LENGTH",
    "OPS_USER_IDS_MAX_ENTRIES",
]

#: 固定错误文案（**不**回传白名单内容 / 项目 ID / 目标资源信息）。
_NOT_AUTHENTICATED_DETAIL: Final[str] = "认证失败：缺少或无效的会话"
_OPS_NOT_CONFIGURED_DETAIL: Final[str] = "运维读取授权未配置（服务不可用）"
_PROJECT_NOT_CONFIGURED_DETAIL: Final[str] = "部署项目作用域未配置（服务不可用）"
_FORBIDDEN_DETAIL: Final[str] = "权限不足：需要运维读取授权"
# M2.4：授权存储不可用 / 项目成员不足的**固定文案**统一由
# authorization_dependency 持有（授权决策的单一事实来源），本模块不再重复定义。

#: 白名单条目长度上限（与 ``ai_ops.app_user.user_id`` 列宽对齐）。
OPS_USER_ID_MAX_LENGTH: Final[int] = 64

#: 白名单条目数上限（防御性上限，防止误配导致无界集合）。
OPS_USER_IDS_MAX_ENTRIES: Final[int] = 100

#: ``request.state`` 上承载服务端项目作用域的键名。
OBSERVABILITY_PROJECT_SCOPE_STATE_KEY: Final[str] = "observability_project_scope"


@dataclass(frozen=True)
class ObservabilityReadScope:
    """服务端授权的只读作用域（project_id 来自部署配置；客户端不可主张）。"""

    project_id: str


def get_observability_read_settings() -> ObservabilityReadSettings:
    """Composition Root：运维授权配置（测试以 monkeypatch 替换本函数）。"""
    return settings.observability_read


def get_observability_project_id() -> str:
    """Composition Root：部署项目作用域（测试以 monkeypatch 替换本函数）。

    与 Conversation 写入侧（M2.2 D1）**同一个** ``PROJECT_ID`` 语义：
    本部署唯一授权项目；"已注册于 Project Registry" 不等于"已授权"。
    """
    return settings.project.project_id


def parse_ops_user_ids(raw: str) -> frozenset[str]:
    """解析逗号分隔白名单 ⇒ :class:`frozenset`\\ [str]。

    Args:
        raw: 环境变量原始值（逗号分隔的 user_id 列表）。

    Returns:
        解析后的 user_id 集合（去重；顺序无关）。

    Raises:
        ValueError: 未配置（空 / 纯空白）、含空条目（如 ``"a,,b"`` / 尾逗号）、
            条目超长或条目数超限——一律视为"无法解析"（调用方 fail closed）。
    """
    if raw is None or not raw.strip():
        raise ValueError("运维读取白名单未配置")

    entries = [entry.strip() for entry in raw.split(",")]
    if any(not entry for entry in entries):
        raise ValueError("运维读取白名单含空条目")
    if len(entries) > OPS_USER_IDS_MAX_ENTRIES:
        raise ValueError("运维读取白名单条目数超限")
    for entry in entries:
        if len(entry) > OPS_USER_ID_MAX_LENGTH:
            raise ValueError("运维读取白名单条目超长")
    return frozenset(entries)


def _require_authenticated(request: Request) -> Principal:
    """复用生产 ``require_session``（401 / 503 fail closed）。"""
    return require_session(request)


def _resolve_ops_user_ids() -> frozenset[str]:
    """解析白名单；任何失败都映射为 503（**不**回退为"放行"）。"""
    try:
        return parse_ops_user_ids(
            get_observability_read_settings().ops_user_ids
        )
    except ValueError:
        # 只记录"未配置 / 无法解析"事实——**不**记录任何配置值
        logger.error("observability ops allowlist is not usable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_OPS_NOT_CONFIGURED_DETAIL,
        ) from None


def _has_system_ops_grant(user_id: str) -> bool:
    """授权表是否显式授予 ``SYSTEM_OPS``（存储不可用 ⇒ 503 fail closed）。

    判定**委托**给 :func:`authorization_dependency.enforce_system_ops_grant`
    —— 系统级授权决策只保留**一份实现**，避免两处逻辑分歧。
    """
    return authz.enforce_system_ops_grant(user_id)


async def require_observability_ops(request: Request) -> None:
    """运维只读端点统一的认证 + **系统级**授权依赖（装饰器级；无返回值）。

    判定顺序（M2.4 D3 —— 过渡期并集，任一步失败即抛出，**无部分结果、无降级**）：

        1. 会话缺失 / 无效 / 过期 / 已撤销   ⇒ **401**
        2. 会话服务不可用                     ⇒ **503**
        3. 旧白名单缺失 / 空白 / 无法解析     ⇒ **503**（A2 语义逐字保留）
        4. 授权存储不可用                     ⇒ **503**（M2.4 新增 fail-closed）
        5. 显式授予 = (授权表含 ``SYSTEM_OPS``) **OR** (``user_id`` ∈ 旧白名单)
        6. 两者皆无                           ⇒ **403**

    铁律：``PROJECT_OPS``（项目级）**绝不**获得系统级访问；旧白名单是**显式**
    部署级授予，不是隐式回退；``membership`` 缺失不会回退为放行。

    Raises:
        HTTPException 401: 会话缺失 / 无效 / 过期 / 已撤销。
        HTTPException 403: 已认证但既无 ``SYSTEM_OPS`` 授权也不在旧白名单。
        HTTPException 503: 会话服务不可用 / 旧白名单不可用 / 授权存储不可用。
    """
    principal = _require_authenticated(request)

    # 3) 旧白名单可用性（A2 变更前的判定顺序保持不变：不可用 ⇒ 503）
    ops_user_ids = _resolve_ops_user_ids()

    # 4) 授权表可用性 + SYSTEM_OPS 判定（存储不可用 ⇒ 503）
    granted_by_table = _has_system_ops_grant(principal.user_id)

    # 5) 过渡期并集：两个来源都是**显式**授予
    if granted_by_table or principal.user_id in ops_user_ids:
        return

    # 6) 拒绝（只记录"拒绝"事实——**不**记录 user_id / 白名单内容）
    logger.warning("observability ops read rejected")
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=_FORBIDDEN_DETAIL,
    )


async def require_observability_project_scope(request: Request) -> None:
    """项目只读端点（history / persistent metrics）认证 + 作用域 + **成员**依赖。

    判定顺序（M2.4 D2 —— 项目成员授权接入）：

        1. 会话缺失 / 无效 / 过期 / 已撤销   ⇒ **401**
        2. 会话服务不可用                     ⇒ **503**
        3. 部署项目作用域缺失 / 空白          ⇒ **503**
        4. 授权存储不可用                     ⇒ **503**（fail closed）
        5. 调用者**不是**该项目成员           ⇒ **403**
        6. 通过 ⇒ 作用域写入 ``request.state``

    作用域**只**来自服务端 ``PROJECT_ID``（不来自客户端 Query / Header /
    Body）；``MEMBER`` 与 ``PROJECT_OPS`` 均满足成员判定（后者是前者的超集）。
    本项目**不**因持有系统级 ``SYSTEM_OPS`` 而自动成为项目成员。

    Raises:
        HTTPException 401: 会话缺失 / 无效 / 过期 / 已撤销。
        HTTPException 403: 已认证但不是该项目成员。
        HTTPException 503: 会话服务不可用 / 作用域未配置 / 授权存储不可用。
    """
    principal = _require_authenticated(request)

    raw = get_observability_project_id()
    if raw is None or not raw.strip():
        logger.error("observability project scope is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_PROJECT_NOT_CONFIGURED_DETAIL,
        )

    # 服务端唯一授权作用域（trim 后使用；客户端不可主张）
    scope = raw.strip()

    # 项目成员判定（存储不可用 ⇒ 503；无授权 ⇒ 403）
    #
    # 判定**委托**给 authorization_dependency.enforce_project_access
    # —— 项目级授权决策只保留**一份实现**（与 require_project_member /
    # require_project_ops 完全同源），避免两处逻辑分歧。
    authz.enforce_project_access(principal.user_id, scope, ops_only=False)

    # 仅在授权通过后才把作用域放进 request.state（端点体据此读数据）
    request.state.observability_project_scope = scope


def get_observability_project_scope(request: Request) -> str:
    """读取依赖注入的服务端项目作用域（端点体调用）。

    Raises:
        HTTPException 503: 作用域缺失（依赖未注入 / 被绕过）——fail closed。
    """
    scope = getattr(
        request, "state", None
    ) and getattr(request.state, OBSERVABILITY_PROJECT_SCOPE_STATE_KEY, None)
    if not isinstance(scope, str) or not scope.strip():
        logger.error("observability project scope is missing")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_PROJECT_NOT_CONFIGURED_DETAIL,
        )
    return scope
