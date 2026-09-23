"""Schema → Prompt Serializer（Phase 3.7.2）。

职责（本阶段唯一目标）：

    DatabaseSchema（+ 可选 ProjectContext）
            ↓
    SchemaSerializer.serialize()
            ↓
    稳定的、Prompt-friendly 的文本上下文

    Project: Vietnam WMS
    Data Source: primary
    Database Type: postgresql

    ## Table: public.knowledge_document
    Description: 知识文档表
    Columns:
    - id: bigint [PK, NOT NULL]
    - title: character varying(512) [NOT NULL] — 文档标题
    Foreign Keys:
    - document_id -> public.knowledge_document.id

设计要点：

- **纯函数式 Presentation 层**：不依赖 SQLAlchemy / Session / Engine，
  不查数据库，不调 LLM，不读 .env。输入 DTO → 输出 str。
- **确定性 / 稳定性**：相同输入 → 字节级相同输出。排序规则：
  table 按 (schema_name, name)，column 按 ordinal_position，
  foreign key 按 source_column。绝不依赖 dict 顺序 / 时间 / 随机数。
- **不猜业务语义**：无 comment → `Description: —`，字段注释缺失就不输出。
  绝不输出"document_id 是文档ID"这类推断。
- **字符预算**：`max_chars`（默认 12000）是**字符数近似**，
  不是精确 Token 数——当前 MVP 不引入 Tokenizer 依赖（见 ADR）。
- **整行截断**：绝不输出半截字段行；溢出的表保留 Header +
  尽可能多的完整行，后续表全部不输出，末尾加 truncation marker。

安全：
- 只输出 ProjectContext 的 project_name / description 与
  DataSource 的 name / type（均为非敏感 metadata）；
- 绝不输出 password / API Key / 连接串 / .env 内容，
  也绝不直接 dump ProjectContext / DataSource 的 repr。
- 日志只记录 table_count / selected_count / truncated / output_chars /
  max_chars / elapsed_ms，不含 schema 内容本身。

异常体系（遵循项目 Service 风格）：

    SchemaSerializerError (Exception)
        ├── SchemaSerializerInputError   schema=None / 类型错 / max_chars 非法
        └── SchemaSerializationError     tables 指定了不存在的表（不静默忽略）
"""
from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Final

from backend.app.projects.models import DataSource, ProjectContext
from backend.app.services.schema_explorer_service import (
    DatabaseSchema,
    SchemaColumn,
    SchemaForeignKey,
    SchemaTable,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SchemaSerializerError",
    "SchemaSerializerInputError",
    "SchemaSerializationError",
    "SchemaSerializer",
    "DEFAULT_MAX_CHARS",
]


# ============================================================
# 常量
# ============================================================

#: 默认字符预算。注意：这是**字符数近似**而非精确 Token 数，
#: 当前 MVP 刻意不引入 Tokenizer 依赖（见 docs/decisions/Phase 3.7.2 — ADR.md）。
DEFAULT_MAX_CHARS: Final[int] = 12000

#: 无 comment 时表 Description 的占位符（明确"没有注释"，而非猜测含义）
_NO_DESCRIPTION: Final[str] = "—"


# ============================================================
# 异常体系
# ============================================================

class SchemaSerializerError(Exception):
    """Schema Serializer 通用异常基类。"""


class SchemaSerializerInputError(SchemaSerializerError):
    """输入非法：schema 为 None / 类型错误 / max_chars <= 0 / tables 元素非法。

    在产生任何输出之前拒绝。
    """


class SchemaSerializationError(SchemaSerializerError):
    """tables 指定了 DatabaseSchema 中不存在的表（绝不静默忽略）。

    Attributes:
        missing: 缺失的表名列表（按字典序，便于稳定复现）。
    """

    def __init__(self, missing: Sequence[str]) -> None:
        self.missing = sorted(missing)
        super().__init__(
            f"tables 中指定的表在 DatabaseSchema 中不存在: {self.missing}"
        )


# ============================================================
# 工具函数（模块级，纯函数）
# ============================================================

def _sanitize(text: str) -> str:
    """压平换行，保证一行输出一个结构（列/注释/默认值不跨行）。"""
    return " ".join(text.splitlines())


def _format_flags(column: SchemaColumn) -> str:
    flags: list[str] = []
    if column.is_primary_key:
        flags.append("PK")
    flags.append("NOT NULL" if not column.nullable else "NULLABLE")
    if column.default:
        flags.append(f"DEFAULT {_sanitize(column.default)}")
    return f"[{', '.join(flags)}]"


def _format_column(column: SchemaColumn) -> str:
    line = f"- {column.name}: {column.data_type} {_format_flags(column)}"
    if column.description:
        line += f" — {_sanitize(column.description)}"
    return line


def _format_foreign_key(fk: SchemaForeignKey) -> str:
    return (
        f"- {fk.source_column} -> "
        f"{fk.target_schema}.{fk.target_table}.{fk.target_column}"
    )


def _format_table(table: SchemaTable) -> list[str]:
    """单张表 → 若干完整文本行（截断以此为最小单位，绝无半截字段行）。"""
    lines = [
        f"## Table: {table.schema_name}.{table.name}",
        f"Description: "
        f"{_sanitize(table.description) if table.description else _NO_DESCRIPTION}",
        "",
        "Columns:",
    ]
    lines.extend(_format_column(c) for c in _sorted_columns(table.columns))
    if table.foreign_keys:
        lines.append("")
        lines.append("Foreign Keys:")
        lines.extend(
            _format_foreign_key(fk) for fk in _sorted_foreign_keys(table.foreign_keys)
        )
    return lines


def _sorted_columns(columns: Sequence[SchemaColumn]) -> list[SchemaColumn]:
    """稳定排序：按 ordinal_position（并列时按列名，双保险确定性）。"""
    return sorted(columns, key=lambda c: (c.ordinal_position, c.name))


def _sorted_foreign_keys(
    foreign_keys: Sequence[SchemaForeignKey],
) -> list[SchemaForeignKey]:
    return sorted(
        foreign_keys,
        key=lambda fk: (
            fk.source_column, fk.target_schema, fk.target_table, fk.target_column
        ),
    )


def _sorted_tables(tables: Sequence[SchemaTable]) -> list[SchemaTable]:
    """稳定排序：schema → table（与 Schema Explorer 输出顺序一致）。"""
    return sorted(tables, key=lambda t: (t.schema_name, t.name))


# ============================================================
# Service
# ============================================================

class SchemaSerializer:
    """Schema → Prompt Context 序列化器（Phase 3.7.2）。

    只负责：DatabaseSchema DTO → 稳定文本。
    不负责：业务语义、相关表选择（Question→Tables）、SQL 生成。
    """

    def serialize(
        self,
        schema: DatabaseSchema,
        *,
        project: ProjectContext | None = None,
        tables: Sequence[str] | None = None,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> str:
        """把 DatabaseSchema 序列化为 Prompt-friendly 文本。

        Args:
            schema:   Schema Explorer 产出的不可变 DatabaseSchema。
            project:  可选项目上下文（只输出 project_name / description /
                      DataSource name / type，绝不输出敏感信息）。
            tables:   None → 全部表；否则按 "schema.table" 精确匹配，
                      或裸表名在当前 schema 中匹配。不存在的表 →
                      SchemaSerializationError（不静默忽略）。
            max_chars: 字符预算（默认 12000）。溢出按完整行截断，
                      末尾追加 truncation marker。

        Returns:
            稳定文本（相同输入 → 完全相同输出）。

        Raises:
            SchemaSerializerInputError: schema=None / 类型错误 /
                                        max_chars <= 0 / tables 元素非法。
            SchemaSerializationError:  tables 指定了不存在的表。
        """
        start_time = time.perf_counter()
        self._validate(schema=schema, project=project, max_chars=max_chars)

        selected = self._select_tables(schema, tables)
        lines = self._render(schema=schema, project=project, tables=selected)
        result, truncated = self._apply_budget(lines, max_chars=max_chars)

        logger.info(
            "schema serialization completed",
            extra={
                "table_count": len(schema.tables),
                "selected_count": len(selected),
                "truncated": truncated,
                "output_chars": len(result),
                "max_chars": max_chars,
                "elapsed_ms": (time.perf_counter() - start_time) * 1000,
            },
        )
        return result

    # ---------- 校验 ----------

    @staticmethod
    def _validate(
        *,
        schema: DatabaseSchema,
        project: ProjectContext | None,
        max_chars: int,
    ) -> None:
        if not isinstance(schema, DatabaseSchema):
            raise SchemaSerializerInputError(
                f"schema 必须是 DatabaseSchema 实例（当前: {type(schema).__name__}）"
            )
        if project is not None and not isinstance(project, ProjectContext):
            raise SchemaSerializerInputError(
                "project 必须是 ProjectContext 实例或 None"
                f"（当前: {type(project).__name__}）"
            )
        # bool 是 int 子类，必须先排除（与 VectorSearchService 风格一致）
        if isinstance(max_chars, bool) or not isinstance(max_chars, int):
            raise SchemaSerializerInputError(
                f"max_chars 必须是整数（当前: {type(max_chars).__name__}）"
            )
        if max_chars <= 0:
            raise SchemaSerializerInputError(
                f"max_chars 必须为正整数（当前: {max_chars}）"
            )

    # ---------- Table Selection（内存过滤，无 SQL） ----------

    @staticmethod
    def _select_tables(
        schema: DatabaseSchema,
        tables: Sequence[str] | None,
    ) -> list[SchemaTable]:
        """选择并稳定排序要输出的表。

        匹配规则（固定、稳定）：
            1. 优先按 "schema.table" 精确匹配；
            2. 否则按裸表名在 DatabaseSchema 内匹配（当前 DTO 单 schema）；
            3. 都未命中 → 记入 missing，全部校验完后统一抛
               SchemaSerializationError（不静默忽略）。
        """
        all_tables = _sorted_tables(schema.tables)
        if tables is None:
            return all_tables

        for requested in tables:
            if not isinstance(requested, str) or not requested.strip():
                raise SchemaSerializerInputError(
                    f"tables 中的元素必须是非空 str（当前: {requested!r}）"
                )

        by_exact = {f"{t.schema_name}.{t.name}": t for t in all_tables}
        by_name: dict[str, list[SchemaTable]] = {}
        for t in all_tables:
            by_name.setdefault(t.name, []).append(t)

        selected_keys: list[str] = []
        selected: dict[str, SchemaTable] = {}
        missing: list[str] = []
        for requested in tables:
            if requested in by_exact:
                key = requested
            elif requested in by_name:
                # 裸表名：取当前 schema 内的同名表（去重后通常唯一）
                matched = by_name[requested]
                if len(matched) == 1:
                    key = f"{matched[0].schema_name}.{matched[0].name}"
                else:
                    raise SchemaSerializationError([requested])
            else:
                missing.append(requested)
                continue
            if key not in selected:
                selected_keys.append(key)
                selected[key] = by_exact[key]

        if missing:
            raise SchemaSerializationError(missing)

        # 输出顺序永远按 (schema, table) 稳定排序，与选择顺序无关
        return [selected[k] for k in sorted(selected_keys)]

    # ---------- 渲染 ----------

    @staticmethod
    def _render(
        *,
        schema: DatabaseSchema,
        project: ProjectContext | None,
        tables: Sequence[SchemaTable],
    ) -> list[str]:
        """渲染完整文本行（未应用预算；行是截断的最小单位）。"""
        lines: list[str] = []
        if project is not None:
            ds: DataSource = project.data_source
            lines.extend(
                [
                    f"Project: {_sanitize(project.project_name)}",
                    f"Data Source: {_sanitize(ds.name)}",
                    f"Database Type: {_sanitize(ds.type)}",
                ]
            )
            if project.description:
                lines.append(
                    f"Project Description: {_sanitize(project.description)}"
                )
        for table in tables:
            if lines:
                lines.append("")
            lines.extend(_format_table(table))
        return lines

    # ---------- 预算 / 截断 ----------

    @staticmethod
    def _apply_budget(lines: Sequence[str], *, max_chars: int) -> tuple[str, bool]:
        """按整行应用字符预算。

        规则：
            - 逐行累加，任何一行放不下即停止（绝不输出半截行）；
            - 停止后移除尾部空行，追加 truncation marker
              （marker 本身不计入预算，保证截断事实永远可见）；
            - 全部行都放得下 → 不加 marker，not truncated。
        """
        kept: list[str] = []
        used = 0
        truncated = False
        for line in lines:
            line_len = len(line) + (1 if kept else 0)  # 换行符
            if used + line_len > max_chars:
                truncated = True
                break
            kept.append(line)
            used += line_len
        if truncated:
            while kept and not kept[-1]:
                kept.pop()
            kept.append(f"[Schema truncated: max_chars={max_chars}]")
        return "\n".join(kept), truncated
