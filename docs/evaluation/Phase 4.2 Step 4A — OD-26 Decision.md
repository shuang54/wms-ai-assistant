# Phase 4.2 Step 4A — OD-26 Decision（EMPTY Completion Semantics）+ Idempotency Implementation Authorization

> 性质：**架构决策 + 实施前置审计**（分析 → 决策 → 授权请求）。
> 本 Step **未修改** 任何代码与数据：
>
> ```text
> backend diff = 0 · tests diff = 0 · DB schema change = 0 · migration = 0
> production DB writes = 0 · API change = 0 · Evidence change = 0
> ```
>
> 依据：冻结契约（Step 1 / 1A / 2 / 3B）+ 真实代码实测 + 真实测试断言。

---

## 1. Scope

```text
IN : 关闭或明确 OD-26；确认 Step 6 实施前 Idempotency 契约 / DB 变更 / API Header /
     并发语义是否完整冻结；输出授权请求与实施前置清单。
OUT: Idempotency 实现 · Backend 修改 · API 修改 · DB 修改 · Migration ·
     Evidence 修改 · Runtime Evidence · Step 6 实现。
```

---

## 2. 已冻结契约（本阶段不重新讨论）

```text
Architecture        = A（ConversationTurn 承担幂等）
Persistence         = conversation_turn.idempotency_key
Scope               = conversation
Constraint          = UNIQUE(conversation_id, idempotency_key)
API                 = Idempotency-Key HTTP Header
Fingerprint         = SHA-256(conversation_id + content)
Evidence            = 不包含 idempotency_key
```

---

## 3. 真实实现审计（Observed）

### 3.1 请求链路

| 事实 | 证据 |
| ---- | ---- |
| `POST /api/conversations/{conversation_id}/messages` 存在 | `api/conversations.py:404-429` |
| 入参 = `ConversationMessageRequest`，**只有 content** | `dto/conversation_api.py:117-141`（1 ≤ len ≤ 10000，纯空白 → 422） |
| API 目前**无任何 Header 参数**（`from fastapi import APIRouter, HTTPException, Path, status`） | `api/conversations.py:66` |
| 响应 = `ConversationMessageResponse{route: str, content: str\|None, data, metadata}` —— **`route` 必填、非 Optional** | `dto/conversation_api.py:144-166` |
| API 只做适配，唯一入口 = `ChatApplicationService.execute_message` | `api/conversations.py:443-448` |

### 3.2 Application 层

```python
# chat_application_service.py:245-307（真实代码，未修改）
conversation = get_conversation(...)           # 不存在 → 404；ARCHIVED → 409（写入前）
user_turn    = append_turn(USER, request_id=None)      # TX1 立即 COMMIT
history      = list_turns(...); 排除 user_turn.turn_id
context      = context_builder.build_context(previous_turns)
result       = await orchestrator.execute(user_turn.content, context=context)   # 事务之外
if _assistant_content(result) is None:  return result        # EMPTY → 不创建，不伪造
if _assistant_request_id(result) is None: return result      # 缺 request_id → 不创建，不伪造
append_turn(ASSISTANT, content, assistant_request_id)        # TX2
```

* 判据只有两个：`content.strip()` 非空（`_assistant_content`，L149-154）与 `metadata["request_id"]` 合法（`_assistant_request_id`，L157-162）；
* **不读 `metadata.outcome`** ⇒ `EMPTY / REFUSED / FAILED` 不参与 Turn 创建判定；
* Orchestrator 异常 → 原样上抛，USER Turn 保留（L66-67 docstring + L279 无 try）。

### 3.3 持久化层

| 事实 | 证据 |
| ---- | ---- |
| `conversation_turn` = 6 列：`turn_id / conversation_id / role / content / assistant_request_id / created_at` | `db/models/conversation_turn.py:68-125` |
| **无** idempotency 列、**无** status 列、**无** UNIQUE | 同上 |
| 索引仅 1 个：`ix_conversation_turn_conversation_id_created_at`（**非唯一**） | `db/models/conversation_turn.py:127-130` |
| Read Model `ConversationTurnRow` / `ConversationTurnView` = 6 字段 | `db/conversation_repository.py:126-134`、`services/conversation_service.py:89-97` |
| `build_turn_insert` **无 `ON CONFLICT`** → 唯一冲突将抛 `IntegrityError` | `db/conversation_repository.py:319-337` |
| `append_turn` 事务 = SELECT status → INSERT turn → UPDATE updated_at（一个 `session.begin()`） | `db/conversation_repository.py:384-424` |

### 3.4 Outcome 持久化（关键事实）

`ai_ops.assistant_outcome_record`（`db/models/assistant_outcome_record.py`）按 `assistant_request_id` UNIQUE
持久化 `SUCCESS / EMPTY / REFUSED / FAILED`。

但：

```text
EMPTY 路径 → 不创建 ASSISTANT Turn → conversation_turn 中没有任何一行持有该 request_id
```

⇒ **"AI 已完成（EMPTY）"这一事实在 Conversation 域不可观测、不可 join**（Step 27 契约禁止用
request_id 反查来构造 Conversation 状态；且 USER 行的 `assistant_request_id` 恒为 None）。

### 3.5 Idempotency 现状

```text
backend 全量检索 "idempotency" → 仅命中
    db/models/evidence_record.py（Evidence 组合幂等键）
    db/init_db.py
Conversation / Turn / Service / API 侧命中数 = 0
```

⇒ 消息幂等 = **MISSING**（与 Step 1 §1 结论一致）。

### 3.6 将受 Step 6 变更影响的既有断言（本阶段 0 修改）

| 测试 | 断言 | 影响 |
| ---- | ---- | ---- |
| `tests/test_conversation_persistence_db.py:169-190` | `conversation_turn` 列集合 **== 6 列**（离线用例，默认套件执行） | 新增列 → **必红** |
| `tests/test_conversation_persistence_db.py:206-215` | `len(table.indexes) == 1` | 新增 UNIQUE → **必红** |

⇒ Step 6 必须在同一次变更中同步更新这两处（列为 Step 6 实施前置，见 §14）。

---

## 4. OD-26 分析：EMPTY 的完成语义

### 4.1 关键区分

```text
AI execution completed   ≟   Assistant Turn persisted
```

由 Step 2 §4（Option A，已冻结）：

> ASSISTANT Turn = **用户最终看到的 assistant message**，不是 AI 执行记录。

反证：

* EMPTY：AI **执行成功**，但**无** ASSISTANT Turn；
* REFUSED + content：AI 执行"业务拒绝"，但**有** ASSISTANT Turn。

⇒ 二者**不等价**。"ASSISTANT Turn 存在"只是"存在可重放结果"的**代理判据（proxy）**，
不是"AI 已完成"的判据。**不得**因为当前数据库没有 execution status 就把代理判据当作事实。

### 4.2 正确的完成判据

```text
Idempotency "completed"  ⟺  存在可重放结果（replayable result）
```

| 结果 | 可重放内容 | 是否 completed |
| ---- | ---------- | -------------- |
| SUCCESS + content | ASSISTANT Turn.content | ✅ |
| REFUSED + content | ASSISTANT Turn.content | ✅ |
| EMPTY | **无**（Turn 未创建；route / data / metadata 未落库） | ❌ |
| REFUSED + empty | **无** | ❌ |
| FAILED（异常） | **无** | ❌ |

### 4.3 Option A（EMPTY = completed）评估

要在 Architecture A 下把 EMPTY 判为 completed，必须有"完成标记"。逐条排除：

| 手段 | 判定 |
| ---- | ---- |
| 为空内容创建 ASSISTANT Turn | ❌ 违反 Step 2 §6「**不伪造** `[EMPTY]` 之类内容」 |
| 新增 Turn status / request state 列 | ❌ 违反本阶段「不要偷偷增加状态字段」+ Roadmap §15 默认 DB CHANGE = NO 原则 |
| 借 `assistant_outcome_record` 判定 | ❌ §3.4：Conversation 侧无 request_id 关联，**不可 join** |
| 升级 Architecture B（独立 MessageRequest + status + 快照） | ⚠ 可解决，但属**架构升级**，超出本阶段授权（OD-23 仍 OPEN） |

且即便标记为 completed，**重放内容不存在**：`ConversationMessageResponse.route` 为**必填 `str`**
（§3.1），EMPTY 重放无 route / data / metadata 可给 → 只能伪造 route → 明确禁止。

```text
Option A 在 Architecture A 下 = 不可实现（除非升级 Architecture B）
```

### 4.4 Option B（EMPTY = not completed）评估

* **零新增状态**：判据 = "无 ASSISTANT Turn" → 允许重新执行；
* 与 **Step 1A §7 状态模型完全一致**（`USER 存在 + ASSISTANT 缺失 = NOT_COMPLETED → 允许重试`）
  ⇒ **不需要修订 Step 1A**；
* 重试**不产生第二条 USER Turn**（复用既有 key 保留行）⇒ 会话历史不出现重复用户消息；
* 代价：同 key 重试会**二次执行 AI**（重复成本；若 AI 有副作用则重复副作用）。
* 前提校验：当前 AI 能力 = RAG / 只读 Text-to-SQL / Tool（Step 1A §11 记录"未来 Tool 有副作用时必须重新评估"）。

```text
Option B = 可实现、不伪造、不加状态字段、与 Step 1A 一致
```

### 4.5 Decision

```text
OD-26 = CLOSED（采用 Option B）
```

**依据（业务语义，非实现方便性）**：幂等保护的对象是"可重放结果"，EMPTY 不产生任何可重放结果；
在 Architecture A + Step 2 冻结（不伪造内容 / 不新增状态）下，EMPTY 无法被表示为 completed。
选择 Option B 是 **语义正确性 + 可实现性**的共同结论，不是为省事而回避。

**附带条件（必须记录）**：

```text
本结论成立的前提 = AI 执行无副作用（RAG / 只读 SQL / 无写 Tool）。
一旦引入有副作用的 Tool / 写操作，EMPTY 重试将产生重复副作用 → 必须重新评估
（与 OD-25、Step 1A §11 同一触发条件）。
```

---

## 5. 冻结规范（OD-26 产出）

```text
EMPTY semantics:
    EMPTY = AI 正常执行完成，但无可展示内容（content 为 None / 空 / 纯空白）。
    - 不创建 ASSISTANT Turn（不伪造内容 / 不伪造 request_id）
    - HTTP 200（outcome ≠ HTTP 状态；API 行为不变）
    - Idempotency 语义上 = NOT COMPLETED

Idempotency behavior:
    completed ⟺ 该 (conversation_id, idempotency_key) 的 USER Turn 存在
                且 对应的 ASSISTANT Turn 存在（= 存在可重放结果）
    completed + 同 key 同 payload → duplicate（content 级重放，不重新执行 AI）
    completed + 同 key 异 payload → IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD

Retry behavior:
    Retry 允许 ⟺ USER Turn 存在 且 ASSISTANT Turn 缺失 ⟺ 无 replayable result
    覆盖：EMPTY · REFUSED+empty · FAILED（异常）· Crash A · Crash B · TX2 失败
    Retry 不创建第二条 USER Turn（复用既有 key 保留行）

Crash behavior:
    Crash A（USER commit 后崩溃，AI 未执行）  → NOT completed → 允许重试
    Crash B（AI 已执行，ASSISTANT 未提交）    → 不可区分 → 按 NOT completed 处理（允许重试）
                                               = 已知 limitation（见 §7）
    Crash C（ASSISTANT 已提交，响应丢失）      → completed → duplicate 重放（content 级）
```

---

## 6. 场景矩阵（Retry 判据统一化）

| Scenario | USER Turn | AI | Assistant Turn | Retry | 判据 |
| -------- | :-------: | --: | :------------: | :---: | ---- |
| SUCCESS + content | ✅ | ✅ | ✅ | **❌** | completed → duplicate 重放 |
| SUCCESS + empty | ✅ | ✅ | ❌ | **✅** | 无可重放结果（OD-26 Option B） |
| REFUSED + content | ✅ | ✅ | ✅ | **❌** | completed → duplicate 重放 |
| REFUSED + empty | ✅ | ✅ | ❌ | **✅** | 无可重放结果（同 EMPTY 路径） |
| FAILED（exception） | ✅ | ❌/exception | ❌ | **✅** | Step 1 §9 S2：失败不得被幂等键锁死 |
| USER persisted + process crash | ✅ | unknown | ❌ | **✅** | Crash A/B；Crash B 见 §7 |
| AI completed + TX2 failed | ✅ | ✅ | ❌ | **✅** | 与 EMPTY / Crash B **状态不可区分**，统一按可重试 |

**Retry 的统一判据（一行规则）**：

```text
Retry ⟺ 该 key 的 USER Turn 存在 ∧ ASSISTANT Turn 缺失 ⟺ 无 replayable result
```

该规则无需任何新增状态字段，且对上表 7 行全部自洽。

---

## 7. Crash B 专项分析

已知事实（Step 1A §11 已确认存在）：

```text
AI executed → process crash → ASSISTANT Turn 未提交
USER Turn = exists · ASSISTANT Turn = absent · AI may already have executed
```

| 问题 | 结论 |
| ---- | ---- |
| 1. OD-26 能否解决？ | **不能**。OD-26 解决的是"EMPTY 是否算完成"；Crash B 是"AI 结果未持久化导致的**结果丢失**"，是**持久性/原子性**问题，不是终态语义问题。二者状态在 DB 中完全相同（USER ✅ / ASSISTANT ❌）。 |
| 2. 属于哪个层次？ | **事务边界层（TX1 → AI → TX2 之间的结果不可持久）**，属 Architecture A 的结构性限制；不在 Conversation 语义层，也不在 EMPTY 分类层。 |
| 3. 是否需要 Step 6 处理？ | Step 6 **必须记录并采用确定性默认行为**（= 允许重试，与 §6 统一规则一致），但**无法彻底解决**：彻底解决要么升级 Architecture B（显式 status + 结果快照），要么由 AI 层提供副作用幂等。**不得**通过伪造 execution status 解决（本阶段明令禁止）。 |
| 4. 是否属于已知 limitation？ | **是**。记为 **KL-1（Crash B ambiguous outcome）**：重试可能导致 AI 二次执行。当前 RAG / 只读 SQL 场景下影响有限；**触发重新评估的条件 = 引入有副作用 Tool / 写操作**（与 §4.5 附带条件同源）。 |

```text
OD-25 = CLOSED（作为 ACCEPTED KNOWN LIMITATION 接受；重新评估触发器已记录）
```

---

## 8. Step 6 事务模型确认（不修改）

```text
TX1  BEGIN  create USER Turn（含 idempotency_key 保留）  COMMIT
     AI execution（事务之外）
TX2  BEGIN  create ASSISTANT Turn（条件式）              COMMIT
```

* 模型**仍然成立**，本阶段与 Step 6 均**不修改**（Step 1A §14 已记录为 Step 6 decision dependency，本次确认 = 维持两阶段）；
* 幂等保留（reservation）发生在 **TX1**，随 USER Turn 一次提交 —— 这是"先占后执行"的唯一落点；
* Step 6 不处理 Evidence（Step 4/5 已 DEFERRED）。

**新增实施约束（C-1，Step 6 implementation concern）**：
retry 路径**不写 USER Turn**，而 `list_turns` 会读到首次保留的 USER Turn ⇒ 必须按保留行 `turn_id`
精确排除，否则"当前问题"会同时进入 `context` 与 `question`，违反 Step 13 冻结的 Context 语义。

---

## 9. DB 变更确认（授权前；未执行）

```text
conversation_turn.idempotency_key   VARCHAR(128) NULL
UNIQUE (conversation_id, idempotency_key)
```

| 项 | 结论 |
| ---- | ---- |
| 列宽 | 128（与 `ASSISTANT_REQUEST_ID_MAX_LENGTH` / Step 1 §12 提案一致） |
| 可空 | **NULL**（历史行兼容；ASSISTANT Turn 行恒 NULL） |
| 历史行 | `idempotency_key = NULL`，**不 backfill** |
| PostgreSQL NULL 语义 | 标准 UNIQUE 下 **NULL 互不相同** ⇒ 历史 NULL 行与未来 NULL 行**不受约束**、可无限共存 ⇒ **符合当前设计**。⚠ **禁止**使用 PG15+ `UNIQUE ... NULLS NOT DISTINCT`（会锁死全部历史行）。 |
| 约束作用域 | 键**只写在 USER 行**；ASSISTANT 行必须保持 NULL，否则同一 `(conversation_id, key)` 出现两行 → 唯一冲突 |
| 约束形式 | 默认 `UNIQUE(conversation_id, idempotency_key)`；如需与项目既有先例一致，可采用等价 partial 形式 `CREATE UNIQUE INDEX ... WHERE idempotency_key IS NOT NULL`（先例：`uq_llm_usage_record_request_id` partial index）。两种形式在非 NULL 域语义等价，由 Step 6 明确记录所选形式。 |
| 迁移机制 | ⚠ `Base.metadata.create_all(checkfirst=True)` 对**已存在**的表整体跳过 ⇒ 既有库**必须**显式幂等 DDL（先例：`ensure_assistant_request_id_column`，`db/init_db.py:118-150`：`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` + `CREATE UNIQUE INDEX IF NOT EXISTS`）；全新库由 ORM 建表。 |
| 本次是否执行 | **否**（migration = 0；`后端 diff = 0`） |

---

## 10. API 契约确认

```http
POST /api/conversations/{conversation_id}/messages
Idempotency-Key: <client-key>          ← Optional Header（非必填）
Content-Type: application/json

{ "content": "..." }                    ← Body 不变
```

| 项 | 结论 |
| ---- | ---- |
| 传输方式 | **HTTP Header**（Step 1A §6 已冻结：request metadata ≠ message content） |
| 是否必填 | **Optional**。未提供 → 该请求**不参与幂等**（每次独立执行，行为与现状一致） |
| Body / DTO | **不新增** `idempotency_key` 字段；`ConversationMessageRequest` 保持仅 `content` |
| 是否需要 `MessageRequest` 独立表 | **不需要**（Architecture A；A2 = DEFERRED） |
| Step 6 代码改动 | `api/conversations.py` 需新增 `Header` 导入与参数（`Annotated[str \| None, Header(alias="Idempotency-Key")]`，当前未 import `Header`） |
| Header 取值处理 | 空白 → 视为未提供；超长（> 128）→ 422；**不截断 / 不改写 / 不生成**（Step 1 §6：服务端不生成 key） |
| GET /messages | **不暴露** `idempotency_key`（DTO 冻结，避免请求身份外泄） |

**未决（OD-34，见 §13）**：duplicate 重放只能给出 `content + assistant_request_id`，
而 `ConversationMessageResponse.route` 为**必填 `str`** ⇒ 重放响应形态必须在 Step 6 明确
（不得伪造 route）。

---

## 11. 冲突语义（冻结）

| 场景 | 语义 |
| ---- | ---- |
| 同 conversation + 同 key + 同 content（已 completed） | **duplicate**（不执行 AI、不新建 Turn；content 级重放） |
| 同 conversation + 同 key + 同 content（未 completed） | **retry**（复用 USER Turn，重新执行 AI） |
| 同 conversation + 同 key + **不同 content** | **IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD**（明确错误；不静默执行第二条） |
| 不同 conversation + 同 key | **independent**（scope = conversation） |
| 同 content + 不同 key | **independent**（"再问一次"是合法场景） |
| 未提供 key | **independent**（不参与幂等） |
| in-flight 重复请求 | 目标 **409 Conflict**（沿用 Step 1A §10 Policy 2） |

### 11.1 payload 比对不需落库（审计结论）

`Fingerprint = SHA-256(conversation_id + content)` **无需新增列**：
保留行（USER Turn）已持久化 `content` ⇒ 请求进入时取保留行 content 现算指纹比对即可
（hash 比对，避免大文本比较）。这与冻结的 Fingerprint 定义一致，且**不增加 DB 字段**。

### 11.2 in-flight 可识别性（必须记录）

```text
当前 DB 模型（USER Turn + idempotency_key，无 status）下：
    "USER Turn 存在 ∧ ASSISTANT Turn 缺失"
无法区分：
    (a) in-flight（第一个请求正在执行 AI）
    (b) EMPTY / REFUSED+empty 已完成
    (c) FAILED（异常）已完成
    (d) Crash A / Crash B / TX2 失败
```

⇒ **当前模型无法可靠识别 in-flight**（除非新增状态字段，本阶段明令禁止）。

记录为 **OD-33 = Step 6 implementation concern**，候选取向（由 Step 6 决策并明确记录）：

| 取向 | 后果 |
| ---- | ---- |
| **默认 retry**（推荐） | 与 Step 1 §9 S2/S3（失败不得被锁死）、§6 统一判据一致；代价：真正并发时可能二次执行 AI（等价于现状风险），但**不会**产生第二条 USER Turn |
| 默认 409 | 与 Step 1A §10 一致；代价：Crash A/B/FAILED 后 key **永久锁死** → 直接违反 Step 1 §9 |
| 进程内 in-flight 表 | 单进程可靠；多 worker / 进程重启后失效 |

推荐 **默认 retry**，理由：正确性（失败可恢复）优先于"避免重复 AI 成本"；
并发重复执行的兜底仍由 DB UNIQUE 保证（**不会产生第二条 USER Turn**）。

---

## 12. 并发语义（确认）

```text
最终保证层 = DB UNIQUE(conversation_id, idempotency_key)
```

* 并发同 key：后到者 INSERT 触发唯一冲突（READ COMMITTED 下阻塞后报错）；
* ⚠ 当前 `build_turn_insert` **无 `ON CONFLICT`**，且 API 只映射
  `ConversationRepositoryError → 500` ⇒ Step 6 **必须**把唯一冲突显式映射为
  domain error → **HTTP 409**，否则 duplicate/conflict 会落成 500（契约违约）；
* 已 completed 的并发请求 → 走 duplicate 重放路径（Step 1A §10 Policy 3）；
* 应用层 check-then-act **不作为**唯一手段（Step 1 §10）。

---

## 13. Evidence 边界（确认不参与）

```text
Idempotency-Key · conversation_id · turn_id · assistant_request_id
        ↓ 全部禁止进入
Evidence identity · Evidence provenance · Evidence dataset_version
```

* Evidence 当前仍 = **Source / Provenance Unit**（Dataset · Provenance · Lifecycle · Annotation · Review ·
  Conversation Reference），**不是** AI Answer / Runtime Result；
* **Runtime Evidence = DEFERRED**（依赖 OD-31 / OD-32）；
* `OD-31 / OD-32 = OPEN`，**不得关闭**；
* Step 6 不创建 Evidence、不创建 ConversationEvidence、不修改 Evidence 契约。

---

## 14. Open Decisions

| ID | 内容 | 状态 |
| -- | ---- | ---- |
| OD-14 | Message idempotency 契约 | **CLOSED**（Step 1A §16 决策已定；实施待 OD-22 授权） |
| OD-22 | 是否授权 DB 变更（1 列 + 1 UNIQUE）实施 | **OPEN —— 待你批准**（本阶段唯一硬门） |
| OD-23 | 是否需要完整 HTTP response replay（A → B 升级） | **OPEN**（Step 6 可用 content 级重放完成；不阻塞） |
| OD-24 | `EMPTY / REFUSED / FAILED` 是否持久化到 Turn | **CLOSED**（由 Step 2 §7/§10/§17 已定事实推导：不新增 Turn status；outcome 仍只存在于 `ai_ops.assistant_outcome_record`） |
| OD-25 | Crash B（ambiguous outcome）可接受度 | **CLOSED（作为 KL-1 已知 limitation 接受）**；重新评估触发器 = 引入有副作用 Tool |
| OD-26 | EMPTY 是否视为 idempotency completed | **CLOSED（Option B = NOT completed）**（本 Step 产出） |
| OD-31 | Runtime Evidence Artifact Architecture | **OPEN**（不关闭） |
| OD-32 | Runtime Dataset Version Contract | **OPEN**（不关闭） |
| OD-33 | **NEW** — in-flight vs NOT_COMPLETED 可区分性 | **OPEN** → Step 6 implementation concern（推荐默认 retry） |
| OD-34 | **NEW** — duplicate 重放响应形态（`route` 必填 vs 可重放字段缺失） | **OPEN** → Step 6 必须与 API 微调一起决策（禁止伪造 route） |

---

## 15. Step 6 Readiness

### 15.1 契约完备性

| 检查项 | 状态 |
| ------ | ---- |
| OD-26 | ✅ CLOSED |
| OD-22（架构决策部分） | ✅ 已定（Step 1A §16） |
| Idempotency 契约（key / scope / duplicate / conflict / retry / isolation） | ✅ FROZEN |
| DB 变更设计（列 / 约束 / NULL 语义 / 迁移机制） | ✅ FROZEN（未执行） |
| API Header 契约 | ✅ FROZEN（未实现） |
| 事务语义（TX1 → AI → TX2） | ✅ FROZEN（不修改） |
| 并发语义（DB UNIQUE 兜底 + 409 映射要求） | ✅ FROZEN（映射为 Step 6 必做项） |
| Evidence 边界 | ✅ FROZEN（不参与） |

### 15.2 授权门（governance gate）

```text
G-1  OD-22：DB 变更授权（conversation_turn.idempotency_key + UNIQUE）      ← 必需
G-2  API 微调授权：新增可选 Header + duplicate 响应形态（OD-34）            ← 必需
```

### 15.3 Step 6 实施前置清单（必须在同一次变更内完成）

```text
C-1  retry 路径按保留行 turn_id 精确排除 USER Turn（防止当前问题进 context）
C-2  唯一冲突 → domain error → HTTP 409 显式映射（当前会落成 500）
C-3  ASSISTANT Turn 的 idempotency_key 恒为 NULL（否则唯一冲突）
C-4  既有库迁移：init_db 增加幂等 DDL（ADD COLUMN IF NOT EXISTS
     + CREATE UNIQUE INDEX IF NOT EXISTS），沿用 ensure_assistant_request_id_column 先例
C-5  Read Model：ConversationTurnRow 增字段；ConversationTurnView / API DTO 保持不变
C-6  HTTP 头取值：空白 = 未提供；> 128 → 422；服务端不生成 / 不改写 key
T-1  更新 tests/test_conversation_persistence_db.py:169-190（列集合断言）
T-2  更新 tests/test_conversation_persistence_db.py:206-215（索引数量断言）
```

### 15.4 判定

```text
Step 6 = READY（条件：G-1 / G-2 授权批准）
         契约侧 blocker = 0；OD-33 / OD-34 为 Step 6 内决策项（不阻塞开工）

若 G-1（DB 变更）未获批准 → Step 6 = BLOCKED（唯一硬 blocker = OD-22）
         ⇒ 无持久化幂等键 ⇒ 无法在并发下保证"恰好一次"，只能退回无幂等现状
```

---

## 16. 本阶段产物与验证

```text
新增文件（唯一）：
    docs/evaluation/Phase 4.2 Step 4A — OD-26 Decision.md
追加修改（append-only）：
    docs/decisions/Phase/Phase 4.2 Roadmap v1.0.md  §22

验证：
    git diff -- backend tests  = 0
    DB schema change           = 0
    migration                  = 0
    production DB writes       = 0
```
