# Phase 3.8.3：Project Semantic Context 正式项目化 — 实施 ADR

> **实际实施结果与技术决策记录**（任务书副本见
> `Phase 3.8.3：Project Semantic Context.md`）。
>
> 在 3.8.1（DataSource 切换）与 3.8.2（Capabilities）基础上，
> 让 Business Semantic 成为 Project Runtime Context 的正式组成部分：
> `project_id` 唯一决定 Semantic，同一套 AI Core 零改动。

---

## 1. 当前 Semantic 架构（改造前）

- DTO（`projects/semantic.py`，3.7.3）：`TableSemantic / ColumnSemantic /
  BusinessRelationship / ProjectSemantic`（frozen，空语义合法）；
- Loader（`semantic_loader.py`）：`<project_id>.yaml` 文件名约定 →
  严格结构校验 → `ProjectSemantic`；
- Serializer / Composer / Selector：`semantic` 均为**显式参数**，本就
  per-project ready，零改动复用；
- **缺口**：`DefaultProjectContextProvider.resolve()` 内部自建 Loader
  按 project_id 加载，但（a）加载失败**静默回退空语义**（吞异常）；
  （b）Semantic 与 ProjectRegistration / 工厂**无正式绑定**——
  Orchestrator 依赖隐式文件名约定，工厂组装时从未注入语义依赖。

## 2. 为什么需要 ProjectSemanticProvider

任务书 §十二：Orchestrator 不应自己读 YAML（`open(...) /
yaml.safe_load(...)`）；§五：`project_id` 必须唯一决定 Semantic，
且映射只能由服务器端控制（HTTP 无法指定 semantic 文件 / 内容）。
引入显式 Provider 抽象后，语义来源从"Orchestrator 内部隐式约定"
变为"工厂组装时显式注入的服务器端依赖"。

## 3. Project → Semantic 映射（新增 `projects/semantic_provider.py`）

```text
ProjectSemanticProvider（Protocol）: get(project_id) -> ProjectSemantic
 ├── InMemoryProjectSemanticProvider   显式注册（服务器端 / 测试）
 │      未注册 → ProjectSemanticNotFoundError（clear error，不静默）
 └── LoaderBackedProjectSemanticProvider   最小包装既有 Loader
        文件名 == project_id 约定；缺文件 → 空语义（合法）；
        配置错误 → ProjectSemanticConfigError 透传
```

- **不新建 DTO**（无 ProjectSemanticV2 / ProjectSemanticContext）；
- **不重写 Loader**：LoaderBacked 只是 1 层包装（任务书 §四
  "最小改造并复用"）；
- **ProjectRegistration 不动**：语义绑定不塞进注册条目（避免职责
  混乱，任务书 §五），由工厂在组装时通过 `semantic_provider`
  参数显式连接——`registry.get(project_id)` 与
  `semantic_provider.get(project_id)` 以同一 project_id 对齐；
- 线程安全；注册重复 / 类型非法 / 空 project_id → clear error。

## 4. Serializer / ContextComposer 复用

零改动。链路变为：

```text
project_id → ProjectSemanticProvider.get() → ProjectSemantic
          → BusinessSemanticSerializer.serialize()   （稳定文本）
          → DatabaseContextComposer.compose(project, schema, semantic, tables)
          → TextToSQLService.generate(database_context=...)
```

## 5. Orchestrator 集成（`DefaultProjectContextProvider`）

- 新增构造参数 `semantic_provider`（优先于旧 `semantic_loader`，
  旧参数保留兼容既有测试）；
- Provider 路径：`semantic_provider.get(project.project_id)`；
  **异常不吞**——`ProjectSemanticError` → `AIOrchestratorUnavailableError`
  （HTTP 503，绝不静默回退其他项目语义）；
- 未注入 provider → 旧 Loader 路径（缺文件回退空语义，旧行为不变）；
- Orchestrator 自身零改动（仍消费 `(ProjectContext, DatabaseSchema,
  ProjectSemantic)` 三元组，不读 YAML）。

## 6. RelevantTableSelector

零改动复用，但**使用当前项目语义**：`select(question, schema,
semantic)` 的 semantic 即 Provider 解析结果。`_index_semantic` 把
语义表引用对齐到 Schema 事实——**A 语义（引用 project_a.inventory）
在 Schema B 下不被索引**（引用不存在 → 忽略），从机制上杜绝
"Schema B + Semantic A" 交叉（单元测试实证）。

## 7. Factory 集成

`build_orchestrator_for_project` 新增 `semantic_provider` 参数：

- `None` → `get_default_project_semantic_provider()`（惰性单例 =
  LoaderBacked，vietnam-wms → vietnam-wms.yaml，旧行为完全等价）；
- 注入后传入 `DefaultProjectContextProvider(semantic_provider=...)`；
- API 层不感知 Semantic（只传 project_id / question，
  `ChatRequest` 零改动）。

## 8. 测试结果

### 单元（`tests/test_project_semantic_provider.py`，28 项，无 DB / LLM）

| 验收点 | 结果 |
|---|---|
| 16.1 Provider：known → semantic / unknown → clear error / A-B 隔离 / 重复注册 / 注销 / 类型校验 | ✅ |
| LoaderBacked：存在 yaml → 解析 / 缺文件 → 空语义 / 配置错误透传 / 默认单例 vietnam-wms 语义 | ✅ |
| 16.3 Serializer：A 只序列化 A 语义 / B 只序列化 B 语义 / 确定性 | ✅ |
| 16.4 Selector：A "库存"→A.inventory / B "可用库存"→B.inventory / A 语义错配 Schema B 不生效 / A 专属别名"料号"在 B 语义下不命中 | ✅ |
| 16.5 Composer：Schema A+Semantic A / Schema B+Semantic B，互不含对方 schema 与语义 | ✅ |
| Provider 注入：注入 → resolve 返回该项目语义 / 未注册 → 503 clear error / 无 provider → 旧路径空语义 / 旧 semantic_loader 参数兼容 | ✅ |

### DB 集成（`tests/test_project_semantic_isolation.py`，9 项，RUN_DB_TESTS=1）

测试项目：project-a（schema project_a，语义：库存/物料编码/库存数量）、
project-b（schema project_b，语义：可用库存/产品编号/可用数量）——
**同一表结构，语义故意不同**（任务书 §七）。Fake Generator 记录
`database_context / allowed_tables / schema`（不调真实 LLM，§16.6）。

| 验收点 | 结果 |
|---|---|
| 16.6 A → context 含 Schema A + Semantic A，无任何 B 语义/schema；allowed_tables=(project_a.inventory,) | ✅ |
| 16.6 B → context 含 Schema B + Semantic B，无任何 A 语义/schema；allowed_tables=(project_b.inventory,) | ✅ |
| §十一 同一问句两项目语义各自生效（A "库存"命中；B 下退化为 Schema B + Semantic B） | ✅ |
| §九 Fake SQL 在各自 schema 执行（A→100 / B→999，Executor 同源） | ✅ |
| 默认项目回归：vietnam-wms → LoaderBacked 默认 Provider → yaml 语义（旧行为） | ✅ |
| §十七 API E2E：A/B 分别拿到各自语义（HTTP 200 + route=text_to_sql） | ✅ |
| §十五 安全：注入 semantic_file / semantic / semantic_id → 忽略，仍 A 语义 | ✅ |
| §十五 安全：unknown project → 404，未触达 Generator | ✅ |

### 全量回归

| 命令 | 结果 |
|---|---|
| `pytest -q` | ✅ **1226 passed, 195 skipped** |
| `RUN_DB_TESTS=1 pytest -q` | ✅ **1383 passed, 38 skipped** |
| `python -m compileall backend` | ✅ 通过 |
| LSP / lint | ✅ 0 errors |
| 测试 schema 清理 | ✅ 无 `project_%` 残留 |

## 9. 修改 / 新增文件清单

| 操作 | 路径 | 说明 |
|---|---|---|
| 新增 | `backend/app/projects/semantic_provider.py` | Protocol + InMemory + LoaderBacked + 默认单例 |
| 修改 | `backend/app/services/ai_orchestrator_service.py` | DefaultProjectContextProvider 增加 semantic_provider 注入（异常不吞） |
| 修改 | `backend/app/services/project_orchestrator_factory.py` | semantic_provider 参数 + 默认单例注入 |
| 新增 | `tests/test_project_semantic_provider.py` | 单元测试（28 项） |
| 新增 | `tests/test_project_semantic_isolation.py` | DB 集成测试（9 项） |
| 新增 | 本 ADR | — |

**未修改**：semantic.py（DTO）/ semantic_loader.py（Loader）/
BusinessSemanticSerializer / DatabaseContextComposer /
RelevantTableSelector / TextToSQLService / SQL Validator / SQL Executor /
Router / RAG / Reranker / Tool Framework / ProjectRegistration /
ChatRequest / 数据库结构（0 migration）。

## 10. 已知限制与设计取舍

1. **默认 Provider（LoaderBacked）缺文件回退空语义**：与 3.8.3 之前
   行为一致（空语义是合法状态"项目尚未配置业务语义"）；显式注册
   语义的项目请用 InMemory（未注册 → clear error 503）。两种语义
   按 Provider 实现划分，均有测试锁定。
2. **Provider 注册与 ProjectRegistry 注册是两个独立服务器端动作**：
   项目在 Registry 注册但语义未在 InMemory Provider 注册 → 503。
   未来可考虑合并为单一项目配置入口（配置中心 / YAML 注册表）。
3. **Selector substring 匹配的固有特性**："库存"（A）是 "可用库存"（B）
   的子串——A 语义下问"可用库存"仍会命中（加分项重叠）。这不构成
   语义串项目（A 请求绝不含 B 的语义文本）；如需更精确匹配，
   未来可引入分词 / 精确词边界（不在本阶段）。
4. **不做热加载**：InMemory / LoaderBacked 均为进程内固定；
   YAML 修改需重启（任务书 §二）。
5. **RAG Knowledge Store 仍全局共享**（任务书 §十九）：
   未增加 knowledge_project_id，未改 knowledge_document /
   knowledge_chunk / Reranker（Project Knowledge Isolation 留待后续）。
6. **测试对 factory 内部单例的依赖**：集成测试通过 monkeypatch
   `factory_module.get_default_project_registry / 
   get_default_project_semantic_provider` 注入测试实例；
   未来若 factory 改为依赖注入容器，测试需同步调整。
