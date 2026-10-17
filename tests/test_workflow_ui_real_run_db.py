"""Phase 7 Step 5 — Workflow 运维工作台 **真实 Run 读取验收**（DB-gated）。

**专用测试库硬断言**（任何 SQL 之前 fail-fast）：

```text
localhost:5433/wms_ai_test
```

真链（**不** monkeypatch 读取服务、**不** monkeypatch 会话、**不** monkeypatch
授权——全部走生产实现）：

```text
POST /api/auth/login（真实 UserService + SessionService → ai_ops.app_user / auth_session）
    ↓ HttpOnly 会话 Cookie
GET /api/ui/projects/{project_id}/workflow-runs/{run_id}[/nodes|/timeline|/graph]
    ↓ require_session（M1 真实实现）
    ↓ enforce_project_access（M2.4 真实实现 → ai_ops.authorization_grant）
    ↓ WorkflowReadService（生产装配：read_factory）
    ↓ Store Adapters → Repositories → PostgreSQL
```

Run 的创建走**生产 Runtime**（`create_workflow_runtime` + 生产注册表
`get_workflow_registry()`），仅注入**确定性外部端口**（LLM / Tool 替身——
与项目既有 DB 测试同一模式），Run / Node / Approval 均经**真实 Store Adapter**
持久化到 PostgreSQL。

覆盖（Step 5 §2 / §3）：

```text
① Run Detail 读取真实持久化记录            ② Node Attempts 读取真实历史 Attempt
③ Timeline 只展示持久化事实（零伪造事件）   ④ Graph 版本匹配真实注册定义
⑤ 定义未注册 / 版本不匹配 ⇒ fail-closed（nodes 为空）
⑥ 未登录 401 · 非成员 403 · 跨项目 404 同构 · 会话过期 401
⑦ 查询前后 DB 状态不变                    ⑧ 清理后 residue 为零
```

定向前缀清理：``wf-p7s5-`` / ``p7s5-``（run / user / project 全部带前缀）。
"""
from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.db.authorization_grant_repository import (
    AuthorizationGrantRepository,
)
from backend.app.db.models.authorization_grant import (
    PROJECT_ROLE_MEMBER,
)
from backend.app.db.session import get_engine
from backend.app.db.workflow_approval_repository import (
    WorkflowApprovalRepository,
)
from backend.app.db.workflow_run_repository import WorkflowRunRepository
from backend.app.main import app
from backend.app.services.user_service import UserService
from backend.app.workflow.catalog import (
    WORKFLOW_NAME_INVENTORY_CHECK,
    WORKFLOW_VERSION_V1,
)
from backend.app.workflow_runtime.command_service import (
    get_workflow_registry,
)
from backend.app.workflow_runtime.factory import create_workflow_runtime
from backend.app.workflow_runtime.read_factory import (
    create_workflow_read_service,
)
from backend.app.workflow_runtime.command_service import WorkflowCommandService
from tests.test_workflow_definition_catalog_db import (
    _RecordingOrchestrator,
    _RecordingToolService,
)

_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

pytestmark = pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable workflow UI real-run acceptance tests",
)

__all__ = [
    "TestRealRunReadDb",
    "TestGraphDefinitionMatchDb",
    "TestSessionAndProjectAuthorizationDb",
    "TestNonMutationDb",
    "TestResidueDb",
]

#: 本文件的全部测试数据前缀（定向清理；绝不触碰其它数据）。
_RUN_PREFIX = "wf-p7s5-"
_USER_PREFIX = "p7s5-"
_PROJECT = "p7s5-proj-a"
_OTHER_PROJECT = "p7s5-proj-b"

_UI = "/api/ui/projects"
_PASSWORD = "P7s5-test-password-NOT-A-PRODUCTION-CREDENTIAL"

#: 真实定义（生产注册表中的 inventory_check v1）。
_DEFINITION_NODES = ("query_stock", "confirm_stock")


# ============================================================
# 专用测试库身份断言
# ============================================================


def _require_test_database() -> Any:
    """**硬断言**当前引擎指向专用测试库（任何 SQL 之前；失败即 fail）。"""
    engine = get_engine()
    if engine is None:
        pytest.fail(
            "DATABASE_URL 未配置：Step 5 真实 Run 验收必须显式指向 "
            "localhost:5433/wms_ai_test"
        )
    host, port, database = engine.url.host, engine.url.port, engine.url.database
    if not (
        host in {"localhost", "127.0.0.1"}
        and port == 5433
        and database == "wms_ai_test"
    ):
        pytest.fail(
            "拒绝执行：数据库隔离断言失败"
            f"（host={host} port={port} database={database}）——"
            "本套件只允许 localhost:5433/wms_ai_test"
        )
    return engine


# ============================================================
# 种子：真实用户 + 真实授权 + 真实 Runtime Run
# ============================================================


def _unique(tag: str) -> str:
    return f"{tag}{uuid.uuid4().hex[:10]}"


def _create_real_user(username: str) -> str:
    """经**真实** ``UserService`` 创建用户（与 ``create_user`` CLI 同一实现）。"""
    view = UserService().create_user(
        username=username, password=_PASSWORD, display_name="P7 Step5"
    )
    return view.user_id


def _grant_membership(user_id: str, project_id: str) -> None:
    """经**真实** ``AuthorizationGrantRepository`` 授予项目成员（与 CLI 同一实现）。"""
    AuthorizationGrantRepository().grant_project_role(
        user_id=user_id, project_id=project_id, role=PROJECT_ROLE_MEMBER
    )


def _build_command_service(project_id: str) -> tuple[WorkflowCommandService, Any, Any]:
    """生产 Runtime 装配（**仅**注入确定性 LLM / Tool 端口）。"""
    orchestrator = _RecordingOrchestrator()
    tool_service = _RecordingToolService()
    runtime = create_workflow_runtime(
        registry=get_workflow_registry(),
        project_id=project_id,
        orchestrator=orchestrator,
        tool_execution_service=tool_service,
        approval_id_factory=lambda: f"{_RUN_PREFIX}ap-{uuid.uuid4().hex[:8]}",
        request_id_factory=lambda: f"req-p7s5-{uuid.uuid4().hex[:8]}",
    )
    service = WorkflowCommandService(
        runtime_provider=lambda _pid: runtime,
        read_service_provider=create_workflow_read_service,
    )
    return service, orchestrator, tool_service


def _start_real_run(project_id: str = _PROJECT) -> tuple[str, str]:
    """用生产 Runtime 启动一个真实 Run（``inventory_check`` v1）。

    Returns:
        ``(run_id, first_node_id)``

    Note:
        ``start`` 会 inline 推进到等待点：TOOL 节点经真实 Store Adapter 持久化
        attempt，然后进入 ``confirm_stock`` 的 APPROVAL 等待态。
    """
    import asyncio

    service, _orchestrator, _tool = _build_command_service(project_id)
    result = asyncio.run(
        service.start_workflow(
            project_id=project_id,
            workflow_id=WORKFLOW_NAME_INVENTORY_CHECK,
            input='{"material_code": "MAT-P7S5-001"}',
            workflow_run_id=_unique(_RUN_PREFIX),
        )
    )
    run_id = result.outcome.workflow_run_id
    assert run_id, "Runtime 未返回 workflow_run_id"
    return run_id, _DEFINITION_NODES[0]


def _seed_run_row(
    *,
    project_id: str = _PROJECT,
    workflow_id: str = WORKFLOW_NAME_INVENTORY_CHECK,
    workflow_version: str = WORKFLOW_VERSION_V1,
) -> str:
    """直接经**真实 Repository** 播种 Run 行（用于定义不匹配场景）。"""
    run_id = _unique(_RUN_PREFIX)
    WorkflowRunRepository().create(
        workflow_run_id=run_id,
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        request_id=f"req-p7s5-{uuid.uuid4().hex[:8]}",
        project_id=project_id,
    )
    return run_id


# ============================================================
# 清理 / 残留
# ============================================================


def _cleanup(engine: Any) -> None:
    """定向清理：本套件前缀的 Run（FK 级联）与用户 / 授权。"""
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM ai_ops.workflow_run "
                "WHERE workflow_run_id LIKE :prefix"
            ),
            {"prefix": f"{_RUN_PREFIX}%"},
        )
        conn.execute(
            text(
                "DELETE FROM ai_ops.authorization_grant "
                "WHERE project_id LIKE :prefix"
            ),
            {"prefix": f"{_USER_PREFIX}%"},
        )
        # 会话与用户：先删会话（FK），再删用户
        conn.execute(
            text(
                "DELETE FROM ai_ops.user_session WHERE user_id IN "
                "(SELECT user_id FROM ai_ops.app_user "
                " WHERE username LIKE :prefix)"
            ),
            {"prefix": f"{_USER_PREFIX}%"},
        )
        conn.execute(
            text("DELETE FROM ai_ops.app_user WHERE username LIKE :prefix"),
            {"prefix": f"{_USER_PREFIX}%"},
        )


def _residue(engine: Any) -> dict[str, int]:
    with engine.connect() as conn:
        counts = {
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
        counts["authorization_grant"] = int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.authorization_grant "
                    "WHERE project_id LIKE :prefix"
                ),
                {"prefix": f"{_USER_PREFIX}%"},
            ).scalar_one()
        )
        counts["app_user"] = int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.app_user "
                    "WHERE username LIKE :prefix"
                ),
                {"prefix": f"{_USER_PREFIX}%"},
            ).scalar_one()
        )
        counts["user_session"] = int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.user_session s "
                    "JOIN ai_ops.app_user u ON u.user_id = s.user_id "
                    "WHERE u.username LIKE :prefix"
                ),
                {"prefix": f"{_USER_PREFIX}%"},
            ).scalar_one()
        )
    return counts


def _state_snapshot(engine: Any, run_id: str) -> tuple[Any, ...]:
    """Run / attempt / approval 关键字段快照（用于"查询不改状态"断言）。"""
    with engine.connect() as conn:
        run = conn.execute(
            text(
                "SELECT status, state_version, current_node_id, updated_at "
                "FROM ai_ops.workflow_run WHERE workflow_run_id = :rid"
            ),
            {"rid": run_id},
        ).one()
        nodes = conn.execute(
            text(
                "SELECT node_id, attempt, status, output, error_code "
                "FROM ai_ops.workflow_node_run WHERE workflow_run_id = :rid "
                "ORDER BY node_id, attempt"
            ),
            {"rid": run_id},
        ).all()
        approvals = conn.execute(
            text(
                "SELECT approval_id, node_id, status, resolved_at "
                "FROM ai_ops.workflow_approval WHERE workflow_run_id = :rid "
                "ORDER BY approval_id"
            ),
            {"rid": run_id},
        ).all()
    return (
        tuple(run),
        [tuple(row) for row in nodes],
        [tuple(row) for row in approvals],
    )


# ============================================================
# Fixture：真实用户 + 真实授权 + 真实 Run + 真实登录
# ============================================================


@pytest.fixture()
def real_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    """硬断言测试库 + 前后定向清理 + 真实用户/授权/Run/会话。"""
    engine = _require_test_database()
    _cleanup(engine)

    username = _unique(_USER_PREFIX)
    user_id = _create_real_user(username)
    _grant_membership(user_id, _PROJECT)
    run_id, first_node_id = _start_real_run()

    client = TestClient(app, base_url="https://testserver")
    login = client.post(
        "/api/auth/login", json={"username": username, "password": _PASSWORD}
    )
    assert login.status_code == 200, login.text

    try:
        yield {
            "engine": engine,
            "client": client,
            "username": username,
            "user_id": user_id,
            "run_id": run_id,
            "first_node_id": first_node_id,
            "project_id": _PROJECT,
        }
    finally:
        _cleanup(engine)
        _require_test_database()


# ============================================================
# ① / ② / ③：真实 Run / Attempt / Timeline
# ============================================================


class TestRealRunReadDb:
    def test_run_detail_reads_persisted_row(self, real_env: dict) -> None:
        """① Run Detail 来自真实持久化行（字段与 DB 一一对应）。"""
        run_id = real_env["run_id"]
        client: TestClient = real_env["client"]

        response = client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{run_id}"
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["workflow_run_id"] == run_id
        assert payload["project_id"] == _PROJECT
        assert payload["workflow_id"] == WORKFLOW_NAME_INVENTORY_CHECK
        assert payload["workflow_version"] == WORKFLOW_VERSION_V1
        assert payload["status"] == "WAITING"
        # 未提供 input / request_id（DTO 字段白名单）
        assert "input" not in payload
        assert "request_id" not in payload

    def test_node_attempts_match_persisted_rows(self, real_env: dict) -> None:
        """② Node Attempts 与 DB 行逐一对应（真实历史，不重建）。"""
        run_id = real_env["run_id"]
        engine = real_env["engine"]

        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT node_id, attempt, status, elapsed_ms "
                    "FROM ai_ops.workflow_node_run WHERE workflow_run_id = :rid "
                    "ORDER BY node_id, attempt"
                ),
                {"rid": run_id},
            ).all()

        response = real_env["client"].get(
            f"{_UI}/{_PROJECT}/workflow-runs/{run_id}/nodes"
        )
        assert response.status_code == 200
        items = response.json()

        assert len(items) == len(rows) >= 1

        # API 顺序 = **执行序**；DB 查询按 (node_id, attempt) 字母序
        # ⇒ 以 (node_id, attempt) 为键比对字段（顺序另有断言）
        api_by_key = {(i["node_id"], i["attempt"]): i for i in items}
        assert len(api_by_key) == len(items), "API 返回了重复的 (node_id, attempt)"
        assert len(api_by_key) == len(rows)

        for row in rows:
            item = api_by_key[(row[0], row[1])]
            assert item["status"] == row[2]
            if row[3] is None:
                assert item["elapsed_ms"] is None
            else:
                assert float(item["elapsed_ms"]) == float(row[3])

        # 真实执行事实：TOOL 节点已执行（有 attempt），APPROVAL 节点尚无 attempt
        assert ("query_stock", 1) in api_by_key
        assert api_by_key[("query_stock", 1)]["status"] in {
            "SUCCEEDED",
            "FAILED",
            "RUNNING",
        }
        assert api_by_key[("query_stock", 1)]["node_type"] == "TOOL"
        # APPROVAL 节点真实持久化为 WAITING_APPROVAL attempt（**不**是成功/失败）
        approval_attempt = api_by_key.get(("confirm_stock", 1))
        assert approval_attempt is not None, "APPROVAL 节点应有等待态 attempt"
        assert approval_attempt["node_type"] == "APPROVAL"
        assert approval_attempt["status"] == "WAITING_APPROVAL"
        # 等待态**不得**被读侧映射为成功 / 失败
        assert approval_attempt["status"] not in {"SUCCEEDED", "FAILED"}

    def test_timeline_only_persisted_facts(self, real_env: dict) -> None:
        """③ Timeline 只含持久化事件；无审批裁决 ⇒ 无 APPROVAL_RESOLVED。"""
        run_id = real_env["run_id"]

        response = real_env["client"].get(
            f"{_UI}/{_PROJECT}/workflow-runs/{run_id}/timeline"
        )
        assert response.status_code == 200
        items = response.json()

        kinds = [item["kind"] for item in items]
        assert kinds[0] == "RUN_CREATED"
        assert "RUN_STARTED" in kinds
        assert "NODE_ATTEMPT_STARTED" in kinds
        # 未裁决 ⇒ **不得**出现审批事件（不伪造）
        assert "APPROVAL_RESOLVED" not in kinds
        # 全部事件时间戳非空且来自持久化列
        for item in items:
            assert item["timestamp"]
            assert item["workflow_run_id"] == run_id
        # 后端稳定排序 ⇒ 时间戳非降序
        stamps = [item["timestamp"] for item in items]
        assert stamps == sorted(stamps)

    def test_approval_pending_and_not_resolved(self, real_env: dict) -> None:
        """审批行真实存在且 PENDING（不得显示为已通过）。"""
        run_id = real_env["run_id"]

        response = real_env["client"].get(
            f"{_UI}/{_PROJECT}/workflow-runs/{run_id}/approvals/confirm_stock"
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status"] == "PENDING"
        assert payload["resolved_at"] is None
        assert payload["node_id"] == "confirm_stock"
        assert "created_at" not in payload


# ============================================================
# ④ / ⑤：Graph 与定义版本匹配
# ============================================================


class TestGraphDefinitionMatchDb:
    def test_graph_matches_registered_definition(self, real_env: dict) -> None:
        """④ Graph 使用与 Run 的 workflow_id / version 匹配的真实注册定义。"""
        run_id = real_env["run_id"]

        response = real_env["client"].get(
            f"{_UI}/{_PROJECT}/workflow-runs/{run_id}/graph"
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status"] == "available"
        assert payload["workflow_id"] == WORKFLOW_NAME_INVENTORY_CHECK
        assert payload["definition_version"] == WORKFLOW_VERSION_V1
        assert payload["start_node_id"] == "query_stock"

        nodes = {node["node_id"]: node for node in payload["nodes"]}
        assert set(nodes) == set(_DEFINITION_NODES)
        assert nodes["query_stock"]["node_type"] == "TOOL"
        assert nodes["query_stock"]["next_node_id"] == "confirm_stock"
        assert nodes["confirm_stock"]["node_type"] == "APPROVAL"
        assert nodes["confirm_stock"]["next_node_id"] is None
        # 不暴露模板 / 参数映射 / 审批文案
        for node in payload["nodes"]:
            assert set(node) == {
                "node_id",
                "node_type",
                "next_node_id",
                "on_true_node_id",
                "on_false_node_id",
            }

    def test_version_mismatch_fails_closed(self, real_env: dict) -> None:
        """⑤ Run 版本与注册版本不一致 ⇒ version_mismatch 且 nodes 为空。"""
        run_id = _seed_run_row(workflow_version="v9")

        response = real_env["client"].get(
            f"{_UI}/{_PROJECT}/workflow-runs/{run_id}/graph"
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "version_mismatch"
        assert payload["nodes"] == []
        assert payload["start_node_id"] is None
        assert payload["workflow_version"] == "v9"
        assert payload["definition_version"] == WORKFLOW_VERSION_V1

    def test_unregistered_definition_fails_closed(self, real_env: dict) -> None:
        """⑤ 定义未注册 ⇒ not_registered 且 nodes 为空（不冒充其它定义）。"""
        run_id = _seed_run_row(workflow_id="ghost_workflow_not_in_catalog")

        response = real_env["client"].get(
            f"{_UI}/{_PROJECT}/workflow-runs/{run_id}/graph"
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "not_registered"
        assert payload["nodes"] == []
        assert payload["definition_version"] is None
        assert payload["start_node_id"] is None


# ============================================================
# ⑥：Session 与项目授权闭环
# ============================================================


class TestSessionAndProjectAuthorizationDb:
    def test_anonymous_rejected_401(self, real_env: dict) -> None:
        run_id = real_env["run_id"]
        with TestClient(app, base_url="https://testserver") as anonymous:
            response = anonymous.get(
                f"{_UI}/{_PROJECT}/workflow-runs/{run_id}"
            )
        assert response.status_code == 401
        assert response.json()["detail"] == "认证失败：缺少或无效的会话"

    def test_authorized_member_can_read(self, real_env: dict) -> None:
        """获授权项目成员可读（真实 authorization_grant 行）。"""
        response = real_env["client"].get(
            f"{_UI}/{_PROJECT}/workflow-runs/{real_env['run_id']}"
        )
        assert response.status_code == 200

    def test_user_without_grant_is_forbidden_403(self, real_env: dict) -> None:
        """同会话用户读**未授权项目** ⇒ 403（不泄漏该 Run 是否存在）。"""
        other_username = _unique(_USER_PREFIX)
        _create_real_user(other_username)   # 建用户但**不**授权

        other_client = TestClient(app, base_url="https://testserver")
        login = other_client.post(
            "/api/auth/login",
            json={"username": other_username, "password": _PASSWORD},
        )
        assert login.status_code == 200

        response = other_client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{real_env['run_id']}"
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "权限不足：需要项目访问授权"
        assert real_env["run_id"] not in response.text

    def test_cross_project_run_isomorphic_404(self, real_env: dict) -> None:
        """跨项目 Run ⇒ 404，且与"不存在"**同码同文案**（不泄漏存在性）。"""
        other_run = _start_real_run(project_id=_OTHER_PROJECT)[0]
        client: TestClient = real_env["client"]

        cross = client.get(f"{_UI}/{_PROJECT}/workflow-runs/{other_run}")
        missing = client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{_unique(_RUN_PREFIX)}"
        )

        assert cross.status_code == 404
        assert cross.json() == missing.json()
        assert other_run not in cross.text
        assert _OTHER_PROJECT not in cross.text

    def test_expired_session_rejected_401(self, real_env: dict) -> None:
        """会话失效后同一 Cookie ⇒ 401（页面据此提示重新登录）。"""
        engine = real_env["engine"]
        client: TestClient = real_env["client"]

        assert (
            client.get(
                f"{_UI}/{_PROJECT}/workflow-runs/{real_env['run_id']}"
            ).status_code
            == 200
        )

        # 经**真实**会话存储吊销该用户的全部会话（与 logout 同一存储语义）
        with engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM ai_ops.user_session WHERE user_id = :uid"
                ),
                {"uid": real_env["user_id"]},
            )

        response = client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{real_env['run_id']}"
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "认证失败：缺少或无效的会话"

    def test_service_api_key_surface_still_isolated(
        self, real_env: dict
    ) -> None:
        """会话 Cookie **不能**替代服务间 X-API-Key（两套机制并存）。"""
        response = real_env["client"].get(
            f"/api/workflow-runs/{real_env['run_id']}"
        )
        # 未配置服务间凭据 ⇒ 503 fail closed（或已配置时 401）
        assert response.status_code in {401, 503}


# ============================================================
# ⑦：查询不改状态
# ============================================================


class TestNonMutationDb:
    def test_all_read_endpoints_leave_state_unchanged(
        self, real_env: dict
    ) -> None:
        run_id = real_env["run_id"]
        engine = real_env["engine"]
        client: TestClient = real_env["client"]

        before = _state_snapshot(engine, run_id)

        for _ in range(2):
            assert (
                client.get(f"{_UI}/{_PROJECT}/workflow-runs/{run_id}").status_code
                == 200
            )
            assert (
                client.get(
                    f"{_UI}/{_PROJECT}/workflow-runs/{run_id}/nodes"
                ).status_code
                == 200
            )
            assert (
                client.get(
                    f"{_UI}/{_PROJECT}/workflow-runs/{run_id}/timeline"
                ).status_code
                == 200
            )
            assert (
                client.get(
                    f"{_UI}/{_PROJECT}/workflow-runs/{run_id}/graph"
                ).status_code
                == 200
            )
            assert (
                client.get(
                    f"{_UI}/{_PROJECT}/workflow-runs/{run_id}"
                    "/approvals/confirm_stock"
                ).status_code
                == 200
            )

        assert _state_snapshot(engine, run_id) == before

    def test_write_methods_rejected_without_state_change(
        self, real_env: dict
    ) -> None:
        run_id = real_env["run_id"]
        engine = real_env["engine"]
        client: TestClient = real_env["client"]

        before = _state_snapshot(engine, run_id)
        path = f"{_UI}/{_PROJECT}/workflow-runs/{run_id}"

        for method in ("post", "put", "patch", "delete"):
            response = getattr(client, method)(path)
            assert response.status_code in {404, 405}, method

        assert _state_snapshot(engine, run_id) == before


# ============================================================
# ⑧：residue
# ============================================================


class TestResidueDb:
    def test_residue_is_zero_after_cleanup(self) -> None:
        engine = _require_test_database()
        _cleanup(engine)
        assert _residue(engine) == {
            "workflow_run": 0,
            "workflow_node_run": 0,
            "workflow_approval": 0,
            "authorization_grant": 0,
            "app_user": 0,
            "user_session": 0,
        }
