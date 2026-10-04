# Phase 4.1 Step 27 — Evidence Provenance & Version Freeze Audit

- 阶段：Phase 4.1（Conversation / Chat 会话层）
- 类型：**Contract only**（Provenance / Version Freeze；不导入真实数据）
- 前置：Step 26（Real WMS Evidence Input Boundary）
- 本阶段只解决一个问题：

```text
当未来第一份 REAL_DEIDENTIFIED WMS Evidence Artifact 进入 Evaluation Layer 时，
如何证明它"是哪一版、来自哪次采样、是否被替换、是否与报告对应"。
```

---

## 1. Current State

```text
Real Evidence Artifact
        ↓
Step 26 Validation（EvidenceDatasetValidator / Artifact Schema）
        ↓
G1
        ↓
G2 / G3 / G4（全部依赖 G1 READY）
```

当前（无真实 artifact）：

```text
G1 = BLOCKED
G2 = INSUFFICIENT
G3 Contract = READY
G3 Evidence = BLOCKED
G4 = BLOCKED
Selection Strategy = BLOCKED
```

核心区分：

```text
Artifact Schema  ≠  Provenance

Artifact 描述：有什么数据；
Provenance 描述：这份数据是哪一版。
```

---

## 2. Provenance

仅增加**测试/文档层面**的概念（不进 production）：

```text
EvidenceProvenance
    dataset_version
    source_type
```

约束：

```text
dataset_version：非空 / 稳定（无前后空白）/ 可比较；
source_type ∈ {REAL_DEIDENTIFIED, SYNTHETIC_ONLY}；
payload key 闭集（除上述两个字段外的 key 一律拒绝）。
```

示例：

```text
wms-conversation-v1
wms-conversation-v2
```

明确**不规定**：

```text
semantic versioning · Git SHA · timestamp · machine id ·
hostname · absolute path · database id
```

---

## 3. Version Semantics

```text
dataset_version != app_version
```

冻结：

```text
* 禁止 dataset_version = project version；
* 禁止自动从 package version / git tag / commit SHA 生成 dataset_version；
* dataset_version 必须由 Evidence Dataset 本身声明（derive 只读取声明，
  不生成、不推断、不改写）；
* 重复加载同一 artifact → provenance 完全一致（确定性）；
  不得使用 timestamp / UUID / random hash 作为版本。
```

---

## 4. Mismatch

### VERSION_MISMATCH

```text
Artifact.dataset_version = V1
Evaluation Report.dataset_version = V2
    → VERSION_MISMATCH
    → 不能继续生成真实 G2/G3/G4 结果
```

### SOURCE_TYPE_MISMATCH

```text
Artifact.source_type = SYNTHETIC_ONLY
Evaluation declares = REAL_DEIDENTIFIED
    → SOURCE_TYPE_MISMATCH
    → 不能继续
```

（反过来也一样。）

说明：

```text
* 本阶段只定义 Contract（reconcile 行为 + 异常状态）；
* 不实现完整 Evaluation Runner；
* mismatch 时下游仍只能是 INSUFFICIENT / BLOCKED，不得出现伪造统计。
```

---

## 5. Identity

```text
Evaluation case identity = case_id
```

```text
* 同一 Dataset Version 内 case_id 必须唯一（duplicate → reject）；
* 同一 case_id 可以出现在 V1 / V2 —— 这代表不同 Dataset Version 的
  不同数据快照（不要求全局永久唯一）；
* artifact identity 不使用生产 correlation identifiers（见 §6）。
```

`project_id` 继续保留在 artifact 中，但只是：

```text
evaluation grouping / context identity

project_id != authorization（见 Step 26 §7：project_id != security boundary）
```

---

## 6. Production ID Isolation

以下四类生产运行时 correlation identifiers 禁止进入 Evidence / Provenance：

```text
conversation_id
assistant_request_id
turn_id
provider_request_id
```

（继续沿用 Step 26 的 forbidden-field 集与递归检测。）

---

## 7. Environment Isolation

禁止进入 `EvidenceProvenance`（属于运行环境/部署信息，不是 Evaluation Dataset 身份）：

```text
hostname
machine_id
username
absolute_path
DATABASE_URL
database_host
server
container_id
git_sha
```

（以及 app_version / package_version / git_tag / commit_sha 等版本别名。）

如果未来需要审计运行环境：

```text
另建独立 Execution Metadata Contract（本阶段不做）。
```

---

## 8. Hash

```text
Artifact Integrity Hash = DEFERRED
```

原因（必须先解决，否则 same dataset → different hash）：

```text
canonical serialization
normalization
encoding
field ordering
content normalization
```

本阶段不实现任何 hash / checksum / digest / fingerprint。

---

## 9. Annotation Separation

```text
Provenance  → Dataset identity
Annotation  → Human / domain interpretation
```

冻结：

```text
* annotation_version **不覆盖** dataset_version（互不覆盖）；
* 未来 Annotated Evidence 应同时保留 dataset_version 与 annotation_version；
* 本阶段只做设计，不创建真实 Annotated Evidence。
```

---

## 10. G1/G2/G3/G4

保持当前状态：

```text
G1 = BLOCKED
G2 = INSUFFICIENT
G3 Contract = READY
G3 Evidence = BLOCKED
G4 = BLOCKED
Selection Strategy = BLOCKED
```

G1 规则（延续 Step 26 + 本阶段 provenance 要求）：

```text
SYNTHETIC_ONLY → BLOCKED（永远）

REAL_DEIDENTIFIED 必须至少：
    dataset_version（非空且由 dataset 声明）
    + source_type
    + valid artifact
    + de-identification attestation
    + sample_count > 0
    + conversation_count > 0
    + turn_count > 0
→ G1 = READY（本阶段不创建真实 artifact，因此实际保持 BLOCKED）
```

Report Binding（Proposed；**不修改** Step 25 Report Schema）：

```text
dataset_version
source_type
g1_status
g2_status
g3_status
g4_status
```

若 G1 = BLOCKED，则 G2/G3/G4 仍不能出现伪造统计。

---

## 11. Deferred

```text
Real Artifact（真实脱敏数据）        = Deferred
Artifact Hash（内容 hash）            = Deferred
Annotation Workflow                   = Deferred
G2 Statistics（真实长度分布）          = Deferred
G4 Evaluation（真实 Full vs Selected） = Deferred
ContextSelector / SelectionPolicy     = Deferred
Recent N / Recent N+First / Hybrid / Relevance = Deferred
Tokenizer / max_tokens / max_chars / max_turns = Deferred
Truncation / Summary / Memory         = Deferred
LLM Judge / 自动评分                   = Deferred
Execution Metadata Contract            = Deferred
```

---

## 附：证据锚点

```text
tests/test_conversation_context_evidence_provenance.py
    → EvidenceProvenance / Version 比较 / Mismatch / Identity /
      Immutability / Environment Isolation / Hash（absent）/ Annotation Separation /
      Proposed Report Binding / G1 门控 / 确定性
tests/test_conversation_context_evidence_provenance_architecture.py
    → backend/app 无 provenance 专有实现；Conversation 运行时无 provenance 依赖；
      既有 dataset_version 仅属 Text-to-SQL 评估域
docs/evaluation/Phase 4.1 Step 26 — Real WMS Evidence Input Boundary.md
    → Artifact Schema / Forbidden Fields（本 Step 的输入）
```
