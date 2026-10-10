"""Workflow Read 装配（Phase 6 Step 7 · S7"新增依赖注入装配"）。

把真实 Repository 经 **Store Adapter** 装配为只读
:class:`WorkflowReadService`：

```text
create_workflow_read_service()
    ↓
WorkflowReadService(
    WorkflowRunStoreAdapter(WorkflowRunRepository()),
    WorkflowNodeRunStoreAdapter(WorkflowNodeRunRepository()),
    WorkflowApprovalStoreAdapter(WorkflowApprovalRepository()),
)
```

定位（与 S5 ``factory.py`` 同轴——装配层；不修改任何既有模块）：
    * **API 层不 import Repository / SQLAlchemy**（S7 §九）——装配集中在本模块；
    * ``read_service`` 不感知本模块（单向依赖；无循环）；
    * 不创建 Engine / 全局 Session（Repository 惰性；每操作独立事务）；
    * 不触达 Runtime 执行入口；纯只读装配。

边界（AST 测试锁定）：本模块是装配层，允许 import db + 同包只读件；
被 API 层依赖；**不**被 read_service 反向依赖。
"""
from __future__ import annotations

from backend.app.workflow_runtime.read_service import WorkflowReadService

__all__ = ["create_workflow_read_service"]


def create_workflow_read_service() -> WorkflowReadService:
    """装配只读服务（延迟 import；**纯构造**——不触 DB 直到首次查询）。"""
    from backend.app.db.workflow_approval_repository import (
        WorkflowApprovalRepository,
    )
    from backend.app.db.workflow_node_run_repository import (
        WorkflowNodeRunRepository,
    )
    from backend.app.db.workflow_run_repository import WorkflowRunRepository
    from backend.app.workflow_runtime.store_adapters import (
        WorkflowApprovalStoreAdapter,
        WorkflowNodeRunStoreAdapter,
        WorkflowRunStoreAdapter,
    )

    return WorkflowReadService(
        run_store=WorkflowRunStoreAdapter(WorkflowRunRepository()),
        node_run_store=WorkflowNodeRunStoreAdapter(
            WorkflowNodeRunRepository()
        ),
        approval_store=WorkflowApprovalStoreAdapter(
            WorkflowApprovalRepository()
        ),
    )
