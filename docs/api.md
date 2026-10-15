# WMS AI Assistant — API 设计

> 当前文档对应 MVP Phase 3.5.8 实现（Chat + RAG + Retrieval Evaluation + 真实知识库加载）。
> Phase 3.5.7 起增加 RAG 检索质量评估（见 § 5）。
> Phase 3.5.8 起增加真实知识库加载 CLI（见 § 6）。
> Phase 3.6.2 起增加 Tool Calling 对话接口 `POST /api/chat/with-tools`（见 § 2.4）。
> Phase 3.6.3 起该接口升级为 Multi-Step Tool Calling（顺序多步 + TOOL_MAX_ROUNDS 预算）。
> 后续阶段按 `docs/requirements.md` 演进。

---

## 1. 文档信息

| 项目     | 内容                                              |
| -------- | ------------------------------------------------- |
| 项目名称 | WMS AI Assistant                                  |
| 当前阶段 | MVP — Phase 3.5.8（Chat + RAG + Retrieval Evaluation + 真实知识库加载） |
| Base URL | `/api`                                            |
| 数据格式 | JSON                                              |
| 鉴权     | 当前无；Phase 6+ 引入 JWT/RBAC                    |
| LLM 协议 | OpenAI Chat Completions（兼容 OpenAI / DeepSeek / Qwen / Ollama / 自部署） |
| RAG 存储 | PostgreSQL + pgvector                            |

---

## 2. 当前已实现的接口

### 2.1 GET /api/health

服务健康检查，**不依赖** LLM、数据库或外部系统。

#### 请求

无请求体，无 Query 参数。

#### 响应 200

```json
{
  "status": "ok",
  "service": "wms-ai-assistant"
}
```

| 字段     | 类型   | 说明               |
| -------- | ------ | ------------------ |
| status   | string | 固定值 `"ok"`       |
| service  | string | 机器标识符         |

---

### 2.2 POST /api/chat

> **认证（Phase 7 Recovery M2.3 A1）**：要求 M1 用户会话（HttpOnly Cookie）+
> CSRF；**不再允许匿名执行**——完整契约见 **§13**。

对话接口。Phase 3.5.6 起基于 RAG 知识库回答：

```text
ChatService → RagService → Vector Search → Context Builder → LLMClient
```

知识库无命中时返回固定提示语（`sources=[]`），**不**回退闲聊 LLM。

#### 请求

```json
{
  "message": "采购入库怎么操作？"
}
```

| 字段     | 类型   | 必填 | 说明                              |
| -------- | ------ | ---- | --------------------------------- |
| message  | string | ✅   | 用户自然语言消息（不可为空字符串） |

#### 响应 200

```json
{
  "answer": "采购入库主要包括收货、核对、质检和上架……",
  "sources": [
    {
      "chunk_id": 3,
      "document_id": 1,
      "chunk_index": 0,
      "similarity": 0.91,
      "metadata": {
        "heading_path": ["采购入库", "操作步骤"]
      }
    }
  ],
  "used_chunks_count": 1
}
```

| 字段              | 类型   | 说明                                                       |
| ----------------- | ------ | ---------------------------------------------------------- |
| answer            | string | AI 回答（经 RAG 链路生成；空知识库时为固定提示语）         |
| sources           | array  | RAG 命中来源（**仅元数据**，不含 chunk content；可扩展）   |
| used_chunks_count | int    | 实际纳入 Context 的片段数                                  |

> `sources` / `used_chunks_count` 为 Phase 3.5.6 向后兼容新增字段；
> 旧客户端可忽略。`message` / `answer` 协议与 Phase 2 保持一致。

---

### 2.3 POST /api/rag/answer

> **认证（Phase 7 Recovery M2.3 A1）**：要求 M1 用户会话 + CSRF；
> **不再允许匿名执行**（该端点会返回知识片段正文 `sources[].content`）——
> 完整契约见 **§13**。

独立 RAG 问答接口（Phase 3.5.5），与 `/api/chat` 共用 RagService，
可显式指定召回片段数。

#### 请求

```json
{
  "query": "采购入库怎么操作？",
  "top_k": 5
}
```

| 字段   | 类型    | 必填 | 说明                                            |
| ------ | ------- | ---- | ----------------------------------------------- |
| query  | string  | ✅   | 用户问题（不可为空或纯空白）                    |
| top_k  | integer | ❌   | 召回片段数，范围 [1, 50]；省略用服务端默认值    |

#### 响应 200

```json
{
  "answer": "采购入库主要包括……",
  "sources": [
    {
      "chunk_id": 3,
      "document_id": 1,
      "chunk_index": 0,
      "content": "采购入库操作步骤……",
      "similarity": 0.91,
      "metadata": {"heading_path": ["采购入库", "操作步骤"]}
    }
  ],
  "used_chunks_count": 1
}
```

| 字段              | 类型   | 说明                                             |
| ----------------- | ------ | ------------------------------------------------ |
| answer            | string | LLM 生成的回答；空检索时为固定提示语             |
| sources           | array  | 命中来源（RAG 专用，含 chunk content）           |
| used_chunks_count | int    | 实际纳入 Context 的片段数                         |

> `/api/rag/answer` 面向调试 / 前端需要完整片段的场景，返回 `content`；
> `/api/chat` 面向对话，sources 仅返回元数据。

---

### 2.4 POST /api/chat/with-tools

> **认证（Phase 7 Recovery M2.3 A1）**：要求 M1 用户会话 + CSRF；
> **不再允许匿名执行**（该端点会触发**真实 Tool 执行**）——完整契约见 **§13**。

对话接口（Phase 3.6.2 引入；Phase 3.6.3 升级为 **Multi-Step Tool Calling**；
Phase 3.11 Step 9 起 Tool 执行统一走 ToolExecutionService；
Phase 3.11 Step 10 起支持可选 `project_id` 项目能力限制；
Phase 3.11 Step 11 起注册**真实只读** `get_inventory`）。
**不经过 RAG / Text-to-SQL**，是独立的 Tool Calling 链路；`/api/chat` 行为不变。

```text
ToolChatService → LLM（携带 tools）
                → 判断是否需要 Tool
                → 需要：ToolExecutionService（capability + project scope）
                       → ToolRegistry.execute() → 真实 get_inventory Handler
                       → PostgreSQL（SELECT …；READ ONLY + 绑定参数 + timeout）
                       → ToolResult → role=tool 消息
                       （append 到完整消息历史）→ LLM（可继续请求 Tool）
                → ... 顺序循环 ...
                → LLM 不再请求 Tool → 最终回答
```

当前阶段约束（Phase 3.6.3 / 3.11）：

- **顺序多步**：每轮最多 **1 个** Tool Call（LLM 单次返回多个 → 502 拒绝，不做并行）；
- **总轮数硬上限** `TOOL_MAX_ROUNDS`（默认 5，钳制 [1, 20]）：
  最坏情况 = 5 次 Tool 执行 + 6 次 LLM 调用；预算耗尽后 LLM 仍请求 Tool → 502；
- Tool 参数错误 / 未注册 Tool **不打断请求**：错误以 tool message 回传 LLM，
  由 LLM 生成自然语言错误说明，并允许继续下一轮；
- **能力拒绝（403）不降级**：`project_id` 指定的项目未启用该 Tool 时，
  执行边界直接拒绝（Handler 0 次调用），**不**转成普通 Tool 失败；
- 注册的 Tool = **真实只读** `get_inventory`（Phase 3.7.12 实现；参数仅
  `material_code`（必填）/ `warehouse_code`（可选，当前库存表无该维度 →
  显式拒绝，不静默全仓汇总）；只读事务 + 绑定参数 + statement_timeout；
  不接受任意 SQL / table / where_clause）；
- 真实 `get_work_order` 接入 = **DEFERRED**（本端点当前不注册该 Tool）；
- Mock Tools（Phase 3.6.1）仅供单元 / characterization 测试使用，
  **不**出现在生产 Registry（避免同名 Tool 双定义）。

#### 请求

```json
{
  "message": "查询 MAT-001 当前库存",
  "project_id": "vietnam-wms"
}
```

> 未提供 `project_id` 时 `capabilities=None`（不限制 Tool 能力，不查注册表）；
> 提供时该项目必须在 `ProjectCapabilities.tool_names` 中允许 `get_inventory`
> （默认项目能力即包含；见 `backend/app/projects/capabilities.py`）。

| 字段       | 类型   | 必填 | 说明                                                         |
| ---------- | ------ | ---- | ------------------------------------------------------------ |
| message    | string | ✅   | 用户自然语言消息（非空）                                     |
| project_id | string | ❌   | 可选：项目上下文 ID（≤128 字符）。提供时由**服务器端** ProjectRegistry 解析该项目 Tool 能力白名单；省略时不限制 Tool 能力（旧行为，不查注册表） |

#### 响应 200

```json
{
  "answer": "MAT001 当前库存为 1000 PCS；工单 MO001 状态为 RELEASED。",
  "tool_calls": [
    {
      "tool_name": "get_inventory"
    },
    {
      "tool_name": "get_work_order"
    }
  ]
}
```

| 字段       | 类型  | 说明                                                        |
| ---------- | ----- | ----------------------------------------------------------- |
| answer     | string | AI 最终回答（无需 Tool 时直接来自 LLM #1）                 |
| tool_calls | array  | 本次请求**实际执行过**的 Tool 调用（**按执行顺序**；仅 `tool_name`，数量上限 = `TOOL_MAX_ROUNDS`；无需 Tool 时为 `[]`） |

#### 错误映射（在 §3.2 基础上新增）

| HTTP | 触发条件                                       | detail 示例                                        |
| ---- | ---------------------------------------------- | -------------------------------------------------- |
| 403  | 项目能力未启用（AIOrchestratorCapabilityError；仅当提供 project_id） | `"项目能力未启用: Tool 'get_work_order' 未在该项目启用"` |
| 404  | project_id 未注册（ProjectNotFoundError；仅当提供 project_id） | `"项目未注册: 项目 'project-x' 未注册（project_id 只能选择服务器端已注册的项目）"` |
| 502  | LLM 返回多个 tool call（MultipleToolCallsError） | `"LLM 返回多个 Tool Call（当前不支持）"`           |
| 502  | Tool Calling 预算耗尽（ToolCallingBudgetExceededError） | `"Tool Calling 预算耗尽（max_rounds=5）…"` |
| 500  | ToolChatError（编排内部错误）                   | `"Tool Chat 服务内部错误: ..."`                     |

> `project_id` 与 `/api/ai/chat`（§2.9）语义一致：只能选择**服务器端已注册**的项目，
> 能力白名单由服务器端 ProjectRegistry 决定（HTTP 无法注入 / 覆盖能力字段）。
> 未提供 `project_id` 时保持旧行为（不限制 Tool 能力）。

LLM 家族异常（LLMConfigError 503 / LLMRequestError、LLMResponseError 502）
沿用 §3.2 映射。

> 配置：`TOOL_MAX_ROUNDS`（环境变量，默认 5，钳制 [1, 20]），
> 读取自 `settings.tool.max_rounds`。

### 2.5 GET /api/observability/tools（Phase 3.11 Step 25）

Tool 执行快照**只读**列表（Read Model → JSON）。

```text
Method:       GET
Auth:         当前项目现有 API 机制（本阶段未新增认证机制）
Side Effect:  None（不触发 Tool 执行 / 不写 Collector）
Persistence:  None（纯内存；不落库）
```

数据来源：

```text
Application-lifetime InMemory Collector（唯一创建点：
backend/app/api/orchestrator_chat.py）
    ↓ ToolObservabilityQueryService（只读查询边界）
    ↓ ToolExecutionSnapshot（11 字段 Read Model）
    ↓ snapshot_to_dict()（Serialization Boundary）
    ↓ JSON
```

说明：

```text
Retention：     当前 Collector max_records（FIFO；默认 1000）
Persistence：   None
Multi-process： 每个 process 独立内存数据（不共享、不聚合）
Query DSL：     第一版无 query parameter（无 project_id / tool_name /
                request_id / limit / offset / sort）
排序 / 过滤 / 分页 / 聚合：本端点不做（沿用 QueryService 语义）
```

Response 200：

```json
{
  "records": [
    {
      "request_id": "0f9c...",
      "round": 1,
      "tool_name": "get_inventory",
      "started_at": "2026-09-26T10:20:30.123456+00:00",
      "finished_at": "2026-09-26T10:20:30.125000+00:00",
      "duration_ms": 1.574,
      "success": true,
      "project_id": "project-a",
      "tool_call_id": null,
      "error_code": null,
      "error_type": null
    }
  ]
}
```

空数据：`{"records": []}`。

安全：响应**只**含 Snapshot 的 11 个字段（无 Tool arguments / ToolResult.data /
SQL / prompt / 凭据 / 连接信息 / traceback / Collector 内部状态）。

### 2.6 GET /api/observability/tools/metrics（Phase 3.11 Step 25）

Tool 执行聚合统计（无维度；只读）。

```text
Method:       GET
Auth:         当前项目现有 API 机制（本阶段未新增认证机制）
Side Effect:  None
Persistence:  None
```

Response 200：

```json
{
  "total_count": 10,
  "success_count": 8,
  "failure_count": 2,
  "success_rate": 0.8,
  "failure_rate": 0.2,
  "total_duration_ms": 123.45,
  "average_duration_ms": 12.345,
  "max_duration_ms": 40.0
}
```

空数据（`null` **不是** `0` —— 与 Metrics Read Model 语义一致）：

```json
{
  "total_count": 0,
  "success_count": 0,
  "failure_count": 0,
  "success_rate": null,
  "failure_rate": null,
  "total_duration_ms": 0.0,
  "average_duration_ms": null,
  "max_duration_ms": null
}
```

错误响应：

| HTTP | 触发条件                     | detail                  |
| ---- | ---------------------------- | ----------------------- |
| 500  | 观测数据不可用（未预期异常） | `"Tool 观测数据不可用"` |

> 不暴露 traceback / 内部模块路径 / SQL / 数据库信息（§十三）。

安全：Metrics 不含 request_id / project_id / tool_name 等 identifier；
不含维度聚合（by_tool / by_project / by_request 均未实现）。

### 2.8 GET /api/observability/tools/metrics/persistent（Phase 3.11 Step 33）

Tool 执行**持久指标**（数据库 `ai_ops.tool_execution_record` 聚合；只读）。

```text
Method:       GET（无写方法）
Auth:         当前项目现有 API 机制（未新增认证）
Side Effect:  None（不写库 / 不触发 Tool 执行 / 不修改内存 Collector）
Persistence:  None（只读）
Query:        project_id · tool_name · success（精确过滤；均可省略）
              **无** limit / offset（聚合结果是单行，不是列表）
```

数据来源：

```text
ToolExecutionPersistentQueryService.metrics(...)
    ↓ Repository.get_metrics()（count(*) / count(*) FILTER / sum / avg / max）
    ↓ PostgreSQL
```

Response 200（有数据）：

```json
{
  "total_count": 5,
  "success_count": 3,
  "failure_count": 2,
  "success_rate": 0.6,
  "failure_rate": 0.4,
  "total_duration_ms": 1500.0,
  "average_duration_ms": 300.0,
  "max_duration_ms": 500.0
}
```

Response 200（空数据集；`null` **不是** `0`）：

```json
{
  "total_count": 0,
  "success_count": 0,
  "failure_count": 0,
  "success_rate": null,
  "failure_rate": null,
  "total_duration_ms": 0.0,
  "average_duration_ms": null,
  "max_duration_ms": null
}
```

过滤示例（精确匹配；省略 = 不过滤；无匹配 → 计数 0）：

```text
GET /api/observability/tools/metrics/persistent?project_id=project-a
GET /api/observability/tools/metrics/persistent?tool_name=get_inventory
GET /api/observability/tools/metrics/persistent?success=false   （false ≠ 省略）
GET /api/observability/tools/metrics/persistent
    ?project_id=project-a&tool_name=get_inventory&success=true
```

错误响应：

| HTTP | 触发条件                   | detail                        |
| ---- | -------------------------- | ----------------------------- |
| 422  | 过滤值非法（success=maybe） | FastAPI 默认校验错误          |
| 502  | 数据库未配置 / 查询失败     | `"Tool 持久观测指标不可用"`   |
| 500  | 其它未预期异常             | `"Tool 观测数据不可用"`       |

> 与 Runtime 指标（§2.6）**并列**：DB 失败**不会**回退到内存指标；
> 响应不含 identifier / Tool args / SQL / traceback（仅 8 个指标字段）。

### 2.7 GET /api/observability/tools/history（Phase 3.11 Step 30）

Tool 执行**持久历史**（数据库 `ai_ops.tool_execution_record`）只读列表。

```text
Method:       GET（无 POST / PUT / PATCH / DELETE）
Auth:         当前项目现有 API 机制（本阶段未新增认证机制）
Side Effect:  None（不写库 / 不触发 Tool 执行 / 不修改内存 Collector）
Persistence:  None（只读）
Query:        limit（1~1000，默认 100）+ offset（>= 0，默认 0）
              —— 仅 LIMIT + OFFSET 分页；无 cursor / keyset / total_count
              + 精确过滤：project_id · tool_name · success（均可省略）
```

过滤示例（**精确匹配**；条件之间 AND；省略 = 不过滤）：

```text
GET /api/observability/tools/history?project_id=project-a
GET /api/observability/tools/history?tool_name=get_inventory
GET /api/observability/tools/history?success=true
GET /api/observability/tools/history?success=false      （false ≠ 省略）
GET /api/observability/tools/history
    ?project_id=project-a&tool_name=get_inventory&success=true
    &limit=20&offset=0

语义：WHERE project_id = :p AND tool_name = :t AND success = :s
      → ORDER BY started_at DESC, id DESC
      → LIMIT :limit OFFSET :offset        （过滤先于分页）
无匹配 → 200 {"items": [], "limit": …, "offset": …}（不是 404）
不支持：LIKE / ILIKE / 正则 / 模糊 / 关键词 / 时间范围（均为 Deferred）
```

分页示例：

```text
GET /api/observability/tools/history?limit=100&offset=0     第 1 页
GET /api/observability/tools/history?limit=100&offset=100   第 2 页
GET /api/observability/tools/history?limit=100&offset=200   第 3 页

所有页共用同一确定性排序（started_at DESC, id DESC）
→ 跨页无重复 / 无遗漏；offset 超出记录数 → 200 + items=[]（不是 404）
```

数据来源：

```text
ToolExecutionPersistentQueryService.list_recent(limit)
    ↓
ToolExecutionRepository（ORDER BY started_at DESC, id DESC LIMIT n）
    ↓
ToolExecutionSnapshot → snapshot_to_dict() → JSON
```

与 Runtime 端点（§2.5）的关系：

```text
Runtime      = 本进程内存 Collector（近期观测；多进程各自独立）
Persistent   = 数据库长期历史（跨进程 / 跨重启）
parallel —— 不 merge · 不 fallback · 不去重
```

Response 200：

```json
{
  "items": [
    {
      "request_id": "step30-test-001",
      "round": 1,
      "tool_name": "get_inventory",
      "started_at": "2026-09-28T12:00:00+00:00",
      "finished_at": "2026-09-28T12:00:00.007500+00:00",
      "duration_ms": 7.5,
      "success": true,
      "project_id": "test-project",
      "tool_call_id": null,
      "error_code": null,
      "error_type": null
    }
  ]
}
```

空库：`{"items": []}`（200）。

错误响应：

| HTTP | 触发条件                          | detail                      |
| ---- | --------------------------------- | --------------------------- |
| 422  | limit 非法（0 / -1 / 1001 / abc） | FastAPI 默认校验错误        |
| 502  | 数据库未配置 / 查询失败           | `"Tool 观测历史数据不可用"` |
| 500  | 其它未预期异常                    | `"Tool 观测数据不可用"`     |

> DB 失败**不会**回退到内存视图（Runtime 与 Persistent 严格并列）；
> 响应只含 Snapshot 的 11 个字段，不含数据库主键 `id` / `created_at`。

---

### 2.9 POST /api/ai/chat（Phase 3.7.11；Trace Contract = Phase 3.12 Step 35）

AI Assistant 统一入口（Orchestrator-backed）：一次请求经 AI Router 进入
RAG / Tool / Text-to-SQL 之一（Phase 3.12 Step 34 审计结论：这是当前项目
唯一的「统一入口 + 统一 Router + 三路由 + 统一 Result Envelope + 统一错误边界」）。

```text
Method:       POST
Request:      { "question": str（必填，非空 / 非纯空白）, "project_id": str | None }
Response:     { "route": "rag" | "tool" | "text_to_sql",
               "content": str | None,
               "data": object | None,
               "metadata": object }
Auth:         M1 用户会话（HttpOnly Cookie）+ CSRF —— Phase 7 Recovery M2.3 A1
              （**不再允许匿名执行**；完整契约见 §13）
Side Effect:  按 route：RAG 检索 + LLM / Tool 只读执行 / Text-to-SQL 只读查询
```

`data` 按 route 分支：

| route         | data 结构                                                        |
| ------------- | ---------------------------------------------------------------- |
| `rag`         | `{ "sources": [ {chunk_id, document_id, chunk_index, similarity, metadata} ], "used_chunks_count": int }` |
| `tool`        | `{ "tool_name": str, "success": bool, "data": object\|null, "error": str\|null }` |
| `text_to_sql` | `{ "columns": [...], "rows": [...], "row_count": int, "truncated": bool, "execution_time_ms": float, "referenced_tables": [...], "project_id": str, "sql": str\|null }`；只读拒绝时 `data = null`（`metadata.refused = true`） |

#### Assistant Trace ID（Phase 3.12 Step 35）

成功响应的 `metadata.request_id` 是本次请求的 **Assistant Trace ID**：

```json
{
  "route": "tool",
  "content": "material_code=MAT-001 qty=120",
  "data": { "tool_name": "get_inventory", "success": true, "data": {}, "error": null },
  "metadata": {
    "decision_source": "tool_match",
    "route_reason": "命中 get_inventory 别名",
    "tool_name": "get_inventory",
    "tool_success": true,
    "request_id": "3f0b2c1e-...-9a7d"
  }
}
```

```text
来源        Orchestrator 一次 execute() 生成一个（API 层不生成、不覆盖）
格式        项目既有 new_request_id()（uuid4 字符串，36 字符；未重新设计 ID）
范围        rag / tool / text_to_sql 三条成功路径 + T2S refusal 均携带
可关联       Tool 路径下 == ai_ops.tool_execution_record.request_id
             → 可用它查询 GET /api/observability/tools/history（响应项含 request_id）
             LLM usage（Phase 3.12 Step 36）：
             == ai_ops.llm_usage_record.assistant_request_id
             （该表另有 request_id = Provider 请求 ID，两个维度不同，勿混用）
错误响应     4xx / 5xx **不含** request_id（错误契约本阶段未变）
不包含       Tool arguments / ToolResult.data / SQL / prompt / LLM 响应 / 凭据 /
             traceback / DB 连接信息（metadata 只增加 request_id 一个键）
```

请求体**不**接受 `request_id`（客户端传入会被忽略；服务端不信任客户端 trace id）。

错误映射（与 Phase 3.7.11 起一致，本阶段未变）：

| HTTP | 触发条件                                   | detail 示例                       |
| ---- | ------------------------------------------ | --------------------------------- |
| 400  | `AIOrchestratorInputError`（question 非法） | `"非法输入: ..."`                 |
| 403  | 项目能力未启用（capability denied）         | `"项目能力未启用: ..."`           |
| 404  | `project_id` 未注册                         | `"项目未注册: ..."`               |
| 422  | 请求体校验失败（Pydantic）                  | 标准校验错误                      |
| 502  | `AIOrchestratorRouteError`（路由决策失败）  | `"路由决策失败: ..."`             |
| 503  | `AIOrchestratorUnavailableError`（上下文不可用） | `"项目上下文不可用: ..."`     |
| 500  | `AIOrchestratorExecutionError` / 未预期异常 | `"AI 服务内部错误"`               |

### 2.10 GET /api/observability/assistant-trace/{assistant_request_id}（Phase 3.12 Step 39）

Assistant Trace 只读 Read Model（组合 LLM Usage 与 Tool 执行；**只读**）。

```text
Method:       GET
Path 参数:    assistant_request_id（1..128 字符；= POST /api/ai/chat 成功响应
             metadata.request_id）
Auth:         当前项目现有 API 机制（未新增认证）
Side Effect:  None（不写库 / 不触发 Tool / 不调用 LLM / 不重新路由）
Persistence:  None（无 Trace 表 / 无 Repository）
```

数据来源（**两者均为持久化，且刻意不与 Runtime 合并**）：

```text
llm_usage[]       ← LLMUsageQueryService
                    → PostgreSQL（ai_ops.llm_usage_record）
tool_executions[] ← Tool 观测 Persistent 读边界（Step 41 起）
                    → PostgreSQL（ai_ops.tool_execution_record）
                    · 进程重启 / 多 worker / Runtime Collector 淘汰后仍可查询
                    · Runtime（/api/observability/tools）与
                      History（/api/observability/tools/history）语义不变
```

Response 200（示例）：

```json
{
  "assistant_request_id": "3f0b2c1e-…-9a7d",
  "llm_usage": [
    {
      "id": 12,
      "assistant_request_id": "3f0b2c1e-…-9a7d",
      "request_id": "chatcmpl-…",
      "provider": "deepseek",
      "model": "deepseek-chat",
      "prompt_tokens": 120,
      "completion_tokens": 45,
      "total_tokens": 165,
      "created_at": "2026-09-28T18:00:00.123456Z"
    }
  ],
  "tool_executions": [
    {
      "request_id": "3f0b2c1e-…-9a7d",
      "round": 1,
      "tool_name": "get_inventory",
      "started_at": "2026-09-28T18:00:01Z",
      "finished_at": "2026-09-28T18:00:01.005000Z",
      "duration_ms": 5.0,
      "success": true,
      "project_id": "project-a",
      "tool_call_id": null,
      "error_code": null,
      "error_type": null
    }
  ]
}
```

空 Trace（不存在的 Trace ID；**不是** 404）：

```json
{ "assistant_request_id": "not-exist", "llm_usage": [], "tool_executions": [] }
```

错误响应：

| HTTP | 触发条件                                   | detail                        |
| ---- | ------------------------------------------ | ----------------------------- |
| 400  | assistant_request_id 非法（纯空白等）       | `"非法输入: …"`               |
| 422  | 路径参数长度越界（>128）                    | FastAPI 标准校验错误          |
| 502  | LLM Usage 数据源不可用（数据库未配置/失败） | `"助手链路观测数据不可用"`    |
| 500  | 其它未预期错误                             | `"助手链路观测数据不可用"`    |

说明：

```text
顺序      llm_usage：created_at ASC, id ASC（Step 37）
          tool_executions：Collector 写入顺序（Step 38）
          本端点不重排 / 不过滤 / 不聚合 / 不去重 / 无分页
安全      仅返回上述字段；不含 prompt / messages / Tool arguments /
          ToolResult.data / SQL / DB 连接 / 凭据 / traceback
```

---

## 3. 错误响应

### 3.1 统一错误格式

```json
{
  "detail": "可理解的错误描述"
}
```

FastAPI 默认 `HTTPException` 走 `{"detail": ...}` 形式。
后续阶段会扩展为 `{code, message, details}` 结构。

### 3.2 HTTP 状态码映射（Phase 3.5.6）

`/api/chat` 与 `/api/rag/answer` 共享同一异常映射
（`backend/app/api/_rag_error_mapping.py`）：

| HTTP | 触发条件                                                         | detail 示例                          |
| ---- | ---------------------------------------------------------------- | ------------------------------------ |
| 200  | 正常（含空知识库）                                                | —                                    |
| 400  | Pydantic 外的空消息（服务层校验）/ VectorSearchInputError        | `"message 不能为空"`                |
| 422  | Pydantic 校验失败：空 / 缺字段 / 类型错误 / top_k 越界           | FastAPI 默认错误列表                  |
| 502  | 上游错误：Embedding / pgvector / LLM（连接失败、超时、非 2xx）    | `"LLM 请求失败: 无法连接 ..."`       |
| 503  | 配置错误（`LLM_API_KEY` / `EMBEDDING_API_KEY` 等缺失）            | `"LLM 配置错误: LLM_API_KEY 未配置"` |
| 500  | 兜底：未预期的 RAG / Embedding / LLM 异常                        | `"RAG 服务内部错误: ..."`            |

> **不暴露**：响应中禁止出现 Authorization header、API Key、Python traceback、SQL 语句、embedding 向量、数据库连接串。

### 3.3 异常分层

```
LLMError（基类）                        llm/client.py
    ├── LLMConfigError                  配置缺失
    ├── LLMRequestError                 网络 / 超时 / 非 2xx
    └── LLMResponseError               响应解析失败 / 结构异常

EmbeddingError（基类）                   embedding/exceptions.py
    ├── EmbeddingConfigurationError      配置缺失 / 非法
    ├── EmbeddingInputError              输入非法
    ├── EmbeddingAPIError                网络 / 超时 / HTTP 非 2xx
    ├── EmbeddingResponseError           响应解析失败
    └── EmbeddingDimensionError          维度不一致

VectorSearchError（基类）                services/vector_search_service.py
    ├── VectorSearchInputError           query 非法
    └── VectorSearchParameterError       top_k 越界

RagError                                services/rag_service.py（RAG 自身错误）
```

---

## 4. 后续计划中的接口（**当前未实现**）

按 `docs/requirements.md` Phase 4+ 演进：

| 方法 | 路径                          | 阶段    | 说明                       |
| ---- | ----------------------------- | ------- | -------------------------- |
| GET  | `/api/conversations`          | Phase 2.5 | 会话列表（**未实现**）   |
| POST | `/api/knowledge/documents`    | Phase 3.6+ | 知识库文档入库（API 化） |
| POST | `/api/knowledge/search`       | Phase 3.6+ | 知识库检索（内部接口）   |
| GET  | `/api/tools`                  | Phase 4 | Tool 注册清单              |
| POST | `/api/tools/{tool_name}`      | Phase 4 | Tool 调用                  |
| POST | `/api/auth/login`             | Phase 6 | 用户登录                   |
| GET  | `/api/audit/logs`             | Phase 6 | 审计日志                   |

> Conversation 端点（创建 / 详情 / 消息读 / 消息写 / 归档）**已实现**
> （Phase 4.1 Step 8、Phase 4.2 Step 12；认证与所有权 = Phase 7 Recovery M2.2）
> ——完整契约见 **§12**。**会话列表 / 分页仍未实现**。

---

## 5. RAG 检索质量评估（Phase 3.5.7）

> 本节为 **测试评估层** 描述，不对外暴露 HTTP 接口。

### 5.1 目的

建立可重复运行的 RAG 检索质量 Baseline，作为后续优化（Reranker / Hybrid
Search / Query Rewrite）的对比参照。

### 5.2 范围

```text
test query
  ↓
RagEvaluationService
  ↓
VectorSearchService.search(query, top_k)
  ↓
Top-K Results
  ↓
Keyword / document_id 匹配
  ↓
RetrievalEvaluationSummary
```

**不含**

- 不重新实现 embedding / pgvector / 距离计算（复用 `VectorSearchService`）
- 不调用 LLM（评估的是检索链路，与生成链路解耦）

### 5.3 评估数据集：`tests/fixtures/rag/evaluation_cases.json`

```jsonc
{
  "_schema_version": "1.0",
  "cases": [
    {
      "id": "case_001",
      "query": "采购入库的操作步骤是什么？",
      "expected_keywords": ["采购入库"],
      "expected_document_id": null,   // 可选；不假设 DB 当前 ID
      "expected_source": null,        // 可选；metadata.source 包含
      "expected_title": null           // 可选；metadata.title 包含
    },
    ...
  ]
}
```

不假设具体 document_id，避免依赖 DB 当前状态。

### 5.4 评估 Service：`backend/app/services/rag_evaluation_service.py`

```python
service = RagEvaluationService(vector_search_service=vector_search_service)

summary = await service.evaluate(cases, top_k=5)
# → RetrievalEvaluationSummary(
#     total_cases, matched_cases, failed_cases,
#     hit_rate = matched_cases / total_cases,
#     top_k, results=(RetrievalEvaluationResult, ...))
```

匹配规则：

| 字段                   | 命中条件                                                       |
| ---------------------- | -------------------------------------------------------------- |
| `expected_keywords`   | 全部关键字（任一 Top-K 命中）才匹配；支持中文/`metadata.source` |
| `expected_document_id` | 任一 Top-K result 的 `document_id == expected_document_id`     |

异常策略：

- 单 case 异常 → 该 `RetrievalEvaluationResult.error` 填入，
  `matched=False`，计入 `failed_cases`；**不阻断**其余 case。

DTO（frozen dataclass）：

| 类型                          | 字段                                                            |
| ----------------------------- | --------------------------------------------------------------- |
| `RetrievalEvaluationCase`     | case_id, query, expected_keywords, expected_document_id, ...    |
| `RetrievalEvaluationResult`   | case_id, query, top_k, matched, results_count, error, ...       |
| `RetrievalEvaluationSummary`  | total_cases, matched_cases, failed_cases, hit_rate, top_k, ... |

### 5.5 Baseline 指标

第一版只计算：

```
hit_rate = matched_cases / total_cases
```

后续如需 MRR / NDCG / Recall@K，独立 Phase。

### 5.6 运行测试

```bash
# 单元（默认）
pytest tests/test_rag_evaluation_service.py -q

# 真实链路（默认 SKIP；不消耗 LLM 额度，只消耗 Embedding）
RUN_REAL_RAG_EVAL=1 pytest tests/test_rag_evaluation_real.py -q -s
```

### 5.7 Baseline 快照（Phase 3.5.8）

导入第一份真实 WMS 知识文档 `docs/knowledge/wms-basic-operations.md`
（10 个 Chunk / BGE-M3 1024 维）后实测：

```
top_k         : 5
total_cases   : 12
matched_cases : 10
failed_cases  : 0
hit_rate      : 83.33%
```

未命中 case（真实反映 KB 覆盖范围，未人工修改 query / keywords）：

- `case_003` "单据归档在哪里处理？" → KB 当前未覆盖「单据归档」
- `case_011` "采购单如何创建？" → KB 覆盖「采购入库」但未涵盖「采购单」

> 不修改 `evaluation_cases.json` / Vector Search / Top-K 以「提升分数」。

---

## 6. 真实知识库加载（Phase 3.5.8）

### 6.1 CLI 导入入口

复用现有 `KnowledgeIngestionService`，**不**直接操作 ORM / Parser /
Chunker / EmbeddingClient：

```bash
python -m backend.app.cli.ingest_knowledge docs/knowledge/wms-basic-operations.md
```

可选 `--json` 输出单行 JSON。

### 6.2 行为契约

| 行为                        | 实现                                                                  |
| --------------------------- | --------------------------------------------------------------------- |
| Markdown 解析               | `MarkdownParser`（Phase 3.3）                                         |
| 标题感知切分                | `MarkdownAwareChunker`（Phase 3.4）                                  |
| Embedding                   | `EmbeddingClient`（BGE-M3 1024 维）                                  |
| 维度校验                    | 双保险（Client 内部 + Service 写库前）                                |
| 重复检测                    | `KnowledgeDocument.content_hash`（UNIQUE INDEX）                       |
| 重复导入                    | `IngestionResult.status == "already_exists"`，不再调 Embedding / DB   |
| 异常                       | `DocumentNotFoundError` / `UnsupportedDocumentTypeError` / `DocumentParseError` / `EmptyDocumentError` / `KnowledgeIngestionDatabaseError` |

### 6.3 不在本阶段实现

- ❌ `POST /api/knowledge/ingest` HTTP 接口（Phase 3.6+）
- ❌ 目录递归批量导入 / 任务队列 / 异步 worker
- ❌ 文档更新 / 删除 / 版本化
- ❌ 文件存储位置（OSS / S3）

### 6.4 CLI 退出码

| 退出码 | 含义                                                       |
| ------ | ---------------------------------------------------------- |
| 0      | 成功（含 `ready` / `already_exists`）                       |
| 1      | 失败（参数错误 / 文件不存在 / Service 异常）                |

---

## 7. 设计原则

- **RESTful**：资源导向，HTTP 语义清晰。
- **OpenAPI 自动生成**：所有接口必须能被 FastAPI 自动文档化。
- **响应统一**：错误响应遵循一致结构。
- **可演进**：接口路径与字段命名考虑后续扩展，避免过早锁定。
- **向后兼容**：新增响应字段（如 `sources`）不破坏既有客户端；不擅自变更既有字段语义。
- **不暴露内部细节**：错误信息对用户友好，敏感信息（API Key、SQL、堆栈、Authorization header、embedding 向量）禁止出现在响应中。
- **单一 LLM 调用**：一次对话请求只允许一次 Embedding + 一次 LLM 调用；Chat 与 RAG 共享同一条链路。

---

## 8. Agent / Workflow Read API（Phase 5 P-8 / Phase 6 Step 7-8A）

四个 Workflow 端点 + 两个 Agent 端点均为**只读**（Query only；不触发
Resume / Approve / Cancel / start / advance），并统一要求 **`X-API-Key`** 请求头：

| 端点 | 认证 | 授权作用域来源 |
| --- | --- | --- |
| `GET /api/workflow-runs/{run_id}` | `X-API-Key`（必需） | 服务端 `WORKFLOW_READ_API_PROJECT_ID` |
| `GET /api/workflow-runs/{run_id}/nodes` | 同上 | 同上 |
| `GET /api/workflow-runs/{run_id}/timeline` | 同上 | 同上 |
| `GET /api/workflow-approvals/{approval_id}` | 同上 | 同上（经父 Run 归属校验） |
| `GET /api/agent/runs/{run_id}` | `X-API-Key`（必需） | 服务端 `AGENT_READ_API_PROJECT_ID` |
| `GET /api/agent/runs/{run_id}/timeline` | 同上 | 同上 |

认证与授权契约（两域一致）：

```text
401  缺少 / 错误 X-API-Key（固定文案；不泄露目标资源是否存在）
503  服务端未配置凭据或授权项目（fail closed；绝不匿名放行、不回退默认项目）
404  资源不存在 **或** 不属于授权项目（**故意同码**——不泄露存在性）
400 / 422 / 502 与各端点既有契约一致（参数校验 / 长度越界 / 持久化不可用）
```

- **授权作用域仅来自服务端配置**：客户端 `project_id` 查询参数被忽略
  （Agent / Workflow Read API 均**不接受**该参数）；`X-Project-ID` 等普通
  身份头不被信任；不使用任何代理身份头。
- API Key **不写入**日志 / 响应体；比较使用恒定时间实现。
- 配置方式见 `.env.example`（`WORKFLOW_READ_API_KEY` /
  `WORKFLOW_READ_API_PROJECT_ID` / `AGENT_READ_API_KEY` /
  `AGENT_READ_API_PROJECT_ID`；均无默认值）与 `docs/security.md` §7。
- **部署边界（如实）**：HTTPS = NOT VERIFIED；网络边界 = NOT VERIFIED ⇒
  `PRODUCTION_DEPLOYMENT = BLOCKED`（应用层认证通过不代表可部署）。

---

## 9. 统一 Runtime Read API（Phase 7 Step 2）

在既有 Agent / Workflow 只读面之上提供**统一查询入口**：调用方经一个路径
查询两类运行记录，不必分别理解两套内部数据结构。本入口为**纯只读**——
不新增任何执行能力（不触发 Resume / Approve / Cancel / start / advance），
不复写两域 ReadService / Repository；详情数据 = 域端点同一映射产物**原样
内嵌**（零重映射、零字段丢失）。

### 9.1 端点

| 端点 | 认证 | 授权作用域来源 |
| --- | --- | --- |
| `GET /api/runtime-runs/agent/{run_id}` | `X-API-Key`（Agent 域凭据） | 服务端 `AGENT_READ_API_PROJECT_ID` |
| `GET /api/runtime-runs/workflow/{run_id}` | `X-API-Key`（Workflow 域凭据） | 服务端 `WORKFLOW_READ_API_PROJECT_ID` |

- `runtime_type` ∈ {`agent`, `workflow`}（小写枚举）；非法值 ⇒ 400；
  纯空白 ⇒ 400（白名单拦截）；路径长度越界 ⇒ 422。
- **按域认证**：`agent` 请求只要求 Agent 域凭据配置、只读 `ai_ops.agent_*`
  视图；`workflow` 请求只要求 Workflow 域凭据配置、只读 `ai_ops.workflow_*`
  视图。未涉及的域**不要求**配置（两域凭据独立、可分别轮换——§8 契约不变）。
- 无分页参数（两域详情端点均无分页；统一入口保持一致）。
### 9.2 统一响应 DTO（公共字段 + 类型专属详情）

```text
runtime_type   "agent" | "workflow"（域标识；**不是**状态、不参与状态映射）
run_id         运行身份（域内 run_id；由详情数据原样投影）
project_id     授权项目作用域（服务端解析结果——绝不来自客户端参数）
status         域状态**原值**（两域词汇不同——不强行映射、不归一化）
started_at     开始时刻（两域共有字段；无 ⇒ null）
detail         类型专属完整详情（Agent：AgentRunResponse 原样；
               Workflow：WorkflowRunDetailResponse 原样）
```

- **不伪造缺失字段**：Agent 域无 `created_at` / `updated_at` ⇒ 统一 DTO
  **不引入**这两个键（需要时读域端点；不猜、不补）。
- `status` 不跨域归一化：同一字符串在两域含义可能不同（如 `RUNNING`），
  统一入口拒绝任何语义映射。
- 响应只由 `backend.app.dto.runtime_read_api` 的 Pydantic DTO 组成
  （`response_model` + `runtime_type` discriminator 同时充当字段过滤与
  类型路由边界）；不暴露 ORM / Session / Connection / 凭据 / 连接串。

### 9.3 错误契约（沿用域端点语义）

```text
400  runtime_type 非法（固定文案）或 run_id 非法（服务层校验）
401  缺少 / 错误 X-API-Key（**按域文案**；不泄露目标资源是否存在）
404  运行不存在 **或** 不属于授权项目（沿用域文案："Agent Run 不存在" /
     "Workflow Run 不存在"——与域端点同码同文案）
422  路径参数长度越界（FastAPI Path 校验）
502  持久化读边界不可用（按域文案；不回传 DB 异常文本）
503  对应域认证 / 作用域未配置（fail closed）
```

- **跨项目与不存在同码同文案**（不泄露存在性）——与 §8 两域端点一致。
- 运行类型不混淆：Agent `run_id` 在 `workflow` 类型下 ⇒ 404（反之亦然）；
  两域数据互不串读。

### 9.3 列表端点（Phase 7 Step 3）

| 端点 | 认证 | 授权作用域来源 |
| --- | --- | --- |
| `GET /api/runtime-runs/agent?limit=20&offset=0` | `X-API-Key`（Agent 域凭据） | 服务端 `AGENT_READ_API_PROJECT_ID` |
| `GET /api/runtime-runs/workflow?limit=20&offset=0` | `X-API-Key`（Workflow 域凭据） | 服务端 `WORKFLOW_READ_API_PROJECT_ID` |

- **项目隔离在 DB 层**：`WHERE project_id = …` 由 SQL 执行（仓储方法
  `list_by_project` / `count_by_project`）——绝不先查全部再在 Python
  过滤；授权 `project_id` 只来自服务端认证配置，客户端 `project_id`
  查询参数被忽略（不可扩宽范围）。
- **分页**（沿用 Agent Read 既有规范）：`limit` 默认 50、上限 100
  （服务层 server-side 校验；`limit=0` / `101` / 负数 ⇒ 400）；
  `offset >= 0`（负数 ⇒ 400）；响应回显**生效值**。
- **稳定排序**：`started_at DESC, <run_id> DESC`（唯一 ID 作
  tiebreaker ⇒ 分页窗口稳定；Workflow 的 `started_at` 可空 ⇒
  `NULLS LAST`）。
- **响应结构**（`RuntimeRunListResponse`）：`items` / `limit` /
  `offset` / `total`；`total` = 同一过滤条件的真实 COUNT（可靠计数，
  不伪造）。`items` 为 `RuntimeRunSummaryResponse` 概要列表
  （`runtime_type` / `run_id` / `project_id` / `status` / `started_at`）——
  **不含** step / node / approval / input / 明细（详情走 §9.1）。
- 两种 Runtime **分别查询**（一次请求不跨域合并两类记录）。
- 错误契约与 §9.2 详情一致（400 分页非法 / 401 / 502 / 503 按域）。
- 测试证据：`tests/test_runtime_read_api.py`（离线，Step 3 列表 30 例）+
  `tests/test_runtime_read_api_db.py`（DB 真链 + 真实项目隔离，7 例）。

### 9.4 已知限制（如实记录）

- ~~**无统一列表端点**~~（Phase 7 Step 3 已提供）：`GET /api/runtime-runs/agent`
  与 `GET /api/runtime-runs/workflow`（DB 层项目过滤 + 分页 + 可靠 total；
  见 §9.3）。
- 测试证据：`tests/test_runtime_read_api.py`（离线契约，73 例）+
  `tests/test_runtime_read_api_db.py`（DB 真链 + 真实项目隔离，16 例；
  `RUN_DB_TESTS=1`）。

---

## 10. Agent 命令 API（Phase 7 Step 4）

在既有 Agent Runtime（run / resume / cancel / approval）之上提供
**认证 + 项目作用域** HTTP 写边界——与 §8 Workflow 命令 API 同形对称。
**只适配、不重造**：不修改 Agent Runtime / Domain / Persistence 语义；
复用既有 `AgentRuntime` / `AgentCancellationCoordinator` /
`AgentApprovalStore`（经 `build_agent_command_service` composition root
以 `agent_enabled=True` 装配——与 Workflow Runtime 决策一致）。

### 10.1 端点

| 端点 | 认证 | 授权作用域来源 |
| --- | --- | --- |
| `POST /api/agent-runs` | `X-API-Key`（Agent 命令凭据） | 服务端 `AGENT_COMMAND_API_PROJECT_ID` |
| `POST /api/agent-runs/{run_id}/resume` | 同上 | 同上 |
| `POST /api/agent-runs/{run_id}/cancel` | 同上 | 同上 |
| `POST /api/agent-approvals/{approval_id}/decision` | 同上 | 同上（经父 Run 归属校验） |

- **凭据独立**：`AGENT_COMMAND_API_KEY` 与 Agent/Workflow 读取凭据、
  Workflow 命令凭据**各自独立**（读写最小权限分离、可分别轮换；
  部署层可配置为相同值）。未配置 ⇒ 503（fail closed）；缺失 / 错误
  ⇒ 401（固定文案）。
- **项目作用域仅来自服务端配置**：客户端 `project_id` 查询参数被忽略；
  `start` 的作用域经 per-project 装配的 Runtime 兑现（allowed_tools /
  ToolExecutionService 绑定授权项目），run 落库 `project_id` 即授权
  作用域。
- **归属前置校验**：`resume` / `cancel` / `decision` 执行前经
  `AgentRunRepository` 按授权项目过滤；不存在**或**跨项目 ⇒ 同一 404
  （不泄露存在性）。

### 10.2 命令语义（沿用 Runtime 契约，不新增能力）

```text
start    有界循环到终态 / 等待点（COMPLETED / FAILED / LIMIT_REACHED /
         WAITING_APPROVAL 均为 200 业务结果；持久化 best-effort——
         先 Steps 后 Run；WAITING_APPROVAL 不写终态 Run）
resume   TERMINAL ⇒ 历史终态；ACTIVE + pending ⇒ Decision / Tool Result
         Replay（不重跑 handler）；不确定 ⇒ fail-closed
cancel   协作式三态：CANCEL_REQUESTED（信号置位，运行中 Agent 在安全边界
         停止）/ CANCELLED（PAUSED 直接终态化）/ NOT_CANCELLABLE（终态或
         无控制状态 ⇒ 409）；不强杀 Tool、不重试
decision APPROVED / REJECTED——**只改控制状态**（不调用 LLM / Tool /
         Resume；执行由 resume 承担）；幂等（同裁决 200）；相反裁决
         或 Run 已终态 ⇒ 409
```

### 10.3 错误契约（与 §9.1 Workflow 命令 API 同形）

```text
200  命令完成（含业务性结果：COMPLETED / FAILED / LIMIT_REACHED /
     WAITING_APPROVAL / CANCELLED / CANCEL_REQUESTED / 幂等审批）
400  输入违反契约（空白 question / run_id / approval_id）
401  缺少 / 错误 X-API-Key（固定文案；不泄露目标资源是否存在）
404  Run / Approval 不存在 **或** 不属于授权项目（**故意同码**）
409  状态冲突（APPROVAL_CONFLICT / CANCELLATION_CONFLICT /
     NOT_CANCELLABLE——固定文案）
422  请求体校验失败（FastAPI / Pydantic）
502  持久化边界不可用（不回传内部细节）
503  服务端认证 / 作用域未配置，或项目未启用 Agent 能力（fail closed）
500  其它未预期错误（固定文案；不暴露 traceback / SQL / 凭据）
```

### 10.4 响应 DTO 与安全边界

- 响应只由 `backend.app.dto.agent_command_api` 的 Pydantic DTO 组成：
  `run_id` / `outcome` / `status` / `error_code` / `approval_id` /
  `approval_status` / `decision` / `content` / `run`（轻量只读视图）。
- **无** prompt / arguments / claim_token / ORM / Session / Connection /
  异常正文；`status` / `error_code` 为域词汇 / 稳定码**原值**（不映射）。
- 测试证据：`tests/test_agent_command_api.py`（离线契约，39 例）+
  `tests/test_agent_command_api_db.py`（DB 真链——控制状态路径，9 例）+
  `tests/test_runtime_e2e_closure_db.py`（Phase 7 Step 5：Agent/Workflow
  端到端闭环——start ⇒ 落库 ⇒ Read/List 可查、审批一致性、终态 resume
  零重复 LLM 调用、协作式取消、跨项目不可见；Agent LLM 以 FakeLLM 注入，
  Runtime / Repository / 认证全部真实）。
- 部署边界（如实）：应用层认证与项目作用域已实现；HTTPS = NOT VERIFIED ·
  网络边界 = NOT VERIFIED ⇒ `PRODUCTION_DEPLOYMENT = BLOCKED`。

---

## 11. Workflow 定义目录 API（Phase 7 Step 7）

补齐 Workflow 命令 API 的**前置能力**：此前生产注册表为空（任何
`POST /api/workflow-runs` 都是 404）。现在服务器端静态**定义目录**
（`backend.app.workflow.catalog`）经 Composition Root 惰性注册，并提供
只读发现端点——调用方在 start 之前即可获知有哪些 workflow、其版本、
起始节点与 start input 需要的字段。

### 11.1 端点

| 端点 | 认证 | 数据来源 |
| --- | --- | --- |
| `GET /api/workflow-definitions` | `X-API-Key`（**Workflow 读取域**凭据；与 `/api/workflow-runs` 读面同一 fail-closed 语义） | 服务器端注册表（与命令 API **同一实例**） |

- 无参数（无分页 / 无过滤 / **无** `project_id`——定义是服务器端静态目录，
  响应内容与客户端输入无关；未知 query 参数被忽略）。
- 只读面形：仅 `GET`；未知写方法 ⇒ 405；不接触 DB / Runtime / Tool / LLM。

### 11.2 响应 DTO

```text
items[]                      已注册定义（注册顺序；可能为空——空 ≠ 错误）
  name                       definition 名称（= start 的 workflow_id）
  version                    当前注册版本（Resume 按**持久化** version 解析）
  start_node_id              起始节点
  input_fields[]             start input JSON 需要的顶层字段
                             （由定义中的受限引用 input.<path> 推导）
  nodes[]                    节点概要：node_id / node_type（AI/TOOL/APPROVAL/CONDITION）
```

**不暴露**定义内部：AI 模板 / TOOL 参数映射（含 Tool 名）/ 审批展示文案 /
retry 策略 / 边（`next_node_id`）。

### 11.3 生产目录（当前 2 条；全部只读语义，无写操作节点）

```text
inventory_check  v1   TOOL get_inventory → APPROVAL → 终态
    输入契约：{"material_code": "<物料编码>"}
    语义：只读库存查询 + 人工确认（数据源由部署提供；缺失 ⇒ 节点
    fail-closed 失败，不伪造查询成功）

answer_review    v1   AI（问题模板）→ APPROVAL → 终态
    输入契约：{"question": "<问题>"}
    语义：项目作用域内 AI 回答 + 人工确认后关闭流程
```

- 定义版本发布后**冻结**（语义变更 ⇒ 新 version；Resume 按持久化 version
  fail-closed 解析）。
- 未注册名称仍 ⇒ 404（目录不改变 fail-closed 语义）。
- 额外定义由服务器端代码经 `register_workflow_definition(...)` 显式注册
  （重复 name ⇒ 域异常，不隐式替换）。

### 11.4 错误契约

```text
200  定义目录（可能为空列表）
401  缺少或无效的 X-API-Key（固定文案）
405  未知写方法
500  定义目录读取未预期失败（固定文案；不暴露内部细节）
503  服务端认证 / 作用域未配置（fail closed）
```

- 测试证据：`tests/test_workflow_definition_catalog.py`（目录 / 注册表 / 装配连线，
  离线 14 例）+ `tests/test_workflow_definitions_api.py`（HTTP 契约，离线 15 例）
  + `tests/test_workflow_definition_catalog_db.py`（DB 真链 e2e 4 例；
  `RUN_DB_TESTS=1`——生产定义目录 start ⇒ 节点执行 ⇒ 审批门 ⇒ 批准 ⇒
  COMPLETED ⇒ Read/List 可读；外部 LLM / Tool 以离线替身注入）。

---

## 12. Conversation API（Phase 4.1 Step 8 / Phase 4.2 Step 12；认证与所有权 = Phase 7 Recovery M2.2）

> 本节记录**当前实际**契约（含 M2.2 认证 / 会话所有权 / D1 项目作用域加固）。
> 会话与消息持久化在 `ai_ops.conversation` / `ai_ops.conversation_turn`；
> AI 执行**只能**经 `ChatApplicationService`（本 API 不直接调用 Orchestrator /
> Router / RAG / Tool / Text-to-SQL，也不直接访问 SQLAlchemy / DB）。

### 12.1 端点

| 方法 | 路径 | 说明 | 登录态 | CSRF |
| --- | --- | --- | --- | --- |
| POST | `/api/conversations` | 创建会话（`conversation_id` 由服务端生成） | 必需 | 必需 |
| GET | `/api/conversations/{conversation_id}` | 会话 metadata（不含消息） | 必需 | — |
| GET | `/api/conversations/{conversation_id}/messages` | 消息列表（持久化顺序：`created_at ASC, turn_id ASC`） | 必需 | — |
| POST | `/api/conversations/{conversation_id}/messages` | 追加消息（AI 执行 + 持久化 USER / ASSISTANT） | 必需 | 必需 |
| POST | `/api/conversations/{conversation_id}/archive` | 归档会话（幂等） | 必需 | 必需 |

**不提供**：会话列表 / 分页 / `DELETE` / `PATCH` / `PUT` / switch-project /
物理删除；`conversation_id` 不可由客户端指定（额外 JSON 字段被忽略）。

### 12.2 登录态要求（M1 用户会话）

- 身份**只**来自服务端会话（`wms_ai_session` HttpOnly Cookie →
  `ai_ops.user_session`）；登录入口 `POST /api/auth/login`。
- 五个端点未携带有效会话（缺失 / 无效 / 过期 / 已撤销 / 登出后重放）⇒
  **401**（固定文案；不区分具体原因，不泄露账号是否存在）。
- **不**接受 `X-API-Key`、Query 或 Body 中的用户身份；本 API 无身份覆盖入口
  （服务 API-Key 身份与用户会话身份并存、互不替代，未混淆）。
- 认证发生在**任何业务调用与数据库写之前**（失败路径零 AI 调用、零写）。

### 12.3 CSRF 要求（写操作）

- 不安全方法（创建 / 追加 / 归档）要求 M1 双提交头：
  `X-CSRF-Token` = `wms_ai_csrf` Cookie 值，且必须等于服务端对该会话（创建为
  无会话上下文）重算的 HMAC 值。
- 缺头 / 缺 Cookie / 两者不一致 / 伪造值 / 其他会话的值 ⇒ **403**，副作用为零。

### 12.4 项目作用域校验（D1 加固）

- 创建时 `project_id` 必须**精确等于服务端配置项目**
  （`PROJECT_ID` → `ProjectContext.project_id`；见 `.env.example`）——
  服务端配置是允许项目的**唯一授权来源**。
- 其他项目**即使已在 Project Registry 注册**也不被授权（已注册 ≠ 已授权）；
  未注册 / 未授权 / 变形（如尾随空格）一律 **422**（同一固定文案，不回显输入，
  不泄露项目存在性）。
- 配置无法解析（缺失 / 空白 / 取值异常）或配置项目在注册表不可解析 ⇒
  **503**（fail-closed；**绝不**回退默认项目）。
- 落库的 `conversation.project_id` 与 `resource_owner.project_id` 均为
  服务端**已验证**值。
- 读取 / 追加 / 归档**不接受** `project_id` 参数：作用域来自会话自身的
  服务端绑定，不可被请求扩宽。

### 12.5 会话所有权校验（M2.2）

- 创建：归属行（`ai_ops.resource_owner`，`resource_type = CONVERSATION`）与
  Conversation **同一事务**写入（任一步失败整体回滚 ⇒ 不产生无主会话）。
- 读取 / 追加 / 归档：**先**做所有权校验，**再**调用业务层。
- 非所有者 / 不存在 / 无归属的历史会话 / ID 篡改 ⇒ **同构 404**
  （同 status + 同 body：`{"detail": "会话不存在"}`）——不泄露资源是否存在，
  **不**自动认领历史会话。
- 归属校验通过后 Service 层仍未命中（如并发删除）⇒ 同样 404
  （`{"detail": "会话不存在: <conversation_id>"}`；此分支仅在归属已通过时可达，
  因此不泄露他人资源）。
- 归属服务 / DB 故障 ⇒ **503**（fail-closed，绝不放行、绝不降级为成功）。

### 12.6 请求 / 响应要点

- 创建请求体只有 `project_id`；响应
  `{conversation_id, project_id, status, created_at, updated_at}`。
- 消息列表响应 `{conversation_id, messages: [{turn_id, role, content,
  assistant_request_id, created_at}]}`。
- 追加消息请求体只有 `content`（≤ 10000 字符）；幂等键**只**经
  `Idempotency-Key` 请求头传递（Body 同名字段被忽略）。响应 envelope 与
  `/api/ai/chat` 一致（`route` / `content` / `data` / `metadata`），
  **不**返回 `conversation_id` / `turn_id` / `assistant_request_id`。
- 幂等：同键同载荷重放 ⇒ 200 + `metadata.idempotent_replay = true`（不重复
  执行 AI、不新增 Turn）；同键不同载荷 ⇒ 409（`IDEMPOTENCY_KEY_REUSED_WITH_
  DIFFERENT_PAYLOAD`）；同键在途 ⇒ 409（`IDEMPOTENCY_KEY_IN_FLIGHT`）；
  键长 > 128 ⇒ 422（无写入）。
- 响应只由 `backend.app.dto.conversation_api` 的 Pydantic DTO 组成
  （`response_model` 即字段过滤边界）；`data` 只透传值均为 JSON 基本类型的
  Mapping（富对象不透传）。日志只记录固定事件与 `error_type`，
  **不**记录凭据 / Cookie / 消息正文 / SQL。

### 12.7 错误契约

```text
200  成功
401  未认证 / 会话无效（固定文案）
403  CSRF 校验失败（固定文案）
404  不存在或无权访问（归属阶段：同构固定文案，不区分二者；
     归属已通过后的 Service 未命中：`会话不存在: <id>`）
409  归档会话仍追加消息 / 幂等冲突（契约保留 + 幂等语义）
422  请求体或路径校验失败 / 项目未授权或未注册（同一固定文案）
500  会话服务不可用 / 内部错误（固定文案；不暴露 traceback / SQL / 凭据）
503  项目配置 / 项目注册表 / 所有权校验不可用（fail-closed）
```

### 12.8 测试证据

- 认证 / 所有权 / 项目作用域：`tests/test_conversation_ownership.py`
  （离线 31 例 + DB 真链 6 例，`RUN_DB_TESTS=1`）
- HTTP 契约（Step 7/8 冻结）：`tests/test_conversation_api.py`、
  `tests/test_conversation_api_contract.py`、
  `tests/test_conversation_message_api_contract.py`
- 幂等：`tests/test_conversation_message_idempotency.py`（+ DB 版）
- 架构审计：`tests/test_conversation_api_architecture_audit.py`、
  `tests/test_conversation_message_api_architecture_audit.py`
- DB E2E：`tests/test_conversation_api_db_e2e.py`、
  `tests/test_conversation_multiturn_db_e2e.py`、
  `tests/test_conversation_context_e2e_db.py`（均 `RUN_DB_TESTS=1`）

---

## 13. AI 执行入口认证（Phase 7 Recovery M2.3 A1）

以下**四个 AI 执行入口**自 M2.3 A1 起要求 **M1 用户会话 + CSRF**：

| 方法 | 路径 | 说明 | 失败拒绝时的业务执行 |
| --- | --- | --- | --- |
| POST | `/api/ai/chat` | Orchestrator 统一入口（RAG / Tool / Text-to-SQL） | LLM = 0 · Tool = 0 · SQL = 0 |
| POST | `/api/chat` | RAG 问答（ChatService → RagService） | LLM = 0 · RAG = 0 |
| POST | `/api/chat/with-tools` | Multi-Step Tool Calling（真实 Tool 执行） | LLM = 0 · Tool = 0 |
| POST | `/api/rag/answer` | 独立 RAG 问答（可指定 `top_k`） | LLM = 0 · RAG = 0 |

```text
Breaking change（M2.3 A1）:
The four AI execution endpoints no longer permit anonymous execution.
```

### 13.1 认证（浏览器调用）

- 身份**只**来自 M1 服务端会话（`wms_ai_session` HttpOnly Cookie →
  `ai_ops.user_session`，见 §12.2 的同一机制）；登录入口 `POST /api/auth/login`。
- 缺 / 无效 / 过期 / 已撤销会话 ⇒ **401**（固定文案「认证失败：缺少或无效的会话」）；
  认证服务不可用 ⇒ **503**（固定文案「认证服务不可用」）——**绝不**降级为匿名。
- 本 API **不**接受 `X-API-Key`、Query 或 Body 中的用户身份；四个端点亦**不**
  新增 API-Key 认证路径（如需机器调用，必须先建立独立契约并单独评审）。
- 认证与 CSRF 校验**先于**任何 LLM / RAG / Tool / Text-to-SQL 执行。

### 13.2 CSRF（写操作保护）

- 四个端点均为 POST 且**有服务端副作用**（LLM 成本、真实 Tool 执行、
  Text-to-SQL 只读查询、观测记录写入）⇒ 与 M1/M2.2 同机制要求会话绑定的
  双提交头：`X-CSRF-Token` = `wms_ai_csrf` Cookie 值，且必须等于服务端用
  当前会话重算的 HMAC。
- 缺头 / 缺 Cookie / 两者不一致 / 伪造值 / 其他会话的值 ⇒ **403**
  （固定文案「CSRF 校验失败」），副作用为零。
- **不**依赖 `SameSite` 或自定义前端头作为 CSRF 防护（见 OWASP CSRF
  Prevention Cheat Sheet）。

### 13.3 兼容性（除认证外契约不变）

- 请求 DTO / 成功响应 DTO / 正常业务结果结构**均未变化**（见 §2.2 / §2.3 /
  §2.4 / §2.9 的既有定义）；参数校验语义（422 / 400）不变。
- Tool 参数契约、RAG 检索语义、Text-to-SQL 语义、Router 策略、Prompt、
  幂等行为（四个端点本就无幂等机制）均未修改。
- 受影响调用方：仓库内 26 个测试文件（已改为携带会话 + CSRF 的客户端）、
  `README.md` 中的匿名用法示例（待后续文档提交同步）；**无**仓库内脚本 /
  服务间调用方依赖匿名访问。

### 13.4 边界（如实声明）

- **A1 ≠ 全部 API 已完成认证**：`/api/usage*`（1）与
  `/api/observability/*`（6，allowlist 冻结）仍为只读匿名端点，
  未纳入本阶段（见 A2 计划）；`/api/health` 保持公开。
- 当前项目作用域仍是**单部署项目作用域**（§12.4），**不是**用户级 RBAC。
- HTTPS / 网络边界**未验证** ⇒ 本阶段完成**不代表**生产部署就绪。

### 13.5 测试证据

- 认证 / CSRF 矩阵：`tests/test_ai_execution_api_auth.py`
  （四端点 × 未登录 / 无效 / 过期 / 已撤销 / 缺 CSRF / 伪造 CSRF / 换会话 /
  合法 / 认证服务故障 / 参数校验；失败路径断言四类业务调用为 0）
- 调用方改造后的回归：`tests/test_chat.py`、`tests/test_chat_api.py`、
  `tests/test_rag_api.py`、`tests/test_tool_chat_api.py` 等 26 个文件
  （认证客户端由 `tests/api_session_auth.py` 提供；生产
  `require_session` / `require_csrf` 仍真实执行，**无** `dependency_overrides`）

---

## 14. 只读用量／可观测性接口认证与授权（Phase 7 Recovery M2.3 A2）

**Breaking change**：下列 7 个端点从**匿名可读**改为**必须认证**（对早期
"只读观测面公开"契约的正式修订；先例同 A1 / S7.1→S7.2）：

| Method | Path | 访问模型 |
| --- | --- | --- |
| GET | `/api/usage/analytics` | Session + 运维白名单 |
| GET | `/api/observability/tools` | Session + 运维白名单 |
| GET | `/api/observability/tools/metrics` | Session + 运维白名单 |
| GET | `/api/observability/tools/history` | Session + 服务端 `PROJECT_ID` |
| GET | `/api/observability/tools/metrics/persistent` | Session + 服务端 `PROJECT_ID` |
| GET | `/api/observability/assistant-trace/{assistant_request_id}` | Session + 运维白名单 |
| GET | `/api/observability/assistant-timeline/{assistant_request_id}` | Session + 运维白名单 |

### 14.1 状态码

| 场景 | 状态码 | 说明 |
| --- | --- | --- |
| 匿名 / 无效 / 过期 / 已撤销会话 | `401` | 固定文案；认证前零数据读取 |
| 会话服务不可用 | `503` | fail closed；绝不匿名放行 |
| 运维白名单缺失 / 空白 / 无法解析 | `503` | 固定文案；不回显白名单内容 |
| 已认证但非运维用户 | `403` | 固定文案；不泄露白名单内容 |
| `PROJECT_ID` 缺失 / 空白 | `503` | 固定文案；不回退默认项目 |
| 其余（DB 不可用 / 非法参数 / 未知异常） | `502` / `422` / `500` | 与 A2 前契约一致 |

### 14.2 与 A2 前的契约差异

- **新增**：401 / 403 / 503 响应（运维面 401+403+503；项目面 401+503）。
- **移除**：`GET /api/observability/tools/history` 与
  `GET /api/observability/tools/metrics/persistent` 的客户端 `project_id`
  查询参数（Step 32 / Step 33 曾提供）。作用域只来自服务端 `PROJECT_ID`。
- **不变**：成功响应 DTO 与字段白名单、分页语义（`limit` / `offset`）、
  排序（`started_at DESC, id DESC`）、空结果语义（200 + 空列表 / 零值）、
  502 不回退内存视图、`project_id IS NULL` 记录不可见（fail-closed）。
- **GET 不适用 CSRF**：缺少或错误的 `X-CSRF-Token` 均不产生 403。

### 14.3 配置

```text
OBSERVABILITY_READ_OPS_USER_IDS=<逗号分隔 user_id>   # 无默认值；未配置 ⇒ 503
PROJECT_ID=<部署唯一授权项目>                        # 见 §12.4
```

### 14.4 边界（不宣称已达成）

- A2-01 / A2-02 / A2-03 是**运维专用全局视图**，无项目级过滤维度。
- A2-06 / A2-07 **无法验证资源级项目归属**（`llm_usage_record` /
  `assistant_outcome_record` 无 `project_id`；`resource_owner` 不登记
  `assistant_request_id`），只能整端点限定为运维专用。
- 运维白名单是**部署级授权**，不是用户级 RBAC。
- 详见 `docs/security.md` §10。

### 14.5 测试证据

`tests/test_observability_read_api_auth.py`（64 例）；DB 级断言见
`tests/test_tool_observability_history_api_db.py`、
`tests/test_tool_observability_history_filtering_db.py`、
`tests/test_tool_observability_persistent_metrics_db.py`。
