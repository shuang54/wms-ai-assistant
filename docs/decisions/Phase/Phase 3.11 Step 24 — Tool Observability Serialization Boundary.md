你现在开始执行：

# Phase 3.11 Step 24 — Tool Observability Serialization Boundary

## 一、阶段目标

在 Step 23 架构审计完成后，继续做一个**非常小的边界建设步骤**：

> 为 Tool Observability Read Model 建立稳定的 Serialization Boundary。

本阶段只解决：

```text
ToolExecutionSnapshot
        ↓
Serializable Representation
        ↓
JSON-safe primitive structure
```

以及：

```text
ToolExecutionMetricsSnapshot
        ↓
Serializable Representation
        ↓
JSON-safe primitive structure
```

**本阶段不开发 HTTP API。**

不要接：

```text
FastAPI endpoint
Database
Redis
Kafka
Prometheus
OpenTelemetry
Dashboard
Frontend
```

---

# 二、严格禁止修改

不要修改：

```text
ToolExecutionService
ToolExecutionRecord
ToolExecutionContext
ToolExecutionObserver
InMemoryToolExecutionCollector
ToolExecutionMetricsService
ToolObservabilityQueryService
AIOrchestrator
AI Router
ToolRegistry
ToolChatService
Tool Argument Extractor
ProjectOrchestratorFactory
Composition Root
```

除非发现明确的 serialization bug。

不要修改：

```text
3.11 Step 1~23 architecture contract
```

不要修改：

```text
existing ToolExecutionSnapshot field semantics
existing ToolExecutionMetricsSnapshot field semantics
```

---

# 三、Step 1：先阅读现有实现

先阅读：

```text
backend/app/services/tool_observability_snapshot.py
backend/app/services/tool_observability_query_service.py
backend/app/services/tool_execution_metrics_service.py
backend/app/services/tool_execution_record.py
backend/app/services/in_memory_tool_execution_collector.py
```

以及相关测试：

```text
tests/test_tool_observability_snapshot.py
tests/test_tool_observability_query_service.py
tests/test_tool_execution_metrics_service.py
tests/test_tool_execution_record.py
```

同时检查：

```text
docs/architecture.md
docs/evaluation/
```

重点确认：

1. Snapshot 当前字段。
2. Metrics Snapshot 当前字段。
3. datetime 当前类型。
4. 是否已经存在类似 DTO serialization helper。
5. 是否已经存在 Pydantic serialization utility。
6. 是否存在 `model_dump()` / `asdict()` / `vars()` 等既有规范。

**优先复用已有项目规范。**

不要重新设计一套序列化框架。

---

# 四、Serialization Boundary

新增一个非常小的纯转换层。

推荐：

```text
backend/app/services/tool_observability_serialization.py
```

如果现有项目已有更合适的位置，则遵循现有结构。

建议提供：

```python
snapshot_to_dict(snapshot)
metrics_to_dict(metrics)
```

或者等价的：

```text
serialize_snapshot()
serialize_metrics()
```

具体命名根据项目现有风格决定。

---

# 五、Snapshot Serialization

当前：

```text
ToolExecutionSnapshot
```

必须能够转换成：

```python
dict[str, primitive]
```

允许：

```text
str
int
float
bool
None
```

如果 datetime 当前字段是：

```text
datetime
```

则转换为：

```text
ISO 8601 string
```

例如：

```text
2026-09-26T10:20:30.123456+00:00
```

不要丢失：

```text
timezone
```

不要转成本地时间。

不要转 timestamp number，除非项目现有规范已经这么做。

---

# 六、Snapshot 字段

必须严格保持当前 Snapshot 的 11 个字段：

```text
request_id
round
tool_name
started_at
finished_at
duration_ms
success
project_id
tool_call_id
error_code
error_type
```

Serialization：

```text
字段不能增加
字段不能删除
字段不能重命名
字段不能改变语义
```

---

# 七、Metrics Serialization

当前：

```text
ToolExecutionMetricsSnapshot
```

也必须可以转换成 JSON-safe dict。

严格保持现有字段。

例如：

```text
total_count
success_count
failure_count
success_rate
failure_rate
total_duration_ms
average_duration_ms
max_duration_ms
```

如果某些值当前是：

```text
None
```

必须继续保持：

```text
None
```

不能转换成：

```text
0
```

尤其注意：

```text
empty metrics
```

当前语义：

```text
success_rate = None
failure_rate = None
average_duration_ms = None
max_duration_ms = None
total_duration_ms = 0.0
```

Serialization 不得改变这个语义。

---

# 八、不要使用通用自动序列化

本阶段不要直接使用：

```python
vars()
asdict()
obj.__dict__
```

作为核心 serialization contract。

原因：

未来 DTO 增加内部字段时，自动 serialization 可能意外泄漏内部状态。

必须采用：

```text
Explicit Field Mapping
```

例如：

```text
snapshot.request_id
snapshot.round
...
```

逐字段转换。

这和 Step 22 Snapshot 的 explicit mapping 原则保持一致。

---

# 九、Security Boundary

Serialization 后的数据：

**不能出现：**

```text
Tool Registry
Tool Handler
Tool arguments
SQL
Database connection
SQLAlchemy Session
LLM client
Prompt
LLM response
API Key
Password
Authorization Header
Exception traceback
```

如果当前 DTO 本身没有这些字段，则通过测试锁定：

```text
serialized output does not contain forbidden fields
```

不要仅检查当前 JSON 文本。

应该检查：

```text
dict keys
```

---

# 十、JSON Compatibility

增加测试：

```python
json.dumps(serialized_snapshot)
```

必须成功。

以及：

```python
json.dumps(serialized_metrics)
```

必须成功。

如果项目当前使用：

```text
orjson
```

则优先遵循项目现有 serializer。

不要为了本阶段新增依赖。

---

# 十一、Determinism

同一个：

```text
Snapshot
```

连续：

```text
serialize(snapshot)
serialize(snapshot)
```

结果必须完全一致。

Metrics 同理。

Serialization 不允许：

```text
当前时间
随机数
UUID
网络
数据库
LLM
```

---

# 十二、Immutability

Serialization：

```text
serialize(snapshot)
```

不能修改原对象。

验证：

```text
snapshot before == snapshot after
```

Collector 中的原始 Record 也不能被修改。

Query Service 返回的 Snapshot 也不能被修改。

---

# 十三、Round-trip

如果项目现有设计没有反序列化需求：

**不要新增 deserialize。**

本阶段只要求：

```text
DTO
 ↓
dict
 ↓
json.dumps()
```

不需要：

```text
JSON
 ↓
DTO
```

避免过度设计。

---

# 十四、测试

新增：

```text
tests/test_tool_observability_serialization.py
```

建议覆盖：

### Snapshot

1. normal snapshot
2. project_id=None
3. tool_call_id=None
4. error_code=None
5. error_type=None
6. datetime → ISO string
7. timezone preserved
8. all 11 fields present
9. no extra fields
10. json.dumps success
11. deterministic
12. source immutable

### Metrics

13. normal metrics
14. empty metrics
15. None semantics preserved
16. all fields present
17. no extra fields
18. json.dumps success
19. deterministic
20. source immutable

### Security

21. forbidden keys absent
22. no nested object references
23. no Record / Collector / Metrics object leakage
24. no SQL / secret / credentials

### Integration

25. QueryService snapshot → serialize
26. QueryService metrics → serialize
27. Collector retention → serialized output only contains retained records
28. evicted record cannot reappear through serialization

目标：

```text
20~30 tests
```

不要为了数量重复测试。

---

# 十五、Architecture Contract

新增：

```text
C26 — Serialization Boundary
```

建议锁定：

```text
C26.1 Snapshot → JSON-safe dict
C26.2 Metrics → JSON-safe dict
C26.3 Explicit field mapping
C26.4 No automatic __dict__/vars/asdict
C26.5 datetime → ISO 8601
C26.6 timezone preserved
C26.7 None semantics preserved
C26.8 no extra fields
C26.9 no forbidden fields
C26.10 no mutation
C26.11 deterministic
C26.12 no DB
C26.13 no LLM
C26.14 no Tool execution
C26.15 no HTTP API
```

如果现有：

```text
tests/test_tool_architecture_contract.py
tests/test_tool_chat_architecture_contract.py
```

更适合放 Contract，则继续复用，不要创建第三套 architecture contract 文件。

---

# 十六、Architecture 文档

更新：

```text
docs/architecture.md
```

新增：

```text
§8.42 Tool Observability Serialization Boundary
```

明确：

```text
Execution
    ↓
Record
    ↓
Observer
    ↓
Collector
    ↓
Query Service
    ├── Snapshot Read Model
    │       ↓
    │   Serialization Boundary
    │
    └── Metrics Read Model
            ↓
        Serialization Boundary
```

并明确：

```text
Serialization
    ↓
JSON-safe primitive structure
```

但：

```text
Serialization
X
↓
Execution
```

Serialization 永远不能触发：

```text
Tool
DB
LLM
Network
```

---

# 十七、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.11 Step 24 — Tool Observability Serialization Boundary.md
```

记录：

1. Scope
2. Existing DTOs
3. Serialization contract
4. Snapshot fields
5. Metrics fields
6. JSON compatibility
7. Security
8. Tests
9. Limitations

明确：

```text
HTTP API = NOT IMPLEMENTED
Persistence = NOT IMPLEMENTED
Dashboard = NOT IMPLEMENTED
```

---

# 十八、不要做这些

本阶段禁止：

```text
❌ FastAPI endpoint
❌ /api/observability
❌ /api/metrics
❌ Pydantic Response Model（除非项目已有 DTO conversion convention 且只是复用）
❌ Database persistence
❌ Redis
❌ Kafka
❌ Prometheus
❌ OpenTelemetry
❌ Dashboard
❌ Frontend
❌ Authentication
❌ Authorization redesign
❌ Pagination
❌ Filtering DSL
❌ Sorting
❌ Time range query
❌ Audit persistence
```

尤其：

**不要因为已经可以 JSON 序列化，就顺手开发 HTTP API。**

---

# 十九、测试

先执行：

```powershell
python -m pytest -q tests/test_tool_observability_serialization.py
```

然后：

```powershell
python -m pytest -q tests/test_tool_observability_snapshot.py tests/test_tool_observability_query_service.py tests/test_tool_execution_metrics_service.py
```

然后：

```powershell
python -m pytest -q
```

如果需要 DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

注意 Windows PowerShell 不要使用：

```text
RUN_DB_TESTS=1 pytest
```

---

# 二十、编译

执行：

```powershell
python -m compileall -q backend tests
```

要求：

```text
0 errors
```

如果项目存在 lint：

使用现有 lint。

不要为了本阶段安装新的 lint 工具。

---

# 二十一、失败处理

如果发现：

```text
Snapshot 本身字段设计不适合 serialization
Metrics DTO 存在泄漏
datetime 语义不明确
已有 serializer 与当前 DTO 冲突
```

先停止。

不要修改：

```text
Snapshot DTO
Metrics Service
Collector
Query Service
```

除非确认是本阶段必须修复的真实 bug。

如果需要修改生产逻辑：

最终报告明确：

```text
发现问题：
根因：
修改：
为什么属于 Serialization Boundary 必要修改：
```

---

# 二十二、最终汇报格式

完成后只汇报：

```text
Phase 3.11 Step 24 COMPLETE

1. 新增文件
2. 修改文件
3. Serialization Contract
4. Snapshot
5. Metrics
6. JSON compatibility
7. Security
8. C26 Contract
9. Tests
10. compile / lint
11. DB writes
12. Network / LLM
13. Production code changes
14. 当前限制
```

最后输出：

```text
Architecture:

ToolExecutionRecord
        ↓
InMemoryCollector
        ↓
QueryService
    ┌───┴──────────┐
    ↓              ↓
Snapshot        Metrics
    ↓              ↓
Serialization  Serialization
    ↓              ↓
JSON-safe      JSON-safe
```

最后：

**立即停止。**

不要进入 Step 25。

不要开发 HTTP API。

不要开发 Database Persistence。

不要开发 Dashboard。

不要接 Redis / Kafka / Prometheus / OpenTelemetry。

不要开发 Agent / MCP / LangGraph / Memory / Planning。
