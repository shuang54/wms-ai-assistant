# Phase 4.1 Step 36：Real Evidence Persistence Evaluation

- 类型：**Persistence & Provenance Boundary Audit**（Contract only）
- 结论：production **尚无** Evidence / Annotation 持久化 → 本 Step 冻结边界，未新增生产代码
- 契约实现：`tests/test_evidence_persistence_boundary.py`（test-local；20 passed）
- 前置：Step 26（Input Boundary）· Step 27（Provenance）· Step 28~30（Annotation 链）·
  Step 31（Import Contract）· Step 32（Annotation Execution）

---

## 1. Current Architecture

```text
backend/app/db/models（8 张表；无 evidence / annotation）
    assistant_outcome_record · conversation · conversation_turn
    knowledge_chunk · knowledge_document · llm_usage_record
    rag_execution_record · tool_execution_record

backend/app/db（6 个 repository；无 evidence / annotation）
    assistant_outcome · conversation · llm_usage · rag_execution · tool_execution

建表机制：init_db() → Base.metadata.create_all()（**无** alembic / migrations）
Conversation 持久化 schema：ai_ops（非 public）
```

## 2. Existing Persistence Components

| 能力 | 是否存在（production） | 位置 |
| --- | --- | --- |
| Evidence ORM | 否 | — |
| Evidence Repository | 否 | — |
| Evidence Service | 否 | — |
| Annotation ORM | 否 | — |
| Annotation Repository | 否 | — |
| Annotation Service | 否 | — |
| Evidence ↔ Annotation 关联 | 仅契约层（Step 32：case_id + dataset_version） | tests（test-local） |
| Provenance | 契约层（Step 27：dataset_version + source_type） | tests（test-local） |
| Idempotency | 契约层（本 Step 冻结：dataset_version + source_type） | tests（test-local） |
| Transaction boundary | 既有模式：Repository 持有事务（会话层 Step 4 §4） | conversation_repository |
| Review / Finalization 状态 | 契约层（Step 28~30：DRAFT / REVIEWED / DISPUTED → REVIEWED） | tests（test-local） |

## 3. Evidence Contract

持久化字段最小集（**只使用 Step 31 Contract 已有字段**）：

```text
evidence_id                      服务端签发（identity；不可变）
dataset_version                  非空 / 稳定 / 显式（Step 27）
source_type                      REAL_DEIDENTIFIED / SYNTHETIC_ONLY
de_identification_attested       bool（Step 31：attestation 必须 true）
de_identification_method         可空 string
status                           IMPORTED / PERSISTED / ANNOTATED / REVIEWED / FINALIZED
created_at / updated_at          timestamps
```

禁止自造字段（Step 31 契约中不存在）：

```text
source_ref · source_reference · content_ref · content_reference ·
locator · file_path · raw_content · conversation_content
```

## 4. Annotation Contract

```text
annotation_id
evidence_id                      指向 Evidence（稳定 identity）
case_id                          Step 31/32 的 case identity
annotation_version               必须 ≠ dataset_version（Step 27）
annotator_id
review_status                    DRAFT / REVIEWED（沿用 Step 28~30 string 状态）
created_at / updated_at
```

## 5. Provenance Contract

复用 Step 27 `EvidenceProvenance`（**不新建体系**）：

```text
dataset_version      这个 Evidence 的版本
source_type          来自哪类 source
```

Evidence 必须能够回答：

```text
从哪里来？            → source_type + de_identification_attestation
什么时候导入？        → created_at
对应哪个 source？     → source_type
是否经过 Annotation？ → status ≥ ANNOTATED
当前版本是什么？      → dataset_version（+ annotation_version 属 annotation 产物）
```

## 6. Evidence ↔ Annotation Association

```text
一个 Annotation 属于哪个 Evidence？     → annotation.evidence_id（稳定 identity）
一个 Evidence 当前有哪些 Annotation？   → 按 evidence_id 检索
```

禁止：

```text
content 文本匹配 · source name 匹配 · 隐式推断
```

## 7. Idempotency

```text
幂等键 = source_type + dataset_version（稳定；无 random / time / UUID）
同一键重复 import → 同一 evidence_id（first-write-wins；不新增记录）
attestation 缺失 → 拒绝导入（不静默降级）
```

## 8. Transaction Boundary

沿用项目既有事务归属（会话层 Step 4 §4：事务由 Repository 持有）：

```text
Import → Evidence 持久化 → Annotation 持久化   属同一事务单元

禁止出现：
  * Evidence 已保存 / Annotation 失败 / 系统认为成功
  * Annotation 已保存 / Evidence 不存在（孤儿）
```

契约验证（test-local registry）：annotation 持久化失败 → annotation 不落库，
Evidence 状态不推进（仍 PERSISTED），无半成品数据。

## 9. Security Boundary

持久化层禁止保存：

```text
api_key · password · authorization · database_url ·
connection_string · llm_secret · token
```

Evidence 若含敏感字段：遵循 Step 26/31 既有边界（仓库外脱敏；不新增脱敏平台）。
`project_id` 继续只作业务上下文，**不是** authorization / tenant boundary。

## 10. Test Results

```text
tests/test_evidence_persistence_boundary.py → 20 passed（0.55s；离线）
覆盖：状态机 / 字段契约 / 自造字段拒绝 / Import→Persist→Read /
      关联（稳定 identity + 跨 Evidence 拒绝）/ Provenance /
      幂等（重复 import + 键稳定 + attestation 拒绝）/
      事务失败无孤儿 / 缺 Evidence 拒绝 / 隔离 / 安全 / 离线 / 确定性
```

## 11. DB Writes

```text
DB = 0 · DB schema = 0 · DB writes = 0
（契约层 in-memory registry；不连接数据库、不建表、不改 schema）
```

## 12. Remaining Gaps

```text
1. production 尚无 Evidence / Annotation ORM / Repository / Service
   → 实际持久化实现（ai_ops 表 + repository + 事务）需在确认后单独执行
2. 尚无 DB-gated 持久化测试（需先有 ORM / repository）
3. Evidence 的 case 集合（Step 31 conversations）尚未定义持久化粒度
4. Annotation 的 dependency / reference / impact 明细持久化未定义
   （Step 24/28 契约层的字段如何落库需后续 Step 决定）
5. G3 / G4 仍 BLOCKED（无真实数据 / 无真实标注）
```
