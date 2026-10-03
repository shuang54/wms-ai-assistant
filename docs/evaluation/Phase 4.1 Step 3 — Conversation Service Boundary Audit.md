# Phase 4.1 Step 3 — Conversation Service Boundary Audit

> 状态：**Design / Freeze Only**（不创建 Service / Repository / ORM / 表 / API）
> 范围：Conversation 生命周期与 Service Boundary（职责 / 依赖 / 事务 / 错误 / 安全）
> 产物：本文件 + `tests/test_conversation_service_boundary_contract.py`（纯离线）
> 纪律：**DB = unchanged · API = unchanged · Orchestrator = unchanged ·
> RAG = unchanged · Tool = unchanged · LLM = unchanged**
> 本 Step 运行指标：DB writes = 0 · DB reads = 0 · network = 0 · LLM = 0
>
> 前置：
>
> * Phase 4.1 Step 1：`Conversation → Assistant Request × N`；
>   `conversation_id ≠ assistant_request_id`。
> * Phase 4.1 Step 2：`Conversation`（5 字段）/ `ConversationTurn`（6 字段；
>   一条消息一行）；`USER ⇒ assistant_request_id = NULL`、
>   `ASSISTANT ⇒ assistant_request_id 非空`。
>
> 本 Step 只回答：**Conversation Service 应该负责什么，以及 Conversation
> 生命周期如何与现有 AI Core 解耦。** 最终冻结：

```text
API Layer
    ↓
Conversation Service
    ↓
Conversation Repository
    ↓
Conversation / Turn Persistence

Conversation Service
    ↓（仅此一条出边，未来由 Application Layer 使用）
AIOrchestrator（不属于 Conversation Service 的调用）
```

---

## 0. Executive Summary（冻结结论）

| # | 结论 | 章节 |
| --- | --- | --- |
| 1 | Conversation Service 只负责 Conversation 生命周期（5 方法，不多不少） | §1 |
| 2 | 唯一构造依赖 = Repository；不依赖 LLM / RAG / Tool / Router / AIOrchestrator | §1 / §13 |
| 3 | ConversationService 不得依赖 FastAPI（Request / Response / HTTPException / Depends） | §1 |
| 4 | Create：输入只有 project_id；conversation_id 由服务端生成；status = ACTIVE；updated_at = created_at | §2 |
| 5 | Archive：ACTIVE → ARCHIVED；ARCHIVED → ARCHIVED（幂等，第二次归档不报错）；ARCHIVED 禁止追加 Turn | §3 |
| 6 | project_id 创建后不可修改；查询只能按 conversation_id 找 project_id | §4 |
| 7 | Append Turn：USER ⇒ NULL / ASSISTANT ⇒ 非空；ARCHIVED 拒绝 | §5 |
| 8 | Assistant Turn 在 AI Core execution 得到最终业务结果之后创建 | §6 |
| 9 | Turn 保存 content；Outcome 保存 execution outcome；两者不合并、不复制 | §7 |
| 10 | 失败处理：有可展示给用户的最终内容 → 创建 ASSISTANT Turn；纯内部异常 → 不创建 | §8 |
| 11 | Regenerate：不修改原 Assistant Turn；新 request → 新 turn | §9 |
| 12 | Repository 是唯一 SQL/Session 边界；Service 不接触 SQLAlchemy | §10 |
| 13 | 事务两段式：USER Turn 写+commit → AI 执行（无事务）→ ASSISTANT Turn 写+commit | §11 |
| 14 | 不拆分 CQRS：保持单一 ConversationService | §12 |
| 15 | Conversation Service 不查询 Trace / Timeline / Outcome（组合属 Application Layer） | §13 |

---

## 1. Service Responsibility

### 1.1 定位

```text
Conversation Layer
    ├── ConversationService      ← 本 Step 冻结
    ├── ConversationRepository   ← SQL / Session 边界（未来）
    └── Conversation / Turn 表   ← Step 2 已冻结模型（未来）
```

**Conversation Service 只负责 Conversation 生命周期**：
创建 / 读取 / 归档会话、追加 / 列出 Turn。
它既不是 AI 执行器，也不是观测查询器，也不是 HTTP 适配器。

### 1.2 冻结方法集（5 个，不多不少）

| 方法 | 签名（冻结） | 返回 | 失败语义 |
| --- | --- | --- | --- |
| `create_conversation` | `(*, project_id: str)` | `ConversationView` | 输入非法 → `ValueError` |
| `get_conversation` | `(conversation_id: str)` | `ConversationView \| None` | 输入非法 → `ValueError` |
| `archive_conversation` | `(conversation_id: str)` | `ConversationView` | 不存在 → `ConversationNotFoundError` |
| `append_turn` | `(*, conversation_id, role, content, assistant_request_id)` | `ConversationTurnView` | 不存在 → `ConversationNotFoundError`；ARCHIVED → `ConversationArchivedError`；字段规则 → `ValueError` |
| `list_turns` | `(conversation_id: str)` | `tuple[ConversationTurnView, ...]` | 空会话 → 空元组（**不是异常**，与既有 QueryService 风格一致） |

设计风格对齐（真实证据）：

* 构造注入 Repository（`None` → 默认实例；构造期不连接数据库）——
  与 `ToolExecutionPersistenceService` / `AssistantOutcomeQueryService` 一致；
* DB 边界方法为**同步**方法（项目现状：SQLAlchemy 同步 + Repository 同步）；
* 查询语义用 `None` / 空元组（"None 不猜"，`AssistantOutcomeQueryService` 先例）；
* 命令语义用显式异常（项目已有按域划分异常家族先例：`AIOrchestratorError` /
  `*RepositoryError`）；
* 字段规则校验用 `ValueError`（`ChatService` / QueryService 先例）。

### 1.3 冻结异常体系（设计；未来实现）

```text
ConversationServiceError（基类）
    ├── ConversationNotFoundError     命令路径：会话不存在
    └── ConversationArchivedError     ARCHIVED 会话禁止追加 Turn
```

* 查询路径（`get_conversation` / `list_turns`）：无记录 → `None` / 空元组；
* 命令路径（`archive_conversation` / `append_turn`）：需要区分
  "不存在"与"状态冲突"，`None` 无法承载 → 显式异常；
* 下游 `ConversationRepositoryError` **原样上抛**（不包装、不吞）——
  与 `AssistantTraceQueryService` 组合层的错误策略一致。

### 1.4 ConversationService 不负责 AI Execution

```text
ConversationService
        X
        ↓
AIOrchestrator internal execution
```

ConversationService **不负责**：RAG、Tool、Text-to-SQL、LLM、Router、
Planning、Memory。**ConversationService 不调用 AIOrchestrator。**

未来编排（推荐形态；本 Step 不实现）：

```text
Chat / Application Layer
        │
        ├── ConversationService
        │       ↓
        │   history / context
        │
        └── AIOrchestrator
                ↓
             execution
```

### 1.5 ConversationService 不得依赖 FastAPI

明确禁止 Service 层依赖：

```text
Request · Response · HTTPException · Depends · APIRouter
```

* Service 不做参数解析（Pydantic DTO 属 API 层）、不做状态码映射、不做 `Depends` 注入；
* HTTP 适配只存在于 `backend/app/api/`（既有分层现状：所有 FastAPI import
  都位于 api 层；Service 层仅有一个既有文件在测试辅助函数内引用
  `fastapi.testclient`，与本 Contract 无关）；
* 未来 `POST /api/conversations/{conversation_id}/messages` 属于
  **Conversation / Chat Application Layer**，由 API 层组合
  ConversationService 与 AIOrchestrator，而不是 Service 自己调用 HTTP。

### 1.6 唯一依赖

```text
ConversationService
    └── ConversationRepository（唯一构造依赖）
```

不得在构造函数中注入：`AIOrchestratorService`、`AIRouterService`、
`RagService`、`ToolExecutionService`、`LLMClient` 或任何观测查询服务（§13）。

---

## 2. Create Conversation

### 2.1 最小输入 / 输出（冻结）

```text
输入（客户端唯一可提供）：
    project_id

输出：
    conversation_id
    project_id
    created_at
    updated_at
    status
```

### 2.2 创建规则（冻结）

```text
project_id 必须有效（非空 str；长度 ≤ 128）
conversation_id 由服务端生成（uuid4 风格；与 new_request_id() 一致）
status = ACTIVE
created_at = now
updated_at = created_at
```

* 客户端**不得**决定：`conversation_id` / `status` / `created_at` / `updated_at`；
* 生成算法属未来实现细节，本步只冻结"服务端生成 + 不可由客户端提供"；
* `project_id` 的**注册存在性**（ProjectRegistry）校验属 Application Layer
  （与现有 `/api/ai/chat` 的 404 语义一致）；ConversationService 只做
  格式校验（非空 / 长度），**不访问 ProjectRegistry**（保持 Conversation Layer
  不依赖 Project 解析基础设施）；
* 只设计，不实现（本 Step 禁止创建 Service）。

---

## 3. Archive Conversation

### 3.1 状态迁移（冻结）

```text
ACTIVE
   ↓
ARCHIVED
```

| 调用 | 结果 |
| --- | --- |
| `archive(ACTIVE)` | → `ARCHIVED`（更新 `updated_at`） |
| `archive(ARCHIVED)` | → `ARCHIVED`；**第二次归档不报错**（幂等，无副作用） |
| `archive(不存在)` | → `ConversationNotFoundError` |

* 不增加 `ARCHIVE_FAILED` 或任何第三态（Step 2 冻结：status 仅两态）；
* 归档是有效变更 → 更新 `updated_at`（Step 2 §1.3）。

### 3.2 归档后语义（冻结）

ARCHIVED 会话**允许**：

```text
读取（get_conversation）
查看历史（list_turns）
查看 Trace（未来由 Application Layer 组合，键 = assistant_request_id）
查看 Timeline（同上）
```

ARCHIVED 会话**禁止**：

```text
追加 Turn（append_turn → ConversationArchivedError）
```

本 Step **不实现**数据库状态修改，只冻结 Contract。

---

## 4. Project Binding

```text
Conversation.project_id：创建时绑定，创建后不可修改
```

* 未来 `GET /api/conversations/{conversation_id}` 必须根据 conversation_id 找到 project_id，
  不能通过客户端传入另一个 project_id 覆盖；
* 所有按 conversation_id 的操作（get / archive / append / list）都只接受
  conversation_id 一个定位参数 —— 冻结签名中**不存在** project_id 参数；
* 一致性校验（请求 project_id == conversation.project_id）属未来
  Application Layer / Service 实现细节，本步只写 Contract；
* **不实现**任何授权系统（认证 / ownership 仍属 Deferred）。

---

## 5. Append Turn

### 5.1 方法（冻结）

```text
append_turn(
    *,
    conversation_id,
    role,
    content,
    assistant_request_id,
) → ConversationTurnView
```

### 5.2 规则（延续 Step 2 Contract）

```text
USER → assistant_request_id = None
ASSISTANT → assistant_request_id 必须非空
```

| 场景 | 结果 |
| --- | --- |
| 合法 USER（request = None） | 写入 Turn |
| 合法 ASSISTANT（request 非空） | 写入 Turn |
| USER 携带 request / ASSISTANT 无 request | `ValueError`（字段规则） |
| content 空 / 纯空白 | `ValueError` |
| conversation 不存在 | `ConversationNotFoundError` |
| conversation 已 ARCHIVED | `ConversationArchivedError` |

### 5.3 content 边界（延续 Step 2 §13）

* 只保存：用户输入 / Assistant 最终回答；
* 不得保存：完整 LLM prompt、RAG chunks、Tool arguments / results、SQL、
  system prompt（Security Contract 见 §14）。

### 5.4 时序约束（与 §6 配合）

追加 ASSISTANT Turn 的前提是 **AI Core execution 已得到最终业务结果**
（先写 ASSISTANT Turn 再调用 AI 是禁止设计，见 §6.2）。

---

## 6. Assistant Request Correlation

### 6.1 一次正常 Chat 的时序（冻结）

```text
USER Turn
    conversation_id = C
    assistant_request_id = NULL

        ↓
（Application Layer 调用 AIOrchestrator）

AIOrchestrator
    assistant_request_id = A

        ↓
（Application Layer 追加结果）

ASSISTANT Turn
    conversation_id = C
    assistant_request_id = A
```

### 6.2 Assistant Turn 产生时机（本 Step 关键冻结）

**Assistant Turn 在 AI Core execution 得到最终业务结果之后创建。**

不采用"先创建 ASSISTANT Turn，再调用 AIOrchestrator"——
否则 AI 失败时会留下空 assistant message（无内容的 Turn 行）。

### 6.3 关联方向（单向）

```text
Conversation
    ↓
Turn（assistant turn）
    ↓ assistant_request_id
Assistant Request
    ↓
Trace / Timeline / Outcome
```

* Turn → Request 是**只读关联**（存一个 ID，不复制任何观测数据）；
* Request 侧（Orchestrator / Trace / Timeline / Outcome）**不知道也不依赖**
  Conversation 的存在（AI Core 零改动，现状保持）。

---

## 7. Outcome Boundary

```text
ConversationTurn 保存：assistant content（用户可读文本）
AssistantOutcome  保存：execution outcome（SUCCESS / EMPTY / REFUSED / FAILED）
```

* 两者**不合并、不复制**：Turn 无 `outcome` / `status` / `success` / `error_code` 字段；
* 合法状态示例：

```text
Assistant Turn
    content = "抱歉，我无法执行这个请求。"
Outcome
    REFUSED
```

（Turn 有 content；Outcome 为 REFUSED；这是合法组合，不是矛盾。）

* Outcome 枚举 4 态 / 表 / 写路径 / 读路径全部不变（本 Step 不修改 Outcome）。

---

## 8. Failure Handling

### 8.1 判定标准（冻结）

是否创建 ASSISTANT Turn 的唯一判定标准 = **是否有可展示给用户的最终内容**
（不按 Outcome 类型"猜"）：

```text
有可展示给用户的最终内容
        ↓
创建 ASSISTANT Turn

只有内部异常、无用户可见内容
        ↓
不创建 ASSISTANT Turn
```

### 8.2 典型情况（设计，不实现）

| Outcome | 典型 content | 是否创建 ASSISTANT Turn |
| --- | --- | --- |
| SUCCESS | 业务回答 | 是 |
| REFUSED | 首类拒绝消息（有 content） | 是 |
| EMPTY | 固定提示语（RAG 空检索） | 是 |
| FAILED（Tool 业务失败等） | 有用户可见的失败说明 | 是 |
| FAILED（内部异常 / HTTP 5xx） | 无 content | **不创建** |

* USER Turn 在 AI 执行之前已持久化（这是正确语义：用户消息确实存在）；
* AI 失败时 USER Turn 保留、ASSISTANT Turn 按上表决定；
* **不修改 AIOrchestrator** —— 本表定义的是未来 Application Layer 的行为；
* 不新增 `FAILED` 状态到 Turn（Outcome 已承载 execution outcome）。

---

## 9. Regenerate

Step 2 已冻结：

```text
User Turn
    ├── Assistant Request A → FAILED
    └── Assistant Request B → SUCCESS
```

本 Step 补充（冻结）：

* Regenerate **不修改原 Assistant Turn**；
* 设计为：

```text
same user turn
    ↓
new assistant request
    ↓
new assistant turn
```

* 同一 User Turn 可以展开多个 Assistant Turn（Step 2 §10）；
  顺序按 `(created_at, turn_id)`；
* 本 Step **不实现** regenerate API（Application Layer 未来设计）。

---

## 10. Repository Boundary

### 10.1 职责

```text
ConversationService
        ↓
ConversationRepository
```

**Repository 负责**：Conversation / Turn 的 CRUD、query、
transaction boundary（一方法一事务）。

Repository **不负责**：LLM、RAG、Tool、Router、AIOrchestrator。

### 10.2 冻结方法集（5 个）

| 方法 | 签名（冻结） | 说明 |
| --- | --- | --- |
| `create` | `(*, conversation_id, project_id, status)` | 插入会话行（`created_at` / `updated_at` 由 DB now()） |
| `get_by_conversation_id` | `(conversation_id)` | 精确读取；无记录 → `None` |
| `update_status` | `(conversation_id, status)` | 原子 UPDATE；返回更新后 Row；不存在 → `None` |
| `append_turn` | `(*, conversation_id, role, content, assistant_request_id)` | **原子**：插入 Turn + 更新 `conversation.updated_at`（同一事务） |
| `list_turns_by_conversation_id` | `(conversation_id)` | `ORDER BY created_at ASC, turn_id ASC` |

### 10.3 Service 禁止直接访问数据库（冻结）

ConversationService 不允许直接使用：

```text
SQLAlchemy Session
select() / insert() / update()
Engine / Connection
```

* 与既有分层一致：`AssistantTraceQueryService` 明确
  "✗ SQLAlchemy Session / Engine / select；✗ ORM Model"；
* Repository 返回 **frozen Row（不是 ORM 对象、不是 Session）**；
  Service 把 Row 转为 View（与 `LLMUsageQueryService → View` 的先例一致）；
* Repository 命名 / 风格复用现有实践：`backend/app/db/<entity>_repository.py`、
  `XxxRepository` + `XxxRepositoryError` + `XxxRow` + `validate_xxx()`
  （真实证据：`assistant_outcome_repository.py` 等 4 个既有仓储）。

---

## 11. Transaction Boundary

### 11.1 两段式（冻结）

```text
DB transaction #1
    写 USER Turn
    commit

AI execution
    network / AI（**无数据库事务**）

DB transaction #2
    写 ASSISTANT Turn
    update conversation.updated_at
    commit
```

**不要把整个 LLM 网络调用包在数据库 transaction 中**（长事务 = 连接占用 +
锁等待 + 失败回滚语义混乱）。

### 11.2 原子性规则

* `append_turn()` = 一个事务（插 Turn + 更新 `updated_at` 两条语句同事务）；
* `archive_conversation()` = 一个事务（状态 + `updated_at`）；
* Service 不跨多个 Repository 事务编排（不存在"Service 开事务"形态）。

### 11.3 失败恢复语义

| 失败点 | 结果 |
| --- | --- |
| 事务 #1 失败 | 请求失败，无 USER Turn（无半写） |
| AI 执行失败 | USER Turn 保留；ASSISTANT Turn 按 §8 规则 |
| 事务 #2 失败 | USER Turn 保留；ASSISTANT Turn 缺失（可用同一 user turn 重新发起 = regenerate 场景） |

本 Step 只冻结边界，不实现。

---

## 12. Read / Write Separation

### 12.1 结论

**不拆分 CQRS**：保持单一 ConversationService。

### 12.2 理由

* 当前规模：5 个方法、两类实体（Conversation / Turn）、单一存储；
* 读写路径差异不显著（都是 thin DB 操作 + 简单规则）；
* 拆分（ConversationQueryService / ConversationCommandService）会带来
  两层装配与两处文档漂移，收益不足；
* 项目既有先例遵循同一原则：只在**存在明确边界**时才分离
  （观测层 QueryService / PersistenceService 分离是因为读取面向
  Trace API、写入面向 best-effort 观测链路，职责确实不同）。

### 12.3 未来条件

若未来读写路径显著分化（例如读侧引入缓存 / 投影 / 多个消费者），
再单独评估拆分 —— 属 Deferred，不在本步。

---

## 13. Trace Separation

ConversationService **不负责**（也不注入、不调用）以下既有服务：

```text
LLMUsageQueryService
ToolObservabilityQueryService
RagExecutionPersistentQueryService
AssistantTraceQueryService
AssistantTimelineQueryService
AssistantOutcomeQueryService
```

冻结规则：

* **不把 Trace 查询塞进 Conversation Repository**（Repository 只拥有
  Conversation / Turn 两张表）；
* 未来 `GET conversation detail`（含 Trace / Timeline）由 **Application Layer 组合**：

```text
Application Layer
    ├── ConversationService.get_conversation / list_turns
    ├── AssistantTraceQueryService.get_trace(assistant_request_id)
    └── AssistantTimelineQueryService.get_timeline(assistant_request_id)
```

* 组合层负责"从 Turn 列表提取 assistant_request_id → 调用既有只读 API/服务"，
  Conversation Service 只回答 Conversation / Turn 自身的事实；
* 现有 Trace / Timeline / Outcome 读边界与 API 全部不变。

---

## 14. Security

### 14.1 Conversation Service 不接触（冻结）

```text
api_key · password · database_url · Authorization · raw HTTP headers
LLM prompt · RAG chunks · Tool internal payload · SQL · DB Session
```

* Service / Repository 的输入输出只包含 Step 2 冻结的字段（会话元数据 +
  user / assistant 文本）；
* **Repository 不返回 SQLAlchemy Session**：只返回 DTO / Row（frozen dataclass）；
* Service 不做鉴权 / 不做脱敏（当前系统无认证，Step 1 §10 已如实记录）；
  未来认证接入时，归属校验属 Application Layer / Service 的新增 Contract。

### 14.2 与既有边界的关系

* Conversation History 未来可能比 Trace 更敏感（Step 2 §13 延续）；
* 观测侧（LLM Usage / Tool / RAG / Outcome）的安全白名单不变；
* 本 Step 不修改任何安全相关的既有代码。

---

## 15. Contract Tests

新增文件：

```text
tests/test_conversation_service_boundary_contract.py
```

覆盖矩阵（对应任务 §二十三 的 10 项要求 + 扩展）：

| # | 要求 | 测试类 |
| --- | --- | --- |
| 1 | Service 只属于 Conversation Layer | `TestServiceLayerBoundary`（5 方法 + 签名 + 依赖 + 同步性） |
| 2 | 不依赖 LLM / RAG / Tool / Router / AIOrchestrator | `TestNoAiCoreDependency` |
| 3 | 不依赖 FastAPI Request / Response | `TestNoFastApiDependency` |
| 4 | 不依赖 DeepSeek / network | `TestNoNetworkOrLlmDependency`（自身 AST 自审） |
| 5 | Create Contract | `TestCreateContract` |
| 6 | Archive Contract（幂等） | `TestArchiveContract` |
| 7 | Project Binding immutable | `TestProjectBinding` |
| 8 | Turn Contract（USER / ASSISTANT） | `TestAppendTurnContract` |
| 9 | Outcome separation | `TestOutcomeSeparation` |
| 10 | Trace separation | `TestTraceSeparation` |
| — | Repository Boundary | `TestRepositoryBoundary` |
| — | Transaction / CQRS | `TestTransactionAndCqrs` |
| — | 现有边界不变 | `TestExistingBoundaryUnchanged` |
| — | Security | `TestSecurityBoundary` |
| — | 文档完整性 + 不变式 | `TestDesignDocument` |
| — | 自身 AST 审计 | `TestSelfAudit` |

设计骨架说明：`_ConversationServiceContractDesign` /
`_ConversationRepositoryContractDesign` / 设计异常类 **只存在于测试文件内**
（`_` 前缀），仅用于 `inspect.signature` 冻结；**没有**创建 production 代码。

运行命令（唯一允许）：

```powershell
python -m pytest -q tests/test_conversation_service_boundary_contract.py
python -m compileall -q backend tests scripts
```

---

## 16. Deferred Implementation

本 Step 结束后的不变式：

```text
DB = unchanged
API = unchanged
Orchestrator = unchanged
RAG = unchanged
Tool = unchanged
LLM = unchanged
```

明确 Deferred（全部未实现）：

| 事项 | 状态 |
| --- | --- |
| ConversationService production code | Deferred（本步禁止） |
| ConversationRepository production code | Deferred（本步禁止） |
| ORM / 表 / migration | Deferred（Step 2 已冻结模型） |
| Conversation API（Step 1 §13 设计） | Deferred |
| conversation_id 生成器（new_conversation_id） | Deferred |
| 状态迁移实现（ACTIVE → ARCHIVED 落库） | Deferred |
| 事务实现（两段式） | Deferred |
| 一致性校验（project_id == conversation.project_id） | Deferred |
| 幂等键 client_message_id | Deferred（Step 1 §12 / Step 2 §16） |
| 认证 / 授权 / ownership | Deferred |
| Context Builder / Memory / Summary | Deferred |
| Regenerate API | Deferred |
| conversation detail 的 Trace / Timeline 组合端点 | Deferred（§13 设计） |

---

## 17. Audit Evidence

### 17.1 实际阅读文件（本步）

```text
backend/app/api/orchestrator_chat.py
backend/app/services/ai_orchestrator_service.py
backend/app/services/assistant_trace_query_service.py
backend/app/services/assistant_timeline_query_service.py
backend/app/services/assistant_outcome_query_service.py
backend/app/services/tool_execution_persistence_service.py
backend/app/services/chat_service.py
backend/app/db/session.py
backend/app/db/assistant_outcome_repository.py
backend/app/db/llm_usage_repository.py        （partial unique / 风格）
backend/app/db/models/（6 个模型）
docs/evaluation/Phase 4.1 Step 1 — Conversation Architecture Audit.md
docs/evaluation/Phase 4.1 Step 2 — Conversation Data Model Audit.md
tests/test_conversation_architecture_contract.py
tests/test_conversation_model_contract.py
```

### 17.2 与任务提示的差异（如实记录）

| 任务提示路径 | 实际情况 |
| --- | --- |
| `backend/app/db/repositories/` | **不存在该子目录**；既有 Repository 直接位于 `backend/app/db/`（扁平结构：`assistant_outcome_repository.py` / `llm_usage_repository.py` / `rag_execution_repository.py` / `tool_execution_repository.py`）。未来 ConversationRepository 应沿用同一位置风格。 |

### 17.3 分层风格证据（本 Step 冻结所依据）

| 维度 | 现有先例 |
| --- | --- |
| Service 构造注入 Repository（None → 默认） | `ToolExecutionPersistenceService` / `AssistantOutcomeQueryService` |
| 查询语义 → `None`（"None 不猜"） | `AssistantOutcomeQueryService` |
| 命令失败 → 域异常家族 | `AIOrchestratorError` 家族 / `*RepositoryError` |
| 字段校验 → `ValueError` | `ChatService` / QueryService |
| Service 不 import FastAPI | `services/` 既有文件（`chat_service.py` 等 0 匹配） |
| Repository 方法内一方法一事务 | `AssistantOutcomeRepository.create/get_by_...` |
| Repository 返回 frozen Row（非 ORM / 非 Session） | `AssistantOutcomeRow` |
| View 从 Row 转换（Application 边界） | `LLMUsageQueryService` → `LLMUsageTraceRecordView` |
| 组合层不包装下游异常 | `AssistantTraceQueryService` |

### 17.4 本 Step 变更范围（git diff 证明）

```text
允许：
    tests/test_conversation_service_boundary_contract.py                （新增）
    docs/evaluation/Phase 4.1 Step 3 — Conversation Service Boundary Audit.md（新增）

必须不变：
    backend/app/            = unchanged
    backend/app/api/        = unchanged
    backend/app/db/         = unchanged
    .github/                = unchanged
    migration/              = 无
```

### 17.5 运行记录

```text
python -m pytest -q tests/test_conversation_service_boundary_contract.py
    → 46 passed（纯离线：不驱动 /api/ai/chat、不写任何表、不调用 LLM；
      本机环境本次运行 session fixture setup ~260s，见下）

python -m pytest -q tests/test_conversation_service_boundary_contract.py --noconftest
    → 46 passed in 0.77s（对照：测试文件自身 < 1s，零 DB / 零网络）

python -m compileall -q backend tests scripts
    → 通过
```

纯度口径（本 Step 产物）：DB writes = 0 · DB reads = 0 · network = 0 · LLM = 0。

环境说明（延续 Step 1 §16.4 / Step 2 §20.4）：仓库 `tests/conftest.py` 的既有
会话级残留守卫在配置了 `DATABASE_URL` 的环境下会执行 1 次水位查询与 1 次
"本次会话新增行"清理（本 Step 测试不产生 outcome 行，DELETE 影响 0 行；
属既有测试基础设施行为）。该 fixture setup 在本机环境耗时约 260s
（DB 连接缓慢的环境波动；`--noconftest` 对照运行 0.77s 证明测试内容本身无慢点）。
如需严格 0 接触，可使用 `--noconftest` 对照命令。

未运行：`RUN_DB_TESTS` / 全量 `pytest -q`（本 Step 明确排除）。

---

## 18. 参考资料

* `docs/evaluation/Phase 4.1 Step 1 — Conversation Architecture Audit.md`
* `docs/evaluation/Phase 4.1 Step 2 — Conversation Data Model Audit.md`
* `docs/architecture.md` §20 Conversation Architecture / §21 Context Management（规划）
* `tests/test_conversation_architecture_contract.py`
* `tests/test_conversation_model_contract.py`
