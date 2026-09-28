# Phase 3.11 Step 19 — Observability Composition Root / Lifecycle Contract

> 目标：在 Step 18（AIOrchestrator 可注入 observer）之上，明确
> **Observability 组件由谁创建、谁持有、如何注入、生命周期多久**。
>
> 只做装配（Composition），不改任何 Step 13~18 的 Observability 核心组件。

```text
Application Composition Root（backend/app/api/orchestrator_chat.py）
        ↓ 仅此一处创建
InMemoryToolExecutionCollector            （Application lifetime）
        ↓ 同一对象；类型引用 = ToolExecutionObserver（Protocol）
AIOrchestrator（默认 + per-project 各自实例）
        ↓ TOOL 路径
ToolExecutionRecord → Collector（共享；request_id 各自独立）
        ↓ 只读下游（调用时计算；不在 Composition Root 缓存）
ToolExecutionMetricsService.snapshot(collector.records())
```

---

## 1. Purpose

```text
Step 18 结束时：AIOrchestrator 支持 tool_execution_observer，但生产装配仍为
observer=None → 真实应用运行时 ToolExecutionRecord = 0（观测价值为零）。

本阶段只回答 4 个问题：

    谁创建 Collector？    → Composition Root（api/orchestrator_chat.py）
    谁持有？              → 该模块（Application lifetime）
    如何注入？            → tool_execution_observer=<同一 Collector>（Protocol 类型）
    生命周期多久？        → 进程生命周期（无 TTL / 无自动 clear / 无持久化）

不做：持久化 / HTTP Metrics API / Dashboard / Prometheus / OpenTelemetry /
      Audit / Event Bus / Redis / Kafka / 后台任务 / DI 框架。
```

---

## 2. Architecture

```text
backend/app/api/orchestrator_chat.py              ← Composition Root（唯一装配点）

    _TOOL_REGISTRY            = build_default_tool_registry()        （既有）
    _TOOL_EXECUTION_COLLECTOR = InMemoryToolExecutionCollector()     ← Step 19 新增
    _TOOL_EXECUTION_OBSERVER  = _TOOL_EXECUTION_COLLECTOR            （Protocol 引用）

    _default_orchestrator = AIOrchestratorService(
        ..., tool_execution_observer=_TOOL_EXECUTION_OBSERVER)

    _build_orchestrator_for_project_id(project_id)
        → build_orchestrator_for_project(project_id, base=_default_orchestrator)
        （**签名不变**：观测出口随 base 继承）

backend/app/services/project_orchestrator_factory.py  ← Service Factory（内部最小修改）

    resolved_observer = getattr(base, "tool_execution_observer", None)
        → AIOrchestratorService(..., tool_execution_observer=resolved_observer)

    * Collector **不**由 Factory 创建（也不接收该依赖）；
    * base 无 observer（含 base=None 的默认 base）→ None（0 Record，旧行为）；
    * Factory 签名保持不变 → 既有「HTTP 不能注入配置」安全契约不受影响。

http POST /api/ai/chat
    ↓（response contract 不变）
    ChatResponse{route, content, data, metadata}
```

装配链（每个请求）：

```text
execute(question)
    ├── request_id = new_request_id()          ← request lifetime（Step 18 未变）
    ├── Router → TOOL → ToolExecutionContext → ToolExecutionService
    ├── ToolResult（原样返回调用方 / API）
    └── ToolExecutionRecord → Collector（Application lifetime）
```

---

## 3. 生命周期契约

```text
Collector   = Application lifetime
    * 仅在 Composition Root 创建一次（AST 扫描：production 代码唯一构造点）；
    * append / 只读查询（records / records_by_*） / 显式 clear；
    * **无** start / stop / flush / persist / close / shutdown；
    * **无** TTL / max_records / 定时清理 / 后台线程 / 自动 clear；
    * **无** 持久化（无 DB / 文件 / Redis / Kafka）；
    * **无** get_default_collector() 之类的全局工厂 helper。

request_id  = Request lifetime
    * 每次 execute() 独立（new_request_id()，唯一生成点位于 Orchestrator）；
    * Collector 共享**不**意味着 request_id 共享（两个请求 id 不同，
      且各自可用 records_by_request_id(id) 精确检索）。

ToolExecutionRecord = Request execution event（请求执行事件；不共享、不改写）。

Metrics = Read-only derived view
    * Composition Root **不**创建 Snapshot、**不**缓存 metrics、
      **不**维护全局 metrics 状态；
    * 需要时调用方自行 collector.records() → snapshot()。
```

---

## 4. 注入与失败隔离

```text
注入（保留 Step 18 语义）：
    * AIOrchestrator 只依赖 ToolExecutionObserver（Protocol），
      **不**依赖 InMemoryToolExecutionCollector（import 层锁定）；
    * Composition Root 可注入同一个 Collector 到多个 Orchestrator
      （默认 + per-project）；
    * observer=None 语义保留（测试 / 自定义装配 → 0 Record）。

失败隔离（不新增 try/except，复用 Step 15 / Step 18 契约）：
    * Collector.on_execution 抛异常 → 执行边界 warning 化并丢弃；
    * ToolResult / AIOrchestrationResult / API response 不变；
    * 不 retry / 不 fallback / 不重跑 Tool。
```

---

## 5. Security

```text
Composition Root 持有：Collector（仅 append / 读 / clear）
    —— 不持有 API Key / password / DATABASE_URL / token；
Collector 自身：``vars() == {"_records", "_lock"}``
    —— 无 Engine / Session / connect / execute / commit；
    —— 无 Tool Handler / Registry / invoke；
    —— 无 LLM client / prompt / model；
API response：不含 request_id / ToolExecutionRecord / metrics / collector / duration
    （ChatResponse 字段仍为 route / content / data / metadata）；
RAG / Text-to-SQL / capability denied → 0 Tool Record（仅 TOOL 路径观测）。
```

---

## 6. 修改边界（本阶段）

```text
修改（生产，仅 2 个文件）：
    backend/app/api/orchestrator_chat.py                  Composition Root 装配
        + import InMemoryToolExecutionCollector / ToolExecutionObserver
        + _TOOL_EXECUTION_COLLECTOR（Application lifetime，唯一创建点）
        + 注入 _default_orchestrator 与 _build_orchestrator_for_project_id
        + docstring（生命周期契约）
    backend/app/services/project_orchestrator_factory.py  Service Factory
        + 继承 base.tool_execution_observer（**签名不变**，无新增参数）

未修改：AIOrchestratorService / ToolExecutionService / ToolExecutionContext /
        ToolExecutionRecord / ToolExecutionObserver / InMemoryToolExecutionCollector /
        ToolExecutionMetricsService / ToolRegistry / Tool / Handler / ToolResult /
        Router / ToolChatService / main.py / DB schema
未引入：第三方 DI 框架 / 全局单例 helper / 持久化 / HTTP Metrics API /
        Dashboard / Prometheus / OpenTelemetry / Audit / Event Bus /
        Redis / Kafka / Celery / Worker / Agent / MCP / LangGraph / Memory / Planning
```

---

## 7. Test Summary

```text
tests/test_tool_observability_composition.py              31 passed
    Composition（6）/ Lifecycle（6，含真实 Factory 继承与「无 observer = 旧行为」）/
    Route isolation（3）/ API contract（4）/ Security（5）/
    Failure isolation（3）/ Collector ownership（4）

tests/test_tool_architecture_contract.py::TestC21*        15 passed
    C21.1 ~ C21.15

全量 no DB：3214 passed / 337 skipped（0 failed）
    （Step 18 基线 3167 / 338 → +47 passed / −1 skipped：
      +31（Composition）+ 15（C21）= +46 新增；
      另 1 例 test_ai_orchestrator.py::TestProjectContextProvider 由
      「跳过（DB 不可用）」变为「通过（PostgreSQL 已恢复）」）
    **零既有测试修改**（无断言放宽 / 无用例删除）

DB-gated 全量（RUN_DB_TESTS=1，PostgreSQL 已恢复）：3510 passed / 41 skipped（0 failed）
    首次运行出现 1 failed：tests/test_knowledge_ingestion_service.py::
    TestTxtIngestion::test_txt_full_pipeline_success
    （assert 'already_exists' == 'ready' —— 数据状态 / 跨用例 fixture 去重问题）；
    该用例单独运行与「同文件运行」均通过；复跑全量 DB 套件 0 failed。
    与本阶段改动无关（涉及 KnowledgeIngestionService 与 knowledge_document 表，
    Step 19 未触碰 knowledge / DB 代码路径），按纪律**不**在本次修改。
```

---

## 8. Limitations

```text
* Collector 为进程内内存（重启即消失）；容量治理（上限 / TTL / 轮转）= 后续阶段
  （Phase 3.11 Step 20 已实现**条数上限** `max_records`（默认 1000，FIFO）；
  TTL / 轮转仍未实现 → 见 Step 20 文档与 architecture §8.38）；
* 无 HTTP Metrics API / Dashboard / Prometheus / OpenTelemetry / Audit；
* Composition Root 为模块级装配（与既有 _TOOL_REGISTRY / _default_orchestrator
  同风格），非 FastAPI lifespan / startup-shutdown hook；
* 无多进程 / 多实例聚合（每个进程各自 Collector）；
* 不观测 RAG / Text-to-SQL（仅 TOOL 路径）；capability denied 不产生 Record；
* 无 request_id 透出（API response 有意不含）；
* lint 工具不可用（环境未安装 ruff / flake8，未新装工具）。
```
