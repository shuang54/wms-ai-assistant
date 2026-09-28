# Phase 3.11 Step 25 — Tool Observability HTTP Read Boundary

> 基于 Step 20（Retention）/ 21（Query Service）/ 22（Snapshot）/
> 23（Audit）/ 24（Serialization），新增**最小只读 HTTP API**。
>
> Tool Observability 第一次暴露给 HTTP 客户端 —— **但只做 Read API**。

---

## 1. Scope

```text
只做：
    GET /api/observability/tools
    GET /api/observability/tools/metrics
    + API Response DTO + API 测试 + 文档

不做：
    Database Persistence / Redis / Kafka / Prometheus / OpenTelemetry /
    Dashboard / Frontend / WebSocket / SSE / 鉴权重设计 / Audit 持久化 /
    Event Bus / Background worker / Celery / Agent / MCP / LangGraph /
    Memory / Planning
```

---

## 2. Endpoints

```text
GET /api/observability/tools
    → {"records": [ ToolExecutionSnapshot × N（11 字段，ISO 8601 时间戳）]}

GET /api/observability/tools/metrics
    → ToolExecutionMetricsSnapshot 的 8 个字段（扁平；无维度）
```

```text
HTTP GET
    ↓
API Layer（仅 DTO 转换 / 异常映射）
    ↓
ToolObservabilityQueryService（Step 21 只读查询边界）
    ↓
Snapshot / Metrics Read Model
    ↓
Serialization Boundary（Step 24）
    ↓
JSON
```

---

## 3. Architecture

```text
Tool Execution
      ↓
ToolExecutionRecord
      ↓
Observer
      ↓
Collector（Application lifetime；唯一创建点 = api/orchestrator_chat.py）
      ↓
QueryService（只读）
      ↓
Snapshot / Metrics
      ↓
Serialization
      ↓
FastAPI Read API
      ↓
JSON
```

---

## 4. QueryService Boundary（§七 —— 本阶段最重要约束）

```text
API  →  QueryService  →  Read Model        ✅
API  ↛  InMemoryToolExecutionCollector     ❌（import 级断言）
API  ↛  ToolExecutionRecord                ❌
API  ↛  ToolExecutionService               ❌
API  ↛  ToolRegistry / Tool Handler        ❌
API  ↛  AIOrchestrator                     ❌

API 模块的 import 只有：
    backend.app.api.orchestrator_chat      （Composition Root accessor）
    backend.app.services.tool_observability_serialization
    fastapi / pydantic / logging / typing

API 不调用 query.records()（只读 Read Model：snapshots() / metrics()）
```

---

## 5. Serialization

```text
只用 Step 24：
    snapshot_to_dict()      → records
    metrics_to_dict()       → metrics

API 不自己 datetime.isoformat() / 不复制字段映射 /
不用 vars() / asdict() / __dict__（AST 级断言）

空数据语义不变：
    success_rate / failure_rate / average_duration_ms / max_duration_ms = null
    total_duration_ms = 0.0
    （null **不**被转换成 0）
```

---

## 6. Composition Root

```text
api/orchestrator_chat.py 新增（最小装配 accessor）：

    def get_tool_observability_query_service() -> ToolObservabilityQueryService:
        return ToolObservabilityQueryService(_TOOL_EXECUTION_COLLECTOR)

* 不创建第二个 Collector：全应用继续共享 Step 19/20 的**同一个**
  Application 级 Collector；
* api/tool_observability.py 只依赖该 accessor（不接触 Collector）；
* QueryService 只读（无 clear / append / evict）。
```

---

## 7. Security

```text
Records 响应：仅 Snapshot 的 11 个字段（无 arguments / ToolResult.data /
              SQL / prompt / LLM response / 凭据 / 连接 / traceback）
Metrics 响应：仅 8 个统计字段（无 request_id / project_id / tool_name）
错误响应：    500 "Tool 观测数据不可用"（不暴露 traceback / 模块路径 /
              SQL / 数据库信息）
测试级断言：  SQL / password / api_key / Authorization / Bearer /
              postgresql:// / Traceback / MAT-001 / result data marker
              均不出现在任何响应文本中
```

---

## 8. Tests

```text
tests/test_tool_observability_api.py                              31 passed
    Records  200 / empty / 单条成功 / 单条失败 / 多条顺序 /
             retention（max_records=2）/ 被淘汰不复现 /
             datetime ISO 字符串 / None→null / 11 字段白名单 /
             不暴露 Collector / 不暴露 Record 内部
    Metrics  200 / empty（全 null）/ 成功失败统计 / None ≠ 0 /
             8 字段白名单 / 不含 identifier
    静态     只用 QueryService accessor / 不 import Collector·Record·
             执行边界·Registry·Handler·DB·LLM / 只用序列化边界 /
             不计算指标（无 Div / sum / max）
    Composition  同一 Application Collector /
             POST /api/ai/chat → Tool 执行 → GET 可见（真实 TestClient）/
             metrics 反映执行 / RAG → 0 Tool Record
    Security 无 SQL / 无凭据 / 无 traceback / 无 arguments / 无 result data

tests/test_tool_chat_architecture_contract.py::TestC27*           11 passed
    C27.1 ~ C27.20

既有契约同步（Step 25 有意改变"无 HTTP API"这一现状）：
    C25.13 → 改为端点**白名单**断言（仅两个只读端点）+
             其它 api 模块不得直连 Observability
    C26.15 → 序列化边界消费方仅允许 api/tool_observability.py
    （两者均为"当前状态"契约；Step 23/24 的 evaluation 历史记录未改写）
```

---

## 9. Limitations

```text
* Persistence = NOT IMPLEMENTED（内存；进程重启即丢失）
* Dashboard = NOT IMPLEMENTED
* Prometheus / OpenTelemetry = NOT IMPLEMENTED
* Redis / Kafka = NOT IMPLEMENTED
* 无 query parameter（第一版不做 Query DSL；无 project_id / tool_name /
  request_id / limit / offset / sort / 时间范围）
* 无排序 / 过滤 / 分页 / 聚合（全部沿用 QueryService 语义）
* 无维度 Metrics（by_tool / by_project / by_request 均未实现）
* 多进程：每个 process 独立内存数据（无共享 / 无聚合）
* 无认证机制（沿用项目现有 API 机制；本阶段未新增）
* 无缓存 / 无 ETag / 无条件请求
* 未做 benchmark / 负载测试
```

---

## 10. 修改边界

```text
新增：backend/app/api/tool_observability.py（API）
      tests/test_tool_observability_api.py（31）
      docs/evaluation/Phase 3.11 Step 25 — …md
修改：backend/app/api/orchestrator_chat.py（+ QueryService accessor，最小装配）
      backend/app/main.py（+ include_router，最小接线）
      tests/test_tool_chat_architecture_contract.py（+ C27；C25.13/C26.15 同步）
      tests/test_tool_observability_architecture_audit.py（C25.13 同步）
      docs/architecture.md（§8.43）
      docs/api.md（§2.5 / §2.6）
未修改：AIOrchestrator / ToolExecutionService / Collector / QueryService /
        Snapshot / Metrics / Serialization / ToolRegistry / Tool Handler /
        AI Router / ToolChatService / DB schema / 既有端点行为
```
