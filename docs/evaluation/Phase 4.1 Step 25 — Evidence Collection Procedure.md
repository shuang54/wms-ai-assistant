# Phase 4.1 Step 25 — Evidence Collection Procedure

- 阶段：Phase 4.1（Conversation / Chat 会话层）
- 类型：**Procedure only**（只冻结 evidence 收集流程；不选择任何策略）
- 前置：Step 22（Decision Gate）+ Step 23（Data Readiness）+ Step 24（Evaluation Rubric）

---

## 1. Objective

冻结未来真实多轮 WMS 数据到达后，G1~G4 如何被客观转换为 evidence 的**最小评测流程**。

核心原则：

```text
Evidence Collection ≠ Strategy Selection
```

例如：

```text
P95 = 18 turns                       不能推出 max_turns = 18；
75% cases lost early constraints     不能推出"使用 Hybrid"。
```

本阶段只回答：

```text
发生了什么？
```

不回答：

```text
应该选择什么策略？
```

---

## 2. Evidence Pipeline

```text
Raw / De-identified Dataset
        ↓
Dataset Validation
        ↓
G1 Real Dataset Readiness
        ↓
G2 Length Distribution
        ↓
G3 Context Dependency Annotation
        ↓
G4 Full vs Selected Impact Evaluation
        ↓
Evidence Report
```

规则：

```text
如果 G1 不 READY：
    G2 / G3 / G4 不得伪造为真实 evidence
    （状态只能是 INSUFFICIENT / BLOCKED，且 evidence_type 不得为 REAL）。
```

---

## 3. Dataset Validation

定义 test-local：

```text
EvidenceDatasetValidator（只存在于 tests/）
```

验证内容：

```text
case_id · project_id · turns · role · content ·
last turn = user · non-empty history/current turn
```

以及安全项：

```text
no secrets · no credentials · no production DB connection
（最小 secret pattern + 连接串形态：postgresql:// … / DATABASE_URL / psycopg2.connect）
```

复用 Step 24 的 schema 校验（case 结构 / annotation / reference / impact），
并追加去重（duplicate case_id 拒绝）。

---

## 4. G1 Procedure

只有三种状态：`READY / BLOCKED`。

```text
READY 条件（全部满足）：
    source_type = REAL_DEIDENTIFIED
    sample_count > 0
    conversation_count > 0
    turn_count > 0
    contains_sensitive_fields = false
```

否则：

```text
G1 = BLOCKED
```

禁止：

```text
synthetic → READY（synthetic dataset 永远不能升级为 G1 evidence）
```

---

## 5. G2 Procedure

只有 `G1 = READY` 才允许计算。

统计口径：

```text
Conversation level：turn_count · character_count
Turn level：user_turn_count · assistant_turn_count ·
            user_character_count · assistant_character_count
Percentiles：P50 · P90 · P95 · P99 · Max（最近秩法，确定性）
```

冻结约束：

```text
* 只使用 character / turn 口径；
* 禁止 chars → tokens 推算（chars / 4、chars * 0.25 一律禁止）；
* 禁止任何 policy 推导：
  max_turns = P95  ✗
  max_chars = P95  ✗
* G1 BLOCKED 时：G2 = INSUFFICIENT（不得计算）。
```

---

## 6. G3 Procedure

使用 Step 24 已冻结的 5 个维度：

```text
early_constraint · middle_decision · recent_context · old_topic · standalone
```

流程：

```text
Annotator
    ↓
ContextDependencyAnnotation
```

要求：

```text
human annotation 或 domain expert validation；
LLM 只可辅助产生 candidate annotation，
不能直接成为最终 annotation。
```

Annotation 单位：

```text
"当前 user turn 相对于历史上下文的依赖"
（不是整段 conversation 一个 label）

Conversation A
  Turn 3 → early_constraint=true
  Turn 5 → recent_context=true
  Turn 7 → standalone=true
```

值域：只有 bool（`false / true`）—— **G3 不允许 unknown**（unknown 只属于 G4）。

最小 annotation record（evaluation artifact，**不是 Conversation ORM**）：

```yaml
case_id:
current_turn_id:

annotation:
  early_constraint: false
  middle_decision: false
  recent_context: true
  old_topic: false
  standalone: false

annotator:
annotation_version:
reviewed:
```

---

## 7. G4 Procedure

必须区分：

```text
Reference / Candidate / Impact
```

流程：

```text
Full History
      ↓
Reference

Selected History
      ↓
Candidate

Reference vs Candidate
      ↓
Impact Annotation
```

### Candidate Generation

本阶段**不得真正选择策略**；只定义未来接口：

```text
candidate_history（来源由未来实验指定）
未来可分别测试 Recent N / Recent + First / Hybrid / Relevance ——
但 Step 25 不执行这些策略。
```

### Impact Annotation

沿用 Step 24：`business_outcome_changed` / `critical_constraint_lost` /
`entity_changed` / `intent_changed`，值域 `true / false / unknown`
（**必须允许 unknown**）。

### Result Classification

```text
NO_IMPACT  —— Selected History 没有改变业务结果
IMPACT     —— 至少一个关键业务维度受影响
UNKNOWN    —— 证据不足
```

---

## 8. Annotation Quality

每个 G3/G4 evidence 必须能够追溯：

```text
case_id · current_turn_id · annotation_version · reviewed
```

但：

```text
不要保存真实敏感业务内容到报告；
报告只显示 case_id / counts / aggregates / categories。
```

---

## 9. Disagreement

```text
Annotator A vs Annotator B 结果不同：
    → DISAGREEMENT
    → 禁止自动 majority vote
    → 必须 domain review
    → 之后形成 final annotation
```

---

## 10. Outcome Comparison

比较不能只做 `answer string equality`，而应按 `outcome_type`：

```text
SQL        → SQL_SEMANTICS
Tool       → TOOL_SELECTION / TOOL_ARGUMENTS
Knowledge  → ANSWER_CONTENT
Router     → ROUTE
Refusal    → REFUSAL
```

原因：自然语言回答可以存在多个合法表达。

---

## 11. Evidence Aggregation

只允许产生：

```text
count · percentage · distribution
```

例如：

```text
early_constraint cases: 20
critical_constraint_lost: 7
可以报告 7 / 20
```

禁止：

```text
score · ranking · weight · winner
（例如 "strategy score = 0.65" ✗）
```

报告字段命名中亦不得出现 score / ranking / weight / winner / rank。

---

## 12. Privacy

未来真实数据：

```text
必须在仓库外完成脱敏
```

进入 repo 的只能是：

```text
de-identified evaluation artifact
```

本阶段**不实现** `deidentify.py` / `anonymize.py` / `redact.py`；
不在仓库中处理真实生产数据。

---

## 13. Synthetic Dataset

Step 21 synthetic dataset 继续：

```text
SYNTHETIC_ONLY
```

可以运行：

```text
schema validation · procedure simulation
```

但输出必须标记：

```text
evidence_type = SYNTHETIC
```

不能：

```text
evidence_type = REAL
```

当前 simulation 输出（确定性）：

```text
g1_status = BLOCKED · g2_status = INSUFFICIENT
g3_status = BLOCKED · g4_status = BLOCKED
selection_strategy_decision = BLOCKED
```

---

## 14. Current Gate State

```text
G1 = BLOCKED
G2 = INSUFFICIENT
G3 Contract = READY
G3 Evidence = BLOCKED
G4 = BLOCKED
Selection Strategy = BLOCKED
```

说明：

```text
Step 25 READY 只表示 evidence collection procedure 已冻结；
不表示任何 gate 解锁、不表示策略可选。
```

---

## 15. Deferred

```text
ContextSelector / SelectionPolicy / Recent N / Recent + First / Hybrid / Relevance = Deferred
Tokenizer / max_tokens / max_chars / max_turns / Truncation / Summarizer / Memory = Deferred
LLM Judge / 自动评分（Scoring）= Deferred
真实候选策略执行（candidate generation 的实际策略）= Deferred
真实数据导入（Real Data Import）= Deferred
脱敏工具链（deidentify / anonymize / redact）= Deferred
```

---

## 附：证据锚点

```text
tests/test_conversation_context_evidence_collection_procedure.py
    → Dataset Validation / G1~G4 procedure / Outcome comparison /
      Aggregation / Report（selection_strategy_decision 保持 BLOCKED）/ 确定性
tests/test_conversation_context_evidence_collection_procedure_architecture.py
    → AST 精确边界（Step 25 组件 + Context 域禁词 + 既有 reranker tokenizer 排除）
docs/evaluation/Phase 4.1 Step 24 — Multi-turn Context Evaluation Rubric.md
    → annotation / impact contract（本 Step procedure 的输入）
```
