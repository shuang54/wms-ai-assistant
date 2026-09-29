"""RAG Runtime Observability Wiring（Phase 3.12 Step 44）——**应用级装配**。

把 Step 43 建立的 RAG Runtime Observation 边界接入**真实生产装配**
（``/api/ai/chat`` 的默认 Orchestrator）：

    backend/app/api/orchestrator_chat.py（Composition Root）
        │  rag_service=get_observed_rag_service()           ← 本模块
        ↓
    AIOrchestratorService(rag_service=...)
        ↓  Router → RAG
    RagService(observer=_RAG_EXECUTION_COLLECTOR)          ← **应用级单实例**
        ↓ assistant_trace_scope(request_id)（Step 36；由 Orchestrator 建立）
    InMemoryRagExecutionCollector（进程内；有界 1000）
        ↓
    RagObservabilityQueryService（get_rag_observability_query_service()）

为什么需要本模块（为什么不是 API 模块直接构造）：

    * ``api/orchestrator_chat.py`` **不得** import ``rag_service``（既有纵深
      防御契约 ``test_api_module_does_not_import_forbidden_services``）；
    * 而 ``RagService`` 的默认实例由 ``AIOrchestratorService.__init__`` 懒构造
      （无 observer），项目级 Orchestrator 又复用 ``base._rag``
      （``project_orchestrator_factory``）→ 只需在**同一个**构造点换成
      "已接线 observer 的实例"，默认路径与 per-project 路径同时生效；
    * 本模块放在 Service 层（与 ``llm_usage_persistence_runtime`` 同风格的
      runtime 装配模块），API 层只调用 accessor，不接触 RagService 类型。

应用级生命周期（**不是** 每请求新建）：

    * 一个进程 = **一个** Collector（``_RAG_EXECUTION_COLLECTOR``）=
      一个 RagService（``_RAG_SERVICE``）→ 跨请求可查询的稳定 Runtime 视图；
    * ``get_rag_observability_query_service()`` 每次返回**新** QueryService
      实例，但数据源始终是同一个 Collector（与 Tool 侧 accessor 同语义）；
    * 无每请求 Collector / 无 Service Locator 框架 / 无隐藏请求状态。

边界（本阶段刻意保持）：

    * **只有** ``/api/ai/chat``（经默认 Orchestrator）被接线：
      旧链路 ``/api/rag/answer``（``api/rag._rag_service``）与 ``/api/chat``
      （``ChatService`` 自己的默认 RagService）**保持不观测** —— 它们没有
      ``assistant_request_id``，Step 43 契约下即使有 observer 也不会记录；
    * 纯运行时：**无 PostgreSQL / 无表 / 无 Repository / 无 HTTP API**；
    * 观测失败（Collector.record 抛错）由 ``RagService`` 收敛为 warning，
      绝不影响 RAG 业务结果（Step 43 隔离契约不变）。
"""
from __future__ import annotations

from backend.app.services.composite_rag_execution_observer import (
    CompositeRagExecutionObserver,
)
from backend.app.services.in_memory_rag_execution_collector import (
    InMemoryRagExecutionCollector,
)
from backend.app.services.rag_execution_persistence_adapter import (
    RagExecutionPersistenceAdapter,
)
from backend.app.services.rag_execution_persistence_service import (
    RagExecutionPersistenceService,
)
from backend.app.services.rag_execution_persistent_query_service import (
    RagExecutionPersistentQueryService,
)
from backend.app.services.rag_observability_query_service import (
    RagObservabilityQueryService,
)
from backend.app.services.rag_service import RagService

__all__ = [
    "get_observed_rag_service",
    "get_rag_execution_collector",
    "get_rag_execution_persistence_adapter",
    "get_rag_execution_persistent_query_service",
    "get_rag_observability_query_service",
]

#: Application 级 RAG Runtime Collector（**唯一创建点**；进程内单实例）。
#: 与 Tool 侧不同：Tool Collector 由 API 模块（Composition Root）持有；
#: RAG 服务实例在 Orchestrator 内部构造，因此接线点放在本 Service 层模块。
_RAG_EXECUTION_COLLECTOR = InMemoryRagExecutionCollector()

# Phase 3.12 Step 46：持久化 fan-out（内存 + PostgreSQL，两者互相独立）。
# 同一 Observation 依次交给两个**并列**的 observer 实现：
#     ├── InMemoryRagExecutionCollector（runtime store；Runtime 查询视图）
#     └── RagExecutionPersistenceAdapter → Service → Repository → ai_ops
# 不新增第二个 Collector；Collector / Adapter 互不依赖（并列挂在端口上）；
# 持久化失败由 Adapter 收敛为 warning → 绝不影响 RAG 业务结果
# （RagService 侧另有一层 best-effort 隔离）。
_RAG_EXECUTION_PERSISTENCE_SERVICE = RagExecutionPersistenceService()
_RAG_EXECUTION_PERSISTENCE_ADAPTER = RagExecutionPersistenceAdapter(
    _RAG_EXECUTION_PERSISTENCE_SERVICE
)
_RAG_EXECUTION_OBSERVER: CompositeRagExecutionObserver = (
    CompositeRagExecutionObserver(
        _RAG_EXECUTION_COLLECTOR,
        _RAG_EXECUTION_PERSISTENCE_ADAPTER,
    )
)

#: Application 级 **已接线** RagService（observer = 上面的 Composite）。
#: 构造是廉价的（Vector Search / LLM / ContextBuilder 仍懒加载；
#: 不在 import 时连接 DB / 加载模型）。
_RAG_SERVICE = RagService(observer=_RAG_EXECUTION_OBSERVER)

#: 持久读边界（只读；模块级**同一实例**，与 Tool 侧 Step 30/41 同模式）。
#: 构造期不连接数据库（Repository 按操作懒解析 session factory）。
_RAG_PERSISTENT_QUERY_SERVICE = RagExecutionPersistentQueryService()


def get_rag_execution_collector() -> InMemoryRagExecutionCollector:
    """Application 级 RAG Runtime Collector（跨请求复用；只读查询用）。

    Returns:
        进程内**同一个** ``InMemoryRagExecutionCollector``（有界 1000；
        无持久化；``clear()`` 为唯一显式清空入口）。
    """
    return _RAG_EXECUTION_COLLECTOR


def get_observed_rag_service() -> RagService:
    """Application 级已接线 RagService（``observer`` 已装配）。

    Composition Root（``api/orchestrator_chat.py``）把它注入默认
    ``AIOrchestratorService(rag_service=...)``；项目级 Orchestrator 通过
    ``base._rag`` 复用**同一实例** → 两类请求共用同一 Collector。

    Note:
        **不是工厂**：永远返回模块级**同一个**实例（本模块是唯一创建点；
        每次调用不新建 RagService / 不新建 Collector）。
        命名刻意避开 ``get_default_*`` —— Composition Root 的
        ``test_no_collector_factory_helper`` 约束禁止工厂式隐藏单例；
        这里只是"取已有引用"，生命周期由本模块显式持有。

    Returns:
        进程内**同一个** ``RagService``（observer = 应用级 Collector）。
    """
    return _RAG_SERVICE


def get_rag_observability_query_service() -> RagObservabilityQueryService:
    """RAG Runtime Observation 的只读查询装配（Composition Root accessor）。

    * 不创建第二个 Collector（每次返回新 QueryService，数据源不变）；
    * 只读：无 record / clear（C43 边界）；
    * 本阶段**不接 HTTP API**：仅供内部测试 / 后续组合使用。
    """
    return RagObservabilityQueryService(_RAG_EXECUTION_COLLECTOR)


def get_rag_execution_persistence_adapter() -> RagExecutionPersistenceAdapter:
    """Application 级 RAG 持久化 Adapter（**同一实例**）。

    * 不是工厂：永远返回模块级同一个 Adapter（唯一创建点 = 本模块）；
    * 供装配断言 / 测试注入（例如替换 ``record`` 以模拟持久化失败）；
    * 生产链路不额外调用它（RagService 通过 Composite observer 触达）。
    """
    return _RAG_EXECUTION_PERSISTENCE_ADAPTER


def get_rag_execution_persistent_query_service() -> (
    RagExecutionPersistentQueryService
):
    """RAG 持久读边界装配（Composition Root accessor；只读）。

        （未来）Assistant Trace / 内部调用
            ↓ get_rag_execution_persistent_query_service()
        RagExecutionPersistentQueryService（只读；本阶段无 HTTP API）
            ↓
        RagExecutionRepository → ai_ops.rag_execution_record

    * 模块级**同一实例**（不每请求新建；不新增全局单例框架）；
    * 只读：无 create / update / delete / clear；
    * 与 Runtime 查询（Collector）互相独立：**无 fallback / 无 merge**。
    """
    return _RAG_PERSISTENT_QUERY_SERVICE
