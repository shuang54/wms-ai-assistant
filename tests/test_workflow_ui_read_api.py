"""Workflow UI Read API 测试（Phase 7 Workflow UI Step 2）—— **全离线**。

覆盖（Step 2 §七.A/B）：

    1. 未登录请求被拒绝（401）
    2. 有效 Session + 项目成员授权可访问
    3. 无效 / 过期 Session 被拒绝（401）
    4. 无项目授权的用户不能读取（403）
    5. 用户不能读取其他项目的 Run（**同构 404**）
    6. Node Attempts 必须属于已授权 Run（跨 Run 记录 ⇒ fail closed 502）
    7. Approval 必须属于正确的 Run 与 Node（归属不一致 ⇒ 502）
    8. 缺失资源与跨项目资源采用**同码同文案**安全错误
    9. 所有 UI 路由都受认证 + 授权依赖保护（源级 + OpenAPI 断言）
   10. 既有服务间 X-API-Key 读面行为未被破坏
   11. 只读边界：只允许 GET；不触 Runtime 执行入口 / Tool / LLM；零写入
   12. 响应不泄漏 input / 敏感 metadata / ORM / Session / Connection

复用项目**既有**测试基础设施（**不新增认证机制**）：
    * ``tests/api_session_auth.py`` 的 ``FakeSessionService`` /
      ``SessionTestClient`` / ``install_authorization_grants``；
    * ``tests/test_workflow_runtime_read_service.py`` 的 ``_Env`` 与记录构造器。
"""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api import workflow_read as service_api_module
from backend.app.api import workflow_ui_read as ui_api_module
from backend.app.db.models.workflow_approval import (
    WORKFLOW_APPROVAL_ID_MAX_LENGTH,
)
from backend.app.db.models.workflow_node_run import (
    WORKFLOW_NODE_RUN_NODE_ID_MAX_LENGTH,
)
from backend.app.db.models.workflow_run import (
    WORKFLOW_RUN_ID_MAX_LENGTH,
    WORKFLOW_RUN_PROJECT_ID_MAX_LENGTH,
)
from backend.app.dto.workflow_read_api import (
    WORKFLOW_APPROVAL_RESPONSE_FIELDS,
    WORKFLOW_NODE_RUN_RESPONSE_FIELDS,
    WORKFLOW_RUN_DETAIL_RESPONSE_FIELDS,
    WORKFLOW_TIMELINE_EVENT_RESPONSE_FIELDS,
)
from backend.app.main import app
from backend.app.workflow_runtime.ports import WorkflowPortsError
from tests.api_session_auth import (
    FakeSessionService,
    SessionTestClient,
    install_authorization_grants,
    install_fake_session_service,
)
from tests.conversation_auth_support import USER_IDS
from tests.test_workflow_runtime_read_service import (
    _SECRET,
    _T0,
    _Env,
    _approval_record,
    _node_record,
    _run_record,
)

__all__ = [
    "TestAuthenticationRequired",
    "TestProjectMembership",
    "TestProjectIsolation",
    "TestNodeAndApprovalOwnership",
    "TestReadOnlyBoundary",
    "TestResponseContract",
    "TestServiceApiUnchanged",
    "TestRouteSurface",
]

_RUN_ID = "wf-run-1"
_APPROVAL_ID = "wf-ap-1"
_PROJECT = "proj-a"
_OTHER_PROJECT = "proj-b"

_UI = "/api/ui/projects"


def _alice() -> str:
    return USER_IDS["alice"]


def _bob() -> str:
    return USER_IDS["bob"]


def _client(
    monkeypatch: pytest.MonkeyPatch,
    env: _Env,
    *,
    username: str = "alice",
) -> SessionTestClient:
    """已登录客户端 + 替换 Read Service 组合根（认证 / 授权链路真实执行）。"""
    monkeypatch.setattr(
        ui_api_module,
        "get_workflow_ui_read_service",
        lambda: env.service,
    )
    return SessionTestClient(app, username=username)


def _full_env() -> _Env:
    return _Env(
        run=_run_record(
            request_id="req-SECRET-TRACE",
            input='{"secret_input": "DO-NOT-EXPOSE"}',
        ),
        nodes=[
            _node_record(
                node_id="n1",
                attempt=1,
                status="FAILED",
                error_code="WORKFLOW_NODE_EXECUTION_FAILURE",
            ),
            _node_record(
                node_id="n1", attempt=2, status="SUCCEEDED", output='{"qty":3}'
            ),
            _node_record(
                node_id="gate", node_type="APPROVAL",
                status="WAITING_APPROVAL",
            ),
        ],
        by_node={(_RUN_ID, "gate"): _approval_record(node_id="gate")},
    )


def _member_env(monkeypatch: pytest.MonkeyPatch) -> _Env:
    """已登录 + **该项目成员** 授权（M2.4 授权表）。"""
    install_authorization_grants(
        monkeypatch,
        project_roles={(_alice(), _PROJECT): {"MEMBER"}},
    )
    return _full_env()


# ============================================================
# 1 / 3：认证
# ============================================================


class TestAuthenticationRequired:
    @pytest.mark.parametrize(
        "path",
        (
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}",
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}/nodes",
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}/timeline",
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}/approvals/gate",
            f"{_UI}/{_PROJECT}/workflow-approvals/{_APPROVAL_ID}",
        ),
    )
    def test_anonymous_rejected_401(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        """未登录（无会话 Cookie）⇒ 401（固定文案）。"""
        install_fake_session_service(monkeypatch)
        _member_env(monkeypatch)

        with TestClient(app) as anonymous:
            response = anonymous.get(path)

        assert response.status_code == 401
        assert response.json()["detail"] == "认证失败：缺少或无效的会话"

    @pytest.mark.parametrize(
        "path",
        (
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}",
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}/nodes",
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}/timeline",
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}/approvals/gate",
            f"{_UI}/{_PROJECT}/workflow-approvals/{_APPROVAL_ID}",
        ),
    )
    def test_invalid_or_expired_session_rejected_401(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        """无效 / 过期会话 ⇒ 401（与"资源是否存在"无关）。"""
        service: FakeSessionService = install_fake_session_service(monkeypatch)
        _member_env(monkeypatch)
        client = SessionTestClient(app)
        # 使当前会话过期（模拟过期 / 吊销）
        service.expire(client.session_token)

        response = client.get(path)

        assert response.status_code == 401
        assert response.json()["detail"] == "认证失败：缺少或无效的会话"

    def test_anonymous_401_precedes_blank_param_400(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """认证在任何数据读取 / 参数校验**之前**。"""
        install_fake_session_service(monkeypatch)
        _member_env(monkeypatch)

        with TestClient(app) as anonymous:
            response = anonymous.get(
                f"{_UI}/%20/workflow-runs/%20"
            )

        assert response.status_code == 401

    def test_valid_session_can_read_authorized_project(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """2：有效 Session + 该项目成员 ⇒ 200。"""
        install_fake_session_service(monkeypatch)
        env = _member_env(monkeypatch)
        client = _client(monkeypatch, env)

        response = client.get(f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}")

        assert response.status_code == 200
        assert response.json()["workflow_run_id"] == _RUN_ID
        env.assert_no_writes()


# ============================================================
# 4：项目授权（M2.4 授权表）
# ============================================================


class TestProjectMembership:
    def test_user_without_grant_is_forbidden_403(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无任何项目授权 ⇒ 403（固定文案；不泄漏资源是否存在）。"""
        install_fake_session_service(monkeypatch)
        install_authorization_grants(monkeypatch)     # 空授权表
        env = _full_env()
        client = _client(monkeypatch, env)

        response = client.get(f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}")

        assert response.status_code == 403
        assert response.json()["detail"] == "权限不足：需要项目访问授权"
        env.assert_no_writes()

    def test_grant_for_other_project_is_forbidden(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """仅持有**其它**项目授权 ⇒ 403（授权按项目隔离）。"""
        install_fake_session_service(monkeypatch)
        install_authorization_grants(
            monkeypatch,
            project_roles={(_alice(), _OTHER_PROJECT): {"MEMBER"}},
        )
        env = _full_env()
        client = _client(monkeypatch, env)

        response = client.get(f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}")

        assert response.status_code == 403

    def test_project_ops_grant_also_allows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``PROJECT_OPS`` 是 ``MEMBER`` 的超集 ⇒ 同样可读。"""
        install_fake_session_service(monkeypatch)
        install_authorization_grants(
            monkeypatch,
            project_roles={(_alice(), _PROJECT): {"PROJECT_OPS"}},
        )
        env = _full_env()
        client = _client(monkeypatch, env)

        assert client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}"
        ).status_code == 200

    def test_authorization_store_unavailable_is_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """授权存储不可用 ⇒ 503 fail closed（**不**降级为放行 / 403）。"""
        install_fake_session_service(monkeypatch)
        install_authorization_grants(monkeypatch, failure="unavailable")
        env = _full_env()
        client = _client(monkeypatch, env)

        response = client.get(f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}")

        assert response.status_code == 503
        assert response.json()["detail"] == "授权服务不可用"

    def test_session_service_unavailable_is_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """会话服务不可用 ⇒ 503 fail closed。"""
        install_fake_session_service(monkeypatch)
        _member_env(monkeypatch)
        env = _full_env()
        client = _client(monkeypatch, env)
        # 会话服务故障（模拟 DB 不可用）
        from backend.app.api import auth_dependency as auth_dep
        from backend.app.services.session_service import SessionServiceError

        class _Broken:
            def validate(self, token: Any) -> Any:
                raise SessionServiceError("down")

            def invalidate(self, token: Any) -> None: ...

            def login(self, **kwargs: Any) -> Any:
                raise SessionServiceError("down")

        monkeypatch.setattr(auth_dep, "get_session_service", lambda: _Broken())

        response = client.get(f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}")

        assert response.status_code == 503
        assert response.json()["detail"] == "认证服务不可用"

    def test_grant_is_bound_to_user_id_not_username(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """授权按 ``user_id`` 绑定：为 alice 授权，bob 仍被拒（403）。"""
        install_fake_session_service(monkeypatch)
        install_authorization_grants(
            monkeypatch,
            project_roles={(_alice(), _PROJECT): {"MEMBER"}},
        )
        env = _full_env()

        bob_client = _client(monkeypatch, env, username="bob")
        response = bob_client.get(f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}")

        assert response.status_code == 403

    def test_client_cannot_override_authorization_by_path_project(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """路径 ``project_id`` 只是请求目标；换成其它项目不会扩权（403）。"""
        install_fake_session_service(monkeypatch)
        install_authorization_grants(
            monkeypatch,
            project_roles={(_alice(), _PROJECT): {"MEMBER"}},
        )
        env = _full_env()
        client = _client(monkeypatch, env)

        for claimed in (_PROJECT, _OTHER_PROJECT, "other", " "):
            response = client.get(
                f"{_UI}/{claimed}/workflow-runs/{_RUN_ID}"
            )
            assert response.status_code in {200, 400, 403, 404}

        # 关键：非成员项目一律 403（不因路径改变而放行）
        assert client.get(
            f"{_UI}/{_OTHER_PROJECT}/workflow-runs/{_RUN_ID}"
        ).status_code == 403


# ============================================================
# 5 / 8：跨项目隔离与同构错误
# ============================================================


class TestProjectIsolation:
    @pytest.mark.parametrize(
        "suffix",
        (
            "",
            "/nodes",
            "/timeline",
        ),
    )
    def test_cross_project_run_isomorphic_404(
        self, monkeypatch: pytest.MonkeyPatch, suffix: str
    ) -> None:
        """成员项目内请求**别的项目**的 Run ⇒ 404（与"不存在"同码同文案）。"""
        install_fake_session_service(monkeypatch)
        install_authorization_grants(
            monkeypatch,
            project_roles={(_alice(), _PROJECT): {"MEMBER"}},
        )
        # Run 实际归属 _OTHER_PROJECT
        env = _Env(run=_run_record(project_id=_OTHER_PROJECT), nodes=[])
        client = _client(monkeypatch, env)

        cross = client.get(f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}{suffix}")
        missing_env = _Env(run=None, nodes=[])
        monkeypatch.setattr(
            ui_api_module, "get_workflow_ui_read_service",
            lambda: missing_env.service,
        )
        missing = client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}{suffix}"
        )

        assert cross.status_code == 404
        assert cross.json() == missing.json()      # 同码**同文案**
        env.assert_no_writes()

    def test_run_not_found_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install_fake_session_service(monkeypatch)
        install_authorization_grants(
            monkeypatch,
            project_roles={(_alice(), _PROJECT): {"MEMBER"}},
        )
        env = _Env(run=None)
        client = _client(monkeypatch, env)

        response = client.get(f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Workflow Run 不存在"

    def test_cross_project_approval_isomorphic_404(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_fake_session_service(monkeypatch)
        install_authorization_grants(
            monkeypatch,
            project_roles={(_alice(), _PROJECT): {"MEMBER"}},
        )
        env = _Env(
            run=_run_record(project_id=_OTHER_PROJECT),
            by_node={(_RUN_ID, "gate"): _approval_record(node_id="gate")},
        )
        client = _client(monkeypatch, env)

        cross = client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}/approvals/gate"
        )
        missing = client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/wf-missing/approvals/gate"
        )

        assert cross.status_code == 404
        assert cross.json() == missing.json()

    def test_error_body_does_not_leak_other_project(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """错误响应**不**含项目 / 用户 / 内部标识。"""
        install_fake_session_service(monkeypatch)
        install_authorization_grants(monkeypatch)
        env = _full_env()
        client = _client(monkeypatch, env)

        body = client.get(f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}").text

        assert _OTHER_PROJECT not in body
        assert _PROJECT not in body
        assert _alice() not in body
        assert "authorization_grant" not in body


# ============================================================
# 6 / 7：子记录归属
# ============================================================


class TestNodeAndApprovalOwnership:
    def test_node_attempts_must_belong_to_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """跨 Run 的 node 记录 ⇒ fail closed 502（不返回跨 Run 数据）。"""
        install_fake_session_service(monkeypatch)
        install_authorization_grants(
            monkeypatch,
            project_roles={(_alice(), _PROJECT): {"MEMBER"}},
        )
        env = _Env(
            run=_run_record(),
            nodes=[_node_record(workflow_run_id="wf-run-OTHER")],
        )
        client = _client(monkeypatch, env)

        response = client.get(f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}/nodes")

        assert response.status_code == 502
        assert response.json()["detail"] == "Workflow 读取数据源不可用"
        env.assert_no_writes()

    def test_approval_must_belong_to_run_and_node(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_fake_session_service(monkeypatch)
        install_authorization_grants(
            monkeypatch,
            project_roles={(_alice(), _PROJECT): {"MEMBER"}},
        )
        env = _Env(
            run=_run_record(),
            nodes=[_node_record(node_id="gate", node_type="APPROVAL")],
            by_node={
                (_RUN_ID, "gate"): _approval_record(node_id="other-node")
            },
        )
        client = _client(monkeypatch, env)

        response = client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}/approvals/gate"
        )

        assert response.status_code == 502

    def test_missing_approval_row_is_404(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """该节点尚无审批行 ⇒ 404（**不**显示为已通过）。"""
        install_fake_session_service(monkeypatch)
        install_authorization_grants(
            monkeypatch,
            project_roles={(_alice(), _PROJECT): {"MEMBER"}},
        )
        env = _Env(run=_run_record(), by_node={})
        client = _client(monkeypatch, env)

        response = client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}/approvals/gate"
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Workflow 审批不存在"

    def test_corrupted_node_output_fails_closed_502(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """损坏的持久化输出 ⇒ 502（不截断 / 不修复 / 不以 200 返回）。"""
        install_fake_session_service(monkeypatch)
        install_authorization_grants(
            monkeypatch,
            project_roles={(_alice(), _PROJECT): {"MEMBER"}},
        )
        env = _Env(run=_run_record(), nodes=[_node_record(output="{not json")])
        client = _client(monkeypatch, env)

        response = client.get(f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}/nodes")

        assert response.status_code == 502
        assert "{not json" not in response.text

    def test_store_failure_502_without_body_leak(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_fake_session_service(monkeypatch)
        install_authorization_grants(
            monkeypatch,
            project_roles={(_alice(), _PROJECT): {"MEMBER"}},
        )
        env = _Env(run=_run_record())
        env.run_store.error = WorkflowPortsError(_SECRET)
        client = _client(monkeypatch, env)

        response = client.get(f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}")

        assert response.status_code == 502
        assert _SECRET not in response.text


# ============================================================
# 11：只读边界
# ============================================================


class TestReadOnlyBoundary:
    def test_only_get_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install_fake_session_service(monkeypatch)
        env = _member_env(monkeypatch)
        client = _client(monkeypatch, env)
        path = f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}"

        for method in ("post", "put", "patch", "delete"):
            response = getattr(client, method)(path)
            assert response.status_code in {404, 405}, method

        env.assert_no_writes()

    def test_all_queries_zero_writes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_fake_session_service(monkeypatch)
        env = _member_env(monkeypatch)
        client = _client(monkeypatch, env)

        for path in (
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}",
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}/nodes",
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}/timeline",
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}/approvals/gate",
            f"{_UI}/{_PROJECT}/workflow-approvals/{_APPROVAL_ID}",
        ):
            assert client.get(path).status_code in {200, 404}, path

        env.assert_no_writes()

    def test_module_has_no_execution_entrypoints(self) -> None:
        for forbidden in (
            "start",
            "advance",
            "request_cancel",
            "submit_approval_decision",
        ):
            assert not hasattr(ui_api_module, forbidden)

    def test_no_llm_tool_or_runtime_imports(self) -> None:
        """AST 边界：UI 读面不得 import Runtime 执行 / Tool / LLM / ORM。"""
        source = Path(ui_api_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)

        for forbidden in (
            "backend.app.db",
            "backend.app.services",
            "backend.app.tools",
            "backend.app.agent",
            "backend.app.llm",
            "backend.app.projects",
            "backend.app.workflow_runtime.runtime",
            "backend.app.workflow_runtime.execution",
            "backend.app.workflow_runtime.store_adapters",
        ):
            assert not any(
                module == forbidden or module.startswith(forbidden + ".")
                for module in modules
            ), f"workflow_ui_read.py 禁入 import: {forbidden}"

        # 必须**只**经 Read Service 读取
        assert any(
            module == "backend.app.workflow_runtime.read_service"
            for module in modules
        )
        assert any(
            module == "backend.app.workflow_runtime.read_factory"
            for module in modules
        )

    def test_blank_path_params_400(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_fake_session_service(monkeypatch)
        env = _member_env(monkeypatch)
        client = _client(monkeypatch, env)

        assert client.get(
            f"{_UI}/%20/workflow-runs/{_RUN_ID}"
        ).status_code == 400
        assert client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/%20"
        ).status_code == 400
        assert client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}/approvals/%20"
        ).status_code == 400
        assert client.get(
            f"{_UI}/{_PROJECT}/workflow-approvals/%20"
        ).status_code == 400

    def test_over_length_path_params_422(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_fake_session_service(monkeypatch)
        env = _member_env(monkeypatch)
        client = _client(monkeypatch, env)

        assert client.get(
            f"{_UI}/{'x' * (ui_api_module.PROJECT_ID_MAX_LENGTH + 1)}"
            f"/workflow-runs/{_RUN_ID}"
        ).status_code == 422
        assert client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{'x' * 65}"
        ).status_code == 422


# ============================================================
# 12：响应契约
# ============================================================


class TestResponseContract:
    def test_run_detail_exact_fields_no_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_fake_session_service(monkeypatch)
        env = _member_env(monkeypatch)
        client = _client(monkeypatch, env)

        payload = client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}"
        ).json()

        assert set(payload.keys()) == set(WORKFLOW_RUN_DETAIL_RESPONSE_FIELDS)
        assert "input" not in payload
        assert "request_id" not in payload
        assert "DO-NOT-EXPOSE" not in str(payload)

    def test_nodes_full_history_exact_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_fake_session_service(monkeypatch)
        env = _member_env(monkeypatch)
        client = _client(monkeypatch, env)

        items = client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}/nodes"
        ).json()

        assert [item["attempt"] for item in items] == [1, 2, 1]  # 全量历史
        for item in items:
            assert set(item.keys()) == set(WORKFLOW_NODE_RUN_RESPONSE_FIELDS)

    def test_timeline_fields_and_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """时间线由**真实持久化时间戳**派生（RUN_CREATED / RUN_STARTED /
        每个 attempt 的 started_at），字段集与服务间读面一致。"""
        install_fake_session_service(monkeypatch)
        env = _member_env(monkeypatch)
        client = _client(monkeypatch, env)

        items = client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}/timeline"
        ).json()

        assert items, "应至少包含 RUN_CREATED"
        assert items[0]["kind"] == "RUN_CREATED"
        kinds = [item["kind"] for item in items]
        assert "RUN_STARTED" in kinds
        assert kinds.count("NODE_ATTEMPT_STARTED") == 3   # 3 条 attempt
        for item in items:
            assert set(item.keys()) == set(
                WORKFLOW_TIMELINE_EVENT_RESPONSE_FIELDS
            )
        # 后端已排序 ⇒ 前端不重排：时间戳非降序
        stamps = [item["timestamp"] for item in items]
        assert stamps == sorted(stamps)

    def test_approval_exact_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_fake_session_service(monkeypatch)
        env = _member_env(monkeypatch)
        client = _client(monkeypatch, env)

        payload = client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}/approvals/gate"
        ).json()

        assert set(payload.keys()) == set(WORKFLOW_APPROVAL_RESPONSE_FIELDS)
        assert payload["status"] == "PENDING"
        assert payload["resolved_at"] is None
        assert "created_at" not in payload       # Port 未暴露 ⇒ 不补造

    def test_no_orm_session_connection_in_response(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_fake_session_service(monkeypatch)
        env = _member_env(monkeypatch)
        client = _client(monkeypatch, env)

        payload = client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}"
        ).json()

        assert not any(
            key in payload
            for key in ("session", "connection", "metadata", "_sa_instance_state")
        )
        for value in payload.values():
            assert not hasattr(value, "__table__")
            assert not hasattr(value, "execute")

    def test_no_default_error_detail_leaks_internals(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_fake_session_service(monkeypatch)
        install_authorization_grants(monkeypatch)
        env = _full_env()
        client = _client(monkeypatch, env)

        for text in (
            client.get(f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}").text,
            client.get(f"{_UI}/%20/workflow-runs/{_RUN_ID}").text,
        ):
            for forbidden in (
                "Traceback", "postgresql", "psycopg", "SELECT", "/api/",
                "user_id", _alice(),
            ):
                assert forbidden not in text, forbidden

    def test_dto_field_constants_match_service_surface(self) -> None:
        """前后端契约：字段常量与 `workflow_read` 共用同一份定义。"""
        assert ui_api_module.to_api_run_detail is (
            service_api_module.to_api_run_detail
        )
        assert ui_api_module.to_api_node_run is service_api_module.to_api_node_run
        assert ui_api_module.to_api_approval is (
            service_api_module.to_api_approval
        )
        assert ui_api_module.to_api_event is service_api_module.to_api_event

    def test_error_messages_match_service_surface(self) -> None:
        """固定文案与服务间读面**逐字一致**（防两套契约漂移）。"""
        assert ui_api_module.RUN_NOT_FOUND_DETAIL == (
            service_api_module._RUN_NOT_FOUND_DETAIL
        )
        assert ui_api_module.APPROVAL_NOT_FOUND_DETAIL == (
            service_api_module._APPROVAL_NOT_FOUND_DETAIL
        )
        assert ui_api_module.UNAVAILABLE_DETAIL == (
            service_api_module._UNAVAILABLE_DETAIL
        )

    def test_path_length_constants_match_db_models(self) -> None:
        assert (
            ui_api_module.PROJECT_ID_MAX_LENGTH
            == WORKFLOW_RUN_PROJECT_ID_MAX_LENGTH
        )
        assert service_api_module.WORKFLOW_RUN_ID_MAX_LENGTH == (
            WORKFLOW_RUN_ID_MAX_LENGTH
        )
        assert service_api_module.WORKFLOW_NODE_ID_MAX_LENGTH == (
            WORKFLOW_NODE_RUN_NODE_ID_MAX_LENGTH
        )
        assert service_api_module.WORKFLOW_APPROVAL_ID_MAX_LENGTH == (
            WORKFLOW_APPROVAL_ID_MAX_LENGTH
        )


# ============================================================
# 10：既有服务间读面未被破坏
# ============================================================


class TestServiceApiUnchanged:
    """既有服务间读面（X-API-Key）**不**受本阶段影响。"""

    @staticmethod
    def _configure_service_auth(
        monkeypatch: pytest.MonkeyPatch,
        *,
        api_key: str = "test-service-key",
        project_id: str = _PROJECT,
    ) -> None:
        """装配服务间读面自身的凭据配置（否则其 fail-closed 503 会掩盖 401）。"""
        from backend.app.api import workflow_read_auth as auth_module
        from backend.app.config import WorkflowReadSettings

        monkeypatch.setattr(
            auth_module,
            "get_workflow_read_settings",
            lambda: WorkflowReadSettings(
                api_key=api_key, api_project_id=project_id
            ),
        )

    def test_service_routes_still_require_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """服务间读面仍只认 X-API-Key：无凭据 ⇒ 401（**不**接受会话）。"""
        install_fake_session_service(monkeypatch)
        self._configure_service_auth(monkeypatch)
        client = SessionTestClient(app)   # 有会话 Cookie，但无 X-API-Key

        response = client.get(f"/api/workflow-runs/{_RUN_ID}")

        assert response.status_code == 401
        assert response.json()["detail"] == "认证失败：缺少或无效的 API Key"

    def test_service_routes_do_not_accept_session_with_membership(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**关键**：会话 + 项目成员授权**不能**替代服务间凭据。"""
        install_fake_session_service(monkeypatch)
        self._configure_service_auth(monkeypatch)
        install_authorization_grants(
            monkeypatch,
            project_roles={(_alice(), _PROJECT): {"MEMBER"}},
        )
        client = SessionTestClient(app)

        for path in (
            f"/api/workflow-runs/{_RUN_ID}",
            f"/api/workflow-runs/{_RUN_ID}/nodes",
            f"/api/workflow-runs/{_RUN_ID}/timeline",
            f"/api/workflow-runs/{_RUN_ID}/approvals/gate",
            f"/api/workflow-approvals/{_APPROVAL_ID}",
        ):
            assert client.get(path).status_code == 401, path

    def test_service_routes_still_fail_closed_when_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """服务间读面凭据为空 ⇒ 仍 503 fail closed（既有语义未变）。"""
        install_fake_session_service(monkeypatch)
        self._configure_service_auth(monkeypatch, api_key="", project_id="")
        client = SessionTestClient(app)

        response = client.get(f"/api/workflow-runs/{_RUN_ID}")

        assert response.status_code == 503

    def test_ui_routes_do_not_accept_api_key_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """UI 读面不接受 API Key 替代会话（匿名 + 伪 Key ⇒ 401）。"""
        install_fake_session_service(monkeypatch)
        _member_env(monkeypatch)

        with TestClient(app) as anonymous:
            response = anonymous.get(
                f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}",
                headers={"X-API-Key": "whatever"},
            )

        assert response.status_code == 401

    def test_service_route_set_unchanged(self) -> None:
        """5 个服务间只读路由仍在（新增 UI 面**不**影响既有路由）。"""
        paths = app.openapi()["paths"]
        for route in (
            "/api/workflow-runs/{run_id}",
            "/api/workflow-runs/{run_id}/nodes",
            "/api/workflow-runs/{run_id}/timeline",
            "/api/workflow-runs/{run_id}/approvals/{node_id}",
            "/api/workflow-approvals/{approval_id}",
        ):
            assert route in paths
            assert set(paths[route].keys()) == {"get"}


# ============================================================
# 9：路由表面（全部受认证 + 授权保护；仅 GET）
# ============================================================


class TestRouteSurface:
    UI_ROUTES = (
        "/api/ui/projects/{project_id}/workflow-runs/{run_id}",
        "/api/ui/projects/{project_id}/workflow-runs/{run_id}/nodes",
        "/api/ui/projects/{project_id}/workflow-runs/{run_id}/timeline",
        "/api/ui/projects/{project_id}/workflow-runs/{run_id}/approvals/{node_id}",
        "/api/ui/projects/{project_id}/workflow-approvals/{approval_id}",
    )

    def test_ui_routes_are_get_only(self) -> None:
        paths = app.openapi()["paths"]
        for route in self.UI_ROUTES:
            assert route in paths, f"缺少 UI 路由: {route}"
            assert set(paths[route].keys()) == {"get"}, route

    def test_every_ui_route_mounts_session_security(self) -> None:
        """**每个** UI 端点都必须真实依赖会话校验（**行为性**断言）。

        通过 FastAPI 的依赖图（``route.dependant``）递归检查，
        而非源码字符串计数 —— 直接证明"该端点运行时确实会执行
        ``require_session``"（比计数更强）。

        使用项目约定的公开依赖别名 ``CurrentPrincipal``
        （``= Annotated[Principal, Security(require_session)]``，与
        ``auth.py`` / ``chat.py`` 一致）——**不**在本模块重复 ``Security(...)``
        包装，也**不** import ``backend.app.services``。
        """
        from backend.app.api.auth_dependency import require_session

        def collect(dependant: Any) -> set[Any]:
            found = {dependant.call}
            for child in dependant.dependencies:
                found |= collect(child)
            return found

        get_routes = [
            route
            for route in ui_api_module.router.routes
            if "GET" in getattr(route, "methods", set())
        ]
        # Step 4 新增 graph 端点 ⇒ 6 个
        assert len(get_routes) == 6

        for route in get_routes:
            callables = collect(route.dependant)
            assert require_session in callables, (
                f"{route.path} 未挂载 require_session 依赖"
            )

    def test_module_does_not_import_services_layer(self) -> None:
        """API 层不得 import ``backend.app.services``（用 ``CurrentPrincipal``）。"""
        source = Path(ui_api_module.__file__).read_text(encoding="utf-8")
        assert "from backend.app.services" not in source
        assert "import backend.app.services" not in source

    def test_every_ui_route_enforces_project_membership(self) -> None:
        """**每个** UI 端点都必须执行项目成员授权（源级计数 == 端点数）。"""
        source = Path(ui_api_module.__file__).read_text(encoding="utf-8")
        assert source.count("_require_project_membership(principal, project_id)") == 6

    def test_no_api_key_security_scheme_declared(self) -> None:
        """UI 面不得声明任何 API Key security scheme。"""
        schema = app.openapi()
        for route in self.UI_ROUTES:
            operation = schema["paths"][route]["get"]
            assert not operation.get("security"), (
                f"{route} 不应声明 API Key security（会话依赖即可）"
            )

    def test_module_declares_no_write_route(self) -> None:
        source = Path(ui_api_module.__file__).read_text(encoding="utf-8")
        for method in ("post", "put", "patch", "delete"):
            assert f"@router.{method}(" not in source, method

    def test_openapi_declares_expected_error_statuses(self) -> None:
        schema = app.openapi()
        for route in self.UI_ROUTES:
            responses = schema["paths"][route]["get"]["responses"]
            assert {"200", "401", "403", "404", "503"} <= set(responses), route
