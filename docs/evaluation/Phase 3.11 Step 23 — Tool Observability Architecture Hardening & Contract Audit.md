# Phase 3.11 Step 23 — Tool Observability Architecture Hardening & Contract Audit

> 本阶段**不新增业务能力**：对 Step 1~22 形成的 Tool Execution / Observability
> 边界做一次完整 Contract Audit（依赖图 / 方向 / 各层契约 / 安全 import），
> 并把结论固化为可执行断言（C25）。
>
> **生产代码修改 = 0**（未发现真实架构缺陷 / 安全越界 / 实际循环依赖）。

---

## 1. Purpose

```text
前 22 个 Step 陆续建立了：
    Execution Context → Record → Observer → Collector → Retention →
    Query Service → Snapshot → Metrics

风险：逐阶段增量容易积累——
    * 隐式反向依赖（Observability → Execution）
    * 职责泄漏（Collector 变成写层 / Query 变成执行层）
    * 延迟 import 掩盖的循环依赖
    * API / 配置漂移

本阶段只做：审计 + 固化 + 回归。不做重构 / 不做抽象 / 不做新功能（§二）。
```

---

## 2. Dependency Graph（静态实测）

```text
context           -> （无内部依赖）
record            -> context, tools.base
observer          -> record
collector         -> record
metrics           -> record
snapshot          -> record
service           -> context, observer, record, registry, projects.capabilities
                     + 延迟：ai_orchestrator_service（AIOrchestratorCapabilityError）
query             -> collector, metrics, record, snapshot
extractor         -> （无内部依赖）
orchestrator      -> context, extractor, observer, registry, service
tool_chat_service -> context, registry, service
factory           -> orchestrator, registry
api/orchestrator_chat -> collector, observer, orchestrator + 延迟：factory
api/tool_chat     -> orchestrator, registry, service, tool_chat_service
registry          -> （无内部依赖）
```

结论：

```text
顶层（模块加载时）依赖图 = DAG（无环）                        ✅
函数内延迟 import 仅 2 处（均为历史有意决策）                 ✅
    1) tool_execution_service → ai_orchestrator_service
       （Phase 3.8.2：capability 拒绝异常类型共享；模块级导入会成环）
    2) api/orchestrator_chat → project_orchestrator_factory
       （Phase 3.8.1：仅在 _build_orchestrator_for_project_id 内导入）
按 §五「不要因为看到 delayed import 就自动修改」→ 保持原样（无实际循环依赖）。
```

---

## 3. Execution / Observability 方向

```text
允许（实测）：
    Execution → Observability：service → observer → record（单向）
    Query → Collector → Metrics（只读方向）

禁止（实测全部 NO）：
    Collector → Orchestrator / Service / Query / API        NO
    Metrics   → Collector / Service / Query                 NO
    Query     → ToolExecutionService / ToolRegistry / API    NO
    Snapshot  → Collector / Metrics / Service               NO
    Record    → API / Collector / Metrics                   NO
    Observer  → Service / Collector                         NO
    Service   → Collector（只依赖 Observer **Protocol**）    NO
    Orchestrator → Collector / Metrics / Query / Snapshot    NO
    ToolRegistry → Orchestrator / Query / Observability      NO
```

---

## 4. 各层契约审计结果

```text
Record      frozen ✅ / 字段白名单 11 项 ✅ / 无 arguments·SQL·prompt·
            response·DB·session·secret·traceback ✅ / 无可变集合 ✅ /
            无持久化 ✅
            （注：模块提供 now_utc() / elapsed_ms() 计时 helper，
              由**调用方**（执行边界）使用；DTO 自身不测量时间）

Collector   finite（max_records，默认 1000）✅ / FIFO ✅ / 单 Lock 线程安全 ✅ /
            explicit clear ✅ / 无 TTL ✅ / 无后台线程 ✅ / 无持久化 ✅
            边界：max_records=1（只留最新 1 条）✅；
                  max_records=100000（未达上限全保留）✅

Observer    只接收 ToolExecutionRecord（Protocol runtime_checkable）✅
            失败隔离：observer 抛异常 → ToolResult / 返回值不变、
            Handler 不重跑、不 retry / 不 fallback ✅
            语义确认：context=None → 0 事件（Step 15 设计，非缺陷）✅

Metrics     纯只读计算（public API == {snapshot}）✅
            不改 Record / 不改 Collector ✅
            空数据集：rates / average / max = None（None ≠ 0）✅

Query       READ ONLY（无 clear / append / on_execution / evict）✅
            无自有存储（内部仅 {_collector, _metrics_service}）✅
            无 deque / list / dict 缓存 / Lock ✅

Snapshot    frozen ✅ / 独立 DTO（≠ Record）✅ / 显式逐字段映射 ✅
            无 vars / asdict / __dict__ 自动传播 ✅
```

---

## 5. API / Composition Root / Project 边界

```text
API         无任何 Tool Observability HTTP 端点（路由路径级扫描：
            无 /observability · /metrics · /tool-observability）✅
            api 模块不依赖 Query Service / Snapshot ✅
            （既有的 /api/usage 是 Phase 3.10 LLM Usage，与本链路无关）

Composition Root
            Collector 唯一创建点 = backend/app/api/orchestrator_chat.py
            （全 backend AST 扫描：仅此一处）✅
            AIOrchestrator 不创建 Collector / Observer / Query Service ✅
            （源码无 InMemoryToolExecutionCollector / Collector( /
              ToolObservabilityQueryService / ToolExecutionSnapshot /
              ToolExecutionMetricsService；标识符无 max_records /
              retention / evict / snapshot）

Project     project_id = Authorization Scope（服务器端），
            不来自 question / arguments / LLM ✅
            Query Service 不做权限判断、不扩大权限：
            capability 拒绝 → 0 Record + Query 仍为空 ✅
```

---

## 6. Security Import Audit

```text
Observability 层（record / observer / collector / metrics / query / snapshot）
不依赖：sqlalchemy · psycopg · redis · kafka · celery · requests · httpx ·
        subprocess · backend.app.db · backend.app.llm · backend.app.api     ✅
Collector 不依赖：LLM / ToolRegistry / Tool Handler / API / Database        ✅
无持久化 / 无后台任务 / 无新 settings·环境变量（标识符级断言）              ✅
```

---

## 7. Contract Matrix（§十九）

```text
Layer        Can Write                  Can Read      Execute Tool   DB   LLM
Service      execution only（不改 Record 集合）  ToolResult   YES     间接*   NO
Record       NO                         data          NO            NO    NO
Observer     NO（on_execution = 接收点）  Record        NO            NO    NO
Collector    Record 集合（内存）          Record        NO            NO    NO
Metrics      NO                         Record        NO            NO    NO
Query        NO                         Collector     NO            NO    NO
Snapshot     NO                         Record        NO            NO    NO

* DB：Tool Handler 自身可能访问数据库（由 Tool 定义决定），
  Observability 层**永不**直接访问。
```

---

## 8. C25 契约测试

```text
tests/test_tool_observability_architecture_audit.py（20 passed）

Dependency   C25.1 顶层无循环 import / C25.2 延迟 import 仅限已知两处 /
             C25.3 Observability 不反向依赖 Execution /
             C25.4 Tool·Registry 不依赖 Observability
Contract     C25.5 Record / C25.6 Collector（finite·FIFO·线程安全）/
             C25.7 explicit clear·无 TTL·无持久化 /
             C25.8 Observer 只收 Record + 失败隔离 /
             C25.9 Metrics 只读 + None 语义 / C25.10 边界值（1 / 极大）
Boundary     C25.11 Query READ ONLY / C25.12 Snapshot 独立 frozen /
             C25.13 无 Tool Observability HTTP 端点
Composition  C25.14 Collector 仅由 Composition Root 创建 /
             C25.15 Orchestrator 不创建 Collector·Observer·Query
Project      C25.16 capability 不可被 Query 绕过
Security     C25.17 Read Model import 白名单 / C25.20 无持久化·配置
Matrix       C25.18 层级能力矩阵
Route        C25.19 TOOL → 1 Record；tool_call_id=None；round=1
```

---

## 9. 修改边界

```text
生产代码：0 修改
新增：tests/test_tool_observability_architecture_audit.py（20）
文档：本文件 + docs/architecture.md §8.41 +
      docs/evaluation/Phase 3.11 — Tool Execution Boundary.md
      （追加 Historical note：Step 9 已把 ToolChatService 的
        registry.execute() 直连迁移为 ToolExecutionService；
        **未改写任何历史事实**）
```

---

## 10. Test Summary

```text
tests/test_tool_observability_architecture_audit.py                   20 passed
tests/test_tool_architecture_contract.py（C1~C12 / C20 / C21）          83 passed
tests/test_tool_chat_architecture_contract.py（C13~C19 / C22~C24）     149 passed
全量 no DB                                                    3382 passed / 337 skipped（0 failed）
  （Step 22 基线 3362 / 337 → +20 = C25 审计用例）

DB-gated（RUN_DB_TESTS=1，本阶段**可选**）：
  * 真实 DB 的 Tool 链路回归：test_get_inventory_tool +
    test_get_work_order_tool + test_tool_execution_context
    → **177 passed（0 failed）**（证明 DB 环境可达、Tool 链路正常）
  * 全量 DB 套件：仍无法跑完 —— knowledge 域 DB 用例再次因遗留数据状态失败
    （already_exists），随后后续 DB 用例挂起；即使排除 5 个 knowledge 文件
    仍在 35% 处停滞。属 Step 19/21/22 同款的
    **Pre-existing DB fixture/data-state coupling**，与 Step 23（0 生产改动、
    纯静态审计）无关；按 §二十四**不修改、不绕过**。
```

---

## 11. 审计发现（非缺陷，记录备查）

```text
1) tool_execution_service → ai_orchestrator_service 为**函数内延迟 import**
   （Phase 3.8.2 历史决策：共享 AIOrchestratorCapabilityError 类型）。
   逻辑上存在双向类型依赖，但模块加载**无环** → 按 §五保持原样。

2) ToolExecutionService：context=None → 0 事件（不计时、不建 Record）。
   这是 Step 15 的既定语义（"边界不自行生成 request_id"），
   不是缺陷；审计用例已显式锁定该语义。

3) api/orchestrator_chat.py 的 Step 19 注释仍写"无 max_records"——
   该文件在 Step 20/22 均属禁止修改范围；以 Step 20 文档与
   architecture §8.38 为准（文档层陈旧，非代码问题）。

4) knowledge 域 DB 集成测试存在数据状态耦合（already_exists → 后续用例挂起）。
   Step 19 首次记录，Step 21/22 复现；与 Observability 链路无关。
```

---

## 12. Limitations

```text
* 本阶段不重构、不抽象、不新增 Repository / Port / Protocol（§二）
* 审计为静态结构 + 少量行为断言；不做性能 benchmark、不做覆盖率统计
* 延迟 import 未消除（保持历史决策；未来如需收敛需独立评估）
* Query Service 当前依赖具体 InMemoryToolExecutionCollector 类型
  （Step 21 §十三允许；未来持久化时再抽象）
* 无生产接线：Query Service / Snapshot 未接入 Composition Root（有意）
* DB-gated 套件受既有 knowledge 数据状态影响（已单独记录，未绕过）
* lint unavailable（环境未安装 ruff / flake8）
```
