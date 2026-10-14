"""Resource Ownership Service（Phase 7 Recovery M1 — 用户 ↔ 资源归属）。

最小归属方案（**不向冻结表加列**）：

    * 归属登记在独立映射表 ``ai_ops.resource_owner``（见 models/resource_owner.py）；
    * 只在**根资源**登记一次：``CONVERSATION`` / ``AGENT_RUN`` / ``WORKFLOW_RUN``；
      子资源（turn / step / decision / tool_result / node_run）经父资源 ID
      继承归属——查询子资源前先校验父资源归属；
    * ``project_id`` 由服务端从资源本体复制（客户端不可主张）；
    * **fail-closed 读取**：没有归属行的资源（M1 前历史资源 / API-Key 服务路径
      创建）在用户上下文下一律拒绝；API-Key 路径（无用户上下文）不做归属校验，
      既有行为不变。

异步执行上下文：Agent / Workflow 的运行时执行是异步的；本服务**只**在
命令入口（``AgentCommandService.start_agent(user_id=...)``）同步登记归属，
执行链路内部不传递用户身份——归属记录与 run 记录同源同刻生成。

组合根（测试可 monkeypatch ``get_resource_ownership_service``）。
"""
from __future__ import annotations

import logging

from backend.app.db.models.resource_owner import (
    RESOURCE_TYPE_AGENT_RUN,
    RESOURCE_TYPE_CONVERSATION,
    RESOURCE_TYPE_WORKFLOW_RUN,
)
from backend.app.db.resource_owner_repository import (
    ResourceOwnerConflictError,
    ResourceOwnerRepository,
    ResourceOwnerRepositoryError,
    ResourceOwnerRow,
    validate_resource_id,
    validate_resource_type,
    validate_owner_project_id,
    validate_owner_user_id,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ResourceOwnershipService",
    "ResourceOwnershipError",
    "ResourceOwnershipDeniedError",
    "get_resource_ownership_service",
    "RESOURCE_TYPE_CONVERSATION",
    "RESOURCE_TYPE_AGENT_RUN",
    "RESOURCE_TYPE_WORKFLOW_RUN",
]


class ResourceOwnershipError(RuntimeError):
    """归属服务错误（不含资源 ID / 用户 ID 以外的内部细节）。"""


class ResourceOwnershipDeniedError(ResourceOwnershipError):
    """资源不属于当前用户（或无归属登记）——读取方必须按"不存在"处理。"""


class ResourceOwnershipService:
    """归属登记 / 查询 / fail-closed 校验（最小实现）。"""

    def __init__(
        self, repository: ResourceOwnerRepository | None = None
    ) -> None:
        self._repo = repository or ResourceOwnerRepository()

    # ---------- 登记 ----------

    def bind_owner(
        self,
        *,
        resource_type: str,
        resource_id: str,
        user_id: str,
        project_id: str,
    ) -> ResourceOwnerRow:
        """登记归属（幂等；异用户冲突 ⇒ ``ResourceOwnershipError``）。

        输入校验在**服务层**先行（不依赖具体 Repository 实现）：资源类型
        白名单 / 非空 ID / 长度上限。
        """
        resource_type = validate_resource_type(resource_type)
        resource_id = validate_resource_id(resource_id)
        user_id = validate_owner_user_id(user_id)
        project_id = validate_owner_project_id(project_id)
        try:
            return self._repo.bind(
                resource_type=resource_type,
                resource_id=resource_id,
                user_id=user_id,
                project_id=project_id,
            )
        except ResourceOwnerConflictError as exc:
            raise ResourceOwnershipError("资源归属冲突") from exc
        except ResourceOwnerRepositoryError as exc:
            raise ResourceOwnershipError(
                f"归属登记失败（{type(exc).__name__}）"
            ) from exc

    # ---------- 查询 ----------

    def get_owner(
        self, *, resource_type: str, resource_id: str
    ) -> ResourceOwnerRow | None:
        """查归属（无 → None）。输入校验先行（不依赖 Repository 实现）。"""
        resource_type = validate_resource_type(resource_type)
        resource_id = validate_resource_id(resource_id)
        try:
            return self._repo.get(
                resource_type=resource_type, resource_id=resource_id
            )
        except ResourceOwnerRepositoryError as exc:
            raise ResourceOwnershipError(
                f"归属读取失败（{type(exc).__name__}）"
            ) from exc

    def assert_can_read(
        self, *, resource_type: str, resource_id: str, user_id: str
    ) -> None:
        """fail-closed 归属校验（**不同用户 / 无归属登记** ⇒ 拒绝）。

        Raises:
            ResourceOwnershipDeniedError: 资源不可被该用户读取。
        """
        resource_type = validate_resource_type(resource_type)
        resource_id = validate_resource_id(resource_id)
        user_id = validate_owner_user_id(user_id)
        owner = self.get_owner(
            resource_type=resource_type, resource_id=resource_id
        )
        if owner is None or owner.user_id != user_id:
            # 不区分"不存在"与"属于他人"——调用方按 404 同构处理
            raise ResourceOwnershipDeniedError("资源不可访问")

    def list_user_resources(
        self,
        user_id: str,
        *,
        resource_type: str | None = None,
        limit: int = 50,
    ) -> tuple[ResourceOwnerRow, ...]:
        """某用户的资源列表（供未来 Web MVP 历史列表；上限由 Repository 钳制）。"""
        try:
            return self._repo.list_for_user(
                user_id, resource_type=resource_type, limit=limit
            )
        except ResourceOwnerRepositoryError as exc:
            raise ResourceOwnershipError(
                f"归属列表读取失败（{type(exc).__name__}）"
            ) from exc


def get_resource_ownership_service() -> ResourceOwnershipService:
    """Composition Root：归属服务（测试以 monkeypatch 替换本函数）。"""
    return _OWNERSHIP_SERVICE


_OWNERSHIP_SERVICE = ResourceOwnershipService()
