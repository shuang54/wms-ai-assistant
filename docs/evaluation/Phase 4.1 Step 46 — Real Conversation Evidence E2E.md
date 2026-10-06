# Phase 4.1 Step 46 — Real Conversation Evidence E2E

> 目标：**闭环验证**，不是新增 AI 能力。
> 测试：`tests/test_conversation_evidence_e2e_db.py`（10 passed，`RUN_DB_TESTS=1`，真实 PostgreSQL）。

---

## 1. Runtime Path

```text
Conversation                     （真实：ConversationRepository.create）
    ↓
ConversationTurn（USER）          （真实：ChatApplicationService.execute_message）
    ↓
ConversationContextBuilder        （真实）
    ↓
AIOrchestrator                    （**唯一 Fake**：FakeOrchestrator；route = RAG）
    ↓
AI Result（AIOrchestrationResult） （route · content · metadata.request_id）
    ↓
Evidence                          （test-composed：EvidenceRepository，按 Step 36–38 冻结契约）
    ↓
ConversationEvidence              （真实：ConversationEvidenceRepository.create_association）
    ↓
PostgreSQL（ai_ops）               （真实）
    ↓
Read-back                          （**新 Repository / 新 Session**）
```

Fake 边界：只替换 Orchestrator（沿用 Step 15 `test_conversation_multiturn_db_e2e.py` 模式，
本文件内 ~30 行）；Conversation / Turn / Evidence / Association / PostgreSQL 全部真实。

**Evidence creation orchestration = test-composed**

项目中不存在 `EvidenceBuilder` / `EvidenceFactory` / `EvidenceMapper`（已审计，0 命中），
因此本 E2E 在测试层按已冻结契约写入 Evidence —— **未**引入新业务层 / Service / API。

---

## 2. E2E Cases

| Case | Expected | Actual | Result |
| ---- | -------- | ------ | ------ |
| 01 single conversation roundtrip | conv + turn + evidence + association 成立，Evidence 仍可独立读取 | route=RAG；association 成立；turn ≥ 2（USER+ASSISTANT） | **PASS** |
| 02 multiple evidence per conversation | 同一会话 2 个 Evidence，互不覆盖 | `list_evidence_ids` = 2 个不同 ID | **PASS** |
| 03 evidence reuse across conversations | 同一 Evidence 被 2 个会话引用，仍只有 1 条 record | `list_conversation_ids` = 2；无 `-copy` 记录 | **PASS** |
| 04 conversation isolation | A 不得看到 B 的 Evidence | 双向隔离，leakage = 0 | **PASS** |
| 05 persistence read-back | 新 Session 可重新读取 | 新 Repository 三重读回一致（含 created_at） | **PASS** |
| 06 association idempotency | 重复写入只保留一条 | first-write-wins；`list` = 1 | **PASS** |
| 07 invalid parent reference | 非法父引用拒绝，无 orphan row | 两种非法组合均 typed reject；count 不变 | **PASS** |
| 08 parent delete semantics | 删父只删关联行 | 删 Conversation → Evidence 保留；删 Evidence → Conversation 保留 | **PASS** |
| 09 runtime result identity boundary | `assistant_request_id` 只作 correlation | request_id ≠ evidence_id，且不进入 provenance | **PASS** |
| 10 turn-level reference（OD-12） | 判定 Q1→Q2→Q3 | Q1 YES；Q2 无契约需求；**Q3 = DEFERRED** | **PASS（判定）** |

---

## 3. Persistence

| 项 | 说明 |
| -- | ---- |
| Conversation | `ai_ops.conversation`（测试自建唯一 `conv-*`；用例结束精确删除） |
| ConversationTurn | `ai_ops.conversation_turn`（USER + ASSISTANT；由真实 `execute_message` 写入） |
| Evidence | `ai_ops.evidence_record`（唯一 `ev-*`；`source_type=SYNTHETIC_ONLY`、`de_identification_attested=True`） |
| Association | `ai_ops.conversation_evidence`（**只经 Repository 写入**，无裸 SQL INSERT） |
| Read-back | 新 Repository（新 Session）读取 association / conversation / evidence 全部一致 |

DB residue：module 级五表计数前后一致 → **0**
Production DB writes → **0**（仅测试自建数据；无 TRUNCATE / 全表 DELETE）

（文档不记录任何具体测试 ID —— 均为一次性 UUID 后缀。）

---

## 4. OD-12

```text
TURN_LEVEL_REFERENCE = DEFERRED
```

判定链：

* **Q1**：`conversation_id + evidence_id` 能否唯一定位 Evidence 所属会话？→ **YES**
  （`list_conversation_ids(evidence_id)` 可反查，复合主键保证唯一）。
* **Q2**：当前是否需要回答"该 Evidence 由哪个具体 Turn 产生"？→ **无**
  （无现有契约 / API / 产品需求要求 turn-level provenance；Step 44/45 冻结的
  关联表不含 `turn_id`）。
* **Q3**：因此 **DEFERRED** —— 保持 Step 45 模型：
  ```text
  conversation_evidence(conversation_id, evidence_id, created_at)
  ```
  不新增 `turn_id` / `assistant_request_id`。

后续触发条件：若未来出现 turn-level 可追溯性需求（例如 Question→Answer→Evidence
审计），需先做 schema/API 决策，再实施（不在 Step 46 内扩展 DB）。

---

## 5. Security

```text
Sensitive metadata leakage = 0     （无 API key / password / DATABASE_URL /
                                     连接串 / authorization header 进入
                                     association、Evidence、E2E 结果）
ORM / session leakage      = 0     （ConversationEvidenceReference 仍为 frozen
                                     dataclass；字段仅 3 个；不含 Session /
                                     Connection / Engine / ORM / metadata / registry）
Unauthorized persistence   = 0     （非法父引用被 typed 异常拒绝；
                                     未绕过 Repository 写入任何关联行）
Real LLM                   = SKIP  （默认不调用 DeepSeek / SiliconFlow）
```

---

## 6. 边界确认

```text
Evidence 新增字段            = 0（dataset_version / source_type 等保持 Step 36–38）
conversation_id 进入 Evidence = 0（Step 27 硬约束）
evidence_id 进入 Conversation = 0
turn_id / assistant_request_id 进入关联表 = 0（OD-12 DEFERRED）
Service / API 新增           = 0
AI Runtime 核心行为修改       = 0（Router / Orchestrator / RAG / Tool / Text-to-SQL 未改）
```
