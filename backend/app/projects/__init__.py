"""Project / Data Source 抽象（Phase 3.7.1.1）。

本包只建立**代码层面的最小抽象**，为未来更换项目、数据库和业务系统做准备：

    ProjectContext          描述"当前项目"（非敏感 metadata）
        └── DataSource       描述项目使用的数据源（name + type）

职责边界（重要）：

- ProjectContext **不持有** SQLAlchemy Engine / Session / 密码 / API Key，
  **不暴露** .env 内容，**不负责**数据库连接。
- 真正的连接配置继续由现有 `backend.app.config.DatabaseSettings` 管理。
- Core 层不依赖任何具体业务（无 WMSProject / project_type="wms"）；
  当前项目身份只体现在配置默认值（见 context.get_default_project_context）。

本阶段明确**不做**：多租户、项目 CRUD、Project API、前端选择器、
动态建库 / 切库、Redis、配置中心（详见 docs/decisions/Phase 3.7.1.1 — ADR.md）。
"""
from __future__ import annotations

from backend.app.projects.context import get_default_project_context
from backend.app.projects.models import (
    DataSource,
    ProjectContext,
    ProjectContextError,
)

__all__ = [
    "DataSource",
    "ProjectContext",
    "ProjectContextError",
    "get_default_project_context",
]
