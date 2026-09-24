"""Project Registry（Phase 3.8.1）。

职责（任务书 §六）：

    project_id
        ↓
    ProjectRegistry.get(project_id)
        ↓
    ProjectRegistration
        ├── context     ProjectContext（非敏感 metadata，含 DataSource 身份）
        └── schema_name 该项目业务数据所在的数据库 schema

设计约束：

- **ProjectRegistration 不含敏感信息**：没有 password / api_key / token /
  secret / connection_string —— 连接配置由
  ``engine_provider.DatabaseEngineProvider`` 从服务器端注册表解析
  （任务书 §五 / §七 / §八）；
- **project_id 只能选择服务器端已注册的项目**：未知 project_id →
  ``ProjectNotFoundError``（clear error，不静默回退默认项目）；
- InMemory 实现：MVP 阶段代码级注册（任务书 §十八：
  "项目注册配置可以先采用代码级测试注册"），
  生产多项目连接配置只设计接口（本阶段不接真实第二生产库）；
- 默认注册表（``get_default_project_registry``）惰性创建，包含
  配置默认项目（settings.project.project_id，默认 vietnam-wms）→
  DataSource(primary, postgresql) + schema "public"，
  保证 "project_id 未提供 / 提供默认值" 的旧行为完全不变（任务书 §十九）。
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Final, Protocol

from backend.app.projects.context import get_default_project_context
from backend.app.projects.models import ProjectContext

logger = logging.getLogger(__name__)

__all__ = [
    "ProjectRegistration",
    "ProjectRegistry",
    "ProjectRegistryError",
    "ProjectNotFoundError",
    "InMemoryProjectRegistry",
    "DEFAULT_BUSINESS_SCHEMA",
    "get_default_project_registry",
]

#: 注册条目未显式指定 schema 时的默认业务 schema
#: （与 SchemaExplorerService.DEFAULT_SCHEMA 对齐：当前项目业务表在 public）
DEFAULT_BUSINESS_SCHEMA: Final[str] = "public"

#: project_id 白名单（与 ProjectSemanticLoader 一致：防路径穿越 / 注入）
_PROJECT_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

#: schema 名白名单（与 SchemaExplorerService._SCHEMA_NAME_RE 对齐：
#: 未加引号 PostgreSQL 标识符；schema 名会进入 SQL，必须严格校验）
_SCHEMA_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# ============================================================
# 异常体系
# ============================================================

class ProjectRegistryError(Exception):
    """Project Registry 通用异常（输入非法 / 重复注册等）。"""


class ProjectNotFoundError(ProjectRegistryError):
    """请求的 project_id 未在服务器端注册（clear error，不静默回退）。

    Attributes:
        project_id: 未注册的项目 ID。
    """

    def __init__(self, project_id: str) -> None:
        super().__init__(f"项目 {project_id!r} 未注册（project_id 只能选择服务器端已注册的项目）")
        self.project_id = project_id


# ============================================================
# DTO
# ============================================================

@dataclass(frozen=True)
class ProjectRegistration:
    """项目注册条目（非敏感）。

    Attributes:
        context:     项目上下文（含 DataSource 身份描述，
                     Engine 由 DatabaseEngineProvider 解析）。
        schema_name: 该项目业务数据所在的数据库 schema
                     （如 "public" / "project_a"）。
                     **不包含**任何连接凭据。
    """

    context: ProjectContext
    schema_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.context, ProjectContext):
            raise ProjectRegistryError(
                "ProjectRegistration.context 必须是 ProjectContext 实例"
                f"（当前: {type(self.context).__name__}）"
            )
        if not isinstance(self.schema_name, str) or not self.schema_name.strip():
            raise ProjectRegistryError(
                f"schema_name 必须是非空 str（当前: {self.schema_name!r}）"
            )
        if not _SCHEMA_NAME_RE.match(self.schema_name):
            raise ProjectRegistryError(
                f"schema_name {self.schema_name!r} 不是合法的 PostgreSQL 标识符，已拒绝"
            )


# ============================================================
# Protocol（任务书 §六）
# ============================================================

class ProjectRegistry(Protocol):
    """项目注册中心协议（Phase 3.8.1 引入）。

    上层（API / Orchestrator 工厂）只依赖本协议：
    ``get(project_id) -> ProjectRegistration``。
    未来可替换为数据库 / YAML / 配置中心实现。
    """

    def get(self, project_id: str) -> ProjectRegistration:
        """解析已注册项目；未注册 → ProjectNotFoundError。"""
        ...


# ============================================================
# In-Memory 实现
# ============================================================

class InMemoryProjectRegistry:
    """最小内存注册表（MVP；代码级注册，任务书 §十八）。

    线程安全（注册 / 读取共享一把锁）；重复注册 → ProjectRegistryError
    （防意外覆盖已注册项目的数据源；测试用独立实例或先 unregister）。
    """

    def __init__(self) -> None:
        self._projects: dict[str, ProjectRegistration] = {}
        self._lock = threading.Lock()

    # ---------- 写入（服务器端 / 测试 setup） ----------

    def register(
        self,
        project_id: str,
        registration: ProjectRegistration,
    ) -> None:
        """注册项目（重复注册 → ProjectRegistryError，不覆盖）。"""
        _validate_project_id(project_id)
        if not isinstance(registration, ProjectRegistration):
            raise ProjectRegistryError(
                "registration 必须是 ProjectRegistration 实例"
                f"（当前: {type(registration).__name__}）"
            )
        with self._lock:
            if project_id in self._projects:
                raise ProjectRegistryError(
                    f"项目 {project_id!r} 已注册；如需替换请先 unregister"
                )
            self._projects[project_id] = registration
        logger.info(
            "project registered",
            extra={
                "project_id": project_id,
                "schema_name": registration.schema_name,
                "data_source_name": registration.context.data_source.name,
            },
        )

    def unregister(self, project_id: str) -> None:
        """移除注册（测试清理 / 配置热更新；未注册 → ProjectRegistryError）。"""
        _validate_project_id(project_id)
        with self._lock:
            if project_id not in self._projects:
                raise ProjectRegistryError(f"项目 {project_id!r} 未注册，无法移除")
            del self._projects[project_id]
        logger.info("project unregistered", extra={"project_id": project_id})

    # ---------- 读取 ----------

    def get(self, project_id: str) -> ProjectRegistration:
        """返回已注册项目的注册条目。

        Raises:
            ProjectRegistryError: project_id 空 / 纯空白 / 非法格式。
            ProjectNotFoundError: project_id 未注册。
        """
        _validate_project_id(project_id)
        with self._lock:
            registration = self._projects.get(project_id)
        if registration is None:
            raise ProjectNotFoundError(project_id)
        return registration

    def list_project_ids(self) -> tuple[str, ...]:
        """已注册项目 ID（排序后；不暴露注册条目内部）。"""
        with self._lock:
            return tuple(sorted(self._projects))


# ============================================================
# 输入校验（纯函数）
# ============================================================

def _validate_project_id(project_id: str) -> None:
    """project_id 非空 / 格式校验（在任何查询之前拒绝）。"""
    if not isinstance(project_id, str):
        raise ProjectRegistryError(
            f"project_id 必须是 str（当前: {type(project_id).__name__}）"
        )
    stripped = project_id.strip()
    if not stripped:
        raise ProjectRegistryError("project_id 不能为空或纯空白")
    if not _PROJECT_ID_RE.match(stripped):
        raise ProjectRegistryError(
            f"project_id 非法（只允许字母/数字/连字符/下划线）: {project_id!r}"
        )


# ============================================================
# 默认注册表（惰性单例）
# ============================================================

_default_registry: InMemoryProjectRegistry | None = None
_default_registry_lock = threading.Lock()


def get_default_project_registry() -> InMemoryProjectRegistry:
    """返回进程级默认注册表（惰性创建，线程安全）。

    默认内容：配置默认项目（settings.project.project_id，默认 vietnam-wms）
    → DataSource(primary, postgresql) + schema "public"。

    保证旧行为（任务书 §十九）：
        - project_id 未提供 → API 层走 `_default_orchestrator`（不查注册表）；
        - project_id = 配置默认值 → 注册表命中，per-project 依赖与
          全局默认完全等价（同一 primary 数据源 + public schema）。
    """
    global _default_registry
    if _default_registry is None:
        with _default_registry_lock:
            if _default_registry is None:
                registry = InMemoryProjectRegistry()
                registry.register(
                    # 配置默认项目（从 .env / ProjectSettings 读取）
                    _build_default_registration().context.project_id,
                    _build_default_registration(),
                )
                _default_registry = registry
    return _default_registry


def _build_default_registration() -> ProjectRegistration:
    """构造"配置默认项目"的注册条目（get_default_project_registry 内部使用）。"""
    default_context = get_default_project_context()
    return ProjectRegistration(
        context=default_context,
        schema_name=DEFAULT_BUSINESS_SCHEMA,
    )
