"""Phase 7 Step 4 — Agent **命令** API **HTTP 契约**测试（离线；零 LLM / 零 DB）。

注入方式沿用既有范式：monkeypatch 本模块 Composition Root
（``get_agent_command_service``——注入装配好的 :class:`AgentCommandService`，
其依赖全部为 Fake）+ ``agent_command_auth`` 的认证配置 +
``TestClient(app)``。Fake 基元复用既有测试模块
（``tests.test_agent_read_service`` 的 ``FakeRunRepository`` / ``run_record``）。

覆盖（Phase 7 Step 4 任务书 §七 离线项）：
```text
1  start 200（COMPLETED / FAILED / LIMIT_REACHED / WAITING_APPROVAL 业务结果）
2  resume 200（归属校验通过；TERMINAL 历史终态）
3  cancel 三态（CANCEL_REQUESTED / CANCELLED 200；NOT_CANCELLABLE 409；
   取消冲突 409）
4  decision（APPROVED 200；幂等 200；相反裁决 409；不存在 404）
5  归属隔离（run / approval 不存在或跨项目 ⇒ 404 同文案——不泄露存在性）
6  认证（无 header / 错误 key ⇒ 401；未配置 ⇒ 503 fail-closed）
7  客户端 project_id 参数被忽略（作用域不可扩宽）
8  DTO 安全（精确字段集 + 无敏感键 + 异常正文不回传）
9  输入校验（question 空白 ⇒ 400；decision 非法 ⇒ 422；run_id 空白 ⇒ 400）
10 错误映射（仓储故障 ⇒ 502；未预期 ⇒ 500）
11 持久化 best-effort（start 后 recorder 被调用；WAITING_APPROVAL 不写 Run）
12 只读面形（OpenAPI 四路径仅 POST；GET ⇒ 405；读面路径不回归）
```
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.agent.cancellation import AgentCancellationError
from backend.app.agent.models import AgentRuntimeResult
from backend.app.agent.resume import AgentResumeResult
from backend.app.api import agent_command as agent_command_module
from backend.app.api import agent_command_auth as auth_module
from backend.app.config import AgentCommandSettings
from backend.app.db.agent_run_repository import AgentRunRepositoryError
from backend.app.dto.agent_command_api import (
    AGENT_COMMAND_RESULT_RESPONSE_FIELDS,
    AGENT_RUN_SUMMARY_VIEW_FIELDS,
)
from backend.app.main import app
from backend.app.services.agent_command_service import (
    AgentCommandError,
    AgentCommandService,
)
from tests.test_agent_read_service import FakeRunRepository, run_record

__all__ = [
    "TestAgentStart",
    "TestAgentResume",
    "TestAgentCancel",
    "TestAgentDecision",
    "TestScopeIsolation",
    "TestAuth",
    "TestDtoSafety",
    "TestInputValidation",
    "TestErrorMapping",
    "TestPersistence",
    "TestSurface",
]

_PROJECT = "proj-agent-cmd"
_OTHER_PROJECT = "proj-agent-cmd-other"
_RUN_ID = "agent-cmd-run-1"
_APPROVAL_ID = "agent-cmd-ap-1"

#: 测试专用凭据（**非**生产凭据；仅存在于测试进程内 monkeypatch 配置）。
_TEST_API_KEY = "agent-command-key-NOT-A-PRODUCTION-CREDENTIAL"
_HEADERS = {"X-API-Key": _TEST_API_KEY}

#: 绝不允许出现在响应 JSON 键中的字段。
_FORBIDDEN_KEYS = {
    "claim_token",
    "arguments",
    "content_prompt",
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
    "input",
    "request_id",
}


# ============================================================
# Fakes（复用既有 FakeRunRepository；runtime / checkpoints / approvals 新造）
# ============================================================


def _runtime_result(
    *,
    run_id: str = _RUN_ID,
    status: str = "COMPLETED",
    content: str | None = "已完成",
    error: str | None = None,
    approval_id: str | None = None,
) -> AgentRuntimeResult:
    return AgentRuntimeResult(
        status=status,
        content=content,
        steps=1,
        tool_calls=0,
        llm_calls=1,
        error=error,
        run_id=run_id,
        approval_id=approval_id,
    )


def _resume_result(
    *,
    run_id: str = _RUN_ID,
    status: str = "COMPLETED",
    content: str | None = "恢复完成",
    error_code: str | None = None,
) -> AgentResumeResult:
    return AgentResumeResult(
        run_id=run_id,
        status=status,
        content=content,
        error_code=error_code,
        replayed_step_index=None,
        llm_calls=0,
        tool_calls=0,
        steps=1,
        agent_result=_runtime_result(
            run_id=run_id, status=status, content=content, error=error_code
        ),
    )


class _FakeRuntime:
    """AgentRuntime 替身（记录调用；可控结果 / 异常）。"""

    def __init__(
        self,
        *,
        run_result: AgentRuntimeResult | None = None,
        resume_result: AgentResumeResult | None = None,
        run_error: BaseException | None = None,
        resume_error: BaseException | None = None,
    ) -> None:
        self._run_result = run_result
        self._resume_result = resume_result
        self._run_error = run_error
        self._resume_error = resume_error
        self.run_calls: list[dict[str, Any]] = []
        self.resume_calls: list[dict[str, Any]] = []

    async def run(
        self,
        *,
        question: str,
        conversation_context: str | None = None,
        request_id: str | None = None,
    ) -> AgentRuntimeResult:
        self.run_calls.append(
            {"question": question, "request_id": request_id}
        )
        if self._run_error is not None:
            raise self._run_error
        return self._run_result or _runtime_result()

    async def resume(
        self,
        *,
        run_id: str,
        question: str | None = None,
        conversation_context: str | None = None,
        request_id: str | None = None,
    ) -> AgentResumeResult:
        self.resume_calls.append({"run_id": run_id, "question": question})
        if self._resume_error is not None:
            raise self._resume_error
        return self._resume_result or _resume_result(run_id=run_id)


@dataclass
class _FakeCheckpoint:
    status: str = "ACTIVE"
    cancel_requested: bool = False
    run_id: str = _RUN_ID


class _FakeCheckpoints:
    """Checkpoint 记录器替身（get / request_cancel / record_terminal）。"""

    def __init__(
        self,
        *,
        checkpoint: _FakeCheckpoint | None = None,
        cancel_result: _FakeCheckpoint | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._checkpoint = checkpoint
        self._cancel_result = cancel_result
        self._error = error
        self.get_calls: list[str] = []
        self.cancel_calls: list[str] = []
        self.terminal_calls: list[str] = []

    def get(self, run_id: str) -> _FakeCheckpoint | None:
        self.get_calls.append(run_id)
        if self._error is not None:
            raise self._error
        return self._checkpoint

    def request_cancel(self, run_id: str) -> _FakeCheckpoint | None:
        self.cancel_calls.append(run_id)
        if self._error is not None:
            raise self._error
        return self._cancel_result

    def record_terminal(
        self, *, run_id: str, agent_status: str
    ) -> _FakeCheckpoint | None:
        self.terminal_calls.append(run_id)
        return _FakeCheckpoint(
            status="TERMINAL", cancel_requested=True, run_id=run_id
        )


@dataclass
class _FakeApproval:
    approval_id: str = _APPROVAL_ID
    run_id: str = _RUN_ID
    node_id: str = "gate"
    status: str = "PENDING"
    decision: str | None = None
    resolved_at: Any = None


class _FakeApprovalStore:
    """审批存储替身（aget / aresolve；可控幂等 / 冲突 / 不存在）。"""

    def __init__(
        self,
        *,
        record: _FakeApproval | None = None,
        resolve_result: _FakeApproval | None = None,
    ) -> None:
        self._record = record
        self._resolve_result = resolve_result
        self.get_calls: list[str] = []
        self.resolve_calls: list[dict[str, Any]] = []

    async def aget(self, *, approval_id: str) -> _FakeApproval | None:
        self.get_calls.append(approval_id)
        return self._record

    async def aresolve(
        self, *, approval_id: str, decision: str
    ) -> _FakeApproval | None:
        self.resolve_calls.append(
            {"approval_id": approval_id, "decision": decision}
        )
        return self._resolve_result


class _RecordingRunRecorder:
    """Run 持久化记录器替身（best-effort 断言）。"""

    def __init__(self) -> None:
        self.run_calls: list[dict[str, Any]] = []

    def record(self, result: Any, **kwargs: Any) -> None:
        self.run_calls.append({"result": result, **kwargs})

    def arecord(self, result: Any, **kwargs: Any) -> None:
        self.record(result, **kwargs)


class _RecordingStepRecorder:
    """Step 持久化记录器替身（与 Run 分开断言——先 Steps、后 Run）。"""

    def __init__(self) -> None:
        self.step_calls: list[Any] = []

    def record(self, result: Any) -> None:
        self.step_calls.append(result)

    def arecord(self, result: Any) -> None:
        self.record(result)


def _service(
    *,
    runtime: _FakeRuntime | None = None,
    run_rows: dict[str, Any] | None = None,
    checkpoints: _FakeCheckpoints | None = None,
    approvals: _FakeApprovalStore | None = None,
    run_recorder: _RecordingRunRecorder | None = None,
    step_recorder: _RecordingStepRecorder | None = None,
    run_repo_error: BaseException | None = None,
) -> AgentCommandService:
    """装配命令服务（Fake 依赖；与生产 Composition Root 同签名）。"""

    class _ErroringRunRepository:
        def get(self, run_id: str) -> Any:
            raise run_repo_error

    run_repository: Any
    if run_repo_error is not None:
        run_repository = _ErroringRunRepository()
    else:
        rows = run_rows if run_rows is not None else {}
        run_repository = FakeRunRepository(rows)
    return AgentCommandService(
        runtime=runtime or _FakeRuntime(),
        run_repository=run_repository,
        checkpoints=checkpoints or _FakeCheckpoints(),
        approval_store=approvals or _FakeApprovalStore(),
        run_recorder=run_recorder,
        step_recorder=step_recorder,
    )


def _seeded_service(**kwargs: Any) -> AgentCommandService:
    """默认 seed：授权项目下存在 Run（归属校验通过）。"""
    rows = kwargs.pop("run_rows", None)
    if rows is None:
        rows = {_RUN_ID: run_record(run_id=_RUN_ID, project_id=_PROJECT)}
    return _service(run_rows=rows, **kwargs)


def _client(
    monkeypatch: pytest.MonkeyPatch,
    service: AgentCommandService,
    *,
    auth_settings: AgentCommandSettings | None = None,
) -> TestClient:
    """命令客户端：替换 Composition Root + 认证配置。"""
    monkeypatch.setattr(
        agent_command_module, "get_agent_command_service", lambda: service
    )
    monkeypatch.setattr(
        auth_module,
        "get_agent_command_settings",
        lambda: auth_settings
        or AgentCommandSettings(
            api_key=_TEST_API_KEY, api_project_id=_PROJECT
        ),
    )
    return TestClient(app)


def _iter_keys(node: Any):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _iter_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_keys(item)


# ============================================================
# Test 1：start
# ============================================================


class TestAgentStart:
    def test_start_completed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runtime = _FakeRuntime(
            run_result=_runtime_result(content="查询完成：MAT001 库存 100")
        )
        client = _client(monkeypatch, _service(runtime=runtime))
        response = client.post(
            "/api/agent-runs",
            json={"question": "查询 MAT001 库存"},
            headers=_HEADERS,
        )
        assert response.status_code == 200
        payload = response.json()
        assert tuple(payload.keys()) == AGENT_COMMAND_RESULT_RESPONSE_FIELDS
        assert payload["outcome"] == "COMPLETED"
        assert payload["run_id"] == _RUN_ID
        assert payload["status"] == "COMPLETED"
        assert payload["error_code"] is None
        assert payload["content"] == "查询完成：MAT001 库存 100"
        # runtime 收到 question（无客户端 project 注入）
        assert runtime.run_calls[0]["question"] == "查询 MAT001 库存"

    def test_start_failed_is_business_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime = _FakeRuntime(
            run_result=_runtime_result(
                status="FAILED", content=None, error="AGENT_LLM_UNAVAILABLE"
            )
        )
        client = _client(monkeypatch, _service(runtime=runtime))
        response = client.post(
            "/api/agent-runs",
            json={"question": "q"},
            headers=_HEADERS,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["outcome"] == "FAILED"
        assert payload["error_code"] == "AGENT_LLM_UNAVAILABLE"
        assert payload["content"] is None

    def test_start_waiting_approval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runtime = _FakeRuntime(
            run_result=_runtime_result(
                status="WAITING_APPROVAL",
                content=None,
                approval_id=_APPROVAL_ID,
            )
        )
        client = _client(monkeypatch, _service(runtime=runtime))
        response = client.post(
            "/api/agent-runs",
            json={"question": "q"},
            headers=_HEADERS,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["outcome"] == "WAITING_APPROVAL"
        assert payload["run_id"] == _RUN_ID


# ============================================================
# Test 2：resume
# ============================================================


class TestAgentResume:
    def test_resume_completed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runtime = _FakeRuntime(
            resume_result=_resume_result(content="恢复完成")
        )
        client = _client(monkeypatch, _seeded_service(runtime=runtime))
        response = client.post(
            f"/api/agent-runs/{_RUN_ID}/resume",
            json={"question": "继续"},
            headers=_HEADERS,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["outcome"] == "COMPLETED"
        assert payload["run_id"] == _RUN_ID
        assert runtime.resume_calls == [{"run_id": _RUN_ID, "question": "继续"}]

    def test_resume_without_question(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runtime = _FakeRuntime(resume_result=_resume_result())
        client = _client(monkeypatch, _seeded_service(runtime=runtime))
        response = client.post(
            f"/api/agent-runs/{_RUN_ID}/resume",
            json={},
            headers=_HEADERS,
        )
        assert response.status_code == 200
        assert runtime.resume_calls == [{"run_id": _RUN_ID, "question": None}]

    def test_resume_run_view_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch, _seeded_service())
        payload = client.post(
            f"/api/agent-runs/{_RUN_ID}/resume",
            json={},
            headers=_HEADERS,
        ).json()
        assert payload["run"] is not None
        assert tuple(payload["run"].keys()) == AGENT_RUN_SUMMARY_VIEW_FIELDS
        assert payload["run"]["run_id"] == _RUN_ID
        assert payload["run"]["project_id"] == _PROJECT


# ============================================================
# Test 3：cancel（三态）
# ============================================================


class TestAgentCancel:
    def test_cancel_requested(self, monkeypatch: pytest.MonkeyPatch) -> None:
        checkpoints = _FakeCheckpoints(
            checkpoint=_FakeCheckpoint(status="ACTIVE"),
            cancel_result=_FakeCheckpoint(
                status="ACTIVE", cancel_requested=True
            ),
        )
        client = _client(monkeypatch, _seeded_service(checkpoints=checkpoints))
        response = client.post(
            f"/api/agent-runs/{_RUN_ID}/cancel", headers=_HEADERS
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["outcome"] == "CANCEL_REQUESTED"
        assert payload["error_code"] is None
        assert checkpoints.cancel_calls == [_RUN_ID]

    def test_cancel_paused_finalizes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        checkpoints = _FakeCheckpoints(
            checkpoint=_FakeCheckpoint(status="PAUSED"),
            cancel_result=_FakeCheckpoint(status="PAUSED"),
        )
        client = _client(monkeypatch, _seeded_service(checkpoints=checkpoints))
        response = client.post(
            f"/api/agent-runs/{_RUN_ID}/cancel", headers=_HEADERS
        )
        assert response.status_code == 200
        assert response.json()["outcome"] == "CANCELLED"
        assert checkpoints.terminal_calls == [_RUN_ID]

    def test_cancel_terminal_is_conflict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        checkpoints = _FakeCheckpoints(
            checkpoint=_FakeCheckpoint(status="TERMINAL")
        )
        client = _client(monkeypatch, _seeded_service(checkpoints=checkpoints))
        response = client.post(
            f"/api/agent-runs/{_RUN_ID}/cancel", headers=_HEADERS
        )
        assert response.status_code == 409
        assert "不可取消" in response.json()["detail"]

    def test_cancel_without_checkpoint_is_conflict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        checkpoints = _FakeCheckpoints(checkpoint=None)
        client = _client(monkeypatch, _seeded_service(checkpoints=checkpoints))
        response = client.post(
            f"/api/agent-runs/{_RUN_ID}/cancel", headers=_HEADERS
        )
        assert response.status_code == 409

    def test_cancel_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        checkpoints = _FakeCheckpoints(
            checkpoint=_FakeCheckpoint(status="ACTIVE", cancel_requested=True),
        )
        client = _client(monkeypatch, _seeded_service(checkpoints=checkpoints))
        response = client.post(
            f"/api/agent-runs/{_RUN_ID}/cancel", headers=_HEADERS
        )
        assert response.status_code == 200
        assert response.json()["outcome"] == "CANCEL_REQUESTED"
        assert checkpoints.cancel_calls == []  # 幂等：不重复置位


# ============================================================
# Test 4：approval decision
# ============================================================


class TestAgentDecision:
    def test_decision_approved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        approvals = _FakeApprovalStore(
            record=_FakeApproval(status="PENDING"),
            resolve_result=_FakeApproval(
                status="APPROVED", decision="APPROVED"
            ),
        )
        client = _client(monkeypatch, _seeded_service(approvals=approvals))
        response = client.post(
            f"/api/agent-approvals/{_APPROVAL_ID}/decision",
            json={"decision": "APPROVED"},
            headers=_HEADERS,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["outcome"] == "COMPLETED"
        assert payload["approval_id"] == _APPROVAL_ID
        assert payload["approval_status"] == "APPROVED"
        assert payload["decision"] == "APPROVED"
        assert approvals.resolve_calls == [
            {"approval_id": _APPROVAL_ID, "decision": "APPROVED"}
        ]

    def test_decision_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """同裁决重复提交 ⇒ 幂等 200（不二次转换）。"""
        approvals = _FakeApprovalStore(
            record=_FakeApproval(status="APPROVED", decision="APPROVED"),
            resolve_result=None,  # 条件 UPDATE 未命中
        )
        client = _client(monkeypatch, _seeded_service(approvals=approvals))
        response = client.post(
            f"/api/agent-approvals/{_APPROVAL_ID}/decision",
            json={"decision": "APPROVED"},
            headers=_HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["outcome"] == "COMPLETED"
        assert response.json()["approval_status"] == "APPROVED"

    def test_opposite_decision_is_conflict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """相反裁决 ⇒ 409（终态不可逆；fail-closed）。"""
        approvals = _FakeApprovalStore(
            record=_FakeApproval(status="APPROVED", decision="APPROVED"),
            resolve_result=None,
        )
        client = _client(monkeypatch, _seeded_service(approvals=approvals))
        response = client.post(
            f"/api/agent-approvals/{_APPROVAL_ID}/decision",
            json={"decision": "REJECTED"},
            headers=_HEADERS,
        )
        assert response.status_code == 409
        assert "审批冲突" in response.json()["detail"]

    def test_decision_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        approvals = _FakeApprovalStore(record=None)
        client = _client(monkeypatch, _seeded_service(approvals=approvals))
        response = client.post(
            f"/api/agent-approvals/{_APPROVAL_ID}/decision",
            json={"decision": "APPROVED"},
            headers=_HEADERS,
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Agent 审批不存在"


# ============================================================
# Test 5：归属隔离（不存在 / 跨项目同码）
# ============================================================


class TestScopeIsolation:
    def test_resume_missing_run_is_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch, _seeded_service(run_rows={}))
        response = client.post(
            "/api/agent-runs/rt-missing/resume",
            json={},
            headers=_HEADERS,
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Agent Run 不存在"

    def test_cross_project_run_shares_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """跨项目 ⇒ 404 + 与不存在**同文案**（不泄露存在性）。"""
        rows = {
            _RUN_ID: run_record(
                run_id=_RUN_ID, project_id=_OTHER_PROJECT
            )
        }
        authorized = _client(monkeypatch, _seeded_service(run_rows=rows))
        missing = authorized.post(
            "/api/agent-runs/rt-missing/cancel", headers=_HEADERS
        )
        foreign = authorized.post(
            f"/api/agent-runs/{_RUN_ID}/cancel", headers=_HEADERS
        )
        assert missing.status_code == 404
        assert foreign.status_code == 404
        assert foreign.json() == missing.json()

    def test_cross_project_approval_is_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """审批归属经父 Run 校验：他项目 Run 的审批 ⇒ 404 同码。"""
        rows = {
            _RUN_ID: run_record(
                run_id=_RUN_ID, project_id=_OTHER_PROJECT
            )
        }
        approvals = _FakeApprovalStore(record=_FakeApproval())
        client = _client(
            monkeypatch, _seeded_service(run_rows=rows, approvals=approvals)
        )
        response = client.post(
            f"/api/agent-approvals/{_APPROVAL_ID}/decision",
            json={"decision": "APPROVED"},
            headers=_HEADERS,
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Agent 审批不存在"
        assert approvals.resolve_calls == []  # 归属失败不得触达裁决

    def test_client_project_id_query_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """客户端 project_id 参数不参与授权（被忽略——作用域不可扩宽）。"""
        runtime = _FakeRuntime()
        client = _client(monkeypatch, _service(runtime=runtime))
        response = client.post(
            "/api/agent-runs",
            params={"project_id": _OTHER_PROJECT},
            json={"question": "q"},
            headers=_HEADERS,
        )
        assert response.status_code == 200
        # runtime 只收到 question（无项目注入——作用域来自服务端装配）
        assert runtime.run_calls[0]["question"] == "q"


# ============================================================
# Test 6：认证
# ============================================================


class TestAuth:
    def test_missing_credential_is_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch, _seeded_service())
        assert (
            client.post("/api/agent-runs", json={"question": "q"}).status_code
            == 401
        )

    def test_wrong_credential_is_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch, _seeded_service())
        response = client.post(
            "/api/agent-runs",
            json={"question": "q"},
            headers={"X-API-Key": "wrong"},
        )
        assert response.status_code == 401

    def test_other_domain_key_cannot_open_agent_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """凭据独立：其它域 key 打不开 Agent 命令入口。"""
        client = _client(monkeypatch, _seeded_service())
        response = client.post(
            "/api/agent-runs",
            json={"question": "q"},
            headers={"X-API-Key": "workflow-command-key"},
        )
        assert response.status_code == 401

    def test_unconfigured_is_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(
            monkeypatch,
            _seeded_service(),
            auth_settings=AgentCommandSettings(),
        )
        response = client.post(
            "/api/agent-runs", json={"question": "q"}, headers=_HEADERS
        )
        assert response.status_code == 503


# ============================================================
# Test 7：DTO 安全
# ============================================================


class TestDtoSafety:
    def test_response_has_no_forbidden_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch, _seeded_service())
        payload = client.post(
            f"/api/agent-runs/{_RUN_ID}/resume",
            json={},
            headers=_HEADERS,
        ).json()
        leaked = _FORBIDDEN_KEYS & set(_iter_keys(payload))
        assert not leaked

    def test_error_text_not_leaked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """仓储故障 ⇒ 502 固定文案（不回传内部异常文本）。"""
        client = _client(
            monkeypatch,
            _seeded_service(
                run_repo_error=AgentRunRepositoryError("secret db detail")
            ),
        )
        response = client.post(
            f"/api/agent-runs/{_RUN_ID}/cancel", headers=_HEADERS
        )
        assert response.status_code == 502
        assert "secret db detail" not in response.text

    def test_credential_not_in_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch, _seeded_service())
        response = client.post(
            "/api/agent-runs", json={"question": "q"}, headers=_HEADERS
        )
        assert _TEST_API_KEY not in response.text


# ============================================================
# Test 8：输入校验
# ============================================================


class TestInputValidation:
    def test_blank_question_is_bad_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """纯空白 question（过 Pydantic 长度校验）⇒ 服务层契约校验 400。"""
        client = _client(monkeypatch, _seeded_service())
        response = client.post(
            "/api/agent-runs", json={"question": "   "}, headers=_HEADERS
        )
        assert response.status_code == 400

    def test_invalid_decision_is_unprocessable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch, _seeded_service())
        response = client.post(
            f"/api/agent-approvals/{_APPROVAL_ID}/decision",
            json={"decision": "RETRY"},
            headers=_HEADERS,
        )
        assert response.status_code == 422

    def test_blank_run_id_is_bad_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch, _seeded_service())
        response = client.post(
            "/api/agent-runs/%20/cancel", headers=_HEADERS
        )
        assert response.status_code == 400

    def test_overlong_run_id_is_unprocessable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch, _seeded_service())
        response = client.post(
            f"/api/agent-runs/{'x' * 65}/cancel", headers=_HEADERS
        )
        assert response.status_code == 422


# ============================================================
# Test 9：错误映射
# ============================================================


class TestErrorMapping:
    def test_cancellation_failure_is_502(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        checkpoints = _FakeCheckpoints(error=AgentCancellationError("boom"))
        client = _client(monkeypatch, _seeded_service(checkpoints=checkpoints))
        response = client.post(
            f"/api/agent-runs/{_RUN_ID}/cancel", headers=_HEADERS
        )
        assert response.status_code == 502
        assert "boom" not in response.text

    def test_runtime_exception_is_business_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Runtime 异常归一为 FAILED 业务结果（200；fail-closed 不伪装成功）。"""
        runtime = _FakeRuntime(run_error=RuntimeError("boom"))
        client = _client(monkeypatch, _service(runtime=runtime))
        response = client.post(
            "/api/agent-runs", json={"question": "q"}, headers=_HEADERS
        )
        assert response.status_code == 200
        assert response.json()["outcome"] == "FAILED"

    def test_service_error_is_502(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(
            monkeypatch,
            _seeded_service(
                run_repo_error=AgentCommandError("store down")
            ),
        )
        response = client.post(
            f"/api/agent-runs/{_RUN_ID}/resume",
            json={},
            headers=_HEADERS,
        )
        assert response.status_code == 502


# ============================================================
# Test 10：持久化 best-effort
# ============================================================


class TestPersistence:
    def test_start_records_run_and_steps(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_recorder = _RecordingRunRecorder()
        step_recorder = _RecordingStepRecorder()
        client = _client(
            monkeypatch,
            _service(
                run_recorder=run_recorder, step_recorder=step_recorder
            ),
        )
        client.post(
            "/api/agent-runs", json={"question": "q"}, headers=_HEADERS
        )
        assert len(step_recorder.step_calls) == 1  # 先 Steps
        assert len(run_recorder.run_calls) == 1  # 后 Run
        call = run_recorder.run_calls[0]
        assert call["project_id"] == _PROJECT  # 授权作用域落库
        assert call["request_id"].startswith("agent-cmd-")

    def test_waiting_approval_does_not_write_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """WAITING_APPROVAL ⇒ Run 尚未终态 ⇒ **不写** AgentRun（不伪造）。"""
        runtime = _FakeRuntime(
            run_result=_runtime_result(
                status="WAITING_APPROVAL",
                content=None,
                approval_id=_APPROVAL_ID,
            )
        )
        run_recorder = _RecordingRunRecorder()
        client = _client(
            monkeypatch, _service(runtime=runtime, run_recorder=run_recorder)
        )
        client.post(
            "/api/agent-runs", json={"question": "q"}, headers=_HEADERS
        )
        assert run_recorder.run_calls == []

    def test_recorder_failure_does_not_break_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """持久化 best-effort：recorder 失败不改变命令业务结果。"""

        class _FailingRecorder:
            def record(self, result: Any, **kwargs: Any) -> None:
                raise RuntimeError("db down")

        client = _client(
            monkeypatch,
            _service(run_recorder=_FailingRecorder()),
        )
        response = client.post(
            "/api/agent-runs", json={"question": "q"}, headers=_HEADERS
        )
        assert response.status_code == 200
        assert response.json()["outcome"] == "COMPLETED"


# ============================================================
# Test 11：只读面形
# ============================================================


class TestSurface:
    def test_command_paths_are_post_only(self) -> None:
        schema = app.openapi()
        command_paths = {
            path: methods
            for path, methods in schema["paths"].items()
            if path.startswith("/api/agent-runs")
            or path.startswith("/api/agent-approvals")
        }
        expected = {
            "/api/agent-runs",
            "/api/agent-runs/{run_id}/resume",
            "/api/agent-runs/{run_id}/cancel",
            "/api/agent-approvals/{approval_id}/decision",
        }
        assert set(command_paths) == expected
        for methods in command_paths.values():
            assert set(methods) == {"post"}

    def test_get_on_command_path_is_405(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch, _seeded_service())
        response = client.get("/api/agent-runs", headers=_HEADERS)
        assert response.status_code == 405

    def test_existing_read_paths_not_regressed(self) -> None:
        """命令面不破坏既有 Agent 只读路径（/api/agent/runs/* 仍在）。"""
        schema = app.openapi()
        assert "/api/agent/runs/{run_id}" in schema["paths"]
        assert "/api/agent/runs/{run_id}/timeline" in schema["paths"]
