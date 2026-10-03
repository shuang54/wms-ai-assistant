# Phase 4.1 Step 8 — Conversation API Implementation

> 状态：**Conversation Management API 已实现**（4 个端点；不含 Chat/Application Layer）
> 新增/修改文件：
>
> ```text
> backend/app/dto/conversation_api.py     （新增：HTTP DTO）
> backend/app/api/conversations.py        （新增：4 个 route）
> backend/app/main.py                     （修改：注册 conversations.router）
> tests/test_conversation_api.py          （新增：Fake Service + TestClient）
> tests/test_conversation_api_contract.py （更新：NOT IMPLEMENTED → IMPLEMENTED / route 4）
> tests/test_conversation_api_architecture_audit.py（更新：route 0 → 4）
> docs/evaluation/Phase 4.1 Step 8 — Conversation API Implementation.md
> ```
>
> 未改动（严格）：
>
> ```text
> ConversationService      = unchanged
> ConversationRepository   = unchanged
> Conversation ORM         = unchanged
> ConversationTurn ORM     = unchanged
> AIOrchestrator / AIRouter / RAG / Tool / T2SQL / LLM / Trace / Timeline / Outcome = unchanged
> POST /api/ai/chat        = unchanged
> ```

---

## 1. API Implementation

```text
HTTP Client
    ↓
Conversation API（api/conversations.py：校验 + 异常映射 + DTO 转换）
    ↓
ConversationService（会话生命周期；业务规则 owner）
    ↓
ConversationRepository（持久化 / 事务）
    ↓
PostgreSQL（ai_ops.conversation / ai_ops.conversation_turn）
```

实现要点：

* API 层**只做 HTTP 适配**：不直接访问 SQLAlchemy / Repository / DB；
* 响应全部经 `backend/app/dto/conversation_api.py` 的 Pydantic DTO
  （`response_model` 同时是 OpenAPI 契约与字段过滤边界）；
* 模块级 `_conversation_service` + `get_conversation_service()` accessor
  （与既有 api 模块风格一致：`api/chat.py` 的 `_chat_service`、
  `api/assistant_trace.py` 的装配 accessor）；
* 路由路径不带 `/api` 前缀 —— 前缀由 `main.py` 的
  `include_router(conversations.router, prefix="/api", tags=["conversations"])` 提供。

---

## 2. Endpoints

| 方法 + 路径 | 调用 | 成功 | 失败 |
| --- | --- | --- | --- |
| `POST /api/conversations` | `create_conversation(project_id=...)` | 200 `ConversationResponse` | 422 / 500 |
| `GET /api/conversations/{conversation_id}` | `get_conversation(...)` | 200 `ConversationResponse`（**不含 turns**） | 404 / 422 / 500 |
| `GET /api/conversations/{conversation_id}/messages` | `list_turns(...)` | 200 `ConversationMessagesResponse` | 404 / 422 / 500 |
| `POST /api/conversations/{conversation_id}/archive` | `archive_conversation(...)` | 200 `ConversationResponse` | 404 / 409 / 422 / 500 |

* 只有 **4 个**端点（不多一个）；
* 不提供：DELETE / PATCH / PUT、`POST /api/conversations/{id}/messages`、
  `switch-project`、物理删除、Pagination。

---

## 3. DTO（`backend/app/dto/conversation_api.py`）

| DTO | 字段 |
| --- | --- |
| `CreateConversationRequest` | `project_id`（required / str / 1..128 / strip 非空） |
| `ConversationResponse` | `conversation_id` · `project_id` · `status` · `created_at` · `updated_at` |
| `ConversationTurnResponse` | `turn_id` · `role` · `content` · `assistant_request_id` · `created_at` |
| `ConversationMessagesResponse` | `conversation_id` · `messages`（`list[ConversationTurnResponse]`，空 = `[]`） |

* `CreateConversationRequest` **不接受** conversation_id / status / created_at /
  updated_at / user_id / tenant_id / title / name / metadata / context / system_prompt；
* 不返回 ORM / SQLAlchemy Row / Session / Repository / Engine；
* `assistant_request_id` 暴露（correlation），**不**暴露 LLM Provider request_id。

---

## 4. Error Mapping（Step 7 冻结）

| 来源 | HTTP | detail |
| --- | --- | --- |
| `ConversationNotFoundError` | 404 | Service 异常文案（不含正文 / 不含 traceback） |
| `ConversationArchivedError` | 409 | 状态冲突（当前 4 个端点真实路径不触发；契约保留） |
| `ValueError`（project_id / 路径参数非法） | 422 | 优先由 Pydantic / `Path(min_length=1, max_length=128)` 拦截 |
| `ConversationRepositoryError` | 500 | 固定文案「会话服务不可用」（**不降级**为 404 / 空列表） |
| 未知异常 | 500 | 固定文案「会话服务内部错误」（不暴露 traceback / SQL / 凭据） |

* 路径参数 `conversation_id`：`Path(min_length=1, max_length=128)`（与既有 Trace API 一致）；
* 500 响应不含：traceback · exception message · SQL · database_url · credentials。

---

## 5. Security

* 响应字段严格等于冻结 DTO；不含 `api_key` / `authorization` / `password` /
  `database_url` / SQL / prompt / system_prompt / raw LLM response / RAG chunk /
  embedding / headers / traceback / SQLAlchemy Session / Engine；
* 允许：`content`（用户保存的会话正文）与 `assistant_request_id`（correlation）；
* error detail 使用固定文案，正文内容不进入错误通道（日志 / exception / detail）；
* 本阶段不做 DLP / 脱敏变换（只保证不新增敏感字段）。

---

## 6. Backward Compatibility

* `POST /api/ai/chat` **完全不变**：请求 `{question, project_id?}`、响应
  `{route, content, data, metadata}`；未向其响应新增 `conversation_id`；
  未修改 `AIOrchestrationResult`；
* Trace / Timeline / Outcome API 与响应结构不变；
* 本次只新增 Conversation Management 端点，未修改任何既有路由。

---

## 7. Tests

| 文件 | 结果 | 覆盖 |
| --- | --- | --- |
| `tests/test_conversation_api.py` | 28 passed | Create（valid / empty / blank / missing / too long / 服务端生成 id / 字段精确 / 客户端无法指定 id）· Get（200 / 404 / 422 / metadata only）· Messages（empty [] / USER / ASSISTANT / ordering 不重排 / assistant_request_id / 无禁字段 / 404）· Archive（ACTIVE→ARCHIVED / 幂等 / 404 / 409）· Errors（RepositoryError→500 / unknown→500 / 无 traceback / 无 secret）· Routes（4 个端点精确 / 无 DELETE·PATCH·PUT·message POST）· 兼容（/api/ai/chat · Trace · Timeline 不变） |
| `tests/test_conversation_api_contract.py` | 27 passed | 原 Step 7 契约保持 + 「NOT IMPLEMENTED → IMPLEMENTED、route = 4」 |
| `tests/test_conversation_api_architecture_audit.py` | 15 passed | route = 4（源码 + OpenAPI 双重）· 单一路由模块 · main.py 注册 · Service/Repository/ORM 无 FastAPI 与 AI Core 依赖 · 既有路由未变 |

测试方式：FastAPI `TestClient`（ASGI 内存调用）+ **Fake ConversationService**
（monkeypatch 模块级 Service；不修改 Production Service）。

隔离指标：**DB = 0 · Network = 0 · LLM = 0 · DB Writes = 0**
（未运行 `RUN_DB_TESTS=1`；真实 PostgreSQL API E2E 属 Step 9）。

---

## 8. Deferred

```text
Message POST = Deferred      （POST /api/conversations/{id}/messages）
Chat Integration = Deferred  （Conversation ↔ AIOrchestrator 组合）
Context Builder = Deferred
Memory / Summary = Deferred
Regenerate = Deferred
Auth = Deferred              （Authentication / Authorization）
Pagination = Deferred
Delete / 物理删除 = Deferred （Archive ≠ Delete）
Switch Project = Deferred
conversation detail 的 Trace / Timeline 组合端点 = Deferred
```

---

## 9. 运行记录

```text
python -m pytest -q tests/test_conversation_api.py                     → 28 passed
python -m pytest -q tests/test_conversation_api_contract.py            → 27 passed
python -m pytest -q tests/test_conversation_api_architecture_audit.py  → 15 passed
python -m compileall -q backend tests                                  → 通过
```

未运行：`pytest -q`（全量）、`RUN_DB_TESTS=1`。

---

## 10. 参考资料

* `docs/evaluation/Phase 4.1 Step 7 — Conversation API Contract Audit.md`
* `docs/evaluation/Phase 4.1 Step 5 — Conversation Persistence Implementation.md`
* `docs/evaluation/Phase 4.1 Step 6 — Conversation Persistence DB Verification.md`
* `backend/app/api/assistant_trace.py`（既有 HTTP 边界与错误映射范式）
