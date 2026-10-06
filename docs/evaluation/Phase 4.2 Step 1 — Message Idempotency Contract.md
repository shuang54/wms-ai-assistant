# Phase 4.2 Step 1 — Message Idempotency Contract（OD-14）

> 性质：**契约分析**（阅读 → 分析 → 契约设计 → 测试设计 → 决策）。
> 本 Step **未修改** backend / tests / API / DB（`backend = 0 · tests = 0 · DB schema = 0 · API = 0`）。

---

## 1. Current Message Flow（真实代码）

```text
HTTP POST /api/conversations/{id}/messages      CURRENT（客户端仅提供 content）
        ↓
ChatApplicationService.execute_message()        CURRENT
        ↓
Conversation Read（不存在 → 404；ARCHIVED → 409，写入前拒绝）   CURRENT
        ↓
USER Turn INSERT（assistant_request_id = None）  CURRENT（**TX1 立即提交**）
        ↓
History Read（排除当前 USER turn）              CURRENT
        ↓
ConversationContextBuilder.build_context()      CURRENT（仅历史 → str | None）
        ↓
AIOrchestrator.execute(content, context)        CURRENT（真实；request_id 在此生成）
        ↓
AIOrchestrationResult                           CURRENT
        ↓
ASSISTANT Turn INSERT（条件式：content 非空 + request_id 合法）  CURRENT（TX2）
        ↓
Return Result                                   CURRENT
```

状态标注：

```text
消息幂等键            = MISSING
幂等键持久化          = MISSING
重复请求识别          = MISSING
并发保护              = MISSING
请求状态持久化        = UNDEFINED（无 request state 字段）
```

---

## 2. Current ConversationTurn Contract

```text
turn_id               BIGINT 自增（持久化后才有）
conversation_id       FK → conversation
role                  USER / ASSISTANT
content               正文
assistant_request_id  可空；USER = None，ASSISTANT = Orchestrator request_id
created_at            写入时间
```

→ **不存在任何业务级 message / request idempotency key**。

---

## 3. assistant_request_id Analysis（7 问，真实代码）

| # | 问题 | 结论 |
| - | ---- | ---- |
| 1 | 能否作为消息请求唯一 correlation ID？ | **可以**（这正是它当前职责：ASSISTANT turn 记录 AI Trace ID） |
| 2 | 能否作为客户端幂等 key？ | **不能** —— 客户端不提供（API 请求体只有 content） |
| 3 | 能否作为服务端 request ID？ | **是**，但由 **Orchestrator 在 AI 执行时**生成（`new_request_id()`） |
| 4 | 是否每次重复 HTTP 请求重新生成？ | **是**（每次 execute 生成新的；USER turn 阶段尚不存在） |
| 5 | 是否在 USER Turn 创建前存在？ | **否**（USER turn `assistant_request_id = None`） |
| 6 | 能否识别重复 USER message？ | **不能**（晚于 USER turn、每次请求新值） |
| 7 | 是否适合作为 `(conversation_id, assistant_request_id)` 唯一键？ | **不适合** —— 语义为 correlation，且 Phase 4.1 已冻结 CORRELATION ONLY |

→ **结论：assistant_request_id 保持 CORRELATION ONLY，不作为幂等键**（与 Phase 4.1 冻结一致）。

---

## 4. Definition of "Same Message"

| Case | 定义 | 契约判定 |
| ---- | ---- | -------- |
| A | 相同 conversation_id + 相同 idempotency_key + 相同 payload | **DUPLICATE**（不得重复执行 AI；不得创建第二个 USER/ASSISTANT turn；未来不得产生第二份 Evidence） |
| B | 相同 content，不同 key | **两个独立请求**（content 相同 ≠ duplicate；"再问一次"是合法场景） |
| C | 相同 key，不同 payload | **CONFLICT**（`IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD`；不得静默执行第二条） |

---

## 5. Idempotency Key Options

| Option | 方案 | 评估 |
| ------ | ---- | ---- |
| **A** | 客户端提供 `idempotency_key`，服务端用 `(conversation_id, idempotency_key)` | **唯一可行**：键在请求前存在、客户端可重试携带、语义明确 |
| B | 使用 `assistant_request_id` | **排除**（§3：生成太晚、每请求新值、correlation-only 契约） |
| C | 使用 `turn_id` | **排除**（持久化后才有；是存储身份，不是请求身份；无法在请求进入时防重） |
| D | `hash(conversation_id + role + content + timestamp)` | **不推荐**：同内容重复提问被误判为 duplicate；时间窗口难界定；用户重复提问是合法场景 |

---

## 6. Recommended Contract（OD-14 Recommendation）

```text
Idempotency Key   = 客户端提供的 request key（Option A）
Scope             = conversation（键仅在单个 conversation 内唯一）
Owner             = 客户端请求（服务端不生成、不改写、不截断）
Persistence       = conversation_turn（USER turn 行；**当前无此列** → 见 §12）
Duplicate         = 返回既有执行结果（不重复执行 AI、不新建 turn）
Conflict          = reject（同 key 不同 payload → 明确错误，不静默执行）
Concurrency       = 由数据库 UNIQUE(conversation_id, idempotency_key) 最终保证
Failure / Retry   = 见 §9
Isolation         = 见 §8
```

---

## 7. Duplicate Behavior

* 第一次 `K1 → SUCCESS`：第二次 `K1` → **返回第一次结果**（不执行 AI、不创建第二个 USER/ASSISTANT turn）
* 未来 Evidence 集成后：重复请求**不得**产生第二份 Evidence（由幂等前置拦截，而非 Evidence 层判断）

---

## 8. Conversation Isolation

```text
C1 + K1  与  C2 + K1  →  两个不同请求（互相隔离）
```

→ 幂等键 **scope = conversation**，不使用 global key（无跨会话共享语义，避免跨会话误判）。

---

## 9. Failure / Retry Behavior

| Scenario | 契约 |
| -------- | ---- |
| S1 首次成功，重复提交 | 返回既有结果（duplicate） |
| S2 首次 **AI 失败** | **允许重试** —— 幂等键不得"锁死"失败请求；否则出现"USER turn 已保存 + 永远无法重试" |
| S3 USER turn 成功、AI 中断、无 ASSISTANT turn | **允许恢复/重试**（同 key 重新执行，不创建第二个 USER turn 或按契约复用既有 USER turn） |

→ 关键：幂等键必须与**请求结果状态**配合；失败态不视为已完成。

---

## 10. Concurrent Request Behavior

```text
A: check → none
B: check → none
A: execute
B: execute          ← 会产生 2 USER turns / 2 AI 执行 / 未来 2 Evidence
```

最终保证层：**数据库 `UNIQUE(conversation_id, idempotency_key)`**
（应用层 check-then-act 无法防并发；`SELECT FOR UPDATE` / 应用锁不推荐作为唯一手段）

→ 本 Step **不实现**；只确定"最终由 DB 约束保证"。

---

## 11. State Machine（分析，不落地）

```text
RECEIVED → PROCESSING → SUCCEEDED
                     ↘ FAILED
```

当前：**无 request state 持久化** → `state persistence = UNDEFINED`。
本 Step 不新增状态字段；是否引入由后续决策（见 §15）。

---

## 12. DB Schema Assessment

```text
conversation_turn 现有字段：turn_id · conversation_id · role · content ·
                            assistant_request_id · created_at
```

→ **无任何字段可承担幂等职责**：

* `turn_id`：持久化后才有，无法请求前置重；
* `assistant_request_id`：correlation-only（冻结），且晚于 USER turn；
* `content`：不能作为业务唯一键（同内容合法重复提问）。

```text
Current Schema Insufficient = YES
```

最小候选设计（**不实施**）：

```text
conversation_turn.idempotency_key   VARCHAR(128) NULL
UNIQUE (conversation_id, idempotency_key)   ← 并发最终保证
```

→ **OD-14 requires future DB change**（DB CHANGE 决策见 §15）。

---

## 13. Evidence Boundary

即使未来 `Message Idempotency → AI Result → Evidence` 接通，仍须保持：

* Evidence identity / provenance **不得**含 `conversation_id` / `turn_id` /
  `assistant_request_id` / `provider_request_id`（Phase 4.1 Step 27 硬约束）；
* 幂等键**不得**进入 Evidence；
* Conversation ↔ Evidence 仍经 `conversation_evidence` 独立关联。

---

## 14. Test Matrix（设计，不大量实现）

| ID | 场景 | 期望 |
| -- | ---- | ---- |
| T1 | 同 conv + 同 key + 同 payload | duplicate（无第二次 AI 执行） |
| T2 | 同 conv + 不同 key + 同 content | 两个独立请求 |
| T3 | 同 conv + 同 key + 不同 payload | conflict（明确错误） |
| T4 | 不同 conv + 同 key + 同 payload | 隔离（互不视为 duplicate） |
| T5 | 首次成功 + 重复 | 无第二次 AI 执行 / 无第二个 turn |
| T6 | 首次 AI 失败 + 同 key | 按 retry 契约：允许重试 |
| T7 | 并发同 key | 恰好一次逻辑执行（DB UNIQUE 保证） |
| T8 | assistant_request_id 变化 | 验证 `assistant_request_id ≠ idempotency key` |

复用既有基础设施：Step 15/46 的 `FakeOrchestrator`（唯一被替换层）、
`ChatApplicationService` 真实装配、DB E2E 的 `RUN_DB_TESTS=1` 与精确清理模式；
**不新建**测试框架。

---

## 15. Open Questions

| ID | 问题 | Status |
| -- | ---- | ------ |
| OD-14 | Message idempotency 契约（Key/Scope/Persistence/Duplicate/Conflict/Failure/Concurrency/Isolation） | **OPEN**（契约方案已提出，见 §6） |
| OD-22 | 是否接受为实现幂等而新增 API 字段（`ConversationMessageRequest.idempotency_key`）+ DB 列 + UNIQUE | **OPEN**（新增；Roadmap 默认 `DB CHANGE = NO` / 不为方便新增 API → 需显式决策） |

---

## 16. Decision Status

```text
OD-14 = OPEN
```

原因：契约方案（§6）有真实代码依据，但其落地**必须**引入：

1. `ConversationMessageRequest` 增加可选 `idempotency_key`（API 契约变更）；
2. `conversation_turn` 增加列 + `UNIQUE(conversation_id, idempotency_key)`（DB 变更）。

二者均超出本 Step 授权范围（Roadmap：DB CHANGE = NO / 不新增 API）→
记为 **OD-14 requires future DB change**，待授权后实施；**不强行关闭**。
