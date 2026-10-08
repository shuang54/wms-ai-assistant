# Phase 6 — Workflow Runtime Architecture Discovery

> Architecture Status: **FROZEN**（Phase 6.0 边界冻结 —— 含明确 Deferred Decisions）
> 性质：Architecture Discovery + Boundary Freeze（**非** Coding Phase；本阶段零代码变更）
> 勘察基线：分支 `phase5-1` · HEAD `a50869c` · 工作区含未提交的 Phase 6 资产（见 §0）
> 依据：Phase 5 Frozen / Exit Audit · `Phase 6 — Workflow Runtime Architecture Decision.md`（ACCEPTED）·
> Phase 6 Step 1 Domain（已实现：`backend/app/workflow/**` + 4 测试文件 / 186 tests）

---

## 0. 与既有资产的关系（先读）

本文件是 Phase 6 的**完整勘察与边界冻结记录**。三层既有资产的关系：

```text
1) docs/decisions/Phase/Phase 6 — Workflow Runtime Architecture Decision.md
   = 核心决策（D1–D17 + OD-1）——**ACCEPTED**；本文件引用、不重开；
2) backend/app/workflow/**（7 源文件）+ tests/test_workflow_*.py（4 文件 / 186 tests）
   = Step 1 Domain —— 已实现并通过全量回归（6888 passed / 0 failed）；
3) 本文件 = Phase 6.0 Discovery：
   对 14 个架构问题、4 张矩阵、3 个 WMS 示例的完整勘察，
   并把「已 ACCEPTED 决策 + 已实现 Domain」冻结为 Phase 6.1 的实施边界。
```

术语映射（**冻结词汇表** —— ADR-01 D17；本文件遵守）：

```text
任务书术语           本文件 / 已实现术语            说明
"Workflow Step"  →  WorkflowNode（node_id）        "step" 一词保留给 AgentStep
"workflow_step"  →  workflow_node_run              表名（node_id + attempt 身份）
"WorkflowCheckpoint" → 概念存在（Recovery Control  承载于 workflow_run 行
                        State），**无独立表**        （state_version），见 §9
```

文件命名更正（实测，任务书内两处路径不存在）：
`conversation_runtime_service.py` / `conversation_context_service.py` →
真实文件 = `conversation_service.py` / `conversation_context_selection_service.py` /
`conversation_context_builder.py` / `chat_application_service.py`。

---

## 1. Context

```text
User → FastAPI（11 router）
     → AIOrchestratorService.execute（单请求·单路由·单次执行）
     → AIRouterService（RAG | TOOL | TEXT_TO_SQL | AGENT）
     → RAG / ToolExecutionService / TextToSQL / AgentRuntime（Phase 5 FROZEN）
     → PostgreSQL（ai_ops 16 表 + public 业务表）
```

Phase 5 已冻结 Agent Runtime（模型驱动：LLM decides → Tool → … → FINAL）。
Phase 6 要引入的是**开发者定义**的确定性执行层（Workflow Runtime），
两者是 **Sibling Runtimes**：互不包含、只经 Service API 交互。

## 2. Phase 5 Frozen Boundary

已冻结（不得修改）：Agent Loop · Decision Contract · Tool Authorization ·
Tool Execution 边界 · 六张 Agent 表 · Replay / Resume · HITL · Cancellation ·
Agent Read API · Agent Timeline · 错误码 · SoT 关系。

Phase 6 调用 Agent 的**唯一**合法路径：

```text
AIOrchestratorService.execute()
AIOrchestratorService.resume_agent_run()
AIOrchestratorService.resolve_agent_approval()
AIOrchestratorService.cancel_agent_run()
```

禁止：Workflow → `AgentRuntime._run_scoped()` / Agent repositories / 六表任意写。

## 3. Phase 6 Problem

```text
需要一条"开发者定义"的确定性控制流：
    sequential · condition/branch · approval gate · retry policy ·
    long-running（durable）· （parallel / scheduled 见 Deferred）
且必须：
    - 与 Agent Runtime 严格分层（Sibling）；
    - 不修改任何 Phase 5 冻结契约；
    - 不引入 Redis / Celery / Kafka / Temporal / LangGraph / MCP。
```

## 4. Workflow vs Agent（+ Phase 5 vs Phase 6 Boundary Matrix）

```text
Phase 5（模型驱动）:              Phase 6（开发者定义）:
LLM decides next step             Definition decides next step
one tool per round                node graph（顺序 + 条件）
bounded loop（8/8/8 + time/token）bounded run（nodes / attempts 预算）
HITL = Tool Approval              HITL = Workflow Approval Gate（§13）
Resume = Replay 已持久化决策       Resume = 从持久化 run 状态继续
```

**Matrix 2 — Phase 5 vs Phase 6 Boundary Matrix**

| 能力 | Phase 5 Agent | Phase 6 Workflow |
| --- | --- | --- |
| 下一步由谁决定 | LLM | Workflow Definition |
| Tool Loop | Agent 拥有 | Workflow **不拥有**（无 loop；单次 node） |
| Tool Authorization | Agent（`allowed_tools` 三重校验） | Workflow tool node 复用同一 `ToolExecutionService` 白名单边界 |
| Tool Replay | Agent（Decision + Tool Result Replay） | Workflow 自己定义 node attempt 语义（不重放 Agent 内部） |
| Retry | **无**（fail-closed；Agent semantic retry 不存在） | Workflow policy（节点级、immediate、fail-before-retry） |
| Parallel | ❌（每轮 0/1 tool） | Workflow 拥有（**Phase 6 Deferred**，§15 设计已冻结） |
| Condition | LLM decision | Workflow（受控断言，无 eval） |
| HITL Tool Approval | Agent（执行前暂停，重新授权） | Workflow Approval Gate（独立表 / 独立状态机） |
| Long-running | Agent Resume（外部调用驱动） | Workflow durable execution（同驱动模型，run 状态在 DB） |
| Timeline | Agent Timeline（FROZEN，派生投影） | Workflow Timeline（独立派生投影；不改 Agent 的） |
| Conversation | Conversation Runtime（独立域） | Optional（Workflow 可脱离 Conversation，§16） |
| Scheduler | ❌ | 外部触发器（**不在本 repo**；§18） |
| Multi-Agent | ❌ | Future（不在 Phase 6） |

## 5. Workflow Definition（Q1）

**Definition = 静态结构（code-defined + registry + version），不含任何执行状态。**

```text
包含（MVP，已实现于 backend/app/workflow/definition.py）：
    nodes               节点集合（node_id 唯一；DAG）
    edges               顺序边 next_node_id + 条件边 on_true/on_false
    conditions          受控判定：source(点分路径) / operator(8 白名单) / expected(JSON 标量)
    retry policy        节点级 NodeRetryPolicy（immediate；max_attempts <= 5）
    approval gates      APPROVAL 节点类型（approval_prompt）
    version             name + version（run 记录；resume 按持久化 version 解析）
不包含（Deferred / 明确不在 Definition）：
    parallel groups     （Phase 6 Deferred；未来以 group 表达，见 §15）
    timeouts            （Deferred；与 Scheduling 决策绑定）
    scheduler 配置       （不属于 Definition；见 §18）
    ？？执行状态          （Definition 永不含 run/attempt/status）
```

**Definition ≠ Execution Instance**：

| | Definition | Instance |
| --- | --- | --- |
| 存在形式 | Python 代码 + `WorkflowRegistry`（服务器端静态注册） | 持久化行（`workflow_run`） |
| 生命周期 | 部署级（随版本演进；发布后 version 冻结） | 每次启动一个（可 resume / cancel） |
| 可变性 | 只读（frozen dataclass；重复注册显式失败） | 状态可推进（RUNNING ⇄ WAITING → 终态） |
| 版本 | `version` 是身份的一部分 | 记录所用 version；resume 校验（不一致 ⇒ fail-closed） |

## 6. Workflow Instance（Q2）

**Instance = 一次 Workflow 执行 = `workflow_run` 持久化行。**

五个标识（互不相同，各有唯一含义）：

| 标识 | 含义 | 生成者 | 复用既有？ |
| --- | --- | --- | --- |
| `workflow_id` | **definition 逻辑身份**（= `WorkflowDefinition.name`） | 开发者（代码） | 新命名空间 |
| `workflow_version` | definition 版本标识（发布后冻结） | 开发者（代码） | 新命名空间 |
| `workflow_run_id` | **执行实例身份**（uuid4；`new_workflow_run_id()`） | Workflow Runtime（start 时一次） | 新命名空间 |
| `request_id` | **一次外部调用**的 correlation identity（start / 每次 resume 各一个） | Service 层（既有 `new_request_id()`） | ✅ 复用既有 trace 体系 |
| `project_id` | 隔离身份（服务器端 ProjectRegistry 解析） | 服务器端配置 | ✅ 复用既有 ProjectContext |

```text
一个 workflow_run 的生命周期内：
    start 调用      → request_id #1
    resume 调用 × N → request_id #2..#N+1
⇒ request_id ≠ workflow identity（禁止把 request_id 当 run 身份 —— Q13）
```

## 7. Workflow Node（Q4）+ Node 类型分类 + Matrix 1

### 7.1 WorkflowNode ≠ AgentStep（层级论证）

```text
Workflow（父层执行域）
  └── Node A（WorkflowNode：父层执行单元）
        └── Agent 调用（一次 Agent Run）
              ├── AgentStep 0   （Agent 内部 LLM decision 单元）
              ├── AgentStep 1
              └── AgentStep 2
  └── Node B（WorkflowNode）
```

```text
WorkflowNode：workflow 定义中的结构单元；身份 = (workflow_run_id, node_id, attempt)
AgentStep   ：Agent Runtime 内部执行记录（FROZEN）；身份 = (run_id, step_index)
⇒ 不同层、不同表、不同状态机、不同生命周期。
⇒ 禁止把 AgentStep 升级/复用为通用 WorkflowNode（Frozen §6）。
⇒ WorkflowNode(type=AI) 与 Agent 的关系是"调用与被调用"（§11），不是包含。
```

### 7.2 Node 类型分类（Candidate / MVP / Deferred）

| 类型 | 分类 | 语义 | 实现状态 |
| --- | --- | --- | --- |
| `CONDITION` | **MVP** | 受控断言（EQ/NE/LT/LTE/GT/GTE/EXISTS/CONTAINS；无 eval） | 已实现（Step 1） |
| `TOOL` | **MVP** | 确定性调用指定只读 Tool（经 ToolExecutionService） | 已实现（Step 1） |
| `AI` | **MVP** | 一次 AI 能力调用（经 Orchestrator.execute；路由决定 RAG/TOOL/T2S/AGENT） | 已实现（Step 1） |
| `APPROVAL` | **MVP** | 人工审批门（workflow_approval；PENDING→APPROVED/REJECTED） | 已实现（Step 1） |
| `FUNCTION` | **Candidate** | 确定性计算函数（无 LLM） | **未实现**；替代方案=只读 Tool；见 DD-1 |
| `AGENT`（强制） | **Deferred** | 强制进入 Agent（跳过 Router） | OD-1 已决 Option 1（不新增 execute_agent；触发式升级） |
| `PARALLEL` | **Deferred** | fan-out/join（§15 设计已冻结） | 不实现（含前置条件） |
| `WAIT` | **Deferred** | 定时/事件等待 | 依赖 Scheduling 决策（§18） |

### 7.3 Matrix 1 — Workflow Capability Matrix

| Capability | Phase 6 Ownership | Persistence | Command/Query |
| --- | --- | --- | --- |
| Definition | Workflow（code + registry） | **无表**（代码；run 记录 version） | Query |
| Instance (Run) | Workflow Runtime | `workflow_run` | Command |
| Node (Step) | Workflow Runtime | `workflow_node_run`（per-attempt） | Command |
| Condition | Workflow Runtime（纯逻辑） | 无（作为 node 行记录） | Command |
| Parallel | Workflow Runtime（**Deferred**） | 未来 per-branch node 行 | Command |
| Retry | Workflow Runtime（节点级） | `workflow_node_run.attempt` | Command |
| Approval Gate | Workflow Runtime + 外部裁决 | `workflow_approval` | Command |
| Resume | Workflow Runtime（外部驱动） | `workflow_run.state_version` CAS | Command |
| Cancellation | Workflow Runtime（协作式） | `workflow_run.cancel_requested` 信号 | Command |
| Timeline | Workflow Read Service（派生） | **无表**（投影） | Query |
| Agent Step | **Agent Runtime（FROZEN）** | agent 六表（Workflow 不写） | Command（经 4 个 Service API） |

## 8. Workflow State（生命周期）

run 状态（已实现于 `states.py`；**不复制 Agent 状态机**）：

```text
PENDING → RUNNING → WAITING → RUNNING → … → TERMINAL
TERMINAL = COMPLETED | FAILED | CANCELLED
```

| 任务书候选 | 决定 | 理由 |
| --- | --- | --- |
| CREATED | = `PENDING`（改名说明：命名已冻结为 PENDING） | 启动窗口极短 |
| RUNNING / PAUSED / WAITING / WAITING_APPROVAL | run 层 = `RUNNING` / `WAITING`；**WAITING_APPROVAL 是 node attempt 状态**（run 用 WAITING + pending 指针表达原因） | 双层状态机：run 不复制 node 状态 |
| COMPLETED / FAILED / CANCELLED | 保留（3 终态，不可逆） | 与 Agent 语义一致（CANCELLED ≠ FAILED） |
| **RETRYING** | **不需要** | retry = node attempt 语义（同一 node_id 新 attempt）；run 不进入独立 RETRYING 状态 |
| **LIMIT_REACHED** | **不设为 run 状态**；用 `FAILED` + `error_code = WORKFLOW_LIMIT_REACHED` 表达 | 与 Agent 的差异论证：Agent loop 无静态上界，预算截断是"设计内正常停止"，故独立状态；Workflow 是 DAG（节点数静态可知），预算触限（attempt 放大）意味着"流程无法按定义完成"⇒ FAILED 是正确终态；**明确区分** cancel（用户意图）与 limit（预算边界） |

node attempt 状态（已实现）：`RUNNING → WAITING_APPROVAL → RUNNING → SUCCEEDED | FAILED`
（终态 per attempt；retry 产生**新行**，不改写历史）。

## 9. Workflow Checkpoint（任务书 §14）

**概念：存在（Recovery Control State）。形态：承载于 `workflow_run` 行，无独立表。**

```text
保存内容（承载字段）：
    workflow_run_id          执行身份
    definition version       版本一致性（resume 校验）
    current_node             当前/下一节点指针
    next_action              LLM/节点推进语义（WAITING 原因）
    state_version            乐观并发（CAS；每次成功推进 +1）
    pending_*                待处理引用（pending approval / 下一 node）
    cancel 信号              cancel_requested（不是 status 枚举成员）
    retry 计数字段            attempt 累计（run 级预算用）
不保存：prompt / Tool Result 全文 / SQL / 凭据（§25）。
```

**UPDATE vs append-only 的最终选择：**

| 方案 | 决定 | 理由 |
| --- | --- | --- |
| 独立 `workflow_checkpoint` 表（UPDATE + CAS；同 Agent 形态） | ❌ 不采用 | Workflow 的推进是**逐节点显式**的（不像 Agent 需要独立于 run 的"循环控制状态"）；`workflow_run` 行自身即可完整判定"我在哪 / 下一步 / 上一节点是否 durable"（ADR-01 §8.3）；少一张表、少一份 CAS 语义 |
| 承载于 `workflow_run`（UPDATE + state_version CAS） | ✅ **采用** | 单行当前状态；终态不可逆；恢复判定基于 `workflow_run.current_node` + `workflow_node_run` 事实（§10） |
| append-only event / checkpoint 历史 | ❌ | 属 event store（ADR-01 RA6 拒绝）；审计由 per-attempt node 行覆盖 |

与 AgentCheckpoint 的区别（**不得混淆**）：

```text
AgentCheckpoint（FROZEN）：独立表 · claim_token · pending_decision_ref / pending_tool_result_ref
Workflow 控制状态        ：workflow_run 行内 · state_version CAS · pending approval 指针
两者不共用表、不共用 claim 语义、不互相引用。
```

Deferred：若未来出现"独立 checkpoint 历史 / 更高并发拆分"需求 ⇒ 新 ADR（触发条件记录于 DD-3）。

## 10. Workflow Persistence

**三张表（最小集合；ADR-01 §8 已决，本 Discovery 确认）：**

| 表（ai_ops） | SoT 内容 | 身份约束 |
| --- | --- | --- |
| `workflow_run` | Run Lifecycle + Execution State（含 §9 控制状态） | UNIQUE(workflow_run_id) |
| `workflow_node_run` | Node Execution Record（per-attempt；output 内联 + 32 KiB 边界） | UNIQUE(workflow_run_id, node_id, attempt) |
| `workflow_approval` | Human Approval State（PENDING → 终态；条件 UPDATE 单赢） | UNIQUE(approval_id)；UNIQUE(workflow_run_id, node_id) |

域字段 ↔ 持久化映射（Step 1 已实现的 domain DTO → 表）：

```text
WorkflowRunResult      → workflow_run
NodeExecutionIdentity  → workflow_node_run 的身份
NodeRunOutcome         → workflow_node_run 的 attempt 行
WorkflowApprovalRecord → workflow_approval
```

## 11. Agent Integration（Q5）

```text
Workflow Node(type=AI)
      ↓
AIOrchestratorService.execute（唯一入口；OD-1 Option 1）
      ↓
AIRouterService（RAG | TOOL | TEXT_TO_SQL | AGENT）
      ↓（命中 AGENT 时）
AgentRuntime（FROZEN）
```

- ✅ 允许：`execute` / `resume_agent_run` / `resolve_agent_approval` / `cancel_agent_run`（Service API）。
- ❌ 禁止：Workflow → `AgentRuntime` 内部（`_run_scoped` / llm / policy / ports / repositories / 六表）。
- ❌ 禁止：Agent → Workflow（反向；防止无限递归 —— §19）。
- 嵌套 HITL 协调：Agent 暂停（`WAITING_APPROVAL`）⇒ workflow node = WAITING_APPROVAL；workflow **不自动**裁决/级联（人先裁决 Agent，再 resume workflow；幂等等待语义）。
- 装配：per-project orchestrator（`agent_enabled=True`；唯一 composition root）——生产 HTTP 入口默认保持 disabled（ADR-01 §10.2）。

## 12. Tool Integration（Q6）

**决策：`Workflow → ToolExecutionService`（直接、确定性），不选 `Workflow → AIOrchestrator → Tool`。**

| 维度 | Workflow → ToolExecutionService ✅ | Workflow → Orchestrator → Tool ❌ |
| --- | --- | --- |
| 确定性 | 指定 tool_name + arguments（node 定义） | 依赖 Router 选择（不确定） |
| 授权 | capability 白名单（既有第二道校验） | 同样经过，但语义绕行 |
| 观测 | `ToolExecutionContext` → `ToolExecutionRecord`（既有链路） | 同 |
| 复用 | 唯一 Tool 边界，不建第二套 executor | 无必要的中介 |

保持既有边界链：`Authorization → Execution → Observation`（capability → Registry/Handler → observer）。
Tool 白名单：workflow-safe 只读集合（write tool = BLOCK；ADR-01 §12 SB1–SB5）。

## 13. HITL Integration（Q7）

```text
Workflow-level HITL（Workflow Approval Gate）:
    Node A → APPROVAL node → WAITING →（外部裁决）→ APPROVED/REJECTED → Node B
    持久化：workflow_approval（独立表；独立状态机）
Agent-level HITL（Agent Tool Approval，FROZEN）:
    LLM decision → Tool Authorization → AgentApproval → Tool
    持久化：agent_approval（不得触碰）
```

| 维度 | Workflow Approval Gate | Agent Tool Approval |
| --- | --- | --- |
| 暂停对象 | Workflow Node（流程等待点） | Agent Run（Tool 执行前） |
| SoT | `workflow_approval` | `agent_approval`（FROZEN） |
| 恢复 | `resume_workflow`（外部调用） | `resolve_agent_approval` + `resume_agent_run` |
| 复用关系 | **仅复用"实现模式"**（条件 UPDATE 单赢 / 终态不可逆）；**不复用表、不复用代码路径** | — |

嵌套（ai node 内 Agent 自行暂停）：两层审批**各自独立裁决**；workflow 不代理（§11）。

## 14. Retry Ownership（Q8）

```text
Retry Owner = Workflow Runtime（节点级）。其余层保持零 retry（Phase 5 FROZEN + fail-closed）。
```

| 概念 | 定义 |
| --- | --- |
| attempt | 同一 node_id 的一次执行；新 attempt = **新行**（attempt+1；历史保留） |
| idempotency | 安全前提 = 只读工具白名单（§12）+ Agent 内部工具只读（Phase 5）；write = BLOCK |
| side effect | 当前可重试集合**零外部副作用**（源码证明：READ ONLY 事务 / LLM 无副作用） |
| replay | 与 retry 区分：**Retry = 新 attempt 重新执行**；Agent 的 Replay（已完成 ⇒ 0 次调用）是 Agent 内部语义，与 Workflow retry 无关 |
| failure classification | 可重试白名单（稳定码；如 LLM 传输类失败 / 可恢复 Tool 故障）；不可重试：审批拒绝 / 能力拒绝 / 参数错误 / REFUSAL / LIMIT_REACHED / cancel |
| 整个 Agent Node 重试？ | **= 新 AgentRun**（新 run_id，完整重新执行）；**不得** resume 旧 run（resume ≠ retry）；也不得进入 Agent Runtime 改 loop |
| fail-before-retry | 仅在"失败事实已持久化"后重试；UNKNOWN / crash **绝不**自动重试（fail-closed） |

**Workflow Retry 不得偷偷进入 Agent Runtime 内部改变 Agent Loop**（Frozen §6）。

## 15. Parallel Ownership（Q9）

**Phase 5 `one tool per round` 不变。Parallel = Workflow Runtime 能力（Phase 6 Deferred）。**

设计已冻结（实现前不再重新辩论）：

```text
fan-out      ：一组 node 同时激活（每分支 = 独立 node_run 行；先记 RUNNING 纪律）
fan-in/join  ：join 条件初始 = ALL-REQUIRED（全成就）；FAIL_FAST（任一败 → 分支组失败）
failure policy（初始决策）：
    A success / B failed / C success  →  分支组 = FAILED（FAIL_FAST 起手）
    未来可配置 WAIT_ALL / PARTIAL_SUCCESS（触发式；见 DD-4）
cancel policy：workflow cancel → 停止调度新分支 + 向 running 分支协作式传播
               （子 Agent 经 cancel_agent_run）；等自然结束，不强杀
partial result：已成功分支的 node_run 行**保留**（不回滚）；join 决策读取全部事实
```

前置条件（实现前必须满足；ADR-01 §13 PC1–PC5）：per-branch durable state ·
join/failure/cancel 传播规则 · crash 恢复 UNKNOWN 判定 · 执行驱动评估。

## 16. Conversation Relationship（Q11）

```text
允许的目标结构（未来）：
    Conversation ──▶（可选触发）Workflow Run
    （Conversation 不拥有 Workflow；Workflow 状态不存 Conversation 表）

Workflow 必须可以脱离 Conversation 启动：
    scheduled workflow     （外部触发，无 conversation）
    event-driven workflow  （业务事件触发）
    background workflow    （API/服务调用触发）
```

决策（Phase 6.1）：

```text
- Workflow 启动入口 = **独立 Service API**（不依赖 Conversation）；
- Conversation → Workflow 触发 = **Deferred**（DD-6；需要对话式启动的交互设计 + 独立 ADR）；
- Conversation 表 / Turn / Idempotency **零修改**（Phase 4 边界保持）；
- 不把 workflow_run_id 写入 conversation_turn（correlation 如需，走未来独立关联设计）。
```

## 17. Evidence Relationship（Q12）

```text
保持：Evidence = Source / Provenance（Phase 4 域）。
    Workflow Step Result  ≠  Evidence
若某个 Step 未来需要产生证据，正确链路：
    Step → Business Result →（经 business / evidence service）→ Evidence
禁止链路：
    Workflow → Evidence 表（直接写）
Phase 6 决策：Workflow 不写 Evidence（Deferred 关联设计 = DD-7）。
```

## 18. Scheduler Relationship（Q14）

实测搜索（scheduler / schedule / background / worker / job / queue / task / cron / celery / apscheduler）：
**本仓库 0 命中实际设施**（3 处均为 docstring 的"不做"声明）。

决策（ADR-01 D13 确认）：

```text
Scheduler（何时启动）与 Workflow Runtime（启动后如何执行）分离：
    External Trigger（企业调度器 / 人工 API / 事件源）
        ↓
    Workflow Application Service（start / resume）
        ↓
    Workflow Runtime（start 之后的全部控制流）
```

```text
- 本 repo **不实现** Scheduler / cron / timer / worker / queue（Phase 6 全周期 Deferred）；
- 企业既有 XXL-JOB = 业务系统调度，不等于 AI Workflow Scheduler；
- "外部触发器调用 Workflow API" 是**部署选择**，不是本 repo 代码；
- Scheduler 不得进入 step 控制逻辑（例如不得让 timer 直接改 node 状态——
  一切推进经 run 的 state_version CAS）。
```

## 19. Router Relationship

```text
AIOrchestrator 负责：用户请求 → route（现状 4 route：RAG | TOOL | TEXT_TO_SQL | AGENT）
```

**Workflow 是否进入 AI Router？→ 决策：Phase 6.1 不加入（Option C：外部 trigger 制）。**

| 候选 | 决定 | 分析 |
| --- | --- | --- |
| A. Router 增加 WORKFLOW route | ❌（Deferred DD-8） | 用户交互场景（"帮我跑一个采购入库流程"）需求存在但未证实；且会让"对话→长流程"的交互/幂等/确认设计成为前提 |
| B. Router → Workflow Application Service 分流 | ❌ | 与 A 同类问题（只是包装差异） |
| C. Workflow 为外部 trigger（API / Scheduler / Event） | ✅ **采用** | 与 §18 驱动模型一致；后台任务/计划任务/业务事件三场景天然满足；用户交互场景 Deferred |
| 递归禁令 | — | **Workflow Runtime 不得调用 AI Router**；ai node 经 `Orchestrator.execute`（内部 route）**安全**——因为 Router 不含 WORKFLOW route，不会回环到 Workflow（§4/§11） |

## 20. Source of Truth Matrix（Matrix 3）

| 事实域 | Source of Truth | 明确不是 | 状态 |
| --- | --- | --- | --- |
| Workflow Definition | 代码 + `WorkflowRegistry`（run 记录 version） | 无表；不是 DB | ✅（Step 1 已实现） |
| Workflow Run | `ai_ops.workflow_run` | Timeline / Read API | 待实现（6.1） |
| Workflow Node (Step) | `ai_ops.workflow_node_run`（per-attempt） | AgentStep；不是 checkpoint | 待实现（6.1） |
| Workflow Checkpoint | **概念**承载于 `workflow_run`（state_version 等） | 独立表（不建） | 决定已冻结（§9） |
| Agent Run | `ai_ops.agent_run` | Workflow run | FROZEN |
| Agent Step | `ai_ops.agent_step` | Workflow node | FROZEN |
| Agent Decision | `ai_ops.agent_decision` | — | FROZEN |
| Agent Tool Result | `ai_ops.agent_tool_result` | — | FROZEN |
| Agent Checkpoint | `ai_ops.agent_checkpoint` | Workflow 控制状态 | FROZEN |
| Agent Approval | `ai_ops.agent_approval` | Workflow Approval | FROZEN |
| Conversation | `ai_ops.conversation` / `conversation_turn` | Workflow 状态 | Phase 4 冻结 |
| Evidence | Evidence 表（Phase 4） | Workflow 执行日志 | Phase 4 冻结 |

```text
关键不等式（全部成立）：
    WorkflowNode ≠ AgentStep（§7.1）
    WorkflowCheckpoint ≠ AgentCheckpoint（§9）
    WorkflowTimeline ≠ AgentTimeline（独立派生投影；Agent Timeline FROZEN）
```

## 21. Command / Query Matrix（Matrix 4）

**Query MUST NOT mutate**（沿用 Phase 5 Read API 冻结原则）。

| 类型 | 操作 | 归属 | 说明 |
| --- | --- | --- | --- |
| Command | `start_workflow` | Workflow Application Service | 创建 run；inline 执行至 wait-point/终态 |
| Command | `resume_workflow` | 同上 | 从持久化状态继续（state_version CAS 单赢） |
| Command | `cancel_workflow` | 同上 | 协作式信号；终态 CANCELLED |
| Command | `resolve_workflow_approval` | 同上 | APPROVED/REJECTED；条件 UPDATE 单赢 / 幂等 |
| Query | `get_workflow_run` | Workflow Read Service | 只读视图（run + nodes + approvals） |
| Query | `get_workflow_status` | 同上 | 轻量状态查询 |
| Query | `get_workflow_timeline` | 同上 | 派生投影（无表） |

```text
命令 = 4 个；查询 ≥ 3 个；HTTP surface 在 Phase 6.3 以 API 契约文档冻结
（路径/字段细节属实现契约，不在此层锁死）。
```

## 22. Real WMS Workflow Examples（架构示例，不实现）

> 标注约定：✅ 现有能力可用 · ⚠️ 依赖 Deferred/写操作（Phase 6 BLOCK）。

### Workflow A — 采购入库

```text
读取 PO（TOOL: get_purchase_order*） → 到货通知核对（TOOL）
  → 审批（APPROVAL） → 收货执行（⚠️ WRITE） → 库存更新（⚠️ WRITE） → Evidence（⚠️ DD-7）
```

| 步骤 | 类型 | 现状 |
| --- | --- | --- |
| 读取 PO / 核对 | TOOL（只读；需新增只读 tool 注册） | 需 Tool 扩展（只读） |
| 审批 | APPROVAL | ✅（Phase 6 可实现） |
| 收货执行 / 库存更新 | **WRITE** | ❌ Phase 6 BLOCK（写操作治理 = Phase 7 范畴） |
| Evidence | — | DD-7 |

⇒ 示例 A 在 Phase 6 只能实现前半段（读取 + 审批）；写入段被安全边界正确阻断。

### Workflow B — 生产日报

```text
读取工单（TOOL: get_work_order ✅） → 读取生产进度（TOOL） → 计算完成率（FUNCTION/Candidate）
  → 异常判断（CONDITION ✅） → Agent Summary（AI node ✅） → 日报输出（业务结果）
```

| 步骤 | deterministic vs Agent |
| --- | --- |
| 读取 / 计算 / 判断 | **必须 deterministic**（数字正确性不可交给 LLM） |
| Agent Summary | 可以用 Agent（自由文本归纳；只读、有界） |
| 计算完成率 | FUNCTION（Candidate；或以只读 tool 实现——DD-1） |

### Workflow C — 异常库存分析

```text
Inventory Query（TOOL ✅） → Threshold Check（CONDITION ✅）
  → Parallel{ Stock / WorkOrder / Purchase Analysis }（PARALLEL Deferred）
  → Join → Agent Summary（AI ✅） → Human Approval（APPROVAL ✅） → Report
```

⇒ Phase 6.1 版本可将 Parallel 降级为**顺序三节点**（语义等价、性能次优；§15 的前置条件满足后升级）。
本示例覆盖 Parallel / Agent / HITL 三个关键设计点的验证。

## 23. Persistence Decision（最终选择）

```text
Workflow persistence = Option A（新增独立表：workflow_run / workflow_node_run / workflow_approval）
```

| 候选 | 决定 | 理由 |
| --- | --- | --- |
| A. 新增独立表 | ✅ **采用** | ownership 清晰；lifecycle 不同（node attempt vs agent step）；retry/parallel/HITL/resume 都要求 per-node durable state；read model 独立 |
| B. 复用 AgentRun/Step/Checkpoint | ❌ | 违反 Frozen §6（不得绕过/复用）；Agent Step 语义（LLM decision 步）与 Workflow Node 不同层；HITL/Retry 状态机完全不同；复用会造成两个执行域互相污染 |
| C. 通用 ExecutionRun/Step/Checkpoint | ❌ | 与 A 实际等价但提前泛化（当前只有一种执行者）；命名抽象会掩盖两层语义差异（YAGNI + 防概念腐败） |
| D. 其他（仅 run 无 node） | ❌ | 无法支撑 per-node retry / resume / timeline |

**为什么不能直接复用 AgentRun / AgentCheckpoint（逐项）**：

```text
AgentRun     ：终态执行记录（append-only、无状态推进语义）——Workflow run 必须是
               可推进的控制状态（state_version / current_node / pending）；
               run_id 身份域、字段、错误码、Read API 全部 FROZEN 且语义不同。
AgentCheckpoint：独立表 + claim_token + pending_decision/result 引用（Agent 循环恢复专用）；
               Workflow 的控制状态承载于自己的 run 行（§9），两者 claim/CAS 语义不共用。
```

Trade-offs（如实记录）：多 3 张表与 3 套 repository 的维护成本；换取的是职责隔离与 FROZEN 不被侵入。

## 24. Durability Decision

| 维度 | Option 1：纯 PG 自研 | Option 2：第三方 Runtime（Temporal 等） | Option 3：轻量自研 + 未来可迁移 |
| --- | --- | --- | --- |
| 开发复杂度 | 中（状态机 + CAS + 恢复语义，已大部分设计完） | 低–中（SDK）但需学范式 | 同 Option 1（先做窄接口） |
| 部署复杂度 | 0 新增基础设施 | **高**（服务端 + worker + 运维） | 初期 0；迁移时评估 |
| 状态持久化 | PostgreSQL（既有） | 自有存储 | PostgreSQL |
| Retry | 节点级（已设计） | 内置 | 节点级（drop-in） |
| Timer / 定时 | ❌（Deferred） | 内置 | Deferred（接口留白） |
| HITL | 自有 approval（已设计） | Signals/Updates | 自有 approval |
| Parallel | Deferred（设计已冻结） | 内置 | Deferred |
| Recovery | 状态可判定 + fail-closed（已设计） | 内建 | 同左 |
| Observability | 复用派生投影模式 | 平台 UI | 复用 |
| Python 兼容 | ✅（3.12+ 团队可控） | ✅（SDK） | ✅ |
| Windows 本地开发 | ✅（现有 docker compose） | 需额外本地服务 | ✅ |
| Docker 兼容 | ✅（现有组合） | ✅（更大镜像体积） | ✅ |
| **决定** | 基础 | **不采用**（本阶段不安装；仅记录评估） | ✅ **推荐（= Option 1 的显式表述）** |

```text
最终：Option 3（= 先以纯 PostgreSQL 自研走通；driver 与状态模型分层，
未来如需升级执行驱动（worker/step-driven）或替换 runtime，状态模型（§9/§10）不需要改）。
本阶段禁止：pip install Temporal / LangGraph；docker compose 增加服务；runtime 集成。
```

## 25. Security Boundary

```text
SB-A  Phase 5 六表 / Agent Read API / Timeline / 错误码：零修改（Frozen）。
SB-B  Workflow 不写 Conversation / Evidence / agent 六表。
SB-C  Runtime State 禁止存储：Prompt · API Key · Authorization Header ·
     完整 RAG chunks · 完整 SQL · 完整 Tool Result（业务数据走 Reference / Hash /
     bounded payload；node output 内联 ≤ 32 KiB，超限 fail-closed 不截断）。
SB-D  project / capability / policy 全部服务器端解析；全实体携带 project_id；
     读边界 project-scoped（同码 404，不泄露存在性）。
SB-E  稳定错误码白名单（WORKFLOW_ 前缀；独立于 AGENT_ERROR_CODES）。
SB-F  控制状态写入（run/node 推进）**不是** best-effort（写失败 ⇒ fail-closed 停止推进）；
     观测/投影写入才是 best-effort（对齐 Phase 5 分层）。
SB-G  写操作（write tool / write node）：Phase 6 全局 BLOCK（重述 ADR-01 SB1–SB5）。
```

## 26. Deferred Decisions（带触发条件）

| ID | 事项 | 触发条件 |
| --- | --- | --- |
| DD-1 | FUNCTION / deterministic compute node | 出现"必须确定性计算且不适宜封装为只读 Tool"的真实需求 → 节点类型扩展 ADR |
| DD-2 | 强制 Agent 节点（execute_agent） | "必须进入 Agent"的真实 workflow 需求 → Phase 5 §5 变更程序（OD-1 触发式） |
| DD-3 | 独立 WorkflowCheckpoint 表 / 更高并发 | 出现独立 checkpoint 历史或并发拆分需求 → 新 ADR |
| DD-4 | Parallel（fan-out/join + failure policy 扩展） | 并发业务需求 + §15 前置条件完成 |
| DD-5 | WAIT / timer / backoff / scheduled | 调度路径 ADR（优先"外部调度器 + API"） |
| DD-6 | Conversation → Workflow 触发 | 对话式启动需求 + 交互/幂等设计 |
| DD-7 | Evidence 关联 | 业务证据溯源需求（经 evidence service） |
| DD-8 | Router 增加 WORKFLOW route | 用户交互式启动需求（DD-6 之后） |
| DD-9 | Workflow UI / Streaming | 产品需求 |
| DD-10 | 执行驱动升级（worker / step-driven / Temporal 评估） | 并发/吞吐/多进程/定时需求 |
| DD-11 | Tool 分类扩展（ToolDefinition） | 第一个 write / external side effect tool 需求 |
| DD-12 | Approval TTL / 通知 | 运营需求（同 Phase 5 策略） |

## 27. Phase 6 Implementation Boundary

```text
Phase 6.1（下一阶段；**编码前必须重新 review**）：
    允许：
        Persistence —— ai_ops.workflow_run / workflow_node_run / workflow_approval
                        + repositories（对齐 agent repository 模式）
                        + 幂等 DDL（create_all + init_db；无 migration 框架）
                        + DB 测试（RUN_DB_TESTS 门控 + 定向清理 + residue=0）
    遵循：§9/§10 的字段与约束；§25 安全边界；Frozen 零修改。

Phase 6.2：Workflow Runtime（node executor + Service API：start/resume/cancel/approve）
Phase 6.3：Workflow Read Service + Timeline（派生投影）
Phase 6.4：Workflow HTTP API（命令/查询端点；API 契约文档）

本阶段（6.0）禁止：实现任何上述内容（本文件为唯一产出）。
```

---

## Appendix A — Q1–Q14 索引

| # | 问题 | 答案位置 | 一句话结论 |
| --- | --- | --- | --- |
| Q1 | Definition 是什么 | §5 | code + registry；nodes/edges/conditions/retry/approval/version；≠ Instance |
| Q2 | Instance 是什么 | §6 | `workflow_run` 行；五个标识含义已冻结 |
| Q3 | 是否需要独立持久化 | §23 | Option A（独立 3 表）；不复用 Agent 表 |
| Q4 | WorkflowStep vs AgentStep | §7 | 不同层不同表；AgentStep 不得升级为通用 Step |
| Q5 | Agent 如何成为 Node | §11 | 只经 4 个 Service API；禁止内部/六表 |
| Q6 | Tool Step 如何执行 | §12 | 直接经 ToolExecutionService（确定性）；不绕行 |
| Q7 | HITL Gate 谁拥有 | §13 | Workflow Approval Gate 独立；与 Agent Approval 分离 |
| Q8 | Retry 谁负责 | §14 | Workflow 节点级；Agent 零 retry；新 attempt 语义 |
| Q9 | Parallel 谁负责 | §15 | Workflow（Deferred；设计已冻结含失败/取消策略） |
| Q10 | Timeline 怎么处理 | §7.3/§20 | 独立派生投影（无表）；**不修改** Agent Timeline |
| Q11 | Conversation 关系 | §16 | 可触发但非必须；可脱离 Conversation 运行 |
| Q12 | Evidence 关系 | §17 | Step Result ≠ Evidence；经业务结果关联（Deferred） |
| Q13 | run_id / request_id | §6 | run_id=执行身份；request_id=调用 correlation；不混用 |
| Q14 | Scheduler 放哪里 | §18 | 外部触发器 → Application Service → Runtime；不进 repo |

## Appendix B — 关键证据（勘察读取清单）

```text
Phase 5：Agent Runtime Architecture Frozen.md · Agent Runtime Exit Audit.md · Scope & Architecture Decision
Phase 6：Workflow Runtime Architecture Decision.md（ACCEPTED）
代码：agent/**（16）· services/ai_orchestrator_service.py · ai_router_service.py（4 route）·
      tool_execution_service.py · tools/** · db/models/**（16 表）· db/*repository* ·
      api/**（11 router：含 agent_read 2 GET）· projects/**（Registry/Configuration/Capabilities）·
      conversation_service.py · conversation_context_*.py · chat_application_service.py ·
      workflow/**（Step 1 Domain，7 文件）· tests/test_workflow_*.py（4 文件 / 186 tests）
实测：scheduler/worker/queue/cron = 0 设施；Router route = {rag, tool, text_to_sql, agent}
```

## Appendix C — Architecture Status 声明

```text
Architecture Status: FROZEN
依据：核心决策与 ACCEPTED 的 ADR-01 完全一致；Step 1 Domain 已实现并测试锁定；
      全部未决项已明确列入 Deferred（DD-1..DD-12，均带触发条件）；
      无阻塞性未知项。
变更程序：修改本文件任一 FROZEN 项 ⇒ 新建 ADR（对齐 Phase 5 §5 变更程序）。
```
