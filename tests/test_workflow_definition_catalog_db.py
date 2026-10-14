"""Phase 7 Step 7 — 生产 Workflow 定义目录 **DB 真链 e2e**（默认跳过）。

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_workflow_definition_catalog_db.py
```

真链路（HTTP → 认证 → 命令服务 → **真** WorkflowRuntime → **真** Repository → PostgreSQL）：

```text
POST /api/workflow-runs {workflow_id: "answer_review" | "inventory_check", ...}
        ↓  生产注册表（workflow.catalog 经 Composition Root 注册——本步交付的能力）
        ↓  真 Runtime（真 Store Adapter / 真 CAS / 真审批落库）
        ↓  离线外部替身（Orchestrator / Tool——**不**调用真实 LLM / 真实 Tool）
        ⇒ WAITING_APPROVAL（审批门：不自动裁决）
POST /api/workflow-approvals/{approval_id}/decision {decision: APPROVED}
        ↓
GET  /api/workflow-runs/{run_id}（+/nodes /timeline）⇒ COMPLETED 终态可读
```

覆盖：

```text
1  answer_review：AI 节点模板渲染（input.question ⇒ 渲染后问题到达 Orchestrator）
   → APPROVAL 门（WAITING_APPROVAL）→ 批准 → COMPLETED；Run / Node / Timeline 可读
2  inventory_check：TOOL 节点参数映射（input.material_code ⇒ 解析后 arguments
   到达 Tool 执行边界）→ APPROVAL 门 → 批准 → COMPLETED
3  未注册定义名 ⇒ 404（fail-closed 不变；目录不改变未注册语义）
4  目录 API 返回的 name 与命令 API 可启动 name **同源**（同一注册表）
5  三表零残留（定向前缀清理 wf-p7s7- / wf-ap-p7s7-）
```

安全边界：只写 ``ai_ops.workflow_*`` 三表（真实 Runtime 语义）；定向前缀清理；
不调用真实 DeepSeek / 真实 Tool Handler；不触碰 agent 表 / 业务数据。
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.api import workflow_command as command_api
from backend.app.api import workflow_command_auth as command_auth_module
from backend.app.api import workflow_read_auth as read_auth_module
from backend.app.config import (
    WorkflowCommandSettings,
    WorkflowReadSettings,
)
from backend.app.db.session import get_engine
from backend.app.main import app
from backend.app.services.ai_orchestrator_service import AIOrchestrationResult
from backend.app.services.ai_router_service import RouteType
from backend.app.tools.base import ToolResult
from backend.app.workflow.catalog import (
    WORKFLOW_NAME_ANSWER_REVIEW,
    WORKFLOW_NAME_INVENTORY_CHECK,
)
from backend.app.workflow_runtime.command_service import (
    WorkflowCommandService,
    get_workflow_registry,
)
from backend.app.workflow_runtime.factory import create_workflow_runtime
from backend.app.workflow_runtime.read_factory import (
    create_workflow_read_service,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS", "").strip().lower() not in {"1", "true", "yes", "on"},
    reason="set RUN_DB_TESTS=1 to enable workflow definition catalog DB tests",
)

__all__ = [
    "TestCatalogEndToEndDb",
    "TestResidueDb",
]

#: 本文件的 run_id / approval_id 前缀（定向清理；绝不触碰其它数据）。
_RUN_PREFIX = "wf-p7s7-"
_APPROVAL_PREFIX = "wf-ap-p7s7-"
_PROJECT_ID = "proj-p7s7"

_TEST_API_KEY = "p7s7-catalog-db-key-NOT-A-PRODUCTION-CREDENTIAL"
_TEST_READ_KEY = "p7s7-catalog-db-read-key-NOT-A-PRODUCTION-CREDENTIAL"
_HEADERS = {"X-API-Key": _TEST_API_KEY}
_READ_HEADERS = {"X-API-Key": _TEST_READ_KEY}


def _run_id() -> str:
    return f"{_RUN_PREFIX}{uuid.uuid4().hex[:12]}"


# ============================================================
# 离线外部替身（**不是** fake Runtime / fake Repository）
# ============================================================


class _RecordingOrchestrator:
    """真实 OrchestratorPort 兼容替身：记录渲染后问题并返回固定回答。"""

    def __init__(self) -> None:
        self.questions: list[str] = []

    async def execute(self, question: Any, *, context: Any = None) -> Any:
        self.questions.append(str(question))
        return AIOrchestrationResult(
            route=RouteType.RAG, content="目录 e2e 回答", data=None, metadata={}
        )

    async def resume_agent_run(
        self, run_id: Any, *, question: Any, context: Any = None
    ) -> Any:
        return AIOrchestrationResult(
            route=RouteType.RAG, content="目录 e2e 回答", data=None, metadata={}
        )

    async def cancel_agent_run(self, run_id: Any) -> Any:
        return object()


class _RecordingToolService:
    """真实 ToolExecutionPort 兼容替身：记录解析后 arguments 并返回成功结果。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(
        self, tool_name: str, *, arguments: Any = None, context: Any = None
    ) -> ToolResult:
        self.calls.append((tool_name, dict(arguments or {})))
        return ToolResult(
            tool_name=tool_name,
            success=True,
            data={"material_code": (arguments or {}).get("material_code"), "qty": 3},
        )


class _Env:
    def __init__(self, *, project_id: str = _PROJECT_ID) -> None:
        self.orchestrator = _RecordingOrchestrator()
        self.tool = _RecordingToolService()
        runtime = create_workflow_runtime(
            # 生产注册表（含 workflow.catalog）——本步交付的装配
            registry=get_workflow_registry(),
            project_id=project_id,
            orchestrator=self.orchestrator,
            tool_execution_service=self.tool,
            approval_id_factory=lambda: f"{_APPROVAL_PREFIX}{uuid.uuid4().hex[:8]}",
            request_id_factory=lambda: f"req-p7s7-{uuid.uuid4().hex[:8]}",
        )
        self.service = WorkflowCommandService(
            runtime_provider=lambda _pid: runtime,
            read_service_provider=create_workflow_read_service,
        )


def _client(monkeypatch: pytest.MonkeyPatch, env: _Env) -> TestClient:
    monkeypatch.setattr(
        command_api, "get_workflow_command_service", lambda: env.service
    )
    monkeypatch.setattr(
        command_auth_module,
        "get_workflow_command_settings",
        lambda: WorkflowCommandSettings(
            api_key=_TEST_API_KEY, api_project_id=_PROJECT_ID
        ),
    )
    monkeypatch.setattr(
        read_auth_module,
        "get_workflow_read_settings",
        lambda: WorkflowReadSettings(
            api_key=_TEST_READ_KEY, api_project_id=_PROJECT_ID
        ),
    )
    return TestClient(app)


# ============================================================
# DB 清理与计数（三表定向；前缀）
# ============================================================


@pytest.fixture(scope="module")
def engine() -> Any:
    eng = get_engine()
    if eng is None:
        pytest.skip("DATABASE_URL 未配置，跳过定义目录 DB 集成测试")
    return eng


def _delete_prefix(engine: Any) -> None:
    with engine.begin() as conn:
        for table in (
            "workflow_run",
            "workflow_node_run",
            "workflow_approval",
        ):
            conn.execute(
                text(
                    f"DELETE FROM ai_ops.{table} "
                    "WHERE workflow_run_id LIKE :prefix"
                ),
                {"prefix": f"{_RUN_PREFIX}%"},
            )
        conn.execute(
            text(
                "DELETE FROM ai_ops.workflow_approval "
                "WHERE approval_id LIKE :prefix"
            ),
            {"prefix": f"{_APPROVAL_PREFIX}%"},
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
def _cleanup(engine: Any) -> Any:
    _delete_prefix(engine)
    yield
    _delete_prefix(engine)


# ============================================================
# 1 / 2：目录定义真链 e2e
# ============================================================


class TestCatalogEndToEndDb:
    def test_answer_review_flows_to_terminal(
        self, monkeypatch: pytest.MonkeyPatch, engine: Any
    ) -> None:
        """AI 节点模板渲染 → 审批门 → 批准 → COMPLETED；Run / Node 可读。"""
        env = _Env()
        client = _client(monkeypatch, env)
        run_id = _run_id()

        started = client.post(
            "/api/workflow-runs",
            json={
                "workflow_id": WORKFLOW_NAME_ANSWER_REVIEW,
                "workflow_run_id": run_id,
                "input": json.dumps({"question": "WMS 收货流程有哪些步骤？"}),
            },
            headers=_HEADERS,
        )
        assert started.status_code == 200, started.text
        body = started.json()
        assert body["outcome"] == "WAITING_APPROVAL"
        approval_id = body["approval_id"]
        assert approval_id and approval_id.startswith(_APPROVAL_PREFIX)
        assert body["run"]["status"] == "WAITING"
        # 模板渲染（真实 binding）：input.question 进入 OrchestratorPort
        assert env.orchestrator.questions == [
            "请回答以下问题：WMS 收货流程有哪些步骤？"
        ]

        # ---- 节点 attempt 落库（AI 节点 SUCCEEDED；审批节点 WAITING）----
        nodes = client.get(
            f"/api/workflow-runs/{run_id}/nodes", headers=_READ_HEADERS
        )
        assert nodes.status_code == 200, nodes.text
        node_items = nodes.json()
        assert [(n["node_id"], n["node_type"]) for n in node_items] == [
            ("answer", "AI"),
            ("review_answer", "APPROVAL"),
        ]

        # ---- 批准 ⇒ 推进到终态 ----
        decided = client.post(
            f"/api/workflow-approvals/{approval_id}/decision",
            json={"decision": "APPROVED"},
            headers=_HEADERS,
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["approval_status"] == "APPROVED"
        assert decided.json()["outcome"] == "COMPLETED"

        detail = client.get(
            f"/api/workflow-runs/{run_id}", headers=_READ_HEADERS
        )
        assert detail.status_code == 200, detail.text
        payload = detail.json()
        assert payload["status"] == "COMPLETED"
        assert payload["workflow_id"] == WORKFLOW_NAME_ANSWER_REVIEW
        assert payload["workflow_version"] == "v1"
        assert payload["project_id"] == _PROJECT_ID
        assert payload["completed_at"] is not None

        timeline = client.get(
            f"/api/workflow-runs/{run_id}/timeline", headers=_READ_HEADERS
        )
        assert timeline.status_code == 200
        kinds = [event["kind"] for event in timeline.json()]
        assert "RUN_CREATED" in kinds
        assert "APPROVAL_RESOLVED" in kinds

        assert _counts(engine)["workflow_run"] >= 1

    def test_inventory_check_resolves_input_into_tool_arguments(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TOOL 节点参数映射（input.material_code ⇒ arguments）→ 审批门 → COMPLETED。"""
        env = _Env()
        client = _client(monkeypatch, env)
        run_id = _run_id()

        started = client.post(
            "/api/workflow-runs",
            json={
                "workflow_id": WORKFLOW_NAME_INVENTORY_CHECK,
                "workflow_run_id": run_id,
                "input": json.dumps({"material_code": "MAT001"}),
            },
            headers=_HEADERS,
        )
        assert started.status_code == 200, started.text
        body = started.json()
        assert body["outcome"] == "WAITING_APPROVAL"
        # 参数映射（真实 binding.resolve_arguments_map）
        assert env.tool.calls == [("get_inventory", {"material_code": "MAT001"})]

        decided = client.post(
            f"/api/workflow-approvals/{body['approval_id']}/decision",
            json={"decision": "APPROVED"},
            headers=_HEADERS,
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["outcome"] == "COMPLETED"

        detail = client.get(
            f"/api/workflow-runs/{run_id}", headers=_READ_HEADERS
        ).json()
        assert detail["status"] == "COMPLETED"
        assert detail["workflow_id"] == WORKFLOW_NAME_INVENTORY_CHECK

    def test_catalog_names_are_startable_and_unknown_is_404(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _Env()
        client = _client(monkeypatch, env)

        catalog = client.get("/api/workflow-definitions", headers=_READ_HEADERS)
        assert catalog.status_code == 200, catalog.text
        names = [item["name"] for item in catalog.json()["items"]]
        assert names == [WORKFLOW_NAME_INVENTORY_CHECK, WORKFLOW_NAME_ANSWER_REVIEW]
        for name in names:
            assert name in get_workflow_registry()

        # 未注册名称 ⇒ 404（fail-closed 语义不因目录而改变）
        missing = client.post(
            "/api/workflow-runs",
            json={"workflow_id": "does_not_exist_p7s7"},
            headers=_HEADERS,
        )
        assert missing.status_code == 404
        assert missing.json()["detail"] == "Workflow 定义不存在"


# ============================================================
# 5：零残留
# ============================================================


class TestResidueDb:
    def test_prefix_residue_is_zero(self, engine: Any) -> None:
        _delete_prefix(engine)
        assert _counts(engine) == {
            "workflow_run": 0,
            "workflow_node_run": 0,
            "workflow_approval": 0,
        }
