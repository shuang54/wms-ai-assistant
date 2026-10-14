"""Phase 7 Step 6 — Agent Command API **真实 LLM + 持久化 + Read/List 闭环** Smoke。

目标（**只验证，不改造**）：证明 HTTP 命令入口 → Agent Runtime → **真实**
DeepSeek Provider → 决策 → Tool 授权 → **真实** WMS 只读 Tool（``get_inventory``，
真实 Handler / Registry / Engine）→ 持久化 → Read/List API 的完整闭环。

```text
POST /api/agent-runs                （真实认证依赖；本测试替换**仅**服务端凭据配置）
    → get_agent_command_service()   （**真实** Composition Root：build_agent_command_service）
        → 真 AgentRuntime → 真 DeepSeek（settings.llm）→ 真只读 Tool → 真 Engine
        → agent_run / agent_step / agent_tool_result / agent_checkpoint / agent_decision
GET  /api/runtime-runs/agent/{run_id}      （真实只读链）
GET  /api/runtime-runs/agent               （真实列表链）
POST /api/agent-runs/{run_id}/resume       （终态幂等：Replay，不重复执行已完成的 Tool）
```

开启方式（**默认 `python -m pytest` 永不发起真实调用**）：

```powershell
$env:RUN_REAL_LLM_TESTS="1"
python -m pytest -m real_llm tests/test_agent_command_real_llm_smoke.py -q -s
```

三层防护（与 ``tests/test_agent_real_llm_smoke.py`` 一致）：
    1. ``RUN_REAL_LLM_TESTS`` / ``RUN_REAL_LLM_TEST`` 未设置 → **SKIP**；
    2. 开关打开但 ``LLM_API_KEY`` 为空 → SKIP；
    3. 开关打开但 ``LLM_BASE_URL`` / ``LLM_MODEL`` 为空 → SKIP。

业务数据边界（**如实**；不为通过 Smoke 创造条件）：
    * 本仓库开发库当前**没有** ``public.inventory``（Phase 5 已记录）；
    * 表存在 ⇒ 断言完整业务闭环（Tool success + 业务结果 + 终态 Resume 幂等）；
    * 表不存在 ⇒ 链路级断言照常（LLM 决策 / Tool 授权 / 真实派发 / 持久化 /
      Read/List 可见），业务断言与 Resume 幂等断言 **SKIP** 并给出原因
      （不伪造业务结果、不建表、不造数）。

清理（**定向**，沿既有残留纪律）：
    * 本次 run 的 ``agent_run / agent_step / agent_tool_result /
      agent_checkpoint / agent_decision`` 行 ⇒ 按 ``run_id`` 删除；
    * 四张观测表 ⇒ 模块内水印（只删本模块新增：``id > watermark``）。

安全边界（报告）：不打印 API Key / Base URL / 完整 prompt / 完整 Tool Result；
不关闭认证 / 项目隔离 / Tool Authorization（HTTP 认证契约复用既有安全测试覆盖）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

_REPO_ROOT = Path(__file__).resolve().parents[1]

__all__ = [
    "test_real_agent_command_chain_persists_run_and_tool_dispatch",
    "test_real_agent_command_run_visible_in_read_and_list_api",
    "test_real_agent_command_terminal_resume_does_not_reexecute_tool",
    "test_real_agent_command_business_result_when_inventory_table_present",
    "test_smoke_script_default_is_no_op",
]

#: Smoke 问题（简单、单 Tool、可直接由 get_inventory schema 回答）。
_QUESTION: str = "查询物料 MAT001 的当前库存数量。"

#: 测试专用凭据（**非**生产凭据；仅存在于测试进程内 monkeypatch 配置）。
_TEST_COMMAND_KEY = "p7s6-agent-command-key-NOT-A-PRODUCTION-CREDENTIAL"
_TEST_READ_KEY = "p7s6-agent-read-key-NOT-A-PRODUCTION-CREDENTIAL"
_COMMAND_HEADERS = {"X-API-Key": _TEST_COMMAND_KEY}
_READ_HEADERS = {"X-API-Key": _TEST_READ_KEY}


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


_RUN_REAL = _env_flag("RUN_REAL_LLM_TESTS") or _env_flag("RUN_REAL_LLM_TEST")

_needs_real_llm = pytest.mark.skipif(
    not _RUN_REAL,
    reason=(
        "set RUN_REAL_LLM_TESTS=1 (or RUN_REAL_LLM_TEST=1) to enable the real "
        "DeepSeek agent command smoke（默认 SKIP：零真实调用）"
    ),
)


def _require_llm_config() -> None:
    """第二 / 三层防护：无 Key / URL / Model → skip。"""
    from backend.app.config import settings

    if not settings.llm.api_key:
        pytest.skip("LLM_API_KEY 未配置；请在 .env 中设置后重试")
    if not settings.llm.base_url:
        pytest.skip("LLM_BASE_URL 未配置")
    if not settings.llm.model:
        pytest.skip("LLM_MODEL 未配置")


@dataclass
class _RealRun:
    """一次真实 run 的只读证据（断言输入；不含 Key / prompt / payload）。"""

    run_id: str
    payload: dict[str, Any]
    detail: dict[str, Any]
    list_ids: list[str]
    table_present: bool
    project_id: str
    schema_name: str
    table_name: str


def _view(detail: dict[str, Any]) -> dict[str, Any]:
    """统一详情响应中内嵌的 Agent 域详情（``detail`` 键）。"""
    return detail.get("detail") or {}


@pytest.fixture(scope="module")
def real_run() -> Any:
    """真实链路执行一次（HTTP + 真实 Composition Root），模块结束定向清理。"""
    _require_llm_config()

    from backend.app.api import agent_command_auth, agent_read_auth
    from backend.app.config import AgentCommandSettings, AgentReadSettings, settings
    from backend.app.db.session import get_engine
    from backend.app.main import app
    from backend.app.projects.configuration import (
        get_default_project_configuration_provider,
    )
    from scripts.run_agent_llm_smoke import _cleanup, _table_exists, _watermarks

    engine = get_engine()
    if engine is None:
        pytest.skip("DATABASE_URL 未配置；Run/Step 持久化与 Read/List 需要 DB")

    project_id = settings.project.project_id
    configuration = get_default_project_configuration_provider().get(project_id)
    table_present = _table_exists(
        engine, configuration.schema_name, settings.inventory_tool.table_name
    )
    watermarks = _watermarks(engine)

    mp = pytest.MonkeyPatch()
    mp.setattr(
        agent_command_auth,
        "get_agent_command_settings",
        lambda: AgentCommandSettings(
            api_key=_TEST_COMMAND_KEY, api_project_id=project_id
        ),
    )
    mp.setattr(
        agent_read_auth,
        "get_agent_read_settings",
        lambda: AgentReadSettings(
            api_key=_TEST_READ_KEY, api_project_id=project_id
        ),
    )

    run_id: str | None = None
    try:
        client = TestClient(app)
        response = client.post(
            "/api/agent-runs", json={"question": _QUESTION}, headers=_COMMAND_HEADERS
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        run_id = payload["run_id"]
        assert isinstance(run_id, str) and run_id

        detail_response = client.get(
            f"/api/runtime-runs/agent/{run_id}", headers=_READ_HEADERS
        )
        assert detail_response.status_code == 200, detail_response.text
        detail = detail_response.json()

        list_response = client.get("/api/runtime-runs/agent", headers=_READ_HEADERS)
        assert list_response.status_code == 200, list_response.text
        list_ids = [item["run_id"] for item in list_response.json()["items"]]

        yield _RealRun(
            run_id=run_id,
            payload=payload,
            detail=detail,
            list_ids=list_ids,
            table_present=table_present,
            project_id=project_id,
            schema_name=configuration.schema_name,
            table_name=settings.inventory_tool.table_name,
        )
    finally:
        mp.undo()
        _cleanup(engine, run_id=run_id, watermarks=watermarks)


# ============================================================
# Test 1：真实链路（决策 + 授权 + 真实派发 + 持久化）
# ============================================================


@pytest.mark.real_llm
@_needs_real_llm
def test_real_agent_command_chain_persists_run_and_tool_dispatch(
    real_run: _RealRun,
) -> None:
    """LLM 决策 / Tool 选择与授权 / 真实 Handler 派发 / Run+Step 持久化。"""
    view = _view(real_run.detail)
    run_status = view.get("status")
    # 业务表存在 ⇒ COMPLETED；缺失 ⇒ FAILED（AGENT_ERROR_TOOL_EXECUTION_FAILURE）
    assert run_status in {"COMPLETED", "FAILED"}, view

    # ---- LLM 决策（持久化证据：decision 行）----
    decisions = view.get("decisions") or []
    assert decisions, "无持久化 LLM 决策行（真实 Provider 未产生决策）"
    assert int(view.get("llm_call_count") or 0) >= 1

    # ---- Tool 选择与授权（TOOL_CALL 决策 + step 记录 get_inventory）----
    tool_decisions = [
        d for d in decisions if d.get("decision_type") == "TOOL_CALL"
    ]
    assert tool_decisions, f"真实 LLM 未产生 TOOL_CALL 决策: {decisions}"
    assert tool_decisions[0].get("tool_name") == "get_inventory"

    steps = view.get("steps") or []
    tool_steps = [s for s in steps if s.get("step_type") == "TOOL_CALL"]
    assert tool_steps, "无 TOOL_CALL step 持久化"
    assert tool_steps[0].get("tool_name") == "get_inventory"

    # ---- 真实 Handler 派发（agent_tool_result 行 = 真实执行的证据）----
    tool_results = view.get("tool_results") or []
    assert tool_results, "无 agent_tool_result 行（真实 Handler 未被派发）"
    assert tool_results[0].get("tool_name") == "get_inventory"
    assert len(tool_results) == 1, "一次 Smoke 只允许一次 Tool 执行"


# ============================================================
# Test 2：Read / List API 可见性（真实只读链）
# ============================================================


@pytest.mark.real_llm
@_needs_real_llm
def test_real_agent_command_run_visible_in_read_and_list_api(
    real_run: _RealRun,
) -> None:
    """运行记录可经现有 Read / List API 查询（同一 DB，同一只读链）。"""
    assert real_run.detail.get("run_id") == real_run.run_id
    assert real_run.detail.get("runtime_type") == "agent"
    assert real_run.detail.get("project_id") == real_run.project_id
    assert real_run.detail.get("status") in {"COMPLETED", "FAILED"}
    assert _view(real_run.detail).get("run_id") == real_run.run_id

    assert real_run.run_id in real_run.list_ids, (
        f"List API 未返回本次 run（items={real_run.list_ids[:5]}…）"
    )


# ============================================================
# Test 3：终态 Resume 幂等（不重复执行已完成的 Tool / 不重复 LLM）
# ============================================================


@pytest.mark.real_llm
@_needs_real_llm
def test_real_agent_command_terminal_resume_does_not_reexecute_tool(
    real_run: _RealRun,
) -> None:
    """COMPLETED run 的 Resume ⇒ 历史终态 Replay：零 LLM / 零 Tool 重跑。"""
    view = _view(real_run.detail)
    if view.get("status") != "COMPLETED":
        pytest.skip(
            "BUSINESS_DATA_BLOCKED：run 未达 COMPLETED（"
            f"{real_run.schema_name}.{real_run.table_name} 缺失）⇒ "
            "终态 Replay 幂等不适用（不为通过 Smoke 建表/造数）"
        )

    from backend.app.api import agent_command_auth
    from backend.app.config import AgentCommandSettings, settings
    from backend.app.main import app

    before = {
        "llm_calls": view.get("llm_call_count"),
        "tool_calls": view.get("tool_call_count"),
        "steps": len(view.get("steps") or []),
        "decisions": len(view.get("decisions") or []),
        "tool_results": len(view.get("tool_results") or []),
    }

    mp = pytest.MonkeyPatch()
    mp.setattr(
        agent_command_auth,
        "get_agent_command_settings",
        lambda: AgentCommandSettings(
            api_key=_TEST_COMMAND_KEY, api_project_id=settings.project.project_id
        ),
    )
    try:
        client = TestClient(app)
        response = client.post(
            f"/api/agent-runs/{real_run.run_id}/resume",
            json={},
            headers=_COMMAND_HEADERS,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["outcome"] == "COMPLETED"
        assert payload["run_id"] == real_run.run_id

        after_view = _view(
            client.get(
                f"/api/runtime-runs/agent/{real_run.run_id}", headers=_READ_HEADERS
            ).json()
        )
    finally:
        mp.undo()

    after = {
        "llm_calls": after_view.get("llm_call_count"),
        "tool_calls": after_view.get("tool_call_count"),
        "steps": len(after_view.get("steps") or []),
        "decisions": len(after_view.get("decisions") or []),
        "tool_results": len(after_view.get("tool_results") or []),
    }
    assert after == before, f"Resume 重跑了已完成的工作: before={before} after={after}"


# ============================================================
# Test 4：业务闭环（仅当真实库存表存在）
# ============================================================


@pytest.mark.real_llm
@_needs_real_llm
def test_real_agent_command_business_result_when_inventory_table_present(
    real_run: _RealRun,
) -> None:
    """真实 WMS 只读业务结果（表存在 ⇒ Tool success + 业务数据进入 Run 结果）。"""
    if not real_run.table_present:
        pytest.skip(
            "BUSINESS_DATA_BLOCKED："
            f"{real_run.schema_name}.{real_run.table_name} 不存在 ⇒ 无法验证真实业务结果"
            "（不建表 / 不造数；真实部署提供该表后本用例即可验证）"
        )

    view = _view(real_run.detail)
    assert view.get("status") == "COMPLETED"
    tool_results = view.get("tool_results") or []
    assert len(tool_results) == 1
    assert tool_results[0].get("success") is True, tool_results[0]
    assert real_run.payload.get("content"), "最终 content 为空（业务结果未进入 Agent 结果）"


# ============================================================
# 离线守卫：Smoke 脚本默认零真实调用（无 real_llm 标记；默认永远运行）
# ============================================================


def test_smoke_script_default_is_no_op() -> None:
    """未设置 RUN_REAL_LLM_TESTS 时，Smoke 脚本必须直接 SKIP（exit 2）。"""
    env = dict(os.environ)
    env.pop("RUN_REAL_LLM_TESTS", None)
    env.pop("RUN_REAL_LLM_TEST", None)
    result = subprocess.run(
        [sys.executable, "scripts/run_agent_llm_smoke.py"],
        capture_output=True,
        env=env,
        cwd=str(_REPO_ROOT),
        timeout=120,
    )
    output = (
        result.stdout.decode("utf-8", errors="replace")
        + result.stderr.decode("utf-8", errors="replace")
    )
    assert result.returncode == 2, output
    assert "SKIPPED" in output
