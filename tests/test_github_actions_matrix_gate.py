"""Phase 3.12 Step 97 — GitHub Actions workflow **静态审计**（不访问 GitHub）。

覆盖 §十八 1～10 + §十九/§二十/§二十二/§二十三 边界：
文件存在 · 触发条件 · checkout/setup-python · 核心命令 · 不吞失败 · 无刷新参数 ·
无 LLM 凭据 · 无外部调用 · 无 secrets · baseline 只读 · 无 GitHub API · 单一 gate。

本文件为纯文件解析（PyYAML 为项目既有依赖），无网络、无 DB、无 LLM。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = ".github/workflows/observability-matrix-gate.yml"
_CLI_COMMAND = "scripts/run_matrix_gate.py"


def _workflow_path() -> Path:
    return _REPO_ROOT / _WORKFLOW


def _workflow_text() -> str:
    return _workflow_path().read_text(encoding="utf-8")


def _workflow() -> dict:
    return yaml.safe_load(_workflow_text())


def _job() -> dict:
    document = _workflow()
    jobs = document.get("jobs") or {}
    assert jobs, "workflow 必须定义 jobs"
    return jobs["observability-matrix-gate"]


def _steps() -> list[dict]:
    return list(_job().get("steps") or [])


def _run_bodies() -> str:
    return "\n".join(step.get("run", "") for step in _steps())


def _executable_text() -> str:
    """可执行内容：YAML 解析后的结构文本（**不含**注释说明）。"""
    return yaml.safe_dump(_workflow(), allow_unicode=True)


# ============================================================
# 1 ~ 4：存在性 / 触发 / actions / 核心命令
# ============================================================

class TestWorkflowShape:
    def test_1_workflow_file_exists(self) -> None:
        assert _workflow_path().is_file()
        assert _workflow()["name"] == "Observability Matrix Gate"

    def test_2_triggers_are_pull_request_and_push_only(self) -> None:
        document = _workflow()
        # PyYAML 会把 `on` 解析为 True（YAML 1.1 布尔）
        triggers = document.get("on", document.get(True))

        assert triggers is not None
        assert set(triggers) == {"pull_request", "push"}, triggers
        for forbidden in (
            "schedule",
            "workflow_dispatch",
            "release",
            "deployment",
            "repository_dispatch",
        ):
            assert forbidden not in triggers, forbidden

    def test_3_uses_official_checkout_and_setup_python(self) -> None:
        uses = [step.get("uses", "") for step in _steps()]

        assert any(item.startswith("actions/checkout@") for item in uses), uses
        assert any(item.startswith("actions/setup-python@") for item in uses), uses

    def test_4_runs_the_existing_cli(self) -> None:
        assert _CLI_COMMAND in _run_bodies()
        assert f"python {_CLI_COMMAND}" in _run_bodies()


# ============================================================
# 5 ~ 7：不吞失败 / 无刷新参数
# ============================================================

class TestNoFailureSwallowing:
    def test_5_no_continue_on_error(self) -> None:
        for step in _steps():
            assert not step.get("continue-on-error"), step
        assert "continue-on-error" not in _executable_text()

    def test_6_no_truthy_fallback(self) -> None:
        for forbidden in ("|| true", "||true", "exit 0", "if: failure()"):
            assert forbidden not in _run_bodies(), forbidden
            assert forbidden not in _executable_text(), forbidden

    def test_7_no_baseline_refresh_arguments(self) -> None:
        for forbidden in (
            "--refresh",
            "--update-baseline",
            "--db",
            "--project",
            "--environment",
        ):
            assert forbidden not in _run_bodies(), forbidden

    def test_7b_single_gate_only(self) -> None:
        """§二十三：只有一个 gate job，不做 Matrix 分层 CI。"""
        jobs = _workflow()["jobs"]

        assert list(jobs) == ["observability-matrix-gate"], list(jobs)
        # §二十三 禁止的拆分 job 名
        for forbidden in (
            "offline-gate",
            "db-gate",
            "rag-gate",
            "tool-gate",
            "t2s-gate",
            "trace-gate",
            "timeline-gate",
        ):
            assert forbidden not in jobs, forbidden


# ============================================================
# 8 ~ 10：LLM 边界 / 网络边界 / runner
# ============================================================

class TestExecutionEnvironment:
    def test_8_no_llm_credentials(self) -> None:
        text = _workflow_text().lower()

        for forbidden in (
            "deepseek_api_key",
            "siliconflow_api_key",
            "llm_api_key",
            "embedding_api_key",
        ):
            assert forbidden not in text, forbidden

    def test_9_no_llm_or_provider_mentions(self) -> None:
        text = _workflow_text().lower()

        for forbidden in ("openai", "deepseek", "siliconflow"):
            assert forbidden not in text, forbidden

    def test_10_no_outbound_network_tools(self) -> None:
        text = _workflow_text().lower()

        for forbidden in ("curl ", "wget ", "requests", "httpx", "gh api"):
            assert forbidden not in text, forbidden

    def test_10b_runner_is_ubuntu_latest(self) -> None:
        assert _job().get("runs-on") == "ubuntu-latest"

    def test_10c_python_version_matches_project_runtime(self) -> None:
        """不凭空选择版本：与当前解释器主次版本一致。"""
        expected = f"{sys.version_info.major}.{sys.version_info.minor}"
        setup = next(
            step for step in _steps() if "actions/setup-python@" in step.get("uses", "")
        )

        assert setup["with"]["python-version"] == expected, (
            setup["with"]["python-version"],
            expected,
        )


# ============================================================
# PostgreSQL 服务 / 依赖安装
# ============================================================

class TestDatabaseService:
    def test_postgres_service_is_ephemeral_and_matches_project(self) -> None:
        services = _job().get("services") or {}

        assert list(services) == ["postgres"], list(services)
        postgres = services["postgres"]

        assert postgres["image"] == "pgvector/pgvector:pg16", postgres["image"]

    def test_database_url_points_to_ci_service_only(self) -> None:
        env = _job().get("env") or {}
        url = env.get("DATABASE_URL", "")

        assert url.startswith("postgresql+psycopg://"), url
        assert "@localhost:" in url, url          # 仅 CI 临时实例
        for forbidden in ("@", "@prod", "amazonaws", "azure", "neon.tech", "supabase"):
            if forbidden == "@":
                continue
            assert forbidden not in url, forbidden

    def test_dependencies_use_existing_requirements(self) -> None:
        bodies = _run_bodies()

        assert "-r requirements.txt" in bodies
        assert (_REPO_ROOT / "requirements.txt").is_file()
        for forbidden in ("pip install httpx", "pip install requests", "poetry install"):
            assert forbidden not in bodies, forbidden

    def test_schema_step_uses_existing_init_db(self) -> None:
        bodies = _run_bodies()

        assert "backend.app.db.init_db" in bodies
        assert (_REPO_ROOT / "backend/app/db/init_db.py").is_file()


# ============================================================
# §十九 / §二十 / §二十二：安全与 baseline 只读
# ============================================================

class TestSecurityBoundary:
    def test_no_credentials_in_executable_content(self) -> None:
        content = _executable_text().lower()

        for forbidden in ("api_key", "authorization", "password"):
            assert forbidden not in content, forbidden

    def test_no_github_api_or_token(self) -> None:
        text = _workflow_text()

        for forbidden in (
            "GITHUB_TOKEN",
            "github.rest",
            "api.github.com",
            "gh pr",
            "gh api",
            "actions/github-script",
        ):
            assert forbidden not in text, forbidden

    def test_no_baseline_write(self) -> None:
        text = _workflow_text()

        for forbidden in (
            "MATRIX_EXECUTION_BASELINE =",
            "json.dump",
            "refresh baseline",
            "update baseline",
            "write baseline",
            "save baseline",
        ):
            assert forbidden not in text, forbidden

    def test_workflow_does_not_reimplement_logic(self) -> None:
        """YAML 中不得出现判断 / 映射逻辑（全部由项目代码完成）。"""
        bodies = _run_bodies().lower()

        for forbidden in ("if current", "!=", "drift:", "status:", "exit_code"):
            assert forbidden not in bodies, forbidden

    def test_yaml_is_loadable(self) -> None:
        try:
            document = yaml.safe_load(_workflow_text())
        except Exception as exc:  # noqa: BLE001 —— YAML 语法错误应直接失败
            pytest.fail(f"workflow YAML 不可解析: {exc}")

        assert isinstance(document, dict)
