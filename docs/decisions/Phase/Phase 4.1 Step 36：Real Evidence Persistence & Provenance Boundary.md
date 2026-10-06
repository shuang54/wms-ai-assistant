# Phase 4.1 Step 36：Real Evidence Persistence & Provenance Boundary

## 一、阶段定位

这是一个**新定义的 Phase 4.1 Step 36**。

注意：

仓库中不存在历史 Step 36，本任务不是恢复旧任务。

本任务基于 Phase 4.1 Step 31～35 已完成能力，继续完善：

```text
Real Evidence Import
        ↓
Real Annotation Execution
        ↓
Evidence Persistence
        ↓
Provenance
        ↓
Review / Finalization
```

唯一目标：

**确认并补齐 Real Evidence 与 Annotation 的持久化边界，使已经执行成功的真实 Evidence / Annotation 能够被可靠保存、关联、追踪，并具备明确 provenance。**

---

# 二、开始编码前必须阅读

先阅读真实代码，不要假设接口。

重点检查：

```text
backend/app/
```

搜索：

```text
Evidence
Annotation
Selection
Provenance
Import
Persistence
Repository
ORM
Conversation
Message
Review
Finalization
```

同时阅读：

```text
docs/decisions/Phase/Phase 4.1 Step 31*.md
docs/decisions/Phase/Phase 4.1 Step 32*.md
docs/decisions/Phase/Phase 4.1 Step 33*.md
docs/decisions/Phase/Phase 4.1 Step 34*.md
docs/decisions/Phase/Phase 4.1 Step 35*.md

docs/evaluation/Phase 4.1 Step 31*.md
docs/evaluation/Phase 4.1 Step 32*.md
```

以及：

```text
AGENTS.md
docs/requirements.md
docs/architecture.md
```

检查当前数据库：

```text
ORM models
Alembic migrations
repositories
services
tests
fixtures
```

---

# 三、第一原则：不要重复造已有能力

先建立当前真实状态表：

| 能力                       | 当前是否存在 | 代码位置 | 测试位置 | 是否完整 |
| ------------------------ | ------ | ---- | ---- | ---- |
| Evidence ORM             | ?      | ?    | ?    | ?    |
| Evidence Repository      | ?      | ?    | ?    | ?    |
| Evidence Service         | ?      | ?    | ?    | ?    |
| Annotation ORM           | ?      | ?    | ?    | ?    |
| Annotation Repository    | ?      | ?    | ?    | ?    |
| Annotation Service       | ?      | ?    | ?    | ?    |
| Evidence ↔ Annotation 关联 | ?      | ?    | ?    | ?    |
| Provenance               | ?      | ?    | ?    | ?    |
| Idempotency              | ?      | ?    | ?    | ?    |
| Transaction boundary     | ?      | ?    | ?    | ?    |
| Review state             | ?      | ?    | ?    | ?    |
| Finalization state       | ?      | ?    | ?    | ?    |

如果某项已经完整实现：

**直接复用，不重新实现。**

---

# 四、Persistence Boundary

本阶段需要明确：

```text
Evidence
```

究竟什么时候被认为是：

```text
imported
persisted
annotated
reviewed
finalized
```

不得通过隐式 boolean 或散落字符串定义状态。

先检查项目当前是否已经存在状态模型。

如果已经存在：

**沿用现有设计。**

不要为了本阶段重新设计整个状态机。

---

# 五、Evidence Persistence

确认 Real Evidence Import 成功后：

```text
Evidence
 ↓
Persistence
```

必须能够可靠保存至少：

```text
evidence identity
source
source reference / locator
content reference
provenance
created_at
updated_at
```

具体字段必须以现有项目模型和 Step 31 Contract 为准。

**禁止自行创造不属于现有业务契约的字段。**

---

# 六、Provenance

必须确认 Evidence 能够回答：

```text
这个 Evidence 从哪里来？
什么时候导入？
对应哪个 source？
是否经过 Annotation？
当前版本是什么？
```

如果项目已有 Provenance Contract：

**严格复用。**

如果没有：

先定义最小 Provenance Contract。

不要扩展成复杂审计平台。

---

# 七、Evidence ↔ Annotation 关联

确认：

```text
Evidence
   ↓
Annotation
```

之间存在稳定关联。

至少能够回答：

```text
一个 Annotation 属于哪个 Evidence？
一个 Evidence 当前有哪些 Annotation？
```

不得只依赖：

```text
content
source name
文本匹配
```

进行关联。

必须使用稳定 identity。

---

# 八、Idempotency

重点验证：

同一个 Evidence 重复 Import：

```text
Import
Import
Import
```

不会无条件产生重复记录。

先检查项目是否已有：

```text
external_id
source_id
content_hash
evidence_hash
idempotency_key
```

如果已有：

**复用。**

如果没有，再根据 Step 31 的真实 Contract 判断是否需要最小幂等机制。

不要自行设计复杂 distributed lock。

---

# 九、Transaction Boundary

必须明确：

```text
Import
 ↓
Evidence Persistence
 ↓
Annotation Persistence
```

哪些操作属于同一个事务。

至少不能出现：

```text
Evidence 已保存
Annotation 保存失败
但系统认为整个操作成功
```

或者：

```text
Annotation 已保存
Evidence 不存在
```

这样的孤儿数据。

具体事务设计必须遵循当前 Repository / Service 架构。

---

# 十、Database Safety

如果需要新增 migration：

必须：

```text
仅新增必要结构
```

禁止：

```text
修改已有业务表结构
删除已有数据
生产数据迁移
写入开发环境真实 WMS 数据
```

测试必须使用：

```text
test database
fixture
transaction rollback
```

优先复用已有 DB fixture。

---

# 十一、测试要求

至少覆盖：

## 1. Evidence Persistence

```text
Import
 ↓
Persist
 ↓
Read
```

验证 Evidence 可以被正确读取。

---

## 2. Annotation Association

```text
Evidence
 ↓
Annotation
```

验证关联正确。

---

## 3. Provenance

验证：

```text
source
source reference
identity
timestamps
```

等现有 Contract 要求的信息能够保存并读取。

---

## 4. Idempotency

重复执行相同 Evidence Import：

```text
first import
second import
```

验证不会产生不符合 Contract 的重复 Evidence。

---

## 5. Transaction Failure

人为制造：

```text
Evidence persistence success
Annotation persistence failure
```

验证不会留下违反事务边界的半成品数据。

---

## 6. Isolation

验证：

```text
Evidence A
Annotation A

Evidence B
Annotation B
```

不会发生交叉关联。

---

# 十二、Security

禁止 Persistence 层保存：

```text
API Key
Password
Authorization Header
Database URL
Connection String
LLM Secret
```

如果 Evidence 本身包含敏感字段：

必须遵循当前项目已有安全边界。

不要为了本阶段新增复杂脱敏平台。

---

# 十三、API

本阶段：

**不要新增 Chat API。**

如果现有 API 已经能够覆盖 Persistence：

直接复用。

只有在现有架构明确要求、且缺失会阻碍本阶段验证时，才允许增加最小 API。

否则：

```text
API changes = 0
```

---

# 十四、禁止范围

本阶段禁止：

```text
Agent
MCP
Memory
Planning
Multi-Agent
自主重规划
Chat UI
Streaming
复杂 Benchmark
向量数据库重构
RAG 算法重构
Text-to-SQL 重构
Tool Framework 重构
```

也禁止修改：

```text
AI Router
AI Orchestrator
Text-to-SQL Generator
SQL Validator
SQL Executor
```

除非发现明确的阻塞性 bug。

如果发现核心代码问题：

先记录：

```text
问题
影响
根因
是否阻塞 Step 36
```

不要为了测试通过而修改无关核心代码。

---

# 十五、如果发现 Persistence 已经完整

非常重要：

如果审计发现：

```text
Evidence Persistence
Annotation Persistence
Provenance
Association
Idempotency
Transaction
```

实际上已经完整存在：

**不要重复实现。**

直接形成：

```text
Step 36 Persistence Boundary Audit = PASS
```

并记录：

```text
Existing implementation
Existing tests
Existing contracts
No additional production code required
```

然后 STOP。

---

# 十六、Evaluation Document

新增：

```text
docs/evaluation/Phase 4.1 Step 36：Real Evidence Persistence Evaluation.md
```

记录：

```text
1. Current Architecture
2. Existing Persistence Components
3. Evidence Contract
4. Annotation Contract
5. Provenance Contract
6. Evidence ↔ Annotation Association
7. Idempotency
8. Transaction Boundary
9. Security Boundary
10. Test Results
11. DB Writes
12. Remaining Gaps
```

---

# 十七、Decision Document

新增：

```text
docs/decisions/Phase/Phase 4.1 Step 36：Real Evidence Persistence & Provenance Boundary.md
```

必须明确：

```text
Decision
Context
Existing Implementation
Required Changes
Non-goals
Transaction Boundary
Provenance Boundary
Idempotency Boundary
Security Boundary
Test Evidence
```

---

# 十八、测试命令

先运行针对性测试：

```powershell
python -m pytest -q <相关测试>
```

如果涉及 DB：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q <相关测试>
```

然后：

```powershell
python -m pytest -q
```

并执行项目现有：

```text
compileall
lint
LSP
```

不要因为本阶段不涉及某项能力就擅自跳过已有 CI 要求。

---

# 十九、Git

本 Step 遵循当前项目已经建立的 Phase Git Governance。

不要直接修改：

```text
main
```

从当前：

```text
phase4.1
```

建立 Step 36 工作分支。

分支命名遵循当前项目已有规范。

完成后：

```text
Audit
 ↓
Implementation（如果确有必要）
 ↓
Tests
 ↓
Evaluation
 ↓
Decision
 ↓
Commit
 ↓
Push
 ↓
PR
 ↓
Merge
 ↓
Merge Verification
```

每一步都保持可审查。

GitHub 官方也建议通过独立 branch、focused change、commit 和 PR 保持变更可审查。

---

# 二十、最终报告

严格输出：

```text
【Phase 4.1 Step 36 COMPLETE】

1. Step 定义
2. Existing Persistence 状态
3. Evidence Persistence
4. Annotation Persistence
5. Evidence ↔ Annotation
6. Provenance
7. Idempotency
8. Transaction Boundary
9. Security
10. 新增文件
11. 修改文件
12. 测试结果
13. DB writes
14. API 是否修改
15. 发现并修复的问题
16. 当前限制
17. 是否需要 Step 37
```

如果没有生产代码修改：

明确写：

```text
Production Code Changes = 0
```

如果没有数据库变化：

```text
DB Schema Changes = 0
DB Writes = 0
```

---

# 二十一、STOP 条件

完成 Step 36 后：

**立即停止。**

不要自行进入：

```text
Step 37
Phase 4.2
Agent
MCP
Memory
Chat API
```

必须等待下一步指令。
