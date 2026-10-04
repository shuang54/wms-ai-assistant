继续 Phase 4.1。

当前：

```text
Step 31 — Real Evidence Import Contract
    READY / 已合并

Step 32 — Real Annotation Execution Contract
    READY
    39 passed
    Full Regression = 5896 passed / 633 skipped / 0 failed
    Production Code = 0
    DB = 0
    Network = 0
    LLM = 0
    G3 = BLOCKED
    G4 = BLOCKED
```

现在只执行：

# Phase 4.1 Step 33：Real Annotation Execution Release Readiness Audit

本步骤不是新增业务能力。

唯一目标：

> **确认 Step 32 是否已经达到可提交状态，并检查是否存在越界修改、旧 Contract 污染、测试依赖错误或文档不一致。**

---

# 一、严格禁止

本步骤禁止：

```text
修改 backend/
修改 Step 28/29/30/31 生产或 Contract 语义
修改 Annotation compare/resolve/finalize 核心逻辑
修改 G1/G2/G3/G4 状态
启动真实人工标注
读取真实生产 Evidence
DeepSeek
SiliconFlow
DB
Network
```

禁止：

```text
新增 Annotation API
新增 Annotation UI
新增 Annotation DB
新增 Selection Strategy
新增 ContextSelector
```

不要新增业务代码。

---

# 二、只检查当前 Step 32 产物

检查：

```text
tests/test_real_annotation_execution_contract.py

tests/fixtures/conversation_context/real_annotation_execution_cases.yaml

docs/evaluation/Phase 4.1 Step 32 — Real Annotation Execution Contract.md

docs/decisions/Phase/Phase 4.1 Step 32：Real Annotation Execution Contract.md
```

---

# 三、检查 Git Diff Scope

执行：

```powershell
git status --short
git diff --stat
git diff -- backend
git diff -- tests/test_real_annotation_execution_contract.py
git diff -- tests/fixtures/conversation_context/real_annotation_execution_cases.yaml
git diff -- docs/evaluation/
git diff -- docs/decisions/
```

要求：

```text
backend diff = 0
```

允许：

```text
Step 32 test
Step 32 fixture
Step 32 evaluation doc
Step 32 decision doc
```

不得存在其他无关文件。

---

# 四、Step 32 Contract 完整性检查

确认以下全部存在：

```text
Sampling
AnnotatorCaseInput
Independence
Input Isolation
Versioning
Comparison
Revision
Finalization
Failure Codes
Synthetic Fixture
Step 29 Compatibility
Step 30 Compatibility
Security
G3 Status
G4 Status
```

不要新增 Contract。

---

# 五、Failure Code 冻结检查

确认只有这 6 个 Step 32 failure codes：

```text
DATASET_VERSION_MISMATCH
CASE_NOT_FOUND
DUPLICATE_CASE_ID
ANNOTATOR_NOT_INDEPENDENT
ANNOTATOR_INPUT_LEAK
INVALID_CASE
```

检查：

```text
ANNOTATION_ERROR
COMPARE_ERROR
REVISION_ERROR
FINALIZATION_ERROR
```

没有被错误加入。

---

# 六、Synthetic Fixture 检查

确认：

```text
SYNTHETIC_ONLY
```

并且：

```text
4 cases
6 scenarios
```

case_kind：

```text
straightforward
disagreement
revision
multi-turn
```

各至少 1 个。

检查不存在：

```text
conversation_id
assistant_request_id
turn_id
provider_request_id
```

等真实生产 ID。

---

# 七、Step 29 / Step 30 Reuse Audit

重点确认 Step 32 没有复制实现：

```text
compare_annotations
resolve_disagreement
finalize_annotated_evidence
```

而是调用已有 Contract。

不要为了“更完整”修改旧实现。

---

# 八、G1-G4 Boundary

确认文档和测试仍然明确：

```text
G1 = BLOCKED
G2 = INSUFFICIENT
G3 = BLOCKED
G4 = BLOCKED
```

Step 32 的测试通过：

```text
≠
G3 READY
```

Step 32 的执行成功：

```text
≠
真实 Evidence
```

Synthetic fixture：

```text
≠
Production Evidence
```

---

# 九、Security Audit

确认 Annotator Input 不允许出现：

```text
api_key
Authorization
password
DATABASE_URL
SQL
prompt
messages
raw LLM response
embedding
other annotation
reviewer hints
ground truth
```

确认：

```text
project_id
```

仍然只是业务上下文，不被错误描述为 authorization / tenant boundary。

---

# 十、Immutability Audit

确认：

```text
AnnotationSamplingResult
AnnotatorCaseInput
AnnotationDraft
AnnotatedEvidence
```

涉及 Step 32 的对象没有被原地修改。

特别确认：

```text
revision
```

产生新对象，而不是修改旧对象。

---

# 十一、Test Audit

运行：

```powershell
python -m pytest -q tests/test_real_annotation_execution_contract.py
```

要求：

```text
39 passed
0 failed
```

然后：

```powershell
python -m pytest -q tests/test_conversation_context_real_evidence_import.py tests/test_conversation_context_real_evidence_import_architecture.py
```

要求：

```text
52 passed
0 failed
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

---

# 十二、Full Regression

最后运行：

```powershell
python -m pytest -q
```

要求：

```text
0 failed
0 errors
```

如果 Full Regression 出现新的失败：

不要修复。

先判断：

```text
Step 33 introduced failures = 0
```

如果不是 0：

立即 STOP 并报告。

---

# 十三、Side Effect Audit

必须确认：

```text
DB = 0
Network = 0
LLM = 0
DeepSeek = 0
SiliconFlow = 0
Production Evidence = 0
```

不要设置：

```text
RUN_DB_TESTS
```

---

# 十四、Compile / Static

执行：

```powershell
python -m compileall -q backend tests scripts
```

如果项目已有 lint：

继续使用已有 lint。

不要安装新的 lint 工具。

---

# 十五、Documentation Consistency

检查：

```text
docs/evaluation/Phase 4.1 Step 32 — Real Annotation Execution Contract.md

docs/decisions/Phase/Phase 4.1 Step 32：Real Annotation Execution Contract.md
```

确认：

```text
文档中的 Contract
=
当前测试实际 Contract
```

不能出现：

```text
文档声称支持
但测试没有
```

或者：

```text
测试实现了
但文档没有
```

---

# 十六、不要 Commit

本步骤：

```text
不要 git add
不要 git commit
不要 git push
不要创建 PR
不要 merge
```

只做 Release Readiness Audit。

---

# 十七、最终报告

严格输出：

```text
Phase 4.1 Step 33 Release Readiness Audit

1. Step 32 Scope
2. Contract Completeness
3. Failure Codes
4. Synthetic Fixture
5. Step 29 Compatibility
6. Step 30 Compatibility
7. Security
8. Immutability
9. G1/G2/G3/G4 Boundary
10. Git Scope
11. Targeted Tests
12. Step 31 Regression
13. Full Regression
14. Compileall
15. DB
16. Network
17. LLM
18. Documentation Consistency
19. Production Code Changes
20. Current Limitations

Release Readiness:
READY / NOT READY

Phase 4.1 Step 33 STOP
```

如果：

```text
READY
```

就停。

**不要自动 commit、push、PR、merge。**

等我下一步指令。
