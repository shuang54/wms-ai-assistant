# Phase 3.12 Step 34 — AI Assistant Chat Architecture Audit

> 审计步骤（**0 生产代码修改**）。目的：在 Phase 3.11 Tool Observability 收尾后，
> 基于真实代码说清当前 Chat / AI API 现状，并确定 Phase 3.12 的最小实现范围。
> 全部结论来自代码阅读（file:line 见各节）；未调用真实 LLM、未访问生产数据库、未修改任何 API。

---

## 1. Scope

```text
审计对象
    backend/app/main.py + backend/app/api/**（10 条路由）
    AIOrchestratorService / AIRouterService / RagService / ToolChatService /
    ToolExecutionService / TextToSQLService / LLM 客户端与 usage 观测
    AIOrchestrationResult / API DTO / 错误映射 / Conversation 能力

不在范围
    不新增 Chat API / 不修改 Orchestrator · Router · ToolChat · RAG · T2S
    不新增 Conversation · Memory · Session · Agent · MCP · LangGraph
    不改数据库 / 不改 API contract / 不新增测试框架
```

## 2. Current API Matrix

```text
| # | API                          | Entry Service                     | Orc | RAG | Tool | T2S | LLM | DB | project_id | request_id |
|---|------------------------------|-----------------------------------|-----|-----|------|-----|-----|----|------------|------------|
| 1 | GET  /api/health             | ping_database()                   |  ✗  |  ✗  |  ✗   |  ✗  |  ✗  | ✓* | ✗          | ✗          |
| 2 | POST /api/chat               | ChatService → RagService.answer   |  ✗  |  ✓  |  ✗   |  ✗  |  ✓  |  ✓ | ✗          | ✗          |
| 3 | POST /api/rag/answer         | RagService.answer(query, top_k)   |  ✗  |  ✓  |  ✗   |  ✗  |  ✓  |  ✓ | ✗          | ✗          |
| 4 | POST /api/chat/with-tools    | ToolChatService.chat              |  ✗  |  ✗  |  ✓†  |  ✗  |  ✓  |  ✓‡| ✓ (body)   | ✗          |
| 5 | POST /api/ai/chat            | AIOrchestratorService.execute     |  ✓  |  ✓  |  ✓   |  ✓  |  ✓  |  ✓ | ✓ (body)   | ✗          |
| 6 | GET  /api/usage/analytics    | LLMUsageAnalyticsReadFacade       |  ✗  |  ✗  |  ✗   |  ✗  |  ✗  | ✓(读)| ✗        | ✓ (query)  |
| 7 | GET  /api/observability/tools         | ToolObservabilityQueryService   | ✗ | ✗ | ✗ | ✗ | ✗ | ✗(内存) | ✗ | ✗ |
| 8 | GET  /api/observability/tools/metrics | 同上 `.metrics()`               | ✗ | ✗ | ✗ | ✗ | ✗ | ✗(内存) | ✗ | ✗ |
| 9 | GET  /api/observability/tools/history | PersistentQueryService          | ✗ | ✗ | ✗ | ✗ | ✗ | ✓(读) | ✓ (query) | ✗ (响应含) |
|10 | GET  /api/observability/tools/metrics/persistent | 同上 `.metrics()`    | ✗ | ✗ | ✗ | ✗ | ✗ | ✓(读) | ✓ (query) | ✗ |

* /api/health 的 DB ping 失败仍返回 200（database="error"），不抛 HTTPException
† ToolChat 是**自身 LLM Function-Calling loop**（Tool 由 LLM 选择，不经 AIRouter）
‡ ToolChat 自身不访问 DB；DB 只可能发生在 Tool Handler 内部（如 get_inventory）
```

路由注册：`main.py:37-47`（`/api` 前缀统一挂载：health / chat / rag / tool_chat /
orchestrator_chat / usage / tool_observability）。
**无路由别名、无 redirect、无 StreamingResponse**（全仓流式搜索 0 命中）。

## 3. `/api/ai/chat`

```text
POST /api/ai/chat（orchestrator_chat.py:490-634）

Request ：ChatRequest{ question: str(min_length=1, strip；纯空白 → 422),
                      project_id: str|None(max_length=128) }   :144-172
Response：ChatResponse{ route: str, content: str|None,
                        data: object|None, metadata: dict }      :207-231
          route 分支映射（:453-483）
            rag          → data = { sources[], used_chunks_count }（无 chunk content）
            tool         → data = { tool_name, success, data, error }
            text_to_sql  → data = { columns, rows, row_count, truncated,
                                    execution_time_ms, referenced_tables,
                                    project_id, sql }（refusal → data=None + refused）
            else         → data = None（不报错；未知 route 由 Orchestrator 抛 502）

Pipeline：AIOrchestratorService.execute(question)
             → AIRouter.route（rule → tool_match → LLM fallback → RAG 兜底）
             → RAG | TOOL | TEXT_TO_SQL（单次执行；无 Agent / 无 Loop）
             → AIOrchestrationResult → ChatResponse
```

```text
project_id  ✓ 提供时 per-project 重建 Orchestrator（factory；复用 base 的 RAG/T2S，
               绑定项目 Engine / 知识 scope，:539-543）：未注册 → 404；能力禁用 → 403
request_id  ✗ 不接受、不返回（Orchestrator 内部每次 execute 生成 uuid4，:520-523）
content     ✓ Orchestrator 自然语言 / 摘要（Tool 路径为结果序列化摘要）
data        ✓ 按 route 的结构化对象（见上）
metadata    ✓ RAG：decision_source / route_reason / knowledge_scope；
              TOOL：decision_source / route_reason / tool_name / tool_success；
              T2S：project_id / sql / selected_tables 等（API 侧取用）
错误        ✓ 统一边界（Orchestrator 异常族 → 400/403/404/502/503/500 + catch-all 500）
Tool 观测   ✓ Tool 路径产生 1 条 ToolExecutionRecord（request_id / round=1 /
              tool_call_id=None）；RAG / T2S → 0 条；403 / 404 → 0 条
LLM 观测    ✗ 默认 NoopAccountingSink（响应内不返回任何 usage / request_id）
```

结论：**`/api/ai/chat` 已经是唯一具备"统一入口 + 三路由 + 统一结果 envelope +
统一错误边界"的端点**，可承担 AI Assistant 后端主入口；缺口是**外部可关联的
trace id**（request_id 不返回）与 Conversation（见 §7 / §12）。

## 4. `/api/chat`

```text
POST /api/chat（chat.py:94-…）   Phase 2 协议，Phase 3.5.6 起改走 RAG

Request ：ChatRequest{ message: str(min_length=1) }        :33-36
Response：ChatResponse{ answer, sources[], used_chunks_count }  :49-65
Service ：ChatService.chat()（chat_service.py:52）→ RagService.answer()
对比 /api/rag/answer：同一条 RagService 链路；差异仅
    · /api/rag/answer 可显式传 top_k（1..50）、返回 source.content；:54-57,79
    · /api/chat 不返回 chunk content（防大段知识原文外泄）；:40,76
错误：400（空白消息）+ 共享 _rag_error_mapping（400 / 422 / 503 / 502 / 500）
relation to /api/ai/chat：完全不同链路（无 Orchestrator / 无 Router / 无 Tool / 无 T2S）
```

```text
Current              ：向后兼容的「单能力（RAG-only）」对话端点（Phase 2 协议）
Candidate future role：Legacy / 兼容保留（前端或既有集成依赖 {answer} 形态）
                       不作为 Assistant 主入口 → 不删除、不改语义（§十五）
```

## 5. `/api/chat/with-tools`

```text
POST /api/chat/with-tools（tool_chat.py:219-…）

Request ：ToolChatRequest{ message: str, project_id: str|None }   :115-134
Response：ToolChatApiResponse{ answer: str, tool_calls[{tool_name}] }  :137-153
Service ：ToolChatService.chat()（自建 LLM Function-Calling loop）
            MAX_TOOL_ROUNDS = settings.tool.max_rounds（默认 5，sequential；
            每轮最多 1 个 Tool Call → 超限 502 MultipleToolCallsError）
          装配：ToolRegistry(get_inventory) + ToolExecutionService（:161-168）
          执行边界：ToolExecutionService → ToolExecutionContext（round 递增）
                    → Tool Observability（collector/observer 来自 Composition Root）
                    → 服务器端 capability 白名单（project_id → ProjectRegistry）
错误：404 未注册项目 / 403 能力禁用 / 400 ValueError /
      502 MultipleToolCallsError · ToolCallingBudgetExceededError /
      500 ToolChatError / 共享 _rag_error_mapping；422 Pydantic
```

```text
/api/chat/with-tools 与 /api/ai/chat(tool) 是否是两套 AI Tool 执行模型？→ 是。

                      /api/ai/chat（route=tool）        /api/chat/with-tools
Tool 选择              AIRouter capability 匹配（rule）  LLM Function Calling（响应 tool_calls）
LLM 决策参与           否（Tool 名不经 LLM）              是（由 LLM 决定调用与参数）
轮次                   单轮（round 恒 1）                 多轮（round 1..N，受预算限制）
tool_call_id          恒 None（不伪造）                   填充 LLM ToolCall.id
回流                  无（Tool 结果不回 LLM）             有（tool message 回传 LLM 再生成 answer）
响应                  route/content/data/metadata         answer + tool_calls[名称]
错误语义              502 route error / 500 execution     502 MultipleToolCalls / BudgetExceeded
共同点                ToolExecutionService 边界 · ToolExecutionContext ·
                     Tool Observability · ProjectCapability（403）
```

## 6. AIOrchestrator

```text
Input（question: str, *, context: str|None）
  ↓ AIRouter.route(question, context)   rule → tool_match → LLM fallback → RAG 兜底
  ↓ 能力硬校验（knowledge / text_to_sql；Tool 白名单在 ToolExecutionService）
  ↓ RAG | TOOL | TEXT_TO_SQL（单次执行）
  ↓ AIOrchestrationResult
```

```text
1.  project_id           ✓（服务器端 ProjectContext / capability；HTTP 无法开启能力）
2.  request_id           ✓ 内部生成（new_request_id()，:520-523）✗ 不返回给调用方
3.  统一错误边界          ✓ AIOrchestrator{Input,Route,Execution,Unavailable,Capability}Error
                            + 未知 route → RouteError；API 映射为 400/403/404/502/503/500
4.  Tool 观测            ✓ 单例 observer 注入（默认 Application Collector）；
                            Record：request_id / round=1 / project_id / tool_call_id=None
5.  LLM usage 观测        ✗ 默认 NoopAccountingSink；且 llm_usage_record.request_id 是
                            Provider 响应 ID，与 Orchestrator 的 request_id **不互通**
6.  连续对话              ✗（无 conversation / 无 turn 概念）
7.  保存 conversation     ✗
8.  保存 message          ✗
9.  上下文历史            ✗（`context` 参数只作为 Router 的辅助文本，不进 RAG/Tool/T2S、
                            不入库、API 未暴露；不是对话历史）
10. 无状态单轮            ✓（execute(question) 幂等无状态；无跨请求状态）
```

## 7. Conversation / Memory

```text
Conversation Model / Message Model / Conversation Service / Repository /
Chat History / Session / Memory          = NOT IMPLEMENTED

证据
    · ORM 仅 4 个：knowledge_document / knowledge_chunk（public）、
      llm_usage_record / tool_execution_record（ai_ops）
      —— db/models/__init__.py 明确「业务表（conversation / user / role）后续 Phase」
    · docs/database.md 把 conversation / conversation_message 列为未来计划表（未建）
    · 无任何端点接受 messages[] / history / previous_*（4 个 chat 端点均为单字段输入）
    · 唯一 messages 列表在 ToolChatService.chat() 内部局部变量，请求结束即丢弃
    · InMemory* 类均为运行时注册表 / 观测窗口，与聊天记忆无关
    · 无 Alembic / 无迁移脚本

影响
    · 每个请求都是独立单轮；无法「继续上一句」
    · Tool 观测的多轮（round>1）只存在于 ToolChat 单次请求内部
```

## 8. Result Contract

```text
@dataclass(frozen=True)
class AIOrchestrationResult:            ai_orchestrator_service.py:126-142
    route: RouteType                    （rag | tool | text_to_sql）
    content: str | None
    data: Any                           （RagResponse | ToolResult | SQLExecutionResult
                                          | None（T2S refusal））
    metadata: Mapping[str, Any]         （路由 + 执行统计；无敏感信息）
```

```text
是否 frozen            ：✓（dataclass(frozen=True)）；metadata 是 Mapping（浅不可变）
是否足够作为统一结果    ：单轮场景 ✓（3 路由 + 统一 envelope 已在用）
记录缺口（**不改 DTO**）：
    a) data: Any 未类型化 → API 侧需按 route 分支映射（已实现，但耦合 DTO 形状）
    b) 无 request_id / trace id → 调用方无法关联这次执行的观测记录
    c) 无显式 error / refused 字段（T2S refusal 用 data=None + metadata 标志承载）
    d) 无 conversation / turn 标识（未来多轮需扩展，属 Phase 3.12 之后）
```

## 9. Error Contract

```text
/api/chat · /api/rag/answer · /api/chat/with-tools   → 共享 _rag_error_mapping
    400 输入非法 / 422 校验 / 503 LLMConfigError / 502 LLM*Error / 500 LLMError
/api/ai/chat                                          → Orchestrator 语义
    400 AIOrchestratorInputError / 403 CapabilityError / 404 ProjectNotFoundError
    502 RouteError / 503 UnavailableError / 500 ExecutionError / 500 catch-all
/api/chat/with-tools 另加
    404 ProjectNotFoundError · 403 CapabilityError
    502 MultipleToolCallsError · ToolCallingBudgetExceededError · 500 ToolChatError
/api/usage/analytics
    422 输入非法 / 502 LLMUsageRepositoryError / 500 查询与聚合错误
/api/observability/tools/*
    422 Query 校验 / 502 RepositoryError（持久端点）/ 500 其它
全局                ：无 exception_handler / 无中间件；未映射异常 → Starlette 默认 500
```

```text
Unified Chat Error Contract = NOT YET DEFINED
    · 无统一错误 envelope（detail 是自由字符串；无 code / request_id / route）
    · 同一「RAG 失败」在 /api/chat 是 502/503 细分，在 /api/ai/chat 是 500 execution
    · 不修改：仅记录语义映射表（本文件），供后续 Step 决策
```

## 10. Observability

```text
Tool Execution  ✓
    request_id（Orchestrator/ToolChat 每次请求生成 uuid4）· round（Orchestrator 恒 1；
    ToolChat 递增）· tool_call_id（Orchestrator None；ToolChat = LLM call id）
    落库 ai_ops.tool_execution_record；读：/history（limit/offset + project_id/
    tool_name/success 精确过滤）；RAG / T2S / 403 / 404 → 0 Record

LLM Usage       △（能力存在，默认未接线，且不可关联 Chat）
    ai_ops.llm_usage_record(request_id=Provider 响应 ID, provider, model,
    prompt/completion/total tokens, created_at)；create_llm_client() 默认
    NoopAccountingSink；与 Tool request_id 不互通 → **无法按 chat 请求关联**

RAG             ✗（只有日志）
    rag_service 结构化日志含 elapsed_ms / rerank_elapsed_ms / counts，
    但 **不含 request_id**、不落库；RagResponse 无 latency 字段；
    Orchestrator 仅透出 decision_source / route_reason / knowledge_scope
```

## 11. Duplicate / Legacy Paths

```text
入口        /api/chat               Legacy（RAG-only，Phase 2 协议）
            /api/rag/answer         RAG 专用（top_k + content），保留
            /api/chat/with-tools    Tool 专用（LLM Function Calling 多轮）
            /api/ai/chat            ★ Canonical Candidate（统一入口）

Router      仅 1 个（AIRouter）；但 ToolChat 的「Tool 选择」由 LLM Function Calling
            完成 → 两套 Tool 选择机制（capability 匹配 vs LLM 选择）

执行 loop   Orchestrator：无 loop（单次）；ToolChat：LLM↔Tool 多轮 loop

Result DTO  AIOrchestrationResult · ChatResponse(chat.py) · ChatResponse(orchestrator_chat.py)
            · ToolChatApiResponse · RagAnswerResponse
            ⚠ 命名冲突：ChatRequest / ChatResponse / ChatSourceResponse 在 chat.py 与
              orchestrator_chat.py 各定义一次（字段不同，import 时易混）

Error       RAG 映射族 / Orchestrator 族 / ToolChat 族 / Usage 族 / Observability 族
```

```text
未删除 / 未废弃任何端点；仅记录 Current + Candidate future role。
```

## 12. Current Readiness

最小 Assistant Backend Contract（Question + Project + Request ID → Orchestrator → Route → Result）：

```text
Question         ✓ /api/ai/chat question（strip + 非空校验）
Project          ✓ project_id（可选；服务器端注册表 → 404 / 403；能力不可由 HTTP 开启）
Request ID       ✗ 内部生成但不返回 → 调用方无法关联观测（Tool/LLM）
Orchestrator     ✓ AIOrchestratorService（唯一编排点；三路由 + RAG 兜底）
Route            ✓ RouteType{rag, tool, text_to_sql} + decision_source 可解释
Result           ✓ route/content/data/metadata（frozen AIOrchestrationResult）
多轮 / 记忆        ✗ Conversation / Memory / History = NOT IMPLEMENTED
错误契约          △ 分层清晰但无统一 envelope / 无 trace id
```

```text
Readiness = PARTIALLY READY
    · 单轮 Assistant 主链路（问答 → 路由 → 结果）**已可用**，无需新端点
    · 缺口集中在「可关联性（request_id）」与「连续性（conversation）」
    · 结论：**不需要**新建 /api/assistant/chat（会造成第三个重复入口）
```

## 13. Proposed Step 35

```text
候选方案对比（基于 §12 缺口，按成本从低到高）

A. Assistant Trace Contract（最小，推荐）
   /api/ai/chat 响应可关联：把 Orchestrator 已生成的 request_id 透出
   （AIOrchestrationResult.metadata["request_id"] → ChatResponse.metadata.request_id
   或顶层 request_id 字段）；不新增端点、不改路由 / Tool 语义 / 数据库
   收益：Tool 观测（/history）与 LLM usage 具备可关联 key；为未来多轮打基础
   成本：Orchestrator 1 行 + API 1~3 行 + 契约测试

B. Conversation 最小骨架（较大，不建议现在做）
   表 + Repository + 多轮 DTO + messages 输入 → 属数据库与产品级改动
   需先有 A（trace）与错误 envelope 决策，否则过早

C. 仅文档化错误契约（最小成本）
   把 §9 映射表写入 docs/api.md；无代码改动

推荐 Step 35 = **A（+ 可附带 C）**，且明确不做
    × 新端点 /api/assistant/chat   （重复入口）
    × Conversation / Memory / Session / Agent / MCP / LangGraph
    × Streaming / Dashboard / 前端
    × 合并 /api/chat/with-tools 与 /api/ai/chat（需独立评估）
    × 修改既有端点响应字段语义（只**新增** metadata 键，不删不改既有键）

验收（建议）
    · /api/ai/chat 响应 metadata 含 request_id（RAG / Tool / T2S 三路径一致）
    · request_id 与 ToolExecutionRecord.request_id 可对齐（Tool 路径实测）
    · 既有响应字段与错误码 0 变化；契约测试锁定「不新增端点 / 不删字段」
```

## 14. Architecture

```text
Current（真实代码）

User
 ├── POST /api/chat ──────────→ ChatService ─→ RagService ─→ LLM + DB(pgvector)
 ├── POST /api/rag/answer ────→ RagService ─→ LLM + DB(pgvector)
 ├── POST /api/chat/with-tools → ToolChatService ─→ LLM(Function Calling loop)
 │                                  └→ ToolExecutionService ─→ ToolRegistry ─→ Tool ─→ DB
 ├── POST /api/ai/chat ───────→ AIOrchestratorService
 │                                  └→ AIRouter ─┬─ RAG ─→ RagService
 │                                               ├─ TOOL ─→ ToolExecutionService ─→ Tool
 │                                               └─ TEXT_TO_SQL ─→ Generator/Validator/Executor
 └── GET  /api/observability/* ─→ QueryService（内存 / ai_ops 只读）

Candidate（本审计结论：现有 /api/ai/chat 即为该形态，无需新端点）

User
  ↓
POST /api/ai/chat        （AI Assistant 统一入口；request_id 待透出 = Step 35 提案）
  ↓
AIOrchestratorService
  ↓
AIRouter（rule → tool_match → LLM fallback → RAG 兜底）
  ├── RAG
  ├── TOOL ──→ ToolExecutionService ─→ ToolRegistry ─→ Tool
  └── TEXT_TO_SQL
  ↓
AIOrchestrationResult（frozen）
  ↓
ChatResponse{ route, content, data, metadata }
```

## 15. Limitations

```text
* 审计为静态 + 只读检查；未做性能 / 并发 / 多 worker 验证
* LLM usage 生产接线（NoopAccountingSink）现状未改动；仅记录
* request_id 与 llm_usage_record.request_id 的语义差异（引擎 UUID vs Provider ID）
  未在本 Step 统一（属 Step 35 之后的独立评估）
* 未评估前端需求 / 流式体验 / 多租户
* 未评估 /api/chat/with-tools 与 /api/ai/chat 的最终归并方案（需要产品级决策）
* 错误契约仅记录映射，未实现统一 envelope
* Conversation / Memory / Multi-turn = NOT IMPLEMENTED（待后续阶段）
* 本 Step 0 生产代码修改；测试为既有套件回归（3733 passed / 406 skipped）
```
