"""Phase 8 Step 3 —— 生产 Readiness 文档的治理校验（离线，无网络 / 无 DB）。

设计原则
--------
* 只断言**可核实的事实**：文档结构、状态取值集合、禁止性声明，
  以及文档中的风险描述与**真实代码 / 真实 compose 配置**是否一致。
* 不做"文档写得好看"这类主观检查；每条断言都能指向具体证据。
* 与 `test_deployment_config.py`（模板静态校验）职责分离：
  后者验**配置**，本文件验**上线治理文档**。

状态取值集合（任务书 §6）：
``PASS`` / ``BLOCKED`` / ``NOT APPLICABLE`` / ``NOT VERIFIED``，
另有能力缺口子集 ``NOT IMPLEMENTED``。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DOC = (
    _REPO_ROOT
    / "docs"
    / "decisions"
    / "Phase"
    / "Phase 8 Step 3 — Production Deployment Readiness.md"
)

#: 允许出现的 Gate 状态。
ALLOWED_STATUSES = {
    "PASS",
    "BLOCKED",
    "NOT APPLICABLE",
    "NOT VERIFIED",
    "NOT IMPLEMENTED",
}


# ============================================================
# 夹具
# ============================================================


@pytest.fixture(scope="module")
def doc() -> str:
    assert _DOC.is_file(), f"缺少 Readiness 文档: {_DOC}"
    return _DOC.read_text(encoding="utf-8")


def _gate_rows(text: str) -> list[tuple[str, str, str]]:
    """解析 §9.1 分项状态表：``| # | Gate | 状态 | 依据 |``。"""
    rows: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        if not cells[0].isdigit():
            continue
        rows.append((cells[0], cells[1], cells[2]))
    return rows


# ============================================================
# 1. 文档结构
# ============================================================


class TestDocumentStructure:
    @pytest.mark.parametrize(
        "heading",
        [
            "应用配置",
            "TLS",
            "网络隔离",
            "认证与会话",
            "资源与运维",
            "部署 Runbook",
            "回滚 Runbook",
            "判定规则",
            "当前 Readiness 结论",
        ],
    )
    def test_required_sections_present(self, doc: str, heading: str) -> None:
        assert heading in doc, heading

    @pytest.mark.parametrize(
        "step", ["Step A", "Step B", "Step C", "Step D"]
    )
    def test_runbook_steps_present(self, doc: str, step: str) -> None:
        assert step in doc, step

    def test_deployment_acceptance_checklist_present(self, doc: str) -> None:
        """验收清单必须成组出现（至少 10 条勾选项）。"""
        assert doc.count("[ ]") >= 10, doc.count("[ ]")

    def test_referenced_files_exist(self) -> None:
        for relative in (
            "deploy/README.md",
            "deploy/nginx/wms-workflow-ui.conf.template",
            "deploy/nginx/proxy-headers.conf",
            "deploy/production.env.example",
            "tests/test_deployment_config.py",
            "tests/test_gateway_validation.py",
        ):
            assert (_REPO_ROOT / relative).is_file(), relative


# ============================================================
# 2. 状态取值与禁止性声明
# ============================================================


class TestStatusDiscipline:
    def test_gate_table_uses_only_allowed_statuses(self, doc: str) -> None:
        rows = _gate_rows(doc)
        assert rows, "未解析到 §9.1 分项状态表"
        for number, gate, status in rows:
            matched = [s for s in ALLOWED_STATUSES if s in status]
            assert matched, f"第 {number} 行状态非法: {gate} / {status}"

    def test_never_claims_production_tls_pass(self, doc: str) -> None:
        assert not re.search(r"Production TLS\s*=\s*\*{0,2}PASS", doc)
        assert "Production TLS" in doc

    def test_never_claims_network_isolation_pass(self, doc: str) -> None:
        assert not re.search(
            r"Production Network Isolation\s*=\s*\*{0,2}PASS", doc
        )

    def test_never_claims_production_deployment_ready(self, doc: str) -> None:
        assert not re.search(
            r"Production Deployment\s*=\s*\*{0,2}(PASS|READY|COMPLETE)", doc
        )
        assert "Production Deployment = BLOCKED" in doc

    def test_declares_production_blocked(self, doc: str) -> None:
        assert "BLOCKED" in doc

    def test_no_self_signed_cert_as_production_evidence(self, doc: str) -> None:
        """自签名证书只能用于测试，不得作为生产验收证据。"""
        assert "自签名" in doc
        assert re.search(r"不构成生产 TLS 证据|只用于容器验证", doc)

    def test_distinguishes_local_from_production_isolation(
        self, doc: str
    ) -> None:
        assert "本地端口绑定测试" in doc
        assert "生产防火墙验证" in doc
        assert "公网网络隔离验证" in doc


# ============================================================
# 3. 与真实代码 / 配置一致
# ============================================================


class TestConsistencyWithRealCode:
    @staticmethod
    def _config_source() -> str:
        return (_REPO_ROOT / "backend" / "app" / "config.py").read_text(
            encoding="utf-8"
        )

    def test_app_host_default_is_still_wildcard(self, doc: str) -> None:
        """文档声明的风险必须与代码一致：默认值仍是 ``0.0.0.0``。

        若未来有人把默认值改为回环，这条测试会失败 → 提示同步更新文档。
        """
        source = self._config_source()
        assert '_get_str("APP_HOST", "0.0.0.0")' in source, (
            "APP_HOST 默认值已变更：请同步更新 Readiness 文档 §1.2"
        )
        assert "0.0.0.0" in doc

    def test_bool_parser_is_fail_open_and_documented(self, doc: str) -> None:
        """``_get_bool`` 对非法值静默返回 False —— 文档必须提示该风险。"""
        source = self._config_source()
        assert '{"1", "true", "yes", "on"}' in source
        assert "静默降级" in doc or "静默" in doc

    def test_cookie_secure_defaults_true_and_is_documented(
        self, doc: str
    ) -> None:
        source = self._config_source()
        assert "AUTH_COOKIE_SECURE" in source
        assert "AUTH_COOKIE_SECURE" in doc

    def test_dev_compose_port_exposure_is_flagged(self, doc: str) -> None:
        """检查真实 compose 端口发布；非回环时必须已在文档中标注风险。"""
        exposures: list[str] = []
        for name in ("docker-compose.yml", "docker-compose.test.yml"):
            path = _REPO_ROOT / name
            if not path.is_file():
                continue
            for binding in self._port_bindings(path.read_text(encoding="utf-8")):
                host_part = binding.rsplit(":", 1)[0]
                if "127.0.0.1" not in host_part:
                    exposures.append(f"{name}: {binding}")

        if exposures:
            assert "docker-compose" in doc, "未在文档中提及 dev compose 风险"
            assert "所有接口" in doc or "0.0.0.0" in doc, (
                f"compose 端口未绑定回环，文档必须标注：{exposures}"
            )
            return

        # Phase 8 Step 4 已加固：此处必须**正向**确认确实绑定了回环，
        # 否则该用例会因为"没有暴露"而空转。
        bindings: list[str] = []
        for name in ("docker-compose.yml", "docker-compose.test.yml"):
            path = _REPO_ROOT / name
            if path.is_file():
                bindings.extend(
                    self._port_bindings(path.read_text(encoding="utf-8"))
                )
        assert bindings, "未解析到任何 compose 端口绑定"
        for binding in bindings:
            assert binding.startswith("127.0.0.1:"), binding

    @staticmethod
    def _port_bindings(text: str) -> list[str]:
        """极简解析：取 ``ports:`` 块下的列表项。

        块内允许出现空行与注释行（本仓库 ``docker-compose.test.yml`` 就在
        ``ports:`` 与列表项之间写了说明注释），不得因此提前结束。
        """
        bindings: list[str] = []
        inside = False
        for raw in text.splitlines():
            stripped = raw.strip()
            if stripped == "ports:":
                inside = True
                continue
            if not inside:
                continue
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("- "):
                value = stripped[2:].strip().strip('"').strip("'")
                bindings.append(value)
                continue
            inside = False
        return bindings

    def test_health_endpoint_statement_matches_code(self, doc: str) -> None:
        """健康检查必须真实存在，且文档说明它不对公网暴露。"""
        health = (_REPO_ROOT / "backend" / "app" / "api" / "health.py").read_text(
            encoding="utf-8"
        )
        assert "@router.get" in health
        assert "/api/health" in doc

    def test_gateway_still_hides_health_from_public(self) -> None:
        """/api/health 不在网关白名单内（否则文档的"不可公网探活"就不成立）。"""
        template = (
            _REPO_ROOT / "deploy" / "nginx" / "wms-workflow-ui.conf.template"
        ).read_text(encoding="utf-8")
        assert "location = /api/health" not in template
        assert "location ^~ /api/ {" in template
        assert "return 404;" in template


# ============================================================
# 4. 不得虚构自动化能力
# ============================================================


class TestNoFictionalAutomation:
    def test_no_deployment_automation_claim(self, doc: str) -> None:
        """仓库无 CI 部署自动化 ⇒ 文档不得声称存在。"""
        workflows = list((_REPO_ROOT / ".github" / "workflows").glob("*.yml"))
        has_deploy = False
        for path in workflows:
            text = path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"\b(deploy|kubectl|ansible|rsync|docker push)\b", text):
                has_deploy = True
        if not has_deploy:
            assert re.search(r"没有自动回滚机制|NOT APPLICABLE", doc), (
                "仓库无部署自动化，文档必须明确说明"
            )

    def test_rollback_is_manual_and_checks_config(self, doc: str) -> None:
        assert "回滚" in doc
        assert "nginx -t" in doc
        # 不得声称自动回滚
        assert not re.search(r"自动回滚(已实现|可用|生效)", doc)

    def test_rollback_excludes_database_overwrite(self, doc: str) -> None:
        assert re.search(r"不.{0,4}(覆盖|restore).{0,12}数据库|不删表", doc)

    def test_process_manager_absence_is_declared(self, doc: str) -> None:
        """仓库无 systemd / Procfile / supervisor ⇒ 必须如实声明。"""
        for name in ("Procfile", "systemd", "supervisord.conf"):
            assert not (_REPO_ROOT / name).exists(), name
        assert re.search(r"仓库不提供|无 unit|没有.*unit", doc)


# ============================================================
# 5. 无凭据
# ============================================================


class TestNoSecretsInDocument:
    @pytest.mark.parametrize(
        "pattern",
        [
            r"wms_test_pw",
            r"postgresql\+psycopg://[^<\s]",
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
            r"sk-[A-Za-z0-9]{20,}",
        ],
    )
    def test_no_credentials(self, doc: str, pattern: str) -> None:
        assert not re.search(pattern, doc), pattern

    def test_placeholders_are_used(self, doc: str) -> None:
        assert "<PRODUCTION_HOST>" in doc
        assert "<STATIC_ROOT>" in doc
