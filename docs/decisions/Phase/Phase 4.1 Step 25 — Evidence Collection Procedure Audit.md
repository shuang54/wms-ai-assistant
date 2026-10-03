# Phase 4.1 Step 25 — Evidence Collection Procedure Audit

## 一、阶段目标

基于 Phase 4.1 Step 22～24 当前状态：

```text
G1 = BLOCKED
G2 = INSUFFICIENT
G3 Contract = READY
G3 Evidence = BLOCKED
G4 = BLOCKED

Selection Strategy = BLOCKED
```

本阶段不实现 ContextSelector。

唯一目标：

> **冻结未来真实多轮 WMS 数据到达后，G1～G4 如何被客观地转换为 evidence 的最小评测流程。**

本阶段只建立：

```text
Data
 ↓
Validation
 ↓
G1
 ↓
G2
 ↓
G3 Annotation
 ↓
G4 Impact Evaluation
 ↓
Evidence Report
```

不要在本阶段产生 Selection Strategy。

---

# 二、严格禁止

禁止：

```text
ContextSelector
SelectionPolicy
Recent N
Recent N + First
Hybrid
Relevance
Tokenizer
max_tokens
max_chars
max_turns
Truncation
Summarizer
Memory
LLM Judge
自动评分
```

禁止：

```text
DeepSeek
SiliconFlow
真实 LLM
PostgreSQL
生产数据库
网络请求
```

禁止修改：

```text
Conversation ORM
ConversationRepository
ConversationService
ChatApplicationService
ConversationContextBuilder
AIOrchestrator
RAG
Tool
TextToSQL
Router
Prompt
API
```

禁止修改：

```text
Step 21 synthetic dataset
Step 22 gate
Step 24 rubric
```

---

# 三、核心原则

本阶段必须明确：

> Evidence Collection ≠ Strategy Selection。

例如：

```text
P95 = 18 turns
```

不能直接推出：

```text
max_turns = 18
```

同样：

```text
75% cases lost early constraints
```

不能直接推出：

```text
使用 Hybrid
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

# 四、Evidence Pipeline

冻结未来最小流程：

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

注意：

如果 G1 不 READY：

```text
G2/G3/G4
```

不得伪造为真实 evidence。

---

# 五、Dataset Validation

定义 test-local：

```text
EvidenceDatasetValidator
```

只存在：

```text
tests/
```

不进入 production。

验证至少：

```text
case_id
project_id
turns
role
content
last turn = user
non-empty history/current turn
```

以及：

```text
no secrets
no credentials
no production DB connection
```

---

# 六、G1 Evidence

G1 只有三种状态：

```text
READY
BLOCKED
```

如果已有：

```text
REAL_DEIDENTIFIED
```

并满足：

```text
sample_count > 0
conversation_count > 0
turn_count > 0
contains_sensitive_fields = false
```

则：

```text
G1 = READY
```

否则：

```text
G1 = BLOCKED
```

禁止：

```text
synthetic → READY
```

---

# 七、G2 Evidence Procedure

只有：

```text
G1 = READY
```

才允许计算。

统计：

### Conversation level

```text
turn_count
character_count
```

### Turn level

```text
user_turn_count
assistant_turn_count
user_character_count
assistant_character_count
```

### Percentiles

至少：

```text
P50
P90
P95
P99
Max
```

只使用：

```text
character / turn
```

禁止：

```text
chars → tokens
```

禁止任何：

```text
max_turns = P95
max_chars = P95
```

推导。

---

# 八、G3 Annotation Procedure

G3 使用 Step 24 已冻结的：

```text
early_constraint
middle_decision
recent_context
old_topic
standalone
```

真实数据到达后：

```text
Annotator
    ↓
ContextDependencyAnnotation
```

要求：

```text
human annotation
```

或：

```text
domain expert validation
```

LLM 可以辅助：

```text
candidate annotation
```

但不能直接成为最终 annotation。

---

# 九、G3 Annotation Unit

明确：

> Annotation 单位是“当前 user turn 相对于历史上下文的依赖”。

不是：

```text
整个 conversation 一个 label
```

例如：

```text
Conversation A
  Turn 3 → early_constraint=true
  Turn 5 → recent_context=true
  Turn 7 → standalone=true
```

不同 current turn 可以有不同 annotation。

---

# 十、G3 Annotation Record

未来最小结构：

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

注意：

这只是 evaluation artifact。

不是 Conversation ORM。

---

# 十一、G3 Disagreement

如果：

```text
Annotator A
vs
Annotator B
```

结果不同：

```text
DISAGREEMENT
```

不能：

```text
自动 majority vote
```

必须：

```text
domain review
```

然后形成：

```text
final annotation
```

---

# 十二、G4 Evaluation Procedure

G4 必须区分：

```text
Reference
Candidate
Impact
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

本阶段不实现实际 Selection Strategy。

---

# 十三、G4 Candidate Generation

本阶段：

**不得真正选择策略。**

只定义未来接口：

```text
candidate_history
```

来源由未来实验指定。

例如未来可以分别测试：

```text
Recent N
Recent + First
Hybrid
Relevance
```

但 Step 25 不执行这些策略。

---

# 十四、G4 Impact Annotation

沿用 Step 24：

```text
business_outcome_changed
critical_constraint_lost
entity_changed
intent_changed
```

值域：

```text
true
false
unknown
```

必须允许：

```text
unknown
```

---

# 十五、Outcome Comparison

未来比较不能只做：

```text
answer string equality
```

而应该根据：

```text
outcome_type
```

比较。

例如：

### SQL

比较：

```text
SQL_SEMANTICS
```

### Tool

比较：

```text
TOOL_SELECTION
TOOL_ARGUMENTS
```

### Knowledge

比较：

```text
ANSWER_CONTENT
```

### Router

比较：

```text
ROUTE
```

### Refusal

比较：

```text
REFUSAL
```

---

# 十六、G4 Result Classification

未来每个 case 最终得到：

```text
NO_IMPACT
IMPACT
UNKNOWN
```

其中：

```text
NO_IMPACT
```

表示：

```text
Selected History
```

没有改变业务结果。

```text
IMPACT
```

表示至少一个关键业务维度受到影响：

```text
business_outcome_changed
critical_constraint_lost
entity_changed
intent_changed
```

```text
UNKNOWN
```

表示证据不足。

---

# 十七、G4 Evidence Aggregation

只允许产生：

```text
count
percentage
distribution
```

例如：

```text
early_constraint cases: 20
critical_constraint_lost: 7
```

可以报告：

```text
7 / 20
```

但不能报告：

```text
strategy score = 0.65
```

禁止：

```text
ranking
score
weight
winner
```

---

# 十八、Evidence Quality

每个 G3/G4 evidence 必须能够追溯：

```text
case_id
current_turn_id
annotation_version
reviewed
```

但：

**不要保存真实敏感业务内容到报告。**

报告只显示：

```text
case_id
counts
aggregates
categories
```

---

# 十九、Privacy

未来真实数据：

```text
必须在仓库外完成脱敏
```

进入 repo 的只能是：

```text
de-identified evaluation artifact
```

本阶段不要实现：

```text
deidentify.py
anonymize.py
redact.py
```

不要在仓库中处理真实生产数据。

---

# 二十、Evidence Report Schema

定义 test-local：

```text
EvidenceCollectionReport
```

最小：

```text
dataset_version
source_type

g1_status

g2:
  status
  turn_distribution
  character_distribution

g3:
  status
  annotation_counts
  disagreement_count

g4:
  status
  impact_counts
  unknown_count

selection_strategy_decision
```

其中：

```text
selection_strategy_decision
```

必须继续：

```text
BLOCKED
```

---

# 二十一、Synthetic Dataset Handling

Step 21 synthetic dataset 继续：

```text
SYNTHETIC_ONLY
```

可以运行：

```text
schema validation
```

可以运行：

```text
procedure simulation
```

但输出必须标记：

```text
evidence_type = SYNTHETIC
```

不能：

```text
evidence_type = REAL
```

---

# 二十二、测试文件

新增：

```text
tests/test_conversation_context_evidence_collection_procedure.py
```

至少测试：

### Dataset validation

```text
valid
missing case_id
missing project_id
empty turns
invalid role
empty content
last turn not user
```

### G1

```text
synthetic → BLOCKED
real deidentified → READY
sensitive → BLOCKED
```

### G2

```text
G1 blocked → insufficient
G1 ready → percentile fields available
no token estimation
```

### G3

```text
five annotation dimensions
multi-label allowed
unknown not allowed for G3
```

### G4

```text
true/false/unknown
impact classification
string equality not required
```

### Aggregation

```text
counts
percentages
no score
no ranking
```

### Determinism

相同输入：

```text
same report
```

---

# 二十三、Architecture Audit

新增：

```text
tests/test_conversation_context_evidence_collection_procedure_architecture.py
```

确认：

```text
EvidenceDatasetValidator
ContextDependencyAnnotation
ContextImpactAnnotation
EvidenceCollectionReport
```

不得进入：

```text
backend/app/dto/
backend/app/services/
backend/app/db/
```

并确认：

```text
ContextSelector
SelectionPolicy
Budget
Tokenizer
Truncation
Summarizer
Memory
```

仍未进入 production。

---

# 二十四、文档

新增：

```text
docs/evaluation/Phase 4.1 Step 25 — Evidence Collection Procedure.md
```

章节：

```text
1. Objective
2. Evidence Pipeline
3. Dataset Validation
4. G1 Procedure
5. G2 Procedure
6. G3 Procedure
7. G4 Procedure
8. Annotation Quality
9. Disagreement
10. Outcome Comparison
11. Evidence Aggregation
12. Privacy
13. Synthetic Dataset
14. Current Gate State
15. Deferred
```

明确：

```text
G1 = BLOCKED
G2 = INSUFFICIENT
G3 Contract = READY
G3 Evidence = BLOCKED
G4 = BLOCKED
Selection Strategy = BLOCKED
```

---

# 二十五、测试命令

只运行：

```powershell
python -m pytest -q tests/test_conversation_context_evidence_collection_procedure.py
```

然后：

```powershell
python -m pytest -q tests/test_conversation_context_evidence_collection_procedure_architecture.py
```

然后：

```powershell
python -m compileall -q backend tests
```

不要：

```text
RUN_DB_TESTS=1
```

不要：

```text
全量 pytest
```

不要：

```text
DeepSeek
SiliconFlow
```

---

# 二十六、Git Diff

必须确认：

```text
Production Code = 0
DB Schema = 0
DB Writes = 0
Network = 0
LLM = 0
Tokenizer = 0
```

允许：

```text
tests/
docs/evaluation/
```

---

# 二十七、最终报告

严格：

```text
Phase 4.1 Step 25 完成报告

1. Evidence Pipeline
2. Dataset Validation
3. G1 Procedure
4. G2 Procedure
5. G3 Procedure
6. G4 Procedure
7. Annotation Quality
8. Disagreement
9. Outcome Comparison
10. Evidence Aggregation
11. Privacy
12. Synthetic Dataset
13. Current Gate State
14. Tests
15. compileall
16. Git Diff
17. Production Changes
18. DB / Network / LLM
19. Current Limitations

G1 = BLOCKED
G2 = INSUFFICIENT
G3 Contract = READY
G3 Evidence = BLOCKED
G4 = BLOCKED
Selection Strategy = BLOCKED

Phase 4.1 Step 25 READY
Phase 4.1 Step 25 STOP
```

# 二十八、硬停止

完成后立即停止。

不要：

```text
ContextSelector
Recent N
Hybrid
Relevance
Tokenizer
LLM Judge
Scoring
Real Data Import
```

等待下一步指令。
