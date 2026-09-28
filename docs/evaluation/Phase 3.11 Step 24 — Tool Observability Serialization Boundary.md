# Phase 3.11 Step 24 — Tool Observability Serialization Boundary

> 在 Step 23 架构审计（0 生产改动）之后，做一个**非常小的边界建设步骤**：
> 为 Tool Observability Read Model 建立稳定的 **Serialization Boundary**。
>
> **本阶段不开发 HTTP API。** 可序列化 ≠ 应当暴露。

---

## 1. Scope

```text
只解决：
    ToolExecutionSnapshot        → JSON-safe primitive dict
    ToolExecutionMetricsSnapshot → JSON-safe primitive dict

不解决：
    FastAPI endpoint / Pydantic Response Model / 数据库持久化 / Redis / Kafka /
    Prometheus / OpenTelemetry / Dashboard / 前端 / 鉴权 / 分页 / 过滤 DSL /
    排序 / 时间范围查询 / Audit 持久化 / 反序列化（dict → DTO）
```

---

## 2. Existing DTOs（复用，不修改）

```text
ToolExecutionSnapshot（Step 22，frozen，11 字段）
    request_id / round / tool_name / started_at(datetime) / finished_at(datetime)
    / duration_ms(float) / success(bool) / project_id / tool_call_id
    / error_code / error_type

ToolExecutionMetricsSnapshot（Step 17，frozen，8 字段）
    total_count / success_count / failure_count / success_rate / failure_rate
    / total_duration_ms / average_duration_ms / max_duration_ms

项目既有序列化规范（已复用，未新建框架）：
    datetime → .isoformat()
        backend/app/cli/knowledge.py::_iso
        backend/app/services/text_to_sql_*（generated_at）
    → 本阶段沿用 .isoformat()（保留 microseconds + tz offset）
    项目无 orjson / Pydantic Response Model 约定 → json.dumps 由**调用方**执行，
    本模块不 import json（不产生新依赖）。
```

---

## 3. Serialization Contract

```text
backend/app/services/tool_observability_serialization.py（新增）

    snapshot_to_dict(snapshot) -> dict[str, Any]     # 11 字段，显式逐字段映射
    metrics_to_dict(metrics)   -> dict[str, Any]     # 8 字段，显式逐字段映射

    * 显式字段映射（§八）：AST 断言"snapshot.<field>"逐字段出现；
      模块内无 vars / asdict / __dict__ / model_dump
    * 契约漂移防护：DTO 字段集合 ≠ 序列化契约 → ValueError（不静默漂移）
    * 值类型门禁：非 (str | int | float | bool | None) → TypeError
    * 纯函数：无 IO / 无 DB / 无 LLM / 无 Tool 执行 / 无当前时间 /
      无随机 / 无 UUID；deterministic
    * 不修改入参（Snapshot / Metrics / Record / Collector 均不变）
    * 无反序列化（§十三）：DTO → dict → json.dumps 单向
```

---

## 4. Snapshot 字段（严格保持 11 项）

```text
request_id  str            round        int
tool_name   str            started_at   str（ISO 8601，含 tz offset）
finished_at str（ISO 8601） duration_ms  float
success     bool           project_id   str | None
tool_call_id str | None    error_code   str | None
error_type  str | None

字段不新增 / 不删除 / 不重命名 / 不改语义
datetime → ISO 8601：保留 timezone（+00:00 / +08:00），
          不转本地时间、不转 epoch number；naive datetime → ValueError
```

---

## 5. Metrics 字段（严格保持 8 项）

```text
total_count int     success_count int     failure_count int
success_rate float | None         failure_rate float | None
total_duration_ms float           average_duration_ms float | None
max_duration_ms float | None

空数据集语义（§七）—— 序列化**不改变**它：
    success_rate = None（不是 0）
    failure_rate = None（不是 0）
    average_duration_ms = None
    max_duration_ms = None
    total_duration_ms = 0.0
```

---

## 6. JSON Compatibility

```text
json.dumps(snapshot_to_dict(snapshot))  ✅
json.dumps(metrics_to_dict(metrics))    ✅（含 null 值）
json.loads(json.dumps(x)) == x          ✅

无新依赖：模块不 import json（序列化模块只产出 primitive dict；
          json 由调用方使用）；项目无 orjson，未引入。
```

---

## 7. Security

```text
dict keys 级断言（不只查 JSON 文本）：
    无 arguments / sql / result / data / traceback / exception / prompt /
    response / messages / password / api_key / authorization / secret /
    token / dsn / database_url / connection / session / engine /
    registry / handler / record(s) / collector / metrics_service / llm

值级断言：
    全部为 JSON primitive（无嵌套对象引用：不引用 Snapshot / Record /
    Collector / Metrics Service 实例）
    json.dumps 文本不含 SELECT / postgresql:// / Bearer / sk- / Traceback
Metrics 不含 identifier：
    序列化结果不含 request_id / project_id / tool_name（维度聚合 Deferred）
```

---

## 8. Tests

```text
tests/test_tool_observability_serialization.py                       32 passed
    Snapshot     normal / 可选字段 None / datetime→ISO / tz 保留 /
                 非 UTC offset 保留 / 11 字段齐全无多余 / json.dumps /
                 deterministic / 源对象不变 / 非法输入 TypeError /
                 naive datetime → ValueError
    Metrics      normal / empty / None 语义 / 8 字段齐全无多余 /
                 json.dumps（含 null）/ deterministic / 不变 / TypeError
    Security     禁用键缺失 / 无嵌套对象引用 / 无 SQL·凭据 /
                 Metrics 不含 identifier
    Integration  QueryService snapshots → serialize /
                 QueryService metrics → serialize /
                 retention 窗口限制 / 被淘汰记录不复现 /
                 序列化不改 Collector
    Contract     显式映射 / 无 vars·asdict·__dict__ / 无 IO·DB·LLM·Tool /
                 公共 API 仅两个函数（无 deserialize）

tests/test_tool_chat_architecture_contract.py::TestC26*              11 passed
    C26.1 ~ C26.15
```

---

## 9. Limitations

```text
* HTTP API = NOT IMPLEMENTED（无任何端点；api/** 不 import 本模块）
* Persistence = NOT IMPLEMENTED（无 DB / Redis / Kafka / 文件）
* Dashboard = NOT IMPLEMENTED
* 无反序列化（dict → DTO）：当前无需求，避免过度设计
* 无分页 / 过滤 / 排序 / 时间范围（属未来 HTTP Read Layer）
* 无批量 API：列表序列化由调用方自己做（[snapshot_to_dict(s) for s in ...]）
* 无缓存 / 无 schema 版本字段（DTO 变更 → 契约漂移直接 ValueError 暴露）
* 未做 benchmark；序列化为 O(1) 字段映射
```

---

## 10. 修改边界

```text
新增（生产）：backend/app/services/tool_observability_serialization.py
新增（测试）：tests/test_tool_observability_serialization.py（32）
修改（测试）：tests/test_tool_chat_architecture_contract.py
             （+ C26 Serialization Boundary，11）
修改（文档）：docs/architecture.md §8.42 + 本文件
未修改：ToolExecutionService / ToolExecutionRecord / ToolExecutionContext /
        ToolExecutionObserver / InMemoryToolExecutionCollector /
        ToolExecutionMetricsService / ToolObservabilityQueryService /
        ToolExecutionSnapshot / ToolChatService / AIOrchestrator /
        AI Router / ToolRegistry / Tool Argument Extractor /
        ProjectOrchestratorFactory / Composition Root / api/**
        （未发现 serialization bug → 按 §二十一无需修改既有 DTO）
```
