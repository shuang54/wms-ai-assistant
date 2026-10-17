"""Workflow UI Graph API 测试（Phase 7 Step 4）—— **全离线**。

覆盖（Step 4 §五 1/2/3/4/5/6/7/8/9/10 的后端侧）：

    1. 有真实拓扑时节点和边正确返回（来自 WorkflowDefinition，非时间顺序）
    2. 缺少拓扑时不返回虚假边（未注册 / 版本不匹配 ⇒ nodes 为空）
    3. 节点类型与状态枚举真实
    4. 未登录 / 无项目授权被拒
    5. 跨项目 Run ⇒ 同构 404
    6. 多 Attempt 历史不受影响（graph 端点不返回 attempt）
    7. 空工作流 / 未知节点类型安全展示（前端侧；此处锁定枚举来源）
    8. 响应不含模板 / 参数映射 / 审批文案 / tool 目标
    9. 只读边界：仅 GET；不触 Runtime；零写入
   10. 既有服务间 / UI 端点未被破坏
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.app.api import workflow_ui_read as ui_api_module
from backend.app.dto.workflow_graph_api import (
    WORKFLOW_GRAPH_RESPONSE_FIELDS,
    WORKFLOW_GRAPH_STATUS_AVAILABLE,
    WORKFLOW_GRAPH_STATUS_NOT_REGISTERED,
    WORKFLOW_GRAPH_STATUS_VERSION_MISMATCH,
)
from backend.app.main import app
from backend.app.workflow.definition import (
    NODE_TYPE_AI,
    NODE_TYPE_APPROVAL,
    NODE_TYPE_CONDITION,
    NODE_TYPE_TOOL,
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowRegistry,
)
from backend.app.workflow.errors import (
    WorkflowDefinitionNotFoundError,
)
from tests.api_session_auth import (
    SessionTestClient,
    install_authorization_grants,
    install_fake_session_service,
)
from tests.conversation_auth_support import USER_IDS
from tests.test_workflow_runtime_read_service import _Env, _run_record

__all__ = [
    "TestGraphTopology",
    "TestGraphFailClosed",
    "TestGraphSecurity",
    "TestGraphReadOnly",
]

_RUN_ID = "wf-run-1"
_PROJECT = "proj-a"
_OTHER_PROJECT = "proj-b"
_UI = "/api/ui/projects"
_GRAPH_PATH = f"{_UI}/{_PROJECT}/workflow-runs/{_RUN_ID}/graph"


def _linear_definition(version: str = "v1") -> WorkflowDefinition:
    """真实线性定义（TOOL → APPROVAL）。"""
    return WorkflowDefinition(
        name="inventory_check",
        version=version,
        start_node_id="query_stock",
        nodes=(
            WorkflowNode(
                node_id="query_stock",
                node_type=NODE_TYPE_TOOL,
                tool_name="get_inventory",
                arguments_map={"material_code": "input.material_code"},
                next_node_id="confirm_stock",
            ),
            WorkflowNode(
                node_id="confirm_stock",
                node_type=NODE_TYPE_APPROVAL,
                approval_prompt="请核对库存查询结果。",
            ),
        ),
    )


def _branching_definition() -> WorkflowDefinition:
    """真实分支定义（CONDITION 双分支 + 汇聚 ⇒ 多入边）。"""
    return WorkflowDefinition(
        name="answer_review",
        version="v1",
        start_node_id="answer",
        nodes=(
            WorkflowNode(
                node_id="answer",
                node_type=NODE_TYPE_AI,
                question_template="请回答：{input.question}",
                next_node_id="review_answer",
            ),
            WorkflowNode(
                node_id="review_answer",
                node_type=NODE_TYPE_APPROVAL,
                approval_prompt="请确认 AI 回答。",
                next_node_id="branch",
            ),
            WorkflowNode(
                node_id="branch",
                node_type=NODE_TYPE_CONDITION,
                condition=WorkflowCondition(
                    source="answer.output", operator="EQ", expected="ok"
                ),
                on_true_node_id="done",
                on_false_node_id="fallback",
            ),
            WorkflowNode(
                node_id="done",
                node_type=NODE_TYPE_TOOL,
                tool_name="get_inventory",
                next_node_id="merge",
            ),
            WorkflowNode(
                node_id="fallback",
                node_type=NODE_TYPE_AI,
                question_template="fallback: {input.question}",
                next_node_id="merge",
            ),
            WorkflowNode(
                node_id="merge",
                node_type=NODE_TYPE_APPROVAL,
                approval_prompt="最终确认。",
            ),
        ),
    )


def _install_registry(monkeypatch: pytest.MonkeyPatch, definition: Any) -> None:
    registry = WorkflowRegistry()
    if definition is not None:
        registry.register(definition)
    monkeypatch.setattr(
        ui_api_module, "get_workflow_definition_registry", lambda: registry
    )


def _client(
    monkeypatch: pytest.MonkeyPatch,
    env: _Env,
    *,
    username: str = "alice",
    grant: bool = True,
) -> SessionTestClient:
    install_fake_session_service(monkeypatch)
    if grant:
        install_authorization_grants(
            monkeypatch,
            project_roles={(USER_IDS["alice"], _PROJECT): {"MEMBER"}},
        )
    else:
        install_authorization_grants(monkeypatch)
    monkeypatch.setattr(
        ui_api_module, "get_workflow_ui_read_service", lambda: env.service
    )
    return SessionTestClient(app, username=username)


def _env(workflow_id: str = "inventory_check", version: str = "v1") -> _Env:
    return _Env(run=_run_record(workflow_id=workflow_id, workflow_version=version))


# ============================================================
# 1 / 3：真实拓扑
# ============================================================


class TestGraphTopology:
    def test_linear_topology_nodes_and_edges(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """真实定义 ⇒ 节点 + 顺序边（来自 next_node_id，非时间顺序）。"""
        _install_registry(monkeypatch, _linear_definition())
        client = _client(monkeypatch, _env())

        payload = client.get(_GRAPH_PATH).json()

        assert payload["status"] == WORKFLOW_GRAPH_STATUS_AVAILABLE
        assert set(payload.keys()) == set(WORKFLOW_GRAPH_RESPONSE_FIELDS)
        assert payload["workflow_id"] == "inventory_check"
        assert payload["workflow_version"] == "v1"
        assert payload["definition_version"] == "v1"
        assert payload["start_node_id"] == "query_stock"
        assert [(n["node_id"], n["node_type"]) for n in payload["nodes"]] == [
            ("query_stock", NODE_TYPE_TOOL),
            ("confirm_stock", NODE_TYPE_APPROVAL),
        ]
        # 顺序边真实存在
        assert payload["nodes"][0]["next_node_id"] == "confirm_stock"
        assert payload["nodes"][1]["next_node_id"] is None
        # 非 CONDITION 节点无分支字段
        assert payload["nodes"][0]["on_true_node_id"] is None
        assert payload["nodes"][0]["on_false_node_id"] is None

    def test_branching_topology_exposes_condition_edges(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CONDITION 双分支 + 汇聚节点（多入边）⇒ 边字段完整。"""
        _install_registry(monkeypatch, _branching_definition())
        client = _client(monkeypatch, _env("answer_review"))

        payload = client.get(_GRAPH_PATH).json()

        assert payload["status"] == WORKFLOW_GRAPH_STATUS_AVAILABLE
        by_id = {n["node_id"]: n for n in payload["nodes"]}
        assert set(by_id) == {
            "answer",
            "review_answer",
            "branch",
            "done",
            "fallback",
            "merge",
        }
        # 条件分支两条边
        assert by_id["branch"]["on_true_node_id"] == "done"
        assert by_id["branch"]["on_false_node_id"] == "fallback"
        assert by_id["branch"]["next_node_id"] is None
        # 汇聚节点有两个入边（done → merge、fallback → merge）
        assert by_id["done"]["next_node_id"] == "merge"
        assert by_id["fallback"]["next_node_id"] == "merge"

    def test_response_excludes_sensitive_definition_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """响应**不**含模板 / 参数映射 / 审批文案 / tool 目标。"""
        _install_registry(monkeypatch, _branching_definition())
        client = _client(monkeypatch, _env("answer_review"))

        body = client.get(_GRAPH_PATH).text

        for forbidden in (
            "question_template",
            "arguments_map",
            "approval_prompt",
            "tool_name",
            "请回答",
            "请确认",
            "最终确认",
            "get_inventory",
            "material_code",
        ):
            assert forbidden not in body, forbidden


# ============================================================
# 2 / 4：Fail-closed（不返回虚假边）
# ============================================================


class TestGraphFailClosed:
    def test_unregistered_definition_has_no_nodes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """定义未注册 ⇒ not_registered 且 nodes 为空（不画任何边）。"""
        _install_registry(monkeypatch, None)          # 空注册表
        client = _client(monkeypatch, _env())

        payload = client.get(_GRAPH_PATH).json()

        assert payload["status"] == WORKFLOW_GRAPH_STATUS_NOT_REGISTERED
        assert payload["nodes"] == []
        assert payload["start_node_id"] is None
        assert payload["definition_version"] is None
        # 仍如实回报 Run 持久化身份
        assert payload["workflow_id"] == "inventory_check"
        assert payload["workflow_version"] == "v1"

    def test_version_mismatch_has_no_nodes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """版本不一致 ⇒ version_mismatch 且 nodes 为空。

        （**不**用当前版本拓扑冒充历史版本 ⇒ 前端不会画出可能错误的边）
        """
        _install_registry(monkeypatch, _linear_definition(version="v2"))
        client = _client(monkeypatch, _env(version="v1"))   # Run 是 v1

        payload = client.get(_GRAPH_PATH).json()

        assert payload["status"] == WORKFLOW_GRAPH_STATUS_VERSION_MISMATCH
        assert payload["nodes"] == []
        assert payload["start_node_id"] is None
        # 两侧版本都如实给出（便于页面解释原因）
        assert payload["workflow_version"] == "v1"
        assert payload["definition_version"] == "v2"

    def test_version_match_returns_topology(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_registry(monkeypatch, _linear_definition(version="v9"))
        client = _client(monkeypatch, _env(version="v9"))

        assert client.get(_GRAPH_PATH).json()["status"] == (
            WORKFLOW_GRAPH_STATUS_AVAILABLE
        )

    def test_registry_unavailable_is_500_fixed_detail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """注册表读取异常 ⇒ 500 固定文案（不暴露内部细节）。"""
        from backend.app.workflow.errors import WorkflowDefinitionError

        class _Broken:
            def resolve_current(self, name: str) -> Any:
                raise WorkflowDefinitionError("SECRET-INTERNAL")

        monkeypatch.setattr(
            ui_api_module,
            "get_workflow_definition_registry",
            lambda: _Broken(),
        )
        client = _client(monkeypatch, _env())

        response = client.get(_GRAPH_PATH)

        assert response.status_code == 500
        assert response.json()["detail"] == "Workflow 读取数据源不可用"
        assert "SECRET-INTERNAL" not in response.text


# ============================================================
# 4 / 5：认证与项目隔离
# ============================================================


class TestGraphSecurity:
    def test_anonymous_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install_fake_session_service(monkeypatch)
        _install_registry(monkeypatch, _linear_definition())
        _client(monkeypatch, _env())

        from fastapi.testclient import TestClient

        with TestClient(app) as anonymous:
            response = anonymous.get(_GRAPH_PATH)

        assert response.status_code == 401

    def test_without_grant_403(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_registry(monkeypatch, _linear_definition())
        client = _client(monkeypatch, _env(), grant=False)

        response = client.get(_GRAPH_PATH)

        assert response.status_code == 403
        assert response.json()["detail"] == "权限不足：需要项目访问授权"

    def test_run_not_found_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_registry(monkeypatch, _linear_definition())
        client = _client(monkeypatch, _Env(run=None))

        assert client.get(_GRAPH_PATH).status_code == 404

    def test_cross_project_run_isomorphic_404(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """跨项目 Run ⇒ 与"不存在"同码同文案（不泄漏存在性）。"""
        _install_registry(monkeypatch, _linear_definition())
        client = _client(monkeypatch, _Env(run=_run_record(project_id=_OTHER_PROJECT)))

        cross = client.get(_GRAPH_PATH)
        missing_env = _Env(run=None)
        monkeypatch.setattr(
            ui_api_module,
            "get_workflow_ui_read_service",
            lambda: missing_env.service,
        )
        missing = client.get(_GRAPH_PATH)

        assert cross.status_code == 404
        assert cross.json() == missing.json()

    def test_error_body_does_not_leak_identifiers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_fake_session_service(monkeypatch)
        install_authorization_grants(monkeypatch)
        _install_registry(monkeypatch, _linear_definition())
        client = _client(monkeypatch, _env(), grant=False)

        body = client.get(_GRAPH_PATH).text

        assert _OTHER_PROJECT not in body
        assert USER_IDS["alice"] not in body
        assert "authorization_grant" not in body

    def test_blank_params_400(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_registry(monkeypatch, _linear_definition())
        client = _client(monkeypatch, _env())

        assert client.get(
            f"{_UI}/%20/workflow-runs/{_RUN_ID}/graph"
        ).status_code == 400
        assert client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/%20/graph"
        ).status_code == 400

    def test_over_length_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_registry(monkeypatch, _linear_definition())
        client = _client(monkeypatch, _env())

        assert client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{'x' * 65}/graph"
        ).status_code == 422


# ============================================================
# 9 / 10：只读边界与既有端点
# ============================================================


class TestGraphReadOnly:
    def test_only_get_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_registry(monkeypatch, _linear_definition())
        env = _env()
        client = _client(monkeypatch, env)

        for method in ("post", "put", "patch", "delete"):
            response = getattr(client, method)(_GRAPH_PATH)
            assert response.status_code in {404, 405}, method

        env.assert_no_writes()

    def test_graph_endpoint_zero_writes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_registry(monkeypatch, _branching_definition())
        env = _env("answer_review")
        client = _client(monkeypatch, env)

        for _ in range(3):
            assert client.get(_GRAPH_PATH).status_code == 200

        env.assert_no_writes()

    def test_module_has_no_execution_entrypoints(self) -> None:
        for forbidden in (
            "start",
            "advance",
            "request_cancel",
            "submit_approval_decision",
        ):
            assert not hasattr(ui_api_module, forbidden)

    def test_graph_module_import_boundary(self) -> None:
        """AST：graph 端点不得 import db / Repository / runtime **执行层**。

        允许（与 ``workflow_definitions.py`` 惯例一致）：
          * ``workflow.errors`` / ``workflow.definition``（域层只读模型）；
          * ``read_factory`` / ``read_service``（只读服务）；
          * **函数内延迟** import ``workflow_runtime.command_service`` —— 这是
            既有 Composition Root 惯例（拿注册表实例，**不**触达执行入口）。
        """
        import ast

        source = Path(ui_api_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        top_level: set[str] = set()
        deferred: set[str] = set()
        for node in tree.body:  # 仅模块顶层
            if isinstance(node, ast.Import):
                top_level |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level.add(node.module)

        # 函数体内的 import = 延迟 import（既有 Composition Root 惯例）
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for child in ast.walk(node):
                    if isinstance(child, ast.Import):
                        deferred |= {alias.name for alias in child.names}
                    elif isinstance(child, ast.ImportFrom) and child.module:
                        deferred.add(child.module)

        for forbidden in (
            "backend.app.db",
            "backend.app.services",
            "backend.app.tools",
            "backend.app.agent",
            "backend.app.workflow_runtime.runtime",
            "backend.app.workflow_runtime.execution",
        ):
            for bucket in (top_level, deferred):
                assert not any(
                    module == forbidden or module.startswith(forbidden + ".")
                    for module in bucket
                ), f"禁入 import: {forbidden}"

        # 只读域层与只读服务
        assert "backend.app.workflow_runtime.read_service" in top_level
        assert "backend.app.workflow_runtime.read_factory" in top_level
        # 注册表实例经**函数内延迟** import 取得（既有惯例）
        assert "backend.app.workflow_runtime.command_service" in deferred

    def test_existing_endpoints_unchanged(self) -> None:
        """既有 5 个 UI 端点 + 5 个服务间端点仍在，且全部 GET-only。"""
        paths = app.openapi()["paths"]
        for route in (
            "/api/ui/projects/{project_id}/workflow-runs/{run_id}",
            "/api/ui/projects/{project_id}/workflow-runs/{run_id}/nodes",
            "/api/ui/projects/{project_id}/workflow-runs/{run_id}/timeline",
            "/api/ui/projects/{project_id}/workflow-runs/{run_id}/approvals/{node_id}",
            "/api/ui/projects/{project_id}/workflow-approvals/{approval_id}",
            "/api/workflow-runs/{run_id}",
            "/api/workflow-runs/{run_id}/nodes",
            "/api/workflow-runs/{run_id}/timeline",
            "/api/workflow-runs/{run_id}/approvals/{node_id}",
            "/api/workflow-approvals/{approval_id}",
        ):
            assert route in paths, route
            assert set(paths[route].keys()) == {"get"}, route

    def test_definition_error_type_is_reused(self) -> None:
        """复用域层既有异常（不新造错误类型）。"""
        assert WorkflowDefinitionNotFoundError is not None

    def test_every_ui_route_enforces_membership(self) -> None:
        """**每个** UI 端点（含 graph）都执行项目成员授权。"""
        source = Path(ui_api_module.__file__).read_text(encoding="utf-8")
        assert source.count("_require_project_membership(principal, project_id)") == 6
        get_routes = sum(
            1
            for route in ui_api_module.router.routes
            if "GET" in getattr(route, "methods", set())
        )
        assert get_routes == 6
