"""Read-only SQL Executor（Phase 3.7.7）。

架构位置（第二道运行时安全边界）：

    TextToSQLGenerator（3.7.6）
            ↓ validated SQL
    SQLExecutor（本模块）
        ├── Re-validate（不信任调用方，防 TOCTOU）
        ├── BEGIN READ ONLY transaction（数据库级写保护）
        ├── SET LOCAL statement_timeout（语句级超时）
        ├── fetchmany(max_rows + 1)（行数第二层保护）
        └── 结果大小保护（max_result_bytes）
            ↓
    PostgreSQL
            ↓
    SQLExecutionResult（纯 Python 数据，无 ORM / Row / Cursor）

安全分层（纵深防御）：

    Prompt            = 软约束（引导 LLM）
    SQLValidator      = SQL 静态安全边界（3.7.5）
    SQLExecutor       = 运行时边界（本层：READ ONLY + timeout + limits）
    只读数据库账号     = 部署层边界（部署时配置，见 ADR）

核心纪律：

- **执行前必须重新验证**：Executor 验证的就是最终执行的同一个
  SQL 字符串（TOCTOU 防护），绝不"验证一个、执行另一个"；
- **不修改 SQL**：不自动加 LIMIT / 不修复 / 不拼接条件；
- **异常不泄露连接信息**：对外只暴露异常类名 / 错误码，
  绝不透传可能包含 DATABASE_URL / 密码的底层消息；
- **不依赖 AI**：不调用任何大模型 / 向量化 / 检索服务。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from backend.app.config import (
    SQL_EXECUTOR_MAX_RESULT_BYTES_MAX,
    SQL_EXECUTOR_MAX_RESULT_BYTES_MIN,
    SQL_EXECUTOR_MAX_ROWS_MAX,
    SQL_EXECUTOR_MAX_ROWS_MIN,
    SQL_EXECUTOR_TIMEOUT_SECONDS_MAX,
    SQL_EXECUTOR_TIMEOUT_SECONDS_MIN,
)
from backend.app.db.session import get_engine
from backend.app.services.schema_explorer_service import DatabaseSchema
from backend.app.services.sql_validator_service import (
    DEFAULT_MAX_ROWS,
    SQLValidationError,
    SQLValidator,
    SQLValidatorInputError,
    SQLValidatorService,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SQLExecutorError",
    "SQLExecutorInputError",
    "SQLExecutorValidationError",
    "SQLExecutorTimeoutError",
    "SQLExecutorDatabaseError",
    "SQLExecutorUnavailableError",
    "SQLExecutionResult",
    "SQLExecutor",
    "SQLExecutorService",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_MAX_RESULT_BYTES",
]


# ============================================================
# 常量
# ============================================================

#: 默认语句级超时（秒）
DEFAULT_TIMEOUT_SECONDS: Final[int] = 10

#: 默认结果大小保护（字节）
DEFAULT_MAX_RESULT_BYTES: Final[int] = 1024 * 1024

_TIMEOUT_ERROR_MARKERS: Final[tuple[str, ...]] = (
    "statement timeout",
    "canceling statement",
    "query_canceled",
)


# ============================================================
# 异常体系（消息只含类名/错误码，不含连接信息）
# ============================================================

class SQLExecutorError(Exception):
    """SQL Executor 通用异常基类。"""


class SQLExecutorInputError(SQLExecutorError):
    """输入非法（空 SQL / 非法 max_rows / 非法 timeout 等），
    在任何验证与数据库访问之前拒绝。"""


class SQLExecutorValidationError(SQLExecutorError):
    """SQLValidator 重新验证未通过（携带错误码，不执行数据库）。

    属性 validation_errors 携带 Validator 的结构化错误，
    便于上层（未来 Generator / Router）据此重新生成。
    """

    def __init__(
        self, validation_errors: tuple[SQLValidationError, ...]
    ) -> None:
        codes = ", ".join(e.code.value for e in validation_errors) or "N/A"
        super().__init__(f"SQL 未通过执行前安全校验: {codes}")
        self.validation_errors = validation_errors


class SQLExecutorTimeoutError(SQLExecutorError):
    """SQL 执行超过语句级超时（statement_timeout 已在数据库端
    取消查询），连接已回滚归还连接池。"""

    def __init__(self, timeout_seconds: int) -> None:
        super().__init__(f"SQL 执行超时（上限 {timeout_seconds}s），已取消")
        self.timeout_seconds = timeout_seconds


class SQLExecutorDatabaseError(SQLExecutorError):
    """数据库访问 / 执行错误（含 READ ONLY 事务中的写尝试）。
    只暴露底层异常类名，不透传可能包含连接串 / 密码的消息。"""

    def __init__(self, reason: str) -> None:
        super().__init__(f"SQL 执行失败: {reason}")
        self.reason = reason


class SQLExecutorUnavailableError(SQLExecutorError):
    """数据库不可用（DATABASE_URL 未配置）。"""


# ============================================================
# DTO（frozen，纯 Python 数据结构）
# ============================================================

@dataclass(frozen=True)
class SQLExecutionResult:
    """查询执行结果。

    Attributes:
        columns:           列名（按 SELECT 顺序）。
        rows:              数据行（tuple of tuple；值为普通 Python
                           类型 int/str/float/bool/None/datetime 等，
                           不含任何 SQLAlchemy Row / Cursor /
                           Connection / Session 对象）。
        row_count:         实际返回行数（<= max_rows）。
        truncated:         结果是否被截断（超过 max_rows 或
                           超过 max_result_bytes）。
        execution_time_ms: 执行耗时（毫秒）。
    """

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    row_count: int
    truncated: bool
    execution_time_ms: float


# ============================================================
# Protocol（上层依赖接口，不依赖 SQLAlchemy）
# ============================================================

class SQLExecutor(Protocol):
    """Read-only SQL Executor 协议（Phase 3.7.7 引入）。"""

    async def execute(
        self,
        sql: str,
        *,
        schema: DatabaseSchema | None = None,
        allowed_tables: Sequence[str] | None = None,
        max_rows: int = DEFAULT_MAX_ROWS,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> SQLExecutionResult:
        """安全执行**已验证**的只读 SQL（执行前会重新验证）。

        Raises:
            SQLExecutorInputError:      输入非法。
            SQLExecutorValidationError: 重新验证未通过（不执行）。
            SQLExecutorTimeoutError:    语句超时。
            SQLExecutorDatabaseError:   数据库错误（消息已脱敏）。
            SQLExecutorUnavailableError: DATABASE_URL 未配置。
        """
        ...


# ============================================================
# 实现
# ============================================================

class SQLExecutorService:
    """基于现有 SQLAlchemy Engine 的 Read-only SQL Executor。

    执行流程（每一步都在线程池中运行，不阻塞事件循环）：

        1. 输入校验（类型 / 边界）
        2. SQLValidator 重新验证（TOCTOU：验证的字符串就是
           最终执行的字符串）
        3. AUTOCOMMIT 连接上显式 ``BEGIN READ ONLY``
        4. ``SET LOCAL statement_timeout``（事务级，不污染连接池）
        5. 执行 SQL，fetchmany(max_rows + 1) 判断截断
        6. 结果大小保护（max_result_bytes 粗粒度估算）
        7. ``ROLLBACK`` 归还连接
    """

    def __init__(
        self,
        *,
        engine: Engine | None = None,
        validator: SQLValidator | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        max_rows: int = DEFAULT_MAX_ROWS,
        max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
    ) -> None:
        """构造 Executor（全部依赖可注入，测试用 Fake）。

        Args:
            engine:            SQLAlchemy Engine；None 时懒加载
                               全局 get_engine()。
            validator:         SQL 校验器；None 时 SQLValidatorService()。
            timeout_seconds:   语句级超时秒数，钳制 [1, 600]。
            max_rows:          行数上限，钳制 [1, 100000]。
            max_result_bytes:  结果大小上限，钳制 [64KB, 64MB]。
        """
        self._timeout_seconds = _clamp_int(
            timeout_seconds, "timeout_seconds",
            SQL_EXECUTOR_TIMEOUT_SECONDS_MIN, SQL_EXECUTOR_TIMEOUT_SECONDS_MAX,
        )
        self._max_rows = _clamp_int(
            max_rows, "max_rows", SQL_EXECUTOR_MAX_ROWS_MIN,
            SQL_EXECUTOR_MAX_ROWS_MAX,
        )
        self._max_result_bytes = _clamp_int(
            max_result_bytes, "max_result_bytes",
            SQL_EXECUTOR_MAX_RESULT_BYTES_MIN,
            SQL_EXECUTOR_MAX_RESULT_BYTES_MAX,
        )
        self._engine = engine
        self._validator = (
            validator if validator is not None else SQLValidatorService()
        )

    # ---------- 依赖解析 ----------

    def _get_engine(self) -> Engine | None:
        if self._engine is not None:
            return self._engine
        return get_engine()

    # ---------- 主流程 ----------

    async def execute(
        self,
        sql: str,
        *,
        schema: DatabaseSchema | None = None,
        allowed_tables: Sequence[str] | None = None,
        max_rows: int = DEFAULT_MAX_ROWS,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> SQLExecutionResult:
        """见 Protocol docstring。"""
        start_time = time.perf_counter()

        # ---- 1) 输入校验（先于一切验证 / 数据库访问） ----
        self._validate_inputs(
            sql=sql, schema=schema, allowed_tables=allowed_tables,
            max_rows=max_rows, timeout_seconds=timeout_seconds,
        )

        # ---- 2) 重新验证（TOCTOU：验证的就是要执行的字符串） ----
        try:
            validation = self._validator.validate(
                sql,
                schema=schema,
                allowed_tables=allowed_tables,
                max_rows=max_rows,
            )
        except SQLValidatorInputError as exc:
            raise SQLExecutorInputError(
                f"参数无法通过校验器输入检查: {exc}"
            ) from exc
        if not validation.valid:
            raise SQLExecutorValidationError(validation.errors)

        # ---- 3) 数据库可用性 ----
        engine = self._get_engine()
        if engine is None:
            raise SQLExecutorUnavailableError(
                "DATABASE_URL 未配置，SQL 执行不可用"
            )

        # ---- 4) 线程池中执行只读事务（不阻塞事件循环） ----
        try:
            columns, rows, truncated = await asyncio.to_thread(
                self._execute_read_only,
                engine, sql, max_rows, timeout_seconds,
                self._max_result_bytes,
            )
        except _TimeoutCancelSignal as exc:
            raise SQLExecutorTimeoutError(timeout_seconds) from exc.cause
        except SQLAlchemyError as exc:
            # 只暴露异常类名，不透传可能含 DATABASE_URL / 密码的消息
            raise SQLExecutorDatabaseError(type(exc).__name__) from exc

        result = SQLExecutionResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            execution_time_ms=(time.perf_counter() - start_time) * 1000,
        )
        logger.info(
            "sql execution completed",
            extra={
                "row_count": result.row_count,
                "truncated": result.truncated,
                "columns": len(result.columns),
                "timeout_seconds": timeout_seconds,
                "elapsed_ms": result.execution_time_ms,
            },
        )
        return result

    # ---------- 输入校验 ----------

    @staticmethod
    def _validate_inputs(
        *,
        sql: str,
        schema: DatabaseSchema | None,
        allowed_tables: Sequence[str] | None,
        max_rows: int,
        timeout_seconds: int,
    ) -> None:
        if not isinstance(sql, str):
            raise SQLExecutorInputError(
                f"sql 必须是 str（当前: {type(sql).__name__}）"
            )
        if not sql.strip():
            raise SQLExecutorInputError("sql 不能为空或纯空白")
        if schema is not None and not isinstance(schema, DatabaseSchema):
            raise SQLExecutorInputError(
                f"schema 必须是 DatabaseSchema 或 None"
                f"（当前: {type(schema).__name__}）"
            )
        if isinstance(max_rows, bool) or not isinstance(max_rows, int):
            raise SQLExecutorInputError(
                f"max_rows 必须是整数（当前: {type(max_rows).__name__}）"
            )
        if not (
            SQL_EXECUTOR_MAX_ROWS_MIN <= max_rows <= SQL_EXECUTOR_MAX_ROWS_MAX
        ):
            raise SQLExecutorInputError(
                f"max_rows 必须在 "
                f"[{SQL_EXECUTOR_MAX_ROWS_MIN}, {SQL_EXECUTOR_MAX_ROWS_MAX}]"
                f" 内（当前: {max_rows}）"
            )
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, int
        ):
            raise SQLExecutorInputError(
                "timeout_seconds 必须是整数"
                f"（当前: {type(timeout_seconds).__name__}）"
            )
        if not (
            SQL_EXECUTOR_TIMEOUT_SECONDS_MIN
            <= timeout_seconds
            <= SQL_EXECUTOR_TIMEOUT_SECONDS_MAX
        ):
            raise SQLExecutorInputError(
                "timeout_seconds 必须在 "
                f"[{SQL_EXECUTOR_TIMEOUT_SECONDS_MIN}, "
                f"{SQL_EXECUTOR_TIMEOUT_SECONDS_MAX}] 内"
                f"（当前: {timeout_seconds}）"
            )
        if allowed_tables is not None:
            if isinstance(allowed_tables, (str, bytes)):
                raise SQLExecutorInputError(
                    "allowed_tables 必须是表名序列，而不是单个字符串"
                )
            for entry in allowed_tables:
                if not isinstance(entry, str):
                    raise SQLExecutorInputError(
                        "allowed_tables 每项必须是 str"
                        f"（当前: {type(entry).__name__}）"
                    )

    # ---------- 只读事务执行（在线程池中运行） ----------

    def _execute_read_only(
        self,
        engine: Engine,
        sql: str,
        max_rows: int,
        timeout_seconds: int,
        max_result_bytes: int,
    ) -> tuple[tuple[str, ...], tuple[tuple[Any, ...], ...], bool]:
        """在 BEGIN READ ONLY 事务中执行 SQL（同步，由 to_thread 调）。

        Returns:
            (columns, rows, truncated)。rows <= max_rows 且
            总大小 <= max_result_bytes（超出则截断）。
        """
        timeout_ms = timeout_seconds * 1000
        # SET 不接受绑定参数；timeout_ms 是钳制后的 int，可安全内插
        set_timeout_sql = f"SET LOCAL statement_timeout = {timeout_ms}"

        with engine.connect() as conn:
            # AUTOCOMMIT：由我们显式控制事务边界
            conn = conn.execution_options(isolation_level="AUTOCOMMIT")
            try:
                conn.execute(text("BEGIN READ ONLY"))
                conn.execute(text(set_timeout_sql))
                cursor = conn.execute(text(sql))  # 执行的正是验证过的 sql
                fetched = cursor.fetchmany(max_rows + 1)
            except SQLAlchemyError as exc:
                if _is_timeout_cancel(exc):
                    raise _TimeoutCancelSignal(exc) from exc
                raise
            finally:
                # 无论成败都结束事务（只读事务没有可提交内容）
                try:
                    conn.execute(text("ROLLBACK"))
                except SQLAlchemyError:
                    logger.warning("rollback after execution failed",
                                   exc_info=True)

        truncated = len(fetched) > max_rows
        if truncated:
            fetched = fetched[:max_rows]
        return _materialize(
            columns=tuple(cursor.keys()),
            fetched=fetched,
            truncated=truncated,
            max_result_bytes=max_result_bytes,
        )


# ============================================================
# 内部工具
# ============================================================

class _TimeoutCancelSignal(Exception):
    """内部信号：statement_timeout 已取消查询（转 SQLExecutorTimeoutError）。"""

    def __init__(self, cause: BaseException) -> None:
        super().__init__()
        self.cause = cause


def _is_timeout_cancel(exc: BaseException) -> bool:
    """判断 SQLAlchemy 异常是否为 statement_timeout 取消。"""
    orig = getattr(exc, "orig", None)
    if orig is not None:
        if type(orig).__name__ in ("QueryCanceled", "QueryCanceledError"):
            return True
        message = str(orig).lower()
    else:
        message = str(exc).lower()
    return any(marker in message for marker in _TIMEOUT_ERROR_MARKERS)


def _materialize(
    *,
    columns: tuple[str, ...],
    fetched: Sequence[Any],
    truncated: bool,
    max_result_bytes: int,
) -> tuple[tuple[str, ...], tuple[tuple[Any, ...], ...], bool]:
    """把 fetch 到的行转成纯 Python 元组，并应用大小保护。"""
    rows: list[tuple[Any, ...]] = []
    total_bytes = 0
    for row in fetched:
        values = tuple(row)
        total_bytes += sum(_value_size(v) for v in values)
        if rows and total_bytes > max_result_bytes:
            # 至少保留一行，避免空结果被误判为失败
            truncated = True
            break
        if total_bytes > max_result_bytes and not rows:
            # 第一行就超限：保留该行并截断（MVP 粗粒度策略）
            rows.append(values)
            truncated = True
            break
        rows.append(values)
    return columns, tuple(rows), truncated


def _value_size(value: Any) -> int:
    """粗粒度估算字段大小（字节）：bytes 按长度，str 按 UTF-8 长度，
    其余按字符串化长度。MVP 级精度，足够阻断超大 TEXT/BYTEA。"""
    if value is None:
        return 4
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    return len(str(value))


def _clamp_int(value: int, name: str, lo: int, hi: int) -> int:
    """构造期参数钳制（与运行期输入校验同一套边界）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise SQLExecutorInputError(
            f"{name} 必须是整数（当前: {type(value).__name__}）"
        )
    if not lo <= value <= hi:
        raise SQLExecutorInputError(
            f"{name} 必须在 [{lo}, {hi}] 内（当前: {value}）"
        )
    return value
