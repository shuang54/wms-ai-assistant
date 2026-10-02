"""Phase 3.12 Step 97 — GitHub Actions workflow **静态审计**（不访问 GitHub）。

覆盖 §十八 1～10 + §十九/§二十/§二十二/§二十三 边界：
文件唯一 · 单一 job · 触发条件 · checkout/setup-python · 核心命令 · 不吞失败 ·
无刷新参数 · 无 LLM 凭据 · 无外部调用 · 无 secrets · baseline 只读 ·
无 GitHub API · 单一 gate · 无 permissions / cache / retry / matrix 分层。

Step 98 增补（**只审计 Workflow，不修改 Gate / baseline / 生产代码**）：
真实 Run 结果属于外部事实，本文件只锁定"Workflow 形状"与"审计文档已记录"，
不联网、不读取 GitHub API。

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


# ============================================================
# Phase 3.12 Step 98 — 首次真实运行审计（**不联网**，只锁形状 / 记录）
# ============================================================

#: Step 98 观察到的真实 GitHub Actions Run（**外部事实快照，只读**）。
#: 用途：文档漂移守卫 —— 防止审计记录被静默删除；**不**用于自动修复或改写结论。
STEP98_REAL_RUN: dict[str, str] = {
    "run_id": "36859788376",
    "run_number": "1",
    "commit": "559ce63876c8f9b21a71ff4eba610227a74d25a2",
    "trigger": "push",
    "workflow": "Observability Matrix Gate",
    "job": "Observability Matrix Gate",
}

#: Step 98 审计文档（真实 Run 记录落点）。
_STEP98_DOC = "docs/evaluation/phase-3.12-step-98-github-actions-run-audit.md"

#: Step 100 冻结的 **真实 GitHub Evidence**（只读快照；Run ≠ Baseline）。
#:    Run #2 = Step 99 代码修复 · Run #3 = Step 99 文档补录（均 success）
STEP100_REAL_RUNS: dict[str, dict[str, str]] = {
    "2": {
        "run_id": "36949287263",
        "commit": "73b3c71eacaeaff4d432453f39e73c7a75b8ef2b",
        "result": "success",
    },
    "3": {
        "run_id": "36949724371",
        "commit": "510ab81caf4d56152bbaa4f71be54702b94a99cc",
        "result": "success",
    },
}

#: Step 100 冻结文档。
_STEP100_DOC = "docs/evaluation/phase-3.12-step-100-ci-gate-contract.md"

#: 生产代码目录（**Run ID 不得出现**；Evidence 只属于 docs / tests）。
_PRODUCTION_CODE_DIRS: tuple[str, ...] = ("backend", "scripts", ".github")


class TestStep98WorkflowUniqueness:
    """§十六：唯一 workflow / 唯一 job / 无额外触发与权限。"""

    def test_workflow_is_the_only_workflow_file(self) -> None:
        directory = _REPO_ROOT / ".github" / "workflows"

        assert directory.is_dir()
        files = sorted(
            path.name
            for path in directory.iterdir()
            if path.is_file() and path.suffix in {".yml", ".yaml"}
        )
        assert files == ["observability-matrix-gate.yml"], files

    def test_workflow_declares_no_permissions(self) -> None:
        """§十三：不为本阶段引入权限体系（保持默认最小权限）。"""
        document = _workflow()

        assert "permissions" not in document, "不得新增 permissions 配置"
        assert "permissions" not in _job(), "job 级 permissions 同样禁止"

    def test_workflow_uses_no_secrets_context(self) -> None:
        assert "secrets." not in _workflow_text()

    def test_no_cache_retry_or_matrix_strategy(self) -> None:
        """§十四：不做性能优化（无 cache / 无 retry / 无 matrix 分层）。"""
        text = _workflow_text().lower()

        for forbidden in (
            "actions/cache",
            "strategy:",
            "matrix:",
            "timeout-minutes",
            "retry",
        ):
            assert forbidden not in text, forbidden


class TestStep98RunAuditRecord:
    """真实 Run 属于外部事实：只保证**记录存在且未被静默删除**（离线）。"""

    def test_step98_audit_document_exists(self) -> None:
        document = _REPO_ROOT / _STEP98_DOC

        assert document.is_file(), document

    def test_step98_audit_document_records_the_real_run(self) -> None:
        content = (_REPO_ROOT / _STEP98_DOC).read_text(encoding="utf-8")

        assert STEP98_REAL_RUN["run_id"] in content
        assert STEP98_REAL_RUN["commit"] in content
        assert STEP98_REAL_RUN["run_number"] in content

    def test_step98_real_run_snapshot_is_stable(self) -> None:
        """快照键集固定（新增字段需显式授权）。"""
        assert set(STEP98_REAL_RUN) == {
            "run_id",
            "run_number",
            "commit",
            "trigger",
            "workflow",
            "job",
        }
        assert STEP98_REAL_RUN["workflow"] == _workflow()["name"]
        assert STEP98_REAL_RUN["job"] == _job()["name"]


class TestStep100GateContractFreeze:
    """§四～§十四：冻结 CI Gate Contract（**不新增** Gate / Adapter / Workflow）。"""

    def test_step100_evidence_snapshot_is_stable(self) -> None:
        """Run #2 / #3 冻结为 Evidence（键集固定；新增需显式授权）。"""
        assert set(STEP100_REAL_RUNS) == {"2", "3"}
        for snapshot in STEP100_REAL_RUNS.values():
            assert set(snapshot) == {"run_id", "commit", "result"}
            assert snapshot["result"] == "success"

    def test_run_ids_never_enter_production_code(self) -> None:
        """§九：Run ID 只写 Evaluation 文档 —— 生产代码 / Workflow 中不得出现。"""
        run_ids = [snapshot["run_id"] for snapshot in STEP100_REAL_RUNS.values()]
        run_ids.append(STEP98_REAL_RUN["run_id"])

        offenders: list[str] = []
        for directory in _PRODUCTION_CODE_DIRS:
            root = _REPO_ROOT / directory
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix not in {".py", ".yml", ".yaml"}:
                    continue
                content = path.read_text(encoding="utf-8", errors="ignore")
                for run_id in run_ids:
                    if run_id in content:
                        offenders.append(f"{path.as_posix()}:{run_id}")
        assert offenders == [], offenders

    def test_workflow_path_and_job_are_frozen(self) -> None:
        """§八：workflow 路径 / 唯一 job 名冻结。"""
        assert (_REPO_ROOT / _WORKFLOW).is_file()
        assert list(_workflow()["jobs"]) == ["observability-matrix-gate"]
        assert _job()["name"] == "Observability Matrix Gate"

    def test_step100_document_records_the_evidence(self) -> None:
        """冻结文档存在且记录 Run #2 / #3（防文档被静默删除）。"""
        content = (_REPO_ROOT / _STEP100_DOC).read_text(encoding="utf-8")

        for snapshot in STEP100_REAL_RUNS.values():
            assert snapshot["run_id"] in content
            # 文档使用短 SHA（与 git log 一致）
            assert snapshot["commit"][:7] in content

    def test_gate_chain_is_not_reimplemented_in_workflow(self) -> None:
        """§四：Workflow 只调用既有 CLI，不重新实现 Gate / Adapter / baseline。"""
        bodies = _run_bodies()

        assert bodies.count("python scripts/run_matrix_gate.py") == 1
        for forbidden in (
            "evaluate_matrix_baseline_gate",
            "adapt_gate_result_to_exit_code",
            "MATRIX_EXECUTION_BASELINE",
        ):
            assert forbidden not in bodies, forbidden


class TestStep101GovernanceReadiness:
    """Step 101 §八/§十/§十一：**只读**审计 Required Check 就绪条件。"""

    def test_required_check_name_is_stable_and_unique(self) -> None:
        """§八：Required Check 名称来自 job name ⇒ 必须唯一且稳定。"""
        jobs = _workflow()["jobs"]

        assert list(jobs) == ["observability-matrix-gate"], list(jobs)
        names = [job.get("name", key) for key, job in jobs.items()]
        assert names == ["Observability Matrix Gate"], names
        assert len(set(names)) == 1                     # 无同名 job（结果歧义）

    def test_triggers_support_push_and_pull_request(self) -> None:
        """§十：PR Required Check 需要 push / pull_request；不得只有手动/定时触发。"""
        document = _workflow()
        triggers = document.get("on", document.get(True))

        assert {"push", "pull_request"} <= set(triggers), triggers
        for forbidden in ("schedule", "workflow_dispatch", "merge_group"):
            assert forbidden not in triggers, forbidden

    def test_workflow_has_no_conditional_bypass(self) -> None:
        """§十三：不得存在条件跳过（gate 必须无条件执行）。"""
        document = _workflow()

        assert "if" not in _job(), "job 级条件跳过禁止"
        for step in _steps():
            assert "if" not in step, step.get("name", step)


#: Step 103 §三：治理审计目标（只读；不得用于任何写操作）。
GOVERNANCE_TARGET: dict[str, str] = {
    "owner": "shuang54",
    "repo": "wms-ai-assistant",
    "branch": "main",
}

#: Step 103 §九：未来若被配置为 Required Check，其 check 名必须等于 job name。
STEP103_REQUIRED_CHECK_NAME = "Observability Matrix Gate"


class TestStep103GovernanceIdentity:
    """Step 103 §九：check 名 / 目标分支身份（**不创建** mock governance system）。"""

    def test_governance_target_is_frozen(self) -> None:
        assert GOVERNANCE_TARGET == {
            "owner": "shuang54",
            "repo": "wms-ai-assistant",
            "branch": "main",
        }
        assert GOVERNANCE_TARGET["branch"] == "main"

    def test_required_check_name_equals_the_job_name(self) -> None:
        """Required Check 名来自 job name ⇒ 必须与 workflow 中声明一致且唯一。"""
        assert STEP103_REQUIRED_CHECK_NAME == _job()["name"]
        assert STEP103_REQUIRED_CHECK_NAME == "Observability Matrix Gate"
        assert len(_workflow()["jobs"]) == 1


#: Step 106：Merge Queue 就绪契约（**只读快照**；不代表代码失败）。
MERGE_QUEUE_READINESS_CONTRACT: dict[str, str] = {
    "merge_queue": "NOT ENABLED",           # Ruleset 24348464 无 merge_queue 规则
    "workflow_merge_group": "absent",       # 当前 workflow 未声明 merge_group
    "required_check": STEP103_REQUIRED_CHECK_NAME,
    "readiness": "NOT READY FOR MERGE_QUEUE",
}


class TestStep106MergeQueueReadiness:
    """Step 106 §九/§十：Merge Queue 触发就绪只读审计（不修改 Workflow）。"""

    def test_workflow_triggers_are_push_and_pull_request_only(self) -> None:
        document = _workflow()
        triggers = document.get("on", document.get(True))

        assert set(triggers) == {"push", "pull_request"}, set(triggers)
        assert "merge_group" not in triggers

    def test_merge_queue_readiness_contract_is_frozen(self) -> None:
        assert MERGE_QUEUE_READINESS_CONTRACT == {
            "merge_queue": "NOT ENABLED",
            "workflow_merge_group": "absent",
            "required_check": "Observability Matrix Gate",
            "readiness": "NOT READY FOR MERGE_QUEUE",
        }
        # 契约必须与真实 workflow 一致（无 merge_group ⇒ 不满足 Merge Queue 触发要求）
        assert (
            "merge_group" not in _workflow_text()
        ), "未启用 Merge Queue 前不得添加 merge_group 触发"


#: Step 107：Merge Queue **决策契约**（只读；**不**表达"未来一定启用"）。
MERGE_QUEUE_DECISION_CONTRACT: dict[str, object] = {
    "current_enabled": False,               # Ruleset 无 merge_queue 规则
    "current_workflow_merge_group": False,  # Workflow 未声明 merge_group
    "required_check": STEP103_REQUIRED_CHECK_NAME,
    "future_required_change": "add merge_group trigger",
    "decision": "DEFER",                    # 无事实证据表明当前需要 Merge Queue
}
#: 决策依据缺失的维度（**无真实数据** ⇒ UNKNOWN，不猜测）。
MERGE_QUEUE_UNKNOWN_DIMENSIONS: tuple[str, ...] = (
    "concurrent_pull_requests",
    "merge_conflicts",
    "post_merge_main_instability",
)


class TestStep107MergeQueueDecision:
    """Step 107 §九：Merge Queue 启用决策契约（只读；不改 Workflow / Ruleset）。"""

    def test_decision_contract_is_frozen(self) -> None:
        assert MERGE_QUEUE_DECISION_CONTRACT["current_enabled"] is False
        assert MERGE_QUEUE_DECISION_CONTRACT["current_workflow_merge_group"] is False
        assert MERGE_QUEUE_DECISION_CONTRACT["required_check"] == (
            STEP103_REQUIRED_CHECK_NAME
        )
        assert MERGE_QUEUE_DECISION_CONTRACT["future_required_change"] == (
            "add merge_group trigger"
        )
        assert MERGE_QUEUE_DECISION_CONTRACT["decision"] == "DEFER"
        # 决策必须与真实 workflow / Ruleset 事实一致
        assert "merge_group" not in _workflow_text()
        assert len(_workflow()["jobs"]) == 1

    def test_decision_does_not_promise_future_enablement(self) -> None:
        """契约不得写死"未来一定启用"（只记录决策 = DEFER 与最小改动方向）。"""
        text = " ".join(str(value) for value in MERGE_QUEUE_DECISION_CONTRACT.values())

        for forbidden in ("ENABLE", "WILL ENABLE", "MUST ENABLE"):
            assert forbidden not in text, forbidden
        assert MERGE_QUEUE_UNKNOWN_DIMENSIONS == (
            "concurrent_pull_requests",
            "merge_conflicts",
            "post_merge_main_instability",
        )


#: Step 108：**CI Governance Contract**（只读冻结：谁负责 / 叫什么 / 保护什么 / 是否启用）。
CI_GOVERNANCE_CONTRACT: dict[str, object] = {
    "workflow_name": "Observability Matrix Gate",
    "job_name": "observability-matrix-gate",
    "required_check": "Observability Matrix Gate",
    "required_branch": "main",
    "merge_queue_enabled": False,
    "merge_group_trigger": False,
    "decision": "DEFER",
}

#: 未来启用 Merge Queue 的**前置条件**（本阶段不执行，仅冻结语义）。
FUTURE_MERGE_QUEUE_REQUIRES_SEPARATE_ENABLEMENT = True

#: 禁止出现的"第二套 check 名"（避免歧义 / 与 GitHub required check 名不一致）。
_FORBIDDEN_SECOND_CHECK_NAMES: tuple[str, ...] = (
    "CI Gate",
    "Merge Gate",
    "Observability Gate",
    "Merge Queue Gate",
)

#: Workflow 只允许承担的步骤（职责边界，§十）。
_EXPECTED_STEP_NAMES: tuple[str, ...] = (
    "Checkout",
    "Setup Python",
    "Install dependencies",
    "Initialize database schema",
    "Run observability matrix gate",
)


class TestStep108CIGovernanceContract:
    """Step 108：冻结 CI Governance（Ruleset → Required Check → Workflow 边界）。"""

    def test_ci_governance_contract_is_frozen(self) -> None:
        assert CI_GOVERNANCE_CONTRACT == {
            "workflow_name": "Observability Matrix Gate",
            "job_name": "observability-matrix-gate",
            "required_check": "Observability Matrix Gate",
            "required_branch": "main",
            "merge_queue_enabled": False,
            "merge_group_trigger": False,
            "decision": "DEFER",
        }
        # 与真实 workflow 事实一致
        assert CI_GOVERNANCE_CONTRACT["workflow_name"] == _workflow()["name"]
        assert list(_workflow()["jobs"]) == [CI_GOVERNANCE_CONTRACT["job_name"]]

    def test_decision_does_not_promise_future_merge_queue(self) -> None:
        text = " ".join(str(value) for value in CI_GOVERNANCE_CONTRACT.values()).lower()

        for forbidden in ("will_enable", "must_enable", "enable_next"):
            assert forbidden not in text, forbidden
        assert CI_GOVERNANCE_CONTRACT["decision"] == "DEFER"
        assert FUTURE_MERGE_QUEUE_REQUIRES_SEPARATE_ENABLEMENT is True

    def test_required_check_identity_is_a_single_name(self) -> None:
        names = [job.get("name") for job in _workflow()["jobs"].values()]

        assert names == [CI_GOVERNANCE_CONTRACT["required_check"]]
        assert STEP103_REQUIRED_CHECK_NAME == CI_GOVERNANCE_CONTRACT["required_check"]
        for forbidden in _FORBIDDEN_SECOND_CHECK_NAMES:
            assert forbidden not in _workflow_text(), forbidden

    def test_branch_scope_is_main_only(self) -> None:
        assert CI_GOVERNANCE_CONTRACT["required_branch"] == GOVERNANCE_TARGET["branch"]
        assert CI_GOVERNANCE_CONTRACT["required_branch"] == "main"
        # 不得扩大为通配 / 全分支
        for value in CI_GOVERNANCE_CONTRACT.values():
            assert value not in ("*", "all branches", "refs/heads/*"), value

    def test_merge_queue_boundary_is_frozen(self) -> None:
        assert CI_GOVERNANCE_CONTRACT["merge_queue_enabled"] is False
        assert CI_GOVERNANCE_CONTRACT["merge_group_trigger"] is False
        assert "merge_group" not in _workflow_text()
        # 当前 CI 在没有 merge_group 的情况下依然有效
        document = _workflow()
        triggers = document.get("on", document.get(True))
        assert set(triggers) == {"push", "pull_request"}

    def test_governance_contract_does_not_duplicate_gate_logic(self) -> None:
        """治理契约只描述归属，不复制 PASS / DRIFT / exit code / baseline 逻辑。"""
        text = " ".join(str(value) for value in CI_GOVERNANCE_CONTRACT.values())

        for forbidden in ("PASS", "DRIFT", "exit_code", "baseline", "residue"):
            assert forbidden not in text, forbidden

    def test_workflow_ownership_is_boundary_only(self) -> None:
        """Workflow 只做 checkout → setup → install → init_db → CLI（不判断 PASS/DRIFT）。"""
        assert tuple(step.get("name") for step in _steps()) == _EXPECTED_STEP_NAMES
        bodies = _run_bodies()

        assert "backend.app.db.init_db" in bodies
        assert "scripts/run_matrix_gate.py" in bodies
        for forbidden in (
            "evaluate_matrix_baseline_gate",
            "adapt_gate_result_to_exit_code",
            "MATRIX_EXECUTION_BASELINE",
            "PASS",
            "DRIFT",
        ):
            assert forbidden not in bodies, forbidden


#: Step 109：Self-Consistency Audit 的**禁用依赖**（离线可运行的要求）。
_FORBIDDEN_AUDIT_DEPENDENCIES: tuple[str, ...] = (
    "requests",
    "httpx",
    "urllib",
    "aiohttp",
    "github",
    "PyGithub",
    "GITHUB_TOKEN",
    "GH_TOKEN",
)

#: Governance Contract 不得承载的 Gate / Adapter 语义 token。
_FORBIDDEN_GOVERNANCE_TOKENS: tuple[str, ...] = (
    "PASS",
    "DRIFT",
    "exit_code",
    "MATRIX_EXECUTION_BASELINE",
    "evaluate_matrix_baseline_gate",
    "adapt_gate_result_to_exit_code",
)


def run_governance_self_consistency_audit() -> dict[str, bool]:
    """**离线**自洽审计（Step 109）：Contract ↔ 本地 Workflow ↔ 测试契约。

    不访问 GitHub API、不读环境变量、不做任何写操作；返回逐项结论（不修改契约）。
    """
    document = _workflow()
    triggers = document.get("on", document.get(True))
    bodies = _run_bodies()
    contract_text = " ".join(str(value) for value in CI_GOVERNANCE_CONTRACT.values())

    return {
        "workflow_name": (
            CI_GOVERNANCE_CONTRACT["workflow_name"] == document["name"]
        ),
        "job_id": list(document["jobs"]) == [CI_GOVERNANCE_CONTRACT["job_name"]],
        "job_name": (
            _job()["name"] == CI_GOVERNANCE_CONTRACT["required_check"]
        ),
        "triggers": (
            "push" in triggers
            and "pull_request" in triggers
            and "merge_group" not in triggers
        ),
        "required_check_single_name": all(
            forbidden not in _workflow_text()
            for forbidden in _FORBIDDEN_SECOND_CHECK_NAMES
        ),
        "branch_scope": CI_GOVERNANCE_CONTRACT["required_branch"] == "main",
        "merge_queue_boundary": (
            CI_GOVERNANCE_CONTRACT["merge_queue_enabled"] is False
            and CI_GOVERNANCE_CONTRACT["merge_group_trigger"] is False
            and CI_GOVERNANCE_CONTRACT["decision"] == "DEFER"
            and "merge_group" not in _workflow_text()
        ),
        "governance_boundary": (
            all(token not in contract_text for token in _FORBIDDEN_GOVERNANCE_TOKENS)
            and all(token not in bodies for token in _FORBIDDEN_GOVERNANCE_TOKENS)
        ),
    }


class TestStep109SelfConsistency:
    """Step 109：CI Governance Self-Consistency Audit（离线 · 无 GitHub API 依赖）。"""

    def test_workflow_and_job_identity_match_contract(self) -> None:
        document = _workflow()

        assert CI_GOVERNANCE_CONTRACT["workflow_name"] == document["name"]
        assert list(document["jobs"]) == [CI_GOVERNANCE_CONTRACT["job_name"]]
        assert _job()["name"] == CI_GOVERNANCE_CONTRACT["required_check"]

    def test_trigger_identity_matches_contract(self) -> None:
        document = _workflow()
        triggers = document.get("on", document.get(True))

        assert "push" in triggers and "pull_request" in triggers
        assert "merge_group" not in triggers
        assert CI_GOVERNANCE_CONTRACT["merge_group_trigger"] is False
        # 不得把"未来 merge_group 支持"误判为当前 trigger
        assert "merge_group" not in _workflow_text()

    def test_branch_scope_is_not_expanded(self) -> None:
        """Ruleset 作用域（main）与 Workflow 触发（分支无关）是两个概念，不得混淆。"""
        assert CI_GOVERNANCE_CONTRACT["required_branch"] == "main"
        assert GOVERNANCE_TARGET["branch"] == "main"
        # Workflow 侧本就不声明分支范围（无 branches 过滤）—— 不因此扩大 Ruleset 作用域
        for step in _steps():
            assert "branches" not in step, step.get("name", step)
        for value in CI_GOVERNANCE_CONTRACT.values():
            assert value not in ("*", "all branches", "refs/heads/*"), value

    def test_ownership_boundary_has_no_gate_semantics(self) -> None:
        contract_text = " ".join(
            str(value) for value in CI_GOVERNANCE_CONTRACT.values()
        )
        bodies = _run_bodies()

        for token in _FORBIDDEN_GOVERNANCE_TOKENS:
            assert token not in contract_text, token
            assert token not in bodies, token

    def test_merge_queue_boundary_is_self_consistent(self) -> None:
        assert CI_GOVERNANCE_CONTRACT["merge_queue_enabled"] is False
        assert CI_GOVERNANCE_CONTRACT["merge_group_trigger"] is False
        assert CI_GOVERNANCE_CONTRACT["decision"] == "DEFER"
        # 不得由 DEFER 推导出 future enablement
        for key in ("future_enablement", "will_enable", "must_enable"):
            assert key not in CI_GOVERNANCE_CONTRACT, key

    def test_audit_has_no_dynamic_github_dependency(self) -> None:
        """本审计必须完全离线：**AST 级**检查（不做全文字符串扫描）。

        允许文本中出现 "requests" / "httpx" 这类**被禁止 token 的清单**（见
        ``test_10_no_outbound_network_tools``）；判据是"是否真的 import / 调用"。
        """
        import ast  # 局部 import：仅用于本审计的静态检查

        source_path = _REPO_ROOT / "tests" / "test_github_actions_matrix_gate.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        modules: set[str] = set()
        attributes: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module.split(".")[0])
            elif isinstance(node, ast.Attribute):
                attributes.add(node.attr)

        for forbidden in _FORBIDDEN_AUDIT_DEPENDENCIES:
            root = forbidden.split(".")[0]
            if forbidden.isupper():                      # Token 常量名（非模块）
                assert forbidden not in attributes, forbidden
                continue
            assert root not in modules, (root, sorted(modules))
        # 离线要求：不读环境变量（不做 token / 凭据探测）
        assert "os" not in modules
        assert "environ" not in attributes
        assert "getenv" not in attributes

    def test_contract_is_immutable_across_audit(self) -> None:
        before = dict(CI_GOVERNANCE_CONTRACT)

        findings = run_governance_self_consistency_audit()
        run_governance_self_consistency_audit()  # 幂等：重复执行结果一致

        assert dict(CI_GOVERNANCE_CONTRACT) == before
        assert all(findings.values()), findings
        assert run_governance_self_consistency_audit() == findings


#: Step 109 / 110：CI Governance 交付物（**candidate commit set**，Step 110 只分类不提交）。
STEP_108_109_PATHS: frozenset[str] = frozenset(
    {
        "tests/test_github_actions_matrix_gate.py",
        "docs/evaluation/phase-3.12-ci-governance-contract.md",
        "docs/evaluation/phase-3.12-ci-governance-self-consistency.md",
    }
)
#: 任务说明副本所在目录（TASK_COPY；不自动纳入 candidate commit set）。
_TASK_COPY_PREFIX = "docs/decisions/"
#: 历史步骤（已合入 origin/main）的 evaluation 文档前缀。
_HISTORICAL_EVALUATION_PREFIX = "docs/evaluation/phase-3.12-step-"


def classify_ci_governance_change(path: str) -> str:
    """按**路径**分类工作区变更（Step 110 §九）。

    只允许四种取值：``STEP_108_109`` · ``HISTORICAL`` · ``TASK_COPY`` · ``OTHER``。

    **不依据 commit message / git 历史**（纯路径判定，无 subprocess / 无 git 调用），
    以免"猜"历史归属。
    """
    normalized = path.replace("\\", "/").strip().strip('"')

    if normalized in STEP_108_109_PATHS:
        return "STEP_108_109"
    if normalized.startswith(_TASK_COPY_PREFIX):
        return "TASK_COPY"
    if normalized.startswith(_HISTORICAL_EVALUATION_PREFIX):
        return "HISTORICAL"
    return "OTHER"


class TestStep110CommitBoundary:
    """Step 110：Commit Boundary Audit（**只分类，不 commit / 不 push**）。"""

    def test_candidate_commit_set_is_exactly_three_paths(self) -> None:
        assert STEP_108_109_PATHS == {
            "tests/test_github_actions_matrix_gate.py",
            "docs/evaluation/phase-3.12-ci-governance-contract.md",
            "docs/evaluation/phase-3.12-ci-governance-self-consistency.md",
        }

    def test_classifier_maps_known_artifacts(self) -> None:
        cases = {
            "tests/test_github_actions_matrix_gate.py": "STEP_108_109",
            "docs/evaluation/phase-3.12-ci-governance-contract.md": "STEP_108_109",
            "docs/evaluation/phase-3.12-ci-governance-self-consistency.md": (
                "STEP_108_109"
            ),
            "docs/decisions/Phase/Phase 3.12 Step 109 — CI Governance Self-Consistency Audit.md": (
                "TASK_COPY"
            ),
            "docs/decisions/Phase/Phase 3.12 Step 110 — CI Governance Commit Boundary Audit.md": (
                "TASK_COPY"
            ),
            "docs/evaluation/phase-3.12-step-104-required-check.md": "HISTORICAL",
            "docs/evaluation/phase-3.12-step-107-merge-queue-decision.md": "HISTORICAL",
            ".github/workflows/observability-matrix-gate.yml": "OTHER",
            "backend/app/main.py": "OTHER",
        }
        for path, expected in cases.items():
            assert classify_ci_governance_change(path) == expected, (path, expected)

    def test_classifier_is_path_based_only(self) -> None:
        """分类器不得依赖 git / commit message（AST 检查函数体内无进程调用）。"""
        import ast  # 局部 import：仅用于本审计

        source = (_REPO_ROOT / "tests" / "test_github_actions_matrix_gate.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        target = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "classify_ci_governance_change"
        )
        used = {
            node.id for node in ast.walk(target) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(target) if isinstance(node, ast.Attribute)
        }

        for forbidden in ("subprocess", "os", "git", "check_output", "commit_message"):
            assert forbidden not in used, forbidden

    def test_known_artifacts_never_classify_as_other(self) -> None:
        """§十：若出现 OTHER ⇒ COMMIT BLOCKED（此处覆盖已知交付物，必须无 OTHER）。"""
        known = tuple(STEP_108_109_PATHS) + (
            "docs/decisions/Phase/Phase 3.12 Step 109 — CI Governance Self-Consistency Audit.md",
            "docs/decisions/Phase/Phase 3.12 Step 110 — CI Governance Commit Boundary Audit.md",
        )

        labels = {classify_ci_governance_change(path) for path in known}
        assert "OTHER" not in labels, labels
        assert labels <= {"STEP_108_109", "TASK_COPY", "HISTORICAL"}
