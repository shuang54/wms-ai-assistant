# Phase 4.1 Step 24 — Multi-turn Context Evaluation Rubric Audit

## 一、阶段目标

基于 Phase 4.1 Step 22 / Step 23：

```text
Selection Strategy = BLOCKED
G1 = BLOCKED
G2 = INSUFFICIENT
G3 = BLOCKED
G4 = BLOCKED
```

本阶段仍然：

**不实现 ContextSelector。**

唯一目标：

> 在真实 WMS 多轮数据尚未提供之前，先把未来 G3 / G4 所需的 Evaluation Rubric 与 Annotation Contract 定义清楚。

形成：

```text
Real Conversation
      ↓
Annotation Schema
      ↓
Context Dependency Label
      ↓
Full vs Selected Evaluation
      ↓
Business Impact Label
```

本阶段只定义：

```text
evaluation contract
annotation contract
reference requirements
```

不产生生产策略。

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

禁止：

```text
DeepSeek
SiliconFlow
真实 LLM
DB
Network
```

禁止：

```text
修改 Step 21 synthetic dataset
```

---

# 三、Evaluation Rubric 核心原则

本阶段不要评价：

```text
“这段历史看起来重要不重要”
```

而要评价：

> **如果历史上下文被移除，是否会影响当前业务请求的正确完成。**

因此将 Evaluation 分成两个层面：

### Level 1：Context Dependency

当前 user turn 是否依赖历史上下文。

### Level 2：Business Impact

历史缺失是否实际改变业务结果。

---

# 四、G3 Annotation Contract

定义 test-local annotation：

```text
ContextDependencyAnnotation
```

只能存在于：

```text
tests/
```

禁止进入 production DTO。

字段建议：

```text
case_id
current_turn_id

early_constraint
middle_decision
recent_context
old_topic
standalone
```

全部使用：

```text
bool
```

---

# 五、Annotation 互斥规则

不要要求五个字段必须严格 one-hot。

允许：

```text
early_constraint = true
old_topic = true
```

同时存在。

例如：

```text
用户早期确定：
“只查询 A01 仓库”

后续：
“还是刚才那个，给我库存”
```

既可能：

```text
early_constraint = true
```

也可能：

```text
old_topic = true
```

因此：

> 五个标签描述不同的 context dependency 维度，不是五选一分类。

---

# 六、Annotation Definitions

## 1. early_constraint

当前请求依赖较早历史中的明确业务约束。

例如：

```text
用户：
只看 A01 仓库

后续多轮后：
查询库存
```

当前请求仍需要：

```text
A01
```

---

## 2. middle_decision

当前请求依赖中间历史中的关键业务决策。

例如：

```text
A 方案
→ 用户修改为 B
→ 后续请求引用“刚才确定的方案”
```

如果删除中间决策，当前请求无法可靠解释。

---

## 3. recent_context

当前请求主要依赖最近几轮。

例如：

```text
用户：查库存
助手：100
用户：那 200 呢？
```

当前请求依赖最近上下文。

---

## 4. old_topic

当前请求重新引用较早主题。

例如：

```text
Topic A
→ Topic B
→ Topic C
→ “回到刚才那个采购单”
```

如果没有旧 Topic，无法确定当前指代对象。

---

## 5. standalone

当前请求不依赖历史。

例如：

```text
“查询今天所有入库单”
```

即使删除历史，也可以独立完成。

---

# 七、G4 Business Impact Contract

定义 test-local：

```text
ContextImpactAnnotation
```

建议字段：

```text
case_id
full_history_result
selected_history_result
business_outcome_changed
critical_constraint_lost
entity_changed
intent_changed
```

其中：

```text
business_outcome_changed
critical_constraint_lost
entity_changed
intent_changed
```

使用：

```text
true
false
unknown
```

不要强制使用 bool。

因为真实人工评测可能存在：

```text
无法判断
```

---

# 八、Full History Reference

定义：

```text
full_history_result
```

不是要求保存完整模型输出。

只要求未来 Evaluation 能够定义：

```text
Full History
```

作为 reference condition。

可以是：

```text
expected business outcome
```

或者：

```text
human-reviewed answer
```

或者：

```text
expected tool arguments
```

或者：

```text
expected SQL semantic result
```

具体形式根据业务场景决定。

---

# 九、Selected History Result

未来需要：

```text
Full History
       ↓
Reference

Selected History
       ↓
Candidate
```

比较：

```text
candidate vs reference
```

禁止简单：

```text
string equality
```

因为自然语言回答可以存在多个合法表达。

---

# 十、Business Outcome 定义

至少允许以下几类：

```text
ANSWER_CONTENT
TOOL_SELECTION
TOOL_ARGUMENTS
SQL_SEMANTICS
ROUTE
REFUSAL
```

本阶段只定义 taxonomy。

不要修改现有业务 DTO。

---

# 十一、WMS 场景映射

建立 test-local matrix：

| WMS 场景  | Primary Outcome                 |
| ------- | ------------------------------- |
| 库存查询    | SQL_SEMANTICS / ANSWER_CONTENT  |
| 入库查询    | SQL_SEMANTICS                   |
| 出库查询    | SQL_SEMANTICS                   |
| 指定仓库查询  | SQL_SEMANTICS                   |
| 指定物料查询  | SQL_SEMANTICS                   |
| Tool 查询 | TOOL_SELECTION / TOOL_ARGUMENTS |
| 业务知识问答  | ANSWER_CONTENT                  |
| 路由判断    | ROUTE                           |

不要给场景打分。

---

# 十二、Loss Impact 判断

定义：

```text
critical_constraint_lost
```

只有在：

> 移除历史后导致当前业务结果无法满足关键约束

时才为：

```text
true
```

例如：

```text
历史：
只查询 A01 仓库

当前：
查询库存

Full:
A01 库存

Selected:
全部仓库库存
```

则：

```text
critical_constraint_lost = true
```

---

# 十三、Entity Changed

用于：

```text
物料
仓库
采购单
销售单
入库单
出库单
```

等业务实体。

如果：

```text
Full:
PO123

Selected:
PO124
```

则：

```text
entity_changed = true
```

否则：

```text
false
```

无法判断：

```text
unknown
```

---

# 十四、Intent Changed

当前：

```text
Full:
查询库存
```

Selected：

```text
查询入库
```

则：

```text
intent_changed = true
```

它不是评价答案文采，而是评价：

> 用户当前真正想完成的业务动作是否发生改变。

---

# 十五、Reference Quality

未来真实数据进入前，必须满足：

```text
reference_created_by_human = true
```

或者：

```text
reference_validated_by_domain_expert = true
```

不能：

```text
LLM output
    ↓
直接当 ground truth
```

如果使用 LLM 辅助标注：

必须保留：

```text
human validation
```

作为最终确认。

这与当前 eval 最佳实践一致：领域专家/人工标注对于建立可靠 ground truth 很重要。

---

# 十六、Annotation Quality

未来真实标注至少记录：

```text
annotator
annotation_version
reviewed
```

但：

**本阶段不要建立用户系统或数据库。**

只定义未来 schema。

---

# 十七、Disagreement

如果未来两个标注者意见不同：

不要自动：

```text
majority vote
```

本阶段只定义：

```text
DISAGREEMENT
```

并要求：

```text
domain review
```

之后再形成 final annotation。

---

# 十八、Evaluation Dataset 最小字段

未来真实数据建议至少：

```yaml
- case_id
  project_id
  turns:
    - role
      content

  annotation:
    early_constraint
    middle_decision
    recent_context
    old_topic
    standalone

  reference:
    outcome_type
    expected_behavior

  impact:
    business_outcome_changed
    critical_constraint_lost
    entity_changed
    intent_changed
```

注意：

这只是：

```text
evaluation dataset schema
```

不是：

```text
Conversation ORM
```

不要混入 production persistence。

---

# 十九、当前 Synthetic Dataset

不要修改 Step 21 dataset。

本阶段只增加一个：

```text
schema validation test
```

验证未来真实数据必须符合上述 contract。

Synthetic 数据：

```text
仍然 SYNTHETIC_ONLY
```

不能因为加入 annotation schema 而变成：

```text
REAL
```

---

# 二十、测试文件

新增：

```text
tests/test_conversation_context_evaluation_rubric.py
```

至少测试：

### Annotation

```text
early_constraint
middle_decision
recent_context
old_topic
standalone
```

### Impact

```text
business_outcome_changed
critical_constraint_lost
entity_changed
intent_changed
```

### Validation

```text
case_id required
turns non-empty
last turn user
role valid
content non-empty
annotation types valid
impact values true/false/unknown
```

### Security

禁止 annotation/reference 中出现：

```text
API key
password
Authorization
DATABASE_URL
SQL credentials
```

### Production boundary

确认这些 DTO/schema 只存在：

```text
tests/
```

不要出现在：

```text
backend/app/dto/
backend/app/services/
backend/app/db/
```

---

# 二十一、Architecture Audit

新增：

```text
tests/test_conversation_context_evaluation_rubric_architecture.py
```

检查：

```text
ContextDependencyAnnotation
ContextImpactAnnotation
```

不得进入 production。

禁止：

```text
backend/app/dto/
backend/app/services/
backend/app/db/
```

出现：

```text
early_constraint
middle_decision
recent_context
old_topic
standalone
critical_constraint_lost
business_outcome_changed
entity_changed
intent_changed
```

除非已有无关业务模块明确包含同名字段。

如果存在误报：

使用 AST 精确识别。

禁止全文字符串扫描。

---

# 二十二、Decision Gate 更新

本阶段：

```text
G1 = BLOCKED
G2 = INSUFFICIENT
G3 = READY
G4 = BLOCKED
```

注意：

G3 READY 只表示：

> Annotation Contract 已经定义并且可以接受未来真实数据。

不表示：

```text
真实 G3 evidence 已存在
```

因此：

```text
Selection Strategy = BLOCKED
```

保持不变。

---

# 二十三、不要开始 G4 Evaluation

特别注意：

本阶段不要：

```text
运行 DeepSeek
```

不要：

```text
Full History vs Selected History
```

真正执行 LLM 比较。

不要实现：

```text
judge
grader
scoring
```

本阶段只定义 contract。

---

# 二十四、文档

新增：

```text
docs/evaluation/Phase 4.1 Step 24 — Multi-turn Context Evaluation Rubric.md
```

章节：

```text
1. Objective
2. G3 Annotation Contract
3. G4 Impact Contract
4. Business Outcome Taxonomy
5. WMS Scenario Mapping
6. Reference Quality
7. Annotation Quality
8. Disagreement
9. Dataset Schema
10. Security
11. Production Boundary
12. Current Gate State
13. Deferred
```

明确：

```text
G3 Contract = READY
G3 Evidence = BLOCKED
G4 = BLOCKED
Selection Strategy = BLOCKED
```

---

# 二十五、测试命令

只运行：

```powershell
python -m pytest -q tests/test_conversation_context_evaluation_rubric.py
```

然后：

```powershell
python -m pytest -q tests/test_conversation_context_evaluation_rubric_architecture.py
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

不要调用：

```text
DeepSeek
SiliconFlow
```

---

# 二十六、Git Diff

确认：

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
Phase 4.1 Step 24 完成报告

1. G3 Annotation Contract
2. G4 Impact Contract
3. Business Outcome Taxonomy
4. WMS Scenario Mapping
5. Reference Quality
6. Annotation Quality
7. Disagreement
8. Dataset Schema
9. Security
10. Production Boundary
11. Decision Gate State
12. Tests
13. compileall
14. Git Diff
15. Production Changes
16. DB / Network / LLM
17. Current Limitations

G3 Contract = READY
G3 Evidence = BLOCKED
G4 = BLOCKED
Selection Strategy = BLOCKED

Phase 4.1 Step 24 READY
Phase 4.1 Step 24 STOP
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
