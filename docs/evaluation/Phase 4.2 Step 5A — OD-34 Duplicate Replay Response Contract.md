# Phase 4.2 Step 5A — OD-34 Duplicate Replay Response Contract

> 性质：**契约分析 + 架构决策**（阅读 → 分析 → 方案比较 → 决策 → 文档冻结）。
> 本 Step **未修改** 任何代码：
>
> ```text
> backend = 0 · tests = 0 · DB = 0 · API = 0 · migration = 0
> production DB writes = 0 · Evidence = 0 · ConversationEvidence = 0
> ```
>
> 唯一问题：当 `conversation_id + Idempotency-Key` 已完成（USER Turn + ASSISTANT Turn 均在），
> 第二次请求返回什么？

---

## 1. Scope

```text
IN : 重新确认 Response / Turn 持久化真实边界；比较 Option A / B / C（+ D）；
     冻结 duplicate response 语义与 HTTP status；关闭或明确 OD-34；判定 G-2 readiness。
OUT: Backend / API / DB / ORM / ChatApplicationService / DTO 修改；Idempotency 实现；
     Header 实现；并发控制；Step 6 实现；Evidence 修改。
```

---

## 2. 真实 Response Contract（以代码为准，不假设）

`backend/app/dto/conversation_api.py:144-166`：

```python
class ConversationMessageResponse(BaseModel):
    route:    str                  = Field(...)               # 必填、非 Optional
    content:  str | None           = Field(default=None)      # Optional
    data:     dict[str, Any] | None = Field(default=None)     # Optional
    metadata: dict[str, Any]       = Field(default_factory=dict)  # Optional（默认 {}）
```

实测结论（与 prompt 中列出的候选字段逐一核对）：

| 字段 | 是否存在 | 是否必填 |
| ---- | :------: | :------: |
| `conversation_id` | ❌ **不存在** | — |
| `turn_id` | ❌ **不存在** | — |
| `role` | ❌ **不存在** | — |
| `content` | ✅ | Optional（默认 `None`） |
| `route` | ✅ | **必填 `str`** |
| `data` | ✅ | Optional（默认 `None`） |
| `metadata` | ✅ | Optional（默认 `{}`） |

补充事实：

* `test_conversation_message_api_contract.py:134-143`：`model_fields` **精确等于**
  `["route","content","data","metadata"]`，且 `== set(ChatResponse.model_fields)`（与 `/api/ai/chat` 同 envelope）；
* `test_conversation_message_api_contract.py:145-147`：响应**禁止**出现
  `conversation_id / turn_id / assistant_request_id` 字段；
* 会话消息自身的 DTO 是**另一个**：`ConversationTurnResponse{turn_id, role, content,
  assistant_request_id, created_at}`（`dto/conversation_api.py:90-104`，用于 `GET .../messages`）。

⇒ **关键判据**：POST 的响应 DTO 是 **AI Runtime Result envelope**（与 `/api/ai/chat` 同构），
**不是** Conversation Message DTO；但持久化侧只保存了 message。二者不一致是本 OD 的根源。

---

## 3. 真实 ASSISTANT Turn 持久化边界

`chat_application_service.py:301-306` + `db/models/conversation_turn.py:68-125`：

```text
ASSISTANT Turn 持久化 = role · content · assistant_request_id · created_at
```

| 字段 | 是否落库 |
| ---- | :------: |
| `route` | ❌ |
| `data` | ❌ |
| `metadata` | ❌ |
| `outcome` | ❌（`ai_ops.assistant_outcome_record` 按 request_id 独立持久化，Turn 内无） |

⇒ **不为 duplicate response 增加这些字段**（§8 明确评估）。

---

## 4. `route` 为什么现在是 required str（真实原因核查）

| 检查项 | 结果 |
| ------ | ---- |
| 生成点 | `api/conversations.py:133-146` `_to_message_response()`：`route=str(getattr(result.route,"value",result.route))` —— 直接来自 `AIOrchestrationResult.route`，**恒有值** |
| 取值域 | `RouteType = {rag, tool, text_to_sql}`（`services/ai_router_service.py:68-73`） |
| tests | `test_success_200` 断言 `payload["route"] == "text_to_sql"`；`test_response_envelope_matches_ai_chat` 只断言**字段名集合**，**未断言 required**（全仓 tests 无 `is_required` / openapi `required` 断言命中该 DTO） |
| frontend | `frontend/` = **空目录（0 文件）** ⇒ **当前仓库未发现可验证的前端消费方**（不猜测） |
| docs | 无独立 API 消费方文档依赖 `route is non-null`（仅 `docs/api.md` 类别描述，非代码断言） |

**EMPTY 的事实**：EMPTY 走的是**正常执行路径** → `route` 有真实值（如 `rag`），只是 `content=None`、
不建 Turn（Step 2 §12：`EMPTY → HTTP 200`）。⇒ EMPTY **不会**产生"route 缺失"的响应。

⇒ 因此 `route: str` 之所以成立，是因为**"每一次响应都对应一次真实 AI 执行"**。
duplicate 是**唯一**"响应不对应本次 AI 执行"的场景 ⇒ 它是 `route: str` 的第一个真实反例，
**必须**通过契约微调表达，而不是伪造值。

**duplicate 是否真的需要完整 AI route？**
不需要 —— duplicate 的语义是"把上次那条回答原样给你"（消息重放），route 是**执行轨迹**属性，
不是**消息**属性。但 response DTO 目前把二者混在同一个 envelope 里 ⇒ 需要显式表达"本次无 route"。

---

## 5. Message Replay vs Execution Result Replay（核心区分）

Phase 4.2 已冻结：

```text
ConversationTurn        = user-visible conversation message
AIOrchestrationResult   = runtime execution result（route / content / data / metadata）
```

| 维度 | Message Replay | Execution Result Replay |
| ---- | -------------- | ----------------------- |
| 重放对象 | 已持久化的 assistant message（content + request_id） | 完整 `AIOrchestrationResult` |
| 数据来源 | `conversation_turn` | 需要新快照（Architecture B） |
| `route / data / metadata` | 不可用 → 显式 null | 完整 |
| DB 变更 | 0 | 需要（或持久化 AI Result） |
| 架构影响 | 0（Turn 仍是 message） | 大（见 §8） |

⇒ **本阶段结论：duplicate = Message Replay，不是 Execution Result Replay。**
理由：ASSISTANT Turn 承载的就是 message；未被持久化的执行属性**不得**被推断或伪造。

---

## 6. Option A — Duplicate 复用同一 DTO（Message Replay）

```text
POST（同 key + 同 payload + completed）
    ↓ 命中已持久化 ASSISTANT Turn
    ↓ 返回 ConversationMessageResponse
        content  = persisted assistant content
        route    = null（需微调为 str | None）
        data     = null
        metadata = { request_id, idempotent_replay: true }
```

* **DB 变更 = 0**；不新增 DTO；字段名集合不变（`test_response_envelope_matches_ai_chat` 不受影响）；
* **API 变更 = 极小**：`route: str` → `route: str | None = None`（仅 required-ness 变化）；
* 代价：所有响应的 `route` 在 OpenAPI 中变为可空（contract 弱化），且 `data` 不重放（§11）。

---

## 7. Option B — Duplicate 返回独立 Response Shape（单独 DTO）

```text
第一次  → ConversationMessageResponse
重放    → ConversationMessageReplayResponse{ content, assistant_request_id, replayed: true, ... }
```

| 维度 | 评估 |
| ---- | ---- |
| 响应完整性 | 显式、可自描述（可带 `replayed` / `replayed_from_turn_id`） |
| DB 变更 | 0 |
| API 变更 | **中**：新增 DTO + `response_model` union ⇒ OpenAPI 200 出现 `anyOf` **两个 schema** |
| 客户端影响 | 前端/调用方必须**判别两套 200 响应**（否则解析失败）；`"route"` 在重放响应中不存在 |
| 现有契约 | 不改变既有 200 响应形状（向后兼容）；但端点级 schema 复杂化 |
| 是否破坏现有 API | 不破坏首次响应；**重放响应是新形状** |
| 是否值得 | 对 MVP 属过度设计：重放只需表达"同一条消息 + 无本次执行"，一个 null 已足够 |

⇒ **不推荐**（复杂度 > 收益）；若未来需要"重放来源 turn / 重放时间 / 完整审计字段"再升级。

---

## 8. Option C — 持久化 AI Result（route / data / metadata / assistant_result）

```text
conversation_turn += route? data? metadata? 或 assistant_result JSON?
```

| 维度 | 评估 |
| ---- | ---- |
| 响应完整性 | 完整（可重建 `AIOrchestrationResult`） |
| DB 变更 | **超出 OD-22 授权范围**（OD-22 仅授权 1 列 idempotency_key + 1 UNIQUE） |
| 架构影响 | **边界变化（BREAK）** —— `ConversationTurn` 从 **user-visible message** 变成 **AI Execution Result Store** |

架构边界变化的直接证据（既有冻结契约会被违反）：

* Step 2 §8 / `chat_application_service.py:58-62`：「`ConversationTurn.content` 只保存 USER 输入 /
  Assistant 最终可展示回答；**不得**保存 metadata / SQL / prompt / RAG chunks / Tool raw result /
  LLM raw response（**Conversation history ≠ observability store**）」；
* Step 2 §5：「`data / route / metadata` **不进** ConversationTurn」；OD-16 / OD-19 会需重启；
* route / data / metadata 已由 observability 侧按 `request_id` 承载（usage / rag / tool / outcome），
  再写一份 = **双写 + 职责重叠**。

⇒ **不推荐**。不得为 duplicate response 的方便而选择；完整重放应走 **OD-23（Architecture B）**。

---

## 9. Option D（附加评估，明确拒绝）— 从 observability 推断 route

设想：用持久化的 `assistant_request_id` 反查 `rag_execution_record / tool_execution_record` 推断 route。

**拒绝理由（真实代码）**：

1. `db/models/` 中**没有** SQL 执行记录表（仅 `rag_execution_record` / `tool_execution_record` /
   `llm_usage_record` / `assistant_outcome_record`）⇒ `text_to_sql` 路由**无法**被推断；
2. Phase 3.12 冻结：「**绝不**根据 LLM usage / Tool / RAG / HTTP status 猜测」（该原则为 outcome 而立，
   对 route 同样适用）；
3. 会让 Conversation Runtime 依赖 observability store（当前 `ChatApplicationService` **零**观测依赖）
   ⇒ 依赖方向破坏。

⇒ **禁止推断 route**。可用则真值，不可用则 `null`。

---

## 10. 决策表（架构方案比较）

| 方案 | 响应完整性 | DB 变更 | API 变更 | 架构影响 | 推荐/不推荐 |
| ---- | ---------- | :-----: | -------- | -------- | ----------- |
| **A Message Replay**（同 DTO + `route: str \| None` + metadata 标记） | 部分（content ✅ · request_id ✅ · route/data ❌→null） | **0** | **极小**（仅 `route` required-ness） | **0**（Turn 仍为 message） | ✅ **推荐** |
| B Separate DTO（独立 Replay Response） | 完整自描述（仍无 route/data） | 0 | 中（新 DTO + union → OpenAPI `anyOf`） | 0（但 API surface 增大、客户端需判别两套 200） | ⚠ 不推荐（MVP 过度设计） |
| C Persist AI Result（route/data/metadata 入 Turn） | 完整 | **超出 OD-22 授权** | 0 | **BREAK**：Turn: message → AI Execution Result Store；违反 Step 2 §5/§8、重启 OD-16/19、双写观测数据 | ❌ **不推荐（禁止为方便而选）** |
| D 从 observability 推断 route | 不完整且不可靠 | 0 | 0 | 破坏依赖方向 + 违反"不猜测"原则；`text_to_sql` 不可推断 | ❌ **拒绝** |

---

## 11. `data` 的处理（含 Text-to-SQL）

第一次（示例）：`content="查询结果如下" · data=[...] · route="text_to_sql"`；
duplicate 只有 `content` ⇒ **数据结果不重放**。

真实代码核查（`api/conversations.py:120-130` `_safe_message_data`）：

```python
if not isinstance(data, Mapping): return None
if not all(isinstance(v, _JSON_PRIMITIVES) for v in data.values()): return None
```

而 `AIOrchestrationResult.data` 实际取值（`ai_orchestrator_service.py:771/882/1024/993`）= 
`RagResponse` / `ToolResult` / `SQLExecutionResult` / `None` —— **均非 Mapping 的 JSON 基本类型字典**
（`rag_service.py:156` / `tools/base.py:124` / `sql_executor_service.py:161` 均为普通类）。

⇒ **`data` 在当前真实响应中恒为 `null`**（`test_success_200` 亦断言 `payload["data"] is None`）。

结论：

```text
duplicate 的 data = null，不是回归（首次响应本就恒为 null）。
但必须明确记录为 ACCEPTED LOSS：Text-to-SQL 的行数据在 Architecture A 下不可重放。
若产品要求 data / route / metadata 完整重放 → 走 OD-23（Architecture B），
**不得**用 Option C（把 AI Result 塞进 ConversationTurn）。
```

---

## 12. HTTP Status（从幂等语义判断）

| 选项 | 判断 |
| ---- | ---- |
| **200 OK** | ✅ **采纳**。duplicate 不是错误：该请求在语义上**已成功完成**，客户端拿到的是第一次的真实结果。幂等的定义即"重复提交得到同一结果"，而非"重复提交被拒绝"。 |
| 409 Conflict | ❌ 语义错：409 表示"请求与当前资源状态冲突、无法完成"；而 duplicate 请求**确实完成了**（只是重放）。409 保留给：in-flight（Step 1A §10）、同 key 异 payload（`IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD`）、ARCHIVED 会话。 |
| 其他（202 / 3xx） | ❌ 无对应语义（无异步任务、无重定向目标）。 |

⇒ **duplicate = 200 OK**（与首次响应同 status；Step 1A 亦要求"返回第一次结果"）。

---

## 13. Replay Marker

| 方案 | 评估 |
| ---- | ---- |
| `metadata.idempotent_replay = true` | ✅ **采纳** |
| `metadata.replayed = true` | 同义；选定 `idempotent_replay`（语义更明确、不与业务字段混淆） |
| 完全不暴露 | ❌ 不可观测：日志/前端无法区分"首次执行"与"重放"，排障成本高 |

设计约束：

* 只作为 **metadata key** 存在，**不新增 DTO 字段**（`model_fields` 集合不变 → 既有契约测试不受影响）；
* metadata 内容 = `{ "request_id": <persisted assistant_request_id>, "idempotent_replay": true }`；
* **不伪造** `outcome`（未落库）、**不写入** `turn_id`（响应禁止会话标识）；
* 安全：无敏感信息；不泄露 prompt / SQL / 凭据。

---

## 14. OD-34 Decision

```text
OD-34 = CLOSED
```

### 完整规范（冻结）

```text
Duplicate Trigger:
    POST /api/conversations/{conversation_id}/messages 携带 Idempotency-Key K
    ∧ (conversation_id, K) 的 USER Turn 已存在
    ∧ 该请求对应的 ASSISTANT Turn 已存在（= completed）
    ∧ SHA-256(conversation_id + content) 与保留 USER Turn 的 content 指纹一致
    ⇒ duplicate

HTTP Status:
    200 OK（与首次响应一致；不是 409）

Response:
    ConversationMessageResponse（**同一 DTO**，不新增 DTO / 不改变字段集合）

content:
    = 已持久化 ASSISTANT Turn.content（原样返回；不改写 / 不截断 / 不重新生成）

route:
    = null（本次未执行 AI，无真实路由）
      实现前提：ConversationMessageResponse.route 微调为 str | None = None
      **禁止**：route = "replay" / "unknown" / "conversation" / "duplicate" / "chat"
               或任何从 observability 推断的值

data:
    = null（未持久化；且当前真实响应 data 本就恒为 null）→ ACCEPTED LOSS

metadata:
    = { "request_id": <已持久化 assistant_request_id>, "idempotent_replay": true }
      （不得伪造 outcome；不得写入 turn_id / conversation_id）

Replay Marker:
    metadata["idempotent_replay"] = true（仅 metadata key，不改 DTO 字段）

AI Execution:
    0 次（不调用 Orchestrator）

USER Turn:
    不创建第二条（复用既有 key 保留行）

ASSISTANT Turn:
    不创建第二条
```

边界：

* EMPTY / REFUSED+empty / FAILED / Crash ⇒ **未 completed** ⇒ **不进入** duplicate 路径（走 retry，OD-26）；
* 同 key 异 payload ⇒ `IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD`（409），**不**进入 duplicate。

### Step 6 实施连带项（本阶段 0 修改）

```text
A-1  ConversationMessageResponse.route: str → str | None = None（唯一 DTO 微调）
     并在 docstring 注明：仅 duplicate replay 场景为 null；两个 envelope 的 required-ness 自此分化
A-2  duplicate 响应构造：content 来自 Turn；metadata = {request_id, idempotent_replay: true}
A-3  不得从 observability 推断 route / outcome
T-3  回归确认 tests/test_conversation_message_api_contract.py:134-147 仍通过
     （字段名集合不变 ⇒ 预期不受影响；若后续追加 replay 字段则需同步更新）
```

---

## 15. Evidence Boundary（再次确认，不修改）

```text
Idempotency-Key · conversation_id · turn_id · assistant_request_id
        ↓ 禁止进入
Evidence identity · Evidence provenance · dataset_version
```

* Evidence = **Source / Provenance Unit**；
* **Runtime Evidence = DEFERRED**；`OD-31 = OPEN · OD-32 = OPEN`（不关闭）；
* duplicate replay **不产生**任何 Evidence（AI 不执行）；
* 本 Step 未修改 Evidence / ConversationEvidence 任何内容。

---

## 16. Open Decisions

```text
OD-22 OPEN      DB 变更实施授权（待批准）
OD-23 OPEN      完整 HTTP response replay（本 Step 明确：需要时走 Architecture B，不走 Option C）
OD-24 CLOSED
OD-25 CLOSED
OD-26 CLOSED
OD-30 OPEN
OD-31 OPEN
OD-32 OPEN
OD-33 OPEN      in-flight vs NOT_COMPLETED 可区分性（Step 6 implementation concern）
OD-34 CLOSED    ← 本 Step：Duplicate = Message Replay（Option A）
```

---

## 17. G-2 Readiness

| G-2 项 | 契约状态 |
| ------ | -------- |
| `Idempotency-Key` Header（Optional；Body 仅 content） | ✅ FROZEN（Step 1A §6 / Step 4A §10） |
| **Duplicate Response Contract** | ✅ **FROZEN（本 Step，OD-34 CLOSED）** |
| 409 In-flight | ✅ FROZEN（Step 1A §10 Policy 2；可识别性 OD-33 为 Step 6 实施细节，不影响契约） |
| 422 invalid key | ✅ FROZEN（空白=未提供；> 128 → 422；服务端不生成/不截断） |
| same-key different-payload conflict | ✅ FROZEN（409 · `IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD`） |

```text
G-2 = READY
```

（G-2 仅表示 API 侧契约完备；Step 6 开工仍需 **G-1 = OD-22 DB 变更授权**。
若 G-1 未批准 → Step 6 = BLOCKED。）

---

## 18. 本阶段产物与验证

```text
新增文件（唯一）：
    docs/evaluation/Phase 4.2 Step 5A — OD-34 Duplicate Replay Response Contract.md
追加修改（append-only）：
    docs/decisions/Phase/Phase 4.2 Roadmap v1.0.md  §23

验证：
    git diff -- backend  = 0
    git diff -- tests    = 0
    DB / migration / production DB writes / API / Evidence = 0
```
