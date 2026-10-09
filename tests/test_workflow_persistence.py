"""Phase 6 Step 2 —— Workflow Persistence 离线测试（**全离线**，不触 DB）。

覆盖（Step 2 §6–§20；不连数据库即可验证的部分）：

```text
Test 1  ORM 元数据：三表列集合 / UNIQUE 索引 / FK(CASCADE) / nullability
Test 2  校验函数：state_version / status / next_action / attempt / node_type /
        node_status / output(JSON-safe 边界) / error_code / elapsed_ms / decision
Test 3  Repository 参数校验（触达 DB **之前**拦截 ⇒ ValueError）
Test 4  DATABASE_URL 未配置 ⇒ RepositoryError（monkeypatch 工厂；fail-closed）
Test 5  错误类语义（稳定码 / reason / 继承链）
Test 6  边界：Workflow 表与 agent 表**不同名不同类**（不共享语义模型）
```

DB 侧（CRUD / CAS / FK / residue / init_db 幂等）在
``tests/test_workflow_persistence_db.py``（RUN_DB_TESTS 门控）。
"""
from __future__ import annotations

import pytest

from backend.app.db.base import Base
from backend.app.db.models import (
    WorkflowApproval,
    WorkflowNodeRun,
    WorkflowRun,
    get_all_models,
)
from backend.app.db.models.workflow_approval import (
    WORKFLOW_APPROVAL_PROJECT_ID_MAX_LENGTH,
    WORKFLOW_APPROVAL_STATUSES,
)
from backend.app.db.models.workflow_node_run import (
    WORKFLOW_NODE_RUN_PROJECT_ID_MAX_LENGTH,
    WORKFLOW_NODE_RUN_STATUSES,
    WORKFLOW_NODE_RUN_TERMINAL_STATUSES,
    WORKFLOW_NODE_RUN_TYPES,
)
from backend.app.db.models.workflow_run import (
    WORKFLOW_NEXT_ACTIONS,
    WORKFLOW_RUN_INITIAL_STATE_VERSION,
    WORKFLOW_RUN_PROJECT_ID_MAX_LENGTH,
    WORKFLOW_RUN_STATUSES,
    WORKFLOW_RUN_TERMINAL_STATUSES,
)
from backend.app.db.workflow_approval_repository import (
    WorkflowApprovalRepository,
    validate_approval_decision,
)
from backend.app.db.workflow_node_run_repository import (
    WORKFLOW_NODE_RUN_ALREADY_EXISTS,
    WORKFLOW_NODE_RUN_NOT_FOUND,
    WorkflowNodeRunAlreadyExistsError,
    WorkflowNodeRunConflictError,
    WorkflowNodeRunNotFoundError,
    WorkflowNodeRunRepository,
    WorkflowNodeRunRepositoryError,
    validate_attempt,
    validate_elapsed_ms,
    validate_error_code,
    validate_node_status,
    validate_node_type,
    validate_output,
)
from backend.app.db.workflow_run_repository import (
    WORKFLOW_RUN_ALREADY_EXISTS,
    WorkflowRunAlreadyExistsError,
    WorkflowRunConflictError,
    WorkflowRunNotFoundError,
    WorkflowRunRepository,
    WorkflowRunRepositoryError,
    validate_attempt_count,
    validate_next_action,
    validate_run_status,
    validate_state_version,
)
from backend.app.workflow.errors import (
    WF_ERROR_NODE_EXECUTION_FAILURE,
    WF_ERROR_RUN_NOT_FOUND,
    WF_ERROR_STATE_CONFLICT,
)
from backend.app.workflow.models import JSON_VALUE_MAX_BYTES

__all__ = [
    "TestOrmMetadata",
    "TestValidators",
    "TestRepositoryInputValidation",
    "TestUnconfiguredDatabase",
    "TestErrorClasses",
    "TestNamespaceIsolation",
]

_RUN_TABLE = Base.metadata.tables["ai_ops.workflow_run"]
_NODE_RUN_TABLE = Base.metadata.tables["ai_ops.workflow_node_run"]
_APPROVAL_TABLE = Base.metadata.tables["ai_ops.workflow_approval"]


class TestOrmMetadata:
    """Test 1：ORM 元数据（离线可查）。"""

    def test_run_columns_exact(self) -> None:
        assert set(_RUN_TABLE.columns.keys()) == {
            "id",
            "workflow_run_id",
            "workflow_id",
            "workflow_version",
            "request_id",
            "project_id",
            "status",
            "state_version",
            "current_node_id",
            "next_action",
            "attempt_count",
            "cancel_requested",
            "cancel_requested_at",
            "created_at",
            "started_at",
            "completed_at",
            "updated_at",
        }

    def test_node_run_columns_exact(self) -> None:
        assert set(_NODE_RUN_TABLE.columns.keys()) == {
            "id",
            "workflow_run_id",
            "project_id",
            "node_id",
            "attempt",
            "node_type",
            "status",
            "output",
            "error_code",
            "elapsed_ms",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        }

    def test_approval_columns_exact(self) -> None:
        assert set(_APPROVAL_TABLE.columns.keys()) == {
            "id",
            "approval_id",
            "workflow_run_id",
            "project_id",
            "node_id",
            "status",
            "resolved_at",
            "created_at",
            "updated_at",
        }

    def test_key_nullability(self) -> None:
        # project_id NOT NULL（Step 2 §9；F-1：子表同样 NOT NULL）
        assert _RUN_TABLE.columns["project_id"].nullable is False
        assert _NODE_RUN_TABLE.columns["project_id"].nullable is False
        assert _APPROVAL_TABLE.columns["project_id"].nullable is False
        assert _RUN_TABLE.columns["workflow_version"].nullable is False
        # run 控制状态指针与时间戳允许 NULL
        assert _RUN_TABLE.columns["current_node_id"].nullable is True
        assert _RUN_TABLE.columns["started_at"].nullable is True
        assert _RUN_TABLE.columns["completed_at"].nullable is True
        # approval：resolved_at 允许 NULL（PENDING）
        assert _APPROVAL_TABLE.columns["resolved_at"].nullable is True

    def test_child_project_id_matches_run_definition(self) -> None:
        """F-1（ADR §16 I1/I4）：子表 project_id 类型 / 长度 / 非空与 run 一致。"""
        run_col = _RUN_TABLE.columns["project_id"]

        assert run_col.type.length == WORKFLOW_RUN_PROJECT_ID_MAX_LENGTH == 128
        for table in (_NODE_RUN_TABLE, _APPROVAL_TABLE):
            col = table.columns["project_id"]
            assert col.type.length == run_col.type.length
            assert col.nullable is False
        assert WORKFLOW_NODE_RUN_PROJECT_ID_MAX_LENGTH == 128
        assert WORKFLOW_APPROVAL_PROJECT_ID_MAX_LENGTH == 128

    def test_unique_indexes(self) -> None:
        run_indexes = {index.name: index.unique for index in _RUN_TABLE.indexes}
        assert run_indexes.get("uq_workflow_run_workflow_run_id") is True

        node_indexes = {index.name: index.unique for index in _NODE_RUN_TABLE.indexes}
        identity = node_indexes.get("uq_workflow_node_run_run_id_node_id_attempt")
        assert identity is True

        approval_indexes = {
            index.name: index.unique for index in _APPROVAL_TABLE.indexes
        }
        assert approval_indexes.get("uq_workflow_approval_approval_id") is True
        assert approval_indexes.get("uq_workflow_approval_run_id_node_id") is True

    def test_run_has_no_foreign_keys(self) -> None:
        """workflow_run 只含 correlation / 服务器端字段 —— **无 FK**。"""
        assert list(_RUN_TABLE.foreign_keys) == []

    @pytest.mark.parametrize("table", [_NODE_RUN_TABLE, _APPROVAL_TABLE])
    def test_child_tables_fk_cascade(self, table: object) -> None:
        """子表域内 FK → workflow_run.workflow_run_id（ON DELETE CASCADE）。"""
        fks = list(table.foreign_keys)  # type: ignore[attr-defined]
        assert len(fks) == 1
        fk = fks[0]
        assert fk.target_fullname == "ai_ops.workflow_run.workflow_run_id"
        assert fk.ondelete == "CASCADE"

    def test_models_registered(self) -> None:
        all_models = get_all_models()
        assert WorkflowRun in all_models
        assert WorkflowNodeRun in all_models
        assert WorkflowApproval in all_models

    def test_whitelist_constants(self) -> None:
        assert WORKFLOW_RUN_STATUSES == {
            "PENDING",
            "RUNNING",
            "WAITING",
            "COMPLETED",
            "FAILED",
            "CANCELLED",
        }
        assert WORKFLOW_RUN_TERMINAL_STATUSES == {
            "COMPLETED",
            "FAILED",
            "CANCELLED",
        }
        assert WORKFLOW_RUN_INITIAL_STATE_VERSION == 0
        assert WORKFLOW_NEXT_ACTIONS == {
            "NONE",
            "NODE_EXECUTION",
            "WAITING_APPROVAL",
            "COMPLETED",
            "FAILED",
            "CANCELLED",
        }
        assert WORKFLOW_NODE_RUN_TYPES == {"AI", "TOOL", "APPROVAL", "CONDITION"}
        assert WORKFLOW_NODE_RUN_STATUSES == {
            "RUNNING",
            "WAITING_APPROVAL",
            "SUCCEEDED",
            "FAILED",
        }
        assert WORKFLOW_NODE_RUN_TERMINAL_STATUSES == {"SUCCEEDED", "FAILED"}
        assert WORKFLOW_APPROVAL_STATUSES == {"PENDING", "APPROVED", "REJECTED"}


class TestValidators:
    """Test 2：校验函数。"""

    def test_state_version(self) -> None:
        assert validate_state_version(0) == 0
        assert validate_state_version(7) == 7
        for bad in (-1, True, False, 1.0, "1", None):
            with pytest.raises(ValueError):
                validate_state_version(bad)  # type: ignore[arg-type]

    def test_run_status(self) -> None:
        assert validate_run_status("RUNNING") == "RUNNING"
        for bad in ("RUNNING ", "running", "BOGUS", "", None, 1):
            with pytest.raises(ValueError):
                validate_run_status(bad)  # type: ignore[arg-type]

    def test_next_action(self) -> None:
        assert validate_next_action("NODE_EXECUTION") == "NODE_EXECUTION"
        for bad in ("LLM_DECISION", "TOOL_EXECUTION", "bogus", None):
            with pytest.raises(ValueError):
                validate_next_action(bad)  # type: ignore[arg-type]

    def test_attempt_count(self) -> None:
        assert validate_attempt_count(0) == 0
        for bad in (-1, True, 1.0, None):
            with pytest.raises(ValueError):
                validate_attempt_count(bad)  # type: ignore[arg-type]

    def test_attempt(self) -> None:
        assert validate_attempt(1) == 1
        for bad in (0, -1, True, 1.0, None):
            with pytest.raises(ValueError):
                validate_attempt(bad)  # type: ignore[arg-type]

    def test_node_type(self) -> None:
        assert validate_node_type("AI") == "AI"
        for bad in ("FUNCTION", "PARALLEL", "ai", None):
            with pytest.raises(ValueError):
                validate_node_type(bad)  # type: ignore[arg-type]

    def test_node_status(self) -> None:
        assert validate_node_status("SUCCEEDED") == "SUCCEEDED"
        for bad in ("OK", "COMPLETED", None):
            with pytest.raises(ValueError):
                validate_node_status(bad)  # type: ignore[arg-type]

    def test_output_json_safe_boundary(self) -> None:
        assert validate_output(None) is None
        assert validate_output('{"a": 1}') == '{"a": 1}'
        assert validate_output("[]") == "[]"
        for bad in ("", "   ", "not-json", "{broken", 123, {"a": 1}, [1]):
            with pytest.raises(ValueError):
                validate_output(bad)  # type: ignore[arg-type]

    def test_output_size_limit(self) -> None:
        oversized = '"' + "a" * (JSON_VALUE_MAX_BYTES + 8) + '"'
        with pytest.raises(ValueError):
            validate_output(oversized)
        # 边界内合法（恰小于上限）
        ok_payload = '"' + "a" * (JSON_VALUE_MAX_BYTES - 16) + '"'
        assert validate_output(ok_payload) == ok_payload

    def test_error_code(self) -> None:
        assert validate_error_code(None) is None
        assert (
            validate_error_code(WF_ERROR_NODE_EXECUTION_FAILURE)
            == WF_ERROR_NODE_EXECUTION_FAILURE
        )
        for bad in ("free text error", "BOGUS", "", 1):
            with pytest.raises(ValueError):
                validate_error_code(bad)  # type: ignore[arg-type]

    def test_elapsed_ms(self) -> None:
        assert validate_elapsed_ms(None) is None
        assert validate_elapsed_ms(12) == 12.0
        assert validate_elapsed_ms(1.5) == 1.5
        for bad in (-1, float("nan"), float("inf"), True, "1"):
            with pytest.raises(ValueError):
                validate_elapsed_ms(bad)  # type: ignore[arg-type]

    def test_approval_decision(self) -> None:
        assert validate_approval_decision("APPROVED") == "APPROVED"
        assert validate_approval_decision("REJECTED") == "REJECTED"
        for bad in ("PENDING", "approved", "retry", "modify", None):
            with pytest.raises(ValueError):
                validate_approval_decision(bad)  # type: ignore[arg-type]


class TestRepositoryInputValidation:
    """Test 3：Repository 参数校验（触达 DB 之前 ⇒ ValueError）。"""

    def test_run_create_rejects_invalid_inputs(self) -> None:
        repo = WorkflowRunRepository()
        base = dict(
            workflow_run_id="r1",
            workflow_id="wf",
            workflow_version="v1",
            request_id="req",
            project_id="proj",
        )
        with pytest.raises(ValueError):
            repo.create(**{**base, "project_id": ""})
        with pytest.raises(ValueError):
            repo.create(**{**base, "workflow_run_id": " "})
        with pytest.raises(ValueError):
            repo.create(**{**base, "status": "COMPLETED"})  # 创建即终态拒绝
        with pytest.raises(ValueError):
            repo.create(**{**base, "status": "WAITING"})  # 非初始态拒绝
        with pytest.raises(ValueError):
            repo.create(**{**base, "next_action": "BOGUS"})
        with pytest.raises(ValueError):
            repo.create(**{**base, "workflow_run_id": "x" * 65})

    def test_run_update_cas_rejects_invalid_inputs(self) -> None:
        repo = WorkflowRunRepository()
        base = dict(
            workflow_run_id="r1",
            expected_state_version=0,
            status="RUNNING",
            current_node_id=None,
            next_action="NODE_EXECUTION",
            attempt_count=0,
        )
        with pytest.raises(ValueError):
            repo.update_cas(**{**base, "expected_state_version": -1})
        with pytest.raises(ValueError):
            repo.update_cas(**{**base, "status": "BOGUS"})
        with pytest.raises(ValueError):
            repo.update_cas(**{**base, "attempt_count": -1})
        with pytest.raises(ValueError):
            repo.update_cas(**{**base, "next_action": "LLM_DECISION"})

    def test_node_run_create_rejects_invalid_inputs(self) -> None:
        repo = WorkflowNodeRunRepository()
        base = dict(
            workflow_run_id="r1",
            node_id="n1",
            attempt=1,
            node_type="AI",
        )
        with pytest.raises(ValueError):
            repo.create(**{**base, "attempt": 0})
        with pytest.raises(ValueError):
            repo.create(**{**base, "node_type": "PARALLEL"})
        with pytest.raises(ValueError):
            repo.create(**{**base, "status": "COMPLETED"})
        with pytest.raises(ValueError):
            repo.create(**{**base, "output": "not-json"})
        with pytest.raises(ValueError):
            repo.create(**{**base, "error_code": "free text"})

    def test_approval_rejects_invalid_decisions(self) -> None:
        repo = WorkflowApprovalRepository()
        with pytest.raises(ValueError):
            repo.resolve(approval_id="a1", decision="PENDING")
        with pytest.raises(ValueError):
            repo.resolve(approval_id="", decision="APPROVED")

    def test_approval_create_rejects_invalid_inputs(self) -> None:
        repo = WorkflowApprovalRepository()
        with pytest.raises(ValueError):
            repo.create(approval_id="", workflow_run_id="r1", node_id="n1")
        with pytest.raises(ValueError):
            repo.create(approval_id="a1", workflow_run_id=" ", node_id="n1")
        with pytest.raises(ValueError):
            repo.create(approval_id="a1", workflow_run_id="r1", node_id="x" * 65)


class TestUnconfiguredDatabase:
    """Test 4：DATABASE_URL 未配置 ⇒ fail-closed RepositoryError。"""

    def test_run_repository_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import backend.app.db.workflow_run_repository as module

        monkeypatch.setattr(module, "get_session_factory", lambda: None)
        with pytest.raises(WorkflowRunRepositoryError):
            WorkflowRunRepository().get("r1")

    def test_node_run_repository_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import backend.app.db.workflow_node_run_repository as module

        monkeypatch.setattr(module, "get_session_factory", lambda: None)
        with pytest.raises(WorkflowNodeRunRepositoryError):
            WorkflowNodeRunRepository().get(
                workflow_run_id="r1", node_id="n1", attempt=1
            )

    def test_approval_repository_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import backend.app.db.workflow_approval_repository as module

        monkeypatch.setattr(module, "get_session_factory", lambda: None)
        with pytest.raises(Exception) as excinfo:
            WorkflowApprovalRepository().get("a1")
        from backend.app.db.workflow_approval_repository import (
            WorkflowApprovalRepositoryError,
        )

        assert isinstance(excinfo.value, WorkflowApprovalRepositoryError)


class TestErrorClasses:
    """Test 5：错误类语义（稳定码 / reason / 继承链）。"""

    def test_run_already_exists_code(self) -> None:
        assert WorkflowRunAlreadyExistsError.code == WORKFLOW_RUN_ALREADY_EXISTS
        assert issubclass(
            WorkflowRunAlreadyExistsError, WorkflowRunRepositoryError
        )

    def test_run_not_found_code(self) -> None:
        assert WorkflowRunNotFoundError.code == WF_ERROR_RUN_NOT_FOUND

    def test_run_conflict_code_and_reason(self) -> None:
        error = WorkflowRunConflictError("boom", reason="version")
        assert WorkflowRunConflictError.code == WF_ERROR_STATE_CONFLICT
        assert error.reason == "version"
        assert isinstance(error, WorkflowRunRepositoryError)

    def test_node_run_codes(self) -> None:
        assert WorkflowNodeRunAlreadyExistsError.code == (
            WORKFLOW_NODE_RUN_ALREADY_EXISTS
        )
        assert WorkflowNodeRunNotFoundError.code == WORKFLOW_NODE_RUN_NOT_FOUND
        conflict = WorkflowNodeRunConflictError("boom")
        assert conflict.reason == "terminal"
        assert WorkflowNodeRunConflictError.code == WF_ERROR_STATE_CONFLICT


class TestNamespaceIsolation:
    """Test 6：与 agent 六表的 namespace 隔离（不共享语义模型）。"""

    def test_tables_are_distinct(self) -> None:
        workflow_tables = {
            "ai_ops.workflow_run",
            "ai_ops.workflow_node_run",
            "ai_ops.workflow_approval",
        }
        agent_tables = {name for name in Base.metadata.tables if "agent_" in name}
        assert workflow_tables & agent_tables == set()

    def test_model_classes_are_distinct(self) -> None:
        from backend.app.db.models import AgentApproval, AgentRun, AgentStep

        assert WorkflowRun is not AgentRun
        assert WorkflowNodeRun is not AgentStep
        assert WorkflowApproval is not AgentApproval

    def test_run_columns_do_not_reuse_agent_names(self) -> None:
        forbidden = {"run_id", "step_index"}
        assert forbidden.isdisjoint(set(_RUN_TABLE.columns.keys()))
        assert forbidden.isdisjoint(set(_NODE_RUN_TABLE.columns.keys()))
        assert forbidden.isdisjoint(set(_APPROVAL_TABLE.columns.keys()))
