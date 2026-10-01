# Phase 3.12 Step 77 — Observability HTTP Allowlist Regression Freeze

继续 `Phase 3.12`。

当前状态：

```text
Phase 3.12 Step 76 READY / STOP
```

Step 76 已完成：

```text
真实 API Route Discovery
        ↓
Observability Route Filtering
        ↓
Allowlist 双向比对
        ↓
Method / Path Identity
        ↓
Duplicate Detection
        ↓
Security Audit
        ↓
6 条 Observability Route 完全一致
```

当前目标：

> **把 Step 76 已验证的 Observability HTTP API 路由集合正式冻结为一个独立的 Regression Contract。**

本阶段仍然只做：

```text
Regression Contract
+
Offline Tests
```

不修改 Production API。

---

# 一、冻结的 Route Contract

当前正式冻结：

```text
GET /api/observability/tools

GET /api/observability/tools/metrics

GET /api/observability/tools/history

GET /api/observability/tools/metrics/persistent

GET /api/observability/assistant-trace/{assistant_request_id}

GET /api/observability/assistant-timeline/{assistant_request_id}
```

要求：

```text
exactly 6 routes
```

不允许：

```text
新增
删除
修改 method
修改 path
```

除非未来单独创建新的 Phase / Step 明确授权。

---

# 二、严格范围

允许修改：

```text
tests/
docs/evaluation/
```

优先：

```text
tests/test_observability_http_allowlist_audit.py
```

以及必要的：

```text
docs/evaluation/phase-3.12-observability-http-allowlist-regression.md
```

如果现有 Audit 测试已经足够表达 Contract：

**优先最小修改，不要重复造第二套扫描器。**

---

# 三、禁止

本阶段禁止修改：

```text
backend/
DB schema
migration
API implementation
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
Network
```

---

# 四、核心设计原则

不要再创建第二套：

```text
Route Discovery Engine
```

优先复用 Step 76 已有的：

```text
AST route discovery
method + path identity
allowlist comparison
```

Regression 的职责只是：

> 锁定当前已经验证过的结果。

不要把测试变成新的 Router Framework。

---

# 五、Contract 数据结构

在测试侧建立一个明确的 frozen expected set：

```python
EXPECTED_OBSERVABILITY_ROUTES = {
    ("GET", "/observability/tools"),
    ("GET", "/observability/tools/metrics"),
    ("GET", "/observability/tools/history"),
    ("GET", "/observability/tools/metrics/persistent"),
    ("GET", "/observability/assistant-trace/{assistant_request_id}"),
    ("GET", "/observability/assistant-timeline/{assistant_request_id}"),
}
```

注意：

这里比较的是：

```text
router decorator 原始 path
```

不是：

```text
/api + path
```

因为：

```text
/api
```

来自 `main.py` 的 router prefix。

---

# 六、Contract Assertions

至少锁定：

### 1. Count

```text
len(routes) == 6
```

---

### 2. Exact equality

```text
actual == expected
```

不能：

```text
subset
```

不能：

```text
expected <= actual
```

必须：

```text
exact set equality
```

---

### 3. Method

全部：

```text
GET
```

---

### 4. Trace

必须存在：

```text
GET /observability/assistant-trace/{assistant_request_id}
```

---

### 5. Timeline

必须存在：

```text
GET /observability/assistant-timeline/{assistant_request_id}
```

---

### 6. No wildcard

不要使用：

```text
/observability/*
```

作为断言。

必须逐条列出。

---

# 七、与 Allowlist 双向一致

继续验证：

```text
discovered routes
        ==
frozen expected routes
        ==
C25 allowlist
```

形成：

```text
               ┌───────────────┐
               │ Actual Routes │
               └───────┬───────┘
                       │
                       ▼
              ┌─────────────────┐
              │ Frozen Contract │
              └───────┬─────────┘
                       │
                       ▼
                C25 Allowlist
```

要求：

```text
Actual == Frozen == Allowlist
```

---

# 八、禁止重复 Allowlist

如果发现：

```text
Step 76 test
```

已经定义了：

```text
allowlist
```

不要再复制一份完整路径列表到多个测试文件。

优先让：

```text
Frozen Contract
```

成为唯一测试事实来源。

其他测试引用它。

如果当前测试结构不适合共享：

可以保留最小重复，但必须在测试中明确说明：

```text
This set mirrors the frozen contract.
```

不要产生多个可能互相漂移的 allowlist。

---

# 九、Regression Test

新增或调整：

```text
tests/test_observability_http_allowlist_regression.py
```

至少包含：

```text
test_exact_observability_route_count
test_exact_observability_route_set
test_all_observability_routes_are_get
test_trace_route_is_frozen
test_timeline_route_is_frozen
test_no_unexpected_observability_route
test_allowlist_matches_frozen_contract
```

建议：

```text
7～10 tests
```

不要为了数量重复测试。

---

# 十、Security

继续验证：

```text
/api_key
/password
/authorization
/database_url
```

等敏感标识符不能出现在：

```text
Observability API route
```

但不要重新实现完整 Step 76 Security Scanner。

复用已有测试。

---

# 十一、No DB / Network

本阶段必须：

```text
RUN_DB_TESTS = 0
DB = 0
Network = 0
DeepSeek = 0
```

这是：

```text
static regression
```

---

# 十二、Documentation

新增：

```text
docs/evaluation/phase-3.12-observability-http-allowlist-regression.md
```

只记录：

## Frozen Contract

```text
6 GET routes
```

## Purpose

```text
防止新增 Observability API 后遗漏 C25 allowlist。
```

## Scope

```text
HTTP route path/method only
```

## Non-goals

```text
No Timeline redesign
No event identity
No pagination
No auth redesign
No persistence change
```

保持简短。

---

# 十三、测试执行

先：

```powershell
python -m pytest -q tests/test_observability_http_allowlist_regression.py
```

然后：

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

预期：

```text
empty
```

不要执行：

```text
RUN_DB_TESTS=1
```

不要运行全量 pytest。

---

# 十四、成功条件

必须：

```text
Frozen Contract = PASS

Actual Routes = 6

Expected Routes = 6

Actual == Expected = PASS

Actual == C25 Allowlist = PASS

All Methods = GET

Trace = PASS

Timeline = PASS

Unexpected = 0

DB = 0

Network = 0

DeepSeek = 0

Backend Diff = EMPTY

Compileall = PASS
```

---

# 十五、失败处理

如果发现：

```text
Actual != Frozen
Frozen != Allowlist
Unexpected route
Missing route
Wrong method
```

不要修改 Production。

报告：

```text
问题：
位置：
Actual：
Expected：
影响：
是否修改：否
```

---

# 十六、最终报告

完成后只报告：

```text
Phase 3.12 Step 77 完成报告

1. Frozen Contract
2. Actual Route Count
3. Exact Route Equality
4. Method Contract
5. Trace / Timeline Contract
6. Allowlist Consistency
7. Security
8. Tests
9. Compileall
10. Backend Diff
11. DB / Network / DeepSeek
12. 新发现问题
13. 当前限制

Phase 3.12 Step 77 READY
Phase 3.12 Step 77 STOP
```

完成后立即停止。

不要进入：

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

等待下一步指令。
