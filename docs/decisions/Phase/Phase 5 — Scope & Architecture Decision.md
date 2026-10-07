# Phase 5 — Scope & Architecture Decision

> 性质：**Decision Gate**（只做范围与架构决策；`Production code = 0 · DB schema = 0 · API = 0 · Prompt = 0`）。
> 前置：`Phase 4.2 RELEASED`（commit `8847cca` / `5525822`，PR #10 已 merge 到 `main` = `f6c0b7f`）
> 与 `Phase 5 Pre-flight Baseline Audit`（基线 / 缺口清单）。
> 本文件是 **Phase 5 的唯一执行依据**；未在本文件冻结的内容，一律不得提前实现。

---

## 0. 第一阶段：Phase 编号冲突的正式声明

### 0.1 事实（历史文档 vs 实际路线）

历史文档中存在**另一套** Phase 编号，且彼此也不一致：

```text
docs/architecture.md §37 技术演进路线（历史草案）
    Phase 1 = Python / FastAPI / LLM API
    Phase 2 = PostgreSQL / Embedding / pgvector / RAG
    Phase 3 = Tool Calling / WMS API
    Phase 4 = Agent
    Phase 5 = LangGraph（复杂业务流程 / 状态管理 / 人工确认 / 多步骤工作流）
    Phase 6 = Permission / Audit / Write Operations

docs/requirements.md（历史草案）
    Phase 5 = WMS Integration
    Phase 6 = Agent（"当多个 Tool 的业务流程变复杂后，再引入 Agent"）
```

而仓库**实际执行且已交付**的编号是：

```text
Phase 3.x    AI Core（Router / RAG / Tool / Text-to-SQL / Observability / Evaluation）
Phase 4.1    Conversation + Evidence Foundation
Phase 4.2    Production Conversation Runtime（Idempotency / Context Selection / Consumption）
Phase 5      ← 本文件定义
```

### 0.2 声明（正式、不可歧义）

```text
Historical architecture.md / requirements.md phase numbering
        ≠
Current implementation roadmap numbering
```

```text
1) 本仓库的实际 Phase 编号以 docs/decisions/Phase/ 下的 Phase Roadmap / Decision 文档为准；
2) 历史 architecture.md §37 与 requirements.md 的 Phase 1–6 属**早期技术演进草案**，
   与本仓库已交付路线**不同步**，仅作未来能力参考（Agent / LangGraph / 权限 / 私有化）；
3) **不重新编号**任何已完成 Phase（Phase 3.x / 4.1 / 4.2 保持原编号）；
4) **不修改**任何历史 Phase 文档（Phase 4.1 / 4.2 全部冻结，含 Roadmap §1–§29）；
5) 历史草案中的 "Phase 4 = Agent / Phase 5 = LangGraph" **不构成本仓库 Phase 5 的定义**；
   本文件定义的 Phase 5 见 §1；
6) 后续开发以本文件与后续 Phase Decision 文档为**唯一执行依据**；与历史草案冲突时以本文件为准。
```

> 说明：历史草案中"LangGraph 用于复杂工作流"的设想并未被废弃，而是被**重新定位**到
> 未来的 `Phase 6 Workflow Runtime`（见 §21 / OD-P5-03）。

---

## 1. Phase 5 定义（OD-P5-01）

```text
Phase 5 = Bounded Agent Runtime
```

即在现有 **AIOrchestrator + Tool Runtime** 之上，引入一个**受严格边界约束的多步 Tool-Calling Runtime**：

```text
一次 AI Request
        ↓
Agent Runtime
        ↓
LLM Decision
        ↓
Tool Call
        ↓
Tool Result
        ↓
LLM Decision
        ↓
...（受 Runtime Policy 约束）
        ↓
Final Answer
```

**不是**：

```text
✗ 万能 Agent / Autonomous Agent
✗ Multi-Agent
✗ LangGraph Platform
✗ Workflow Engine
✗ Memory / Planning / Self-reflection
```

**为什么现在需要它（事实依据）**：

```text
现状（Phase 4.2 交付后）：
    Question → AIOrchestrator → Router ─┬─ RAG
                                        ├─ Tool（**一次性执行**）
                                        └─ Text-to-SQL（单次生成 → 校验 → 只读执行）

缺口（Pre-flight Audit §8 实测）：
    * 无 Tool 结果回灌 LLM 的迭代回路（Tool 路径 = 提取参数 → 执行 → 文本化，无第二轮 LLM）
    * 无多步编排 / 无步级状态 / 无步级观测（现观测为单次执行记录）
    * 无步级预算（steps / tool calls / llm calls / time / cost 上限）
    * 无步级授权决策点（当前 Tool 授权是"项目级静态白名单"，非"每步 Runtime 决策"）

已具备（无需新建）：
    LLM Provider（generate / chat）· LLM usage / token 观测 · AIOrchestrator · AI Router
    · Tool Registry（register / get_definition / list_definitions / execute）
    · Tool Capability 白名单（ProjectCapabilities.tool_names）· ToolExecutionService
    · ToolExecutionContext（request_id / round / project_id / tool_call_id，frozen 不可变）
    · Conversation Runtime（Idempotency / Turn / Context）
    · Evidence Foundation · Observability（4 张观测表）· Evaluation · SQL Validator / Read-only Executor
```

因此 Phase 5 的定位是 **"在既有安全与运行时基座上补齐多步能力"**，而不是引入新的执行范式。

---

## 2. 第一原则：能力隔离（OD-P5-02 的前置）

Agent Runtime **不允许**直接获得：

```text
Database Connection / SQLAlchemy Session
HTTP Client
Tool implementation（Handler 函数本体）
filesystem
ProjectContext 解析结果以外的任何后端对象
```

Agent 只能看到：

```text
Tool Definition
Tool Schema（parameters）
Tool Permission（该项目允许的 tool_names）
Tool Result（ToolResult: tool_name / success / data / error）
```

```text
Agent
  ↓
Tool Registry
  ↓
Authorized Tool
  ↓
ToolExecutionService
  ↓
Tool Result
```

禁止：

```text
Agent
  ↓
任意 Python function（绕过 Registry / 授权 / 观测）
```

---

## 3. Agent Runtime 与 AIOrchestrator 的关系（OD-P5-02）

### Decision

```text
Agent Runtime = AI Capability / Runtime（与 RAG / Tool / Text-to-SQL 同级）
AIOrchestrator = 唯一编排入口（RouteDecision 的拥有者）
```

Phase 5 后（**未来形态，本阶段不实现**）：

```text
AIOrchestrator
        ↓
   RouteDecision
        ├── RAG
        ├── TOOL
        ├── TEXT_TO_SQL
        └── AGENT            ← Phase 5 新增路由目标（RouteType 扩展）
                 ↓
           AgentRuntime
                 ↓
            LLM Loop（bounded）
                 ↓
           Tool Registry / ToolExecutionService
```

### Reason

* 现有 `_check_capability` 硬校验、`RouteDecision`、`request_id` 生成、Outcome 判定、
  观测写入都集中在 Orchestrator：Agent 若另建编排层会产生两套能力校验与两套 trace；
* Conversation Runtime 已经把 `question + context` 交给 Orchestrator；
  Agent 只是"该 question 的一种执行方式"，不是新的请求入口。

### Consequences

```text
1) 禁止递归编排：Orchestrator → Agent → Orchestrator → Agent（不存在）
2) Agent 不得自行 route / 不得调用 Orchestrator
3) Agent 内部工具执行**必须**经 ToolExecutionService（授权 / 观测 / 归一化）
4) 能力校验（capability）仍在 Orchestrator 层先行；Agent 内部再做**每步**授权（§11）
5) 现有三种 route 的行为、契约、响应形态**不变**（Agent 是增量）
```

---

## 4. 单 Agent（Multi-Agent 排除）

```text
ONE Agent
Multiple Tools
Bounded Loop
```

禁止：

```text
Agent → Agent · Agent → SubAgent · Planner Agent · Reviewer Agent
```

Multi-Agent 推迟到未来独立阶段（新 Decision 才允许）。

---

## 5. Tool Loop 模型（OD-P5-04）

### 5.1 最小循环（唯一允许的形态）

```text
START
  ↓
LLM
  ↓
Decision
  ├── FINAL
  │     ↓
  │   COMPLETE
  │
  └── TOOL_CALL
        ↓
      Policy Check（授权 + 预算 + approval）
        ↓
      Tool Registry
        ↓
      ToolExecutionService
        ↓
      Tool Result（untrusted data）
        ↓
       LLM（下一轮）
```

### 5.2 轮次契约

```text
每一轮：0 or 1 logical tool decision（禁止一轮多 Tool）
第一版：**不支持 parallel tool calls**（不引入并行调度）
LLM 只能提出：tool_name + arguments（结构化）
Runtime 决定：allowed / denied / requires_approval
```

### 5.3 LLM 决策协议（架构要求，接口实现在下一阶段）

```text
Decision ∈ { FINAL(answer), TOOL_CALL(tool_name, arguments) }
* 必须可被 Runtime 严格解析（非法 / 不可解析 ⇒ 视为 LLM failure，不猜测、不重试风暴）
* LLM **不得**在 decision 中携带：max_steps / max_cost / allowed_tools / requires_approval / project_id
  （出现即忽略，且按 Policy violation 记录）
```

> 实现事实：当前 `LLMProvider` Protocol 仅有 `generate` / `chat`；
> Tool 选择当前由**规则 + 元数据匹配**（`_match_tool`）完成，**不是** LLM function calling。
> 因此"LLM 工具决策协议"是 Phase 5 实施的第一个必须冻结的接口（见 §23 Open Questions）。

---

## 6. Runtime Policy（OD-P5-05）

### 6.1 必须存在的停止条件（五类，缺一不可）

```text
max_steps              —— LLM 决策轮次上限
max_tool_calls         —— Tool 执行总次数上限
max_llm_calls          —— LLM 调用总次数上限
max_execution_time_ms  —— 墙钟时间上限
max_cost               —— 成本上限（或等价 token 预算）
```

### 6.2 Decision（默认值）

```text
MVP 默认（本阶段冻结"存在性与语义"，数值在实现阶段随 RuntimePolicy 冻结）：

max_steps      = 8
max_tool_calls = 8
max_llm_calls  = 8
max_execution_time_ms = 实现阶段冻结（必须存在，缺省不得为"无限"）
max_cost              = 实现阶段冻结（必须存在；见 6.3）
```

### 6.3 成本口径（事实约束）

```text
事实：现有观测只有 **token**（llm_usage_record: prompt_tokens / completion_tokens /
      total_tokens），**没有** cost 列，也没有 pricing 表。
Decision：max_cost 的 MVP 口径 = **token 预算**（max_total_tokens），
          货币化成本需先有 pricing 来源 —— 属实现阶段 Open Question（§23）；
          不得为凑成本指标而新增 pricing 基础设施到 Phase 5 MVP。
```

### 6.4 Policy 是不变量（trusted control plane）

```text
RuntimePolicy 由**服务端构造**，LLM / Conversation Context / Tool Result 均不可修改；
任何一个 limit 缺失 ⇒ 不得启动 Agent Run（fail-closed，缺省**不允许**无限循环）。
```

---

## 7. Agent State（OD-P5-06）

> **本阶段只定义语义，不创建数据库表**（DB schema = 0）。

### 7.1 AgentRun（语义字段）

```text
run_id            —— 本次 Agent Run 标识（服务端生成）
request_id        —— 与既有 chat 关联 ID 一致（trace correlation）
conversation_id   —— 会话归属（由 Conversation Runtime 提供；Agent 不创建）
project_id        —— 授权作用域（服务端解析；**不来自** LLM）
status            —— 见 §8
current_step      —— 当前步序号
step_count        —— 已发生步数
tool_call_count   —— 已执行 Tool 次数
llm_call_count    —— 已调用 LLM 次数
started_at
finished_at
```

### 7.2 AgentStep（语义字段）

```text
step_id
run_id
step_index
type              —— LLM_DECISION / TOOL_CALL / POLICY / FINAL
status
tool_name         —— 仅 TOOL_CALL 步
input / output    —— **仅存最小必要信息**（见 7.3）
error
created_at
```

### 7.3 硬规则（数据最小化）

```text
禁止写入 state：
  * 完整 prompt（system / user / tool schema 全文）
  * Tool Result 原文（可引用其观测记录 ID / 摘要，不入 state）
  * secret / credential / API key / 连接串 / SQL 全文
第一版 state 只回答："发生了什么步、用了哪个 Tool、结果成功与否、为什么停"。
```

---

## 8. Agent Run 状态（OD-P5-06 续）

```text
RUNNING
COMPLETED
FAILED
CANCELLED
LIMIT_REACHED
WAITING_APPROVAL
```

```text
LIMIT_REACHED ≠ FAILED
    理由：Agent 可能是**正常执行到安全预算边界**（例如已达 max_steps 但过程无错误）；
          二者对上层（Conversation / Outcome / 观测 / 评估）语义不同：
          FAILED        = 执行出错（不能给出可信答案）
          LIMIT_REACHED = 按策略停止（可给出"已达步数/预算上限"的受限回答）
```

与既有 `AssistantOutcome` 的关系（`determine_assistant_outcome(route=..., refused=...)`）：

```text
本阶段结论：Agent 的 Outcome 映射在实现阶段冻结（需扩展 Outcome 判定输入），
            但**必须**满足：LIMIT_REACHED 不得被记为 SUCCESS，也不得被记为 FAILED。
            CANCELLED / WAITING_APPROVAL 同样是独立语义（不得折叠）。
```

---

## 9. Error Recovery（OD-P5-04 续）

必须区分的失败类别：

```text
LLM failure     —— Provider / transport / generation error（含 decision 不可解析）
Tool failure    —— ToolExecutionService 执行错误（ToolResult.success=False）
Policy failure  —— Tool not allowed / 参数越权 / approval required 被拒绝
Runtime limit   —— max_steps / max_tool_calls / max_llm_calls / timeout / max_cost
```

### Decision

```text
第一版**不做**自动复杂恢复。明确禁止：
  ✗ 失败 → 自动换模型
  ✗ 失败 → 自动重新规划（re-plan）
  ✗ 失败 → 无限 retry
只允许：失败 ⇒ 进入明确状态（FAILED / LIMIT_REACHED）+ 记录错误边界 + 停。
```

> 与既有事实一致：当前 Orchestrator 的失败语义是"异常原样上抛（不包装 / 不 retry）"，
> Agent 不得破坏该语义方向。

---

## 10. Tool Authorization（OD-P5-07）

```text
Project Capability（ProjectCapabilities.tool_names —— 保留，不废弃）
        ↓
Agent Runtime（每步）
        ↓
Tool Authorization（allowed / denied / requires_approval）
        ↓
ToolExecutionService（执行边界内**再次**做 capability 校验）
        ↓
Tool Result
```

```text
LLM 只能提出：tool_name + arguments
Runtime 决定：allowed / denied
禁止：LLM 自行决定"我需要这个 Tool，所以允许执行"
```

```text
双层校验（必须）：
1) Route / Capability 层：项目是否具备 Tool 能力（既有 ProjectCapabilities）
2) Step / Runtime 层：本步是否允许该 Tool（Phase 5 新增；白名单 = 项目 tool_names 的子集）
事实：DEFAULT_TOOL_NAMES = ("get_inventory",)；已注册 Tool = get_inventory / get_work_order（均只读）
```

---

## 11. Tool Side Effect Policy（OD-P5-08）

```text
Phase 5 第一阶段：**只允许现有 READ-only Tool**。
```

禁止 Agent Runtime 首次实现：

```text
CREATE / UPDATE / DELETE / POST
ERP write / WMS write / database write
```

若未来要支持写操作，必须**另立 Decision**，且至少同时具备：

```text
Approval · Authorization · Idempotency · Audit · Compensation
```

（缺一不得开启写操作；本阶段不设计其形态。）

---

## 12. Human-in-the-loop（OD-P5-09）

```text
架构必须预留 WAITING_APPROVAL，但**第一阶段不实现审批 UI**。

    Tool Policy
        ↓
    requires_approval = true
        ↓
    WAITING_APPROVAL
        ↓
    停止（不继续执行、不自动跨步骤推进）
```

```text
事实：现有 Read-only Tool 下 requires_approval 恒为 false（无触发场景）；
      该状态存在性属于**安全设计预留**，用于未来写操作 / 高风险工具的接入点。
未来独立阶段实现 Approval Center（不在 Phase 5 范围）。
```

---

## 13. Conversation Integration（OD-P5-10）

Agent Runtime **必须运行在 Conversation Runtime 之上**：

```text
Conversation
      ↓
ChatApplicationService（Turn / Idempotency / Context —— 既有，不变）
      ↓
AIOrchestrator
      ↓
AgentRuntime
```

```text
Agent Runtime **不得**自己：
    create Conversation / create ConversationTurn / 处理 Idempotency-Key
    （这些仍属 Conversation Runtime；Agent 是"一次 AI execution"的内部实现）
```

```text
现有幂等语义不受影响：
    duplicate ⇒ replay（AI = 0 · 不进入 Agent Runtime）
    NOT_COMPLETED ⇒ retry（复用既有 USER Turn）
```

---

## 14. Conversation Context（OD-P5-10 续）

Agent 可以读取 `conversation_context`（由 ChatApplicationService 传入），但必须遵守 Phase 4.2 安全优先级：

```text
System / Safety
    >
Capability / Business
    >
Conversation Context（untrusted reference）
    >
Current Question
```

```text
Conversation Context 不能变成 runtime instruction；
不能覆盖：Tool permission · Runtime policy · Safety policy · 预算上限。
（Phase 4.2 已冻结的两处消费点语义不变；Agent LLM 轮次属于"Final generation 的扩展"，
  需在实现阶段明确定义并测试，见 §18 Evaluation / §23 Open Questions。）
```

---

## 15. Evidence Boundary（OD-P5-11）

**Phase 4.1 Evidence Contract 不变**：

```text
Evidence = Source / Provenance Unit
（不是 AI Answer / Tool Result / RAG Result / SQL Result / Conversation Turn）
dataset_version · source_type = Evidence provenance boundary
```

未来接口（**本阶段只定义，不实现**）：

```text
Agent Step
      ↓
Tool Result
      ↓
Evidence Candidate
```

```text
硬规则：
1) Tool Result ≠ Evidence；
2) 只有存在明确 provenance（dataset_version / source_type）才**可能**成为 Evidence；
3) 当前 Tool 无 provenance ⇒ Tool Result **只能**作为 runtime observation（观测记录），
   **不得**强行写入 Evidence；
4) Runtime Evidence 仍 **DEFERRED**；OD-31 / OD-32 保持 **OPEN**（不因 Phase 5 关闭）。
```

---

## 16. Observability Boundary（OD-P5-12）

目标关联关系（父子 trace）：

```text
AgentRun
   ↓
AgentStep
   ├── LLM     （关联 llm_usage_record）
   ├── Tool    （关联 tool_execution_record）
   └── Policy  （授权 / 拒绝 / approval 决策）
```

### 事实（现有 observation model 可扩展性评估）

```text
tool_execution_record : id · request_id · round · tool_name · started_at · finished_at
                        · duration_ms · success · project_id · tool_call_id · error_code · error_type
llm_usage_record      : id · request_id · provider · model · assistant_request_id
                        · prompt_tokens · completion_tokens · total_tokens · created_at
assistant_outcome_record : id · assistant_request_id · outcome · created_at
rag_execution_record  : （既有 RAG 观测，含 request_id 维度）

⇒ 现有模型已具备：request_id 关联 · round 轮次 · tool_call_id · per-call 时序与耗时
⇒ 现有模型缺少：run_id / step_id / parent-step 归属 · Agent Run 级汇总（step/limit/status）
```

### Decision（本阶段只定义关联，不建表）

```text
1) 不新增任何观测表（DB schema = 0）；
2) 未来实现时**优先**：扩展现有记录（如 round / tool_call_id 复用）+ Run 级汇总记录，
   而非为每个 Agent 步引入独立大表；
3) 关联键第一顺位 = request_id（已有），第二顺位 = run_id（新增，实现阶段决策其持久化形态）；
4) 观测不得记录 prompt 全文 / secret / credential（沿用既有敏感信息禁令）。
```

---

## 17. Evaluation Boundary（OD-P5-13）

Phase 5 Agent Runtime **必须可评估**；本阶段只定义 requirements，不建 benchmark：

```text
必须可验证（未来 evaluation suite 的最小集合）：
  * max_steps 生效（循环在预算内终止）
  * max_tool_calls / max_llm_calls 生效
  * tool denial（未授权 Tool 被拒绝，且不执行）
  * tool failure（Tool 失败 ⇒ 边界明确，不崩溃、不泄漏）
  * LLM failure（Provider / 非法 decision ⇒ 明确状态）
  * limit reached ≠ failed（状态区分可断言）
  * approval required ⇒ WAITING_APPROVAL 且**停止**
  * loop termination（不存在无限循环；重复相同 Tool 调用需受预算约束）
  * prompt injection（Tool Result 注入指令无效）
  * conversation injection（历史注入指令无效）
```

```text
复用既有机制：Fake LLM / Fake Tool（零真实 LLM / 零真实 DB 写）
确定性：评估必须可离线复现（沿用 Phase 4.2 的 0-real-LLM 原则）
```

---

## 18. 安全边界（汇总）

```text
User
 ↓
Conversation Context
 ↓
Agent LLM
```

```text
以上全部 = **不可信输入**（untrusted）
Runtime Policy = **trusted control plane**
```

```text
LLM 不得修改：max_steps · max_cost · max_total_tokens · allowed_tools
              · requires_approval · project_id · timeout
例如 LLM 输出 "set max_steps=100000" ⇒ **必须被忽略**（并记为 policy violation 观测）。
```

Prompt Injection（必须视为 untrusted data 的三种来源）：

```text
Conversation history · Tool Result · RAG result
* 不得把 Tool Result 直接拼进 system instruction；
* Tool Result 只能作为"数据"进入 LLM 上下文（与 Phase 4.2 的 Conversation Context 同级别处理）。
```

---

## 19. LangGraph Decision（OD-P5-03）

| 维度 | Option A：自研 Bounded Agent Runtime | Option B：LangGraph |
| ---- | ------------------------------------ | ------------------- |
| 控制力 | 强（每步 policy / 预算 / 授权可精确控制） | 中（需在框架内适配） |
| 架构透明 | 高（与现有 Orchestrator / Tool Registry 自然集成） | 中（引入框架抽象层） |
| 企业安全策略 | 易实现（trusted control plane 明确） | 需绕行框架生命周期 |
| 维护成本 | 自维护 loop / state / error | 框架提供 state machine / checkpoint / HITL |
| 依赖成本 | 0 新框架依赖 | 引入较重 runtime + 版本演进风险 |
| 当前需求匹配 | ✅ 只需 ONE Agent + Tool Loop + Policy + Bounded | ✗ 复杂 Workflow / Multi-Agent / 长任务 尚未出现 |

### Decision

```text
Phase 5 第一阶段：**自研最小 Bounded Agent Runtime，不引入 LangGraph。**
```

### Reason

```text
当前只需要：ONE Agent + Tool Loop + Policy + Bounded Execution；
尚不存在：复杂 Workflow / Multi-Agent / Long-running execution / 复杂 checkpoint graph。
现在引入 LangGraph 会**扩大架构面**，与 Phase 4.2 已验证的"显式 control plane + 可审计边界"方向冲突。
```

### Consequences

```text
1) loop / state / error / 预算必须自研并冻结契约（本文件 §5–§9）；
2) state 持久化形态留待实现阶段决策（§23），第一版可以不持久化 Run 级状态；
3) 若未来进入 **Phase 6 Workflow Runtime**（复杂流程 / 状态机 / 人工确认 / 长任务），
   重新做一次 Decision（LangGraph 或等价方案），并接受"迁移成本"这一已知代价。
```

---

## 20. Phase 5 非目标（明确禁止）

```text
✗ Multi-Agent            ✗ LangGraph              ✗ MCP
✗ Workflow Engine        ✗ Autonomous Planning    ✗ Self-reflection
✗ Self-improvement       ✗ Long-term Memory       ✗ Vector Memory
✗ Browser Agent          ✗ Computer Use           ✗ Code Execution Agent
✗ Database Write Agent   ✗ Approval UI            ✗ Streaming
✗ Agent Marketplace      ✗ Agent Builder
✗ 写操作 Tool（CREATE / UPDATE / DELETE / POST）
✗ 并行 Tool 调用 / 复杂调度
✗ 自动换模型 / 自动重新规划 / 无限 retry
✗ 基于 cost 的新 pricing 基础设施
```

> 一句话：**不要把 Phase 5 做成"大而全 Agent Framework"。**

---

## 21. Phase 5 MVP（第一阶段能力集合）

```text
Bounded Agent Runtime

ONE Agent
    ↓
LLM
    ↓
Tool Decision
    ↓
Policy（授权 / 预算 / approval）
    ↓
Existing Read-only Tool
    ↓
Tool Result
    ↓
LLM
    ↓
Final Answer
```

```text
能力（IN）：
  ✓ bounded loop                      ✓ max steps
  ✓ max tool calls / max llm calls    ✓ tool authorization
  ✓ tool failure boundary             ✓ LLM failure boundary
  ✓ cancellation state                ✓ approval state（WAITING_APPROVAL）
  ✓ runtime trace correlation         ✓ conversation context（untrusted）
  ✓ existing idempotency boundary（复用，不改）

不做（OUT）：
  ✗ write tools   ✗ multi-agent   ✗ planning graph   ✗ memory
  ✗ workflow      ✗ LangGraph     ✗ parallel tools   ✗ approval UI
```

---

## 22. Phase 5 架构图（正式）

```text
                         ┌────────────────────┐
                         │ ConversationRuntime│
                         └─────────┬──────────┘
                                   │
                                   ▼
                         ┌────────────────────┐
                         │   AIOrchestrator   │
                         └─────────┬──────────┘
                                   │
                             RouteDecision
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
              ▼                    ▼                    ▼
             RAG                  Tool             Text-to-SQL
                                   │
                                   │
                                   ▼
                         ┌────────────────────┐
                         │   AgentRuntime     │
                         │                    │
                         │  RuntimePolicy     │
                         │  Step State        │
                         │  Loop Controller   │
                         └─────────┬──────────┘
                                   │
                              LLM Decision
                                   │
                                   ▼
                         ┌────────────────────┐
                         │  Tool Authorization│
                         └─────────┬──────────┘
                                   │
                                   ▼
                         ┌────────────────────┐
                         │    Tool Registry   │
                         └─────────┬──────────┘
                                   │
                                   ▼
                         ┌────────────────────┐
                         │ ToolExecutionSvc   │
                         └─────────┬──────────┘
                                   │
                                   ▼
                              Tool Result
                                   │
                                   └──────────→ LLM
```

安全优先级（不变）：

```text
System / Safety > Capability / Business > Conversation Context > Current Question
```

---

## 23. Architecture Decisions（OD-P5-01 … OD-P5-13）

### OD-P5-01 Phase 5 Definition

```text
Decision      : Phase 5 = Bounded Agent Runtime（ONE Agent + Bounded Tool Loop + Runtime Policy）；
                非 Autonomous / 非 Multi-Agent / 非 LangGraph Platform。
Reason        : 现有多步能力缺口明确（无 Tool→LLM 回路、无步级预算/授权/状态），
                且既有基座（Orchestrator / Tool Registry / Capability / Observability / Conversation）
                足以承载"受约束的多步执行"。
Alternatives  : (a) 继续一次性 Tool（不做 Phase 5）；(b) 直接上 Multi-Agent；(c) 引入 LangGraph。
                (a) 无法满足多步业务问题；(b)(c) 超出当前需求与安全可控范围。
Consequences  : 需要新 Runtime 组件（policy / loop / state 语义），
                但不得新增写操作、不得改变既有 route 与 Conversation 契约。
```

### OD-P5-02 Agent vs Orchestrator Boundary

```text
Decision      : AgentRuntime 是 AI Capability（同级 RAG / Tool / Text-to-SQL），
                由 AIOrchestrator 通过新的 RouteDecision 目标（AGENT）触发；禁止递归编排。
Reason        : 能力校验 / trace / outcome / 观测必须单点拥有；
                双编排层会产生两套策略与两套 trace。
Alternatives  : (a) Agent 作为独立服务被 API 直接调用；(b) Agent 内含自己的 Orchestrator。
                (a) 绕过 Conversation 与 capability；(b) 递归编排，均否决。
Consequences  : 既有 route 行为不变；Agent 只新增一个 route 目标；
                未来 ORCHESTRATOR → AGENT → ORCHESTRATOR 结构在架构上被禁止。
```

### OD-P5-03 Self-built Runtime vs LangGraph

```text
Decision      : 自研最小 Bounded Agent Runtime；**不引入 LangGraph**（Phase 5）。
Reason        : 当前仅需 ONE Agent + Loop + Policy + Bounded；框架能力（复杂图 / checkpoint /
                HITL / 长任务）尚无需求；引入会扩大架构面与依赖面。
Alternatives  : LangGraph（state machine / checkpoint / human-in-loop / workflow）。
                —— 不是错的方案，而是**时机未到**。
Consequences  : loop / state / error / 预算契约需自研并冻结（本文件 §5–§9）；
                若 Phase 6 进入 Workflow Runtime，重新 Decision（并接受迁移成本）。
```

### OD-P5-04 Tool Loop Model

```text
Decision      : 单轮 0 or 1 logical tool decision；Decision ∈ {FINAL, TOOL_CALL}；
                第一版禁并行 Tool；失败不自动换模型 / 不重新规划 / 不无限 retry。
Reason        : 可审计性 / 可评估性 / 预算可计算性优先于吞吐；
                并行与自动恢复会引入不可控状态空间。
Alternatives  : (a) 一轮多 Tool；(b) 并行 Tool；(c) 自动 re-plan。
                全部推迟（需新 Decision）。
Consequences  : LLM 决策协议必须严格可解析；非法 decision = LLM failure（明确状态）。
```

### OD-P5-05 Runtime Policy

```text
Decision      : 必须存在五类上限：max_steps / max_tool_calls / max_llm_calls /
                max_execution_time_ms / max_cost（MVP 口径 = token 预算）。
                MVP 默认：steps = tool_calls = llm_calls = 8；time / cost 数值实现阶段冻结，
                但**缺省不得为无限**（fail-closed：任一 limit 缺失 ⇒ 不启动 Run）。
Reason        : 无限循环与成本失控是 Agent 的首要生产风险；
                现有观测只提供 token（无 cost 列 / 无 pricing 表）。
Alternatives  : 仅 max_steps；或仅 time-based；依赖 provider 自带配额。均不足以覆盖三类失控
                （步数 / 次数 / 成本）。
Consequences  : RuntimePolicy 为 trusted 不可变对象；
                货币化成本需要 pricing 来源 ⇒ 属实现阶段 Open Question（不在 MVP 硬造）。
```

### OD-P5-06 Agent State

```text
Decision      : 定义 AgentRun / AgentStep **语义**（§7），本阶段不建表、不持久化；
                禁止在 state 中保存完整 prompt / Tool Result 原文 / secret。
Reason        : 状态形态应在实现阶段依据观测与评估需求决定，避免过早 schema 冻结；
                数据最小化是硬要求（与既有"观测不存 prompt 全文"一致）。
Alternatives  : (a) 立即建 agent_run / agent_step 表；(b) 完全不定义 state。
                (a) 过早；(b) 无法表达预算与状态。→ 采用"语义冻结 + 形态后置"。
Consequences  : 实现阶段必须先做"state 持久化形态 Decision"（Open Question §24）。
```

### OD-P5-07 Tool Authorization

```text
Decision      : 保留 ProjectCapabilities.tool_names 作为**项目级能力边界**；
                新增**每步** Runtime 授权（allowed / denied / requires_approval），
                白名单为项目 tool_names 的**子集**；执行边界内仍由 ToolExecutionService
                做 capability 二次校验。
Reason        : LLM 不得自行授予权限；双层校验可防"项目已禁用但仍被调用"与"新 MCP/Tool 越权"。
Alternatives  : 仅保留项目级白名单（无法表达"本步不允许"）；或让 LLM 自选工具集（不可接受）。
Consequences  : 授权决策必须可观测（policy 步 / 拒绝原因）；拒绝不得执行、不得泄漏 Tool 细节。
```

### OD-P5-08 Write Tool Policy

```text
Decision      : Phase 5 第一阶段**只允许 READ-only Tool**；写操作需另立 Decision，
                且必须同时具备 Approval / Authorization / Idempotency / Audit / Compensation。
Reason        : 写操作不可逆且影响生产数据；当前 Conversation 幂等只覆盖"消息级"，
                不覆盖"工具副作用级"。
Alternatives  : 允许写 Tool + 事后审计（不可接受）；用 Idempotency-Key 直接覆盖写操作（不足）。
Consequences  : Agent 在架构上只能观察、不能改变生产数据；写路径的接入点是未来独立阶段。
```

### OD-P5-09 Human Approval Boundary

```text
Decision      : 预留 WAITING_APPROVAL 状态语义（requires_approval=true ⇒ 停止，不继续执行）；
                第一阶段**不实现审批 UI / 不实现恢复执行协议**。
Reason        : 安全设计需要接入点，但当前所有 Tool 均只读（requires_approval 恒 false），
                审批中心属独立产品能力。
Alternatives  : 不预留状态（未来写操作需改动状态机）；立即实现审批中心（超出范围）。
Consequences  : 状态机包含 WAITING_APPROVAL；恢复执行的协议（如何 approve / 如何继续）留待未来 Decision。
```

### OD-P5-10 Conversation Integration

```text
Decision      : Agent Runtime 运行在 Conversation Runtime **之上**：
                Conversation（Turn / Idempotency / Context）仍由 ChatApplicationService 拥有；
                Agent 不创建 Conversation / Turn，不处理 Idempotency-Key；
                conversation_context 只作 untrusted reference（优先级不变）。
Reason        : Phase 4.2 已冻结该分层与安全优先级；Agent 属"AI execution 内部实现"。
Alternatives  : Agent 自建会话（重复实现幂等与上下文，不可接受）；
                Agent 直接读 DB 历史（破坏依赖方向，不可接受）。
Consequences  : duplicate ⇒ replay 不进入 Agent；retry 复用既有 USER Turn；
                Agent 的 LLM 轮次消费 context 的语义需在实现阶段明确定义并测试。
```

### OD-P5-11 Evidence Boundary

```text
Decision      : Evidence Contract（Phase 4.1）**不变**；本阶段只定义未来接口
                Agent Step → Tool Result → Evidence Candidate；
                Tool Result 无 provenance ⇒ 只能作为 runtime observation；
                Runtime Evidence 保持 DEFERRED；OD-31 / OD-32 保持 OPEN。
Reason        : Evidence = Source / Provenance Unit；Tool Result 是运行时产物，
                二者混合会污染 provenance 与 dataset_version 语义。
Alternatives  : Tool Result 直接写 Evidence（否决）；Phase 5 顺带实现 Runtime Evidence（越界）。
Consequences  : Agent 步级证据归属问题保持"未解决但已界定"，属于未来独立 Decision。
```

### OD-P5-12 Observability Boundary

```text
Decision      : 定义 AgentRun → AgentStep → {LLM, Tool, Policy} 的父子关联语义；
                本阶段**不新增观测表**；实现阶段优先"扩展现有记录 + Run 级汇总"，
                关联键第一顺位 = request_id（已有），run_id 形态实现阶段决策。
Reason        : 现有 4 张观测表已提供 request_id / round / tool_call_id / token / 时序，
                足以承载扩展；新增大量表会重复既有能力。
Alternatives  : 立即新增 agent_run / agent_step 观测表（过早）；
                复用 Tool 记录但不区分 Agent 轮次（无法回答"为什么停"）。
Consequences  : 实现阶段必须先回答：run 级汇总落在 Observation 还是 State（Open Question §24）。
```

### OD-P5-13 Evaluation Boundary

```text
Decision      : 冻结 Phase 5 必须可评估的最小集合（§17），全部离线可复现（Fake LLM / Fake Tool）；
                本阶段不建 benchmark、不改既有 baseline。
Reason        : 多步 runtime 的回归价值取决于"预算 / 拒绝 / 失败 / 终止"是否可断言；
                离线确定性是既有 Phase 4.2 评估原则。
Alternatives  : 依赖真实 LLM 评估（不可复现、成本高、不稳定）。
Consequences  : 实现阶段每个能力点必须有对应离线用例；评估不得成为 baseline 漂移来源。
```

---

## 24. Open Questions（不阻塞本 Decision，必须在下阶段开工前关闭）

```text
OQ-P5-1  LLM 工具决策协议：function calling（provider 级）与结构化 JSON 输出，二选一及其校验契约
         （现状：LLMProvider 只有 generate / chat；Tool 选择由规则匹配完成 —— 不是 function calling）
OQ-P5-2  Agent State 持久化形态：是否持久化 AgentRun / AgentStep；若持久化，落 State 表还是观测表
OQ-P5-3  max_execution_time_ms / max_total_tokens 的具体默认值
OQ-P5-4  max_cost 货币化所需 pricing 来源（或确认 MVP 长期只用 token 预算）
OQ-P5-5  Agent 的 AssistantOutcome 映射（LIMIT_REACHED / CANCELLED / WAITING_APPROVAL 如何表达）
OQ-P5-6  Agent Run 的取消入口（CANCELLED 由谁触发：客户端 / 超时 / 服务端策略）
OQ-P5-7  RouteType 扩展方式（新增 AGENT 值与既有 tool 路由的判定优先级）
OQ-P5-8  Agent LLM 轮次与 conversation_context 的消费语义（是否每轮注入、注入位置与预算）
OQ-P5-9  重复相同 Tool 调用（loop 抖动）的判定与处理（计入预算即可，还是额外检测）
OQ-P5-10 Agent 观测的 run 级汇总位置（Observation vs State），及与 Assistant Timeline 的关系
```

---

## 25. 与既有 Open Decisions 的关系（不关闭、不覆盖）

```text
OD-22 / OD-23 / OD-30 / OD-31 / OD-32 / OD-33 / OD-35 / OD-38 / OD-39 = 保持原状态（OPEN）
OD-31 / OD-32（Runtime Evidence）—— 不因 Phase 5 关闭
OD-35（Query Understanding）—— 与 Phase 5 解耦，可独立立项
KL-1（AI exactly-once NOT GUARANTEED）· KL-2（duplicate 不重放完整 AI Result）—— 仍然成立，
      且 Agent 的"步级副作用"精度**不得**被用来声称解决了 KL-1
```

---

## 26. Phase 5 后续实施顺序（仅建议，**本阶段未授权**）

```text
建议（描述性命名，不代表已批准的子步骤编号）：
  1) Runtime Policy & State Contract（五类上限 + Run/Step 语义 + 状态机冻结）
  2) LLM Tool-Decision Protocol（OQ-P5-1 关闭；离线可复现）
  3) Tool Loop（0/1 decision · Policy Check · 只读 Tool · 失败边界）
  4) Observability & Evaluation（父子关联 + 最小评估集合）
  5) Conversation Integration（route 接线 + context 消费 + 幂等复用回归）

约束：以上每一项在开工前必须由用户明确授权；
      不得在 Phase 5 内引入写操作、Multi-Agent、Memory、Workflow、LangGraph。
```

---

## 27. 冻结声明

```text
本文件冻结：
  Phase 5 定义 · Agent/Orchestrator 边界 · 单 Agent · Tool Loop 模型 ·
  Runtime Policy（五类上限 + 不变量）· Agent State 语义 · Run 状态机 ·
  Error Recovery 边界 · Tool Authorization 双层模型 · 只读 Tool 政策 ·
  WAITING_APPROVAL 预留 · Conversation 集成方式 · Context 安全优先级 ·
  Evidence 边界 · Observability 关联语义 · Evaluation 最小集合 ·
  自研 Runtime（不用 LangGraph）· 非目标清单

产生副作用：无（Production code = 0 · DB = 0 · API = 0 · Prompt = 0）
```

---

# 28. OQ-P5-1 Decision — LLM Tool Decision Protocol

> **本节为追加修订（append-only）**：不修改 §1–§27 的任何历史决策文字。
> 触发：Phase 5 Core Loop MVP 完成后，`AgentLLM` 仍是内部 seam，尚未正式接入 `LLMProvider`。
> 唯一问题：**Agent Runtime 应该如何从现有 LLM Provider 获得稳定、严格、可验证的 `FINAL / TOOL_CALL` Decision。**
> 本阶段只做 Architecture Decision：`Production code = 0 · DB = 0 · API = 0 · Prompt = 0 · 真实 LLM = 0`。

## 28.1 事实基线（以代码实测为准，非推断）

| 事实 | 真实情况 | 证据 |
| ---- | -------- | ---- |
| Provider 接口 | `LLMProvider.generate(prompt) -> str`；`chat(messages, *, tools=None) -> str \| LLMResponse` | `llm/provider.py` L46–81 |
| Provider 实现 | `DeepSeekProvider` = **纯 delegation**（参数原样透传、结果原样转发、无重试/fallback） | `llm/deepseek_provider.py` L43–63 |
| tools 通道 | **已实现**：`_build_payload` 在 `tools` 非空时写入 `payload["tools"]` | `llm/client.py` L427–440 |
| 单 Tool 约束 | **已强制**：一次响应 >1 个 tool call ⇒ `LLMToolCallFormatError`（"本阶段仅支持单个"） | `llm/client.py` L495–511、L164–171 |
| ToolCall DTO | `ToolCall{id, name, arguments: dict}`（id/name 非空校验；arguments 必须是 dict） | `llm/client.py` L178–200 |
| arguments 解析 | JSON 字符串 → dict；空字符串 → `{}`；非法 JSON / 非 object ⇒ 报错（不修复） | `llm/client.py` L495–535；`tests/test_llm_tool_calling.py` |
| `LLMResponse` | `content` / `tool_calls` / `model` / `finish_reason` / `usage` / `metadata` | `llm/client.py` L244–271 |
| usage | `LLMUsage{prompt,completion,total}`；允许 `None`（不虚构）；负数/不一致 ⇒ 拒绝 | `llm/client.py` L203–241 |
| **无 tools 时** | 返回 **纯 `str`**（无 usage / 无 finish_reason）——`tools` 为空或 `[]` 时等同无 tools | `llm/client.py` L288、`tests/test_llm_tool_calling.py::test_chat_without_tools_returns_str_and_payload_has_no_tools` |
| JSON mode | **不存在** `response_format` / `json_object`（全仓 0 命中）⇒ 本仓库**没有** JSON-mode 能力 | `llm/client.py` 全文件检索 |
| Tool schema 转换 | **已存在**：`definitions_to_openai_tools()`（深拷贝；只暴露 name/description/parameters；不含 Handler） | `llm/tool_schema.py` L35–72 |
| 通用结构化解析 | **已存在**：`parse_structured_response(raw, PydanticModel)`（严格、`extra=forbid`、reason 契约、message 不含原文；接受**完整** ```json fence；未接生产路径） | `llm/structured.py` L97–237 |
| Retry | `llm/retry.py` = **仅分类，不重试**（transport 分类交由 Client 层；无自动重试/退避） | `llm/retry.py` |
| 既有生产者 | `tool_chat_service.py` **已经在生产使用** `chat(messages, tools=definitions_to_openai_tools(...))` | `tool_chat_service.py` L405、L424 |
| Agent 侧现状 | `AgentLLM.complete(messages) -> AgentLLMCompletion{text,total_tokens}`（文本 seam）；`parse_agent_decision(raw)` 严格 bare-JSON（拒绝 fence / 自然语言 / 跨类型字段）；`AgentDecision` 已冻结；Runtime 已有 5 类预算 + 双层授权 + 错误码 | `backend/app/agent/*`、`tests/test_agent_runtime.py`（57 passed） |

**结论（事实层）**：Provider **已经**提供 provider-native tool calling（且已被生产路径使用、被单测冻结），但**不提供** JSON mode；无 tools 的文本路径**不返回 usage**。

## 28.2 责任划分（本决策的核心三问）

```text
问 1：哪一层负责把 LLM 输出约束成 AgentDecision？
答 1：**Agent 层适配器**（AgentLLM 端口的实现）。
      LLMProvider 只做通用 transport/response 抽象（content / tool_calls /
      usage / finish_reason 的严格解析）；**不得**让 Provider 认识
      AgentDecision / RuntimePolicy / Tool Authorization。

问 2：哪一层负责把 Agent 可用 Tool 描述提供给 LLM？
答 2：**Agent 层**：授权子集（allowed_tools ∩ ProjectCapabilities.tool_names）
      → 既有 `definitions_to_openai_tools()` → `tools=` 参数。

问 3：哪一层负责最终验证 LLM 返回的 Tool Name + Arguments？
答 3：**Runtime 层**：① 授权白名单（TOOL_NOT_ALLOWED）
      ② `ToolExecutionService` 第二道 capability 校验
      ③ `ToolRegistry.validate_arguments()` 的真实 schema 校验。
      Provider 不做业务权限，Handler 不暴露给 LLM。
```

## 28.3 方案对比（Option A / B / C；文字评估，不做数字评分）

| 维度 | Option A：Structured JSON Decision | Option B：Provider-native Function Calling | Option C：Hybrid（两套并存） |
| ---- | ---------------------------------- | ------------------------------------------ | ---------------------------- |
| Provider Contract 改动 | 无 | **无**（`tools=` 通道已存在并已被生产使用） | 无 |
| Agent Runtime 独立性 | 高（文本进、JSON 出） | 中（需 OpenAI 风格 message 语义：assistant.tool_calls + role=tool 回填） | 高 |
| Tool Schema | 自行注入 prompt（MVP 已实现 `_tool_definitions_payload`） | **复用** `llm/tool_schema.py`（标准化、深拷贝、零 Handler 泄漏） | 两者 |
| Tool Authorization | Runtime（不变） | Runtime（不变） | Runtime |
| DeepSeek 兼容性 | 依赖模型 JSON 遵循；**无 response_format 硬保证**（仓库无 JSON mode） | 通道在生产已被使用 + 单测冻结；**live API 行为本阶段 UNVERIFIED** | 两者 |
| 严格解析 | 高（自有严格 parser；bare JSON only） | 高（Provider 已严格解析 id/name/arguments/单调用；Agent 层再做 FINAL/TOOL_CALL 判定） | 高 |
| 多 Provider 未来扩展 | 强（任何能输出 JSON 的 provider） | 中（需 provider 支持 OpenAI tools 语义 —— 主流兼容端点均支持） | 强 |
| 实现复杂度 | 低（已完成） | 中（adapter + message 语义 + tool_call_id 回填） | 高（两套协议，**本阶段明确禁止**） |
| 安全边界 | 好；**但 token 预算事实上不可验证**（无 usage ⇒ `max_cost_tokens` 无法执行） | 好；**usage 可得 ⇒ 冻结的 `max_cost_tokens` 可真正执行**；单 Tool 约束原生满足 | — |
| 当前项目适配度 | 高（Core Loop 已按此实现） | 高（ToolChatService 已用同一通道；schema 转换器已存在） | 低 |

**Option A 的实测缺陷（决定性证据）**：`chat(messages)` 返回 `str` ⇒ **无 usage** ⇒ Phase 5 §6 冻结的 `max_cost_tokens` 退化为"永远 `token_budget_unverifiable`"，即一条**已经冻结的安全预算形同虚设**。
**Option B 的实测代价**：Agent 层需按 OpenAI 风格构造/回填 `assistant.tool_calls` + `role="tool"` 消息（多一个 `tool_call_id` 回填约束）。
**Option C**：§六 已明确禁止"为兼容未来现在实现两套"。

## 28.4 Decision（Selected Architecture）

```text
Selected Option : **Option B —— Provider-native Function Calling**
Decision        : Agent Runtime 通过 **Agent 侧适配器** 调用既有
                  `LLMProvider.chat(messages, tools=<已授权 Tool 的 OpenAI schema>)`，
                  由适配器把 `LLMResponse` **严格映射**为 `AgentDecision`；
                  Provider contract **不修改**、Provider **不认识** Agent 概念。
```

Decision Protocol（冻结）：

```text
provider 返回（tools 路径，恒为 LLMResponse）
  ├── tool_calls 非空（Provider 已强制 ≤1）
  │        → AgentDecision(type=TOOL_CALL,
  │                        tool_name=tool_calls[0].name,
  │                        arguments=tool_calls[0].arguments,
  │                        tool_call_id=tool_calls[0].id)
  │        显式规则：伴随的 content **被丢弃**（不用于 FINAL、不进入历史）
  │        —— 理由：动作请求明确（tool call 是显式动作），
  │           丢弃杂散文本不会改变授权/预算/安全边界；**不猜测**语义
  │
  └── tool_calls 为空
           ├── finish_reason == "length" → INVALID_DECISION（截断 ⇒ 不可信）
           ├── content 非空（strip 后）  → AgentDecision(type=FINAL, content=content)
           └── content None / 空白       → INVALID_DECISION（不伪造空回答）

provider / 客户端异常映射（不新增错误体系）
  LLMToolCallFormatError · LLMResponseError · LLMError 家族 → LLM_FAILURE
```

Why（依据 §28.1 实测事实）：

```text
1) Provider contract 零改动（`tools=` 已实现、已被 ToolChatService 生产使用、已被单测冻结）；
2) **恢复 token 预算可执行性**：tools 路径返回 LLMResponse.usage，
   Phase 5 §6 冻结的 max_cost_tokens 从"不可验证"变为"可执行"；
3) 单 Tool 约束由既有契约原生保证（>1 tool call 直接报错）——
   与 §5.2「每轮 0 or 1 Tool 决策」天然一致；
4) Tool schema 复用既有 `definitions_to_openai_tools()`（结构化暴露，而非拼文本），
   减少"schema 漂移"风险；
5) Provider 保持通用（content/tool_calls/usage/finish_reason），
   Agent-specific 解释全部留在 Agent 层（满足 §16 Provider Contract 保护）；
6) finish_reason 提供额外可判定信号（stop / length / tool_calls）。
```

Trade-offs（明确承认）：

```text
* Agent 层必须构造 OpenAI 风格消息（assistant.tool_calls + role="tool" 回填 tool_call_id）；
* Tool Result 回填语义从"user 消息文本块"变为"role=tool 数据块"，
  但**仍为 untrusted data**（标记保留在 content 内，安全优先级不变）；
* 若未来必须支持"不支持 tools 语义"的 provider，需要额外 fallback 适配器
  （见 §28.13 Migration Path；**不在本阶段实现**）；
* live DeepSeek 行为本阶段无法验证（UNVERIFIED，见 §28.11）。
```

## 28.5 Decision Schema（冻结）

```text
FINAL
  { "type": "FINAL", "content": "<非空最终回答>" }
  * content：Required，非空（strip 后非空）；空字符串 / None ⇒ INVALID_DECISION
    （与 Core Loop MVP 现行规则完全一致，不放松）
  * 不得携带 tool_name / arguments（跨类型字段 ⇒ INVALID_DECISION）

TOOL_CALL
  { "type": "TOOL_CALL", "tool_name": "<已授权 Tool 名>",
    "arguments": { ... }, "tool_call_id": "<provider 提供的 id>" }
  * tool_name：Required，非空 str；必须 ∈ Runtime 授权白名单，否则 TOOL_NOT_ALLOWED
  * arguments：Required，JSON object（允许空对象 {}）；
    缺失 / null / 非 object ⇒ INVALID_DECISION；
    允许嵌套任意 JSON（结构合法性由 ToolRegistry schema 终验）；
    未知字段 / 类型不符 ⇒ TOOL_EXECUTION_FAILURE（由真实 Registry 拒绝）
  * tool_call_id：**Required（provider 路径）**
      —— 由 Provider 的 ToolCall.id 原样携带（Provider 已校验非空），
         用途唯一：回填 role="tool" 消息（协议要求）；
         Agent 层**不生成 / 不替换 / 不派发**该 ID。
      ——（未来文本 fallback 协议中为 Optional/忽略：无协议回填需求。）
  * 不得携带 content（跨类型字段 ⇒ INVALID_DECISION）
```

## 28.6 Provider Boundary

```text
LLMProvider = 通用 LLM transport / response 抽象（**不变**）
    * 知道：messages / tools schema / content / tool_calls / usage / finish_reason
    * 不知道：AgentDecision · RuntimePolicy · Tool Authorization · Project Permission
              · Conversation · 预算 · 授权白名单
禁止：把 AgentDecision / RuntimePolicy / Authorization 塞进 Provider；
      Provider 不得 import backend.app.agent（方向：Agent → Provider，单向）。
```

## 28.7 Tool Schema Boundary

```text
Project Capabilities（ProjectCapabilities.tool_names）
        ↓ 交集
Agent Runtime 授权白名单（allowed_tools）
        ↓ definitions_to_openai_tools(ToolDefinition)
tools=[ ... ]（只含 name / description / parameters）
        ↓
LLM Decision
```

```text
硬规则：
1) 只有**已授权**子集进入 tools=（不暴露项目未启用 / Agent 未授权的 Tool）；
2) 暴露的 schema 只来自 ToolDefinition 三字段（既有转换器已保证零 Handler 泄漏 / 深拷贝）；
3) LLM 看到 Tool ≠ LLM 拥有 Tool 权限 —— 权限仍在 Runtime（§28.8）。
```

## 28.8 Authorization Boundary

```text
LLM 输出 tool_name
        ↓ ① Runtime 授权白名单（allowed_tools ⊆ ProjectCapabilities.tool_names）
        ↓ ② ToolExecutionService（第二道 capability 校验）
        ↓ ③ ToolRegistry.validate_arguments()（真实 schema 终验）
        ↓ ④ Handler
```

```text
不变式：LLM 返回 delete_inventory（未授权）⇒ TOOL_NOT_ALLOWED，**不执行**；
       LLM 伪造 arguments 未知字段 ⇒ Registry 拒绝 ⇒ TOOL_EXECUTION_FAILURE；
       Handler 不直接暴露给 LLM（Agent 层不得持有 / 调用 Handler）。
```

## 28.9 Error Boundary（复用既有错误码，不新建体系）

| 情形 | 分类 | Agent 错误码 | 结果 |
| ---- | ---- | ------------ | ---- |
| provider 抛异常 / `LLMError` 家族（含 `LLMToolCallFormatError`、`LLMResponseError`、非 str content） | LLM failure | `LLM_FAILURE` | FAILED |
| `finish_reason == "length"` | Decision parse failure | `INVALID_DECISION` | FAILED |
| content 为空 / 缺失且无 tool_calls | Decision parse failure | `INVALID_DECISION` | FAILED |
| 跨类型字段（FINAL+tool_name / TOOL_CALL+content） | Decision parse failure | `INVALID_DECISION` | FAILED |
| 文本 fallback 协议的非法 JSON / 未知 type / 缺字段 / 类型错 | Decision parse failure | `INVALID_DECISION` | FAILED |
| tool_name 不在授权白名单 | Authorization failure | `TOOL_NOT_ALLOWED` | FAILED（Tool 0 次执行） |
| arguments 未通过 Registry schema / Handler 失败 / 结果非 JSON-compatible | Tool argument / execution failure | `TOOL_EXECUTION_FAILURE` | FAILED |
| 任一预算触限 | Runtime limit | `LIMIT_REACHED` | LIMIT_REACHED |

```text
说明：Provider 的解析异常归 LLM_FAILURE（不是 INVALID_DECISION）——
      "provider 无法给出可用响应" 与 "模型给出了非法决策" 是两种不同的失败面。
```

## 28.10 Retry Boundary

```text
不增加任何 Decision 级自动重试：
  invalid JSON / invalid decision  → FAILED（**不**重调 LLM）
  unknown tool                     → TOOL_NOT_ALLOWED（**不**让 LLM 重猜）
  invalid arguments                → TOOL_EXECUTION_FAILURE（**不**自动修正）
  Tool 执行失败                     → FAILED（**不**重试 Tool）
transport 级分类（llm/retry.py：429 / 5xx / 网络）保持**仅分类**语义，本决策不改动其行为。
```

## 28.11 DeepSeek Compatibility

```text
VERIFIED（本仓库内，离线实证）：
  * tools 通道：`payload["tools"]` 由 OpenAICompatibleClient 发送（client.py L438）；
  * tool_calls 严格解析：id / name 必填；arguments 必须 JSON object；
    单响应 >1 tool call 直接拒绝（LLMToolCallFormatError）；
  * content 非 str ⇒ LLMResponseError；
  * usage / model / finish_reason 进入 LLMResponse；
  * 生产消费者：tool_chat_service.py 已使用同一通道（L405/L424）。

UNVERIFIED（本阶段禁止真实调用，不得假设）：
  * live DeepSeek API 对 `tools=` 的实际行为（是否会返回 tool_calls、
    是否 stable、模型选择对 function calling 的支持度）——**未验证**；
  * 本仓库**没有** response_format / JSON mode 支持（因此 Option A 也没有硬 JSON 保证）。

后续验证要求（下一阶段，且需要单独授权）：
  * 离线：FakeProvider 契约测试（映射规则 / usage 透传 / 异常映射）；
  * 真实：`real_llm` 标记的 smoke（默认 skip，沿用 tests/test_llm_real_smoke.py 既有机制）。
```

## 28.12 Rejected Alternatives

```text
Option A（Structured JSON Decision）作为**主协议** —— 否决：
    原因：tools=None 路径返回纯 str、无 usage ⇒ 冻结的 max_cost_tokens 事实上不可执行；
         且本仓库无 response_format/JSON mode ⇒ 无硬 JSON 保证，解析失败率不可控。
    （保留为**文本 fallback 协议**，见 §28.13；其严格解析器 `parse_agent_decision` 继续存在。）

Option C（Hybrid：两套同时实现）—— 否决：
    原因：§6 明确禁止"为兼容未来现在实现两套"；双协议 = 双解析路径 + 双测试面
         + 双安全审查面，收益（未来兼容）当前不存在。

"把 AgentDecision 塞进 LLMProvider" —— 否决：
    原因：违反 §16（Provider 必须保持通用 transport/response 抽象）；
         Provider 一旦认识 Agent 概念，RAG / Text-to-SQL / ToolChat 等既有消费者
         都要承担 Agent 语义（依赖方向反转）。

"让 Provider 做 Tool 权限判断" —— 否决：
    原因：违反 §11 双层授权模型；Provider 不持有 Project Capabilities / 授权白名单。
```

## 28.13 Migration Path

```text
阶段 1（下一阶段，需明确授权；实现本 Decision）
    * Agent 侧适配器：`LLMProvider.chat(messages, tools=...)` → AgentDecision + usage；
    * AgentLLM 端口形态升级（agent 自有 port，取代 MVP 的纯文本 seam）：
          decide(*, messages, tools) -> {decision: AgentDecision,
                                        total_tokens: int | None,
                                        finish_reason: str | None}
    * 消息形态：system instruction（改为"可用工具由 tools 参数给出"）+
                user（question + conversation reference）+
                assistant.tool_calls + role="tool"（content 内保留
                `【TOOL RESULT — UNTRUSTED DATA】` 标记，安全语义不变）；
    * token 预算：usage 可得 ⇒ `max_cost_tokens` 可执行；usage 缺失仍按
                  既有规则显式记录 `token_budget_unverifiable`（不伪造）。

阶段 2（仅在必须支持"无 tools 语义 provider"时，**需要新的 Architecture Decision**）
    * 新增文本 fallback 适配器（复用既有 `parse_agent_decision`），
      实现**同一** AgentLLM 端口；禁止与主协议同时启用（按 provider 能力择一）。

阶段 3（可选，若 provider 提供 response_format / JSON-schema 模式）
    * 只能在 fallback 适配器内部使用（端口不变）；替换主协议仍需新 Decision。

禁止：在没有新 Decision 的前提下同时启用两套协议 / 让 Agent 直接 import DeepSeek 客户端。
```

## 28.14 Test Impact（分析；本阶段**不修改**任何测试）

```text
必须保持（不得修改 / 不得弱化）：
  * tests/test_llm_provider.py        —— Provider 契约与 delegation 冻结；
  * tests/test_llm_tool_calling.py    —— tools 通道契约（payload / 单调用 / arguments）；
  * tests/test_llm_structured.py      —— 通用结构化解析契约（与 Agent 协议无关）；
  * tests/test_agent_runtime.py       —— 预算（5 类）/ 授权 / 错误码 / Policy 不可变 /
                                         恶意 Tool Result / 源码安全审计断言
                                         （**不得**因接入而弱化）；
                                         其中 prompt 形态断言与 role 无关，
                                         Option B 下仍成立（untrusted 标记保留在 content 内）。

下一阶段需要适配（仅实现层 seam 形态，不改变语义）：
  * test_agent_runtime.py 中依赖"文本 seam"的 FakeLLM（complete() 返回文本）→ 改为
    返回 decision + usage 的 Fake（断言矩阵不变）。

下一阶段必须新增（不得以"已有 Core Loop 测试"替代）：
  * tests/test_agent_llm_adapter.py（建议名）：
      - LLMResponse(tool_calls=1) → AgentDecision(TOOL_CALL)（字段逐一对应）
      - LLMResponse(tool_calls=0, content=...) → FINAL
      - content 空白 / None → INVALID_DECISION
      - finish_reason="length" → INVALID_DECISION
      - content + tool_calls 同时存在 → TOOL_CALL 且 content 被丢弃（显式规则）
      - usage 透传（total_tokens）/ usage 缺失 → token_budget_unverifiable
      - LLMToolCallFormatError / LLMResponseError → LLM_FAILURE
      - tools= 只含授权子集（未授权 Tool 不出现在 schema）
      - role="tool" 回填含 tool_call_id 与 untrusted 标记
      - Provider 侧源码审计：backend/app/llm 不 import backend.app.agent
```

## 28.15 Target Architecture（本 Decision 冻结后的形态）

```text
AgentRuntime                     ← Agent-specific：Decision / Policy / Authorization / Loop
      │
      │ AgentDecision（FINAL | TOOL_CALL{name, arguments, tool_call_id}）
      ▼
AgentLLM（agent-owned port；适配器实现）
      │
      ▼
LLMResponse（content / tool_calls / usage / finish_reason / metadata）
      │
      ▼
LLMProvider（通用抽象，**不知道 Agent**）
      │
      ▼
DeepSeekProvider（纯 delegation）
      │
      ▼
OpenAI-compatible HTTP（tools=[...]）
```

```text
Provider 不知道：Agent Policy · Tool Authorization · Conversation · Project Permission
AgentRuntime 负责：Decision · Policy · Authorization · Loop · Tool Result
```

## 28.16 OQ-P5-1 Status

```text
OQ-P5-1 = **CLOSED**

冻结内容：
  Selected Option（B，provider-native function calling）
  · Decision Protocol（映射规则 / content 丢弃规则 / finish_reason=length 规则）
  · Decision Schema（FINAL / TOOL_CALL / tool_call_id 归属）
  · Provider Boundary · Tool Schema Boundary · Authorization Boundary
  · Error Boundary（复用既有错误码）· Retry Boundary（无 Decision 级重试）
  · DeepSeek Compatibility（VERIFIED / UNVERIFIED 清单）
  · Migration Path（阶段 1/2/3 + 禁止事项）· Rejected Alternatives

未改变：Phase 5 §1–§27（含 §5 Tool Loop、§6 Policy、§9 Error Recovery、
        §OD-P5-02/03/04/07/12）——本决策是对 §5.3 遗留问题（LLM 工具决策协议）的关闭。
实现状态：仍为 **NOT IMPLEMENTED**（适配器实现属下一阶段，需明确授权）。
```
