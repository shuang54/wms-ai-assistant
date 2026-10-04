# Phase 4.1 Step 23 — 真实多轮对话数据证据准备审计

## 一、阶段目标

基于 Phase 4.1 Step 22：

```text
Selection Strategy Decision = BLOCKED
```

本阶段不实现 ContextSelector。

只解决一个问题：

> **确认当前项目是否已经存在可以用于 Selection Strategy 决策的真实/脱敏多轮 WMS 对话数据，以及 G1～G4 需要的数据结构是否能够从现有数据中获得。**

本阶段只做：

```text
阅读现有数据
    ↓
寻找真实/脱敏多轮样本
    ↓
审计数据结构
    ↓
审计隐私/敏感信息风险
    ↓
定义 G1～G4 evidence readiness
    ↓
测试 + 文档
    ↓
STOP
```

---

# 二、严格禁止

本阶段禁止：

```text
实现 ContextSelector
实现 SelectionPolicy
实现 Budget
实现 max_turns
实现 max_chars
实现 max_tokens
实现 Tokenizer
实现 Truncation
实现 Summarizer
实现 Memory
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
```

禁止：

```text
DeepSeek
SiliconFlow
真实 LLM
生产数据库
网络请求
```

禁止：

```text
修改现有 synthetic WMS dataset
```

禁止把任何真实业务数据复制到：

```text
tests/
docs/
git
```

如果发现真实数据中包含：

```text
姓名
手机号
邮箱
地址
客户名称
供应商名称
订单号
合同号
SKU
库存数量
生产数据
数据库连接信息
```

不要直接保存或提交。

---

# 三、Step 1：寻找现有数据来源

检查项目：

```text
tests/
tests/fixtures/
docs/
data/
fixtures/
scripts/
```

重点寻找：

```text
conversation
conversation_turn
chat
multi_turn
dialog
history
sample
dataset
evaluation
```

同时检查是否存在：

```text
本地导出的历史聊天数据
脱敏测试数据
已有 evaluation snapshot
```

---

# 四、只接受三种数据状态

将当前状态归类为：

### A：REAL_DEIDENTIFIED

存在真实业务来源，并且已经脱敏。

可以作为 G1 candidate evidence。

---

### B：REAL_NOT_DEIDENTIFIED

存在真实业务来源，但仍包含敏感信息。

只能记录：

```text
发现真实数据
但不能进入 evaluation
```

禁止复制数据。

---

### C：SYNTHETIC_ONLY

只有：

```text
Step 21 synthetic dataset
```

或者其他人工构造数据。

则：

```text
G1 = BLOCKED
```

不能把 synthetic 当真实业务证据。

---

# 五、Data Readiness DTO

不要创建 production DTO。

在：

```text
tests/
```

中可以使用 test-local 数据结构，例如：

```text
EvidenceDataReadiness
```

只允许描述：

```text
source_type
sample_count
conversation_count
turn_count
has_real_origin
is_deidentified
contains_sensitive_fields
usable_for_evaluation
```

不要保存实际 conversation content。

---

# 六、G1：Real WMS Dataset

需要回答：

```text
是否存在真实业务来源？
是否已经脱敏？
是否可以用于 evaluation？
```

G1 READY 必须至少满足：

```text
source_type = REAL_DEIDENTIFIED
sample_count > 0
conversation_count > 0
turn_count > 0
contains_sensitive_fields = false
usable_for_evaluation = true
```

否则：

```text
G1 = BLOCKED
```

---

# 七、G2：Length Distribution

如果 G1 READY：

只统计：

```text
turn count
character count
conversation length
```

可以计算：

```text
P50
P90
P95
P99
Max
```

但是：

**禁止任何 token 推算。**

禁止：

```text
chars / 4
chars * 0.25
```

也禁止：

```text
max_chars = P95
max_turns = P95
```

本阶段只是：

```text
evidence
```

不是：

```text
policy
```

如果 G1 BLOCKED：

```text
G2 = INSUFFICIENT
```

---

# 八、G3：Constraint Annotation

审计真实数据是否能够进行以下 annotation：

```text
early_constraint
middle_decision
recent_context
old_topic
standalone
```

要求：

### early_constraint

关键约束出现在较早历史 turn。

### middle_decision

关键决策出现在中间 turn。

### recent_context

关键上下文主要来自最近 turn。

### old_topic

当前问题重新依赖较早主题。

### standalone

当前问题基本不依赖历史。

本阶段：

**只定义 annotation readiness。**

不要真的给 synthetic 数据重新标注。

---

# 九、G4：Loss Impact Evidence

G4 是本阶段最重要的设计审计。

未来需要比较：

```text
Full History
      ↓
Answer A

Selected History
      ↓
Answer B
```

然后判断：

```text
是否改变业务结果
是否丢失关键约束
是否改变关键实体
是否改变用户意图
```

本阶段不调用 LLM。

只确认：

```text
现有真实数据是否允许未来做这种 evaluation
```

至少需要：

```text
真实多轮 history
+
当前 user turn
+
可比较的 expected/reference outcome
```

如果没有可靠的 reference：

```text
G4 = BLOCKED
```

---

# 十、敏感信息审计

必须确认 evaluation 数据不能包含：

```text
API key
Authorization
password
DATABASE_URL
SQL credentials
真实数据库连接串
```

业务数据方面：

如果没有现成脱敏机制：

不要自己把真实数据复制到测试目录。

只报告：

```text
de-identification mechanism = unavailable
```

---

# 十一、Data Leakage Tests

新增：

```text
tests/test_conversation_selection_evidence_data_audit.py
```

至少测试：

### 1. Synthetic dataset classification

Step 21 dataset 必须被识别为：

```text
SYNTHETIC_ONLY
```

不能：

```text
REAL_DEIDENTIFIED
```

---

### 2. No real content in repository

检查 evaluation fixture 不包含：

```text
API key
password
DATABASE_URL
Authorization
```

不要使用简单全文扫描判断业务数据。

只对明确敏感 secret patterns 做最小检查。

---

### 3. No production DB dependency

测试：

```text
DB = 0
Network = 0
LLM = 0
```

---

# 十二、Evidence Readiness Matrix

建立：

```text
G1 | G2 | G3 | G4
```

例如：

```text
G1 = BLOCKED
G2 = INSUFFICIENT
G3 = BLOCKED
G4 = BLOCKED
```

具体状态必须由真实审计结果决定。

不能预设 PASS。

---

# 十三、Transition Rule

本阶段结束后：

如果：

```text
G1 = BLOCKED
```

则：

```text
Selection Strategy = BLOCKED
```

继续保持。

即使：

```text
G2/G3/G4
```

全部 READY，也不能冻结策略。

只有后续证据满足：

```text
G1 READY
G2 READY
G3 READY
G4 READY
```

才允许进入下一次 Strategy Decision Gate。

---

# 十四、文档

新增：

```text
docs/evaluation/Phase 4.1 Step 23 — Selection Evidence Data Readiness.md
```

必须记录：

## 1. Data Source

```text
REAL_DEIDENTIFIED
REAL_NOT_DEIDENTIFIED
SYNTHETIC_ONLY
```

## 2. G1

状态 + 阻塞原因。

## 3. G2

状态 + 可获得的统计维度。

## 4. G3

annotation readiness。

## 5. G4

result/reference readiness。

## 6. Privacy

是否存在脱敏机制。

## 7. Leakage

是否发现 secrets / production data exposure。

## 8. Decision

```text
Selection Strategy remains BLOCKED
```

如果确实如此。

---

# 十五、测试命令

只运行：

```powershell
python -m pytest -q tests/test_conversation_selection_evidence_data_audit.py
```

然后：

```powershell
python -m compileall -q backend tests
```

不要运行：

```text
RUN_DB_TESTS=1
```

不要运行：

```text
全量 pytest
```

不要调用任何外部 API。

---

# 十六、Git Diff

检查：

```powershell
git status --short
git diff --stat
```

本阶段原则：

```text
Production Code = 0
DB Schema = 0
DB Writes = 0
Network = 0
LLM = 0
```

允许：

```text
tests/
docs/evaluation/
```

---

# 十七、最终报告

严格输出：

```text
Phase 4.1 Step 23 完成报告

1. Data Source
2. G1 Real WMS Dataset
3. G2 Length Distribution
4. G3 Constraint Annotation
5. G4 Loss Impact Evidence
6. Privacy / De-identification
7. Data Leakage
8. Evidence Readiness Matrix
9. Selection Strategy Decision
10. Tests
11. compileall
12. Git Diff
13. Production Changes
14. DB / Network / LLM
15. Current Limitations

Phase 4.1 Step 23 READY
Phase 4.1 Step 23 STOP
```

注意：

这里的：

```text
Step 23 READY
```

只表示**本审计步骤完成**。

不代表：

```text
Selection Strategy READY
```

---

# 十八、硬停止

完成后立即 STOP。

不要：

```text
实现 ContextSelector
实现 Recent N
实现 Hybrid
引入 Tokenizer
引入 LLM Relevance
创建真实对话数据
```

如果发现需要用户提供真实脱敏数据才能继续：

只报告：

```text
G1 blocked: real de-identified dataset unavailable
```

等待下一步指令。
