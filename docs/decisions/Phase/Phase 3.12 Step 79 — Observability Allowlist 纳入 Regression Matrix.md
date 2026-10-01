# Phase 3.12 Step 79 — Observability Allowlist 纳入 Regression Matrix

继续 `Phase 3.12`。

当前状态：

```text
Phase 3.12 Step 78 READY / STOP
```

Step 78 已确认：

```text
Frozen Contract
    ↓
唯一 Route Scanner
    ↓
唯一 Allowlist Source
    ↓
C25 Consumer
    ↓
Regression Consumer
```

当前目标：

> **把 Step 76～78 的 Observability HTTP Allowlist 契约正式纳入 Phase 3.12 Regression Matrix。**

本阶段仍然只做：

```text
Regression Matrix
+
Offline Test Registration
```

**不要修改 Production Code。**

---

# 一、严格范围

允许修改：

```text
tests/
docs/evaluation/
```

优先检查并复用：

```text
tests/test_assistant_trace_timeline_regression.py
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

以及现有 Phase 3.12 regression collector。

如果项目已有统一 regression matrix：

**必须复用现有结构。**

不要新建第二套 regression framework。

---

# 二、禁止

禁止修改：

```text
backend/
docs/architecture.md
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
DB
Network
DeepSeek
```

---

# 三、先阅读

先阅读真实现有 Regression Matrix：

```text
tests/test_assistant_trace_timeline_regression.py
```

以及：

```text
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

如果还有统一的：

```text
regression collector
matrix
manifest
category registry
```

一并阅读。

不要假设结构。

---

# 四、当前需要纳入的测试类别

将以下能力视为一个新的 Regression Category：

```text
OBSERVABILITY_HTTP_ALLOWLIST
```

至少包含：

```text
Step 76
  tests/test_observability_http_allowlist_audit.py

Step 77
  tests/test_observability_http_allowlist_regression.py

Step 78
  tests/test_observability_http_allowlist_architecture_audit.py

C25
  tests/test_tool_observability_architecture_audit.py
```

注意：

这不是要求 Regression Matrix 重复执行所有测试。

需要根据当前 Matrix 的设计决定：

```text
file-level registration
```

还是：

```text
representative contract tests
```

---

# 五、不要重复运行测试逻辑

如果现有 Regression Matrix 是：

```text
case → pytest node
```

只登记已有测试 node。

不要复制：

```text
route discovery
allowlist
security scanner
```

到 Regression Matrix。

Regression Matrix 只是：

> **入口 / 索引 / 分类。**

---

# 六、Category Contract

新增 category：

```text
OBSERVABILITY_HTTP_ALLOWLIST
```

说明：

```text
Purpose:
确保 Observability HTTP API 的实际 route、
Frozen Contract、C25 Allowlist 三者保持一致。
```

覆盖：

```text
route discovery
allowlist completeness
frozen contract
single source of truth
scanner uniqueness
security
```

---

# 七、Regression Matrix 行

至少加入：

```text
Category:
OBSERVABILITY_HTTP_ALLOWLIST
```

并记录：

```text
test file
test purpose
DB required
Network required
Production code touched
```

预期：

```text
DB required = NO
Network required = NO
Production code touched = NO
```

---

# 八、推荐 Matrix 条目

如果当前 Matrix 支持 file-level entries：

```text
1.
tests/test_observability_http_allowlist_audit.py

2.
tests/test_observability_http_allowlist_regression.py

3.
tests/test_observability_http_allowlist_architecture_audit.py

4.
tests/test_tool_observability_architecture_audit.py
```

如果 Matrix 已经有“代表性测试”机制：

至少选择：

```text
C25-13 allowlist test
Frozen exact route set
Single source of truth
Single route scanner
```

不要同时重复几十个等价断言。

---

# 九、Regression Metadata

每个新增 Matrix entry 至少记录：

```text
category
test_file
scope
db_required
network_required
llm_required
production_code_required
```

例如：

```text
category:
  OBSERVABILITY_HTTP_ALLOWLIST

db_required:
  false

network_required:
  false

llm_required:
  false

production_code_required:
  false
```

---

# 十、不要修改原有 Contract

Step 73 baseline：

```text
Phase 3.12 Trace / Timeline Contract
```

保持不变。

Step 76：

```text
Route Discovery semantics
```

保持不变。

Step 77：

```text
Frozen Route Contract
```

保持不变。

Step 78：

```text
Single Source / Scanner uniqueness
```

保持不变。

本阶段只是：

```text
register
```

不是：

```text
redesign
```

---

# 十一、Regression 文档

如果现有文档允许扩展：

更新：

```text
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

增加一个简短章节：

```text
## Observability HTTP Allowlist
```

记录：

```text
Step 76 — Route Audit
Step 77 — Frozen Contract
Step 78 — Test Architecture Self-Audit
```

如果当前文档已经明确禁止修改历史 regression 文档：

则新建：

```text
docs/evaluation/phase-3.12-observability-http-allowlist-regression.md
```

但：

**优先遵循现有文档结构，不要重复建立两个报告。**

---

# 十二、Regression Test

如果当前项目已有统一 collector：

运行它。

如果没有：

不要为了本步骤创建大型 collector。

只创建最小的：

```text
OBSERVABILITY_HTTP_ALLOWLIST
```

registration。

---

# 十三、验证要求

先执行：

```powershell
python -m pytest -q tests/test_observability_http_allowlist_architecture_audit.py
```

然后：

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

如果有统一 Regression Matrix 测试：

再执行对应的：

```powershell
python -m pytest -q <existing-regression-entry>
```

不要执行：

```text
RUN_DB_TESTS=1
```

---

# 十四、Compile

执行：

```powershell
python -m compileall -q backend tests scripts
```

要求：

```text
0 errors
```

---

# 十五、Backend Integrity

执行：

```powershell
git diff -- backend
```

必须：

```text
empty
```

如果 backend 有任何修改：

**立即 STOP 并报告。**

---

# 十六、成功条件

必须：

```text
OBSERVABILITY_HTTP_ALLOWLIST category = registered

Step 76 = registered
Step 77 = registered
Step 78 = registered

C25 allowlist audit = registered

Duplicate regression framework = 0

Frozen Contract unchanged

Route Scanner unchanged

Allowlist semantics unchanged

DB = 0

Network = 0

DeepSeek = 0

Backend Diff = EMPTY

Compileall = PASS
```

---

# 十七、特别检查：不要过度扩大 Matrix

如果发现现有 Matrix 已经非常大：

**不要把全部 Observability 测试机械复制进去。**

目标是：

```text
Regression Matrix
    ↓
代表性入口
    ↓
完整 Contract 已由各自测试覆盖
```

而不是：

```text
Regression Matrix
    ↓
重复所有测试
```

---

# 十八、失败处理

如果发现：

```text
Matrix 不支持 category
Matrix 存在重复入口
某个测试需要 DB
某个测试需要 Network
某个测试修改 Production
```

不要擅自重构 Matrix。

报告：

```text
问题：
位置：
影响：
建议：
是否修改：否
```

---

# 十九、最终报告

完成后只报告：

```text
Phase 3.12 Step 79 完成报告

1. Regression Matrix
2. OBSERVABILITY_HTTP_ALLOWLIST Category
3. Step 76 Registration
4. Step 77 Registration
5. Step 78 Registration
6. C25 Registration
7. Duplicate Check
8. Tests
9. Compileall
10. Backend Diff
11. DB / Network / DeepSeek
12. Contract 是否变化
13. 新发现问题
14. 当前限制

Phase 3.12 Step 79 READY
Phase 3.12 Step 79 STOP
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
