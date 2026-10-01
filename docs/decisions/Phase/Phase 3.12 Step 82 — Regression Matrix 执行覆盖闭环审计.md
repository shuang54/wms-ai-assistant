# Phase 3.12 Step 82 — Regression Matrix 执行覆盖闭环审计

继续 Phase 3.12。

当前已经完成：

```text
Step 79
→ OBSERVABILITY_HTTP_ALLOWLIST 纳入 Regression Matrix

Step 80
→ Matrix Contract 冻结
→ 13 categories
→ 28 registered files
→ 18 offline
→ 15 DB

Step 81
→ 28/28 registered files pytest collectable
→ 18/18 offline collectable
→ 15/15 DB collectable
```

本步骤只做：

> **验证 Matrix 中注册的每一个 file，确实进入对应的 category / suite 执行入口。**

注意：

Step 81 验证的是：

```text
registered file
    ↓
pytest can collect
```

Step 82 验证：

```text
registered file
    ↓
category registration
    ↓
offline / DB suite
```

不要修改生产代码。

不要修改 Step 73～81 Contract。

不要新增第二套 Regression Framework。

---

# 一、开始前阅读

阅读：

```text
tests/test_assistant_trace_timeline_regression.py
```

重点确认真实结构：

```text
FILES
CATEGORIES
_REQUIRED_CATEGORIES
_offline_suite
_db_suite
FileSpec
```

以及：

```text
EXPECTED_MATRIX_SCALE
REPRESENTATIVE_CONTRACT_NODES
```

不要假设结构。

---

# 二、核心原则

必须保证：

```text
FILES
  ↓
CATEGORIES
  ↓
suite
```

不存在断链。

即：

```text
每一个 registered file
```

必须：

```text
至少属于一个 category
```

并且：

```text
根据 db metadata
```

进入正确 suite。

---

# 三、File → Category → Suite

对每一个：

```text
FileSpec
```

验证：

### 1. Category

```text
file.path
```

至少存在于一个：

```text
CATEGORIES[category]
```

---

### 2. Suite

如果：

```text
db == "no"
```

必须进入：

```text
_offline_suite
```

如果：

```text
db == "yes"
```

必须进入：

```text
_db_suite
```

如果：

```text
db == "partial"
```

必须按照当前项目已有真实语义判断。

**不要重新定义 partial。**

如果当前 suite 已经规定：

```text
partial
```

同时存在于：

```text
offline
DB
```

则保持现有行为。

如果当前实现没有明确规定：

**只审计当前实际行为，不修改它。**

---

# 四、反向检查

不能只验证：

```text
FILES → suite
```

还必须：

```text
suite → FILES
```

即：

### Offline suite

所有：

```text
_offline_suite
```

文件必须存在于：

```text
FILES
```

---

### DB suite

所有：

```text
_db_suite
```

文件必须存在于：

```text
FILES
```

禁止：

```text
suite 中存在未注册文件
```

---

# 五、Category → Suite

对每个：

```text
category
```

至少验证：

```text
category 有 registered files
```

并且：

```text
category 的 files
```

能够根据当前 db metadata 进入正确 suite。

特别验证：

```text
OBSERVABILITY_HTTP_ALLOWLIST
```

仍然：

```text
4 files
db=no
offline
```

不要修改其内容。

---

# 六、覆盖率定义

本步骤定义：

```text
registration execution coverage
```

不是：

```text
test pass rate
```

因此：

```text
registered files = 28
suite-covered files = 28
```

即可证明：

```text
Matrix execution coverage = 100%
```

不要使用：

```text
passed tests / total tests
```

作为这里的 coverage。

---

# 七、禁止把 Test Result 当 Coverage

例如：

```text
28 files
467 collected nodes
```

不能写成：

```text
467/467 coverage
```

也不要统计：

```text
passed / total
```

因为本阶段检查的是：

```text
Matrix registration topology
```

---

# 八、建议测试

继续放在：

```text
tests/test_assistant_trace_timeline_regression.py
```

不要创建新的：

```text
test_regression_matrix_execution.py
```

建议新增：

```text
TestRegressionMatrixExecutionCoverage
```

控制：

```text
6～8 tests
```

---

# 九、至少覆盖

### Test 1

```text
all_registered_files_are_in_at_least_one_category
```

---

### Test 2

```text
all_offline_suite_files_are_registered
```

---

### Test 3

```text
all_db_suite_files_are_registered
```

---

### Test 4

```text
all_registered_files_are_in_a_suite
```

---

### Test 5

```text
category_files_are_suite_covered
```

---

### Test 6

```text
observability_allowlist_category_is_suite_covered
```

确认：

```text
OBSERVABILITY_HTTP_ALLOWLIST
→ 4 files
→ offline suite
```

---

### Test 7

如果当前结构允许：

```text
suite_partition_matches_existing_db_semantics
```

只验证当前实现，不重新定义：

```text
partial
```

---

# 十、不要执行实际 Suite

本步骤是：

```text
topology audit
```

不是：

```text
full regression
```

所以：

禁止：

```text
pytest -q
```

禁止：

```text
RUN_DB_TESTS=1
```

禁止：

```text
DeepSeek
```

禁止：

```text
PostgreSQL
```

只检查：

```text
FILES
CATEGORIES
_offline_suite
_db_suite
```

这些 Python 对象。

---

# 十一、DB / Network

必须：

```text
DB = 0
Network = 0
DeepSeek = 0
```

不要启动数据库。

不要执行 fixture。

不要调用 HTTP。

---

# 十二、与 Step 80 的关系

Step 80：

```text
Matrix Contract
```

Step 81：

```text
Collectability
```

Step 82：

```text
Execution Registration Coverage
```

形成：

```text
Matrix Contract
      ↓
File Exists
      ↓
Collectable
      ↓
Registered
      ↓
Suite Covered
```

不要把 Step 82 做成测试执行。

---

# 十三、测试命令

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
```

不要：

```text
RUN_DB_TESTS=1
```

---

# 十四、Git Diff

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

除：

```text
tests/test_assistant_trace_timeline_regression.py
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

之外不要出现本步骤新增修改。

---

# 十五、文档

只在：

```text
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

增加一个简短：

```text
§7.3 Execution Registration Coverage
```

记录：

```text
registered files = 28
suite-covered files = 28
coverage = 100%
offline = 18
DB = 15
```

如果 `partial` 导致 overlap：

必须明确记录：

```text
offline + DB != registered
```

不要错误相加。

---

# 十六、最终报告

严格：

```text
Phase 3.12 Step 82 完成报告

1. Matrix
- categories:
- registered files:
- offline:
- DB:
- partial:

2. Registration Coverage
- registered:
- category-covered:
- suite-covered:
- orphan:
- dangling suite entries:

3. Offline Suite
- registered:
- covered:
- missing:

4. DB Suite
- registered:
- covered:
- missing:

5. OBSERVABILITY_HTTP_ALLOWLIST
- category:
- files:
- suite:
- coverage:

6. Partial Semantics
- existing behavior:
- changed: NO

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

11. Contract Changes
- Step 73:
- Step 76:
- Step 77:
- Step 78:
- Step 79:
- Step 80:
- Step 81:

12. 当前限制

Phase 3.12 Step 82 READY
Phase 3.12 Step 82 STOP
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
