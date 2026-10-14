"""Phase 7 Step 4 — Agent 命令 API **DB-gated** 集成测试（真 PostgreSQL）。

**默认跳过**（沿用项目既有 ``RUN_DB_TESTS`` 门控；专用测试库 = 本地开发库）：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_agent_command_api_db.py
```

真链（**不 monkeypatch 命令服务**——走生产 Composition Root；仅
monkeypatch **认证配置**，因为真实环境凭据不属于测试）：

```text
TestClient → agent_command（X-API-Key）
→ get_agent_command_service() → build_agent_command_service(project)
→ build_orchestrator_for_project(agent_enabled=True)（真装配）
→ AgentCommandService → 真 Repository（归属校验 / cancel / decision）
→ PostgreSQL
```

范围说明（如实记录）：``start`` / ``resume`` 的 **LLM 执行**路径不在本
文件重复验证（离线契约测试已用 Fake Runtime 覆盖其契约；真 LLM 调用属
外部依赖，不进入 DB 门控）。本文件覆盖**控制状态路径**的真链：
归属隔离（DB 层项目过滤）· 取消三态 · 审批裁决（幂等 / 冲突）·
认证 fail-closed · 无残留。

只写 ``ai_ops.agent_run`` / ``ai_ops.agent_approval``（seed 数据）；
定向前缀清理（``ac-p7s4-``）。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api import agent_command_auth as auth_module
from backend.app.config import AgentCommandSettings, settings
from backend.app.db.agent_approval_repository import (
    AgentApprovalRepository,
)
from backend.app.db.agent_run_repository import AgentRunRepository
from backend.app.db.session import get_engine
from backend.app.main import app

_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

pytestmark = pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable agent command API PostgreSQL tests",
)

__all__ = [
    "TestAgentCommandDb",
]

_RUN_PREFIX = "ac-p7s4-run-"
_APPROVAL_PREFIX = "ac-p7s4-ap-"
#: 授权作用域 = **已注册**的默认项目（composition root 经默认注册表解析；
#: 硬编码未注册项目 id 会使真链装配 503——项目隔离由 run 行的 project_id
#: 对照兑现，不依赖注册表）。
_PROJECT = settings.project.project_id
_OTHER_PROJECT = f"{_PROJECT}-other"

#: 测试专用凭据（**非**生产凭据；仅存在于测试进程内 monkeypatch 配置）。
_TEST_API_KEY = "agent-cmd-db-key-NOT-A-PRODUCTION-CREDENTIAL"
_HEADERS = {"X-API-Key": _TEST_API_KEY}


def _run_id() -> str:
    return f"{_RUN_PREFIX}{uuid.uuid4().hex[:12]}"


def _approval_id() -> str:
    return f"{_APPROVAL_PREFIX}{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def engine() -> Any:
    eng = get_engine()
    if eng is None:
        pytest.skip("DATABASE_URL 未配置，跳过 Agent 命令 API DB 集成测试")
    return eng


def _delete_prefixes_impl(engine: Any) -> None:
    """定向前缀清理（agent_run + agent_approval；**不触**其它数据）。"""
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM ai_ops.agent_run WHERE run_id LIKE :prefix"),
            {"prefix": f"{_RUN_PREFIX}%"},
        )
        conn.execute(
            text(
                "DELETE FROM ai_ops.agent_approval "
                "WHERE approval_id LIKE :prefix OR run_id LIKE :prefix"
            ),
            {"prefix": f"{_RUN_PREFIX}%"},
        )


def _counts(engine: Any) -> dict[str, int]:
    from sqlalchemy import text

    with engine.connect() as conn:
        return {
            "agent_run": int(
                conn.execute(
                    text(
                        "SELECT COUNT(*) FROM ai_ops.agent_run "
                        "WHERE run_id LIKE :p"
                    ),
                    {"p": f"{_RUN_PREFIX}%"},
                ).scalar_one()
            ),
            "agent_approval": int(
                conn.execute(
                    text(
                        "SELECT COUNT(*) FROM ai_ops.agent_approval "
                        "WHERE run_id LIKE :p"
                    ),
                    {"p": f"{_RUN_PREFIX}%"},
                ).scalar_one()
            ),
        }


@pytest.fixture(scope="module", autouse=True)
def _cleanup(engine: Any):
    _delete_prefixes_impl(engine)
    yield
    _delete_prefixes_impl(engine)


@pytest.fixture(autouse=True)
def _clean_prefix(engine: Any) -> Any:
    """每测试前清零定向前缀（自零基线）。"""
    _delete_prefixes_impl(engine)
    yield
    _delete_prefixes_impl(engine)


def _seed_run(*, project_id: str = _PROJECT) -> str:
    """真实 Repository 写一条 Agent Run（返回 run_id）。"""
    run_id = _run_id()
    now = datetime.now(timezone.utc)
    AgentRunRepository().create(
        run_id=run_id,
        request_id=f"{_RUN_PREFIX}req-{uuid.uuid4().hex[:8]}",
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
        duration_ms=5.0,
    )
    return run_id


def _seed_approval(*, run_id: str) -> str:
    """真实 Repository 写一条审批（返回 approval_id）。"""
    approval_id = _approval_id()
    AgentApprovalRepository().create(
        approval_id=approval_id,
        run_id=run_id,
        step_index=0,
        tool_name="get_inventory",
    )
    return approval_id


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """真链客户端：只替换**认证配置**（命令服务走生产装配 → DB）。"""
    monkeypatch.setattr(
        auth_module,
        "get_agent_command_settings",
        lambda: AgentCommandSettings(
            api_key=_TEST_API_KEY, api_project_id=_PROJECT
        ),
    )
    return TestClient(app)


class TestAgentCommandDb:
    def test_cancel_without_checkpoint_is_conflict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """真链取消：seed 的 Run 无 Checkpoint 控制状态 ⇒ NOT_CANCELLABLE 409
        （不伪造行；fail-closed）。"""
        run_id = _seed_run()
        response = _client(monkeypatch).post(
            f"/api/agent-runs/{run_id}/cancel", headers=_HEADERS
        )
        assert response.status_code == 409
        assert "不可取消" in response.json()["detail"]

    def test_cross_project_cancel_is_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """真实项目隔离：他项目 Run 的取消 ⇒ 404（与不存在同码同文案）。"""
        run_id = _seed_run(project_id=_OTHER_PROJECT)
        client = _client(monkeypatch)
        missing = client.post(
            "/api/agent-runs/ac-p7s4-missing/cancel", headers=_HEADERS
        )
        foreign = client.post(
            f"/api/agent-runs/{run_id}/cancel", headers=_HEADERS
        )
        assert missing.status_code == 404
        assert foreign.status_code == 404
        assert foreign.json() == missing.json()

    def test_decision_approved_via_real_chain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """真链审批裁决：PENDING → APPROVED（真 Repository 条件 UPDATE）。"""
        run_id = _seed_run()
        approval_id = _seed_approval(run_id=run_id)
        response = _client(monkeypatch).post(
            f"/api/agent-approvals/{approval_id}/decision",
            json={"decision": "APPROVED"},
            headers=_HEADERS,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["outcome"] == "COMPLETED"
        assert payload["approval_status"] == "APPROVED"
        assert payload["decision"] == "APPROVED"
        assert payload["run"]["run_id"] == run_id

    def test_decision_idempotent_via_real_chain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """同裁决重复提交 ⇒ 幂等 200（真链）。"""
        run_id = _seed_run()
        approval_id = _seed_approval(run_id=run_id)
        client = _client(monkeypatch)
        first = client.post(
            f"/api/agent-approvals/{approval_id}/decision",
            json={"decision": "APPROVED"},
            headers=_HEADERS,
        )
        second = client.post(
            f"/api/agent-approvals/{approval_id}/decision",
            json={"decision": "APPROVED"},
            headers=_HEADERS,
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["approval_status"] == "APPROVED"

    def test_opposite_decision_is_conflict_via_real_chain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """相反裁决 ⇒ 409（终态不可逆；真链）。"""
        run_id = _seed_run()
        approval_id = _seed_approval(run_id=run_id)
        client = _client(monkeypatch)
        assert (
            client.post(
                f"/api/agent-approvals/{approval_id}/decision",
                json={"decision": "APPROVED"},
                headers=_HEADERS,
            ).status_code
            == 200
        )
        conflict = client.post(
            f"/api/agent-approvals/{approval_id}/decision",
            json={"decision": "REJECTED"},
            headers=_HEADERS,
        )
        assert conflict.status_code == 409
        assert "审批冲突" in conflict.json()["detail"]

    def test_cross_project_decision_is_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """审批归属经父 Run 校验：他项目 Run 的审批 ⇒ 404（真链）。"""
        run_id = _seed_run(project_id=_OTHER_PROJECT)
        approval_id = _seed_approval(run_id=run_id)
        response = _client(monkeypatch).post(
            f"/api/agent-approvals/{approval_id}/decision",
            json={"decision": "APPROVED"},
            headers=_HEADERS,
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Agent 审批不存在"

    def test_missing_credential_is_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run_id = _seed_run()
        assert (
            _client(monkeypatch)
            .post(f"/api/agent-runs/{run_id}/cancel")
            .status_code
            == 401
        )

    def test_unconfigured_is_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """§四 fail-closed：认证未配置 ⇒ 503（不匿名放行）。"""
        run_id = _seed_run()
        client = _client(monkeypatch)
        # 先建 client（有效配置），再改为未配置——覆盖 auth Composition Root
        monkeypatch.setattr(
            auth_module,
            "get_agent_command_settings",
            lambda: AgentCommandSettings(),
        )
        assert (
            client.post(
                f"/api/agent-runs/{run_id}/cancel", headers=_HEADERS
            ).status_code
            == 503
        )

    def test_reads_leave_no_residue(self, engine: Any) -> None:
        """控制状态查询 / 裁决后清理 ⇒ 定向前缀无残留。"""
        _delete_prefixes_impl(engine)
        assert _counts(engine) == {"agent_run": 0, "agent_approval": 0}
