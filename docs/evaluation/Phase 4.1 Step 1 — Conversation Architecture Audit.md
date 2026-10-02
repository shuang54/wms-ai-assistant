# Phase 4.1 Step 1 — Conversation Architecture Audit

> 状态：**Audit Only / Design Only**（不实现 Conversation）
> 范围：Conversation / Chat 会话层的架构审计与最小 Contract 设计
> 产物：本文件 + `tests/test_conversation_architecture_contract.py`（纯离线）
> 纪律：**DB = unchanged · API = unchanged · Orchestrator = unchanged ·
> RAG = unchanged · Tool = unchanged · LLM = unchanged**
> 本 Step 运行指标：DB writes = 0 · DB reads = 0 · network = 0 · LLM = 0
>
> 前置：Phase 3.12 已完成 AI Core（RAG / Tool / Text-to-SQL / LLM Usage /
> Tool Execution / RAG Execution / Assistant Outcome / Assistant Trace /
> Assistant Timeline / CI Governance）。
>
> 目标问题：
>
> > 当前 AI Core 如何从「一次独立请求」演进为「同一个会话中的多轮请求」，
> > 而不破坏现有 request_id / assistant_request_id / Trace / Timeline /
> > Outcome 体系？

---

## 0. Executive Summary

结论先行（全部以真实代码为证，证据见 §1 / §16）：

1. 当前系统是**无状态单轮**：`POST /api/ai/chat` → `AIOrchestratorService.execute()`
   → 三条能力路径（RAG / Tool / Text-to-SQL）之一 → Outcome → 返回。
2. 每次请求由 Orchestrator 生成**唯一**关联 ID（`new_request_id()` = uuid4），
   它就是 Assistant Request ID / Assistant Trace ID；
   API 层不生成第二个 ID，DB 侧以 `assistant_request_id` 或同义列 `request_id` 落库。
3. **不存在** `session_id` / `conversation_id` / message 表 / 历史存储 /
   用户身份；`conversation` 一词在 backend 只出现在注释与文档中（明确"不新增"）。
4. Conversation 层必须是**纯新增的容器层**：`Conversation` → `Assistant Request × N`，
   且 `conversation_id ≠ assistant_request_id`（两个独立语义，不得混用）。
5. Trace / Timeline / Outcome 的查询键**保持 assistant_request_id 不变**；
   未来 `conversation_id → assistant_request_id 列表 → 分别查询` 是只读派生，
   不新增 conversation trace / conversation timeline / conversation outcome。
6. 本 Step **零生产代码变更**：只新增本审计文档与离线 contract 测试。

---

## 1. Current State

### 1.1 当前真实请求链（代码为证）

```text
HTTP Client
    ↓
POST /api/ai/chat                        （backend/app/api/orchestrator_chat.py:628）
    ↓  仅校验 + 异常映射 + DTO 转换（API 层不生成 ID）
AIOrchestratorService.execute(question)  （backend/app/services/ai_orchestrator_service.py:553）
    ↓  request_id = new_request_id()     （:574，唯一生成点）
    ↓  with assistant_trace_scope(request_id)（:581，contextvar 隐式传播）
AIRouterService.route()                  （Router 决策；三选一）
    ├── RAG      → RagService.answer()            （:734）
    ├── TOOL     → ToolArgumentExtractor → ToolExecutionService → ToolRegistry（:794）
    └── T2SQL    → SchemaExplorer → TextToSQLService → SQLExecutor（:902）
    ↓
AssistantOutcome（判定点 = Orchestrator；:645-693）
    ↓  metadata["request_id"] = request_id（三条成功路径 + refusal 均透出）
ChatResponse（route / content / data / metadata）→ JSON
```

关键入口事实：

| 事实 | 证据 |
| --- | --- |
| 应用路由注册（prefix `/api`） | `backend/app/main.py:43-45` |
| 端点定义 | `backend/app/api/orchestrator_chat.py:628-641`（`@router.post("/ai/chat")`） |
| 请求体 = `question` + 可选 `project_id` | `backend/app/api/orchestrator_chat.py:156-184` |
| 响应体 = `route` / `content` / `data` / `metadata` | `backend/app/api/orchestrator_chat.py:219-249` |
| 单次 execute 生成一个 ID | `backend/app/services/ai_orchestrator_service.py:574` |
| ID 生成实现（uuid4） | `backend/app/services/tool_execution_context.py:56-71` |
| ID 隐式传播（contextvar） | `backend/app/services/assistant_trace.py:57-59 / 80-107 / 110-116` |
| LLM Usage 关联 | `backend/app/services/llm_usage_persistence_service.py:124-137` |

> 任务提示中的 `backend/app/api/ai.py` 在本仓库**不存在**；实际端点在
> `backend/app/api/orchestrator_chat.py`（如实记录，见 §16.2）。

### 1.2 审计问题 1-9 的逐条回答（以真实代码为准）

**1. `request_id` 从哪里产生？**

`AIOrchestratorService.execute()` 内 `request_id = new_request_id()`
（`ai_orchestrator_service.py:574`）；`new_request_id()` 返回 `str(uuid.uuid4())`
（`tool_execution_context.py:56-71`）。它不落库、不进 LLM messages、不进 Tool arguments。
（另一条旧链路 `POST /api/chat/with-tools` 的 `ToolChatService.chat()` 也在
`tool_chat_service.py:414` 调用 `new_request_id()`，但那是**另一条链路**的本地
关联 ID，不进入 Assistant Trace scope，见第 3 条说明。）

**2. `assistant_request_id` 从哪里产生？**

它不是一个"新的 ID"：**同一个值**经由 `assistant_trace_scope(request_id)` 传播，
由 LLM Usage 持久化边界读取 `current_assistant_request_id()`
（`llm_usage_persistence_service.py:128`）后写入 `ai_ops.llm_usage_record.assistant_request_id`。
全链路只有 Orchestrator 一个生成点（`assistant_trace.py:35-36` 明确"本模块不生成 ID"）。

**3. 两者是否相同？**

在 `/api/ai/chat` 链路上：**同一个值**（同一 ID 的不同命名位置）。
但审计必须区分两个**不同维度**的列名（`llm_usage_record.py:164-171` 注释为证）：

```text
llm_usage_record.request_id             = Provider / LLM 侧请求 ID（chatcmpl-…；可为 NULL）
llm_usage_record.assistant_request_id   = Assistant Trace ID（= Orchestrator 的 request_id）
tool_execution_record.request_id        = Assistant Trace ID（同名同义，与 metadata.request_id 一致）
rag_execution_record.request_id         = Assistant Trace ID（同名同义）
assistant_outcome_record.assistant_request_id = Assistant Trace ID（UNIQUE；first-write-wins）
```

**4. 谁负责生成？**

唯一生成点：`AIOrchestratorService.execute()` 调用 `new_request_id()`
（`ai_orchestrator_service.py:574`；`tool_execution_context.py:56`）。
API 层、Router、RAG、Tool、Text-to-SQL、LLM Client、Trace API 全都不生成该 ID。

**5. 是否已经存在 request context？**

存在**最小**形态，且只有两个载体：

* 隐式：`contextvars.ContextVar("assistant_request_id")`
  （`assistant_trace.py:57-59`）——只承载一个不可变字符串，
  并发隔离（per-task），`asyncio.to_thread` 线程边界可用；
* 显式：`ToolExecutionContext`（frozen，4 字段白名单：
  `request_id / round / project_id / tool_call_id`，`tool_execution_context.py:74-138`）
  ——仅用于 Tool 执行层观测，**不进入** LLM messages / Tool arguments / API contract。

没有 question / messages / user / history 等其它上下文字段。

**6. 是否存在 session/conversation ID？**

**不存在。** 全仓库搜索（backend）：

* `conversation` 仅出现 3 处：`db/models/__init__.py:11-13`（"暂未实现（按 Phase 计划）：
  业务表（conversation / user / role 等）"）、`api/assistant_trace.py:32-33`（"不新增：
  … Conversation / Memory / …"）、`services/llm_usage_query_service.py:514`
  （"**不**自动查 Tool / RAG / Conversation"）；
* 没有任何 `session_id` / `conversation_id` / message 表 / 历史存储；
* 需求与架构文档中的 Conversation 属**未来规划**：
  `docs/requirements.md:642`（FR-011 Conversation Context）、
  `docs/architecture.md` §20 Conversation Architecture / §21 Context Management。

**7. 当前 Trace 使用哪个 ID？**

`GET /api/observability/assistant-trace/{assistant_request_id}`
（`api/assistant_trace.py:266-293`）。查询键是 `assistant_request_id`，
分别命中 llm_usage_record（`assistant_request_id`）、tool_execution_record /
rag_execution_record（同义列 `request_id`）、assistant_outcome_record
（`assistant_request_id`）。

**8. 当前 Timeline 使用哪个 ID？**

`GET /api/observability/assistant-timeline/{assistant_request_id}`
（`api/assistant_timeline.py:120-123`）。同上，单一请求 ID。

**9. Outcome 使用哪个 ID？**

`assistant_request_id`。写侧：`AIOrchestratorService._record_assistant_outcome()`
（`ai_orchestrator_service.py:671-693`）；表：`ai_ops.assistant_outcome_record`
（`db/models/assistant_outcome_record.py`，`assistant_request_id` **UNIQUE**，
first-write-wins；无 error_class / 无 content）。

### 1.3 现状 ID 全景（一张图）

```text
一次 /api/ai/chat
    │
    │  OrchestratorService.execute() 生成（唯一生成点）
    ▼
request_id（uuid4）──────────────────────────────┐
    │                                             │
    │ assistant_trace_scope（contextvar）          │ metadata["request_id"]
    ▼                                             ▼
llm_usage_record.assistant_request_id        ChatResponse.metadata（成功响应）
tool_execution_record.request_id             └→ 作为 {assistant_request_id}
rag_execution_record.request_id                 输入 Trace / Timeline API
assistant_outcome_record.assistant_request_id
```

---

## 2. Proposed State

```text
Conversation（多轮容器；未来新增，本步不实现）
    │
    ├── Assistant Request 1（现有语义，完全不变）
    │      ├── LLM Usage        → llm_usage_record.assistant_request_id
    │      ├── RAG Execution    → rag_execution_record.request_id
    │      ├── Tool Execution   → tool_execution_record.request_id
    │      └── Outcome          → assistant_outcome_record.assistant_request_id
    │
    ├── Assistant Request 2（同上）
    │
    └── Assistant Request 3
```

未来模块边界（设计；本步不实现）：

```text
Chat API（现有 POST /api/ai/chat 不变；未来 Conversation API 另行新增）
    ↓
Conversation Service（会话容器 + History 读写 + Context 组装）
    ↓
AIOrchestratorService（现有；只接收 question，未来只多接收"已组装好的上下文"）
    ↓
RAG / Tool / Text-to-SQL / LLM（全部不变）
```

硬约束：

* Conversation 层是 AI Core 的**上游容器**，不是 AI Core 的一部分；
* `AIOrchestratorService` **不得**反向查询 Conversation DB / History
  （禁止 `Chat API → AIOrchestrator → Conversation DB` 反向依赖）；
* 现有 `/api/ai/chat` 请求/响应契约完全不变（本步与 Step 2 都不改它）。

---

## 3. ID Boundary

核心原则（测试锁定）：

```text
conversation_id ≠ assistant_request_id
```

| ID | 语义 | 生命周期 | 生成方 | 现状 |
| --- | --- | --- | --- | --- |
| `conversation_id` | 多轮会话容器 | 跨多次请求（长） | 未来（推荐服务端；见 §3.2） | **不存在** |
| `assistant_request_id` | 单次 Assistant 请求 | 单次请求（短） | `new_request_id()`（现状唯一生成点） | 已实现 |
| `llm_usage_record.request_id` | Provider 请求 ID | 单次 LLM 调用 | LLM Provider | 已实现（不同维度） |
| 观测表主键 `id` | 行身份 | 行 | 数据库 | 已实现 |

规则：

1. 不得用一个 ID 同时承担两个语义（禁止把 `conversation_id` 直接当
   `assistant_request_id`，反之亦然）；
2. 不得从 `conversation_id` 派生出 `assistant_request_id`
   （`assistant_request_id` 必须继续由 Orchestrator 按"一次请求一个"生成）；
3. 不得把 `assistant_request_id` 当 `message_id` 使用（见 §12）；
4. Trace / Timeline / Outcome **继续只依赖** `assistant_request_id`。

### 3.2 Conversation ID 来源：方案比较与推荐

| 维度 | A 客户端生成 | B 纯服务端生成 | C 服务端签发 + 客户端携带 |
| --- | --- | --- | --- |
| 安全性 | 弱（可伪造 / 可枚举 / 可碰撞） | 强 | 强（服务端可校验归属 / 防伪造） |
| 可控性 | 弱 | 强 | 强（服务端仍是唯一签发者） |
| 幂等性 | 客户端可控但不可信 | 重试会新建会话（弱） | 客户端携带 → 重试命中同一会话（强） |
| API 简洁性 | 中（客户端须先造 ID） | 简单（POST 创建） | 中（首次不带，后续带） |
| 未来持久化 | 需校验/去重 | 简单 | 简单 + 可追溯来源 |
| 多端使用 | 各端自造，易分裂 | 每次创建新会话，跨端同步弱 | 强（同一 conversation_id 跨端续话） |

**推荐方案：C —— 服务端首次创建签发，后续客户端携带。**

* 首次：`POST /api/conversations` → 服务端生成并返回 `conversation_id`；
* 后续：在 Conversation API 的 URL / body 中携带 `conversation_id`（服务端校验存在性）；
* 客户端**不得**自行伪造 ID；服务端不能信任"客户端首次提供的 ID"，
  只接受**本服务端签发过**的 ID；
* 该方案与现有先例一致：`assistant_request_id` 就是服务端签发、客户端只读携带
  （`ChatResponse.metadata.request_id → GET /assistant-trace/{id}`）。

本步只记录结论，**不实现**任何签发 / 校验逻辑。

---

## 4. Project Boundary

现状（真实代码）：

* `ChatRequest.project_id` 是**每请求可选参数**（`orchestrator_chat.py:169-176`）；
* 携带时经 `_build_orchestrator_for_project_id()`（`orchestrator_chat.py:483-524`）
  → `ProjectRegistry` 解析（未注册 → 404）；省略时用默认项目
  （配置 `PROJECT_ID`，默认 `vietnam-wms`，`projects/context.py:35-54`）；
* `ProjectContext`（frozen DTO，`projects/models.py:61-102`）是**每次请求**在
  Text-to-SQL 路径解析的（`ai_orchestrator_service.py:926-933`）；
* capability（Phase 3.8.2）、knowledge scope（Phase 3.8.4）、Tool 授权
  （`ToolExecutionContext.project_id`）都按**请求**绑定项目。

设计结论：

```text
Conversation
    ↓ 创建时绑定（不可变）
project_id
    ↓ 后续每个 Request 校验一致
AIOrchestratorService（per-request，现状不变）
```

* **Conversation 必须绑定 project_id**：建议在创建时确定并持久化
  （future：会话记录字段之一）；
* **同一个 Conversation 不允许切换 project**：后续请求携带的 project_id
  若与 conversation.project_id 不一致 → 未来应拒绝（4xx，具体错误码在 Step 2 定义）；
* 理由：历史上下文（RAG 引用、Tool 结果、SQL 结果）与 capability /
  knowledge scope / 数据源都是 project 相关的；跨项目复用会话会导致
  越权查询或语义错误（例如 A 项目的库存语义解释 B 项目的数据）；
* 本步**不修改** ProjectContext / ProjectRegistry / `/api/ai/chat` 的
  project_id 语义（现状 = 请求级；Conversation 级绑定属未来）。

---

## 5. History Boundary

概念（本步只定义，不实现）：

```text
Conversation
    ↓
Messages / Turns
    ↓
role（user / assistant）
content
created_at
assistant_request_id（assistant turn 的关联键；user turn 可为空/未来 client_message_id）
```

### 5.2 存储位置评估

| 方案 | 持久化 | 一致性 | 查询/排序 | 恢复 | 多实例 | 审计 | 隐私 | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A PostgreSQL | 强（事务） | 强 | 强（ORDER BY / 索引 / 分页） | 强（备份体系复用） | 强（共享库） | 强（可审计） | 可控（可脱敏 / 可删除） | **推荐** |
| B Redis | 中（需持久化配置） | 弱 | 中 | 弱（重启风险） | 中（cluster 复杂） | 弱 | 中 | 仅可作缓存，不作事实来源 |
| C 第三方 Conversation API | 外部 | 外部 | 外部 | 外部 | 外部 | 外部 | **风险高** | 不采用（违反私有化 / 数据边界） |
| D 现有业务数据库 public | 强 | 强 | 强 | 强 | 强 | 强 | 中 | **禁止**（污染业务 schema；与 ai_ops 物理隔离原则冲突） |
| E 其它（文件 / 对象存储） | 中 | 弱 | 弱（随机查询差） | 中 | 中 | 弱 | 中 | 不采用 |

**推荐：A PostgreSQL**（与项目栈一致：PostgreSQL + pgvector 已存在；
未来落点建议为新 schema（如 `conversation`）或 `ai_ops` 内的新增表，
**本步不建表、不迁移**）。

### 5.3 设计要点（未来实现时必须遵守）

1. 追加写（append-only turns），不做就地修改；排序以 `(created_at, id)` 稳定排序；
2. 读取以 `conversation_id` 精确过滤 + 分页（分页契约属未来 Step）；
3. 多实例安全：数据库为唯一事实来源，不依赖进程内缓存做正确性判断；
4. 审计：turn 与 `assistant_request_id` 的关联是关联键，**不复制**观测
   metadata（Observability 与 History 分离，见 §10）；
5. 隐私：历史含用户问题 / 答案正文（业务敏感），需在未来配合权限与脱敏策略；
6. **本步不实现 Repository / ORM / 表 / migration**。

---

## 6. Context Boundary

```text
Conversation History ≠ LLM Context
```

```text
Conversation（100 条历史）
    ↓ 未来 Context Builder（Conversation 层内，本步不实现）
relevant history（截断 / 选择策略）
    ↓
AIOrchestratorService（现有签名不变）
    ↓
LLM / RAG / Tool
```

边界原则：

* Context Builder 属于 **Conversation Layer**（或 AI Core 上游适配层），
  不属于 RAG、不属于 Orchestrator 内部（RAG 的 `ContextBuilder` 只处理
  知识片段，不处理对话历史——两者不得混为一个组件）；
* 未来 Orchestrator 若接收上下文，必须是**已组装好的显式参数**
  （例如 future `context: str | None`，该参数已存在：`execute(question, *, context=None)`），
  不允许 Orchestrator 自己查 Conversation DB；
* 历史不能全量塞进 Prompt（`docs/architecture.md` §21 已规划 Recent + Summary 思路）；
* 本步**不实现** Context Builder / Summary / Memory。

---

## 7. Trace Boundary

```text
Conversation
    ↓
assistant_request_id 列表（未来由 Conversation 记录提供）
    ↓ 逐个调用（N 次只读请求）
GET /api/observability/assistant-trace/{assistant_request_id}（现状不变）
```

* 现有 Trace API **路径 / 参数 / 响应字段全部保持**（`api/assistant_trace.py:266-293`；
  响应字段：assistant_request_id / outcome / llm_usage / tool_executions / rag_executions）；
* 未来**不新增** conversation 级 Trace 端点、不新增 conversation_id 查询参数；
* Conversation → Trace 是**只读派生视图**（可能在未来由上游前端组合），
  不是新的存储 / 不是新的 trace scope；
* 本步**不修改** Trace API。

---

## 8. Timeline Boundary

```text
conversation_id
    ↓
assistant_request_id（逐个）
    ↓
GET /api/observability/assistant-timeline/{assistant_request_id}（现状不变）
```

* **不新增 conversation timeline**；
* 不改变现有 `api/assistant_timeline.py:120-123` 的路径与语义
  （Grouped Timeline 是 request-scoped 投影）；
* 跨请求时间线属未来派生视图（N 个 request timeline 组合），
  不属于本步、也不属于现有 Timeline API 的语义扩展。

---

## 9. Outcome Boundary

现状：

```text
assistant_request_id → AssistantOutcome（SUCCESS / EMPTY / REFUSED / FAILED；1:1，UNIQUE）
```

未来：

```text
conversation_id
    ↓
multiple assistant_request_id
    ↓
multiple outcomes（每请求一行，互不覆盖）
```

* **Conversation Outcome 与 Assistant Request Outcome 是两个概念**：
  不聚合、不覆盖、不新增 conversation-level 终态列；
* Outcome 枚举 / 表 / 写路径 / 读路径在 Step 1 **全部不修改**
  （`dto/assistant_outcome.py`、`db/models/assistant_outcome_record.py`、
  `assistant_outcome_persistence_service.py`、Trace 响应中的 `outcome` 字段）；
* 若未来需要"会话级状态"，必须是**独立派生视图**（例如按 request 列表汇总），
  不得落成第二套终态事实表（本步不做任何决定之外的实现）。

---

## 10. Security

### 10.1 当前授权现状（如实记录，不隐藏）

* 代码中**不存在** `user_id` / `tenant_id` / `organization_id`；
* 不存在 request-level authentication / authorization（`api/assistant_timeline.py:33-38`
  明确声明"没有 request-level authorization"）；
* 因此：**Conversation ownership 当前未定义**；
  本阶段**不**假设存在用户级权限隔离，**不**自行增加 user_id / tenant_id。
* 结论句（供未来 Step 使用）：当前 Conversation 不应在本阶段假设存在用户级权限隔离。

### 10.2 敏感字段禁令（Conversation metadata）

以下字段**禁止**进入 Conversation 的 observability metadata / trace metadata：

```text
api_key · password · database_url · connection_string · authorization · token · secret
raw SQL · embedding vector · internal DB session / connection
LLM prompt / raw provider response（观测层既有禁令继续适用）
```

（本文件 contract 测试用 `api_key` / `password` / `database_url` / `authorization`
四项 + 片段哨兵 `postgresql://` / `sk-` / `Bearer ` 做漂移报警。）

### 10.3 History 与 Observability 分离

* Conversation History（业务数据：用户问题 / 答案 / 未来 Tool 摘要展示）
  与 Observability Metadata（identity / timing / status）必须分离存储与分离合同；
* History 可以包含 content；观测侧继续只保留白名单字段
  （现有 Trace / Timeline DTO 的安全约束不变）；
* Conversation 层不得把 History 正文写入观测表；也不得把观测表内容当作
  History 渲染源（两者各自单一事实来源）。

### 10.4 未来补充（仅记录）

* 未来如果引入认证：conversation 的归属校验应在 Conversation Service 层做，
  不在 Trace / Timeline / Outcome 读边界做（读边界保持 ID 透明）；
* 未来 History 中的用户输入属于不可信数据：Conversation 层不得让历史文本
  绕过 capability / 权限（提示注入属于 Step 2+ 的设计事项，本步不展开实现）。

---

## 11. Concurrency

场景：

```text
同一个 conversation 同时收到 Request A / Request B
```

风险分析（仅分析，不实现）：

| 风险 | 说明 | 影响 | 未来方向（不在本步） |
| --- | --- | --- | --- |
| history ordering | 两个请求的 turn 落库顺序与用户真实顺序可能不一致 | 上下文顺序错乱 | 单会话串行化 / 序列号（conversation-scoped seq） |
| race condition | 两个请求同时读取同一历史快照 | 后发请求看不到先发请求的 turn | 读时快照 + 显式版本 |
| duplicate turn | 重试 / 双端发送导致同一条消息落两次 | 历史重复 | 幂等键（见 §12） |
| last-write-wins | 若未来存在 conversation 级可变字段（如 updated_at / summary） | 更新丢失 | 乐观锁（version 列） |

边界：

* 本步**不实现**锁、不实现队列、不增加数据库字段；
* 现有 per-request 体系天然并发安全（每个请求独立 `assistant_request_id`，
  观测写入各自独立行；`assistant_outcome_record` 的 UNIQUE 保证
  first-write-wins，重复写不覆盖）；
* Phase 3.12 已有并发的只读审计先例
  （`docs/evaluation/phase-3.12-step-70-timeline-concurrency-audit.md`、
  `phase-3.12-step-72-read-during-write-audit.md`）；Conversation 层的写并发
  属未来 Step，本步不重新打开其结论。

---

## 12. Idempotency

三个 ID 的职责必须区分：

| 候选 | 语义 | 现状 | 本步决定 |
| --- | --- | --- | --- |
| `conversation_id` | 会话容器 | 不存在 | 未来由服务端签发（§3.2）；不承担请求幂等 |
| `assistant_request_id` | **服务端**单次请求 ID（uuid4） | 已实现 | **不**当作 message_id；不改其语义 |
| `client_message_id` | 客户端消息幂等键 | 不存在 | **本步不新增**；未来若需要，单独设计（见下） |

分析与结论：

1. 未来客户端可能对 `POST /chat` 类端点重复发送（网络重试 / 双端提交）；
2. 区分"新 turn"与"重复投递"需要**客户端可重放的幂等键**，
   `assistant_request_id` 无法承担该职责（它由服务端生成，
   客户端第一次请求时还不存在，无法在重试时携带）；
3. 因此未来若引入幂等，推荐独立概念 `client_message_id`
   （客户端生成、随重试原样重放），由 Conversation 层做去重；
   **本步不新增该字段、不实现去重**；
4. 现有服务端幂等先例（可复用语义，不复制实现）：
   `llm_usage_record` 的 ON CONFLICT DO NOTHING（`db/llm_usage_repository.py:286-298`）、
   `assistant_outcome_record` 的 first-write-wins UNIQUE；
5. 明令：不得把 `assistant_request_id` 当 `message_id`（本次 contract 测试锁定
   设计 DTO 不含 `message_id` / `client_message_id` / `seq` 字段）。

---

## 13. Proposed APIs

**全部只做设计，不实现、不注册路由。** 现有 `POST /api/ai/chat` 保持完全不变。

### 13.1 `POST /api/conversations`

* 语义：创建会话（服务端签发 `conversation_id`；绑定 `project_id`）。
* 请求（草案）：`{ "project_id": "vietnam-wms" }`（可省略 → 默认项目，与现有语义一致）。
* 响应（草案）：

```json
{
  "conversation_id": "…",
  "project_id": "vietnam-wms",
  "created_at": "…",
  "updated_at": "…"
}
```

### 13.2 `GET /api/conversations/{conversation_id}`

* 语义：读取会话元数据（不含消息正文）。
* 响应：同 13.1 的最小字段；不存在 → 404（未来契约）。

### 13.3 `POST /api/conversations/{conversation_id}/messages`

* 语义：追加一条用户消息，触发一次 Assistant Request。
* 请求（草案）：`{ "content": "…", "client_message_id": "可选（未来幂等，本步不实现）" }`。
* 响应（草案）：`{ "assistant_request_id": "…", "content": "…", "metadata": {…} }`
  —— `assistant_request_id` 仍由 Orchestrator 生成并回显，
  与现有 `/api/ai/chat` 的 `metadata.request_id` 同一语义。
* 硬约束：project_id **不在本端点切换**（继承 conversation.project_id，见 §4）。

### 13.4 `GET /api/conversations/{conversation_id}/messages`

* 语义：读取会话历史（分页契约属未来 Step；本步明确 **Pagination = Deferred**）。
* 响应（草案）：turn 列表（conversation_id / assistant_request_id / role / content / created_at）。

### 13.5 与现有端点的关系

```text
现有（不改）：
    POST /api/ai/chat                          ← 单轮无状态入口，保持完全不变
未来（新增，设计中）：
    POST /api/conversations                    ← 会话容器
    POST /api/conversations/{id}/messages      ← 内部复用 AIOrchestratorService（不复制 AI Core）
```

---

## 14. Deferred Implementation

本 Step 结束后的不变式（测试 + git diff 双重确认）：

```text
DB = unchanged
API = unchanged
Orchestrator = unchanged
RAG = unchanged
Tool = unchanged
LLM = unchanged
```

明确 Deferred（全部未实现，留给后续 Step）：

| 事项 | 状态 |
| --- | --- |
| Conversation 表 / ConversationTurn 表 | Deferred（**本步禁止新增表**） |
| migration | Deferred |
| Conversation ORM / Repository | Deferred |
| Conversation Service / API | Deferred（仅 §13 设计记录） |
| Memory / Context Builder / Summary | Deferred |
| 并发控制（锁 / 序列号 / 队列） | Deferred（§11 仅风险分析） |
| 幂等键 `client_message_id` | Deferred（§12 仅设计结论） |
| 认证 / 授权 / ownership | Deferred（当前未定义，§10；**不**假设用户级隔离） |
| History 分页 / 保留策略 / 脱敏 | Deferred（§5 仅边界） |
| Conversation 级 Trace / Timeline / Outcome 端点 | 明确不做（§7 / §8 / §9） |

未来 Conversation 层实现时必须遵守的边界（供 Step 2+ 使用）：

* Conversation Layer 的访问边界：
    - 不得 import SQLAlchemy（会话持久化只能经由自身 Repository 边界，
      不允许在 Service / API 内直接持有 ORM 能力）；
    - 不得直连 PostgreSQL 业务表（禁止绕过 Service 直查 public schema 业务数据）；
    - 不得直接调用 RAG（RagService）、Tool（ToolRegistry /
      ToolExecutionService）、LLM（LLMClient）——这些能力只能经由
      AIOrchestratorService 触达；
* Conversation Service 与 AIOrchestratorService 之间是**单向调用**：
  Conversation → Orchestrator（组装好的 question / context），
  Orchestrator 不反向依赖 Conversation；
* 不依赖 DeepSeek / 任何真实网络（测试语境）：本步与本文件均 network = 0。

---

## 15. Contract Tests

新增文件：

```text
tests/test_conversation_architecture_contract.py
```

性质：纯离线（DB = 0 · network = 0 · LLM = 0），运行命令：

```powershell
python -m pytest -q tests/test_conversation_architecture_contract.py
```

覆盖矩阵（8 项任务要求 → 测试类）：

| # | 任务要求 | 测试类 |
| --- | --- | --- |
| 1 | Conversation ID 与 Assistant Request ID 是不同概念 | `TestIdBoundary`（含设计 DTO 字段锁定 / contextvar 单 ID / execute 单 ID 生成点） |
| 2 | 现有 Trace API 不被修改 | `TestTraceApiUnchanged`（路由 / 响应字段 / 源码无 conversation_id） |
| 3 | 现有 Timeline API 不被修改 | `TestTimelineApiUnchanged` |
| 4 | 现有 Outcome contract 不被修改 | `TestOutcomeContractUnchanged`（枚举 4 态 / UNIQUE / nullable） |
| 5 | Conversation 不直接访问 SQLAlchemy / PostgreSQL / RAG / Tool / LLM | `TestConversationRuntimeBoundaries`（不存在 conversation 生产模块 + 文档边界声明） |
| 6 | Conversation 不依赖 DeepSeek / network | `TestNoNetworkOrLlmDependency`（自身 AST 自审 + 文档声明） |
| 7 | 未来 API 为设计记录，不注册路由 | `TestProposedApisAreDesignOnly`（api 源码扫描 + `/ai/chat` 不变） |
| 8 | Security fields 不进入设计 metadata | `TestSecurityMetadata`（守卫函数 + 文档声明 + DTO 字段） |

设计 DTO 说明：`_ConversationDesign` / `_ConversationTurnDesign` **只存在于测试文件内**
（`_` 前缀），**没有**创建 production DTO（遵守任务 §十九）。

漂移报警语义：任何"注册 conversation 路由 / 修改现有 API 字段 /
新增 message_id / 把敏感字段写进 metadata"的变化都会使该文件失败。

---

## 16. Audit Evidence

### 16.1 实际阅读文件（本步）

```text
backend/app/main.py
backend/app/api/orchestrator_chat.py        （真实 /api/ai/chat）
backend/app/api/chat.py                     （旧 /api/chat，RAG-only）
backend/app/api/assistant_trace.py
backend/app/api/assistant_timeline.py
backend/app/services/ai_orchestrator_service.py
backend/app/services/ai_router_service.py   （协议 / 类签名）
backend/app/services/assistant_trace.py
backend/app/services/tool_execution_context.py
backend/app/services/llm_usage_persistence_service.py
backend/app/services/tool_chat_service.py   （旧链路 request_id 生成点）
backend/app/services/rag_service.py         （observation 读取 contextvar）
backend/app/projects/models.py
backend/app/projects/context.py
backend/app/dto/assistant_outcome.py
backend/app/db/models/（assistant_outcome_record / llm_usage_record /
                       tool_execution_record / rag_execution_record / knowledge_*）
tests/（test_assistant_trace_timeline_contract.py 等既有 contract 风格、
       conftest.py、pytest.ini）
docs/architecture.md（§20 / §21 规划）
docs/requirements.md（FR-011 / FR-012）
docs/decisions/（检索）
docs/evaluation/（Phase 3.12 系列，共 56 个相关文档）
```

### 16.2 与任务提示的差异（如实记录）

| 任务提示路径 | 实际情况 |
| --- | --- |
| `backend/app/api/ai.py` | **不存在**；实际端点文件 = `backend/app/api/orchestrator_chat.py` |
| `backend/app/services/assistant_trace_read_model.py` | **不存在**；实际读模型 = `backend/app/services/assistant_trace_query_service.py`（`AssistantTraceView`）+ `backend/app/dto/assistant_timeline*.py` |
| `backend/app/dto/` | 仅含 `assistant_outcome.py` / `assistant_timeline.py` / `assistant_timeline_api.py`（无 conversation DTO，符合预期） |

### 16.3 本 Step 变更范围（git diff 证明）

```text
允许：
    tests/test_conversation_architecture_contract.py   （新增）
    docs/evaluation/Phase 4.1 Step 1 — Conversation Architecture Audit.md（新增）

必须不变：
    backend/app/            = unchanged
    backend/app/api/        = unchanged
    backend/app/db/         = unchanged
    .github/                = unchanged
```

### 16.4 运行记录

```text
python -m pytest -q tests/test_conversation_architecture_contract.py
    → 27 passed（纯离线：不驱动 /api/ai/chat、不写任何表、不调用 LLM）

python -m pytest -q tests/test_conversation_architecture_contract.py --noconftest
    → 27 passed（对照运行：证明测试文件自身零 DB / 零网络依赖）

python -m compileall -q backend tests scripts
    → 通过
```

本 Step 产物（测试文件 + 本审计文档）的纯度口径：

```text
DB writes = 0 · DB reads = 0 · network = 0 · LLM = 0
```

环境说明（如实记录，不隐藏）：本仓库 `tests/conftest.py` 含 Phase 3.12 Step 64
引入的**会话级残留守卫**（autouse fixture）；当环境配置了 `DATABASE_URL`
（本机 `.env` → `localhost:5432`，端口监听中）时，运行**任何** pytest 会话
都会在会话开始执行 1 次 `SELECT COALESCE(MAX(id))` 水位查询，并在会话结束
执行 1 次 `DELETE ... WHERE id > 水位`（只清理"本会话新增"的 outcome 测试残留）。
本 Step 的测试不产生任何 outcome 行 → 该 DELETE 影响 0 行。
这属于既有测试基础设施行为（先于本 Step 存在），不由本 Step 产物触发；
如需严格 0 接触，可使用上述 `--noconftest` 对照命令。

未运行：`RUN_DB_TESTS` / 全量 `pytest -q`（本 Step 明确排除）。

---

## 17. 参考资料

* `docs/architecture.md` §20 Conversation Architecture（规划，未实现）
* `docs/architecture.md` §21 Context Management（规划，未实现）
* `docs/requirements.md` FR-011 Conversation Context / FR-012 Logging
* `docs/evaluation/phase-3.12-trace-timeline-contract-baseline.md`（现状契约基线）
* `docs/evaluation/phase-3.12-step-70-timeline-concurrency-audit.md`
* `docs/evaluation/phase-3.12-step-72-read-during-write-audit.md`
* `tests/test_assistant_trace_timeline_contract.py`（既有 contract 测试风格来源）
