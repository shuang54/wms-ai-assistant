"""Project Semantic Loader（Phase 3.7.3）。

职责：

    project_id
        ↓
    读取 backend/app/projects/semantic/<project_id>.yaml
        ↓
    结构校验（未知字段拒绝 / 必填校验 / 重复检测）
        ↓
    ProjectSemantic（frozen DTO）

设计约束：

- **不访问数据库**、**不调用 LLM**、不依赖 FastAPI / SQLAlchemy；
- **不读 .env**、不做任何环境变量展开 / 字符串插值——
  配置里的值一律按字面量处理（防止 secrets 注入或意外展开）；
- project_id 只允许 [A-Za-z0-9_-]+（防路径穿越，如 ../../etc/passwd）；
- 配置错误（YAML 语法 / 结构 / 必填 / 未知字段 / 重复定义）
  → ProjectSemanticConfigError，**绝不静默吞掉**；
- 配置文件不存在 → ProjectSemanticNotFoundError（显式，不返回默认值）。

配置文件格式（示例）：

    tables:
      - table: public.inventory
        business_name: 库存
        description: 当前库存明细
        aliases: [库存, 库存明细]
    columns:
      - table: public.inventory
        column: material_code
        business_name: 物料编码
        aliases: [物料编码, 料号, SKU]
    relationships:
      - source_table: public.inventory
        source_column: material_code
        target_table: public.material
        target_column: code
        description: 物料主数据关联
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from backend.app.projects.semantic import (
    BusinessRelationship,
    ColumnSemantic,
    ProjectSemantic,
    TableSemantic,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ProjectSemanticLoader",
    "ProjectSemanticError",
    "ProjectSemanticNotFoundError",
    "ProjectSemanticConfigError",
    "DEFAULT_SEMANTIC_DIR",
]

#: 默认配置目录：与 projects 包同级的 semantic/ 目录
DEFAULT_SEMANTIC_DIR: Path = Path(__file__).parent / "semantic"

#: project_id 白名单（防路径穿越：拒绝 ../ 、/ 、\ 等）
_PROJECT_ID_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

#: 各配置节允许的键（未知键一律拒绝，防止拼写错误静默丢失语义）
_TABLE_KEYS: frozenset[str] = frozenset(
    {"table", "business_name", "description", "aliases"}
)
_COLUMN_KEYS: frozenset[str] = frozenset(
    {"table", "column", "business_name", "description", "aliases"}
)
_RELATIONSHIP_KEYS: frozenset[str] = frozenset(
    {
        "source_table",
        "source_column",
        "target_table",
        "target_column",
        "description",
    }
)


# ============================================================
# 异常体系
# ============================================================

class ProjectSemanticError(Exception):
    """Semantic 加载通用异常基类。"""


class ProjectSemanticNotFoundError(ProjectSemanticError):
    """指定 project 的语义配置文件不存在。

    Attributes:
        project_id: 未找到配置的项目 ID。
    """

    def __init__(self, project_id: str) -> None:
        super().__init__(
            f"项目 {project_id!r} 的语义配置文件不存在"
        )
        self.project_id = project_id


class ProjectSemanticConfigError(ProjectSemanticError):
    """语义配置文件错误：YAML 语法 / 结构 / 必填字段 / 未知字段 / 重复定义。"""


# ============================================================
# Loader
# ============================================================

class ProjectSemanticLoader:
    """从 YAML 配置加载 ProjectSemantic（Phase 3.7.3）。

    纯文件解析 + 结构校验；不查库、不调 LLM、不读 .env。
    """

    def __init__(self, *, base_dir: Path | None = None) -> None:
        """构造 Loader。

        Args:
            base_dir: 语义配置目录；None 时使用 DEFAULT_SEMANTIC_DIR
                      （backend/app/projects/semantic/）。测试可注入临时目录。
        """
        self._base_dir = base_dir if base_dir is not None else DEFAULT_SEMANTIC_DIR

    # ---------- 入口 ----------

    def load(self, project_id: str) -> ProjectSemantic:
        """加载指定项目的业务语义。

        Raises:
            ProjectSemanticNotFoundError: 配置文件不存在。
            ProjectSemanticConfigError:  project_id 非法 / YAML 解析失败 /
                                        结构校验失败。
        """
        if not isinstance(project_id, str) or not _PROJECT_ID_RE.match(project_id):
            raise ProjectSemanticConfigError(
                f"project_id 非法（只允许字母/数字/连字符/下划线）: {project_id!r}"
            )

        path = self._base_dir / f"{project_id}.yaml"
        if not path.is_file():
            raise ProjectSemanticNotFoundError(project_id)

        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ProjectSemanticConfigError(
                f"读取语义配置失败: {path.name}: {type(exc).__name__}"
            ) from exc

        try:
            data = yaml.safe_load(raw_text)
        except yaml.YAMLError as exc:
            raise ProjectSemanticConfigError(
                f"语义配置 YAML 解析失败: {path.name}: {exc}"
            ) from exc

        # 空 YAML（None）→ 空语义（合法：项目尚未配置业务语义）
        if data is None:
            return ProjectSemantic()
        if not isinstance(data, dict):
            raise ProjectSemanticConfigError(
                f"语义配置顶层必须是 mapping: {path.name}"
                f"（当前: {type(data).__name__}）"
            )

        tables = self._parse_tables(data.get("tables"), path.name)
        columns = self._parse_columns(data.get("columns"), path.name)
        relationships = self._parse_relationships(
            data.get("relationships"), path.name
        )
        _reject_unknown_top_level_keys(data, path.name)

        semantic = ProjectSemantic(
            tables=tuple(tables),
            columns=tuple(columns),
            relationships=tuple(relationships),
        )
        logger.info(
            "project semantic loaded",
            extra={
                "project_id": project_id,
                "table_semantics": len(semantic.tables),
                "column_semantics": len(semantic.columns),
                "relationships": len(semantic.relationships),
            },
        )
        return semantic

    # ---------- 各节解析 ----------

    @staticmethod
    def _parse_tables(raw: Any, source: str) -> list[TableSemantic]:
        entries = _as_entry_list(raw, "tables", source)
        parsed: list[TableSemantic] = []
        seen: set[str] = set()
        for i, entry in enumerate(entries):
            _reject_unknown_keys(entry, _TABLE_KEYS, f"tables[{i}]", source)
            table = _require_str(entry, "table", f"tables[{i}]", source)
            if table in seen:
                raise ProjectSemanticConfigError(
                    f"{source}: tables[{i}] 重复定义表语义 {table!r}"
                )
            seen.add(table)
            parsed.append(
                TableSemantic(
                    table=table,
                    business_name=_optional_str(entry, "business_name", f"tables[{i}]", source),
                    description=_optional_str(entry, "description", f"tables[{i}]", source),
                    aliases=_parse_aliases(entry, f"tables[{i}]", source),
                )
            )
        return parsed

    @staticmethod
    def _parse_columns(raw: Any, source: str) -> list[ColumnSemantic]:
        entries = _as_entry_list(raw, "columns", source)
        parsed: list[ColumnSemantic] = []
        seen: set[tuple[str, str]] = set()
        for i, entry in enumerate(entries):
            _reject_unknown_keys(entry, _COLUMN_KEYS, f"columns[{i}]", source)
            table = _require_str(entry, "table", f"columns[{i}]", source)
            column = _require_str(entry, "column", f"columns[{i}]", source)
            if (table, column) in seen:
                raise ProjectSemanticConfigError(
                    f"{source}: columns[{i}] 重复定义字段语义"
                    f" {table}.{column}"
                )
            seen.add((table, column))
            parsed.append(
                ColumnSemantic(
                    table=table,
                    column=column,
                    business_name=_optional_str(entry, "business_name", f"columns[{i}]", source),
                    description=_optional_str(entry, "description", f"columns[{i}]", source),
                    aliases=_parse_aliases(entry, f"columns[{i}]", source),
                )
            )
        return parsed

    @staticmethod
    def _parse_relationships(raw: Any, source: str) -> list[BusinessRelationship]:
        entries = _as_entry_list(raw, "relationships", source)
        parsed: list[BusinessRelationship] = []
        for i, entry in enumerate(entries):
            _reject_unknown_keys(entry, _RELATIONSHIP_KEYS, f"relationships[{i}]", source)
            parsed.append(
                BusinessRelationship(
                    source_table=_require_str(entry, "source_table", f"relationships[{i}]", source),
                    source_column=_require_str(entry, "source_column", f"relationships[{i}]", source),
                    target_table=_require_str(entry, "target_table", f"relationships[{i}]", source),
                    target_column=_require_str(entry, "target_column", f"relationships[{i}]", source),
                    description=_optional_str(entry, "description", f"relationships[{i}]", source),
                )
            )
        return parsed


# ============================================================
# 结构校验辅助（模块级纯函数）
# ============================================================

def _as_entry_list(raw: Any, section: str, source: str) -> list[dict[str, Any]]:
    """某节配置 → list[dict]；None（未配置）→ 空 list。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ProjectSemanticConfigError(
            f"{source}: {section} 必须是列表（当前: {type(raw).__name__}）"
        )
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ProjectSemanticConfigError(
                f"{source}: {section}[{i}] 必须是 mapping"
                f"（当前: {type(entry).__name__}）"
            )
    return raw


def _reject_unknown_top_level_keys(
    data: dict[str, Any], source: str
) -> None:
    allowed = {"tables", "columns", "relationships"}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ProjectSemanticConfigError(
            f"{source}: 存在未知顶层配置项 {unknown}（允许: {sorted(allowed)}）"
        )


def _reject_unknown_keys(
    entry: dict[str, Any], allowed: frozenset[str], where: str, source: str
) -> None:
    unknown = sorted(set(entry) - allowed)
    if unknown:
        raise ProjectSemanticConfigError(
            f"{source}: {where} 存在未知字段 {unknown}（允许: {sorted(allowed)}）"
        )


def _require_str(entry: dict[str, Any], key: str, where: str, source: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProjectSemanticConfigError(
            f"{source}: {where} 缺少必填字段 {key!r}（非空字符串）"
        )
    return value


def _optional_str(
    entry: dict[str, Any], key: str, where: str, source: str
) -> str | None:
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProjectSemanticConfigError(
            f"{source}: {where} 的 {key!r} 必须是非空字符串或省略"
        )
    return value


def _parse_aliases(entry: dict[str, Any], where: str, source: str) -> tuple[str, ...]:
    raw = entry.get("aliases")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ProjectSemanticConfigError(
            f"{source}: {where} 的 aliases 必须是字符串列表"
            f"（当前: {type(raw).__name__}）"
        )
    for i, alias in enumerate(raw):
        if not isinstance(alias, str) or not alias.strip():
            raise ProjectSemanticConfigError(
                f"{source}: {where}.aliases[{i}] 必须是非空字符串"
            )
    return tuple(raw)
