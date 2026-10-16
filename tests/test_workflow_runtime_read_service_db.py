"""Phase 6 Step 6 — S6 Read Layer **DB-gated** 集成测试（真 PostgreSQL）。

**默认跳过**（沿用项目既有 ``RUN_DB_TESTS`` 门控；专用测试库 = 本地开发库，
与 Step 2/5 DB 测试同一机制）：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_workflow_runtime_read_service_db.py
```

覆盖（S6 任务书 §八B 六项 + Timeline 真实时间戳验证）：
```text
1  创建 Run 后可以读取（真实 Repository → StoreAdapter → ReadService）
2  多个 Node Attempt 按正确顺序读取（历史保留）
3  Approval 状态与裁决时间读取正确
4  跨 Project 查询被拒绝（同构 NotFound；不泄漏存在性）
5  查询前后 Run / Node Attempt / Approval 状态不变（快照对比）
6  清理后 DB residue 为零
★ Timeline 由真实持久化时间戳构建（RUN_CREATED/STARTED/FINISHED 顺序）
```

安全边界：只读验证（数据准备经真实 Repository 显式写入）；只写
``ai_ops.workflow_*`` 三表；定向前缀清理（``wf-p6s6-``）；不调用真实
LLM / Tool / Runtime 执行入口。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import text

from backend.app.db.session import get_engine
from backend.app.db.workflow_approval_repository import (
    WorkflowApprovalRepository,
)
from backend.app.db.workflow_node_run_repository import (
    WorkflowNodeRunRepository,
)
from backend.app.db.workflow_run_repository import WorkflowRunRepository
from backend.app.workflow.errors import WF_ERROR_NODE_EXECUTION_FAILURE
from backend.app.workflow.states import APPROVAL_STATUS_APPROVED
from backend.app.workflow_runtime.read_service import (
    WorkflowReadNotFoundError,
    WorkflowReadService,
)
from backend.app.workflow_runtime.store_adapters import (
    WorkflowApprovalStoreAdapter,
    WorkflowNodeRunStoreAdapter,
    WorkflowRunStoreAdapter,
)

_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

pytestmark = pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable workflow read layer PostgreSQL tests",
)

__all__ = [
    "TestReadServiceDb",
    "TestReadNonMutationDb",
    "TestResidueDb",
    "TestNewReadCapabilitiesDb",
]

_RUN_PREFIX = "wf-p6s6-"
_PROJECT = "proj-s6"
_OTHER_PROJECT = "wf-p6s6-other"


def _run_id() -> str:
    return f"{_RUN_PREFIX}{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def engine() -> Any:
    eng = get_engine()
    if eng is None:
        pytest.skip("DATABASE_URL 未配置，跳过 Workflow Read DB 集成测试")
    return eng


def _delete_prefix(engine: Any) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM ai_ops.workflow_run "
                "WHERE workflow_run_id LIKE :prefix"
            ),
            {"prefix": f"{_RUN_PREFIX}%"},
        )


def _counts(engine: Any) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            table: int(
                conn.execute(
                    text(
                        f"SELECT COUNT(*) FROM ai_ops.{table} "
                        "WHERE workflow_run_id LIKE :prefix"
                    ),
                    {"prefix": f"{_RUN_PREFIX}%"},
                ).scalar_one()
            )
            for table in (
                "workflow_run",
                "workflow_node_run",
                "workflow_approval",
            )
        }


@pytest.fixture(scope="module", autouse=True)
def _cleanup(engine: Any):
    _delete_prefix(engine)
    yield
    _delete_prefix(engine)


def _read_service() -> WorkflowReadService:
    """真实 Repository → Store Adapter → Read Service（S6 目标架构）。"""
    return WorkflowReadService(
        run_store=WorkflowRunStoreAdapter(WorkflowRunRepository()),
        node_run_store=WorkflowNodeRunStoreAdapter(
            WorkflowNodeRunRepository()
        ),
        approval_store=WorkflowApprovalStoreAdapter(
            WorkflowApprovalRepository()
        ),
    )


def _seed_run(
    *,
    status: str = "PENDING",
    input: str | None = '{"seed": "read-layer"}',
) -> str:
    run_id = _run_id()
    WorkflowRunRepository().create(
        workflow_run_id=run_id,
        workflow_id="s6_read_flow",
        workflow_version="v1",
        request_id=f"{_RUN_PREFIX}req",
        project_id=_PROJECT,
        input=input,
        status=status,
    )
    return run_id


# ============================================================
# Test 1：读取（Run / Node / Approval / Timeline）
# ============================================================


class TestReadServiceDb:
    def test_run_readable_after_creation(self) -> None:
        run_id = _seed_run()
        detail = _read_service().get_run_detail(
            workflow_run_id=run_id, project_id=_PROJECT
        )

        assert detail.workflow_run_id == run_id
        assert detail.workflow_id == "s6_read_flow"
        assert detail.workflow_version == "v1"
        assert detail.project_id == _PROJECT
        assert detail.status == "PENDING"
        assert detail.state_version == 0
        assert detail.created_at is not None  # 真实持久化时间戳
        assert detail.started_at is None  # 未开始（不伪造）
        assert not hasattr(detail, "input")  # 启动输入永不进入 Read DTO

    def test_multiple_attempts_read_in_order(self) -> None:
        run_id = _seed_run()
        node_repo = WorkflowNodeRunRepository()
        node_repo.create(
            workflow_run_id=run_id,
            node_id="step_one",
            attempt=1,
            node_type="TOOL",
            status="FAILED",
            error_code=WF_ERROR_NODE_EXECUTION_FAILURE,
        )
        node_repo.create(
            workflow_run_id=run_id,
            node_id="step_one",
            attempt=2,
            node_type="TOOL",
            status="SUCCEEDED",
            output='{"qty":3}',
        )

        records = _read_service().get_node_runs(
            workflow_run_id=run_id, project_id=_PROJECT
        )
        assert [(r.node_id, r.attempt) for r in records] == [
            ("step_one", 1),
            ("step_one", 2),
        ]
        assert records[0].status == "FAILED"
        assert records[1].status == "SUCCEEDED"
        assert records[1].output == '{"qty":3}'  # 已持久化输出原样

    def test_approval_status_and_resolved_time(self) -> None:
        run_id = _seed_run()
        approval_repo = WorkflowApprovalRepository()
        approval_id = f"{run_id}-ap"
        approval_repo.create(
            approval_id=approval_id, workflow_run_id=run_id, node_id="gate"
        )

        service = _read_service()
        pending = service.get_approval_detail(
            approval_id=approval_id, project_id=_PROJECT
        )
        assert pending.status == "PENDING"
        assert pending.resolved_at is None

        approval_repo.resolve(
            approval_id=approval_id, decision=APPROVAL_STATUS_APPROVED
        )
        resolved = service.get_approval_detail(
            approval_id=approval_id, project_id=_PROJECT
        )
        assert resolved.status == APPROVAL_STATUS_APPROVED
        assert resolved.resolved_at is not None  # 真实裁决时间

    def test_cross_project_read_rejected(self) -> None:
        run_id = _seed_run()
        service = _read_service()

        with pytest.raises(WorkflowReadNotFoundError):
            service.get_run_detail(
                workflow_run_id=run_id, project_id=_OTHER_PROJECT
            )
        with pytest.raises(WorkflowReadNotFoundError):
            service.get_node_runs(
                workflow_run_id=run_id, project_id=_OTHER_PROJECT
            )
        with pytest.raises(WorkflowReadNotFoundError):
            service.get_run_timeline(
                workflow_run_id=run_id, project_id=_OTHER_PROJECT
            )

    def test_timeline_from_real_persisted_timestamps(self) -> None:
        run_id = _seed_run()
        run_repo = WorkflowRunRepository()
        run_repo.update_cas(
            workflow_run_id=run_id,
            expected_state_version=0,
            status="RUNNING",
            current_node_id="step_one",
            next_action="NODE_EXECUTION",
            attempt_count=1,
        )
        run_repo.update_cas(
            workflow_run_id=run_id,
            expected_state_version=1,
            status="COMPLETED",
            current_node_id="step_one",
            next_action="COMPLETED",
            attempt_count=1,
        )

        events = _read_service().get_run_timeline(
            workflow_run_id=run_id, project_id=_PROJECT
        )
        kinds = [event.kind for event in events]
        assert kinds == ["RUN_CREATED", "RUN_STARTED", "RUN_FINISHED"]
        stamps = [event.timestamp for event in events]
        assert stamps == sorted(stamps)
        for event in events:
            assert event.workflow_run_id == run_id


# ============================================================
# Test 2：查询不改状态（快照对比）
# ============================================================


class TestReadNonMutationDb:
    def test_reads_do_not_change_persisted_state(self) -> None:
        run_id = _seed_run()
        node_repo = WorkflowNodeRunRepository()
        node_repo.create(
            workflow_run_id=run_id,
            node_id="step_one",
            attempt=1,
            node_type="APPROVAL",
            status="WAITING_APPROVAL",
        )
        approval_repo = WorkflowApprovalRepository()
        approval_id = f"{run_id}-ap"
        approval_repo.create(
            approval_id=approval_id, workflow_run_id=run_id, node_id="step_one"
        )

        run_before = WorkflowRunRepository().get(run_id)
        node_before = node_repo.get(
            workflow_run_id=run_id, node_id="step_one", attempt=1
        )
        approval_before = approval_repo.get(approval_id)
        assert run_before is not None
        assert node_before is not None
        assert approval_before is not None

        service = _read_service()
        service.get_run_detail(workflow_run_id=run_id, project_id=_PROJECT)
        service.get_node_runs(workflow_run_id=run_id, project_id=_PROJECT)
        service.get_approval_detail(
            approval_id=approval_id, project_id=_PROJECT
        )
        service.get_run_timeline(workflow_run_id=run_id, project_id=_PROJECT)

        run_after = WorkflowRunRepository().get(run_id)
        node_after = node_repo.get(
            workflow_run_id=run_id, node_id="step_one", attempt=1
        )
        approval_after = approval_repo.get(approval_id)
        assert run_after is not None and node_after is not None
        assert approval_after is not None

        # 状态 / 版本 / 时间戳逐字段不变（查询是纯读）
        assert run_after.state_version == run_before.state_version
        assert run_after.status == run_before.status
        assert run_after.updated_at == run_before.updated_at
        assert node_after.status == node_before.status
        assert node_after.output == node_before.output
        assert approval_after.status == approval_before.status
        assert approval_after.resolved_at == approval_before.resolved_at


# ============================================================
# Test 3：残留
# ============================================================


class TestResidueDb:
    def test_residue_is_zero_after_cleanup(self, engine: Any) -> None:
        _delete_prefix(engine)
        assert _counts(engine) == {
            "workflow_run": 0,
            "workflow_node_run": 0,
            "workflow_approval": 0,
        }


# ============================================================
# Test 4：新增只读能力（安全投影 / 按 Node 读取审批）
# ============================================================


class TestNewReadCapabilitiesDb:
    """真实 Repository → Store Adapter → Read Service 的新增能力集成。

    验证：真实持久化行的安全投影（output 原样 / error_code 保留）、
    按 ``(run, node)`` 读取审批、空结果语义、跨项目拒绝、查询不改状态。
    """

    def test_node_run_details_from_real_rows(self) -> None:
        run_id = _seed_run()
        node_repo = WorkflowNodeRunRepository()
        node_repo.create(
            workflow_run_id=run_id,
            node_id="step_one",
            attempt=1,
            node_type="TOOL",
            status="FAILED",
            error_code=WF_ERROR_NODE_EXECUTION_FAILURE,
        )
        started_at = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
        node_repo.create(
            workflow_run_id=run_id,
            node_id="step_one",
            attempt=2,
            node_type="TOOL",
            status="SUCCEEDED",
            output='{"qty":3}',
            elapsed_ms=12.5,
            started_at=started_at,
        )

        details = _read_service().get_node_run_details(
            workflow_run_id=run_id, project_id=_PROJECT
        )

        # 同一节点全部 attempt 保留（不被折叠成最新一条）
        assert [d.attempt for d in details] == [1, 2]
        assert details[0].status == "FAILED"
        assert details[0].error_code == WF_ERROR_NODE_EXECUTION_FAILURE
        assert details[0].output is None
        assert details[1].status == "SUCCEEDED"
        assert details[1].output == '{"qty":3}'      # 已持久化输出原样
        assert details[1].elapsed_ms == 12.5
        # 真实持久化时间戳往返一致（未写入的行保持 None——不伪造）
        assert details[1].started_at is not None
        assert details[1].started_at == started_at
        assert details[0].started_at is None
        # 投影面不含启动输入 / 内部对象
        for detail in details:
            assert not hasattr(detail, "input")
            assert not hasattr(detail, "project_id")

    def test_node_run_details_empty_for_run_without_attempts(self) -> None:
        run_id = _seed_run()

        assert _read_service().get_node_run_details(
            workflow_run_id=run_id, project_id=_PROJECT
        ) == ()

    def test_node_run_details_cross_project_rejected(self) -> None:
        run_id = _seed_run()
        WorkflowNodeRunRepository().create(
            workflow_run_id=run_id,
            node_id="step_one",
            attempt=1,
            node_type="TOOL",
            status="SUCCEEDED",
            output='{"qty":1}',
        )

        with pytest.raises(WorkflowReadNotFoundError):
            _read_service().get_node_run_details(
                workflow_run_id=run_id, project_id=_OTHER_PROJECT
            )

    def test_approval_for_node_pending_then_resolved(self) -> None:
        run_id = _seed_run()
        node_repo = WorkflowNodeRunRepository()
        node_repo.create(
            workflow_run_id=run_id,
            node_id="gate",
            attempt=1,
            node_type="APPROVAL",
            status="WAITING_APPROVAL",
        )
        approval_repo = WorkflowApprovalRepository()
        approval_id = f"{run_id}-ap"
        approval_repo.create(
            approval_id=approval_id, workflow_run_id=run_id, node_id="gate"
        )

        service = _read_service()
        pending = service.get_approval_for_node(
            workflow_run_id=run_id, node_id="gate", project_id=_PROJECT
        )
        assert pending is not None
        assert pending.approval_id == approval_id
        assert pending.status == "PENDING"
        assert pending.resolved_at is None       # 未裁决 ⇒ None（不伪造）
        assert not hasattr(pending, "created_at")  # Port 未暴露 ⇒ 不伪造

        approval_repo.resolve(
            approval_id=approval_id, decision=APPROVAL_STATUS_APPROVED
        )
        resolved = service.get_approval_for_node(
            workflow_run_id=run_id, node_id="gate", project_id=_PROJECT
        )
        assert resolved is not None
        assert resolved.status == APPROVAL_STATUS_APPROVED
        assert resolved.resolved_at is not None  # 真实裁决时间

    def test_approval_for_node_absent_is_none(self) -> None:
        """该节点尚无审批行 ⇒ ``None``（合法事实——不是错误）。"""
        run_id = _seed_run()

        assert _read_service().get_approval_for_node(
            workflow_run_id=run_id, node_id="never-created",
            project_id=_PROJECT,
        ) is None

    def test_approval_for_node_cross_project_rejected(self) -> None:
        run_id = _seed_run()
        WorkflowApprovalRepository().create(
            approval_id=f"{run_id}-ap",
            workflow_run_id=run_id,
            node_id="gate",
        )

        with pytest.raises(WorkflowReadNotFoundError):
            _read_service().get_approval_for_node(
                workflow_run_id=run_id, node_id="gate",
                project_id=_OTHER_PROJECT,
            )

    def test_new_queries_do_not_change_persisted_state(
        self, engine: Any
    ) -> None:
        """新增查询前后三表行 + 关键字段完全不变（只读性）。"""
        run_id = _seed_run()
        WorkflowNodeRunRepository().create(
            workflow_run_id=run_id,
            node_id="gate",
            attempt=1,
            node_type="APPROVAL",
            status="WAITING_APPROVAL",
        )
        WorkflowApprovalRepository().create(
            approval_id=f"{run_id}-ap",
            workflow_run_id=run_id,
            node_id="gate",
        )

        before_counts = _counts(engine)
        service = _read_service()
        approval_before = service.get_approval_for_node(
            workflow_run_id=run_id, node_id="gate", project_id=_PROJECT
        )
        run_before = service.get_run_detail(
            workflow_run_id=run_id, project_id=_PROJECT
        )

        for _ in range(3):
            service.get_node_run_details(
                workflow_run_id=run_id, project_id=_PROJECT
            )
            service.get_approval_for_node(
                workflow_run_id=run_id, node_id="gate", project_id=_PROJECT
            )

        assert _counts(engine) == before_counts
        approval_after = service.get_approval_for_node(
            workflow_run_id=run_id, node_id="gate", project_id=_PROJECT
        )
        run_after = service.get_run_detail(
            workflow_run_id=run_id, project_id=_PROJECT
        )
        assert approval_after is not None and approval_before is not None
        assert approval_after.status == approval_before.status
        assert approval_after.resolved_at == approval_before.resolved_at
        assert run_after.state_version == run_before.state_version
        assert run_after.status == run_before.status
        assert run_after.updated_at == run_before.updated_at
