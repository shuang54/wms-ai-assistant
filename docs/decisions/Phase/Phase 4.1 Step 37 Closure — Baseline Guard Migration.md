# Phase 4.1 Step 37 Closure — Baseline Guard Migration

## 当前状态

Phase 4.1 Step 37 已完成真实 Persistence：

```text
Evidence ORM                 PASS
Annotation ORM               PASS
Repository                   PASS
Real PostgreSQL              PASS
DB Tests                     7 passed
Transaction                  PASS
Idempotency                  PASS
compileall                   PASS
```

Step 36 baseline：

```text
5916 passed
633 skipped
0 failed
```

Step 37 Full Regression：

```text
12 failed
5904 passed
640 skipped
0 errors
```

Regression Triage 已确认：

```text
12/12 = Step 37 related
0 = pre-existing
0 = test infrastructure
0 = unknown
```

其中：

```text
#1-#4
```

主要是 Regression Matrix 的级联失败。

```text
#5-#11
```

是 Step 36 历史 Architecture Guard 对“Persistence 不存在”的断言。

```text
#12
```

是 Step 37 production change 尚未 commit 导致的 working-tree guard。

---

# 一、唯一目标

完成 Step 37 的：

**Baseline Guard Migration + Regression Closure**

目标：

```text
Step 36 historical guards
        ↓
Step 37 scope-aware guards
        ↓
Full Regression = 0 failed
```

---

# 二、严格禁止

本任务禁止：

```text
修改 Evidence ORM
修改 Annotation ORM
修改 Repository
修改 Transaction
修改 Idempotency
修改 DB Schema
修改 AI Runtime
修改 Router
修改 Orchestrator
修改 RAG
修改 Tool
修改 Text-to-SQL
```

除非测试证明存在 Step 37 implementation bug。

本任务也禁止：

```text
Step 38
Provenance implementation
Review
Finalization
Conversation integration
```

---

# 三、修改历史 Guard

根据 Triage 结果定位：

```text
#5
#6
#7
#8
#9
#10
#11
```

这些测试中的“尚不存在”断言。

不要删除这些测试。

不要 skip。

不要降低断言强度。

把它们迁移为：

```text
Step 37 scope-aware architecture guards
```

---

# 四、Step 37 Frozen Production Scope

允许的新增 Production Evidence Persistence 模块必须严格限定为：

```text
backend/app/db/models/evidence_record.py
backend/app/db/models/evidence_annotation_record.py
backend/app/db/evidence_repository.py
```

以及：

```text
backend/app/db/models/__init__.py
```

中为上述 ORM 所需的注册。

除此之外：

不得出现新的：

```text
evidence API
evidence service
evidence workflow
annotation API
review service
finalization service
```

---

# 五、修改 #5 / #6

原来的语义：

```text
production 不允许出现 annotation identifiers
production 不允许出现 annotation modules
```

已经过时。

改成：

```text
Step 37 允许 annotation persistence
但只允许冻结的 Step 37 persistence modules
```

必须继续阻止：

```text
Annotation API
Annotation Service
Annotation Workflow
Review implementation
Finalization implementation
```

---

# 六、修改 #7 / #8

Rubric architecture guard 同样不能简单删除。

改为：

```text
Rubric / Evaluation production modules
仍然保持原有边界
```

同时允许 Step 37 的：

```text
Evidence
Annotation persistence
```

存在。

不要改变 Rubric 本身的 architecture contract。

---

# 七、修改 #9

原来的：

```text
dataset_version domain is preexisting t2sql only
```

已经被 Step 37 扩展。

改成：

```text
dataset_version allowed domains:

1. Existing Text-to-SQL usage
2. Step 37 Evidence persistence
```

禁止其他未经 Roadmap 授权的生产用途。

---

# 八、修改 #10

原来的：

```text
no evidence directories created
```

已经与 Step 37 冲突。

改成：

```text
only Step 37 frozen evidence persistence modules may exist
```

允许：

```text
evidence_record.py
evidence_annotation_record.py
evidence_repository.py
```

禁止：

```text
evidence_service.py
evidence_api.py
evidence_workflow.py
```

---

# 九、修改 #11

原测试：

```text
test_19_production_has_no_evidence_persistence_yet
```

不要删除。

改造成：

```text
Step 37 production evidence persistence scope guard
```

必须验证：

```text
Evidence Persistence exists
AND
implementation scope == Step 37 frozen scope
```

也就是说：

```text
PASS:
evidence_record.py
evidence_annotation_record.py
evidence_repository.py

FAIL:
additional unapproved evidence production layers
```

---

# 十、不要修改 #12

不要弱化：

```text
test_11_backend_working_tree_is_unmodified
```

Step 37 production code 本来就应该最终提交。

完成 Guard migration 后：

提交 Step 37 production/documentation changes。

然后重新运行 Full Regression。

---

# 十一、测试顺序

先：

```powershell
python -m pytest -q <affected-tests>
```

然后：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_evidence_persistence_db.py
```

然后：

```powershell
python -m pytest -q
```

确认：

```text
0 failed
0 errors
```

再运行现有：

```text
Regression Matrix
Offline Gate
compileall
```

---

# 十二、提交前检查

执行：

```powershell
git status --short
git diff --stat
git diff
```

确认：

```text
Step 37 production implementation
Step 37 tests
Step 37 evaluation
Step 37 decision
Step 37 triage / guard migration
```

没有无关修改。

---

# 十三、最终报告

严格：

```text
【Phase 4.1 Step 37 COMPLETE】

1. Implementation
2. Baseline Guard Migration
3. Evidence Persistence
4. Annotation Persistence
5. PostgreSQL
6. DB Tests
7. Regression
8. Matrix Gate
9. Security
10. Production Code Changes
11. DB Schema Changes
12. DB Test Writes
13. Production DB Writes
14. New Files
15. Modified Files
16. Problems Found
17. Problems Fixed
18. Current Limitations
19. Git Commit
20. Next Step
```

明确：

```text
Full Regression = 0 failed
```

并明确：

```text
Production DB Writes = 0
```

---

# STOP

如果 Full Regression 仍然失败：

**不要继续修改。**

报告剩余失败及根因。

如果：

```text
0 failed
```

则：

**Step 37 正式 COMPLETE。**

仍然不要进入 Step 38。
