# Phase 3.12 Step 80 — Regression Matrix Contract 完整性冻结

继续 Phase 3.12。

当前 Step 79 已完成：

```text
Regression Matrix
    ↓
13 categories
    ↓
28 registered files
    ↓
18 offline files
    ↓
15 DB files
```

并且：

```text
OBSERVABILITY_HTTP_ALLOWLIST
```

已经正式纳入 Matrix。

---

# Step 80 目标

本步骤只做一个事情：

> **冻结并验证 Regression Matrix 自身的 Contract，防止后续阶段出现 category / metadata / file registration 漂移。**

不要修改生产代码。

不要修改 Step 73 / 76 / 77 / 78 Contract。

不要修改 Observability HTTP Allowlist。

不要增加新的测试框架。

---

# 一、开始前阅读

先阅读：

```text
tests/test_assistant_trace_timeline_regression.py

docs/evaluation/phase-3.12-trace-timeline-regression.md

tests/test_observability_http_allowlist_regression.py

tests/test_observability_http_allowlist_audit.py
```

重点确认当前真实结构：

```text
FILES
CATEGORIES
_REQUIRED_CATEGORIES
FileSpec
_offline_suite
_db_suite
REPRESENTATIVE_CONTRACT_NODES
```

不要假设字段名称。

---

# 二、冻结 Matrix Contract

在现有：

```text
tests/test_assistant_trace_timeline_regression.py
```

中增加最小的 Contract Audit。

不要创建第二个 regression 文件。

不要复制整个 Matrix。

---

# 三、必须验证

## 1. Category 数量

当前应为：

```text
13 categories
```

测试：

```text
len(CATEGORIES) == 13
```

---

## 2. Required Categories

验证：

```text
set(_REQUIRED_CATEGORIES) == set(CATEGORIES)
```

确保：

```text
required category
```

与：

```text
actual category
```

完全一致。

---

## 3. Category 唯一性

验证：

```text
_REQUIRED_CATEGORIES
```

没有 duplicate。

---

## 4. File Registration 唯一性

验证：

```text
FILES
```

中：

```text
path
```

唯一。

不能出现：

```text
same test file
registered twice
```

---

# 四、FileSpec Metadata Contract

Step 79 已经扩展：

```text
path
db
coverage
network
llm
production_code
```

其中：

```text
db == db_required
coverage == scope
```

验证每个 FileSpec：

```text
path 非空
coverage 非空
db 是 bool
network 是 bool
llm 是 bool
production_code 是 bool
```

不要修改现有 FileSpec 结构。

---

# 五、Offline Category Contract

当前：

```text
OBSERVABILITY_HTTP_ALLOWLIST
```

下的四个文件必须全部：

```text
db = False
network = False
llm = False
production_code = False
```

继续保持 Step 79 语义。

不要把 allowlist audit 改成 DB test。

---

# 六、DB Category Contract

不要假设所有 category 都是 offline。

根据当前 Matrix：

```text
db=True
```

的 FileSpec 必须进入：

```text
_db_suite
```

而：

```text
db=False
```

的 FileSpec 必须进入：

```text
_offline_suite
```

验证：

```text
db=True → DB suite
db=False → offline suite
```

不要执行 DB。

这里仅验证 registration。

---

# 七、Category → File 完整性

对于：

```text
CATEGORIES[category]
```

每个 category：

必须：

```text
至少 1 个 registered file
```

并且：

```text
所有 file 都存在
```

验证：

```text
registered file → FILES
```

不能出现 dangling registration。

---

# 八、File → Category 完整性

反向验证：

```text
FILES
```

中的每个 file：

必须至少属于一个 category。

不要允许：

```text
orphan file
```

---

# 九、禁止 Self Registration

继续保持：

```text
test_assistant_trace_timeline_regression.py
```

不能把自己注册为 Matrix entry。

验证：

```text
regression collector
∉ FILES
```

---

# 十、代表性 Node

Step 79 已有：

```text
REPRESENTATIVE_CONTRACT_NODES
```

保持：

```text
只登记 node id
不复制测试断言
```

本阶段只验证：

1. node id 唯一
2. node 对应文件已经注册
3. node 可以被 `--collect-only` 找到

不要执行这些 node。

pytest 官方文档确认 `--collect-only` 只进行测试收集而不执行测试。

---

# 十一、不要做的事情

禁止：

```text
❌ 新建第二个 Regression Matrix
❌ 新建新的 category registry
❌ 复制 CATEGORIES
❌ 复制 FILES
❌ 复制 OBSERVABILITY_HTTP_ALLOWLIST
❌ 修改 Step 76
❌ 修改 Step 77
❌ 修改 Step 78
❌ 修改 Step 79 的语义
❌ 修改 backend
❌ DB 测试
❌ DeepSeek
❌ Network
```

尤其：

> Step 80 只验证 Matrix Contract，不增加业务测试。

---

# 十二、测试文件

继续使用：

```text
tests/test_assistant_trace_timeline_regression.py
```

不要新增第二个 Matrix audit 文件，除非当前结构已经明确要求拆分。

建议新增测试类：

```text
TestRegressionMatrixContract
```

测试数量控制在：

```text
8～12 个
```

不要堆测试。

---

# 十三、测试要求

至少覆盖：

```text
category count
required categories exact match
category uniqueness
file path uniqueness
FileSpec metadata validity
offline category metadata
db/offline suite partition
category → file completeness
file → category completeness
no self registration
representative node uniqueness
representative node collectability
```

---

# 十四、运行命令

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
RUN_DB_TESTS=1
```

不要：

```text
pytest -q
```

不要调用 DeepSeek。

不要访问网络。

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

并检查：

```powershell
git status --short
```

确认只修改：

```text
tests/test_assistant_trace_timeline_regression.py
```

以及如果确实需要：

```text
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

不要产生其他生产代码修改。

---

# 十六、完成报告

严格输出：

```text
Phase 3.12 Step 80 完成报告

1. Matrix Contract
- categories:
- registered files:
- offline files:
- DB files:

2. Category Contract
- required categories:
- duplicate categories:

3. FileSpec Contract
- metadata:
- duplicate paths:
- orphan files:

4. Category ↔ File 完整性
- category → file:
- file → category:

5. Representative Nodes
- count:
- unique:
- collectable:

6. Self Registration
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

11. Contract Changes
- Step 73:
- Step 76:
- Step 77:
- Step 78:
- Step 79:

12. 当前限制

Phase 3.12 Step 80 READY
Phase 3.12 Step 80 STOP
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
