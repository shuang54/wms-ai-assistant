"""Phase 6 Step 2 —— Workflow Persistence **DB** 测试（真 PostgreSQL）。

**默认跳过**（沿用项目既有 ``RUN_DB_TESTS`` 门控）：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_workflow_persistence_db.py
```

覆盖（Step 2 §24 全部 11 项）：

```text
1  WorkflowRun CRUD（create / get / update_cas）
2  Workflow version persistence（v1 不丢失；request_id 独立于 workflow_run_id）
3  Project isolation（project-A / project-B 不交叉）
4  state_version CAS（expected=0 成功 ⇒ 1；过期 expected ⇒ conflict）
5  Node attempt（attempt=1 FAILED + attempt=2 SUCCEEDED 共存；重复身份拒绝）
6  Approval lifecycle（PENDING → APPROVED / REJECTED）
7  Approval race（resolver A / B 只有一个成功；条件 UPDATE 单赢）
8  Approval invalid state（PENDING 无 resolved_at；终态有 resolved_at）
9  FK / integrity（非法引用 ⇒ 完整性错误；删除 run ⇒ CASCADE）
10 Init idempotency（init_db() 两次均成功）
11 Residue（清理后 workflow 三表 = 0）
12 F-1 子表 project_id（从 run 继承 / project 隔离 / 旧库缺列回填）
```

边界：只写 ``ai_ops.workflow_*`` 三表；定向前缀清理（``wf-p6s2-``）；
不触碰 agent 六表 / conversation / evidence。
"""
from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from backend.app.db.session import get_engine
from backend.app.db.workflow_approval_repository import (
    WorkflowApprovalRepository,
)
from backend.app.db.workflow_node_run_repository import (
    WorkflowNodeRunAlreadyExistsError,
    WorkflowNodeRunConflictError,
    WorkflowNodeRunNotFoundError,
    WorkflowNodeRunRepository,
    WorkflowNodeRunRepositoryError,
)
from backend.app.db.workflow_run_repository import (
    WorkflowRunAlreadyExistsError,
    WorkflowRunConflictError,
    WorkflowRunNotFoundError,
    WorkflowRunRepository,
)

_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

pytestmark = pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable workflow persistence PostgreSQL tests",
)

__all__ = [
    "TestWorkflowRunPersistenceDb",
    "TestWorkflowNodeRunPersistenceDb",
    "TestWorkflowApprovalPersistenceDb",
    "TestWorkflowInitAndResidueDb",
    "TestWorkflowChildProjectIdAlignmentDb",
]

#: 本文件的 run_id 前缀（定向清理；绝不触碰其它数据）。
_RUN_PREFIX = "wf-p6s2-"

#: 本文件写入的三张表。
_OWN_TABLES: tuple[str, ...] = (
    "workflow_run",
    "workflow_node_run",
    "workflow_approval",
)


def _run_id() -> str:
    return f"{_RUN_PREFIX}{uuid.uuid4().hex[:12]}"


def _run_repo() -> WorkflowRunRepository:
    return WorkflowRunRepository()


def _node_repo() -> WorkflowNodeRunRepository:
    return WorkflowNodeRunRepository()


def _approval_repo() -> WorkflowApprovalRepository:
    return WorkflowApprovalRepository()


def _delete_prefix(engine: Any) -> None:
    """删除本模块前缀的全部数据（FK CASCADE 连带清理 node / approval）。"""
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM ai_ops.workflow_run "
                "WHERE workflow_run_id LIKE :prefix"
            ),
            {"prefix": f"{_RUN_PREFIX}%"},
        )


def _counts(engine: Any) -> dict[str, int]:
    """本模块前缀行计数（三表；node / approval 经 workflow_run_id 过滤）。"""
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
            for table in _OWN_TABLES
        }


def _create_run(
    *,
    status: str = "PENDING",
    project_id: str = "wf-p6s2-project-a",
    workflow_version: str = "v1",
    current_node_id: str | None = None,
    next_action: str = "NONE",
    input: str | None = None,
) -> str:
    """创建一条本模块 run（返回 run_id）。"""
    run_id = _run_id()
    _run_repo().create(
        workflow_run_id=run_id,
        workflow_id="wf_p6s2_flow",
        workflow_version=workflow_version,
        request_id=f"{_RUN_PREFIX}req",
        project_id=project_id,
        input=input,
        status=status,
        current_node_id=current_node_id,
        next_action=next_action,
    )
    return run_id


@pytest.fixture(scope="module")
def engine() -> Any:
    resolved = get_engine()
    if resolved is None:
        pytest.skip("DATABASE_URL 未配置（DB 测试跳过）")
    return resolved


@pytest.fixture(scope="module")
def _ensure_project_id_columns(engine: Any):
    """F-1：旧库（子表缺 project_id 列）在任何测试前幂等补齐（复用 init_db 实现）。

    `Base.metadata.create_all(checkfirst=True)` 对**已存在**的表整体跳过，
    不会补列；本 fixture 让 DB 测试在旧库上自足（不依赖先手工跑 init_db）。
    """
    from backend.app.db.init_db import (
        ensure_workflow_approval_project_id_column,
        ensure_workflow_node_run_project_id_column,
    )

    with engine.begin() as conn:
        ensure_workflow_node_run_project_id_column(conn)
        ensure_workflow_approval_project_id_column(conn)
    yield


@pytest.fixture(scope="module", autouse=True)
def _cleanup(engine: Any, _ensure_project_id_columns: Any):
    """前置 + 后置定向清理（防上次失败残留；不触碰其它数据）。"""
    _delete_prefix(engine)
    yield
    _delete_prefix(engine)


# ============================================================
# 1–4：WorkflowRun CRUD / version / isolation / CAS
# ============================================================


class TestWorkflowRunPersistenceDb:
    def test_create_and_read_roundtrip(self) -> None:
        run_id = _create_run()
        row = _run_repo().get(run_id)
        assert row is not None
        assert row.workflow_run_id == run_id
        assert row.workflow_id == "wf_p6s2_flow"
        assert row.workflow_version == "v1"
        assert row.request_id == f"{_RUN_PREFIX}req"
        assert row.project_id == "wf-p6s2-project-a"
        assert row.status == "PENDING"
        assert row.state_version == 0  # 初始 0（Step 2 §10）
        assert row.current_node_id is None
        assert row.next_action == "NONE"
        assert row.attempt_count == 0
        assert row.cancel_requested is False
        assert row.cancel_requested_at is None
        assert row.started_at is None
        assert row.completed_at is None

    def test_input_roundtrip_and_null_compatibility(self, engine: Any) -> None:
        """GAP-1（D1）：input 写入 → 规范化读回；NULL 兼容（无输入语义）。

        先验证 schema：列存在、nullable、无长度限制（TEXT）。
        """
        with engine.connect() as conn:
            resolved = conn.execute(
                text(
                    "SELECT data_type, is_nullable, character_maximum_length "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'ai_ops' "
                    "AND table_name = 'workflow_run' "
                    "AND column_name = 'input'"
                )
            ).one_or_none()
        assert resolved is not None, "workflow_run.input 列不存在（GAP-1 未生效）"
        assert resolved[0] == "text"
        assert resolved[1] == "YES"  # nullable
        assert resolved[2] is None  # 无长度限制

        run_id = _create_run(input='{"b": 1, "a": [2]}')
        row = _run_repo().get(run_id)
        assert row is not None
        assert row.input == '{"b":1,"a":[2]}'  # 域层规范化产物（紧凑文本）

        plain_id = _create_run()
        plain = _run_repo().get(plain_id)
        assert plain is not None
        assert plain.input is None  # None = 无输入（D1；历史行语义一致）

    def test_duplicate_create_rejected(self) -> None:
        run_id = _create_run()
        with pytest.raises(WorkflowRunAlreadyExistsError):
            _run_repo().create(
                workflow_run_id=run_id,
                workflow_id="wf_p6s2_flow",
                workflow_version="v1",
                request_id=f"{_RUN_PREFIX}req",
                project_id="wf-p6s2-project-a",
            )

    def test_get_missing_returns_none(self) -> None:
        assert _run_repo().get(f"{_RUN_PREFIX}missing") is None

    def test_update_cas_success_increments_version(self) -> None:
        run_id = _create_run()
        row = _run_repo().update_cas(
            workflow_run_id=run_id,
            expected_state_version=0,
            status="RUNNING",
            current_node_id="step_one",
            next_action="NODE_EXECUTION",
            attempt_count=1,
        )
        assert row.state_version == 1
        assert row.status == "RUNNING"
        assert row.current_node_id == "step_one"
        assert row.next_action == "NODE_EXECUTION"
        assert row.attempt_count == 1
        # 首次进入 RUNNING ⇒ started_at 只写一次
        assert row.started_at is not None
        first_started = row.started_at

        row2 = _run_repo().update_cas(
            workflow_run_id=run_id,
            expected_state_version=1,
            status="RUNNING",
            current_node_id="step_two",
            next_action="NODE_EXECUTION",
            attempt_count=2,
        )
        assert row2.state_version == 2
        assert row2.started_at == first_started  # COALESCE：不重写

    def test_update_cas_stale_version_conflicts(self) -> None:
        run_id = _create_run()
        _run_repo().update_cas(
            workflow_run_id=run_id,
            expected_state_version=0,
            status="RUNNING",
            current_node_id=None,
            next_action="NODE_EXECUTION",
            attempt_count=0,
        )
        # 过期 expected（旧版本再提交）⇒ conflict；数据库保持不动
        with pytest.raises(WorkflowRunConflictError) as excinfo:
            _run_repo().update_cas(
                workflow_run_id=run_id,
                expected_state_version=0,
                status="RUNNING",
                current_node_id=None,
                next_action="NODE_EXECUTION",
                attempt_count=0,
            )
        assert excinfo.value.reason == "version"
        current = _run_repo().get(run_id)
        assert current is not None and current.state_version == 1

    def test_update_cas_missing_run_not_found(self) -> None:
        with pytest.raises(WorkflowRunNotFoundError):
            _run_repo().update_cas(
                workflow_run_id=f"{_RUN_PREFIX}missing",
                expected_state_version=0,
                status="RUNNING",
                current_node_id=None,
                next_action="NODE_EXECUTION",
                attempt_count=0,
            )

    def test_version_persisted_and_run_id_distinct_from_request(self) -> None:
        """§24.2：v1 不丢失；workflow_run_id ≠ request_id（语义严格区分）。"""
        run_id = _create_run(workflow_version="v1")
        row = _run_repo().get(run_id)
        assert row is not None
        assert row.workflow_version == "v1"
        assert row.request_id != row.workflow_run_id
        assert row.request_id == f"{_RUN_PREFIX}req"

    def test_project_isolation(self) -> None:
        """§24.3：project-A / project-B 数据不交叉。"""
        run_a = _create_run(project_id="wf-p6s2-proj-a")
        run_b = _create_run(project_id="wf-p6s2-proj-b")
        row_a = _run_repo().get(run_a)
        row_b = _run_repo().get(run_b)
        assert row_a is not None and row_a.project_id == "wf-p6s2-proj-a"
        assert row_b is not None and row_b.project_id == "wf-p6s2-proj-b"
        assert row_a.workflow_run_id != row_b.workflow_run_id

    def test_terminal_completed_at_written(self) -> None:
        run_id = _create_run()
        _run_repo().update_cas(
            workflow_run_id=run_id,
            expected_state_version=0,
            status="RUNNING",
            current_node_id=None,
            next_action="NODE_EXECUTION",
            attempt_count=0,
        )
        row = _run_repo().update_cas(
            workflow_run_id=run_id,
            expected_state_version=1,
            status="COMPLETED",
            current_node_id=None,
            next_action="COMPLETED",
            attempt_count=1,
        )
        assert row.completed_at is not None

    def test_request_cancel_signal(self) -> None:
        run_id = _create_run(status="RUNNING")
        row = _run_repo().request_cancel(run_id)
        assert row.cancel_requested is True
        assert row.cancel_requested_at is not None
        assert row.state_version == 1  # 取消与推进共用版本域
        # 重复取消 ⇒ conflict（already_requested；不做 force update）
        with pytest.raises(WorkflowRunConflictError) as excinfo:
            _run_repo().request_cancel(run_id)
        assert excinfo.value.reason == "already_requested"

    def test_request_cancel_terminal_is_conflict(self) -> None:
        run_id = _create_run()
        _run_repo().update_cas(
            workflow_run_id=run_id,
            expected_state_version=0,
            status="COMPLETED",
            current_node_id=None,
            next_action="COMPLETED",
            attempt_count=0,
        )
        with pytest.raises(WorkflowRunConflictError) as excinfo:
            _run_repo().request_cancel(run_id)
        assert excinfo.value.reason == "terminal"

    def test_request_cancel_missing_run(self) -> None:
        with pytest.raises(WorkflowRunNotFoundError):
            _run_repo().request_cancel(f"{_RUN_PREFIX}missing")


# ============================================================
# 5 / 9：Node attempt / FK integrity
# ============================================================


class TestWorkflowNodeRunPersistenceDb:
    def test_attempt_rows_retained(self) -> None:
        """§24.5：attempt=1 FAILED 与 attempt=2 SUCCEEDED **共存**（不改写历史）。"""
        run_id = _create_run()
        node = _node_repo()
        node.create(
            workflow_run_id=run_id, node_id="check", attempt=1, node_type="TOOL"
        )
        node.update(
            workflow_run_id=run_id,
            node_id="check",
            attempt=1,
            status="FAILED",
            error_code="WORKFLOW_NODE_EXECUTION_FAILURE",
        )
        node.create(
            workflow_run_id=run_id, node_id="check", attempt=2, node_type="TOOL"
        )
        row2 = node.update(
            workflow_run_id=run_id,
            node_id="check",
            attempt=2,
            status="SUCCEEDED",
            output='{"qty": 100}',
        )
        assert row2.status == "SUCCEEDED"

        attempt1 = node.get(workflow_run_id=run_id, node_id="check", attempt=1)
        assert attempt1 is not None and attempt1.status == "FAILED"  # 历史保留
        attempt2 = node.get(workflow_run_id=run_id, node_id="check", attempt=2)
        assert attempt2 is not None and attempt2.status == "SUCCEEDED"

    def test_duplicate_identity_rejected(self) -> None:
        run_id = _create_run()
        _node_repo().create(
            workflow_run_id=run_id, node_id="n1", attempt=1, node_type="AI"
        )
        with pytest.raises(WorkflowNodeRunAlreadyExistsError):
            _node_repo().create(
                workflow_run_id=run_id, node_id="n1", attempt=1, node_type="AI"
            )

    def test_terminal_attempt_immutable(self) -> None:
        run_id = _create_run()
        _node_repo().create(
            workflow_run_id=run_id, node_id="n1", attempt=1, node_type="AI"
        )
        _node_repo().update(
            workflow_run_id=run_id,
            node_id="n1",
            attempt=1,
            status="SUCCEEDED",
            output='{"ok": true}',
        )
        with pytest.raises(WorkflowNodeRunConflictError):
            _node_repo().update(
                workflow_run_id=run_id,
                node_id="n1",
                attempt=1,
                status="FAILED",
                error_code="WORKFLOW_NODE_EXECUTION_FAILURE",
            )

    def test_update_missing_attempt(self) -> None:
        run_id = _create_run()
        with pytest.raises(WorkflowNodeRunNotFoundError):
            _node_repo().update(
                workflow_run_id=run_id,
                node_id="ghost",
                attempt=1,
                status="SUCCEEDED",
            )

    def test_get_latest_attempt_and_list_order(self) -> None:
        run_id = _create_run()
        node = _node_repo()
        node.create(
            workflow_run_id=run_id, node_id="a", attempt=1, node_type="AI"
        )
        node.create(
            workflow_run_id=run_id, node_id="a", attempt=2, node_type="AI"
        )
        node.create(
            workflow_run_id=run_id, node_id="b", attempt=1, node_type="TOOL"
        )
        latest = node.get_latest_attempt(workflow_run_id=run_id, node_id="a")
        assert latest is not None and latest.attempt == 2
        rows = node.list_for_run(run_id)
        assert [(r.node_id, r.attempt) for r in rows] == [
            ("a", 1),
            ("a", 2),
            ("b", 1),
        ]  # id ASC = 写入序

    def test_fk_rejects_unknown_run(self) -> None:
        """§24.9：非法引用 ⇒ DB integrity 错误（归一为 RepositoryError）。"""
        with pytest.raises(WorkflowNodeRunRepositoryError):
            _node_repo().create(
                workflow_run_id=f"{_RUN_PREFIX}missing",
                node_id="n1",
                attempt=1,
                node_type="AI",
            )

    def test_delete_run_cascades_node_rows(self, engine: Any) -> None:
        """§24.9：删除 run ⇒ FK CASCADE 连带删除其 node 行。"""
        run_id = _create_run()
        _node_repo().create(
            workflow_run_id=run_id, node_id="n1", attempt=1, node_type="AI"
        )
        _run_repo().delete_for_test(run_id)
        assert (
            _node_repo().get(workflow_run_id=run_id, node_id="n1", attempt=1)
            is None
        )


# ============================================================
# 6–9：Approval lifecycle / race / state / FK
# ============================================================


class TestWorkflowApprovalPersistenceDb:
    def test_create_pending_has_no_resolved_at(self) -> None:
        """§24.6 / §24.8：PENDING 无 resolved_at。"""
        run_id = _create_run()
        row = _approval_repo().create(
            approval_id=f"{run_id}-ap-1",
            workflow_run_id=run_id,
            node_id="approve_step",
        )
        assert row.status == "PENDING"
        assert row.resolved_at is None
        assert row.created_at is not None

    def test_create_idempotent_same_row(self) -> None:
        run_id = _create_run()
        first = _approval_repo().create(
            approval_id=f"{run_id}-ap-1",
            workflow_run_id=run_id,
            node_id="approve_step",
        )
        second = _approval_repo().create(
            approval_id=f"{run_id}-ap-2",  # 不同 approval_id 同 node ⇒ 幂等返回既有
            workflow_run_id=run_id,
            node_id="approve_step",
        )
        assert second.approval_id == first.approval_id
        assert second.status == "PENDING"

    def test_resolve_approved(self) -> None:
        run_id = _create_run()
        approval_id = f"{run_id}-ap-approved"
        _approval_repo().create(
            approval_id=approval_id,
            workflow_run_id=run_id,
            node_id="approve_step",
        )
        row = _approval_repo().resolve(approval_id=approval_id, decision="APPROVED")
        assert row is not None
        assert row.status == "APPROVED"
        assert row.resolved_at is not None

    def test_resolve_rejected(self) -> None:
        run_id = _create_run()
        approval_id = f"{run_id}-ap-rejected"
        _approval_repo().create(
            approval_id=approval_id,
            workflow_run_id=run_id,
            node_id="approve_step",
        )
        row = _approval_repo().resolve(approval_id=approval_id, decision="REJECTED")
        assert row is not None
        assert row.status == "REJECTED"
        assert row.resolved_at is not None

    def test_single_winner_race(self) -> None:
        """§24.7：resolver A / B 只有一个成功（第二个 ⇒ 无转换 / None）。"""
        run_id = _create_run()
        approval_id = f"{run_id}-ap-race"
        _approval_repo().create(
            approval_id=approval_id,
            workflow_run_id=run_id,
            node_id="approve_step",
        )
        winner = _approval_repo().resolve(
            approval_id=approval_id, decision="APPROVED"
        )
        loser = _approval_repo().resolve(
            approval_id=approval_id, decision="REJECTED"
        )
        assert winner is not None and winner.status == "APPROVED"
        assert loser is None  # 条件 UPDATE 不曾命中（PENDING 已被赢家消费）
        final = _approval_repo().get(approval_id)
        assert final is not None and final.status == "APPROVED"  # 终态不可逆

    def test_repeated_same_decision_returns_none(self) -> None:
        """幂等：同一裁决重复提交 ⇒ 无转换（None；由上层判定幂等返回既有态）。"""
        run_id = _create_run()
        approval_id = f"{run_id}-ap-repeat"
        _approval_repo().create(
            approval_id=approval_id,
            workflow_run_id=run_id,
            node_id="approve_step",
        )
        assert (
            _approval_repo().resolve(approval_id=approval_id, decision="APPROVED")
            is not None
        )
        assert (
            _approval_repo().resolve(approval_id=approval_id, decision="APPROVED")
            is None
        )

    def test_resolve_missing_approval(self) -> None:
        assert (
            _approval_repo().resolve(
                approval_id=f"{_RUN_PREFIX}missing", decision="APPROVED"
            )
            is None
        )

    def test_get_for_node(self) -> None:
        run_id = _create_run()
        approval_id = f"{run_id}-ap-node"
        _approval_repo().create(
            approval_id=approval_id,
            workflow_run_id=run_id,
            node_id="approve_step",
        )
        row = _approval_repo().get_for_node(
            workflow_run_id=run_id, node_id="approve_step"
        )
        assert row is not None and row.approval_id == approval_id
        assert _approval_repo().get(approval_id) is not None

    def test_fk_rejects_unknown_run(self) -> None:
        with pytest.raises(Exception):
            _approval_repo().create(
                approval_id="wf-p6s2-ap-fk-bad",
                workflow_run_id=f"{_RUN_PREFIX}missing",
                node_id="approve_step",
            )

    def test_delete_run_cascades_approval_rows(self, engine: Any) -> None:
        run_id = _create_run()
        approval_id = f"{run_id}-ap-cascade"
        _approval_repo().create(
            approval_id=approval_id,
            workflow_run_id=run_id,
            node_id="approve_step",
        )
        _run_repo().delete_for_test(run_id)
        assert _approval_repo().get(approval_id) is None


# ============================================================
# 10–11：init_db 幂等 / residue
# ============================================================


class TestWorkflowInitAndResidueDb:
    def test_init_db_is_idempotent(self) -> None:
        """§24.10：init_db() 连续两次均成功（create_all + 幂等 DDL 不报错）。"""
        from backend.app.db.init_db import init_db

        init_db()
        init_db()

    def test_schema_present(self, engine: Any) -> None:
        """§24.1（schema 侧）：三表存在且关键唯一约束生效。"""
        from sqlalchemy import inspect

        insp = inspect(engine)
        for table in _OWN_TABLES:
            assert insp.has_table(table, schema="ai_ops")
        run_indexes = {
            index["name"]
            for index in insp.get_indexes("workflow_run", schema="ai_ops")
        }
        assert "uq_workflow_run_workflow_run_id" in run_indexes
        fks = insp.get_foreign_keys("workflow_node_run", schema="ai_ops")
        assert any(
            fk["referred_table"] == "workflow_run" for fk in fks
        )

    def test_residue_is_zero_after_cleanup(self, engine: Any) -> None:
        """§24.11：清理后本模块三表行数 = 0（不污染其它测试 / 其它数据）。"""
        _delete_prefix(engine)
        assert _counts(engine) == {
            "workflow_run": 0,
            "workflow_node_run": 0,
            "workflow_approval": 0,
        }


# ============================================================
# 12：F-1 —— 子表 project_id 冗余列（继承 / 隔离 / 旧库回填）
# ============================================================


class TestWorkflowChildProjectIdAlignmentDb:
    """F-1（ADR §16 I1/I4）：子表 project_id 从 workflow_run 解析复制。"""

    def test_node_run_inherits_project_from_run(self) -> None:
        """node_run.project_id = 所属 run 的 project_id（不来自调用方）。"""
        run_id = _create_run(project_id="wf-p6s2-project-a")
        row = _node_repo().create(
            workflow_run_id=run_id, node_id="n1", attempt=1, node_type="AI"
        )

        assert row.project_id == "wf-p6s2-project-a"

    def test_approval_inherits_project_from_run(self) -> None:
        """approval.project_id = 所属 run 的 project_id（不来自调用方）。"""
        run_id = _create_run(project_id="wf-p6s2-project-b")
        row = _approval_repo().create(
            approval_id=f"{run_id}-ap-p",
            workflow_run_id=run_id,
            node_id="approve_step",
        )

        assert row.project_id == "wf-p6s2-project-b"

    def test_children_follow_their_own_run_project(self) -> None:
        """隔离：不同 project 的 run 的 child 各自继承自己项目（不越界）。"""
        run_a = _create_run(project_id="wf-p6s2-project-a")
        run_b = _create_run(project_id="wf-p6s2-project-b")
        node_a = _node_repo().create(
            workflow_run_id=run_a, node_id="n1", attempt=1, node_type="AI"
        )
        node_b = _node_repo().create(
            workflow_run_id=run_b, node_id="n1", attempt=1, node_type="AI"
        )

        assert node_a.project_id == "wf-p6s2-project-a"
        assert node_b.project_id == "wf-p6s2-project-b"
        rows_a = _node_repo().list_for_run(run_a)
        assert {r.project_id for r in rows_a} == {"wf-p6s2-project-a"}

    def test_legacy_children_are_backfilled_by_init_db(
        self, engine: Any
    ) -> None:
        """旧库模拟：drop 列 → init_db() 幂等补列 + 从父行回填 + NOT NULL。

        只操作本模块 ``wf-p6s2-`` 前缀数据的 schema；不删表 / 不清数据；
        finally 再跑一次 init_db() 恢复列（幂等）。
        """
        from sqlalchemy import inspect as sa_inspect

        from backend.app.db.init_db import init_db

        run_id = _create_run(project_id="wf-p6s2-project-legacy")
        node_row = _node_repo().create(
            workflow_run_id=run_id,
            node_id="n-legacy",
            attempt=1,
            node_type="AI",
        )
        approval_row = _approval_repo().create(
            approval_id=f"{run_id}-ap-legacy",
            workflow_run_id=run_id,
            node_id="approve_legacy",
        )
        assert node_row.project_id == "wf-p6s2-project-legacy"
        assert approval_row.project_id == "wf-p6s2-project-legacy"

        try:
            # 模拟旧 schema（列缺失；行保留）
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE ai_ops.workflow_node_run "
                        "DROP COLUMN IF EXISTS project_id"
                    )
                )
                conn.execute(
                    text(
                        "ALTER TABLE ai_ops.workflow_approval "
                        "DROP COLUMN IF EXISTS project_id"
                    )
                )
            insp = sa_inspect(engine)
            for table in ("workflow_node_run", "workflow_approval"):
                names = {
                    col["name"]
                    for col in insp.get_columns(table, schema="ai_ops")
                }
                assert "project_id" not in names

            # 幂等补列 + 从父行回填 + SET NOT NULL
            init_db()

            insp = sa_inspect(engine)
            for table in ("workflow_node_run", "workflow_approval"):
                cols = {
                    col["name"]: col
                    for col in insp.get_columns(table, schema="ai_ops")
                }
                assert cols["project_id"]["nullable"] is False
            node_back = _node_repo().get(
                workflow_run_id=run_id, node_id="n-legacy", attempt=1
            )
            approval_back = _approval_repo().get(
                approval_id=f"{run_id}-ap-legacy"
            )
            assert node_back is not None
            assert node_back.project_id == "wf-p6s2-project-legacy"
            assert approval_back is not None
            assert approval_back.project_id == "wf-p6s2-project-legacy"
        finally:
            init_db()  # 无论成败：恢复列（幂等；不清数据）
