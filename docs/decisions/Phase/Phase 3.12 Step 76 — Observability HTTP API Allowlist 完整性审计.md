# Phase 3.12 Step 76 — Observability HTTP API Allowlist 完整性审计

继续 `Phase 3.12`。

当前状态：

```text
Step 75 READY / STOP
```

Step 75 已修复：

```text
tests/test_tool_observability_architecture_audit.py
C25-13 allowlist drift
```

当前目标：

> **确认整个 Observability HTTP API 白名单机制与当前实际 backend API 路由是否完整一致。**

本阶段只做：

```text
Audit
    ↓
发现 drift
    ↓
分类
    ↓
报告
```

**默认不修改任何代码。**

---

# 一、严格范围

本阶段允许修改：

```text
tests/
```

但：

### 默认只允许新增/修改 Audit 测试。

如果发现新的 allowlist drift：

**先报告，不要直接修复。**

除非明确确认只是测试白名单遗漏，并且下一步单独授权修复。

---

# 二、禁止

本阶段禁止修改：

```text
backend/
docs/architecture.md
docs/evaluation/
DB schema
migration
API contract
Trace
Timeline
Outcome
Tool Framework
RAG
T2SQL
LLM
```

禁止：

```text
Unified Timeline
event_id
sequence
span_id
parent_event_id
pagination
Conversation
Memory
Agent
MCP
OpenTelemetry
```

禁止：

```text
DB write
DeepSeek
network request
```

---

# 三、审计目标

当前已知 Observability HTTP API 白名单至少包含：

```text
/api/observability/tools
/api/observability/tools/metrics
/api/observability/tools/history
/api/observability/tools/metrics/persistent
/api/observability/assistant-trace/{assistant_request_id}
/api/observability/assistant-timeline/{assistant_request_id}
```

不要直接相信这个列表。

必须从真实代码重新发现：

```text
backend/app/api/*.py
```

中的 HTTP route。

---

# 四、Route Discovery

使用 AST 静态扫描：

```text
backend/app/api/*.py
```

识别：

```python
@app.get(...)
@app.post(...)
@app.put(...)
@app.delete(...)
@app.patch(...)
@router.get(...)
@router.post(...)
...
```

重点收集：

```text
path
method
source_file
```

不要使用简单全文字符串扫描。

---

# 五、Observability Route 分类

发现所有 API 后，仅筛选 path 中包含：

```text
observability
metrics
snapshot
```

的 HTTP route。

然后分类：

### A. Tool Observability

例如：

```text
/api/observability/tools
/api/observability/tools/metrics
/api/observability/tools/history
/api/observability/tools/metrics/persistent
```

### B. Assistant Trace

```text
/api/observability/assistant-trace/{assistant_request_id}
```

### C. Assistant Timeline

```text
/api/observability/assistant-timeline/{assistant_request_id}
```

### D. Other / Unexpected

如果发现：

```text
observability/*
```

但不属于已有冻结类别：

记录：

```text
UNEXPECTED_OBSERVABILITY_ROUTE
```

**不要自动把它加入 allowlist。**

---

# 六、Method 也必须审计

不能只检查 path。

输出：

```text
METHOD + PATH
```

例如：

```text
GET /api/observability/tools
GET /api/observability/assistant-trace/{assistant_request_id}
GET /api/observability/assistant-timeline/{assistant_request_id}
```

如果出现：

```text
POST
PUT
PATCH
DELETE
```

必须单独报告。

原因：

当前 Observability API 原则上是：

```text
read-only
```

本阶段不要修改生产代码，只报告发现。

---

# 七、Allowlist Completeness

建立：

```text
discovered observability routes
        VS
frozen allowlist
```

检查两个方向：

### Case 1

```text
discovered route
+
not in allowlist
```

→

```text
ALLOWLIST_MISSING
```

---

### Case 2

```text
allowlist route
+
not discovered
```

→

```text
STALE_ALLOWLIST_ENTRY
```

---

### Case 3

```text
same path
different method
```

必须识别。

例如：

```text
GET /x
POST /x
```

不能认为是同一个 route。

---

# 八、当前已知 Baseline

以 Step 75 后的状态为基础：

```text
/api/observability/tools
/api/observability/tools/metrics
/api/observability/tools/history
/api/observability/tools/metrics/persistent
/api/observability/assistant-trace/{assistant_request_id}
/api/observability/assistant-timeline/{assistant_request_id}
```

预期：

```text
ALLOWLIST_MISSING = 0
STALE_ALLOWLIST_ENTRY = 0
```

如果不是：

**不要修改。**

直接报告：

```text
发现新的 allowlist drift
```

---

# 九、Duplicate Route

检查是否存在：

```text
same HTTP method
+
same normalized path
```

被多个 API module 注册。

如果发现：

```text
DUPLICATE_OBSERVABILITY_ROUTE
```

只报告。

不要修改生产代码。

---

# 十、Path Normalization

必须正确处理：

```text
/api/observability/foo
/api/observability/foo/
/api/observability/foo/{id}
```

不要使用简单：

```python
path in set
```

造成动态 path 误判。

但是：

**不要重新设计复杂 router matcher。**

只需要使用当前项目 route decorator 中的原始 path 进行一致性比较。

---

# 十一、Security Audit

继续使用 AST。

确认 Observability route module 不直接出现：

```text
api_key
password
authorization
database_url
```

并且：

```text
GET
```

之外如果出现写方法，单独报告。

不要扫描 docstring 作为 executable violation。

---

# 十二、Production Code Integrity

运行：

```powershell
git diff -- backend
```

预期：

```text
empty
```

如果不是：

**立即 STOP 并报告。**

本阶段不应该产生 backend 修改。

---

# 十三、测试设计

新增：

```text
tests/test_observability_http_allowlist_audit.py
```

至少覆盖：

1. route discovery
2. observability route filtering
3. method + path identity
4. allowlist missing detection
5. stale allowlist detection
6. duplicate route detection
7. dynamic path handling
8. security path scan
9. backend diff integrity
10. expected six-route baseline

不要机械堆测试数量。

建议：

```text
10～15 tests
```

---

# 十四、不要依赖运行中的 DB

本阶段：

```text
RUN_DB_TESTS = 0
```

不需要：

```text
PostgreSQL
SQLAlchemy
psycopg
Redis
```

这是：

```text
static architecture audit
```

---

# 十五、不要调用真实 LLM

必须：

```text
DeepSeek calls = 0
Network = 0
```

---

# 十六、执行测试

先：

```powershell
python -m pytest -q tests/test_observability_http_allowlist_audit.py
```

然后：

```powershell
python -m pytest -q tests/test_tool_observability_architecture_audit.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

最后：

```powershell
git diff -- backend
```

不要跑：

```text
RUN_DB_TESTS=1
```

不要跑全量 pytest。

---

# 十七、成功条件

必须达到：

```text
route discovery = PASS
allowlist completeness = PASS
method/path identity = PASS
duplicate detection = PASS
security audit = PASS
six-route baseline = PASS
backend diff = EMPTY
DeepSeek = 0
Network = 0
DB writes = 0
compileall = PASS
```

---

# 十八、失败处理

如果发现：

```text
ALLOWLIST_MISSING
STALE_ALLOWLIST_ENTRY
DUPLICATE_OBSERVABILITY_ROUTE
unexpected write route
```

不要修改。

最终报告明确：

```text
问题：
位置：
影响：
是否修改：
```

其中：

```text
是否修改：否
```

---

# 十九、最终报告

完成后只报告：

```text
Phase 3.12 Step 76 完成报告

1. Route Discovery
2. Observability Routes
3. Allowlist Completeness
4. Method / Path Identity
5. Duplicate Routes
6. Security Audit
7. Six-route Baseline
8. Tests
9. Compileall
10. Backend Diff
11. DB / Network / DeepSeek
12. 新发现问题
13. 当前限制

Phase 3.12 Step 76 READY
Phase 3.12 Step 76 STOP
```

**完成后立即停止。**

不要进入：

```text
Unified Timeline
event_id
sequence
span_id
pagination
Conversation
Memory
Agent
MCP
OpenTelemetry
```

等待下一步指令。
