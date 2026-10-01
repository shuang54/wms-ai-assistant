# Phase 3.12 Step 88 — Node-hosted Contract Regression Integration

继续 Phase 3.12。

当前：

```text
Step 87
→ Node-hosted Contract Registration Freeze
→ 74 passed / 2 skipped
→ backend diff = empty
→ Step 73～86 Contract unchanged
```

本步骤只完成：

> **将 Step 87 的 Node-hosted Contract 自检正式纳入现有 Regression Matrix 的“Contract Audit”范围。**

注意：

**不是把 Node-hosted 加入 FILES。**

**不是增加 Matrix category。**

**不是增加 offline/db execution suite。**

**不是修改生产代码。**

---

# 一、开始前阅读

阅读：

```text
tests/test_assistant_trace_timeline_regression.py
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

重点确认：

```text
NODE_HOSTED_CONTRACT_CATEGORIES
NODE_HOSTED_REPRESENTATIVE_NODES

EXPECTED_MATRIX_SCALE
FILES
CATEGORIES
_offline_suite()
_db_suite()

TestNodeHostedContractRegistration
TestRegressionMatrixContract
```

---

# 二、严格范围

只允许修改：

```text
tests/test_assistant_trace_timeline_regression.py
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

禁止：

```text
backend/
```

禁止新增文件。

必须保持：

```powershell
git diff -- backend
```

为空。

---

# 三、目标

建立一个明确的：

```text
Regression Matrix Contract Audit
```

能够证明：

```text
File-hosted Contract
        ↓
FILES / CATEGORIES / suites

Node-hosted Contract
        ↓
NODE_HOSTED_CONTRACT_CATEGORIES
        ↓
NODE_HOSTED_REPRESENTATIVE_NODES
        ↓
Contract Audit
```

而不是：

```text
Node-hosted
    ↓
FILES
    ↓
Execution Suite
```

---

# 四、Matrix Contract 不得扩大

必须保持：

```text
file-hosted categories = 13
registered files = 28
offline files = 18
db files = 15
partial = 5
```

保持：

```text
orphan = 0
dangling = 0
registration execution coverage = 100%
```

不要修改：

```text
EXPECTED_MATRIX_SCALE
```

不要把：

```text
OFFLINE_EXECUTION_CONTRACT
```

加入 `CATEGORIES`。

---

# 五、Node-hosted Contract Audit

在现有 Regression Matrix Contract 测试附近增加最小测试。

建议：

```text
TestNodeHostedContractRegressionIntegration
```

控制：

```text
4～6 tests
```

至少验证：

### Test 1：Node-hosted Contract 已被 Contract Audit 覆盖

确认：

```text
NODE_HOSTED_CONTRACT_CATEGORIES
```

存在且非空。

---

### Test 2：Node-hosted Contract 不进入 FILES

明确：

```text
OFFLINE_EXECUTION_CONTRACT not in FILES
```

---

### Test 3：Node-hosted Contract 不进入 Execution Suites

确认：

```text
OFFLINE_EXECUTION_CONTRACT
```

不会导致：

```text
offline suite count +1
db suite count +1
```

---

### Test 4：Representative Nodes 与 Contract 一致

确认：

```text
NODE_HOSTED_REPRESENTATIVE_NODES
```

中的 node：

```text
host 正确
class 正确
function 正确
唯一
可 collect
```

---

### Test 5：Node-hosted Contract 不影响 Matrix Scale

确认：

```text
EXPECTED_MATRIX_SCALE
```

仍然：

```text
13 / 28 / 18 / 15
```

不要使用 Snapshot 中的：

```text
375
356
19
```

参与 Scale 判断。

---

### Test 6：File-hosted / Node-hosted 双向边界

明确验证：

```text
FILE-hosted → 不要求 NODE_HOSTED_CONTRACT_CATEGORIES
NODE-hosted → 不要求 FILES/CATEGORIES/suite
```

避免以后有人为了“统一”而把 Node-hosted 强行塞进 FileSpec。

---

# 六、禁止重复 Step 87 测试

非常重要。

Step 87 已经存在：

```text
TestNodeHostedContractRegistration
```

本步骤不要重新测试：

```text
category 唯一
host 唯一
scope 唯一
metadata
self-registration
```

除非这些是为了验证：

```text
Regression Matrix Integration
```

而不是重复验证 Node-hosted 本身。

优先复用已有 helper / fixture / 常量。

---

# 七、Representative Node

继续使用 Step 86 / 87 的两个 node：

```text
TestOfflineRegressionSnapshotDrift
::test_snapshot_is_immutable_during_drift_audit

TestOfflineRegressionExecutionSummaryContract
::test_current_snapshot_matches_recorded_baseline
```

不要新增 node。

本步骤仍然：

```text
collect-only
```

不要真正执行这两个 node。

pytest 的 `--collect-only` 只进行测试收集而不执行测试，适合继续保持当前的轻量 Contract Audit 边界。

---

# 八、不要运行真实 Regression Suite

本步骤：

```text
DB = 0
Network = 0
DeepSeek = 0
```

不要：

```text
_offline_suite()
_db_suite()
RUN_DB_TESTS=1
PostgreSQL
```

只运行当前 regression collector：

```powershell
python -m pytest -q tests/test_assistant_trace_timeline_regression.py
```

---

# 九、Documentation

在：

```text
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

增加：

```text
§7.8 Node-hosted Contract Regression Integration
```

明确：

```text
Node-hosted Contract
        ↓
Contract Audit
```

但：

```text
Node-hosted Contract
        X
        ↓
Execution Matrix
```

同时记录：

```text
Matrix Scale remains 13 / 28 / 18 / 15
Node-hosted Contract count is intentionally excluded
```

---

# 十、Previous Contract

必须保持：

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
Step 86 unchanged
Step 87 unchanged
```

本步骤只增加：

```text
Step 88 Contract Integration Audit
```

---

# 十一、测试数量

目标：

```text
4～6 tests
```

不要为了数量重复 Step 87。

---

# 十二、Compile

运行：

```powershell
python -m compileall -q backend tests scripts
```

要求：

```text
0 errors
```

---

# 十三、Git Diff

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

本步骤允许：

```text
tests/test_assistant_trace_timeline_regression.py
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

不要新增文件。

---

# 十四、最终报告

严格：

```text
Phase 3.12 Step 88 完成报告

1. Contract Integration
- Node-hosted Contract:
- Contract Audit:

2. File-hosted Matrix
- FILES:
- CATEGORIES:
- offline:
- DB:
- partial:

3. Node-hosted Boundary
- FILES:
- CATEGORIES:
- offline suite:
- DB suite:

4. Representative Nodes
- count:
- collectable:

5. Matrix Scale
- result:

6. Snapshot / Drift
- result:

7. Tests
- passed:
- skipped:
- failed:

8. Compileall
- result:

9. Backend Diff
- result:

10. DB / Network / DeepSeek
- DB:
- Network:
- DeepSeek:

11. Previous Contract
- Step 73～87:

12. 当前限制

Phase 3.12 Step 88 READY
Phase 3.12 Step 88 STOP
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
