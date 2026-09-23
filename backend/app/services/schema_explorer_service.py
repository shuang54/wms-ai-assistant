"""Database Schema Explorer Service（Phase 3.7.1）。

职责（本阶段**唯一**产物——数据库结构读取能力）：

    PostgreSQL
        ↓
    SchemaExplorerService.inspect()   （只读 metadata 查询）
        ↓
    DatabaseSchema（稳定结构化 DTO）
        ├── tables
        │   ├── table_name / description（PG COMMENT）
        │   └── columns
        │       ├── name / data_type / nullable / default
        │       ├── ordinal_position
        │       ├── is_primary_key
        │       └── description（PG 列 COMMENT）
        └── foreign_keys
            └── source(schema, table, column) → target(schema, table, column)

设计要点：

- **只读**：仅执行 pg_catalog 上的 SELECT（表 / 列 / PK / FK / COMMENT），
  绝无 INSERT / UPDATE / DELETE / DDL。SQL 全部为模块常量，
  schema 名只通过 bind parameter 传入，**绝不字符串拼接**。

- **复用现有基建**：Engine 复用 `backend.app.db.session.get_engine()`
  （DATABASE_URL 未配置时返回 None → inspect 抛 RuntimeError，
  与 get_db / VectorSearchService 的行为一致）。

- **Provider 架构（Phase 3.7.1.1 起）**：
  `SchemaExplorerService`（业务编排）只依赖
  `DatabaseMetadataProvider` 协议，PostgreSQL 的 pg_catalog 查询全部
  下沉到 `PostgreSQLMetadataProvider`（Phase 3.7.1 的逻辑原样迁入，
  行为与 SQL 零改动）。未来接入新数据源时新增 Provider 实现即可，
  本模块业务 API 与既有测试零改动。

- **默认 schema 策略**：`schema=None` 时使用 `public`
  （PostgreSQL 默认 schema；当前项目全部业务表均在 public，
  已于 Phase 3.7.1 前置调研确认：库中唯一用户 schema 即 public，
  含 knowledge_document / knowledge_chunk）。
  这不是业务命名硬编码——如后续引入业务 schema，
  只需调用 `inspect(schema="xxx")` 或调整 DEFAULT_SCHEMA 常量。

- **系统对象隔离**：pg_catalog / information_schema / pg_* 被显式拒绝
  （SchemaExplorerInputError），系统表绝不暴露给 AI。

- **不含 AI**：本 Service 不调用 LLM / Embedding / RAG；
  AI 理解 Schema 是下一阶段（Text-to-SQL 前置）的工作。

- **不做 Text-to-SQL**：不提供 execute_sql / SQLGenerator / NL2SQL，
  这些留待后续 Phase（见 docs/decisions/Phase 3.7.1 — ADR.md）。

安全：
- 日志只记录 schema / table_count / column_count / foreign_key_count /
  elapsed_ms，不记录 DATABASE_URL / 密码 / 业务数据内容。
- SQLAlchemyError 包装为 SchemaExplorerError（只含异常类名，
  不透传可能携带连接串的原始消息）。

异常体系：

    SchemaExplorerError (Exception)
        ├── SchemaExplorerInputError   schema 名非法 / 系统 schema / 空
        └── SchemaNotFoundError        schema 在数据库中不存在
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Final, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from backend.app.db.session import get_engine

logger = logging.getLogger(__name__)

__all__ = [
    "SchemaColumn",
    "SchemaForeignKey",
    "SchemaTable",
    "DatabaseSchema",
    "SchemaExplorerError",
    "SchemaExplorerInputError",
    "SchemaNotFoundError",
    "DatabaseMetadataProvider",
    "PostgreSQLMetadataProvider",
    "SchemaExplorerService",
    "DEFAULT_SCHEMA",
]


# ============================================================
# 常量
# ============================================================

#: schema=None 时的默认 schema。PostgreSQL 默认 schema 为 public；
#: 当前项目全部业务表均在 public（Phase 3.7.1 前置调研确认）。
#: 这不是业务命名，如引入业务 schema 只需显式传参或调整此常量。
DEFAULT_SCHEMA: Final[str] = "public"

#: 合法 PostgreSQL 标识符（未加引号形式）。schema 参数必须匹配，
#: 杜绝任何拼接 / 注入可能（配合 bind parameter 双重防护）。
_SCHEMA_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: 绝不允许探索的 PostgreSQL 系统 schema（系统表不暴露给 AI）。
_FORBIDDEN_SCHEMAS: Final[frozenset[str]] = frozenset(
    {"pg_catalog", "information_schema"}
)
_FORBIDDEN_SCHEMA_PREFIX: Final[str] = "pg_"


# ============================================================
# 异常
# ============================================================

class SchemaExplorerError(Exception):
    """Schema Explorer 通用异常基类。"""


class SchemaExplorerInputError(SchemaExplorerError):
    """schema 参数非法：非 str / 空 / 纯空白 / 非法标识符 / 系统 schema。

    在发起任何数据库查询**之前**拒绝（不产生连接开销，更不可能执行 SQL）。
    """


class SchemaNotFoundError(SchemaExplorerError):
    """请求的 schema 在数据库中不存在。

    Attributes:
        schema: 未找到的 schema 名。
    """

    def __init__(self, schema: str) -> None:
        super().__init__(f"Schema {schema!r} 在当前数据库中不存在")
        self.schema = schema


# ============================================================
# DTO（frozen dataclass，不暴露 ORM / Row 对象）
# ============================================================

@dataclass(frozen=True)
class SchemaColumn:
    """表字段元数据。

    Attributes:
        name:            列名。
        data_type:       PostgreSQL 类型（format_type 输出，
                         如 "bigint" / "character varying(50)" / "vector(1024)"）。
        nullable:        是否允许 NULL。
        default:         列默认值表达式（无默认时为 None）。
        ordinal_position: 列序号（从 1 开始，按表内定义顺序）。
        is_primary_key:  是否为主键列。
        description:     列 COMMENT（无 comment 时为 None；不让 AI 猜含义）。
    """

    name: str
    data_type: str
    nullable: bool
    default: str | None
    ordinal_position: int
    is_primary_key: bool
    description: str | None


@dataclass(frozen=True)
class SchemaForeignKey:
    """外键关系（源列 → 目标列）。

    例如 knowledge_chunk.document_id → knowledge_document.id。
    """

    source_schema: str
    source_table: str
    source_column: str
    target_schema: str
    target_table: str
    target_column: str


@dataclass(frozen=True)
class SchemaTable:
    """表元数据（含全部列与外键）。

    Attributes:
        schema_name:  所属 schema。
        name:         表名。
        description:  表 COMMENT（无 comment 时为 None）。
        columns:      列元数据（按 ordinal_position 升序）。
        foreign_keys: 以本表为 source 的外键关系。
    """

    schema_name: str
    name: str
    description: str | None
    columns: tuple[SchemaColumn, ...]
    foreign_keys: tuple[SchemaForeignKey, ...]


@dataclass(frozen=True)
class DatabaseSchema:
    """一次 inspect 的完整结果（稳定、适合后续 AI 消费的结构）。

    Attributes:
        schema_name: 本次探索的 schema。
        tables:      全部表（按表名升序；空 schema 时为空 tuple）。
    """

    schema_name: str
    tables: tuple[SchemaTable, ...]


# ============================================================
# SQL（模块常量：全部只读 SELECT，schema 仅通过 :schema 绑定传入）
# ============================================================

#: schema 是否存在（PostgreSQL 任何 schema 都在 pg_namespace 中）
_SCHEMA_EXISTS_SQL: Final[str] = """
SELECT 1
FROM pg_namespace
WHERE nspname = :schema
"""

#: 表清单 + 表 COMMENT（relkind r=普通表, p=分区表；排除视图/序列等）
_TABLES_SQL: Final[str] = """
SELECT
    c.relname                              AS table_name,
    obj_description(c.oid, 'pg_class')     AS table_description
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = :schema
  AND c.relkind IN ('r', 'p')
ORDER BY c.relname
"""

#: 列清单 + 列 COMMENT + 默认值 + 主键标记
_COLUMNS_SQL: Final[str] = """
SELECT
    c.relname                                   AS table_name,
    a.attname                                   AS column_name,
    format_type(a.atttypid, a.atttypmod)        AS data_type,
    NOT a.attnotnull                            AS is_nullable,
    pg_get_expr(ad.adbin, ad.adrelid)           AS column_default,
    a.attnum                                    AS ordinal_position,
    col_description(a.attrelid, a.attnum)       AS column_description,
    EXISTS (
        SELECT 1
        FROM pg_index i
        WHERE i.indrelid = a.attrelid
          AND i.indisprimary
          AND a.attnum = ANY(i.indkey)
    )                                           AS is_primary_key
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attrdef ad
    ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
WHERE n.nspname = :schema
  AND c.relkind IN ('r', 'p')
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""

#: 外键关系（conkey/confkey 按 ordinality 配对展开）
_FOREIGN_KEYS_SQL: Final[str] = """
SELECT
    sn.nspname    AS source_schema,
    sc.relname    AS source_table,
    sa.attname    AS source_column,
    tn.nspname    AS target_schema,
    tc.relname    AS target_table,
    ta.attname    AS target_column
FROM pg_constraint con
JOIN pg_class sc ON sc.oid = con.conrelid
JOIN pg_namespace sn ON sn.oid = sc.relnamespace
JOIN pg_class tc ON tc.oid = con.confrelid
JOIN pg_namespace tn ON tn.oid = tc.relnamespace
CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS src(attnum, ord)
JOIN pg_attribute sa
    ON sa.attrelid = con.conrelid AND sa.attnum = src.attnum
JOIN pg_attribute ta
    ON ta.attrelid = con.confrelid AND ta.attnum = con.confkey[src.ord]
WHERE con.contype = 'f'
  AND sn.nspname = :schema
ORDER BY sc.relname, src.ord
"""


# ============================================================
# Service / Provider 架构（Phase 3.7.1.1 起）
# ============================================================

def _validate_schema(schema: str | None) -> str:
    """校验 schema 参数，返回解析后的 schema 名。

    规则（全部在发起数据库查询之前拒绝）：
        - None → DEFAULT_SCHEMA（"public"）
        - 非 str / 空 / 纯空白 → SchemaExplorerInputError
        - 不满足 PostgreSQL 未加引号标识符格式 → SchemaExplorerInputError
          （'public"; DROP TABLE ...' 等注入串在此被拒绝）
        - pg_catalog / information_schema / pg_* 系统 schema
          → SchemaExplorerInputError（系统表不暴露给 AI）
    """
    if schema is None:
        return DEFAULT_SCHEMA

    if not isinstance(schema, str):
        raise SchemaExplorerInputError(
            f"schema 必须是 str（当前: {type(schema).__name__}）"
        )
    stripped = schema.strip()
    if not stripped:
        raise SchemaExplorerInputError("schema 不能为空或纯空白")

    if not _SCHEMA_NAME_RE.match(stripped):
        raise SchemaExplorerInputError(
            f"schema {schema!r} 不是合法的 PostgreSQL 标识符，已拒绝"
        )
    if (
        stripped in _FORBIDDEN_SCHEMAS
        or stripped.startswith(_FORBIDDEN_SCHEMA_PREFIX)
    ):
        raise SchemaExplorerInputError(
            f"schema {stripped!r} 是 PostgreSQL 系统 schema，拒绝探索"
        )
    return stripped


class DatabaseMetadataProvider(Protocol):
    """数据库 metadata 读取器协议（Phase 3.7.1.1 引入）。

    SchemaExplorerService 只依赖本协议，不依赖具体数据库方言：

        SchemaExplorerService
                ↓
        DatabaseMetadataProvider（Protocol）
                ↓
        PostgreSQLMetadataProvider（当前唯一实现）
                ↓
        PostgreSQL pg_catalog

    未来支持新数据源（mysql / sqlserver / api）时新增实现即可，
    SchemaExplorerService 与其全部测试零改动。
    """

    async def inspect(
        self,
        *,
        schema: str | None = None,
    ) -> DatabaseSchema:
        """读取指定 schema 的结构，返回不可变 DatabaseSchema。"""
        ...


class PostgreSQLMetadataProvider:
    """PostgreSQL metadata 读取器（Phase 3.7.1 的 pg_catalog 逻辑原样迁入）。

    职责（与 Phase 3.7.1 的 SchemaExplorerService 实现完全一致）：
        - schema 校验（标识符白名单 + 系统 schema 拒绝）
        - Engine 懒加载（复用 db.session.get_engine）
        - 只读 SELECT：表 / 列 / 主键 / 外键 / COMMENT
        - SQLAlchemyError → SchemaExplorerError（只含异常类名）
    """

    def __init__(self, *, engine: Engine | None = None) -> None:
        """构造 PostgreSQLMetadataProvider。

        Args:
            engine: SQLAlchemy Engine；None 时懒加载全局
                    `get_engine()`（DATABASE_URL 未配置时 inspect 抛
                    RuntimeError）。测试可注入独立 Engine。
        """
        self._engine = engine

    # ---------- 依赖解析（懒加载，复用现有 session 模块） ----------

    def _get_engine(self) -> Engine:
        engine = self._engine if self._engine is not None else get_engine()
        if engine is None:
            raise RuntimeError(
                "DATABASE_URL 未配置，无法探索数据库 Schema。"
                "请在 .env 中设置 DATABASE_URL 后再使用本能力。"
            )
        return engine

    # ---------- 主流程（Phase 3.7.1 逻辑，行为不变） ----------

    async def inspect(
        self,
        *,
        schema: str | None = None,
    ) -> DatabaseSchema:
        """探索指定 schema 的全部表结构，返回不可变 DatabaseSchema。

        Raises:
            SchemaExplorerInputError: schema 参数非法 / 系统 schema。
            SchemaNotFoundError:      schema 在数据库中不存在。
            RuntimeError:             DATABASE_URL 未配置。
            SchemaExplorerError:      查询执行失败（包装，不泄露连接串）。
        """
        resolved_schema = _validate_schema(schema)
        engine = self._get_engine()

        start_time = time.perf_counter()
        try:
            with engine.connect() as conn:
                # 1) schema 必须真实存在（区分"空 schema"与"写错名字"）
                exists = conn.execute(
                    text(_SCHEMA_EXISTS_SQL), {"schema": resolved_schema}
                ).first()
                if exists is None:
                    raise SchemaNotFoundError(resolved_schema)

                # 2) 表 + 表 COMMENT
                table_rows = conn.execute(
                    text(_TABLES_SQL), {"schema": resolved_schema}
                ).fetchall()

                # 3) 列 + 列 COMMENT + 默认值 + 主键
                column_rows = conn.execute(
                    text(_COLUMNS_SQL), {"schema": resolved_schema}
                ).fetchall()

                # 4) 外键
                fk_rows = conn.execute(
                    text(_FOREIGN_KEYS_SQL), {"schema": resolved_schema}
                ).fetchall()
        except SQLAlchemyError as exc:
            # 只暴露异常类名，不透传可能包含 DATABASE_URL / 密码的原始消息
            raise SchemaExplorerError(
                f"Schema 查询失败: {type(exc).__name__}"
            ) from exc

        # ---- 组装 DTO ----
        columns_by_table: dict[str, list[SchemaColumn]] = {}
        for row in column_rows:
            columns_by_table.setdefault(row.table_name, []).append(
                SchemaColumn(
                    name=row.column_name,
                    data_type=row.data_type,
                    nullable=bool(row.is_nullable),
                    default=row.column_default,
                    ordinal_position=int(row.ordinal_position),
                    is_primary_key=bool(row.is_primary_key),
                    description=row.column_description,
                )
            )

        fks_by_table: dict[str, list[SchemaForeignKey]] = {}
        for row in fk_rows:
            fks_by_table.setdefault(row.source_table, []).append(
                SchemaForeignKey(
                    source_schema=row.source_schema,
                    source_table=row.source_table,
                    source_column=row.source_column,
                    target_schema=row.target_schema,
                    target_table=row.target_table,
                    target_column=row.target_column,
                )
            )

        tables = tuple(
            SchemaTable(
                schema_name=resolved_schema,
                name=row.table_name,
                description=row.table_description,
                columns=tuple(columns_by_table.get(row.table_name, ())),
                foreign_keys=tuple(fks_by_table.get(row.table_name, ())),
            )
            for row in table_rows
        )
        result = DatabaseSchema(schema_name=resolved_schema, tables=tables)

        column_count = sum(len(t.columns) for t in tables)
        fk_count = sum(len(t.foreign_keys) for t in tables)
        logger.info(
            "schema inspection completed",
            extra={
                "schema": resolved_schema,
                "table_count": len(tables),
                "column_count": column_count,
                "foreign_key_count": fk_count,
                "elapsed_ms": (time.perf_counter() - start_time) * 1000,
            },
        )
        return result


class SchemaExplorerService:
    """PostgreSQL 数据库结构探索器（Phase 3.7.1，业务层入口）。

    Phase 3.7.1.1 起架构：

        SchemaExplorerService（业务入口，只做编排）
                ↓
        DatabaseMetadataProvider（Protocol）
                ↓
        PostgreSQLMetadataProvider（pg_catalog 只读查询）

    兼容性：旧用法 `SchemaExplorerService(engine=...)` 与
    `SchemaExplorerService().inspect(schema=...)` 行为完全不变
    （engine 会被包装成 PostgreSQLMetadataProvider）。

    只负责：编排 Provider、暴露稳定业务 API。
    不负责：具体方言的 catalog 查询（下沉到 Provider）、
            AI 理解 Schema、Text-to-SQL、任何数据读写。
    """

    def __init__(
        self,
        *,
        engine: Engine | None = None,
        provider: DatabaseMetadataProvider | None = None,
    ) -> None:
        """构造 SchemaExplorerService。

        Args:
            engine:  兼容旧签名——注入 Engine 时自动包装为
                     PostgreSQLMetadataProvider(engine=engine)。
            provider: 显式注入 DatabaseMetadataProvider（测试可注入
                     fake；未来接入新数据源时注入对应实现）。
                     engine 与 provider 不能同时指定。
        """
        if engine is not None and provider is not None:
            raise ValueError("engine 与 provider 不能同时指定")
        if provider is not None:
            self._provider: DatabaseMetadataProvider = provider
        else:
            self._provider = PostgreSQLMetadataProvider(engine=engine)

    # ---------- 兼容保留：Phase 3.7.1 的静态校验入口 ----------

    _validate_schema = staticmethod(_validate_schema)

    # ---------- 业务 API ----------

    async def inspect(
        self,
        *,
        schema: str | None = None,
    ) -> DatabaseSchema:
        """探索指定 schema 的全部表结构（委托给 Provider）。

        Args:
            schema: 目标 schema；None 时使用 DEFAULT_SCHEMA（public）。

        Returns:
            DatabaseSchema（空 schema 时 tables=()）。

        Raises:
            SchemaExplorerInputError: schema 参数非法 / 系统 schema。
            SchemaNotFoundError:      schema 在数据库中不存在。
            RuntimeError:             DATABASE_URL 未配置。
            SchemaExplorerError:      查询执行失败（包装，不泄露连接串）。
        """
        return await self._provider.inspect(schema=schema)
