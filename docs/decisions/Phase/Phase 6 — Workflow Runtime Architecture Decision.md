# Phase 6 — Workflow Runtime Architecture Decision

> 状态：**ACCEPTED**（已批准 · 2026-10-08；Phase 6 Implementation 依据本 ADR）
> 批准记录：用户确认批准（PROPOSED → ACCEPTED）·
> OD-1 决议 = Option 1（ai node 经 `AIOrchestratorService.execute()`；暂不新增 `execute_agent()`）
> 性质：ADR（Architecture Decision Record）——只做决策，不写代码
> 依据：Phase 5 Frozen（`Phase 5 — Agent Runtime Architecture Frozen.md`）·
> Phase 5 Exit Audit · Phase 6 PRE-ARCHITECTURE AUDIT（基于真实代码 / 真实表 / 真实测试）
> 冻结基线：`a50869c`（分支 `phase5-1`）
> 本文档是 Phase 6 的**唯一架构入口**；实现任务书不得违背本文档。

---

## 1. Context

当前系统（实测）：

```text
User
  ↓
FastAPI（11 个 router）
  ↓
AIOrchestratorService.execute（单请求 · 单路由 · 单次执行）
  ↓
AIRouterService.route（RAG | TOOL | TEXT_TO_SQL | AGENT）
  ↓
RAG / Tool / Text-to-SQL / Agent Runtime（Phase 5，FROZEN）
  ↓
PostgreSQL（ai_ops schema + public 业务 schema）
```

Phase 5 交付并冻结了**模型驱动**的 Agent Runtime：

```text
LLM decides → Tool → LLM decides → Tool → … → FINAL
有界 · 可恢复（Resume）· 可审批（HITL）· 可取消（协作式）· 可只读观察（Read API + Timeline）
```

Phase 5 明确将以下能力**留给 Phase 6**（Developer-Defined Control Flow）：

```text
deterministic business flow · branch / condition · approval gate ·
parallelism · retry policy · scheduled execution · long-running workflow
```

Phase 5 同时冻结了 Phase 6 的调用约束（Frozen §6，不可谈判）：

```text
Phase 6 只能通过既有内部 Service API 触发 Agent：
  AIOrchestratorService.execute / resume_agent_run /
  resolve_agent_approval / cancel_agent_run
不得绕过 Runtime 直接写 agent 六表；
不得修改 Agent Loop / Decision Contract / 六表 schema / 错误码 / Read API / Timeline；
Workflow 的 retry / 并行 / 补偿不得下沉进 Agent Runtime。
```

其他相关事实（实测）：

* `chat_application_service.py` 已自述 "workflow composition"（Application Layer 的
  request-scoped 线性组合）——Phase 6 必须与该语义区分；
* `agent_enabled=True` 在生产代码中 **0 调用点**（AGENT 路由经 HTTP 默认不可达）；
* 全系统无 retry / 无并行 / 无 scheduler / 无 worker / 无队列（依赖清单实证）；
* 无 Alembic；建表 = `create_all` + `init_db.py` 幂等 DDL；agent 六表在 `ai_ops`；
* 六张 agent 表中仅 `agent_run` / `agent_tool_result` 有 `project_id`（OQ-P5-25 的根因）。

---

## 2. Problem

Phase 6 需要一条**确定性控制流**执行路径，用于"开发者定义的业务步骤序列"，例如：

```text
查询库存 → 低于阈值？ → 是：生成补货建议 → 人工审批 → 记录结果
                        → 否：直接生成库存报告
```

这类需求无法用 Agent Runtime 表达（控制流由 LLM 决定，且为只读、有界、单 Agent）。
Phase 6 必须回答的核心问题（本 ADR 逐一裁决）：

```text
C1  控制流模型（DAG / 状态机 / 线性 / 混合）
C2  定义如何存在（代码 / YAML / DB）
C3  持久化模型（哪些实体、谁是 SoT、独立 namespace）
C4  执行驱动（请求内 / 外部 API / 后台 / worker）
C5  与 Agent 的层级与接口（Node 调 Agent 的确切边界）
C6  Retry 归属（Workflow vs Node vs Agent）
C7  Tool 安全边界（副作用分类缺失下如何安全）
C8  Parallel / Scheduling / Long-running / HITL / Cancellation
C9  Project Isolation / Read API / Timeline / 与 Conversation、Evidence 的边界
```

约束：所有决策不得修改 Phase 5 冻结契约；不得为"未来可能需要"引入 Redis / Celery /
Kafka / RabbitMQ / Temporal / APScheduler / LangGraph / MCP。

---

## 3. Goals

```text
G1  Developer-Defined Deterministic Control Flow（顺序 + 条件分支为第一等公民）
G2  节点类型受限且明确：ai / tool / approval / condition（无任意代码执行）
G3  Workflow 级持久化 + 恢复（durable state；进程重启后状态可判定）
G4  Workflow 级审批（独立于 Agent HITL；独立持久化）
G5  协作式取消（可传播到运行中的子 Agent；不强杀）
G6  Workflow 级重试（有界、按稳定错误码分类、可证明安全）
G7  Workflow 级只读观察（Read Service + 派生 Timeline；无 event store）
G8  与 Agent Runtime 严格分层（Sibling Runtimes；只经 AIOrchestrator Service API）
G9  Project Isolation（服务器端解析的 project_id 贯穿全链路；查询同码 404）
G10 与既有架构风格一致（DTO / ports / best-effort 边界 / 稳定错误码 / 测试先例）
```

---

## 4. Non-Goals

```text
NG1  不实现通用低代码平台（无 YAML/JSON DSL 蓝图编辑器、无动态热更新）
NG2  不实现图形化 Workflow 设计器 / UI
NG3  不引入 Multi-Agent / LangGraph / MCP / A2A
NG4  不引入 write / external-side-effect Tool（Phase 6 明确 BLOCK；见 §12）
NG5  不实现 workflow 内部并行执行（Parallel = Deferred；见 §13）
NG6  不实现 in-repo Scheduler / cron / timer / delayed execution（见 §17）
NG7  不实现后台执行器 / worker / 队列 / 分布式执行
NG8  不实现 Agent 能力变更（除 OD-1 触发式决策项外，Agent 六表与 Runtime 零修改）
NG9  不实现 RBAC / User / Permission（Phase 7；Phase 6 只做 project-scoped 边界）
NG10 不实现 Workflow UI / Streaming / SSE / WebSocket
NG11 不把 Workflow 状态写入 Conversation / Evidence / agent 六表
NG12 不开放生产 HTTP 的全局 agent_enabled（见 §10 决策）
```

---

## 5. Architecture Principles

```text
P1  Frozen First         Phase 5 冻结契约不可修改；新能力只能叠加在上层
P2  Sibling Runtimes     Workflow Runtime 与 Agent Runtime 平级；
                         前者只经 Service API 调用后者，不 import 其内部
P3  Single Boundary      每类能力只有一个执行边界：
                         Tool → ToolExecutionService（不建第二套）
                         Agent → AIOrchestratorService 4 个 Service API
P4  Durable State        所有推进点持久化；asyncio task ≠ durable execution
P5  Fail-Closed          不可判定 ⇒ 拒绝/停止，绝不猜、绝不自动重跑未知状态
P6  Explicit Budget      有界：run 级步数上限 + node 级 attempt 上限，全部有限
P7  Server-Side Truth    project / capability / policy 全部服务器端解析，HTTP 不可注入
P8  Read ≠ Write         只读观察独立于执行；Read Service 0 写入
P9  Derived Over Stored  Timeline / Read Model 优先派生投影，不建 event store
P10 Simple First         最小可用；不为未来提前引入基础设施
P11 Vocabulary Fixed     新词汇表冻结（见 §28），禁止复用 Orchestrator / Step 裸词
```

---

## 6. Control Flow Decision

### 6.1 候选

| 候选 | branch | approval | parallel（未来） | retry | long-running | developer ergonomics |
| --- | --- | --- | --- | --- | --- | --- |
| A. DAG | 天然（边） | 需外挂状态 | 天然（fan-out/join） | 需外挂 | 需外挂 | 中（拓扑清晰，运行语义弱） |
| B. State Machine | 天然（转移） | 天然（WAITING 状态） | 差（状态爆炸） | 天然（重入） | 天然 | 低（多步骤业务流难读） |
| C. Sequential Pipeline | 勉强（条件跳转破坏线性） | 需外挂 | 不支持 | 需外挂 | 需外挂 | 高（但表达力不足） |
| D. DAG + State Machine hybrid | 天然 | 天然 | 天然 | 天然 | 天然 | 高（结构 + 语义分层） |

结合本项目实际需求判断：

* Phase 6 必须支持 **condition/branch**（用户目标）；纯线性 Pipeline（C）无法表达
  非顺序控制流 → 淘汰为"表达力不足"；
* **approval / retry / long-running** 都需要节点级"等待 / 重试 / 终态"语义 → 必须有
  状态机成分；纯 DAG（A）只描述拓扑，不描述执行语义 → 不完整；
* **parallel 为 Deferred**（§13），不构成对 hybrid 的反证；反之 hybrid 的图结构
  为未来 fan-out/join 预留拓扑表达力；
* developer ergonomics：State Machine（B）表达"查询→判断→审批→记录"这类业务流
  可读性差（状态爆炸）→ 不选纯 B；
* 与 Phase 5 一致的风格：Agent 的执行语义即"内存循环 + 持久化控制状态"，
  Workflow 的"图结构 + 节点状态机"是同一哲学在确定性域的推广。

### 6.2 决策

**RECOMMENDED MODEL：DAG + State Machine Hybrid（受限版）**

```text
拓扑（静态 · 开发者定义）：
    有向节点图 · 顺序边 + 条件分支边（condition 节点）
    Phase 6 第一阶段限制：
      - 单一活跃路径（无同时活跃的多分支 = 无 parallel）
      - 无环（重试由节点 retry policy 表达，不引入回边）
      - 每个节点声明其后继（unconditional 或 conditional）

执行语义（运行时 · 状态机）：
    WorkflowRun 状态机：PENDING → RUNNING ⇄ WAITING → TERMINAL
                        （TERMINAL = COMPLETED | FAILED | CANCELLED）
    NodeRun 状态机：     PENDING → RUNNING → SUCCEEDED | FAILED | SKIPPED
                        （approval 节点含 WAITING_APPROVAL 等待态）
    每节点执行 = 一次状态转移；每次转移是持久化事实。
```

### 6.3 理由（为什么不是其它）

```text
为什么不是纯 DAG（A）：
    DAG 无法表达 WAITING（approval 挂起）与 retry 重入；两者是 Phase 6 核心需求。
为什么不是纯 State Machine（B）：
    多步骤业务流的"节点图"比"状态枚举"更直观；状态爆炸直接损害可维护性。
为什么不是 Sequential Pipeline（C）：
    条件分支使"线性"名不副实；一旦引入分支即退化为图，不如直接采用受控图模型。
```

---

## 7. Workflow Definition Decision

### 7.1 候选

| 维度 | A. Python Code | B. YAML / JSON | C. Database | D. Code + Version Metadata |
| --- | --- | --- | --- | --- |
| 类型安全 | 强 | 弱（运行时校验） | 弱 | 强 |
| IDE 支持 | 强 | 中 | 无 | 强 |
| 可测试性 | 强（直接单测） | 中 | 差 | 强 |
| 动态修改 | 无（需部署） | 中 | 强 | 无（需部署） |
| versioning | git | 需自建 | 需自建 | git + run 记录版本 |
| auditability | 强（review/CI） | 中 | 弱 | 强 |
| migration | 随部署 | 随部署 | 独立迁移 | 随部署 |
| security | 强（不可注入执行） | 中（schema 注入面） | 弱（动态定义面） | 强 |
| enterprise maintainability | 强 | 中 | 弱 | 强 |

### 7.2 决策

**决策：D — Python Code Definition + persisted version metadata**

```text
1) Workflow 用 Python 代码定义（frozen 结构 + 服务器端注册表，
   模式对齐既有的 ProjectRegistry / ToolRegistry：静态注册、明确失败）；
2) WorkflowRun 持久化时记录 definition 版本引用（name + version 标识），
   使历史 run 可解释（哪个定义版本产生了它）；
3) 不接受任意用户提交 DSL / DB 动态定义（NG1；无需求 + 安全面）。
```

理由：本项目是 Python backend；已有"服务器端代码注册 + frozen DTO"惯例
（ProjectCapabilities / ToolDefinition / ProjectRegistration）；代码定义获得
类型安全、测试性、review、CI 的全部收益；无低代码需求证据（只为"Workflow"一词
引入 JSON 蓝图是过度工程）。

---

## 8. Persistence Decision

### 8.1 候选实体评估

| 实体 | 是否持久化 | 理由 |
| --- | --- | --- |
| WorkflowDefinition | **否**（代码） | §7 决策；不建表 |
| WorkflowVersion | **否**（run 上的版本引用字段） | 避免版本表与迁移复杂度 |
| WorkflowRun | **是** | Run Lifecycle + Execution State 的 SoT |
| WorkflowNodeRun | **是** | 节点执行事实（per-attempt）的 SoT |
| WorkflowApproval | **是** | 人类审批决策事实的 SoT（独立状态机） |
| WorkflowCheckpoint | **否**（由 WorkflowRun 承载） | 见 8.3 |
| WorkflowEvent / Timeline | **否**（派生投影） | 见 §19 |

### 8.2 决策：三张表（最小集合）

```text
ai_ops.workflow_run         一行 = 一次 Workflow 执行（SoT：Run Lifecycle + Execution State）
ai_ops.workflow_node_run    一行 = 一个节点的一次 attempt（SoT：Node Execution Record）
ai_ops.workflow_approval    一行 = 一个审批节点的决策请求（SoT：Human Approval State）
```

概念要点（字段细节在实现任务书冻结，此处只锁架构约束）：

```text
workflow_run
    - workflow_run_id（独立身份；与 agent run_id 不同 namespace）
    - workflow_name + definition_version（§7）
    - project_id（服务器端解析结果；**必填**——不重复 OQ-P5-25）
    - status（PENDING/RUNNING/WAITING/COMPLETED/FAILED/CANCELLED）
    - current_node_id（执行指针）+ state_version（乐观并发 CAS）
    - cancel 信号列（协作式；不是 status 枚举成员——沿用 Phase 5 语义）
    - input（结构化启动输入；大小边界 + 敏感字段禁令）
    - timestamps（created/started/finished/updated）

workflow_node_run
    - workflow_run_id + node_id + attempt（UNIQUE 身份；attempt 递增，历史保留）
    - status（PENDING/RUNNING/WAITING_APPROVAL/SUCCEEDED/FAILED/SKIPPED）
    - output（结构化节点结果；内联 + 大小边界；超限 fail-closed，
      对齐 Agent Tool Result 的 32KiB 内联先例——**不重跑、不静默截断**）
    - error_code（稳定错误码白名单——**禁止**自由文本/异常正文）
    - timestamps（started/finished）——"先记 RUNNING、后记完成"写入纪律

workflow_approval
    - approval_id（独立身份）+ workflow_run_id + node_id
    - status（PENDING → APPROVED/REJECTED；条件 UPDATE 单赢、终态不可逆）
    - resolved_at（无 TTL / 无通知——与 Phase 5 同策略）
```

### 8.3 为什么没有 WorkflowCheckpoint 表

Agent 的 Checkpoint 存在原因是：Agent 的循环可能在任意步骤中断，需要独立于
Run 状态的"当前控制状态 + CAS + pending 引用"。Workflow 的推进是**逐节点显式**
的：`workflow_run.current_node_id` + `workflow_node_run` 状态即可完整判定
"我在哪 / 下一步是什么 / 上一节点结果是否已 durable"。

```text
恢复判定（幂等）：
    读 workflow_run.current_node_id + 对应 node_run（按 attempt 取最新）：
      - node 无 RUNNING/有 SUCCEEDED ⇒ 该节点已完成 ⇒ 可安全推进
      - node RUNNING 且无终态记录      ⇒ 状态不可判定 ⇒ fail-closed（UNKNOWN）
      - node PENDING                   ⇒ 可安全执行
减少一个表 / 一份 CAS 语义；如未来出现独立 checkpoint 需求（例如更高并发），
可作为新 ADR 拆分——当前不预建。
```

### 8.4 SoT / Projection 分类

```text
SoT          : workflow_run（生命周期+执行态）· workflow_node_run（节点事实）·
               workflow_approval（审批事实）
Projection   : Workflow Timeline（派生；无表）
Execution    : WorkflowNodeRun 即执行记录（不需要与 Agent 的
Record         observation 表对齐的另一套观测表——见 §19）
```

### 8.5 Namespace 纪律（防与 Agent 混淆）

```text
workflow_run_id          ≠ run_id（Agent）          ≠ request_id（trace）
workflow_node_run_id     ≠ step_index（Agent）      ≠ tool_call_id（协议）
workflow: {run} : {node}:{attempt}                    （内部引用格式，非字段锁定）
禁止：Workflow 表出现 run_id / step_index 列名；
      禁止引用 agent_run / agent_step 的任何行为语义。
```

---

## 9. Execution Driver Decision

### 9.1 候选

| 候选 | crash recovery | long-running | HITL | scheduled | 运维复杂度 | 新依赖 |
| --- | --- | --- | --- | --- | --- | --- |
| A. 请求内同步执行 | 部分（状态持久化） | 差（HTTP 时限） | 差（挂起占请求） | 不支持 | 最低 | 0 |
| B. 外部 API 驱动（每步 advance） | 好 | 好 | 好 | 外部触发可 | 低 | 0 |
| C. 进程内后台执行器 | 差（asyncio ≠ durable） | 中 | 中 | 弱 | 中 | 0（但非 durable） |
| D. Worker + Queue | 好 | 好 | 好 | 好 | 高 | Redis/Celery 等——**禁止** |
| E. Scheduler + Worker | 好 | 好 | 好 | 好 | 最高 | 同上——**禁止** |

### 9.2 决策

**决策：A' = Inline Execution until Wait-Point + Durable State + External-Driven Resume**

```text
Start   : 创建 run（持久化）→ 顺序执行就绪节点（请求内 inline）→ 直到
          TERMINAL（COMPLETED/FAILED/CANCELLED）或 WAITING（approval 等）
Resume  : 外部调用 Service API（resume）→ 读取持久化状态 → 从断点继续 inline
Crash   : run 停在最后持久化状态；RUNNING 且无终态记录的节点 = UNKNOWN
          ⇒ fail-closed（**不自动重跑**）；恢复需显式操作
```

该模型与 Phase 5 Agent 完全同构（Agent 的 HITL 恢复即"外部调用 resume"模式），
是本项目**已被验证**的执行范式：

* 短流程（秒级）：一次调用内完成，零额外机制；
* 长流程（分钟级以上）：由 WAITING 点（approval）与显式 resume 分段，
  状态始终 durable，进程重启不丢；
* 明确的架构边界（诚实记录）：
  - **没有**自动推进者——"无人调用 resume"= 停在 WAITING（这是显式语义，
    与 Phase 5 HITL 相同，不是缺陷）；
  - **没有** force kill / 强制超时——in-flight LLM/Tool 调用自然结束（协作式）。

### 9.3 为什么不是其它

```text
为什么不是 B（每步外部 advance）：
    "inline until wait" 已覆盖其全部恢复能力；逐步 advance 只增加调用方负担，
    没有净收益（我们对"跨节点人工节奏"的需求由 WAITING 点表达）。
为什么不是 C（进程内后台执行器）：
    asyncio task 不是 durable execution —— 进程重启即丢，且多进程部署下无协调；
    引入它等于引入一份"看起来能跑、实际不可靠"的假象。
为什么不是 D/E（Worker/Queue/Scheduler）：
    违反 Non-Goals / 依赖纪律；当前无并发吞吐需求证明其必要性。
    未来如出现真实需求（高并发 / 秒级调度 / 多进程推进），
    以独立 ADR 升级 driver —— 届时**状态模型（§8）不需要改变**（driver 可插拔）。
```

---

## 10. Agent Integration Decision

### 10.1 层级与边界（冻结约束的落地）

```text
Workflow Runtime
    ↓ （唯一允许路径）
AIOrchestratorService.execute（及 resume / resolve_approval / cancel ——按需）
    ↓
AgentRuntime（FROZEN；Workflow 不得 import / 不得直达六表）
```

### 10.2 决策

```text
1) ai node：经 AIOrchestratorService.execute()（接受 Router 决定的具体能力）。
   - 复用既有契约：request_id / trace / capability 校验 / outcome 记录全部不变；
   - conversation_context = None（workflow 节点无会话语义）；
   - 该 node 是"AI 能力调用节点"（RAG / TOOL / TEXT_TO_SQL / AGENT 之一）。

2) 子 Agent 的生命周期协调：
   - ai node 命中 AGENT 且返回 WAITING_APPROVAL（Agent HITL）
     ⇒ workflow node = WAITING_APPROVAL（等待子 Agent 裁决；workflow run = WAITING）；
   - workflow resume 时检查子 Agent 状态：
       子 Agent 仍 WAITING_APPROVAL ⇒ workflow resume 幂等返回"仍等待"（不自动裁决）；
       子 Agent 已 APPROVED 并 resume ⇒ workflow 继续下游节点；
       子 Agent 终态 FAILED ⇒ node FAILED（进入 node retry policy / run fail）；
   - **不自动**调用 resolve_agent_approval（人类裁决是外部事实，由人通过
     Agent 既有内部 Service API 完成；workflow 只观察结果）。

3) Workflow cancel → 传播：对 running 子 Agent 调 cancel_agent_run（协作式）。

4) Production Agent Enable：**决策 B（Workflow 装配路径显式启用）**：
   - 唯一 composition root = build_orchestrator_for_project(agent_enabled=True)；
   - 不改 HTTP 入口默认（/api/ai/chat 与 Conversations 保持 agent_enabled=False）；
   - 不开全局开关（C 拒绝）、不改 ProjectCapabilities（D 记录为 Deferred，
     触发条件 = 多项目需要差异化 Agent 能力时另立 ADR）。
```

### 10.3 OD-1「强制 Agent 入口」（已决议 · Option 1 采纳）

```text
问题：ai node 经 execute() 时，Router 可能不返回 AGENT（规则命中 TOOL/RAG 等）。
      若 workflow 语义要求"此节点必须执行 Agent（多步模型驱动）"，
      当前 Service API 列表没有"强制 Agent"入口。

选项 1（推荐先行的最小方案）：
    只提供 ai node（execute()，接受路由结果）。
    - 优点：零修改 Phase 5 冻结面；行为可预测（路由是既有语义）；
    - 代价：无法保证进入 Agent；依赖 Router 规则/LLM fallback 与 agent_enabled。

选项 2（触发式升级）：
    新增 AIOrchestratorService.execute_agent(question, ...)（跳过 Router 强制 AGENT）。
    - 性质：属于"新的 Agent 能力"⇒ 必须走 Phase 5 Frozen §5 变更程序
      （本 ADR 即为该变更的决策载体；获批后实施面极小：新增一个 Service 方法 +
      测试；不修改 Agent Loop / 六表 / 错误码 / Read API）；
    - 缺点：扩大 Phase 5 冻结面的批准负担；无当前需求证明（无真实 workflow
      定义证明必须强制 Agent）。

决议（2026-10-08 批准）：**采用 Option 1** ——
    ai node 默认经 AIOrchestratorService.execute() 进入现有 Router；
    暂不新增 execute_agent()；
    Option 2 保持触发式升级：只有出现真实业务需求时，
    才触发 Phase 5 Frozen §5 变更程序。
状态：RESOLVED（Option 1）。
```

---

## 11. Tool Integration Decision

```text
1) tool node：确定性调用（tool_name + arguments 由 definition/input 提供），
   唯一通道 = 既有 ToolExecutionService.execute()：
       - 不得新建第二套 Tool executor / 不得直达 Handler / 不得直达 Registry；
       - 使用与项目一致的工具实例（per-project ToolExecutionService，
         与 Orchestrator 共享同一实例与 capabilities 白名单）；
       - 必须携带 ToolExecutionContext（request_id / project_id / round /
         tool_call_id）——project_id 与执行边界作用域一致性校验保持有效。

2) 节点能力边界（Node 可以调用什么）：
       - ai node      → AIOrchestratorService.execute（RAG/TOOL/T2S/AGENT）
       - tool node    → ToolExecutionService.execute（白名单内只读 Tool）
       - approval node→ WorkflowApproval（自有持久化）
       - condition    → 纯逻辑（无 IO）
       普通 Service 节点：**不允许**（第一期）。理由：无已证实需求；
       agent/tool 节点已覆盖业务场景；任意 Service 调用会让 Workflow 成为
       隐蔽的代码执行平台，违反最小权限（P7）。未来如需要，采用
       注册表白名单模式（对齐 ToolRegistry），且仅限只读——另立 ADR。

3) Workflow 不直接调用 RAG / Text-to-SQL 内部服务（统一经 execute()），
   避免重复实现 capability 校验 / trace / outcome。
```

---

## 12. Tool Safety Boundary + Retry Decision

### 12.1 Phase 6 Tool Safety Boundary（对 OQ-P5-18 的答复）

**决策：write / external-side-effect Tool 在 Phase 6 全局 BLOCK。**

```text
SB1  Phase 6 不注册、不允许、不重试任何 write / external-side-effect Tool；
     当前生产 registry 仅含 get_inventory / get_work_order（源码证明只读：
     BEGIN READ ONLY + 无 INSERT/UPDATE/DELETE + 无 HTTP 调用）。
SB2  workflow tool node 只能调用"workflow-safe 白名单"内的 Tool
     （初始 = 现有只读 Tool 全集）；白名单是 workflow 侧的显式注册，
     服务器端静态配置，**不因 registry 注册而自动获得 workflow 资格**。
SB3  **不扩展 ToolDefinition**（read_only/idempotent/retryable 等属性保持
     NOT CURRENTLY MODELED）；OQ-P5-18 维持 Deferred；
     触发条件 = 第一个 write/external tool 需求出现（届时另立 ADR，
     走 Tool Framework 契约变更程序）。
SB4  任何解除 BLOCK 的前提：Tool 副作用/幂等可证明 + 重试/并行安全重新论证。
SB5  无法安全证明 ⇒ 保持 BLOCK（宁可缺功能，不放开风险——用户与项目原则一致）。
```

### 12.2 Retry Decision

**决策：Retry Owner = Workflow Runtime（节点级）；其余层保持零 retry。**

```text
R1  重试单元 = 一个 NodeRun 的一次 attempt；每次 attempt 追加一行（历史保留，
    不复写、不覆盖）。
R2  可重试前提（全部满足，缺一不可）：
      a) 节点类型 ∈ {tool（白名单只读）, ai}（condition 不需要；approval 不可重试）；
      b) 失败分类命中 retryable 白名单（稳定错误码；见 R4）；
      c) run 非终态、无 cancel 信号；
      d) attempt < max_attempts（definition 显式配置；runtime 强制硬上限）。
R3  fail-before-retry 纪律：重试只发生在失败事实已持久化（node_run FAILED 行）
    之后；**UNKNOWN / crash 状态绝不自动重试**（fail-closed）。
R4  失败分类（第一阶段，保守）：
      可重试（示例类别，具体码表实现阶段冻结）：
        - LLM 传输类失败（对齐 is_retryable_llm_error 的既有分类语义：
          网络/超时/429/5xx）——经 ai node 上报的稳定错误码；
        - Tool 执行的可恢复故障（如连接暂时失败类稳定码）。
      不可重试：
        - business refusal（T2S refusal 等）· capability/权限拒绝（403 语义）
        - 参数校验失败 · approval REJECTED · LIMIT_REACHED（预算耗尽；
          重试将突破预算语义）· cancel 相关一切。
R5  backoff / delayed retry：**Phase 6 第一阶段不做**（无 timer/scheduler；
    immediate retry only）。Deferred（见 §25；依赖调度决策）。
R6  timeout：不做强制 node timeout（无强制中断机制；与协作式模型一致）。
    RUNNING 超时的判定属 crash 恢复语义（UNKNOWN，fail-closed）。
```

### 12.3 为什么重试是安全的（安全性证明，不是"失败自动重试 3 次"）

```text
1. 重试对象：在白名单（SB2）与 SB1 下，所有可重试节点均无外部副作用：
   - tool node：只读（READ ONLY 事务；无 INSERT/UPDATE/DELETE/HTTP）；
   - ai node ：Agent 内部 Tool 同为只读；LLM 调用无副作用；
   ⇒ 重跑 = 重复计算/成本，无业务副作用。
2. duplicate execution：R3 保证只在"已持久化失败"后重试；
   未知状态一律不重试（fail-closed）——不存在"可能已执行却重跑"的窗口。
3. 一旦 SB1/SB2 被突破（引入 write tool），本证明立即失效 ⇒
   重试资格必须随 Tool 安全边界重新论证（SB4）。
4. Agent / Tool / LLM 层的零 retry 不被修改（Phase 5 冻结 + fail-closed 原则）；
   重试集中一处（workflow 节点），避免多级重试叠加风暴。
```

---

## 13. Parallelism Decision

**决策：Phase 6 第一阶段不支持 parallel（明确 DEFERRED，非"以后再说"）。**

```text
理由（基于真实架构）：
1. 执行驱动是 inline-until-wait（§9）；并行 fan-out 需要为每个分支持久化
   独立的 in-flight 状态与 durable join 协调——当前不存在该机制；
2. 当前无 worker/queue/并发执行资源；asyncio.gather 只能解决"进程内同时跑"，
   解决不了 crash 语义（N 个 in-flight 分支的 UNKNOWN 判定）与 join 一致性；
3. join 语义（all / any / quorum）、branch failure 传播、cancellation 传播、
   branch retry 组合——每一项都是独立的复杂决策，当前无需求证据；
4. Phase 5 冻结已排除"把并行下沉 Agent"的任何路径；workflow 是唯一可能的位置，
   但必须等其自身的持久化/驱动成熟。
```

未来引入的前置条件（防将来重新论证，先记录）：

```text
PC1  per-branch durable child state（fan-out 的原子持久化语义）
PC2  join 初始仅支持 ALL-REQUIRED（全成就）；FAIL-FAST（任一败即败）
PC3  branch failure / cancellation 的统一传播规则（先设计后实现）
PC4  crash 恢复的 UNKNOWN 判定（N 个 in-flight 分支的 fail-closed 策略）
PC5  执行驱动评估（可能需外部 step-driven 或 worker；届时独立 ADR）
```

---

## 14. HITL Decision

**决策：Workflow Approval = 独立状态机 + 独立持久化（workflow_approval 表）。**

```text
H1  流程：
    workflow 到达 approval node
      → node_run = WAITING_APPROVAL + approval 行 = PENDING + run = WAITING
      → 请求返回（§9 的 wait-point）
      → 外部裁决（Service API：resolve_workflow_approval；APPROVED/REJECTED）
      → APPROVED ⇒ resume 后继续 next node；REJECTED ⇒ node FAILED → run FAILED
        （第一阶段 on_reject = fail run；分支语义 Deferred）

H2  单赢 / 幂等 / 冲突语义（对齐 Agent 先例，独立实现）：
      条件 UPDATE 单赢；重复裁决幂等返回既有终态；不同裁决/终态后裁决
      ⇒ 冲突错误 + fail-closed；终态不可逆。

H3  与 Agent Approval 的关系（严格分离）：
      - 不复用、不修改 agent_approval；两个审批状态机独立存在；
      - 嵌套场景（ai node 内的子 Agent 也有 HITL）：
        两层审批各自独立裁决（先 Agent 裁决 + resume，再 workflow resume）；
        workflow 不代理/不自动级联裁决（§10.2）。
H4  TTL / 通知 / 参数改写：无（与 Phase 5 同策略；Deferred）。
```

---

## 15. Cancellation Decision

**决策：协作式取消（cooperative）；不强杀；终态不可逆；传播方向单一。**

```text
C1  cancel_workflow_run(run_id)：
      - 设置 run 的 cancel 信号（条件 UPDATE / CAS；信号列，不新增 status 枚举值）；
      - runtime 在节点边界检查信号 ⇒ 停止调度新节点；
      - 终态 = CANCELLED（≠ FAILED）；不可逆；取消后禁止 resume。

C2  传播（Workflow ⊃ Agent 方向）：
      - running 子 Agent（ai node 命中 AGENT 且仍在执行）
        ⇒ 调 cancel_agent_run(run_id)（协作式；Agent 在安全边界停止）；
      - running Tool：不直接 cancel（等自然返回；结果保留）——
        Agent 内部已有该语义，workflow 不新增 tool 级强杀；
      - **禁止**反向（Agent 不 cancel Workflow）。
      - 边界情况：ai node 调用的 Agent 已进入 WAITING_APPROVAL（无活循环）
        ⇒ workflow cancel 使 approval 失效（裁决后进入 workflow 时被拒绝；
        cancel 与裁决竞争采用单赢语义）。

C3  Race 规则（全部单一赢家，fail-closed）：
      - cancel vs 节点完成：条件 UPDATE 判定（先完成即完成；信号后到则终态化）；
      - cancel vs approval 裁决：条件 UPDATE 单赢；败者冲突错误；
      - cancel vs resume：state_version CAS 单赢。

C4  无 force kill（无需求；in-flight LLM/Tool 自然结束——与 Phase 5 一致）。
```

---

## 16. Project Isolation Decision

**决策：project_id 全部来自服务器端 ProjectRegistry 解析；链路一致性强制。**

```text
I1  WorkflowRun.project_id（+ NodeRun / Approval 冗余携带）：
      来源 = start 时经 ProjectRegistry → ProjectContext 解析结果
      （HTTP 传入的 project_id 仅作"选择已注册项目"的输入，经 registry 校验；
       代码内启动 = 服务端给定 project_id，同样经 registry 验证）；
      **禁止**：runtime memory 猜测 / AgentRun fallback / 信任用户输入直用。

I2  链路一致性（全链必须同 project）：
      Workflow(project_id)
        → per-project Orchestrator（build_orchestrator_for_project(project_id, ...)）
        → Agent（per-project 构造；继承）
        → ToolExecutionService（一致性校验保持有效；不一致 ⇒ fail-closed）
      任何一层解析失败 / 不一致 ⇒ 拒绝启动或拒绝推进（不静默回退默认项目）。

I3  读边界：Workflow Read API 全部 project-scoped；
      不存在与跨 project 使用同一固定文案错误（不泄露存在性）。

I4  教训复用：所有 workflow 持久化实体**都携带 project_id**
      （不重复 OQ-P5-25 的暂停窗口不可见问题）。
```

---

## 17. Scheduling Decision

**决策：Phase 6 不包含 in-repo Scheduler（cron / timer / delayed execution 全部 Deferred）。**

```text
S1  不做：无持久化定时状态、无触发器、无 delayed execution、无 cron。
    理由：无需求证据；引入等于自建 mini-scheduler（含恢复/并发/时区语义），
    违背 Non-Goals 与 Simple First。
S2  边界声明：
      - 企业现有 XXL-JOB 属**业务系统调度**，不是 AI Workflow Scheduler；
      - AI Workflow Scheduler **不属于本项目 Phase 6 范围**；
      - 若部署方需要定时启动 workflow：由**外部调度器**调用
        Start / Resume API（本项目只提供 API；任何外部调度器不进 repo）。
S3  与 Retry 的联动：backoff / delayed retry 依赖调度能力 ⇒ Deferred（§25）。
S4  未来如出现真实需求（日切批次 workflow 等）：独立 ADR，
    优先评估"外部调度器 + API"与"in-repo 最小 timer"两条路径。
```

---

## 18. Read API Decision

```text
A1  独立 WorkflowReadService（**不扩展 Agent Read API / 不修改 Agent Timeline**）。
A2  HTTP surface（架构级决策；路径/字段在 API 契约文档中实现阶段冻结）：
      - 命令端点（§9 外部驱动模型的必要入口）：
          start workflow run / resume / cancel / resolve approval
      - 读端点：
          GET run（Run + NodeRuns + Approvals 的只读视图）
          GET runs（project-scoped 列表；分页）
          GET timeline（派生投影；分页）
A3  复用 Agent Read 的既有安全模式（强制）：
      - Query only（0 写入；AST 审计同标准）；
      - DTO 三层防泄露（内部字段如 claim / state_version 不外暴露）；
      - project-scoped 归属校验 + 同码 404；
      - limit/offset server-side cap。
A4  实现顺序：domain → persistence → runtime → read（API 不是第一优先级，
    但架构上必须存在——见 §9 外部驱动依赖）。
```

---

## 19. Timeline / Event Decision

**决策：Timeline = 派生投影（无 event store；无 workflow_event 表）。**

```text
T1  数据来源 = workflow_run + workflow_node_run + workflow_approval 的组合
    （每源一次查询；无 N+1）；事件 = 可被持久化事实证明者：
      RUN_STARTED · NODE_STARTED（"先记 RUNNING"纪律保证是事实）·
      NODE_RETRY_ATTEMPT · NODE_SUCCEEDED / NODE_FAILED ·
      APPROVAL_REQUIRED / APPROVAL_RESOLVED ·
      RUN_WAITING · RUN_COMPLETED / RUN_FAILED / RUN_CANCELLED
      （不得伪造不可证明的事件——沿用 Agent Timeline 纪律）
T2  排序：occurred_at + event_rank + node 序 + tie-breaker；
    相同输入 ⇒ 相同 Timeline（deterministic；仓储正/逆序无关）。
T3  无 event sourcing：audit 由持久化事实（per-attempt 行）覆盖；
    无 replay-from-events 需求（恢复用 state，不用 events）；
    拒绝理由记录于 §23。
```

---

## 20. Security Boundary

```text
SEC1  Agent 六表 / Agent Read API / Agent Timeline 零修改（Frozen）。
SEC2  Workflow 不写 Conversation / Evidence / agent 六表。
SEC3  Read DTO 禁入清单（沿用并扩展 Agent 审核标准）：
      内部 token / state_version / claim / input 原文（视需要）/
      prompt / SQL / 凭据 / 异常正文 / stack trace / payload 详情。
SEC4  错误码：workflow 自己的稳定错误码白名单（独立于 AGENT_ERROR_CODES；
      不复用 agent 错误码命名空间）；错误响应不含内部信息。
SEC5  日志：request_id / workflow_run_id / node_id / attempt / 稳定错误码；
      禁入：密钥 / token / 业务敏感原文。
SEC6  project / capability / policy 全部服务器端解析（P7）。
SEC7  后端写入纪律：best-effort 持久化边界与 Agent 先例一致（观测/投影写入
      失败不改变控制流）；但**控制状态写入（run/node 状态推进）不是 best-effort**
      ——控制状态写失败 ⇒ fail-closed（停止推进）。
```

---

## 21. Testing Strategy

```text
TS1  沿用既有测试范式（不发明新范式）：
       - 全离线（Fake LLM / Fake Agent service / RecordingHandler 模式）；
       - DB 测试 RUN_DB_TESTS=1 门控；
       - 定向清理 + residue=0 断言；
       - conftest 残留守卫扩展（workflow 三表）——实现阶段实施。
TS2  复用既有测试替身：FakeLLM / FakeClock / RecordingHandler /
      _AgentRouteRouter / DB 助手模式（见审计 §16）。
TS3  必测矩阵（对齐 Phase 5 标准）：
      状态机全转移 · 幂等 resume · fail-closed 全矩阵 ·
      retry 边界（可重试/不可重试/UNKNOWN 不重试）·
      cancel 竞争（node 完成 / approval / resume）·
      project isolation（A↔B 矩阵 + 同码 404）·
      ai node ↔ Agent WAITING 嵌套 ·
      持久化写入点崩溃窗口（Tool result 先例的同构测试）。
TS4  不测不存在的东西：parallel / scheduler / backoff（Deferred 项不写测试）。
```

---

## 22. Migration / Compatibility

```text
M1  建表沿用既有机制：Base.metadata create_all + init_db.py 幂等 DDL；
    三张表落 ai_ops schema（无 Alembic，与项目一致）。
M2  零破坏：Agent 六表 / Read API / Timeline / 错误码 / Conversation /
    Evidence / ToolDefinition 全部零修改（回归门保证）。
M3  新代码默认不启用：Workflow 未装配 / 无 workflow 定义注册 ⇒
    HTTP 不暴露新路由行为变化；workflow 路由/端点新增独立 router。
M4  生产 agent_enabled：按 §10.2 决策 B——仅 Workflow 装配路径显式启用；
    既有 HTTP 入口行为不变（回归门锁定）。
M5  版本兼容：definition_version 只进 run 记录；不引入 definition 表迁移。
```

---

## 23. Rejected Alternatives

```text
RA1  纯 DAG 控制模型          → WAITING/retry 语义无法表达（§6）
RA2  纯 State Machine         → 多步骤业务流状态爆炸（§6）
RA3  YAML/JSON 低代码定义      → 无需求 + 类型/测试/安全劣势（§7）
RA4  DB 动态定义              → 同上 + 迁移/审计复杂度（§7）
RA5  WorkflowCheckpoint 表    → run 承载即可；减少冗余（§8.3）
RA6  Event Store / event sourcing → 无重放需求 + 存储/复杂度成本（§19）
RA7  Worker + Queue（Celery 等）→ 违反 Non-Goals；无吞吐需求（§9）
RA8  进程内后台执行器（asyncio task 推进）→ 非 durable；误导性（§9）
RA9  每步外部 advance API     → inline-until-wait 已覆盖；无净收益（§9）
RA10 强制 Agent 入口（当前）    → 需变更 Phase 5 冻结面；无需求证据（OD-1）
RA11 ToolDefinition 扩展分类    → 无 write tool 需求；变更面大（§12，触发式）
RA12 Workflow 直接持第二套 Tool executor → 违反 Single Boundary（§11）
RA13 任意 Service 节点          → 隐蔽代码执行平台；最小权限（§11）
RA14 Workflow 复用 agent_approval → 两个状态机独立；Frozen（§14）
RA15 复用 agent_run/step 命名    → namespace 冲突；Frozen（§8.5）
RA16 Conversation 承载 Workflow 状态 → 违反 Phase 4/5 边界（§16、§4）
RA17 Evidence 记录 Workflow 执行 → 违反 Source/Provenance 语义（NG11）
RA18 in-repo Scheduler          → 无需求；自建 mini-scheduler 复杂度（§17）
RA19 Parallel 第一阶段          → join/failure/crash 语义未成熟（§13）
RA20 forward+backoff 重试第一阶段 → 无 timer；immediate only（§12）
```

---

## 24. Phase 6 Scope

```text
IN SCOPE（第一阶段实现目标）：
S1  Workflow Definition（Python code + registry + version 记录）
S2  Workflow Runtime（受控 DAG + 状态机；ai / tool / approval / condition 节点）
S3  Execution Driver（inline-until-wait + durable state + external resume）
S4  Persistence（workflow_run / workflow_node_run / workflow_approval；ai_ops）
S5  Retry（immediate；节点级；分类白名单；fail-before-retry）
S6  HITL（Workflow Approval 独立状态机 + 裁决 Service API）
S7  Cancellation（协作式 + 单赢语义 + 向子 Agent 传播）
S8  Project Isolation（全链 project_id；服务器端解析）
S9  Read Service + Timeline（派生投影）+ HTTP 读/命令端点
S10 测试（离线 + DB 门控 + 残留守卫扩展）
S11 Agent 集成（ai node 经 execute()；装配路径 agent_enabled=True；
    WAITING 嵌套协调）
S12 Tool 集成（tool node 经 ToolExecutionService；workflow-safe 白名单）
```

---

## 25. Phase 6 Deferred（带触发条件）

```text
D1  Parallel Execution          触发：真实并发业务需求 + PC1–PC5 前置完成
D2  Scheduled Workflow          触发：定时批次需求 + 调度路径 ADR（优先外部调度器）
D3  Backoff / Delayed Retry     触发：D2 或 WAITING+timer 路径确立
D4  Node Timeout Enforcement    触发：需要中断长节点（需强制机制决策）
D5  Tool Classification（ToolDefinition 扩展）触发：第一个 write/external tool 需求
D6  Write Tools / Write Nodes   触发：AI 执行写操作需求（Phase 7 治理 + SB4 论证）
D7  Project-level agent_enabled（capabilities 扩展）触发：多项目差异化 Agent 能力
D8  Conversation 触发 Workflow   触发：对话式启动需求（独立组合 ADR）
D9  Evidence 关联                触发：业务证据溯源需求（经 evidence service）
D10 Workflow UI / Streaming      触发：产品需求（独立 ADR）
D11 执行驱动升级（worker/step-driven）触发：并发/吞吐/多进程需求
D12 Approval TTL / 通知          触发：运营需求（同 Phase 5 策略）
```

---

## 26. Phase 6 Exit Criteria

```text
EC1  全部 IN SCOPE 项实现并通过全量离线 + 全量 DB 回归（0 破坏）
EC2  Agent Frozen 契约零修改（AST / diff 双证）
EC3  Workflow 与 Agent 命名/表/状态机零混淆（差分审计）
EC4  fail-closed 全矩阵通过（UNKNOWN / 冲突 / 越权 / 跨 project）
EC5  幂等性：resume / cancel / approve / retry 重复调用幂等（DB 实测）
EC6  崩溃恢复语义测试通过（RUNNING UNKNOWN 不自动重跑）
EC7  security 审计：Read DTO 禁入扫描 = 空；错误码白名单稳定
EC8  文档：architecture.md 更新 + Phase 6 Exit Audit + Frozen 声明
EC9  本 ADR 被批准并归档（PROPOSED → ACCEPTED）
EC10 任何 Deferred 项未被偷偷夹带（diff 审计）
```

---

## 27. Architecture Diagram

```text
                        HTTP / API
          ┌──────────────────┴──────────────────┐
          │                                     │
   Workflow API                          Agent Read API（FROZEN，只读）
（start/resume/cancel/approve/read）             │
          │                                     │
          ▼                                     ▼
  WorkflowRuntime（Application 层入口）     AgentReadService
          │                                     │
          ├── workflow_run / workflow_node_run / workflow_approval（ai_ops）
          │
          ├── Node Executor ──────────────────────────────┐
          │      ├── condition  → 纯逻辑                  │
          │      ├── approval   → WorkflowApproval        │
          │      ├── tool       → ToolExecutionService ───┼──→ ToolRegistry → Handler → PostgreSQL
          │      └── ai         → AIOrchestratorService ──┼──→ Router → RAG / Tool / T2S / AGENT
          │                                               │              ↓
          │                                               │        AgentRuntime（FROZEN）
          │                                               │         ├── LLM（ProviderAgentLLM）
          │                                               │         ├── Tool（同一 ToolExecutionService）
          │                                               │         └── 六表 ports
          │                                               │
          └──（取消传播 cancel_agent_run / resume 协调）────┘

Sibling Runtimes：
    WorkflowRuntime  ⇄  AgentRuntime   （平级；只经 AIOrchestratorService Service API 交互）
    WorkflowRuntime  →   ToolExecutionService（唯一 Tool 边界）
```

---

## 28. Decision Summary

| # | 决策点 | 结论 | 关键理由 |
| --- | --- | --- | --- |
| D1 | Control Model | DAG + State Machine Hybrid（受限版） | branch/approval/retry 需状态机；结构需图 |
| D2 | Definition | Python Code + version metadata | 类型安全 / 测试性 / 无低代码需求 |
| D3 | Persistence | 3 表（run / node_run / approval）；无 checkpoint 表 | run 承载执行态；最小集合 |
| D4 | Execution Driver | Inline-until-wait + durable + external resume | 与 Phase 5 同构；无新基础设施 |
| D5 | Agent Integration | 只经 4 个 Service API；装配路径显式启用 Agent | Frozen §6；不改生产默认 |
| D6 | Tool Integration | 只经 ToolExecutionService；tool node ⊆ 只读白名单 | Single Boundary；安全 |
| D7 | Tool Safety | write tool BLOCK；不扩展 ToolDefinition；workflow 侧白名单 | 无法证明安全 ⇒ 不放开 |
| D8 | Retry | Owner = Workflow（节点级）；immediate only；fail-before-retry | 集中归属；无副作用证明 |
| D9 | Parallel | Deferred（带 PC1–PC5 前置条件） | join/crash/failure 语义未成熟 |
| D10 | HITL | 独立 WorkflowApproval 状态机（不改 agent_approval） | 两状态机严格分离 |
| D11 | Cancellation | 协作式 + 单赢 + 向子 Agent 传播 | 同 Phase 5 范式 |
| D12 | Project Isolation | 全部服务器端解析；所有实体携带 project_id | 不重蹈 OQ-P5-25 |
| D13 | Scheduling | 不做（外部调度器调 API 为部署选择） | 无需求；不进 repo |
| D14 | Read API | 独立 WorkflowReadService（不扩展 Agent Read） | Frozen + 安全模式复用 |
| D15 | Timeline | 派生投影（无 event store） | 同 Agent 先例；无重放需求 |
| D16 | Boundaries | Conversation / Evidence / 六表 零耦合 | Phase 4/5 隔离原则 |
| D17 | Naming | WorkflowRuntime / workflow_run / node_id / attempt | 防 Orchestrator / Step 冲突 |
| OD-1 | 强制 Agent 入口 | **RESOLVED**：Option 1 采纳（ai node 经 execute() 进 Router）；Option 2 触发式 | 批准决议（2026-10-08） |

词汇表（冻结，Phase 6 全部文档/代码遵守）：

```text
WorkflowRuntime / WorkflowRun / WorkflowNode / WorkflowNodeRun / WorkflowApproval
workflow_run_id / node_id / attempt / definition_version
禁止：Workflow 语境使用 "Orchestrator"（保留给 AIOrchestrator）、
      "step"（保留给 AgentStep）、"run_id" 裸词（Agent 语义）
```

---

## 29. STOP

```text
本文档 = Phase 6 唯一架构入口（ACCEPTED · 2026-10-08）。
实现必须逐条对齐本文档；任何偏离必须新建 ADR。
实施顺序（§18 A4）：Domain → Persistence → Runtime → Read Service → API。
Phase 5 Agent Runtime = FROZEN（零修改；只经 4 个 Service API 交互）。
```
