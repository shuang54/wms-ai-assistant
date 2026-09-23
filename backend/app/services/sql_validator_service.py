"""SQL Validator（Phase 3.7.5，只读 SQL 安全校验）。

职责定位：

    LLM 生成 SQL（未来 Phase 3.7.6）
            ↓
    SQLValidator（本模块：纯内存校验，不执行、不修改、不生成）
            ↓
    安全 / 合法
            ↓
    未来 Read-only SQL Executor

安全边界（§三十二）：

    - SELECT only（允许只读 CTE / 子查询 / JOIN / 聚合 / UNION）
    - 单 statement（多语句一律拒绝，即使全是 SELECT）
    - 禁止 SELECT INTO / 一切 DML / DDL / 事务控制 / GRANT / REVOKE
    - 已知危险函数拒绝（pg_sleep / pg_advisory_* / pg_read_* /
      dblink / lo_import / setval / nextval ...）
    - 表必须存在于 DatabaseSchema（如提供）且在 allowed_tables
      白名单内（如提供），两者同时提供时取交集语义
    - 最外层查询必须有整数 LIMIT 且 LIMIT <= max_rows

实现方式：

    sqlglot（纯 Python SQL parser，AST-based）
        ↓
    AST 结构化检查（Statement Type / Table / Limit / Func 节点）
        ↓
    ValidationResult

明确不做：

    - 不执行 SQL、不连数据库（不 import 任何 ORM / DB session 模块）
    - 不调用任何大模型 / 向量化服务
    - 不自动修复 / 改写 SQL（缺 LIMIT → ROW_LIMIT_REQUIRED，
      由未来 Generator 层重新生成）
    - 不做字符串搜索式安全检查（'DELETE' 出现在字符串字面量中
      不会被误判——判断依据是 AST 节点类型，不是文本内容）

保守原则：

    无法安全判断（parser 不认识的语法、非字面量 LIMIT、
    裸表名歧义、未知裸表名）→ 拒绝，而不是放行。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol, Sequence

import sqlglot
from sqlglot import exp

from backend.app.services.schema_explorer_service import DatabaseSchema

logger = logging.getLogger(__name__)

__all__ = [
    "SQLValidationCode",
    "SQLValidationError",
    "SQLValidationResult",
    "SQLValidatorError",
    "SQLValidatorInputError",
    "SQLValidator",
    "SQLValidatorService",
    "DEFAULT_MAX_ROWS",
]


# ============================================================
# 常量
# ============================================================

#: 默认最大返回行数（§二十一）
DEFAULT_MAX_ROWS: Final[int] = 1000

#: 危险函数名前缀（PostgreSQL 可产生副作用 / 锁 / 系统访问的函数族）
_DANGEROUS_FUNC_PREFIXES: Final[tuple[str, ...]] = (
    "pg_sleep",
    "pg_advisory_",
    "pg_terminate_",
    "pg_cancel_",
    "pg_read_",
    "pg_ls_dir",
    "pg_stat_file",
    "pg_reload_",
    "pg_rotate_",
    "pg_logical_emit_",
    "dblink",
    "lo_import",
    "lo_export",
)

#: 危险函数名（精确匹配）
_DANGEROUS_FUNC_NAMES: Final[frozenset[str]] = frozenset(
    {
        "pg_notify",
        "setval",
        "nextval",
        "pg_sleep",
        "pg_reload_conf",
    }
)

#: AST 中一旦出现即代表写 / DDL / 事务控制的节点类型
_FORBIDDEN_NODES: Final[tuple[type[exp.Expression], ...]] = tuple(
    cls
    for cls in (
        # DML
        exp.Insert,
        exp.Update,
        exp.Delete,
        getattr(exp, "Merge", None),
        # DDL / 对象操作
        exp.Create,
        exp.Drop,
        getattr(exp, "Alter", None),
        getattr(exp, "AlterTable", None),
        getattr(exp, "TruncateTable", None),
        getattr(exp, "Grant", None),
        getattr(exp, "Revoke", None),
        getattr(exp, "Rename", None),
        getattr(exp, "RenameTable", None),
        getattr(exp, "Copy", None),
        getattr(exp, "VacuumColumn", None),
        getattr(exp, "VacuumTable", None),
        # 事务控制
        getattr(exp, "Transaction", None),
        getattr(exp, "Commit", None),
        getattr(exp, "Rollback", None),
        # 兜底：sqlglot 无法归类的一般语句（SET / USE / COPY 等）
        exp.Command,
    )
    if isinstance(cls, type)
)

#: 写操作节点 → DML（NON_READ_ONLY）。注：Merge 虽含 UPDATE 子句，
#: 但作为语句级写操作按任务书 §十 归入 DML 分类。
_DML_NODES: Final[tuple[type[exp.Expression], ...]] = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge,
)

#: 允许作为最外层语句的只读节点
_READ_ONLY_ROOTS: Final[tuple[type[exp.Expression], ...]] = (
    exp.Select,
    exp.SetOperation,
)


# ============================================================
# 异常体系
# ============================================================

class SQLValidatorError(Exception):
    """SQL Validator 通用异常基类。"""


class SQLValidatorInputError(SQLValidatorError):
    """输入非法（类型错误 / max_rows 越界），在解析前拒绝。"""


# ============================================================
# Error Model / DTO（frozen）
# ============================================================

class SQLValidationCode(str, Enum):
    """校验错误码（§八）。

    每个错误消息都会明确说明"发生了什么"，不返回
    "validation failed" 这类无法定位的信息。
    """

    EMPTY_SQL = "EMPTY_SQL"
    INVALID_SQL = "INVALID_SQL"
    MULTI_STATEMENT = "MULTI_STATEMENT"
    NON_READ_ONLY = "NON_READ_ONLY"
    DANGEROUS_OPERATION = "DANGEROUS_OPERATION"
    TABLE_NOT_ALLOWED = "TABLE_NOT_ALLOWED"
    UNKNOWN_TABLE = "UNKNOWN_TABLE"
    ROW_LIMIT_REQUIRED = "ROW_LIMIT_REQUIRED"
    ROW_LIMIT_EXCEEDED = "ROW_LIMIT_EXCEEDED"
    UNSUPPORTED_SQL = "UNSUPPORTED_SQL"


@dataclass(frozen=True)
class SQLValidationError:
    """单条校验错误（可定位、可解释）。

    Attributes:
        code:    错误码（SQLValidationCode）。
        message: 人类可读说明，例如
                 "SQL contains forbidden statement: DELETE"。
    """

    code: SQLValidationCode
    message: str


@dataclass(frozen=True)
class SQLValidationResult:
    """校验结果。

    Attributes:
        valid:             是否允许进入未来的 SQL Executor。
        normalized_sql:    parser 生成的规范化 SQL（展示 / 后续使用
                           信息）；解析失败时为 None。
                           注意：Validator 从不修改调用方原文。
        errors:            全部错误（收集而非首错即停，便于上层
                           一次性反馈给 Generator 重新生成）。
        referenced_tables: 规范化 "schema.table"（小写、排序、去重）；
                           裸表名无法解析时保留小写裸名。
    """

    valid: bool
    normalized_sql: str | None
    errors: tuple[SQLValidationError, ...]
    referenced_tables: tuple[str, ...]


# ============================================================
# Protocol（上层依赖接口，不依赖具体 parser）
# ============================================================

class SQLValidator(Protocol):
    """只读 SQL 校验器协议（Phase 3.7.5 引入）。"""

    def validate(
        self,
        sql: str,
        *,
        schema: DatabaseSchema | None = None,
        allowed_tables: Sequence[str] | None = None,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> SQLValidationResult:
        """校验 SQL 是否允许进入未来的执行层。

        Raises:
            SQLValidatorInputError: 输入类型 / max_rows 非法。
        """
        ...


# ============================================================
# sqlglot 实现
# ============================================================

class SQLValidatorService:
    """基于 sqlglot AST 的只读 SQL 校验器（Phase 3.7.5 唯一实现）。

    纯内存：只接收 sql 字符串 + DatabaseSchema + 白名单，
    不连数据库、不执行 SQL、不调用 LLM。
    """

    def validate(
        self,
        sql: str,
        *,
        schema: DatabaseSchema | None = None,
        allowed_tables: Sequence[str] | None = None,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> SQLValidationResult:
        """校验 SQL（收集全部错误，而非首错即停）。

        Args:
            sql:            待校验 SQL 原文（不会被修改）。
            schema:         数据库事实；提供时 SQL 引用的每张表
                            必须存在于 schema.tables。
            allowed_tables: 表白名单（"schema.table" 或裸表名）；
                            None 表示不额外限制（仍受 schema 与
                            只读规则约束）。
            max_rows:       LIMIT 上限；最外层查询必须有整数
                            LIMIT 且 <= max_rows。

        Returns:
            SQLValidationResult（valid=False 时 errors 非空）。

        Raises:
            SQLValidatorInputError: 输入类型 / max_rows 非法。
        """
        start_time = time.perf_counter()
        allowed = self._validate_inputs(
            sql=sql, schema=schema, allowed_tables=allowed_tables,
            max_rows=max_rows,
        )

        errors: list[SQLValidationError] = []

        # ---- 1) 空 SQL ----
        if not sql.strip():
            return self._build(
                sql, None,
                (SQLValidationError(SQLValidationCode.EMPTY_SQL,
                                    "SQL 为空或纯空白"),),
                start_time,
            )

        # ---- 2) 解析（parser 异常 → INVALID_SQL，不向外抛） ----
        try:
            statements = [s for s in sqlglot.parse(sql, read="postgres")
                          if s is not None]
        except sqlglot.errors.SqlglotError as exc:
            return self._build(
                sql, None,
                (SQLValidationError(
                    SQLValidationCode.INVALID_SQL,
                    f"SQL 解析失败（{type(exc).__name__}）：无法识别的语法",
                ),),
                start_time,
            )
        if not statements:
            return self._build(
                sql, None,
                (SQLValidationError(SQLValidationCode.EMPTY_SQL, "SQL 为空"),),
                start_time,
            )

        normalized_sql = statements[0].sql(dialect="postgres")

        # ---- 3) 单 statement ----
        if len(statements) > 1:
            errors.append(SQLValidationError(
                SQLValidationCode.MULTI_STATEMENT,
                f"只允许单条 SQL 语句（当前 {len(statements)} 条）；"
                "即使是多条 SELECT 也一律拒绝",
            ))

        root = statements[0]

        # ---- 4) 只读检查：覆盖**全部**语句（root 类型 + 树内禁止节点
        #     + SELECT INTO）。多语句时后续语句可能是 DELETE/DROP，
        #     必须同样被识别，绝不能只看第一条。 ----
        for stmt in statements:
            errors.extend(self._validate_read_only(stmt))

        # ---- 5) 危险函数（全部语句） ----
        for stmt in statements:
            errors.extend(self._validate_functions(stmt))

        # ---- 6) 表提取 + 解析（CTE 别名排除 / 裸名解析） ----
        referenced, table_errors = self._extract_tables(root, schema, allowed)
        errors.extend(table_errors)

        # ---- 7) 表存在性 + 白名单 ----
        errors.extend(self._validate_tables(referenced, schema, allowed))

        # ---- 8) LIMIT ----
        errors.extend(self._validate_limit(root, max_rows))

        return self._build(
            sql, normalized_sql, tuple(errors), start_time, referenced
        )

    # ---------- 输入校验（先于一切解析） ----------

    @staticmethod
    def _validate_inputs(
        *,
        sql: str,
        schema: DatabaseSchema | None,
        allowed_tables: Sequence[str] | None,
        max_rows: int,
    ) -> frozenset[str] | None:
        """校验输入类型；返回规范化（小写）后的白名单 frozenset。"""
        if not isinstance(sql, str):
            raise SQLValidatorInputError(
                f"sql 必须是 str（当前: {type(sql).__name__}）"
            )
        if schema is not None and not isinstance(schema, DatabaseSchema):
            raise SQLValidatorInputError(
                f"schema 必须是 DatabaseSchema 或 None"
                f"（当前: {type(schema).__name__}）"
            )
        if isinstance(max_rows, bool) or not isinstance(max_rows, int):
            raise SQLValidatorInputError(
                f"max_rows 必须是整数（当前: {type(max_rows).__name__}）"
            )
        if max_rows < 1:
            raise SQLValidatorInputError(
                f"max_rows 必须 >= 1（当前: {max_rows}）"
            )
        if allowed_tables is None:
            return None
        if isinstance(allowed_tables, (str, bytes)):
            raise SQLValidatorInputError(
                "allowed_tables 必须是表名序列，而不是单个字符串"
            )
        normalized: list[str] = []
        for entry in allowed_tables:
            if not isinstance(entry, str):
                raise SQLValidatorInputError(
                    f"allowed_tables 每项必须是 str（当前: {type(entry).__name__}）"
                )
            normalized.append(entry.strip().lower())
        return frozenset(normalized)

    # ---------- 只读检查 ----------

    def _validate_read_only(
        self, root: exp.Expression
    ) -> list[SQLValidationError]:
        """SELECT-only：root 类型 + 树内禁止节点 + SELECT INTO。"""
        errors: list[SQLValidationError] = []

        # 树内任何位置出现写 / DDL / 事务节点 → 拒绝
        # （覆盖 CTE 内 DELETE、子查询内 INSERT 等一切嵌套形态）
        for node in root.walk():
            if isinstance(node, _FORBIDDEN_NODES):
                errors.append(self._forbidden_node_error(node))
                # 继续收集其它类别错误，但禁止节点只报一次类别
                break

        if not isinstance(root, _READ_ONLY_ROOTS):
            if not any(e.code in (
                SQLValidationCode.NON_READ_ONLY,
                SQLValidationCode.DANGEROUS_OPERATION,
            ) for e in errors):
                errors.append(SQLValidationError(
                    SQLValidationCode.NON_READ_ONLY,
                    f"只允许 SELECT 查询（当前语句类型: {type(root).__name__}）",
                ))

        # SELECT ... INTO（建表写入）显式拒绝（§十二）
        if isinstance(root, exp.Select) and root.args.get("into") is not None:
            errors.append(SQLValidationError(
                SQLValidationCode.DANGEROUS_OPERATION,
                "SELECT INTO 会创建/写入对象，只读校验拒绝",
            ))
        return errors

    @staticmethod
    def _forbidden_node_error(node: exp.Expression) -> SQLValidationError:
        """把禁止节点映射为明确错误（DML / DDL / 事务分类）。"""
        name = type(node).__name__.lower()
        if isinstance(node, _DML_NODES):
            return SQLValidationError(
                SQLValidationCode.NON_READ_ONLY,
                f"SQL contains forbidden statement: {name.upper()}",
            )
        return SQLValidationError(
            SQLValidationCode.DANGEROUS_OPERATION,
            f"SQL contains dangerous operation: {name.upper()}",
        )

    # ---------- 危险函数 ----------

    def _validate_functions(
        self, root: exp.Expression
    ) -> list[SQLValidationError]:
        """已知危险函数拒绝（保守原则：不维护巨大白名单）。"""
        errors: list[SQLValidationError] = []
        for func in root.find_all(exp.Func):
            name = func.name.lower()
            if not name:
                continue
            if name in _DANGEROUS_FUNC_NAMES or name.startswith(
                _DANGEROUS_FUNC_PREFIXES
            ):
                errors.append(SQLValidationError(
                    SQLValidationCode.DANGEROUS_OPERATION,
                    f"SQL 调用了危险函数: {name}()",
                ))
                break
        return errors

    # ---------- 表提取 / 解析 ----------

    def _extract_tables(
        self,
        root: exp.Expression,
        schema: DatabaseSchema | None,
        allowed: frozenset[str] | None,
    ) -> tuple[tuple[str, ...], list[SQLValidationError]]:
        """从 AST 提取全部真实引用表（CTE 别名排除）。

        返回 (规范化引用表 tuple, 错误列表)。引用表保持出现顺序
        去重；最终结果在 _build 中统一排序。
        """
        errors: list[SQLValidationError] = []
        cte_aliases = {c.alias.lower() for c in root.find_all(exp.CTE)}

        referenced: list[str] = []
        for table in root.find_all(exp.Table):
            db = (table.db or "").lower()
            name = (table.name or "").lower()

            # CTE 引用（裸名命中别名）不是物理表
            if not db and name in cte_aliases:
                continue

            if db:
                canonical = f"{db}.{name}"
            else:
                canonical, resolve_error = self._resolve_bare_table(
                    name, schema, allowed
                )
                if resolve_error is not None:
                    errors.append(resolve_error)
                    if canonical is None:
                        continue
            if canonical not in referenced:
                referenced.append(canonical)
        return tuple(referenced), errors

    @staticmethod
    def _resolve_bare_table(
        name: str,
        schema: DatabaseSchema | None,
        allowed: frozenset[str] | None,
    ) -> tuple[str | None, SQLValidationError | None]:
        """解析裸表名 → 规范化 schema.table（§十七：不猜）。

        优先用 DatabaseSchema 解析；无 schema 时用 allowed_tables
        唯一后缀解析；两者都没有时保留裸名（仅做安全校验，§二十）。
        唯一性不满足 → 拒绝（歧义 / 无法定位）。
        """
        if schema is not None:
            matches = [
                t for t in schema.tables
                if t.name.lower() == name
            ]
            if len(matches) == 1:
                return f"{matches[0].schema_name.lower()}.{name}", None
            if len(matches) == 0:
                return None, SQLValidationError(
                    SQLValidationCode.UNKNOWN_TABLE,
                    f"表 {name} 在当前 DatabaseSchema 中不存在，"
                    "且未指定 schema 无法进一步解析",
                )
            return None, SQLValidationError(
                SQLValidationCode.UNSUPPORTED_SQL,
                f"裸表名 {name} 在多个 schema 中存在"
                f"（{', '.join(sorted(t.schema_name for t in matches))}），"
                "请在 SQL 中显式指定 schema",
            )

        if allowed is not None:
            suffix_matches = [e for e in allowed if e.split(".")[-1] == name]
            if len(suffix_matches) == 1:
                return suffix_matches[0], None
            if len(suffix_matches) == 0:
                return None, SQLValidationError(
                    SQLValidationCode.TABLE_NOT_ALLOWED,
                    f"裸表名 {name} 无法在 allowed_tables 中唯一解析",
                )
            return None, SQLValidationError(
                SQLValidationCode.UNSUPPORTED_SQL,
                f"裸表名 {name} 在 allowed_tables 中存在多个匹配"
                f"（{', '.join(sorted(suffix_matches))}），"
                "请在 SQL 中显式指定 schema",
            )

        # 无 schema 无白名单：仅做安全校验，保留裸名
        return name, None

    # ---------- 表存在性 + 白名单 ----------

    @staticmethod
    def _validate_tables(
        referenced: tuple[str, ...],
        schema: DatabaseSchema | None,
        allowed: frozenset[str] | None,
    ) -> list[SQLValidationError]:
        """表必须同时满足 DatabaseSchema 存在性 ∩ allowed 白名单。"""
        errors: list[SQLValidationError] = []

        if schema is not None:
            known = {
                f"{t.schema_name.lower()}.{t.name.lower()}"
                for t in schema.tables
            }
            for ref in referenced:
                if ref not in known:
                    errors.append(SQLValidationError(
                        SQLValidationCode.UNKNOWN_TABLE,
                        f"Table {ref} does not exist in DatabaseSchema",
                    ))

        if allowed is not None:
            for ref in referenced:
                if ref in allowed:
                    continue
                # 允许白名单写裸表名且无歧义的后缀匹配
                suffix = ref.split(".")[-1]
                suffix_matches = [e for e in allowed
                                  if e.split(".")[-1] == suffix]
                if len(suffix_matches) == 1:
                    continue
                errors.append(SQLValidationError(
                    SQLValidationCode.TABLE_NOT_ALLOWED,
                    f"Table {ref} is not in the allowed table set",
                ))
        return errors

    # ---------- LIMIT ----------

    def _validate_limit(
        self, root: exp.Expression, max_rows: int
    ) -> list[SQLValidationError]:
        """最外层查询必须有整数 LIMIT 且 <= max_rows（§二十一~二十三）。

        只检查 root 自身的 limit（子查询的 LIMIT 不算数——
        外层无界仍然危险）。不修改 SQL，只拒绝 + 原因。
        """
        limit = root.args.get("limit")
        if limit is None:
            return [SQLValidationError(
                SQLValidationCode.ROW_LIMIT_REQUIRED,
                f"查询缺少 LIMIT；请生成带 LIMIT <= {max_rows} 的 SQL"
                "（本 Validator 不自动改写 SQL）",
            )]

        value = limit.expression
        if not isinstance(value, exp.Literal) or not value.is_number:
            return [SQLValidationError(
                SQLValidationCode.UNSUPPORTED_SQL,
                "LIMIT 必须是整数常量（不支持表达式 / 参数占位符 / ALL）",
            )]
        try:
            rows = int(value.this)
        except (TypeError, ValueError):
            return [SQLValidationError(
                SQLValidationCode.UNSUPPORTED_SQL,
                f"LIMIT 值无法解析为整数: {value.this!r}",
            )]
        if rows < 0:
            return [SQLValidationError(
                SQLValidationCode.UNSUPPORTED_SQL,
                f"LIMIT 不能为负数（当前: {rows}）",
            )]
        if rows > max_rows:
            return [SQLValidationError(
                SQLValidationCode.ROW_LIMIT_EXCEEDED,
                f"LIMIT {rows} 超过最大允许行数 {max_rows}",
            )]
        return []

    # ---------- 结果组装 / 日志 ----------

    @staticmethod
    def _build(
        sql: str,
        normalized_sql: str | None,
        errors: tuple[SQLValidationError, ...],
        start_time: float,
        referenced: tuple[str, ...] = (),
    ) -> SQLValidationResult:
        result = SQLValidationResult(
            valid=not errors,
            normalized_sql=normalized_sql,
            errors=errors,
            referenced_tables=tuple(sorted(set(referenced))),
        )
        logger.info(
            "sql validation completed",
            extra={
                "valid": result.valid,
                "error_codes": tuple(e.code.value for e in errors),
                "referenced_table_count": len(result.referenced_tables),
                "sql_chars": len(sql),
                "elapsed_ms": (time.perf_counter() - start_time) * 1000,
            },
        )
        return result
