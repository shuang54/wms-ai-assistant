# Phase 3.7.11 — AI Chat API 集成（ADR）

日期：2026-09-24
阶段：Phase 3.7.11
状态：**已实施**

---

## 1. 背景 / 为什么新增 `/api/ai/chat`

Phase 3.7.9 完成了 `AIOrchestratorService`（一次 question → 单路由 → 单能力执行），
但此前 **FastAPI 没有任何端点能直接调用 Orchestrator**。

| 端点 | 路径 | 行为 |
|---|---|---|
| `/api/chat` (Phase 3.5.6) | POST | `ChatService → RagService`（**仅 RAG**） |
| `/api/chat/with-tools` (Phase 3.6.3) | POST | `ToolChatService`（**仅 Tool**） |
| `/api/rag/answer` (Phase 3.5.5) | POST | `RagService`（**仅 RAG**） |

> 这三个端点各自封装**单条 AI 能力**；调用方需要根据问题预先判断走哪个端点。
> 调用方无法享受到 `AIOrchestratorService` 的"按问题自动路由"。

本阶段新增 **`POST /api/ai/chat`**（Orchestrator-backed 统一入口），
**不替换** 上述任一旧端点：

* `/api/chat` 协议保持 Phase 2 + 3.5.6 字段（向后兼容）。
* `/api/ai/chat` 提供 `{route, content, data, metadata}` 统一响应，
  内部统一委托给 `AIOrchestratorService.execute()`。

---

## 2. 路径命名说明

需求文档示例使用 `POST /api/chat`，但 `/api/chat` 已被 `ChatService / RAG-only`
占用且经过严格回归测试（`tests/test_chat.py`，111 用例）。

为避免**协议语义不同**的两个端点共用同一路径导致客户端误判，
本阶段采用**新路径 `/api/ai/chat`**：

* `/api/chat`     → 仅 RAG（保留）
* `/api/ai/chat`  → Orchestrator-backed 统一入口（**新增**）

ADR 不破坏历史 `/api/chat` 行为；如未来需要将 `/api/chat` 升级为 Orchestrator，
应保留兼容期（双协议返回 `answer` 字段），不在本阶段处理。

---

## 3. API 与 Orchestrator 的职责边界

```
HTTP Client
  ↓
POST /api/ai/chat               ← Pydantic 入参校验（422）
  ↓
backend/app/api/orchestrator_chat.py  ← API 适配层（仅 DTO + 异常映射）
  ↓
AIOrchestratorService.execute()      ← 业务编排（路由 + 单能力执行）
  ↓
AIRouterService → RAG | Tool | Text-to-SQL
  ↓
AIOrchestrationResult
  ↓
ChatResponse (route / content / data / metadata)
```

**API 层严禁**：

| 行为 | 是否允许 |
|---|---|
| 直接调用 `RagService` / `ToolRegistry` / `TextToSQLService` | ❌ |
| 直接调用 `SQLValidator` / `SQLExecutor` | ❌ |
| 在 endpoint 内做路由判断（"if 库存 in question..."） | ❌ |
| 创建新的 `LLMClient` / `DBEngine` / `Embedding Client` | ❌ |
| 在 response 中暴露 API Key / DATABASE_URL / traceback / SQL / ToolHandler | ❌ |
| 硬编码 `vietnam-wms` / 任何 project_id 默认值 | ❌ |

**API 层只负责**：

* Pydantic 入参校验（422）
* 调用 `AIOrchestratorService.execute(question)`
* `AIOrchestrationResult → ChatResponse` 映射
* 业务异常 → HTTP 状态码映射（400 / 502 / 503 / 500）

---

## 4. Request / Response 设计

### 4.1 Request

```python
class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)
    project_id: str | None = Field(default=None, max_length=128)

    @field_validator("question")
    def _reject_blank_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question 不能为空或纯空白")
        return value
```

校验策略：

| 输入 | HTTP |
|---|---|
| 缺失 `question` 字段 | 422（Pydantic） |
| `question` 为 `""` | 422（Pydantic min_length=1） |
| `question` 为 `"   "` / `"\n\t "` | 422（field_validator） |
| `question` 类型不是 `str` | 422（Pydantic） |
| `project_id` 长度 > 128 | 422（Pydantic max_length） |

> **不允许** 通过 Request 传递：SQL / Tool handler / Tool 参数 / DB credentials /
> API key / arbitrary context —— DTO 仅含 `question` 与 `project_id` 两个字段。

### 4.2 Response（统一 envelope）

```python
class ChatResponse(BaseModel):
    route: str                       # rag | tool | text_to_sql
    content: str | None              # Orchestrator 的 content
    data: object | None              # route-specific 结构化数据
    metadata: dict[str, Any] = {}    # 路由 + 执行统计（不含敏感信息）
```

各 route 的 `data` 形态：

* **RAG**:
  ```json
  {
    "sources": [{"chunk_id": 101, "document_id": 12, "chunk_index": 3,
                 "similarity": 0.95, "metadata": {...}}],
    "used_chunks_count": 1
  }
  ```
  > **仅 sources 元数据**；不返回 `chunk content`（防知识库原文泄露）。

* **Tool**:
  ```json
  {
    "tool_name": "get_inventory",
    "success": true,
    "data": {"material_code": "10001", "qty": 120},
    "error": null
  }
  ```

* **Text-to-SQL**:
  ```json
  {
    "columns": ["material_code", "qty"],
    "rows": [["10001", 120], ["10002", 80]],
    "row_count": 2,
    "truncated": false,
    "execution_time_ms": 12.3,
    "referenced_tables": ["public.inventory"],
    "project_id": "vietnam-wms",
    "sql": "SELECT ..."
  }
  ```
  > rows 已 `list[list[Any]]` 化；不暴露 SQLAlchemy Row / Cursor。

---

## 5. Project Context 传递

### 5.1 设计原则

* API 层不修改 `AIOrchestratorService.execute(question, *, context=None)`
  的现有签名（**核心 API 不变**）。
* 通过**注入自定义 `ProjectContextProvider`** 实现 per-request project_id：

  ```
  request.project_id = "another-warehouse"
                          ↓
  _build_orchestrator_for_project_id("another-warehouse")
                          ↓
  AIOrchestratorService(
      router=base._router,            # 复用默认实例
      rag_service=base._rag,           # 复用
      tool_registry=base._tools,      # 复用
      text_to_sql=base._text_to_sql,  # 复用
      sql_executor=base._sql_executor,# 复用
      project_context_provider=_ProjectedProvider(),  # 重写
  )
  ```

  * 复用默认 Orchestrator 的 Router / RAG / Tool / T2S / Executor 实例
    （**Engine / LLMClient / Embedding Client 不重新创建**）。
  * 仅替换 `ProjectContextProvider.resolve()` 返回的 `ProjectContext.project_id`。
  * Schema / Semantic 仍来自默认 Provider，避免重复 inspect DB。

### 5.2 不做项目存在性校验

* 当前项目**没有 project registry**（不存有"项目是否存在"的真值来源）。
* API 层不做 `if project_id == "..."` 判断；直接透传到 Orchestrator。
* 未来若引入 project registry，应在此层新增 `404 Not Found` 映射。

---

## 6. 错误处理

| Orchestrator 异常 | HTTP | 说明 |
|---|---|---|
| `AIOrchestratorInputError` | 400 | question 非法 / 纯空白（防御性兜底，Pydantic 已覆盖大部分） |
| `AIOrchestratorRouteError` | 502 | Router 决策失败 / Tool 路由未命中 / 未知 route |
| `AIOrchestratorUnavailableError` | 503 | Project context 不可用 / DB Schema 解析失败 |
| `AIOrchestratorExecutionError` | 500 | RAG / Tool / T2S 能力执行失败；保留原始异常链 |
| 其它 `AIOrchestratorError` | 500 | 已知 Orchestrator 兜底异常 |
| 其它 `Exception` | 500 | 不暴露 traceback / 异常消息（仅 `detail="AI 服务内部错误"`） |
| Pydantic 校验失败 | 422 | FastAPI 自动 |

### 6.1 不泄露策略

* Response `detail` 不包含：API Key / Bearer / Authorization / DATABASE_URL /
  数据库密码 / traceback / SQL Validator 内部信息 / 数据库原始错误详情。
* `metadata["sql"]` 仅在 SQL 路径写入，且来自 Orchestrator 已校验过的 SQL；
  **不再追加** Schema 表名 / column 注释等敏感细节。
* `metadata` 不携带：`project_description` 内部数据、`data_source` 私有字段。

---

## 7. 为什么 API 层不直接调用 RAG / Tool / Text-to-SQL

* **职责清晰**：API 层 = HTTP 适配；Service 层 = 业务编排。
* **可替换性**：未来若 Orchestrator 引入 Agent / Loop，仅替换 Orchestrator，
  API 层协议不变。
* **可测试性**：单测通过 monkeypatch `_default_orchestrator` 即可覆盖所有 route。
* **纵深防御**：API 层不接触底层能力 → 即便 API 被攻击，攻击者也无法绕过
  Orchestrator 的安全链路（Validator / READ ONLY / statement_timeout）。

静态 + 运行时检查通过（`tests/test_chat_api.py::TestApiDoesNotBypassOrchestrator`）：

* AST 扫描：API 模块不 `import` `rag_service / tool_registry / text_to_sql_service /
  sql_validator_service / sql_executor_service`。
* 调用计数：每次 endpoint 调用 → `_default_orchestrator.execute()` 精确 1 次。

---

## 8. 当前限制

1. **无流式输出**：当前为 Request-Response 一次性返回，未支持 SSE / WebSocket。
2. **无认证 / 权限**：未引入 auth / RBAC；假设所有调用方均合法。
3. **无请求限流**：当前 API 层未做速率限制；生产部署需在前置网关层处理。
4. **无审计日志**：Orchestrator 不记录 user_id / request_id（受 Phase 3.7.11
   范围约束）。
5. **无法校验 project_id 合法性**：项目无 registry；当前仅透传 project_id。
6. **无缓存**：相同 question 不会复用上一次 Orchestration 结果。
7. **`/api/chat` 与 `/api/ai/chat` 共存**：用户需根据需要选择；不存在协议升级期。
8. **`metadata["sql"]` 仍暴露 SQL 字符串**：便于调试 / 测试断言；
   生产场景若需关闭应改 Orchestrator 或新增 `?omit_sql=true` 标志。
9. **未引入 FastAPI DI**：Orchestrator 仍走模块级单例 + monkeypatch；
   大规模依赖时需引入 FastAPI Depends()（**不在本阶段范围**）。

---

## 9. 测试覆盖

`tests/test_chat_api.py`（**31 个测试**）：

| 类别 | 用例数 | 覆盖 |
|---|---|---|
| `TestNormalRequest` | 4 | 正常请求 / question 透传 / execute 调用次数 / OpenAPI |
| `TestValidation` | 4 | 空 / 空白 / 缺失 / 非 str / project_id 超长 → 422 |
| `TestRagRoute` | 2 | sources 元数据 / 不泄露 chunk content / used_chunks_count |
| `TestToolRoute` | 2 | tool_name / success / data / 不泄露 Tool handler |
| `TestSqlRoute` | 2 | columns / rows / row_count / referenced_tables / project_id |
| `TestProjectContextPortability` | 2 | project_id 透传到 factory / 无 project_id 走默认 |
| `TestErrorMapping` | 11 | 4 类 Orchestrator 异常 + 未预期异常 + 6 类敏感片段不泄露 |
| `TestApiDoesNotBypassOrchestrator` | 2 | AST 静态 import 检查 + 运行时 execute 调用计数 |

---

## 10. 测试结果

```text
pytest -q
   → 1063 passed, 129 skipped, 0 failed

RUN_DB_TESTS=1 pytest -q
   → 1157 passed, 35 skipped, 0 failed

python -m compileall backend
   → OK

py_compile backend/app/api/orchestrator_chat.py tests/test_chat_api.py
   → OK
```

---

## 11. 当前限制下不包含的阶段

* ❌ Phase 3.7.12+：未进入。
* ❌ Agent / LangGraph / MCP / Memory / Planning：未实现。
* ❌ 前端 / 流式 / WebSocket：未实现。
* ❌ 权限 / 审计 / 限流：未实现。
* ❌ 修改 Orchestrator / Router / RAG / Tool / T2S / Validator / Executor
  核心逻辑：**零修改**。

---

## 12. Architecture 接入（最终闭环）

```text
HTTP Client
   ↓
POST /api/ai/chat
   ↓
backend/app/api/orchestrator_chat.py（API 适配层）
   ↓
AIOrchestratorService.execute(question)
   ↓
AIRouterService.route
   ↓
┌───────────┼────────────────┐
│ RAG       │ Tool           │ Text-to-SQL
│ (3.5)     │ (3.6.1)        │ ↓
│           │                │ RelevantTableSelector
│           │                │ DatabaseContextComposer
│           │                │ TextToSQLService (FakeLLM)
│           │                │ ↓
│           │                │ SQLValidator (内嵌重试)
│           │                │ SQLExecutor (READ ONLY)
│           │                │ ↓
│           │                │ PostgreSQL
└───────────┴────────────────┘
   ↓
AIOrchestrationResult
   ↓
ChatResponse (route / content / data / metadata)
   ↓
HTTP 200
```