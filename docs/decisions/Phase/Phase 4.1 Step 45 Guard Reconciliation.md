你现在开始执行：

# Phase 4.1 Step 45 Guard Reconciliation

## Scope-aware Guard Migration

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、任务目标

Step 45 已经完成真实的 Conversation ↔ Evidence 持久化：

```text
backend/app/db/models/conversation_evidence.py
backend/app/db/conversation_evidence_repository.py
```

当前失败不是 Step 45 生产实现问题，而是历史 Guard 的**显式文件白名单没有同步更新**。

本阶段唯一目标：

> 将现有 Guard 从旧的静态文件集合迁移为能够正确识别 Step 45 新增 ConversationEvidence 持久化模块的 scope-aware Guard。

必须保持 Guard 的安全强度。

---

# 二、开始前先阅读

不要直接修改。

先阅读：

```text
tests/test_conversation_architecture_contract.py
tests/test_conversation_model_contract.py
tests/test_conversation_persistence_contract.py
tests/test_evidence_persistence_boundary.py
```

同时阅读 Step 45 新增文件：

```text
backend/app/db/models/conversation_evidence.py
backend/app/db/conversation_evidence_repository.py
```

以及：

```text
backend/app/db/models/__init__.py
```

确认实际文件内容和 Guard 当前意图。

---

# 三、当前已知失败

Step 45 后出现 6 个新的 Guard whitelist failures：

```text
tests/test_conversation_architecture_contract.py::test_5a

tests/test_conversation_model_contract.py::test_8a
tests/test_conversation_model_contract.py::test_8c

tests/test_conversation_persistence_contract.py::test_15a
tests/test_conversation_persistence_contract.py::test_16d

tests/test_evidence_persistence_boundary.py::test_19
```

它们的共同原因：

历史 Guard 的显式 production module / file whitelist 尚未包含：

```text
backend/app/db/models/conversation_evidence.py
backend/app/db/conversation_evidence_repository.py
```

---

# 四、允许修改

只允许修改：

```text
tests/test_conversation_architecture_contract.py
tests/test_conversation_model_contract.py
tests/test_conversation_persistence_contract.py
tests/test_evidence_persistence_boundary.py
```

并且只允许修改：

**与 Step 45 新增两个文件对应的显式 Guard 白名单 / scope 分类。**

不得进行其他重构。

---

# 五、必须加入的文件

Conversation 领域允许新增：

```text
backend/app/db/models/conversation_evidence.py
backend/app/db/conversation_evidence_repository.py
```

必须按照当前 Guard 的真实分类方式分别加入。

不要简单粗暴地把整个：

```text
backend/app/db/
```

加入白名单。

不要把：

```text
backend/app/db/models/
```

整体加入白名单。

---

# 六、绝对禁止弱化 Guard

禁止：

```text
*
**/*
```

禁止：

```python
startswith(...)
```

这种可能导致整个目录被放行的宽泛规则。

禁止：

```python
if "conversation" in filename:
    allow
```

禁止删除 Guard assertion。

禁止把：

```text
assert actual == expected
```

改成：

```text
assert expected <= actual
```

除非原 Guard 本身就是这种语义并且 Step 45 已明确要求。

禁止删除未知文件检测。

禁止降低 unauthorized file detection 能力。

---

# 七、Conversation Architecture Guard

检查：

```text
tests/test_conversation_architecture_contract.py
```

找到：

```text
_DECLARED_CONVERSATION_MODULES
```

或者等价的显式模块白名单。

将 Step 45 两个真实模块按现有命名方式加入：

```text
backend/app/db/models/conversation_evidence.py
backend/app/db/conversation_evidence_repository.py
```

不要修改 Conversation 架构契约本身。

不要新增其他 Conversation module。

---

# 八、Conversation Model Guard

检查：

```text
tests/test_conversation_model_contract.py
```

重点：

```text
test_8a
test_8c
```

如果存在：

```text
_DECLARED_CONVERSATION_MODULES
```

或者：

```text
_ALLOWED_CONVERSATION_MODEL_FILES
```

将：

```text
conversation_evidence.py
```

加入正确的显式 whitelist。

如果 Guard 同时检查 repository module：

只在现有 Guard 语义允许的情况下加入：

```text
backend/app/db/conversation_evidence_repository.py
```

不要改变：

```text
Conversation
ConversationTurn
```

的字段契约。

不要修改 ORM model。

不要修改 repository。

---

# 九、Conversation Persistence Guard

检查：

```text
tests/test_conversation_persistence_contract.py
```

重点：

```text
test_15a
test_16d
```

找到 production module whitelist。

加入：

```text
backend/app/db/models/conversation_evidence.py
backend/app/db/conversation_evidence_repository.py
```

但必须保持：

```text
Conversation persistence
≠
Evidence persistence
```

的边界。

ConversationEvidence 是：

```text
Conversation ↔ Evidence Association
```

不是新的 Conversation entity。

---

# 十、Evidence Persistence Boundary Guard

这是本次最重要的 Guard。

检查：

```text
tests/test_evidence_persistence_boundary.py
```

重点：

```text
test_19
```

当前 Evidence persistence boundary 发现了：

```text
conversation_evidence.py
conversation_evidence_repository.py
```

因此失败。

必须修改 Guard，使它能够区分：

### Evidence / Annotation persistence

```text
backend/app/db/models/evidence.py
backend/app/db/models/evidence_annotation.py
backend/app/db/evidence_repository.py
```

与：

### Conversation ↔ Evidence Association

```text
backend/app/db/models/conversation_evidence.py
backend/app/db/conversation_evidence_repository.py
```

后者：

**不属于 Evidence / Annotation 核心 persistence boundary。**

但是：

**不能简单从扫描结果中删除所有 conversation_evidence 文件。**

必须明确声明：

```text
ConversationEvidence is an association persistence module,
not an Evidence / Annotation persistence module.
```

Guard 仍然必须继续阻止未来未经声明的其他 Evidence persistence 文件。

---

# 十一、Guard 安全性测试

修改完成后，至少人工确认以下逻辑仍然成立：

```text
新增 conversation_evidence.py
→ PASS

新增 conversation_evidence_repository.py
→ PASS

新增 evidence_fake_repository.py
→ FAIL

新增 random_evidence_service.py
→ FAIL

新增 random_conversation_repository.py
→ FAIL

新增未声明 persistence module
→ FAIL
```

如果当前 Guard 使用固定 fixture / monkeypatch 测试未知文件，则不要删除这些测试。

如果没有现成 negative case：

**不要为了测试而大规模重构 Guard。**

只确认现有 assertion 没有被弱化。

---

# 十二、禁止修改生产代码

本任务：

```text
backend/
```

禁止修改。

特别禁止：

```text
backend/app/db/models/conversation_evidence.py
backend/app/db/conversation_evidence_repository.py
backend/app/db/models/__init__.py
```

禁止修改。

---

# 十三、禁止修改数据库

本阶段：

```text
DB schema = 0
DB migration = 0
DB write = 0
```

不要：

```text
CREATE TABLE
ALTER TABLE
DROP TABLE
INSERT
UPDATE
DELETE
```

本阶段只是 Guard reconciliation。

---

# 十四、禁止修改 Baseline

不要修改：

```text
baseline
summary
matrix expected result
```

不要为了让 Matrix 变绿而修改 baseline。

如果 Matrix 仍然因为：

```text
working-tree guard
```

失败，这是正常的。

Step 45 尚未进行 Step 50 Git closeout。

---

# 十五、执行测试

先执行精准 Guard：

```powershell
python -m pytest -q `
  tests/test_conversation_architecture_contract.py `
  tests/test_conversation_model_contract.py `
  tests/test_conversation_persistence_contract.py `
  tests/test_evidence_persistence_boundary.py
```

预期：

```text
0 failed
```

然后：

```powershell
python -m pytest -q
```

记录：

```text
passed
failed
skipped
```

---

# 十六、DB Regression

再执行：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

注意：

不要因为 Guard Reconciliation 而修改任何 DB 测试。

如果仍然出现历史：

```text
working-tree guard
offline baseline
DB residue
```

相关失败：

先分类。

不要擅自修改。

---

# 十七、Matrix

执行：

```powershell
python scripts/run_matrix_gate.py
```

如果失败：

区分：

### 本阶段问题

例如：

```text
Guard whitelist failure
```

需要继续处理。

### Step 45 Git 状态导致的历史问题

例如：

```text
test_11_backend_working_tree_is_unmodified
```

保持记录。

不要为了 Matrix 通过而提前 commit。

不要修改 working-tree guard。

---

# 十八、Compile

执行：

```powershell
python -m compileall -q backend tests scripts
```

要求：

```text
0 errors
```

---

# 十九、Git

只检查：

```powershell
git status --short
git diff --stat
git diff
```

禁止：

```text
git add
git commit
git push
git checkout
git reset
git clean
```

本阶段不进行 Git closeout。

---

# 二十、最终检查

确认：

```text
Production code changed = 0
DB schema changed = 0
DB writes = 0
Baseline changed = 0
Guard weakening = NO
Wildcard whitelist = NO
Unknown-file detection preserved = YES
```

---

# 二十一、完成后报告

严格按照：

```text
【Phase 4.1 Step 45 Guard Reconciliation COMPLETE】

1. Guard 修改
- test_conversation_architecture_contract.py
- test_conversation_model_contract.py
- test_conversation_persistence_contract.py
- test_evidence_persistence_boundary.py

2. 新增白名单
- conversation_evidence.py
- conversation_evidence_repository.py

3. Guard 强度
- wildcard 放行：NO
- directory-wide 放行：NO
- assertion weakening：NO
- unauthorized file detection：PRESERVED

4. 精准 Guard 测试
- passed：
- failed：

5. Full Regression
- passed：
- failed：
- skipped：

6. DB Regression
- passed：
- failed：
- skipped：

7. Matrix
- 结果：
- 是否存在 working-tree historical failure：

8. Compile
- 结果：

9. Production Code
- changed：0

10. DB Schema
- changed：0

11. DB Writes
- 0

12. Baseline
- changed：0

13. Git
- commit：NO
- push：NO
- PR：NO

14. 当前状态
- Step 45 Guard Reconciliation：COMPLETE
- Step 46：NOT STARTED

STOP.
```

完成后立即停止。

**不要进入 Phase 4.1 Step 46。**
