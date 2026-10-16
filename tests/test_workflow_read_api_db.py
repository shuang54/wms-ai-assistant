"""Phase 6 Step 7 / 7.2 — S7 Read API **DB-gated** 集成测试（真 PostgreSQL）。

**默认跳过**（沿用项目既有 ``RUN_DB_TESTS`` 门控；专用测试库 = 本地开发库）：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_workflow_read_api_db.py
```

真链（**不 monkeypatch 读取服务**——直接走生产 Composition Root；仅
monkeypatch **认证配置**，因为真实环境凭据不属于测试）：

```text
TestClient → Router → require_workflow_read_scope（X-API-Key）
→ get_workflow_read_service() → read_factory
→ WorkflowReadService → Store Adapters → Repositories → PostgreSQL
```

覆盖（S7 §八B + S7.2 §7.2）：
```text
① Run 详情可读（200）  ② Node attempt 历史可读（顺序）  ③ Approval 详情可读
④ Timeline 可读（真实时间戳）  ⑤ 跨项目被拒绝（404 同构）+ 无凭据 401
⑥ 查询前后状态一致（快照对比）  ⑦ 清理后数据库无残留
```
只写 ``ai_ops.workflow_*`` 三表；定向前缀清理（``wf-p6s7-``）。
"""
from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.api import workflow_read_auth as auth_module
from backend.app.config import WorkflowReadSettings
from backend.app.db.session import get_engine
from backend.app.db.workflow_approval_repository import (
    WorkflowApprovalRepository,
)
from backend.app.db.workflow_node_run_repository import (
    WorkflowNodeRunRepository,
)
from backend.app.db.workflow_run_repository import WorkflowRunRepository
from backend.app.dto.workflow_read_api import (
    WORKFLOW_APPROVAL_RESPONSE_FIELDS,
)
from backend.app.main import app
from backend.app.workflow.errors import WF_ERROR_NODE_EXECUTION_FAILURE
from backend.app.workflow.states import APPROVAL_STATUS_APPROVED

_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

pytestmark = pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable workflow read API PostgreSQL tests",
)

__all__ = [
    "TestReadApiDb",
    "TestReadApiNonMutationDb",
    "TestResidueDb",
]

_RUN_PREFIX = "wf-p6s7-"
_PROJECT = "proj-s7"
_OTHER_PROJECT = "wf-p6s7-other"

#: 测试专用凭据（**非**生产凭据；仅存在于测试进程内 monkeypatch 配置）。
_TEST_API_KEY = "db-suite-api-key-NOT-A-PRODUCTION-CREDENTIAL"
_HEADERS = {"X-API-Key": _TEST_API_KEY}


def _run_id() -> str:
    return f"{_RUN_PREFIX}{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def engine() -> Any:
    eng = get_engine()
    if eng is None:
        pytest.skip("DATABASE_URL 未配置，跳过 Workflow Read API DB 集成测试")
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


def _seed_run(
    *,
    with_approval: bool = False,
    project_id: str = _PROJECT,
) -> tuple[str, str | None]:
    """真实 Repository 写数据（返回 run_id 与可选 approval_id）。"""
    run_id = _run_id()
    WorkflowRunRepository().create(
        workflow_run_id=run_id,
        workflow_id="s7_read_flow",
        workflow_version="v1",
        request_id=f"{_RUN_PREFIX}req",
        project_id=project_id,
        input='{"seed": "read-api"}',
        status="PENDING",
    )
    approval_id: str | None = None
    if with_approval:
        approval_id = f"{run_id}-ap"
        WorkflowApprovalRepository().create(
            approval_id=approval_id, workflow_run_id=run_id, node_id="gate"
        )
    return run_id, approval_id


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """真链客户端：只替换**认证配置**（读取服务走生产装配 → DB）。"""
    monkeypatch.setattr(
        auth_module,
        "get_workflow_read_settings",
        lambda: WorkflowReadSettings(
            api_key=_TEST_API_KEY, api_project_id=_PROJECT
        ),
    )
    return TestClient(app)


# ============================================================
# Test 1：真链读取（Run / Nodes / Approval / Timeline / 跨项目 / 认证）
# ============================================================


class TestReadApiDb:
    def test_run_detail_via_real_chain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id, _ = _seed_run()
        response = _client(monkeypatch).get(
            f"/api/workflow-runs/{run_id}", headers=_HEADERS
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["workflow_run_id"] == run_id
        assert payload["workflow_id"] == "s7_read_flow"
        assert payload["status"] == "PENDING"
        assert payload["project_id"] == _PROJECT
        assert payload["created_at"] is not None
        assert "input" not in payload  # 启动输入永不进入响应

    def test_node_history_via_real_chain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id, _ = _seed_run()
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

        response = _client(monkeypatch).get(
            f"/api/workflow-runs/{run_id}/nodes", headers=_HEADERS
        )
        assert response.status_code == 200
        items = response.json()
        assert [(item["node_id"], item["attempt"]) for item in items] == [
            ("step_one", 1),
            ("step_one", 2),
        ]
        assert items[0]["error_code"] == WF_ERROR_NODE_EXECUTION_FAILURE
        assert items[1]["output"] == '{"qty":3}'

    def test_approval_detail_via_real_chain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id, approval_id = _seed_run(with_approval=True)
        assert approval_id is not None
        client = _client(monkeypatch)

        pending = client.get(
            f"/api/workflow-approvals/{approval_id}", headers=_HEADERS
        )
        assert pending.status_code == 200
        assert pending.json()["status"] == "PENDING"
        assert pending.json()["resolved_at"] is None

        WorkflowApprovalRepository().resolve(
            approval_id=approval_id, decision=APPROVAL_STATUS_APPROVED
        )
        resolved = client.get(
            f"/api/workflow-approvals/{approval_id}", headers=_HEADERS
        )
        assert resolved.status_code == 200
        assert resolved.json()["status"] == APPROVAL_STATUS_APPROVED
        assert resolved.json()["resolved_at"] is not None

    def test_approval_by_node_via_real_chain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Phase 7 新增端点：HTTP → API → Read Service → Adapter → Repository。

        同时验证 ``None``（无审批行）与"不存在 / 跨项目"同构（404 同码同文案），
        以及 PENDING → APPROVED 的真实裁决时间往返。
        """
        run_id, approval_id = _seed_run(with_approval=True)
        assert approval_id is not None
        WorkflowNodeRunRepository().create(
            workflow_run_id=run_id,
            node_id="gate",
            attempt=1,
            node_type="APPROVAL",
            status="WAITING_APPROVAL",
        )
        client = _client(monkeypatch)

        pending = client.get(
            f"/api/workflow-runs/{run_id}/approvals/gate", headers=_HEADERS
        )
        assert pending.status_code == 200
        payload = pending.json()
        assert set(payload.keys()) == set(WORKFLOW_APPROVAL_RESPONSE_FIELDS)
        assert payload["approval_id"] == approval_id
        assert payload["workflow_run_id"] == run_id
        assert payload["node_id"] == "gate"
        assert payload["status"] == "PENDING"
        assert payload["resolved_at"] is None       # 未裁决 ⇒ null
        assert "created_at" not in payload          # Port 未暴露 ⇒ 不补造

        # 无审批行的节点 ⇒ 404（与"不存在"同码同文案）
        absent = client.get(
            f"/api/workflow-runs/{run_id}/approvals/never-created",
            headers=_HEADERS,
        )
        missing_run = client.get(
            "/api/workflow-runs/wf-p6s7-missing/approvals/gate",
            headers=_HEADERS,
        )
        assert absent.status_code == missing_run.status_code == 404
        assert absent.json() == missing_run.json()

        WorkflowApprovalRepository().resolve(
            approval_id=approval_id, decision=APPROVAL_STATUS_APPROVED
        )
        resolved = client.get(
            f"/api/workflow-runs/{run_id}/approvals/gate", headers=_HEADERS
        )
        assert resolved.status_code == 200
        assert resolved.json()["status"] == APPROVAL_STATUS_APPROVED
        assert resolved.json()["resolved_at"] is not None   # 真实裁决时间

    def test_timeline_via_real_chain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id, _ = _seed_run()
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

        response = _client(monkeypatch).get(
            f"/api/workflow-runs/{run_id}/timeline", headers=_HEADERS
        )
        assert response.status_code == 200
        kinds = [item["kind"] for item in response.json()]
        assert kinds == ["RUN_CREATED", "RUN_STARTED", "RUN_FINISHED"]

    def test_cross_project_rejected_isomorphic(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """授权作用域 = _PROJECT：其他项目资源 ⇒ 404（与不存在同构）。"""
        other_id, other_approval = _seed_run(
            with_approval=True, project_id=_OTHER_PROJECT
        )
        client = _client(monkeypatch)

        outside_scope = client.get(
            f"/api/workflow-runs/{other_id}", headers=_HEADERS
        )
        missing = client.get(
            "/api/workflow-runs/wf-p6s7-missing", headers=_HEADERS
        )
        assert outside_scope.status_code == missing.status_code == 404
        assert outside_scope.json() == missing.json()  # 同码同文案

        nodes = client.get(
            f"/api/workflow-runs/{other_id}/nodes", headers=_HEADERS
        )
        timeline = client.get(
            f"/api/workflow-runs/{other_id}/timeline", headers=_HEADERS
        )
        assert nodes.status_code == 404
        assert timeline.status_code == 404

        approval = client.get(
            f"/api/workflow-approvals/{other_approval}", headers=_HEADERS
        )
        assert approval.status_code == 404

        # Phase 7 新增端点同样不得跨项目读取：
        # "跨项目 Run 的审批" 必须与 "Run 不存在" **同码同文案**
        # （比同一端点内的两个 404 —— 不泄漏 Run / 审批是否存在）
        approval_by_node = client.get(
            f"/api/workflow-runs/{other_id}/approvals/gate", headers=_HEADERS
        )
        approval_missing_run = client.get(
            "/api/workflow-runs/wf-p6s7-missing/approvals/gate",
            headers=_HEADERS,
        )
        assert approval_by_node.status_code == 404
        assert approval_by_node.json() == approval_missing_run.json()

    def test_missing_credential_rejected_on_real_chain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id, _ = _seed_run()
        client = _client(monkeypatch)
        response = client.get(f"/api/workflow-runs/{run_id}")  # 无凭据
        assert response.status_code == 401
        assert "input" not in response.text


# ============================================================
# Test 2：查询不改状态（快照对比）
# ============================================================


class TestReadApiNonMutationDb:
    def test_http_reads_do_not_change_persisted_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id, approval_id = _seed_run(with_approval=True)
        node_repo = WorkflowNodeRunRepository()
        node_repo.create(
            workflow_run_id=run_id,
            node_id="gate",
            attempt=1,
            node_type="APPROVAL",
            status="WAITING_APPROVAL",
        )

        run_before = WorkflowRunRepository().get(run_id)
        node_before = node_repo.get(
            workflow_run_id=run_id, node_id="gate", attempt=1
        )
        approval_before = WorkflowApprovalRepository().get(approval_id)
        assert run_before is not None and node_before is not None
        assert approval_before is not None

        client = _client(monkeypatch)
        for url in (
            f"/api/workflow-runs/{run_id}",
            f"/api/workflow-runs/{run_id}/nodes",
            f"/api/workflow-runs/{run_id}/timeline",
            f"/api/workflow-approvals/{approval_id}",
        ):
            response = client.get(url, headers=_HEADERS)
            assert response.status_code == 200, url

        run_after = WorkflowRunRepository().get(run_id)
        node_after = node_repo.get(
            workflow_run_id=run_id, node_id="gate", attempt=1
        )
        approval_after = WorkflowApprovalRepository().get(approval_id)
        assert run_after is not None and node_after is not None
        assert approval_after is not None
        assert run_after.state_version == run_before.state_version
        assert run_after.status == run_before.status
        assert run_after.updated_at == run_before.updated_at
        assert node_after.status == node_before.status
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
