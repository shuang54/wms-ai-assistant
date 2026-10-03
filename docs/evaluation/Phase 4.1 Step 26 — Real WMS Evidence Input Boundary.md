# Phase 4.1 Step 26 — Real WMS Evidence Input Boundary

- 阶段：Phase 4.1（Conversation / Chat 会话层）
- 类型：**Contract only**（只冻结 Real Evidence 进入 Evaluation Layer 的边界）
- 前置：Step 21~25（Synthetic Dataset / Decision Gate / Data Audit / Rubric / Procedure）
- 声明：

```text
本阶段不导入真实数据 · 不实现脱敏工具 · 不实现 ContextSelector ·
不实现任何 History Selection Strategy。
```

---

## 1. Purpose

明确未来 Real De-identified WMS Evidence 应以什么**最小结构**进入 Evaluation Layer：

```text
External / Offline De-identified Artifact
        ↓
Evidence Dataset Loader（contract stub；不做 IO）
        ↓
EvidenceDatasetValidator（复用 Step 25）
        ↓
G1
        ↓
G2 / G3 / G4（全部依赖 G1 READY）
```

只定义：Artifact Schema / Loader Contract / Validation Contract /
Privacy Boundary / Provenance Boundary。

---

## 2. Artifact Schema

最小结构（严格字段）：

```yaml
dataset_version: "wms-evidence-<version>"
source_type: REAL_DEIDENTIFIED

conversations:
  - case_id: "..."
    project_id: "..."
    turns:
      - role: user
        content: "..."
      - role: assistant
        content: "..."
      - role: user
        content: "..."
```

要求：

```text
dataset_version · source_type · conversations · case_id · project_id ·
turns · role · content
```

说明：

```text
* source_type ∈ {REAL_DEIDENTIFIED, SYNTHETIC_ONLY}（只有这两种）；
* turns 只含 role + content（除此之外的 turn key 一律拒绝）；
* last turn = user（当前 user turn 由 Evaluation Layer 按 position 判断）；
* case_id 作为 Evaluation Identity（重复 case_id 拒绝）。
```

---

## 3. Forbidden Fields

Real Evidence Artifact 中禁止出现（**递归**检查所有 key）：

### 生产运行时 correlation identifiers

```text
conversation_id
assistant_request_id
turn_id
provider_request_id
```

（这些属于生产运行时 correlation identifiers，不属于 evaluation artifact；
Evaluation Artifact 只依赖 turn position：turns[0] / turns[1] / turns[2]。）

### Evaluation derived fields（raw artifact 不得预先携带结论）

```text
annotation
reference
impact
strategy_id
selected_turns
selected_history
candidate_history
selection_result
score
ranking
winner
tokens
```

原因：

```text
Raw Evidence
    ↓
Evaluation
```

不能：

```text
Raw Evidence
    ↓
预先带结论
```

### 运行环境 / provenance 扩展字段

```text
git_sha
machine_id
hostname
absolute_path
file_path
credential / credentials
customer / factory / employee / database / server / host
```

### 凭据与路径形态（值级检查）

```text
API key / Authorization / DATABASE_URL
postgresql:// / postgres:// / psycopg2.connect / password
绝对路径：C:\… / D:\… / \\server\… / /home/… / /Users/… / /etc/…
```

---

## 4. Privacy

```text
De-identification happens outside repo.
```

冻结：

```text
* 真实 WMS 数据必须在**仓库外**完成脱敏；
* 进入 Evaluation Layer 的只能是 REAL_DEIDENTIFIED；
* 本阶段不负责：识别个人信息 / 自动脱敏 / 自动匿名化 / 替换真实订单号；
* 不新增 deidentify.py / anonymize.py / redact.py。
```

如果未来真实数据**无法证明**已经脱敏：

```text
G1 = BLOCKED
```

不能"默认认为安全"（缺少 de-identified attestation → 视为未脱敏）。

---

## 5. G1

```text
source_type = SYNTHETIC_ONLY
    → G1 BLOCKED（永远）

source_type = REAL_DEIDENTIFIED
    + valid artifact
    + de-identified attestation（no sensitive）
    + sample_count > 0
    + conversation_count > 0
    + turn_count > 0
    → G1 READY
```

禁止：

```text
SYNTHETIC_ONLY → REAL_DEIDENTIFIED 自动转换
（转换只能由**仓库外**脱敏流程显式产生新 artifact；
  Evaluation Layer 不做任何自动升级 / promote / coerce。）
```

---

## 6. G2/G3/G4

全部依赖 `G1 READY`：

```text
G1 = BLOCKED
    → G2 = INSUFFICIENT（不生成任何统计）
    → G3 = BLOCKED（annotated_turn_count = 0）
    → G4 = BLOCKED（evaluated_case_count = 0）
```

输入边界：

```text
G2 输入：artifact（turn / character 口径）；
G3 输入：Real Evidence + Human Annotation（artifact 本身不含 annotation）
        → 未来链路：Artifact → Annotation Workflow → Annotated Evidence；
G4 输入：Full History Reference + Candidate Selected History + Outcome Comparison
        → artifact 只保存历史，不保存 selected_history / candidate_history / strategy_id。
```

当前状态：

```text
G1 = BLOCKED → 本阶段不生成任何真实 G2/G3/G4 evidence。
```

---

## 7. Project Isolation

Real Artifact 保留 `project_id`，但它只是：

```text
evaluation grouping / context identity
```

不是：

```text
authorization
```

```text
project_id != authorization
project_id != security boundary
```

当前系统仍没有 project-level authorization；本阶段不增加 auth。

---

## 8. Synthetic

```text
Step21 dataset remains SYNTHETIC_ONLY.
```

```text
tests/fixtures/conversation_context/wms_multiturn_conversations.yaml
    → 未被修改（无 dataset_version / annotation / reference / impact 字段）
    → source_type 分类仍为 SYNTHETIC_ONLY
```

两个 source type 严格区分（SYNTHETIC_ONLY / REAL_DEIDENTIFIED）。
测试中使用**极小 synthetic stand-in** 模拟 REAL_DEIDENTIFIED 契约，但明确：

```text
This is contract simulation only.
It is NOT real WMS evidence.
```

---

## 9. Runtime Isolation

```text
Evaluation Artifact does not enter production runtime.
```

```text
* backend/app/ 中不存在 real evidence loader / deidentify tool /
  anonymize tool / evaluation artifact importer（AST 审计锁定）；
* boundary 测试 helper 不得依赖 sqlalchemy / psycopg / redis / celery / kafka /
  backend.app.db；
* 不得与运行时耦合（ConversationService / ChatApplicationService / AIOrchestrator /
  RAG / Tool / TextToSQL 均不得 import）；
* Loader contract 是 test-local stub：不做任何 IO（无 open / read_text / parse），
  parsed artifact 必须由仓库外流程提供；
* Evaluation Report 只输出 counts / percentages / distributions / status，
  不输出完整 content / answer / SQL / RAG chunk。
```

---

## 10. Deferred

```text
真实数据导入（Real Data Import）                = Deferred
脱敏（De-identification）                       = Deferred
Annotation Workflow                             = Deferred
Selection Strategy（含 ContextSelector）        = Deferred
Recent N / Recent N+First / Hybrid / Relevance  = Deferred
Tokenizer / max_tokens / max_chars / max_turns  = Deferred
Truncation / Summary / Memory                   = Deferred
LLM Judge / 自动评分                            = Deferred
project-level authorization                     = Deferred
```

当前 Gate 状态（延续 Step 25）：

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
tests/test_conversation_context_real_evidence_boundary.py
    → Artifact Schema / Forbidden Fields / Privacy / Provenance /
      G1 边界 / Turn 校验 / 下游 Gate / 确定性 / 文档完整性
tests/test_conversation_context_real_evidence_boundary_architecture.py
    → production 无 loader / 脱敏 / importer；tests 允许 contract；
      无 forbidden imports；无 runtime coupling
docs/evaluation/Phase 4.1 Step 25 — Evidence Collection Procedure.md
    → EvidenceDatasetValidator / G1~G4 procedure（本 Step 复用的输入）
```
