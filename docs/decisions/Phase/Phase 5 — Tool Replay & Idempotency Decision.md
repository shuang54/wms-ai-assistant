# Phase 5 — Tool Replay & Idempotency Decision

> **阶段性质**：**只做架构调查 + 决策冻结（append-only 新文档）**。
> **不实现** Tool Idempotency / Tool Result Store / Checkpoint / Resume / HITL / Cancellation / Read API。
> **不修改** AgentRuntime 主循环 · Tool Registry · 现有 Tool Handler · Conversation / Idempotency-Key ·
> DB Schema · Prompt · Provider · API。
>
> 承接：
> `Phase 5 — Scope & Architecture Decision.md`（§29 / §30）
> `Phase 5 — Agent State & Resume Boundary Decision.md`（S-0；§10 Tool Replay = **Decision Deferred (target = Option B, gated)**）
> 本决策即对该 Deferred 的**收口**（P-2 Tool Idempotency/Dedup Contract + P-3 Replayable Tool Result Store）。

---

## 1. Problem

```text
核心问题（S-0 §10 遗留）：
    Agent 在 **Tool 执行完成之后、Checkpoint/结果持久化之前** 进程崩溃时，
    下一次 Resume 如何避免**错误地重复执行**同一个 Tool Call？

链路（真实实现，S-1 起已可观测）：
    LLM decision(TOOL_CALL, tool_call_id)
        ↓ Runtime 授权 + 构造 ToolExecutionContext（request_id/round/project_id/tool_call_id）
        ↓ ToolExecutionService.execute(context=…)  ← handler 真正执行（可能早已产生副作用）
        ↓ ToolExecutionObserver → ai_ops.tool_execution_record（success/duration/…）
        ↓ AgentRuntime 继续（role=tool 回灌）
    缺失：
        ① 执行身份（谁 = 同一个 Tool Call）没有唯一约束 ⇒ 无法判"是否已执行"；
        ② Tool **结果本身**没有任何可靠存储（observation 只存事实，不存结果）
           ⇒ 即使知道"已执行"，也无法 **Replay**（只能重新执行）。
本决策只冻结这两件事的语义与边界（不实现）。
```

## 2. Current State（代码实测，非推断）

| 记录 / 组件 | 真实职责 | 关键字段（实测） | 能否用于 Replay |
| ---- | ---- | ---- | ---- |
| `ai_ops.agent_run` | **Agent Run terminal record**（终态一行；append-only；`UNIQUE(run_id)`；first-write-wins） | id · run_id · request_id · project_id · status · error_code · limit_reason · step_count · tool_call_count · llm_call_count · token_usage_unverifiable · started_at · finished_at · duration_ms · created_at | ❌（无 Tool 结果；终态后才写） |
| `ai_ops.agent_step` | **Runtime State + Audit Projection**（`UNIQUE(run_id, step_index)`；无 FK；append-only） | id · run_id · step_index · **step_type（FINAL/TOOL_CALL）** · status（OK/FAILED） · tool_name · error_code · elapsed_ms · created_at | ❌（**明确不存** arguments / output / result） |
| `ai_ops.tool_execution_record` | **Tool-level observation**（每次执行一行；**无 UNIQUE**；`ix_request_id` / `ix_started_at`；注释明文"不做 retention/长期历史"） | id · **request_id** · **round** · tool_name · started_at · finished_at · duration_ms · **success** · project_id · **tool_call_id** · error_code · error_type | ❌ **不可**：无 `run_id` / 无 `step_index` / 无唯一键 / **无 Tool 结果**；且 `context=None` 时不产生记录 |
| `ai_ops.llm_usage_record` | LLM-level observation（provider 请求 id partial unique + assistant_request_id 索引） | request_id(provider) · assistant_request_id · provider · model · prompt/completion/total tokens | ❌（LLM 级；与 Tool 无关） |
| Conversation `Idempotency-Key` | `conversation_turn UNIQUE(conversation_id, idempotency_key)`；duplicate ⇒ replay 已持久化的 ASSISTANT 消息（AI = 0） | turn_id · conversation_id · role · content · assistant_request_id · idempotency_key | ❌（**用户请求级**，与 Tool 执行无关） |
| `ToolExecutionContext` | Tool 执行**运行时**上下文（frozen；4 字段白名单） | `request_id` · `round` · `project_id` · `tool_call_id` | ⚠️ 只是入参；不持久化 |
| `AgentRuntime` 内部 | in-memory：`messages` / `step_records`；`tool_calls` 仅计数；**无 dedup / 无已执行集合** | `AgentStep{step_index,type,status,tool_name,arguments,output,error,elapsed_ms}` | ❌（内存；进程崩溃即丢失） |
| `ToolDefinition` | `{name, description, parameters, aliases}` —— **无 side-effect / read-only 字段** | — | —（分类靠约定：registry 只注册只读 Tool） |
| 现有 Tool | `get_inventory`（`material_code` / `warehouse_code`，SELECT SUM(qty)）· `get_work_order`（`work_order_no`，SELECT status） | 均 **READ_ONLY** | — |

**结论（事实层）**：
```text
① 现有 4 张表**都**不能承担 Replay：observation 不存结果，state 不存 payload，terminal record 无 Tool 明细；
② Agent 路径的 `round = step_index + 1`（S-1 冻结），`tool_call_id` 为 Provider 原值，
   但**没有**任何持久化唯一键（既没有 run_id，也没有 step_index）；
③ Runtime **没有** dedup：同一 Run 内重复相同 Tool Call（同名同参、不同 step）会**再次执行**
   （仅受 `max_tool_calls` 限制 ⇒ OQ-P5-9 的真实语义）。
```

## 3. Tool Call Identity

### 3.1 三个 Identity 的冻结（与 S-0 §7 / §30.7 一致，本决策确认 Implemented 状态）

| ID | 语义（冻结） | 生成点 | 生命周期 | 持久化现状 |
| ---- | ---- | ---- | ---- | ---- |
| `request_id`（= `assistant_request_id`） | **AI Runtime request correlation** | `AIOrchestrator.execute()`（`new_request_id()`；唯一生成点） | 一次 execute | ✅ agent_run.request_id · llm_usage_record · tool_execution_record |
| `run_id` | **一次 Agent execution identity** | `AgentRuntime.run()`（`new_agent_run_id()`） | 一次 Agent Run | ✅ agent_run.run_id（UNIQUE）· agent_step.run_id |
| `step_index` | **Run 内逻辑步序号（0 起，单调递增）** | `AgentRuntime`（`len(records)`） | 一步 | ✅ agent_step（UNIQUE(run_id, step_index)） |
| `tool_call_id` | **Provider/LLM 单轮协议关联**（`assistant.tool_calls[i].id` ↔ `role=tool.tool_call_id`） | **Provider 响应**（适配器要求非空；不生成/不替换） | 单个 LLM 响应 | ✅ tool_execution_record.tool_call_id（**observation 用，非唯一键**） |

### 3.2 Provider 行为（必须明确的证据）

```text
实测（backend/app/agent/llm.py L254–261 · llm/client.py L164–168）：
    * ToolCall.id **必须非空**（缺失 ⇒ 适配器抛 LLM_FAILURE，不猜测 / 不补造）；
    * 单次响应**最多 1 个** tool call（>1 ⇒ LLMToolCallFormatError）。
推断（Provider 语义，本项目不依赖它做 identity）：
    * tool_call_id 由 Provider **每次响应生成** ⇒ 若为了同一逻辑动作**重新发起 LLM 调用**，
      会得到**新的** tool_call_id（同样的动作 = 不同的 id）；
    * 因此 `tool_call_id` **不能**单独作为跨进程/跨恢复的执行身份。
本项目**不**尝试跨 LLM 调用复用旧 tool_call_id（也不允许"改名重问"）。
```

### 3.3 Decision（OD-P5-35）：Tool Execution Identity

```text
**唯一 Tool 执行身份 = (run_id, step_index)**（"这一次 Run 的第几步"）。
    * 稳定：与 Provider 重发 / Resume / 重放无关（decision 由 checkpoint/step 重放，不重新问 LLM）；
    * 已有载体：`agent_step UNIQUE(run_id, step_index)`（**已实现**）—— 无需新建 identity 体系；
    * 与现有观测自洽：`tool_execution_record.round = step_index + 1`（S-1 冻结映射）。

`tool_call_id` 的职责收窄为：**单轮协议关联 + observation 明细**（不是 idempotency key、不是主键）。
Conversation `Idempotency-Key` 与本 identity **无关**（§21）。
```

## 4. Idempotency Decision（P-2）

### 方案比较

| 维度 | Option A：`tool_call_id` 作为执行身份 | Option B：`(run_id, step_index)` 作为执行身份 | Option C：其它 |
| ---- | ---- | ---- | ---- |
| 跨 Resume 稳定性 | ❌ Provider 重发 ⇒ 新 id（同一动作换 id ⇒ 无法判重） | ✅ 与 decision/step 绑定 | — |
| 与既有持久化自洽 | ⚠️ 仅 observation 有该列（**无 UNIQUE**，且`context=None` 时无行） | ✅ `agent_step UNIQUE(run_id, step_index)` **已存在** | — |
| 是否需要新 schema | 需给 observation 表加 UNIQUE（**改观测表语义** ⇒ 违背 §六 边界） | ❌ 不需要（复用既有唯一键） | — |
| 与 step 语义一致 | ⚠️ 一步一 tool，但"一步"不是身份 | ✅ 一步 = 一次执行（Runtime 保证 one tool per step） | — |
| 供应商无关 | ❌ 依赖 Provider id 语义 | ✅ | — |
| HITL / 未来写入 Tool | ✅（仍需身份） | ✅ | — |

### Decision（OD-P5-36）

```text
**采纳 Option B**：`(run_id, step_index)` 为 Tool 执行（Dedup / 未来 Replay）的唯一身份；
`tool_call_id` 仅作协议关联。

配套规则：
    1) 同一 `(run_id, step_index)` **至多**允许一次 handler 执行（未来由唯一约束 + atomic claim 保证）；
    2) 本阶段**不实现**该约束（无新表 / 无 migration）；身份语义先行冻结；
    3) **不做**"内容相同即去重"（同名同参、不同 step ⇒ 仍是两次不同执行 —— 那是 OQ-P5-9 的范围，
       未来若要内容级 dedup 必须另立 Decision；当前只按 step 粒度判重）；
    4) Conversation `Idempotency-Key` 不参与本判重（§21）。
```

## 5. Replay Decision（P-3）

### 5.1 Replay ≠ Re-execute（语义冻结）

```text
Replay       = 恢复**同一个已完成 Tool Call 的历史结果**（确定性；不改写 Run 语义）
Re-execute   = 再次调用 handler ⇒ 结果可能不同（数据新鲜度变化）、可能重复副作用

⚠️ READ_ONLY ≠ 可以盲目重跑（**必须区分**）：
    read-only 只保证"没有副作用"，**不**保证"结果不变"。
    例：`get_inventory` 在 T1 返回 120；崩溃后重跑在 T2 返回 100 ⇒
        这不是"恢复"，而是**静默改变了 Run 的事实**（且与已回灌给 LLM 的历史不一致）。
    因此：**能 Replay 就 Replay；不能 Replay 就 fail-closed**（默认），
    重跑仅在"显式只读分类 + 显式策略授权"下才允许（未来 gated，见 §10 / OQ-P5-22）。
```

### 5.2 Decision（OD-P5-37）

| 已持久化状态 | Resume 行为 | Handler 执行次数 | 理由 |
| ---- | ---- | ---- | ---- |
| **SUCCEEDED**（成功 + 结果已可靠保存） | **Replay 历史结果**（原样回灌，`role=tool` + `【TOOL RESULT — UNTRUSTED DATA】`） | **0** | 确定性与事实一致；避免副作用与语义漂移 |
| **FAILED**（失败 + 结果/稳定码已保存） | **Replay 失败**（保持 FAILED；**不自动重跑**） | **0** | 无法从记录区分"业务失败 / 瞬时基础设施失败"；自动重跑会引入不确定语义（重试策略 = OQ-P5-19） |
| **UNKNOWN / IN-FLIGHT**（无 SUCCEEDED/FAILED 记录：崩溃窗口、结果未持久化、`running` 未收敛） | **FAIL CLOSED**（不允许 Resume 该 step；不 blind rerun） | **0** | 无证据证明副作用未发生 ⇒ 违背 §17 的"不声称 exactly-once"前提 |
| 无记录但 decision 也未持久化 | **FAIL CLOSED**（与 S-0 §11 LLM replay 规则一致） | **0** | decision 缺失 ⇒ 不重问 LLM（非确定性） |
| （未来 opt-in）READ_ONLY + 显式策略 | 允许"受控重跑"（**必须**标注为 re-execute 而非 replay，并记录新事实） | 1 | 需 side-effect 分类 + 授权（OQ-P5-18 / OQ-P5-22） |

## 6. Tool Result Store Decision（P-3）

### 6.1 结论：需要**独立** Store；**不**复用/不改动既有表

```text
* `tool_execution_record` **不可**承担 Replay（§2 实测：无 result / 无 run_id / 无 step_index / 无 UNIQUE；
  且它是 observation：`context=None ⇒ 0 事件`、无 retention）；
* `agent_step` **不可**承担（已冻结"不存 arguments/output"）；
* `agent_run` **不可**承担（terminal record，且不含 Tool 明细）。
⇒ 未来新增**独立** `ai_ops.agent_tool_result`（**本阶段不创建**）。
```

### 6.2 设计（冻结；字段为设计草案，非本阶段实施）

```text
ai_ops.agent_tool_result（未来表；**本阶段 0 DDL**）
    id                     BIGINT PK
    run_id                 VARCHAR(64) NOT NULL        ← 执行身份（§3.3）
    step_index             INTEGER     NOT NULL        ← 执行身份
    tool_name              VARCHAR(128) NOT NULL
    project_id             VARCHAR(128) NULL
    success                BOOLEAN     NOT NULL
    error_code             VARCHAR(64)  NULL            ← 仅稳定码
    result_payload         JSONB        NULL            ← 安全序列化结果（小结果内联）
    result_hash            VARCHAR(64)  NULL            ← 规范化序列化后的 SHA-256（完整性校验）
    result_size_bytes      INTEGER      NULL
    result_truncated       BOOLEAN      NOT NULL        ← 是否发生截断 / 仅存引用
    result_unavailable     BOOLEAN      NOT NULL        ← True ⇒ 不可 Replay（fail-closed 依据）
    result_ref             VARCHAR(256) NULL            ← 大结果外部 artifact 引用（未来）
    tool_call_id           VARCHAR(128) NULL            ← 协议关联（**非唯一键**）
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT now()
    UNIQUE (run_id, step_index)                          ← 执行身份唯一（Dedup 基础）
    （无 FK；correlation 语义与 agent_run / agent_step 一致）
```

### 6.3 保存 / 不保存 / 大小边界

```text
保存：
    * **JSON-safe 安全序列化结果**（复用 Runtime 既有序列化路径的结果体，不含 marker 包装）；
    * 稳定错误码（FAILED 时）；成功/失败布尔；tool_name / project_id / 身份字段；时间；
    * `result_hash`（规范化序列化 → SHA-256）；`result_size_bytes`。
不保存（硬禁止）：
    system prompt · user prompt · conversation context · RAG chunks · tool definitions ·
    messages / assistant.tool_calls 原文 · SQL 原文 / SQL credentials · headers ·
    API key / authorization / password / database URL · 原始异常 message / stack trace ·
    原始 Provider 响应 · 密钥类字段。
大小边界（必须显式，禁止"TEXT 无上限"）：
    small  ：规范化 JSON ≤ **32 KiB** ⇒ 内联 `result_payload`；
    large  ：32 KiB < size ≤ **1 MiB** ⇒ 仅 `result_ref` + `result_hash` + `result_size_bytes`
             （**本期不实现外部 artifact 存储** ⇒ 标记 result_unavailable=true ⇒ 不可 Replay）；
    oversize：> 1 MiB ⇒ 不保存 payload（hash + unavailable 标记）；
    * 任何截断都必须置 `result_truncated=true`；被截断的结果**不可**用于 Replay（fail-closed）。
```

### 6.4 敏感数据 / 加密（决策 + 未决）

```text
判定：Tool Result **可能**包含业务数据与 PII（库存 / 工单等），且**必然**是不可信输入
      （Prompt Injection 载体 ⇒ §9）。
决策（本阶段）：
    * 不实现应用层加密（不凭空设计密码体系）；
    * 依赖：DB 层静态加密（部署能力，非项目代码）+ **最小化**（只存结果体，不存 prompt/context）
      + retention（沿用既有"长期历史"先例，但本表可另立策略）；
    * 存储内容仍标记为 **untrusted data**：Replay 回灌时必须带
      `【TOOL RESULT — UNTRUSTED DATA】` + `role=tool`（不得升格为 system / policy）。
未决（OQ-P5-21）：是否需要 application-level encryption / PII 脱敏（redaction）与字段白名单化。
```

## 7. Checkpoint Boundary（三者不可混用）

```text
AgentRun                = 发生了什么（terminal record：状态 / 计数 / 时间）
AgentStep               = 已经发生过什么（**安全投影**：type/status/tool_name/稳定码/耗时）
Checkpoint（未来）      = 恢复需要**知道**什么（current_step / next_action / pending ids / state_version / claim_token）
Tool Result Store（未来）= 恢复后需要**重放**什么（(run_id, step_index) → 安全结果体 / hash）

关系（未来形态）：
AgentRun
   ├── AgentStep*                        （审计投影；已有）
   └── Checkpoint（未实现）
          ├── current_step / next_action
          ├── pending_tool_call_id       （协议关联；不是执行身份）
          └── Tool Result Reference      （(run_id, step_index) → agent_tool_result）
Tool Result Store
   └── identity = (run_id, step_index)（**不是** tool_call_id）
```

```text
硬边界：
    * Checkpoint **不**保存 Tool Result 本体（只保存引用 / 身份）；
    * Tool Result Store **不**保存恢复控制信息（next_action / approval 等）；
    * AgentStep **不**因本决策扩展任何字段（尤其不新增 result / arguments）。
```

## 8. Concurrency

```text
场景：两个 Resume/恢复请求（或重放）同时到达同一 (run_id, step_index)。
结论：**UNIQUE(run_id, step_index) 是必要但非充分条件**；必须叠加：

    ① `UNIQUE(run_id, step_index)`（身份唯一；写入用 ON CONFLICT DO NOTHING = first-write-wins）
    ② `state_version`（乐观并发：UPDATE … WHERE version = ?）
    ③ `claim_token`（恢复租约：谁持有 token 谁可推进；带 TTL，防"双恢复"）
    ④ read-after-write 校验：Replay 前校验 `result_hash`（防读到半写 / 被改写的数据）

本阶段：**只冻结要求，不实现**（Checkpoint / Resume 阶段的前置条件，OQ-P5-14 系列）。
明确：**Resume 没有并发控制不能安全上线**（延续 S-0 §15 结论）。
```

## 9. Security Boundary

```text
① Prompt Injection 持久化：Tool Result 是 untrusted data —— 一旦进 Store，
   必须**保持** untrusted 语义（Replay 回灌：role=tool + marker；绝不进 system / policy）。
② PII / secrets：只允许"业务结果体 + 稳定码"；禁止 prompt / context / SQL 原文 /
   headers / 凭证 / 异常正文（§6.3 硬禁止清单）。
③ Large result：必须有大小边界（§6.3），禁止无上限 TEXT；
   超限 ⇒ 不保存 payload ⇒ 标记 unavailable ⇒ fail-closed（不得靠截断后的结果继续 Run）。
④ 完整性：`result_hash` 用于**校验**（不是加密、不是认证）；hash 不匹配 ⇒ fail-closed。
⑤ 不得把 Store 用作跨 Run 缓存（identity 含 run_id ⇒ 天然限定在单次 Run 内）。
⑥ 本阶段不设计加密系统（OQ-P5-21）。
```

## 10. Failure Model（四象限 + 关键窗口）

| # | handler | 结果持久化 | Store 状态 | Resume 行为 | Handler 执行次数 |
| ---- | ---- | ---- | ---- | ---- | ---- |
| 1 | success | ✅ persisted | SUCCEEDED | **Replay** | 0 |
| 2 | success | ❌ crash before persist | UNKNOWN | **FAIL CLOSED**（不 blind rerun；未来 READ_ONLY + 策略可 opt-in 重跑，须标注 re-execute） | 0（默认） |
| 3 | failure | ✅ persisted | FAILED | **Replay failure**（不自动重跑） | 0 |
| 4 | failure | ❌ crash before persist | UNKNOWN | **FAIL CLOSED** | 0 |
| 5（补充） | success | ✅ result persisted，**Checkpoint 未写** | SUCCEEDED | **Replay**（由 result store 判"已执行"）⇒ **不重复执行**（本决策要解决的核心窗口） | 0 |

```text
**取消承诺（§17）**：本设计**不**声称 exactly-once。真实模型为：
    at-least-once 可能性（崩溃窗口 ②/④）
    + 执行身份唯一（(run_id, step_index)）
    + 已完成结果 Replay
    + 边界不确定时 **fail-closed**
（无分布式事务 / 无 XA / 无两阶段提交；不依赖 DB 与外部副作用同事务。）
```

## 11. Resume Contract（本决策对其 Tool 部分的冻结）

```text
Resume(run_id) 未来**可以**：
    * 重放已持久化的 LLM decision（S-0 §11：不重问 LLM；缺失 ⇒ fail-closed）；
    * Replay 已保存的 Tool Result（成功 / 失败）并按既有顺序回灌（role=tool + marker）；
    * 继续推进尚未执行的 step（其 decision 已持久化且执行身份无冲突记录）。
Resume **禁止**：
    * blind rerun（无记录即重跑）—— 尤其 READ_ONLY 也不默认允许（§5.1）；
    * 重新生成 tool_call_id / 重新问 LLM 补 decision；
    * 绕过 RuntimePolicy / allowed_tools / ToolExecutionService 授权（第二道校验必须照旧）；
    * 把 Replay 结果升格为 system / Evidence。
未决：Resume 的入口 / 授权 / 审批（HITL）不在本决策范围。
```

## 12. Migration Path

```text
P-0（已完成）  AgentRun Persistence / Tool Observation(S-1) / AgentStep Persistence
P-1（已完成）  Agent State & Resume Boundary Decision（S-0；Tool Replay 标为 Deferred）
P-2（本决策）  Tool Idempotency / Dedup Contract            → **DECIDED（Option B）**
P-3（本决策）  Replayable Tool Result Store 设计            → **DECIDED（独立表 + 身份 + 边界；未实现）**
P-4（未来授权） 实现 `ai_ops.agent_tool_result`（表 + ORM + Repository + best-effort writer：
                **写入点必须在 Tool 执行成功后立即发生**，先于 Run/Checkpoint 落盘；
                不改 agent_run / agent_step / tool_execution_record schema）
P-5（未来授权） Checkpoint（含 state_version / claim_token / next_action / pending ids）
P-6（未来授权） Resume（replay decision + replay tool result + fail-closed 边界）
P-7（未来授权） HITL / Cancellation / Read API

每个 P-x 的前置条件（gating）：
    P-4 ⇐ 本决策（P-2/P-3）+ 表结构评审（含大小/敏感数据策略）
    P-5 ⇐ P-4（结果可 Replay 才有意义）+ 并发控制设计
    P-6 ⇐ P-5 + 幂等/重放测试（含崩溃窗口用例）
```

## 13. Open Questions

```text
新增（不阻塞本决策，但阻塞 P-4/P-6）：
OQ-P5-18 Tool 副作用分类（READ_ONLY / IDEMPOTENT_WRITE / NON_IDEMPOTENT_WRITE / UNKNOWN）
         如何声明与强制（`ToolDefinition` 当前**无**该字段；现靠"registry 只注册只读 Tool"约定）
OQ-P5-19 FAILED 是否允许策略化重试（`retryable` 标记 / 最大重试 / 谁批准）
OQ-P5-20 大结果（> 1 MiB）外部 artifact 存储形态与 retention
OQ-P5-21 是否需要 application-level encryption / PII redaction（当前仅依赖 DB 层 + 最小化）
OQ-P5-22 READ_ONLY 在 UNKNOWN 边界下的 opt-in 重跑策略（谁批准 + 如何审计）
OQ-P5-23 `agent_tool_result` 的 retention / TTL（与 agent_run 同寿命？）
OQ-P5-24 Checkpoint ↔ result store 引用方向（FK or correlation；延续"correlation 不用 FK"）
保持 OPEN（来自前序决策）：
OQ-P5-6 取消触发源 · OQ-P5-9 重复相同 Tool 调用（本决策已明确"按 step 粒度判重，不做内容判重"）
· OQ-P5-12 AssistantOutcome 专用状态 · OQ-P5-17 Agent 读模型 · OQ-P5-2（已由 §30 关闭）
```

## 14. Architecture Drift / Findings（只报告，不修改）

```text
AD-1 `tool_execution_record` 不能兼作 Replay Store：无 result / 无 run_id / 无 step_index / 无 UNIQUE；
      且 `context=None ⇒ 0 事件`（S-1 之前 Agent 路径无记录）—— 本决策据此分离
      "observation" 与 "replay store"（§2 / §6.1）。
AD-2 `ToolDefinition` 无 side-effect 字段 ⇒ 只读政策是**约定**而非可校验元数据（OQ-P5-18）。
AD-3 AgentRuntime 无 dedup：同一 Run 内重复相同 Tool Call（不同 step）会再次执行（OQ-P5-9）；
      本决策**不**改变该行为（仅冻结"按 (run_id, step_index) 判重"的身份语义）。
AD-4 `tool_call_id` 由 Provider 每次响应生成（适配器要求非空、单 tool call）⇒ 不可作为跨恢复身份（§3.2）。
AD-5 `agent_step` 已含 `tool_name`/`error_code`/`elapsed_ms`（安全投影），
      但**不**含 result ⇒ 与 result store 互补，无需扩展（§7）。
```

## 15. Decision Summary（冻结）

```text
OD-P5-35 Tool Execution Identity = (run_id, step_index)；tool_call_id 仅协议关联
OD-P5-36 Idempotency = **Option B**（step 粒度唯一；不内容判重；Conversation 幂等不参与）
OD-P5-37 Replay = 成功/失败均 Replay 历史事实；UNKNOWN/IN-FLIGHT ⇒ FAIL CLOSED；
         READ_ONLY ≠ 可盲目重跑；不声称 exactly-once（at-least-once + dedup + replay + fail-closed）
OD-P5-38 Tool Result Store = 未来**独立** `ai_ops.agent_tool_result`（身份 + 安全序列化 + hash +
         大小边界 + untrusted 回灌）；**本阶段不创建**
OD-P5-39 Concurrency = UNIQUE(run_id, step_index) + state_version + claim_token + hash 校验（延期实现）
不变式：AgentRuntime 0 改动 · Tool Registry 0 改动 · Tool Handler 0 改动 · Conversation 0 改动 ·
        既有 4 张表 schema 0 改动 · Prompt 0 改动 · Provider 0 改动 · DB 0 写入

本阶段产出：仅本文档。**Tool Idempotency / Tool Result Store = NOT IMPLEMENTED**（P-4 需明确授权）。
```
