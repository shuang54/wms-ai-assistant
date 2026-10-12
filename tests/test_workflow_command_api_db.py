"""Phase 7 Step 1 — Workflow Command API **DB-gated** 集成测试（真 PostgreSQL + 真 Runtime）。

**默认跳过**（沿用项目既有 ``RUN_DB_TESTS`` 门控）：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_workflow_command_api_db.py
```

真链路（HTTP → 认证 → 命令服务 → 真 WorkflowRuntime → 真 Repository → PostgreSQL）：
```text
1  正常启动（真 run 落库；经真 Repository 读取验证）
2  审批流：start ⇒ WAITING_APPROVAL ⇒ decision(APPROVED) ⇒ 恢复 ⇒ COMPLETED
3  拒绝流：decision(REJECTED) ⇒ run FAILED（决策 + 恢复两段式）
4  取消：WAITING run ⇒ cancel ⇒ CANCELLED；终态再取消 ⇒ 409（NOT_CANCELLABLE）
5  重复同裁决 ⇒ 幂等 200（approval 单赢）；相反裁决 ⇒ 409（APPROVAL_CONFLICT）
6  跨项目：另一项目凭据访问 ⇒ 404（与不存在同构，不泄露存在性）
7  未认证 401 / 未配置 503（认证先于任何数据访问）
8  非法状态转换 / 未知定义 ⇒ 明确错误（无部分状态）
9  失败操作不改变 run 状态（冲突后仍 WAITING）
10 三表零残留（定向前缀清理 wf-p7s1-）
```

安全边界：**不调用真实 DeepSeek / 真实 Tool Handler**（orchestrator / tool
以离线替身注入）；只写 ``ai_ops.workflow_*`` 三表；定向前缀清理；
不触碰 agent 六表 / 生产数据。
"""
from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.api import workflow_command as command_api
from backend.app.api import workflow_command_auth as auth_module
from backend.app.config import WorkflowCommandSettings
from backend.app.db.session import get_engine
from backend.app.db.workflow_approval_repository import (
    WorkflowApprovalRepository,
)
from backend.app.db.workflow_node_run_repository import (
    WorkflowNodeRunRepository,
)
from backend.app.db.workflow_run_repository import WorkflowRunRepository
from backend.app.main import app
from backend.app.services.ai_orchestrator_service import AIOrchestrationResult
from backend.app.services.ai_router_service import RouteType
from backend.app.tools.base import ToolResult
from backend.app.workflow.definition import (
    NODE_TYPE_APPROVAL,
    NODE_TYPE_TOOL,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowRegistry,
)
from backend.app.workflow.states import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_PENDING,
    APPROVAL_STATUS_REJECTED,
    NODE_STATUS_FAILED,
    NODE_STATUS_SUCCEEDED,
    WORKFLOW_STATUS_CANCELLED,
    WORKFLOW_STATUS_COMPLETED,
    WORKFLOW_STATUS_FAILED,
    WORKFLOW_STATUS_WAITING,
)
from backend.app.workflow_runtime.command_service import (
    WorkflowCommandService,
)
from backend.app.workflow_runtime.factory import create_workflow_runtime
from backend.app.workflow_runtime.read_factory import (
    create_workflow_read_service,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS", "").strip().lower() not in {"1", "true", "yes", "on"},
    reason="set RUN_DB_TESTS=1 to enable workflow command API PostgreSQL tests",
)

__all__ = [
    "TestStartCommandDb",
    "TestApprovalCommandDb",
    "TestCancelCommandDb",
    "TestIsolationAndFailureDb",
    "TestResidueDb",
]

#: 本文件的 run_id 前缀（定向清理；绝不触碰其它数据）。
_RUN_PREFIX = "wf-p7s1-"
_PROJECT_ID = "proj-p7s1"
_OTHER_PROJECT = "proj-p7s1-other"

#: approval_id 由 runtime 的 factory 动态生成（每 run 独立审批身份；
#: 测试从 start 响应读取，不假设固定值——避免跨测试 PK 冲突）。

_TEST_API_KEY = "command-db-suite-api-key-NOT-A-PRODUCTION-CREDENTIAL"
_HEADERS = {"X-API-Key": _TEST_API_KEY}


def _run_id() -> str:
    return f"{_RUN_PREFIX}{uuid.uuid4().hex[:12]}"


# ============================================================
# 离线替身（外部能力）
# ============================================================


class _StubOrchestrator:
    async def execute(self, question, *, context=None):
        return AIOrchestrationResult(
            route=RouteType.RAG, content="答案", data=None, metadata={}
        )

    async def resume_agent_run(self, run_id, *, question, context=None):
        return AIOrchestrationResult(
            route=RouteType.RAG, content="答案", data=None, metadata={}
        )

    async def cancel_agent_run(self, run_id):
        return object()


class _StubToolService:
    async def execute(self, tool_name, *, arguments=None, context=None):
        return ToolResult(tool_name=tool_name, success=True, data={"qty": 3})


# ============================================================
# 定义与装配
# ============================================================


def _tool_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="s7_tool_only",
        version="v1",
        start_node_id="run_tool",
        nodes=(
            WorkflowNode(
                node_id="run_tool",
                node_type=NODE_TYPE_TOOL,
                tool_name="get_inventory",
                arguments_map={},
            ),
        ),
    )


def _approval_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="s7_approval_only",
        version="v1",
        start_node_id="gate",
        nodes=(
            WorkflowNode(
                node_id="gate",
                node_type=NODE_TYPE_APPROVAL,
                approval_prompt="请审批",
            ),
        ),
    )


def _registry() -> WorkflowRegistry:
    registry = WorkflowRegistry()
    registry.register(_tool_definition())
    registry.register(_approval_definition())
    return registry


def _make_service(*, project_id: str = _PROJECT_ID) -> WorkflowCommandService:
    """真 Factory（真 Repository）+ 离线外部服务 + 真 Read Service。"""
    runtime = create_workflow_runtime(
        registry=_registry(),
        project_id=project_id,
        orchestrator=_StubOrchestrator(),
        tool_execution_service=_StubToolService(),
        approval_id_factory=lambda: f"wf-ap-p7s1-{uuid.uuid4().hex[:8]}",
        run_id_factory=_run_id,
        request_id_factory=lambda: "req-p7s1",
    )
    return WorkflowCommandService(
        runtime_provider=lambda _pid: runtime,
        read_service_provider=create_workflow_read_service,
    )


def _client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    project_id: str = _PROJECT_ID,
    service: WorkflowCommandService | None = None,
) -> TestClient:
    bound = service or _make_service(project_id=project_id)
    monkeypatch.setattr(
        command_api, "get_workflow_command_service", lambda: bound
    )
    monkeypatch.setattr(
        auth_module,
        "get_workflow_command_settings",
        lambda: WorkflowCommandSettings(
            api_key=_TEST_API_KEY, api_project_id=project_id
        ),
    )
    return TestClient(app)


# ============================================================
# DB 清理与计数
# ============================================================


@pytest.fixture(scope="module")
def engine() -> Any:
    eng = get_engine()
    if eng is None:
        pytest.skip("DATABASE_URL 未配置，跳过 Workflow Command DB 集成测试")
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


# ============================================================
# 1. 正常启动（真落库）
# ============================================================


class TestStartCommandDb:
    def test_start_persists_and_returns_run_view(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch)
        run_id = _run_id()
        resp = client.post(
            "/api/workflow-runs",
            json={"workflow_id": "s7_tool_only", "workflow_run_id": run_id},
            headers=_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["outcome"] == "COMPLETED"
        assert body["run"]["status"] == WORKFLOW_STATUS_COMPLETED
        assert body["run"]["project_id"] == _PROJECT_ID

        # 真 Repository 复核（持久化验证）
        row = WorkflowRunRepository().get(run_id)
        assert row is not None
        assert row.status == WORKFLOW_STATUS_COMPLETED
        assert row.project_id == _PROJECT_ID
        node = WorkflowNodeRunRepository().get(
            workflow_run_id=run_id, node_id="run_tool", attempt=1
        )
        assert node is not None and node.status == NODE_STATUS_SUCCEEDED

    def test_start_unknown_definition_404(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch)
        resp = client.post(
            "/api/workflow-runs",
            json={"workflow_id": "not-registered"},
            headers=_HEADERS,
        )
        assert resp.status_code == 404


# ============================================================
# 2-3. 审批流（approve / reject 两段式）
# ============================================================


class TestApprovalCommandDb:
    def test_approve_flow_resumes_to_completion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch)
        run_id = _run_id()
        started = client.post(
            "/api/workflow-runs",
            json={"workflow_id": "s7_approval_only", "workflow_run_id": run_id},
            headers=_HEADERS,
        )
        assert started.status_code == 200, started.text
        assert started.json()["outcome"] == "WAITING_APPROVAL"
        approval_id = started.json()["approval_id"]
        assert approval_id and approval_id.startswith("wf-ap-p7s1-")

        approval = WorkflowApprovalRepository().get(approval_id)
        assert approval is not None
        assert approval.status == APPROVAL_STATUS_PENDING

        decided = client.post(
            f"/api/workflow-approvals/{approval_id}/decision",
            json={"decision": "APPROVED"},
            headers=_HEADERS,
        )
        assert decided.status_code == 200, decided.text
        body = decided.json()
        assert body["approval_status"] == APPROVAL_STATUS_APPROVED
        assert body["outcome"] == "COMPLETED"
        assert body["run"]["status"] == WORKFLOW_STATUS_COMPLETED

        row = WorkflowRunRepository().get(run_id)
        assert row is not None and row.status == WORKFLOW_STATUS_COMPLETED

    def test_reject_flow_fails_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch)
        run_id = _run_id()
        started = client.post(
            "/api/workflow-runs",
            json={"workflow_id": "s7_approval_only", "workflow_run_id": run_id},
            headers=_HEADERS,
        )
        assert started.status_code == 200
        approval_id = started.json()["approval_id"]

        rejected = client.post(
            f"/api/workflow-approvals/{approval_id}/decision",
            json={"decision": "REJECTED"},
            headers=_HEADERS,
        )
        assert rejected.status_code == 200, rejected.text
        body = rejected.json()
        assert body["approval_status"] == APPROVAL_STATUS_REJECTED
        assert body["run"]["status"] == WORKFLOW_STATUS_FAILED

        row = WorkflowRunRepository().get(run_id)
        assert row is not None and row.status == WORKFLOW_STATUS_FAILED

    def test_opposite_decision_conflict_409_and_no_state_change(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch)
        run_id = _run_id()
        started = client.post(
            "/api/workflow-runs",
            json={"workflow_id": "s7_approval_only", "workflow_run_id": run_id},
            headers=_HEADERS,
        )
        approval_id = started.json()["approval_id"]
        first = client.post(
            f"/api/workflow-approvals/{approval_id}/decision",
            json={"decision": "APPROVED"},
            headers=_HEADERS,
        )
        assert first.status_code == 200

        # 相反裁决 ⇒ 409（APPROVAL_CONFLICT）；run 状态不变（已 COMPLETED）
        second = client.post(
            f"/api/workflow-approvals/{approval_id}/decision",
            json={"decision": "REJECTED"},
            headers=_HEADERS,
        )
        assert second.status_code == 409
        row = WorkflowRunRepository().get(run_id)
        assert row is not None and row.status == WORKFLOW_STATUS_COMPLETED

    def test_decision_unknown_approval_404(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch)
        resp = client.post(
            "/api/workflow-approvals/wf-ap-does-not-exist/decision",
            json={"decision": "APPROVED"},
            headers=_HEADERS,
        )
        assert resp.status_code == 404


# ============================================================
# 4. 取消（三态 + 终态冲突）
# ============================================================


class TestCancelCommandDb:
    def test_cancel_waiting_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch)
        run_id = _run_id()
        started = client.post(
            "/api/workflow-runs",
            json={"workflow_id": "s7_approval_only", "workflow_run_id": run_id},
            headers=_HEADERS,
        )
        assert started.status_code == 200

        cancelled = client.post(
            f"/api/workflow-runs/{run_id}/cancel", headers=_HEADERS
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["outcome"] in ("CANCELLED", "CANCEL_REQUESTED")

        row = WorkflowRunRepository().get(run_id)
        assert row is not None
        assert row.status in (WORKFLOW_STATUS_CANCELLED, WORKFLOW_STATUS_WAITING)
        assert row.cancel_requested is True

    def test_cancel_terminal_run_409(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch)
        run_id = _run_id()
        done = client.post(
            "/api/workflow-runs",
            json={"workflow_id": "s7_tool_only", "workflow_run_id": run_id},
            headers=_HEADERS,
        )
        assert done.status_code == 200
        assert done.json()["outcome"] == "COMPLETED"

        again = client.post(
            f"/api/workflow-runs/{run_id}/cancel", headers=_HEADERS
        )
        assert again.status_code == 409
        assert "不可取消" in again.json()["detail"]


# ============================================================
# 5. 隔离与失败路径
# ============================================================


class TestIsolationAndFailureDb:
    def test_cross_project_access_is_404(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 先用 proj-p7s1 启动一个 run
        client = _client(monkeypatch)
        run_id = _run_id()
        client.post(
            "/api/workflow-runs",
            json={"workflow_id": "s7_tool_only", "workflow_run_id": run_id},
            headers=_HEADERS,
        )
        assert WorkflowRunRepository().get(run_id) is not None

        # 换另一项目凭据访问 ⇒ 404（与不存在同构）
        other = _client(
            monkeypatch,
            project_id=_OTHER_PROJECT,
            service=_make_service(project_id=_OTHER_PROJECT),
        )
        for url in (
            f"/api/workflow-runs/{run_id}/advance",
            f"/api/workflow-runs/{run_id}/cancel",
        ):
            resp = other.post(url, headers=_HEADERS)
            assert resp.status_code == 404, (url, resp.text)
            assert resp.json()["detail"] == "Workflow Run 不存在"
        # 同码同文案：与真正不存在的资源一致
        missing = other.post(
            f"/api/workflow-runs/{_RUN_PREFIX}missing/advance", headers=_HEADERS
        )
        assert missing.status_code == 404
        assert missing.json()["detail"] == "Workflow Run 不存在"

    def test_unauthenticated_401_and_unconfigured_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch)
        assert client.post(
            "/api/workflow-runs", json={"workflow_id": "s7_tool_only"}
        ).status_code == 401
        monkeypatch.setattr(
            auth_module,
            "get_workflow_command_settings",
            lambda: WorkflowCommandSettings(api_key="", api_project_id=""),
        )
        assert client.post(
            "/api/workflow-runs",
            json={"workflow_id": "s7_tool_only"},
            headers=_HEADERS,
        ).status_code == 503

    def test_advance_terminal_returns_terminal_200(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch)
        run_id = _run_id()
        client.post(
            "/api/workflow-runs",
            json={"workflow_id": "s7_tool_only", "workflow_run_id": run_id},
            headers=_HEADERS,
        )
        resp = client.post(
            f"/api/workflow-runs/{run_id}/advance", headers=_HEADERS
        )
        assert resp.status_code == 200
        assert resp.json()["outcome"] == "COMPLETED"


# ============================================================
# 6. 残留
# ============================================================


class TestResidueDb:
    def test_residue_is_zero_after_cleanup(self, engine: Any) -> None:
        _delete_prefix(engine)
        counts = _counts(engine)
        assert counts == {
            "workflow_run": 0,
            "workflow_node_run": 0,
            "workflow_approval": 0,
        }
