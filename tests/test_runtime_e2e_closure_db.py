"""Phase 7 Step 5 — Runtime **端到端闭环** DB 集成测试（真 PostgreSQL）。

验证 Agent 与 Workflow 从 HTTP 命令入口 → Runtime 执行 → 持久化 →
Read/List 查询的完整闭环（任务书 §3 六项）：

```text
闭环 1  启动运行后，Read/List API 可查询到运行记录
闭环 2  等待审批时，运行状态与审批记录一致
闭环 3  批准 / 拒绝后，状态转换符合 Runtime 规则
闭环 4  Resume / 终态再推进：恢复已有运行，不重复执行已持久化步骤 / 工具
闭环 5  Cancel 保持协作式语义（三态；不强杀 Tool）
闭环 6  跨项目请求不能读取或操作其他项目的运行
```

真链（**不** monkeypatch 命令服务之外的装配）：
```text
TestClient → Command API（X-API-Key + 服务端作用域）
→ 真 CommandService → 真 Runtime → 真 Repository → PostgreSQL
TestClient → Read/List API（按域认证）→ 真 ReadService → 同一 PostgreSQL
```

外部能力边界：Workflow 的 Orchestrator / Tool 以离线替身注入
（**不**调用真实 DeepSeek / 真实 Tool Handler）；Agent 的 LLM 以
``FakeLLM``（AgentLLM 端口）注入——Runtime / Repository / 认证全部真实。
只写 ``ai_ops.workflow_*`` 三表与 ``ai_ops.agent_*`` 表；定向前缀清理
（``ec-p7s5-`` / ``ec-p7s5-wf-``）；不触碰生产数据。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.agent import AgentRuntimePolicy
from backend.app.agent.cancellation import AgentCancellationCoordinator
from backend.app.agent.models import AgentDecision
from backend.app.agent.recorder import (
    build_agent_approval_store,
    build_agent_checkpoint_recorder,
    build_agent_decision_store,
    build_agent_run_recorder,
    build_agent_step_recorder,
    build_agent_tool_result_reader,
)
from backend.app.agent.runtime import DECISION_FINAL, AgentRuntime
from backend.app.api import agent_command as agent_command_api
from backend.app.api import agent_command_auth as agent_command_auth
from backend.app.api import agent_read_auth as agent_read_auth
from backend.app.api import runtime_read as runtime_read_api
from backend.app.api import workflow_command as workflow_command_api
from backend.app.api import workflow_command_auth as workflow_command_auth
from backend.app.api import workflow_read_auth as workflow_read_auth
from backend.app.config import (
    AgentCommandSettings,
    AgentReadSettings,
    WorkflowCommandSettings,
    WorkflowReadSettings,
)
from backend.app.db.agent_approval_repository import (
    AgentApprovalRepository,
)
from backend.app.db.agent_checkpoint_repository import (
    AgentCheckpointRepository,
)
from backend.app.db.agent_decision_repository import (
    AgentDecisionRepository,
)
from backend.app.db.agent_run_repository import AgentRunRepository
from backend.app.db.agent_step_repository import AgentStepRepository
from backend.app.db.agent_tool_result_repository import (
    AgentToolResultRepository,
)
from backend.app.db.session import get_engine
from backend.app.db.workflow_run_repository import WorkflowRunRepository
from backend.app.main import app
from backend.app.services.agent_command_service import AgentCommandService
from backend.app.services.agent_read_service import AgentReadService
from backend.app.services.ai_orchestrator_service import AIOrchestrationResult
from backend.app.services.ai_router_service import RouteType
from backend.app.services.runtime_run_list_service import RuntimeRunListService
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
    APPROVAL_STATUS_REJECTED,
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
from tests.test_agent_runtime import FakeLLM

_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

pytestmark = pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable runtime e2e closure PostgreSQL tests",
)

__all__ = [
    "TestWorkflowClosureDb",
    "TestAgentClosureDb",
    "TestClosureResidueDb",
]

_AGENT_PREFIX = "ec-p7s5-"
_WF_PREFIX = "ec-p7s5-wf-"
_PROJECT = "proj-p7s5"
_OTHER_PROJECT = "proj-p7s5-other"

_AGENT_CMD_KEY = "ec-agent-cmd-key-NOT-A-PRODUCTION-CREDENTIAL"
_AGENT_READ_KEY = "ec-agent-read-key-NOT-A-PRODUCTION-CREDENTIAL"
_WF_CMD_KEY = "ec-wf-cmd-key-NOT-A-PRODUCTION-CREDENTIAL"
_WF_READ_KEY = "ec-wf-read-key-NOT-A-PRODUCTION-CREDENTIAL"

_AGENT_CMD_HEADERS = {"X-API-Key": _AGENT_CMD_KEY}
_AGENT_READ_HEADERS = {"X-API-Key": _AGENT_READ_KEY}
_WF_CMD_HEADERS = {"X-API-Key": _WF_CMD_KEY}
_WF_READ_HEADERS = {"X-API-Key": _WF_READ_KEY}


# ============================================================
# 离线替身（外部能力：Workflow Orchestrator / Tool）
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
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, tool_name, *, arguments=None, context=None):
        self.calls.append({"tool_name": tool_name, "arguments": arguments})
        return ToolResult(tool_name=tool_name, success=True, data={"qty": 3})


# ============================================================
# Workflow 装配（真 Runtime + 真 Repository + 离线外部能力）
# ============================================================


def _tool_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="ec_p7s5_tool_only",
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
        name="ec_p7s5_approval_only",
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


def _wf_run_id() -> str:
    return f"{_WF_PREFIX}{uuid.uuid4().hex[:12]}"


def _wf_clients(monkeypatch: pytest.MonkeyPatch) -> dict[str, TestClient]:
    """Workflow 命令 + 读取客户端（同一真装配；各自域认证）。"""
    tool_service = _StubToolService()
    runtime = create_workflow_runtime(
        registry=_registry(),
        project_id=_PROJECT,
        orchestrator=_StubOrchestrator(),
        tool_execution_service=tool_service,
        approval_id_factory=lambda: f"ec-p7s5-ap-{uuid.uuid4().hex[:8]}",
        run_id_factory=_wf_run_id,
        request_id_factory=lambda: f"ec-p7s5-req-{uuid.uuid4().hex[:8]}",
    )
    command_service = WorkflowCommandService(
        runtime_provider=lambda _pid: runtime,
        read_service_provider=create_workflow_read_service,
    )
    monkeypatch.setattr(
        workflow_command_api,
        "get_workflow_command_service",
        lambda: command_service,
    )
    monkeypatch.setattr(
        workflow_command_auth,
        "get_workflow_command_settings",
        lambda: WorkflowCommandSettings(
            api_key=_WF_CMD_KEY, api_project_id=_PROJECT
        ),
    )
    monkeypatch.setattr(
        workflow_read_auth,
        "get_workflow_read_settings",
        lambda: WorkflowReadSettings(
            api_key=_WF_READ_KEY, api_project_id=_PROJECT
        ),
    )
    monkeypatch.setattr(
        runtime_read_api,
        "get_runtime_run_list_service",
        lambda: RuntimeRunListService(
            agent_run_repository=AgentRunRepository(),
            workflow_run_repository=_WorkflowRunRepo(),
        ),
    )
    return {"command": TestClient(app), "read": TestClient(app), "tool": tool_service}


def _registry() -> WorkflowRegistry:
    registry = WorkflowRegistry()
    registry.register(_tool_definition())
    registry.register(_approval_definition())
    return registry


def _WorkflowRunRepo() -> Any:
    from backend.app.db.workflow_run_repository import WorkflowRunRepository

    return WorkflowRunRepository()


# ============================================================
# Agent 装配（真 Runtime + 真 Repository + FakeLLM）
# ============================================================


def _agent_run_id() -> str:
    return f"{_AGENT_PREFIX}{uuid.uuid4().hex[:12]}"


def _make_agent_service(
    *,
    project_id: str = _PROJECT,
    llm: FakeLLM | None = None,
) -> tuple[AgentCommandService, FakeLLM]:
    """真装配（与生产 composition root 同构）+ FakeLLM。"""
    checkpoints = build_agent_checkpoint_recorder()
    approval_store = build_agent_approval_store()
    fake_llm = llm or FakeLLM(
        [AgentDecision(type=DECISION_FINAL, content="闭环完成")]
    )
    runtime = AgentRuntime(
        llm=fake_llm,
        tool_execution=_StubToolService(),
        policy=AgentRuntimePolicy(
            max_execution_time_ms=60_000, max_cost_tokens=10_000
        ),
        allowed_tools=(),
        checkpoint_sink=checkpoints,
        checkpoint_store=checkpoints,
        decision_store=build_agent_decision_store(),
        tool_result_store=build_agent_tool_result_reader(),
        approval_port=approval_store,
        approval_required_tools=(),
    )
    service = AgentCommandService(
        runtime=runtime,
        run_repository=AgentRunRepository(),
        checkpoints=checkpoints,
        approval_store=approval_store,
        run_recorder=build_agent_run_recorder(),
        step_recorder=build_agent_step_recorder(),
    )
    return service, fake_llm


def _agent_clients(
    monkeypatch: pytest.MonkeyPatch,
    *,
    service: AgentCommandService | None = None,
) -> dict[str, TestClient]:
    """Agent 命令 + 读取客户端（真 ReadService；各自域认证）。"""
    bound = service or _make_agent_service()[0]
    monkeypatch.setattr(
        agent_command_api, "get_agent_command_service", lambda: bound
    )
    monkeypatch.setattr(
        agent_command_auth,
        "get_agent_command_settings",
        lambda: AgentCommandSettings(
            api_key=_AGENT_CMD_KEY, api_project_id=_PROJECT
        ),
    )
    monkeypatch.setattr(
        agent_read_auth,
        "get_agent_read_settings",
        lambda: AgentReadSettings(
            api_key=_AGENT_READ_KEY, api_project_id=_PROJECT
        ),
    )
    monkeypatch.setattr(
        runtime_read_api,
        "get_agent_read_service",
        lambda: AgentReadService(
            run_repository=AgentRunRepository(),
            step_repository=AgentStepRepository(),
            decision_repository=AgentDecisionRepository(),
            tool_result_repository=AgentToolResultRepository(),
            checkpoint_repository=AgentCheckpointRepository(),
            approval_repository=AgentApprovalRepository(),
        ),
    )
    return {"command": TestClient(app), "read": TestClient(app)}


# ============================================================
# DB 清理与计数
# ============================================================


@pytest.fixture(scope="module")
def engine() -> Any:
    eng = get_engine()
    if eng is None:
        pytest.skip("DATABASE_URL 未配置，跳过 Runtime 闭环 DB 集成测试")
    return eng


def _delete_prefixes(engine: Any) -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM ai_ops.agent_run WHERE run_id LIKE :p"),
            {"p": f"{_AGENT_PREFIX}%"},
        )
        conn.execute(
            text("DELETE FROM ai_ops.workflow_run WHERE workflow_run_id LIKE :p"),
            {"p": f"{_WF_PREFIX}%"},
        )


def _counts(engine: Any) -> dict[str, int]:
    from sqlalchemy import text

    with engine.connect() as conn:
        return {
            "agent_run": int(
                conn.execute(
                    text(
                        "SELECT COUNT(*) FROM ai_ops.agent_run WHERE run_id LIKE :p"
                    ),
                    {"p": f"{_AGENT_PREFIX}%"},
                ).scalar_one()
            ),
            "workflow_run": int(
                conn.execute(
                    text(
                        "SELECT COUNT(*) FROM ai_ops.workflow_run "
                        "WHERE workflow_run_id LIKE :p"
                    ),
                    {"p": f"{_WF_PREFIX}%"},
                ).scalar_one()
            ),
        }


#: AgentRuntime 真实执行写入的**控制行**表（UUID run_id，无法用前缀匹配；
#: conftest 会话守卫只覆盖 agent_run / agent_step ⇒ 本文件自行兜底）。
_AGENT_CONTROL_TABLES: tuple[str, ...] = (
    "agent_checkpoint",
    "agent_decision",
    "agent_tool_result",
)


@pytest.fixture(scope="module", autouse=True)
def _agent_control_rows_cleanup(engine: Any) -> Any:
    """控制行水位清理（Phase 7 Step 6 发现：闭环测试的真实 runtime 运行会写
    ``agent_checkpoint / agent_decision``（最终决策 + 控制状态），而这些表不在
    conftest 会话守卫范围内（守卫只清 ``agent_run / agent_step``）；前缀清理
    也匹配不到 runtime 自签发的 UUID run_id ⇒ 遗留孤儿控制行。

    机制与 conftest 守卫一致（保守）：模块开始记录 ``MAX(id)`` 水位 → 模块结束
    只删 ``id > 水位`` 的行；不使用 TRUNCATE / 不触碰水位以下的历史行。
    """
    from sqlalchemy import text

    marks: dict[str, int] = {}
    with engine.begin() as conn:
        for table in _AGENT_CONTROL_TABLES:
            marks[table] = int(
                conn.execute(
                    text(f"SELECT COALESCE(MAX(id), 0) FROM ai_ops.{table}")
                ).scalar_one()
            )
    yield
    with engine.begin() as conn:
        for table in _AGENT_CONTROL_TABLES:
            conn.execute(
                text(f"DELETE FROM ai_ops.{table} WHERE id > :watermark"),
                {"watermark": marks[table]},
            )


@pytest.fixture(scope="module", autouse=True)
def _cleanup(engine: Any):
    _delete_prefixes(engine)
    yield
    _delete_prefixes(engine)


@pytest.fixture(autouse=True)
def _clean_prefix(engine: Any) -> Any:
    _delete_prefixes(engine)
    yield
    _delete_prefixes(engine)


def _seed_agent_run(*, project_id: str = _PROJECT) -> str:
    run_id = _agent_run_id()
    now = datetime.now(timezone.utc)
    AgentRunRepository().create(
        run_id=run_id,
        request_id=f"{_AGENT_PREFIX}req-{uuid.uuid4().hex[:8]}",
        project_id=project_id,
        status="COMPLETED",
        error_code=None,
        limit_reason=None,
        step_count=1,
        tool_call_count=0,
        llm_call_count=1,
        token_usage_unverifiable=False,
        started_at=now,
        finished_at=now,
        duration_ms=5.0,
    )
    return run_id


# ============================================================
# 闭环 1-6：Workflow
# ============================================================


class TestWorkflowClosureDb:
    def test_start_then_read_and_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """闭环 1：start ⇒ 落库 ⇒ Read 详情可读 ⇒ List 可见（同一真链）。"""
        clients = _wf_clients(monkeypatch)
        started = clients["command"].post(
            "/api/workflow-runs",
            json={
                "workflow_id": "ec_p7s5_tool_only",
                "workflow_version": "v1",
                "request_id": f"ec-p7s5-req-{uuid.uuid4().hex[:8]}",
            },
            headers=_WF_CMD_HEADERS,
        )
        assert started.status_code == 200
        run_id = started.json()["workflow_run_id"]

        detail = clients["read"].get(
            f"/api/runtime-runs/workflow/{run_id}", headers=_WF_READ_HEADERS
        )
        assert detail.status_code == 200
        assert detail.json()["detail"]["workflow_run_id"] == run_id
        assert detail.json()["detail"]["status"] == WORKFLOW_STATUS_COMPLETED

        listing = clients["read"].get(
            "/api/runtime-runs/workflow", headers=_WF_READ_HEADERS
        )
        assert listing.status_code == 200
        assert run_id in [item["run_id"] for item in listing.json()["items"]]

    def test_approval_flow_state_consistency(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """闭环 2/3：start ⇒ WAITING（Read 状态一致）⇒ decision(APPROVED)
        ⇒ advance ⇒ COMPLETED；审批记录状态一致。"""
        clients = _wf_clients(monkeypatch)
        started = clients["command"].post(
            "/api/workflow-runs",
            json={
                "workflow_id": "ec_p7s5_approval_only",
                "workflow_version": "v1",
                "request_id": f"ec-p7s5-req-{uuid.uuid4().hex[:8]}",
            },
            headers=_WF_CMD_HEADERS,
        )
        assert started.status_code == 200
        payload = started.json()
        run_id = payload["workflow_run_id"]
        approval_id = payload["approval_id"]
        assert payload["run"]["status"] == WORKFLOW_STATUS_WAITING

        # 闭环 2：等待审批时 Read 状态与审批记录一致
        waiting = clients["read"].get(
            f"/api/runtime-runs/workflow/{run_id}", headers=_WF_READ_HEADERS
        )
        assert waiting.json()["detail"]["status"] == WORKFLOW_STATUS_WAITING
        approval = clients["read"].get(
            f"/api/workflow-approvals/{approval_id}", headers=_WF_READ_HEADERS
        )
        assert approval.status_code == 200
        assert approval.json()["status"] == "PENDING"

        # 闭环 3：批准 ⇒ 状态转换符合 Runtime 规则
        decided = clients["command"].post(
            f"/api/workflow-approvals/{approval_id}/decision",
            json={"decision": APPROVAL_STATUS_APPROVED},
            headers=_WF_CMD_HEADERS,
        )
        assert decided.status_code == 200
        advanced = clients["command"].post(
            f"/api/workflow-runs/{run_id}/advance", headers=_WF_CMD_HEADERS
        )
        assert advanced.status_code == 200
        assert advanced.json()["run"]["status"] == WORKFLOW_STATUS_COMPLETED
        final = clients["read"].get(
            f"/api/runtime-runs/workflow/{run_id}", headers=_WF_READ_HEADERS
        )
        assert final.json()["detail"]["status"] == WORKFLOW_STATUS_COMPLETED

    def test_reject_flow_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """闭环 3：decision(REJECTED) ⇒ advance ⇒ FAILED（拒绝路径）。"""
        clients = _wf_clients(monkeypatch)
        started = clients["command"].post(
            "/api/workflow-runs",
            json={
                "workflow_id": "ec_p7s5_approval_only",
                "workflow_version": "v1",
                "request_id": f"ec-p7s5-req-{uuid.uuid4().hex[:8]}",
            },
            headers=_WF_CMD_HEADERS,
        )
        run_id = started.json()["workflow_run_id"]
        approval_id = started.json()["approval_id"]
        assert (
            clients["command"]
            .post(
                f"/api/workflow-approvals/{approval_id}/decision",
                json={"decision": APPROVAL_STATUS_REJECTED},
                headers=_WF_CMD_HEADERS,
            )
            .status_code
            == 200
        )
        advanced = clients["command"].post(
            f"/api/workflow-runs/{run_id}/advance", headers=_WF_CMD_HEADERS
        )
        assert advanced.json()["run"]["status"] == WORKFLOW_STATUS_FAILED
        final = clients["read"].get(
            f"/api/runtime-runs/workflow/{run_id}", headers=_WF_READ_HEADERS
        )
        assert final.json()["detail"]["status"] == WORKFLOW_STATUS_FAILED

    def test_terminal_advance_does_not_reexecute(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """闭环 4：终态再 advance ⇒ 历史终态；Tool 不重复执行。"""
        clients = _wf_clients(monkeypatch)
        started = clients["command"].post(
            "/api/workflow-runs",
            json={
                "workflow_id": "ec_p7s5_tool_only",
                "workflow_version": "v1",
                "request_id": f"ec-p7s5-req-{uuid.uuid4().hex[:8]}",
            },
            headers=_WF_CMD_HEADERS,
        )
        run_id = started.json()["workflow_run_id"]
        calls_after_start = len(clients["tool"].calls)
        assert calls_after_start == 1  # start 即执行一次

        again = clients["command"].post(
            f"/api/workflow-runs/{run_id}/advance", headers=_WF_CMD_HEADERS
        )
        assert again.status_code == 200
        assert again.json()["run"]["status"] == WORKFLOW_STATUS_COMPLETED
        assert len(clients["tool"].calls) == calls_after_start  # 零重复执行

    def test_cancel_waiting_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """闭环 5：WAITING run ⇒ cancel ⇒ CANCELLED；终态再取消 ⇒ 409。"""
        clients = _wf_clients(monkeypatch)
        started = clients["command"].post(
            "/api/workflow-runs",
            json={
                "workflow_id": "ec_p7s5_approval_only",
                "workflow_version": "v1",
                "request_id": f"ec-p7s5-req-{uuid.uuid4().hex[:8]}",
            },
            headers=_WF_CMD_HEADERS,
        )
        run_id = started.json()["workflow_run_id"]
        cancelled = clients["command"].post(
            f"/api/workflow-runs/{run_id}/cancel", headers=_WF_CMD_HEADERS
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["run"]["status"] == WORKFLOW_STATUS_CANCELLED
        detail = clients["read"].get(
            f"/api/runtime-runs/workflow/{run_id}", headers=_WF_READ_HEADERS
        )
        assert detail.json()["detail"]["status"] == WORKFLOW_STATUS_CANCELLED
        # 终态不可逆：再取消 ⇒ 409
        assert (
            clients["command"]
            .post(f"/api/workflow-runs/{run_id}/cancel", headers=_WF_CMD_HEADERS)
            .status_code
            == 409
        )

    def test_cross_project_run_is_invisible(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """闭环 6：他项目 seed 的 run ⇒ Read 404 / List 不可见 / 命令 404。"""
        from backend.app.db.workflow_run_repository import (
            WorkflowRunRepository,
        )

        other_run_id = _wf_run_id()
        WorkflowRunRepository().create(
            workflow_run_id=other_run_id,
            workflow_id="ec_p7s5_tool_only",
            workflow_version="v1",
            request_id=f"ec-p7s5-req-{uuid.uuid4().hex[:8]}",
            project_id=_OTHER_PROJECT,
            input=None,
            status="PENDING",
        )
        clients = _wf_clients(monkeypatch)
        # Read ⇒ 404（与不存在同码）
        assert (
            clients["read"]
            .get(
                f"/api/runtime-runs/workflow/{other_run_id}",
                headers=_WF_READ_HEADERS,
            )
            .status_code
            == 404
        )
        # List ⇒ 不可见
        listing = clients["read"].get(
            "/api/runtime-runs/workflow", headers=_WF_READ_HEADERS
        )
        assert other_run_id not in [
            item["run_id"] for item in listing.json()["items"]
        ]
        # 命令 ⇒ 404
        assert (
            clients["command"]
            .post(
                f"/api/workflow-runs/{other_run_id}/advance",
                headers=_WF_CMD_HEADERS,
            )
            .status_code
            == 404
        )


# ============================================================
# 闭环 1/4/5/6：Agent（控制状态 + 查询路径；LLM 以 FakeLLM 注入）
# ============================================================


class TestAgentClosureDb:
    def test_start_then_read_and_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """闭环 1：Agent start（FakeLLM FINAL）⇒ run 落库 ⇒ Read 可读
        ⇒ List 可见。"""
        clients = _agent_clients(monkeypatch)
        started = clients["command"].post(
            "/api/agent-runs", json={"question": "闭环验证"}, headers=_AGENT_CMD_HEADERS
        )
        assert started.status_code == 200
        run_id = started.json()["run_id"]
        assert started.json()["outcome"] == "COMPLETED"

        detail = clients["read"].get(
            f"/api/runtime-runs/agent/{run_id}", headers=_AGENT_READ_HEADERS
        )
        assert detail.status_code == 200
        assert detail.json()["run_id"] == run_id
        assert detail.json()["detail"]["status"] == "COMPLETED"

        listing = clients["read"].get(
            "/api/runtime-runs/agent", headers=_AGENT_READ_HEADERS
        )
        assert run_id in [item["run_id"] for item in listing.json()["items"]]

    def test_resume_terminal_does_not_recall_llm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """闭环 4：终态 run resume ⇒ 历史终态；LLM 零新增调用。"""
        service, fake_llm = _make_agent_service()
        clients = _agent_clients(monkeypatch, service=service)
        started = clients["command"].post(
            "/api/agent-runs", json={"question": "q"}, headers=_AGENT_CMD_HEADERS
        )
        run_id = started.json()["run_id"]
        calls_after_start = fake_llm.calls

        resumed = clients["command"].post(
            f"/api/agent-runs/{run_id}/resume",
            json={},
            headers=_AGENT_CMD_HEADERS,
        )
        assert resumed.status_code == 200
        assert resumed.json()["outcome"] == "COMPLETED"
        assert fake_llm.calls == calls_after_start  # 零重复 LLM 调用

    def test_cancel_without_checkpoint_is_conflict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """闭环 5：无控制状态的 run ⇒ cancel ⇒ 409（不伪造行）。"""
        run_id = _seed_agent_run()
        clients = _agent_clients(monkeypatch)
        response = clients["command"].post(
            f"/api/agent-runs/{run_id}/cancel", headers=_AGENT_CMD_HEADERS
        )
        assert response.status_code == 409

    def test_cross_project_run_is_invisible(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """闭环 6：他项目 seed 的 agent run ⇒ Read 404 / List 不可见 /
        cancel 404。"""
        other_run_id = _seed_agent_run(project_id=_OTHER_PROJECT)
        clients = _agent_clients(monkeypatch)
        assert (
            clients["read"]
            .get(
                f"/api/runtime-runs/agent/{other_run_id}",
                headers=_AGENT_READ_HEADERS,
            )
            .status_code
            == 404
        )
        listing = clients["read"].get(
            "/api/runtime-runs/agent", headers=_AGENT_READ_HEADERS
        )
        assert other_run_id not in [
            item["run_id"] for item in listing.json()["items"]
        ]
        assert (
            clients["command"]
            .post(
                f"/api/agent-runs/{other_run_id}/cancel",
                headers=_AGENT_CMD_HEADERS,
            )
            .status_code
            == 404
        )

    def test_decision_then_read_consistency(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """闭环 3（Agent）：decision(APPROVED) ⇒ 幂等；Read 的 run 视图
        与审批记录一致（decision 只改控制状态）。"""
        run_id = _seed_agent_run()
        approval_id = f"ec-p7s5-ap-{uuid.uuid4().hex[:8]}"
        AgentApprovalRepository().create(
            approval_id=approval_id,
            run_id=run_id,
            step_index=0,
            tool_name="get_inventory",
        )
        clients = _agent_clients(monkeypatch)
        decided = clients["command"].post(
            f"/api/agent-approvals/{approval_id}/decision",
            json={"decision": APPROVAL_STATUS_APPROVED},
            headers=_AGENT_CMD_HEADERS,
        )
        assert decided.status_code == 200
        assert decided.json()["approval_status"] == APPROVAL_STATUS_APPROVED
        assert decided.json()["run"]["run_id"] == run_id

        # 幂等：同裁决重复 ⇒ 200
        again = clients["command"].post(
            f"/api/agent-approvals/{approval_id}/decision",
            json={"decision": APPROVAL_STATUS_APPROVED},
            headers=_AGENT_CMD_HEADERS,
        )
        assert again.status_code == 200

        # Read 视图一致（run 行未被 decision 改写）
        detail = clients["read"].get(
            f"/api/runtime-runs/agent/{run_id}", headers=_AGENT_READ_HEADERS
        )
        assert detail.json()["detail"]["run_id"] == run_id
        assert detail.json()["detail"]["approval"]["approval_id"] == approval_id
        assert (
            detail.json()["detail"]["approval"]["status"]
            == APPROVAL_STATUS_APPROVED
        )


# ============================================================
# 残留
# ============================================================


class TestClosureResidueDb:
    def test_prefix_cleanup_leaves_no_rows(self, engine: Any) -> None:
        _delete_prefixes(engine)
        assert _counts(engine) == {"agent_run": 0, "workflow_run": 0}
