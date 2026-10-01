# Phase 3.12 Step 86 — Snapshot Drift 纳入 Regression Matrix Contract

继续 Phase 3.12。

当前已经完成：

```text
Step 79
→ OBSERVABILITY_HTTP_ALLOWLIST 纳入 Matrix

Step 80
→ Matrix Contract 冻结

Step 81
→ 28/28 文件 collectable

Step 82
→ registration execution coverage = 100%

Step 83
→ Offline Execution Summary
→ 375 total / 356 passed / 19 skipped / 0 failed / 0 errors

Step 84
→ Offline Execution Summary Contract 冻结

Step 85
→ Offline Snapshot Drift Audit
→ NO_DRIFT / COUNT_DRIFT / EXIT_CODE_DRIFT / STATUS_DRIFT
```

本步骤只做：

> **把 Step 85 的 Drift Audit 纳入现有 Regression Matrix Contract。**

不执行真实 Suite。

不修改 Snapshot。

不修改生产代码。

---

# 一、开始前阅读

先阅读：

```text
tests/test_assistant_trace_timeline_regression.py
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

重点确认当前：

```text
FILES
CATEGORIES
_offline_suite
_db_suite
EXPECTED_MATRIX_SCALE
REPRESENTATIVE_CONTRACT_NODES

OFFLINE_EXECUTION_SUMMARY_CONTRACT
OFFLINE_EXECUTION_SUMMARY_SNAPSHOT

classify_snapshot_drift()
```

不要假设结构。

---

# 二、严格范围

只允许修改：

```text
tests/test_assistant_trace_timeline_regression.py
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

不要新增文件。

不要修改：

```text
backend/
```

要求：

```powershell
git diff -- backend
```

必须为空。

---

# 三、核心目标

当前 Matrix 已经覆盖：

```text
Registration
    ↓
Category
    ↓
Suite
    ↓
Collectability
    ↓
Execution Coverage
```

本步骤扩展为：

```text
Registration
    ↓
Category
    ↓
Suite
    ↓
Collectability
    ↓
Execution Coverage
    ↓
Execution Summary Contract
    ↓
Snapshot Drift Contract
```

---

# 四、新增 Matrix Category

在现有：

```text
CATEGORIES
```

中增加一个：

```text
OFFLINE_EXECUTION_CONTRACT
```

不要创建新的 Regression Framework。

---

# 五、Category Scope

该 Category 只覆盖：

```text
OFFLINE_EXECUTION_SUMMARY_CONTRACT
OFFLINE_EXECUTION_SUMMARY_SNAPSHOT
classify_snapshot_drift()
```

以及：

```text
TestOfflineRegressionExecutionSummaryContract
TestOfflineRegressionSnapshotDrift
```

不要把：

```text
DB suite
```

纳入本 Category。

---

# 六、Category Metadata

沿用现有 FileSpec / Category metadata。

要求：

```text
db = "no"
network = false
llm = false
production_code = false
```

不要改变已有：

```text
db = "partial"
```

语义。

---

# 七、优先复用现有 Regression 文件

不要新增：

```text
tests/test_offline_execution_contract.py
```

不要新增：

```text
tests/test_snapshot_drift.py
```

直接把当前：

```text
tests/test_assistant_trace_timeline_regression.py
```

继续作为唯一 Matrix Collector。

---

# 八、Representative Contract Nodes

继续使用：

```text
REPRESENTATIVE_CONTRACT_NODES
```

新增至少一个代表性 node：

```text
TestOfflineRegressionSnapshotDrift
```

如果当前结构更适合：

```text
TestOfflineRegressionExecutionSummaryContract
```

也可以选择它。

但：

> Representative node 必须真实存在、可 collect、属于当前 Matrix。

不要创建 dummy test。

---

# 九、Execution Coverage

Step 82 已冻结：

```text
registered files = 28
category-covered = 28
suite-covered = 28
orphan = 0
dangling = 0
```

新增 Category 后：

重新检查：

```text
file → category
category → file
file → suite
```

必须仍然：

```text
orphan = 0
dangling = 0
```

---

# 十、重要：不要增加 registered file 数量

当前：

```text
registered files = 28
```

本步骤不要新增测试文件。

因此：

```text
registered files
```

仍然必须：

```text
28
```

只是：

```text
categories
```

从：

```text
13
```

增加到：

```text
14
```

如果当前实现实际上能够在不增加 Category 的情况下表达该 Contract：

可以保持 13。

但：

**优先让 Matrix 明确表达 Snapshot Drift Contract。**

---

# 十一、Offline Suite

Step 85 的测试本身已经属于：

```text
offline
```

因此新增 Category 后：

```text
offline suite
```

文件数量仍然：

```text
18
```

不要新增：

```text
DB suite
```

不要改变：

```text
partial files = 5
```

---

# 十二、Contract Tests

在现有：

```text
TestRegressionMatrixContract
```

中增加最小断言。

至少验证：

### 1

Category 存在：

```text
OFFLINE_EXECUTION_CONTRACT
```

---

### 2

Category 唯一。

---

### 3

Category metadata 正确：

```text
db = no
network = false
llm = false
production_code = false
```

---

### 4

Category 有对应测试文件：

```text
tests/test_assistant_trace_timeline_regression.py
```

---

### 5

Drift tests 可以被 collect。

---

# 十三、不要冻结具体 Snapshot 数字到 Matrix Scale

非常重要。

不要修改：

```text
EXPECTED_MATRIX_SCALE
```

去写：

```text
375
356
19
```

因为：

```text
Matrix Scale
≠
Execution Snapshot
```

Matrix Scale 继续表达：

```text
13/28/18/15
```

如果新增 Category：

按照实际 Matrix Contract 正确更新：

```text
13 → 14
```

但不要加入：

```text
375
356
19
```

---

# 十四、Snapshot Drift Contract

Matrix 只验证：

```text
classify_snapshot_drift()
```

的语义存在且测试覆盖。

不要在 Matrix Contract 中要求：

```text
current == snapshot
```

因为未来合法的测试数量增长会产生：

```text
COUNT_DRIFT
```

---

# 十五、必须保持 Future Count Independence

继续保持：

```text
380 / 361 / 19 / 0 / 0
```

得到：

```text
COUNT_DRIFT
Contract = PASS
```

这个语义不能因为加入 Matrix 而改变。

---

# 十六、测试数量

本步骤新增：

```text
4～7 tests
```

不要机械增加。

至少：

### Test 1

Category registered。

### Test 2

Category metadata correct。

### Test 3

Drift tests registered / collectable。

### Test 4

Snapshot not treated as Matrix Scale。

### Test 5

Future count independence remains PASS。

### Test 6

No DB/network/LLM metadata。

如果当前 Contract 已经能够覆盖其中部分，不要重复测试。

---

# 十七、不要执行 18 文件

本步骤：

```text
DB = 0
Network = 0
DeepSeek = 0
```

不要执行：

```text
_offline_suite
```

不要重新生成：

```text
375 / 356 / 19
```

只验证：

```text
Matrix registration
+
Contract
+
collectability
+
synthetic drift
```

---

# 十八、Documentation

在：

```text
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

新增：

```text
§7.6 Snapshot Drift Contract Registration
```

说明：

```text
Regression Matrix
    ├── Registration
    ├── Collectability
    ├── Execution Coverage
    ├── Execution Summary
    └── Snapshot Drift
```

明确：

```text
Snapshot Drift
    ≠
Test Failure
```

并说明：

```text
Contract
    ≠
Snapshot
```

---

# 十九、Previous Contract 必须保持

验证：

```text
Step 73 unchanged
Step 76 unchanged
Step 77 unchanged
Step 78 unchanged
Step 79 unchanged
Step 80 unchanged
Step 81 unchanged
Step 82 unchanged
Step 83 unchanged
Step 84 unchanged
Step 85 unchanged
```

Step 85 的：

```text
NO_DRIFT
COUNT_DRIFT
EXIT_CODE_DRIFT
STATUS_DRIFT
```

必须保持。

---

# 二十、测试命令

只运行：

```powershell
python -m pytest -q tests/test_assistant_trace_timeline_regression.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

不要运行：

```text
pytest -q
RUN_DB_TESTS=1
DeepSeek
PostgreSQL
Network
```

---

# 二十一、Git Diff

检查：

```powershell
git diff -- backend
```

必须：

```text
empty
```

检查：

```powershell
git status --short
```

允许：

```text
tests/test_assistant_trace_timeline_regression.py
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

不要出现生产代码修改。

---

# 二十二、最终报告

严格：

```text
Phase 3.12 Step 86 完成报告

1. Matrix Category
- category:
- category count:
- metadata:

2. Registration
- registered files:
- category-covered:
- suite-covered:
- orphan:
- dangling:

3. Offline Suite
- files:
- DB:
- Network:
- DeepSeek:

4. Snapshot Drift
- NO_DRIFT:
- COUNT_DRIFT:
- EXIT_CODE_DRIFT:
- STATUS_DRIFT:

5. Future Count Independence
- result:

6. Representative Nodes
- count:
- new node:

7. Tests
- passed:
- skipped:
- failed:

8. Compileall
- result:

9. Backend Diff
- result:

10. Previous Contract
- Step 73:
- Step 76:
- Step 77:
- Step 78:
- Step 79:
- Step 80:
- Step 81:
- Step 82:
- Step 83:
- Step 84:
- Step 85:

11. 当前限制

Phase 3.12 Step 86 READY
Phase 3.12 Step 86 STOP
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
