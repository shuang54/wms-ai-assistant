"""ProjectContext / DataSource DTO（Phase 3.7.1.1）。

设计约束（全部有测试锁定）：

- frozen dataclass：不可变，可作为常量在进程内安全传递；
- 只保存**非敏感 metadata**：project_id / project_name / description /
  DataSource(name, type)；
- **不保存**：密码、用户名、完整 DATABASE_URL、API Key、连接串——
  这些继续由 `backend.app.config` 的各 Settings（.env）管理；
- **不做** `data_source.connect()`：DataSource 只是"身份描述"，
  绝不创建 Engine / Session / Connection；
- Core 层不依赖 WMS / PostgreSQL 以外的业务概念（type 是开放字符串，
  当前默认 "postgresql"，未来可以是 mysql / sqlserver / api，
  但本阶段不实现任何新 Adapter）。
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "DataSource",
    "ProjectContext",
    "ProjectContextError",
]


class ProjectContextError(Exception):
    """Project / DataSource 抽象的输入校验异常（Phase 3.7.1.1）。"""


@dataclass(frozen=True)
class DataSource:
    """数据源身份描述（非敏感 metadata）。

    只回答"当前项目用的是什么类型的数据源"，
    不回答"怎么连接它"（连接配置见 DatabaseSettings），
    更不负责创建连接。

    Attributes:
        name: 数据源逻辑名（同一项目内区分多数据源时使用；
              当前项目只有一个，默认 "primary"）。
        type: 数据源类型标识（开放字符串，非硬编码枚举）：
              当前 "postgresql"；未来可扩展 mysql / sqlserver / api，
              但 Core 层不依赖任何具体类型的实现。
    """

    name: str
    type: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ProjectContextError(
                f"DataSource.name 必须是非空 str（当前: {self.name!r}）"
            )
        if not isinstance(self.type, str) or not self.type.strip():
            raise ProjectContextError(
                f"DataSource.type 必须是非空 str（当前: {self.type!r}）"
            )


@dataclass(frozen=True)
class ProjectContext:
    """当前项目的上下文描述（非敏感 metadata）。

    概念关系：

        ProjectContext
        ├── project_id    项目唯一标识（如 vietnam-wms）
        ├── project_name  项目显示名（如 Vietnam WMS）
        ├── description  项目说明（可为空）
        └── data_source  数据源身份描述（DataSource）

    **不持有**：Engine / Session / 密码 / API Key / .env 内容；
    **不负责**：数据库连接（连接统一走 DatabaseSettings + db.session）。
    未来换项目：只替换本 DTO 的取值（配置层），
    Core（RAG / Tool / Schema Explorer）不感知具体项目身份。
    """

    project_id: str
    project_name: str
    description: str | None
    data_source: DataSource

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not self.project_id.strip():
            raise ProjectContextError(
                f"project_id 必须是非空 str（当前: {self.project_id!r}）"
            )
        if not isinstance(self.project_name, str) or not self.project_name.strip():
            raise ProjectContextError(
                f"project_name 必须是非空 str（当前: {self.project_name!r}）"
            )
        if self.description is not None and not isinstance(self.description, str):
            raise ProjectContextError(
                "description 必须是 str 或 None"
                f"（当前: {type(self.description).__name__}）"
            )
        if not isinstance(self.data_source, DataSource):
            raise ProjectContextError(
                "data_source 必须是 DataSource 实例"
                f"（当前: {type(self.data_source).__name__}）"
            )
