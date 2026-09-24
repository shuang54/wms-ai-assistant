# Phase 3.8.4：Project Knowledge Context 实施 ADR

> 状态：已实施
> 阶段：Phase 3.8.4（Project Runtime Context 系列第 4 阶段）
> 前置：Phase 3.8.1（DataSource）、3.8.2（Capabilities）、3.8.3（Semantic）

---

## 1. 为什么要 Project Knowledge Scope

Phase 3.8.1～3.8.3 之后，Project Runtime Context 已包含：

```text
Project
├── DataSource    （3.8.1：数据源切换）
├── Capabilities  （3.8.2：能力开关）
├── Semantic      （3.8.3：业务语义）
└── Knowledge     （本阶段：知识检索范围）
```

此前 Knowledge Store（`knowledge_document` / `knowledge_chunk`）是**全局共享**的：
任何 project 的 RAG 请求都会检索整个知识库。这意味着：

- project-a 的知识会被 project-b 检索到（跨项目泄漏）；
- 无法为不同项目维护不同的知识集合；
- 无法证明 "project_id 决定 Knowledge Context"。

本阶段把 Knowledge 正式纳入 Project Runtime Context，形成最终运行链：

```text
HTTP project_id
      ↓
ProjectRegistry
      ↓
ProjectContext（DataSource / Capabilities / Semantic / Knowledge）
      ↓
RAG Service（Project-scoped Retrieval）
      ↓
VectorSearch（显式 scope 过滤）
      ↓
[ Reranker ] → Context → LLM
```

## 2. 当前旧架构的问题

1. `VectorSearchService.search()` 无任何过滤维度——全库 `ORDER BY embedding <=> :vec`；
2. `RagService.answer()` 无 scope 参数；
3. 工厂注释明确 "RAG 继续复用 base 的全局 RagService（知识库不按项目拆分）"；
4. Orchestrator 的 RAG 路径不知道项目，无法传递知识上下文；
5. `knowledge_document` / `knowledge_chunk` 无 `project_id` 列（历史数据无归属标记）。

## 3. Provider 设计

新增 `backend/app/projects/knowledge_provider.py`：

```python
@dataclass(frozen=True)
class ProjectKnowledgeScope:
    project_id: str
    namespace: str
    includes_global: bool = True     # 共享 __global__ 公共知识
    includes_legacy: bool = False    # 可见无 project_id 标记的历史知识

class ProjectKnowledgeProvider(Protocol):
    def get_scope(self, project_id: str) -> ProjectKnowledgeScope: ...
```

两个实现（与 3.8.3 SemanticProvider 同构）：

- `InMemoryProjectKnowledgeProvider`：显式注册、线程安全、重复注册 /
  类型错误 / 未注册均 clear error，**绝不静默回退到其他项目**；
- `DefaultProjectKnowledgeProvider`（默认）：每个项目 → 自身 namespace +
  `__global__`；仅历史项目（`settings.project.project_id`，即 vietnam-wms）
  额外 `includes_legacy=True`。

约束：frozen DTO、无 DB 连接、无密码、无 LLM、无 HTTP、不做热加载、
不接受 HTTP 传入路径。字段白名单有单元测试锁定。

## 4. Project → Knowledge 的绑定方式

```text
HTTP（仅 project_id + question）
      ↓
orchestrator_chat._build_orchestrator_for_project_id
      ↓
build_orchestrator_for_project(project_id)
      ├── ProjectRegistry.get(project_id)     （未注册 → 404）
      ├── capabilities.knowledge_enabled？
      │       ↓ True
      ├── knowledge_provider.get_scope(project_id)  （未注册 → 503）
      ↓
AIOrchestratorService(knowledge_scope=scope)
      ↓
_run_rag → rag.answer(question, knowledge_scope=scope)
```

- 绑定只存在于服务器端 Provider / Factory；HTTP 请求体中的
  `knowledge_scope` / `knowledge_id` / `knowledge_file` /
  `knowledge_namespace` / `knowledge_project_id` 均被 Pydantic 丢弃（有测试）；
- `knowledge_enabled=False` 时 Factory **不解析** scope（Provider 0 次执行，
  任务书 §十三）；即使 Router 兜底落到 RAG，Orchestrator capability 硬校验
  也在 RagService 之前拦截（403）。

## 5. VectorSearch 如何实现 Scope

`VectorSearchService.search(query, *, top_k, knowledge_scope=None)`：

- `knowledge_scope` 非 None → JOIN `knowledge_document` 并**强制**过滤
  `meta_data->>'project_id'`：

```text
普通项目：    = namespace OR = '__global__'
legacy 项目：  = namespace OR = '__global__' OR IS NULL
```

- `knowledge_scope=None` → 旧行为（无 JOIN、无过滤），供历史端点
  `/api/chat`、`/api/rag/answer` 与既有 Mock 兼容。

**补充要求 §1 落实**：global 是显式命名空间 `__global__`，scope 存在时
永不退化为全库检索；"不加过滤" 仅保留给 scope=None 的历史调用形态。

**legacy 语义（补充要求 §2 / §3）**：

```text
meta_data.project_id IS NULL        → legacy，仅 vietnam-wms 可见
meta_data.project_id = "__global__" → 公共，所有项目共享
meta_data.project_id = "project-a"  → 仅 project-a
meta_data.project_id = "vietnam-wms"→ 仅 vietnam-wms
```

隔离矩阵（DB 集成测试锁定）：

| 请求项目     |  A |  B | Global | Legacy | VN |
| ---------- | -: | -: | -----: | -----: | -: |
| project-a  | ✅ | ❌ |      ✅ |      ❌ |  ❌ |
| project-b  | ❌ | ✅ |      ✅ |      ❌ |  ❌ |
| vietnam-wms| ❌ | ❌ |      ✅ |      ✅ |  ✅ |

隔离在 **VectorSearch（Reranker 之前）** 完成，符合任务书 §十九：
候选集进入 Reranker 前已按项目过滤。

## 6. 为什么不让 RagService 读取 ProjectRegistry

依赖倒置（任务书 §十）：

```text
Factory → KnowledgeProvider → KnowledgeScope → RagService → VectorSearch
```

而不是 `RagService → ProjectRegistry → KnowledgeProvider`。

- RagService / VectorSearch 保持可复用（不知道 Registry / DataSource /
  Tool / Capability / Semantic / LLM 配置的存在）；
- Orchestrator 只传递上下文，不访问 Knowledge DB；
- scope 是 frozen 值对象，可以安全地在进程内传递与断言。

## 7. Capability 与 Knowledge 的关系

Phase 3.8.2 的 `knowledge_enabled` 继续有效，且是 RAG 的**第一道**硬校验：

```text
knowledge_enabled=False
      ↓
Router 规则不选 RAG（仍可能保守兜底到 RAG 决策）
      ↓
Orchestrator._check_capability("knowledge") → 403（RagService 0 次调用）
      ↓
（Factory 侧：Provider / VectorSearch / RagService 均不执行）
```

本阶段新增验证：Factory 在 `knowledge_enabled=False` 时**不解析**
knowledge scope（Provider 0 次执行），纵深防御三层齐全。

## 8. 安全隔离

| 威胁 | 防线 | 测试 |
|---|---|---|
| 跨项目知识检索 | VectorSearch 显式 scope 过滤（Reranker 前） | `TestVectorSearchKnowledgeMatrix` |
| HTTP 注入 knowledge 字段 | Pydantic 丢弃未知字段；scope 只由服务器端 Provider 决定 | `test_api_http_injection_ignored` |
| 未知项目 | ProjectRegistry → 404，绝不 fallback 到 vietnam-wms | `test_api_unknown_project_404` |
| 项目知识未注册 | Provider clear error → 503，不回退其他项目知识 | `test_api_unregistered_knowledge_503` / `test_unregistered_knowledge_503` |
| 能力禁用绕过 | capability 403 + Provider/RAG 零执行 | `test_capability_disabled_provider_not_executed` / `test_api_capability_disabled_403` |
| legacy 泄漏 | `includes_legacy` 仅历史项目为 True | `test_normal_projects_cannot_hit_legacy` |
| 同一问题串项目 | scope 在调用点独立解析 | `test_api_same_question_different_projects` |

## 9. 向后兼容策略

1. **零数据库 Migration**：复用 `knowledge_document.meta_data` JSONB
   （其设计用途即"业务自定义元数据"）。历史数据 `meta_data` 为 NULL /
   不含 `project_id` 键 → `->>'project_id'` 为 SQL NULL → legacy 语义，
   仅 vietnam-wms 可见；
2. **scope=None 调用形态保留**：`/api/chat`、`/api/rag/answer` 及既有
   Fake/Mock 签名完全不变（RagService / Orchestrator 仅在 scope 非 None
   时传递新参数）；
3. **默认 Provider 兼容**：未显式配置知识的项目（Default Provider）获得
   自身 namespace 的 scope——不注册知识的项目检索结果为空（安全默认），
   唯独 vietnam-wms 保持历史全量可见（legacy + `__global__` + 自身）；
4. **3.8.2 测试 Fake 适配**：`test_project_capability_isolation._FakeRag.answer`
   增加 `knowledge_scope=None` 参数（与新签名对齐，行为不变）。

## 10. 当前限制

1. **JSONB 过滤暂无索引**：`meta_data->>'project_id'` 过滤是顺序扫描后
   再做向量排序（数据量小，可接受）；数据量增长后需要 GIN/表达式索引；
2. **知识归属靠写入约定**：ingestion pipeline 尚未自动打
   `meta_data.project_id` 标签；当前项目知识需要写入时显式标记
   （测试即范例：`KnowledgeDocument(meta_data={"project_id": "project-a"}, ...)`）；
3. **全局检索路径仍存在**：scope=None（`/api/chat`、`/api/rag/answer`）
   是历史端点的全局检索，未接入项目体系——多项目租户场景应统一走
   `/api/ai/chat` + project_id；
4. **Provider 无热加载 / 无配置中心**：InMemory 注册为进程内静态注册，
   变更需重启（与 SemanticProvider 一致，按需演进）；
5. **`includes_global` 尚无"完全私有项目"的实际需求**：字段已预留，
   未在 Factory 暴露配置入口。

## 11. 后续是否需要 Knowledge DB Migration

**当前不需要**。`meta_data` JSONB 已可安全表达 Project Scope，本阶段
零 Migration 完成真实隔离（DB 集成测试证明）。

未来出现以下需求时再评估：

- 知识量增大导致 JSONB 过滤性能问题 → 表达式索引或独立 `project_id`
  列 + B-tree 索引；
- 需要在 DB 层强约束归属（外键到 project 表）；
- ingestion pipeline 需要按项目批量管理（list / delete by project）。

届时在 `docs/database.md` 记录迁移方案并单独开阶段执行。

---

## 附录：本阶段文件清单

新增：

```text
backend/app/projects/knowledge_provider.py
tests/test_project_knowledge_provider.py
tests/test_rag_project_scope.py
tests/test_project_knowledge_isolation.py
docs/decisions/Phase/Phase 3.8.4：Project Knowledge Context 实施 ADR.md
```

修改：

```text
backend/app/services/vector_search_service.py   （knowledge_scope 参数 + 显式过滤）
backend/app/services/rag_service.py             （knowledge_scope 透传）
backend/app/services/ai_orchestrator_service.py （knowledge_scope 注入 + RAG 传递）
backend/app/services/project_orchestrator_factory.py（knowledge_provider 接线）
tests/test_project_capability_isolation.py      （_FakeRag 签名适配）
```

测试结果：`pytest -q` 1267 passed / 0 failed；
`RUN_DB_TESTS=1 pytest -q` 1449 passed / 0 failed；`compileall` 通过。
