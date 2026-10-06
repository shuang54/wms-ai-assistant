你现在开始执行：

# Phase 4.2 Step 6A — Commit Readiness Gate

项目：

`D:\coding\ai\wms-ai-assistant`

## 一、目标

Step 6 已经完成实现。

本步骤只做：

```text
Implementation Audit
→ Diff Audit
→ Test Audit
→ DB Audit
→ Commit Readiness
→ STOP
```

**本步骤不修改功能。**

---

# 二、先确认当前 Git 状态

执行：

```powershell
git status --short
git diff --stat
git diff -- backend
git diff -- tests
```

确认 Step 6 的范围：

### Backend 允许

```text
api/conversations.py
db/conversation_repository.py
db/init_db.py
db/models/conversation_turn.py
dto/conversation_api.py
services/chat_application_service.py
services/conversation_service.py
```

### Tests

允许：

```text
Step 6 相关既有测试修改
tests/test_conversation_message_idempotency.py
tests/test_conversation_message_idempotency_db.py
```

以及 Step 6 必须同步的 schema/contract assertions。

### 禁止出现

```text
Prompt
Router
Orchestrator
RAG
Tool
Text-to-SQL
Evidence
ConversationEvidence
Runtime Evidence
```

功能修改。

---

# 三、验证 G-1 已实际落实

确认：

```text
conversation_turn.idempotency_key
VARCHAR(128) NULL
```

以及：

```text
UNIQUE(conversation_id, idempotency_key)
```

确认：

```text
NULLS NOT DISTINCT
```

没有被使用。

确认：

```text
历史 NULL rows
```

仍然合法。

---

# 四、运行 Step 6 专项测试

先运行：

```powershell
python -m pytest -q tests/test_conversation_message_idempotency.py
```

要求：

```text
35 passed
0 failed
```

然后：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_conversation_message_idempotency_db.py
```

要求：

```text
14 passed
0 failed
```

如果实际数量变化，以真实结果为准，但不得有 failed。

---

# 五、运行 Conversation 相关回归

执行：

```powershell
python -m pytest -q tests/test_conversation_persistence_db.py
```

然后：

```powershell
python -m pytest -q tests/
```

DB：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

---

# 六、处理全量测试失败

当前已知：

```text
offline:
5931 passed / 5 failed

DB:
6661 passed / 6 failed
```

已知根因：

```text
test_11_backend_working_tree_is_unmodified
```

以及其下游：

```text
suite-green / Matrix baseline
```

这些属于：

```text
UNCOMMITTED WORKTREE GUARD
```

而不是 Step 6 功能失败。

因此：

### 禁止

```text
❌ skip
❌ xfail
❌ 删除 guard
❌ 修改 baseline
❌ 修改 suite-green 逻辑
❌ 修改 Matrix snapshot
❌ 回滚 Step 6
```

必须记录：

```text
Expected until commit
```

---

# 七、DB residue

执行 Step 6 专项 DB 测试后确认：

```text
step6-% residue = 0
```

同时确认：

```text
没有修改历史 Conversation
没有修改历史 Turn content
没有修改历史 Observation
没有产生 Evidence
```

---

# 八、Compile

执行：

```powershell
python -m compileall -q backend tests scripts
```

要求：

```text
0 errors
```

---

# 九、OpenAPI

检查：

```text
POST /api/conversations/{conversation_id}/messages
```

确认：

```text
Idempotency-Key
```

出现在：

```text
Header
```

而不是：

```text
Request Body
```

确认：

```text
max_length = 128
```

确认 response：

```text
route = nullable
```

确认 duplicate contract：

```text
200
```

same-key different-payload：

```text
409
```

---

# 十、Step 6 行为矩阵最终确认

必须确认：

| Scenario                            | Expected                        |
| ----------------------------------- | ------------------------------- |
| No key                              | historical independent behavior |
| First request + key                 | create USER → AI → ASSISTANT    |
| Same key + same payload + completed | 200 replay                      |
| Same key + different payload        | 409                             |
| USER exists + ASSISTANT absent      | retry                           |
| EMPTY                               | retry                           |
| FAILED exception                    | retry                           |
| TX2 failure                         | retry                           |
| Different conversation + same key   | independent                     |
| Same content + different key        | independent                     |
| Concurrent same key                 | one USER Turn                   |
| ASSISTANT idempotency_key           | NULL                            |

---

# 十一、Replay Contract

确认 duplicate：

```json
{
  "route": null,
  "content": "<persisted assistant content>",
  "data": null,
  "metadata": {
    "request_id": "<persisted assistant_request_id>",
    "idempotent_replay": true
  }
}
```

确认：

```text
AI execution = 0
```

确认：

```text
USER Turn count unchanged
ASSISTANT Turn count unchanged
```

禁止：

```text
route = replay
route = unknown
route = duplicate
```

---

# 十二、KL-1 / KL-2

最终审计必须保留：

### KL-1

```text
AI exactly-once execution
=
NOT GUARANTEED
```

原因：

```text
AI executes
→ process crashes
→ ASSISTANT Turn not committed
→ DB cannot distinguish
```

不得通过新增：

```text
execution_status
```

解决。

---

### KL-2

```text
Duplicate replay
```

只重放：

```text
content
request_id
idempotent_replay
```

不重放：

```text
route
data
outcome
```

Text-to-SQL result data loss：

```text
ACCEPTED LOSS
```

完整 Result Replay：

```text
OD-23 / Architecture B
```

未来再处理。

---

# 十三、Git Commit Readiness

如果：

```text
Step 6 tests = PASS
DB tests = PASS
compile = PASS
DB residue = 0
scope audit = PASS
```

则输出：

```text
COMMIT READY
```

**不要执行 git commit。**

因为当前任务只负责 readiness。

---

# 十四、如果发现功能问题

如果发现真正的 Step 6 功能问题：

```text
停止
```

报告：

```text
Problem
Impact
Root Cause
Required Fix
```

不要自行扩大范围。

---

# 十五、允许修改文件

本步骤原则上：

```text
Backend = 0
Tests = 0
DB = 0
API = 0
```

除非发现纯测试基础设施错误且能够证明属于 Step 6 必要修复。

否则不要修改任何文件。

---

# 十六、最终报告

严格输出：

```text
【Phase 4.2 Step 6A COMPLETE】

1. Step 6 Functional Tests
2. DB Tests
3. API Contract
4. Schema
5. Concurrency
6. Retry
7. Duplicate Replay
8. Context Retry
9. Security
10. KL-1
11. KL-2
12. DB Residue
13. Compile
14. Full Regression
15. Known Baseline/Working-tree Guard Failures
16. Scope Audit
17. Commit Readiness
```

最终只能是：

```text
COMMIT READY
```

或者：

```text
NOT READY
```

**不要执行 commit。**

**不要进入 Step 7。**

**完成后立即停止。**
