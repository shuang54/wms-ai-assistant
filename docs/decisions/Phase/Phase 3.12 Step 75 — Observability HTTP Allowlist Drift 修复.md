# Phase 3.12 Step 75 — Observability HTTP Allowlist Drift 修复

## 一、阶段目标

修复 Step 74 发现的唯一测试基础设施漂移：

```text
tests/test_tool_observability_architecture_audit.py
```

中的：

```text
C25-13
only allowlisted tool observability HTTP API
```

当前 allowlist 未包含：

```text
/api/observability/assistant-timeline/{assistant_request_id}
```

本阶段只同步测试白名单。

**不修改生产代码。**

---

# 二、严格范围

允许修改：

```text
tests/test_tool_observability_architecture_audit.py
```

允许新增：

```text
无
```

禁止修改：

```text
backend/
docs/architecture.md
docs/evaluation/
DB schema
migration
API implementation
Trace
Timeline
Outcome
Tool Framework
RAG
T2SQL
LLM
```

不要顺便修改其它测试。

---

# 三、开始前阅读

阅读：

```text
tests/test_tool_observability_architecture_audit.py
```

定位：

```text
C25-13
```

确认当前 allowlist 的真实结构。

同时确认生产路由实际已经存在：

```text
GET /api/observability/assistant-timeline/{assistant_request_id}
```

不要修改路由。

---

# 四、最小修改

只将：

```text
/api/observability/assistant-timeline/{assistant_request_id}
```

加入现有：

```text
allowlisted tool observability HTTP API
```

要求：

* 使用现有 allowlist 数据结构
* 保持现有排序/格式风格
* 不新增新的判断逻辑
* 不改变 C25-13 的审计规则
* 不删除任何已有 allowlist 项

---

# 五、特别注意

不要因为这个问题：

```text
C25-13 failure
```

去修改：

```text
backend/app/api/
```

不要修改：

```text
main.py
```

不要修改：

```text
assistant_timeline.py
```

因为 Step 74 已经证明 Timeline API 本身正确。

这是：

```text
Test Allowlist Drift
```

不是：

```text
Production API Bug
```

---

# 六、测试

首先只运行：

```powershell
python -m pytest -q tests/test_tool_observability_architecture_audit.py
```

要求：

```text
0 failed
```

然后运行相关 Timeline Contract：

```powershell
python -m pytest -q tests/test_assistant_trace_timeline_contract.py
```

要求：

```text
19 passed
```

然后运行 Step 74 Regression：

```powershell
python -m pytest -q tests/test_assistant_trace_timeline_regression.py
```

要求：

```text
12 passed
2 skipped
0 failed
```

如果该测试当前数量因为环境变化不同，以实际结果为准，但：

```text
failed = 0
```

必须满足。

---

# 七、Compile

执行：

```powershell
python -m compileall -q backend tests scripts
```

要求：

```text
0 errors
```

---

# 八、Git Diff

执行：

```powershell
git status --short
git diff --stat
git diff -- tests/test_tool_observability_architecture_audit.py
git diff -- backend
```

必须确认：

```text
backend = 0
```

并且唯一代码修改：

```text
tests/test_tool_observability_architecture_audit.py
```

---

# 九、DB / Network

本阶段默认：

```text
DB writes = 0
Network = 0
DeepSeek = 0
```

不要：

```text
RUN_DB_TESTS=1
```

除非上述 targeted test 本身已经要求 DB。

如果该 audit 测试离线即可完成：

**不要主动打开 DB。**

---

# 十、不要修改 Phase 3.12 Contract

本阶段不得修改：

```text
Trace / Timeline Contract Baseline
Partial State Contract
Outcome Contract
Source ID Contract
Ordering Contract
Security Contract
```

Step 73 Baseline 必须保持原样。

---

# 十一、最终报告

完成后严格：

```text
Phase 3.12 Step 75 完成报告

1. 漂移问题
2. 修改文件
3. 修改内容
4. Production Code
5. C25-13
6. Contract Regression
7. Step 74 Regression
8. Compileall
9. DB / Network
10. Git Diff
11. 发现的问题
12. 当前限制

Phase 3.12 Step 75 READY
Phase 3.12 Step 75 STOP
```

如果 C25-13 仍失败：

```text
Phase 3.12 Step 75 NOT READY
```

只报告，不继续修其它问题。

---

# 十二、硬停止

完成后立即停止。

不要进入：

```text
Unified Timeline
event_id
sequence
span_id
pagination
Conversation
Memory
Agent
MCP
OpenTelemetry
```

等待下一步指令。
