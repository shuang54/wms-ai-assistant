# Phase 4.1 Step 10 — Chat / Application Layer Boundary Audit

- 阶段：Phase 4.1（Conversation / Chat 会话层）
- 类型：**Audit Only**（只审计 + 冻结设计；不实现 Conversation → AI 集成）
- 前置：Step 9（Conversation API → ConversationService → ConversationRepository → 真实 PostgreSQL，23 passed）
- 状态：`Production Code Changes = 0` · `DB Schema = unchanged` · `DB Writes = 0` · `Network = 0` · `LLM = 0`

本 Step 唯一目标：

> **审计当前 Chat / AI Application Layer，确定 Conversation 应该在哪一层进入 AI 执行链路。**

最终冻结的 Integration Point：

```text
/api/conversations/{conversation_id}/messages
                ↓
       Chat Application Service            ← 未来新增（本 Step 不实现）
                ├── ConversationService    → Conversation / Turn persistence
                └── AIOrchestratorService  → AI execution
```

---

## 1. Current Architecture

当前系统存在**两条完全独立**的请求链路（真实代码）：

```text
# 链路 1：AI 执行（无会话）
POST /api/ai/chat                       backend/app/api/orchestrator_chat.py:628
        ↓
AIOrchestratorService.execute()         backend/app/services/ai_orchestrator_service.py:574
        ↓
AIRouterService.route()
        ├── RAG
        ├── Tool
        └── Text-to-SQL
        ↓
Assistant Outcome / LLM Usage / Tool Execution / RAG Execution
```

```text
# 链路 2：Conversation 持久化（无 AI）
POST /api/conversations                 backend/app/api/conversations.py:103
GET  /api/conversations/{id}            backend/app/api/conversations.py:150
GET  /api/conversations/{id}/messages   backend/app/api/conversations.py:212
POST /api/conversations/{id}/archive    backend/app/api/conversations.py:277
        ↓
ConversationService                     backend/app/services/conversation_service.py
        ↓
ConversationRepository                  backend/app/db/conversation_repository.py
        ↓
conversation / conversation_turn（PostgreSQL）
```

```text
# 链路 3（既有旁路，RAG-only，非 Application Layer）
POST /api/chat                          backend/app/api/chat.py:94
        ↓
ChatService（只委托 RagService）         backend/app/services/chat_service.py:36
        ↓
RagService
```

三条链路当前**零交叉 import**：

| 校验项 | 真实结果 | 证据 |
| --- | --- | --- |
| ConversationService → AI Core | 无 | `conversation_service.py` 仅 import `conversation_repository` |
| AIOrchestrator → Conversation | 无 | `ai_orchestrator_service.py` import 列表中无任何 conversation 依赖 |
| Conversation API → AI Core | 无 | `api/conversations.py` 仅 import FastAPI / DTO / Service / Repository 错误类 |
| Repository / ORM → AI Core | 无 | `conversation_repository.py` 仅 import SQLAlchemy / ORM / session |

不存在 `backend/app/application/` 与 `backend/app/use_cases/` 目录（已核实）——**Application Layer 目前尚未建立**，是未来 Integration 的落点。

---

## 2. Current Responsibilities

### ConversationService（`backend/app/services/conversation_service.py`）

```text
create_conversation(*, project_id)
get_conversation(conversation_id)
archive_conversation(conversation_id)
append_turn(*, conversation_id, role, content, assistant_request_id)
list_turns(conversation_id)
```

- 只负责：Conversation / Turn 生命周期持久化；
- 不负责：RAG / Tool / Text-to-SQL / LLM / Router / Planning / Memory；
- 不调用、不注入 AIOrchestrator（Step 3 冻结，Step 10 复核）。

### AIOrchestratorService（`backend/app/services/ai_orchestrator_service.py`）

- 只负责：一次 Assistant Request 的 AI execution（路由决策 + 三条执行路径 + Outcome 判定）；
- `request_id = new_request_id()`（:574）→ 写入 `assistant_trace_scope(request_id)`（:581）；
- Outcome 记录：`_record_assistant_outcome(request_id, outcome)`（:671，经注入的 outcome_recorder）；
- 不读、不写 Conversation；不感知 conversation_id / turn_id。

### API 层

- `api/orchestrator_chat.py`：`POST /api/ai/chat`，DTO 映射，**不生成 ID**（只透传 `metadata.request_id`）；
- `api/conversations.py`：4 个 Conversation 端点，**无** AI execution 语义；
- 两个 API 模块各自独立装配（`_default_orchestrator` / `_conversation_service`），无共享可变状态。

---

## 3. Future Integration Point

三个方案的评估（结论：**推荐方案 B**）：

### 方案 A（不采用）

```text
API
 ↓
ConversationService
 ↓
AIOrchestrator
```

问题：ConversationService 将开始依赖 AI Core → 两个业务领域**互相依赖**（Conversation ← AI、AI ← Conversation 的潜在双向耦合），违反 Step 3 已冻结的"ConversationService 不负责 AI Execution"。

### 方案 B（推荐）

```text
API
 ↓
Chat Application Service
 ├── ConversationService
 └── AIOrchestrator
```

- Application Service 承担 **workflow composition**（用例编排）；
- ConversationService 保持纯持久化；AIOrchestrator 保持纯执行；
- 依赖方向单向：Application → Domain Services，无反向依赖；
- 与"API → Application → Domain → Repository"分层自然对齐。

### 方案 C（不优先）

```text
API
 ↓
AI Application Service
 ├── ConversationService
 └── AIOrchestrator
```

与 B 结构相同，差异在**语义中心**：C 以 AI 为中心命名，容易让 Conversation 沦为 AI 的附属；且未来非 AI 用例（历史读取、归档检索、导出）不属于"AI 应用服务"。B（以 Chat 用例为中心）覆盖面更准确。

### 冻结结论

```text
推荐：方案 B
FUTURE_INTEGRATION_POINT = "Chat Application Service"
职责：ConversationService = "Conversation / Turn persistence"
      AIOrchestrator      = "AI execution"
      Application Service = "workflow composition"
```

命名注意事项（真实代码事实）：`backend/app/services/chat_service.py` 已存在一个 RAG-only 的 `ChatService`（服务于 `POST /api/chat`）。未来新增的 Application Service **不得**复用 `ChatService` 名称 —— 建议命名 `ChatApplicationService`（或放入未来的 `backend/app/application/` 包内），避免与既有 RAG 单链路服务混淆。

**本 Step 不创建这个 Service。**

---

## 4. Turn Lifecycle

未来 `POST /api/conversations/{conversation_id}/messages` 的用例流程（只设计，不实现）：

```text
append USER turn
        ↓
AI execution（AIOrchestratorService.execute）
        ↓
append ASSISTANT turn
```

契约要点（延续 Step 2 / Step 3 冻结）：

```text
USER turn      → assistant_request_id = NULL
ASSISTANT turn → assistant_request_id = AI request ID（非 provider request_id）
```

**禁止**"先创建 ASSISTANT Turn 再调用 AIOrchestrator"（AI 失败会留下空 assistant message）。

---

## 5. Transaction Boundary

```text
TX1：
    append USER turn
    COMMIT

AI execution
    ↓ outside transaction（外部耗时操作，不得占用 DB 连接 / 事务）

TX2：
    append ASSISTANT turn
    update conversation.updated_at
    COMMIT
```

- AI 执行（网络 + LLM + Tool + SQL）**必须**在 DB 事务之外；
- TX1 与 TX2 各自独立提交（与 Step 3 / Step 4 两段式事务冻结一致）；
- TX1 成功而 AI 失败 → USER turn 保留（见 §7）。

---

## 6. ID Correlation

```text
conversation_id
      │
      └── turn_id
              │
              └── assistant_request_id
                      │
                      ├── LLM Usage
                      ├── Tool Execution
                      ├── RAG Execution
                      └── Assistant Outcome
```

四个 ID 语义独立、互不等价：

| ID | 语义 | 生成者 | 现状 |
| --- | --- | --- | --- |
| `conversation_id` | 多轮会话容器 | ConversationService（服务端签发） | 已实现（Step 5–9） |
| `turn_id` | 会话内一次消息/轮次 | 数据库自增 | 已实现 |
| `assistant_request_id` | 一次 AI Core execution | `AIOrchestratorService.execute()`（`new_request_id()`，:574） | 已实现 |
| `provider request_id` | LLM Provider 级请求 ID | Provider / LLM Client | 已实现（`llm_usage_record.request_id` 维度） |

关键约束：

- `provider request_id` 只属于 LLM Usage 自身维度，**不能**替代 `assistant_request_id`；
- 未来 Chat 集成**必须复用** `AIOrchestratorService.execute()` 已生成的 `assistant_request_id`，**不得**新增第二套 ID generator；
- `assistant_request_id` 在 execute() 内部生成（API 层不生成，Phase 3.12 Step 35 契约），因此 Application Service 只能从执行结果（`metadata.request_id`）取回该 ID 再写入 ASSISTANT turn。

---

## 7. Failure Semantics

未来 Chat Integration 的失败语义（设计结论，本 Step 不修改任何代码）：

| 场景 | USER turn | ASSISTANT turn |
| --- | --- | --- |
| AI 成功（SUCCESS） | 保留 | 创建（有 content） |
| AI 拒绝（REFUSED） | 保留 | 创建（拒绝话术是可展示内容） |
| AI 空结果（EMPTY） | 保留 | 创建（若有可展示的兜底话术） |
| AI 失败（FAILED，但有可展示内容） | 保留 | 创建 |
| 纯内部异常（无任何可展示内容） | 保留 | **不创建** |

冻结规则：

```text
USER turn 保留（不因 AI 失败而回滚 TX1 —— 用户消息是会话事实）

只有可展示给用户的最终内容才创建 ASSISTANT turn
纯内部异常（无内容）→ 不创建 ASSISTANT turn
```

Outcome（SUCCESS / EMPTY / REFUSED / FAILED）**不复制**进 ConversationTurn；两者通过 `assistant_request_id` 关联。

---

## 8. Project Binding

```text
conversation.project_id = AI execution project_id
```

- Conversation 创建时绑定 `project_id`，之后不可切换（Step 2 / Step 3 冻结）；
- 未来 Chat 集成时，AI execution 的 `project_id` **必须**取 `conversation.project_id`，客户端**不得**通过每次 message request 覆盖；
- 若请求携带与 Conversation 不一致的 `project_id` → 拒绝（HTTP 4xx；具体形态属未来 Step）；
- 当前无 Auth：必须明确

```text
project_id binding ≠ authorization
```

project 绑定是**数据一致性边界**，不是权限边界；Conversation ownership 当前未定义（Step 1 结论延续）。

---

## 9. Backward Compatibility

```text
POST /api/ai/chat unchanged
```

当前 Contract（真实代码，`api/orchestrator_chat.py`）：

```text
Request:
{
  question,        # 必填，非空
  project_id?      # 可选，省略时用默认 ProjectContext
}

Response:
{
  route, content, data, metadata   # metadata 含 request_id（透传，API 层不生成）
}
```

- `/api/ai/chat` **不新增** conversation_id（本 Step 不改）；
- 未来 Conversation Chat 是**新增 endpoint**（`POST /api/conversations/{conversation_id}/messages`），而不是修改旧 `/api/ai/chat`；
- 旧 endpoint 保持无会话语义（一次性请求），继续服务非会话场景。

---

## 10. Deferred

以下全部延期，本 Step 不实现：

```text
Message POST（POST /api/conversations/{id}/messages）
Chat Application Service implementation
Context Builder
Memory
Summary
Regenerate
Auth
Pagination
```

补充延期项：

```text
conversation detail 的 Trace / Timeline 组合端点
失败语义的落库实现（USER turn 保留 + 条件式 ASSISTANT turn）
project 不一致时的 4xx 契约
```

---

## 11. Dependency Direction（Architecture Audit）

```text
# 目标分层
API
 ↓
Application（未来 Chat Application Service）
 ↓
Domain Services（ConversationService / AIOrchestratorService）
 ↓
Repository（ConversationRepository）
 ↓
PostgreSQL / AI Core 外部系统

# 独立链路（保持不变）
AI API
 ↓
AIOrchestrator
 ↓
AI Core
```

目标：Conversation 与 AI Core 保持**相对独立**；二者不互相 import，只在未来的 Application Layer 中组合。**不强制创建完整 DDD**（无 Aggregates / Domain Events / Unit of Work 泛化）。

当前实测结论：依赖方向**已经满足**目标（三条链路零交叉 import，见 §1 表格）。

---

## 12. Contract Tests

新增：`tests/test_chat_application_boundary_contract.py`（纯离线）。

覆盖：

```text
1  ConversationService 不 import AIOrchestrator / RagService / ToolChatService / TextToSQLService
2  AIOrchestrator 不 import ConversationRepository / Conversation ORM
3  /api/ai/chat 未新增 conversation_id（request / response 字段 + 源码 + 路由存在）
4  Conversation API 无 AI execution（import / 标识符 / 4 条 legacy 路由集合）
5  无新的 global mutable state（模块级裸赋值最小化）
6  无新的 DB dependency / LLM / Network
7  设计结论冻结（方案 B / 生命周期 / 事务 / 4 ID / 失败语义 / project binding）
8  既有边界不变（Outcome / Trace / Timeline / Service 方法集）
9  文档完整性
10 自身离线审计（测试文件自身零 DB / 零 Network / 零 LLM）
```

行号级证据锚点（真实代码）：

```text
api/orchestrator_chat.py:156        ChatRequest（question / project_id）
api/orchestrator_chat.py:219        ChatResponse（route / content / data / metadata）
api/orchestrator_chat.py:628        POST /ai/chat
api/orchestrator_chat.py:464        _default_orchestrator 装配
services/ai_orchestrator_service.py:574   request_id = new_request_id()
services/ai_orchestrator_service.py:581   assistant_trace_scope(request_id)
services/ai_orchestrator_service.py:671   _record_assistant_outcome(...)
api/conversations.py:73             _conversation_service 装配（无 AI 依赖）
```

运行命令：

```text
python -m pytest -q tests/test_chat_application_boundary_contract.py
python -m compileall -q backend tests
```

---

## 13. Current Limitations（如实记录）

1. **Application Layer 不存在**：无 `backend/app/application/` / `use_cases/`；Future Integration Point 当前只是设计结论。
2. **`ChatService` 命名已被占用**：`services/chat_service.py` 是 RAG-only 服务（服务于 `/api/chat`），未来 Application Service 命名必须区分（建议 `ChatApplicationService`）。
3. **无 Auth**：project binding 不是 authorization；Conversation ownership 未定义。
4. **无 Conversation ↔ AI 集成**：当前 Conversation API 与 `/api/ai/chat` 两条链路完全独立，无法从 conversation_id 直接得到 AI 回答（这是本 Step 之后才实现的）。
5. **ASSISTANT turn 的 assistant_request_id 获取路径依赖 metadata 透传**：目前唯一出口是 ChatResponse.metadata.request_id（Phase 3.12 Step 35 契约），未来 Application Service 需通过 Orchestrator 返回值获取，不得自行生成 ID。

---

## 14. Audit Verdict

```text
Production Code = 0
DB Schema = unchanged
Migration = 0
DB Writes = 0
Network = 0
LLM = 0

ConversationService → AI Core   = 无依赖（已核实）
AIOrchestrator → Conversation   = 无依赖（已核实）
/api/ai/chat Contract           = unchanged
Conversation API Contract       = unchanged（4 条 legacy 路由）
```

结论：当前代码结构**已经支持**方案 B（Chat Application Service 组合 Conversation + AI），无需重构既有分层；未来只需**新增** Application Layer 与新的 message endpoint。
