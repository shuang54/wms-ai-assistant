# Phase 4.1 Step 7 — Conversation API Contract Audit

> 状态：**Design / Freeze Only**（**不实现** Conversation API、不注册路由）
> 范围：Conversation Management HTTP API 的请求/响应契约、状态码、错误映射、安全边界
> 产物：本文件 + `tests/test_conversation_api_contract.py`
>       + `tests/test_conversation_api_architecture_audit.py`
> 纪律：DB = 0 · Network = 0 · DB Writes = 0 · DeepSeek = 0
>
> 当前真实状态：
>
> ```text
> Conversation Persistence = READY      （Step 5 实现 / Step 6 真实 DB 验证）
> Conversation HTTP API    = NOT IMPLEMENTED（routes = 0）
> ```

---

## 1. Current State

### 1.1 真实 API 架构（代码审计结论，不凭此前报告推断）

| 关注点 | 真实实现 | 证据 |
| --- | --- | --- |
| Router 注册 | `backend/app/main.py`：`app.include_router(<module>.router, prefix="/api", tags=[...])` | `main.py:39-57` |
| 模块划分 | 一个端点域一个模块：`api/orchestrator_chat.py` / `api/assistant_trace.py` / `api/assistant_timeline.py` 等 | `backend/app/api/` |
| DTO 风格 | Pydantic **v2**（`BaseModel` + `Field(...)` + `@field_validator`）；`response_model=` 显式声明 | `api/orchestrator_chat.py:156-249`；`requirements.txt: pydantic>=2.7.0,<3.0` |
| HTTP 方法 | 现有 API **只有 GET / POST**（无 PUT / PATCH / DELETE） | api 目录扫描 |
| 错误抛出 | `raise HTTPException(status_code=status.HTTP_xxx, detail=...)` | `api/assistant_trace.py`、`api/orchestrator_chat.py` |
| 统一错误映射 | RAG 链路：`api/_rag_error_mapping.py` 的 `rag_pipeline_error_to_http()`（400/422/502/503/500；未知异常原样上抛） | `_rag_error_mapping.py:52-202` |
| 响应风格 | `response_model=<Pydantic DTO>` + `responses={200:..., 4xx:..., 5xx:...}` 显式状态码文档 | `api/assistant_trace.py:266-280` |
| **409 先例** | **无**（项目此前未使用 409；`409` 仅出现在 SQL `ON CONFLICT` 语境） | 代码搜索 |
| `/api/ai/chat` Contract | 请求 `{question, project_id?}`；响应 `{route, content, data, metadata}`（metadata 含 `request_id` = Assistant Trace ID） | `api/orchestrator_chat.py` |
| Observability Contract | `GET /api/observability/assistant-trace/{assistant_request_id}`、`GET /api/observability/assistant-timeline/{assistant_request_id}`（键 = assistant_request_id） | `api/assistant_trace.py` / `api/assistant_timeline.py` |

### 1.2 本 Step 的边界

* 只冻结 **Conversation Management API**（4 个端点）；
* **不**实现 `POST /api/conversations/{conversation_id}/messages`（写消息 + 触发 AI = Chat/Application Layer，后续阶段）；
* 不修改 `main.py`、不新增 FastAPI route、不改 `/api/ai/chat`、不改 Trace / Timeline；
* 不改 ConversationService / Repository / ORM。

---

## 2. Proposed Endpoints

| # | 方法 + 路径 | 用途 | 请求 | 响应 |
| --- | --- | --- | --- | --- |
| 1 | `POST /api/conversations` | 创建会话 | `CreateConversationRequest` | 200 `ConversationResponse` |
| 2 | `GET /api/conversations/{conversation_id}` | 会话 metadata | path 参数 | 200 `ConversationResponse` |
| 3 | `GET /api/conversations/{conversation_id}/messages` | 消息列表 | path 参数 | 200 `ConversationMessagesResponse` |
| 4 | `POST /api/conversations/{conversation_id}/archive` | 归档（幂等） | path 参数 | 200 `ConversationResponse` |

约束：

* 只用 **GET / POST**（与现有 API 一致）；
* 不提供 DELETE / PATCH / PUT；
* 不提供 `switch-project` / `unarchive` / 物理删除端点；
* 全部位于 `/api` prefix 之下（由 `main.py` 的 `include_router(..., prefix="/api")` 提供）。

---

## 3. Request / Response DTO

### 3.1 `CreateConversationRequest`

```json
{ "project_id": "vietnam-wms" }
```

| 字段 | 规则 |
| --- | --- |
| `project_id` | 必填（required）· 非空（strip 后非空）· `String`（1..128） |

**不得**包含：`conversation_id` / `status` / `created_at` / `updated_at` /
`user_id` / `tenant_id` / `title` / `name` / `metadata` / `context` / `system_prompt`
（全部 Deferred）。

### 3.2 `ConversationResponse`

```json
{
  "conversation_id": "5f2c…（UUID4）",
  "project_id": "vietnam-wms",
  "status": "ACTIVE",
  "created_at": "2026-10-03T12:00:00Z",
  "updated_at": "2026-10-03T12:00:00Z"
}
```

只允许 5 个字段：`conversation_id` / `project_id` / `status` / `created_at` / `updated_at`。
**不含** turns / messages / content（metadata only）。

### 3.3 `ConversationTurnResponse`

```json
{
  "turn_id": 1,
  "role": "USER",
  "content": "查询 A001 库存",
  "assistant_request_id": null,
  "created_at": "2026-10-03T12:00:00Z"
}
```

只允许 5 个字段：`turn_id` / `role` / `content` / `assistant_request_id` / `created_at`。

### 3.4 `ConversationMessagesResponse`

```json
{ "conversation_id": "5f2c…", "messages": [ … ] }
```

只允许 2 个字段（无分页字段）。

### 3.5 `assistant_request_id` 是否暴露给 HTTP 客户端

**允许暴露。** 理由：它是 `Conversation Turn → Assistant Trace → Timeline` 的正式
correlation boundary（客户端据此调用现有 Trace / Timeline 只读 API）。

**不暴露** LLM Provider request_id —— 两者完全不同
（`assistant_request_id` = 服务端单次 Assistant 请求；Provider request_id = LLM 供应商侧 ID）。

### 3.6 不暴露 ORM / 基础设施

响应只由 Pydantic DTO 组成：不含 SQLAlchemy Session / ORM 实例 / engine /
connection / repository 内部对象。

---

## 4. HTTP Status

| 状态 | 语义 | 出现于 |
| --- | --- | --- |
| 200 | 成功（创建 / 读取 / 列表 / 归档均返回 200） | 4 个端点 |
| 404 | 会话不存在 | GET 会话、GET messages、archive |
| 409 | 会话状态冲突（`ConversationArchivedError`） | 当前 4 个端点暂不触发；映射为未来 message write API 保留 |
| 422 | 请求体 / 路径参数校验失败（Pydantic / `Path(min_length=1, max_length=128)`） | 创建（project_id）、全部端点（conversation_id） |
| 500 | Repository / DB 失败（内部错误） | 全部端点 |

补充：

* 路径参数 `conversation_id` 边界：`min_length=1`、`max_length=128`
  （与既有 Assistant Trace API 的 `Path` 校验一致）；
* **409 是本 API 首次引入**（项目此前无 409 先例），仅用于状态冲突语义。

---

## 5. Error Mapping

| 来源 | Service / 校验 | HTTP | detail |
| --- | --- | --- | --- |
| 会话不存在 | `ConversationNotFoundError` | 404 | 固定文案（不含 content / 不含 traceback） |
| 会话已归档 | `ConversationArchivedError` | 409 | 固定文案（状态冲突，非权限问题） |
| 参数非法 | `ValueError`（project_id / role / content / request 组合） | 422 | 优先由 Pydantic 校验拦截 |
| 持久化失败 | `ConversationRepositoryError` | 500 | 固定文案；**不降级**为 404 / 空列表 |
| 未知异常 | 其它 | 500 | 不泄露 traceback / SQL / 凭据 |

要求：

* Repository failure **不得**映射为 404（不是"不存在"）或 `[]`（不是"空结果"）；
* error detail **不得**包含 conversation content（§10）。

---

## 6. Empty Semantics

| 场景 | 结果 |
| --- | --- |
| 会话不存在 | `404` |
| 会话存在但无消息 | `200` + `{ "conversation_id": "…", "messages": [] }`（**不是** null、不是 404） |

与 Step 6 Persistence Contract 一致（Repository：不存在抛错；存在但无 Turn 返回空元组）。

---

## 7. Archive Semantics

```http
POST /api/conversations/{conversation_id}/archive
```

| 当前状态 | 结果 |
| --- | --- |
| ACTIVE | `200` + `status = ARCHIVED`（`updated_at = now`） |
| ARCHIVED | `200` + `status = ARCHIVED`（**幂等**，无错误、无 DB 写入） |
| 不存在 | `404` |

* Archive ≠ Delete：本阶段不提供 DELETE，也不提供物理删除 API；
* 归档后仍可读取会话 metadata 与消息列表。

---

## 8. Ordering

`messages` 必须保持持久化层顺序：

```text
created_at ASC
turn_id ASC
```

* 不在 API 层重新排序（不 reverse、不按 LLM 顺序、不由客户端排序）；
* 相同 `created_at` 由 `turn_id` 稳定 tie-break（Step 6 已真实验证）。

---

## 9. Project Binding

* `project_id` 在创建时写入，之后**不可修改**；
* API **不允许 PATCH project_id**，也不提供 `POST /api/conversations/{id}/switch-project`；
* 所有定位只按 `conversation_id`（不按 project_id 查询 / 覆盖）；
* 响应始终回显会话创建时绑定的 `project_id`。

---

## 10. Security

HTTP 响应**禁止**包含：

```text
API Key · Authorization · password · database_url · SQL
prompt · system_prompt · messages raw · RAG chunk · embedding
raw LLM response · LLM headers · internal traceback · SQLAlchemy objects
```

* `conversation_turn.content` **允许**返回（它是用户明确保存的会话内容）；
* 但 `content` **不得**写入日志 / error message / exception（避免正文进入错误通道）；
* 不暴露 Provider request_id、不暴露 ORM / Session；
* 本阶段不做 DLP / 脱敏变换（只保证不新增敏感字段返回）。

---

## 11. Authentication Limitation

```text
Authentication = NOT IMPLEMENTED
Authorization = NOT IMPLEMENTED
```

* 当前项目没有完整认证体系；
* `GET /api/conversations/{conversation_id}` 只依赖 `conversation_id` 定位，
  这是一条明确的**安全限制**，本阶段不解决；
* `project_id` **不是**安全边界（不得假装它是权限隔离）。

---

## 12. Backward Compatibility

* `POST /api/ai/chat` **完全不变**：请求 `{question, project_id?}`、响应
  `{route, content, data, metadata}`；
* **不**向 `/api/ai/chat` 响应新增 `conversation_id`；
* **不**修改 `AIOrchestrationResult`；
* Trace / Timeline / Outcome API 与响应结构不变；
* 本阶段只是"新增 Conversation Management API"的设计冻结，Chat 集成留待后续阶段。

---

## 13. Pagination Deferred

* 本阶段**不实现 Pagination**（无 `limit` / `offset` / `cursor` / `page` / `before` / `after`）；
* 原因：先保持 Conversation API 简单；
* 未来若历史变大再单独设计（limit / cursor / before / after），不在本阶段提前引入。

---

## 14. Message POST Deferred

* **不**在本阶段实现 `POST /api/conversations/{conversation_id}/messages`；
* 原因：它会立即涉及 `Conversation → AIOrchestrator → LLM/RAG/Tool/T2SQL → Assistant Turn`，
  已进入 Chat / Application Layer；
* 本阶段只冻结 Conversation Management API。

---

## 15. Implementation Deferred

| 事项 | 状态 |
| --- | --- |
| 4 个 Conversation API 的实现与路由注册 | Deferred（本 Step 明确不实现） |
| `POST /api/conversations/{id}/messages`（写消息 + 触发 AI） | Deferred |
| Chat 集成（Conversation ↔ AIOrchestrator 组合） | Deferred |
| Context Builder / Memory / Summary | Deferred |
| Regenerate API | Deferred |
| Authentication / Authorization | Deferred |
| Pagination | Deferred（§13） |
| 物理删除 / Delete API | Deferred（Archive ≠ Delete） |
| conversation detail 的 Trace / Timeline 组合端点 | Deferred |

---

## 16. Contract Tests

| 文件 | 覆盖 |
| --- | --- |
| `tests/test_conversation_api_contract.py` | 20 项：create/get/turn/messages DTO 字段 · 端点集合 · 404 / 409 / 422 / 500 映射 · empty = [] · ordering · project binding · UUID4 服务端生成 · assistant_request_id correlation · 禁止字段 · 无 SQLAlchemy 泄漏 · 无 secrets · 无分页 · 无 auth claim · `/api/ai/chat` 不变 · 未实现路由 · 文档章节 · 自身离线审计 |
| `tests/test_conversation_api_architecture_audit.py` | conversations 路由数 = 0（源码 + 应用路由表双重）· 无 conversation api 模块 · `main.py` 未注册 · ConversationService 不 import FastAPI / APIRouter / Request · Repository 与 ORM 不 import FastAPI / AI · 既有路由未变 · 自身离线审计 |

设计 DTO 只存在于测试文件内部（`_` 前缀的 Pydantic 模型），**没有**创建 production DTO。

---

## 17. Audit Evidence

### 17.1 实际阅读文件（本步）

```text
backend/app/main.py
backend/app/api/orchestrator_chat.py
backend/app/api/assistant_trace.py
backend/app/api/assistant_timeline.py
backend/app/api/_rag_error_mapping.py
backend/app/api/chat.py / tool_chat.py / usage.py / tool_observability.py（状态码与风格）
backend/app/services/conversation_service.py
backend/app/dto/（assistant_outcome.py 等）
requirements.txt（fastapi>=0.115 / pydantic>=2.7）
tests/test_chat_api.py（API 契约测试风格）
tests/test_conversation_service_boundary_contract.py（Step 3 契约）
tests/test_conversation_persistence_db.py（Step 6 契约）
```

### 17.2 运行记录

```text
python -m pytest -q tests/test_conversation_api_contract.py           → 通过（纯离线）
python -m pytest -q tests/test_conversation_api_architecture_audit.py → 通过（纯离线）
python -m compileall -q backend tests                                 → 通过
```

纯度口径：DB = 0 · Network = 0 · DB Writes = 0 · DeepSeek = 0。
未运行 `pytest -q`（全量）与 `RUN_DB_TESTS=1`。

### 17.3 Git Diff（本 Step）

```text
允许（新增）：
    tests/test_conversation_api_contract.py
    tests/test_conversation_api_architecture_audit.py
    docs/evaluation/Phase 4.1 Step 7 — Conversation API Contract Audit.md

必须不变：
    ConversationService      = unchanged
    ConversationRepository   = unchanged
    Conversation ORM         = unchanged
    ConversationTurn ORM     = unchanged
    Conversation API routes  = 0
    AI Chat API              = unchanged
    Trace API                = unchanged
    Timeline API             = unchanged
```

---

## 18. 参考资料

* `docs/evaluation/Phase 4.1 Step 5 — Conversation Persistence Implementation.md`
* `docs/evaluation/Phase 4.1 Step 6 — Conversation Persistence DB Verification.md`
* `backend/app/api/_rag_error_mapping.py`（既有统一错误映射范式）
* `docs/api.md`
