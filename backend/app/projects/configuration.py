"""Project Configuration Aggregation（Phase 3.8.6）。

职责（任务书 §四 / §六 / §七 / §八）：

    project_id
        ↓
    ProjectConfigurationProvider.get(project_id)
        ↓
    ProjectConfiguration
        ├── context          ProjectContext / DataSource
        ├── schema_name      业务 schema
        ├── capabilities     ProjectCapabilities
        ├── semantic         ProjectSemantic
        └── knowledge_scope  ProjectKnowledgeScope | None

收敛 Phase 3.8.1 ~ 3.8.5 各自独立的配置来源，避免配置漂移：

    Project ID ──┬── DataSource / Capability（ProjectRegistry，权威存在性）
                 ├── Semantic（ProjectSemanticProvider）
                 └── Knowledge Scope（ProjectKnowledgeProvider）
                          ↓
                   ProjectConfiguration（唯一配置结果出口）
                          ↓
              ProjectOrchestratorFactory

关键区分（任务书 §五）：

- **Provider = 如何获取配置**：``ProjectRegistry`` /
  ``ProjectSemanticProvider`` / ``ProjectKnowledgeProvider``；
- **Configuration = 已解析的配置结果**：本模块只产生结果，
  DTO 内**绝不保存** registry / provider / service / engine。

安全约束（任务书 §四）：

- DTO frozen；不含 password / db url / api key / llm secret /
  engine / session / service / provider；
- Capability 不新增 ``CapabilityProvider``：直接从 Registry 读取
  （任务书 §十四）；
- 调用顺序（任务书 §七 / §八）：**Registry 先行**，未注册即
  ``ProjectNotFoundError`` 立即失败，不再向 Semantic / Knowledge 查询，
  杜绝 "unknown project + 其它 Provider 恰好有配置 → partial project"；
- 配置缺失（Semantic / Knowledge）→ **clear error**，
  绝不 fallback 到 vietnam-wms / global / 其它项目（任务书 §二十）。

本阶段不改：SemanticLoader / Serializer / ContextComposer /
RelevantTableSelector / KnowledgeProvider 判定规则 / ORM / Core。
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Protocol

from backend.app.projects.capabilities import ProjectCapabilities
from backend.app.projects.knowledge_provider import (
    ProjectKnowledgeProvider,
    ProjectKnowledgeScope,
    get_default_project_knowledge_provider,
)
from backend.app.projects.models import ProjectContext
from backend.app.projects.registry import (
    ProjectRegistry,
    get_default_project_registry,
)
from backend.app.projects.semantic import ProjectSemantic
from backend.app.projects.semantic_provider import (
    ProjectSemanticProvider,
    get_default_project_semantic_provider,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ProjectConfiguration",
    "ProjectConfigurationError",
    "ProjectConfigurationProvider",
    "DefaultProjectConfigurationProvider",
    "get_default_project_configuration_provider",
]


# ============================================================
# 异常（仅用于 DTO 自身校验；配置源错误沿用各 Provider 既有类型）
# ============================================================

class ProjectConfigurationError(Exception):
    """ProjectConfiguration 自身的类型 / 一致性校验异常。

    注意区分：
        - ``ProjectNotFoundError``         → 项目不存在（Registry 权威）
        - ``ProjectSemantic*Error``        → Semantic 配置错误 / 缺失
        - ``ProjectKnowledge*Error``       → Knowledge 配置错误 / 缺失
    """


# ============================================================
# DTO（任务书 §四）
# ============================================================

@dataclass(frozen=True)
class ProjectConfiguration:
    """单个项目已解析的完整运行配置（frozen、非敏感、只描述不执行）。

    保存的是**配置结果**，不是获取方式（§五）：

        ProjectConfiguration(context, schema_name, capabilities,
                             semantic, knowledge_scope)

    Attributes:
        context:         项目上下文（含 DataSource 身份；不含凭据）。
        schema_name:     该项目业务数据所在的数据库 schema（来自 Registry）。
        capabilities:    该项目允许的能力（来自 Registry.capabilities）。
        semantic:        该项目业务语义（``ProjectSemantic``；注意该 DTO
                         本身不含 project_id 字段，归属由 registry +
                         provider 调用键决定）。
        knowledge_scope: 项目知识检索范围；
                         ``None`` = 该项目未启用 Knowledge
                         （``capabilities.knowledge_enabled=False``，
                         对应 Phase 3.8.4 的 "Provider 0 次解析" 语义）。
    """

    context: ProjectContext
    schema_name: str
    capabilities: ProjectCapabilities
    semantic: ProjectSemantic
    knowledge_scope: ProjectKnowledgeScope | None = None

    @property
    def project_id(self) -> str:
        """项目 ID（唯一权威来源：context.project_id）。"""
        return self.context.project_id

    def __post_init__(self) -> None:
        if not isinstance(self.context, ProjectContext):
            raise ProjectConfigurationError(
                "context 必须是 ProjectContext 实例"
                f"（当前: {type(self.context).__name__}）"
            )
        if not isinstance(self.schema_name, str) or not self.schema_name.strip():
            raise ProjectConfigurationError(
                f"schema_name 必须是非空 str（当前: {self.schema_name!r}）"
            )
        if not isinstance(self.capabilities, ProjectCapabilities):
            raise ProjectConfigurationError(
                "capabilities 必须是 ProjectCapabilities 实例"
                f"（当前: {type(self.capabilities).__name__}）"
            )
        if not isinstance(self.semantic, ProjectSemantic):
            raise ProjectConfigurationError(
                "semantic 必须是 ProjectSemantic 实例"
                f"（当前: {type(self.semantic).__name__}）"
            )
        if self.knowledge_scope is None:
            return
        if not isinstance(self.knowledge_scope, ProjectKnowledgeScope):
            raise ProjectConfigurationError(
                "knowledge_scope 必须是 ProjectKnowledgeScope 实例或 None"
                f"（当前: {type(self.knowledge_scope).__name__}）"
            )
        # §六：跨 Provider 一致性 —— Knowledge Scope 必须属于本项目
        # （Semantic 无 project_id 字段，无法在 DTO 内校验其归属）
        if self.knowledge_scope.project_id != self.context.project_id:
            raise ProjectConfigurationError(
                "knowledge_scope.project_id"
                f"（{self.knowledge_scope.project_id!r}）与 "
                f"context.project_id（{self.context.project_id!r}）不一致；"
                "拒绝装配"
            )


# ============================================================
# 协议（任务书 §六）
# ============================================================

class ProjectConfigurationProvider(Protocol):
    """project_id → ProjectConfiguration 的服务器端解析协议。"""

    def get(self, project_id: str) -> ProjectConfiguration:
        """返回该项目已解析的完整配置。

        Raises:
            ProjectNotFoundError: 项目未注册（必须由 Registry 最先判定）。
            ProjectSemantic*Error / ProjectKnowledge*Error: 配置缺失 / 错误。
        """
        ...


# ============================================================
# 默认实现（任务书 §六 / §七）
# ============================================================

class DefaultProjectConfigurationProvider:
    """按固定顺序装配项目配置（Registry 权威先行，每个源恰好 1 次）。

    顺序（任务书 §七）：

        1. ``ProjectRegistry.get(project_id)``  未注册 → ProjectNotFoundError
                                                （**立即失败**，不再查询后续源）
        2. Capability：直接取 ``registration.capabilities``（§十四：不新增
           CapabilityProvider）
        3. ``ProjectSemanticProvider.get(project_id)``
        4. ``ProjectKnowledgeProvider.get_scope(project_id)``
           —— 仅当 ``capabilities.knowledge_enabled=True``
           （保持 Phase 3.8.4 §十三：禁用 RAG 的项目 KnowledgeProvider
           0 次解析）
        5. 装配 frozen ``ProjectConfiguration``

    零 fallback（任务书 §二十）：任何一步缺失都是 clear error，
    不回退到 vietnam-wms / __global__ / 其它项目。
    """

    def __init__(
        self,
        *,
        registry: ProjectRegistry | None = None,
        semantic_provider: ProjectSemanticProvider | None = None,
        knowledge_provider: ProjectKnowledgeProvider | None = None,
    ) -> None:
        """
        Args:
            registry:           项目注册表；None → 默认注册表（惰性）。
            semantic_provider:  Semantic 解析器；None → 默认 Provider（惰性）。
            knowledge_provider: Knowledge 解析器；None → 默认 Provider（惰性）。
        """
        self._registry = registry
        self._semantic_provider = semantic_provider
        self._knowledge_provider = knowledge_provider

    # ---------- 惰性默认解析（仅在确实需要该源时发生） ----------

    def _resolve_registry(self) -> ProjectRegistry:
        if self._registry is None:
            self._registry = get_default_project_registry()
        return self._registry

    def _resolve_semantic_provider(self) -> ProjectSemanticProvider:
        if self._semantic_provider is None:
            self._semantic_provider = get_default_project_semantic_provider()
        return self._semantic_provider

    def _resolve_knowledge_provider(self) -> ProjectKnowledgeProvider:
        if self._knowledge_provider is None:
            self._knowledge_provider = (
                get_default_project_knowledge_provider()
            )
        return self._knowledge_provider

    # ---------- 装配 ----------

    def get(self, project_id: str) -> ProjectConfiguration:
        """按 Registry → Capability → Semantic → Knowledge 顺序装配配置。"""
        # 1) Registry 是项目存在性的唯一权威（§八）：未注册立即失败
        registration = self._resolve_registry().get(project_id)
        capabilities = registration.capabilities

        # 3) Semantic（缺失 → Provider 自身的 clear error，不 fallback）
        semantic = self._resolve_semantic_provider().get(project_id)

        # 4) Knowledge：仅 knowledge_enabled=True 时解析（§十三）
        knowledge_scope: ProjectKnowledgeScope | None = None
        if capabilities.knowledge_enabled:
            knowledge_scope = self._resolve_knowledge_provider().get_scope(
                project_id
            )

        configuration = ProjectConfiguration(
            context=registration.context,
            schema_name=registration.schema_name,
            capabilities=capabilities,
            semantic=semantic,
            knowledge_scope=knowledge_scope,
        )
        logger.info(
            "project configuration assembled",
            extra={
                "project_id": project_id,
                "schema_name": configuration.schema_name,
                "data_source_name": configuration.context.data_source.name,
                "tools": list(capabilities.tool_names),
                "knowledge_enabled": capabilities.knowledge_enabled,
                "text_to_sql_enabled": capabilities.text_to_sql_enabled,
                "semantic_tables": len(semantic.tables),
                "knowledge_namespace": (
                    knowledge_scope.namespace
                    if knowledge_scope is not None
                    else None
                ),
            },
        )
        return configuration


# ============================================================
# 默认 Provider（惰性单例）
# ============================================================

_default_provider: DefaultProjectConfigurationProvider | None = None
_default_provider_lock = threading.Lock()


def get_default_project_configuration_provider() -> (
    DefaultProjectConfigurationProvider
):
    """返回进程级默认配置 Provider（惰性创建，线程安全）。

    默认组合 = 默认 Registry + 默认 Semantic Provider + 默认 Knowledge
    Provider，保证 vietnam-wms 的既有行为完全不变（任务书 §九）。
    """
    global _default_provider
    if _default_provider is None:
        with _default_provider_lock:
            if _default_provider is None:
                _default_provider = DefaultProjectConfigurationProvider()
    return _default_provider
