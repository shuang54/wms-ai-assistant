# Phase 5 P-8 — Agent Read Model & Timeline Decision

> 状态：**IMPLEMENTED**（只读观察面；Append-only 决策记录 —— 不覆盖历史决策）
> 前置：P-2 / P-3 Tool Idempotency & Replay Decision · P-4 Tool Result Store ·
> P-5 Agent State & Resume Boundary Decision · P-6 Resume/Replay · P-7 HITL &
> Cooperative Cancellation Decision。

---

## 1. Problem

Phase 5 已经让 Agent Runtime 产生**完整**的持久化事实：

```text
AgentRun · AgentStep · AgentDecision · AgentToolResult · AgentCheckpoint · AgentApproval
```

但它们此前**没有统一的安全读取面**：

* 每个调用方都要自己 import Repository / 拼装 SQL ⇒ 隔离与安全无处强制；
* 原始行含**不可外泄**字段（`claim_token` · tool arguments · Decision content ·
  Tool Result payload · prompt/context/SQL/凭据），直接暴露即事故；
* 没有"一次 Run 当前发生了什么"的**统一视图**，也没有**有序** Timeline；
* 若为阅读而新建存储（event store / timeline 表），会引入第二个 source of truth。

P-8 只解决"**已经存在的状态如何被安全读取**"。

---

## 2. 核心决策

### 2.1 Command / Query 分离（P-8 的首要原则）

```text
Command（既有内部 Application Service；P-8 **不**改动）
    execute · resume · resolve_agent_approval · cancel_agent_run

Query（P-8 新增）
    GET /api/agent/runs/{run_id}            → AgentRunView
    GET /api/agent/runs/{run_id}/timeline   → AgentTimelineEvent[]
```

Read API **不**：调用 LLM / 执行 Tool / Resume / Approve / Cancel / 写任何表 /
修改 Conversation / Evidence。HTTP 层**不**接触 DB（不 import Engine / Session /
Repository）。

### 2.2 Timeline 是 Read Model，不是事件系统

* **不**新增 `agent_timeline_event` 表 / **不**引入 event sourcing；
* 事件只表达"当前持久化数据能够**证明**的事实"；
* 数据不足 ⇒ **不返回该事件**（例如 `STEP_STARTED`：`agent_step` 只持久化写入
  时刻与 `elapsed_ms`，没有可证明的开始时刻 ⇒ 不伪造）；
* `sequence` 是**投影下标**（排序后 0..n-1），不是持久化 event id。

### 2.3 视图 = 既有行的**显式逐字段**安全投影

| 视图 | 来源 | 关键排除 |
| --- | --- | --- |
| `AgentRunSummary` | `agent_run` | 无（Run 本身即安全字段集） |
| `AgentCheckpointView` | `agent_checkpoint` | **`claim_token`** |
| `AgentApprovalView` | `agent_approval` | tool arguments / prompt / context |
| `AgentDecisionView` | `agent_decision` | **arguments / content**（Recovery 数据） |
| `AgentToolResultView` | `agent_tool_result` | **`result_payload` / `result_ref`** |
| `AgentStepView` | `agent_step` | arguments / output |
| `AgentTimelineEvent` | 以上组合 | 任何正文（payload / prompt / SQL / 凭据） |

**状态语义（§八）**：`status` **只**来自 `agent_run`（无终态记录 ⇒ `null`）；
`checkpoint.next_action` **绝不**冒充 Run status —— 它只在 `checkpoint` 视图里，
语义是"从哪恢复"。

### 2.4 数据访问层：只加**只读**方法

| Repository | 新增（P-8） | 说明 |
| --- | --- | --- |
| `AgentRunRepository` | `get(run_id)` | 之前**没有**任何读方法 |
| `AgentStepRepository` | `list_for_run` + `AgentStepRead` | 含 `created_at`（写入行不含） |
| `AgentDecisionRepository` | `list_for_run` | 复用 `AgentDecisionRecord` |
| `AgentToolResultRepository` | `list_for_run` + `AgentToolResultRead` | **不含** payload / ref |
| `AgentApprovalRepository` | `list_for_run` + `AgentApprovalRead` | 无 arguments |
| `AgentCheckpointRepository` | （复用 `get`） | 视图侧丢弃 `claim_token` |

全部显式 `ORDER BY`（不依赖 DB 自然顺序）；**无** create/update/delete 进入读链路。

### 2.5 性能

一次 Read Model 组合 = **每源恰好一次**查询（最多 6 次）+ 内存 merge：
**无 N+1**（不会 100 steps → 100 次查询）。跨 project 探测更少：先读 Run +
Tool Result（归属判定），命中失败即 404，其余 4 源零查询。

### 2.6 Timeline 排序（deterministic）

```text
sort key = (occurred_at, event_rank, step_index, event_type)
event_rank: RUN_STARTED(0) < DECISION_RECORDED(1) < APPROVAL_*(2)
            < TOOL_EXECUTED(3) < STEP_COMPLETED(4) < RUN_TERMINAL(9)
```

* 全序、可复现；与输入顺序 / `dict` / `set` / DB 自然顺序无关；
* **跨钟域不比较、不修正**：`agent_run.started_at/finished_at` 是**应用时钟**，
  其余表的 `created_at` 是 **DB 时钟（写入时刻）** ⇒ 终态事件**不保证**排在
  最后；Timeline 不做"终态置尾"之类的重排（与既有 Assistant Timeline 的
  "跨钟域不可安全比较"原则一致）。

### 2.7 分页

```text
limit  默认 50；有效 1..100；>100 / <=0 ⇒ 400（**server-side cap**）
offset 默认 0；>= 0；<0 ⇒ 400
```

分页是**切片**（`events[offset : offset+limit]`）⇒ `sequence` 不重新编号，
多页可无损拼接（测试覆盖）。

### 2.8 项目隔离（唯一授权语义）

* 服务层签名 = `get_run(run_id, project_id)`（**不**提供"只有 run_id"的入口）；
* 归属只能由**带 project 的持久化行**证明：`agent_run.project_id`，
  否则 `agent_tool_result.project_id`（必须**恰好**等于请求 project）；
* 无法证明 / 不匹配 ⇒ `AgentRunNotFoundError`（**固定文案**，不区分
  "不存在"与"跨 project"）⇒ HTTP **404**（不泄露存在性、防枚举）；
* API 层不猜 project：省略 `project_id` ⇒ 项目默认
  `settings.project.project_id`（与既有 Chat API 默认语义一致）。

### 2.9 错误契约（沿用既有 Trace / Timeline API 风格）

```text
400  run_id / project_id / limit / offset 非法（服务层校验）
404  Run 不存在 **或** 不属于当前 project（**同码**）
422  路径参数长度越界（FastAPI Path 校验）
502  持久化读边界不可用（DATABASE_URL 未配置 / 查询失败）
500  其它未预期错误（不暴露 traceback / SQL / 凭据 / 内部路径）
```

---

## 3. 安全边界（硬约束）

禁入 Read Model（含 Timeline / 所有视图 / 所有错误响应）：

```text
claim_token · tool arguments · Decision content · Tool Result payload/ref
prompt · system prompt · messages · conversation context · RAG chunks
SQL · 数据库 URL · API key · Authorization · password
LLM raw response · exception body · stack trace · headers
```

实现手段：DTO 层**没有**这些字段（最小权限：读 DTO 从不读取 payload）+
API 层**显式逐字段映射** + `response_model` 作为字段过滤边界（内部 DTO 未来
新增字段不会自动泄漏）。

---

## 4. 明确不做（P-8 边界）

```text
Streaming / WebSocket / SSE · Agent UI · Background Agent
Workflow Runtime · Multi-Agent · LangGraph · MCP · A2A · Memory
event store / event sourcing / agent_timeline 表
Artifact / Result Detail（完整 Tool Result 查看）
cursor pagination · 缓存层 · RBAC / JWT / OAuth（Enterprise Governance 属 Phase 7）
Read API → 修改状态 / Resume / Approve / Cancel
```

---

## 5. 已知限制（如实记录）

1. **暂停中且无 Tool Result 的 Run 不可读**：`agent_decision` /
   `agent_checkpoint` / `agent_approval` 都**没有** project 列，因此 HITL 刚暂停
   （尚未执行过任何 Tool）时无法证明归属 ⇒ 404（fail-closed，不泄露存在性）。
   归属来源（Run 行或 Tool Result 行）落盘后立即可读。**修复需要未来阶段为
   控制状态补 project 归属来源（OQ）。**
2. Timeline `sequence` 不是持久化 id；分页跨请求只保证"同一时刻数据不变"的顺序
   （无快照隔离）。
3. `STEP_STARTED` / 任何 LLM 请求级事件当前**不可证明** ⇒ 不产生。
4. 仅 project-scoped query boundary：本阶段**没有**用户/角色级授权。

---

## 6. 迁移路径

```text
P-0 … P-7（已完成）
P-8 Read Model + Read API        ← 本决策（IMPLEMENTED）
P-9+（候选，需另行决策）
    Agent UI / Timeline UI        ← 通过本 Read API，不新建读路径
    Streaming（SSE / WS）         ← 只读推送；不改变 Read Model 语义
    Artifact / Result Detail      ← 完整 Tool Result 的独立能力（含授权/脱敏）
    Control-state project 归属    ← 解决限制 1（schema 决策 + 迁移）
    Enterprise Governance（RBAC） ← Phase 7
```
