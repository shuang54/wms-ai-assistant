# Phase 4.1 Step 24 — Multi-turn Context Evaluation Rubric

- 阶段：Phase 4.1（Conversation / Chat 会话层）
- 类型：**Audit only / Contract only**（只定义 evaluation contract，不实现任何策略与评测执行）
- 前置：Step 22（Decision Gate）+ Step 23（Data Readiness）：
  `Selection Strategy = BLOCKED` / `G1 = BLOCKED` / `G2 = INSUFFICIENT` / `G3 = BLOCKED` / `G4 = BLOCKED`
- 本阶段产出链路：

```text
Real Conversation
      ↓
Annotation Schema（ContextDependencyAnnotation）
      ↓
Context Dependency Label
      ↓
Full vs Selected Evaluation
      ↓
Business Impact Label（ContextImpactAnnotation）
```

---

## 1. Objective

在真实 WMS 多轮数据尚未提供之前，先把未来 G3 / G4 所需的
**Evaluation Rubric 与 Annotation Contract** 定义清楚。

核心原则：

> 不要评价"这段历史看起来重要不重要"；
> 而要评价：**如果历史上下文被移除，是否会影响当前业务请求的正确完成。**

Evaluation 分两个层面：

```text
Level 1：Context Dependency  —— 当前 user turn 是否依赖历史上下文；
Level 2：Business Impact     —— 历史缺失是否实际改变业务结果。
```

本阶段只定义：evaluation contract / annotation contract / reference requirements。
不产生生产策略；不实现 ContextSelector / judge / grader / scoring。

---

## 2. G3 Annotation Contract

Test-local 类型（**只存在于 tests/，禁止进入 production DTO**）：

```text
ContextDependencyAnnotation
    case_id: str
    current_turn_id: str

    early_constraint: bool
    middle_decision: bool
    recent_context: bool
    old_topic: bool
    standalone: bool
```

**互斥规则（重要）**：

```text
五个标签描述不同的 context dependency 维度，不是五选一分类。
允许同时为真，例如：

  early_constraint = true
  old_topic = true

（用户早期确定"只查询 A01 仓库"，后续"还是刚才那个，给我库存" ——
 既可能 early_constraint = true，也可能 old_topic = true。）
```

### 定义

1. `early_constraint`：当前请求依赖**较早历史**中的明确业务约束。

```text
用户：只看 A01 仓库
（多轮之后）
用户：查询库存
→ 当前请求仍需要 A01
```

2. `middle_decision`：当前请求依赖**中间历史**中的关键业务决策。

```text
A 方案 → 用户修改为 B → 后续请求引用"刚才确定的方案"
→ 删除中间决策后，当前请求无法可靠解释
```

3. `recent_context`：当前请求主要依赖**最近几轮**。

```text
用户：查库存
助手：100
用户：那 200 呢？
```

4. `old_topic`：当前请求**重新引用较早主题**。

```text
Topic A → Topic B → Topic C → "回到刚才那个采购单"
→ 没有旧 Topic，无法确定当前指代对象
```

5. `standalone`：当前请求**不依赖历史**。

```text
"查询今天所有入库单"
→ 即使删除历史，也可以独立完成
```

---

## 3. G4 Impact Contract

Test-local 类型：

```text
ContextImpactAnnotation
    case_id: str
    full_history_result: str
    selected_history_result: str

    business_outcome_changed: true | false | unknown
    critical_constraint_lost: true | false | unknown
    entity_changed: true | false | unknown
    intent_changed: true | false | unknown
```

**值域说明**：4 个 impact 字段使用 `true / false / unknown` **字符串**，
不强制 bool —— 因为真实人工评测可能存在"无法判断"。本阶段不调用 LLM。

### critical_constraint_lost

只有当"移除历史后导致当前业务结果无法满足关键约束"时才为 `true`：

```text
历史：只查询 A01 仓库
当前：查询库存

Full:      A01 库存
Selected:  全部仓库库存
→ critical_constraint_lost = true
```

### entity_changed

用于业务实体变化（物料 / 仓库 / 采购单 / 销售单 / 入库单 / 出库单）：

```text
Full: PO123
Selected: PO124
→ entity_changed = true
否则 false；无法判断 → unknown
```

### intent_changed

```text
Full:     查询库存
Selected: 查询入库
→ intent_changed = true
```

它不是评价答案文采，而是评价：
**用户当前真正想完成的业务动作是否发生改变。**

---

## 4. Business Outcome Taxonomy

至少允许以下 6 类（本阶段只定义 taxonomy，不修改现有业务 DTO）：

```text
ANSWER_CONTENT
TOOL_SELECTION
TOOL_ARGUMENTS
SQL_SEMANTICS
ROUTE
REFUSAL
```

---

## 5. WMS Scenario Mapping

Test-local matrix（**不给场景打分**）：

| WMS 场景 | Primary Outcome |
| --- | --- |
| 库存查询 | SQL_SEMANTICS / ANSWER_CONTENT |
| 入库查询 | SQL_SEMANTICS |
| 出库查询 | SQL_SEMANTICS |
| 指定仓库查询 | SQL_SEMANTICS |
| 指定物料查询 | SQL_SEMANTICS |
| Tool 查询 | TOOL_SELECTION / TOOL_ARGUMENTS |
| 业务知识问答 | ANSWER_CONTENT |
| 路由判断 | ROUTE |

映射约束：每个场景的 outcome 必须落在 §4 taxonomy 内（测试锁定）。

---

## 6. Reference Quality

### Full History Reference

`full_history_result` **不是**要求保存完整模型输出；
只要求未来 Evaluation 能够定义 `Full History` 作为 reference condition：

```text
* expected business outcome；或
* human-reviewed answer；或
* expected tool arguments；或
* expected SQL semantic result（具体形式按业务场景决定）。
```

### Selected History Result 与比较方式

```text
Full History
       ↓
   Reference

Selected History
       ↓
   Candidate

比较：candidate vs reference
```

禁止简单 **string equality**：自然语言回答可以存在**多个合法表达**，
比较必须基于语义 / business outcome 维度。

### Reference 质量门槛

```text
reference_created_by_human = true
       或
reference_validated_by_domain_expert = true
```

不能：

```text
LLM output → 直接当 ground truth
```

若使用 LLM 辅助标注，必须保留 **human validation** 作为最终确认。

---

## 7. Annotation Quality

未来真实标注至少记录：

```text
annotator
annotation_version
reviewed
```

本阶段**不建立用户系统或数据库**（只定义未来 schema）。

---

## 8. Disagreement

未来两名标注者意见不同时：

```text
不要自动 majority vote；
只标记 DISAGREEMENT，并要求 domain review；
review 之后再形成 final annotation。
```

---

## 9. Dataset Schema

未来真实 Evaluation Dataset 最小字段（**evaluation schema，不是 Conversation ORM**）：

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

校验规则（schema validation test 锁定）：

```text
case_id required · turns non-empty · last turn = user · role ∈ {user, assistant} ·
content non-empty · annotation 5 键且为 bool · reference outcome_type ∈ taxonomy ·
impact 值 ∈ {true, false, unknown}
```

明确：该 schema **不得混入 production persistence**
（无 conversation_id / turn_id / assistant_request_id / created_at / status 字段）。

---

## 10. Security

annotation / reference 中禁止出现：

```text
API key / password / Authorization / DATABASE_URL / SQL credentials
（以最小 secret pattern 检查；只检查明确凭据形态，不扫描业务内容）
```

---

## 11. Production Boundary

```text
ContextDependencyAnnotation / ContextImpactAnnotation
    → 只允许存在于 tests/
    → 不得出现在 backend/app/dto/ · backend/app/services/ · backend/app/db/
```

AST 精确审计（禁止全文字符串扫描）覆盖 9 个标识符：

```text
early_constraint · middle_decision · recent_context · old_topic · standalone ·
critical_constraint_lost · business_outcome_changed · entity_changed · intent_changed
```

测查时以 AST 标识符（Name / Attribute / arg / keyword / def / 字符串常量）为准；
当前 production 三目录**零命中**。

---

## 12. Current Gate State

```text
G1 = BLOCKED
G2 = INSUFFICIENT
G3 Contract = READY      （Annotation Contract 已定义，可接受未来真实数据）
G3 Evidence = BLOCKED    （真实 G3 evidence 尚未存在）
G4 = BLOCKED
Selection Strategy = BLOCKED（保持不变）
```

说明：

```text
G3 Contract = READY 不表示真实 G3 evidence 已存在；
Step 24 READY 只表示 evaluation contract 已建立。
```

Synthetic dataset 状态：仍然 `SYNTHETIC_ONLY`（未因加入 annotation schema 而改变）；
Step 21 dataset **未被修改**（本阶段未向其中加入任何 annotation / reference / impact 字段）。

---

## 13. Deferred

```text
ContextSelector / Recent N / Recent + First / Hybrid / Relevance  = Deferred
Tokenizer / max_tokens / max_chars / max_turns / Truncation        = Deferred
Summarizer / Memory                                               = Deferred
LLM judge / grader / scoring                                      = Deferred
Full vs Selected 的真实 LLM 执行比较                                = Deferred
真实数据导入（Real Data Import）                                    = Deferred
G4 evaluation 执行                                                 = Deferred
```

本阶段明确不做：

```text
* 不运行 DeepSeek / SiliconFlow；
* 不实现 judge / grader / scoring；
* 不修改 Step 21 synthetic dataset；
* 不实现任何 selection 策略。
```

---

## 附：证据锚点

```text
tests/test_conversation_context_evaluation_rubric.py
    → 5 个 dependency 维度 / 4 个 impact 字段 / taxonomy / WMS 映射 /
      reference quality / annotation quality / disagreement / dataset schema /
      security / gate 状态 / 文档完整性
tests/test_conversation_context_evaluation_rubric_architecture.py
    → AST 精确边界（9 个标识符 + 6 个类型名 + 模块命名）
docs/evaluation/Phase 4.1 Step 23 — Selection Evidence Data Readiness.md
    → G1~G4 数据 readiness（本 Step 的输入）
```
