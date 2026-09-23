"""Business Semantic 模型（Phase 3.7.3）。

核心原则：

    Schema   = database facts（数据库事实，Schema Explorer 产出）
    Semantic = human-defined business meaning（人工定义的业务含义）

AI Core 不知道"WMS 是什么"；项目语义属于 Project（配置文件），
不属于代码。本模块只提供**中立的数据模型**，不含任何 WMS 专属逻辑。

所有内容必须来自人工配置（YAML，见 semantic_loader.py）：
- 禁止从数据库自动推断业务语义；
- 禁止用 LLM 生成业务语义。

模型均为 frozen dataclass（项目 DTO 风格），不依赖 ORM / SQLAlchemy。
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "TableSemantic",
    "ColumnSemantic",
    "BusinessRelationship",
    "ProjectSemantic",
]


@dataclass(frozen=True)
class TableSemantic:
    """表的业务语义（人工配置）。

    Attributes:
        table:         表引用，推荐 "schema.table"（如 public.inventory），
                       裸表名（inventory）也允许，由 Validator 负责对齐解析。
        business_name: 业务名称（如"库存"）。
        description:   业务描述。
        aliases:       业务别名（用于未来问句匹配 / 选表提示）。
    """

    table: str
    business_name: str | None = None
    description: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ColumnSemantic:
    """字段的业务语义（人工配置）。

    Attributes:
        table:         所属表引用（同 TableSemantic.table 规则）。
        column:        字段名（数据库真实列名，非业务名）。
        business_name: 业务名称（如"物料编码"）。
        description:   业务描述。
        aliases:       业务别名（如 物料编码 / 料号 / SKU）。
    """

    table: str
    column: str
    business_name: str | None = None
    description: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class BusinessRelationship:
    """业务关系（描述模型，不做关系推理）。

    Attributes:
        source_table:  源表引用。
        source_column: 源列名。
        target_table:  目标表引用。
        target_column: 目标列名。
        description:  关系描述（如"分片属于文档"）。
    """

    source_table: str
    source_column: str
    target_table: str
    target_column: str
    description: str | None = None


@dataclass(frozen=True)
class ProjectSemantic:
    """一个项目的完整业务语义。

    Attributes:
        tables:        表语义集合。
        columns:       字段语义集合。
        relationships: 业务关系集合。

    空语义（全部为空 tuple）是合法状态——表示该项目尚未配置业务语义。
    """

    tables: tuple[TableSemantic, ...] = ()
    columns: tuple[ColumnSemantic, ...] = ()
    relationships: tuple[BusinessRelationship, ...] = ()
