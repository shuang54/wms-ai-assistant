"""Project / Data Source / Semantic 抽象（Phase 3.7.1.1 + 3.7.3）。

本包只建立**代码层面的最小抽象**，为未来更换项目、数据库和业务系统做准备：

    ProjectContext          描述"当前项目"（非敏感 metadata）（3.7.1.1）
        └── DataSource       描述数据源（name + type）（3.7.1.1）

    ProjectSemantic         人工定义的业务语义（3.7.3）
        ├── TableSemantic / ColumnSemantic
        └── BusinessRelationship

职责边界（重要）：

- ProjectContext **不持有** SQLAlchemy Engine / Session / 密码 / API Key，
  **不暴露** .env 内容，**不负责**数据库连接。
- 真正的连接配置继续由现有 `backend.app.config.DatabaseSettings` 管理。
- Core 层不依赖任何具体业务（无 WMSProject / project_type="wms"）；
  当前项目身份只体现在配置默认值（见 context.get_default_project_context）。
- 业务语义属于项目配置文件（semantic/<project_id>.yaml），
  不写死在代码中，禁止自动推断 / LLM 生成
  （见 semantic_loader.ProjectSemanticLoader）。
"""
from __future__ import annotations

from backend.app.projects.context import get_default_project_context
from backend.app.projects.models import (
    DataSource,
    ProjectContext,
    ProjectContextError,
)
from backend.app.projects.semantic import (
    BusinessRelationship,
    ColumnSemantic,
    ProjectSemantic,
    TableSemantic,
)
from backend.app.projects.semantic_loader import (
    ProjectSemanticConfigError,
    ProjectSemanticError,
    ProjectSemanticLoader,
    ProjectSemanticNotFoundError,
)

__all__ = [
    "DataSource",
    "ProjectContext",
    "ProjectContextError",
    "get_default_project_context",
    "TableSemantic",
    "ColumnSemantic",
    "BusinessRelationship",
    "ProjectSemantic",
    "ProjectSemanticLoader",
    "ProjectSemanticError",
    "ProjectSemanticNotFoundError",
    "ProjectSemanticConfigError",
]
