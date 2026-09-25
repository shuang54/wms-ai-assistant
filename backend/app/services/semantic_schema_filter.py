"""Semantic ↔ Schema 对齐过滤器（Phase 3.9.2）。

职责（单一、纯内存）：

    ProjectSemantic（人工配置，可能配置过宽 / 已过期）
        +
    DatabaseSchema（数据库事实，Source of Truth）
        +
    allowed_tables（本次 Text-to-SQL 选中的表）
        ↓ SemanticSchemaFilter.filter()
    Filtered ProjectSemantic（只能解释"本次可见"的表与列）

为什么需要：业务语义是人工配置，可能引用：
    - 已改名 / 已删除的表或列；
    - 本次 Text-to-SQL 并未选中的合法表（Selector 决定可见范围）。
把这些语义原样喂给 LLM，会增加"生成不存在的表/字段"的风险，
也会白白拉长 Prompt。本模块只做**减法**：

    Schema → 限制 Semantic      （允许）
    Semantic → 推断 Schema      （禁止，本阶段不做任何同义词推断/猜测）

与 Phase 3.7.3 既有组件的关系：

    SemanticSchemaValidator  只"报告"失配（validate / validate_or_raise）
    SemanticSchemaFilter     只"裁剪"语义（filter，非破坏性）

严格纪律：
- **非破坏性**：不修改传入的 ProjectSemantic / DatabaseSchema（frozen DTO），
  只返回新的 ProjectSemantic；原对象在调用前后完全一致（Test 6）；
- **纯内存**：不查数据库、不调 LLM / Embedding / Reranker、不读 .env、
  不 import SQLAlchemy；
- **确定性**：相同输入 → 相同输出；输出按稳定键排序，与输入顺序无关；
- **空结果安全**：全部被过滤 → 空 ProjectSemantic（序列化后为空文本），
  绝不抛异常（Step 5）；
- **禁止推断**：`warehouse_name` 不在 Schema 中 → 删除，
  绝不猜测它对应 `name`，绝不在输出里写"可能是 name"。

引用解析规则（与 SemanticSchemaValidator / SchemaSerializer 一致）：
- "schema.table"：精确匹配；
- 裸表名：在当前 schema 内唯一匹配。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Sequence

from backend.app.projects.semantic import (
    BusinessRelationship,
    ColumnSemantic,
    ProjectSemantic,
    TableSemantic,
)
from backend.app.services.schema_explorer_service import (
    DatabaseSchema,
    SchemaColumn,
    SchemaTable,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SemanticSchemaFilterError",
    "SemanticSchemaFilterInputError",
    "SemanticSchemaFilter",
]


# ============================================================
# 异常体系
# ============================================================

class SemanticSchemaFilterError(Exception):
    """Semantic-Schema Filter 通用异常基类。"""


class SemanticSchemaFilterInputError(SemanticSchemaFilterError):
    """输入非法：semantic / schema / allowed_tables 类型或取值错误。

    在产生任何输出之前拒绝（不静默降级）。
    """


# ============================================================
# Service
# ============================================================

class SemanticSchemaFilter:
    """ProjectSemantic × DatabaseSchema × allowed_tables → 过滤后的语义。

    只负责：裁剪业务语义到"当前 Schema 中真实存在 + 本次被选中"的范围。
    不负责：序列化、选表、SQL 生成、语义推断。
    """

    def filter(
        self,
        semantic: ProjectSemantic,
        schema: DatabaseSchema,
        *,
        allowed_tables: Sequence[str] | None = None,
    ) -> ProjectSemantic:
        """按当前 Schema 与本次选中表过滤业务语义（非破坏性）。

        Args:
            semantic:       项目业务语义（空语义合法）。
            schema:         数据库事实（Source of Truth）。
            allowed_tables: 本次 Text-to-SQL 允许引用的表
                            （RelevantTableSelector 产出，"schema.table"）。
                            - ``None`` 或空序列 → 不按选中范围限制，
                              只过滤 Schema 中不存在的 table/column/relationship。
                              （空序列与 Composer 的 ``tables=allowed_tables
                              or None`` 降级行为保持一致：零选中时 SQL
                              可见范围是全库 schema，语义也随之解释全库。）
                            - 非空 → 只保留 Schema 中存在 **且** 被选中的表
                              的语义（Step 2.3）。

        Returns:
            新的 ProjectSemantic（原对象绝不被修改）；
            全部被过滤 → ``ProjectSemantic()``（空语义是合法状态）。

        Raises:
            SemanticSchemaFilterInputError: 输入类型/取值非法。
        """
        start_time = time.perf_counter()
        self._validate(
            semantic=semantic, schema=schema, allowed_tables=allowed_tables
        )

        selected_keys = self._selected_keys(schema, allowed_tables)

        tables = self._filter_tables(semantic.tables, schema, selected_keys)
        columns = self._filter_columns(semantic.columns, schema, selected_keys)
        relationships = self._filter_relationships(
            semantic.relationships, schema, selected_keys
        )

        result = ProjectSemantic(
            tables=tuple(tables),
            columns=tuple(columns),
            relationships=tuple(relationships),
        )

        logger.info(
            "semantic-schema filter completed",
            extra={
                "table_semantics_in": len(semantic.tables),
                "table_semantics_out": len(result.tables),
                "column_semantics_in": len(semantic.columns),
                "column_semantics_out": len(result.columns),
                "relationships_in": len(semantic.relationships),
                "relationships_out": len(result.relationships),
                "allowed_tables": (
                    None
                    if allowed_tables is None
                    else len(allowed_tables)
                ),
                "elapsed_ms": (time.perf_counter() - start_time) * 1000,
            },
        )
        return result

    # ---------- 校验（先于一切处理） ----------

    @staticmethod
    def _validate(
        *,
        semantic: ProjectSemantic,
        schema: DatabaseSchema,
        allowed_tables: Sequence[str] | None,
    ) -> None:
        if not isinstance(semantic, ProjectSemantic):
            raise SemanticSchemaFilterInputError(
                "semantic 必须是 ProjectSemantic 实例（空语义请传 "
                f"ProjectSemantic()；当前: {type(semantic).__name__}）"
            )
        if not isinstance(schema, DatabaseSchema):
            raise SemanticSchemaFilterInputError(
                "schema 必须是 DatabaseSchema 实例"
                f"（当前: {type(schema).__name__}）"
            )
        if allowed_tables is None:
            return
        if isinstance(allowed_tables, (str, bytes)):
            raise SemanticSchemaFilterInputError(
                "allowed_tables 必须是表名序列或 None，而不是单个字符串"
            )
        for entry in allowed_tables:
            if not isinstance(entry, str):
                raise SemanticSchemaFilterInputError(
                    f"allowed_tables 每项必须是 str（当前: {type(entry).__name__}）"
                )

    # ---------- 选中表索引 ----------

    @staticmethod
    def _selected_keys(
        schema: DatabaseSchema, allowed_tables: Sequence[str] | None
    ) -> frozenset[str] | None:
        """把 allowed_tables 解析为 "schema.table" 键集合。

        - ``None`` / 空序列 → 返回 None（不按选中范围限制，
          与 Composer 零选中降级为全库 schema 的行为一致）；
        - 解析不到的条目（Schema 中不存在的表）不会产生键，
          因此任何语义都无法匹配它——绝不静默放大范围。
        """
        if not allowed_tables:  # None 或空序列 → 不按选中范围限制
            return None
        keys: set[str] = set()
        for entry in allowed_tables:
            table = _resolve_table(schema, entry)
            if table is not None:
                keys.add(f"{table.schema_name}.{table.name}")
        return frozenset(keys)

    # ---------- 三类过滤 ----------

    @staticmethod
    def _filter_tables(
        table_semantics: Sequence[TableSemantic],
        schema: DatabaseSchema,
        selected_keys: frozenset[str] | None,
    ) -> list[TableSemantic]:
        kept: list[TableSemantic] = []
        for ts in sorted(table_semantics, key=lambda t: t.table):
            table = _resolve_table(schema, ts.table)
            if table is None:
                continue  # Schema 中不存在的表（Step 2.1）
            if selected_keys is not None and (
                f"{table.schema_name}.{table.name}" not in selected_keys
            ):
                continue  # 合法但本次未选中（Step 2.3）
            kept.append(ts)
        return kept

    @staticmethod
    def _filter_columns(
        column_semantics: Sequence[ColumnSemantic],
        schema: DatabaseSchema,
        selected_keys: frozenset[str] | None,
    ) -> list[ColumnSemantic]:
        kept: list[ColumnSemantic] = []
        for cs in sorted(column_semantics, key=lambda c: (c.table, c.column)):
            table = _resolve_table(schema, cs.table)
            if table is None:
                continue  # 表不存在 → 字段语义不可能成立
            if selected_keys is not None and (
                f"{table.schema_name}.{table.name}" not in selected_keys
            ):
                continue
            if _find_column(table, cs.column) is None:
                continue  # 字段在真实 Schema 中不存在（Step 2.2，禁止猜测）
            kept.append(cs)
        return kept

    @staticmethod
    def _filter_relationships(
        relationships: Sequence[BusinessRelationship],
        schema: DatabaseSchema,
        selected_keys: frozenset[str] | None,
    ) -> list[BusinessRelationship]:
        kept: list[BusinessRelationship] = []
        ordered = sorted(
            relationships,
            key=lambda r: (
                r.source_table, r.source_column,
                r.target_table, r.target_column,
            ),
        )
        for rel in ordered:
            if not SemanticSchemaFilter._relationship_holds(
                rel, schema, selected_keys
            ):
                continue  # 任一端表/列不存在或未选中 → 关系不成立（Step 2.4）
            kept.append(rel)
        return kept

    @staticmethod
    def _relationship_holds(
        rel: BusinessRelationship,
        schema: DatabaseSchema,
        selected_keys: frozenset[str] | None,
    ) -> bool:
        """关系两端（表 + 列）都必须真实存在，且两端表都被本次选中。"""
        for table_ref, column in (
            (rel.source_table, rel.source_column),
            (rel.target_table, rel.target_column),
        ):
            table = _resolve_table(schema, table_ref)
            if table is None:
                return False
            if selected_keys is not None and (
                f"{table.schema_name}.{table.name}" not in selected_keys
            ):
                return False
            if _find_column(table, column) is None:
                return False
        return True


# ============================================================
# 引用解析（模块级纯函数）
#
# 规则与 SemanticSchemaValidator（Phase 3.7.3）/ SchemaSerializer
# 完全一致：这里刻意保持同构实现，避免过滤与校验两套规则漂移；
# 测试用 "过滤后的语义必通过 SemanticSchemaValidator" 交叉锁定。
# ============================================================

def _resolve_table(schema: DatabaseSchema, reference: str) -> SchemaTable | None:
    """解析表引用：精确 "schema.table" 优先，裸表名在当前 schema 内唯一匹配。"""
    for t in schema.tables:
        if f"{t.schema_name}.{t.name}" == reference:
            return t
    bare = [t for t in schema.tables if t.name == reference]
    return bare[0] if len(bare) == 1 else None


def _find_column(table: SchemaTable, column: str) -> SchemaColumn | None:
    for c in table.columns:
        if c.name == column:
            return c
    return None
