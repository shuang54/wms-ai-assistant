# Phase 3.12 Step 61 — Assistant Trace Outcome Boundary Audit

> **Outcome boundary audit · 无生产实现**
> 新增：`tests/test_assistant_trace_outcome_audit.py`（10 离线；**未新增 DTO / 字段 / API 参数**）。
> **Production code changes = 0 · API contract changes = 0 · DB schema changes = 0 ·
> Index changes = 0 · Prompt changes = 0**（未发现需要 STOP 的安全问题）

---

## 1. Scope

```text
问题：当前系统除了"调用了什么"，能否可靠表达"这次 AI 请求最终是什么结果"？
      （Success / Failure / Refusal / Empty；以及 execution success ≠ assistant-level outcome）
方式：只读审计真实代码 + 用既有装配驱动 8 类场景，**捕获真实响应包络**（非推断）
入口：POST /api/ai/chat（真实 app / Orchestrator / Router / Trace API；只替换外部边界）
未做：Outcome DTO / status / outcome / error_code / result_status / trace_status / success 字段
```

## 2. Current Result Contract

```text
AIOrchestrationResult（frozen；backend/app/services/ai_orchestrator_service.py）
    route    : RouteType（rag / tool / text_to_sql）        ← 能力选择结果
    content  : str | None                                  ← 用户可见文本
    data     : Any（RagResponse / ToolResult / SQLExecutionResult / None）← 结构化对象
    metadata : Mapping[str, Any]                           ← 路由 + 执行统计

HTTP 响应（/api/ai/chat）= { route, content, data, metadata }（无 outcome / status 字段）
失败 = HTTP 4xx/5xx + { detail: <固定短语 + 异常**类名**> }（无结构化 outcome）

⇒ **route ≠ outcome**：route 只表达"选中了哪种能力"，
   不表达"业务结果成功 / 空 / 拒绝 / 失败"。
```

## 3. Outcome Matrix（实测包络）

| 场景 | HTTP | route | content | data | metadata 关键字段 | LLM 调用 | Validator / Executor |
| --- | --- | --- | --- | --- | --- | --- | --- |
| RAG 正常 | 200 | `rag` | LLM 答案 | dict | `rag_used_chunks=1` · `request_id` | 1 | — |
| RAG **空检索** | 200 | `rag` | **固定提示语**（"知识库中没有找到…"） | dict | `rag_used_chunks=**0**` | **0** | — |
| TOOL 成功 | 200 | `tool` | 数据摘要（`material_code=MAT-001`） | dict | `tool_success=**true**` · `tool_name` | 0 | Handler 1 次 |
| TOOL **失败** | **200** | `tool` | `[Tool get_inventory 失败] Tool 'get_inventory' 执行失败: [RuntimeError] handler raised unexpected exception` | dict | `tool_success=**false**` | 0 | Handler 1 次（异常被归一化） |
| T2SQL 成功（重试后） | 200 | `text_to_sql` | `查询到 3 条` | dict | `sql` · `row_count=3` · `truncated=false` · `execution_time_ms=1.5` · `selected_tables` · `project_id` · `request_id` | **2** | Validator 2 次 / Executor 1 次 |
| T2SQL **refusal** | 200 | `text_to_sql` | 固定拒绝文本 | **None** | `refused=**true**` · `request_id`（无 `refusal_reason`） | 1 | Validator **0** / Executor **0** |
| LLM 失败（provider 500） | **500** | — | — | — | — | — | detail `AI 能力执行失败: RAG 执行失败: LLMRequestError` |
| RAG 检索失败 | **500** | — | — | — | — | 0 | detail `…RAG 执行失败: RuntimeError`；**RAG observation 仍记录**（1 条，含 request_id） |
| T2SQL 重试耗尽 | **500** | — | — | — | — | **3** | detail `…Text-to-SQL 生成失败: TextToSQLRetryExceededError` |
| 能力禁用 | **403** | — | — | — | — | 0 | detail `项目能力未启用: 该项目未启用知识库（RAG）能力` |

```text
关键事实：
    * **Tool 业务失败 = HTTP 200**（业务失败 ≠ 传输失败）→ 仅可由 metadata.tool_success 判别
    * **Refusal = HTTP 200** + data=None + metadata.refused=True（与 500/4xx 明确区分）
    * 空检索 = HTTP 200 + 固定 content + rag_used_chunks=0（**无显式 empty 标记**）
    * 失败 = 5xx，且 detail 只到 **异常类名** 粒度（LLM / RAG / T2SQL 生成耗尽 的类名不同，
      但 **HTTP 状态码相同**、无结构化 error 字段）
```

## 4. Current Trace Capability

| 问题 | 当前是否能回答 | 数据来源 |
| --- | --- | --- |
| 请求走了什么 Route | **✗**（Trace 三段不含 route；仅 chat 响应 `metadata.route`） | AI result |
| 是否调用 LLM | ✓ | LLM Usage 段（非空） |
| 调用了多少次 LLM | ✓（段长度） | LLM Usage |
| 是否调用 RAG | ✓ | RAG Execution 段 |
| 是否调用 Tool | ✓ | Tool Execution 段 |
| Tool 执行是否成功 | ✓（每条 `success`） | Tool Record |
| RAG 是否成功 | **✗**（记录只有 result_count / 耗时等事实，无 success 字段；失败仅体现在 HTTP） | RAG Record / HTTP |
| Text-to-SQL 是否成功 | **✗**（Trace 无 SQL 段；usage 条数无法表达"SQL 执行成功 / 拒绝 / 耗尽"） | 现有 Result |
| 最终 HTTP 是否成功 | **✗**（Trace 不记录 HTTP 结果） | API |
| 最终业务结果是否为空 | **✗**（`result_count=0` 可**推断**空检索，但无显式 empty 语义） | Result |
| 是否 refusal | **✗**（Trace 无 refused；仅 chat `metadata.refused`） | Result |
| 为什么失败 | **✗**（Tool 记录 `error_code`/`error_type` 在通用 Handler 异常时为 **None**；其余段无 error 字段；原因文本仅在 chat content） | Error / Record |
| 最终返回内容 | **✗**（Trace 不含 answer / content / data） | AI API Response |

```text
结论（§八 的核心区分）：
    LLM Success / Tool Success / Assistant-level Outcome 三者**不同层**：
        LLM = SUCCESS + Tool = SUCCESS + Assistant = FAILURE（结果组装失败）→ 当前**无法表达**
        LLM attempt1 = FAIL(validator) → attempt2 = SUCCESS → Assistant = SUCCESS → 可**间接**推断（usage=2）
    ⇒ **底层 execution success ≠ Assistant-level outcome**，当前系统**缺少**该表达层。
```

## 5. Metadata Audit

| Field | Meaning | Source | Safe? |
| --- | --- | --- | --- |
| `decision_source` | Router 决策来源（rule / tool_match / fallback） | RouteDecision | ✓ |
| `route_reason` | 规则命中说明文本（固定模板 + 匹配词） | RouteDecision | ✓ |
| `request_id` | **Assistant Trace ID**（≠ provider request id） | Orchestrator `new_request_id()` | ✓ |
| `knowledge_scope` | 知识库 namespace 或 null（仅 RAG） | ProjectKnowledgeScope | ✓ |
| `rag_used_chunks` | 使用的 chunk 数（int，空检索 = 0） | RagResponse | ✓ |
| `tool_name` / `tool_success` | Tool 名称 / **执行成功布尔**（仅 TOOL） | ToolResult | ✓ |
| `sql` | **生成的只读 SQL**（仅 T2SQL；chat 响应可见，**不在 Trace**） | TextToSQLResult.sql | 事实：设计如此（Validator 通过；非凭据） |
| `row_count` / `truncated` / `execution_time_ms` | 结果行数 / 是否截断 / 执行耗时 | SQLExecutionResult | ✓ |
| `selected_tables` / `project_id` | 选中表名 + 项目标识（仅 T2SQL） | 上下文 | ✓ |
| `refused` | **refusal 标记**（仅 T2SQL refusal） | Orchestrator（内部 refusal_reason 不外泄） | ✓ |
| — | **不包含**：prompt / messages / raw response / 凭据 / traceback / refusal_reason | — | ✓ |

## 6. Security

```text
实测（哨兵法：异常消息 = "STEP61-RAW-EXCEPTION-MESSAGE"）：
    * 异常 detail 只含 **固定短语 + 异常类名**（如 `RAG 执行失败: RuntimeError`）——
      原始异常消息 / traceback **不外泄**（4 类失败分支均验证）✓
    * Tool 失败 content 使用 registry 规范文本（`[RuntimeError] handler raised unexpected
      exception`）+ 类名；原始异常消息不外泄 ✓
    * 未知异常 → detail = "AI 服务内部错误"（无细节）✓
    * Trace API 502/500 detail 为固定短语（"助手链路观测数据不可用"）✓
    * Trace payload 不含 prompt / SQL / chunk 正文 / 凭据（Step 54/59/60 持续锁定）✓
观察（非新风险，未阻止本阶段）：`/api/ai/chat` 的 T2SQL `metadata.sql` 对客户端可见
    （Phase 3.9 既有设计；只读 SELECT + Validator 通过；**不进入** Assistant Trace）
Security finding requiring STOP = **无**
```

## 7. Decision

```text
Outcome Decision = **OUTCOME_BOUNDARY_MISSING**

理由（全部来自 §3 / §4 实测）：
    * Trace 层：无 route / 无 status / 无 refusal / 无 empty / 无 HTTP 结果 / 无失败原因汇总
    * API 层：outcome 语义**分散且不统一** —— TOOL 用 metadata.tool_success、
      T2SQL-refusal 用 metadata.refused、空检索只能靠 rag_used_chunks=0 + 固定文案推断、
      失败只能靠 HTTP 5xx（且 LLM / RAG / 生成耗尽 三类失败状态码相同，仅 detail 类名不同）
    * 无法表达 §八 的组合情形（LLM 成功 + Tool 成功 + 组装失败）

Future Step Candidate（**仅记录，本阶段不实现**）：
    * 候选 1：Chat/Chat 响应层增加**统一 outcome**（如 success / empty / refused / failed +
      可选 error_class）——需先决定数据源（Orchestrator 返回值）与是否需要持久化
    * 候选 2：Assistant Trace 增加"请求级 outcome 摘要"——需先决定**存储边界**
      （Trace 现为纯读模型：三段 records 无请求级记录）与安全白名单
    * 两者均属未来阶段，需单独设计 + 审计（本阶段禁止实现 Outcome DTO）
```

## 8. Limitations

```text
* 场景覆盖为 8 类（RAG 正常/空 · TOOL 成功/失败 · T2SQL 成功/refusal/耗尽 · LLM/RAG 失败 ·
  能力禁用）；未覆盖 T2SQL Executor 失败 / 超时取消等分支（代码路径已读，未逐一驱动）
* 未新增 DB-gated 用例：失败场景的**持久化**关联已由 Step 60 覆盖（本阶段聚焦响应包络语义）
* Outcome 语义结论基于当前代码与既有装配（MockTransport；0 真实 LLM / 0 网络）
* "Assistant-level Outcome" 的未来设计（存储 / 安全 / 保留期）超出本阶段范围，未评估
* 未评估客户端解析现有 metadata 的兼容成本（若未来统一 outcome，需兼容性设计）
```
