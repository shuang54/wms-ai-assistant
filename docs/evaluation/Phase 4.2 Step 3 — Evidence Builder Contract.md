# Phase 4.2 Step 3 — Evidence Builder Contract

> **Contract Design only**：未创建 Builder 实现、未修改任何生产代码 / DB / API。
> 依据：真实代码 + Phase 4.1 冻结契约 + Step 0/1/1A/2 结论。

---

## 1. Scope

定义 `AIOrchestrationResult → Evidence` 的稳定契约：
Input · Eligibility · Mapping · Provenance · Identity · Lifecycle · Output。
**不实现**、**不改 Schema**、**不改 Runtime**。

---

## 2. Current Evidence Model（权威，实测）

```text
evidence_record:
  evidence_id · dataset_version · source_type · de_identification_attested
  · de_identification_method · status · created_at · updated_at

evidence_annotation_record:
  annotation_id · evidence_id · case_id · annotation_version
  · annotator_id · review_status · created_at · updated_at

SOURCE_TYPE_VALUES = (REAL_DEIDENTIFIED, SYNTHETIC_ONLY)
Evidence 幂等键 = (source_type, dataset_version)   ← UNIQUE
Evidence 状态机  = IMPORTED → PERSISTED → ANNOTATED → REVIEWED → FINALIZED（FINALIZED terminal）
```

**关键事实 1**：Evidence **没有任何内容字段**（无 content / data / raw_content / payload）。
→ Evidence 是 **provenance 元数据对象**，不是内容容器。

**关键事实 2**：`source_type` 值域只有 **2 个**（REAL_DEIDENTIFIED / SYNTHETIC_ONLY），
**不能**用于表达 route（rag / tool / text_to_sql）。

**关键事实 3**：`dataset_version` 在 AI Runtime（RAG / Tool / Text-to-SQL 执行路径）
中**不存在**；现有 `dataset_version` 仅出现在 Text-to-SQL **离线评估/基线服务**
（baseline / quality evaluation / prompt experiment），不属于运行时 AI Result。

---

## 3. AIOrchestrationResult → Evidence 字段可用性

| 字段 | 存在 | 类型 | Evidence 是否使用 | 原因 |
| ---- | ---- | ---- | ----------------- | ---- |
| `route` | YES | RouteType（rag/tool/text_to_sql） | **否** | `source_type` 值域不含路由语义（事实 2） |
| `content` | YES | str \| None | **否**（无字段承载） | Evidence 无 content 列（事实 1） |
| `data` | YES | Any（ToolResult / SQLExecutionResult / RagResponse） | **否**（无字段承载） | 同上；且不得塞入 provenance |
| `metadata.request_id` | YES | str | **禁止** | Step 27 冻结：请求 ID 不得进 Evidence identity / provenance |
| `metadata.outcome` | YES | SUCCESS/EMPTY/REFUSED/FAILED | 仅用于 **Eligibility** | 决定是否构建，不进入 Evidence 字段 |
| `metadata.refused` | YES（Text-to-SQL） | bool | 仅用于 Eligibility | 同上 |
| `conversation_id` | **不存在于 AI Result** | — | **禁止** | 运行时关联上下文，非 Evidence 身份 |
| `idempotency_key` | **不存在于 Evidence** | — | **禁止** | Step 1A 明确：不得进入 Evidence identity |

→ 可用作 Evidence 输入的 AI Result 信息**仅剩：是否 eligible（outcome/route）+ 外部 source context**。

---

## 4. Evidence Eligibility（0..N）

```text
AIOrchestrationResult → Eligibility 判定 → 0..N Evidence
```

| 状态 | 判定 | Evidence |
| ---- | ---- | -------- |
| SUCCESS | 业务结果产生 | **待定**（依赖 dataset_version，见 §6） |
| EMPTY | 无可展示内容（data 可能非空） | **待定**（是否允许 Builder 独立消费 data → OD-30） |
| REFUSED | 业务拒绝（非业务事实） | **no Evidence**（拒答本身不是可追溯业务事实） |
| FAILED（异常） | 无 AI Result 对象 | **no Evidence**（不得从异常伪造） |

---

## 5. Evidence Content Contract

```text
CURRENT LIMITATION：Evidence Schema 无任何内容字段。
```

→ 本 Step **不定义** "Evidence content"（无字段可落）；
若未来需要承载 RAG chunk / Tool result / DB result，必须**先扩展 Evidence Schema**
（属独立决策，不在 Step 3 内）。当前 Evidence 只能表达"存在一份可追溯来源"这一事实。

---

## 6. dataset_version（关键阻塞项）

```text
Dataset version source = UNRESOLVED
```

* AI Runtime 执行路径**不提供** dataset_version（事实 3）；
* RAG：无 Knowledge Base version 字段暴露给 AI Result；
* Tool：Tool result 无 dataset 概念；
* Text-to-SQL：DB 查询结果无 dataset_version；仅有离线评估数据集版本（不适用运行时）。

**禁止**以下替代（任务 §十）：timestamp · request_id · conversation_id · random UUID · idempotency_key。

→ 因此：`dataset_version` 标记为 **BLOCKED / DEFERRED field**；
在来源未定前，Evidence Builder **无法**从 AI Result 安全构造 Evidence。

---

## 7. source_type

```text
source_type ∈ {REAL_DEIDENTIFIED, SYNTHETIC_ONLY}（Phase 4.1 冻结值域）
```

* **不得**映射为 rag / tool / text_to_sql / tool name（超出值域）；
* 运行时 AI 结果属于真实数据 → 候选为 `REAL_DEIDENTIFIED`，
  但需 `de_identification_attested` 与 method 依据（当前 AI Result 未提供）→ 亦未定。

---

## 8. Evidence Identity

```text
DB primary key        = evidence_id（服务端签发；唯一 / 不可变）
business idempotency  = (source_type, dataset_version)   ← UNIQUE
```

两者**严格区分**；identity 输入**不得**含：request_id · conversation_id · turn_id ·
idempotency_key · timestamp · random UUID。

---

## 9. Cardinality

模型上：`1 AI Result → 0..N Evidence`（每条独立 evidence_id）。
**但**由于无内容字段 + dataset_version 未定，当前**无法安全表达 N > 0** 的实际语义
→ 记录为：cardinality 语义待 Schema / dataset_version 决策后确定。

---

## 10. Lifecycle Initial State

`EvidenceRepository.create_evidence()` 默认状态 = **`IMPORTED`**（EVIDENCE_STATUS_VALUES[0]）
→ Builder 产生的新 Evidence 初始状态 = **IMPORTED**（不新增状态；FINALIZED 仍 terminal）。

---

## 11. Annotation Boundary

**Builder 不创建 Annotation**。
`AI Result → Evidence →（后续）Annotation / Review` 为独立生命周期（Phase 4.1 Step 41+）。

---

## 12. RAG / Tool / Text-to-SQL Mapping（统一结论）

| Route | 候选 Evidence 内容 | 当前能否落入 Schema | 结论 |
| ----- | ------------------ | ------------------- | ---- |
| RAG | Knowledge chunk | **否**（无字段 + 无 KB version） | BLOCKED |
| Tool | Tool result | **否**（无字段 + 无 dataset 概念） | BLOCKED |
| Text-to-SQL | SQL / DB result | **否**（无字段 + 无 dataset_version） | BLOCKED |

→ 三者均因 **事实 1（无内容字段）+ 事实 3（无 dataset_version）** 无法在当前契约下安全构建。

---

## 13. Security Boundary

Builder **不得**复制进 Evidence：API key · password · database URL · connection string ·
Authorization header · LLM provider secret · DB session · SQLAlchemy connection。
SQL / DB result 若未来落库需先满足 schema 与脱敏前提；
**脱敏机制不在 Step 3 范围**（`Sensitive-data redaction is outside Step 3`）。

---

## 14. Input / Output Contract（概念结构，不创建 DTO）

```text
EvidenceBuildInput（概念）
  ai_result            : AIOrchestrationResult      （必填）
  source_context       : 来源上下文（RAG/Tool/SQL 来源标识）   ← 当前未定义
  dataset_version      : str                        ← **UNRESOLVED（阻塞）**
  de_identification    : attested + method          ← 当前 AI Result 未提供
  （显式排除：conversation_id · turn_id · idempotency_key · request_id
     → 仅可作 runtime correlation，**不得**进入 identity/provenance）

EvidenceBuildResult（概念）
  evidence_count  : int            （0 / 1 / N）
  evidence_items  : tuple[...]     （每条 = EvidenceRecord 语义）
  skipped_reason  : str | None     （REFUSED / EMPTY / FAILED / MISSING_DATASET_VERSION）
```

---

## 15. Contract Test Matrix（设计，不执行）

| ID | Case | Input | Expected Count | source_type | provenance | identity |
| -- | ---- | ----- | -------------- | ----------- | ---------- | -------- |
| E1 | RAG + valid source | RAG result + source | 待定（阻塞） | 值域内 | 需 dataset_version | evidence_id |
| E2 | RAG + no source | RAG result 无来源 | **0** | — | — | — |
| E3 | Tool + structured result | Tool result | 待定（阻塞） | 值域内 | 需 dataset_version | evidence_id |
| E4 | Tool + empty result | 空 result | **0** | — | — | — |
| E5 | Text-to-SQL + DB result | SQL + rows | 待定（阻塞） | 值域内 | 需 dataset_version | evidence_id |
| E6 | Text-to-SQL + refusal | refusal 结果 | **0** | — | — | — |
| E7 | REFUSED | refused=True | **0** | — | — | — |
| E8 | EMPTY | content 空 | 待定（OD-30） | — | — | — |
| E9 | FAILED（异常） | exception | **0**（不伪造） | — | — | — |
| E10 | missing dataset_version | 无来源版本 | **0** + skipped_reason | — | — | — |
| E11 | multiple RAG sources | N chunks | 待定（阻塞） | 值域内 | 同上 | N × evidence_id |
| E12 | forbidden identifiers | 含 request_id/conv/idempotency | **reject**（不得进入 identity） | — | — | — |

---

## 16. Existing Test Compatibility

检查 tests/ 中所有 Evidence / ConversationEvidence / Annotation 测试：
**未发现契约冲突**（Step 3 未提出任何 Schema 变更；仅记录限制）。
若未来扩展 Schema（内容字段 / source_type 值域），需重新评估兼容性。

---

## 17. OD-26 状态

```text
OD-26（EMPTY 是否视为 idempotency completed）= OPEN（不在本 Step 关闭）
```

Evidence Builder **不依赖** OD-26：Builder 只关心 AI Result 与 source context；
EMPTY 是否允许 Builder 独立消费 `data` 记为 **OD-30（OPEN）**。

---

## 18. Step 3 Decision

```text
Step 3 = BLOCKED
```

阻塞原因（真实代码证据）：

1. **dataset_version 来源未定**（AI Runtime 不提供；禁止用时间/请求ID/UUID 替代）→ §6；
2. **Evidence Schema 无内容字段** → RAG chunk / Tool result / DB result 无处承载 → §5；
3. **source_type 值域仅 2 值**，无法表达 route → §7。

→ 在以上任一解决前，Evidence Builder **不可安全实现**。
建议后续：先冻结 `dataset_version` 来源契约（新 Step）→ 再决定是否需要 Schema 扩展 → 然后实现 Builder。
