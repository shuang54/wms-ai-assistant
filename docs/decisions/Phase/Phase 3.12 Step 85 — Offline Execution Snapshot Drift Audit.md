# Phase 3.12 Step 85 — Offline Execution Snapshot Drift Audit

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
→ Offline Matrix 实际执行
→ 375 total / 356 passed / 19 skipped / 0 failed / 0 errors

Step 84
→ Offline Execution Summary Contract 冻结
→ Contract 与 Snapshot 分离
```

本步骤只解决一个问题：

> **如果未来 Offline Matrix 的测试规模或执行结果发生变化，能够明确识别 Snapshot Drift，而不是静默接受。**

本步骤不是更新 Snapshot。

不是修改 Contract。

不是自动修 Snapshot。

---

# 一、开始前阅读

先阅读：

```text
tests/test_assistant_trace_timeline_regression.py
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

重点确认当前：

```text
OFFLINE_EXECUTION_SUMMARY_CONTRACT
OFFLINE_EXECUTION_SUMMARY_SNAPSHOT
RegressionExecutionSummary
_offline_suite_execution()
TestOfflineRegressionExecutionSummaryContract
```

不要假设字段。

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

```text
git diff -- backend
```

必须为空。

---

# 三、核心原则

明确区分：

```text
Contract
    ↓
定义“什么叫 PASS / FAIL”

Snapshot
    ↓
记录“最近一次已验证的数量是多少”

Drift Audit
    ↓
发现“当前执行结果是否偏离 Snapshot”
```

三者不能混淆。

---

# 四、Drift 定义

当前 Snapshot：

```text
total = 375
passed = 356
skipped = 19
failed = 0
errors = 0
exit_code = 0
status = PASS
```

如果未来实际结果：

```text
380 / 361 / 19 / 0 / 0 / 0
```

则：

```text
Contract = PASS
Snapshot = DRIFT
```

这是**正常的 Drift**，不能判定 Contract 失败。

---

# 五、Drift 分类

建立最小分类：

### 1. NO_DRIFT

```text
当前计数
==
Snapshot
```

---

### 2. COUNT_DRIFT

任意：

```text
total
passed
skipped
failed
errors
```

发生变化。

---

### 3. EXIT_CODE_DRIFT

```text
exit_code
```

发生变化。

---

### 4. STATUS_DRIFT

```text
status
```

发生变化。

---

禁止增加复杂分类。

---

# 六、不要把 Drift 自动判成 Test Failure

这是本步骤最重要的规则。

例如：

```text
Snapshot:
375 / 356 / 19

Current:
380 / 361 / 19
```

结果：

```text
Drift = COUNT_DRIFT
Contract Status = PASS
```

不能：

```text
pytest FAILED
```

因为新增 5 个测试本身不是系统故障。

---

# 七、但 Failure Drift 必须明确暴露

例如 Snapshot：

```text
failed = 0
errors = 0
```

当前：

```text
failed = 1
```

或者：

```text
errors = 1
```

则：

```text
Drift = COUNT_DRIFT
Contract Status = FAIL
```

这里仍然保持：

```text
PASS / FAIL
```

由 Step 84 Contract 决定。

Drift 只是指出 Snapshot 与当前结果不同。

---

# 八、推荐最小 DTO

如果现有结构适合，可以定义：

```text
RegressionExecutionDrift
```

例如：

```text
drift_type
snapshot
current
```

但：

**优先复用普通 tuple / dict / existing DTO。**

不要为了一个 Drift 创建复杂模型。

如果确实创建 DTO：

必须 immutable。

---

# 九、Snapshot 不自动更新

禁止：

```text
current
    ↓
overwrite snapshot
```

禁止：

```text
auto-update
```

禁止：

```text
self-healing baseline
```

必须：

```text
Drift detected
    ↓
developer explicitly reviews
    ↓
future step manually updates snapshot
```

---

# 十、测试

继续使用：

```text
tests/test_assistant_trace_timeline_regression.py
```

新增：

```text
TestOfflineRegressionSnapshotDrift
```

控制：

```text
6～10 tests
```

至少：

### Test 1

Current == Snapshot：

```text
NO_DRIFT
```

---

### Test 2

只增加 passed：

```text
COUNT_DRIFT
```

---

### Test 3

只增加 skipped：

```text
COUNT_DRIFT
```

---

### Test 4

failed 从 0 → 1：

```text
COUNT_DRIFT
Contract Status = FAIL
```

---

### Test 5

errors 从 0 → 1：

```text
COUNT_DRIFT
Contract Status = FAIL
```

---

### Test 6

exit_code：

```text
0 → 1
```

得到：

```text
EXIT_CODE_DRIFT
Contract Status = FAIL
```

---

### Test 7

status：

```text
PASS → FAIL
```

得到：

```text
STATUS_DRIFT
```

---

### Test 8

Future count independence：

```text
380 / 361 / 19 / 0 / 0 / 0
```

必须：

```text
COUNT_DRIFT
Contract = PASS
```

---

### Test 9

Snapshot immutability：

Drift 检查之后：

```text
SNAPSHOT
```

不能被修改。

---

### Test 10

No auto-update：

验证：

```text
current != snapshot
```

执行 Drift Audit 后：

```text
snapshot remains unchanged
```

---

# 十一、不要重新执行 18 个文件

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

本步骤测试：

```text
synthetic current summary
+
frozen snapshot
```

即可。

Step 83 已经证明实际执行链路。

---

# 十二、不要修改 Step 84 Contract

保持：

```text
PASS:
exit_code == 0
AND failed == 0
AND errors == 0

FAIL:
exit_code != 0
OR failed > 0
OR errors > 0
```

不要让：

```text
Drift
```

改变 PASS / FAIL 语义。

---

# 十三、不要处理 DB Snapshot

当前只建立：

```text
OFFLINE snapshot
```

不要增加：

```text
DB snapshot
DB drift
```

DB suite 后续需要独立授权。

---

# 十四、Documentation

在：

```text
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

新增：

```text
§7.5 Offline Snapshot Drift Audit
```

说明：

```text
Contract
    ≠
Snapshot
    ≠
Drift
```

并记录：

```text
NO_DRIFT
COUNT_DRIFT
EXIT_CODE_DRIFT
STATUS_DRIFT
```

特别说明：

> Snapshot Drift 本身不等于系统失败；最终 PASS / FAIL 仍由 Step 84 Execution Summary Contract 决定。

---

# 十五、测试命令

只运行：

```powershell
python -m pytest -q tests/test_assistant_trace_timeline_regression.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

不要：

```text
pytest -q
RUN_DB_TESTS=1
DeepSeek
Network
PostgreSQL
```

---

# 十六、Git Diff

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

# 十七、最终报告

严格：

```text
Phase 3.12 Step 85 完成报告

1. Drift Contract
- NO_DRIFT:
- COUNT_DRIFT:
- EXIT_CODE_DRIFT:
- STATUS_DRIFT:

2. Snapshot
- total:
- passed:
- skipped:
- failed:
- errors:
- exit_code:
- status:

3. Synthetic Drift Tests
- passed:
- skipped:
- failed:

4. Contract Independence
- Future count:
- Contract status:
- Drift status:

5. Snapshot Mutation
- result:

6. Auto-update
- result:

7. Compileall
- result:

8. Backend Diff
- result:

9. DB / Network / DeepSeek
- DB:
- Network:
- DeepSeek:

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

11. 当前限制

Phase 3.12 Step 85 READY
Phase 3.12 Step 85 STOP
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
