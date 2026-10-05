你现在继续实现：

`D:\coding\ai\wms-ai-assistant`

# Phase 4.1 Step 38 Closure — Provenance Guard Migration

## 一、当前状态

Step 38 功能实现已经通过：

```text
Gate 1  20 passed
Gate 2  13 passed
Gate 5  compileall OK
```

Closure 当前：

```text
Full Regression
7 failed / 5909 passed / 646 skipped / 0 errors

Matrix
DRIFT / exit 1
```

Triage 已确认：

```text
Implementation Bug = 0
Test Bug = 0
Historical Guard = 3
Matrix Cascade = 4
Infrastructure = 0
Pre-existing = 0
```

因此现在只进行：

**Closure Guard Migration + Commit + Full Verification**

---

# 二、绝对禁止修改 Step 38 Production Logic

不得修改：

```text
backend/app/db/evidence_repository.py
```

中的 Step 38 实现逻辑。

不得修改：

```text
EvidenceProvenanceRow
EvidenceWithProvenance
validate_provenance()
get_by_id()
get_provenance()
```

不得修改：

```text
backend/app/db/models/evidence_record.py
```

不得新增 Provenance 表。

不得新增 Provenance ORM。

不得修改：

```text
AI Router
AI Orchestrator
RAG
Tool
Text-to-SQL
SQL Validator
SQL Executor
```

---

# 三、只修改两个历史 Provenance Guard

## Guard #1

文件：

```text
tests/test_conversation_context_real_evidence_import_architecture.py
```

测试：

```text
test_1d_annotation_and_provenance_still_absent
```

当前旧语义：

```text
production 不得出现 provenance
```

这个语义已经过时。

改为：

```text
Step 38 只允许冻结范围内存在 Provenance Persistence
```

允许：

```text
backend/app/db/evidence_repository.py
```

因为 Step 38 已经明确冻结该文件承载：

```text
EvidenceProvenanceRow
EvidenceWithProvenance
validate_provenance
get_provenance
```

但仍然禁止：

```text
provenance_service.py
provenance_api.py
provenance_workflow.py
```

以及任何新的 Provenance framework。

---

# 四、Guard #2

文件：

```text
tests/test_conversation_context_evidence_provenance_architecture.py
```

测试：

```text
test_1d_no_provenance_word_in_backend
```

当前旧语义：

```text
backend 不允许出现 provenance
```

迁移为：

```text
backend 允许 Step 38 冻结范围内的 provenance
```

允许：

```text
backend/app/db/evidence_repository.py
```

如果当前实现中 Provenance 相关标识只存在该文件，则 allowlist 应明确限定该文件。

继续禁止：

```text
provenance_service.py
provenance_api.py
provenance_workflow.py
provenance_manager.py
provenance_registry.py
```

以及其他未授权 Provenance production implementation。

---

# 五、不要扩大 Allowlist

特别注意：

不能写成：

```python
assert "provenance" in backend
```

也不能简单删除 assertion。

必须变成：

```text
发现 provenance
        ↓
是否属于 Step 38 冻结文件？
        ↓
YES → PASS
NO  → FAIL
```

即：

**Scope Guard，而不是 Disable Guard。**

---

# 六、Step 37 Scope 必须保持

Step 37 已冻结：

```text
backend/app/db/models/evidence_record.py
backend/app/db/models/evidence_annotation_record.py
backend/app/db/evidence_repository.py
backend/app/db/models/__init__.py
```

Step 38 在此基础上允许：

```text
backend/app/db/evidence_repository.py
```

承载 Provenance Persistence / Read Model。

不要把以下内容加入允许范围：

```text
evidence_service.py
evidence_api.py
evidence_workflow.py
annotation_service.py
annotation_api.py
annotation_workflow.py
review_service.py
review_api.py
finalization_service.py
finalization_api.py
```

---

# 七、不要修改 Matrix Baseline

绝对禁止修改：

```text
Matrix baseline
Matrix FILES
OFFLINE_EXECUTION_DRIFT baseline
MATRIX_STATUS_DRIFT baseline
```

当前 Matrix DRIFT 是：

```text
offline suite
    ↓
working-tree guard #3
    ↓
suite failed
    ↓
Matrix DRIFT
```

提交 Step 38 production changes 后：

```text
working tree clean
    ↓
offline suite green
    ↓
Matrix PASS
```

因此：

**不要通过修改 baseline 来消除 Drift。**

---

# 八、不要修改 Working-tree Guard

失败：

```text
test_11_backend_working_tree_is_unmodified
```

是因为：

```text
backend/app/db/evidence_repository.py
```

尚未 commit。

这个测试本身正确。

不要：

```text
skip
```

不要：

```text
allow uncommitted Step 38
```

不要：

```text
修改 baseline
```

正确解决方式：

**提交 Step 38 production changes。**

---

# 九、Commit 前检查

执行：

```powershell
git status --short
```

确认当前 Step 38 变更。

重点应该包括：

```text
backend/app/db/evidence_repository.py
tests/test_evidence_provenance_persistence_db.py
```

以及 Step 38 文档。

如果存在明显不属于 Step 38 的修改：

**停止并报告。**

不要一起 commit。

---

# 十、先跑局部测试

执行：

```powershell
python -m pytest -q `
tests/test_conversation_context_real_evidence_import_architecture.py `
tests/test_conversation_context_evidence_provenance_architecture.py
```

必须：

```text
0 failed
```

然后：

```powershell
python -m pytest -q tests/test_evidence_persistence_boundary.py
```

必须：

```text
20 passed
```

---

# 十一、DB Tests

执行：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q `
tests/test_evidence_persistence_db.py `
tests/test_evidence_provenance_persistence_db.py
```

必须：

```text
13 passed
```

如果实际数量发生变化，以真实结果记录。

必须：

```text
Production DB Writes = 0
```

---

# 十二、Full Regression

执行：

```powershell
python -m pytest -q
```

目标：

```text
0 failed
0 errors
```

必须记录：

```text
passed
failed
skipped
errors
exit code
```

---

# 十三、Matrix Gate

执行：

```powershell
python scripts/run_matrix_gate.py
```

必须：

```text
Status: PASS
Exit code: 0
```

不得修改 Matrix baseline。

---

# 十四、Compile

执行：

```powershell
python -m compileall -q backend
```

必须：

```text
OK
```

---

# 十五、最终检查 Production Diff

执行：

```powershell
git diff --stat
git diff -- backend/app/db/evidence_repository.py
```

确认：

Step 38 实现没有被 Closure Migration 意外修改。

Closure 本轮允许修改的测试文件只有：

```text
tests/test_conversation_context_real_evidence_import_architecture.py
tests/test_conversation_context_evidence_provenance_architecture.py
```

除此之外，如果出现额外修改：

**先报告。**

---

# 十六、Commit

确认所有 Gate：

```text
Full Regression = 0 failed
Matrix = PASS
compileall = OK
```

之后提交：

```powershell
git add `
backend/app/db/evidence_repository.py `
tests/test_evidence_provenance_persistence_db.py `
tests/test_conversation_context_real_evidence_import_architecture.py `
tests/test_conversation_context_evidence_provenance_architecture.py `
docs/evaluation/ `
docs/decisions/
```

但是：

**只 add 实际属于 Step 38 的文件。**

不要 blindly add 所有 docs。

然后：

```powershell
git status --short
```

确认 staging 内容。

Commit：

```powershell
git commit -m "feat: Phase 4.1 Step 38 Provenance Persistence"
```

不要 push。

不要 PR。

不要 merge。

---

# 十七、Commit 后验证

执行：

```powershell
git status --short
git log -2 --oneline
```

要求：

```text
working tree clean
```

如果仍有：

```text
Closure / Triage
```

等 untracked 文档：

不要为了 clean 而删除。

如果这些文件是本阶段流程记录，则可以单独判断是否应该纳入 Step 38 documentation。

如果无法确定：

**停止并报告。**

---

# 十八、最终 Closure 验收

只有同时满足：

```text
Architecture Guards = PASS
Step 37 DB Tests = PASS
Step 38 DB Tests = PASS
Full Regression = 0 failed
Matrix = PASS
compileall = PASS
Working Tree = clean
Commit = created
Production DB Writes = 0
```

才可以宣布：

```text
Phase 4.1 Step 38 COMPLETE
```

---

# 十九、最终报告格式

严格按照：

```text
【Phase 4.1 Step 38 COMPLETE】

1. Provenance Contract
2. Current Implementation Audit
3. Schema Decision
4. ORM / Read Model
5. Repository
6. Transaction
7. Idempotency
8. Guard Migration
9. PostgreSQL
10. DB Tests
11. Security
12. Regression
13. Matrix Gate
14. Production Code Changes
15. DB Schema Changes
16. DB Test Writes
17. Production DB Writes
18. New Files
19. Modified Files
20. Problems Found
21. Problems Fixed
22. Current Limitations
23. Git Commit
24. Next Step
```

必须明确：

```text
Production DB Writes = 0
```

---

# 二十、如果 Full Regression 仍失败

如果：

```text
failed > 0
```

或者：

```text
Matrix != PASS
```

立即停止。

不要进入 Step 39。

报告：

```text
Step 38 Closure = BLOCKED

Remaining failures:
...

Root cause:
...

Production DB Writes:
0
```

---

# 二十一、STOP

如果全部 Gate 通过：

**Step 38 COMPLETE。**

但：

**不要进入 Step 39。**

等待下一条指令。

不要：

```text
push
PR
merge
Agent
MCP
Workflow
Memory
Chat API
```
