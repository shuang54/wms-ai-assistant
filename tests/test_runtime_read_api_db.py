"""Phase 7 Step 2 — 统一 Runtime 读取 API **DB-gated** 集成测试（真 PostgreSQL）。

**默认跳过**（沿用项目既有 ``RUN_DB_TESTS`` 门控；专用测试库 = 本地开发库）：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_runtime_read_api_db.py
```

真链（**不 monkeypatch 读取服务**——直接走生产 Composition Root；仅
monkeypatch **认证配置**，因为真实环境凭据不属于测试）：

```text
TestClient → /api/runtime-runs/{runtime_type}/{run_id}
→ runtime_read_auth（按域复用 agent_read_auth / workflow_read_auth）
→ runtime_read（按域分发）
→ AgentReadService / WorkflowReadService（生产装配）
→ Repositories / Store Adapters → PostgreSQL
```

覆盖（Phase 7 Step 2 任务书 §七 DB 项）：

```text
① Agent 运行经统一入口真链可读（200）
② Workflow 运行经统一入口真链可读（200）
③ 跨项目被拒绝（404 同构）+ 无凭据 401
④ 运行类型不混淆（跨类型 run_id ⇒ 404 + 对应域文案）
⑤ 查询前后状态一致（快照对比）
⑥ 清理后数据库无残留（两域定向前缀）
```

只写 ``ai_ops.agent_run`` 与 ``ai_ops.workflow_*``（seed 数据）；定向前缀
清理（agent ``rt-p7s2-agent-`` / workflow ``rt-p7s2-wf-``）。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.api import agent_read_auth as agent_auth_module
from backend.app.api import workflow_read_auth as workflow_auth_module
from backend.app.config import AgentReadSettings, WorkflowReadSettings
from backend.app.db.agent_run_repository import AgentRunRepository
from backend.app.db.session import get_engine
from backend.app.db.workflow_run_repository import WorkflowRunRepository
from backend.app.main import app

_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

pytestmark = pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable runtime read API PostgreSQL tests",
)

__all__ = [
    "TestRuntimeReadApiDb",
    "TestRuntimeReadApiResidueDb",
]

_AGENT_PREFIX = "rt-p7s2-agent-"
_WF_PREFIX = "rt-p7s2-wf-"
_PROJECT = "proj-p7s2-db"
_OTHER_PROJECT = "proj-p7s2-db-other"

#: 测试专用凭据（**非**生产凭据；仅存在于测试进程内 monkeypatch 配置）。
_AGENT_KEY = "runtime-db-agent-key-NOT-A-PRODUCTION-CREDENTIAL"
_WF_KEY = "runtime-db-workflow-key-NOT-A-PRODUCTION-CREDENTIAL"
_AGENT_HEADERS = {"X-API-Key": _AGENT_KEY}
_WF_HEADERS = {"X-API-Key": _WF_KEY}


def _agent_run_id() -> str:
    return f"{_AGENT_PREFIX}{uuid.uuid4().hex[:12]}"


def _wf_run_id() -> str:
    return f"{_WF_PREFIX}{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def engine() -> Any:
    eng = get_engine()
    if eng is None:
        pytest.skip("DATABASE_URL 未配置，跳过统一 Runtime 读取 DB 集成测试")
    return eng


def _delete_prefixes(engine: Any) -> None:
    """定向前缀清理（agent_run + workflow 三表；**不触**其它数据）。"""
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM ai_ops.agent_run WHERE run_id LIKE :prefix"),
            {"prefix": f"{_AGENT_PREFIX}%"},
        )
        conn.execute(
            text("DELETE FROM ai_ops.workflow_run WHERE workflow_run_id LIKE :prefix"),
            {"prefix": f"{_WF_PREFIX}%"},
        )
        conn.execute(
            text(
                "DELETE FROM ai_ops.workflow_node_run "
                "WHERE workflow_run_id LIKE :prefix"
            ),
            {"prefix": f"{_WF_PREFIX}%"},
        )
        conn.execute(
            text(
                "DELETE FROM ai_ops.workflow_approval "
                "WHERE workflow_run_id LIKE :prefix"
            ),
            {"prefix": f"{_WF_PREFIX}%"},
        )


def _counts(engine: Any) -> dict[str, int]:
    """定向前缀计数（只读；用于前后快照对比）。"""
    with engine.connect() as conn:
        agent = int(
            conn.execute(
                text("SELECT COUNT(*) FROM ai_ops.agent_run WHERE run_id LIKE :p"),
                {"p": f"{_AGENT_PREFIX}%"},
            ).scalar_one()
        )
        wf = int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.workflow_run "
                    "WHERE workflow_run_id LIKE :p"
                ),
                {"p": f"{_WF_PREFIX}%"},
            ).scalar_one()
        )
    return {"agent_run": agent, "workflow_run": wf}


@pytest.fixture(scope="module", autouse=True)
def _cleanup(engine: Any):
    _delete_prefixes(engine)
    yield
    _delete_prefixes(engine)


def _seed_agent_run(*, project_id: str = _PROJECT) -> str:
    """真实 Repository 写一条 Agent Run（返回 run_id）。"""
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
        duration_ms=12.5,
    )
    return run_id


def _seed_workflow_run(*, project_id: str = _PROJECT) -> str:
    """真实 Repository 写一条 Workflow Run（返回 run_id）。"""
    run_id = _wf_run_id()
    WorkflowRunRepository().create(
        workflow_run_id=run_id,
        workflow_id="p7s2_runtime_read_flow",
        workflow_version="v1",
        request_id=f"{_WF_PREFIX}req-{uuid.uuid4().hex[:8]}",
        project_id=project_id,
        input='{"seed": "runtime-read-db"}',
        status="PENDING",
    )
    return run_id


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """真链客户端：只替换**两域认证配置**（读取服务走生产装配 → DB）。"""
    monkeypatch.setattr(
        agent_auth_module,
        "get_agent_read_settings",
        lambda: AgentReadSettings(api_key=_AGENT_KEY, api_project_id=_PROJECT),
    )
    monkeypatch.setattr(
        workflow_auth_module,
        "get_workflow_read_settings",
        lambda: WorkflowReadSettings(api_key=_WF_KEY, api_project_id=_PROJECT),
    )
    return TestClient(app)


# ============================================================
# Test 1：真链读取（Agent / Workflow / 隔离 / 认证 / 类型不混淆）
# ============================================================


class TestRuntimeReadApiDb:
    def test_agent_run_via_unified_entry_real_chain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id = _seed_agent_run()
        response = _client(monkeypatch).get(
            f"/api/runtime-runs/agent/{run_id}", headers=_AGENT_HEADERS
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["runtime_type"] == "agent"
        assert payload["run_id"] == run_id
        assert payload["project_id"] == _PROJECT
        assert payload["status"] == "COMPLETED"
        assert payload["started_at"] is not None
        # 类型专属详情原样内嵌（域字段集）
        assert payload["detail"]["run_id"] == run_id
        assert payload["detail"]["status"] == "COMPLETED"
        assert payload["detail"]["project_id"] == _PROJECT
        assert "created_at" not in payload["detail"]

    def test_workflow_run_via_unified_entry_real_chain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id = _seed_workflow_run()
        response = _client(monkeypatch).get(
            f"/api/runtime-runs/workflow/{run_id}", headers=_WF_HEADERS
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["runtime_type"] == "workflow"
        assert payload["run_id"] == run_id
        assert payload["project_id"] == _PROJECT
        assert payload["status"] == "PENDING"
        assert payload["detail"]["workflow_run_id"] == run_id
        assert payload["detail"]["workflow_id"] == "p7s2_runtime_read_flow"
        assert "input" not in payload["detail"]
        assert "request_id" not in payload["detail"]

    def test_cross_project_agent_run_is_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id = _seed_agent_run(project_id=_OTHER_PROJECT)
        response = _client(monkeypatch).get(
            f"/api/runtime-runs/agent/{run_id}", headers=_AGENT_HEADERS
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Agent Run 不存在"

    def test_cross_project_workflow_run_is_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id = _seed_workflow_run(project_id=_OTHER_PROJECT)
        response = _client(monkeypatch).get(
            f"/api/runtime-runs/workflow/{run_id}", headers=_WF_HEADERS
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Workflow Run 不存在"

    def test_agent_run_id_under_workflow_type_is_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """真链下运行类型不混淆：Agent run_id 在 workflow 类型下不可读。"""
        run_id = _seed_agent_run()
        response = _client(monkeypatch).get(
            f"/api/runtime-runs/workflow/{run_id}", headers=_WF_HEADERS
        )
        assert response.status_code == 404

    def test_workflow_run_id_under_agent_type_is_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id = _seed_workflow_run()
        response = _client(monkeypatch).get(
            f"/api/runtime-runs/agent/{run_id}", headers=_AGENT_HEADERS
        )
        assert response.status_code == 404

    def test_missing_credential_is_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run_id = _seed_agent_run()
        client = _client(monkeypatch)
        assert client.get(f"/api/runtime-runs/agent/{run_id}").status_code == 401
        assert client.get(
            f"/api/runtime-runs/workflow/{run_id}"
        ).status_code == 401


# ============================================================
# Test 2：查询前后一致 + 无残留
# ============================================================


class TestRuntimeReadApiResidueDb:
    def test_reads_do_not_mutate_and_leave_no_residue(
        self, engine: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """查询前后快照一致；清理后定向前缀无残留（零 DELETE 验证之外）。"""
        agent_id = _seed_agent_run()
        wf_id = _seed_workflow_run()

        before = _counts(engine)
        client = _client(monkeypatch)
        assert (
            client.get(
                f"/api/runtime-runs/agent/{agent_id}", headers=_AGENT_HEADERS
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/runtime-runs/workflow/{wf_id}", headers=_WF_HEADERS
            ).status_code
            == 200
        )
        after = _counts(engine)

        # 查询本身不写库（seed 前后快照一致）
        assert after == before
        assert before["agent_run"] >= 1
        assert before["workflow_run"] >= 1

    def test_prefix_cleanup_leaves_no_rows(self, engine: Any) -> None:
        """定向前缀清理后无残留（测试自清纪律）。"""
        _delete_prefixes(engine)
        assert _counts(engine) == {"agent_run": 0, "workflow_run": 0}
