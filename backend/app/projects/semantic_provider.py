"""Project Semantic Provider（Phase 3.8.3）。

职责（任务书 §四 / §五 / §六）：

    project_id
        ↓
    ProjectSemanticProvider.get(project_id)     （服务器端解析）
        ↓
    ProjectSemantic（frozen DTO，Phase 3.7.3 既有模型，零重复）

设计约束：

- **project_id 唯一决定 Semantic**：映射只存在于服务器端
  （Registry / Provider），HTTP 请求无法指定 semantic 文件 / 内容
  （任务书 §六）；
- **复用既有模型**：不创建 ProjectSemanticV2 / ProjectSemanticContext
  等重复 DTO；不重新实现 Loader / Serializer（任务书 §四）；
- **不读 .env、不访问数据库、不调 LLM**（继承 semantic_loader 纪律）；
- **不做热加载**：注册后进程内固定（任务书 §二）。

两个实现：

- ``InMemoryProjectSemanticProvider``
    显式注册映射（代码级，服务器端 / 测试 setup）；
    未注册项目 → ProjectSemanticNotFoundError（clear error，
    任务书 §16.1，**不静默回退**）。
- ``LoaderBackedProjectSemanticProvider``
    最小包装既有 ``ProjectSemanticLoader``（文件名 == project_id 的
    既有约定）；**yaml 文件不存在 → 空语义**（合法状态：项目尚未
    配置业务语义），配置错误（YAML 语法 / 结构 / 未知字段）→
    透传 ProjectSemanticConfigError。

    该实现是默认 Provider（``get_default_project_semantic_provider``），
    保证 vietnam-wms 及未配置语义项目的旧行为完全不变（Phase 3.8.3
    之前 DefaultProjectContextProvider 的"缺文件 → 空语义"语义）。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Protocol

from backend.app.projects.semantic import ProjectSemantic
from backend.app.projects.semantic_loader import (
    ProjectSemanticConfigError,
    ProjectSemanticLoader,
    ProjectSemanticNotFoundError,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ProjectSemanticProvider",
    "InMemoryProjectSemanticProvider",
    "LoaderBackedProjectSemanticProvider",
    "get_default_project_semantic_provider",
]


# ============================================================
# 协议（任务书 §四）
# ============================================================

class ProjectSemanticProvider(Protocol):
    """project_id → ProjectSemantic 的服务器端解析协议。

    实现要求：

    - 相同 project_id → 相同（或等值）ProjectSemantic（确定性）；
    - 不访问数据库 / 不调 LLM / 不读 .env；
    - 项目未注册语义时的行为由实现定义（InMemory → clear error；
      LoaderBacked → 空语义兼容），但**绝不能**返回其他项目的语义。
    """

    def get(self, project_id: str) -> ProjectSemantic:
        """解析该项目的业务语义。

        Raises:
            ProjectSemanticNotFoundError: 项目未注册语义
                （InMemory 实现的 clear error 语义）。
            ProjectSemanticConfigError:   语义配置错误（透传自 Loader）。
        """
        ...


# ============================================================
# In-Memory 实现（显式注册，服务器端控制）
# ============================================================

class InMemoryProjectSemanticProvider:
    """显式注册的项目语义映射（Phase 3.8.3 测试 / 代码级注册）。

    线程安全（注册 / 读取共享一把锁）；未注册项目 → clear error
    （ProjectSemanticNotFoundError，绝不静默回退、绝不串项目）。
    """

    def __init__(self) -> None:
        self._semantics: dict[str, ProjectSemantic] = {}
        self._lock = threading.Lock()

    def register(
        self, project_id: str, semantic: ProjectSemantic
    ) -> None:
        """注册项目语义（服务器端 / 测试 setup；重复注册报错）。"""
        if not isinstance(project_id, str) or not project_id.strip():
            raise ProjectSemanticConfigError(
                f"project_id 必须是非空 str（当前: {project_id!r}）"
            )
        if not isinstance(semantic, ProjectSemantic):
            raise ProjectSemanticConfigError(
                "semantic 必须是 ProjectSemantic 实例"
                f"（当前: {type(semantic).__name__}）"
            )
        with self._lock:
            if project_id in self._semantics:
                raise ProjectSemanticConfigError(
                    f"项目 {project_id!r} 的语义已注册；如需替换请先 unregister"
                )
            self._semantics[project_id] = semantic
        logger.info(
            "project semantic registered",
            extra={
                "project_id": project_id,
                "tables": len(semantic.tables),
                "columns": len(semantic.columns),
            },
        )

    def unregister(self, project_id: str) -> None:
        """移除注册（测试清理；未注册 → clear error）。"""
        with self._lock:
            if project_id not in self._semantics:
                raise ProjectSemanticConfigError(
                    f"项目 {project_id!r} 的语义未注册，无法移除"
                )
            del self._semantics[project_id]

    def get(self, project_id: str) -> ProjectSemantic:
        """返回该项目注册的语义。

        Raises:
            ProjectSemanticNotFoundError: project_id 未注册（clear error）。
        """
        if not isinstance(project_id, str) or not project_id.strip():
            raise ProjectSemanticConfigError(
                f"project_id 必须是非空 str（当前: {project_id!r}）"
            )
        with self._lock:
            semantic = self._semantics.get(project_id)
        if semantic is None:
            raise ProjectSemanticNotFoundError(project_id)
        return semantic

    def list_project_ids(self) -> tuple[str, ...]:
        """已注册语义的项目 ID（排序后；不暴露语义内容）。"""
        with self._lock:
            return tuple(sorted(self._semantics))


# ============================================================
# Loader 包装实现（文件名 == project_id 的既有约定）
# ============================================================

class LoaderBackedProjectSemanticProvider:
    """包装既有 ``ProjectSemanticLoader`` 的兼容实现（默认 Provider）。

    行为（与 Phase 3.8.3 之前的 DefaultProjectContextProvider 等价）：

    - ``semantic/<project_id>.yaml`` 存在 → 解析并返回；
    - 文件不存在 → 空语义 ``ProjectSemantic()``（合法状态：项目
      尚未配置业务语义；**不是错误**）；
    - YAML / 结构 / 未知字段 / 重复定义 → ProjectSemanticConfigError
      （透传 Loader，绝不静默吞掉配置错误）。
    """

    def __init__(self, *, loader: ProjectSemanticLoader | None = None,
                 base_dir: Path | None = None) -> None:
        """构造 Provider。

        Args:
            loader:   已构造的 Loader（测试可注入带临时目录的实例）。
            base_dir: 语义配置目录（仅 loader 为 None 时生效）；
                      None 时使用 Loader 默认目录。
        """
        if loader is not None and base_dir is not None:
            raise ProjectSemanticConfigError(
                "loader 与 base_dir 不能同时指定（二选一）"
            )
        self._loader = loader if loader is not None else ProjectSemanticLoader(
            base_dir=base_dir
        )

    def get(self, project_id: str) -> ProjectSemantic:
        """按文件名约定加载语义；缺文件 → 空语义（兼容旧行为）。"""
        try:
            return self._loader.load(project_id)
        except ProjectSemanticNotFoundError:
            # 合法状态：项目尚未配置业务语义（空语义）。
            # 生产默认路径；显式注册项目请用 InMemory 实现。
            logger.info(
                "project semantic file missing; using empty semantic",
                extra={"project_id": project_id},
            )
            return ProjectSemantic()


# ============================================================
# 默认 Provider（惰性单例）
# ============================================================

_default_provider: LoaderBackedProjectSemanticProvider | None = None
_default_provider_lock = threading.Lock()


def get_default_project_semantic_provider() -> LoaderBackedProjectSemanticProvider:
    """返回进程级默认 Semantic Provider（惰性创建，线程安全）。

    默认实现 = LoaderBackedProjectSemanticProvider（文件名约定），
    保证 vietnam-wms（vietnam-wms.yaml）及未配置语义项目的
    旧行为完全不变。
    """
    global _default_provider
    if _default_provider is None:
        with _default_provider_lock:
            if _default_provider is None:
                _default_provider = LoaderBackedProjectSemanticProvider()
    return _default_provider
