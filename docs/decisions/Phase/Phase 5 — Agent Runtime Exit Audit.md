# Phase 5 — Agent Runtime Exit Audit

> 状态：**AUDIT COMPLETE · NO BLOCKER · NO CODE CHANGE**
> 性质：只读架构收口审计（Audit → Identify → Classify → Minimal Fix if required →
> Regression → Freeze → STOP）
> 结论：Phase 5 = **FROZEN**（见 `Phase 5 — Agent Runtime Architecture Frozen.md`）
> 基线 commit：`06cdebf`（分支 `phase5-1`）

---

## 0. 审计方法与范围

```text
只读证据          git log / git status / AST / dataclass 字段 / ORM 列 / 运行时测试
被测对象          backend/app/agent/**（16 模块）· services/{ai_orchestrator_service,
                  agent_read_service}.py · api/agent_read.py · dto/agent_*.py ·
                  db/models/agent_*.py · db/agent_*_repository.py
回归              全量离线 + 全量 DB（真 PostgreSQL）+ compileall + git diff
禁止事项          本阶段**未**新增能力 / 未重构 / 未优化 / 未改 schema
```

Phase 5 全部提交（本审计基线）：

```text
06cdebf feat: Phase 5 agent read API and timeline          ← P-8
e551b0a Phase 5 S-1 — Agent Tool Execution Observation 补齐
8562d43 test: Phase 5 P-7 approval pause projection coverage
e243732 feat: Phase 5 HITL and cooperative cancellation    ← P-7
5ed6ba5 feat: Phase 5 agent resume and replay              ← P-6
a4ca8ff feat: Phase 5 agent checkpoint persistence         ← P-5
931aa4a test: Phase 5 agent tool result integration assembly coverage
cc14a93 feat: Phase 5 agent tool result integration        ← P-4 Integration
ba20f9d feat: Phase 5 agent tool result store              ← P-4
fb7c53a docs: freeze Phase 5 tool replay and idempotency decision  ← P-2/P-3
c6c2ac6 feat: Phase 5 agent step persistence               ← P-1
4febb07 feat: Phase 5 agent tool execution observation      ← S-1
```

---

## 1. Phase 5 Capability Matrix（§五；按真实代码填写）

| Capability | Phase 5 State | Source of Truth | Runtime Owner | Persistence | Deferred |
| --- | --- | --- | --- | --- | --- |
| Agent Loop | READY | 无（纯内存控制流） | `agent/runtime.py::AgentRuntime.run/_run_scoped`（唯一主循环；`resume` 复用同一循环） | 结果投影 → `agent_run` / `agent_step` | Streaming / 并行 / 多 Agent |
| Tool Call | READY | `agent_decision`（Decision Replay Source） | `agent/llm.py::ProviderAgentLLM`（provider-native function calling，0/1 tool call/轮） | 写入点 = Decision Store（`decision_ref = run:step`） | 多 tool call / 文本 fallback 协议 |
| Tool Authorization | READY | `ProjectCapabilities` + `AgentRuntimePolicy`（装配期冻结） | `AgentRuntime`（第一道）+ `ToolExecutionService`（第二道 capability） | 不落盘（授权是装配期事实） | Tool 级 policy / RBAC（Phase 7） |
| Tool Result | READY | `agent_tool_result`（Tool Result Replay Source） | `AgentToolResultWritePoint`（Tool handler 返回后**立即**写；在 Run/Checkpoint 落盘之前） | `agent_tool_result`（UNIQUE(run_id, step_index)；32 KiB 内联 / 超限不可 Replay） | Artifact / Result Detail / 加密 / retention |
| Replay | READY | `agent_decision` + `agent_tool_result` | `AgentRuntime.resume`（**不**重跑 handler；**不**重问 LLM） | 读 Decision / Tool Result（0 写入） | 内容级 dedup（OQ-P5-9） |
| Resume | READY | `agent_checkpoint`（Recovery Control State） | `AgentRuntime.resume` + `AIOrchestratorService.resume_agent_run` | `agent_checkpoint`（state_version CAS + claim_token；**不**换 claim） | lease / TTL / claim rotation |
| HITL | READY | `agent_approval`（Human Approval State） | `AgentRuntime`（执行前暂停）+ `AIOrchestratorService.resolve_agent_approval` | `agent_approval`（PENDING → APPROVED/REJECTED，条件 UPDATE 单赢）+ `agent_checkpoint`（PAUSED/WAITING_APPROVAL） | 审批 TTL / 通知 / 参数改写 / HITL UI |
| Cancellation | READY | `agent_checkpoint.cancel_requested`（信号，**不是** status 枚举） | `AgentCancellationCoordinator`（信号 CAS）+ `AgentRuntime` 安全边界检查 | `agent_checkpoint`（信号列）+ `agent_run.status = CANCELLED` | lease / 强制中断 / Restart |
| Read API | READY | **派生**读模型（不存在独立 SoT） | `AgentReadService` + `api/agent_read.py` | 只读 6 表（0 写入） | UI / Streaming / cursor / RBAC |
| Timeline | READY | **派生**读投影（**无** timeline 表） | `AgentReadService._build_timeline`（纯函数式组合） | 只读 6 表（0 写入） | SSE / 事件推送 / 快照隔离 |

### 1.1 任务书 §四 十四项能力逐条确认

| # | Capability | State | 证据 |
| --- | --- | --- | --- |
| 1 | Agent Decision | **READY** | `decision.py`（严格校验 0/1 tool call）、`llm.py` 适配器、P-6 Decision Store |
| 2 | Tool Authorization | **READY** | `allowed_tools` 三重校验（装配 / Runtime / 执行边界）+ Approval 后**重新授权** |
| 3 | Tool Execution | **READY** | 唯一通道 `ToolExecutionService.execute`（Runtime 不 import Registry / Handler） |
| 4 | Tool Result Persistence | **READY** | P-4 写入点在 handler 之后**立即**落盘（崩溃窗口覆盖，DB 实测） |
| 5 | AgentRun | **READY** | `agent_run`（UNIQUE(run_id)；终态执行记录） |
| 6 | AgentStep | **READY** | `agent_step`（UNIQUE(run_id, step_index)；安全投影，无 arguments/output） |
| 7 | AgentDecision | **READY** | `agent_decision`（UNIQUE(run_id, step_index) + UNIQUE(decision_ref)） |
| 8 | AgentCheckpoint | **READY** | `agent_checkpoint`（state_version CAS + claim_token + cancel 信号；PAUSED/WAITING_APPROVAL） |
| 9 | Tool Replay | **READY** | handler 0 次重跑（离线 + 真 DB 双证）；失败重放为失败 |
| 10 | Resume | **READY** | `AGENT_ERROR_RUN_NOT_RESUMABLE` / fail-closed 全矩阵；run_id 不变 |
| 11 | HITL Approval | **READY** | 审批在 Tool 执行**之前**；Tool=0 于暂停期（DB 实测 agent_run=0） |
| 12 | Cooperative Cancellation | **READY** | 信号 + 安全边界；running Tool **不**强杀、结果保留；`run.status=CANCELLED` |
| 13 | Agent Read API | **READY** | 2 个 GET 端点；AST 审计 = DB read + DTO（无写 / 无命令） |
| 14 | Agent Timeline | **READY** | 11 类型白名单（10 可证明）；deterministic 排序；无 timeline 表 |

**PARTIAL / BLOCKED：无。** 唯一被显式推迟的是"暂停中 Run 的读可见性"（§3，Deferred，非 blocker）。

---

## 2. Source of Truth Matrix（§六）

| 事实域 | Source of Truth | 明确**不是** |
| --- | --- | --- |
| Run Lifecycle | `ai_ops.agent_run`（status / counts / timing） | Timeline 的 `sequence`；Read API 响应；Runtime 内存 |
| Step Projection | `ai_ops.agent_step`（step_index / type / status / 错误码 / 耗时） | Tool 执行明细（属 observation） |
| Decision Replay Source | `ai_ops.agent_decision`（type / tool_name / tool_call_id / arguments / content） | UI 数据（Read API **不**暴露 arguments / content） |
| Tool Result Replay Source | `ai_ops.agent_tool_result`（success / payload / hash / 尺寸边界） | Timeline 展示数据（Read API **不**暴露 payload / ref） |
| Recovery Control State | `ai_ops.agent_checkpoint`（state_version / claim_token / next_action / pending_* / cancel 信号） | Run status（`next_action` ≠ `status`；有专项测试）；Read API（**不**暴露 claim_token） |
| Human Approval State | `ai_ops.agent_approval`（PENDING → 终态，条件 UPDATE 单赢） | Tool Result（审批**不是**执行结果） |
| Conversation State | Conversation 表（Phase 4） | Agent checkpoint（Conversation **不**存 Checkpoint / Approval / Cancellation） |
| Source / Provenance | Evidence 表（Phase 4） | Agent 执行日志（Evidence **不**记录 Agent 执行） |

**禁止等式核查（全部通过）**：

```text
Timeline = Source of Truth            ✗ 未发生：Timeline 由 6 表组合派生、无表、不可写
Read API = Source of Truth            ✗ 未发生：AST 审计 = 只读；无 create/update/delete/命令
AgentRuntime memory = durable SoT     ✗ 未发生：Runtime 0 DB import；所有 durable 事实在 6 表
Conversation = Agent checkpoint       ✗ 未发生：conversation_service 0 agent import
Evidence = Agent execution log        ✗ 未发生：agent 包 0 evidence import；Evidence 未改
```

---

## 3. HITL Project Ownership OQ（§七）→ **Decision A：保持 404 + DEFERRED**

### 3.1 事实（真实代码 / ORM 列实测）

```text
agent_run            project_id = ✅
agent_tool_result    project_id = ✅
agent_step           project_id = ✗
agent_decision       project_id = ✗
agent_checkpoint     project_id = ✗
agent_approval       project_id = ✗
```

⇒ HITL 刚暂停（尚未执行任何 Tool）时，**不存在**任何带 project 的持久化行。

### 3.2 当前行为（P-8 真实实现，已由 DB 测试固定）

```text
GET /api/agent/runs/{run_id}（暂停中）
    → 服务层归属判定：Run 行不存在 ⇒ 回退 Tool Result ⇒ 无行
    → AgentRunNotFoundError（固定文案）
    → HTTP 404（不泄露存在性）
    → fail-closed；**不**写任何表（DB 测试断言行数 / state_version 不变）
批准 + Resume ⇒ Tool 执行 ⇒ Tool Result 带 project ⇒ **立即可读**（同一 run_id）
```

### 3.3 判定：**A（合理的 Phase 5 安全边界）**

理由：

1. **安全性**：当前行为是 fail-closed（拒绝而非泄露），不是漏洞；
2. **正确性**：Agent 执行 / HITL / Resume / Cancellation 全部正常；缺的只是"暂停窗口的
   **只读可见性**"，而不是控制能力（内部 `resolve_agent_approval` / `cancel_agent_run`
   只以 run_id 操作，不受影响）；
3. **不属于 §二十一 Regression Gate 的任何一项**（Project Isolation = 强制生效，PASS）；
4. **B 方案的代价超出"最小修复"**：
   * B1 `agent_checkpoint` 增加 `project_id`：改**已冻结**的 P-5 schema（无 Alembic ⇒
     需幂等 DDL）+ 所有 Checkpoint 写入点必须携带 project_id + Orchestrator 必须提供
     project 上下文（当前 `_capability_project_id()` 在测试装配下为 `None`）+
     Read Service 归属规则改序；
   * B2 同时给 `agent_decision` / `agent_approval` 加 project ⇒ 更大；
   * B3 新建 "run ownership" 表 ⇒ 违反"**不**重新设计持久化结构"；
   * B4 信任调用方传入的 project（不校验）⇒ **破坏隔离**，不可接受。
   两个候选设计（control-state 作用域归属 vs run/tool-result 作用域归属）各有正当理由，
   **无法证明哪个正确** ⇒ 按任务书要求"**不要修改**，记录 DEFERRED"。

### 3.4 Deferred 记录（供 Phase 6+ 决策）

```text
OQ-P5-25  Agent Run Ownership Source
    现象：暂停中且无 Tool Result 的 Run 在 Read API 不可见（404，fail-closed）
    候选：B1 控制状态补 project_id（checkpoint/approval/decision）
          B2 runtime 侧在暂停写入点携带 project_id（不改读侧语义）
          B3 保留现状，UI/调用方改经内部 Service API 观察暂停窗口
    约束：任何方案都不得放宽隔离；必须新建 Architecture Decision 后再改
    现状：**保持现状**（404 + fail-closed），Phase 5 不修改
```

---

## 4. Cancellation Audit（§二十四–§三十八）

| 检查项 | 结论 | 证据 |
| --- | --- | --- |
| 信号而非状态枚举 | PASS | `agent_checkpoint.cancel_requested/_at`（2 列；`status` 枚举未新增 `CANCEL_REQUESTED`） |
| 信号 CAS | PASS | `request_cancel` 单语句条件 UPDATE（`cancel_requested=false` → true，version+1）；重复 / 终态 ⇒ `CHECKPOINT_CANCEL_CONFLICT` |
| 安全边界检查 | PASS | LLM 前 / LLM 返回后 / Tool 前 / Tool 返回后（`_cancel_requested`；读失败 ⇒ 视为未取消） |
| running Tool 不强杀 | PASS | AST 审计：`cancellation.py` 不 import asyncio/threading/os/signal，无 `cancel`/`kill` 调用；handler 完整返回、结果落盘 |
| 终态 | PASS | `agent_run.status = CANCELLED`（**不**映射 FAILED，DB 实测）+ `checkpoint TERMINAL/CANCELLED` |
| 终态不可逆 | PASS | `_save` 守卫：取消终态**不**被后到的非取消终态覆盖 |
| 幂等 / no-op | PASS | 重复取消幂等；终态 run ⇒ `AGENT_RUN_NOT_CANCELLABLE`（no-op，无写入） |
| 禁止 Resume | PASS | `AGENT_RUN_NOT_RESUMABLE`（LLM 0 / Tool 0，DB 实测） |
| HITL 竞争 | PASS | `AgentCancellationCoordinator`（CAS）+ 审批条件 UPDATE ⇒ 单赢；PENDING → CANCELLED 后 approve ⇒ `AGENT_APPROVAL_CONFLICT` |

---

## 5. Replay / Resume Audit（§五十六 / P-6 冻结项）

| 检查项 | 结论 | 证据 |
| --- | --- | --- |
| Decision Replay | PASS | 已持久化 Decision ⇒ **不**重新调用 LLM（离线 + DB：`llm_calls == 0` 的 FINAL 路径） |
| Tool Replay | PASS | 已持久化 Tool Result ⇒ handler **0** 次（`registry.execute_calls == []`） |
| 失败重放 | PASS | 持久化失败事实 ⇒ 重放为失败（不重试、不换 Tool） |
| 缺失 ⇒ fail-closed | PASS | `AGENT_DECISION_NOT_FOUND` / `AGENT_TOOL_RESULT_NOT_FOUND` / `AGENT_*_UNAVAILABLE`（LLM 0 / Tool 0） |
| READ_ONLY ≠ 可盲重跑 | PASS | 无 blind rerun 路径；`AGENT_*_NOT_FOUND` 即拒绝 |
| 并发 Resume | PASS | `state_version` + `claim_token` claim ⇒ 单赢；败者 `AGENT_RESUME_CONFLICT`（不 retry / 不 takeover） |
| 假 Step 不产生 | PASS | Resume 后的 step 投影只含新产出 step（DB 实测） |
| 计数语义 | PASS | 只统计**本次**真实调用（Replay 不计）；`steps` 含历史逻辑步 |

---

## 6. Read API Audit（§十三 / §十四 / §十五）

### 6.1 Mutation Audit（AST 实测）

```text
api/agent_read.py          调用集合 = {get, get_run, get_timeline, getLogger, error}
services/agent_read_service.py
                           调用集合 = {get, list_for_run, sort, append, strip,
                                       _validated_*, _*_view, _load, _build_timeline}
禁止集合（create/insert/update/delete/execute/resume/approve/cancel/record/
          arecord/request_cancel/resolve/claim_for_resume）：**0 命中**
DB 直连（sqlalchemy / sessionmaker / Session( / Engine(）：**0 命中**
```

⇒ Read API = `DB Read → DTO → Response`（§十三 PASS）。

### 6.2 Project Isolation（§十四）

```text
服务层签名            get_run(run_id, project_id)（**无**"只有 run_id"入口）
归属来源              ① agent_run.project_id ② 回退 agent_tool_result.project_id（必须恰好相等）
run.project_id NULL   回退 Tool Result；两者皆无 / 冲突 ⇒ fail-closed（404）
矩阵（真 DB）         A↔A 200 · A↔B 404 · B↔B 200 · B↔A 404
不泄露              不存在与跨 project **同一固定文案**（测试断言两次 body 相等）
跨 project 读代价     只读 Run + Tool Result（其余 4 源零查询）
```

### 6.3 Security Boundary（§十五；三层 + 递归扫描）

```text
层 1  DTO 字段        无 claim_token / arguments / content / payload / ref / prompt / SQL / 凭据
层 2  mapper           显式逐字段映射（不用 model_dump / asdict）
层 3  response_model   Pydantic 过滤边界（内部 DTO 新增字段不会自动泄漏）
递归扫描（DTO + Response，会话内实测）  禁入字段命中 = **[]**
禁入清单  claim_token · tool arguments · prompt · system prompt · messages · context ·
          RAG chunks · SQL · database_url · api_key · authorization · password ·
          raw_response · exception body · stack trace · headers ·
          tool result payload · tool result ref · decision content
```

---

## 7. Timeline Audit（§八 – §十一）

### 7.1 是 Read Projection，不是 Event Store

```text
db/models 中 timeline / event 模型文件 = []（无 agent_timeline 表）
service / dto / api 中 event_store / event_sourcing / EventStore = 0 命中
Timeline 数据来源 = 6 张既有表的内存组合（每源 1 次查询，无 N+1）
```

### 7.2 事实完整性（每个事件都有真实持久化事实）

| 事件 | 事实来源 | 是否可证明 | 状态 |
| --- | --- | --- | --- |
| RUN_STARTED | `agent_run.started_at` | ✅ | 产生 |
| DECISION_RECORDED | `agent_decision.created_at` | ✅ | 产生 |
| APPROVAL_REQUIRED | `agent_approval.created_at`（当时必为 PENDING） | ✅ | 产生 |
| APPROVAL_RESOLVED | `agent_approval.resolved_at`（仅已裁决） | ✅ | 产生 |
| TOOL_EXECUTED | `agent_tool_result.created_at`（OK / FAILED + error_code） | ✅ | 产生 |
| STEP_COMPLETED | `agent_step.created_at` + status + elapsed_ms | ✅ | 产生 |
| RUN_COMPLETED / FAILED / CANCELLED / LIMIT_REACHED | `agent_run.finished_at` + status（逐字映射） | ✅ | 产生 |
| STEP_STARTED | —— 无开始时刻事实 | ❌ | **不产生**（白名单保留） |
| LLM_REQUEST_STARTED / FINISHED | —— 无持久化事件源 | ❌ | 不在白名单（永不产生） |

事件数 == 可证明事实数（专项测试）；未知终态 ⇒ **无**终态事件（不猜）。

### 7.3 Ordering（deterministic）

```text
sort key = (occurred_at, event_rank, step_index, event_type)   ← 全序
event_rank: RUN_STARTED 0 < DECISION_RECORDED 1 < APPROVAL_* 2 < TOOL_EXECUTED 3
            < STEP_COMPLETED 4 < RUN_TERMINAL 9
实测：相同输入 ⇒ 相同 Timeline（逐字相等）；仓储正序 / 逆序返回 ⇒ 结果相同
跨钟域：run 时间戳=应用时钟、其余=DB 写入时刻 ⇒ 终态事件**不保证**最后
        （不重排 / 不修正；文档化 + DB 实测）
```

### 7.4 Sequence 语义（§十一）

```text
sequence = projection index（排序后 0..n-1；分页不重新编号，可无损拼接）
sequence ≠ event_id ≠ database primary key ≠ global ordering id
（DTO docstring + API Field description + 决策文档三处明确）
```

### 7.5 Pagination（§十二）

```text
1 <= limit <= 100（默认 50；<=0 / >100 / bool ⇒ 400）· offset >= 0（负数 ⇒ 400）
server-side cap 在服务层强制执行（`limit=1000000` ⇒ 400，**绝不**透传 SQL）
路径长度越界 ⇒ 422（FastAPI Path，沿用项目风格）；空白 run_id ⇒ 400
```

---

## 8. Agent Runtime Boundary（§十六）

### DOES

```text
LLM decision（0/1 tool call；严格校验）· Tool authorization（三重）·
Tool execution orchestration（唯一通道 + 结果立即持久化）· Loop control（有界预算）·
Replay（Decision / Tool Result）· Resume（乐观 claim）· HITL（执行前暂停 + 复授权）·
Cancellation（协作式安全边界）
```

### DOES NOT（AST / 依赖实测）

```text
Conversation ownership     → agent 包 0 conversation import；Conversation 0 agent import
DB direct access           → 13 个 agent 内核模块 db_imports = []
HTTP API                   → agent 包 0 fastapi/starlette import（API 层单独存在）
Evidence                   → agent 包 0 evidence import；Evidence 未修改
Project RBAC               → 仅 project-scoped query boundary（Phase 7 属 RBAC）
Workflow definition        → 无 workflow / 无 retry policy / 无并行（Phase 6）
Multi-Agent orchestration  → 单 Agent（ONE Agent）
```

Tool Framework 侧同样反向 0 依赖：`tools/**` 与 `tool_execution_service.py`
**0** agent import（Tool Framework Agent dependency = 0）。

---

## 9. Phase 5 / Phase 6 边界（§十七，冻结）

```text
Phase 5 = Bounded Agent Runtime（模型驱动）
    LLM decides → Tool → LLM decides → Tool → … → FINAL
    控制流由**模型**决定；有界（步数 / 成本 / 时间预算）；失败即 fail-closed

Phase 6 = Workflow Runtime（开发者定义）
    deterministic business flow · branch / condition · approval gate ·
    parallelism · retry policy · scheduled execution · long-running workflow
    控制流由**代码/定义**决定；需要 DAG / 状态机 / 补偿等独立机制
```

**不把 Workflow 能力塞进 Agent Runtime**：Agent Runtime 冻结后不得出现
branch DSL / 并行节点 / retry policy / 调度器 / 补偿 / Saga。

---

## 10. Regression Gate（§二十一）

| Gate | 结果 | 证据 |
| --- | --- | --- |
| P-6 Resume | **PASS** | `test_agent_resume.py` 23 · `test_agent_resume_integration.py` 6(+2 skip) · DB 全链 |
| P-7 HITL | **PASS** | `test_agent_hitl.py` 47 · `test_agent_hitl_db.py` 24 · 暂停期 Tool=0 / agent_run=0 |
| P-7 Cancellation | **PASS** | `test_agent_cancellation.py` 21 · `test_agent_cancellation_db.py` 12 · CANCELLED 终态 |
| P-8 Read API | **PASS** | `test_agent_read_service.py` 40 · `test_agent_read_api.py` 30 · `test_agent_read_db.py` 10 |
| P-8 Timeline | **PASS** | `test_agent_timeline.py` 25 · 顺序确定性 / 无伪造 / 无表 |
| Tool Replay | **PASS** | handler 0 次（离线 + DB） |
| Decision Replay | **PASS** | LLM 0 次（FINAL 路径） |
| Project Isolation | **PASS** | 真 DB 矩阵 A↔A / A↔B / B↔B / B↔A + 同码 404 |
| Security Boundary | **PASS** | 递归禁入字段扫描 = [] · claim_token 三层不暴露 · 哨兵串扫描 |

```text
Agent 专项离线：636 passed / 0 failed（21 个文件；6 skips = DB 门控）
Agent 专项 DB ：98 passed / 0 failed（7 个文件）
全量离线     ：6702 passed / 0 failed / 856 skipped
全量 DB      ：7511 passed / 3 failed / 44 skipped
               （3 = **既有 order-dependent**：#MatrixExecutionBaseline ×2 +
                 #MatrixBaselineGate ×1；与 Phase 5 无关，未修改）
DB residue   ：10 张表全部 = 0（agent_run/step/decision/tool_result/checkpoint/
               approval + tool_execution_record/llm_usage_record/
               assistant_outcome_record/rag_execution_record）
compileall   ：0 errors
git diff     ：工作区干净（本阶段 **0** 代码改动）
```

⇒ **Phase 5 = READY（全部 Gate PASS）**

---

## 11. Minimal Fixes（§十九）

```text
NONE（本阶段 **未**修改任何生产代码 / 测试 / schema）
```

Audit 未发现 Phase 5 blocker；HITL Ownership OQ 判定为 Deferred（§3），
按任务书"无法证明哪个方案正确 ⇒ 不要修改"处理。

---

## 12. Deferred Items（Phase 6+ 输入）

| ID | 事项 | 现状 | 归属 |
| --- | --- | --- | --- |
| OQ-P5-25 | Agent Run Ownership Source（暂停窗口读可见性） | 404 + fail-closed（保持） | Phase 6+ 决策后再改 |
| OQ-P5-18 | Tool 副作用分类声明与强制 | 未实现（约定为只读） | Phase 6+ / Tool 治理 |
| OQ-P5-19 | FAILED 是否策略化重试 | 不重试（fail-closed） | Phase 6 Workflow retry policy |
| OQ-P5-20 | 大结果外部 artifact 存储与 retention | 32 KiB–1 MiB 不可 Replay | Phase 6+ |
| OQ-P5-21 | application-level encryption / PII redaction | 依赖 DB 层 + 最小化 | Phase 7（Governance） |
| OQ-P5-22 | READ_ONLY 在 UNKNOWN 边界的 opt-in 重跑 | 禁止 blind rerun | Phase 6+ 决策 |
| OQ-P5-23 | agent_tool_result / checkpoint / approval retention | 无 retention | Phase 7 |
| OQ-P5-24 | Checkpoint ↔ result store 引用方向 | 已按"引用 + 身份"落地 | 已闭环（保留记录） |
| 未编号 | HITL 审批 TTL / 通知 | 无 TTL（`AGENT_APPROVAL_EXPIRED` 未实现未注册） | Phase 6+ |
| 未编号 | lease / TTL / claim rotation / force kill | 无（单写者假设） | Phase 6+ |
| 未编号 | Agent UI / Streaming / SSE / WS | 无 | 后续阶段（需 ADR） |
| 未编号 | RBAC / User / Permission（Enterprise Governance） | 无（仅 project-scoped boundary） | Phase 7 |
