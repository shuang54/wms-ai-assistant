"""Phase 8 Step 4 —— P0 部署配置加固的测试（离线；Docker 部分可跳过）。

覆盖四块：

A. **安全关键布尔解析**（``AUTH_COOKIE_SECURE``）：合法值、非法值必须失败、
   未设置 / 空值取默认、异常信息不得回显取值。
B. **生产监听地址边界**：``APP_ENV=production`` 时 ``APP_HOST`` 必须是回环，
   否则构造 ``Settings`` 即失败；非生产环境行为不变。
C. **Compose 端口隔离**：两份 compose 的 PostgreSQL 发布端口必须绑
   ``127.0.0.1``；用 ``docker compose config`` 做**真实解析**（不可用时 skip）。
D. **Runbook 破坏性操作防护**：``rsync --delete`` 前必须有 dry-run、
   数据库数据目录不得进入删除范围、``git checkout`` 前必须校验工作区。

原则：能解析就解析，不用字符串包含代替结构判断；环境不具备时 skip，不冒充 PASS。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from backend.app.config import (  # noqa: E402
    _get_bool,
    _get_bool_strict,
    _validate_production_binding,
)

_DEPLOY_README = _REPO_ROOT / "deploy" / "README.md"
_READINESS = (
    _REPO_ROOT
    / "docs"
    / "decisions"
    / "Phase"
    / "Phase 8 Step 3 — Production Deployment Readiness.md"
)
_COMPOSE_FILES = (
    _REPO_ROOT / "docker-compose.yml",
    _REPO_ROOT / "docker-compose.test.yml",
)

__all__ = [
    "TestStrictBooleanParsing",
    "TestProductionBindingBoundary",
    "TestComposePortIsolation",
    "TestRunbookDestructiveSafety",
]


# ============================================================
# A. 严格布尔解析
# ============================================================


class TestStrictBooleanParsing:
    @pytest.mark.parametrize(
        "raw", ["true", "TRUE", "True", "1", "yes", "YES", "on", " on ", "on\t"]
    )
    def test_true_tokens(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        monkeypatch.setenv("WMS_TEST_BOOL", raw)
        assert _get_bool_strict("WMS_TEST_BOOL", False) is True

    @pytest.mark.parametrize(
        "raw", ["false", "FALSE", "0", "no", "off", " off ", "No"]
    )
    def test_false_tokens(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        monkeypatch.setenv("WMS_TEST_BOOL", raw)
        assert _get_bool_strict("WMS_TEST_BOOL", True) is False

    @pytest.mark.parametrize(
        "raw", ["flase", "enabled", "abc", "2", "-1", "tru", "none", "null", ":"]
    )
    def test_invalid_values_raise(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        """拼写错误**必须**抛错，不得静默降级为 False。"""
        monkeypatch.setenv("WMS_TEST_BOOL", raw)
        with pytest.raises(ValueError) as error:
            _get_bool_strict("WMS_TEST_BOOL", True)
        assert "WMS_TEST_BOOL" in str(error.value)

    def test_unset_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("WMS_TEST_BOOL", raising=False)
        assert _get_bool_strict("WMS_TEST_BOOL", True) is True
        assert _get_bool_strict("WMS_TEST_BOOL", False) is False

    def test_empty_returns_default_not_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """空值视为未设置 ⇒ 取默认（对 Cookie 而言默认是安全的 ``True``）。"""
        monkeypatch.setenv("WMS_TEST_BOOL", "")
        assert _get_bool_strict("WMS_TEST_BOOL", True) is True

    def test_whitespace_only_returns_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WMS_TEST_BOOL", "   ")
        assert _get_bool_strict("WMS_TEST_BOOL", True) is True

    def test_error_message_does_not_echo_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """异常信息只含变量名，不回显取值（防止把误贴的凭据写进日志）。"""
        secret_like = "s3cr3t-value-abcdef"
        monkeypatch.setenv("WMS_TEST_BOOL", secret_like)
        with pytest.raises(ValueError) as error:
            _get_bool_strict("WMS_TEST_BOOL", True)
        message = str(error.value)
        assert secret_like not in message
        assert "WMS_TEST_BOOL" in message

    def test_legacy_get_bool_is_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """非安全关键开关保持原宽松语义（不扩大改动范围）。"""
        monkeypatch.setenv("WMS_TEST_BOOL", "flase")
        assert _get_bool("WMS_TEST_BOOL", True) is False   # 历史行为
        monkeypatch.setenv("WMS_TEST_BOOL", "yes")
        assert _get_bool("WMS_TEST_BOOL", False) is True

    def test_only_cookie_secure_uses_strict_variant(self) -> None:
        """仅 ``AUTH_COOKIE_SECURE`` 加固；其它开关仍走宽松解析。"""
        source = (_REPO_ROOT / "backend" / "app" / "config.py").read_text(
            encoding="utf-8"
        )
        strict_calls = re.findall(r"_get_bool_strict\(\"([A-Z_]+)\"", source)
        assert strict_calls == ["AUTH_COOKIE_SECURE"], strict_calls

        for key in ("DATABASE_ECHO", "RERANKER_ENABLED"):
            assert f'_get_bool("{key}"' in source, key

    def test_cookie_secure_default_remains_true(self) -> None:
        """**不得**为了测试通过把默认安全值改成不安全值。"""
        from backend.app.config import AuthSettings

        assert AuthSettings().cookie_secure is True


# ============================================================
# B. 生产监听地址
# ============================================================


class TestProductionBindingBoundary:
    """监听地址边界（Phase 8 Step 4 + 4A 的 fail-closed 契约）。

    判定表（与 ``config._validate_production_binding`` 一致）：

    ==============================  ==========================  ==========
    APP_ENV                         APP_HOST                    结果
    ==============================  ==========================  ==========
    production / prod               回环                        允许
    production / prod               非回环                      拒绝
    显式 dev / test / ci / local    任意                        允许
    缺失 / "" / 纯空白               回环                        允许
    缺失 / "" / 纯空白               默认值（未显式设置）          允许
    缺失 / "" / 纯空白               **显式**非回环              拒绝
    非法值（staging / prodction…）   任意                        拒绝
    ==============================  ==========================  ==========
    """

    # ---- 1/2. 显式生产 ----

    @pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
    def test_production_with_loopback_is_accepted(self, host: str) -> None:
        _validate_production_binding("production", host, 8000)   # 不抛错

    @pytest.mark.parametrize(
        "host", ["0.0.0.0", "::", "192.168.1.10", "10.0.0.5", "example.com", ""]
    )
    def test_production_with_non_loopback_is_rejected(self, host: str) -> None:
        with pytest.raises(ValueError) as error:
            _validate_production_binding("production", host, 8000)
        assert "APP_HOST" in str(error.value)

    @pytest.mark.parametrize("env", ["production", "PRODUCTION", "Prod", " prod "])
    def test_production_env_is_case_and_space_insensitive(self, env: str) -> None:
        with pytest.raises(ValueError):
            _validate_production_binding(env, "0.0.0.0", 8000)
        _validate_production_binding(env, "127.0.0.1", 8000)

    # ---- 3/4. 显式非生产 ----

    @pytest.mark.parametrize(
        "env", ["development", "dev", "test", "testing", "ci", "local"]
    )
    def test_explicit_non_production_is_not_restricted(self, env: str) -> None:
        """显式声明的开发 / 测试环境不受监听地址限制。"""
        _validate_production_binding(env, "0.0.0.0", 8000)       # 不抛错
        _validate_production_binding(env, "127.0.0.1", 8000)

    # ---- 5/6/7. 未声明（缺失 / 空串 / 纯空白）----

    @pytest.mark.parametrize("env", [None, "", "   ", "\t", "  \n "])
    def test_unspecified_env_with_default_host_is_allowed(self, env) -> None:
        """兼容：未声明环境 + 沿用默认值（未显式设置 APP_HOST）⇒ 允许。"""
        _validate_production_binding(env, "0.0.0.0", 8000, host_explicit=False)

    @pytest.mark.parametrize("env", [None, "", "   "])
    def test_unspecified_env_with_loopback_is_allowed(self, env) -> None:
        _validate_production_binding(env, "127.0.0.1", 8000, host_explicit=True)

    @pytest.mark.parametrize("env", [None, "", "   "])
    @pytest.mark.parametrize("host", ["0.0.0.0", "::", "10.0.0.5"])
    def test_unspecified_env_with_explicit_non_loopback_is_rejected(
        self, env, host: str
    ) -> None:
        """**核心 fail-closed**：未声明环境 + 显式非回环 ⇒ 拒绝。"""
        with pytest.raises(ValueError) as error:
            _validate_production_binding(env, host, 8000, host_explicit=True)
        message = str(error.value)
        assert "APP_ENV" in message
        assert "APP_HOST" in message

    # ---- 8. 非法值 ----

    @pytest.mark.parametrize(
        "env", ["staging", "prodction", "prd", "devlopment", "prod-1", "unknown"]
    )
    def test_invalid_env_is_rejected(self, env: str) -> None:
        """拼写错误不得被当作开发环境（即使监听回环也拒绝）。"""
        with pytest.raises(ValueError) as error:
            _validate_production_binding(env, "127.0.0.1", 8000)
        assert "APP_ENV" in str(error.value)

    def test_environment_resolution_three_states(self) -> None:
        from backend.app.config import (
            ENVIRONMENT_NON_PRODUCTION,
            ENVIRONMENT_PRODUCTION,
            ENVIRONMENT_UNSPECIFIED,
            _resolve_environment,
        )

        assert _resolve_environment("production") == ENVIRONMENT_PRODUCTION
        assert _resolve_environment("PROD") == ENVIRONMENT_PRODUCTION
        assert _resolve_environment("development") == ENVIRONMENT_NON_PRODUCTION
        assert _resolve_environment("test") == ENVIRONMENT_NON_PRODUCTION
        assert _resolve_environment(None) == ENVIRONMENT_UNSPECIFIED
        assert _resolve_environment("") == ENVIRONMENT_UNSPECIFIED
        assert _resolve_environment("   ") == ENVIRONMENT_UNSPECIFIED
        with pytest.raises(ValueError):
            _resolve_environment("prodction")

    # ---- 9. 异常信息不泄漏配置内容 ----

    @pytest.mark.parametrize(
        "env", ["s3cr3t-env-value-abcdef", "postgresql+psycopg://u:p@h/db"]
    )
    def test_invalid_env_error_does_not_echo_value(self, env: str) -> None:
        with pytest.raises(ValueError) as error:
            _validate_production_binding(env, "127.0.0.1", 8000)
        message = str(error.value)
        assert env not in message
        assert "APP_ENV" in message

    @pytest.mark.parametrize("host", ["10.1.2.3", "db.internal.example"])
    def test_unspecified_error_does_not_echo_host(self, host: str) -> None:
        with pytest.raises(ValueError) as error:
            _validate_production_binding("", host, 8000, host_explicit=True)
        assert host not in str(error.value)

    # ---- 10. 真实子进程（退出码）----

    def _run_import(self, extra_env: dict[str, str]) -> subprocess.CompletedProcess:
        env = {
            "PYTHONPATH": str(_REPO_ROOT),
            "PATH": os.environ.get("PATH", ""),
            **extra_env,
        }
        for key in ("APP_ENV", "APP_HOST"):
            if key not in extra_env:
                env.pop(key, None)
        return subprocess.run(
            [sys.executable, "-c", "import backend.app.config"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=env,
        )

    def test_real_startup_fails_on_wildcard_in_production(self) -> None:
        result = self._run_import(
            {"APP_ENV": "production", "APP_HOST": "0.0.0.0"}
        )
        assert result.returncode != 0, "生产环境使用 0.0.0.0 竟可启动"
        assert "APP_HOST" in (result.stdout or "") + (result.stderr or "")

    def test_real_startup_succeeds_on_loopback_in_production(self) -> None:
        result = self._run_import(
            {"APP_ENV": "production", "APP_HOST": "127.0.0.1", "APP_PORT": "8000"}
        )
        assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")

    def test_real_startup_fails_on_invalid_env(self) -> None:
        result = self._run_import(
            {"APP_ENV": "prodction", "APP_HOST": "127.0.0.1"}
        )
        assert result.returncode != 0, "非法 APP_ENV 竟然可以启动"
        assert "APP_ENV" in (result.stdout or "") + (result.stderr or "")

    def test_real_startup_fails_on_unspecified_with_explicit_wildcard(
        self,
    ) -> None:
        """**核心回归**：生产漏配 APP_ENV、却显式绑 0.0.0.0 ⇒ 必须启动失败。"""
        result = self._run_import({"APP_HOST": "0.0.0.0"})
        assert result.returncode != 0, "未声明环境 + 显式 0.0.0.0 竟然可以启动"
        assert "APP_ENV" in (result.stdout or "") + (result.stderr or "")

    def test_real_startup_unaffected_in_development(self) -> None:
        """回归：完全不设环境变量时仍可导入（默认值不破坏开发/测试）。"""
        result = self._run_import({})
        assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")

    def test_real_startup_unaffected_with_explicit_development(self) -> None:
        result = self._run_import(
            {"APP_ENV": "development", "APP_HOST": "0.0.0.0"}
        )
        assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")

    def test_real_startup_unaffected_with_explicit_test(self) -> None:
        result = self._run_import({"APP_ENV": "test", "APP_HOST": "0.0.0.0"})
        assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")

    # ---- 属性语义：未声明不得"冒充"已验证 ----

    def test_settings_flags(self) -> None:
        from backend.app.config import Settings

        unset = Settings()
        assert unset.environment_kind == "unspecified"
        assert unset.production_binding_verified is False

        prod_ok = Settings(app_env="production", app_host="127.0.0.1")
        assert prod_ok.environment_kind == "production"
        assert prod_ok.production_binding_verified is True

        dev = Settings(app_env="development", app_host="0.0.0.0")
        assert dev.environment_kind == "non-production"
        assert dev.production_binding_verified is False

    def test_app_env_default_is_unspecified_not_development(self) -> None:
        """默认值必须是"未声明"（空串），不得伪装成 development。"""
        from backend.app.config import Settings

        source = (_REPO_ROOT / "backend" / "app" / "config.py").read_text(
            encoding="utf-8"
        )
        assert '_get_str("APP_ENV", "")' in source
        assert '_get_str("APP_ENV", "development")' not in source
        assert Settings().app_env == ""


# ============================================================
# C. Compose 端口隔离
# ============================================================


class TestComposePortIsolation:
    @pytest.mark.parametrize(
        "path", _COMPOSE_FILES, ids=lambda p: p.name
    )
    def test_published_ports_bind_loopback(self, path: Path) -> None:
        """静态解析：``ports:`` 每一项必须以 ``127.0.0.1:`` 开头。"""
        entries = self._entries(path.read_text(encoding="utf-8"))
        assert entries, f"{path.name} 未解析到 ports 项"
        for entry in entries:
            assert entry.startswith("127.0.0.1:"), f"{path.name}: {entry}"

    @pytest.mark.parametrize(
        "path", _COMPOSE_FILES, ids=lambda p: p.name
    )
    def test_container_port_is_preserved(self, path: Path) -> None:
        """只收窄宿主绑定，容器内端口仍是 5432（不改变容器语义）。"""
        entries = self._entries(path.read_text(encoding="utf-8"))
        for entry in entries:
            assert entry.endswith(":5432"), f"{path.name}: {entry}"

    @staticmethod
    def _entries(text: str) -> list[str]:
        """取 ``ports:`` 块下的列表项（块内允许空行与注释）。"""
        out: list[str] = []
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
                out.append(stripped[2:].strip().strip('"').strip("'"))
                continue
            inside = False
        return out

    @pytest.mark.parametrize(
        "path", _COMPOSE_FILES, ids=lambda p: p.name
    )
    def test_compose_config_reports_loopback_host_ip(self, path: Path) -> None:
        """真实解析：``docker compose config`` 必须给出 ``host_ip: 127.0.0.1``。"""
        docker = shutil.which("docker")
        if docker is None:
            pytest.skip("Docker 不可用：跳过 compose 解析（未执行，非 PASS）")

        result = subprocess.run(
            [docker, "compose", "-f", str(path), "config"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if result.returncode != 0:
            pytest.skip(
                "docker compose config 不可用："
                f"{(result.stderr or '')[-200:]}（未执行，非 PASS）"
            )

        published = re.findall(
            r"host_ip:\s*(\S+)\s*\n\s*target:\s*(\d+)\s*\n\s*published:\s*\"?(\d+)\"?",
            result.stdout,
        )
        assert published, "未在 compose config 输出中解析到端口映射"
        for host_ip, target, published_port in published:
            assert host_ip == "127.0.0.1", (host_ip, published_port)
            assert target == "5432", (target, published_port)


# ============================================================
# D. Runbook 破坏性操作防护
# ============================================================


class TestRunbookDestructiveSafety:
    @staticmethod
    def _text(path: Path) -> str:
        assert path.is_file(), path
        return path.read_text(encoding="utf-8")

    def test_deploy_readme_requires_path_checks(self) -> None:
        text = self._text(_DEPLOY_README)
        assert "DB_DATA_DIR" in text
        assert "STATIC_ROOT" in text
        # 危险路径拦截
        assert re.search(r'STATIC_ROOT.*=.*"/"', text) or "不能是 /" in text

    @staticmethod
    def _rsync_commands(text: str) -> list[tuple[int, bool]]:
        """只取**命令行**（``rsync ...``），返回 ``(行号, 是否 dry-run)``。

        标题 / 说明文字中的 ``rsync --delete`` 不是可执行命令，不参与判断。
        """
        out: list[tuple[int, bool]] = []
        for index, line in enumerate(text.splitlines()):
            stripped = line.strip()
            if not stripped.startswith("rsync "):
                continue
            out.append((index, "--dry-run" in stripped))
        return out

    def test_destructive_rsync_is_preceded_by_dry_run(self) -> None:
        """结构性断言：**破坏性** ``rsync --delete`` 之前必须先出现 dry-run。"""
        for path in (_DEPLOY_README, _READINESS):
            commands = self._rsync_commands(self._text(path))
            dry_runs = [line for line, is_dry in commands if is_dry]
            destructive = [line for line, is_dry in commands if not is_dry]
            assert dry_runs, f"{path.name}: 缺少 rsync --dry-run 步骤"
            assert destructive, f"{path.name}: 缺少破坏性 rsync 步骤"
            assert min(dry_runs) < min(destructive), (
                f"{path.name}: 破坏性 rsync 出现在 dry-run 之前"
                f"（dry-run@{min(dry_runs)} vs destructive@{min(destructive)}）"
            )

    def test_database_data_dir_excluded_from_delete_scope(self) -> None:
        """数据库数据目录必须被显式排除在任何同步/删除范围之外。"""
        text = self._text(_DEPLOY_README)
        assert "DB_DATA_DIR" in text
        assert re.search(r"DB_DATA_DIR.*(禁止|不得|永不)", text), (
            "未声明数据库数据目录不得进入同步范围"
        )

    def test_git_checkout_requires_clean_worktree(self) -> None:
        for path in (_DEPLOY_README, _READINESS):
            text = self._text(path)
            if "git checkout" not in text:
                continue
            assert "status --porcelain" in text, path.name
            assert re.search(r"git.*checkout", text)

    def test_runbook_does_not_assume_blue_green(self) -> None:
        """不得假设已存在蓝绿 / release 目录机制。"""
        for path in (_DEPLOY_README, _READINESS):
            text = self._text(path)
            assert re.search(r"没有.{0,6}蓝绿|不存在.{0,6}蓝绿|不得假设", text), (
                path.name
            )

    def test_privileged_steps_are_called_out(self) -> None:
        text = self._text(_DEPLOY_README)
        assert "有权限的部署人员" in text or "必须由有权限" in text

    def test_readiness_document_keeps_rollback_sections(self) -> None:
        text = self._text(_READINESS)
        for heading in ("7.2", "7.3", "7.4", "7.5"):
            assert heading in text, heading
        assert "回滚前：路径与版本校验" in text

# ============================================================
# E. 生产守卫边界审计（Step 4A Review Fix）
# ============================================================


class TestProductionGuardAudit:
    """确认 `unspecified` 不会被任何路径误判为"生产配置已通过"。

    审计结论对应的三条不变量：

    1. 守卫在 **import 期**执行（`settings = Settings()`），不存在"先启动、
       后校验"的窗口；
    2. 唯一内建启动入口 `python -m backend.app.main` 使用 `settings.app_host`，
       不得硬编码通配地址；
    3. `production_binding_verified` 只证明**配置意图**，不证明真实 socket。
    """

    @staticmethod
    def _config_source() -> str:
        return (_REPO_ROOT / "backend" / "app" / "config.py").read_text(
            encoding="utf-8"
        )

    @staticmethod
    def _env_pairs(path: Path) -> dict[str, str]:
        out: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            out[key.strip()] = value.strip()
        return out

    # ---- 1. 守卫在 import 期执行，且未被 try/except 静默吞掉 ----

    def test_settings_constructed_at_import_time(self) -> None:
        source = self._config_source()
        assert "\nsettings = Settings()" in source
        index = source.index("\nsettings = Settings()")
        context = source[max(0, index - 160) : index]
        assert "try:" not in context, "Settings 构造被 try 包裹，可能吞掉校验失败"

    # ---- 2. 不存在绕过校验的正式启动入口 ----

    def test_builtin_entry_uses_settings_host(self) -> None:
        main = (_REPO_ROOT / "backend" / "app" / "main.py").read_text(
            encoding="utf-8"
        )
        assert "uvicorn.run(" in main
        assert "host=settings.app_host" in main
        assert 'host="0.0.0.0"' not in main
        assert "host='0.0.0.0'" not in main

    def test_entry_imports_validated_settings(self) -> None:
        """入口必须从 config 导入已校验的 `settings`，而不是自行读环境变量。"""
        main = (_REPO_ROOT / "backend" / "app" / "main.py").read_text(
            encoding="utf-8"
        )
        assert "from backend.app.config import" in main
        assert "getenv" not in main, "入口不得绕过 Settings 直接读环境变量"

    def test_no_other_listening_entry(self) -> None:
        """仓库内不得出现第二个绑定地址的入口（如 app.run / http.server）。"""
        offenders: list[str] = []
        for path in (_REPO_ROOT / "backend").rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "uvicorn.run(" in text and path.name != "main.py":
                offenders.append(str(path.relative_to(_REPO_ROOT)))
            if "app.run(" in text:
                offenders.append(str(path.relative_to(_REPO_ROOT)))
        assert offenders == [], offenders

    # ---- 3. 部署示例必须显式声明环境 ----

    def test_production_example_declares_production(self) -> None:
        env = self._env_pairs(_REPO_ROOT / "deploy" / "production.env.example")
        assert env["APP_ENV"] == "production"
        assert env["APP_HOST"] == "127.0.0.1"

    def test_dev_example_declares_development(self) -> None:
        """`.env.example` 必须显式写 development；否则通配绑定会被判为未声明。"""
        env = self._env_pairs(_REPO_ROOT / ".env.example")
        assert env["APP_ENV"] == "development"
        _validate_production_binding(
            env["APP_ENV"], env["APP_HOST"], 8000, host_explicit=True
        )   # 显式开发 ⇒ 允许通配（仅本地）

    # ---- 4. unspecified 不得被报告为已验证 ----

    def test_unspecified_never_verified(self) -> None:
        from backend.app.config import Settings

        for env in ("", "   ", "\t"):
            settings = Settings(app_env=env, app_host="127.0.0.1")
            assert settings.environment_kind == "unspecified"
            assert settings.production_binding_verified is False

    def test_non_production_never_verified(self) -> None:
        from backend.app.config import Settings

        for env in ("development", "test", "ci", "local"):
            settings = Settings(app_env=env, app_host="127.0.0.1")
            assert settings.production_binding_verified is False

    # ---- 5. 文档必须写清边界（config-only，不是 socket 验证） ----

    def test_readiness_documents_guard_semantics(self) -> None:
        text = _READINESS.read_text(encoding="utf-8")
        assert "production_binding_verified" in text
        assert "unspecified" in text
        assert "不证明" in text
        assert "配置意图" in text

    def test_readiness_documents_three_state_contract(self) -> None:
        text = _READINESS.read_text(encoding="utf-8")
        for token in ("`production`", "`non-production`", "`unspecified`"):
            assert token in text, token

