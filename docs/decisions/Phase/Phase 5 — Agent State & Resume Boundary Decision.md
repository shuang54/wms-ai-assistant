# Phase 5 — Agent State & Resume Boundary Decision

> **阶段性质**：**只做架构审计 · 状态机设计 · 恢复语义设计 · Decision 文档**。
> **不实现** AgentStep Persistence / Resume / Checkpoint / HITL / Cancellation；
> **不修改任何生产代码**（`backend/` 0 改动 · DB schema 0 · API 0 · Prompt 0 · tests 0）。
>
> **append-only**：不修改已冻结的
> `docs/decisions/Phase/Phase 5 — Scope & Architecture Decision.md`（§29 Integration / §30 Persistence）。
> 本文档只在其之上冻结 **AgentRun / AgentStep / Checkpoint / Resume / HITL / Cancellation**
> 之间的责任边界，供"是否进入 AgentStep Persistence"这一决策使用。

---

## 1. Context

```text
已完成：Agent Core Loop · Provider-native Tool Calling · Agent Integration ·
        AgentRun Persistence（ai_ops.agent_run = terminal run-level record）
未开始：AgentStep Persistence · Resume · Checkpoint · HITL · Cancellation

问题：在进入 AgentStep Persistence 之前，必须一次性回答
      "AgentStep 是什么？Resume 恢复什么？Checkpoint 是什么？HITL / Cancel 落在哪里？"
      否则会出现：
        * 把 terminal record 误当成可变运行状态（append-only 模型被破坏）；
        * 把 paused 状态误当成终态（WAITING_APPROVAL 被当成 FAILED / COMPLETED）；
        * 把 observation 误当成 state（Resume 依赖不可靠的 LLM / Tool 重放）。
```

## 2. Current State（代码实测，非推断）

| 事实 | 真实情况 | 证据 |
| ---- | -------- | ---- |
| Agent 终态 | `COMPLETED / FAILED / LIMIT_REACHED`；`CANCELLED / WAITING_APPROVAL` **仅常量**（无产生路径） | `agent/models.py` L42–46；全库仅常量与 docstring 引用 |
| AgentStep | `step_index`（0 起）· `type ∈ {FINAL, TOOL_CALL}` · `status ∈ {OK, FAILED}` · `tool_name` · `arguments` · `output` · `error`（稳定码） · `elapsed_ms`；**in-memory only** | `agent/models.py` L49–54 / L130–152 |
| 步类型 | **没有** `LLM_DECISION` 步：一次成功解析的 LLM decision ⇒ 一个 step（`FINAL` 或 `TOOL_CALL`） | `runtime.py` `steps += 1` 后按 `decision.type` 分支 |
| 循环 | `while True`：先 `_check_limits`（steps → llm_calls → tool_calls → tokens → elapsed）→ LLM 调用（计数）→ FINAL/TOOL_CALL/非法；Tool 失败 / 未授权 / 缺 `tool_call_id` ⇒ **立即 FAILED 终止**（不继续循环） | `agent/runtime.py` `run()` / `_check_limits` |
| run 身份 | `run_id` 由 Runtime 在 `run()` 内签发一次（`uuid4`），随 `AgentRuntimeResult.run_id` 暴露；持久化层**只读取** | `agent/ids.py` · `agent/runtime.py` |
| agent_run 写入 | **终态写入一行**；`UNIQUE(run_id)` + `ON CONFLICT DO NOTHING`（first-write-wins）；**append-only**（无 update / delete / TTL / 级联） | `db/models/agent_run.py` · `db/agent_run_repository.py` |
| Tool 观测（Agent 路径） | ⚠️ Runtime 调用 `execute(tool_name, arguments=arguments)` **未传 `ToolExecutionContext`**；边界 `emit_record = context is not None and observer is not None` ⇒ **Agent 的 Tool 执行不产生 `tool_execution_record`** | `agent/runtime.py` L453 · `services/tool_execution_service.py` L265–282 |
| LLM 观测（Agent 路径） | 每次 Agent LLM 调用经 Provider accounting sink 写 `llm_usage_record`（`assistant_request_id` = trace scope 内 request_id） | `llm_usage_persistence_service` · Orchestrator `assistant_trace_scope` |
| Assistant 终态 | Orchestrator 写 `assistant_outcome_record`（`UNIQUE(assistant_request_id)`，4 态；LIMIT_REACHED ⇒ FAILED） | `ai_orchestrator_service._record_assistant_outcome` |
| Conversation | `conversation_turn` **append-only**（Repository 无 turn 内容 update）；ASSISTANT turn 在 AI 结果产生**之后**创建；`Idempotency-Key` 归 ConversationRuntime | `db/conversation_repository.py` 方法清单 · `chat_application_service` |
| Runtime 依赖 | agent 执行内核 **0 DB / 0 HTTP / 0 Conversation / Provider 无关**（源码审计锁定）；仅 `agent/recorder.py` 触碰 DB | `tests/test_agent_runtime.py` · `tests/test_agent_orchestrator_integration.py` |

## 3. AgentRun Definition（已冻结，不复述争议）

```text
ai_ops.agent_run = 一次 AgentRuntime.run() 的 **terminal execution record**
    * 写入时机：run 终态确定之后（一次性、一行、不更新）
    * 语义：run-level 事实与汇总（status / 稳定码 / 计数 / 时间 / correlation）
    * 明确**不是**：checkpoint · resume state · step state · HITL state · Evidence
```

## 4. AgentStep Definition（Question 1 —— 必须明确选择）

### 三个候选

| 定义 | 含义 | 评估 |
| ---- | ---- | ---- |
| A. Observation | 与 `llm_usage_record` / `tool_execution_record` 同类的调用事件 | ❌ 会与既有 observation 重复（LLM/Tool 明细已有专门表） |
| B. Runtime State | 仅用于恢复的状态（下一步做什么 / 挂起了什么） | ⚠️ 不足以支撑审计（"为什么在第 3 步终止"无法回答） |
| C. Runtime State + Audit Projection | 既可驱动恢复，又可作为 run 内逻辑步的审计投影（**不含** LLM/Tool payload 副本） | ✅ 采纳 |

### Decision（OD-P5-30）

```text
**AgentStep = C：Agent Runtime 的「逻辑步状态 + 审计投影」**。

精确边界：
    * 表达：step_index · step_type（FINAL / TOOL_CALL；未来可加 LLM_DECISION）·
      status（OK / FAILED）· tool_name · **稳定错误码** · elapsed_ms；
    * **不表达 / 不复制**：LLM prompt / usage · Tool arguments 正文 ·
      Tool result 正文 · SQL · 任何密钥（这些分别归 LLMUsageRecord /
      ToolExecutionRecord / 不落盘）；
    * 当前状态：**in-memory**（`AgentRuntimeResult.step_records`），不落库；
    * 未来一旦持久化，它**同时**承担 (1) resume 的步级状态依据 与
      (2) run 内审计投影 —— 但 payload 仍留在 observation / 不落盘。
```

## 5. Terminal States

```text
COMPLETED       终态：FINAL decision（content 非空）
FAILED          终态：LLM 失败 / 非法 decision / Tool 未授权 / Tool 执行失败
LIMIT_REACHED   终态：触达 RuntimePolicy 预算（steps / llm_calls / tool_calls /
                      tokens / elapsed）—— **不是失败**（§十三 语义保持）
CANCELLED       终态（未来）：取消请求生效 —— 当前 Runtime 无产生路径

不变式：
    LIMIT_REACHED ≠ FAILED（DB 列**原样**保存；4 态 outcome 的 FAILED 映射是
    Orchestrator 既有行为，本文档不改）
```

## 6. Paused States（§六 关键结论）

```text
WAITING_APPROVAL = **非终态（paused）**：run 未完成，等待人类决策。

**不**允许把它当成 FAILED，也**不**允许当成 COMPLETED：
    * 当成 FAILED  ⇒ 丢失"可恢复"语义，且污染 LIMIT_REACHED/FAILED 的区分；
    * 当成 COMPLETED ⇒ 伪造业务结果（尚无最终回答）。
```

### 关键架构结论：paused 状态**不写** `agent_run`

```text
原因（结构性冲突，不是偏好）：
    agent_run = append-only + UNIQUE(run_id) + ON CONFLICT DO NOTHING（first-write-wins）
        ⇒ 同一 run **只能写一次**
        ⇒ 无法表达 WAITING_APPROVAL → (approve) → COMPLETED 的**同一 run 状态迁移**
    （写第二行 ⇒ 违反 UNIQUE；UPDATE 第一行 ⇒ 违反 append-only / first-write-wins）

因此：
    * agent_run.status 的**取值白名单**仍包含 WAITING_APPROVAL（列定义保留，供未来终态判定），
      但 **paused 生命周期由独立 checkpoint 承载**（见 §8 / §9）；
    * agent_run 依旧只在**终态**写入一行（MVP 与 Resume 前均如此）。
```

## 7. Resume Contract（§七）

```text
Resume(run_id) 的未来契约（只设计，不实现）：

1) Run identity
   run_id **保持不变**（一次 AgentRuntime.run() 的执行身份贯穿暂停与恢复；
   不因 resume 生成新 run_id —— 否则 §9 checkpoint 与 step 索引失去归属）。

2) Request identity
   恢复执行 **产生新的 request_id**：
       * request_id = 一次 AI Runtime 请求的 correlation（Orchestrator 生成，
         `assistant_trace_scope` / `assistant_outcome_record` 的 UNIQUE 键）；
       * 第二次网络请求语义不同 ⇒ 必须是**新** request_id；
       * agent_run.request_id 记录**首次**执行的 request_id（不变），
         resume 产生的新 request_id 由 checkpoint / 未来 state 行携带。

3) Conversation identity
   ConversationTurn 是 **append-only**（Repository 无内容 update）：
       * resume **不编辑**原 turn；
       * 若会话侧需要体现恢复结果 ⇒ **新建 turn**（新 request_id ⇒ 新 assistant_request_id），
         原 USER turn 不变；
       * Agent 层仍**不**查询/拥有 Conversation（关联只通过 request_id ↔
         conversation_turn.assistant_request_id 的字符串等值）。

4) Step identity
   step_index：从 **0** 开始 · **单调递增** · 未来 `(run_id, step_index) UNIQUE`；
   resume 后继续**原有**编号（不重置）。

5) Tool call identity
   tool_call_id 由 **LLM** 提供（Runtime 不生成 / 不替换），用于单轮
   assistant.tool_calls ↔ role=tool 关联：
       * 关系 = AgentStep **引用**一个 tool_call_id（1 step ⇄ 0..1 tool_call），
         而不是 AgentStep 拥有 tool_call 实体（tool 实体在 ToolExecutionRecord /
         未来 checkpoint 中）；
       * resume 时：已执行的 tool_call_id 必须可判定"已执行过"（见 §10）。
```

## 8. Checkpoint Definition（§八）

```text
**agent_run 行 ≠ checkpoint**（明确区分）：

    agent_run      = terminal record（事后事实；append-only；first-write-wins）
    checkpoint     = **可恢复的执行现场**（运行中/暂停时可写、可被后续覆盖，
                     用于"从哪一步继续"）

Checkpoint 是否成立的最小条件（四要素）：
    ① run_id（归属）            ② 当前步位置 / 下一步动作（step_index + next_action）
    ③ 未决动作标识（pending_tool_call_id / pending_approval_id）
    ④ 恢复所需**引用**（不是大文本）：decision 引用 · tool result 引用 ·
       conversation context **引用**（不复制正文）

⇒ 结论：Checkpoint **不能**复用 agent_run 的写入模型，
   未来需要一个**独立、可更新**的 state/checkpoint 载体（表或行版本）。
```

## 9. Minimum Recoverable State（§九 —— 逐项判定，**只作文档概念**）

| 字段（候选） | 判定 | 理由 |
| ---- | ---- | ---- |
| `run_id` | **required** | 恢复归属；贯穿暂停/恢复不变 |
| `step_index` | **required** | 从哪一步继续；单调递增 |
| `state`（RUNNING / WAITING_APPROVAL …） | **required** | 区分 terminal 与 paused |
| `next_action`（继续循环 / 执行 Tool / 请求审批 / 结束） | **required** | 恢复的**动作**依据 |
| `pending_tool_call_id` | **optional** | 仅在"已决策待执行 / 已执行待回灌"时存在 |
| `pending_approval_id` | **optional** | 仅在 WAITING_APPROVAL 时存在 |
| 已保存的 `decision`（type/tool_name/arguments/tool_call_id） | **required（resume 语义）** | 避免重新调用 LLM（非确定性，见 §12） |
| Tool result 引用（record id / 引用键） | **required（若采用 §10 Option B）** | 否则无法判定"该 tool_call 已执行过" |
| conversation context | **not persisted**（只存引用） | 由 Conversation 拥有；正文禁止落盘（§10 / §20） |
| messages / prompt / tool definitions | **not persisted**（可重建） | 系统指令与 tool schema 可从代码/registry 重建 |
| `request_id` | **derived / 记录首次值** | 属请求 correlation，不属 checkpoint 状态 |
| `status`（终态） | **not in checkpoint** | 终态归 `agent_run`（terminal record） |

## 10. Tool Replay Semantics（§十一 —— 最重要）

### 场景

```text
Step k：LLM → TOOL_CALL(get_inventory) → Tool 执行 SUCCESS → （假设）commit
       → worker crash → Resume(run_id)
```

### 三方案比较

| 维度 | A 重新执行 Tool | B 读取历史 Tool Result | C ToolExecutionRecord 作为 replay source |
| ---- | ---- | ---- | ---- |
| 重复执行风险 | ❌ 高（同一 tool_call 可能执行两次） | ✅ 无 | ✅ 无 |
| 幂等性依赖 | ❌ 强依赖 Tool 幂等（当前**无**幂等契约） | ✅ 弱 | ✅ 弱 |
| 数据新鲜度 | ✅ 最新 | ⚠️ 恢复时刻用的是旧值 | ⚠️ 同 B |
| 成本 | ❌ 额外一次执行 | ✅ 0 | ✅ 0 |
| 安全 | ⚠️ 读 Tool 可接受；写 Tool 不可接受 | ✅ | ✅ |
| 实现复杂度 | ✅ 最低（**但语义错**） | ⚠️ 需要 checkpoint 存 result 引用 | ⚠️ 需要可关联的 Tool 记录 |
| 当前可用性 | ✅ | ❌（无 checkpoint） | ❌ **Agent 路径目前不产生 tool_execution_record**（见 §18 D-1） |

### Decision（OD-P5-31）

```text
未来目标语义 = **Option B（读取历史 Tool Result 后继续，不重新执行同一 tool_call）**，
              以便最终满足 **at-most-once**（每个 tool_call_id 至多真正执行一次）。

但：**Decision Deferred（gated）** —— 当前**不具备**实现前提：
    P-1  Agent 路径必须补齐 `ToolExecutionContext` ⇒ 使 Tool 执行可观测、
         可关联（否则既无 replay source，也无法证明"执行过"）；【D-1】
    P-2  Tool 幂等 / 去重契约必须存在（OQ-P5-9：重复相同 Tool 调用如何判定）；
    P-3  Tool Result 的可重放载体（checkpoint 引用 或 可关联 observation）
         必须先落地（即 AgentStep Persistence / Checkpoint 的**前置**）。

MVP（当前，无 resume）实际语义 = **at-most-once + 不重放**：
    每个 step 内部 Tool 至多执行一次；失败即 FAILED 终止，**从不自动重跑**。
（不得把"以后再决定"留作永久状态：一旦 P-1…P-3 任一被授权实现，
  必须回到本节并把 Option B 落地或显式否决。）
```

## 11. LLM Decision Replay Semantics（§十二）

```text
事实：LLM 非确定性 —— 相同 state 重新调用可能得到**不同** decision。

Decision（OD-P5-32）：
    * **不**在 resume 时重新请求 LLM 来"补回"已经产生的 decision；
    * resume 必须 **replay 已保存的 decision**（type / tool_name / arguments /
      tool_call_id）⇒ 保持 run 语义确定；
    * 若 checkpoint 缺少该 decision（不可恢复）⇒ **不允许 resume**，
      fail-closed（终止为 FAILED 或保持 paused 等待人工），**不猜测**；
    * 恢复后**新的** LLM 调用是允许的（那是新的 step），不计入 replay 范畴。
```

## 12. HITL Contract（§十三 —— 只定义语义，不建表）

```text
未来 approval 记录（逻辑字段，**不实现表**）：
    approval_id           审批身份
    run_id                归属的 Agent Run
    step_index            触发审批的步
    requested_action      待批准的动作（稳定描述；**不含** prompt 正文）
    status                PENDING / APPROVED / REJECTED / EXPIRED / CANCELLED
    created_at / resolved_at
    decision              人类决策结果（稳定值）

触发位置（§十五 强制顺序）：
    LLM proposes tool → **Authorization** → (policy 要求审批?) →
        YES ⇒ WAITING_APPROVAL（**Tool 0 次执行**）→ 人类决策 →
        APPROVED ⇒ 才进入 Tool Execution
    ✗ 禁止：Tool 已执行 → 再问人（那是事后追认，不是审批）
```

## 13. Approval State Machine（§十四）

```text
                ┌──────────┐
                │ PENDING  │
                └────┬─────┘
        ┌────────────┼────────────┬─────────────┐
        ▼            ▼            ▼             ▼
   ┌────────┐  ┌──────────┐  ┌─────────┐  ┌──────────┐
   │APPROVED│  │ REJECTED │  │ EXPIRED │  │ CANCELLED│
   └───┬────┘  └────┬─────┘  └────┬────┘  └────┬─────┘
       │            │             │             │
       ▼            ▼             ▼             ▼
   resume(run)   run 终止      run 终止      run 终止
   （继续循环）  （业务判定：  （等同未批准） （取消）
                  FAILED 或
                  CANCELLED ——
                  由业务决策，
                  本文档不替业务选择）
```

## 14. Cancellation Semantics（§十六）

```text
问题：Tool 正在执行时收到取消 —— 中断 还是 排空？

Decision（OD-P5-33）：**drain（排空）**
    * 不中断正在执行的 Tool：等待其返回，随后**不再进入下一轮循环**，
      run 终止为 CANCELLED；
    * 理由：`ToolExecutionService` 与 Handler **没有中断协议**（无法安全取消
      进行中的 DB 查询）；只读 Tool 执行时间短；避免半成品与不可判定状态；
    * 若未来需要 interrupt ⇒ 必须新增：Handler 协作取消协议 + 取消传播 +
      观测语义（属**新的独立 Decision**，不在本文档授权范围）。
```

## 15. Concurrency Model（§十七）

```text
Resume(run_id) 被两个 worker 同时调用 ⇒ 必须防止"双恢复"。

Decision（OD-P5-34）：未来 Resume 必须同时具备
    ① state_version（乐观并发：UPDATE ... WHERE version = ?）
    ② claim_token（恢复租约：谁持有 token 谁可推进；带过期时间）
    （二者互补：version 防写覆盖，token 防并发推进；**不**依赖应用内锁 ——
      进程重启/多实例失效）

⚠️ 明确结论：**Resume 没有并发控制就不能安全上线**（本文档不构成上线授权）。
```

## 16. Resume Idempotency（§十八）

```text
区分（两者不是同一个东西）：
    Conversation Idempotency-Key = 消息级重复请求防护（ConversationRuntime 独占）
    Resume Idempotency           = 恢复动作的重复防护（归 Agent/Resume 边界）

规则（未来）：
    * 同一 resume 请求重复到达 ⇒ 幂等：返回同一 run 状态；
      已被 claim 且未过期 ⇒ 第二次请求**不得**再次推进（拒绝/返回当前状态）；
    * 同一 approval 响应重复提交 ⇒ approval 状态机幂等（APPROVED 后再 APPROVED 无副作用）；
    * resume **不**复用 Conversation `Idempotency-Key` 作为自己的幂等键；
    * duplicate（Conversation replay）⇒ Agent 0 次执行 ⇒ 无 resume、无新 run 行。
```

## 17. Conversation Relationship（§二十二）

```text
Conversation owns：Conversation · ConversationTurn · Idempotency-Key · ConversationContext
Agent owns      ：AgentRun · AgentStep（内存）· policy · loop
Agent 不拥有     ：ConversationTurn · Idempotency-Key · ConversationContext

Resume 只通过 **run_id** 恢复 Agent Execution；
Agent Runtime 仍**不得**查询 Conversation（关联仅经 request_id 字符串等值）。
ConversationTurn append-only ⇒ resume 不编辑原 turn（见 §7.3）。
```

## 18. Observation Relationship（§二十）

```text
LLMUsageRecord        = LLM-level observation（每次 LLM 调用；assistant_request_id 关联）
ToolExecutionRecord   = Tool-level observation（每次 Tool 执行；⚠️ Agent 路径当前缺失，见 D-1）
AssistantOutcomeRecord= Assistant-level outcome（UNIQUE assistant_request_id；4 态）
AgentRun              = Agent-run-level terminal record

未来 AgentStep **不复制** LLMUsageRecord / ToolExecutionRecord 的 payload，
只表达 Agent runtime execution state / transition（见 §4 OD-P5-30）。
```

## 19. Evidence Relationship（§二十一）

```text
保持不变：AgentRun ≠ Evidence · AgentStep ≠ Evidence · ToolResult ≠ Evidence
* 不因"未来需要恢复上下文"而把 Tool Result 自动升级为 Evidence；
* 不写入 evidence_record / conversation_evidence；
* "Agent Evidence Candidate" 仍为未来独立 Decision 的扩展点。
```

## 20. Security Boundary（§十）

```text
Checkpoint / AgentStep（未来持久化）**禁止**落盘：
    system prompt · user prompt · RAG chunks · full conversation context ·
    tool definitions 正文 · SQL · tool result 正文 · API key · Authorization ·
    password · database URL · headers · stack trace · exception message

若恢复需要这些信息 ⇒ 只存 **reference / ID / 可重建标识**：
    * conversation context ⇒ 引用（conversation_id + turn 引用；正文由 Conversation 持有）
    * tool result          ⇒ 引用（ToolExecutionRecord id 或 checkpoint 引用键）
    * prompt / tool schema ⇒ 不存（代码 / registry 可重建）
```

## 21. Persistence Boundary（图 2）

```text
Conversation（ConversationRuntime 拥有）
    │
    ├── ConversationTurn（append-only；ASSISTANT turn 在 AI 结果之后创建）
    ├── Idempotency-Key（UNIQUE(conversation_id, key)）
    └── ConversationContext（Selection/Builder 产出；untrusted reference）

Agent Runtime（执行内核：0 DB / 0 HTTP / 0 Conversation）
    │
    ├── in-memory execution state（messages · step_records · 计时）
    ├── AgentRun  ── ai_ops.agent_run（**terminal record**；append-only；
    │                  UNIQUE(run_id)；first-write-wins）
    └── future AgentStep / Checkpoint（**未实现**；需独立、可更新的载体）

LLM Observation
    └── LLMUsageRecord（每次 LLM 调用；assistant_request_id 关联）

Tool Observation
    └── ToolExecutionRecord（⚠️ Agent 路径当前不产生：无 ToolExecutionContext）

Assistant Outcome
    └── AssistantOutcomeRecord（UNIQUE assistant_request_id；4 态）

Evidence
    └── EvidenceRecord / ConversationEvidence（reference，非 ownership）
```

## 22. Deferred Decisions（§十九/各节 gate）

```text
本阶段**有结论**的（不接受"以后再说"）：
    OD-P5-30 AgentStep 定义（C） · OD-P5-31 Tool Replay 目标语义（B，gated）
    · OD-P5-32 LLM Decision Replay（replay，不重问） · OD-P5-33 取消（drain）
    · OD-P5-34 并发（state_version + claim_token）
    · paused 不写 agent_run（结构性冲突结论）

保持 Deferred（明确前置条件，非永久搁置）：
    D-1 → 何时修复 Agent 路径 Tool 观测缺失（前置：Runtime 需接收 correlation
          （保持 DB-free 前提下由 Orchestrator 传入）或边界提供新入口）
    D-2 → Tool Replay 最终落地（前置：P-1/P-2/P-3，见 §10）
    D-3 → AgentStep/Checkpoint 表结构（前置：Resume 或 HITL 任一立项）
    D-4 → CANCELLED 触发源（OQ-P5-6：谁可以取消、超时策略）
    D-5 → 审批触发策略（哪些 Tool/动作需要审批；当前无策略）
    D-6 → Resume 的 API/权限模型（谁可以 resume(run_id)）
```

## 23. Migration Path（未来授权顺序）

```text
S-0（本阶段）冻结状态/恢复/HITL 边界（本文档）        ✅ DONE
S-1 修复【D-1】：Agent 路径 Tool 执行可观测（ToolExecutionContext /
    关联契约）—— **这是 Tool Replay 与审计的前置**
S-2 若 Resume 或 HITL 立项 ⇒ 设计并落地 **独立** checkpoint/state 载体
    （含 state_version + claim_token）+ AgentStep（若采用）
S-3 实现 Resume(run_id)（replay decision + 不重跑 Tool + 并发控制 + 幂等）
S-4 实现 HITL（approval 记录 + 状态机 + **先审批后执行**）
S-5 （可选）Cancellation / Observability 读模型 / Timeline

禁止：在 S-0 阶段创建任何表 / ORM / repository / API；
禁止把 agent_run 改造成可变状态（破坏 append-only / first-write-wins）。
```

## 24. Final Decision

```text
① AgentStep = Runtime State + Audit Projection（不复制 observation payload；当前 in-memory）
② Terminal = COMPLETED / FAILED / LIMIT_REACHED / CANCELLED；
   Paused   = WAITING_APPROVAL（既不是 FAILED 也不是 COMPLETED）
③ paused 状态**不写** agent_run（append-only + UNIQUE(run_id) 无法表达迁移）
   ⇒ 未来必须引入**独立** checkpoint/state 载体
④ agent_run 行 ≠ checkpoint（terminal record ≠ 可恢复现场）
⑤ Resume：run_id 不变 · request_id 新建 · 不编辑原 turn（append-only）·
   step_index 连续 · tool_call_id 引用关系
⑥ Tool Replay 目标 = Option B（不重跑同一 tool_call，at-most-once）；
   MVP 现状 = at-most-once 且不重放；**gated by D-1 / OQ-P5-9 / checkpoint**
⑦ LLM Decision = replay 已保存 decision（禁止重问）；缺失 ⇒ fail-closed
⑧ HITL = 先审批后执行；状态机 PENDING→APPROVED/REJECTED/EXPIRED/CANCELLED
⑨ Cancellation = drain（不中断进行中的 Tool）
⑩ Resume 并发 = state_version + claim_token（无并发控制不上线）
⑪ Resume 幂等 ≠ Conversation Idempotency-Key
⑫ Conversation / Evidence / Observation 边界全部保持（0 修改）
```

---

## 附录 A：图 1 —— Agent State Machine（依据真实 Runtime 语义重画）

```text
                          ┌──────────────────────────────┐
   AgentRuntime.run()  →  │ RUNNING（内存；未落盘）        │
                          └───────────────┬──────────────┘
                                          │ while True:
                                          │   ① _check_limits（预算）
                                          ▼
                        ┌─────────────────────────────────┐
                        │ 触限？（steps / llm_calls /       │──YES──▶ LIMIT_REACHED（终态）
                        │ tool_calls / tokens / elapsed）  │
                        └───────────────┬─────────────────┘
                                        │ NO
                                        ▼
                                   LLM 调用（llm_calls += 1）
                                        │
                ┌───────────────────────┼────────────────────────┐
                │                       │                        │
          decision=FINAL          decision=TOOL_CALL        解析失败/LLM 失败
                │                       │                        │
                ▼                       ▼                        ▼
        COMPLETED（终态）      tool_call_id 缺失            FAILED（终态）
        （content 非空）              │                    （INVALID_DECISION /
                                     │ YES                 LLM_FAILURE）
                                     ▼
                              FAILED（终态）
                                     │ 存在 id
                                     ▼
                        tool_name ∈ allowed_tools ?
                              │ NO                        │ YES
                              ▼                           ▼
                     FAILED（TOOL_NOT_ALLOWED；   ToolExecutionService.execute
                     **Tool 0 次执行**）                  │
                                          ┌───────────────┴───────────────┐
                                          ▼                               ▼
                                    success                          failure
                                          │                               │
                                          ▼                               ▼
                        append assistant.tool_calls + role=tool   FAILED（终态）
                        （marker + 同一 tool_call_id）             （TOOL_EXECUTION_FAILURE）
                                          │
                                          ▼
                                  回到 RUNNING（下一轮）

  CANCELLED      （未来）：取消请求 ⇒ **drain** 当前 Tool ⇒ 不再进入下一轮 ⇒ 终态
  WAITING_APPROVAL（未来）：**paused** —— 出现在 `tool_name ∈ allowed_tools` **之后**、
                  Tool 执行**之前**（先审批后执行）；不写 agent_run（见 §6 / §12）
```

## 附录 B：Architecture Drift（只报告，不修改）

```text
【D-1】Agent 路径的 Tool 执行**不产生** `tool_execution_record`
       事实：`AgentRuntime` 调用 `execute(tool_name, arguments=arguments)`，
             未传 `ToolExecutionContext`；边界 `emit_record = context is not None
             and observer is not None`（`tool_execution_service.py` L269）
       影响：Tool-level observation 对 Agent 缺失 ⇒ ① 审计缺口；
             ② §10 Option C（以 ToolExecutionRecord 为 replay source）当前不可用
       处置：只报告；修复属 S-1（需 Runtime 在保持 DB-free 前提下接收 correlation）

【D-2】`AgentStep.type` 只有 `FINAL` / `TOOL_CALL`（**无** `LLM_DECISION`）
       影响：一次 LLM decision 不单独成步；`steps` = 成功解析的 decision 数；
             未来若需"LLM 决策步"审计投影，需扩展 step 类型（属 AgentStep 立项范围）

【D-3】`agent_run` 的 append-only + `UNIQUE(run_id)` + first-write-wins
       **无法**表达 paused → terminal 的同一 run 迁移（§6 已据此决定 paused 不写 agent_run）
       影响：Resume/HITL 必须引入独立可更新载体（S-2）

【D-4】`CANCELLED` / `WAITING_APPROVAL` 仅常量：Runtime 无产生路径
       （无取消触发源 OQ-P5-6、无审批策略 D-5）⇒ 当前**不可达**（与决策语义一致，非代码缺陷）

【D-5】会话消息路径（`ChatApplicationService()` 默认 factory）未注入
       tool observer / outcome recorder（既有事实，OQ-P5-16）——只记录

【D-6】Agent Runtime 不感知 `request_id` / `assistant_request_id`：
       Runtime 只接收 question + conversation_context；
       若未来要补齐 Tool 观测（D-1），需要 Orchestrator 显式传入 correlation 值，
       且 Runtime **仍不得**接触 Session / Repository / DB Model。
```
