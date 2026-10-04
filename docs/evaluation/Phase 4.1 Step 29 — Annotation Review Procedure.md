# Phase 4.1 Step 29 — Annotation Review Procedure

- 阶段：Phase 4.1（Conversation / Chat 会话层）
- 类型：**Procedure only**（只定义离线复核流程契约；不执行真实 Annotation）
- 前置：Step 24（Rubric）+ Step 25（Procedure）+ Step 28（Annotation Workflow Contract）
- 使用：synthetic contract fixtures（`annotation_review_cases.yaml`，6 场景）

---

## 1. Procedure

```text
Case
 ↓
Annotator 1
 ↓
Draft Annotation
 ↓
Annotator 2 / Reviewer
 ↓
Independent Review
 ↓
Compare
 ├── AGREEMENT
 └── DISAGREEMENT
        ↓
   Domain Review（DOMAIN_REVIEW_REQUIRED）
        ↓
   Final Reviewed Annotation（FINAL_REVIEW）
```

Annotator 1 负责三个标注产物：

```text
dependency_annotation · reference_annotation · impact_annotation
```

标注依据：

```text
case · current_turn · candidate history · full history reference
```

禁止依据：

```text
LLM 自动判断 · 数据库查询 · 生产业务系统 · Prompt hidden information
```

本阶段只定义流程；不实现人员系统 / Reviewer UI。

---

## 2. Independence

第二位标注员必须：

```text
独立重新判断，而不是先看 Annotator 1 的答案。
```

禁止：

```text
copy · reuse · majority
```

目的：

```text
测量 annotation disagreement
```

实现约束（test-local）：比较两名标注者时 annotator_id 必须不同
（同一 annotator 比较 → 拒绝）。

---

## 3. Comparison

比较：

```text
Annotator 1  vs  Annotator 2
```

至少比较 4 个关键字段：

```text
dependency_annotation（多标签完整结构比较）
reference_type
impact_annotation（三值精确比较）
business_outcome
```

规则：

```text
全部一致 → AGREEMENT
任一关键字段不同 → DISAGREEMENT
```

禁止模糊规则（如 "80% agreement" 之类阈值判定）—— 只做精确结构比较。

---

## 4. Disagreement

```text
DISAGREEMENT
    ↓
DOMAIN_REVIEW_REQUIRED
```

禁止：

```text
majority vote · average · automatic merge · LLM judge · random choice
```

（AST 审计断言不存在 majority_vote / majority / average / pick_winner /
auto_select / auto_merge / random_choice 函数。）

---

## 5. Review Status

继续使用 Step 28 的状态集：

```text
DRAFT · REVIEWED · DISPUTED
```

状态转换：

```text
DRAFT → REVIEWED

或

DRAFT → DISPUTED → DOMAIN REVIEW → REVIEWED
```

禁止：

```text
REVIEWED → 直接覆盖
（修正必须产生新的 annotation_version。）
```

---

## 6. Versioning

```text
dataset_version     原始数据身份（不因复核改变）
annotation_version  标注结果身份（每次修正递增）
```

`REVIEWED` 之后需要修正 → `revise(new_annotation_version=...)`：

```text
* 相同 annotation_version → 拒绝；
* 返回新对象，原对象完全不变（frozen）；
* dataset_version 保持不变。
```

---

## 7. Statistics

只做 **descriptive statistics**：

```text
cases_reviewed
agreements
disagreements
domain_review_required
final_reviewed
agreement_rate
```

禁止：

```text
accuracy · score · quality_score · weighted_score
以及任何标注员排名（A = 80 分 / B = 90 分 之类）。
```

`agreement_rate` 规则：

```text
cases = 0            → agreement_rate = N/A（不能报 0%）
cases = 10, agree=10 → 100%
cases = 10, agree=8  → 80%
```

---

## 8. G3

本阶段不改变 G3 Gate：

```text
G1 BLOCKED → G3 Evidence BLOCKED
```

即使 synthetic annotation 全部 `REVIEWED`：

```text
也不能升级 G3 READY —— synthetic 只能验证 Procedure。
```

（G3 Gate 条件仍由 Step 28 定义：G1 READY + Annotated Evidence + version match +
case IDs valid + annotation complete + review status valid。）

---

## 9. Security

禁止保存：

```text
API key · Authorization · password · DATABASE_URL ·
SQL · stack trace · raw LLM response · tool raw payload · embedding · vector
```

允许：

```text
case_id · project_id · dataset_version · annotation_version ·
annotator_id · review_status · dependency flags · impact flags ·
business outcome · reference_type
```

（复用 Step 28 的 `validate_no_forbidden_annotation_fields` 递归检查。）

---

## 10. Production Isolation

```text
Annotation Review 属于 offline Evaluation Layer，不进入 production runtime。
```

```text
* backend/app 不存在 AnnotationReviewService / AnnotationComparisonService /
  DomainReviewService / AnnotatorService；
* 不存在 annotation_review / annotation_comparison / domain_review / annotator
  命名模块；
* ConversationService / ChatApplicationService / Builder / AIOrchestrator /
  AIRouter / RagService / ToolChatService / TextToSQLService 不 import tests /
  annotation review / evaluation review；
* 未新增数据库表 / Migration / Annotation API / 生产 Annotation Service /
  Annotation Repository。
```

---

## 11. Deferred

```text
真实 Annotator（第一位标注）        = Deferred
真实 Reviewer（第二位独立复核）      = Deferred
Annotation UI                       = Deferred
Annotation Storage                  = Deferred
真实 G3（G3 Evidence READY）         = Deferred
G4 Evaluation                       = Deferred
ContextSelector / SelectionPolicy   = Deferred
Recent N / Hybrid / Relevance       = Deferred
Tokenizer / max_tokens / max_chars / max_turns = Deferred
Truncation / Summary / Memory       = Deferred
LLM Judge / 自动评分                 = Deferred
```

当前 Gate 状态：

```text
G1 = BLOCKED
G2 = INSUFFICIENT
G3 Contract = READY
G3 Evidence = BLOCKED
G4 = BLOCKED
Selection Strategy = BLOCKED
```

---

## 附：证据锚点

```text
tests/test_conversation_context_annotation_review.py
    → Agreement / Dependency / Impact / Outcome / Reference 比较 /
      多字段 DISAGREEMENT / Domain Review / 无 majority vote /
      Versioning / Agreement Rate / Fixture 校验 / G3 不升级
tests/test_conversation_context_annotation_review_architecture.py
    → production 无 review service；模块命名边界；runtime 无耦合
tests/fixtures/conversation_context/annotation_review_cases.yaml
    → 6 场景 synthetic fixture（无 annotation 结论、无生产 correlation IDs）
docs/evaluation/Phase 4.1 Step 28 — Real Evidence Annotation Workflow Contract.md
    → AnnotatedEvidence / Review Status / Versioning（本 Step 的输入）
```
