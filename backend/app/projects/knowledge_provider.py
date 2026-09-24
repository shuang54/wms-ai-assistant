"""Project Knowledge Provider（Phase 3.8.4）。

职责（任务书 §四 / §五 / §六）：

    project_id
        ↓
    ProjectKnowledgeProvider.get_scope(project_id)   （服务器端解析）
        ↓
    ProjectKnowledgeScope（frozen DTO）

Project Runtime Context 至此完整：

    Project
     ├── DataSource          （Phase 3.8.1）
     ├── Capabilities        （Phase 3.8.2）
     ├── Semantic            （Phase 3.8.3）
     └── Knowledge           （本阶段）
          ↓
      RAG Service（Project-scoped Retrieval）

设计约束（任务书 §四 / §五）：

- **project_id 唯一决定 Knowledge Scope**：映射只存在于服务器端
  （Provider / Factory），HTTP 请求无法注入 knowledge_scope /
  knowledge_file / knowledge_namespace 等字段越权切换项目知识库；
- Scope 是 frozen DTO：无数据库连接、无密码、无 LLM、无 HTTP、
  不接受 HTTP 传入的任意路径；
- **显式 scope，绝不退化为全库检索**：global 也有明确命名空间
  ``__global__``，VectorSearch 在有 scope 时必须显式过滤
  （补充要求 §1）；
- **legacy 兼容只属于历史项目**（补充要求 §2 / §3）：

    meta_data.project_id IS NULL        → legacy，仅 vietnam-wms 可见
    meta_data.project_id = "__global__" → 公共，所有项目共享
    meta_data.project_id = "project-a"  → 仅 project-a
    meta_data.project_id = "project-b"  → 仅 project-b

  普通项目**绝不**因 legacy 兼容看到其他项目知识；
- Provider 不操作数据库、不读 .env、不调 LLM、不做热加载。

两个实现：

- ``InMemoryProjectKnowledgeProvider``
    显式注册映射（服务器端 / 测试 setup）；未注册项目 → clear error
    （**绝不静默 fallback 到其他项目知识**，任务书 §十八-2）。
- ``DefaultProjectKnowledgeProvider``
    兼容实现（默认 Provider）：每个项目获得以自身 project_id 命名的
    namespace + 共享 ``__global__``；仅历史项目（默认 vietnam-wms，
    即 ``settings.project.project_id``）额外可见 legacy 知识。
    该行为是**显式表达的服务器端兼容策略**，不是"未配置 → 用别人的"。
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Final, Protocol

from backend.app.config import settings

logger = logging.getLogger(__name__)

__all__ = [
    "GLOBAL_NAMESPACE",
    "ProjectKnowledgeScope",
    "ProjectKnowledgeProvider",
    "ProjectKnowledgeProviderError",
    "ProjectKnowledgeScopeError",
    "InMemoryProjectKnowledgeProvider",
    "DefaultProjectKnowledgeProvider",
    "get_default_project_knowledge_provider",
]


#: 公共知识的显式命名空间：所有项目共享（补充要求 §1）
GLOBAL_NAMESPACE: Final[str] = "__global__"


# ============================================================
# 异常体系
# ============================================================

class ProjectKnowledgeProviderError(Exception):
    """Project Knowledge Provider 通用异常（Phase 3.8.4）。"""


class ProjectKnowledgeScopeError(ProjectKnowledgeProviderError):
    """项目知识 Scope 未注册 / 输入非法（clear error，绝不静默回退）。"""


# ============================================================
# DTO
# ============================================================

@dataclass(frozen=True)
class ProjectKnowledgeScope:
    """项目知识检索范围（frozen，非敏感，只描述不执行）。

    VectorSearch 按以下语义过滤 ``knowledge_document.meta_data->>'project_id'``：

    - ``namespace``：该项目自己的知识（必须显式过滤，永不省略）；
    - ``includes_global=True``：额外可见 ``__global__`` 公共知识（默认）；
    - ``includes_legacy=True``：额外可见**未标记 project_id 的历史知识**
      （仅 vietnam-wms 兼容路径为 True；普通项目必须为 False，
      否则会发生跨项目 legacy 泄漏）。

    Attributes:
        project_id:     请求 / 注册的项目 ID（溯源用）。
        namespace:      该项目知识命名空间（通常 == project_id）。
        includes_global: 是否共享 ``__global__`` 公共知识（默认 True）。
        includes_legacy: 是否可见 legacy（无 project_id 标记）历史知识
                         （默认 False；仅历史项目显式开启）。
    """

    project_id: str
    namespace: str
    includes_global: bool = True
    includes_legacy: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not self.project_id.strip():
            raise ProjectKnowledgeScopeError(
                f"project_id 必须是非空 str（当前: {self.project_id!r}）"
            )
        if not isinstance(self.namespace, str) or not self.namespace.strip():
            raise ProjectKnowledgeScopeError(
                f"namespace 必须是非空 str（当前: {self.namespace!r}）"
            )
        for flag in ("includes_global", "includes_legacy"):
            if not isinstance(getattr(self, flag), bool):
                raise ProjectKnowledgeScopeError(
                    f"{flag} 必须是 bool（当前: "
                    f"{type(getattr(self, flag)).__name__}）"
                )


# ============================================================
# 协议（任务书 §四）
# ============================================================

class ProjectKnowledgeProvider(Protocol):
    """project_id → ProjectKnowledgeScope 的服务器端解析协议。

    实现要求：

    - 相同 project_id → 相同（或等值）scope（确定性）；
    - 不访问数据库 / 不调 LLM / 不读 .env / 不发 HTTP 请求；
    - 未注册项目的处理由实现定义（InMemory → clear error；
      Default → 每个项目自身 namespace 的兼容 scope），
      但**绝不能**返回指向其他项目 namespace 的 scope。
    """

    def get_scope(self, project_id: str) -> ProjectKnowledgeScope:
        """解析该项目的知识检索范围。

        Raises:
            ProjectKnowledgeScopeError: 项目知识未注册（clear error）。
        """
        ...


# ============================================================
# 输入校验（共用）
# ============================================================

def _validate_project_id(project_id: str) -> None:
    if not isinstance(project_id, str) or not project_id.strip():
        raise ProjectKnowledgeScopeError(
            f"project_id 必须是非空 str（当前: {project_id!r}）"
        )


# ============================================================
# In-Memory 实现（显式注册，服务器端控制）
# ============================================================

class InMemoryProjectKnowledgeProvider:
    """显式注册的项目知识映射（Phase 3.8.4 测试 / 代码级注册）。

    线程安全（注册 / 读取共享一把锁）：

    - 重复注册 → clear error；
    - 类型错误 → clear error；
    - 未注册项目 → clear error（ProjectKnowledgeScopeError），
      **绝不静默 fallback 到其他项目 / 全局知识**。
    """

    def __init__(self) -> None:
        self._scopes: dict[str, ProjectKnowledgeScope] = {}
        self._lock = threading.Lock()

    def register(
        self, project_id: str, scope: ProjectKnowledgeScope
    ) -> None:
        """注册项目知识 scope（服务器端 / 测试 setup；重复注册报错）。"""
        _validate_project_id(project_id)
        if not isinstance(scope, ProjectKnowledgeScope):
            raise ProjectKnowledgeScopeError(
                "scope 必须是 ProjectKnowledgeScope 实例"
                f"（当前: {type(scope).__name__}）"
            )
        if scope.project_id != project_id:
            raise ProjectKnowledgeScopeError(
                f"scope.project_id（{scope.project_id!r}）与注册的 "
                f"project_id（{project_id!r}）不一致；拒绝注册"
            )
        with self._lock:
            if project_id in self._scopes:
                raise ProjectKnowledgeScopeError(
                    f"项目 {project_id!r} 的知识 scope 已注册；"
                    "如需替换请先 unregister"
                )
            self._scopes[project_id] = scope
        logger.info(
            "project knowledge scope registered",
            extra={
                "project_id": project_id,
                "namespace": scope.namespace,
                "includes_global": scope.includes_global,
                "includes_legacy": scope.includes_legacy,
            },
        )

    def unregister(self, project_id: str) -> None:
        """移除注册（测试清理；未注册 → clear error）。"""
        with self._lock:
            if project_id not in self._scopes:
                raise ProjectKnowledgeScopeError(
                    f"项目 {project_id!r} 的知识 scope 未注册，无法移除"
                )
            del self._scopes[project_id]

    def get_scope(self, project_id: str) -> ProjectKnowledgeScope:
        """返回该项目注册的知识 scope。

        Raises:
            ProjectKnowledgeScopeError: project_id 未注册（clear error）。
        """
        _validate_project_id(project_id)
        with self._lock:
            scope = self._scopes.get(project_id)
        if scope is None:
            raise ProjectKnowledgeScopeError(
                f"项目 {project_id!r} 的知识 scope 未注册"
                "（不会回退到其他项目 / 全局知识）"
            )
        return scope

    def list_project_ids(self) -> tuple[str, ...]:
        """已注册知识 scope 的项目 ID（排序后；不暴露 scope 内容）。"""
        with self._lock:
            return tuple(sorted(self._scopes))


# ============================================================
# 默认实现（vietnam-wms legacy 兼容，显式表达）
# ============================================================

class DefaultProjectKnowledgeProvider:
    """兼容默认实现：每个项目 → 自身 namespace + 共享 ``__global__``。

    与既有行为的兼容策略（补充要求 §2 / §3，显式表达、非静默回退）：

    - 任何项目 → ``namespace=project_id`` + ``includes_global=True``；
    - 历史项目（默认 ``settings.project.project_id``，即 vietnam-wms）
      额外 ``includes_legacy=True``：可继续检索 Phase 3.8.4 之前入库、
      未标记 ``project_id`` 的历史知识；
    - 其他项目 ``includes_legacy=False``：legacy 知识对普通项目不可见，
      杜绝"legacy 兼容"导致的跨项目泄漏。

    项目特殊逻辑（vietnam-wms legacy）**只**存在于本 Provider /
    Factory（任务书 §六），RagService / VectorSearch 不含任何
    ``if project_id == "vietnam-wms"`` 分支。
    """

    def __init__(self, *, legacy_project_id: str | None = None) -> None:
        """构造 Provider。

        Args:
            legacy_project_id: 可见 legacy 历史知识的项目 ID；
                None 时使用 ``settings.project.project_id``
                （默认 vietnam-wms）。
        """
        resolved = (
            legacy_project_id
            if legacy_project_id is not None
            else settings.project.project_id
        )
        _validate_project_id(resolved)
        self._legacy_project_id = resolved

    def get_scope(self, project_id: str) -> ProjectKnowledgeScope:
        """每个项目显式获得自身 namespace 的 scope（永不指向他人）。"""
        _validate_project_id(project_id)
        return ProjectKnowledgeScope(
            project_id=project_id,
            namespace=project_id,
            includes_global=True,
            includes_legacy=(project_id == self._legacy_project_id),
        )


# ============================================================
# 默认 Provider（惰性单例）
# ============================================================

_default_provider: DefaultProjectKnowledgeProvider | None = None
_default_provider_lock = threading.Lock()


def get_default_project_knowledge_provider() -> DefaultProjectKnowledgeProvider:
    """返回进程级默认 Knowledge Provider（惰性创建，线程安全）。

    默认实现 = DefaultProjectKnowledgeProvider（vietnam-wms legacy
    兼容），保证 vietnam-wms 及历史知识的检索行为完全不变。
    """
    global _default_provider
    if _default_provider is None:
        with _default_provider_lock:
            if _default_provider is None:
                _default_provider = DefaultProjectKnowledgeProvider()
    return _default_provider
