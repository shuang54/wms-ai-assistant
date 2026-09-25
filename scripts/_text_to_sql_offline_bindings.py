"""Text-to-SQL 离线共享 Bindings（Phase 3.9.4 / 3.9.5 脚本共用）。

- 把 3.9.4 Fake baseline 与 3.9.5 Real LLM baseline 用同一套 Project Bindings
  跑，确保两者面对的 "Selector / Filter / Composer / Semantic / Schema" 完全一致；
- Schema 字段与真实库一致，不虚构列；Project A/B 隔离 schema
  同样复刻现有测试 fixture 的形状（material_code / qty）。
- **不做生成**：真实 LLM 链路仍由 ``TextToSQLService`` 提供，Fake 由生成脚本内
  DeterministicGenerator 实现；本模块只负责把 Schema / Semantic / ProjectContext
  装好。
"""
from __future__ import annotations

from backend.app.projects.models import DataSource, ProjectContext
from backend.app.projects.semantic import (
    ColumnSemantic,
    ProjectSemantic,
    TableSemantic,
)
from backend.app.projects.semantic_loader import ProjectSemanticLoader
from backend.app.services.schema_explorer_service import (
    DatabaseSchema,
    SchemaColumn,
    SchemaTable,
)
from backend.app.services.text_to_sql_evaluation_service import (
    EvaluationProjectBinding,
)

__all__ = [
    "OFFLINE_SCHEMA_BY_PROJECT",
    "build_offline_project_bindings",
]


#: case.project_id → 该 binding 使用的 schema.schema_name
#  （用于 DeterministicGenerator 区分 Project A/B 共用同一 question 的隔离 case）
OFFLINE_SCHEMA_BY_PROJECT: dict[str, str] = {
    "vietnam-wms": "public",
    "eval-project-a": "project_a",
    "eval-project-b": "project_b",
}


def _column(name: str, position: int) -> SchemaColumn:
    return SchemaColumn(
        name=name, data_type="character varying", nullable=True,
        default=None, ordinal_position=position, is_primary_key=False,
        description=None,
    )


def _table(schema_name: str, name: str, columns: tuple[str, ...]) -> SchemaTable:
    return SchemaTable(
        schema_name=schema_name,
        name=name,
        description=None,
        columns=tuple(_column(c, i + 1) for i, c in enumerate(columns)),
        foreign_keys=(),
    )


def _knowledge_schema() -> DatabaseSchema:
    return DatabaseSchema(
        schema_name="public",
        tables=(
            _table("public", "knowledge_document", (
                "id", "title", "file_name", "file_type", "source",
                "status", "created_at", "updated_at",
            )),
            _table("public", "knowledge_chunk", (
                "id", "document_id", "chunk_index", "content",
                "token_count", "created_at",
            )),
        ),
    )


def _inventory_schema(schema_name: str) -> DatabaseSchema:
    return DatabaseSchema(
        schema_name=schema_name,
        tables=(_table(schema_name, "inventory", ("material_code", "qty")),),
    )


def _inventory_semantic(schema_name: str) -> ProjectSemantic:
    return ProjectSemantic(
        tables=(
            TableSemantic(
                table=f"{schema_name}.inventory",
                business_name="库存",
                aliases=("库存",),
            ),
        ),
        columns=(
            ColumnSemantic(
                table=f"{schema_name}.inventory", column="material_code",
                business_name="物料编码",
            ),
            ColumnSemantic(
                table=f"{schema_name}.inventory", column="qty",
                business_name="库存数量",
            ),
        ),
    )


def _project(project_id: str, name: str) -> ProjectContext:
    return ProjectContext(
        project_id=project_id,
        project_name=name,
        description=None,
        data_source=DataSource(name="primary", type="postgresql"),
    )


def build_offline_project_bindings() -> dict[str, EvaluationProjectBinding]:
    """3 套静态 binding：真实知识库语义 + Project A/B 隔离 schema。"""
    return {
        "vietnam-wms": EvaluationProjectBinding(
            project=_project("vietnam-wms", "Vietnam WMS"),
            schema=_knowledge_schema(),
            semantic=ProjectSemanticLoader().load("vietnam-wms"),
        ),
        "eval-project-a": EvaluationProjectBinding(
            project=_project("eval-project-a", "Project A"),
            schema=_inventory_schema("project_a"),
            semantic=_inventory_semantic("project_a"),
        ),
        "eval-project-b": EvaluationProjectBinding(
            project=_project("eval-project-b", "Project B"),
            schema=_inventory_schema("project_b"),
            semantic=_inventory_semantic("project_b"),
        ),
    }