# Phase 3.12 Step 81 — Regression Matrix 可执行性审计

继续 Phase 3.12。

当前：

```text
Step 79
→ OBSERVABILITY_HTTP_ALLOWLIST 已进入 Regression Matrix

Step 80
→ Matrix Contract 已冻结
→ 13 categories
→ 28 registered files
→ 18 offline
→ 15 DB
→ 0 orphan
→ 0 duplicate
→ 5 representative nodes collectable
```

本步骤只完成：

> **验证 Regression Matrix 中登记的 28 个测试文件，能够被 pytest 正常 collect。**

不要修改生产代码。

不要修改已有业务 Contract。

不要增加第二套 Regression Framework。

---

# 一、开始前阅读

阅读：

```text
tests/test_assistant_trace_timeline_regression.py
```

重点使用现有：

```text
FILES
CATEGORIES
_offline_suite
_db_suite
FileSpec
```

以及 Step 80 已建立的：

```text
TestRegressionMatrixContract
EXPECTED_MATRIX_SCALE
```

不要重新定义 FILES / CATEGORIES。

---

# 二、核心目标

建立一个最小测试：

```text
Matrix registered file
        ↓
pytest --collect-only
        ↓
success
```

要求：

```text
28 registered files
    ↓
28 files collectable
```

注意：

这里的：

```text
collectable
```

只表示 pytest 能成功收集测试。

不代表：

```text
tests passed
```

也不执行测试。

pytest 官方定义 `--collect-only` 为“只收集测试，不执行测试”。

---

# 三、不要复制 pytest collector

禁止重新实现复杂的 pytest collector。

可以使用：

```text
subprocess
```

调用：

```powershell
python -m pytest <file> --collect-only -q
```

或者复用当前项目已有的 collect helper。

如果已有 helper：

**优先复用。**

---

# 四、逐文件验证

对：

```text
FILES
```

中的每一个：

```text
path
```

执行 collection audit。

要求：

### 文件存在

```text
path.exists()
```

### pytest collection 成功

退出码：

```text
0
```

### stdout/stderr 不包含 collection error

不要对完整输出做脆弱全文匹配。

只判断：

```text
returncode
```

以及必要的：

```text
collection error
```

---

# 五、不要执行测试

本步骤：

```text
pytest --collect-only
```

可以执行。

但是禁止：

```text
pytest -q
```

运行这些 28 个文件。

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
network
```

---

# 六、DB 文件特殊处理

对于：

```text
db == "yes"
```

或：

```text
db == "partial"
```

仍然只做：

```text
--collect-only
```

不启动 PostgreSQL。

不执行 fixture。

不执行数据库测试。

所以：

```text
DB = 0
```

仍然成立。

---

# 七、Offline 文件

对于：

```text
db == "no"
```

同样只做：

```text
--collect-only
```

不执行测试。

---

# 八、建议新增测试

仍然修改：

```text
tests/test_assistant_trace_timeline_regression.py
```

不要新建：

```text
test_regression_matrix_collectability.py
```

建议增加：

```text
TestRegressionMatrixCollectability
```

控制在：

```text
3～5 tests
```

---

# 九、测试要求

至少：

### Test 1

```text
all_registered_files_are_collectable
```

验证：

```text
28 / 28
```

---

### Test 2

```text
offline_registered_files_are_collectable
```

验证：

```text
18 / 18
```

---

### Test 3

```text
db_registered_files_are_collectable
```

验证：

```text
15 / 15
```

---

### Test 4

```text
collectability_does_not_require_db_gate
```

验证在：

```text
RUN_DB_TESTS
```

未开启时：

```text
collect-only
```

仍然能够完成。

---

# 十、重要：不要把 collect-only 结果写进 Matrix

不要新增：

```text
collectable=True
```

到：

```text
FileSpec
```

不要改变：

```text
FileSpec
```

不要增加新的 production/runtime configuration。

Collectability 是：

> Audit result

不是：

> Matrix metadata

---

# 十一、Representative Nodes

Step 80 已经验证：

```text
5 representative nodes
```

本步骤不需要增加新的 node。

不要扩大：

```text
REPRESENTATIVE_CONTRACT_NODES
```

---

# 十二、超时保护

如果使用 subprocess：

必须设置合理 timeout。

例如：

```text
30 seconds / file
```

具体数值根据当前环境选择。

如果超时：

```text
FAIL
```

不要自动 retry。

不要无限等待。

---

# 十三、网络 / DB / LLM

严格：

```text
DB = 0
Network = 0
DeepSeek = 0
```

不要：

```text
RUN_DB_TESTS
```

不要：

```text
httpx
requests
OpenAI client
```

---

# 十四、测试命令

完成后只运行：

### 1. Matrix regression

```powershell
python -m pytest -q tests/test_assistant_trace_timeline_regression.py
```

### 2. Compile

```powershell
python -m compileall -q backend tests scripts
```

不要运行：

```text
pytest -q
```

不要运行：

```text
RUN_DB_TESTS=1
```

---

# 十五、Git Diff

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

除本步骤允许修改的：

```text
tests/test_assistant_trace_timeline_regression.py
```

以及必要的：

```text
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

不要有其他文件。

---

# 十六、最终报告

严格：

```text
Phase 3.12 Step 81 完成报告

1. Matrix
- registered files:
- offline files:
- DB files:

2. Collectability
- total:
- collected successfully:
- failed:
- timeout:

3. Offline Files
- result:

4. DB Files
- result:

5. Representative Nodes
- unchanged:
- collectable:

6. Tests
- passed:
- skipped:
- failed:

7. Compileall
- result:

8. Backend Diff
- result:

9. DB / Network / DeepSeek
- DB:
- Network:
- DeepSeek:

10. Contract Changes
- Step 73:
- Step 76:
- Step 77:
- Step 78:
- Step 79:
- Step 80:

11. 当前限制

Phase 3.12 Step 81 READY
Phase 3.12 Step 81 STOP
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
