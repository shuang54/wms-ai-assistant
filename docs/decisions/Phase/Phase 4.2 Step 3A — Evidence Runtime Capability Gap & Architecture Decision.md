你现在开始执行：

# Phase 4.2 Step 3A — Evidence Runtime Capability Gap & Architecture Decision

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、阶段目标

Phase 4.2 Step 3 已经完成。

Step 3 的结论是：

```text
AI Result → Evidence Builder Contract
```

当前存在明确 Runtime Capability Gap：

```text
Evidence Model
    ↓
只有 provenance / lifecycle / annotation metadata
    ↓
没有 content/data/raw_content
```

同时：

```text
AI Runtime
    ↓
没有 dataset_version
```

因此：

```text
Step 4 Evidence Runtime Integration
```

当前被 BLOCKED。

本阶段 Step 3A 的唯一目标：

> **确认这个 Capability Gap 是当前架构的设计意图，还是 Phase 4.1 Evidence Model 尚未具备 Runtime Evidence 所需能力，并据此做最小架构决策。**

本阶段：

**只做 Architecture Decision。**

不实现 Runtime。

不实现 Evidence Builder。

不修改生产代码。

不修改数据库。

不修改 API。

不写 Evidence。

---

# 二、必须先阅读

先不要修改任何文件。

阅读：

```text
backend/app/db/models/evidence.py
backend/app/db/models/evidence_annotation_record.py
backend/app/db/models/conversation_evidence.py

backend/app/services/
backend/app/db/
backend/app/api/

tests/
```

重点检查：

```text
Evidence
EvidenceAnnotation
ConversationEvidence
Evidence Repository
Evidence Service
```

然后阅读：

```text
docs/decisions/Phase/Phase 4.2 Roadmap v1.0.md

docs/evaluation/Phase 4.2 Step 0 — Roadmap Freeze Audit.md
docs/evaluation/Phase 4.2 Step 1 — Message Idempotency Contract.md
docs/evaluation/Phase 4.2 Step 1A — OD-22 Idempotency Architecture Decision.md
docs/evaluation/Phase 4.2 Step 2 — AI Result Assistant Turn Semantics.md
docs/evaluation/Phase 4.2 Step 3 — Evidence Builder Contract.md
```

同时搜索 Phase 4.1 中：

```text
Evidence
provenance
dataset_version
source_type
REAL_DEIDENTIFIED
SYNTHETIC_ONLY
ConversationEvidence
Annotation
Review
```

---

# 三、首先确认一个事实

Step 3 已经确认：

```text
evidence_record
```

当前字段：

```text
evidence_id
dataset_version
source_type
de_identification_attested
de_identification_method
status
created_at
updated_at
```

并且：

```text
SOURCE_TYPE_VALUES =
(
    REAL_DEIDENTIFIED,
    SYNTHETIC_ONLY
)
```

Evidence business uniqueness：

```text
(source_type, dataset_version)
```

必须重新从真实代码确认。

**如果真实代码与 Step 3 报告不同：**

不要猜。

以真实代码为准，并报告：

```text
Step 3 finding discrepancy
```

---

# 四、核心问题：Evidence 到底是什么？

必须回答：

> Phase 4.1 的 Evidence Domain Object 到底代表什么？

必须从：

```text
Evidence Model
Annotation Model
ConversationEvidence
Review lifecycle
Phase 4.1 design
```

推导。

重点判断 Evidence 更接近：

### Model A

```text
Evidence
=
durable source/provenance/review record
```

还是：

### Model B

```text
Evidence
=
runtime business fact / source content
```

还是：

### Model C

```text
Evidence
=
两者的 metadata wrapper
```

不要根据名字猜。

必须给出：

```text
Evidence Semantic Conclusion
```

并引用真实代码 / Phase 4.1 Contract。

---

# 五、分析当前 Evidence Model 能力边界

建立明确表格：

```text
Capability | Current Support | Required by Runtime Evidence | Gap
```

至少包含：

```text
Evidence identity
Source type
Dataset version
Source content
Structured result
Source locator
Provenance
De-identification attestation
Lifecycle
Annotation
Review
Conversation reference
Runtime correlation
Deduplication
```

重点区分：

```text
已经支持
```

与：

```text
未来可能需要
```

不要把 Future Requirement 当成 Current Contract。

---

# 六、分析 AI Runtime 与 Evidence Runtime 的边界

当前：

```text
AIOrchestrationResult
```

包含：

```text
route
content
data
metadata
```

以及：

```text
metadata.request_id
metadata.outcome
metadata.refused
```

而 Evidence：

```text
没有 content
没有 data
没有 route
没有 request_id
没有 conversation_id
没有 turn_id
没有 idempotency_key
```

必须分析：

```text
AI Runtime Result
        ↓
        ?
        ↓
Durable Evidence
```

这里缺失的到底是：

```text
Evidence Builder
```

还是：

```text
Evidence Artifact / Source Content Layer
```

还是：

```text
Dataset / Source Registry
```

不要把所有问题都归咎于 Builder。

---

# 七、分析 dataset_version 缺口

Step 3 已确认：

```text
AI Runtime
    ↓
没有 dataset_version
```

必须继续追踪：

### RAG

检查：

```text
Knowledge Base
Knowledge Document
Knowledge Chunk
Embedding
Retrieval
RAG Result
```

是否存在任何：

```text
dataset_version
version
snapshot
revision
```

### Tool

检查：

```text
Tool Registry
Tool Result
Tool metadata
```

是否存在 dataset/snapshot/version。

### Text-to-SQL

检查：

```text
Text-to-SQL
Database Context
Schema
Evaluation Dataset
```

明确区分：

```text
Evaluation dataset_version
```

与：

```text
Runtime database snapshot/version
```

不能因为离线 Evaluation 已经存在：

```text
dataset_version
```

就认为 Runtime 已经有。

---

# 八、非常重要：不要把 Evaluation Dataset 当 Runtime Dataset

必须明确：

```text
tests/fixtures/text_to_sql/
```

中的：

```text
dataset_version
```

如果只是 Regression / Evaluation metadata：

**不能直接作为生产 AI Runtime Evidence 的 dataset_version。**

需要确认：

```text
Evaluation Dataset
≠
Production Runtime Dataset
```

除非真实代码已经明确建立两者关系。

---

# 九、分析三个架构方向

只分析，不实现。

---

## Architecture A：扩展现有 Evidence Record

概念：

```text
AI Result
   ↓
Evidence Builder
   ↓
evidence_record
   ├── provenance
   ├── lifecycle
   └── content / payload
```

需要分析：

### 优点

```text
模型简单
查询简单
生命周期统一
```

### 缺点

可能把：

```text
Provenance Record
```

和：

```text
Runtime Evidence Artifact
```

混为一个对象。

重点检查：

```text
RAG chunk
Tool result
SQL result
```

是否都能合理塞进同一个 Evidence。

不要只考虑数据库实现成本。

---

# 十、Architecture B：Runtime Evidence Artifact + Evidence Record

概念：

```text
AI Result
    ↓
Runtime Evidence Artifact
    ↓
Evidence Record
```

例如：

```text
RuntimeEvidence
    ↓
content / structured data / source reference
    ↓
Evidence
    ↓
provenance / lifecycle / review
```

重点分析：

```text
Evidence
=
governance / provenance / review object

Runtime Evidence Artifact
=
actual source/result object
```

这种模型是否更符合当前已有：

```text
Evidence
Annotation
Review
ConversationEvidence
```

语义。

也分析：

```text
ConversationEvidence
```

到底应该关联：

```text
Evidence Record
```

还是：

```text
Runtime Artifact
```

不要修改当前 schema，只做架构分析。

---

# 十一、Architecture C：Runtime Evidence 暂不持久化

概念：

```text
AI Result
    ↓
Assistant Turn
```

Evidence 仍然只服务：

```text
Dataset
Review
Annotation
Evaluation
```

运行时：

```text
AI Result
    ↓
Trace
```

而不是：

```text
AI Result
    ↓
Evidence
```

分析这种方式是否意味着：

```text
Step 4
```

应该被重新定义为：

```text
No Runtime Evidence Integration
```

如果是：

必须明确说明：

```text
Phase 4.2 Runtime Evidence = deferred
```

以及为什么。

---

# 十二、不要为了“Step 4 READY”强行选方案

最终必须基于：

```text
Phase 4.1 original semantics
Current code
Current tests
Existing domain boundaries
```

选择。

如果证据不足：

```text
Decision = DEFERRED
```

也是合法结果。

但必须明确：

```text
为什么无法决策
缺少什么信息
哪个未来 Phase 负责
```

---

# 十三、重点分析 Evidence 与 Annotation 的关系

当前：

```text
Evidence
 ↓
Annotation
 ↓
Review
```

如果 Evidence 是：

```text
AI Runtime output
```

那么需要判断：

```text
RAG chunk
Tool result
SQL result
```

是否都适合进入：

```text
Annotation
```

如果不适合：

可能说明：

```text
Evidence
```

本身并不是 Runtime Result。

必须从真实 Annotation Model：

```text
case_id
annotation_version
annotator_id
review_status
```

推导其真实业务目的。

---

# 十四、重点分析 Evidence 与 ConversationEvidence

当前关系：

```text
Conversation
    ↕
ConversationEvidence
    ↕
Evidence
```

Phase 4.1 已冻结：

```text
Conversation → 0..N Evidence
Evidence → 0..N Conversation
```

并且：

```text
Evidence reusable
```

必须分析：

如果 Runtime 每次：

```text
AI Result
→ Evidence
```

都会产生一个新的 Evidence：

那么：

```text
Evidence reuse
```

如何发生？

而当前：

```text
(source_type, dataset_version)
UNIQUE
```

意味着同一个：

```text
REAL_DEIDENTIFIED + dataset_version
```

可能只有一个 Evidence Record。

这说明：

**Evidence 很可能代表 Dataset / Source Provenance Unit，而不是一次 AI Answer。**

这是本 Step 必须重点验证的架构线索。

不要直接下结论。

---

# 十五、分析三种 Runtime Result

分别研究：

## RAG

```text
Knowledge Chunk
```

它天然可能有：

```text
source
document
chunk
version
```

如果当前代码没有 version：

记录 Gap。

---

## Tool

```text
Tool Result
```

它可能只是一次 runtime result。

分析：

```text
Tool Result
```

是否具备：

```text
stable source
dataset
version
content
```

如果没有：

不要强行成为 Evidence。

---

## Text-to-SQL

```text
Database Result
```

分析：

```text
database snapshot
schema version
query
result
```

当前是否存在可持久化的 stable source identity。

特别注意：

```text
SQL text
```

本身不等于：

```text
dataset_version
```

---

# 十六、形成 Capability Matrix

必须输出：

```text
Capability Matrix

                     RAG      Tool      Text-to-SQL
----------------------------------------------------
Stable source         ?         ?             ?
Content               ?         ?             ?
Dataset version       ?         ?             ?
De-identification     ?         ?             ?
Provenance            ?         ?             ?
Durable identity      ?         ?             ?
Reviewable            ?         ?             ?
Current Evidence fit  ?         ?             ?
```

每个 `?` 必须来自代码事实。

不要猜。

---

# 十七、必须分析三个最小决策

最终至少形成：

## Decision 1

```text
Evidence Semantic
```

到底是：

```text
Source / Provenance Record
```

还是：

```text
Runtime Evidence Artifact
```

还是：

```text
Hybrid
```

---

## Decision 2

```text
Runtime Evidence Content
```

应该：

```text
进入 Evidence
```

还是：

```text
独立 Artifact
```

还是：

```text
暂不持久化
```

---

## Decision 3

```text
dataset_version
```

是否：

```text
已有能力
```

还是：

```text
必须新增 Runtime Dataset Version Contract
```

如果必须新增：

**本阶段不要实现。**

只建立新的 Open Decision。

---

# 十八、Open Decisions

如果需要新增 Decision：

继续使用：

```text
OD-XX
```

先检查现有编号，避免重复。

Step 3 已有：

```text
OD-26
OD-30
```

不要覆盖。

例如如果下一个可用编号是：

```text
OD-31
```

可以定义：

```text
OD-31 Runtime Evidence Artifact Architecture
OD-32 Runtime Dataset Version Contract
```

但：

**只有实际需要才创建。**

---

# 十九、严格禁止

本阶段禁止：

```text
❌ 修改 Evidence Model
❌ 新增 Evidence 字段
❌ 新增 Evidence Artifact table
❌ 新增 dataset_version runtime field
❌ 修改 ConversationEvidence
❌ 修改 Annotation
❌ 修改 ChatApplicationService
❌ 修改 AIOrchestrator
❌ 修改 RAG
❌ 修改 Tool
❌ 修改 Text-to-SQL
❌ 修改 API
❌ Migration
❌ DB write
❌ Evidence Builder implementation
❌ Runtime Evidence implementation
```

---

# 二十、唯一允许新增文件

只允许：

```text
docs/evaluation/Phase 4.2 Step 3A — Evidence Runtime Capability Decision.md
```

如果文件已经存在，则更新。

除此之外：

```text
Backend = 0
Tests = 0
DB = 0
API = 0
```

---

# 二十一、Step 3A 完成标准

必须明确回答：

```text
1. Evidence 当前到底是什么？
2. Evidence 是否适合承载 Runtime AI Result？
3. 为什么当前没有 content？
4. 为什么 source_type 只有 REAL_DEIDENTIFIED / SYNTHETIC_ONLY？
5. dataset_version 从哪里来？
6. RAG 能否产生 Evidence？
7. Tool 能否产生 Evidence？
8. Text-to-SQL 能否产生 Evidence？
9. Evidence 与 Annotation 的关系是什么？
10. Evidence 与 ConversationEvidence 的关系是什么？
11. Runtime Artifact 是否需要独立存在？
12. 三个架构方案 A/B/C 的取舍是什么？
13. 最终选择什么？
14. 是否新增 OD？
15. Step 4 为什么 READY / BLOCKED？
```

---

# 二十二、最终报告格式

完成后严格按照：

```text
【Phase 4.2 Step 3A COMPLETE】

1. Evidence Domain Semantics
2. Current Capability Matrix
3. AI Runtime → Evidence Gap
4. dataset_version Analysis
5. RAG Analysis
6. Tool Analysis
7. Text-to-SQL Analysis
8. Annotation Boundary
9. ConversationEvidence Boundary
10. Architecture A
11. Architecture B
12. Architecture C
13. Final Architecture Decision
14. Runtime Evidence Decision
15. Runtime Dataset Version Decision
16. New Open Decisions
17. Step 3 Impact
18. 新增文件
19. 修改文件
20. Backend
21. Tests
22. DB
23. API
24. Step 4 Readiness
```

最终必须明确：

```text
Step 3A = COMPLETE
```

以及：

```text
Step 4 = READY
```

或者：

```text
Step 4 = BLOCKED
```

如果仍然 BLOCKED：

必须明确：

```text
BLOCKER
→ 为什么
→ 缺什么
→ 下一步应该解决什么
```

---

# 二十三、最终 STOP

完成 Step 3A 后：

**立即停止。**

不要：

```text
Step 4
Evidence Builder
Evidence Runtime
DB Migration
API
```

全部不要做。

本阶段唯一目标：

```text
Evidence Runtime Capability
        ↓
Architecture Decision
        ↓
明确 Step 4 是否具备实施条件
```
