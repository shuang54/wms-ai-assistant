# Phase 4.2 Step 3A — Evidence Runtime Capability Decision

> **只做 Architecture Decision**：未实现 Runtime / Builder；未修改 backend / tests / DB / API。
> 依据：真实代码 + Phase 4.1 冻结契约 + Step 0/1/1A/2/3 结论。

---

## 1. Evidence Domain Semantics（核心推导）

线索（全部来自真实代码 / 冻结契约）：

| 线索 | 事实 |
| ---- | ---- |
| 业务唯一键 | `(source_type, dataset_version)` UNIQUE → 同一组合**只有一个** Evidence Record |
| 幂等键语义 | Step 40：`create_evidence` 同 (source_type, dataset_version) → first-write-wins 返回既有 |
| 样本组织 | Annotation 以 `case_id` 标识 Evidence 内的样本（Step 27/31） |
| 生命周期 | IMPORTED → PERSISTED → ANNOTATED → REVIEWED → FINALIZED（**数据集级**） |
| source_type 值域 | 仅 `REAL_DEIDENTIFIED` / `SYNTHETIC_ONLY`（**治理/合规维度**，非技术路由） |
| 无内容字段 | 无 content / data / raw_content |
| 复用 | Step 44：Evidence = Reusable，可被多个 Conversation 引用 |

**结论：Evidence = Source / Provenance Unit（Model A）**

```text
Evidence = 一个"数据集 / 来源单元"的治理与评审对象
         （provenance + lifecycle + annotation + review）
不是一次 AI Answer，也不是 runtime 结果容器
```

证据要点：若 Evidence 是一次 AI Answer，则同 (source_type, dataset_version) 唯一键会导致
"同一数据集只能有一条回答"—— 与生命周期（标注/评审/终态）和复用语义矛盾。

---

## 2. Current Capability Matrix（代码事实）

| Capability | Current Support | Runtime Evidence 需要 | Gap |
| ---------- | --------------- | --------------------- | --- |
| Evidence identity（evidence_id） | ✅ | ✅ | — |
| Business uniqueness（source_type+dataset_version） | ✅ | ✅ | — |
| Source type（2 值，治理维度） | ✅ | 部分（不能表达 route） | 语义差异 |
| Dataset version（字段存在） | ✅ 字段 | ❌ **运行时无来源** | **GAP** |
| Source content | ❌ 无字段 | 需要（若承载结果） | **GAP** |
| Structured result | ❌ 无字段 | 需要（若承载结果） | **GAP** |
| Source locator | ❌ | 需要（若承载结果） | **GAP** |
| Provenance（dataset_version+source_type） | ✅ | ✅ | — |
| De-identification attestation | ✅ 字段 | ❌ AI Result 未提供 | GAP |
| Lifecycle | ✅ | ✅ | — |
| Annotation / Review | ✅ | 部分（runtime 结果是否走评审未定） | 语义待定 |
| Conversation reference | ✅（ConversationEvidence） | ✅ | — |
| Runtime correlation | ❌（request_id 禁止入 Evidence） | 需要（但被契约禁止） | 契约性缺口 |
| Deduplication | ✅（组合唯一键） | 部分（无法区分"同数据集不同结果"） | GAP |

---

## 3. AI Runtime → Evidence Gap（缺的不是 Builder）

```text
AI Result（route/content/data/metadata）
        ↓  缺：dataset version · source identity · content 承载层
Durable Evidence（provenance/lifecycle/review）
```

缺失的是**上游能力**（Runtime Dataset Version Contract + Artifact/Content Layer），
**不是** Builder 本身 —— Builder 只是映射器，无法凭空产生 dataset_version。

---

## 4. dataset_version Analysis（RAG / Tool / Text-to-SQL）

| 来源 | 是否存在 dataset/snapshot/version | 事实 |
| ---- | --------------------------------- | ---- |
| RAG | **否** | knowledge_document（content_hash/file_name/source/status/title/metadata）、knowledge_chunk（content_hash/chunk_index/content/document_id/metadata）**均无 version**；RagResponse = answer + sources + used_chunks_count，**无 dataset_version** |
| Tool | **否** | ToolResult = tool_name / success / data / error；**无 dataset / snapshot / version** |
| Text-to-SQL | **否** | SQLExecutionResult = columns / rows / row_count / truncated / execution_time_ms；**无 snapshot / schema version** |

**Evaluation Dataset ≠ Runtime Dataset**：现有 `dataset_version` 仅存在于
`text_to_sql_baseline_service` / `quality_evaluation_service` / `prompt_experiment_service`
等**离线评估**服务（读取评估数据集路径），**不是** AI Runtime 结果的一部分。
→ 不得把评估数据集版本当作生产运行时 dataset_version。

---

## 5–7. RAG / Tool / Text-to-SQL 能否产生 Evidence（当前）

| Route | 内容 | 稳定来源 | dataset_version | 当前能否 |
| ----- | ---- | -------- | --------------- | -------- |
| RAG | chunk content ✅ | document_id / content_hash ✅ | ❌ | **否** |
| Tool | data ✅ | tool_name（非 dataset） | ❌ | **否** |
| Text-to-SQL | rows ✅ | SQL / DB（非版本化） | ❌ | **否** |

→ 三者均**不能**在当前契约下安全产生 Evidence（缺 dataset_version + 无内容承载）。

---

## 8. Annotation Boundary

Annotation 字段：`case_id` · `annotation_version` · `annotator_id` · `review_status`
→ 语义 = **人工标注/评审单元**（Evidence 内样本维度），
不是"runtime 结果记录"。若把 RAG/Tool/SQL 结果直接做成 Annotation，
会混淆"人工评审样本"与"机器执行结果"→ **不支持**。

---

## 9. ConversationEvidence Boundary

```text
Conversation ↕ ConversationEvidence ↕ Evidence   （0..N × 0..N，REFERENCE）
```

* 关联对象是 **Evidence Record**（治理单元），不是 runtime artifact；
* Evidence reuse 语义成立的前提：Evidence 是**可复用数据集**，而非每次回答的新记录 ——
  这进一步验证 §1 结论。

---

## 10. Architecture A（扩展 Evidence Record 承载 content）

* 优点：模型简单、查询直接、生命周期统一
* 缺点：**混同** Provenance Record 与 Runtime Artifact ——
  同 (source_type, dataset_version) 唯一键下无法表达"同数据集的多次结果"；
  RAG chunk / Tool result / SQL rows 结构与治理元数据强耦合；破坏 reuse 语义
* 结论：**不推荐**

---

## 11. Architecture B（Runtime Artifact + Evidence Record）

```text
Runtime Artifact（content / structured data / source reference）
        ↓
Evidence（provenance / lifecycle / review）
```

* 最符合当前语义边界（Evidence = 治理单元；Artifact = 实际结果）
* 代价：新持久化层 + 新 Repository + Runtime Dataset Version Contract + 脱敏前提
* 结论：**未来正确方向，但超出 Phase 4.2 当前授权（DB/API 变更 + 新契约）**

---

## 12. Architecture C（Runtime Evidence 暂不持久化）

```text
AI Result → Assistant Turn（用户可见） + Trace（可观测）
Evidence 继续服务：Dataset / Review / Annotation / Evaluation
```

* 与当前 Evidence 语义（治理单元）**完全一致**，且**不需要任何 DB/API 变更**
* 意味着：Step 4（Evidence Runtime Integration）当前**没有可实施的内容**

---

## 13–15. Final Decisions

```text
Decision 1（Evidence Semantic）
  = Source / Provenance Record（Model A）· 不是 Runtime Evidence Artifact · 不是 Hybrid

Decision 2（Runtime Evidence Content）
  = 暂不持久化（Architecture C）
    未来若需要 → 走 Architecture B（独立 Artifact），不得塞进 Evidence Record

Decision 3（dataset_version）
  = 当前 AI Runtime **不提供** → 必须新增 "Runtime Dataset Version Contract"
    **本阶段不实现**；禁止用 timestamp / request_id / conversation_id / UUID 替代
    （RAG/Tool/Text-to-SQL 均无稳定版本；评估数据集版本 ≠ 运行时数据集版本）
```

---

## 16. New Open Decisions

| ID | Decision | Status |
| -- | -------- | ------ |
| OD-31 | Runtime Evidence Artifact Architecture（是否需要独立 Artifact 层） | **OPEN** |
| OD-32 | Runtime Dataset Version Contract（RAG/Tool/SQL 如何提供稳定 dataset_version） | **OPEN** |
| OD-26 | EMPTY 是否视为 idempotency completed（Step 2 遗留） | OPEN（未关闭） |
| OD-30 | EMPTY 是否允许 Builder 独立消费 data（Step 3 遗留） | OPEN（未关闭） |

---

## 17. Step 3 Impact

Step 3（Evidence Builder Contract）结论**成立且不矛盾**：
其 BLOCKED 判定正是由本 Step 确认的两个上游缺口（dataset_version + 无内容承载）导致；
Step 3 文档无需修订。

---

## 18. Step 4 Readiness

```text
Step 4 = BLOCKED
```

原因：Step 4 原定义 = "Evidence Runtime Integration（生产创建 Evidence）"，
但 Decision 1 + Decision 3 证明：Evidence 是治理单元、AI Runtime 无 dataset_version
→ **没有任何合法输入可让 Runtime 产生 Evidence**。

解除路径（需你决策，本阶段不实施）：
1. 走 Architecture C → Roadmap 将 Step 4 重新定义为
   "Runtime Evidence DEFERRED + 强化 Trace/Observability"（无 DB 变更）；或
2. 立项 OD-31 + OD-32（新增 Artifact 层 + Runtime Dataset Version 契约）→
   完成后再实施 Runtime Evidence Integration。
