# Phase 3.11 Step 18 — AIOrchestrator Tool Execution Observability Integration

> 目标：把 **AIOrchestrator 主链路（链路 A）** 的 TOOL 路径接入 Step 13~17 已建成的
> Execution Context + Observer / Collector / Metrics —— **只做装配**，
> 不新增执行能力、不修改任何既有组件。
>
> Step 17 结束时链路 A 仍 `context=None`（执行边界不产生 Record）；
> 本阶段补齐：

```text
Question
   ↓
AIOrchestrator
   ↓
AI Router
   ↓
TOOL
   ↓
ToolExecutionContext          ← Step 18 新增（由 Orchestrator 创建）
   ↓
ToolExecutionService          （未修改）
   ↓
ToolRegistry                  （未修改）
   ↓
Tool
   ↓
ToolResult
   +
ToolExecutionRecord           （未修改）
   ↓
Observer                      （可选注入；None = 旧行为）
   ↓
InMemoryCollector             （调用方 / composition root 持有）
   ↓
Metrics Read Model            （只读下游，未修改）
```

---

## 1. Purpose

```text
Step 13~17 建立了执行上下文 / Record / Observer / Collector / Metrics，
但 AIOrchestrator（链路 A：Question → Router → TOOL → Tool）在
Step 15/16 均未接入 —— 主链路 Tool 执行**不产生 Record**（0 事件）。

本阶段只回答一个问题：

    AIOrchestrator 的 TOOL 路径如何**正确装配**这套观测基础设施？

不做：新执行语义 / 新 Tool / 新 API / 持久化 / 聚合 / 告警 / 维度分析。
```

---

## 2. Architecture

```text
backend/app/services/ai_orchestrator_service.py    ← 本阶段唯一生产修改

    execute(question)
        ├── request_id = new_request_id()          （一次 execute 一个）
        ├── Router.route(...)                      （未修改）
        │      ↓ decision.route == TOOL
        ├── _run_tool(decision, question, request_id=...)
        │      ├── ToolArgumentExtractor.extract(...)        （未修改）
        │      ├── ToolExecutionContext(
        │      │       request_id = execute 的 request_id,
        │      │       round = 1,                （one execute = one Tool）
        │      │       project_id = execution.project_id,    （服务器端作用域）
        │      │       tool_call_id = None)      （非 Function Calling 链路）
        │      ├── execution.execute(tool_name, arguments=..., context=Context)
        │      └── AIOrchestrationResult（metadata 不含 request_id / context）
        └── _run_rag / _run_text_to_sql            （**不**创建 Context / 无 Record）
```

装配方式（构造参数；默认完全兼容）：

```python
collector = InMemoryToolExecutionCollector()          # 调用方 / composition root

orchestrator = AIOrchestratorService(
    router=...,
    tool_registry=registry,
    tool_execution_observer=collector,   # 只注入 Protocol（None = 旧行为）
)
# 或显式注入已装配 observer 的边界：
boundary = ToolExecutionService(registry=registry, observer=collector)
orchestrator = AIOrchestratorService(tool_execution_service=boundary)
```

```text
注入规则（避免观测静默失效）：
    * tool_execution_observer=None        → 构造默认边界时不传 observer
                                            （0 Record；逐字保持旧行为）；
    * observer 非 None + 未注入边界        → 构造默认边界时注入 observer；
    * observer 非 None + 显式注入边界      → observer 必须**就是**该边界的
                                            observer（同一对象）；
                                            否则 AIOrchestratorInputError；
    * observer 形状非法（无 on_execution） → AIOrchestratorInputError。
```

---

## 3. Context 语义（§四 / §五 / §六 / §十八）

```text
request_id    一次 execute() 一个（new_request_id()，唯一调用点位于
              execute() 顶层；不落库 / 不进 API response /
              不进 Tool arguments 或 LLM messages）
round         恒为 1（one question → one route → one Tool；
              无 round loop / 无 multi-step / 无 retry）
project_id    **唯一权威** = 执行边界的授权作用域（服务器端解析结果；
              默认边界来自 ProjectContext）；绝不来自 question /
              Tool arguments / Router 决策 / LLM
tool_call_id  恒为 None（本链路不是 OpenAI Function Calling round：
              Tool 名称由 Router 决策给出）—— **不伪造** call id
```

Record 映射（Context + ToolResult + Timing，全部来自既有 Step 14 契约）：

```text
record.request_id  = execute 的 request_id
record.round       = 1
record.tool_name   = decision.tool_name
record.project_id  = 边界作用域（None = 未绑定）
record.tool_call_id = None
record.success     = ToolResult.success（不推断）
```

---

## 4. Event 语义（Record 计数，全部锁定）

```text
Tool Success            → 1 Record（success=True）
Tool Failure（ToolResult(success=False)） → 1 Record（success=False；不 retry）
RAG 路径                → 0 Record（不创建 Context；不产生空 Record）
Text-to-SQL 路径        → 0 Record（SQL Executor 不是 Tool，不观测）
Capability denied       → 0 Record（拒绝发生在执行之前；
                          AIOrchestratorCapabilityError 语义不变 403）
observer=None           → 0 Record（默认兼容；ToolResult 不变）
Observer 抛异常          → ToolResult / AIOrchestrationResult **不变**；
                          不 retry / 不 fallback / 不重跑 Tool（Step 15 隔离）
两次 execute()          → 两个不同 request_id（各自 1 Record）
一次 execute()          → ≤ 1 Record（当前 contract）
```

---

## 5. Security

```text
Collector 只接收 ToolExecutionRecord（字段白名单 11 项；非 Record 被拒）；
Record 不含：question / Tool arguments / ToolResult.data / SQL /
    error message / traceback / API key / password / DATABASE_URL /
    connection string / Authorization header；
Context 不进 Tool arguments / 不进 LLM messages / 不进 result metadata
    （metadata 仍只有 decision_source / route_reason / tool_name / tool_success）；
Orchestrator 不 import metrics / collector / DB / API（静态锁定，C20.14/C20.15）；
observer 异常只被执行边界 warning 化（error_type + tool_name），
    **不**写 exception message / traceback。
```

---

## 6. 修改边界（本阶段）

```text
修改（生产，唯一）：backend/app/services/ai_orchestrator_service.py
    * 新增构造参数 tool_execution_observer（Protocol；None = 旧行为）
    * execute() 创建 request_id（唯一调用点）
    * _run_tool() 构造 ToolExecutionContext 并传给执行边界
    * 只读属性 tool_execution_observer
    * docstring / 注释更新（无新 import 副作用：仅 context / observer 类型）

测试适配（仅替身签名，断言语义未放宽）：
    tests/test_ai_orchestrator.py                     _CountingToolExecution 接受 context
    tests/test_tool_architecture_contract.py          _RecordingExecution 接受 context
    tests/test_tool_execution_service.py              duck-typed 替身接受 context
    tests/test_tool_chat_architecture_contract.py     C18.12 更新为 Step 18 语义
                                                      （不创建 Collector；observer 仅注入）

未修改：ToolExecutionService / ToolExecutionRecord / ToolExecutionObserver /
        InMemoryToolExecutionCollector / ToolExecutionMetricsService /
        ToolExecutionContext（Step 13 契约）/ ToolRegistry / Tool / Handler /
        ToolResult / Router / API / DB schema / ToolChatService
未引入：Collector 内部创建 / Metrics API / HTTP / DB / Redis / Kafka /
        持久化 / Dashboard / Prometheus / OpenTelemetry / Audit / Event Bus /
        Agent / MCP / LangGraph / Memory / Planning / Retry / Fallback
```

---

## 7. Test Summary

```text
tests/test_ai_orchestrator_tool_observability.py         39 passed
    Context（7）/ Collector（7）/ Request isolation（3）/ Metrics（4）/
    Observer failure isolation（5）/ Security（6）/ Static（4）/ 注入校验（3）

tests/test_tool_architecture_contract.py::TestC20*       15 passed
    C20.1 ~ C20.15（Context / request_id / round / call id / project_id /
    0 Record 三类 / 1 Record 两类 / observer 隔离 / 无 retry·fallback /
    不创建 Collector / 不接 API·DB / Metrics 只读下游）

既有回归：tests/test_ai_orchestrator.py / test_tool_architecture_contract.py /
    test_tool_execution_service.py / test_tool_chat_architecture_contract.py /
    test_tool_execution_context.py / test_tool_execution_observer.py /
    test_in_memory_tool_execution_collector.py            365 passed / 5 skipped

全量 no DB：3113 + 39 + 15 = 3167 passed / 338 skipped（0 failed）
（Step 17 基线 3113 / 338）

DB-gated：Environment DB unavailable
    psycopg.errors.ConnectionTimeout（localhost:5432；本机无监听）
    → 未修改代码绕过；既有 DB-gated 结论沿用 Step 11~17
    → 本阶段 DB writes = 0 / 0 Network / Fake LLM only
```

---

## 8. Limitations

```text
* 生产接线仍为 Deferred：API / 工厂**不**注入 observer（默认 0 Record）；
  Collector 生命周期由调用方（composition root）负责；
* 无 HTTP / Dashboard / Prometheus / OpenTelemetry / Audit / Event Bus；
* 无持久化（进程内 Records；重启即消失）；
* 一次 execute 至多 1 个 Tool 执行（无 multi-tool / multi-step / retry / fallback）；
* project_id 只作为授权作用域与 Record 字段，未按项目绑定 Engine / schema；
* 无 request_id → API response 透出（有意不暴露）；
* 环境限制：本机 PostgreSQL 不可用（localhost:5432），DB-gated 套件无法执行；
* lint 工具不可用（环境未安装 ruff / flake8，未新装工具）。
```
