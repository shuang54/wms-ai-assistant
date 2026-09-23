"""默认 ProjectContext 工厂（Phase 3.7.1.1）。

当前项目身份从配置读取（PROJECT_ID / PROJECT_NAME / PROJECT_DESCRIPTION，
见 config.ProjectSettings），数据源身份为固定默认值：

    project_id:   vietnam-wms   （配置默认值，可用环境变量覆盖）
    project_name: Vietnam WMS   （同上）
    data_source: DataSource(name="primary", type="postgresql")

注意：
- WMS 只出现在**配置默认值**这一层（可覆盖、非 Core 依赖）；
- 本工厂不读取、不复制 DATABASE_URL / 密码——连接配置仍在 DatabaseSettings；
- 未来接入新项目 / 新数据源时，替换这里的取值或新增工厂即可，
  上层（RAG / Tool / Schema Explorer）消费的是同一个 ProjectContext DTO。
"""
from __future__ import annotations

from backend.app.config import settings
from backend.app.projects.models import DataSource, ProjectContext

__all__ = [
    "get_default_project_context",
    "DEFAULT_DATA_SOURCE_NAME",
    "DEFAULT_DATA_SOURCE_TYPE",
]

#: 默认数据源逻辑名（当前项目只有一个数据源）
DEFAULT_DATA_SOURCE_NAME: str = "primary"

#: 默认数据源类型（当前仅实现 PostgreSQL；未来可为 mysql / sqlserver / api，
#: 但本阶段不实现任何新 Provider）
DEFAULT_DATA_SOURCE_TYPE: str = "postgresql"


def get_default_project_context() -> ProjectContext:
    """返回当前项目的默认 ProjectContext（非敏感 metadata）。

    取值来源：
        - project_id / project_name / description：
          环境变量 PROJECT_ID / PROJECT_NAME / PROJECT_DESCRIPTION
          （默认 vietnam-wms / Vietnam WMS / 空）
        - data_source：固定默认 DataSource(primary, postgresql)

    绝不包含：DATABASE_URL、密码、用户名、API Key（安全要求 §十五）。
    """
    return ProjectContext(
        project_id=settings.project.project_id,
        project_name=settings.project.project_name,
        description=settings.project.project_description or None,
        data_source=DataSource(
            name=DEFAULT_DATA_SOURCE_NAME,
            type=DEFAULT_DATA_SOURCE_TYPE,
        ),
    )
