# Phase 3.12 Step 87 — Node-hosted Contract Registration Freeze

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

Step 84
→ Execution Summary Contract

Step 85
→ Snapshot Drift Audit

Step 86
→ OFFLINE_EXECUTION_CONTRACT
→ node-hosted category
→ 不进入 CATEGORIES / FILES / suite
```

本步骤只完成：

> **冻结 Step 86 的 node-hosted contract registration 结构。**

不是增加新的 Matrix category。

不是把 node-hosted category 改成 file-hosted。

不是执行 Regression Suite。

---

# 一、开始前阅读

先阅读：

```text
tests/test_assistant_trace_timeline_regression.py
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

重点检查当前：

```text
NODE_HOSTED_CONTRACT_CATEGORIES
NODE_HOSTED_REPRESENTATIVE_NODES

OFFLINE_EXECUTION_CONTRACT

TestOfflineRegressionExecutionSummary
TestOfflineRegressionExecutionSummaryContract
TestOfflineRegressionSnapshotDrift
```

以当前真实实现为准。

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

必须保持：

```text
git diff -- backend
```

为空。

---

# 三、冻结 Node-hosted Contract

明确冻结：

```text
NODE_HOSTED_CONTRACT_CATEGORIES
```

至少包含：

```text
OFFLINE_EXECUTION_CONTRACT
```

以及：

```text
NODE_HOSTED_REPRESENTATIVE_NODES
```

必须包含 Step 86 已有的两个真实 node。

不要创建新的 dummy node。

---

# 四、Node-hosted 与 File-hosted 必须严格分离

冻结以下规则：

```text
File-hosted:
FILES
    ↓
CATEGORIES
    ↓
offline/db suite
```

Node-hosted：

```text
NODE_HOSTED_CONTRACT_CATEGORIES
    ↓
NODE_HOSTED_REPRESENTATIVE_NODES
    ↓
collect-only validation
```

Node-hosted：

**不进入：**

```text
FILES
CATEGORIES
_offline_suite
_db_suite
```

---

# 五、禁止 Self-registration

必须明确断言：

```text
tests/test_assistant_trace_timeline_regression.py
```

不能因为它 host：

```text
OFFLINE_EXECUTION_CONTRACT
```

而被加入：

```text
FILES
```

也不能加入：

```text
CATEGORIES
```

否则会重新违反 Step 80 的：

```text
no self-registration
```

---

# 六、唯一性

增加最小唯一性断言：

### Category 唯一

```text
OFFLINE_EXECUTION_CONTRACT
```

只能在：

```text
NODE_HOSTED_CONTRACT_CATEGORIES
```

出现一次。

---

### Host 唯一

每个 node-hosted category：

```text
exactly one host
```

当前：

```text
tests/test_assistant_trace_timeline_regression.py
```

---

### Representative node 唯一

同一个 nodeid：

```text
不能重复登记
```

---

### Scope 唯一

当前：

```text
OFFLINE_EXECUTION_CONTRACT
```

scope：

```text
OFFLINE_EXECUTION_SUMMARY_CONTRACT
OFFLINE_EXECUTION_SUMMARY_SNAPSHOT
classify_snapshot_drift()
```

不要重复注册相同 scope。

---

# 七、Metadata 冻结

Node-hosted category 必须保持：

```text
db_required = false
network_required = false
llm_required = false
production_code_required = false
```

建议使用当前项目已有 metadata 表达方式。

不要重新创建第二套 FileSpec。

---

# 八、Scale Independence

继续保持：

```text
EXPECTED_MATRIX_SCALE
```

不包含：

```text
OFFLINE_EXECUTION_CONTRACT
```

也不包含：

```text
375
356
19
```

必须明确：

```text
Matrix Scale
    ≠
Node-hosted Contract Count
    ≠
Execution Snapshot
```

---

# 九、Representative Nodes

Step 86 当前有：

```text
TestOfflineRegressionSnapshotDrift
::test_snapshot_is_immutable_during_drift_audit

TestOfflineRegressionExecutionSummaryContract
::test_current_snapshot_matches_recorded_baseline
```

本步骤：

1. 验证两个 node 都存在
2. 验证 nodeid 唯一
3. 验证属于正确 class
4. 验证属于正确 host file
5. 验证 `--collect-only` 可收集
6. 不执行 node

不要新增 node。

---

# 十、禁止执行真实 Suite

本步骤：

```text
DB = 0
Network = 0
DeepSeek = 0
```

不要：

```text
_offline_suite
_db_suite
RUN_DB_TESTS=1
PostgreSQL
```

Node validation 使用：

```text
pytest --collect-only
```

即可。

---

# 十一、测试

继续使用：

```text
tests/test_assistant_trace_timeline_regression.py
```

在现有：

```text
TestRegressionMatrixContract
```

附近增加：

```text
TestNodeHostedContractRegistration
```

控制：

```text
5～8 tests
```

至少：

### Test 1

node-hosted category 唯一。

### Test 2

host 唯一。

### Test 3

representative nodes 唯一。

### Test 4

node-hosted category 不进入 `CATEGORIES`。

### Test 5

node-hosted host 不进入 `FILES`。

### Test 6

node-hosted category 不进入 offline/db suite。

### Test 7

metadata 全部 false。

### Test 8

代表性 node 可 collect。

如果现有 Step 86 测试已经完整覆盖其中某项：

**复用现有断言，不重复堆测试。**

---

# 十二、不要修改 Step 80 Contract

必须继续保持：

```text
FILES = 28
file-hosted categories = 13
offline = 18
db = 15
partial = 5
```

以及：

```text
orphan = 0
dangling = 0
registration execution coverage = 100%
```

Node-hosted contract 是：

```text
parallel contract registry
```

不是对 File-hosted Matrix 的修改。

---

# 十三、不要修改 Step 85 Drift 语义

保持：

```text
NO_DRIFT
COUNT_DRIFT
EXIT_CODE_DRIFT
STATUS_DRIFT
```

以及：

```text
380 / 361 / 19
→ COUNT_DRIFT + PASS
```

不要修改 Drift classifier。

---

# 十四、Documentation

在：

```text
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

新增：

```text
§7.7 Node-hosted Contract Registration
```

明确：

```text
File-hosted Contract
    ↓
FILES / CATEGORIES / suites

Node-hosted Contract
    ↓
NODE_HOSTED_CONTRACT_CATEGORIES
    ↓
NODE_HOSTED_REPRESENTATIVE_NODES
```

并明确：

```text
Node-hosted
    ≠
File-hosted
```

以及：

```text
Node-hosted
    ≠
Execution Suite
```

---

# 十五、Previous Contract

必须验证：

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
```

本步骤只是：

```text
Node-hosted registration freeze
```

---

# 十六、测试命令

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
Network
PostgreSQL
```

---

# 十七、Git Diff

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

不要出现生产代码。

---

# 十八、最终报告

严格：

```text
Phase 3.12 Step 87 完成报告

1. Node-hosted Contract
- categories:
- host:
- scope:

2. File-hosted Independence
- FILES unchanged:
- CATEGORIES unchanged:
- offline suite unchanged:
- DB suite unchanged:

3. Self-registration
- result:

4. Uniqueness
- category:
- host:
- representative nodes:
- scope:

5. Metadata
- db:
- network:
- llm:
- production_code:

6. Representative Nodes
- count:
- collectable:

7. Matrix Scale
- result:

8. Snapshot / Drift
- result:

9. Tests
- passed:
- skipped:
- failed:

10. Compileall
- result:

11. Backend Diff
- result:

12. DB / Network / DeepSeek
- DB:
- Network:
- DeepSeek:

13. Previous Contract
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
- Step 86:

14. 当前限制

Phase 3.12 Step 87 READY
Phase 3.12 Step 87 STOP
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
