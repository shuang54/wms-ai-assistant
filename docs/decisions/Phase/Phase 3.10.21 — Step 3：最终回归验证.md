# Phase 3.10.21 — Step 3：最终回归验证

## 一、目标

Phase 3.10.21 当前已经完成：

```text
Facade = implemented
Facade Tests = 19 passed
Composition = 73 passed
Architecture §8.21 = created
Evaluation = created
Python implementation = unchanged since Step 1
```

本 Step 只做：

```text
测试
静态检查
DB 回归
Git Diff
```

**禁止新增功能。**

---

# 二、Step 3.1：Facade 定向测试

执行：

```powershell
cd D:\coding\ai\wms-ai-assistant

python -m pytest -q tests/test_llm_usage_analytics_facade.py
```

目标：

```text
19 passed
0 failed
```

---

# 三、Step 3.2：Facade + Analytics + Aggregation

执行：

```powershell
python -m pytest -q `
  tests/test_llm_usage_analytics_facade.py `
  tests/test_llm_usage_analytics.py `
  tests/test_llm_usage_aggregation.py
```

目标：

```text
73 passed
0 failed
```

如果出现失败：

**立即停止，不修改 Python。**

---

# 四、Step 3.3：Usage Query 链回归

执行：

```powershell
python -m pytest -q `
  tests/test_llm_usage_analytics_facade.py `
  tests/test_llm_usage_analytics.py `
  tests/test_llm_usage_aggregation.py `
  tests/test_llm_usage_query.py `
  tests/test_llm_usage_query_runtime.py
```

记录：

```text
passed
skipped
failed
```

重点确认：

```text
Query
Query Runtime
Aggregation
Analytics
Facade
```

之间没有互相破坏。

---

# 五、Step 3.4：Usage 全链回归

执行：

```powershell
python -m pytest -q `
  tests/test_llm_usage_persistence.py `
  tests/test_llm_usage_persistence_idempotency.py `
  tests/test_llm_usage_query.py `
  tests/test_llm_usage_query_runtime.py `
  tests/test_llm_usage_aggregation.py `
  tests/test_llm_usage_analytics.py `
  tests/test_llm_usage_analytics_facade.py
```

目标：

```text
0 failed
```

记录真实：

```text
passed
skipped
```

不要修改已有测试结果。

---

# 六、Step 3.5：全量测试

执行：

```powershell
python -m pytest -q
```

这一轮：

**不要设置 `RUN_DB_TESTS=1`。**

记录：

```text
passed
skipped
failed
```

目标：

```text
failed = 0
```

如果失败：

立即停止并报告：

```text
失败测试：
失败原因：
是否与 Phase 3.10.21 有关：
涉及模块：
```

不要为了让全量测试通过而修改旧代码。

---

# 七、Step 3.6：DB Integration Regression

如果项目当前 DB 测试仍然使用：

```text
RUN_DB_TESTS=1
```

PowerShell 执行：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

记录：

```text
passed
skipped
failed
```

目标：

```text
failed = 0
```

如果测试完成后需要恢复环境变量：

```powershell
Remove-Item Env:RUN_DB_TESTS -ErrorAction SilentlyContinue
```

---

# 八、Step 3.7：数据库写入检查

确认本阶段没有产生数据库写入。

至少检查：

```text
ai_ops.llm_usage_record
```

以及当前项目已有 DB regression 检查结果。

重点确认：

```text
INSERT = 0
UPDATE = 0
DELETE = 0
```

最终记录：

```text
DB writes = 0
```

同时确认：

```text
Facade DB Access = 0
Analytics DB Access = 0
Aggregation DB Access = 0
```

---

# 九、Step 3.8：compileall

执行：

```powershell
python -m compileall backend tests
```

目标：

```text
0 errors
```

如果出现错误：

**立即停止。**

不要修改 Python。

---

# 十、Step 3.9：LSP / Diagnostics

使用项目当前已有的 lint / LSP 检查方式。

至少检查：

```text
backend/app/services/llm_usage_analytics_facade.py
tests/test_llm_usage_analytics_facade.py
```

同时确认本阶段文档修改没有导致工具链问题。

目标：

```text
0 diagnostics
```

如果出现与本阶段无关的已有 warning：

不要为了清零而修改无关代码。

如有必要记录：

```text
Pre-existing diagnostic
```

---

# 十一、Step 3.10：Git Status

执行：

```powershell
git status --short
```

确认 Phase 3.10.21 相关文件：

```text
backend/app/services/llm_usage_analytics_facade.py
tests/test_llm_usage_analytics_facade.py
docs/architecture.md
docs/evaluation/Phase 3.10.21 — Usage Analytics Read Facade.md
```

如果 Facade 和测试文件是 Step 1 前就存在的新增文件，保持其当前状态即可。

---

# 十二、Step 3.11：Git Diff Stat

执行：

```powershell
git --no-pager diff --stat
```

确认没有意外修改：

```text
LLM Client
Observation
Accounting
Persistence
Idempotency
Query Repository
Query Service
Query Runtime
Aggregation
Analytics
Router
RAG
Tool
Text-to-SQL
SQL Validator
SQL Executor
```

---

# 十三、Step 3.12：最终架构检查

最终架构应该保持：

```text
LLM Request
  ↓
Observation
  ↓
Accounting
  ↓
Persistence
  ↓
Idempotency
  ↓
PostgreSQL
  ↓
Query Repository
  ↓
Query Service
  ↓
Query Runtime
  ↓
LLMUsageRecordView
  ↓
Usage Aggregation
  ↓
Usage Analytics
  ↓
Usage Analytics Read Facade
  ↓
Future API / Dashboard / Admin / AI Ops
```

其中：

```text
Facade
  ↓
Query Runtime
  ↓
Analytics Service
```

是本阶段新增的 Application Read Boundary。

---

# 十四、最终安全边界

确认：

```text
Facade
├── NO SQL
├── NO Session
├── NO Engine
├── NO Connection
├── NO Repository
├── NO DB write
├── NO Network
└── NO LLM call
```

---

# 十五、最终报告

全部完成后严格按照：

```text
【Phase 3.10.21 COMPLETE】

1. 新增文件
2. 修改文件
3. Facade Contract
4. Query → Analytics Composition
5. Snapshot
6. Pagination
7. Convenience Methods
8. Error Propagation
9. Immutability
10. Security Boundary
11. 测试结果
12. DB Access / DB writes
13. Network
14. compileall
15. LSP / Diagnostics
16. Git Diff
17. 发现并修复的问题
18. 当前限制
```

测试结果必须填写**实际数字**，不要沿用之前的数字。

最后：

```text
Architecture:

LLM Request
  ↓
Observation
  ↓
Accounting
  ↓
Persistence
  ↓
Idempotency
  ↓
PostgreSQL
  ↓
Query Repository
  ↓
Query Service
  ↓
Query Runtime
  ↓
LLMUsageRecordView
  ↓
Usage Aggregation
  ↓
Usage Analytics
  ↓
Usage Analytics Read Facade
  ↓
Future Consumers
```

明确：

```text
Billing       = NOT IMPLEMENTED
Dashboard     = NOT IMPLEMENTED
HTTP API      = NOT IMPLEMENTED
Frontend      = NOT IMPLEMENTED
Queue/Worker  = NOT IMPLEMENTED
Outbox        = NOT IMPLEMENTED
```

**完成后立即停止。**

不要进入 Phase 3.10.22。

不要开发 API。

不要开发 Dashboard。

不要开发 Billing。

不要开发 Frontend。

不要继续扩展 Facade。
