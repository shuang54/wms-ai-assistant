"""Text-to-SQL Baseline Evaluation（Phase 3.9.4）。

本模块**只做测量**，不优化 Text-to-SQL 链路：

    Phase 3.9.3 Dataset（只读，不修改 case）
            ↓ load_text_to_sql_regression_dataset()
    14 Cases
            ↓ Phase 3.9.3 TextToSQLEvaluationRunner（不重复实现）
    TextToSQLEvaluationResult × 14
            ↓ calculate_baseline_metrics()（纯函数）
    TextToSQLBaselineMetrics
            ↓ to_snapshot_dict() / render_report()
    Snapshot JSON + Markdown Report

度量定义（§五 ~ §十）：

- **不重新定义评分体系**：全部复用 3.9.3 Result 中已有的
  ``validation_passed`` / ``execution_passed`` /
  ``matched_expectations`` / ``failed_expectations``。
- **Case 通过** = 该 case 所有已声明期望项全部匹配（无任何 failed）。
- **Validation Expectation**：区分"SQL 本身是否通过 Validator"与
  "validation 期望是否被满足"——安全 case（``must_pass_validation=false``）
  被正确拒绝必须算 PASS。
- **Execution**：分母只统计真正要求执行（``must_execute``）的 case；
  分母为 0 时 rate = ``None``（N/A），而不是 0.0，避免把"没有执行测试"
  误读成"执行成功率 0%"。
- **Security**：``must_pass_validation == false`` 的 case；
  Validator 正确拒绝 → PASS。
- **Project Isolation**：project_id 属于隔离项目集合的 case
  （默认 dataset 里的 ``eval-project-a`` / ``eval-project-b``）。
- 比率统一 ``0.0 ~ 1.0``（保留 4 位小数），绝不使用 "85%" 这类字符串。

纪律：

- 纯统计：``calculate_baseline_metrics()`` 不查 DB、不调 LLM/Embedding、
  不读网络、不改文件。
- 不留机密：环境信息只记录 Python 版本 / LLM provider / model /
  数据库版本（可选注入）；**绝不**记录 API Key、DATABASE_URL、
  password、连接串。
- 确定性：``duration_ms``（3.9.3 已 compare=False）与
  ``generated_at``（本 DTO compare=False）都不参与相等性比较。
"""
from __future__ import annotations

import logging
import platform
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

import yaml

from backend.app.config import settings
from backend.app.services.text_to_sql_evaluation_service import (
    DEFAULT_REGRESSION_DATASET_PATH,
    EXPECTATION_EXECUTION,
    EXPECTATION_VALIDATION,
    TextToSQLEvaluationCase,
    TextToSQLEvaluationDatasetConfigError,
    TextToSQLEvaluationDatasetNotFoundError,
    TextToSQLEvaluationResult,
    TextToSQLEvaluationRunner,
    load_text_to_sql_regression_dataset,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PHASE",
    "BASELINE_SNAPSHOT_PATH",
    "BASELINE_REPORT_PATH",
    "DEFAULT_EXECUTION_MODE",
    "DEFAULT_ISOLATION_PROJECT_IDS",
    "UNKNOWN_DATASET_VERSION",
    "UNAVAILABLE",
    "TextToSQLBaselineMetrics",
    "TextToSQLBaselineEnvironment",
    "TextToSQLBaseline",
    "calculate_baseline_metrics",
    "collect_environment_info",
    "read_dataset_version",
    "run_baseline",
]


# ============================================================
# 常量
# ============================================================

#: 当前阶段号（写进 Snapshot / Report）。
PHASE: Final[str] = "3.9.4"

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]

#: Baseline Snapshot 位置（§十四）
BASELINE_SNAPSHOT_PATH: Final[Path] = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "text_to_sql"
    / "baselines"
    / "phase_3_9_4_baseline.json"
)

#: Markdown Report 位置（§十一）
BASELINE_REPORT_PATH: Final[Path] = (
    _REPO_ROOT / "docs" / "evaluation" / "text-to-sql-baseline-3.9.4.md"
)

#: 确定性 Baseline 的执行模式（默认不跑真实 LLM，§十八 / §十九）
DEFAULT_EXECUTION_MODE: Final[str] = "deterministic-fake-generator"

#: Project Isolation 计算时视为"隔离项目"的 project_id（默认取 dataset 现值）
DEFAULT_ISOLATION_PROJECT_IDS: Final[tuple[str, ...]] = (
    "eval-project-a",
    "eval-project-b",
)

#: 数据集未声明 _schema_version 时的占位值
UNKNOWN_DATASET_VERSION: Final[str] = "unknown"

#: 无法安全获取时的占位值
UNAVAILABLE: Final[str] = "unavailable"

#: 比率精度（0.0 ~ 1.0，保留 4 位小数）
RATE_PRECISION: Final[int] = 4

#: Snapshot 中绝不允许出现的敏感键（序列化后自检）
_FORBIDDEN_SNAPSHOT_KEYS: Final[frozenset[str]] = frozenset(
    {"api_key", "password", "database_url", "dsn", "connection_string",
     "secret", "token", "authorization"}
)


# ============================================================
# Metrics（§五 ~ §十）
# ============================================================

def _rate(numerator: int, denominator: int) -> float | None:
    """比率：分母为 0 → None（N/A）；否则四舍五入到 4 位小数。"""
    if denominator <= 0:
        return None
    return round(numerator / denominator, RATE_PRECISION)


@dataclass(frozen=True)
class TextToSQLBaselineMetrics:
    """Baseline 统计结果（纯数据；比率以 property 提供，避免双份真值）。

    Attributes:
        total_cases / passed_cases / failed_cases:
            总用例数 / 全部期望项通过的用例数 / 存在失败期望项的用例数。
        validation_passed_cases:
            **实际**通过 SQLValidator 的用例数（与安全 case 是否被
            正确拒绝无关，仅用于对照）。
        validation_expectation_cases / validation_expectation_passed_cases:
            声明了 ``must_pass_validation`` 的用例数 / 其期望被满足的用例数。
        execution_cases / execution_passed_cases:
            要求执行（``must_execute``）的用例数 / 执行成功的用例数。
        security_cases / security_passed_cases:
            ``must_pass_validation == false``（安全边界）用例数 /
            Validator 正确拒绝的用例数。
        project_isolation_cases / project_isolation_passed_cases:
            project-a / project-b 隔离用例数 / 满足期望的用例数。
    """

    total_cases: int
    passed_cases: int
    failed_cases: int
    validation_passed_cases: int
    validation_expectation_cases: int
    validation_expectation_passed_cases: int
    execution_cases: int
    execution_passed_cases: int
    security_cases: int
    security_passed_cases: int
    project_isolation_cases: int
    project_isolation_passed_cases: int

    # ---------- 派生量（property：单一真值） ----------

    @property
    def expectation_passed_cases(self) -> int:
        """全部期望项都满足的用例数（== passed_cases，语义别名）。"""
        return self.passed_cases

    @property
    def expectation_pass_rate(self) -> float | None:
        """最重要指标：case 级通过率 = passed_cases / total_cases。"""
        return _rate(self.passed_cases, self.total_cases)

    @property
    def overall_pass_rate(self) -> float | None:
        """``expectation_pass_rate`` 的别名（任务书 §五 字段同名）。"""
        return self.expectation_pass_rate

    @property
    def validation_pass_rate(self) -> float | None:
        """实际通过 Validator 的比例（仅供参考，不是"正确率"）。"""
        return _rate(self.validation_passed_cases, self.total_cases)

    @property
    def validation_expectation_pass_rate(self) -> float | None:
        """validation 期望满足率（安全 case 正确拒绝计入通过）。"""
        return _rate(
            self.validation_expectation_passed_cases,
            self.validation_expectation_cases,
        )

    @property
    def execution_pass_rate(self) -> float | None:
        """执行成功率；无执行要求 → None（不是 0.0）。"""
        return _rate(self.execution_passed_cases, self.execution_cases)

    @property
    def security_expectation_pass_rate(self) -> float | None:
        """安全边界通过率；无安全 case → None。"""
        return _rate(self.security_passed_cases, self.security_cases)

    @property
    def project_isolation_pass_rate(self) -> float | None:
        """Project A/B 隔离通过率；无隔离 case → None。"""
        return _rate(
            self.project_isolation_passed_cases, self.project_isolation_cases
        )

    def as_dict(self) -> dict[str, Any]:
        """机器可读 dict（Snapshot metrics 段 / Report 表格共用）。"""
        return {
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "validation_passed_cases": self.validation_passed_cases,
            "validation_expectation_cases": self.validation_expectation_cases,
            "validation_expectation_passed_cases": (
                self.validation_expectation_passed_cases
            ),
            "execution_cases": self.execution_cases,
            "execution_passed_cases": self.execution_passed_cases,
            "security_cases": self.security_cases,
            "security_passed_cases": self.security_passed_cases,
            "project_isolation_cases": self.project_isolation_cases,
            "project_isolation_passed_cases": (
                self.project_isolation_passed_cases
            ),
            "expectation_pass_rate": self.expectation_pass_rate,
            "overall_pass_rate": self.overall_pass_rate,
            "validation_pass_rate": self.validation_pass_rate,
            "validation_expectation_pass_rate": (
                self.validation_expectation_pass_rate
            ),
            "execution_pass_rate": self.execution_pass_rate,
            "security_expectation_pass_rate": (
                self.security_expectation_pass_rate
            ),
            "project_isolation_pass_rate": self.project_isolation_pass_rate,
        }


def _expected_validation_value(
    result: TextToSQLEvaluationResult,
    case_by_id: Mapping[str, TextToSQLEvaluationCase],
) -> bool | None:
    """反推该 case 的 ``must_pass_validation`` 期望值；None = 未声明。

    推导依据 3.9.3 Checker 的确定性约定：

        actual_valid == expected  →  matched
        actual_valid != expected  →  failed

    即 ``expected == (validation_passed == matched)``。
    若调用方传入了 dataset cases，则优先使用 dataset 真值（更直白）。
    """
    case = case_by_id.get(result.case_id)
    if case is not None and case.expected.must_pass_validation is not None:
        return case.expected.must_pass_validation

    declared = (
        EXPECTATION_VALIDATION in result.matched_expectations
        or EXPECTATION_VALIDATION in result.failed_expectations
    )
    if not declared:
        return None
    matched = EXPECTATION_VALIDATION in result.matched_expectations
    return result.validation_passed == matched


def _declared(result: TextToSQLEvaluationResult, name: str) -> bool:
    return name in result.matched_expectations or name in result.failed_expectations


def _expectation_matched(result: TextToSQLEvaluationResult, name: str) -> bool:
    return (
        name in result.matched_expectations
        and name not in result.failed_expectations
    )


def calculate_baseline_metrics(
    results: Sequence[TextToSQLEvaluationResult],
    cases: Sequence[TextToSQLEvaluationCase] = (),
    *,
    isolation_project_ids: Sequence[str] = DEFAULT_ISOLATION_PROJECT_IDS,
) -> TextToSQLBaselineMetrics:
    """纯函数统计 Baseline Metrics（§二十三：无 DB / LLM / 网络 / 文件写入）。

    Args:
        results: 3.9.3 Runner 产出的结果（按 dataset 顺序）。
        cases:   可选 dataset cases；用于精确定位安全 case
                 （``must_pass_validation == false``）。缺省时从 Result
                 精确反推（见 ``_expected_validation_value``）。
        isolation_project_ids: 视为 Project Isolation 项目的 project_id。

    Returns:
        TextToSQLBaselineMetrics（比率以 property 暴露）。
    """
    case_by_id = {case.case_id: case for case in cases}
    isolation = frozenset(isolation_project_ids)

    total = len(results)
    passed = failed = 0
    validation_passed = 0
    validation_exp_cases = validation_exp_passed = 0
    execution_cases = execution_passed = 0
    security_cases = security_passed = 0
    isolation_cases = isolation_passed = 0

    for result in results:
        case_passed = not result.failed_expectations
        if case_passed:
            passed += 1
        else:
            failed += 1

        if result.validation_passed:
            validation_passed += 1

        expected_validation = _expected_validation_value(result, case_by_id)
        if expected_validation is not None:
            validation_exp_cases += 1
            if _expectation_matched(result, EXPECTATION_VALIDATION):
                validation_exp_passed += 1

        if expected_validation is False:
            security_cases += 1
            if _expectation_matched(result, EXPECTATION_VALIDATION):
                security_passed += 1

        if _declared(result, EXPECTATION_EXECUTION):
            execution_cases += 1
            if _expectation_matched(result, EXPECTATION_EXECUTION):
                execution_passed += 1

        if result.project_id is not None and result.project_id in isolation:
            isolation_cases += 1
            if case_passed:
                isolation_passed += 1

    return TextToSQLBaselineMetrics(
        total_cases=total,
        passed_cases=passed,
        failed_cases=failed,
        validation_passed_cases=validation_passed,
        validation_expectation_cases=validation_exp_cases,
        validation_expectation_passed_cases=validation_exp_passed,
        execution_cases=execution_cases,
        execution_passed_cases=execution_passed,
        security_cases=security_cases,
        security_passed_cases=security_passed,
        project_isolation_cases=isolation_cases,
        project_isolation_passed_cases=isolation_passed,
    )


# ============================================================
# Environment（§十二：绝不留机密）
# ============================================================

@dataclass(frozen=True)
class TextToSQLBaselineEnvironment:
    """Baseline 运行环境说明（只含非敏感信息）。

    Attributes:
        python_version: ``platform.python_version()``。
        llm_provider:   ``settings.llm.provider``；空 → "unavailable"。
        llm_model:      ``settings.llm.model``；空 → "unavailable"。
        database:       PostgreSQL 版本；默认 "unavailable"
                        （由调用方可选注入，本模块不主动连库）。
        execution_mode: 本次 Baseline 的执行方式
                        （默认 "deterministic-fake-generator"）。
    """

    python_version: str = platform.python_version()
    llm_provider: str = UNAVAILABLE
    llm_model: str = UNAVAILABLE
    database: str = UNAVAILABLE
    execution_mode: str = DEFAULT_EXECUTION_MODE

    def as_dict(self) -> dict[str, Any]:
        return {
            "python_version": self.python_version,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "database": self.database,
            "execution_mode": self.execution_mode,
        }


def _safe_setting(value: str) -> str:
    """非敏配置读取：空值 → "unavailable"。"""
    return value.strip() if value and value.strip() else UNAVAILABLE


def collect_environment_info(
    *,
    database: str = UNAVAILABLE,
    execution_mode: str = DEFAULT_EXECUTION_MODE,
) -> TextToSQLBaselineEnvironment:
    """收集环境信息（**不读取任何密钥 / 连接串**）。

    只读 ``settings.llm.provider`` / ``settings.llm.model``（非敏感标识），
    API Key / BASE_URL / DATABASE_URL 一律不碰。
    """
    return TextToSQLBaselineEnvironment(
        python_version=platform.python_version(),
        llm_provider=_safe_setting(settings.llm.provider),
        llm_model=_safe_setting(settings.llm.model),
        database=_safe_setting(database) if database else UNAVAILABLE,
        execution_mode=execution_mode or DEFAULT_EXECUTION_MODE,
    )


# ============================================================
# Dataset Version（§十三）
# ============================================================

def read_dataset_version(
    path: str | Path | None = None,
) -> str:
    """读取 dataset 自描述块里的 ``_schema_version``（不修改 dataset）。

    下划线开头的键是 3.9.3 Loader 允许的自描述元数据，本函数单独读取，
    从而让 Baseline 绑定 Dataset Version——否则以后增加 case 后无法知道
    旧 Baseline 对应什么数据。

    Returns:
        版本字符串；缺失 → ``UNKNOWN_DATASET_VERSION``。

    Raises:
        TextToSQLEvaluationDatasetNotFoundError: 文件不存在。
        TextToSQLEvaluationDatasetConfigError:  YAML 解析失败 / 顶层非 mapping。
    """
    target = Path(path) if path is not None else DEFAULT_REGRESSION_DATASET_PATH
    try:
        raw_text = target.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise TextToSQLEvaluationDatasetNotFoundError(str(target)) from exc
    except OSError as exc:
        raise TextToSQLEvaluationDatasetConfigError(
            f"读取评估数据集失败: {target.name}: {type(exc).__name__}"
        ) from exc

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise TextToSQLEvaluationDatasetConfigError(
            f"评估数据集 YAML 解析失败: {target.name}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        return UNKNOWN_DATASET_VERSION
    version = data.get("_schema_version")
    if version is None:
        return UNKNOWN_DATASET_VERSION
    return str(version)


# ============================================================
# Baseline 聚合（Snapshot + Report）
# ============================================================

def _percent(rate: float | None) -> str:
    """比率 → 展示字符串；None → "N/A"（避免把 N/A 显示成 0%）。"""
    if rate is None:
        return "N/A"
    return f"{rate * 100:.2f}%"


def _relative_path(path: Path) -> str:
    """仓库相对路径（POSIX 风格），避免报告里出现本地绝对路径。"""
    try:
        return path.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return path.name


def _failure_reasons(
    result: TextToSQLEvaluationResult,
    case_by_id: Mapping[str, TextToSQLEvaluationCase],
) -> tuple[str, ...]:
    """把失败原因写成事实描述（§二十二：不做主观评价）。"""
    reasons: list[str] = []
    for name in result.failed_expectations:
        if name == EXPECTATION_VALIDATION:
            expected = _expected_validation_value(result, case_by_id)
            reasons.append(
                f"expected validation={expected}, "
                f"actual validation={result.validation_passed}"
            )
        elif name == EXPECTATION_EXECUTION:
            reasons.append(
                f"expected successful execution, "
                f"actual execution_passed={result.execution_passed}"
            )
        else:
            reasons.append(f"{name} not satisfied")
    if result.error_code:
        reasons.append(f"error_code={result.error_code}")
    return tuple(reasons)


@dataclass(frozen=True)
class TextToSQLBaseline:
    """一次完整 Baseline 的产物（聚合对象）。

    Attributes:
        phase / dataset_path / dataset_version / total_cases: 绑定信息。
        results:    3.9.3 Result（按 dataset 顺序）。
        metrics:    ``calculate_baseline_metrics()`` 的产物。
        environment: 运行环境（非敏感）。
        generated_at: ISO-8601 UTC 时间戳；**不参与相等性比较**（§十七）。
    """

    phase: str
    dataset_path: str
    dataset_version: str
    total_cases: int
    results: tuple[TextToSQLEvaluationResult, ...]
    metrics: TextToSQLBaselineMetrics
    environment: TextToSQLBaselineEnvironment = field(
        default_factory=collect_environment_info
    )
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        compare=False,
    )

    # ---------- Snapshot（§十四 / §十六） ----------

    def failed_results(self) -> tuple[TextToSQLEvaluationResult, ...]:
        return tuple(r for r in self.results if not r.passed)

    def case_snapshots(self) -> list[dict[str, Any]]:
        """Case 级快照：**不保存 generated SQL**（§十六）。"""
        return [
            {
                "case_id": result.case_id,
                "project_id": result.project_id,
                "validation_passed": result.validation_passed,
                "execution_passed": result.execution_passed,
                "expectations_passed": result.passed,
                "failed_expectations": list(result.failed_expectations),
                "error_code": result.error_code,
            }
            for result in self.results
        ]

    def to_snapshot_dict(self) -> dict[str, Any]:
        """机器可读 Snapshot；序列化后自检不含敏感字段。"""
        snapshot: dict[str, Any] = {
            "phase": self.phase,
            "dataset": {
                "path": self.dataset_path,
                "version": self.dataset_version,
                "total_cases": self.total_cases,
            },
            "execution_mode": self.environment.execution_mode,
            "environment": self.environment.as_dict(),
            "metrics": self.metrics.as_dict(),
            "cases": self.case_snapshots(),
            "generated_at": self.generated_at,
        }
        _assert_no_secrets(snapshot)
        return snapshot

    # ---------- Report（§十一 / §二十二 / §二十五） ----------

    def render_report(self) -> str:
        """渲染 Markdown Report（失败原因只写事实）。"""
        metrics = self.metrics
        lines = [
            f"# Text-to-SQL Baseline — Phase {self.phase}",
            "",
            "Dataset:",
            f"- File: `{self.dataset_path}`",
            f"- Version: `{self.dataset_version}`",
            f"- Cases: {self.total_cases}",
            "",
            "Results:",
            "",
            "| Metric | Rate | Cases |",
            "|---|---:|---:|",
            f"| Total Cases | - | {metrics.total_cases} |",
            f"| Expectation Pass Rate | "
            f"{_percent(metrics.expectation_pass_rate)} | "
            f"{metrics.passed_cases}/{metrics.total_cases} |",
            "| Validation Expectation Pass Rate | "
            f"{_percent(metrics.validation_expectation_pass_rate)} | "
            f"{metrics.validation_expectation_passed_cases}/"
            f"{metrics.validation_expectation_cases} |",
            "| Execution Pass Rate | "
            f"{_percent(metrics.execution_pass_rate)} | "
            f"{metrics.execution_passed_cases}/{metrics.execution_cases} |",
            "| Security Pass Rate | "
            f"{_percent(metrics.security_expectation_pass_rate)} | "
            f"{metrics.security_passed_cases}/{metrics.security_cases} |",
            "| Project Isolation Pass Rate | "
            f"{_percent(metrics.project_isolation_pass_rate)} | "
            f"{metrics.project_isolation_passed_cases}/"
            f"{metrics.project_isolation_cases} |",
            "",
            "Environment:",
            f"- Python: {self.environment.python_version}",
            f"- PostgreSQL: {self.environment.database}",
            f"- LLM Provider: {self.environment.llm_provider}",
            f"- LLM Model: {self.environment.llm_model}",
            f"- Execution Mode: {self.environment.execution_mode}",
            f"- Dataset Version: {self.dataset_version}",
            "",
            f"Generated At: {self.generated_at}",
            "",
        ]
        failures = self.failed_results()
        lines.append("Failed Cases:")
        lines.append("")
        if not failures:
            lines.append("- None")
        else:
            for result in failures:
                lines.append(f"- `{result.case_id}`:")
                for reason in _failure_reasons(result, {}):
                    lines.append(f"  - reason: {reason}")
        lines.append("")
        return "\n".join(lines)

    def render_summary(self) -> str:
        """CLI 摘要（§二十五格式）。"""
        metrics = self.metrics
        return "\n".join(
            [
                f"Text-to-SQL Baseline — Phase {self.phase}",
                "",
                f"Dataset: {Path(self.dataset_path).name}",
                f"Version: {self.dataset_version}",
                f"Cases: {self.total_cases}",
                "",
                f"Expectation Pass Rate: "
                f"{_percent(metrics.expectation_pass_rate)}",
                "Validation Expectation Pass Rate: "
                f"{_percent(metrics.validation_expectation_pass_rate)}",
                f"Execution Pass Rate: "
                f"{_percent(metrics.execution_pass_rate)}",
                f"Security Pass Rate: "
                f"{_percent(metrics.security_expectation_pass_rate)}",
                "Project Isolation Pass Rate: "
                f"{_percent(metrics.project_isolation_pass_rate)}",
                "",
                f"Passed: {metrics.passed_cases}",
                f"Failed: {metrics.failed_cases}",
            ]
        )


def _assert_no_secrets(payload: Mapping[str, Any]) -> None:
    """Snapshot 序列化自检：出现敏感键立即报错（绝不落盘）。"""
    stack: list[Any] = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, Mapping):
            forbidden = {str(k).lower() for k in node} & _FORBIDDEN_SNAPSHOT_KEYS
            if forbidden:
                raise ValueError(
                    f"baseline snapshot contains forbidden keys: "
                    f"{sorted(forbidden)}"
                )
            stack.extend(node.values())
        elif isinstance(node, (list, tuple)):
            stack.extend(node)


# ============================================================
# Baseline Runner（§二十四：不复制 3.9.3 Runner）
# ============================================================

async def run_baseline(
    *,
    generator: Any,
    context_resolver: Any,
    validator: Any | None = None,
    executor: Any | None = None,
    cases: Sequence[TextToSQLEvaluationCase] | None = None,
    dataset_path: str | Path | None = None,
    phase: str = PHASE,
    environment: TextToSQLBaselineEnvironment | None = None,
    isolation_project_ids: Sequence[str] = DEFAULT_ISOLATION_PROJECT_IDS,
) -> TextToSQLBaseline:
    """跑一次 Baseline 并产出可比较的结构化结果。

    流程严格复用既有组件：

        load dataset → Phase 3.9.3 TextToSQLEvaluationRunner
        → calculate_baseline_metrics() → TextToSQLBaseline

    Args:
        generator:        Text-to-SQL Generator（Fake 或真实，契约不变）。
        context_resolver: case → 生成上下文。
        validator:        可选 SQLValidator；None → Runner 默认。
        executor:         可选 SQLExecutor；默认 Baseline 不需要数据库。
        cases:            可选 cases；None → 从 dataset_path 加载。
        dataset_path:     dataset 位置；None → 3.9.3 默认路径。
        environment:      可选环境信息；None → ``collect_environment_info()``。
    """
    resolved_cases = (
        tuple(cases)
        if cases is not None
        else load_text_to_sql_regression_dataset(dataset_path)
    )
    dataset_version = read_dataset_version(dataset_path)

    runner = TextToSQLEvaluationRunner(
        generator=generator,
        context_resolver=context_resolver,
        validator=validator,
        executor=executor,
    )
    summary = await runner.run(resolved_cases)
    metrics = calculate_baseline_metrics(
        summary.results,
        resolved_cases,
        isolation_project_ids=isolation_project_ids,
    )
    baseline = TextToSQLBaseline(
        phase=phase,
        dataset_path=_relative_path(
            Path(dataset_path)
            if dataset_path is not None
            else DEFAULT_REGRESSION_DATASET_PATH
        ),
        dataset_version=dataset_version,
        total_cases=len(resolved_cases),
        results=summary.results,
        metrics=metrics,
        environment=(
            environment if environment is not None else collect_environment_info()
        ),
    )
    logger.info(
        "text-to-sql baseline completed",
        extra={
            "phase": phase,
            "total_cases": metrics.total_cases,
            "passed_cases": metrics.passed_cases,
            "failed_cases": metrics.failed_cases,
            "expectation_pass_rate": metrics.expectation_pass_rate,
        },
    )
    return baseline
