# Phase 4.2 Roadmap v1.0

> 性质：**Architecture Analysis / Roadmap**，不是编码阶段。
> 依据：真实代码 + 真实 Git 历史 + 真实测试 + 已冻结契约（四者共同决定，不凭经验设计）。
> Phase 4.1 基线：`Phase 4.1 = RELEASED / MERGED / VERIFIED`（main `585e0cb`）。

---

## 1. Phase 4.2 Goal

在 Phase 4.1 建成的 **Conversation + Evidence Persistence Foundation** 之上，
把 Conversation 变成**真正可持续运行的 AI Runtime**：

```text
User Message
    ↓
Conversation Runtime（真实）
    ↓
Conversation Context（真实）
    ↓
AI Orchestrator（真实，Phase 3 复用）
    ↓
AI Result
    ├── ConversationTurn（已存在）
    ├── Evidence（**尚未接通**）
    └── ConversationEvidence（**尚未接通**）
```

Phase 4.2 = **Integration**，不是 rewrite（Phase 3 能力全部复用）。

---

## 2. Phase 4.1 Baseline（已冻结，继承）

```text
Evidence 持久化             PASS
Provenance（2 字段）        PASS
Annotation 关联 / Review     PASS
幂等键（组合）+ 事务         PASS
Lifecycle（状态机 + 矩阵）   PASS
Conversation ↔ Evidence 契约 PASS
ConversationEvidence 持久化  PASS
Real Conversation E2E       PASS
Security / Boundary         PASS
Traceability                PASS
Release / Merge             PASS
```

继承的硬约束：

* Evidence identity / provenance **不得**含 `conversation_id` / `turn_id` /
  `assistant_request_id` / `provider_request_id`；
* Conversation ↔ Evidence = **REFERENCE**（非 ownership）；
* OD-12（turn-level）仍 **DEFERRED**；
* RBAC / Multi-tenancy / Agent / MCP / Memory / Workflow = **OUT OF SCOPE**。

---

## 3. Current Architecture（真实代码）

| 层 | 组件 | 状态 |
| -- | ---- | ---- |
| Persistence | `ConversationRepository` / `EvidenceRepository` / `ConversationEvidenceRepository` | 真实（ORM-only，Repository owns transaction） |
| Application | `ChatApplicationService.execute_message()` | **真实生产路径** |
| Context | `ConversationContextBuilder.build_context(turns)` | 真实（仅历史 turns → `"role: content"` 字符串） |
| AI Runtime | `AIOrchestratorService.execute()`（Router → RAG / Tool / Text-to-SQL） | 真实（Phase 3 既有） |
| API | `backend/app/api/conversations.py`（`/api/conversations*`） | 真实 |
| Trace | `new_request_id()` + `assistant_trace_scope` + LLM Usage `request_id` | 真实 |
| Evidence Runtime | **AI Result → Evidence 映射** | **MISSING** |
| Association Runtime | `create_association()` 生产调用方 | **无（仅 Repository 定义 + tests）** |

---

## 4. Current Conversation Runtime

`ChatApplicationService.execute_message(conversation_id, content)` 真实流程（代码实测）：

```text
1) 读 Conversation（不存在 → ConversationNotFoundError；ARCHIVED → 写入前拒绝）
2) TX1：append_turn(USER, assistant_request_id=None)          ← 已提交
3) 读历史 turns（精确排除当前 USER turn）
4) context = context_builder.build_context(previous_turns)     ← 仅历史；当前问题不重复进 context
5) orchestrator = factory(conversation.project_id)             ← 生产 = 真实 Orchestrator
   result = await orchestrator.execute(user_turn.content, context=context)
6) TX2（条件式）：content 非空 且 metadata.request_id 合法
   → append_turn(ASSISTANT, assistant_request_id=request_id)
7) return AIOrchestrationResult
```

结论：

* **生产路径 = REAL**（默认 factory 构造真实 Orchestrator；Step 46 的 Fake 仅测试注入）；
* **不创建 Evidence、不创建 ConversationEvidence**；
* AI 失败：异常原样上抛（USER turn 已提交保留；不创建 ASSISTANT turn；不包装 / 不 retry）；
* 空内容 / 缺 request_id：不创建 ASSISTANT turn（不伪造 ID）。

---

## 5. Current AI Runtime

* `AIOrchestrationResult`（frozen）：`route` · `content` · `data` · `metadata`
  （metadata 含 `request_id`、`outcome`；不含凭据 / 域 ID）；
* Phase 3 能力全部可直接复用（Router / RAG / Tool / Text-to-SQL / Validator / ReadOnly Executor）；
* Context 以 `str | None` 传入（真实契约支持 None）。

---

## 6. Current Evidence Runtime（核心 Gap）

```text
EvidenceBuilder / EvidenceFactory / EvidenceMapper / AIResultToEvidence → 0 命中 = MISSING
create_association 调用方 → 仅 Repository 自身 + tests = 无生产调用方
```

→ 当前 **AI Result → Evidence → ConversationEvidence** 在生产侧**完全未接通**；
Step 46 的闭环是 **test-composed**（测试层按冻结契约写入）。

---

## 7. Current Trace / Observability

```text
Orchestrator: request_id = new_request_id()  → assistant_trace_scope(request_id)
                                              → LLM Usage / Tool / RAG 记录关联到 request_id
ConversationTurn.assistant_request_id        = 该 request_id（correlation，非 FK）
Evidence                                     = 不含 request_id（Step 27 硬约束）
```

→ Conversation → AI request → LLM/Tool/RAG **已可经 request_id 关联**；
但 **request_id 无法到达 Evidence**（契约禁止）→ Evidence 与 Trace 的关联需新设计（见 OD）。

---

## 8. Gap Analysis（Q1–Q12）

| # | 问题 | Current State | Contract Status | Gap | Recommended |
| - | ---- | ------------- | --------------- | --- | ----------- |
| Q1 | Runtime Entry | `ChatApplicationService.execute_message` | IMPLEMENTED | 无 API/Service 歧义，但**无消息幂等** | Phase 4.2 |
| Q2 | User Message 身份 | `turn_id`（自增），无业务唯一键 | FROZEN(turn_id) / UNDEFINED(业务键) | 重复提交无法识别 | Phase 4.2（OD-14） |
| Q3 | assistant_request_id | correlation only（非 FK、非幂等键） | FROZEN | 不承担幂等 | 保持（OD-14） |
| Q4 | Context 边界 | 仅历史 turns（当前问题不入 context，无窗口上限记录） | IMPLEMENTED | 窗口/预算策略未冻结 | Phase 4.2（OD-20） |
| Q5 | Context → Orchestrator | `execute(question, context=str\|None)` | FROZEN | 无 | 复用 |
| Q6 | AI Result → Turn | 条件式（content + request_id） | IMPLEMENTED | 错误/拒答语义未正式化 | Phase 4.2（OD-19） |
| Q7 | AI Result → Evidence | **无生产映射** | MISSING | Evidence Builder | Phase 4.2（OD-17） |
| Q8 | 谁创建 Evidence / Association | 当前**无人创建**（生产） | UNDEFINED | 归属未定 | Phase 4.2（OD-17/18） |
| Q9 | AI 失败一致性 | USER 保留、无 ASSISTANT、异常上抛 | IMPLEMENTED | 未覆盖"部分失败 + Evidence"场景 | Phase 4.2（OD-19） |
| Q10 | 多轮顺序 | turn_id 自增 + created_at；无并发保护 | IMPLEMENTED | 并发消息顺序未定义 | Phase 4.2（OD-15） |
| Q11 | 重复提交 | 无幂等（每次都新建 turn 并调用 AI） | UNDEFINED | 幂等键缺失 | Phase 4.2（OD-14） |
| Q12 | request_id 贯穿 | Conversation ✓ / AI ✓ / LLM Usage ✓ / **Evidence ✗** | 部分 | Evidence 侧关联缺口 | Phase 4.2（OD-16/OD-21） |

---

## 9. Architecture Target（每条箭头状态）

```text
                         User Message
                              │ CURRENT
                              ▼
                     Conversation Runtime            CURRENT
                              │
                    ┌─────────┴─────────┐
                    │ CURRENT           │ CURRENT
              Conversation          Context Builder
                    │                   │
                    └─────────┬─────────┘
                              ▼ CURRENT
                       AI Orchestrator（Phase 3 复用）
                              │ CURRENT
                       AI Router
                    ┌─────────┼─────────┐
                    ▼         ▼         ▼  CURRENT
                   RAG       Tool    Text-to-SQL
                    │         │         │
                    └─────────┼─────────┘
                              ▼ CURRENT
                     AIOrchestrationResult
                         │       │
                  ┌──────┘       └──────┐
                  ▼ CURRENT             ▼ **MISSING**
          ConversationTurn          Evidence（Builder）
                                        │ **MISSING**
                                        ▼
                              ConversationEvidence
                                        │ CURRENT（持久化层已就绪）
                                        ▼
                                   PostgreSQL
```

* `AIOrchestrationResult → Evidence` = **MISSING（Phase 4.2 核心）**
* `Evidence → ConversationEvidence` = **MISSING（生产创建，Phase 4.2）**
* `Evidence → Trace` = **FUTURE**（受 Step 27 禁止 request_id 入 Evidence 约束）

---

## 10. Frozen Contracts（仅继承，不新冻结）

* Phase 4.1 全部契约（Evidence / Provenance / Annotation / 幂等 / 状态机 /
  Conversation ↔ Evidence / 关联表 / 安全边界）；
* `AIOrchestrationResult` 结构（不得为 Phase 4.2 添加 conversation_id / evidence_id）；
* `assistant_request_id` = correlation（不得升级为 FK / Evidence identity）。

---

## 11. Open Decisions（真实编号）

| ID | Open Decision | 归属 |
| -- | ------------- | ---- |
| OD-12 | turn-level provenance（继承 Phase 4.1） | DEFERRED |
| OD-14 | Conversation message idempotency（业务唯一键是否存在） | Phase 4.2 |
| OD-15 | 同一 Conversation 并发消息的顺序语义 | Phase 4.2 |
| OD-16 | AI Result 持久化语义（哪些字段进 Turn / Evidence / Trace） | Phase 4.2 |
| OD-17 | Evidence Builder 归属与触发者（Service / Application / Repository） | Phase 4.2 |
| OD-18 | Conversation Runtime transaction boundary（Turn + Evidence 是否同事务） | Phase 4.2 |
| OD-19 | AI 失败 / 拒答 / 空内容时的 Turn 与 Evidence 语义 | Phase 4.2 |
| OD-20 | Context window / budget 策略 | Phase 4.2 |
| OD-21 | Evidence ↔ Trace 关联方式（在禁止 request_id 入 Evidence 前提下） | Phase 4.2 / FUTURE |

---

## 12. Step-by-Step Roadmap

每个 Step 只解决一个边界；不得合并为"完成 Conversation Runtime 全部功能"。

### Step 1 — Conversation Message Idempotency Contract

* 目标：定义"同一条 User Message 重复提交"的识别与处理（OD-14）
* 依赖：Phase 4.1（Conversation 持久化）
* INPUT：现有 `append_turn` / `execute_message`
* OUTPUT：幂等契约（UNDEFINED → FROZEN）；若不需要业务键则明确"不新增"
* TEST CODE：YES · PRODUCTION CODE：待定（契约阶段 0）
* DB CHANGE：**NO**（优先不加列；确需时另行决策）· API CHANGE：NO
* SECURITY IMPACT：LOW · RISK：MEDIUM（可能触发 AI 重复调用）
* 禁止：自行发明第二套 ID 体系；把 assistant_request_id 当幂等键

### Step 2 — AI Result → ConversationTurn 语义冻结

* 目标：冻结 content / route / request_id / 错误 / 拒答 如何进入 ASSISTANT turn（OD-16、OD-19）
* 依赖：Step 1
* OUTPUT：Turn 持久化语义表（哪些入库 / 哪些仅 runtime）
* TEST CODE：YES · PRODUCTION CODE：仅当现有行为与契约冲突时最小修正
* DB CHANGE：NO · API CHANGE：NO · SECURITY：LOW · RISK：MEDIUM
* 禁止：为错误场景伪造 request_id；把 raw LLM 内容写入 turn

### Step 3 — AI Result → Evidence 映射契约（Evidence Builder Contract）

* 目标：冻结 `AIOrchestrationResult → EvidenceRecord` 的映射规则（OD-17）
* 依赖：Step 2 + Phase 4.1 Evidence 契约
* OUTPUT：映射契约（dataset_version / source_type / de_identification_* 如何取值）
* PRODUCTION CODE：**YES**（最小 Builder，放 Service 层，不新增 DB）· TEST CODE：YES
* DB CHANGE：NO · API CHANGE：NO · SECURITY：MEDIUM（内容边界）· RISK：**HIGH**
* 禁止：把 conversation_id / turn_id / request_id / raw_content 写入 Evidence

### Step 4 — Evidence Runtime Integration（生产创建）

* 目标：让真实 AI Runtime 产生的 Evidence 进入 `EvidenceRepository`
* 依赖：Step 3
* OUTPUT：生产侧 Evidence 创建路径（替代 test-composed）
* PRODUCTION CODE：YES · TEST CODE：YES（DB + E2E）
* DB CHANGE：NO · API CHANGE：NO · SECURITY：MEDIUM · RISK：HIGH

### Step 5 — ConversationEvidence Runtime Association

* 目标：生产侧调用 `create_association()`（当前仅 Repository + tests）（OD-18）
* 依赖：Step 4
* PRODUCTION CODE：YES · DB CHANGE：NO · API CHANGE：NO
* SECURITY：MEDIUM · RISK：MEDIUM
* 禁止：绕过 Repository 裸写关联；把 association 变成 ownership

### Step 6 — Conversation Runtime Transaction Boundary

* 目标：明确 Turn / Evidence / Association 的事务归属（OD-18）
* 依赖：Step 4、Step 5
* OUTPUT：事务边界契约（是否允许跨 Repository 单事务；失败回滚语义）
* PRODUCTION CODE：待定（契约优先）· DB CHANGE：NO · RISK：MEDIUM
* 禁止：引入 Unit of Work / 全局 Session；把多 Repository 强行合并

### Step 7 — Multi-turn Context 边界

* 目标：冻结 Context window / budget / 当前问题是否入 context（OD-20）
* 依赖：Step 2
* OUTPUT：Context 契约（现状：仅历史 + 无上限记录）
* PRODUCTION CODE：待定 · DB CHANGE：NO · RISK：MEDIUM
* 禁止：设计 long-term / user / semantic / episodic memory（≠ Conversation Context）

### Step 8 — Trace Correlation

* 目标：在禁止 request_id 入 Evidence 前提下，定义 Evidence ↔ Trace 关联（OD-21）
* 依赖：Step 4、Step 5
* OUTPUT：关联方案（若当前证据不足 → 维持 DEFERRED）
* DB CHANGE：**NO 优先** · RISK：MEDIUM · 可 FUTURE
* 禁止：把 request_id 写入 Evidence identity / provenance

### Step 9 — Conversation Runtime E2E

* 目标：真实链路 E2E（User Message → Context → Orchestrator → Turn + Evidence + Association → 读回）
* 依赖：Step 1–8
* TEST CODE：YES（真实 PostgreSQL；Fake 仅用于 LLM/外部依赖）
* DB CHANGE：NO · RISK：MEDIUM

### Step 10 — Security / Boundary Audit

覆盖：Conversation isolation · Evidence isolation · Association boundary ·
Read Model · 敏感字段 · Identity boundary · Repository boundary
继承 Phase 4.1 Security Matrix；不引入 RBAC

### Step 11 — Regression / Traceability

建立 Step → Contract → Implementation → Test 追溯矩阵；Contract Drift = 0

### Step 12 — Release Readiness

判定 BLOCKER / NON-BLOCKER / DEFERRED / OUT_OF_SCOPE；Matrix + 全量回归

### Step 13 — Git Closeout（Commit / Push / PR / Merge / Verification）

沿用 Phase 4.1 Step 50–51 流程

---

## 13. Dependency Graph

```text
Step 1 (Idempotency Contract)
   ↓
Step 2 (Turn Semantics)
   ├── Step 3 (Evidence Builder Contract) → Step 4 (Evidence Runtime)
   │                                          ↓
   │                                    Step 5 (Association Runtime)
   │                                          ↓
   │                                    Step 6 (Transaction Boundary)
   └── Step 7 (Context Boundary)
                    ↓
              Step 8 (Trace Correlation)  ← 可 DEFERRED
                    ↓
              Step 9 (E2E)
                    ↓
              Step 10 (Security)
                    ↓
              Step 11 (Traceability)
                    ↓
              Step 12 (Release)
                    ↓
              Step 13 (Git Closeout)
```

Parallel-eligible：Step 4/5 与 Step 7 数据域不同；Step 8 可延后。

---

## 14. Security Boundaries

* Conversation isolation · Evidence isolation · Cross-conversation reference 禁止；
* Evidence identity / provenance 不含会话与请求 ID（Step 27 硬约束）；
* Read Model 仍 frozen、无 ORM/Session 泄漏；
* Repository 边界：不得出现 service → raw SQL / API → Session；
* 新增分析项：Prompt injection boundary、Tool 权限边界、SQL 权限边界（**仅分析**）；
* **RBAC / Multi-tenancy / ACL / Object-level Authorization = OUT OF SCOPE**。

---

## 15. Database Change Plan

```text
默认：NO DB CHANGE
```

Phase 4.2 候选变更必须逐项回答：Why? 现有表为何不足? 是否触碰冻结契约?
事务要求? Read Model? 幂等? 无充分理由 → NO。
当前**不预见**必须新增表（Evidence / Annotation / Conversation / ConversationEvidence 已够用）；
若 Step 1 幂等需要业务键，须单独决策（优先不加列）。

---

## 16. API Change Plan

* 现状：`POST /api/conversations` · `GET /api/conversations/{id}` ·
  `GET /api/conversations/{id}/messages` · `POST /api/conversations/{id}/archive` ·
  `POST /api/conversations/{id}/messages` = **EXISTS**
* 缺口：`GET /api/conversations`（列表）= **MISSING**（若需要须单独论证）
* 原则：Application Service ≠ HTTP API；不为"未来方便"提前新增 API；
  Phase 4.2 优先在 Application/Service 层完成 Runtime，API 仅在真实需要时列入。

---

## 17. Testing Strategy

| 层级 | 用途 |
| ---- | ---- |
| Unit | 契约常量 / 映射规则（纯函数） |
| Integration | Service + Repository（Fake 仅替换 Orchestrator/LLM） |
| DB Integration | 真实 PostgreSQL（RUN_DB_TESTS=1） |
| Conversation E2E | 真实链路 + 新 Session 读回 |
| Security E2E | 隔离 / 负例 / 身份边界 |
| Regression | 全量（不带 DB）+ 全量（RUN_DB_TESTS=1）+ Matrix |

默认 **Real LLM = SKIP**（沿用 Phase 3/4.1 Fake 基础设施，不新建 Fake LLM Framework）。

---

## 18. Release Gate（候选）

```text
Conversation Runtime        PASS
Multi-turn Context          PASS
AI Runtime Integration      PASS
AI Result Persistence       PASS
Evidence Integration        PASS（若 Step 3–5 完成）
Idempotency                 PASS
Error Semantics             PASS
Trace Correlation           PASS / DEFERRED（OD-21）
Security                    PASS
E2E                         PASS
Regression                  PASS
Production DB writes        0
```

---

## 19. Deferred

```text
OD-12  turn-level provenance（关联表仍无 turn_id）
OD-21  Evidence ↔ Trace 直接关联（受 Step 27 约束，可能长期 DEFERRED）
Conversation list API（未论证前不列入）
```

---

## 20. Out of Scope

```text
Agent · Multi-Agent · MCP · Memory（long-term / user / semantic / episodic）
Workflow · Planning · 自主重规划
RBAC · Multi-tenancy · ACL · Object-level Authorization
Chat UI · WebSocket / SSE / Streaming UI · Markdown rendering
Phase 3 能力重写（AIOrchestrator / Router / RAG / Tool / Text-to-SQL 一律复用）
```

潜在瓶颈（**仅记录，不优化**）：Context 体积随轮次增长、每轮 DB 往返次数、
Evidence 持久化写入、Trace 写入频率。

---

## 21. Reconciliation（Phase 4.2 Step 3B）

> 本节为 **追加修订**（不删除 §1–§20 原始决定，确保历史可追溯）。
> 触发：Step 3A 确认 `Evidence = Source / Provenance Unit` 且 Runtime 无 `dataset_version`。

### 21.1 Revised Scope

```text
Phase 4.2 = Production Conversation Runtime Foundation
```

```text
Message Idempotency → AI Result → Assistant Turn Semantics → Conversation Context
→ Transaction / Concurrency → Real Conversation Runtime E2E → Security / Release
```

**Runtime Evidence 不再是 Phase 4.2 完成条件。**

### 21.2 Step Reconciliation

| Step | Original | Reconciled | Reason |
| ---- | -------- | ---------- | ------ |
| 4 | Evidence Runtime Integration | **DEFERRED** | 依赖 OD-31（Artifact 架构）+ OD-32（Runtime Dataset Version）；当前无合法输入 |
| 5 | ConversationEvidence Runtime Association | **DEFERRED** | 与 Step 4 同因；持久化层已在 Phase 4.1 Step 45 完成，保留为 Foundation |
| 6 | Conversation Runtime Transaction Boundary | **MODIFY + KEEP** | 收敛为 Turn 一致性 / 幂等 / 并发；移除 Evidence 事务部分 |
| 7 | Multi-turn Context 边界 | **KEEP** | 独立于 Runtime Evidence，属本阶段核心 |
| 8 | Trace Correlation | **MODIFY + KEEP** | 收敛为验证 `request_id` 已有关联；不为 Evidence 扩展 |
| 9 | Conversation Runtime E2E | **MODIFY + KEEP** | 链路不含 Evidence：`Message → USER Turn → Context → Orchestrator → AI Result → ASSISTANT Turn → Trace` |
| 10–13 | Security / Traceability / Release / Git | **KEEP** | 不受影响 |

### 21.3 Evidence Boundary（冻结）

```text
Evidence = Source / Provenance Unit（Dataset · Provenance · Lifecycle · Annotation · Review · Conversation Reference）
不是 AI Answer / Runtime Result / Tool Result / RAG Chunk / SQL Result
```

### 21.4 Open Decisions（均 OPEN，未关闭）

```text
OD-26 EMPTY 是否视为 idempotency completed
OD-30 EMPTY 是否允许 Builder 独立消费 data
OD-31 Runtime Evidence Artifact Architecture（候选 Architecture B）
OD-32 Runtime Dataset Version Contract（禁止 request_id / conversation_id / turn_id /
      timestamp / UUID / idempotency_key 冒充 dataset_version）
```

### 21.5 Revised Completion Criteria

```text
Message Idempotency（契约 + 授权后实施）· AI Result → Turn Semantics · Multi-turn Context
· Transaction / Concurrency · Runtime E2E · Trace Correlation · Security · Traceability · Release
· Production DB writes = 0
（不含 Runtime Evidence 与 Runtime ConversationEvidence Association）
```

---

## 22. Reconciliation（Phase 4.2 Step 4A — OD-26 + Idempotency Implementation Authorization）

> 本节为 **追加修订**（不删除 §1–§21 原始决定，确保历史可追溯）。
> 触发：Step 4A 关闭 OD-26，并审计 Step 6（Transaction / Concurrency）实施前置条件。
> 依据文档：`docs/evaluation/Phase 4.2 Step 4A — OD-26 Decision.md`
> 本阶段 `backend diff = 0 · tests diff = 0 · DB schema = 0 · migration = 0 · API = 0`。

### 22.1 OD-26 Decision

```text
OD-26 = CLOSED（Option B）
EMPTY = NOT completed（无 replayable result → 允许重试）

依据：
  idempotency "completed" ⟺ 存在可重放结果（不是"AI 跑完了"）
  ASSISTANT Turn = 用户可见 message（Step 2 §4 Option A）⇒ 其存在性只是代理判据
  Option A（EMPTY = completed）在 Architecture A 下不可实现：
      建空 Turn = 伪造内容（Step 2 禁止）· 新增 status 列 = 禁止 ·
      assistant_outcome_record 与 Conversation 不可 join ·
      ConversationMessageResponse.route 必填 ⇒ 无法重放

统一 Retry 判据（覆盖 EMPTY / REFUSED+empty / FAILED / Crash A / Crash B / TX2 失败）：
  Retry ⟺ USER Turn 存在 ∧ ASSISTANT Turn 缺失
Retry 不创建第二条 USER Turn（复用既有 key 保留行）
```

### 22.2 Step 6 Scope Confirmation

```text
Step 6 = Transaction / Concurrency（Turn 一致性 + 幂等 + 并发 + 重试）
TX1（USER Turn + idempotency_key 保留）→ AI execution（事务之外）→ TX2（ASSISTANT Turn）
事务模型不修改 · 不含 Evidence · 不处理 Runtime Evidence
```

### 22.3 DB / API Authorization Request（待批准）

```text
DB : conversation_turn.idempotency_key VARCHAR(128) NULL
     UNIQUE(conversation_id, idempotency_key)（禁止 NULLS NOT DISTINCT；历史 NULL 行兼容）
     既有库需 init_db 幂等 DDL（ADD COLUMN / CREATE UNIQUE INDEX IF NOT EXISTS）
API: Idempotency-Key Optional Header（Body 仍只有 content；
     不新增 DTO 字段 · 不新增 MessageRequest 表）
```

### 22.4 Step 6 Readiness

```text
Step 6 = READY（条件：OD-22 DB 变更授权 + duplicate 响应形态 API 微调授权）
         契约侧 blocker = 0；OD-33 / OD-34 为 Step 6 内决策项
若 OD-22 未批准 → Step 6 = BLOCKED（唯一硬 blocker）
```

### 22.5 Open Decisions（Step 4A 后）

```text
OD-22 OPEN —— DB 变更实施授权（待批准）
OD-23 OPEN —— 完整 HTTP response replay（不阻塞 Step 6）
OD-24 CLOSED（Step 2 已定：Turn 不持久化 outcome）
OD-25 CLOSED（Crash B = 已知 limitation KL-1；副作用 Tool 出现时重新评估）
OD-26 CLOSED（EMPTY = NOT completed）
OD-30 OPEN（随 Step 3/4 DEFERRED）
OD-31 OPEN · OD-32 OPEN（Runtime Evidence，不关闭）
OD-33 OPEN —— in-flight vs NOT_COMPLETED 可区分性（Step 6 implementation concern）
OD-34 OPEN —— duplicate 重放响应形态（route 必填；禁止伪造）
```

### 22.6 Known Limitation

```text
KL-1  Crash B（AI 已执行、ASSISTANT Turn 未提交）：当前 DB 模型不可判定
      → 重试可能二次执行 AI；**不**通过伪造 execution status 解决；
        完整解决需 Architecture B 或 AI 层副作用幂等（触发条件：有副作用 Tool）
```

---

## 23. Reconciliation（Phase 4.2 Step 5A — OD-34 Duplicate Replay Response Contract）

> 本节为 **追加修订**（不删除 §1–§22 原始决定，确保历史可追溯）。
> 触发：Step 5A 关闭 OD-34，确认 duplicate 重放响应契约与 G-2 就绪度。
> 依据文档：`docs/evaluation/Phase 4.2 Step 5A — OD-34 Duplicate Replay Response Contract.md`
> 本阶段 `backend = 0 · tests = 0 · DB = 0 · API = 0 · migration = 0`。

### 23.1 Real Contract Facts（实测）

```text
ConversationMessageResponse = route(str, 必填) · content(str|None) · data(dict|None) · metadata(dict)
    —— 不含 conversation_id / turn_id / role（dto/conversation_api.py:144-166）
    —— 是 AI Runtime Result envelope（与 /api/ai/chat 的 ChatResponse 同构），不是 Message DTO
ConversationTurn（ASSISTANT）只持久化 content + assistant_request_id
    —— 不含 route / data / metadata / outcome
frontend/ = 空目录（0 文件）⇒ 当前仓库未发现可验证的前端消费方
data 在当前真实响应中恒为 null（_safe_message_data 只透传 JSON 基本类型 Mapping）
```

### 23.2 OD-34 Decision

```text
OD-34 = CLOSED
Duplicate = Message Replay（不是 Execution Result Replay）

HTTP 200 OK（不是 409：duplicate 是成功重放，不是冲突）
Response = ConversationMessageResponse（同一 DTO，不新增 DTO / 不改字段集合）
  content  = 已持久化 ASSISTANT Turn.content
  route    = null（DTO 微调为 str | None = None；禁止 "replay"/"unknown"/推断值）
  data     = null（ACCEPTED LOSS：Text-to-SQL 行数据不可重放）
  metadata = { request_id: <persisted assistant_request_id>, idempotent_replay: true }
AI 执行 = 0 · USER Turn 不新建 · ASSISTANT Turn 不新建
```

### 23.3 Rejected Options（记录）

```text
Option B 独立 Replay DTO      → 不推荐（OpenAPI anyOf 两套 200；客户端需判别；MVP 过度设计）
Option C 持久化 AI Result 入 Turn → **禁止**（Turn: message → AI Execution Result Store，
                                    违反 Step 2 §5/§8，重启 OD-16/19，双写观测数据）
Option D 从 observability 推断 route → **拒绝**（无 SQL 执行记录表 ⇒ text_to_sql 不可推断；
                                    且违反"绝不猜测"原则与依赖方向）
完整重放需求 → 走 OD-23（Architecture B），不得走 Option C
```

### 23.4 G-2 Readiness

```text
G-2 = READY
   Idempotency-Key Header ✅ · Duplicate Response Contract ✅（OD-34 CLOSED）
   · 409 In-flight ✅ · 422 invalid key ✅ · same-key different-payload 409 ✅
Step 6 开工仍需 G-1（OD-22 DB 变更授权）；G-1 未批准 ⇒ Step 6 = BLOCKED
```

### 23.5 Open Decisions（Step 5A 后）

```text
OD-22 OPEN · OD-23 OPEN · OD-24 CLOSED · OD-25 CLOSED · OD-26 CLOSED
OD-30 OPEN · OD-31 OPEN · OD-32 OPEN · OD-33 OPEN · OD-34 CLOSED
```

### 23.6 Known Limitation（新增）

```text
KL-2  Duplicate replay 不重放 route / data / metadata（仅 content + request_id）
      ⇒ Text-to-SQL 结果数据在 Architecture A 下丢失；
        完整重放需 Architecture B（OD-23），不得以持久化 AI Result 入 Turn 方式解决
```

---

## 24. Reconciliation（Phase 4.2 Step 6 — Transaction / Concurrency / Idempotency 实施）

> 本节为 **追加修订**（不删除 §1–§23 原始决定，确保历史可追溯）。
> Step 6 是 Phase 4.2 **第一个**允许修改 Backend / DB Schema / API / Tests 的实施阶段。
> 冻结来源：Step 1 · 1A（OD-22）· 2 · 4A（OD-26）· 5A（OD-34）。

### 24.1 实施内容

```text
Idempotency-Key Header（Optional · ≤128 · 空白=无幂等 · 超长 422 · 服务端不生成/不改写）
conversation_turn.idempotency_key VARCHAR(128) NULL
UNIQUE(conversation_id, idempotency_key)（标准 UNIQUE；禁止 NULLS NOT DISTINCT；历史 NULL 兼容）
init_db 幂等 DDL：ensure_conversation_turn_idempotency_key_column
                  ensure_conversation_turn_idempotency_unique_index
ChatApplicationService._resolve_user_turn()：先查幂等键 → 写 / 重放 / 重试
MessageReplay（frozen DTO）：content + assistant_request_id
ConversationMessageResponse.route: str → str | None = None（字段名集合不变）
409 映射：IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD · IDEMPOTENCY_KEY_IN_FLIGHT
```

### 24.2 事务模型（未修改）

```text
TX1（USER Turn + idempotency_key）→ AI execution（事务之外）→ TX2（ASSISTANT Turn）
幂等键只写 USER Turn（ASSISTANT 恒 NULL）
retry 复用既有 USER Turn：list_turns 后按 turn_id 精确排除 ⇒ 不重复注入 context
```

### 24.3 修改文件

```text
backend/app/api/conversations.py                    Header + 409 + replay DTO 映射
backend/app/db/conversation_repository.py           校验 · 读列 · insert · 幂等查询 · 唯一冲突异常
backend/app/db/init_db.py                           幂等 DDL（列 + 唯一索引）
backend/app/db/models/conversation_turn.py          idempotency_key 列 + UNIQUE 索引
backend/app/dto/conversation_api.py                 route 可选（OD-34）
backend/app/services/chat_application_service.py    幂等流程 + MessageReplay
backend/app/services/conversation_service.py        append_turn key · 查询方法 · fingerprint · 异常映射
tests/（15 个既有文件同步更新 + 2 个新增文件）
```

### 24.4 Open Decisions（Step 6 后）

```text
OD-22 OPEN   DB 变更已实施（待你确认后 commit）
OD-23 OPEN   完整 HTTP response replay（需要时走 Architecture B）
OD-24 CLOSED · OD-25 CLOSED · OD-26 CLOSED · OD-30 OPEN · OD-31 OPEN · OD-32 OPEN
OD-33 OPEN   in-flight vs NOT_COMPLETED 可区分性（实现采用 default retry）
OD-34 CLOSED duplicate = Message Replay（已实施）
```

### 24.5 已知限制（实施后仍然成立）

```text
KL-1  Crash B（AI 已执行、ASSISTANT Turn 未提交）⇒ AI exactly-once execution = NOT GUARANTEED
KL-2  duplicate replay 不重放 route / data / metadata（Text-to-SQL 行数据丢失）
```

### 24.6 回归状态

```text
全量（离线）    ：5931 passed · 5 failed（全部 = backend 未提交导致的 working-tree guard 及其下游）
全量（DB）      ：6661 passed · 6 failed（同上 + 1 个既有 DB 残留 baseline，单独运行通过）
compileall      ：0 errors
DB 残留（Step 6）：0
```

> 未提交原因：Step 6 未授予 commit 授权；working-tree guard 按历史约定
> **不 skip / 不改 baseline**，待授权 commit 后恢复绿灯。
