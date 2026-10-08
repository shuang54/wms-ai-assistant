"""Phase 5 P-8 Agent Read API —— **HTTP 契约**测试（离线；Fake 服务注入；零 DB）。

覆盖（任务书 §十九 / §二十 / §二十三 / §二十七 / §二十九 / §三十 / §三十四）：

```text
1  200            GET /api/agent/runs/{run_id}（Run 摘要 + 四个视图；字段集精确）
                 GET /api/agent/runs/{run_id}/timeline（list[事件]）
2  分页           limit=1/10/100 ⇒ 200；limit=0/101/-1 ⇒ 400；offset=-1 ⇒ 400
3  404            不存在 / 跨 project ⇒ **同一**固定文案（不泄露存在性）
4  422            路径参数长度越界（FastAPI Path 校验）
5  400            纯空白 run_id（服务层校验）
6  502 / 500      持久化读边界不可用 / 未预期错误（不暴露内部细节）
7  只读面形        OpenAPI 中这两个路径**只有 GET**；/api/agent 下无写方法
8  安全           响应 JSON **递归**键扫描：无 claim_token / arguments / content /
                 payload / prompt / SQL / 凭据
9  默认 project    省略 project_id ⇒ 项目默认（不猜）
```

注入方式沿用既有 ``test_assistant_timeline_api`` 范式：
``monkeypatch.setattr(module, "get_agent_read_service", …)`` + ``TestClient(app)``。
"""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api import agent_read as agent_read_module
from backend.app.config import settings
from backend.app.db.agent_approval_repository import AgentApprovalRepositoryError
from backend.app.db.agent_tool_result_repository import (
    AgentToolResultRepositoryError,
)
from backend.app.dto.agent_read_api import (
    AGENT_RUN_RESPONSE_FIELDS,
    AGENT_TIMELINE_EVENT_RESPONSE_FIELDS,
)
from backend.app.main import app
from backend.app.services.agent_read_service import (
    AgentReadService,
    AgentRunNotFoundError,
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

__all__ = [
    "TestAgentReadApiRun",
    "TestAgentReadApiTimeline",
    "TestAgentReadApiErrors",
    "TestAgentReadApiSurface",
]

#: 数据归属 project = 项目默认 project（省略 project_id 时按默认读取）。
_PROJECT_A = settings.project.project_id
_RUN_ID = "run-p8-read"

_FORBIDDEN_KEYS = {
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
    "raw_response",
    "exception",
    "stack",
}


class _RecordingService:
    """委托真实 Service + 记录 (method, args, kwargs)（用于默认 project 断言）。"""

    def __init__(self, inner: AgentReadService) -> None:
        self._inner = inner
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def get_run(self, run_id: str, project_id: str) -> Any:
        self.calls.append(("get_run", (run_id, project_id), {}))
        return self._inner.get_run(run_id, project_id)

    def get_timeline(self, run_id: str, project_id: str, **kwargs: Any) -> Any:
        self.calls.append(("get_timeline", (run_id, project_id), kwargs))
        return self._inner.get_timeline(run_id, project_id, **kwargs)


def _full_read_service() -> AgentReadService:
    service, _repositories = build_service(
        run=FakeRunRepository({_RUN_ID: run_record(project_id=_PROJECT_A)}),
        step=FakeStepRepository([step_read(step_index=0)]),
        decision=FakeDecisionRepository([decision_record(step_index=0)]),
        tool_result=FakeToolResultRepository(
            [replace(tool_result_read(step_index=0), project_id=_PROJECT_A)]
        ),
        checkpoint=FakeCheckpointRepository({_RUN_ID: checkpoint_dto()}),
        approval=FakeApprovalRepository([approval_read(step_index=0)]),
    )
    return service


def _client(monkeypatch: pytest.MonkeyPatch, service: Any) -> TestClient:
    monkeypatch.setattr(
        agent_read_module, "get_agent_read_service", lambda: service
    )
    return TestClient(app)


def _keys(payload: Any) -> set[str]:
    """递归收集 JSON 键名（含列表元素）。"""
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.add(key)
            found |= _keys(value)
    elif isinstance(payload, list):
        for item in payload:
            found |= _keys(item)
    return found


# ============================================================
# §十九：GET /api/agent/runs/{run_id}
# ============================================================


class TestAgentReadApiRun:
    def test_run_response_has_exact_field_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch, _full_read_service())

        response = client.get(f"/api/agent/runs/{_RUN_ID}")

        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == set(AGENT_RUN_RESPONSE_FIELDS)
        assert payload["run_id"] == _RUN_ID
        assert payload["request_id"] == "req-p8-1"
        assert payload["project_id"] == _PROJECT_A
        assert payload["status"] == "COMPLETED"
        assert payload["step_count"] == 2
        assert payload["token_usage_unverifiable"] is False
        assert payload["started_at"] is not None
        # 子视图存在（dict / list）
        assert isinstance(payload["checkpoint"], dict)
        assert isinstance(payload["approval"], dict)
        assert len(payload["steps"]) == 1
        assert len(payload["decisions"]) == 1
        assert len(payload["tool_results"]) == 1

    def test_run_response_is_json_serializable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch, _full_read_service())
        payload = client.get(f"/api/agent/runs/{_RUN_ID}").json()
        json.dumps(payload)  # 不抛异常 = JSON-safe

    def test_run_without_terminal_record_has_null_summary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service, _repositories = build_service(
            tool_result=FakeToolResultRepository(
                [replace(tool_result_read(), project_id=_PROJECT_A)]
            ),
            checkpoint=FakeCheckpointRepository({_RUN_ID: checkpoint_dto()}),
        )
        client = _client(monkeypatch, service)

        payload = client.get(f"/api/agent/runs/{_RUN_ID}").json()

        assert payload["status"] is None  # **不**由 Checkpoint 推导
        assert payload["request_id"] is None
        assert payload["step_count"] is None
        assert payload["checkpoint"]["status"] == "ACTIVE"
        assert payload["checkpoint"]["next_action"] == "LLM_DECISION"

    def test_project_id_is_passed_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recording = _RecordingService(_full_read_service())
        client = _client(monkeypatch, recording)

        response = client.get(
            f"/api/agent/runs/{_RUN_ID}", params={"project_id": _PROJECT_A}
        )

        assert response.status_code == 200
        assert recording.calls == [("get_run", (_RUN_ID, _PROJECT_A), {})]

    def test_project_id_defaults_to_settings_project(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recording = _RecordingService(_full_read_service())
        client = _client(monkeypatch, recording)

        client.get(f"/api/agent/runs/{_RUN_ID}")

        assert recording.calls[0][1] == (_RUN_ID, settings.project.project_id)

    def test_cross_project_is_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch, _full_read_service())

        response = client.get(
            f"/api/agent/runs/{_RUN_ID}", params={"project_id": "project-B"}
        )

        assert response.status_code == 404


# ============================================================
# §十九 / §二十：GET /api/agent/runs/{run_id}/timeline
# ============================================================


class TestAgentReadApiTimeline:
    def test_timeline_is_a_list_of_events(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch, _full_read_service())

        response = client.get(f"/api/agent/runs/{_RUN_ID}/timeline")

        assert response.status_code == 200
        payload = response.json()
        assert isinstance(payload, list) and payload
        for event in payload:
            assert set(event) == set(AGENT_TIMELINE_EVENT_RESPONSE_FIELDS)
        types = [event["event_type"] for event in payload]
        assert types[0] == "RUN_STARTED"
        assert types[-1] == "RUN_COMPLETED"
        assert [event["sequence"] for event in payload] == list(
            range(len(payload))
        )

    @pytest.mark.parametrize("limit", [1, 10, 100])
    def test_valid_limits(
        self, monkeypatch: pytest.MonkeyPatch, limit: int
    ) -> None:
        client = _client(monkeypatch, _full_read_service())
        response = client.get(
            f"/api/agent/runs/{_RUN_ID}/timeline", params={"limit": limit}
        )
        assert response.status_code == 200
        assert len(response.json()) <= limit

    def test_offset_paging_keeps_sequence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch, _full_read_service())
        full = client.get(
            f"/api/agent/runs/{_RUN_ID}/timeline", params={"limit": 100}
        ).json()
        page = client.get(
            f"/api/agent/runs/{_RUN_ID}/timeline",
            params={"limit": 2, "offset": 2},
        ).json()

        assert [event["sequence"] for event in page] == [2, 3]
        assert page == full[2:4]

    @pytest.mark.parametrize("limit", [0, -1, 101, 1000000])
    def test_invalid_limits_are_rejected(
        self, monkeypatch: pytest.MonkeyPatch, limit: int
    ) -> None:
        client = _client(monkeypatch, _full_read_service())
        response = client.get(
            f"/api/agent/runs/{_RUN_ID}/timeline", params={"limit": limit}
        )
        assert response.status_code == 400

    def test_negative_offset_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch, _full_read_service())
        response = client.get(
            f"/api/agent/runs/{_RUN_ID}/timeline", params={"offset": -1}
        )
        assert response.status_code == 400

    def test_timeline_project_id_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recording = _RecordingService(_full_read_service())
        client = _client(monkeypatch, recording)

        client.get(f"/api/agent/runs/{_RUN_ID}/timeline", params={"limit": 5})

        method, args, kwargs = recording.calls[0]
        assert method == "get_timeline"
        assert args == (_RUN_ID, settings.project.project_id)
        assert kwargs == {"limit": 5, "offset": 0}


# ============================================================
# §二十三：错误契约
# ============================================================


class _FailingService:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def get_run(self, run_id: str, project_id: str) -> Any:
        raise self._error

    def get_timeline(self, run_id: str, project_id: str, **kwargs: Any) -> Any:
        raise self._error


class TestAgentReadApiErrors:
    def test_missing_and_cross_project_share_same_detail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch, _full_read_service())

        missing = client.get("/api/agent/runs/run-unknown")
        foreign = client.get(
            f"/api/agent/runs/{_RUN_ID}", params={"project_id": "project-B"}
        )

        assert missing.status_code == foreign.status_code == 404
        assert missing.json() in (
            {"detail": "Agent Run 不存在"},
            {"detail": "Agent Run 不存在"},
        )
        assert missing.json() == foreign.json()  # 不泄露存在性

    def test_timeline_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch, _full_read_service())
        response = client.get("/api/agent/runs/run-unknown/timeline")
        assert response.status_code == 404

    def test_whitespace_run_id_is_bad_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch, _full_read_service())
        response = client.get("/api/agent/runs/%20%20%20")
        assert response.status_code == 400
        assert "非法输入" in response.json()["detail"]

    def test_overlong_run_id_is_unprocessable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch, _full_read_service())
        response = client.get(f"/api/agent/runs/{'x' * 65}")
        assert response.status_code == 422

    def test_repository_failure_is_502(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for endpoint in (
            f"/api/agent/runs/{_RUN_ID}",
            f"/api/agent/runs/{_RUN_ID}/timeline",
        ):
            client = _client(
                monkeypatch,
                _FailingService(AgentToolResultRepositoryError("db down")),
            )
            response = client.get(endpoint)
            assert response.status_code == 502, endpoint
            assert "db down" not in response.text  # 不回传内部异常文本

    def test_unexpected_failure_is_500(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch, _FailingService(RuntimeError("boom")))
        response = client.get(f"/api/agent/runs/{_RUN_ID}/timeline")
        assert response.status_code == 500
        assert "boom" not in response.text

    def test_approval_repository_failure_is_502(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(
            monkeypatch, _FailingService(AgentApprovalRepositoryError("down"))
        )
        assert client.get(f"/api/agent/runs/{_RUN_ID}").status_code == 502

    def test_not_found_error_class_is_importable(self) -> None:
        assert issubclass(AgentRunNotFoundError, Exception)


# ============================================================
# §二十七 / §三十四：只读 HTTP 面 + 安全
# ============================================================


class TestAgentReadApiSurface:
    def test_agent_paths_are_get_only(self) -> None:
        schema = app.openapi()
        agent_paths = {
            path: methods
            for path, methods in schema["paths"].items()
            if path.startswith("/api/agent/")
        }
        assert set(agent_paths) == {
            "/api/agent/runs/{run_id}",
            "/api/agent/runs/{run_id}/timeline",
        }
        for path, methods in agent_paths.items():
            assert set(methods) == {"get"}, (path, sorted(methods))

    def test_no_login_or_command_endpoints_under_agent(self) -> None:
        """§五：Read API **不**提供 Resume / Approve / Cancel 端点。"""
        paths = set(app.openapi()["paths"])
        for forbidden in ("resume", "approve", "cancel", "execute", "command"):
            assert not any(
                forbidden in path for path in paths if "agent" in path
            ), forbidden

    def test_response_contains_no_forbidden_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch, _full_read_service())

        run_keys = _keys(client.get(f"/api/agent/runs/{_RUN_ID}").json())
        timeline_keys = _keys(
            client.get(f"/api/agent/runs/{_RUN_ID}/timeline").json()
        )

        assert not (run_keys & _FORBIDDEN_KEYS), sorted(run_keys & _FORBIDDEN_KEYS)
        assert not (
            timeline_keys & _FORBIDDEN_KEYS
        ), sorted(timeline_keys & _FORBIDDEN_KEYS)

    def test_claim_token_never_appears_in_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch, _full_read_service())
        body = client.get(f"/api/agent/runs/{_RUN_ID}").text
        assert "claim" not in body.lower()

    def test_service_is_not_cached_between_requests(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无缓存（§二十六 边界：P-8 不做缓存层）—— 每次请求重新解析服务。"""
        calls: list[int] = []

        def _factory() -> AgentReadService:
            calls.append(1)
            return _full_read_service()

        monkeypatch.setattr(agent_read_module, "get_agent_read_service", _factory)
        client = TestClient(app)
        client.get(f"/api/agent/runs/{_RUN_ID}")
        client.get(f"/api/agent/runs/{_RUN_ID}")

        assert calls == [1, 1]
