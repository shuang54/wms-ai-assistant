"""Phase 7 Step 1 — Workflow Command API 离线测试（认证 / 错误映射 / DTO 边界）。

全离线：命令服务以 Fake 注入（monkeypatch Composition Root），**零真实 DB /
零真实 LLM / 零真实 Tool**。DB 真链路见 ``test_workflow_command_api_db.py``。

覆盖：
```text
认证：缺失 401 · 错误 401 · 未配置 503 · 空 project 503（fail closed）
命令：start / advance / cancel / decision 正常路径（200 + outcome + run 视图）
错误映射：400（输入契约）· 404（NotFound / 定义不存在——同码同构）·
          409（CONFLICT / APPROVAL_CONFLICT / NOT_CANCELLABLE / UNKNOWN /
              定义版本不匹配）· 422（请求体校验）· 502（读边界不可用）·
          500（未预期异常，固定文案）
DTO 边界：响应字段集精确（顶层 + run 视图）· 无 input / request_id /
          claim_token 等敏感键 · 不泄露凭据
作用域：project_id 只来自服务端配置（无 Query / Header / Body 参数；
        客户端提交的 ?project_id= 被忽略）
OpenAPI：4 个命令端点均声明 X-API-Key security
```
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api import workflow_command as command_api
from backend.app.api import workflow_command_auth as auth_module
from backend.app.config import WorkflowCommandSettings
from backend.app.dto.workflow_command_api import (
    WORKFLOW_COMMAND_RESULT_RESPONSE_FIELDS,
)
from backend.app.dto.workflow_read_api import (
    WORKFLOW_RUN_DETAIL_RESPONSE_FIELDS,
)
from backend.app.main import app
from backend.app.workflow.errors import (
    WF_ERROR_APPROVAL_CONFLICT,
    WF_ERROR_APPROVAL_NOT_FOUND,
    WF_ERROR_DEFINITION_VERSION_MISMATCH,
    WF_ERROR_NODE_UNKNOWN,
    WF_ERROR_RUN_NOT_CANCELLABLE,
    WF_ERROR_RUN_NOT_FOUND,
    WF_ERROR_STATE_CONFLICT,
    WorkflowDefinitionNotFoundError,
    WorkflowDefinitionVersionMismatchError,
)
from backend.app.workflow_runtime.command_service import (
    CommandResult,
    WorkflowCommandInputError,
)
from backend.app.workflow_runtime.read_service import (
    RunDetail,
    WorkflowReadError,
)
from backend.app.workflow_runtime.runtime import (
    OUTCOME_CANCELLED,
    OUTCOME_COMPLETED,
    OUTCOME_CONFLICT,
    OUTCOME_NOT_CANCELLABLE,
    OUTCOME_NOT_FOUND,
    OUTCOME_UNKNOWN,
    OUTCOME_WAITING_APPROVAL,
    RunOutcome,
)

__all__ = [
    "TestAuthentication",
    "TestStartCommand",
    "TestAdvanceCommand",
    "TestCancelCommand",
    "TestDecisionCommand",
    "TestErrorMapping",
    "TestDtoAndScopeBoundary",
    "TestOpenApi",
]

#: 测试专用凭据（**非**生产凭据；仅存在于测试进程内 monkeypatch 配置）。
_TEST_API_KEY = "command-suite-api-key-NOT-A-PRODUCTION-CREDENTIAL"
_AUTH_PROJECT = "proj-command-a"
_HEADERS = {"X-API-Key": _TEST_API_KEY}

_NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
_RUN_ID = "wf-cmd-run-1"


# ============================================================
# Fake 命令服务（记录调用；可编排结果 / 异常）
# ============================================================


class _RecordingService:
    def __init__(self, *, result: CommandResult | None = None, error: BaseException | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._result = result
        self._error = error

    def _resolve(self) -> CommandResult:
        if self._error is not None:
            raise self._error
        if self._result is not None:
            return self._result
        return _result()

    async def start_workflow(self, *, project_id: str, workflow_id: str, input: str | None = None, workflow_run_id: str | None = None) -> CommandResult:
        self.calls.append(("start", {"project_id": project_id, "workflow_id": workflow_id, "input": input, "workflow_run_id": workflow_run_id}))
        return self._resolve()

    async def advance_workflow(self, *, project_id: str, workflow_run_id: str) -> CommandResult:
        self.calls.append(("advance", {"project_id": project_id, "workflow_run_id": workflow_run_id}))
        return self._resolve()

    async def cancel_workflow(self, *, project_id: str, workflow_run_id: str) -> CommandResult:
        self.calls.append(("cancel", {"project_id": project_id, "workflow_run_id": workflow_run_id}))
        return self._resolve()

    async def decide_approval(self, *, project_id: str, approval_id: str, decision: str) -> CommandResult:
        self.calls.append(("decision", {"project_id": project_id, "approval_id": approval_id, "decision": decision}))
        return self._resolve()


# ============================================================
# 构造器
# ============================================================


def _outcome(
    *,
    outcome: str = OUTCOME_COMPLETED,
    run_id: str = _RUN_ID,
    error_code: str | None = None,
    approval_id: str | None = None,
    approval_status: str | None = None,
) -> RunOutcome:
    return RunOutcome(
        workflow_run_id=run_id,
        outcome=outcome,
        error_code=error_code,
        approval_id=approval_id,
        approval_status=approval_status,
    )


def _run_detail(*, status: str = "COMPLETED", project_id: str = _AUTH_PROJECT) -> RunDetail:
    return RunDetail(
        workflow_run_id=_RUN_ID,
        workflow_id="cmd_wf",
        workflow_version="v1",
        project_id=project_id,
        status=status,
        current_node_id=None,
        next_action="COMPLETED",
        state_version=2,
        attempt_count=1,
        cancel_requested=False,
        cancel_requested_at=None,
        created_at=_NOW,
        started_at=_NOW,
        completed_at=_NOW,
        updated_at=_NOW,
    )


def _result(
    *,
    outcome: RunOutcome | None = None,
    detail: RunDetail | None = None,
) -> CommandResult:
    return CommandResult(outcome=outcome or _outcome(), run_detail=detail or _run_detail())


def _client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: CommandResult | None = None,
    error: BaseException | None = None,
    settings_override: WorkflowCommandSettings | None = None,
) -> tuple[TestClient, _RecordingService]:
    service = _RecordingService(result=result, error=error)
    monkeypatch.setattr(
        command_api, "get_workflow_command_service", lambda: service
    )
    monkeypatch.setattr(
        auth_module,
        "get_workflow_command_settings",
        lambda: settings_override
        or WorkflowCommandSettings(
            api_key=_TEST_API_KEY, api_project_id=_AUTH_PROJECT
        ),
    )
    return TestClient(app), service


# ============================================================
# 认证
# ============================================================


class TestAuthentication:
    def test_missing_key_rejected_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(monkeypatch)
        for method, url, body in (
            ("post", "/api/workflow-runs", {"workflow_id": "w"}),
            ("post", f"/api/workflow-runs/{_RUN_ID}/advance", None),
            ("post", f"/api/workflow-runs/{_RUN_ID}/cancel", None),
            ("post", "/api/workflow-approvals/ap-1/decision", {"decision": "APPROVED"}),
        ):
            resp = getattr(client, method)(url, json=body)
            assert resp.status_code == 401, (url, resp.text)
            assert "缺少或无效的 API Key" in resp.json()["detail"]

    def test_wrong_key_rejected_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(monkeypatch)
        resp = client.post(
            "/api/workflow-runs", json={"workflow_id": "w"},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401

    def test_unconfigured_fails_closed_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(
            monkeypatch,
            settings_override=WorkflowCommandSettings(api_key="", api_project_id=""),
        )
        resp = client.post(
            "/api/workflow-runs", json={"workflow_id": "w"}, headers=_HEADERS
        )
        assert resp.status_code == 503
        assert "命令认证未配置" in resp.json()["detail"]

    def test_blank_project_fails_closed_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(
            monkeypatch,
            settings_override=WorkflowCommandSettings(
                api_key=_TEST_API_KEY, api_project_id="   "
            ),
        )
        resp = client.post(
            "/api/workflow-runs", json={"workflow_id": "w"}, headers=_HEADERS
        )
        assert resp.status_code == 503

    def test_unauthenticated_request_never_reaches_service(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, service = _client(monkeypatch, error=AssertionError("must not run"))
        resp = client.post("/api/workflow-runs", json={"workflow_id": "w"})
        assert resp.status_code == 401
        assert service.calls == []  # 认证先于任何命令执行


# ============================================================
# start / advance / cancel / decision 正常路径
# ============================================================


class TestStartCommand:
    def test_start_happy_path_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, service = _client(monkeypatch)
        resp = client.post(
            "/api/workflow-runs",
            json={"workflow_id": "cmd_wf", "input": json.dumps({"q": 1})},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["outcome"] == OUTCOME_COMPLETED
        assert body["workflow_run_id"] == _RUN_ID
        assert body["run"]["status"] == "COMPLETED"
        # 作用域来自服务端配置（客户端未提交）
        assert service.calls[0][1]["project_id"] == _AUTH_PROJECT

    def test_start_duplicate_identity_conflict_409(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(
            monkeypatch,
            result=_result(
                outcome=_outcome(
                    outcome=OUTCOME_CONFLICT, error_code=WF_ERROR_STATE_CONFLICT
                )
            ),
        )
        resp = client.post(
            "/api/workflow-runs", json={"workflow_id": "cmd_wf"}, headers=_HEADERS
        )
        assert resp.status_code == 409
        assert "状态冲突" in resp.json()["detail"]

    def test_start_definition_not_found_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(
            monkeypatch, error=WorkflowDefinitionNotFoundError("nope")
        )
        resp = client.post(
            "/api/workflow-runs", json={"workflow_id": "nope"}, headers=_HEADERS
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Workflow 定义不存在"


class TestAdvanceCommand:
    def test_advance_happy_path_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, service = _client(monkeypatch)
        resp = client.post(f"/api/workflow-runs/{_RUN_ID}/advance", headers=_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["outcome"] == OUTCOME_COMPLETED
        assert service.calls[0][0] == "advance"

    def test_advance_not_found_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(
            monkeypatch, result=_result(outcome=_outcome(outcome=OUTCOME_NOT_FOUND, error_code=WF_ERROR_RUN_NOT_FOUND), detail=None)
        )
        resp = client.post("/api/workflow-runs/wf-x/advance", headers=_HEADERS)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Workflow Run 不存在"

    def test_advance_unknown_conflict_409(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(
            monkeypatch,
            result=_result(outcome=_outcome(outcome=OUTCOME_UNKNOWN, error_code=WF_ERROR_NODE_UNKNOWN)),
        )
        resp = client.post(f"/api/workflow-runs/{_RUN_ID}/advance", headers=_HEADERS)
        assert resp.status_code == 409
        assert "未知状态" in resp.json()["detail"]


class TestCancelCommand:
    def test_cancel_cancelled_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(
            monkeypatch,
            result=_result(outcome=_outcome(outcome=OUTCOME_CANCELLED), detail=_run_detail(status="CANCELLED")),
        )
        resp = client.post(f"/api/workflow-runs/{_RUN_ID}/cancel", headers=_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["outcome"] == OUTCOME_CANCELLED
        assert resp.json()["run"]["status"] == "CANCELLED"

    def test_cancel_terminal_not_cancellable_409(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(
            monkeypatch,
            result=_result(outcome=_outcome(outcome=OUTCOME_NOT_CANCELLABLE)),
        )
        resp = client.post(f"/api/workflow-runs/{_RUN_ID}/cancel", headers=_HEADERS)
        assert resp.status_code == 409
        assert "不可取消" in resp.json()["detail"]


class TestDecisionCommand:
    def test_decision_approved_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, service = _client(
            monkeypatch,
            result=_result(
                outcome=_outcome(
                    outcome=OUTCOME_WAITING_APPROVAL,
                    approval_id="wf-ap-1",
                    approval_status="APPROVED",
                ),
                detail=_run_detail(status="WAITING"),
            ),
        )
        resp = client.post(
            "/api/workflow-approvals/wf-ap-1/decision",
            json={"decision": "APPROVED"},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["approval_status"] == "APPROVED"
        assert service.calls[0][1]["decision"] == "APPROVED"

    def test_decision_conflict_409(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(
            monkeypatch,
            result=_result(
                outcome=_outcome(
                    outcome=OUTCOME_CONFLICT,
                    error_code=WF_ERROR_APPROVAL_CONFLICT,
                )
            ),
        )
        resp = client.post(
            "/api/workflow-approvals/wf-ap-1/decision",
            json={"decision": "REJECTED"},
            headers=_HEADERS,
        )
        assert resp.status_code == 409
        assert "审批冲突" in resp.json()["detail"]

    def test_decision_not_found_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(
            monkeypatch,
            result=_result(outcome=_outcome(outcome=OUTCOME_NOT_FOUND, error_code=WF_ERROR_APPROVAL_NOT_FOUND), detail=None),
        )
        resp = client.post(
            "/api/workflow-approvals/wf-ap-x/decision",
            json={"decision": "APPROVED"},
            headers=_HEADERS,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Workflow 审批不存在"

    def test_decision_invalid_value_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(monkeypatch)
        resp = client.post(
            "/api/workflow-approvals/wf-ap-1/decision",
            json={"decision": "MAYBE"},
            headers=_HEADERS,
        )
        assert resp.status_code == 422

    def test_decision_missing_body_field_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(monkeypatch)
        resp = client.post(
            "/api/workflow-approvals/wf-ap-1/decision", json={}, headers=_HEADERS
        )
        assert resp.status_code == 422

    def test_start_missing_workflow_id_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(monkeypatch)
        resp = client.post("/api/workflow-runs", json={}, headers=_HEADERS)
        assert resp.status_code == 422


# ============================================================
# 错误映射（400 / 404 / 409 / 502 / 500）
# ============================================================


class TestErrorMapping:
    def test_input_contract_error_400(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(
            monkeypatch, error=WorkflowCommandInputError("bad json")
        )
        resp = client.post(
            "/api/workflow-runs",
            json={"workflow_id": "w", "input": "{not-json"},
            headers=_HEADERS,
        )
        assert resp.status_code == 400

    def test_definition_version_mismatch_409(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(
            monkeypatch,
            error=WorkflowDefinitionVersionMismatchError("w", "v1"),
        )
        resp = client.post(f"/api/workflow-runs/{_RUN_ID}/advance", headers=_HEADERS)
        assert resp.status_code == 409
        assert "版本不匹配" in resp.json()["detail"]

    def test_read_boundary_unavailable_502(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(monkeypatch, error=WorkflowReadError("db down"))
        resp = client.post(f"/api/workflow-runs/{_RUN_ID}/advance", headers=_HEADERS)
        assert resp.status_code == 502
        assert resp.json()["detail"] == "Workflow 命令服务不可用"

    def test_unexpected_error_500_fixed_detail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(monkeypatch, error=RuntimeError("boom secret sql"))
        resp = client.post(f"/api/workflow-runs/{_RUN_ID}/advance", headers=_HEADERS)
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Workflow 命令服务不可用"
        assert "boom" not in resp.text and "secret" not in resp.text

    def test_error_details_never_contain_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = _client(
            monkeypatch,
            error=WorkflowDefinitionNotFoundError("x"),
        )
        for url in (
            f"/api/workflow-runs/{_RUN_ID}/advance",
            f"/api/workflow-runs/{_RUN_ID}/cancel",
        ):
            resp = client.post(url, headers={"X-API-Key": "wrong-key"})
            assert _TEST_API_KEY not in resp.text
            assert "wrong-key" not in resp.text


# ============================================================
# DTO 边界与作用域
# ============================================================


_FORBIDDEN_KEYS = {
    "input",
    "request_id",
    "claim_token",
    "api_key",
    "password",
    "session",
    "connection",
}


def _collect_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            keys.add(str(k))
            keys |= _collect_keys(v)
    elif isinstance(value, list):
        for item in value:
            keys |= _collect_keys(item)
    return keys


class TestDtoAndScopeBoundary:
    def test_response_field_set_is_exact(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(monkeypatch)
        resp = client.post(f"/api/workflow-runs/{_RUN_ID}/advance", headers=_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == set(WORKFLOW_COMMAND_RESULT_RESPONSE_FIELDS)
        assert set(body["run"]) == set(WORKFLOW_RUN_DETAIL_RESPONSE_FIELDS)

    def test_no_forbidden_keys_leaked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(monkeypatch)
        resp = client.post(f"/api/workflow-runs/{_RUN_ID}/advance", headers=_HEADERS)
        keys = _collect_keys(resp.json())
        assert not (keys & _FORBIDDEN_KEYS), keys & _FORBIDDEN_KEYS

    def test_client_project_id_query_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, service = _client(monkeypatch)
        resp = client.post(
            f"/api/workflow-runs/{_RUN_ID}/advance?project_id=victim-project",
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        # 作用域仍来自服务端配置（客户端参数未影响任何命令参数）
        assert service.calls[0][1]["project_id"] == _AUTH_PROJECT

    def test_run_view_project_id_is_server_scope(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _client(monkeypatch)
        resp = client.post(f"/api/workflow-runs/{_RUN_ID}/advance", headers=_HEADERS)
        assert resp.json()["run"]["project_id"] == _AUTH_PROJECT


# ============================================================
# OpenAPI
# ============================================================


class TestOpenApi:
    def test_four_command_endpoints_declare_api_key_security(self) -> None:
        schema = app.openapi()
        scheme_name = None
        for name, scheme in schema["components"]["securitySchemes"].items():
            if scheme.get("type") == "apiKey" and scheme.get("name") == "X-API-Key":
                scheme_name = name
        assert scheme_name is not None, "X-API-Key scheme 缺失"
        for route, method in (
            ("/api/workflow-runs", "post"),
            ("/api/workflow-runs/{run_id}/advance", "post"),
            ("/api/workflow-runs/{run_id}/cancel", "post"),
            ("/api/workflow-approvals/{approval_id}/decision", "post"),
        ):
            operation = schema["paths"][route][method]
            requirement = operation.get("security")
            assert requirement, f"{route} 未声明 security"
            assert any(scheme_name in entry for entry in requirement), (
                route,
                requirement,
            )

    def test_runtime_behaviour_401_not_200_without_key(self) -> None:
        """声明与运行时一致：无凭据 ⇒ 401（非 200）——不依赖 OpenAPI 显示。"""
        client = TestClient(app)
        resp = client.post("/api/workflow-runs", json={"workflow_id": "w"})
        assert resp.status_code in (401, 503)
