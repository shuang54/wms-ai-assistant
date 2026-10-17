"""Workflow UI Read API **DB-gated** 集成测试（真 PostgreSQL）。

Phase 7 Workflow UI Step 2 §七.D。

**专用测试库硬断言**（运行前 fail-fast，**不**只依赖 ``RUN_DB_TESTS``）：

```text
localhost:5433/wms_ai_test
```

真链（**不** monkeypatch 读取服务，也**不** monkeypatch 授权服务——直接走
生产 Composition Root 与**真实** ``ai_ops.authorization_grant``）：

```text
TestClient（会话 Cookie）→ Router
  → require_session（**唯一**被替换的部分：会话存储）
  → enforce_project_access → AuthorizationService → AuthorizationGrantRepository
                            → ai_ops.authorization_grant（**真实表**）
  → get_workflow_ui_read_service() → read_factory
  → WorkflowReadService → Store Adapters → Repositories → PostgreSQL
```

覆盖（Step 2 §七.D 1–7）：

```text
① 已授权会话可读测试 Run          ② 未授权会话不可读（403）
③ 跨项目访问被拒绝（404 同构）     ④ 多 attempt 按正确顺序读取
⑤ Approval 归属验证正确            ⑥ 查询前后状态不变
⑦ 清理后 DB residue 为零
```

只写 ``ai_ops.workflow_*`` 三表 + ``ai_ops.authorization_grant``；
定向前缀清理（``wf-p7ui-`` / ``p7ui-``）。
"""
from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from backend.app.db.authorization_grant_repository import (
    AuthorizationGrantRepository,
)
from backend.app.db.models.authorization_grant import (
    PROJECT_ROLE_MEMBER,
    SCOPE_PROJECT,
)
from backend.app.db.session import get_engine
from backend.app.db.workflow_approval_repository import (
    WorkflowApprovalRepository,
)
from backend.app.db.workflow_node_run_repository import (
    WorkflowNodeRunRepository,
)
from backend.app.db.workflow_run_repository import WorkflowRunRepository
from backend.app.dto.workflow_read_api import (
    WORKFLOW_RUN_DETAIL_RESPONSE_FIELDS,
)
from backend.app.main import app
from tests.api_session_auth import (
    SessionTestClient,
    install_fake_session_service,
)
from tests.conversation_auth_support import USER_IDS

_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

pytestmark = pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable workflow UI read API PostgreSQL tests",
)

__all__ = [
    "TestUiReadApiDb",
    "TestUiReadApiIsolationDb",
    "TestUiReadApiNonMutationDb",
    "TestResidueDb",
]

_RUN_PREFIX = "wf-p7ui-"
_USER_PREFIX = "p7ui-"
_PROJECT = "p7ui-proj-a"
_OTHER_PROJECT = "p7ui-proj-b"

_UI = "/api/ui/projects"


def _require_test_database() -> Any:
    """**硬断言**当前引擎指向专用测试库（任何 SQL 之前；失败即 fail）。"""
    engine = get_engine()
    if engine is None:
        pytest.fail(
            "DATABASE_URL 未配置：Workflow UI DB 集成测试必须显式指向 "
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
# 种子 / 清理 / 快照
# ============================================================


def _grant_membership(project_id: str) -> None:
    """经**真实** Repository 为已登录用户（alice）写入项目成员授权（幂等）。"""
    AuthorizationGrantRepository().grant_project_role(
        user_id=USER_IDS["alice"],
        project_id=project_id,
        role=PROJECT_ROLE_MEMBER,
    )


def _seed_run(project_id: str = _PROJECT) -> str:
    """真实 Repository 播种：Run + 2 次 attempt（同节点）+ APPROVAL 节点."""
    run_id = f"{_RUN_PREFIX}{uuid.uuid4().hex[:12]}"
    WorkflowRunRepository().create(
        workflow_run_id=run_id,
        workflow_id="wf-def-p7ui",
        workflow_version="v1",
        request_id="req-p7ui-fixture",
        project_id=project_id,
    )
    node_repo = WorkflowNodeRunRepository()
    node_repo.create(
        workflow_run_id=run_id,
        node_id="step_one",
        attempt=1,
        node_type="TOOL",
        status="FAILED",
        error_code="WORKFLOW_NODE_EXECUTION_FAILURE",
    )
    node_repo.create(
        workflow_run_id=run_id,
        node_id="step_one",
        attempt=2,
        node_type="TOOL",
        status="SUCCEEDED",
        output='{"qty":3}',
    )
    node_repo.create(
        workflow_run_id=run_id,
        node_id="gate",
        attempt=1,
        node_type="APPROVAL",
        status="WAITING_APPROVAL",
    )
    WorkflowApprovalRepository().create(
        approval_id=f"{run_id}-ap",
        workflow_run_id=run_id,
        node_id="gate",
    )
    return run_id


def _cleanup(engine: Any) -> None:
    """定向清理：本套件的 workflow Run（FK 级联）与授权行。"""
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
            for table in ("workflow_run", "workflow_node_run", "workflow_approval")
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
    return counts


def _state_snapshot(engine: Any, run_id: str) -> dict[str, Any]:
    """Run / attempt / approval 的关键字段快照（用于"查询不改状态"断言）。"""
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
    return {
        "run": tuple(run),
        "nodes": [tuple(row) for row in nodes],
        "approvals": [tuple(row) for row in approvals],
    }


@pytest.fixture()
def db_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    """硬断言测试库 + 前后定向清理 + 仅替换**会话存储**。"""
    engine = _require_test_database()
    _cleanup(engine)
    # 会话存储替换为进程内 Fake（M1 认证语义仍由真实 require_session 执行）；
    # **授权**保持真实：enforce_project_access 查真实 authorization_grant 表。
    install_fake_session_service(monkeypatch)
    try:
        yield engine
    finally:
        _cleanup(engine)
        _require_test_database()


# ============================================================
# ① / ② / ③：授权与会话
# ============================================================


class TestUiReadApiDb:
    def test_authorized_member_can_read_run(
        self, db_env: Any
    ) -> None:
        """① 已授权（真实 authorization_grant 行）会话可读测试 Run。"""
        client = SessionTestClient(app)
        _grant_membership(_PROJECT)
        run_id = _seed_run()

        response = client.get(f"{_UI}/{_PROJECT}/workflow-runs/{run_id}")

        assert response.status_code == 200, response.text
        payload = response.json()
        assert set(payload.keys()) == set(WORKFLOW_RUN_DETAIL_RESPONSE_FIELDS)
        assert payload["workflow_run_id"] == run_id
        assert payload["project_id"] == _PROJECT

    def test_nodes_and_timeline_and_approval_readable(
        self, db_env: Any
    ) -> None:
        client = SessionTestClient(app)
        _grant_membership(_PROJECT)
        run_id = _seed_run()

        nodes = client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{run_id}/nodes"
        )
        timeline = client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{run_id}/timeline"
        )
        approval = client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{run_id}/approvals/gate"
        )

        assert nodes.status_code == 200
        assert timeline.status_code == 200
        assert approval.status_code == 200
        assert approval.json()["status"] == "PENDING"
        assert approval.json()["resolved_at"] is None

    def test_unauthorized_user_cannot_read(
        self, db_env: Any
    ) -> None:
        """② 无授权行 ⇒ 403（真实授权表查询结果为空）。"""
        client = SessionTestClient(app, username="bob")   # bob 无授权行
        run_id = _seed_run()      # 播种 Run，但**不**授予成员资格

        for path in (
            f"{_UI}/{_PROJECT}/workflow-runs/{run_id}",
            f"{_UI}/{_PROJECT}/workflow-runs/{run_id}/nodes",
            f"{_UI}/{_PROJECT}/workflow-runs/{run_id}/timeline",
            f"{_UI}/{_PROJECT}/workflow-runs/{run_id}/approvals/gate",
        ):
            response = client.get(path)
            assert response.status_code == 403, (path, response.text)
            assert response.json()["detail"] == "权限不足：需要项目访问授权"
            # 403 响应不得泄漏 Run 数据
            assert run_id not in response.text

    def test_grant_for_other_project_does_not_help(
        self, db_env: Any
    ) -> None:
        """授权按项目隔离：只有 B 项目授权时读 A 项目 ⇒ 403。"""
        client = SessionTestClient(app)
        _grant_membership(_OTHER_PROJECT)
        run_id = _seed_run()

        response = client.get(f"{_UI}/{_PROJECT}/workflow-runs/{run_id}")

        assert response.status_code == 403

    def test_anonymous_cannot_read(self, db_env: Any) -> None:
        run_id = _seed_run()

        from fastapi.testclient import TestClient

        with TestClient(app) as anonymous:
            response = anonymous.get(
                f"{_UI}/{_PROJECT}/workflow-runs/{run_id}"
            )

        assert response.status_code == 401


class TestUiReadApiIsolationDb:
    def test_cross_project_run_isomorphic_404(self, db_env: Any) -> None:
        """③ 已被授权 A 项目，但请求的 Run 实际属于 B 项目 ⇒ 404 同构。"""
        client = SessionTestClient(app)
        _grant_membership(_PROJECT)
        other_run = _seed_run(project_id=_OTHER_PROJECT)

        cross = client.get(f"{_UI}/{_PROJECT}/workflow-runs/{other_run}")
        missing = client.get(f"{_UI}/{_PROJECT}/workflow-runs/wf-p7ui-missing")

        assert cross.status_code == 404
        assert cross.json() == missing.json()

    def test_cross_project_nodes_timeline_approval_404(self, db_env: Any) -> None:
        client = SessionTestClient(app)
        _grant_membership(_PROJECT)
        other_run = _seed_run(project_id=_OTHER_PROJECT)

        for suffix in (
            "/nodes",
            "/timeline",
            "/approvals/gate",
        ):
            response = client.get(
                f"{_UI}/{_PROJECT}/workflow-runs/{other_run}{suffix}"
            )
            assert response.status_code == 404, suffix
            assert other_run not in response.text


# ============================================================
# ④ / ⑤：顺序与归属
# ============================================================


class TestUiReadApiOwnershipDb:
    def test_multiple_attempts_returned_in_order(self, db_env: Any) -> None:
        """④ 同一节点的多次 attempt 全部返回，按 attempt 升序。"""
        client = SessionTestClient(app)
        _grant_membership(_PROJECT)
        run_id = _seed_run()

        items = client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{run_id}/nodes"
        ).json()

        step_one = [item for item in items if item["node_id"] == "step_one"]
        assert [item["attempt"] for item in step_one] == [1, 2]   # 不折叠
        assert step_one[0]["status"] == "FAILED"
        assert step_one[0]["error_code"] == "WORKFLOW_NODE_EXECUTION_FAILURE"
        assert step_one[1]["status"] == "SUCCEEDED"
        assert step_one[1]["output"] == '{"qty":3}'               # 原样

    def test_approval_membership_verified(self, db_env: Any) -> None:
        """⑤ Approval 归属正确（真实表行的 run/node 与请求一致）。"""
        client = SessionTestClient(app)
        _grant_membership(_PROJECT)
        run_id = _seed_run()

        response = client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{run_id}/approvals/gate"
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["workflow_run_id"] == run_id
        assert payload["node_id"] == "gate"
        assert "created_at" not in payload       # Port 未暴露 ⇒ 不补造

    def test_approval_for_absent_node_is_404(self, db_env: Any) -> None:
        client = SessionTestClient(app)
        _grant_membership(_PROJECT)
        run_id = _seed_run()

        response = client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{run_id}/approvals/no-such-node"
        )

        assert response.status_code == 404

    def test_approval_by_id_cross_project_404(self, db_env: Any) -> None:
        client = SessionTestClient(app)
        _grant_membership(_PROJECT)
        other_run = _seed_run(project_id=_OTHER_PROJECT)
        approval_id = f"{other_run}-ap"

        response = client.get(
            f"{_UI}/{_PROJECT}/workflow-approvals/{approval_id}"
        )

        assert response.status_code == 404


# ============================================================
# ⑥：查询不改状态
# ============================================================


class TestUiReadApiNonMutationDb:
    def test_queries_leave_state_unchanged(self, db_env: Any) -> None:
        client = SessionTestClient(app)
        _grant_membership(_PROJECT)
        run_id = _seed_run()

        before = _state_snapshot(db_env, run_id)

        for _ in range(3):
            for path in (
                f"{_UI}/{_PROJECT}/workflow-runs/{run_id}",
                f"{_UI}/{_PROJECT}/workflow-runs/{run_id}/nodes",
                f"{_UI}/{_PROJECT}/workflow-runs/{run_id}/timeline",
                f"{_UI}/{_PROJECT}/workflow-runs/{run_id}/approvals/gate",
                f"{_UI}/{_PROJECT}/workflow-approvals/{run_id}-ap",
            ):
                assert client.get(path).status_code in {200, 404}, path

        assert _state_snapshot(db_env, run_id) == before

    def test_denied_query_does_not_change_state(self, db_env: Any) -> None:
        client = SessionTestClient(app)   # 无授权
        run_id = _seed_run()

        before = _state_snapshot(db_env, run_id)
        assert client.get(
            f"{_UI}/{_PROJECT}/workflow-runs/{run_id}"
        ).status_code == 403

        assert _state_snapshot(db_env, run_id) == before

    def test_write_methods_rejected_without_state_change(
        self, db_env: Any
    ) -> None:
        client = SessionTestClient(app)
        _grant_membership(_PROJECT)
        run_id = _seed_run()

        before = _state_snapshot(db_env, run_id)
        path = f"{_UI}/{_PROJECT}/workflow-runs/{run_id}"

        for method in ("post", "put", "patch", "delete"):
            response = getattr(client, method)(path)
            assert response.status_code in {404, 405}, method

        assert _state_snapshot(db_env, run_id) == before


# ============================================================
# ⑦：residue
# ============================================================


class TestResidueDb:
    def test_residue_is_zero_after_cleanup(self, db_env: Any) -> None:
        _cleanup(db_env)
        assert _residue(db_env) == {
            "workflow_run": 0,
            "workflow_node_run": 0,
            "workflow_approval": 0,
            "authorization_grant": 0,
        }
