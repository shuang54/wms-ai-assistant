# Phase 3.8.6 实施 ADR：Project Configuration Aggregation

> 本文件记录 Phase 3.8.6 的实施结论（侦察发现、设计取舍与回归结果）。
> 阶段任务书见同目录 `Phase 3.8.6：Project Configuration Aggregation.md`。

---

## 一、侦察发现（为什么需要收敛）

| 问题 | 侦察结论 |
| --- | --- |
| Registry 保存什么 | `project_id → ProjectRegistration(context / schema_name / capabilities)`；含 `_validate_project_id` 正则白名单与 `ProjectNotFoundError` |
| Capability 来源 | `registration.capabilities`（**Registry 自带**，无 CapabilityProvider） |
| Semantic 来源 | `ProjectSemanticProvider.get(project_id)`（InMemory / LoaderBacked 默认），在 `DefaultProjectContextProvider.resolve()` **每请求**调用 |
| Knowledge 来源 | `ProjectKnowledgeProvider.get_scope(project_id)`，工厂 build 期、**仅 `knowledge_enabled=True` 时**调用 |
| Factory 装配 | `registry.get` → `engine_provider.get_engine` → `DefaultProjectContextProvider(semantic_provider)` → knowledge（按需）→ `_build_project_tool_registry` |
| 配置漂移 | **存在**：三个 Provider 独立注册、来源不同（代码注册 / YAML / InMemory），无任何跨源一致性校验；"只有 Registry 先失败才能阻止 partial project"仅由 Factory 调用顺序隐式保证 |
| project_id 校验重复 | Registry 正则白名单 / Semantic 仅非空校验 / Knowledge 仅非空校验 / Engine 按键查找——四套不一致 |
| Semantic DTO 归属 | `ProjectSemantic` 只有 `tables / columns / relationships`，**无 `project_id` 字段** |

结论：需要一个**唯一配置出口**把四份结果一次性装配，并把"Registry 权威"从不变量升级为显式契约。

---

## 二、设计

### 2.1 DTO（任务书 §四，按真实字段调整）

```python
@dataclass(frozen=True)
class ProjectConfiguration:
    context: ProjectContext              # 含 DataSource 身份
    schema_name: str                     # 业务 schema（Factory 组装所需）
    capabilities: ProjectCapabilities    # 来自 Registry（不新增 CapabilityProvider）
    semantic: ProjectSemantic
    knowledge_scope: ProjectKnowledgeScope | None   # knowledge 禁用时为 None
```

调整说明：

- 任务书示例未列 `schema_name`，但工厂必须要它才能组装 Explorer / Tool（且它是非敏感配置结果），因此保留为 DTO 字段并在 `__post_init__` 校验；
- `knowledge_scope` 是**可选**的（保持 Phase 3.8.4 §十三：`knowledge_enabled=False` → KnowledgeProvider 0 次解析）；
- DTO 内**不含** registry / provider / service / engine / 任何凭据（有字段白名单测试锁定）。

### 2.2 Provider ≠ Configuration（§五）

```
Provider（如何获取）                Configuration（已解析结果）
 ├── ProjectRegistry            →   context / schema_name / capabilities
 ├── ProjectSemanticProvider    →   semantic
 └── ProjectKnowledgeProvider   →   knowledge_scope
```

### 2.3 读取顺序与权威性（§七 / §八）

```text
1. ProjectRegistry.get(project_id)  未注册 → ProjectNotFoundError（立即失败）
2. Capability ← registration.capabilities
3. ProjectSemanticProvider.get(project_id)
4. ProjectKnowledgeProvider.get_scope(project_id)  仅 knowledge_enabled=True
5. 装配 ProjectConfiguration
```

每个源**恰好 1 次**；缺失一律 clear error，**零 fallback**（不到 vietnam-wms / global / 其它项目）。

### 2.4 Core 层零改动（§十一）

Semantic 结果通过一个工厂内部的极小适配器交给既有
`DefaultProjectContextProvider`（它仍然只看到 "SemanticProvider" 接口）：

```python
class _ResolvedSemanticProvider:
    def get(self, project_id): ...   # 一致返回配置结果；不一致 → clear error
```

因此 **AIOrchestrator / Core 无任何字段改动**，`Orchestrator` 不持有 `ProjectConfiguration`。

---

## 三、修改 / 新增文件

```text
新增 backend/app/projects/configuration.py
    ProjectConfiguration（frozen DTO）
    ProjectConfigurationProvider（Protocol）
    DefaultProjectConfigurationProvider（Registry → Capability → Semantic → Knowledge）
    get_default_project_configuration_provider()

修改 backend/app/services/project_orchestrator_factory.py
    新增 keyword-only 入参 configuration_provider
    Factory 不再分别调用 registry.get / semantic_provider.get /
      knowledge_provider.get_scope，统一 configuration_provider.get(project_id)
    保留全部既有 DI 入参（registry / engine_provider / semantic_provider /
      knowledge_provider），旧调用与既有 monkeypatch 测试语义不变
    新增 _ResolvedSemanticProvider（Core 零改动适配器）

新增 tests/test_project_configuration.py（29 项）
    DTO / Case 1-6 / 调用次数 / Factory / 跨项目 / HTTP 注入安全

新增 docs/decisions/Phase/Phase 3.8.6：Project Configuration Aggregation 实施 ADR.md
```

未修改：ORM / Migration / Embedding / Reranker / ContextBuilder / VectorSearch
过滤语义 / Text-to-SQL / Tool / Semantic Provider 判定规则 / ProjectRegistry /
ProjectContext / AI Router / AI capability / AIOrchestrator 核心。

---

## 四、兼容性决策（关键）

1. **Factory 保留旧 DI 入参**：既有测试通过 `registry=` / `semantic_provider=` /
   `knowledge_provider=` 注入，以及 monkeypatch
   `factory_module.get_default_*`；因此 `configuration_provider=None` 时，
   Factory 用**工厂模块自身的 `get_default_*` 符号**构造默认装配器，
   monkeypatch 与注入语义完全保持。
2. **403 / 503 映射不变**：Semantic 缺失 → `AIOrchestratorUnavailableError(
   "…业务语义不可用…")`，Knowledge 缺失 → `…知识库上下文不可用…`（与 Phase 3.8.3 /
   3.8.4 既有文案一致）；未注册项目仍透传 `ProjectNotFoundError` → 404。
3. **Semantic 解析时机**：由"每请求 resolve()"变为"每次 build 聚合"。
   Factory 本身每次请求都重新 build，因此 YAML / Provider 读取次数等价；
   缺省行为（LoaderBacked 缺文件 → 空语义）不变。

---

## 五、回归结果

```text
python -m pytest -q                        → 1309 passed / 238 skipped / 0 failed
$env:RUN_DB_TESTS="1"; python -m pytest -q → 1509 passed /  38 skipped / 0 failed
python -m compileall backend               → OK
lint（修改文件）                            → 0 errors
数据库残留                                  → knowledge_document/chunk = (0, 0)
```

基线对比（Phase 3.8.5 完成态）：非 DB 1281 → 1309（+28）、DB 1480 → 1509（+29），
无既有用例被修改或删除。

---

## 六、已知限制

1. `ProjectSemantic` 无 `project_id` 字段 → 配置层**无法**在 DTO 内做
   "semantic 归属一致性"校验；一致性测试改为校验 semantic ≡该项目 Provider
   注册对象（identity），以及 `knowledge_scope.project_id == context.project_id`。
2. `schema_name` 放在 Configuration 而非独立 Provider：本阶段按 §十四"不为对称
   创造新 Provider"处理，未来若 schema 需要按环境动态解析，再单独收敛。
3. 每次请求仍会重新 build Orchestrator 并重新聚合配置（既有行为），
   本阶段不引入缓存 / 单例缓存以免改变"配置注册后可即时生效"的语义。
4. Semantic 解析从"每请求"变为"每次 build"，次数等价但**发生点提前**；
   若未来 Orchestrator 被长期复用，需要重新评估是否需要缓存与失效策略。
