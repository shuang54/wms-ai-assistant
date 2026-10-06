# Phase 4.2 Step 1A — OD-22 Idempotency Architecture Decision

> **ONLY ANALYSIS + ARCHITECTURE DECISION**：未修改 backend / tests / ORM / DB / API。
> 不修改 Phase 4.2 Roadmap v1.0、Phase 4.1 Roadmap / Lifecycle Contract、Step 1 文档。

---

## 1. Decision Scope

回答：**是否接受为实现 Message Idempotency 而新增 API 字段 + DB 持久化 + UNIQUE 约束？**
并在 Architecture A（ConversationTurn 承担）与 B（独立 MessageRequest）之间做出选择。

---

## 2. Current Runtime Facts（Step 1 冻结，不推翻）

```text
客户端请求体                 = { "content": ... }（仅 content）
assistant_request_id         = CORRELATION ONLY（AI 执行阶段由 new_request_id() 生成，晚于 USER Turn）
assistant_request_id        ≠ idempotency_key
conversation_turn            = turn_id · conversation_id · role · content ·
                               assistant_request_id · created_at（无幂等键、无 status、无 result）
TX1：USER Turn commit → AI execution → TX2：ASSISTANT Turn commit（两阶段，AI 在事务之外）
ASSISTANT Turn 持久化内容     = content + assistant_request_id（**不含** route / data / metadata）
HTTP 响应                    = ConversationMessageResponse（route · content · data · metadata）
```

---

## 3. Architecture A — ConversationTurn 承担幂等

```text
conversation_turn
├── turn_id
├── conversation_id
├── role
├── content
├── assistant_request_id
├── idempotency_key          ← 新增（NULL 允许，兼容历史行）
└── created_at

UNIQUE (conversation_id, idempotency_key)
```

能力：

* 幂等身份 ✓ · Conversation isolation ✓ · 并发最终保证（UNIQUE）✓
* 状态推断：**USER turn 存在 + ASSISTANT turn 缺失 = 未完成/失败**（可推断，非显式字段）
* 结果重放：**仅 content 级**（ASSISTANT turn 只存 content；
  `route / data / metadata` **未持久化** → 无法完整重建 `AIOrchestrationResult`）
* 最小变更：1 列 + 1 约束；不引入新持久化模型

---

## 4. Architecture B — 独立 MessageRequest

```text
conversation_message_request
├── request_id
├── conversation_id
├── idempotency_key
├── request_fingerprint
├── status            （PROCESSING / SUCCEEDED / FAILED）
├── response snapshot （route / content / data / metadata）
├── created_at / updated_at

UNIQUE (conversation_id, idempotency_key)
```

能力：

* 幂等身份 ✓ · **显式状态** ✓ · **完整结果重放** ✓ · 并发 ✓
* 代价：新表 + 新 Repository + 新生命周期 + 清理策略；
  与 `conversation_turn` 存在**职责重叠**（USER turn 与其并存）；
  可能提前解决 Step 2（AI Result 持久化语义）与 Step 6（事务边界）→ **过度设计风险高**

---

## 5. Architecture Comparison

| Capability | A: ConversationTurn | B: MessageRequest |
| ---------- | ------------------- | ----------------- |
| 幂等身份 | ✓（新增列） | ✓ |
| 请求状态 | 推断（ASSISTANT turn 存在与否） | **显式** status |
| payload fingerprint | 需新增列或由 content 比对 | ✓ 原生 |
| 并发控制 | UNIQUE ✓ | UNIQUE ✓ |
| 重复识别 | ✓ | ✓ |
| 成功结果重放 | **仅 content 级** | **完整（含 route/data/metadata）** |
| 失败重试 | ✓（推断：无 ASSISTANT turn 可重试） | ✓（显式 FAILED） |
| HTTP response replay | 部分（content） | 完整 |
| Conversation isolation | ✓（scope = conversation） | ✓ |
| 生命周期 | 随 Turn（无额外清理） | 需独立清理策略 |
| 与 ConversationTurn 关系 | 同一行承载 | 重叠（需映射） |
| 与 AIOrchestrationResult 关系 | 间接（仅 content） | 直接（快照） |
| 与 Evidence 关系 | 均不进入 Evidence（契约保证） | 同 |
| 变更规模 | **1 列 + 1 约束** | **1 新表 + Repository + 状态机** |

---

## 6. API Contract

| 维度 | Header `Idempotency-Key` | Body `idempotency_key` |
| ---- | ------------------------ | ---------------------- |
| 语义归属 | **request metadata**（非业务内容） | 混入 message 业务体 |
| DTO 影响 | 不改 `ConversationMessageRequest`（Step 12 契约：客户端只提供 content） | **改 DTO**，与既有契约措辞冲突 |
| OpenAPI | Header 参数显式 | body 字段显式 |
| 客户端 retry | 标准做法（幂等键与业务体解耦） | 可行但耦合 |
| 日志 / 审计 | 便于按 header 记录 | 需解析 body |

**Decision：Header `Idempotency-Key`**（更符合"request metadata ≠ message content"边界，
且不破坏 Step 12 冻结的 DTO 语义）。

---

## 7. Idempotency State Model

在 Architecture A 下**不新增状态字段**，状态由既有事实推断：

```text
无 USER turn（同 key）                        → NEW（首次）
USER turn 存在 + ASSISTANT turn 存在          → SUCCEEDED（可 content 级 replay）
USER turn 存在 + ASSISTANT turn 缺失          → NOT_COMPLETED（允许重试）
```

→ 若未来需要区分 `EMPTY / REFUSED / FAILED`（API 层已有该语义但**未持久化**），
则 A 不足 → 属 **Step 2 / 后续决策**（见 §13）。

---

## 8. Request Fingerprint

```text
fingerprint = SHA-256(canonical(conversation_id + content))
```

* `idempotency_key` **不进入** fingerprint（fingerprint 判断"同 key 是否为同请求"，不是判断 key 本身）；
* 输入范围：`conversation_id` + `content`（`project_id` 由 conversation 绑定，不重复计入）；
* 用途：同 key 不同 payload → **conflict**；
* 未实现（仅设计）。

---

## 9. Duplicate / Conflict / Retry

| 场景 | 行为（Architecture A） |
| ---- | ---------------------- |
| 已完成（SUCCEEDED）+ 同 key 同 payload | **duplicate**：返回既有 ASSISTANT turn 的 content（**不**重新执行 AI） |
| 同 key 不同 payload | **conflict**：明确错误（不静默执行第二条） |
| 不同 key 同 content | **两个独立请求** |
| 不同 conversation 同 key | **隔离**（互不视为 duplicate） |
| NOT_COMPLETED（无 ASSISTANT turn） | **允许重试**（不因 USER turn 已存在而锁死） |

---

## 10. Concurrent Requests

```text
K1 = PROCESSING 期间的第二请求：
```

| Policy | 评估 |
| ------ | ---- |
| 1 等待 A 完成 | 需锁/等待机制；AI 延迟秒级 → 请求挂起，复杂度高 |
| 2 返回 409 / 202 让客户端稍后 retry | **最简单且与当前架构匹配**（FastAPI async + 两阶段事务；无 in-flight 协调组件） |
| 3 直接读最终结果 | 仅在已 SUCCEEDED 时可行（等价于 duplicate 路径） |

**Decision：Policy 2（409 Conflict + 客户端 retry）**；已 SUCCEEDED 时走 Policy 3（duplicate replay）。
并发最终正确性由 `UNIQUE(conversation_id, idempotency_key)` 保证（后到者插入失败 → 走 duplicate/conflict 判定）。

---

## 11. Crash Windows

| Crash | 状态 | Retry 行为 |
| ----- | ---- | ---------- |
| A：USER turn commit 后崩溃，AI 未执行 | 无 ASSISTANT turn | **允许重试**（幂等键不锁死） |
| B：AI 执行完成，ASSISTANT turn 未提交 | **ambiguous outcome** | **已知风险**：A 架构下无法判定 AI 是否已执行 → 重试可能产生第二次 AI 调用 |
| C：ASSISTANT turn 已提交，HTTP 响应丢失 | SUCCEEDED | **duplicate replay（content 级）**，不重新执行 AI |

**Crash B 是本阶段明确的已知限制**：完整解决需 Architecture B（显式 status + 结果快照）
或 AI 层提供副作用幂等；当前 RAG / 只读 SQL 场景下影响有限，
**未来 Tool 有副作用时必须重新评估**（记录为风险，不在本阶段解决）。

---

## 12. DB Design

| 方案 | 结论 |
| ---- | ---- |
| A1 `conversation_turn.idempotency_key` + `UNIQUE(conversation_id, idempotency_key)` | **采纳**（最小、与 Turn 生命周期一致、无新表） |
| A2 独立 `conversation_message_request` | **DEFERRED**（仅当需要显式状态 / 完整 response replay 时再引入） |
| A3 仅用 `turn_id` | **排除**（持久化后才有；是存储身份，非请求身份） |

```text
DB migration necessity = YES（1 列 + 1 UNIQUE；create_all 模式，非 Alembic）
```

---

## 13. Step 2 Dependency

* Step 1A **不需要**先定义 Assistant Turn 与 Request 的关联：
  A 架构下 USER turn 承载 `idempotency_key`，ASSISTANT turn 由既有流程创建；
* **Step 2（AI Result → Assistant Turn 语义）**将决定：
  `EMPTY / REFUSED / FAILED` 是否持久化 → 直接影响 A 架构能否区分这些结果；
* 因此：`idempotency_key` 进入 **ConversationTurn（USER 行）**，
  与 Assistant Turn 的对应关系由 Step 2 明确。

---

## 14. Step 6 Dependency

当前 `TX1 → AI → TX2` 两阶段结构：

```text
幂等保留（reservation）发生在 TX1（随 USER turn 写入 idempotency_key）
```

* **本 Step 不改变 TX1 / TX2**；
* Step 6（Conversation Runtime Transaction Boundary）需决定是否将
  "保留 + AI + 结果"合并或维持两阶段 → **记录为 Step 6 decision dependency**。

---

## 15. Evidence Boundary

无论 A / B，禁止：

```text
Evidence.idempotency_key · Evidence.conversation_id · Evidence.turn_id
· Evidence.assistant_request_id · provenance = conversation runtime identity
```

保持链路：

```text
Conversation → Message Request / Turn → AI Result → Evidence → ConversationEvidence
```

`Evidence` 仍为独立 Domain Object；幂等键只存在于 Conversation 侧。

---

## 16. Decision

```text
Decision:
Architecture           = A（ConversationTurn 承担幂等）
API                    = HTTP Header `Idempotency-Key`（可选，非必填）
Persistence            = conversation_turn.idempotency_key（USER turn 行）
Unique Key             = UNIQUE(conversation_id, idempotency_key)
Fingerprint            = SHA-256(conversation_id + content)（key 不入指纹）
Completed Duplicate    = duplicate replay（content 级，不重新执行 AI）
In-flight Duplicate    = 409 Conflict + 客户端 retry
Failed / Not completed = 允许重试（USER turn 存在但无 ASSISTANT turn）
Conflict               = reject（同 key 不同 payload）
Concurrency            = DB UNIQUE 最终保证
Crash B                = 已知限制（ambiguous outcome），不在本阶段解决
DB migration           = YES（1 列 + 1 UNIQUE），**待授权实施**
```

---

## 17. Consequences

* 需要一次 DB 变更（新增列 + UNIQUE）—— 与 Phase 4.2 Roadmap 默认 `DB CHANGE = NO`
  存在**策略冲突**，须由你显式授权后实施；
* 完整 HTTP response replay（route/data/metadata）**不在 A 架构范围内**
  （仅 content 级），若产品要求完整重放 → 升级为 Architecture B；
* `EMPTY / REFUSED / FAILED` 的持久化区分依赖 Step 2；
* in-flight 采用 409 + retry，客户端需处理该状态码。

---

## 18. Open Questions

| ID | 问题 | Status |
| -- | ---- | ------ |
| OD-23 | 是否需要完整 HTTP response replay（决定 A 是否升级为 B） | **OPEN** |
| OD-24 | `EMPTY / REFUSED / FAILED` 是否持久化（Step 2 决策） | **OPEN（Step 2）** |
| OD-25 | Crash B（ambiguous outcome）可接受度 / 是否需要 B 或 AI 层幂等 | **OPEN** |
| OD-22 | 是否授权 DB 变更（1 列 + UNIQUE）实施 | **待你授权**（决策已定） |

---

## 19. Future Test Design（不实现）

```text
T1  same key + same payload            → duplicate
T2  same key + different payload       → conflict
T3  different key + same content       → independent
T4  same key across conversations      → isolated
T5  sequential duplicate               → no second AI execution
T6  concurrent duplicate               → exactly one logical execution（其余 409）
T7  first request failure + retry      → allowed
T8  crash before assistant persistence → allowed retry（Crash A）
T9  response lost + retry              → content-level replay（Crash C）
T10 assistant_request_id correlation-only → 仍随每次 AI 执行变化，≠ idempotency_key
```
