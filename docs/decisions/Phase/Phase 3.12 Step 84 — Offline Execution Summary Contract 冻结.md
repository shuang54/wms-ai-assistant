# Phase 3.12 Step 84 — Offline Execution Summary Contract 冻结

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
→ Offline Matrix 实际执行：
   375 total
   356 passed
   19 skipped
   0 failed
   0 errors
   exit_code = 0
   status = PASS
```

本步骤只做：

> **冻结 Step 83 的 Execution Summary Contract。**

不是新增执行能力。

不是重新设计 Matrix。

不是增加 DB Summary。

不是进入 Timeline。

---

# 一、开始前阅读

先阅读当前：

```text
tests/test_assistant_trace_timeline_regression.py
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

确认 Step 83 当前真实实现：

```text
RegressionExecutionSummary
_offline_suite_execution()
TestOfflineRegressionExecutionSummary
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

Backend diff 必须保持：

```text
empty
```

---

# 三、冻结 Summary Contract

在现有 Matrix Contract 中增加：

```text
OFFLINE_EXECUTION_SUMMARY
```

冻结：

```text
total
passed
skipped
failed
errors
exit_code
status
```

其中：

```text
duration_seconds
```

仍然只是 runtime information：

```text
不参与 equality
不进入 Contract
不参与 baseline
```

---

# 四、冻结当前已验证基线

Step 83 当前实际执行结果：

```text
total = 375
passed = 356
skipped = 19
failed = 0
errors = 0
exit_code = 0
status = PASS
```

将这些作为：

```text
offline execution baseline
```

但注意：

这不是业务测试结果 baseline。

只是：

```text
Regression Matrix execution baseline
```

---

# 五、不要把 375 绑定成永久测试数量

必须区分：

```text
Contract
```

与：

```text
Current Snapshot
```

Contract 冻结：

```text
PASS semantics
FAIL semantics
SKIP semantics
arithmetic semantics
exit_code semantics
```

Snapshot 记录：

```text
375 / 356 / 19 / 0 / 0
```

未来如果增加测试，例如：

```text
380 total
361 passed
19 skipped
```

不能因此自动判定 Contract 回归。

---

# 六、PASS Contract

冻结：

```text
PASS iff:

exit_code == 0
AND
failed == 0
AND
errors == 0
```

---

# 七、FAIL Contract

冻结：

```text
FAIL iff:

exit_code != 0
OR
failed > 0
OR
errors > 0
```

---

# 八、SKIP Contract

冻结：

```text
skipped >= 0
```

并且：

```text
skipped
```

只是 outcome count。

不能单独导致：

```text
FAIL
```

---

# 九、Arithmetic Contract

冻结：

```text
total
==
passed
+ skipped
+ failed
+ errors
```

当前 snapshot：

```text
375
=
356
+
19
+
0
+
0
```

---

# 十、Extra Outcomes

继续保持：

```text
xfail
xpass
deselected
warnings
```

不进入：

```text
total
```

但是如果当前实现已经能够检测：

```text
unexpected outcome token
```

则保留。

不要在本步骤增加新的 outcome 类型。

---

# 十一、Immutability

冻结：

```text
RegressionExecutionSummary
```

必须保持：

```text
frozen=True
```

禁止修改：

```python
summary.passed = ...
```

---

# 十二、Snapshot 建议

如果当前 regression 文档已经存在 execution summary：

直接补充：

```text
### Step 83 Offline Execution Snapshot
```

记录：

```text
total: 375
passed: 356
skipped: 19
failed: 0
errors: 0
exit_code: 0
status: PASS
```

明确：

```text
duration_seconds:
informational only
```

不要记录机器路径、用户名、环境变量、API Key 等信息。

---

# 十三、测试

继续复用：

```text
tests/test_assistant_trace_timeline_regression.py
```

新增：

```text
TestOfflineRegressionExecutionSummaryContract
```

控制在：

```text
5～8 tests
```

至少验证：

### 1

Current snapshot：

```text
375 / 356 / 19 / 0 / 0 / 0
```

符合当前执行结果。

---

### 2

PASS semantics。

---

### 3

FAIL semantics。

---

### 4

SKIP 不导致失败。

---

### 5

Arithmetic。

---

### 6

DTO immutable。

---

### 7

Future-count independence。

例如：

```text
380 total
361 passed
19 skipped
0 failed
0 errors
exit_code=0
```

仍然：

```text
PASS
```

证明 Contract 不绑定 375。

---

# 十四、不要重新执行 DB

本步骤：

```text
DB = 0
```

不要：

```powershell
$env:RUN_DB_TESTS="1"
```

不要启动 PostgreSQL。

---

# 十五、不要调用 DeepSeek

必须：

```text
DeepSeek = 0
Network = 0
```

---

# 十六、不要重新执行完整 Offline Matrix

Step 83 已经完成：

```text
18/18 executed
```

本步骤主要冻结 Contract。

测试只使用：

```text
synthetic summary
```

以及当前已知 snapshot。

不要为了验证 Contract 再跑 18 个文件。

这样可以避免：

```text
每个小阶段都重复跑 7 秒
```

---

# 十七、禁止修改历史 Contract

保持：

```text
Step 73 unchanged
Step 76 unchanged
Step 77 unchanged
Step 78 unchanged
Step 79 unchanged
Step 80 unchanged
Step 81 unchanged
Step 82 unchanged
```

本步骤只新增：

```text
Step 83 execution summary contract
```

---

# 十八、测试命令

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
```

---

# 十九、Git Diff

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

不要出现其他生产代码。

---

# 二十、最终报告

严格：

```text
Phase 3.12 Step 84 完成报告

1. Contract
- PASS:
- FAIL:
- SKIP:
- Arithmetic:
- Exit code:

2. Current Snapshot
- total:
- passed:
- skipped:
- failed:
- errors:
- exit_code:
- status:

3. Future-count Independence
- result:

4. Immutability
- result:

5. Tests
- passed:
- skipped:
- failed:

6. Compileall
- result:

7. Backend Diff
- result:

8. DB / Network / DeepSeek
- DB:
- Network:
- DeepSeek:

9. Previous Contract
- Step 73:
- Step 76:
- Step 77:
- Step 78:
- Step 79:
- Step 80:
- Step 81:
- Step 82:

10. 当前限制

Phase 3.12 Step 84 READY
Phase 3.12 Step 84 STOP
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
