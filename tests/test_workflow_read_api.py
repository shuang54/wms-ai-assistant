"""Phase 6 Step 7 — S7 Workflow Read API **HTTP 契约**测试（离线；零 DB）。

Phase 6 Step 7.2 更新：四端点已接入 **X-API-Key 认证 + 服务端项目作用域**
（认证依赖 ``workflow_read_auth.require_workflow_read_scope``）。本文件测试
**认证通过后**的响应契约；认证/越权/未配置等安全语义见
``tests/test_workflow_read_api_security_audit.py``。

覆盖（S7 任务书 §八A + S7.2 契约适配）：
```text
1  Run 详情 200（精确字段集；授权作用域来自服务端配置）
2  Node attempt 历史**完整**返回（多 attempt 全保留 + 执行序）
3  Approval 详情 200
4  Timeline 稳定返回（事件顺序）
5  Run 不存在 ⇒ 404
6  Approval 不存在 ⇒ 404
7  缺少凭据 ⇒ 401（四端点 sanity）
8  跨项目资源（不在授权作用域）⇒ 404（与不存在**同码同文案**）
9  input / request_id 不出现在响应（键 + 值双重扫描）
10 凭证 / DB URL 不泄漏（含异常正文注入）
11 Read Service 异常安全映射（502 / 500）
12 无法访问的资源不返回 200
13 不调用 / 不依赖 Runtime 执行入口（AST + 结构）
14 不调用 Tool / LLM / Orchestrator（AST 白名单）
15 写方法零调用 + 只读面形（OpenAPI 仅 GET + security 声明）+ 未知写方法 405
★  客户端 project_id 查询参数被忽略（不信任客户端作用域——S7.2 §4.2）
```
注入方式沿用 ``test_agent_read_api`` 范式：monkeypatch 两个 Composition Root
（读取服务 + 认证配置）+ ``TestClient(app)``；Fake Store 基元复用 S6 测试模块。
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api import workflow_read as workflow_read_module
from backend.app.api import workflow_read_auth as auth_module
from backend.app.config import WorkflowReadSettings
from backend.app.db.models.workflow_approval import (
    WORKFLOW_APPROVAL_ID_MAX_LENGTH,
)
from backend.app.db.models.workflow_node_run import (
    WORKFLOW_NODE_RUN_NODE_ID_MAX_LENGTH,
)
from backend.app.workflow.models import JSON_VALUE_MAX_BYTES
from backend.app.db.models.workflow_run import (
    WORKFLOW_RUN_ID_MAX_LENGTH,
)
from backend.app.dto.workflow_read_api import (
    WORKFLOW_APPROVAL_RESPONSE_FIELDS,
    WORKFLOW_NODE_RUN_RESPONSE_FIELDS,
    WORKFLOW_RUN_DETAIL_RESPONSE_FIELDS,
    WORKFLOW_TIMELINE_EVENT_RESPONSE_FIELDS,
)
from backend.app.main import app
from backend.app.workflow_runtime.ports import WorkflowPortsError
from tests.test_workflow_runtime_read_service import (
    _SECRET,
    _T0,
    _Env,
    _approval_record,
    _node_record,
    _run_record,
)

__all__ = [
    "TestRunDetailEndpoint",
    "TestNodesEndpoint",
    "TestNodeSafeProjectionEndpoint",
    "TestApprovalEndpoint",
    "TestApprovalByNodeEndpoint",
    "TestTimelineEndpoint",
    "TestErrorsAndIsolation",
    "TestSurfaceAndBoundaries",
]

_RUN_ID = "wf-run-1"
_APPROVAL_ID = "wf-ap-1"
_PROJECT_A = "proj-a"

#: 测试专用凭据（**非**生产凭据；仅存在于测试进程内 monkeypatch 配置）。
_TEST_API_KEY = "test-suite-api-key-NOT-A-PRODUCTION-CREDENTIAL"
_HEADERS = {"X-API-Key": _TEST_API_KEY}

#: 绝不允许出现在响应 JSON 键中的字段（S7 §七 禁止清单）。
_FORBIDDEN_KEYS = {
    "input",
    "request_id",
    "claim_token",
    "arguments",
    "content",
    "payload",
    "prompt",
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
}


def _client(
    monkeypatch: pytest.MonkeyPatch,
    env: _Env,
    *,
    settings_override: WorkflowReadSettings | None = None,
) -> TestClient:
    """monkeypatch 读取服务 + 认证配置（两个 Composition Root）。"""
    monkeypatch.setattr(
        workflow_read_module,
        "get_workflow_read_service",
        lambda: env.service,
    )
    monkeypatch.setattr(
        auth_module,
        "get_workflow_read_settings",
        lambda: settings_override
        or WorkflowReadSettings(
            api_key=_TEST_API_KEY, api_project_id=_PROJECT_A
        ),
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


def _full_env() -> _Env:
    run = _run_record(
        request_id="req-SECRET-TRACE",
        input='{"secret_input": "DO-NOT-EXPOSE"}',
    )
    attempts = [
        _node_record(
            node_id="n1",
            attempt=1,
            status="FAILED",
            error_code="WORKFLOW_NODE_EXECUTION_FAILURE",
        ),
        _node_record(
            node_id="n1",
            attempt=2,
            status="SUCCEEDED",
            output='{"qty":3}',
        ),
    ]
    return _Env(
        run=run,
        nodes=attempts,
        approvals={_APPROVAL_ID: _approval_record()},
    )


# ============================================================
# 1 / 5 / 8 / 9 / 10：Run 详情
# ============================================================


class TestRunDetailEndpoint:
    def test_run_detail_200_exact_field_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _full_env()
        client = _client(monkeypatch, env)
        response = client.get(
            f"/api/workflow-runs/{_RUN_ID}", headers=_HEADERS
        )
        assert response.status_code == 200
        payload = response.json()
        assert set(payload.keys()) == set(WORKFLOW_RUN_DETAIL_RESPONSE_FIELDS)
        assert payload["workflow_run_id"] == _RUN_ID
        assert payload["status"] == "RUNNING"
        assert payload["state_version"] == 1
        assert payload["project_id"] == _PROJECT_A
        env.assert_no_writes()

    def test_run_not_found_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        env = _Env()  # 无任何 run
        client = _client(monkeypatch, env)
        response = client.get(
            "/api/workflow-runs/wf-missing", headers=_HEADERS
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Workflow Run 不存在"
        env.assert_no_writes()

    def test_resource_outside_authorized_scope_is_isomorphic_with_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """资源属其他项目（不在授权作用域）⇒ 与不存在**同码同文案**。"""
        env = _Env(run=_run_record(project_id="proj-OTHER"))
        client = _client(monkeypatch, env)
        outside_scope = client.get(
            f"/api/workflow-runs/{_RUN_ID}", headers=_HEADERS
        )
        missing = client.get(
            "/api/workflow-runs/wf-missing", headers=_HEADERS
        )
        assert outside_scope.status_code == missing.status_code == 404
        assert outside_scope.json() == missing.json()  # 不泄露存在性

    def test_missing_credential_rejected_401_sanity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _full_env()
        client = _client(monkeypatch, env)
        response = client.get(f"/api/workflow-runs/{_RUN_ID}")  # 无 header
        assert response.status_code == 401
        env.assert_no_writes()

    def test_input_and_request_id_never_exposed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _full_env()
        client = _client(monkeypatch, env)
        response = client.get(
            f"/api/workflow-runs/{_RUN_ID}", headers=_HEADERS
        )
        assert response.status_code == 200
        keys = _keys(response.json())
        assert keys & _FORBIDDEN_KEYS == set()
        text = response.text
        assert "DO-NOT-EXPOSE" not in text  # input 内容不泄漏
        assert "SECRET-TRACE" not in text  # request_id 值不泄漏

    def test_client_project_id_query_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S7.2 §4.2：客户端 project_id 参数**不**参与授权——被忽略。

        授权作用域 = 服务端配置（proj-a）；客户端携带 proj-B ⇒ 结果仍按
        proj-a 判定（命中 ⇒ 200），客户端**无法**扩宽 / 切换作用域。
        """
        env = _Env(run=_run_record(project_id=_PROJECT_A))
        client = _client(monkeypatch, env)
        response = client.get(
            f"/api/workflow-runs/{_RUN_ID}",
            headers=_HEADERS,
            params={"project_id": "proj-B"},  # 伪装请求（未知参数 ⇒ 忽略）
        )
        assert response.status_code == 200
        assert response.json()["project_id"] == _PROJECT_A  # 非客户端主张值


# ============================================================
# 2：Node attempt 历史
# ============================================================


class TestNodesEndpoint:
    def test_full_attempt_history_in_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _full_env()
        client = _client(monkeypatch, env)
        response = client.get(
            f"/api/workflow-runs/{_RUN_ID}/nodes", headers=_HEADERS
        )
        assert response.status_code == 200
        items = response.json()
        assert [(item["node_id"], item["attempt"]) for item in items] == [
            ("n1", 1),
            ("n1", 2),
        ]  # 历史 attempt 全保留（不止最后一次）
        assert items[0]["status"] == "FAILED"
        assert items[1]["output"] == '{"qty":3}'  # 已持久化输出原样
        for item in items:
            assert set(item.keys()) == set(WORKFLOW_NODE_RUN_RESPONSE_FIELDS)
        env.assert_no_writes()

    def test_empty_history_200_empty_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _Env(run=_run_record(), nodes=[])
        client = _client(monkeypatch, env)
        response = client.get(
            f"/api/workflow-runs/{_RUN_ID}/nodes", headers=_HEADERS
        )
        assert response.status_code == 200
        assert response.json() == []


# ============================================================
# Phase 7：Node 安全投影（损坏 ⇒ 502 fail closed，绝不 200）
# ============================================================


class TestNodeSafeProjectionEndpoint:
    """``/nodes`` 经 ``get_node_run_details``（安全投影）而非原始 Store Record。

    旧实现直接透传 ``NodeRunRecord`` ⇒ 损坏的 ``output`` / 越白名单
    ``status`` · ``node_type`` · ``error_code`` 会以 **200** 泄漏。
    本类锁定"损坏 ⇒ 502 fail closed"（不截断 / 不修复 / 不原样返回）。
    """

    @pytest.mark.parametrize(
        "corrupted",
        ("{not json", '{"truncated": "abc', "123abc"),
    )
    def test_invalid_json_output_never_reaches_200(
        self, monkeypatch: pytest.MonkeyPatch, corrupted: str
    ) -> None:
        env = _Env(run=_run_record(), nodes=[_node_record(output=corrupted)])
        client = _client(monkeypatch, env)

        response = client.get(
            f"/api/workflow-runs/{_RUN_ID}/nodes", headers=_HEADERS
        )

        assert response.status_code == 502
        assert corrupted not in response.text  # 损坏数据不进入任何响应
        assert response.json()["detail"] == "Workflow 读取数据源不可用"
        env.assert_no_writes()

    def test_oversized_output_never_reaches_200(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        oversized = '{"pad":"' + ("x" * (JSON_VALUE_MAX_BYTES + 16)) + '"}'
        env = _Env(run=_run_record(), nodes=[_node_record(output=oversized)])
        client = _client(monkeypatch, env)

        response = client.get(
            f"/api/workflow-runs/{_RUN_ID}/nodes", headers=_HEADERS
        )

        assert response.status_code == 502
        assert "x" * 128 not in response.text     # 不截断后回传部分输出

    @pytest.mark.parametrize(
        "overrides",
        (
            {"status": "NOT_A_STATUS"},
            {"node_type": "MAGIC_TYPE"},
            {"status": "FAILED", "error_code": "Traceback (most recent call last)"},
            {"status": "FAILED", "error_code": "X" * 65},
        ),
    )
    def test_illegal_status_type_or_error_code_never_reaches_200(
        self, monkeypatch: pytest.MonkeyPatch, overrides: dict
    ) -> None:
        env = _Env(run=_run_record(), nodes=[_node_record(**overrides)])
        client = _client(monkeypatch, env)

        response = client.get(
            f"/api/workflow-runs/{_RUN_ID}/nodes", headers=_HEADERS
        )

        assert response.status_code == 502
        for value in overrides.values():
            assert str(value) not in response.text  # 非法值不被原样返回

    def test_valid_projection_still_200_with_exact_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """安全投影不改变合法响应的字段集与取值（契约兼容）。"""
        env = _Env(
            run=_run_record(),
            nodes=[
                _node_record(
                    node_id="n1",
                    attempt=1,
                    status="FAILED",
                    error_code="WORKFLOW_NODE_EXECUTION_FAILURE",
                ),
                _node_record(
                    node_id="n1", attempt=2, status="SUCCEEDED",
                    output='{"qty":3}', elapsed_ms=12.5,
                ),
            ],
        )
        client = _client(monkeypatch, env)

        response = client.get(
            f"/api/workflow-runs/{_RUN_ID}/nodes", headers=_HEADERS
        )

        assert response.status_code == 200
        items = response.json()
        assert [item["attempt"] for item in items] == [1, 2]
        assert items[1]["output"] == '{"qty":3}'
        assert items[1]["elapsed_ms"] == 12.5
        assert items[0]["error_code"] == "WORKFLOW_NODE_EXECUTION_FAILURE"
        for item in items:
            assert set(item.keys()) == set(WORKFLOW_NODE_RUN_RESPONSE_FIELDS)
        assert not (_keys(items) & _FORBIDDEN_KEYS)


# ============================================================
# Phase 7：按 (run, node) 读取审批（新增最小只读端点）
# ============================================================


class TestApprovalByNodeEndpoint:
    """``GET /api/workflow-runs/{run_id}/approvals/{node_id}``。

    复用 ``WorkflowReadService.get_approval_for_node``：
    父 Run 归属 + 服务端项目作用域 + 审批行 ``(run, node)`` 一致性。
    """

    def _path(self, node_id: str = "gate") -> str:
        return f"/api/workflow-runs/{_RUN_ID}/approvals/{node_id}"

    def _env_with_approval(self, approval=None) -> _Env:
        record = approval if approval is not None else _approval_record(
            node_id="gate"
        )
        return _Env(
            run=_run_record(),
            nodes=[_node_record(node_type="APPROVAL", node_id="gate")],
            by_node={(_RUN_ID, "gate"): record},
        )

    def test_pending_approval_200_exact_field_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = self._env_with_approval(
            _approval_record(node_id="gate", status="PENDING", resolved_at=None)
        )
        client = _client(monkeypatch, env)

        response = client.get(self._path(), headers=_HEADERS)

        assert response.status_code == 200
        payload = response.json()
        assert set(payload.keys()) == set(WORKFLOW_APPROVAL_RESPONSE_FIELDS)
        assert payload["approval_id"] == _APPROVAL_ID
        assert payload["workflow_run_id"] == _RUN_ID
        assert payload["node_id"] == "gate"
        assert payload["status"] == "PENDING"
        assert payload["resolved_at"] is None      # 未裁决 ⇒ null（不伪造）
        assert "created_at" not in payload         # Port 未暴露 ⇒ 不补造
        env.assert_no_writes()

    def test_resolved_approval_200_keeps_persisted_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolved = _T0
        env = self._env_with_approval(
            _approval_record(
                node_id="gate", status="APPROVED", resolved_at=resolved
            )
        )
        client = _client(monkeypatch, env)

        payload = client.get(self._path(), headers=_HEADERS).json()

        assert payload["status"] == "APPROVED"
        assert payload["resolved_at"] is not None  # 真实裁决时间

    def test_no_approval_row_is_404(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """该节点尚无审批行 ⇒ 404（合法事实；与"不存在"同码同文案）。"""
        env = _Env(run=_run_record(), by_node={})
        client = _client(monkeypatch, env)

        response = client.get(self._path("never-created"), headers=_HEADERS)

        assert response.status_code == 404
        assert response.json()["detail"] == "Workflow 审批不存在"
        env.assert_no_writes()

    def test_run_not_found_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        env = _Env(run=None)
        client = _client(monkeypatch, env)

        response = client.get(self._path(), headers=_HEADERS)

        assert response.status_code == 404
        assert response.json()["detail"] == "Workflow 审批不存在"

    def test_project_mismatch_404_isomorphic_with_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """授权作用域（服务端配置）与 Run 所属项目不一致 ⇒ 404（不泄漏存在性）。"""
        env = self._env_with_approval()
        client = _client(
            monkeypatch,
            env,
            settings_override=WorkflowReadSettings(
                api_key=_TEST_API_KEY, api_project_id="proj-OTHER"
            ),
        )

        response = client.get(self._path(), headers=_HEADERS)

        assert response.status_code == 404
        assert response.json()["detail"] == "Workflow 审批不存在"

    @pytest.mark.parametrize(
        "record_kwargs",
        (
            {"workflow_run_id": "wf-run-OTHER"},
            {"node_id": "other-node"},
        ),
    )
    def test_ownership_inconsistency_fails_closed_502(
        self, monkeypatch: pytest.MonkeyPatch, record_kwargs: dict
    ) -> None:
        """存储层返回跨 Run / 跨 Node 的审批 ⇒ Read Layer fail closed ⇒ 502。"""
        env = self._env_with_approval(
            _approval_record(**record_kwargs)
        )
        client = _client(monkeypatch, env)

        response = client.get(self._path(), headers=_HEADERS)

        assert response.status_code == 502
        assert response.json()["detail"] == "Workflow 读取数据源不可用"
        env.assert_no_writes()

    def test_status_outside_whitelist_fails_closed_502(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = self._env_with_approval(
            _approval_record(node_id="gate", status="MAYBE")
        )
        client = _client(monkeypatch, env)

        response = client.get(self._path(), headers=_HEADERS)

        assert response.status_code == 502
        assert "MAYBE" not in response.text

    @pytest.mark.parametrize("suffix", ("%20", "%20%20"))
    def test_blank_run_or_node_400(
        self, monkeypatch: pytest.MonkeyPatch, suffix: str
    ) -> None:
        env = self._env_with_approval()
        client = _client(monkeypatch, env)

        assert client.get(
            f"/api/workflow-runs/{suffix}/approvals/gate", headers=_HEADERS
        ).status_code == 400
        assert client.get(
            f"/api/workflow-runs/{_RUN_ID}/approvals/{suffix}",
            headers=_HEADERS,
        ).status_code == 400

    def test_missing_credential_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        env = self._env_with_approval()
        client = _client(monkeypatch, env)

        response = client.get(self._path())      # 无 X-API-Key

        assert response.status_code == 401
        assert response.json()["detail"] == "认证失败：缺少或无效的 API Key"

    def test_wrong_credential_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        env = self._env_with_approval()
        client = _client(monkeypatch, env)

        response = client.get(
            self._path(), headers={"X-API-Key": "wrong-key"}
        )

        assert response.status_code == 401

    def test_over_length_path_params_422(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = self._env_with_approval()
        client = _client(monkeypatch, env)

        assert client.get(
            f"/api/workflow-runs/{'x' * 65}/approvals/gate", headers=_HEADERS
        ).status_code == 422
        assert client.get(
            f"/api/workflow-runs/{_RUN_ID}/approvals/{'x' * 65}",
            headers=_HEADERS,
        ).status_code == 422

    def test_auth_precedes_data_access_and_blank_checks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未认证优先于 400 / 404（认证在任何数据读取之前）。"""
        env = _Env(run=None)
        client = _client(monkeypatch, env)

        blank = client.get(
            f"/api/workflow-runs/%20/approvals/%20"
        )
        assert blank.status_code == 401

    def test_response_has_no_input_or_forbidden_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = self._env_with_approval()
        client = _client(monkeypatch, env)

        payload = client.get(self._path(), headers=_HEADERS).json()

        assert not (_keys(payload) & _FORBIDDEN_KEYS)
        # 无 ORM / Session / Connection 痕迹
        for value in payload.values():
            assert not hasattr(value, "__table__")
            assert not hasattr(value, "connection")

    def test_read_endpoint_does_not_trigger_runtime_advance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """只读端点不得触发 Runtime 状态推进（Fake 写方法计数 = 0）。"""
        env = self._env_with_approval()
        client = _client(monkeypatch, env)

        for _ in range(3):
            assert client.get(self._path(), headers=_HEADERS).status_code == 200

        env.assert_no_writes()
        assert env.run_store.get_calls >= 3      # 每次都经父 Run 校验
        assert env.approval_store.for_node_calls >= 3


# ============================================================
# 3 / 6：Approval 详情
# ============================================================


class TestApprovalEndpoint:
    def test_approval_detail_200(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _full_env()
        client = _client(monkeypatch, env)
        response = client.get(
            f"/api/workflow-approvals/{_APPROVAL_ID}", headers=_HEADERS
        )
        assert response.status_code == 200
        payload = response.json()
        assert set(payload.keys()) == set(WORKFLOW_APPROVAL_RESPONSE_FIELDS)
        assert payload["approval_id"] == _APPROVAL_ID
        assert payload["status"] == "PENDING"
        assert payload["resolved_at"] is None
        env.assert_no_writes()

    def test_approval_not_found_404(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _Env(run=_run_record())
        client = _client(monkeypatch, env)
        response = client.get(
            "/api/workflow-approvals/wf-ap-missing", headers=_HEADERS
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Workflow 审批不存在"

    def test_approval_parent_outside_scope_404_isomorphic(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _Env(
            run=_run_record(project_id="proj-OTHER"),
            approvals={_APPROVAL_ID: _approval_record()},
        )
        client = _client(monkeypatch, env)
        outside_scope = client.get(
            f"/api/workflow-approvals/{_APPROVAL_ID}", headers=_HEADERS
        )
        missing = client.get(
            "/api/workflow-approvals/wf-ap-missing", headers=_HEADERS
        )
        assert outside_scope.status_code == missing.status_code == 404
        assert outside_scope.json() == missing.json()


# ============================================================
# 4：Timeline
# ============================================================


class TestTimelineEndpoint:
    def test_timeline_200_stable_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = _run_record()
        attempts = [
            _node_record(
                node_id="n1",
                attempt=1,
                status="SUCCEEDED",
                output='{"qty":3}',
            )
        ]
        env = _Env(run=run, nodes=attempts)
        client = _client(monkeypatch, env)
        response = client.get(
            f"/api/workflow-runs/{_RUN_ID}/timeline", headers=_HEADERS
        )
        assert response.status_code == 200
        items = response.json()
        kinds = [item["kind"] for item in items]
        assert kinds == ["RUN_CREATED", "RUN_STARTED", "NODE_ATTEMPT_STARTED"]
        for item in items:
            assert set(item.keys()) == set(
                WORKFLOW_TIMELINE_EVENT_RESPONSE_FIELDS
            )
        stamps = [item["timestamp"] for item in items]
        assert stamps == sorted(stamps)  # 稳定排序保持
        env.assert_no_writes()

    def test_timeline_outside_scope_404(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _full_env()
        client = _client(monkeypatch, env)
        response = client.get(
            f"/api/workflow-runs/{_RUN_ID}/timeline", headers=_HEADERS
        )
        assert response.status_code == 200
        env_other = _Env(run=_run_record(project_id="proj-OTHER"))
        client_other = _client(monkeypatch, env_other)
        blocked = client_other.get(
            f"/api/workflow-runs/{_RUN_ID}/timeline", headers=_HEADERS
        )
        assert blocked.status_code == 404


# ============================================================
# 10 / 11 / 12：错误与隔离
# ============================================================


class TestErrorsAndIsolation:
    def test_read_service_failure_maps_to_502_without_body_leak(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _full_env()
        env.run_store.error = WorkflowPortsError(f"db down {_SECRET}")
        client = _client(monkeypatch, env)
        response = client.get(
            f"/api/workflow-runs/{_RUN_ID}", headers=_HEADERS
        )
        assert response.status_code == 502
        assert response.json()["detail"] == "Workflow 读取数据源不可用"
        assert _SECRET not in response.text  # 异常正文 / 凭证不泄漏

    def test_unexpected_error_maps_to_500(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _BoomService:
            def get_run_detail(self, **kwargs: Any) -> Any:  # pragma: no cover
                raise RuntimeError(f"boom {_SECRET}")

        monkeypatch.setattr(
            workflow_read_module,
            "get_workflow_read_service",
            lambda: _BoomService(),
        )
        monkeypatch.setattr(
            auth_module,
            "get_workflow_read_settings",
            lambda: WorkflowReadSettings(
                api_key=_TEST_API_KEY, api_project_id=_PROJECT_A
            ),
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            f"/api/workflow-runs/{_RUN_ID}", headers=_HEADERS
        )
        assert response.status_code == 500
        assert _SECRET not in response.text  # 不暴露堆栈 / 正文

    def test_corrupted_record_maps_to_502_not_200(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _Env(run=_run_record(status="NOT_A_STATUS"))  # 数据损坏
        client = _client(monkeypatch, env)
        response = client.get(
            f"/api/workflow-runs/{_RUN_ID}", headers=_HEADERS
        )
        assert response.status_code == 502  # 损坏不伪装成 200

    def test_blank_run_id_400(self, monkeypatch: pytest.MonkeyPatch) -> None:
        env = _full_env()
        client = _client(monkeypatch, env)
        blank_run = client.get("/api/workflow-runs/%20", headers=_HEADERS)
        assert blank_run.status_code == 400

    def test_auth_rejected_before_blank_input_checks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """认证先于任何数据读取 / 参数处理（缺凭据 ⇒ 401，不泄露输入处理）。"""
        env = _full_env()
        client = _client(monkeypatch, env)
        response = client.get("/api/workflow-runs/%20")  # 无凭据
        assert response.status_code == 401

    def test_over_length_path_422(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _full_env()
        client = _client(monkeypatch, env)
        response = client.get(
            f"/api/workflow-runs/{'x' * 65}", headers=_HEADERS
        )
        assert response.status_code == 422  # FastAPI Path 校验


# ============================================================
# 12 / 13 / 14 / 15：面形与边界
# ============================================================


#: Workflow Read 只读面（Phase 7 闭环后 5 个端点：GET-only + 全部挂认证）。
_READ_ROUTES = (
    "/api/workflow-runs/{run_id}",
    "/api/workflow-runs/{run_id}/nodes",
    "/api/workflow-runs/{run_id}/timeline",
    "/api/workflow-runs/{run_id}/approvals/{node_id}",
    "/api/workflow-approvals/{approval_id}",
)


class TestSurfaceAndBoundaries:
    def test_openapi_surface_is_get_only(self) -> None:
        paths = app.openapi()["paths"]
        for route in _READ_ROUTES:
            assert route in paths, f"缺少路由: {route}"
            assert set(paths[route].keys()) == {"get"}, (
                f"{route} 只能有 GET（只读面形）"
            )

    def test_openapi_declares_auth_on_all_read_endpoints(self) -> None:
        """只读面**每个**端点均声明 API Key security（与运行时一致）。

        Phase 7 闭环新增 ``/workflow-runs/{run_id}/approvals/{node_id}``
        ⇒ 由四个端点扩展为五个；不变式（"无未认证读端点"）保持不变。
        """
        schema = app.openapi()
        schemes = schema["components"]["securitySchemes"]
        assert schemes, "缺少 securitySchemes（认证未声明）"
        api_key_schemes = [
            name
            for name, definition in schemes.items()
            if definition.get("type") == "apiKey"
            and definition.get("in") == "header"
            and definition.get("name") == "X-API-Key"
        ]
        assert api_key_schemes, f"无 X-API-Key scheme: {schemes!r}"
        scheme_name = api_key_schemes[0]
        for route in _READ_ROUTES:
            operation = schema["paths"][route]["get"]
            requirement = operation.get("security")
            assert requirement, f"{route} 未声明 security"
            assert any(scheme_name in entry for entry in requirement), (
                f"{route} security 与 scheme 不一致: {requirement!r}"
            )

    def test_write_methods_rejected_without_state_change(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _full_env()
        client = _client(monkeypatch, env)
        for method in ("post", "put", "patch", "delete"):
            response = getattr(client, method)(
                f"/api/workflow-runs/{_RUN_ID}", headers=_HEADERS
            )
            assert response.status_code in (404, 405)
        env.assert_no_writes()  # 未知写方法不创建 / 不修改任何状态

    def test_path_constants_match_db_model(self) -> None:
        assert (
            workflow_read_module.WORKFLOW_RUN_ID_MAX_LENGTH
            == WORKFLOW_RUN_ID_MAX_LENGTH
        )
        assert (
            workflow_read_module.WORKFLOW_APPROVAL_ID_MAX_LENGTH
            == WORKFLOW_APPROVAL_ID_MAX_LENGTH
        )
        # Phase 7 闭环：按 (run, node) 的审批端点的 node_id 边界须与列宽一致
        assert (
            workflow_read_module.WORKFLOW_NODE_ID_MAX_LENGTH
            == WORKFLOW_NODE_RUN_NODE_ID_MAX_LENGTH
        )

    def test_api_module_does_not_expose_execution_entrypoints(self) -> None:
        for forbidden in (
            "start",
            "advance",
            "request_cancel",
            "submit_approval_decision",
        ):
            assert not hasattr(workflow_read_module, forbidden)

    def _modules(self, path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        return modules

    def test_api_import_boundary(self) -> None:
        modules = self._modules(Path(workflow_read_module.__file__))
        assert modules, "workflow_read.py import 收集为空（环境异常）"
        allowed_stdlib = {"__future__", "asyncio", "logging", "typing"}
        allowed_app = {
            "backend.app.dto.workflow_read_api",
            "backend.app.workflow_runtime.read_factory",
            "backend.app.workflow_runtime.read_service",
            "backend.app.api.workflow_read_auth",
        }
        for module in modules:
            if module.split(".")[0] in allowed_stdlib:
                continue
            if module == "fastapi" or module.startswith("fastapi."):
                continue
            assert module in allowed_app, (
                f"workflow_read.py 出现边界外 import: {module!r}"
            )

    def test_api_forbidden_imports_absent(self) -> None:
        modules = self._modules(Path(workflow_read_module.__file__))
        for forbidden in (
            "backend.app.db",
            "backend.app.services",
            "backend.app.tools",
            "backend.app.agent",
            "backend.app.llm",
            "backend.app.projects",
            "backend.app.config",
            "backend.app.workflow_runtime.runtime",
            "backend.app.workflow_runtime.execution",
            "backend.app.workflow_runtime.binding",
            "backend.app.workflow_runtime.ports",
            "backend.app.workflow_runtime.factory",
            "backend.app.workflow_runtime.adapters",
            "backend.app.workflow_runtime.store_adapters",
            "sqlalchemy",
        ):
            assert not any(
                m == forbidden or m.startswith(forbidden + ".")
                for m in modules
            ), f"workflow_read.py 禁入 import: {forbidden!r}"

    def test_auth_module_import_boundary(self) -> None:
        modules = self._modules(Path(auth_module.__file__))
        assert modules, "workflow_read_auth.py import 收集为空（环境异常）"
        for module in modules:
            if module.split(".")[0] in {"__future__", "logging", "secrets", "dataclasses", "typing"}:
                continue
            if module == "fastapi" or module.startswith("fastapi."):
                continue
            assert module == "backend.app.config", (
                f"workflow_read_auth.py 出现边界外 import: {module!r}"
            )

    def test_read_factory_import_boundary(self) -> None:
        from backend.app.workflow_runtime import read_factory as read_factory_module

        modules = self._modules(Path(read_factory_module.__file__))
        for module in modules:
            if module.split(".")[0] in {"__future__"}:
                continue
            if module.startswith("backend.app.db."):
                continue
            assert module in {
                "backend.app.workflow_runtime.read_service",
                "backend.app.workflow_runtime.store_adapters",
            }, f"read_factory.py 出现边界外 import: {module!r}"

    def test_timeline_endpoint_does_not_reorder_events(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """API 层不重排（服务层稳定排序原样透出）。"""
        same = _T0
        env = _Env(
            run=_run_record(
                created_at=same, started_at=None, completed_at=None
            ),
            nodes=[
                _node_record(
                    node_id="b", attempt=1, started_at=same, completed_at=None
                ),
                _node_record(
                    node_id="a", attempt=1, started_at=same, completed_at=None
                ),
            ],
        )
        client = _client(monkeypatch, env)
        response = client.get(
            f"/api/workflow-runs/{_RUN_ID}/timeline", headers=_HEADERS
        )
        assert response.status_code == 200
        node_order = [
            item["node_id"] for item in response.json() if item["node_id"]
        ]
        assert node_order == ["a", "b"]  # 服务层身份键升序原样
