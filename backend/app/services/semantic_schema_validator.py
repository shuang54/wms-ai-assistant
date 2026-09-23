"""Semantic ↔ Schema 对齐校验器（Phase 3.7.3）。

职责：

    ProjectSemantic（人工配置）
        +
    DatabaseSchema（数据库事实）
        ↓
    SemanticSchemaValidator
        ↓
    全部对齐 / 全部失配清单（一次返回，绝不发现第一个就停）

为什么需要：语义配置引用的表 / 列可能在后续数据库演进中消失
（改名、删除、迁移）。越早发现失配，Text-to-SQL 越不容易拿过期
语义生成错误 SQL。

重要原则：
- **不自动修复**：配置写了 public.inventory.material_code 而库里是
  mat_code → 报错，不改配置、不改库；
- **不查数据库**：只消费 DatabaseSchema DTO（内存校验）；
- **不调 LLM**、不依赖 ORM / FastAPI / .env。

引用解析规则（与 SchemaSerializer 的表选择规则一致）：
- "schema.table"：精确匹配；
- 裸表名：在 DatabaseSchema 当前 schema 内匹配。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.app.projects.semantic import ProjectSemantic
from backend.app.services.schema_explorer_service import (
    DatabaseSchema,
    SchemaColumn,
    SchemaTable,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SemanticMismatch",
    "SemanticSchemaMisalignmentError",
    "SemanticSchemaValidator",
]


@dataclass(frozen=True)
class SemanticMismatch:
    """单条失配记录。

    Attributes:
        kind:      失配类型：unknown_table / unknown_column /
                   unknown_relationship_source / unknown_relationship_target。
        reference: 失配引用（如 public.inventory / public.inventory.material_code）。
        message:   人类可读说明（含全部线索，可直接进入日志 / 告警）。
    """

    kind: str
    reference: str
    message: str


class SemanticSchemaMisalignmentError(Exception):
    """语义与数据库结构不一致（聚合所有失配项后抛出）。

    Attributes:
        mismatches: 全部失配记录。
    """

    def __init__(self, mismatches: tuple[SemanticMismatch, ...]) -> None:
        self.mismatches = mismatches
        details = "\n".join(f"  - {m.message}" for m in mismatches)
        super().__init__(
            f"Semantic 与 DatabaseSchema 不一致（{len(mismatches)} 处）:\n{details}"
        )


class SemanticSchemaValidator:
    """Semantic ↔ Schema 对齐校验（纯内存，Phase 3.7.3）。"""

    def validate(
        self,
        semantic: ProjectSemantic,
        schema: DatabaseSchema,
    ) -> tuple[SemanticMismatch, ...]:
        """校验并返回**全部**失配项（空 tuple = 完全对齐）。

        检查项：
            1. 每个 TableSemantic.table 存在于 DatabaseSchema；
            2. 每个 ColumnSemantic 的 table 与 column 均存在；
            3. 每个 BusinessRelationship 的 source/target（表 + 列）均存在。
        """
        mismatches: list[SemanticMismatch] = []

        for ts in sorted(semantic.tables, key=lambda t: t.table):
            if _resolve_table(schema, ts.table) is None:
                mismatches.append(
                    SemanticMismatch(
                        kind="unknown_table",
                        reference=ts.table,
                        message=(
                            f"Semantic references unknown table: {ts.table}"
                        ),
                    )
                )

        for cs in sorted(
            semantic.columns, key=lambda c: (c.table, c.column)
        ):
            table = _resolve_table(schema, cs.table)
            if table is None:
                mismatches.append(
                    SemanticMismatch(
                        kind="unknown_table",
                        reference=cs.table,
                        message=(
                            f"Semantic references unknown table: {cs.table}"
                            f"（字段语义 {cs.table}.{cs.column}）"
                        ),
                    )
                )
            elif _find_column(table, cs.column) is None:
                mismatches.append(
                    SemanticMismatch(
                        kind="unknown_column",
                        reference=f"{cs.table}.{cs.column}",
                        message=(
                            f"Semantic references unknown column:"
                            f" {cs.table}.{cs.column}"
                        ),
                    )
                )

        for rel in sorted(
            semantic.relationships,
            key=lambda r: (r.source_table, r.source_column,
                           r.target_table, r.target_column),
        ):
            for side, table_ref, column in (
                ("source", rel.source_table, rel.source_column),
                ("target", rel.target_table, rel.target_column),
            ):
                table = _resolve_table(schema, table_ref)
                if table is None:
                    mismatches.append(
                        SemanticMismatch(
                            kind=f"unknown_relationship_{side}",
                            reference=table_ref,
                            message=(
                                f"Semantic relationship {side} table 不存在:"
                                f" {table_ref}"
                                f"（{rel.source_table}.{rel.source_column}"
                                f" -> {rel.target_table}.{rel.target_column}）"
                            ),
                        )
                    )
                elif _find_column(table, column) is None:
                    mismatches.append(
                        SemanticMismatch(
                            kind=f"unknown_relationship_{side}",
                            reference=f"{table_ref}.{column}",
                            message=(
                                f"Semantic relationship {side} column 不存在:"
                                f" {table_ref}.{column}"
                            ),
                        )
                    )

        result = tuple(mismatches)
        logger.info(
            "semantic-schema validation completed",
            extra={
                "table_semantics": len(semantic.tables),
                "column_semantics": len(semantic.columns),
                "relationships": len(semantic.relationships),
                "mismatch_count": len(result),
            },
        )
        return result

    def validate_or_raise(
        self,
        semantic: ProjectSemantic,
        schema: DatabaseSchema,
    ) -> None:
        """校验；存在任何失配 → SemanticSchemaMisalignmentError（聚合全部）。"""
        mismatches = self.validate(semantic, schema)
        if mismatches:
            raise SemanticSchemaMisalignmentError(mismatches)


# ============================================================
# 引用解析（模块级纯函数；表选择规则与 SchemaSerializer 一致）
# ============================================================

def _resolve_table(schema: DatabaseSchema, reference: str) -> SchemaTable | None:
    """解析表引用："schema.table" 精确匹配优先，裸表名在当前 schema 内匹配。"""
    exact = {
        f"{t.schema_name}.{t.name}": t for t in schema.tables
    }
    if reference in exact:
        return exact[reference]
    bare = [t for t in schema.tables if t.name == reference]
    return bare[0] if len(bare) == 1 else None


def _find_column(table: SchemaTable, column: str) -> SchemaColumn | None:
    for c in table.columns:
        if c.name == column:
            return c
    return None
