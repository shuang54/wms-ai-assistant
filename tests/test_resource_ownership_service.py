"""ResourceOwnershipService 单元测试（Phase 7 Recovery M1；离线 Fake）。

覆盖任务书测试清单中的"跨用户资源访问"与"伪造 user_id"的服务端语义：
    * 绑定 / 幂等重绑 / 异用户冲突；
    * fail-closed 读取：无归属登记（历史资源）⇒ 拒绝；
    * 跨用户读取 ⇒ 拒绝（调用方按 404 同构处理）；
    * 资源类型白名单（拒绝任意字符串）。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.app.db.resource_owner_repository import (
    ResourceOwnerConflictError,
    ResourceOwnerRepositoryError,
    ResourceOwnerRow,
)
from backend.app.services.resource_ownership_service import (
    RESOURCE_TYPE_AGENT_RUN,
    RESOURCE_TYPE_CONVERSATION,
    ResourceOwnershipDeniedError,
    ResourceOwnershipError,
    ResourceOwnershipService,
)

__all__ = [
    "FakeOwnershipRepository",
    "test_bind_and_idempotent_rebind",
    "test_bind_conflict_for_other_user",
    "test_get_owner_none_for_unknown_resource",
    "test_assert_can_read_allows_owner",
    "test_assert_can_read_denies_other_user",
    "test_assert_can_read_denies_legacy_resource_without_owner",
    "test_list_user_resources_scoped",
    "test_unknown_resource_type_rejected",
]


class FakeOwnershipRepository:
    """进程内 Fake 归属存储（含唯一约束语义）。"""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], ResourceOwnerRow] = {}
        self._next_id = 1

    def bind(
        self,
        *,
        resource_type: str,
        resource_id: str,
        user_id: str,
        project_id: str,
    ) -> ResourceOwnerRow:
        key = (resource_type, resource_id)
        existing = self.rows.get(key)
        if existing is not None:
            if existing.user_id != user_id:
                raise ResourceOwnerConflictError(key)
            return existing
        row = ResourceOwnerRow(
            id=self._next_id,
            resource_type=resource_type,
            resource_id=resource_id,
            user_id=user_id,
            project_id=project_id,
            created_at=datetime.now(timezone.utc),
        )
        self._next_id += 1
        self.rows[key] = row
        return row

    def get(
        self, *, resource_type: str, resource_id: str
    ) -> ResourceOwnerRow | None:
        return self.rows.get((resource_type, resource_id))

    def list_for_user(
        self,
        user_id: str,
        *,
        resource_type: str | None = None,
        limit: int = 50,
    ) -> tuple[ResourceOwnerRow, ...]:
        rows = [
            row
            for row in self.rows.values()
            if row.user_id == user_id
            and (resource_type is None or row.resource_type == resource_type)
        ]
        rows.sort(key=lambda row: row.id, reverse=True)
        return tuple(rows[: max(1, min(int(limit), 200))])


def _service() -> tuple[ResourceOwnershipService, FakeOwnershipRepository]:
    repo = FakeOwnershipRepository()
    return ResourceOwnershipService(repository=repo), repo  # type: ignore[arg-type]


def test_bind_and_idempotent_rebind() -> None:
    service, repo = _service()
    first = service.bind_owner(
        resource_type=RESOURCE_TYPE_CONVERSATION,
        resource_id="conv-1",
        user_id="user-1",
        project_id="vietnam-wms",
    )
    again = service.bind_owner(
        resource_type=RESOURCE_TYPE_CONVERSATION,
        resource_id="conv-1",
        user_id="user-1",
        project_id="vietnam-wms",
    )
    assert first.id == again.id  # 同用户重绑幂等
    assert len(repo.rows) == 1


def test_bind_conflict_for_other_user() -> None:
    service, _ = _service()
    service.bind_owner(
        resource_type=RESOURCE_TYPE_AGENT_RUN,
        resource_id="run-1",
        user_id="user-1",
        project_id="vietnam-wms",
    )
    with pytest.raises(ResourceOwnershipError):
        service.bind_owner(
            resource_type=RESOURCE_TYPE_AGENT_RUN,
            resource_id="run-1",
            user_id="user-2",
            project_id="vietnam-wms",
        )


def test_get_owner_none_for_unknown_resource() -> None:
    service, _ = _service()
    assert (
        service.get_owner(
            resource_type=RESOURCE_TYPE_AGENT_RUN, resource_id="missing"
        )
        is None
    )


def test_assert_can_read_allows_owner() -> None:
    service, _ = _service()
    service.bind_owner(
        resource_type=RESOURCE_TYPE_AGENT_RUN,
        resource_id="run-1",
        user_id="user-1",
        project_id="vietnam-wms",
    )
    # 不抛错即通过
    service.assert_can_read(
        resource_type=RESOURCE_TYPE_AGENT_RUN,
        resource_id="run-1",
        user_id="user-1",
    )


def test_assert_can_read_denies_other_user() -> None:
    service, _ = _service()
    service.bind_owner(
        resource_type=RESOURCE_TYPE_AGENT_RUN,
        resource_id="run-1",
        user_id="user-1",
        project_id="vietnam-wms",
    )
    with pytest.raises(ResourceOwnershipDeniedError):
        service.assert_can_read(
            resource_type=RESOURCE_TYPE_AGENT_RUN,
            resource_id="run-1",
            user_id="user-2",
        )


def test_assert_can_read_denies_legacy_resource_without_owner() -> None:
    """M1 之前创建的资源（无归属行）在用户上下文一律拒绝（fail-closed）。"""
    service, _ = _service()
    with pytest.raises(ResourceOwnershipDeniedError):
        service.assert_can_read(
            resource_type=RESOURCE_TYPE_AGENT_RUN,
            resource_id="legacy-run",
            user_id="user-1",
        )


def test_list_user_resources_scoped() -> None:
    service, _ = _service()
    for index in range(3):
        service.bind_owner(
            resource_type=RESOURCE_TYPE_CONVERSATION,
            resource_id=f"conv-{index}",
            user_id="user-1",
            project_id="vietnam-wms",
        )
    service.bind_owner(
        resource_type=RESOURCE_TYPE_CONVERSATION,
        resource_id="conv-other",
        user_id="user-2",
        project_id="vietnam-wms",
    )
    rows = service.list_user_resources("user-1")
    assert len(rows) == 3
    assert {row.user_id for row in rows} == {"user-1"}


def test_unknown_resource_type_rejected() -> None:
    service, _ = _service()
    with pytest.raises(ValueError):
        service.bind_owner(
            resource_type="ANYTHING",
            resource_id="x",
            user_id="user-1",
            project_id="vietnam-wms",
        )
    # 读取路径同样拒绝（输入校验先于存储访问）
    with pytest.raises(ValueError):
        service.get_owner(resource_type="ANYTHING", resource_id="x")
    with pytest.raises(ValueError):
        service.assert_can_read(
            resource_type="ANYTHING", resource_id="x", user_id="user-1"
        )
