你现在开始执行：

# Phase 4.2 — Architecture Roadmap Recovery & Technical Route Analysis

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、阶段性质

这是：

```text
ARCHITECTURE ANALYSIS ONLY
```

不是编码阶段。

本阶段唯一目标：

> 在 Phase 4.1 已经 RELEASED / MERGED / VERIFIED 的基础上，重新读取真实代码、真实 Git 历史、真实测试和现有架构文档，建立一份准确的 Phase 4.2 技术路线图。

---

# 二、绝对禁止

本阶段禁止修改：

```text
backend/
tests/
docs/architecture.md
docs/requirements.md
Business Semantic
Prompt
Router
Orchestrator
RAG
Tool Framework
Text-to-SQL
Evidence
Annotation
Conversation
ConversationEvidence
Database Schema
```

禁止：

```text
Agent
MCP
Memory
Workflow
Multi-Agent
Planning
自主重规划
Chat UI
Streaming
RBAC
Multi-tenancy
```

禁止：

```text
git commit
git push
git merge
git reset
git rebase
git checkout
git clean
git stash
```

除非只是创建本阶段最终的分析文档；除此之外不得修改项目。

---

# 三、当前已知基线

Phase 4.1 已完成：

```text
Persistence                 PASS
Provenance                  PASS
Annotation                 PASS
Idempotency                PASS
Lifecycle                  PASS
Conversation               PASS
ConversationEvidence       PASS
Real E2E                   PASS
Security                   PASS
Traceability               PASS
Release Readiness          PASS
Git Closeout               PASS
PR Merge                   PASS
Merge Verification        PASS
```

最终：

```text
Phase 4.1 = RELEASED / MERGED / VERIFIED
```

main：

```text
585e0cb8f33c8dd70e332c1f0d333efb2b779f93
```

当前用户已经切换到：

```text
phase4.1-2
```

但是：

**不要假设这个分支已经开始 Phase 4.2。**

---

# 四、Phase 4.2 的核心问题

本阶段首先回答：

> Phase 4.1 已经把 Conversation / Evidence Persistence Foundation 建好了，那么下一阶段到底应该补什么，才能让 Conversation 成为一个真正可持续运行的 AI Runtime？

重点分析：

```text
Conversation
    ↓
Conversation Turn
    ↓
Conversation Context
    ↓
AI Runtime
    ↓
AI Orchestrator
    ↓
RAG / Tool / Text-to-SQL
    ↓
AI Result
    ↓
Evidence
    ↓
ConversationEvidence
    ↓
Trace / Observability
```

需要判断：

哪些已经存在？

哪些只是测试存在？

哪些是 production runtime？

哪些存在接口但没有真正接通？

哪些必须进入 Phase 4.2？

哪些应该继续延期？

---

# 五、第一步：Git 基线确认

执行：

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git log --oneline --decorate -20
git remote -v
```

确认：

```text
main = Phase 4.1 merged state
current branch = phase4.1-2
```

然后：

```powershell
git fetch origin --prune
git log --oneline --decorate --graph --all -40
```

确认：

```text
origin/main
```

仍然包含：

```text
Phase 4.1
```

不要修改任何 Git 状态。

---

# 六、第二步：读取 Phase 4.1 冻结合同

必须重新阅读：

```text
docs/decisions/Phase/Phase 4.1 Roadmap v1.1.md
docs/decisions/Phase/Phase 4.1 Lifecycle Contract.md
```

同时阅读：

```text
docs/evaluation/
docs/decisions/
```

重点寻找：

```text
OD-12
Evidence Builder
Conversation Runtime
Conversation Integration
AI Runtime
Conversation Turn
Trace
```

不要根据记忆推断。

以当前仓库文件为准。

---

# 七、第三步：完整扫描 Conversation 当前实现

必须阅读：

```text
backend/app/
```

搜索：

```text
Conversation
ConversationTurn
ConversationRepository
ChatApplicationService
ConversationContextBuilder
AIOrchestrator
AIOrchestrationResult
assistant_request_id
request_id
trace_id
EvidenceRepository
ConversationEvidenceRepository
```

重点回答：

## 1. Conversation

当前模型：

```text
conversation_id
project_id
status
created_at
updated_at
```

确认是否仍然如此。

---

## 2. ConversationTurn

确认：

```text
turn_id
conversation_id
role
content
assistant_request_id
created_at
```

以及：

```text
USER
ASSISTANT
```

当前角色模型是否还有其他值。

---

## 3. ChatApplicationService

确认：

```text
execute_message()
```

当前真实流程。

尤其区分：

```text
production path
```

和：

```text
test-only path
```

---

# 八、第四步：判断真实 AI Runtime 是否已经接通

重点检查：

```text
ChatApplicationService
        ↓
AIOrchestrator
```

是否是真实生产依赖。

必须明确：

```text
REAL
FAKE
TEST COMPOSED
NOT CONNECTED
```

不要使用：

```text
“理论上可以调用”
```

这种模糊结论。

---

# 九、重点调查 Step 46 暴露的问题

Phase 4.1 Step 46 已知：

```text
Conversation
    ↓
ChatApplicationService
    ↓
FakeOrchestrator
    ↓
AI Result
    ↓
test-composed Evidence
    ↓
ConversationEvidenceRepository
```

现在必须确认生产代码中：

### 问题 A

是否存在：

```text
AI Result
→ Evidence
```

正式 production mapper / builder / factory？

搜索：

```text
EvidenceBuilder
EvidenceFactory
EvidenceMapper
AIResultToEvidence
```

以及所有 Evidence 创建入口。

---

### 问题 B

当前：

```text
AIOrchestrationResult
```

包含哪些字段？

必须画出：

```text
AIOrchestrationResult
        ↓
?
        ↓
EvidenceRecord
```

如果中间没有 production mapping：

明确记录：

```text
Evidence Builder = MISSING
```

---

### 问题 C

ConversationEvidence association 是不是 production runtime 自动创建？

搜索：

```text
ConversationEvidenceRepository.create_association
```

确认所有调用方。

分类：

```text
PRODUCTION
TEST
DOCUMENTATION
```

---

# 十、第五步：Context Runtime 分析

重点检查：

```text
ConversationContextBuilder
```

确认它当前到底提供：

```text
current user message
previous turns
conversation metadata
project context
```

还是只有：

```text
recent messages
```

分析：

```text
Conversation
   ↓
ConversationContext
   ↓
AIOrchestrator
```

当前缺什么。

---

# 十一、不要提前设计 Memory

特别注意：

Conversation Context：

```text
≠ Memory
```

Phase 4.2 只允许：

```text
Conversation-scoped context
```

例如：

```text
current conversation turns
project context
current request
```

禁止在本阶段设计：

```text
long-term memory
user memory
semantic memory
episodic memory
memory retrieval
memory agent
```

---

# 十二、第六步：多轮对话能力分析

确认当前系统是否支持：

```text
Turn 1
User → AI

Turn 2
User → AI

Turn 3
User → AI
```

重点判断：

### A

第二轮是否能够读取第一轮？

### B

Context 是否真正进入 AI Runtime？

### C

RAG 是否能够在 Conversation Context 下运行？

### D

Tool 是否能够使用当前 Conversation Context？

### E

Text-to-SQL 是否能够正确处理：

```text
上一轮：
查询库存最多的10个物料

下一轮：
那前5个呢？
```

注意：

本阶段不是优化 Text-to-SQL。

只是判断：

```text
Conversation Context
```

是否能够到达已有 Text-to-SQL 能力。

---

# 十三、第七步：AI Result → Conversation Turn

确认当前：

```text
AIOrchestrationResult
```

如何转换成：

```text
ConversationTurn(role=ASSISTANT)
```

检查：

```text
content
route
request_id
metadata
error
refusal
```

哪些会进入 ConversationTurn？

哪些只存在 runtime？

哪些需要 Evidence？

必须画出：

```text
AIOrchestrationResult
       ├── ConversationTurn
       ├── Evidence
       ├── Trace
       └── API Response
```

并指出：

```text
current implementation
```

与：

```text
target design
```

的差异。

---

# 十四、第八步：错误模型分析

重点检查：

```text
LLM error
Router error
RAG error
Tool error
Text-to-SQL refusal
Validator rejection
Executor error
Conversation persistence error
Evidence persistence error
```

当前是否都有统一处理？

尤其确认：

```text
AI Runtime failure
```

发生时：

```text
USER turn 是否已经保存？
ASSISTANT turn 是否应该保存？
Evidence 是否创建？
Conversation status 是否改变？
```

不要直接决定。

先分析现有行为和缺口。

---

# 十五、第九步：并发分析

检查当前 Conversation 是否有：

```text
concurrent requests
```

例如：

```text
同一个 conversation
同时发送两个 message
```

分析：

```text
Turn ordering
updated_at
assistant_request_id
duplicate request
race condition
```

但是：

**不要实现并发锁。**

本阶段只识别问题。

---

# 十六、第十步：Idempotency 分析

当前已有：

```text
Evidence idempotency
ConversationEvidence idempotency
```

现在继续检查：

```text
Conversation message idempotency
AI request idempotency
Assistant turn idempotency
```

尤其是：

```text
assistant_request_id
```

现在只是：

```text
correlation only
```

还是已经承担：

```text
idempotency
```

必须根据真实代码判断。

---

# 十七、第十一步：Trace / Observability

读取当前：

```text
LLM usage
Assistant Trace
request_id
AI Runtime observation
```

确认：

```text
Conversation
    ↓
AI request
    ↓
AI Router
    ↓
RAG / Tool / Text-to-SQL
    ↓
LLM
```

是否能够通过：

```text
request_id
```

关联。

如果不能：

记录为：

```text
Phase 4.2 candidate
```

不要马上修改。

---

# 十八、第十二步：API 分析

检查：

```text
backend/app/api/
```

确认当前是否已经存在：

```text
conversation create
conversation list
conversation detail
conversation message
```

以及：

```text
POST /conversation/{id}/messages
```

之类的 API。

明确：

```text
EXISTS
PARTIAL
MISSING
```

---

# 十九、不要把 Chat UI 放进 Phase 4.2

当前阶段只研究：

```text
Backend Conversation Runtime
```

不要实现：

```text
Vue Chat UI
WebSocket
SSE
Streaming UI
Markdown rendering
Chat history UI
```

如果这些能力确实必要：

记录：

```text
Future Phase
```

---

# 二十、第十三步：重新划分 Phase 4.2

完成上述分析后，把 Phase 4.2 拆成：

```text
Phase 4.2
├── Foundation
├── Conversation Runtime
├── Multi-turn Context
├── AI Runtime Integration
├── Evidence Integration
├── Error / Idempotency
├── Trace / Observability
├── E2E
├── Security
└── Release
```

但是：

**不要直接使用这些名字作为最终 Step。**

根据真实代码重新设计。

---

# 二十一、Step 拆解规则

每一个 Step 必须满足：

```text
一个明确目标
一个明确边界
一个明确输出
一组明确测试
一个明确 STOP 条件
```

例如：

```text
Step X
目标：
实现真实 Conversation → AI Runtime Integration

输入：
ConversationContext

输出：
AIOrchestrationResult

测试：
...

禁止：
...
```

不要出现：

```text
Step X
完成 Conversation Runtime 全部功能
```

这种大步骤。

---

# 二十二、每个 Step 必须标记

为每一个候选 Step 标记：

```text
DEPENDENCY
INPUT
OUTPUT
PRODUCTION CODE
TEST CODE
DB CHANGE
API CHANGE
SECURITY IMPACT
RISK
```

例如：

```text
DB CHANGE:
NO

API CHANGE:
NO

Security:
MEDIUM
```

---

# 二十三、Phase 4.2 核心边界

最终必须明确：

### Phase 4.2 可以做

```text
Conversation Runtime
Conversation Context
真实 AI Orchestrator Integration
Multi-turn Conversation
AI Result Persistence
Evidence Runtime Integration
Conversation Evidence Association
Error Semantics
Idempotency
Trace correlation
Conversation E2E
```

### Phase 4.2 不做

```text
Agent
MCP
Memory
Workflow
Planning
Multi-Agent
RBAC
Multi-tenancy
Chat UI
Streaming
Long-term Memory
```

如果分析发现某项必须提前：

不要自行扩大范围。

标记：

```text
DEPENDENCY / FUTURE PHASE
```

---

# 二十四、重点架构问题

必须回答下面 12 个问题：

```text
Q1  Conversation 的 Runtime Entry Point 是什么？

Q2  User Message 的唯一身份是什么？

Q3  assistant_request_id 到底承担什么职责？

Q4  ConversationContext 的边界是什么？

Q5  Context 如何进入 AIOrchestrator？

Q6  AIOrchestrationResult 如何进入 ConversationTurn？

Q7  AI Result 如何生成 Evidence？

Q8  Evidence 与 ConversationEvidence 谁负责创建？

Q9  AI Runtime failure 时 Conversation 如何保持一致？

Q10 多轮消息如何保证顺序？

Q11 同一个请求重复提交如何处理？

Q12 request_id 如何贯穿 Conversation → AI Runtime → Evidence → Trace？
```

每个问题必须给：

```text
Current State
Contract Status
Gap
Recommended Phase
Reason
```

---

# 二十五、禁止“凭经验设计”

不能因为：

```text
LangGraph 通常这样做
Dify 通常这样做
OpenAI 通常这样做
```

就直接采用。

必须：

```text
Repository Code
+
Existing Tests
+
Frozen Contracts
+
Architecture Docs
```

四者共同决定。

---

# 二十六、对现有 Phase 3 能力做复用分析

必须检查：

```text
AIOrchestrator
AI Router
RAG
Tool Registry
Text-to-SQL
SQL Validator
ReadOnlySQLExecutor
Assistant Trace
LLM Usage
```

明确：

```text
可以直接复用
需要 Adapter
需要 Context Integration
需要修改 Contract
不能使用
```

---

# 二十七、不要重新实现 Phase 3

特别检查：

```text
AIOrchestrator
Router
RAG
Tool
Text-to-SQL
```

如果已经存在：

```text
Phase 4.2 = Integration
```

而不是：

```text
Phase 4.2 = rewrite
```

---

# 二十八、输出一张完整架构图

最终必须输出：

```text
                         User Message
                              │
                              ▼
                     Conversation Runtime
                              │
                    ┌─────────┴─────────┐
                    │                   │
              Conversation        Context Builder
                    │                   │
                    └─────────┬─────────┘
                              ▼
                       AI Orchestrator
                              │
                       AI Router
                    ┌─────────┼─────────┐
                    ▼         ▼         ▼
                   RAG       Tool    Text-to-SQL
                    │         │         │
                    └─────────┼─────────┘
                              ▼
                     AIOrchestrationResult
                         │       │
                  ┌──────┘       └──────┐
                  ▼                     ▼
          ConversationTurn          Evidence
                                        │
                                        ▼
                              ConversationEvidence
                                        │
                                        ▼
                                   PostgreSQL
```

然后在每条箭头旁标记：

```text
CURRENT
MISSING
PHASE 4.2
FUTURE
```

---

# 二十九、最终输出文档

新增：

```text
docs/decisions/Phase/Phase 4.2 Roadmap v1.0.md
```

注意：

**这是本阶段唯一允许新增的项目文件。**

如果当前仓库已有更适合的 Phase 4.2 roadmap 文件：

先判断是否已经存在。

如果存在：

不要覆盖。

根据实际状态决定是否创建新的版本。

---

# 三十、Roadmap 文档必须包含

```text
1. Phase 4.2 Goal

2. Phase 4.1 Baseline

3. Current Architecture

4. Current Conversation Runtime

5. Current AI Runtime

6. Current Evidence Runtime

7. Current Trace / Observability

8. Gap Analysis

9. Architecture Target

10. Frozen Contracts

11. Open Decisions

12. Step-by-Step Roadmap

13. Dependency Graph

14. Security Boundaries

15. Database Change Plan

16. API Change Plan

17. Testing Strategy

18. Release Gate

19. Deferred

20. Out of Scope
```

---

# 三十一、Frozen Contract

不要在本阶段偷偷冻结所有细节。

只冻结已经有足够证据的：

```text
Phase 4.1 contracts
```

对于新问题：

```text
UNDEFINED
```

就明确标记：

```text
OPEN DECISION
```

不要为了让 Roadmap 看起来完整而随意决定。

---

# 三十二、Open Decisions

必须列出所有真正需要下一步决定的问题。

例如：

```text
OD-14 Conversation request idempotency
OD-15 Conversation turn ordering
OD-16 AI Result persistence semantics
OD-17 Evidence creation ownership
OD-18 Conversation runtime transaction boundary
OD-19 AI failure turn semantics
OD-20 Context window policy
```

但：

**不能直接假设这些编号一定存在。**

根据真实分析结果编号。

---

# 三十三、Dependency Graph

最终给出：

```text
Conversation Runtime
        │
        ├── Conversation Context
        │
        ├── AI Orchestrator Integration
        │
        ├── AI Result Persistence
        │
        ├── Evidence Integration
        │
        ├── Idempotency
        │
        ├── Error Semantics
        │
        └── Trace Correlation
```

以及：

```text
Step A
  ↓
Step B
  ↓
Step C
  ├── Step D
  └── Step E
        ↓
      Step F
        ↓
      E2E
        ↓
      Security
        ↓
      Release
```

---

# 三十四、Release Gate

Phase 4.2 最终应该至少要求：

```text
Conversation Runtime       PASS
Multi-turn Context        PASS
AI Runtime Integration    PASS
AI Result Persistence     PASS
Evidence Integration      PASS
Idempotency               PASS
Error Semantics           PASS
Trace Correlation         PASS
Security                  PASS
E2E                       PASS
Regression                PASS
Production DB writes      0
```

但注意：

这是候选 Release Gate。

如果分析后发现某项不属于 Phase 4.2：

标记：

```text
DEFERRED
```

不要强行加入。

---

# 三十五、测试策略

必须区分：

```text
Unit
Integration
DB Integration
Conversation E2E
Security E2E
Regression
```

默认：

```text
Real LLM = SKIP
```

除非已有安全的 smoke infrastructure。

不要为了 Phase 4.2 新建一套 Fake LLM Framework。

优先复用 Phase 3 / Phase 4.1。

---

# 三十六、数据库原则

Phase 4.2 不得因为“感觉需要”就增加表。

对于每一个候选 DB change：

必须回答：

```text
Why?
Existing table insufficient?
Frozen contract?
Transaction requirement?
Read model?
Idempotency?
```

如果没有充分理由：

```text
NO DB CHANGE
```

---

# 三十七、API 原则

不要为了“未来方便”提前新增 API。

只有当：

```text
Conversation Runtime
```

真实需要 API boundary 时才列入 roadmap。

并明确：

```text
Internal Application Service
vs
HTTP API
```

两者不能混为一谈。

---

# 三十八、性能原则

本阶段只分析：

```text
Conversation Context size
LLM latency
DB round trips
Evidence persistence
Trace persistence
```

不要进行性能优化。

只记录：

```text
Potential bottleneck
```

---

# 三十九、安全原则

必须分析：

```text
Conversation isolation
project isolation
Evidence isolation
Cross-conversation reference
Credential leakage
Prompt injection boundary
Tool permission boundary
SQL permission boundary
```

但：

```text
RBAC
Multi-tenancy
ACL
Object-level Authorization
```

仍然保持：

```text
OUT OF SCOPE
```

除非现有 Frozen Contract 已经要求提前解决。

---

# 四十、最终必须给出三个结果

## Result A

```text
Phase 4.2 Target Architecture
```

## Result B

```text
Phase 4.2 Step-by-Step Roadmap
```

至少拆成：

```text
Step 1
Step 2
Step 3
...
```

数量根据真实复杂度决定。

不要为了凑数量拆步骤。

## Result C

```text
Phase 4.2 Open Decisions
```

---

# 四十一、最终报告格式

完成后严格输出：

```text
【Phase 4.2 Roadmap Analysis COMPLETE】

1. Phase 4.1 Baseline
2. Current Conversation Runtime
3. Current AI Runtime
4. Current Evidence Runtime
5. Current Trace / Observability
6. Current API
7. Gap Analysis
8. Target Architecture
9. Frozen Contracts
10. Open Decisions
11. Proposed Steps
12. Dependency Graph
13. Database Plan
14. API Plan
15. Security Boundary
16. Testing Strategy
17. Release Gate
18. Deferred
19. Out-of-Scope
20. 新增文件
21. Git 状态
```

---

# 四十二、最重要的 STOP 条件

本阶段完成：

```text
阅读
→ 代码审计
→ Contract Recovery
→ Gap Analysis
→ Target Architecture
→ Step Decomposition
→ Roadmap
```

之后：

**立即停止。**

不要：

```text
开始 Step 1
修改 Conversation
修改 ChatApplicationService
修改 AIOrchestrator
新增 API
新增 DB table
```

必须等用户明确：

```text
开始 Phase 4.2 Step 1
```

之后才能进入实现阶段。

# END — Phase 4.2 Roadmap Analysis
