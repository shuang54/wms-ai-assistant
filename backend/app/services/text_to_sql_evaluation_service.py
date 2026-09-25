"""Text-to-SQL Evaluation & Regression Runner（Phase 3.9.3）。

本模块**只做评估**，不优化也不改动 Text-to-SQL 链路本身：

    Evaluation Dataset（YAML）
            ↓ Dataset Loader（纯读取 + 结构校验）
    TextToSQLEvaluationCase
            ↓ Context Resolver（现有 Selector / Composer / Filter）
    TextToSQLContext（Phase 3.9.1 既有 DTO，不新增字段）
            ↓ TextToSQLGenerator（现有契约，可注入 Fake / 真实服务）
    Generated SQL
            ↓ SQLValidator（Runner **自己**再校验一次，绝不信任 Generator）
    TextToSQLEvaluationResult
            ↓
    TextToSQLEvaluationSummary（文本报告）

核心纪律（与既有 ADR 一致）：

- **绝不绕过 Validator**：Runner 拿到 Generator 返回的 SQL 后，
  一定用注入的 ``SQLValidator`` 重新校验；Fake Generator 声称
  ``validated=True`` 不构成任何放行依据（安全边界仍在 Validator）。
- **不修改 Pipeline**：Resolver 只调用 Phase 3.7.4 / 3.9.1 / 3.9.2
  既有服务（RelevantTableSelector / SemanticSchemaFilter /
  DatabaseContextComposer）；Generator / Validator / Executor 契约零改动。
- **DB 执行可选**：只有 ``expected.must_execute`` 为 True 才调用
  ``SQLExecutor``，且必须重新注入 executor；否则整个评估零数据库访问。
- **结构期望而非精确 SQL**：同一问题存在多种合法 SQL，
  因此期望只表达 "must_pass_validation / must_contain_tables /
  must_contain_columns / must_not_contain / must_execute"，
  不做 SQL 字符串相等比较；table / column 判定走 **sqlglot AST**
  （含 alias 解析），不做朴素子串搜索。
- **确定性**：相同输入 → 相同结果；
  ``duration_ms`` 通过 ``field(compare=False)`` 排除在相等性之外。
- **不新增 DB 写、不引入新表、不调 LLM / Embedding / Reranker**。
"""
from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol

import sqlglot
import yaml
from sqlglot import exp

from backend.app.projects.models import ProjectContext
from backend.app.projects.semantic import ProjectSemantic
from backend.app.services.business_semantic_serializer import (
    BusinessSemanticSerializer,
)
from backend.app.services.database_context_composer import DatabaseContextComposer
from backend.app.services.relevant_table_selector import (
    RuleBasedRelevantTableSelector,
)
from backend.app.services.schema_explorer_service import (
    DatabaseSchema,
    SchemaTable,
)
from backend.app.services.semantic_schema_filter import SemanticSchemaFilter
from backend.app.services.sql_validator_service import (
    DEFAULT_MAX_ROWS,
    SQLValidationResult,
    SQLValidator,
    SQLValidatorService,
)
from backend.app.services.text_to_sql_context import TextToSQLContext
from backend.app.services.text_to_sql_service import TextToSQLGenerator

if TYPE_CHECKING:
    from backend.app.services.sql_executor_service import SQLExecutor

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_REGRESSION_DATASET_PATH",
    "EXPECTATION_VALIDATION",
    "EXPECTATION_TABLES",
    "EXPECTATION_COLUMNS",
    "EXPECTATION_FORBIDDEN",
    "EXPECTATION_EXECUTION",
    "TextToSQLEvaluationCase",
    "TextToSQLEvaluationInput",
    "TextToSQLExecutionOutcome",
    "TextToSQLExpectations",
    "TextToSQLEvaluationResult",
    "TextToSQLEvaluationSummary",
    "TextToSQLEvaluationDatasetError",
    "TextToSQLEvaluationDatasetNotFoundError",
    "TextToSQLEvaluationDatasetConfigError",
    "TextToSQLEvaluationRunnerError",
    "EvaluationProjectBinding",
    "EvaluationContextResolver",
    "StaticEvaluationContextResolver",
    "TextToSQLEvaluationRunner",
    "check_expectations",
    "load_text_to_sql_regression_dataset",
]


# ============================================================
# 常量
# ============================================================

#: 默认回归数据集位置（仓库根下 tests/fixtures/text_to_sql/）
DEFAULT_REGRESSION_DATASET_PATH: Final[Path] = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "text_to_sql"
    / "text_to_sql_regression.yaml"
)

#: 期望项名称（Result.matched_expectations / failed_expectations 中使用）
EXPECTATION_VALIDATION: Final[str] = "must_pass_validation"
EXPECTATION_TABLES: Final[str] = "must_contain_tables"
EXPECTATION_COLUMNS: Final[str] = "must_contain_columns"
EXPECTATION_FORBIDDEN: Final[str] = "must_not_contain"
EXPECTATION_EXECUTION: Final[str] = "must_execute"

#: dataset 顶层允许键（下划线开头 = 自描述元数据，忽略）
_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset({"cases"})

#: case 允许键
_CASE_KEYS: Final[frozenset[str]] = frozenset(
    # ``result_expectation``（Phase 3.9.10）是**可选**的结果级预期：
    # 本 Loader 只接受并忽略它（语义由 text_to_sql_result_evaluation_service
    # 单独解析），缺少该键的 dataset 行为完全不变（向后兼容）。
    {"id", "question", "project_id", "expected", "result_expectation"}
)

#: expected 允许键（未知键一律拒绝，防止拼写错误静默失效）
_EXPECTED_KEYS: Final[frozenset[str]] = frozenset(
    {
        "must_pass_validation",
        "must_contain_tables",
        "must_contain_columns",
        "must_not_contain",
        "must_execute",
    }
)


# ============================================================
# 异常体系
# ============================================================

class TextToSQLEvaluationDatasetError(Exception):
    """Evaluation Dataset 通用异常基类。"""


class TextToSQLEvaluationDatasetNotFoundError(TextToSQLEvaluationDatasetError):
    """数据集文件不存在。"""


class TextToSQLEvaluationDatasetConfigError(TextToSQLEvaluationDatasetError):
    """数据集内容错误：YAML 语法 / 结构 / 必填 / 未知键 / 重复 id / 类型。"""


class TextToSQLEvaluationRunnerError(Exception):
    """Runner / Resolver 契约错误（缺 executor、未注册项目等）。"""


# ============================================================
# DTO（frozen）
# ============================================================

@dataclass(frozen=True)
class TextToSQLExpectations:
    """单条 case 的**结构期望**（全部可选）。

    刻意不包含 ``expected_sql``：同一问题存在多种合法 SQL，
    字符串相等会制造大量假失败。

    Attributes:
        must_pass_validation: True → SQL 必须通过 SQLValidator；
                              False → 必须被 SQLValidator 拒绝
                              （用于危险问句的安全边界回归）。
        must_contain_tables:  AST 中提取的表必须包含全部列出的表
                              （"schema.table" 或裸表名均可）。
        must_contain_columns: AST 中提取的列必须包含全部列出的列
                              （"table.column" 或裸列名均可，
                              alias 会被解析回物理表）。
        must_not_contain:     列出的标识符（表 / 列 / 限定名）一旦出现即失败；
                              AST 判定，字符串字面量中的同名文本不算命中。
        must_execute:         仅 True 有语义 → 要求 SQL 在 executor
                              上执行成功（会真的访问数据库）。
    """

    must_pass_validation: bool | None = None
    must_contain_tables: tuple[str, ...] = ()
    must_contain_columns: tuple[str, ...] = ()
    must_not_contain: tuple[str, ...] = ()
    must_execute: bool | None = None

    def declared(self) -> tuple[str, ...]:
        """返回本 case 声明了哪些期望项（Runner 早失败时全部计入 failed）。"""
        names: list[str] = []
        if self.must_pass_validation is not None:
            names.append(EXPECTATION_VALIDATION)
        if self.must_contain_tables:
            names.append(EXPECTATION_TABLES)
        if self.must_contain_columns:
            names.append(EXPECTATION_COLUMNS)
        if self.must_not_contain:
            names.append(EXPECTATION_FORBIDDEN)
        if self.must_execute:
            names.append(EXPECTATION_EXECUTION)
        return tuple(names)


@dataclass(frozen=True)
class TextToSQLEvaluationCase:
    """一条回归用例（纯数据，不持有 Engine / LLM / Semantic 对象）。

    Attributes:
        case_id:    唯一用例 ID（dataset 内不得重复）。
        question:   自然语言问题（非空）。
        project_id: 关联项目（由 Resolver 解释；None → 由 Resolver 决定）。
        expected:   结构期望（默认空期望 = 只跑链路、不做断言）。
    """

    case_id: str
    question: str
    project_id: str | None = None
    expected: TextToSQLExpectations = field(default_factory=TextToSQLExpectations)


@dataclass(frozen=True)
class TextToSQLEvaluationInput:
    """Resolver 产出的单 case 执行输入。

    刻意把 ``TextToSQLContext``（文本上下文 Phase 3.9.1）与
    ``DatabaseSchema``（Validator 事实来源）分开保存——
    不给既有 ``TextToSQLEvaluationContext`` DTO 增加字段。
    """

    context: TextToSQLContext
    schema: DatabaseSchema | None = None


@dataclass(frozen=True)
class TextToSQLExecutionOutcome:
    """可选执行步骤的结果（只有 must_execute 才会产生）。

    Attributes:
        attempted: 是否真的尝试执行（未注入 executor / SQL 未通过校验
                   → False）。
        passed:    执行是否成功。
        error_code:失败异常类名（不含连接串等敏感信息）。
    """

    attempted: bool
    passed: bool
    error_code: str | None = None


@dataclass(frozen=True)
class TextToSQLEvaluationResult:
    """单条 case 的评估结果（§七）。

    Attributes:
        case_id / project_id / question: 来自 dataset。
        generated_sql:      Generator 产出的 SQL；生成失败时 None。
        validation_passed:  Runner 自己重跑 SQLValidator 的结果
                            （生成失败 → False，绝不默认 True）。
        execution_passed:   是否执行成功；未要求执行 → None。
        matched_expectations / failed_expectations: 期望项名称（固定顺序）。
        error_code:         生成 / 执行 / 校验失败的错误码（可为空）。
        duration_ms:        **不参与相等性比较**（determinism 要求）。
    """

    case_id: str
    project_id: str | None
    question: str
    generated_sql: str | None
    validation_passed: bool
    execution_passed: bool | None
    matched_expectations: tuple[str, ...]
    failed_expectations: tuple[str, ...]
    error_code: str | None = None
    validation_error_codes: tuple[str, ...] = ()
    duration_ms: float = field(default=0.0, compare=False)

    @property
    def passed(self) -> bool:
        """该 case 是否通过（无任何失败的期望项）。"""
        return not self.failed_expectations


@dataclass(frozen=True)
class TextToSQLEvaluationSummary:
    """评估汇总（§十五：只做 Python object / 文本，不做 HTML）。

    Attributes:
        total / passed / failed:          用例数。
        validation_passed:                通过 SQLValidator 的用例数。
        execution_passed:                 成功执行 SQL 的用例数（未执行不算）。
        results:                          按 dataset 顺序的结果。
    """

    total: int
    passed: int
    failed: int
    validation_passed: int
    execution_passed: int
    results: tuple[TextToSQLEvaluationResult, ...] = ()

    def failed_results(self) -> tuple[TextToSQLEvaluationResult, ...]:
        return tuple(r for r in self.results if not r.passed)

    def render_text(self) -> str:
        """渲染固定格式文本报告（确定性，无时间戳依赖）。"""
        lines = [
            "Text-to-SQL Regression Evaluation",
            "",
            f"Total: {self.total}",
            f"Passed: {self.passed}",
            f"Failed: {self.failed}",
            f"Validation Passed: {self.validation_passed}",
            f"Execution Passed: {self.execution_passed}",
        ]
        failures = self.failed_results()
        if failures:
            lines.append("")
            lines.append("Failed Cases:")
            for result in failures:
                detail = ", ".join(result.failed_expectations)
                if result.error_code:
                    detail = f"{detail} ({result.error_code})"
                lines.append(f"  - {result.case_id}: {detail}")
        return "\n".join(lines)


# ============================================================
# AST 事实提取（sqlglot；禁止朴素子串搜索）
# ============================================================

@dataclass(frozen=True)
class _SqlFacts:
    """从 AST 提取的事实集合（全部小写，供匹配使用）。"""

    tables: frozenset[str]
    columns: frozenset[str]

    @property
    def identifiers(self) -> frozenset[str]:
        """forbidden 判定用的标识符全集（表 + 列，含限定名形态）。"""
        return self.tables | self.columns


def _physical_names(table: exp.Table) -> tuple[str, ...]:
    """表节点的物理名候选：("schema.table", "table") / ("table",)。"""
    name = (table.name or "").lower()
    if not name:
        return ()
    db = (table.db or "").lower()
    return (f"{db}.{name}", name) if db else (name,)


def _extract_sql_facts(
    sql: str, schema: DatabaseSchema | None = None
) -> _SqlFacts | None:
    """解析 SQL 并提取表 / 列事实；无法解析 → None（调用方据此判失败）。

    规则（容忍"合法但不合格"的写法，避免把等价 SQL 判成回归）：
    - CTE 别名不算物理表（与 ``SQLValidator._extract_tables`` 同规则）；
    - 别名：``FROM inventory i ... i.qty`` 解析回 ``inventory.qty``；
    - 裸表名：schema 能唯一解析时补齐 ``schema.table`` 形态，
      （多 schema 同名 → 不猜，只保留裸名）；
    - 裸列名：用 ``DatabaseSchema`` 找出**本次查询引用过**且确实含该列的
      表，补齐 ``table.column`` / ``schema.table.column`` 形态；
      schema 为 None 时只记录裸列名（期望侧也应用裸列名）。
    """
    try:
        root = sqlglot.parse_one(sql, read="postgres")
    except sqlglot.errors.SqlglotError:
        return None
    if root is None:
        return None

    table_nodes = list(root.find_all(exp.Table))
    cte_aliases = {c.alias.lower() for c in root.find_all(exp.CTE)}

    tables: set[str] = set()
    alias_to_physical: dict[str, tuple[str, ...]] = {}
    referenced_tables: list[SchemaTable] = []

    for node in table_nodes:
        physical = _physical_names(node)
        if not physical:
            continue
        bare = physical[-1]
        alias = (node.alias or "").lower()
        if alias and alias != bare:
            alias_to_physical.setdefault(alias, physical)
        # CTE 引用（裸名命中 CTE 别名）不是物理表
        if not node.db and bare in cte_aliases:
            continue
        tables.update(physical)

        schema_table = _resolve_in_schema(schema, bare)
        if schema_table is not None:
            referenced_tables.append(schema_table)
            forms = _physical_forms(schema_table)
            tables.update(forms)
            if alias:
                alias_to_physical[alias] = forms
            else:
                alias_to_physical.setdefault(bare, forms)

    columns: set[str] = set()
    for node in root.find_all(exp.Column):
        name = (node.name or "").lower()
        if not name:
            continue
        columns.add(name)
        qualifier = (node.table or "").lower()
        if qualifier:
            for physical in alias_to_physical.get(qualifier, (qualifier,)):
                columns.add(f"{physical}.{name}")
            continue
        # 裸列名：只关联"本次查询引用过 + schema 中确有该列"的表
        for schema_table in referenced_tables:
            if _has_column(schema_table, name):
                columns.update(
                    f"{form}.{name}" for form in _physical_forms(schema_table)
                )

    return _SqlFacts(tables=frozenset(tables), columns=frozenset(columns))


def _physical_forms(table: SchemaTable) -> tuple[str, ...]:
    """SchemaTable 的候选形态：("schema.table", "table")。"""
    name = table.name.lower()
    return (f"{table.schema_name.lower()}.{name}", name)


def _resolve_in_schema(
    schema: DatabaseSchema | None, bare_name: str
) -> SchemaTable | None:
    """裸表名 → SchemaTable；不唯一 / 无 schema → None（不猜）。"""
    if schema is None:
        return None
    matches = [t for t in schema.tables if t.name.lower() == bare_name]
    return matches[0] if len(matches) == 1 else None


def _has_column(table: SchemaTable, column: str) -> bool:
    return any(c.name.lower() == column for c in table.columns)


def _references_missing(facts: _SqlFacts | None, references: Sequence[str]
                        ) -> tuple[str, ...]:
    """返回未被命中的引用（排序 → 确定性）。"""
    if facts is None:
        return tuple(sorted(references))
    return tuple(sorted(r for r in references
                        if r.strip().lower() not in facts.identifiers))


# ============================================================
# Expectation Checker（§九：纯函数）
# ============================================================

def check_expectations(
    case: TextToSQLEvaluationCase,
    generated_sql: str | None,
    validation_result: SQLValidationResult | None,
    execution_result: TextToSQLExecutionOutcome | None = None,
    schema: DatabaseSchema | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """检查单条 case 的结构期望。

    Args:
        case:             用例（含 expected）。
        generated_sql:    Generator 产出的 SQL；None 表示生成失败。
        validation_result:Runner 重跑 Validator 的结果；None 表示未校验。
        execution_result: 可选执行结果（None = 未要求 / 未执行）。
        schema:           可选数据库事实；提供时 table / column 期望
                          会补齐 schema 限定形态（容忍裸名写法）。

    Returns:
        ``(matched_expectations, failed_expectations)``——
        顺序固定为 validation → tables → columns → forbidden → execution。

    注意：不承担任何安全判断——"是否可执行"永远是 SQLValidator 的职责。
    """
    expected = case.expected
    matched: list[str] = []
    failed: list[str] = []
    facts = (
        _extract_sql_facts(generated_sql, schema) if generated_sql else None
    )

    if expected.must_pass_validation is not None:
        actual_valid = bool(
            validation_result is not None and validation_result.valid
        )
        if actual_valid == expected.must_pass_validation:
            matched.append(EXPECTATION_VALIDATION)
        else:
            failed.append(EXPECTATION_VALIDATION)

    if expected.must_contain_tables:
        missing = _references_missing(facts, expected.must_contain_tables)
        if missing:
            failed.append(EXPECTATION_TABLES)
        else:
            matched.append(EXPECTATION_TABLES)

    if expected.must_contain_columns:
        missing_columns = _references_missing(
            facts, expected.must_contain_columns
        )
        if missing_columns:
            failed.append(EXPECTATION_COLUMNS)
        else:
            matched.append(EXPECTATION_COLUMNS)

    if expected.must_not_contain:
        if facts is None:
            # 无法用 AST 确认"未出现" → 保守判失败（绝不假装通过）
            failed.append(EXPECTATION_FORBIDDEN)
        else:
            hits = tuple(
                sorted(
                    r for r in expected.must_not_contain
                    if r.strip().lower() in facts.identifiers
                )
            )
            if hits:
                failed.append(EXPECTATION_FORBIDDEN)
            else:
                matched.append(EXPECTATION_FORBIDDEN)

    if expected.must_execute:
        ok = execution_result is not None and execution_result.passed
        if ok:
            matched.append(EXPECTATION_EXECUTION)
        else:
            failed.append(EXPECTATION_EXECUTION)

    return tuple(matched), tuple(failed)


# ============================================================
# Dataset Loader（§十三：纯读取逻辑）
# ============================================================

def load_text_to_sql_regression_dataset(
    path: str | Path | None = None,
) -> tuple[TextToSQLEvaluationCase, ...]:
    """加载 Text-to-SQL 回归数据集。

    Args:
        path: YAML 路径；None → ``DEFAULT_REGRESSION_DATASET_PATH``。

    Returns:
        按 YAML 顺序排列的 cases（tuple，确定性）。

    Raises:
        TextToSQLEvaluationDatasetNotFoundError: 文件不存在。
        TextToSQLEvaluationDatasetConfigError:  YAML 语法 / 结构 / 必填 /
                                               未知键 / 重复 id / 类型错误。
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

    source = target.name
    if data is None:
        raise TextToSQLEvaluationDatasetConfigError(f"{source}: 数据集为空")
    if not isinstance(data, dict):
        raise TextToSQLEvaluationDatasetConfigError(
            f"{source}: 顶层必须是 mapping（当前: {type(data).__name__}）"
        )
    unknown_top = sorted(
        k for k in data
        if not str(k).startswith("_") and k not in _TOP_LEVEL_KEYS
    )
    if unknown_top:
        raise TextToSQLEvaluationDatasetConfigError(
            f"{source}: 存在未知顶层配置项 {unknown_top}"
            f"（允许: {sorted(_TOP_LEVEL_KEYS)}）"
        )

    entries = data.get("cases")
    if entries is None:
        raise TextToSQLEvaluationDatasetConfigError(f"{source}: cases 缺失")
    if not isinstance(entries, list):
        raise TextToSQLEvaluationDatasetConfigError(
            f"{source}: cases 必须是列表（当前: {type(entries).__name__}）"
        )

    cases: list[TextToSQLEvaluationCase] = []
    seen: set[str] = set()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise TextToSQLEvaluationDatasetConfigError(
                f"{source}: cases[{i}] 必须是 mapping"
                f"（当前: {type(entry).__name__}）"
            )
        case = _parse_case(entry, f"cases[{i}]", source)
        if case.case_id in seen:
            raise TextToSQLEvaluationDatasetConfigError(
                f"{source}: cases[{i}] 重复定义 case id {case.case_id!r}"
            )
        seen.add(case.case_id)
        cases.append(case)

    result = tuple(cases)
    logger.info(
        "text-to-sql regression dataset loaded",
        extra={"path": str(target), "case_count": len(result)},
    )
    return result


def _parse_case(
    entry: dict[str, Any], where: str, source: str
) -> TextToSQLEvaluationCase:
    _reject_unknown_keys(entry, _CASE_KEYS, where, source)
    return TextToSQLEvaluationCase(
        case_id=_require_str(entry, "id", where, source),
        question=_require_str(entry, "question", where, source),
        project_id=_optional_str(entry, "project_id", where, source),
        expected=_parse_expectations(
            entry.get("expected"), f"{where}.expected", source
        ),
    )


def _parse_expectations(
    raw: Any, where: str, source: str
) -> TextToSQLExpectations:
    if raw is None:
        return TextToSQLExpectations()
    if not isinstance(raw, dict):
        raise TextToSQLEvaluationDatasetConfigError(
            f"{source}: {where} 必须是 mapping（当前: {type(raw).__name__}）"
        )
    _reject_unknown_keys(raw, _EXPECTED_KEYS, where, source)
    return TextToSQLExpectations(
        must_pass_validation=_parse_optional_bool(
            raw, "must_pass_validation", where, source
        ),
        must_contain_tables=_parse_str_tuple(
            raw, "must_contain_tables", where, source
        ),
        must_contain_columns=_parse_str_tuple(
            raw, "must_contain_columns", where, source
        ),
        must_not_contain=_parse_str_tuple(
            raw, "must_not_contain", where, source
        ),
        must_execute=_parse_optional_bool(raw, "must_execute", where, source),
    )


# ---------- 校验辅助（模块级纯函数，风格对齐 ProjectSemanticLoader） ----------

def _reject_unknown_keys(
    entry: Mapping[str, Any],
    allowed: frozenset[str],
    where: str,
    source: str,
) -> None:
    unknown = sorted(set(entry) - allowed)
    if unknown:
        raise TextToSQLEvaluationDatasetConfigError(
            f"{source}: {where} 存在未知配置项 {unknown}"
            f"（允许: {sorted(allowed)}）"
        )


def _require_str(
    entry: Mapping[str, Any], key: str, where: str, source: str
) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TextToSQLEvaluationDatasetConfigError(
            f"{source}: {where} 缺少/非法必填字段 {key!r}"
            f"（必须是非空 str，当前: {value!r}）"
        )
    return value.strip()


def _optional_str(
    entry: Mapping[str, Any], key: str, where: str, source: str
) -> str | None:
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise TextToSQLEvaluationDatasetConfigError(
            f"{source}: {where} 的 {key} 必须是非空 str 或省略"
            f"（当前: {value!r}）"
        )
    return value.strip()


def _parse_optional_bool(
    entry: Mapping[str, Any], key: str, where: str, source: str
) -> bool | None:
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise TextToSQLEvaluationDatasetConfigError(
            f"{source}: {where} 的 {key} 必须是 true / false"
            f"（当前: {value!r}）"
        )
    return value


def _parse_str_tuple(
    entry: Mapping[str, Any], key: str, where: str, source: str
) -> tuple[str, ...]:
    value = entry.get(key)
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise TextToSQLEvaluationDatasetConfigError(
            f"{source}: {where} 的 {key} 必须是字符串列表"
            f"（当前: {type(value).__name__}）"
        )
    parsed: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise TextToSQLEvaluationDatasetConfigError(
                f"{source}: {where} 的 {key} 每项必须是非空 str"
                f"（当前: {item!r}）"
            )
        parsed.append(item.strip())
    return tuple(parsed)


# ============================================================
# Context Resolver（§八：Runner 不自己拼装上下文）
# ============================================================

@dataclass(frozen=True)
class EvaluationProjectBinding:
    """一个被评估项目的静态绑定（ProjectContext + Schema + Semantic）。"""

    project: ProjectContext
    schema: DatabaseSchema
    semantic: ProjectSemantic = field(default_factory=ProjectSemantic)


class EvaluationContextResolver(Protocol):
    """case → Text-to-SQL 输入（可由同步或 async 实现提供）。"""

    def resolve(
        self, case: TextToSQLEvaluationCase
    ) -> TextToSQLEvaluationInput | Awaitable[TextToSQLEvaluationInput]:
        """产出该 case 的生成上下文（不生成 SQL、不访问数据库）。"""
        ...


class StaticEvaluationContextResolver:
    """把 case.project_id 映射到静态绑定的 Resolver。

    上下文组装走**既有服务**（不改 Pipeline）：

        RelevantTableSelector（3.7.4）
        → SemanticSchemaFilter（3.9.2）
        → DatabaseContextComposer（3.7.3）
        → TextToSQLContext（3.9.1）

    不查 ProjectRegistry、不读 YAML、不调 LLM：DB 模式下由调用方
    先用 SchemaExplorerService 取出真实 DatabaseSchema 再注入。
    """

    def __init__(
        self,
        *,
        bindings: Mapping[str, EvaluationProjectBinding],
        table_selector: RuleBasedRelevantTableSelector | None = None,
        context_composer: DatabaseContextComposer | None = None,
        semantic_filter: SemanticSchemaFilter | None = None,
        semantic_serializer: BusinessSemanticSerializer | None = None,
        top_k: int = 10,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> None:
        self._bindings = dict(bindings)
        self._table_selector = (
            table_selector
            if table_selector is not None
            else RuleBasedRelevantTableSelector()
        )
        self._context_composer = (
            context_composer
            if context_composer is not None
            else DatabaseContextComposer()
        )
        self._semantic_filter = (
            semantic_filter
            if semantic_filter is not None
            else SemanticSchemaFilter()
        )
        self._semantic_serializer = (
            semantic_serializer
            if semantic_serializer is not None
            else BusinessSemanticSerializer()
        )
        self._top_k = top_k
        self._max_rows = max_rows

    def resolve(self, case: TextToSQLEvaluationCase) -> TextToSQLEvaluationInput:
        if not case.project_id:
            raise TextToSQLEvaluationRunnerError(
                f"case {case.case_id} 未声明 project_id，无法解析上下文"
            )
        binding = self._bindings.get(case.project_id)
        if binding is None:
            raise TextToSQLEvaluationRunnerError(
                f"case {case.case_id} 的项目 {case.project_id!r} 未注册绑定"
            )

        selection = self._table_selector.select(
            case.question,
            schema=binding.schema,
            semantic=binding.semantic,
            top_k=self._top_k,
        )
        allowed_tables = tuple(s.table for s in selection.selections)

        database_context = self._context_composer.compose(
            project=binding.project,
            schema=binding.schema,
            semantic=None,
            tables=allowed_tables or None,
        )
        filtered_semantic = self._semantic_filter.filter(
            binding.semantic, binding.schema, allowed_tables=allowed_tables
        )
        business_context = (
            self._semantic_serializer.serialize(filtered_semantic) or None
        )

        return TextToSQLEvaluationInput(
            context=TextToSQLContext(
                database_context=database_context,
                business_context=business_context,
                allowed_tables=allowed_tables,
                max_rows=self._max_rows,
                project_id=binding.project.project_id,
            ),
            schema=binding.schema,
        )


# ============================================================
# Runner（§八）
# ============================================================

class TextToSQLEvaluationRunner:
    """按 dataset 逐条跑 Text-to-SQL 链路并产出结构化结果。

    流程（每条 case 独立，单条失败不中断整个数据集）：

        1) Resolver 产出 TextToSQLEvaluationInput
        2) Generator.generate（契约不变）
        3) Runner 自己重跑 SQLValidator（**绝不信任 Generator**）
        4) 可选：Executor.execute（仅 must_execute）
        5) check_expectations → TextToSQLEvaluationResult

    不修改 / 不绕过任何既有组件：Generator、Validator、Executor 都既可
    注入 Fake（离线确定性测试），也可注入真实实现（DB 模式）。
    """

    def __init__(
        self,
        *,
        generator: TextToSQLGenerator,
        context_resolver: EvaluationContextResolver,
        validator: SQLValidator | None = None,
        executor: SQLExecutor | None = None,
    ) -> None:
        """构造 Runner。

        Args:
            generator:        Text-to-SQL 生成器（Fake 或真实，契约一致）。
            context_resolver: case → 生成上下文。
            validator:        SQL 校验器；None 时 ``SQLValidatorService()``。
            executor:         可选 SQLExecutor（``must_execute`` 必需）。
        """
        self._generator = generator
        self._resolver = context_resolver
        self._validator = (
            validator if validator is not None else SQLValidatorService()
        )
        self._executor = executor

    # ---------- 单条 ----------

    async def run_case(
        self, case: TextToSQLEvaluationCase
    ) -> TextToSQLEvaluationResult:
        """运行单条 case（异常被记录为 error_code，不向外抛出）。

        Raises:
            TextToSQLEvaluationRunnerError: 契约错误（如 must_execute
                但未注入 executor）——属于用法错误，需要显式修复。
        """
        start_time = time.perf_counter()
        generated_sql: str | None = None
        validation: SQLValidationResult | None = None
        execution: TextToSQLExecutionOutcome | None = None
        error_code: str | None = None

        try:
            resolved = self._resolver.resolve(case)
            if inspect.isawaitable(resolved):
                resolved = await resolved
            if not isinstance(resolved, TextToSQLEvaluationInput):
                raise TextToSQLEvaluationRunnerError(
                    f"resolver 必须返回 TextToSQLEvaluationInput"
                    f"（当前: {type(resolved).__name__}）"
                )
        except Exception as exc:  # 用法/契约错误：记录，不中断数据集
            logger.error(
                "t2s evaluation context resolution failed",
                extra={"case_id": case.case_id, "error": type(exc).__name__},
            )
            return TextToSQLEvaluationResult(
                case_id=case.case_id,
                project_id=case.project_id,
                question=case.question,
                generated_sql=None,
                validation_passed=False,
                execution_passed=None,
                matched_expectations=(),
                failed_expectations=case.expected.declared(),
                error_code=type(exc).__name__,
                duration_ms=(time.perf_counter() - start_time) * 1000,
            )

        # ---- 1) 生成（Generator 契约零改动） ----
        try:
            result = await self._generator.generate(
                case.question,
                database_context=resolved.context.render(),
                allowed_tables=resolved.context.allowed_tables or None,
                schema=resolved.schema,
                max_rows=resolved.context.max_rows,
            )
            generated_sql = getattr(result, "sql", None)
        except Exception as exc:
            error_code = type(exc).__name__
            logger.warning(
                "t2s evaluation generation failed",
                extra={"case_id": case.case_id, "error_code": error_code},
            )

        # ---- 2) 自己再校验一次（Fake Generator 的 validated 不算数） ----
        if generated_sql is not None:
            validation = self._validator.validate(
                generated_sql,
                schema=resolved.schema,
                allowed_tables=resolved.context.allowed_tables or None,
                max_rows=resolved.context.max_rows,
            )
            if not validation.valid and validation.errors:
                error_code = error_code or validation.errors[0].code.value

        # ---- 3) 可选执行（只有 must_execute 才会碰数据库） ----
        if case.expected.must_execute:
            if self._executor is None:
                raise TextToSQLEvaluationRunnerError(
                    f"case {case.case_id} 要求 must_execute，"
                    "但 Runner 未注入 executor"
                )
            execution = await self._execute(
                generated_sql=generated_sql,
                validation=validation,
                resolved=resolved,
            )
            if not execution.passed and execution.error_code:
                error_code = error_code or execution.error_code

        matched, failed = check_expectations(
            case, generated_sql, validation, execution, resolved.schema
        )
        execution_passed = execution.passed if execution is not None else None
        return TextToSQLEvaluationResult(
            case_id=case.case_id,
            project_id=case.project_id,
            question=case.question,
            generated_sql=generated_sql,
            validation_passed=bool(
                validation is not None and validation.valid
            ),
            execution_passed=execution_passed,
            matched_expectations=matched,
            failed_expectations=failed,
            error_code=error_code,
            validation_error_codes=(
                tuple(e.code.value for e in validation.errors)
                if validation is not None
                else ()
            ),
            duration_ms=(time.perf_counter() - start_time) * 1000,
        )

    # ---------- 批量 ----------

    async def run(
        self, cases: Sequence[TextToSQLEvaluationCase]
    ) -> TextToSQLEvaluationSummary:
        """按顺序运行全部 cases 并汇总（顺序保持 = 确定性）。"""
        start_time = time.perf_counter()
        results = tuple([await self.run_case(case) for case in cases])
        summary = TextToSQLEvaluationSummary(
            total=len(results),
            passed=sum(1 for r in results if r.passed),
            failed=sum(1 for r in results if not r.passed),
            validation_passed=sum(1 for r in results if r.validation_passed),
            execution_passed=sum(
                1 for r in results if r.execution_passed is True
            ),
            results=results,
        )
        logger.info(
            "text-to-sql regression evaluation completed",
            extra={
                "total": summary.total,
                "passed": summary.passed,
                "failed": summary.failed,
                "validation_passed": summary.validation_passed,
                "execution_passed": summary.execution_passed,
                "elapsed_ms": (time.perf_counter() - start_time) * 1000,
            },
        )
        return summary

    # ---------- 内部：执行 ----------

    async def _execute(
        self,
        *,
        generated_sql: str | None,
        validation: SQLValidationResult | None,
        resolved: TextToSQLEvaluationInput,
    ) -> TextToSQLExecutionOutcome:
        """按需执行；未通过校验的 SQL 不进入 Executor。"""
        can_execute = (
            generated_sql is not None
            and validation is not None
            and validation.valid
        )
        if not can_execute:
            return TextToSQLExecutionOutcome(
                attempted=False,
                passed=False,
                error_code=(
                    validation_error_code(validation)
                    if validation is not None
                    else "NO_SQL"
                ),
            )
        try:
            await self._executor.execute(
                generated_sql,
                schema=resolved.schema,
                allowed_tables=resolved.context.allowed_tables or None,
                max_rows=resolved.context.max_rows,
            )
        except Exception as exc:
            return TextToSQLExecutionOutcome(
                attempted=True, passed=False, error_code=type(exc).__name__
            )
        return TextToSQLExecutionOutcome(attempted=True, passed=True)


def validation_error_code(
    validation: SQLValidationResult,
) -> str:
    """取首个校验错误码（报告展示用；无错误 → "UNKNOWN"）。"""
    if validation.errors:
        return validation.errors[0].code.value
    return "UNKNOWN"


def with_execution_required(
    case: TextToSQLEvaluationCase,
) -> TextToSQLEvaluationCase:
    """返回把 ``expected.must_execute`` 置为 True 的副本（原 case 不变）。

    Dataset 默认不要求执行（保持 pytest 零数据库依赖）；DB 测试用本函数
    显式打开执行要求。
    """
    return replace(case, expected=replace(case.expected, must_execute=True))
