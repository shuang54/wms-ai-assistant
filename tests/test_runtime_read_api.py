"""Phase 7 Step 2 / Step 3 — 统一 Runtime 读取 API **HTTP 契约**测试（离线；零 DB）。

覆盖（Phase 7 Step 2 §七 + Phase 7 Step 3 §七 离线项）：

```text
【Step 2 详情】
1  Agent 与 Workflow 运行都能通过统一入口读取（200；公共字段 + 类型专属 detail）
2  runtime_type 路由正确（agent→Agent 域；workflow→Workflow 域；非法⇒400）
3  详情不混淆运行类型（跨类型 run_id ⇒ 404 + 对应域文案）
4  不存在 / 跨项目 ⇒ 404（沿用域文案；与不存在同码同文案）
5  未认证 ⇒ 401；未配置 ⇒ 503（**按需**：本域未配置拒本域，另一域不受影响）
6  客户端 project_id 参数被忽略；授权作用域来自服务端配置（Recording 证）
7  DTO 安全：递归键扫描 + 敏感值扫描 + 不伪造缺失字段
8  错误映射：仓储/存储故障 ⇒ 502；未预期 ⇒ 500（不回传内部文本）
9  与域端点一致性：统一入口 detail == 域端点响应（零重映射）
10 只读面形：OpenAPI 仅 GET；未知写方法 ⇒ 405
【Step 3 列表】
11 Agent / Workflow 列表正常响应（items 概要字段集 + limit/offset/total 回显）
12 空列表
13 分页参数边界（默认 50；1..100；offset >= 0；非法 ⇒ 400）
14 稳定排序（API 不重排——仓储顺序原样透出）
15 两域列表互不混淆（调用独立 + item.runtime_type 纯）
16 未认证 ⇒ 401；未配置 ⇒ 503（按域 fail-closed）
17 客户端 project_id 不能改变授权范围（Recording 证）
18 列表响应不含敏感字段 / 明细字段
19 仓储故障 ⇒ 502；未预期 ⇒ 500
```

注入方式沿用既有范式：monkeypatch 本模块 Composition Root（详情服务 /
列表服务）+ 两域 auth 的 ``get_*_read_settings`` + ``TestClient(app)``；
Fake 基元复用既有测试模块（``tests.test_agent_read_service`` /
``tests.test_workflow_runtime_read_service``）。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api import agent_read as agent_read_module
from backend.app.api import agent_read_auth as agent_auth_module
from backend.app.api import runtime_read as runtime_read_module
from backend.app.api import workflow_read as workflow_read_module
from backend.app.api import workflow_read_auth as workflow_auth_module
from backend.app.config import AgentReadSettings, WorkflowReadSettings, settings
from backend.app.db.agent_approval_repository import AgentApprovalRepositoryError
from backend.app.dto.agent_read_api import AGENT_RUN_RESPONSE_FIELDS
from backend.app.dto.runtime_read_api import (
    RUNTIME_AGENT_RUN_DETAIL_FIELDS,
    RUNTIME_RUN_LIST_FIELDS,
    RUNTIME_RUN_SUMMARY_FIELDS,
    RUNTIME_WORKFLOW_RUN_DETAIL_FIELDS,
)
from backend.app.services.runtime_run_list_service import (
    RuntimeRunListError,
    RuntimeRunListItem,
    RuntimeRunListPage,
    _validate_pagination,
)
from backend.app.dto.workflow_read_api import WORKFLOW_RUN_DETAIL_RESPONSE_FIELDS
from backend.app.main import app
from backend.app.services.agent_read_service import AgentRunNotFoundError
from backend.app.workflow_runtime.ports import WorkflowPortsError
from backend.app.workflow_runtime.read_service import (
    WorkflowReadError,
    WorkflowReadNotFoundError,
)
from tests.test_agent_read_service import (
    FakeApprovalRepository,
    FakeCheckpointRepository,
    FakeDecisionRepository,
    FakeRunRepository,
    FakeStepRepository,
    FakeToolResultRepository,
    approval_read,
    build_service,
    checkpoint_dto,
    decision_record,
    run_record,
    step_read,
    tool_result_read,
)
from tests.test_workflow_runtime_read_service import _Env, _run_record

__all__ = [
    "TestUnifiedAgentDetail",
    "TestUnifiedWorkflowDetail",
    "TestRuntimeTypeRouting",
    "TestNotFoundAndIsolation",
    "TestAuthBoundary",
    "TestDtoSafety",
    "TestErrorMapping",
    "TestSurface",
    "TestRuntimeRunList",
]

_AGENT_RUN_ID = "rt-p7s2-agent-run"
_WF_RUN_ID = "rt-p7s2-wf-run"
_PROJECT = "proj-p7s2"
_OTHER_PROJECT = "proj-p7s2-other"

#: 测试专用凭据（**非**生产凭据；仅存在于测试进程内 monkeypatch 配置）。
_AGENT_KEY = "runtime-agent-key-NOT-A-PRODUCTION-CREDENTIAL"
_WF_KEY = "runtime-workflow-key-NOT-A-PRODUCTION-CREDENTIAL"
_AGENT_HEADERS = {"X-API-Key": _AGENT_KEY}
_WF_HEADERS = {"X-API-Key": _WF_KEY}

#: 绝不允许出现在响应 JSON 键中的字段（合并两域既有禁止清单）。
#: 注意：``request_id`` 是 Agent 域 DTO 的**合法**字段（S8A 锁定），仅
#: Workflow 域禁止 ⇒ agent 场景检查时剔除（见 _AGENT_FORBIDDEN_KEYS）。
_FORBIDDEN_KEYS = {
    "input",
    "request_id",
    "claim_token",
    "arguments",
    "content",
    "result_payload",
    "result_ref",
    "payload",
    "prompt",
    "system_prompt",
    "messages",
    "context",
    "sql",
    "password",
    "authorization",
    "api_key",
    "database_url",
    "raw_response",
    "exception",
    "stack",
    "metadata",
    "session",
    "connection",
    "secret_input",
}

#: Agent 场景禁止键（剔除 Agent DTO 合法字段 request_id）。
_AGENT_FORBIDDEN_KEYS = _FORBIDDEN_KEYS - {"request_id"}

#: 敏感值（Fake 数据内置；不得出现在任何响应文本中）。
_SECRET_INPUT = "DO-NOT-EXPOSE"
_SECRET_URL = "postgresql://user:SUPER-SECRET@host/db"


# ============================================================
# Fake / 注入辅助（复用既有测试基元）
# ============================================================


class _RecordingAgentService:
    """委托真实 Service + 记录 (run_id, project_id)（作用域断言用）。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[tuple[str, str]] = []

    def get_run(self, run_id: str, project_id: str) -> Any:
        self.calls.append((run_id, project_id))
        return self._inner.get_run(run_id, project_id)


class _FailingAgentService:
    """get_run 恒抛指定异常（502 / 500 映射用）。"""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def get_run(self, run_id: str, project_id: str) -> Any:
        raise self._exc


def _agent_service(*, project_id: str = _PROJECT) -> Any:
    """完整 Agent 只读服务（六仓储 Fake；记录属授权项目）。"""
    service, _repositories = build_service(
        run=FakeRunRepository(
            {_AGENT_RUN_ID: run_record(run_id=_AGENT_RUN_ID, project_id=project_id)}
        ),
        step=FakeStepRepository([step_read(step_index=0)]),
        decision=FakeDecisionRepository([decision_record(step_index=0)]),
        tool_result=FakeToolResultRepository(
            [replace(tool_result_read(step_index=0), project_id=project_id)]
        ),
        checkpoint=FakeCheckpointRepository({_AGENT_RUN_ID: checkpoint_dto()}),
        approval=FakeApprovalRepository([approval_read(step_index=0)]),
    )
    return service


def _wf_service(*, project_id: str = _PROJECT) -> Any:
    """完整 Workflow 只读服务（三 Fake Store；记录属授权项目）。"""
    return _Env(
        run=_run_record(workflow_run_id=_WF_RUN_ID, project_id=project_id)
    ).service


def _agent_client(
    monkeypatch: pytest.MonkeyPatch,
    service: Any = None,
    *,
    auth_settings: AgentReadSettings | None = None,
) -> TestClient:
    """统一入口客户端（Agent 域）：替换本模块 + 域模块 Composition Root + Agent auth 配置。"""
    monkeypatch.setattr(
        runtime_read_module,
        "get_agent_read_service",
        lambda: service if service is not None else _agent_service(),
    )
    monkeypatch.setattr(
        agent_read_module,
        "get_agent_read_service",
        lambda: service if service is not None else _agent_service(),
    )
    monkeypatch.setattr(
        agent_auth_module,
        "get_agent_read_settings",
        lambda: auth_settings
        or AgentReadSettings(api_key=_AGENT_KEY, api_project_id=_PROJECT),
    )
    return TestClient(app)


def _wf_client(
    monkeypatch: pytest.MonkeyPatch,
    service: Any = None,
    *,
    auth_settings: WorkflowReadSettings | None = None,
) -> TestClient:
    """统一入口客户端（Workflow 域）：替换本模块 + 域模块 Composition Root + Workflow auth 配置。"""
    monkeypatch.setattr(
        runtime_read_module,
        "get_workflow_read_service",
        lambda: service if service is not None else _wf_service(),
    )
    monkeypatch.setattr(
        workflow_read_module,
        "get_workflow_read_service",
        lambda: service if service is not None else _wf_service(),
    )
    monkeypatch.setattr(
        workflow_auth_module,
        "get_workflow_read_settings",
        lambda: auth_settings
        or WorkflowReadSettings(api_key=_WF_KEY, api_project_id=_PROJECT),
    )
    return TestClient(app)


def _iter_keys(node: Any):
    """递归遍历 JSON 结构的所有 dict 键。"""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _iter_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_keys(item)


# ============================================================
# Test 1：Agent 统一详情
# ============================================================


class TestUnifiedAgentDetail:
    def test_agent_run_readable_via_unified_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _agent_client(monkeypatch)
        response = client.get(
            f"/api/runtime-runs/agent/{_AGENT_RUN_ID}", headers=_AGENT_HEADERS
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["runtime_type"] == "agent"
        assert payload["run_id"] == _AGENT_RUN_ID
        assert payload["project_id"] == _PROJECT
        assert payload["status"] is not None
        assert payload["started_at"] is not None

    def test_public_fields_have_exact_field_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _agent_client(monkeypatch)
        payload = client.get(
            f"/api/runtime-runs/agent/{_AGENT_RUN_ID}", headers=_AGENT_HEADERS
        ).json()
        assert tuple(payload.keys()) == RUNTIME_AGENT_RUN_DETAIL_FIELDS
        assert RUNTIME_RUN_SUMMARY_FIELDS == (
            "runtime_type",
            "run_id",
            "project_id",
            "status",
            "started_at",
        )

    def test_agent_detail_has_exact_domain_field_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _agent_client(monkeypatch)
        payload = client.get(
            f"/api/runtime-runs/agent/{_AGENT_RUN_ID}", headers=_AGENT_HEADERS
        ).json()
        assert tuple(payload["detail"].keys()) == AGENT_RUN_RESPONSE_FIELDS

    def test_unified_detail_equals_domain_endpoint_response(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """统一入口 detail 与域端点响应**逐字节一致**（零重映射）。"""
        client = _agent_client(monkeypatch)
        unified = client.get(
            f"/api/runtime-runs/agent/{_AGENT_RUN_ID}", headers=_AGENT_HEADERS
        ).json()
        domain = client.get(
            f"/api/agent/runs/{_AGENT_RUN_ID}", headers=_AGENT_HEADERS
        ).json()
        assert unified["detail"] == domain
        assert unified["run_id"] == domain["run_id"]
        assert unified["status"] == domain["status"]
        assert unified["started_at"] == domain["started_at"]


# ============================================================
# Test 2：Workflow 统一详情
# ============================================================


class TestUnifiedWorkflowDetail:
    def test_workflow_run_readable_via_unified_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _wf_client(monkeypatch)
        response = client.get(
            f"/api/runtime-runs/workflow/{_WF_RUN_ID}", headers=_WF_HEADERS
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["runtime_type"] == "workflow"
        assert payload["run_id"] == _WF_RUN_ID
        assert payload["project_id"] == _PROJECT
        assert payload["status"] == "RUNNING"

    def test_public_fields_have_exact_field_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _wf_client(monkeypatch)
        payload = client.get(
            f"/api/runtime-runs/workflow/{_WF_RUN_ID}", headers=_WF_HEADERS
        ).json()
        assert tuple(payload.keys()) == RUNTIME_WORKFLOW_RUN_DETAIL_FIELDS

    def test_workflow_detail_has_exact_domain_field_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _wf_client(monkeypatch)
        payload = client.get(
            f"/api/runtime-runs/workflow/{_WF_RUN_ID}", headers=_WF_HEADERS
        ).json()
        assert tuple(payload["detail"].keys()) == WORKFLOW_RUN_DETAIL_RESPONSE_FIELDS

    def test_unified_detail_equals_domain_endpoint_response(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """统一入口 detail 与域端点响应**逐字节一致**（零重映射）。"""
        client = _wf_client(monkeypatch)
        unified = client.get(
            f"/api/runtime-runs/workflow/{_WF_RUN_ID}", headers=_WF_HEADERS
        ).json()
        domain = client.get(
            f"/api/workflow-runs/{_WF_RUN_ID}", headers=_WF_HEADERS
        ).json()
        assert unified["detail"] == domain
        assert unified["run_id"] == domain["workflow_run_id"]
        assert unified["status"] == domain["status"]


# ============================================================
# Test 3：runtime_type 路由与类型不混淆
# ============================================================


class TestRuntimeTypeRouting:
    def test_agent_type_reads_agent_domain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        agent_client = _agent_client(monkeypatch)
        # workflow 域未注入 ⇒ agent 路径不触达 workflow 服务（纯路由证）
        payload = agent_client.get(
            f"/api/runtime-runs/agent/{_AGENT_RUN_ID}", headers=_AGENT_HEADERS
        ).json()
        assert payload["runtime_type"] == "agent"
        assert payload["detail"]["run_id"] == _AGENT_RUN_ID

    def test_workflow_type_reads_workflow_domain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wf_client = _wf_client(monkeypatch)
        payload = wf_client.get(
            f"/api/runtime-runs/workflow/{_WF_RUN_ID}", headers=_WF_HEADERS
        ).json()
        assert payload["runtime_type"] == "workflow"
        assert payload["detail"]["workflow_run_id"] == _WF_RUN_ID

    def test_agent_run_id_under_workflow_type_is_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Agent run_id 不会在 workflow 类型下被误读（域数据不混淆）。"""
        client = _wf_client(monkeypatch)
        response = client.get(
            f"/api/runtime-runs/workflow/{_AGENT_RUN_ID}", headers=_WF_HEADERS
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Workflow Run 不存在"

    def test_workflow_run_id_under_agent_type_is_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Workflow run_id 不会在 agent 类型下被误读（域数据不混淆）。"""
        client = _agent_client(monkeypatch)
        response = client.get(
            f"/api/runtime-runs/agent/{_WF_RUN_ID}", headers=_AGENT_HEADERS
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Agent Run 不存在"

    @pytest.mark.parametrize("bad_type", ["Agent", "AGENT", "workflows", "unknown"])
    def test_unknown_runtime_type_is_bad_request(
        self, monkeypatch: pytest.MonkeyPatch, bad_type: str
    ) -> None:
        client = _agent_client(monkeypatch)
        response = client.get(
            f"/api/runtime-runs/{bad_type}/{_AGENT_RUN_ID}", headers=_AGENT_HEADERS
        )
        assert response.status_code == 400
        assert "runtime_type" in response.json()["detail"]

    def test_blank_runtime_type_is_bad_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """纯空白 runtime_type（长度过 Path 校验）⇒ 白名单拦截 400（固定文案）。"""
        client = _agent_client(monkeypatch)
        response = client.get(
            f"/api/runtime-runs/%20/{_AGENT_RUN_ID}", headers=_AGENT_HEADERS
        )
        assert response.status_code == 400
        assert "runtime_type" in response.json()["detail"]


# ============================================================
# Test 4：不存在 / 跨项目（沿用域语义）
# ============================================================


class TestNotFoundAndIsolation:
    def test_missing_agent_run_is_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _agent_client(monkeypatch)
        response = client.get(
            "/api/runtime-runs/agent/rt-does-not-exist", headers=_AGENT_HEADERS
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Agent Run 不存在"

    def test_missing_workflow_run_is_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _wf_client(monkeypatch)
        response = client.get(
            "/api/runtime-runs/workflow/rt-does-not-exist", headers=_WF_HEADERS
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Workflow Run 不存在"

    def test_cross_project_agent_run_shares_not_found_shape(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """跨项目 ⇒ 404 + 与不存在**同文案**（不泄露存在性；沿用域端点语义）。"""
        authorized = _agent_client(monkeypatch)
        missing = authorized.get(
            "/api/runtime-runs/agent/rt-missing", headers=_AGENT_HEADERS
        )
        foreign_client = _agent_client(
            monkeypatch, _agent_service(project_id=_OTHER_PROJECT)
        )
        foreign = foreign_client.get(
            f"/api/runtime-runs/agent/{_AGENT_RUN_ID}", headers=_AGENT_HEADERS
        )
        assert foreign.status_code == 404
        assert foreign.json() == missing.json()

    def test_cross_project_workflow_run_shares_not_found_shape(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        authorized = _wf_client(monkeypatch)
        missing = authorized.get(
            "/api/runtime-runs/workflow/rt-missing", headers=_WF_HEADERS
        )
        foreign_client = _wf_client(
            monkeypatch, _wf_service(project_id=_OTHER_PROJECT)
        )
        foreign = foreign_client.get(
            f"/api/runtime-runs/workflow/{_WF_RUN_ID}", headers=_WF_HEADERS
        )
        assert foreign.status_code == 404
        assert foreign.json() == missing.json()


# ============================================================
# Test 5：认证与项目隔离（按域复用）
# ============================================================


class TestAuthBoundary:
    def test_agent_entry_without_credential_is_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _agent_client(monkeypatch)
        response = client.get(f"/api/runtime-runs/agent/{_AGENT_RUN_ID}")
        assert response.status_code == 401

    def test_workflow_entry_without_credential_is_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _wf_client(monkeypatch)
        response = client.get(f"/api/runtime-runs/workflow/{_WF_RUN_ID}")
        assert response.status_code == 401

    def test_agent_entry_with_wrong_credential_is_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _agent_client(monkeypatch)
        response = client.get(
            f"/api/runtime-runs/agent/{_AGENT_RUN_ID}",
            headers={"X-API-Key": "wrong-key"},
        )
        assert response.status_code == 401

    def test_workflow_key_cannot_open_agent_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """两域凭据独立：workflow key 打不开 agent 入口（不互相放行）。"""
        client = _agent_client(monkeypatch)
        response = client.get(
            f"/api/runtime-runs/agent/{_AGENT_RUN_ID}", headers=_WF_HEADERS
        )
        assert response.status_code == 401

    def test_agent_unconfigured_rejects_only_agent_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """§五.4 按需 fail-closed：agent 域未配置 ⇒ agent 503，
        但 workflow 域**不受影响**（未涉及的域不要求配置）。"""
        _agent_client(monkeypatch, auth_settings=AgentReadSettings())
        _wf_client(monkeypatch)
        agent_response = TestClient(app).get(
            f"/api/runtime-runs/agent/{_AGENT_RUN_ID}", headers=_AGENT_HEADERS
        )
        assert agent_response.status_code == 503
        wf_response = TestClient(app).get(
            f"/api/runtime-runs/workflow/{_WF_RUN_ID}", headers=_WF_HEADERS
        )
        assert wf_response.status_code == 200

    def test_workflow_unconfigured_rejects_only_workflow_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _agent_client(monkeypatch)
        _wf_client(monkeypatch, auth_settings=WorkflowReadSettings())
        agent_response = TestClient(app).get(
            f"/api/runtime-runs/agent/{_AGENT_RUN_ID}", headers=_AGENT_HEADERS
        )
        assert agent_response.status_code == 200
        wf_response = TestClient(app).get(
            f"/api/runtime-runs/workflow/{_WF_RUN_ID}", headers=_WF_HEADERS
        )
        assert wf_response.status_code == 503

    def test_authorized_scope_comes_from_server_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """project_id 来自服务端认证配置——非 Query、非项目默认回退。"""
        assert _PROJECT != settings.project.project_id  # 非默认回退
        recording = _RecordingAgentService(_agent_service())
        client = _agent_client(monkeypatch, recording)
        response = client.get(
            f"/api/runtime-runs/agent/{_AGENT_RUN_ID}", headers=_AGENT_HEADERS
        )
        assert response.status_code == 200
        assert recording.calls == [(_AGENT_RUN_ID, _PROJECT)]

    def test_client_project_id_query_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """客户端 project_id 参数**不**参与授权（被忽略——作用域不可扩宽）。"""
        recording = _RecordingAgentService(_agent_service())
        client = _agent_client(monkeypatch, recording)
        response = client.get(
            f"/api/runtime-runs/agent/{_AGENT_RUN_ID}",
            params={"project_id": _OTHER_PROJECT},
            headers=_AGENT_HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["project_id"] == _PROJECT
        assert recording.calls == [(_AGENT_RUN_ID, _PROJECT)]


# ============================================================
# Test 6：DTO 安全（不泄露 / 不伪造）
# ============================================================


class TestDtoSafety:
    def test_agent_response_has_no_forbidden_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _agent_client(monkeypatch)
        payload = client.get(
            f"/api/runtime-runs/agent/{_AGENT_RUN_ID}", headers=_AGENT_HEADERS
        ).json()
        leaked = _AGENT_FORBIDDEN_KEYS & set(_iter_keys(payload))
        assert not leaked

    def test_workflow_response_has_no_forbidden_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _wf_client(monkeypatch)
        payload = client.get(
            f"/api/runtime-runs/workflow/{_WF_RUN_ID}", headers=_WF_HEADERS
        ).json()
        leaked = _FORBIDDEN_KEYS & set(_iter_keys(payload))
        assert not leaked

    def test_sensitive_values_never_appear_in_response_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fake 数据内置敏感值不得出现在任何响应文本中。"""
        wf_client = _wf_client(monkeypatch)
        wf_text = wf_client.get(
            f"/api/runtime-runs/workflow/{_WF_RUN_ID}", headers=_WF_HEADERS
        ).text
        assert _SECRET_INPUT not in wf_text
        assert _SECRET_URL not in wf_text
        agent_client = _agent_client(monkeypatch)
        agent_text = agent_client.get(
            f"/api/runtime-runs/agent/{_AGENT_RUN_ID}", headers=_AGENT_HEADERS
        ).text
        assert _SECRET_URL not in agent_text

    def test_agent_detail_does_not_fabricate_created_or_updated_at(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Agent 无 created_at / updated_at ⇒ 统一 DTO **不引入**该键（不伪造）。"""
        client = _agent_client(monkeypatch)
        payload = client.get(
            f"/api/runtime-runs/agent/{_AGENT_RUN_ID}", headers=_AGENT_HEADERS
        ).json()
        assert "created_at" not in payload
        assert "updated_at" not in payload
        assert "created_at" not in payload["detail"]
        assert "updated_at" not in payload["detail"]

    def test_workflow_detail_excludes_input_and_request_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _wf_client(monkeypatch)
        payload = client.get(
            f"/api/runtime-runs/workflow/{_WF_RUN_ID}", headers=_WF_HEADERS
        ).json()
        assert "input" not in payload["detail"]
        assert "request_id" not in payload["detail"]

    def test_status_is_not_normalized_across_domains(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """两域 status 原样透传（同一字符串不做跨域语义归一化）。"""
        wf_client = _wf_client(monkeypatch)
        wf_status = wf_client.get(
            f"/api/runtime-runs/workflow/{_WF_RUN_ID}", headers=_WF_HEADERS
        ).json()["status"]
        agent_client = _agent_client(monkeypatch)
        agent_status = agent_client.get(
            f"/api/runtime-runs/agent/{_AGENT_RUN_ID}", headers=_AGENT_HEADERS
        ).json()["status"]
        assert wf_status == "RUNNING"
        assert agent_status  # 非空（Agent 终态记录真实状态）
        assert isinstance(wf_status, str) and isinstance(agent_status, str)


# ============================================================
# Test 7：错误映射（502 / 500）
# ============================================================


class TestErrorMapping:
    def test_agent_repository_failure_is_502(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _agent_client(
            monkeypatch,
            _FailingAgentService(AgentApprovalRepositoryError("db down")),
        )
        response = client.get(
            f"/api/runtime-runs/agent/{_AGENT_RUN_ID}", headers=_AGENT_HEADERS
        )
        assert response.status_code == 502
        assert "db down" not in response.text  # 不回传内部异常文本

    def test_agent_unexpected_failure_is_500(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _agent_client(monkeypatch, _FailingAgentService(RuntimeError("boom")))
        response = client.get(
            f"/api/runtime-runs/agent/{_AGENT_RUN_ID}", headers=_AGENT_HEADERS
        )
        assert response.status_code == 500
        assert "boom" not in response.text

    def test_workflow_store_failure_is_502(self, monkeypatch: pytest.MonkeyPatch) -> None:
        env = _Env(run=_run_record(workflow_run_id=_WF_RUN_ID, project_id=_PROJECT))
        env.run_store.error = WorkflowPortsError("store down")
        client = _wf_client(monkeypatch, env.service)
        response = client.get(
            f"/api/runtime-runs/workflow/{_WF_RUN_ID}", headers=_WF_HEADERS
        )
        assert response.status_code == 502
        assert "store down" not in response.text

    def test_workflow_unexpected_failure_is_mapped_safely(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Workflow Read Service 将存储异常收敛为 WorkflowReadError ⇒ 502
        （与域端点行为一致；不回传内部文本）。"""
        env = _Env(run=_run_record(workflow_run_id=_WF_RUN_ID, project_id=_PROJECT))
        env.run_store.error = RuntimeError("boom")
        client = _wf_client(monkeypatch, env.service)
        response = client.get(
            f"/api/runtime-runs/workflow/{_WF_RUN_ID}", headers=_WF_HEADERS
        )
        assert response.status_code == 502
        assert "boom" not in response.text

    def test_not_found_error_classes_are_importable(self) -> None:
        assert issubclass(AgentRunNotFoundError, Exception)
        assert issubclass(WorkflowReadNotFoundError, WorkflowReadError)


# ============================================================
# Test 8：只读面形（不新增执行能力）
# ============================================================


class TestSurface:
    def test_runtime_paths_are_get_only(self) -> None:
        schema = app.openapi()
        runtime_paths = {
            path: methods
            for path, methods in schema["paths"].items()
            if path.startswith("/api/runtime-runs")
        }
        assert runtime_paths, "统一 Runtime 读取端点必须注册"
        for methods in runtime_paths.values():
            assert set(methods) == {"get"}

    def test_write_method_on_unified_entry_is_405(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _agent_client(monkeypatch)
        response = client.post(
            f"/api/runtime-runs/agent/{_AGENT_RUN_ID}", headers=_AGENT_HEADERS
        )
        assert response.status_code == 405

    def test_domain_endpoints_not_regressed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """统一入口不破坏既有域只读面（sanity；全量回归由套件承担）。"""
        agent_client = _agent_client(monkeypatch)
        agent_response = agent_client.get(
            f"/api/agent/runs/{_AGENT_RUN_ID}", headers=_AGENT_HEADERS
        )
        assert agent_response.status_code == 200
        wf_client = _wf_client(monkeypatch)
        wf_response = wf_client.get(
            f"/api/workflow-runs/{_WF_RUN_ID}", headers=_WF_HEADERS
        )
        assert wf_response.status_code == 200


# ============================================================
# Phase 7 Step 3：统一 Runtime Run 列表（离线）
# ============================================================


@dataclass(frozen=True)
class _FakeListCall:
    domain: str
    project_id: str
    limit: int | None
    offset: int


class _FakeListService:
    """列表服务 Fake：记录调用（项目隔离断言）+ 可控返回 / 异常。

    分页校验**复用真实 service 的 ``_validate_pagination``**（模拟 service
    契约：API 层不重复校验，400 语义来自服务层单一校验点）。
    """

    def __init__(
        self,
        *,
        agent_page: RuntimeRunListPage | None = None,
        workflow_page: RuntimeRunListPage | None = None,
        agent_error: BaseException | None = None,
        workflow_error: BaseException | None = None,
    ) -> None:
        self._agent_page = agent_page
        self._workflow_page = workflow_page
        self._agent_error = agent_error
        self._workflow_error = workflow_error
        self.calls: list[_FakeListCall] = []

    def list_agent_runs(
        self, *, project_id: str, limit: int | None = None, offset: int = 0
    ) -> RuntimeRunListPage:
        validated_limit, validated_offset = _validate_pagination(limit, offset)
        self.calls.append(
            _FakeListCall("agent", project_id, validated_limit, validated_offset)
        )
        if self._agent_error is not None:
            raise self._agent_error
        if self._agent_page is not None:
            return self._agent_page
        return RuntimeRunListPage(
            items=(), limit=validated_limit, offset=validated_offset, total=0
        )

    def list_workflow_runs(
        self, *, project_id: str, limit: int | None = None, offset: int = 0
    ) -> RuntimeRunListPage:
        validated_limit, validated_offset = _validate_pagination(limit, offset)
        self.calls.append(
            _FakeListCall("workflow", project_id, validated_limit, validated_offset)
        )
        if self._workflow_error is not None:
            raise self._workflow_error
        if self._workflow_page is not None:
            return self._workflow_page
        return RuntimeRunListPage(
            items=(), limit=validated_limit, offset=validated_offset, total=0
        )


def _agent_page(
    *, count: int = 2, total: int | None = None
) -> RuntimeRunListPage:
    items = tuple(
        RuntimeRunListItem(
            runtime_type="agent",
            run_id=f"rt-list-agent-{index}",
            project_id=_PROJECT,
            status="COMPLETED",
            started_at=datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc),
        )
        for index in range(count)
    )
    return RuntimeRunListPage(
        items=items,
        limit=50,
        offset=0,
        total=total if total is not None else count,
    )


def _workflow_page(
    *, count: int = 2, total: int | None = None
) -> RuntimeRunListPage:
    items = tuple(
        RuntimeRunListItem(
            runtime_type="workflow",
            run_id=f"rt-list-wf-{index}",
            project_id=_PROJECT,
            status="PENDING",
            started_at=None,
        )
        for index in range(count)
    )
    return RuntimeRunListPage(
        items=items,
        limit=50,
        offset=0,
        total=total if total is not None else count,
    )


def _list_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    domain: str,
    service: _FakeListService,
    auth_settings: AgentReadSettings | WorkflowReadSettings | None = None,
) -> TestClient:
    """列表客户端：替换列表 Composition Root + 对应域 auth 配置。"""
    monkeypatch.setattr(
        runtime_read_module, "get_runtime_run_list_service", lambda: service
    )
    if domain == "agent":
        monkeypatch.setattr(
            agent_auth_module,
            "get_agent_read_settings",
            lambda: auth_settings
            or AgentReadSettings(api_key=_AGENT_KEY, api_project_id=_PROJECT),
        )
    else:
        monkeypatch.setattr(
            workflow_auth_module,
            "get_workflow_read_settings",
            lambda: auth_settings
            or WorkflowReadSettings(api_key=_WF_KEY, api_project_id=_PROJECT),
        )
    return TestClient(app)


class TestRuntimeRunList:
    # ---------- 正常响应 ----------

    def test_agent_list_returns_summary_items(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = _FakeListService(agent_page=_agent_page(count=2, total=5))
        client = _list_client(monkeypatch, domain="agent", service=service)
        response = client.get("/api/runtime-runs/agent", headers=_AGENT_HEADERS)
        assert response.status_code == 200
        payload = response.json()
        assert tuple(payload.keys()) == RUNTIME_RUN_LIST_FIELDS
        assert payload["limit"] == 50
        assert payload["offset"] == 0
        assert payload["total"] == 5
        assert len(payload["items"]) == 2
        for item in payload["items"]:
            assert tuple(item.keys()) == RUNTIME_RUN_SUMMARY_FIELDS
            assert item["runtime_type"] == "agent"
            assert item["project_id"] == _PROJECT
            assert "detail" not in item  # 列表不含类型专属详情

    def test_workflow_list_returns_summary_items(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = _FakeListService(workflow_page=_workflow_page(count=1, total=3))
        client = _list_client(monkeypatch, domain="workflow", service=service)
        response = client.get("/api/runtime-runs/workflow", headers=_WF_HEADERS)
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert len(payload["items"]) == 1
        assert payload["items"][0]["runtime_type"] == "workflow"
        assert payload["items"][0]["status"] == "PENDING"

    def test_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service = _FakeListService()
        client = _list_client(monkeypatch, domain="agent", service=service)
        response = client.get("/api/runtime-runs/agent", headers=_AGENT_HEADERS)
        assert response.status_code == 200
        payload = response.json()
        assert payload["items"] == []
        assert payload["total"] == 0
        assert payload["limit"] == 50
        assert payload["offset"] == 0

    # ---------- 分页 ----------

    def test_default_pagination_is_50(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """默认 limit=50 由**服务层**生效（API 透传 None；server-side 默认）。"""
        service = _FakeListService(agent_page=_agent_page())
        client = _list_client(monkeypatch, domain="agent", service=service)
        client.get("/api/runtime-runs/agent", headers=_AGENT_HEADERS)
        assert service.calls == [_FakeListCall("agent", _PROJECT, 50, 0)]

    @pytest.mark.parametrize("limit", [1, 50, 100])
    def test_valid_limits_pass_through(
        self, monkeypatch: pytest.MonkeyPatch, limit: int
    ) -> None:
        service = _FakeListService(agent_page=_agent_page())
        client = _list_client(monkeypatch, domain="agent", service=service)
        response = client.get(
            "/api/runtime-runs/agent",
            params={"limit": limit},
            headers=_AGENT_HEADERS,
        )
        assert response.status_code == 200
        assert service.calls[-1].limit == limit

    @pytest.mark.parametrize("limit", [0, -1, 101, 1000000])
    def test_invalid_limits_are_bad_request(
        self, monkeypatch: pytest.MonkeyPatch, limit: int
    ) -> None:
        service = _FakeListService(agent_page=_agent_page())
        client = _list_client(monkeypatch, domain="agent", service=service)
        response = client.get(
            "/api/runtime-runs/agent",
            params={"limit": limit},
            headers=_AGENT_HEADERS,
        )
        assert response.status_code == 400
        assert "非法输入" in response.json()["detail"]

    def test_negative_offset_is_bad_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = _FakeListService(agent_page=_agent_page())
        client = _list_client(monkeypatch, domain="agent", service=service)
        response = client.get(
            "/api/runtime-runs/agent",
            params={"offset": -1},
            headers=_AGENT_HEADERS,
        )
        assert response.status_code == 400

    def test_offset_passes_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service = _FakeListService(agent_page=_agent_page())
        client = _list_client(monkeypatch, domain="agent", service=service)
        response = client.get(
            "/api/runtime-runs/agent",
            params={"limit": 10, "offset": 20},
            headers=_AGENT_HEADERS,
        )
        assert response.status_code == 200
        assert service.calls[-1] == _FakeListCall("agent", _PROJECT, 10, 20)

    def test_api_does_not_reorder_items(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """稳定排序由仓储保证；API 层**不重排**（顺序原样透出）。"""
        page = RuntimeRunListPage(
            items=(
                RuntimeRunListItem(
                    runtime_type="agent",
                    run_id="rt-order-b",
                    project_id=_PROJECT,
                    status="COMPLETED",
                    started_at=datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc),
                ),
                RuntimeRunListItem(
                    runtime_type="agent",
                    run_id="rt-order-a",
                    project_id=_PROJECT,
                    status="COMPLETED",
                    started_at=datetime(2026, 10, 13, 11, 0, tzinfo=timezone.utc),
                ),
            ),
            limit=50,
            offset=0,
            total=2,
        )
        client = _list_client(
            monkeypatch, domain="agent", service=_FakeListService(agent_page=page)
        )
        payload = client.get(
            "/api/runtime-runs/agent", headers=_AGENT_HEADERS
        ).json()
        assert [item["run_id"] for item in payload["items"]] == [
            "rt-order-b",
            "rt-order-a",
        ]

    # ---------- 两域不混淆 ----------

    def test_agent_list_does_not_touch_workflow_domain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = _FakeListService(agent_page=_agent_page())
        client = _list_client(monkeypatch, domain="agent", service=service)
        client.get("/api/runtime-runs/agent", headers=_AGENT_HEADERS)
        assert [c.domain for c in service.calls] == ["agent"]

    def test_workflow_list_does_not_touch_agent_domain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = _FakeListService(workflow_page=_workflow_page())
        client = _list_client(monkeypatch, domain="workflow", service=service)
        client.get("/api/runtime-runs/workflow", headers=_WF_HEADERS)
        assert [c.domain for c in service.calls] == ["workflow"]

    def test_agent_list_items_are_all_agent_typed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _list_client(
            monkeypatch, domain="agent", service=_FakeListService(agent_page=_agent_page())
        )
        payload = client.get(
            "/api/runtime-runs/agent", headers=_AGENT_HEADERS
        ).json()
        assert {item["runtime_type"] for item in payload["items"]} == {"agent"}

    # ---------- 认证与项目隔离 ----------

    def test_agent_list_without_credential_is_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _list_client(
            monkeypatch, domain="agent", service=_FakeListService()
        )
        assert client.get("/api/runtime-runs/agent").status_code == 401

    def test_workflow_list_without_credential_is_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _list_client(
            monkeypatch, domain="workflow", service=_FakeListService()
        )
        assert client.get("/api/runtime-runs/workflow").status_code == 401

    def test_agent_list_unconfigured_is_503_while_workflow_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """§四 按域 fail-closed：agent 域未配置 ⇒ agent 列表 503；
        workflow 域不受影响（未涉及的域不要求配置）。"""
        _list_client(
            monkeypatch,
            domain="agent",
            service=_FakeListService(),
            auth_settings=AgentReadSettings(),
        )
        _list_client(
            monkeypatch,
            domain="workflow",
            service=_FakeListService(workflow_page=_workflow_page()),
        )
        assert (
            TestClient(app)
            .get("/api/runtime-runs/agent", headers=_AGENT_HEADERS)
            .status_code
            == 503
        )
        assert (
            TestClient(app)
            .get("/api/runtime-runs/workflow", headers=_WF_HEADERS)
            .status_code
            == 200
        )

    def test_client_project_id_cannot_widen_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """客户端 project_id 查询参数**不**参与授权（服务层只收到授权 scope）。"""
        service = _FakeListService(agent_page=_agent_page())
        client = _list_client(monkeypatch, domain="agent", service=service)
        response = client.get(
            "/api/runtime-runs/agent",
            params={"project_id": _OTHER_PROJECT},
            headers=_AGENT_HEADERS,
        )
        assert response.status_code == 200
        assert service.calls[-1].project_id == _PROJECT

    def test_scope_comes_from_server_config_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = _FakeListService(agent_page=_agent_page())
        client = _list_client(monkeypatch, domain="agent", service=service)
        client.get("/api/runtime-runs/agent", headers=_AGENT_HEADERS)
        assert service.calls == [_FakeListCall("agent", _PROJECT, 50, 0)]

    # ---------- DTO 安全 ----------

    def test_list_response_has_no_forbidden_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _list_client(
            monkeypatch, domain="agent", service=_FakeListService(agent_page=_agent_page())
        )
        payload = client.get(
            "/api/runtime-runs/agent", headers=_AGENT_HEADERS
        ).json()
        leaked = _AGENT_FORBIDDEN_KEYS & set(_iter_keys(payload))
        assert not leaked

    def test_list_items_have_no_detail_or_steps(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """列表项只含概要（无 detail / steps / nodes / approvals / input）。"""
        client = _list_client(
            monkeypatch, domain="agent", service=_FakeListService(agent_page=_agent_page())
        )
        payload = client.get(
            "/api/runtime-runs/agent", headers=_AGENT_HEADERS
        ).json()
        for item in payload["items"]:
            assert set(item.keys()) == set(RUNTIME_RUN_SUMMARY_FIELDS)

    def test_sensitive_values_never_appear_in_list_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _list_client(
            monkeypatch,
            domain="workflow",
            service=_FakeListService(workflow_page=_workflow_page()),
        )
        text = client.get(
            "/api/runtime-runs/workflow", headers=_WF_HEADERS
        ).text
        assert _SECRET_INPUT not in text
        assert _SECRET_URL not in text

    # ---------- 错误映射 ----------

    def test_agent_list_repository_failure_is_502(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _list_client(
            monkeypatch,
            domain="agent",
            service=_FakeListService(agent_error=RuntimeRunListError("db down")),
        )
        response = client.get("/api/runtime-runs/agent", headers=_AGENT_HEADERS)
        assert response.status_code == 502
        assert "db down" not in response.text

    def test_workflow_list_repository_failure_is_502(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _list_client(
            monkeypatch,
            domain="workflow",
            service=_FakeListService(workflow_error=RuntimeRunListError("store down")),
        )
        response = client.get("/api/runtime-runs/workflow", headers=_WF_HEADERS)
        assert response.status_code == 502
        assert "store down" not in response.text

    def test_unexpected_failure_is_500(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _list_client(
            monkeypatch,
            domain="agent",
            service=_FakeListService(agent_error=RuntimeError("boom")),
        )
        response = client.get("/api/runtime-runs/agent", headers=_AGENT_HEADERS)
        assert response.status_code == 500
        assert "boom" not in response.text

    # ---------- 只读面形 ----------

    def test_list_paths_are_get_only(self) -> None:
        schema = app.openapi()
        list_paths = {
            path: methods
            for path, methods in schema["paths"].items()
            if path in ("/api/runtime-runs/agent", "/api/runtime-runs/workflow")
        }
        assert set(list_paths) == {
            "/api/runtime-runs/agent",
            "/api/runtime-runs/workflow",
        }
        for methods in list_paths.values():
            assert set(methods) == {"get"}

    def test_write_method_on_list_path_is_405(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _list_client(
            monkeypatch, domain="agent", service=_FakeListService()
        )
        assert (
            client.post("/api/runtime-runs/agent", headers=_AGENT_HEADERS).status_code
            == 405
        )
