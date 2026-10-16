"""Authorization Service（Phase 7 Recovery M2.4 — 最小 RBAC 授权判定）。

职责（**只做授权判定**；不碰会话 / HTTP / 业务）：

    project_role(user_id, project_id)      → str | None   该项目上的角色
    is_project_member(user_id, project_id) → bool         MEMBER 或 PROJECT_OPS
    is_project_ops(user_id, project_id)    → bool         仅 PROJECT_OPS
    is_system_ops(user_id)                 → bool         仅 SYSTEM 作用域

授权判定铁律（任务书 §五 / §四）：

    1. 身份**只**来自服务端（调用方传入的是已验证的 ``Principal.user_id``）；
       本服务**绝不**接受客户端传入的 role / membership 作为判定依据；
    2. **项目级 ≠ 系统级**：
       - ``PROJECT_OPS`` **不**推导为 ``SYSTEM_OPS``；
       - ``SYSTEM_OPS`` **不**推导为任意项目成员（系统级行没有 project_id，
         项目级查询按 ``project_id = ?`` 过滤，永远看不到它）；
    3. **缺少授权行 = 无授权**（``None`` / ``False``），绝不回退默认放行；
    4. **fail closed**：授权存储不可用 ⇒ :class:`AuthorizationStoreUnavailableError`
       （调用方映射 503），**绝不**降级为"放行"或"当作无授权后继续读数据"；
    5. "存储未配置"（无 ``DATABASE_URL``）与"存储不可用"（查询失败）**必须区分**：
       前者是本部署的既有形态（M2.3 A2 时代无授权表），调用方据此仅使用显式
       配置来源；后者是真故障 ⇒ 503。

组合根（沿用 M1 ``get_user_service`` 模式；测试 monkeypatch 本函数）：

    get_authorization_service() → AuthorizationService(AuthorizationGrantRepository())
    构造期**不**连接数据库（Session 工厂延迟获取）。
"""
from __future__ import annotations

import logging
from typing import Final

from backend.app.db.authorization_grant_repository import (
    AuthorizationGrantRepository,
    AuthorizationGrantRepositoryError,
    AuthorizationGrantStoreNotConfiguredError,
)
from backend.app.db.models.authorization_grant import (
    PROJECT_ROLE_MEMBER,
    PROJECT_ROLE_PROJECT_OPS,
    SYSTEM_ROLE_SYSTEM_OPS,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AuthorizationService",
    "AuthorizationServiceError",
    "AuthorizationStoreUnavailableError",
    "get_authorization_service",
    "PROJECT_ROLE_MEMBER",
    "PROJECT_ROLE_PROJECT_OPS",
    "SYSTEM_ROLE_SYSTEM_OPS",
]


class AuthorizationServiceError(RuntimeError):
    """授权判定失败（基类）。"""


class AuthorizationStoreUnavailableError(AuthorizationServiceError):
    """授权存储**已配置但不可用** ⇒ 调用方必须 fail closed（HTTP 503）。

    注意：``DATABASE_URL`` 未配置（存储**未配置**）**不会**抛出本异常 ——
    那种情况由 :meth:`AuthorizationService.project_role` 等方法返回
    "无授权"（``None`` / ``False``），使无 DB 部署保持 M2.3 A2 的既有形态。
    """


class AuthorizationService:
    """授权判定服务（只读为主；写入由 CLI 经 Repository 完成）。"""

    def __init__(
        self,
        repository: AuthorizationGrantRepository | None = None,
    ) -> None:
        """构造服务。

        Args:
            repository: 可注入的仓储（测试用）；``None`` ⇒ 延迟构造默认仓储
                （构造期不连接数据库）。
        """
        self._repository = (
            repository if repository is not None else AuthorizationGrantRepository()
        )

    # ---- 内部：统一错误映射 ---------------------------------------------

    def _call(self, func: object, /, **kwargs: object) -> object:
        """"执行仓储调用并把错误映射为服务语义。

        * 存储**未配置** ⇒ 视为"本部署没有持久化授权" ⇒ 返回 ``None``
          （= 无授权），由调用方决定是否还有其它显式来源；
        * 存储**不可用**  ⇒ :class:`AuthorizationStoreUnavailableError`（fail closed）。
        """
        try:
            return func(**kwargs)  # type: ignore[operator]
        except AuthorizationGrantStoreNotConfiguredError:
            logger.debug("authorization store is not configured")
            return None
        except AuthorizationGrantRepositoryError as exc:
            # 只记录错误类型——不记录 user_id / project_id / 表内容
            logger.error(
                "authorization store unavailable",
                extra={"error_type": type(exc).__name__},
            )
            raise AuthorizationStoreUnavailableError(
                "授权存储不可用"
            ) from exc
        except ValueError:
            # 参数非法属**调用方**错误（服务端代码 bug），不静默吞掉
            raise

    # ---- 项目级 ----------------------------------------------------------

    def project_role(self, user_id: str, project_id: str) -> str | None:
        """返回该用户在该项目上的"最强"角色；无授权 ⇒ ``None``。

        强度序：``PROJECT_OPS`` > ``MEMBER``（仅用于返回单一值；**不**影响
        判定语义 —— :meth:`is_project_member` 接受两者）。

        Raises:
            AuthorizationStoreUnavailableError: 授权存储不可用（fail closed）。
        """
        roles = self._call(
            self._repository.get_project_roles,
            user_id=user_id,
            project_id=project_id,
        )
        if not roles:
            return None
        if PROJECT_ROLE_PROJECT_OPS in roles:  # type: ignore[operator]
            return PROJECT_ROLE_PROJECT_OPS
        if PROJECT_ROLE_MEMBER in roles:  # type: ignore[operator]
            return PROJECT_ROLE_MEMBER
        # 仓储保证只可能返回合法项目角色；到此处说明出现未建模角色
        raise AuthorizationServiceError("未知项目级角色")

    def is_project_member(self, user_id: str, project_id: str) -> bool:
        """是否至少是该项目成员（``MEMBER`` 或 ``PROJECT_OPS``）。

        Raises:
            AuthorizationStoreUnavailableError: 授权存储不可用（fail closed）。
        """
        return self.project_role(user_id, project_id) is not None

    def is_project_ops(self, user_id: str, project_id: str) -> bool:
        """是否持有该项目 ``PROJECT_OPS``（**不**含系统级权限）。

        Raises:
            AuthorizationStoreUnavailableError: 授权存储不可用（fail closed）。
        """
        return (
            self.project_role(user_id, project_id) == PROJECT_ROLE_PROJECT_OPS
        )

    # ---- 系统级 ----------------------------------------------------------

    def is_system_ops(self, user_id: str) -> bool:
        """是否持有显式 ``SYSTEM`` 作用域的 ``SYSTEM_OPS``。

        判定**只**看 ``scope_type = 'SYSTEM'`` 的行；任何项目级授权都不参与。

        Raises:
            AuthorizationStoreUnavailableError: 授权存储不可用（fail closed）。
        """
        result = self._call(
            self._repository.has_system_role,
            user_id=user_id,
            role=SYSTEM_ROLE_SYSTEM_OPS,
        )
        return bool(result)


def get_authorization_service() -> AuthorizationService:
    """组合根：默认装配 ``AuthorizationService(AuthorizationGrantRepository())``。

    构造期不连接数据库（Session 工厂延迟获取）；测试以 monkeypatch 替换。
    """
    return AuthorizationService()
