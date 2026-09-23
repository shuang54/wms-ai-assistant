"""Business Semantic Serializer（Phase 3.7.3）。

职责（与 SchemaSerializer 严格分工）：

    SchemaSerializer            → 数据库事实（类型 / nullable / PK / FK）
    BusinessSemanticSerializer  → 人工定义的业务含义（本模块）
        ProjectSemantic
                ↓ serialize()
        AI-readable Business Semantic Context

输出示例：

    Business Semantics:

    ## Table: public.knowledge_document
    Business Name: 知识文档
    Description: 知识库文档元数据表
    Aliases:
    - 知识文档
    - 文档

    Columns:
    - title
      Business Name: 文档标题
      Description: 文档展示标题
      Aliases: 标题

    ## Business Relationships
    - public.knowledge_chunk.document_id -> public.knowledge_document.id — 分片属于文档

要求（与 SchemaSerializer 一致的纪律）：
- **稳定排序**：table 按 key、column 按 (table, column)、relationship 按
  四元组排序——相同输入字节级相同输出，与输入顺序无关；
- **不猜业务含义**：只输出配置中人工填写的字段，缺失字段整行省略；
- **不输出 secrets**：不读 .env、不做环境变量展开（配置值一律字面量）；
- **不依赖 ORM**：不 import SQLAlchemy、不访问数据库、不调 LLM。
"""
from __future__ import annotations

import logging
import time
from typing import Final

from backend.app.projects.semantic import (
    BusinessRelationship,
    ColumnSemantic,
    ProjectSemantic,
    TableSemantic,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BusinessSemanticSerializer",
    "SEMANTIC_CONTEXT_HEADER",
]

#: 输出首行标题（Composer 据此识别语义段落）
SEMANTIC_CONTEXT_HEADER: Final[str] = "Business Semantics:"


class BusinessSemanticSerializer:
    """ProjectSemantic → Prompt Context（Phase 3.7.3）。

    只负责：语义 DTO → 稳定文本。
    不负责：Schema 事实（SchemaSerializer 的职责）、SQL 生成、选表。
    """

    def serialize(self, semantic: ProjectSemantic) -> str:
        """把项目业务语义序列化为 AI-readable 文本。

        Args:
            semantic: Loader 产出的 ProjectSemantic（空语义合法）。

        Returns:
            稳定文本；空语义（无表 / 列语义且无关系）返回 ""。
        """
        start_time = time.perf_counter()
        if not isinstance(semantic, ProjectSemantic):
            raise TypeError(
                f"semantic 必须是 ProjectSemantic 实例（当前: {type(semantic).__name__}）"
            )

        # ---- 按表分组：表语义 + 该表的字段语义 ----
        columns_by_table: dict[str, list[ColumnSemantic]] = {}
        for cs in _sorted_columns(semantic.columns):
            columns_by_table.setdefault(cs.table, []).append(cs)

        table_keys = sorted(
            {ts.table for ts in semantic.tables}
            | set(columns_by_table)
        )

        lines: list[str] = [SEMANTIC_CONTEXT_HEADER]
        table_semantics = {ts.table: ts for ts in semantic.tables}
        for key in table_keys:
            if len(lines) > 1:
                lines.append("")
            lines.extend(
                _render_table(
                    table_semantics.get(key),
                    columns_by_table.get(key, ()),
                )
            )

        relationships = _sorted_relationships(semantic.relationships)
        if relationships:
            if len(lines) > 1:
                lines.append("")
            lines.append("## Business Relationships")
            lines.extend(_render_relationship(rel) for rel in relationships)

        result = "\n".join(lines) if len(lines) > 1 else ""
        logger.info(
            "business semantic serialization completed",
            extra={
                "table_semantics": len(semantic.tables),
                "column_semantics": len(semantic.columns),
                "relationships": len(semantic.relationships),
                "output_chars": len(result),
                "elapsed_ms": (time.perf_counter() - start_time) * 1000,
            },
        )
        return result


# ============================================================
# 渲染辅助（模块级纯函数）
# ============================================================

def _sanitize(text: str) -> str:
    """压平换行，保证一行一结构（与 SchemaSerializer 相同规则）。"""
    return " ".join(text.splitlines())


def _sorted_columns(
    columns: tuple[ColumnSemantic, ...] | list[ColumnSemantic],
) -> list[ColumnSemantic]:
    return sorted(columns, key=lambda c: (c.table, c.column))


def _sorted_relationships(
    relationships: tuple[BusinessRelationship, ...],
) -> list[BusinessRelationship]:
    return sorted(
        relationships,
        key=lambda r: (r.source_table, r.source_column,
                       r.target_table, r.target_column),
    )


def _render_table(
    table: TableSemantic | None,
    columns: list[ColumnSemantic] | tuple[ColumnSemantic, ...],
) -> list[str]:
    """渲染一个表的语义段（表头必出；字段段仅当有字段语义）。"""
    lines = [f"## Table: {table.table if table else columns[0].table}"]
    if table is not None:
        if table.business_name:
            lines.append(f"Business Name: {_sanitize(table.business_name)}")
        if table.description:
            lines.append(f"Description: {_sanitize(table.description)}")
        if table.aliases:
            lines.append("Aliases:")
            lines.extend(f"- {_sanitize(a)}" for a in table.aliases)
    if columns:
        lines.append("")
        lines.append("Columns:")
        for cs in columns:
            lines.extend(_render_column(cs))
    return lines


def _render_column(cs: ColumnSemantic) -> list[str]:
    """渲染一个字段语义段（两行式：`- name` + 缩进属性行；缺失属性行省略）。"""
    lines = [f"- {cs.column}"]
    if cs.business_name:
        lines.append(f"  Business Name: {_sanitize(cs.business_name)}")
    if cs.description:
        lines.append(f"  Description: {_sanitize(cs.description)}")
    if cs.aliases:
        lines.append(f"  Aliases: {', '.join(_sanitize(a) for a in cs.aliases)}")
    return lines


def _render_relationship(rel: BusinessRelationship) -> str:
    line = (
        f"- {rel.source_table}.{rel.source_column}"
        f" -> {rel.target_table}.{rel.target_column}"
    )
    if rel.description:
        line += f" — {_sanitize(rel.description)}"
    return line
