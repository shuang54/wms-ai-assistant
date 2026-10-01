# Phase 3.12 Step 83 — Regression Matrix Offline 执行结果汇总 Contract

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
→ 28/28 文件完成 registration → category → suite 闭环
→ registration execution coverage = 100%
```

本步骤开始进入一个新的维度：

> **验证 Offline Regression Matrix 的执行结果能够被结构化汇总。**

注意：

本步骤不是修改 pytest。

不是建立新的 pytest plugin。

不是建立新的 Regression Framework。

只是给现有 Matrix 增加一个最小、确定性的：

```text
Offline Execution Summary Contract
```

---

# 一、开始前阅读

先阅读：

```text
tests/test_assistant_trace_timeline_regression.py
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

重点理解当前：

```text
FILES
CATEGORIES
_offline_suite
_db_suite
FileSpec
TestRegressionMatrixContract
TestRegressionMatrixCollectability
TestRegressionMatrixExecutionCoverage
```

以及现有 offline suite 的执行方式。

不要假设结构。

---

# 二、本步骤严格范围

允许：

```text
tests/test_assistant_trace_timeline_regression.py
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

必要时可以增加一个非常小的纯 Python DTO / helper：

```text
RegressionExecutionSummary
```

但：

**优先不新增文件。**

---

# 三、核心目标

建立：

```text
Offline Regression Matrix
        ↓
Execute existing offline suite
        ↓
Collect result
        ↓
Structured Summary
```

Summary 至少能够表达：

```text
total
passed
skipped
failed
errors
```

以及：

```text
exit_code
```

---

# 四、不要重新实现 pytest

禁止：

```text
pytest plugin
conftest.py
pytest_runtest_logreport hook
pytest_terminal_summary hook
```

除非当前项目已经存在并明确要求使用。

本步骤只需要：

```text
subprocess
    ↓
python -m pytest
    ↓
parse existing terminal result
```

如果项目已经有：

```text
_run_pytest(...)
```

优先复用。

---

# 五、结果 DTO

如果当前代码结构适合，可以增加一个 immutable DTO：

```text
RegressionExecutionSummary
```

建议：

```text
total: int
passed: int
skipped: int
failed: int
errors: int
exit_code: int
```

可以增加：

```text
duration_seconds
```

但不是必须。

不要加入：

```text
database_rows
llm_tokens
network_calls
```

这些不属于 Matrix execution summary。

---

# 六、Result Semantics

必须明确：

### PASS

```text
exit_code == 0
failed == 0
errors == 0
```

---

### FAIL

任意：

```text
failed > 0
```

或者：

```text
errors > 0
```

或者：

```text
exit_code != 0
```

则：

```text
status = FAIL
```

---

### SKIP

`skipped` 只是统计：

```text
skipped count
```

不能因为存在 skipped 就自动认为失败。

---

# 七、重要：不要把 skipped 当 failure

例如：

```text
356 passed
19 skipped
0 failed
```

应该：

```text
PASS
```

而不是：

```text
FAIL
```

DB-gated test 如果因为：

```text
RUN_DB_TESTS not enabled
```

被 skip：

属于：

```text
SKIPPED
```

不是：

```text
FAILED
```

---

# 八、当前 Offline Suite

Step 82 已确定：

```text
offline suite = 18 files
```

本步骤执行：

```text
18 files
```

组成的现有：

```text
_offline_suite
```

不要重新列出 18 个文件。

直接使用现有对象。

---

# 九、Execution Command

使用项目现有执行方式。

优先：

```powershell
python -m pytest -q <offline suite files>
```

如果 `_offline_suite` 当前已经有 helper：

直接复用。

不要：

```text
RUN_DB_TESTS=1
```

不要：

```text
DeepSeek
```

不要访问外部 API。

---

# 十、Result Parsing

不要使用脆弱的：

```text
stdout.split("passed")
```

之类简单字符串逻辑。

优先使用当前已有：

```text
pytest output parser
```

如果项目没有 parser：

可以使用 pytest 标准 terminal summary 的稳定结构。

例如：

```text
=== 356 passed, 19 skipped in ... ===
```

pytest 官方 API 也提供 `parseoutcomes()` 用于解析 terminal summary。

但：

**先检查当前项目是否已经有 parser，再决定是否复用。**

不要重复造一个通用 pytest parser。

---

# 十一、Determinism

同一个：

```text
_offline_suite
```

在相同环境下：

```text
summary
```

的结构必须稳定：

```text
total
passed
skipped
failed
errors
exit_code
```

不要将：

```text
duration
```

纳入 equality。

不要保存：

```text
timestamps
random ids
```

---

# 十二、Summary Arithmetic

验证：

```text
total
```

与：

```text
passed
+ skipped
+ failed
+ errors
```

保持一致。

如果 pytest 当前输出存在：

```text
xfailed
xpassed
warnings
deselected
```

先检查项目现有语义。

**不要擅自把这些类别塞进 total。**

如果当前 Matrix 没有这些情况：

保持最小实现。

---

# 十三、不要把 Matrix Summary 变成 Dashboard

禁止：

```text
HTML
JSON persistence
SQLite
PostgreSQL
Redis
Kafka
Grafana
Prometheus
```

本步骤：

```text
summary = in-memory object
```

即可。

---

# 十四、测试

继续：

```text
tests/test_assistant_trace_timeline_regression.py
```

建议增加：

```text
TestOfflineRegressionExecutionSummary
```

控制：

```text
5～8 tests
```

---

# 十五、至少测试

### Test 1

```text
summary DTO immutable
```

---

### Test 2

```text
all pass
```

例如：

```text
10 passed
0 skipped
0 failed
0 errors
exit_code=0
```

→ PASS。

---

### Test 3

```text
pass + skip
```

例如：

```text
10 passed
2 skipped
0 failed
0 errors
```

→ PASS。

---

### Test 4

```text
failed
```

→ FAIL。

---

### Test 5

```text
error
```

→ FAIL。

---

### Test 6

```text
non-zero exit code
```

→ FAIL。

---

### Test 7

```text
summary arithmetic consistency
```

---

### Test 8

```text
real offline suite execution
```

只执行：

```text
_offline_suite
```

并生成实际 summary。

如果当前项目运行时间可接受，可以执行一次。

---

# 十六、禁止真实 DB

即使：

```text
_offline_suite
```

中存在：

```text
partial
```

本步骤仍然不要打开：

```text
RUN_DB_TESTS
```

根据当前 Step 74 起的语义：

```text
partial
```

会进入 DB suite。

但：

> 本步骤只执行 offline suite。

因此必须明确：

```text
DB = 0
```

---

# 十七、禁止 DeepSeek / Network

必须：

```text
DeepSeek = 0
Network = 0
```

如果某个 offline test 意外触发网络：

立即停止。

不要修改测试让它绕过网络。

---

# 十八、Documentation

在：

```text
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

新增：

```text
§7.4 Offline Execution Summary Contract
```

记录：

```text
Offline Matrix
    ↓
Execution
    ↓
Summary
```

定义：

```text
PASS:
exit_code = 0
failed = 0
errors = 0

SKIPPED:
仅作为统计，不等于失败

FAIL:
failed > 0
OR errors > 0
OR exit_code != 0
```

不要记录当前机器具体耗时作为 Contract。

---

# 十九、不要修改历史 Contract

必须保持：

```text
Step 73
Step 76
Step 77
Step 78
Step 79
Step 80
Step 81
Step 82
```

全部语义不变。

本步骤只是：

```text
execution result summary
```

不修改：

```text
Trace
Timeline
HTTP Allowlist
Matrix registration
Collectability
Registration coverage
```

---

# 二十、测试命令

先：

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
```

不要：

```text
RUN_DB_TESTS=1
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
Phase 3.12 Step 83 完成报告

1. Offline Matrix
- files:
- registered:
- executed:

2. Execution Summary
- total:
- passed:
- skipped:
- failed:
- errors:
- exit_code:
- status:

3. Summary Contract
- PASS semantics:
- FAIL semantics:
- SKIP semantics:

4. Arithmetic
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

9. Contract Changes
- Step 73:
- Step 76:
- Step 77:
- Step 78:
- Step 79:
- Step 80:
- Step 81:
- Step 82:

10. 当前限制

Phase 3.12 Step 83 READY
Phase 3.12 Step 83 STOP
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

