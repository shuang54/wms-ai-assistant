# Phase 4.1 Step 38 — Closure Triage

当前 Step 38 实现已经完成，但 Closure 被 Full Regression / Matrix Gate 阻塞。

当前结果：

```text
Gate 1  test_evidence_persistence_boundary.py
20 passed

Gate 2  Step 37 + Step 38 DB tests
13 passed

Gate 5  compileall
OK

Gate 3  Full Regression
7 failed / 5909 passed / 646 skipped

Gate 4  Matrix Gate
Status: DRIFT / exit 1
```

## 一、现在禁止修改代码

本轮只做：

```text
Failure Triage
Architecture Guard Analysis
Matrix Drift Analysis
```

禁止：

```text
修改 EvidenceProvenance 实现
修改 EvidenceRepository 核心逻辑
修改 EvidenceRecord Schema
删除测试
skip 测试
降低 Guard 强度
修改 AI Router
修改 AI Orchestrator
修改 RAG
修改 Tool
修改 Text-to-SQL
修改 SQL Validator
修改 SQL Executor
进入 Step 39
```

---

# 二、首先完整收集 7 个失败

执行：

```powershell
python -m pytest -q
```

如果输出过长，使用：

```powershell
python -m pytest -q --tb=short
```

记录全部 7 个失败：

```text
#1
test:
file:
line:
assertion:
failure:

#2
...

#7
...
```

**不要只看最后的 summary。**

必须拿到每一个失败测试的：

```text
test name
file
line
assertion
failure reason
```

---

# 三、逐个判断 Failure Category

每个失败必须归类为：

```text
A = Step 38 implementation bug
B = Step 38 test bug
C = Historical architecture guard
D = Matrix baseline drift
E = Infrastructure failure
F = Pre-existing failure
G = Unknown
```

必须提供证据。

格式：

```text
Failure #N
Category:
Evidence:
Root cause:
Step 38 related: YES / NO
Required fix:
```

---

# 四、重点检查历史 Architecture Guards

Step 37 已经证明：

项目存在一类历史 Guard：

```text
“生产环境不应该存在 Evidence Persistence”
```

Step 37 将其迁移为：

```text
“生产环境只能存在冻结的 Step 37 Evidence Persistence”
```

现在 Step 38 又增加：

```text
EvidenceProvenanceRow
EvidenceWithProvenance
validate_provenance()
get_provenance()
```

因此必须重点检查：

```text
tests/
```

中是否存在类似：

```text
no provenance
no provenance persistence
no provenance model
no provenance repository
no dataset_version
no source_type
no evidence provenance
```

之类的历史 Guard。

搜索：

```powershell
Select-String -Path tests\*.py -Pattern `
"EvidenceProvenance|provenance|dataset_version|source_type|evidence_record|evidence_repository"
```

如果项目测试存在递归目录：

```powershell
Get-ChildItem tests -Recurse -Filter *.py |
    Select-String -Pattern `
    "EvidenceProvenance|provenance|dataset_version|source_type|evidence_record|evidence_repository"
```

---

# 五、特别检查 Step 36 / Step 37 历史审计快照

重点检查：

```text
tests/test_evidence_persistence_boundary.py
tests/test_conversation_context_evidence_provenance.py
tests/test_conversation_context_evidence_provenance_architecture.py
tests/test_conversation_context_annotation_workflow_architecture.py
tests/test_conversation_context_evaluation_rubric_architecture.py
```

以及所有包含：

```text
provenance
evidence
dataset_version
source_type
```

的 architecture guard。

判断它们到底是在验证：

### 错误旧语义

```text
生产代码中不能存在 Provenance Persistence
```

还是：

### 正确的新语义

```text
Step 38 只允许冻结的 Provenance Persistence
```

如果是前者：

**不要删除测试。**

迁移为 Scope Guard。

---

# 六、Step 38 冻结 Production Scope

当前 Step 38 已经实现的正式生产范围暂定为：

```text
EvidenceRecord
    └── dataset_version
    └── source_type

EvidenceProvenanceRow
    └── dataset_version
    └── source_type

EvidenceWithProvenance
    └── Evidence
    └── Provenance

EvidenceRepository
    ├── validate_provenance()
    ├── get_by_id()
    └── get_provenance()
```

以及 Step 37 已冻结：

```text
backend/app/db/models/evidence_record.py
backend/app/db/models/evidence_annotation_record.py
backend/app/db/evidence_repository.py
```

**注意：**

`EvidenceProvenanceRow` 和 `EvidenceWithProvenance` 是 Step 38 新增的 Read Model。

不要把它们误判成：

```text
Provenance Service
Provenance API
Provenance Workflow
```

它们只是 persistence/read boundary。

---

# 七、继续保持禁止范围

即使 Step 38 完成，下面这些仍然必须被 Guard 禁止：

```text
evidence_service.py
evidence_api.py
evidence_workflow.py

provenance_service.py
provenance_api.py
provenance_workflow.py

annotation_service.py
annotation_api.py
annotation_workflow.py

review_service.py
review_api.py
finalization_service.py
finalization_api.py
```

如果这些不存在：

**不要创建。**

---

# 八、检查 Matrix DRIFT

执行：

```powershell
python scripts/run_matrix_gate.py
```

记录：

```text
Status
Exit code
具体 drift 项
```

重点判断：

```text
Matrix expected state
        ↓
Step 38 当前合法新增能力
```

是否只是 Matrix 仍然认为：

```text
Provenance Persistence = forbidden
```

如果是：

属于：

```text
D = Matrix baseline drift
```

而不是生产代码 bug。

---

# 九、禁止为了 Matrix 通过而删除 Step 38

如果 Matrix 发现：

```text
Provenance persistence
```

仍然被历史规则判定为 forbidden：

**不能删除 Step 38 实现。**

正确方向是：

```text
Old:
Provenance Persistence = forbidden

New:
Provenance Persistence =
allowed only within Step 38 frozen scope
```

保持其他 Provenance 扩展仍然禁止。

---

# 十、检查是否存在真正的 Step 38 功能问题

特别检查：

### 1. Provenance 不完整

```text
dataset_version = None
source_type = None
```

是否正确拒绝。

### 2. Padding

例如：

```text
" v1 "
```

是否正确拒绝。

### 3. 非法 source_type

是否按照 Step 27 contract 拒绝。

### 4. Read Model

确认：

```text
EvidenceWithProvenance
```

没有：

```text
Session
Connection
Engine
SQLAlchemy Result
ORM object
```

### 5. Transaction

确认：

```text
Evidence
+
Provenance
```

仍然属于同一个 Repository transaction。

SQLAlchemy 2.x 明确要求数据库写操作具有明确的 transaction 边界，`Session.begin()` context manager 正适合这种原子操作范围。

---

# 十一、最终输出

本轮只输出：

```text
【Phase 4.1 Step 38 — Closure Triage】

1. Full Regression
2. 7 Failures
3. Failure Classification
4. Historical Guard Findings
5. Matrix Drift
6. Step 38 Production Code Assessment
7. Required Fixes
8. Production Code Changes
9. DB Changes
10. Production DB Writes
11. Recommendation
12. STOP
```

最后必须明确：

```text
Step 38 Closure:
BLOCKED / READY FOR CLOSURE

Production DB Writes:
0

Step 39:
NOT STARTED
```

如果确认 7 个失败全部属于：

```text
Historical Guard
+
Matrix Baseline Drift
```

那么下一轮再做 **Step 38 Closure Guard Migration**。

如果发现任何真正的生产实现 Bug：

**先停止，不要自行修复，报告具体根因。**

本轮执行完后：

# STOP

不要进入 Step 39。
