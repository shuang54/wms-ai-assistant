你现在开始执行：

# Phase 4.2 Step 6B — Commit Closeout & Verification

## 一、目标

Step 6 Implementation 与 Step 6A Commit Readiness Gate 已全部通过。

现在只完成：

```text
Step 6 Commit
    ↓
Commit Verification
    ↓
Push / Remote Verification
    ↓
Regression Guard Re-check
    ↓
STOP
```

**本步骤不允许继续开发 Step 7。**

---

# 二、Step 6A 已确认事实

Step 6A 已经确认：

```text
Functional Tests:
35 passed / 0 failed

DB Tests:
14 passed / 0 failed

API Contract:
PASS

OpenAPI:
PASS

DB Schema:
PASS

UNIQUE(conversation_id,idempotency_key):
PASS

NULL compatibility:
PASS

Concurrency:
PASS

Retry:
PASS

Duplicate Replay:
PASS

Security:
PASS

DB residue:
0

compileall:
0 errors

Scope Audit:
PASS
```

Step 6 production backend 修改范围已经冻结为：

```text
backend/app/api/conversations.py
backend/app/db/conversation_repository.py
backend/app/db/init_db.py
backend/app/db/models/conversation_turn.py
backend/app/dto/conversation_api.py
backend/app/services/chat_application_service.py
backend/app/services/conversation_service.py
```

测试修改：

```text
15 existing Step 6 test files
2 new Step 6 test files
```

文档：

```text
Roadmap §24
Step 6 task / evaluation documentation
```

不得扩大范围。

---

# 三、第一步：确认 Git 状态

执行：

```powershell
git status --short
git diff --stat
git diff -- backend
git diff -- tests
```

确认：

```text
只有 Step 6 允许范围内修改
```

如果发现任何新的、未授权修改：

**立即停止并报告。**

不要自动删除用户修改。

---

# 四、Commit 前最后 Scope Audit

再次确认：

```text
Prompt = 0
Router = 0
AIOrchestrator = 0
RAG = 0
Tool = 0
Text-to-SQL = 0
SQL Validator = 0
SQL Executor = 0
Evidence = 0
ConversationEvidence = 0
Runtime Evidence = 0
```

以下文件不得出现 Step 6 之外的修改：

```text
backend/app/services/ai_router_service.py
backend/app/services/ai_orchestrator_service.py
backend/app/services/rag_service.py
backend/app/services/text_to_sql_service.py
backend/app/services/sql_validator_service.py
backend/app/services/sql_executor_service.py
```

如果出现：

```text
STOP
```

不要提交。

---

# 五、Commit 前运行 Step 6 核心测试

执行：

```powershell
python -m pytest -q tests/test_conversation_message_idempotency.py
```

必须：

```text
35 passed
0 failed
```

然后：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_conversation_message_idempotency_db.py
```

必须：

```text
14 passed
0 failed
```

如果失败：

**STOP，不提交。**

---

# 六、Commit

只有上述检查全部通过后才允许 commit。

Commit message：

```text
feat: Phase 4.2 Step 6 message idempotency
```

执行：

```powershell
git add backend/app/api/conversations.py `
  backend/app/db/conversation_repository.py `
  backend/app/db/init_db.py `
  backend/app/db/models/conversation_turn.py `
  backend/app/dto/conversation_api.py `
  backend/app/services/chat_application_service.py `
  backend/app/services/conversation_service.py
```

以及已经确认属于 Step 6 的测试与文档文件。

然后：

```powershell
git status --short
```

确认 staged 内容仍然只属于 Step 6。

最后：

```powershell
git commit -m "feat: Phase 4.2 Step 6 message idempotency"
```

---

# 七、Commit 后验证

执行：

```powershell
git status --short
git log -1 --oneline
git show --stat --oneline HEAD
```

要求：

```text
working tree clean
HEAD = Step 6 commit
```

不得有新的未提交生产修改。

---

# 八、重新验证原先 Working-tree Guard

Step 6A 已经确认此前：

```text
test_11_backend_working_tree_is_unmodified
```

失败的唯一原因是：

```text
Step 6 尚未 commit
```

现在 commit 完成后重新运行：

```powershell
python -m pytest -q
```

目标：

确认之前由：

```text
working-tree dirty
```

导致的 guard failure 消失。

---

# 九、DB Regression

执行：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

重点观察：

```text
test_11_backend_working_tree_is_unmodified
test_offline_gate_suite_is_green
test_real_offline_suite_execution_summary
test_baseline_matches_step89_actual_execution
test_gate_passes_when_current_matches_baseline
test_db_residue_is_zero
```

不要修改：

```text
baseline
guard
skip
xfail
```

如果这些测试仍然失败：

**不要修测试。**

只记录：

```text
remaining failure
root cause
whether pre-existing
```

然后 STOP。

---

# 十、Step 6 Commit 后 DB Residue

重新确认：

```text
conversation = 0
conversation_turn = 0
step6-% = 0

evidence_record = 0
conversation_evidence = 0
evidence_annotation_record = 0
assistant_outcome_record = 0
```

不得：

```text
TRUNCATE
修改历史数据
修改 baseline
```

---

# 十一、Compile

执行：

```powershell
python -m compileall -q backend tests scripts
```

必须：

```text
0 errors
```

---

# 十二、Remote

如果当前分支已经配置 remote/upstream：

先执行：

```powershell
git branch --show-current
git remote -v
git status --short
```

确认后：

```powershell
git push
```

如果没有 upstream：

**不要自行创建新的远程分支策略。**

报告当前状态即可。

---

# 十三、Push 后验证

执行：

```powershell
git status --short
git log -1 --oneline
git rev-parse HEAD
git rev-parse origin/$(git branch --show-current)
```

要求：

```text
HEAD == origin/current-branch
working tree clean
```

如果当前 shell 不支持最后一条命令，使用实际分支名执行等价命令。

---

# 十四、Step 6 最终边界

Step 6 到此正式关闭：

```text
Message Idempotency
        ↓
Idempotency-Key
        ↓
Conversation-scoped UNIQUE
        ↓
USER Turn reuse
        ↓
AI execution
        ↓
ASSISTANT Turn
        ↓
Duplicate Message Replay
        ↓
409 Conflict / In-flight
```

已接受限制：

```text
KL-1:
AI exactly-once execution NOT GUARANTEED

KL-2:
Duplicate Replay 不重放完整 AI Result
```

完整 Result Replay：

```text
OD-23
Architecture B
Future
```

不得在本步骤解决。

---

# 十五、禁止

本步骤禁止：

```text
❌ Step 7
❌ Conversation Context 扩展
❌ Evidence Runtime
❌ Result Replay
❌ Execution Status
❌ Agent
❌ MCP
❌ Workflow
❌ Memory
❌ 修改 Prompt
❌ 修改 Router
❌ 修改 Orchestrator
❌ 修改 RAG
❌ 修改 Tool
❌ 修改 Text-to-SQL
❌ 修改 Validator
❌ 修改 Executor
❌ 修改 Baseline
❌ 修改 Guard
```

---

# 十六、最终报告

完成后严格输出：

```text
【Phase 4.2 Step 6 COMMIT CLOSEOUT】

1. Commit
- commit：
- message：

2. Working Tree
- clean / dirty：

3. Scope
- Backend：
- Tests：
- Docs：
- AI Runtime changes：

4. Step 6 Tests
- Functional：
- DB：

5. Full Regression
- Offline：
- DB：

6. DB Residue
- result：

7. Compile
- result：

8. Remote
- branch：
- upstream：
- HEAD：
- origin：

9. Known Limitations
- KL-1：
- KL-2：
- OD-23：

10. Step 6 Status
- COMMITTED
- PUSHED
- VERIFIED

11. Next
- Step 7 READY

然后立即 STOP。
```

**不要执行 Step 7。**
**不要开始新的代码设计。**
**不要继续扩展 Phase 4.2。**
