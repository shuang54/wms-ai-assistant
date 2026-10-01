# Phase 3.12 — Trace / Timeline Contract Baseline（Step 73 固化）

> 基线来源：Step 64（Assistant Outcome Persistence）→ Step 65（Unified Timeline Audit）
> → Step 66（分组投影）→ Step 67（HTTP API 可行性）→ Step 68（HTTP Read API）
> → Step 69（PostgreSQL E2E）→ Step 70（并发隔离）→ Step 71（Trace ↔ Timeline 一致性）
> → Step 72（写入中读取）。
> 回归守卫：`tests/test_assistant_trace_timeline_contract.py`（offline · DB = 0 · network = 0）。
> **本文件只固化已经验证过的行为**；未验证的行为一律不写入。

---

## 1. Contract Matrix

| Contract | Current Behavior | Verified By |
| --- | --- | --- |
| Unknown Trace request | 200 + 三段空 + `outcome=null` | Step 39/40/41/48（既有）· 68/69/72 |
| Unknown Timeline request | 200 + 四段空（`outcome_event=null`） | Step 68 · 69 · 72 |
| LLM Trace `request_id` | **Provider** request ID（`chatcmpl-…`） | Step 37（既有）· 71 · 72 |
| LLM Trace `assistant_request_id` | Assistant Request ID（关联键） | Step 36/37 · 71 · 72 |
| LLM Timeline `source_id` | `llm_usage_record.id`（DB PK） | Step 66 · 69 · 71 · 72 |
| Tool Trace `request_id` | **Assistant Request ID**（无独立 Tool scope） | Step 41 · 69 · 72 |
| Tool Timeline `source_id` | `tool_execution_record.id`（DB PK） | Step 66 · 69 · 72 |
| RAG Trace `request_id` | **Assistant Request ID** | Step 46/48 · 69 · 72 |
| RAG Timeline `source_id` | `rag_execution_record.id`（DB PK） | Step 66 · 69 · 72 |
| Outcome Trace `outcome` | 枚举四值 或 `null`（无 `source_id`） | Step 64 · 71 · 72 |
| Outcome Timeline `source_id` | `assistant_outcome_record.id`（DB PK） | Step 66 · 69 · 71 · 72 |
| Partial state | **合法**（HTTP 200 ≠ 完整快照） | Step 72 |
| Missing outcome | `null`（两侧一致；**不推断**） | Step 64 · 69 · 71 · 72 |
| Outcome `SUCCESS` | 仅 `assistant_outcome_record` 明确存在时表达 | Step 64 · 71 · 72 |
| Outcome `FAILED` | 同上（含 Tool 200 / 5xx 失败路径） | Step 64 · 71 · 72 |
| Outcome `REFUSED` | 同上（refusal 200） | Step 64 · 71 · 72 |
| Outcome `EMPTY` | 同上（RAG 空检索 200） | Step 64 · 71 · 72 |
| Cross-request isolation | **必需**（HTTP + DB 双层） | Step 70 · 71 · 72 |
| No fake outcome inference | **必需**（不从 HTTP status / 事件存在性推断） | Step 72 |
| Group ordering（LLM） | `created_at ASC, id ASC` | Step 37 · 66 · 69 · 70 |
| Group ordering（Tool / RAG） | `id ASC` | Step 41/48 · 66 · 69 · 70 |
| Group ordering（Outcome） | 至多 1 条（`UNIQUE(assistant_request_id)`） | Step 64 · 66 · 70 |
| Global ordering（跨组） | **NOT PROVIDED** | Step 65 · 72 |
| `event_id` | **NOT PROVIDED** | Step 65 · 72 |
| `sequence` | **NOT PROVIDED** | Step 65 · 72 |
| `span_id` / `parent_event_id` / `trace_id` | **NOT PROVIDED** | Step 65 · 72 |
| `pagination`（limit / offset / cursor） | **NOT PROVIDED**（DEFER） | Step 59 · 67 · 72 |
| transaction snapshot（跨表原子可见） | **NOT PROVIDED** | Step 72 |
| 写入中读取 | partial state 合法；只保证 isolation / source_id / contract validity | Step 72 |

---

## 2. Request ID Semantics（**三个概念，禁止合并**）

```text
① Assistant Request ID          = 一次 /api/ai/chat 请求的身份（UUID）
    LLM 表列名：assistant_request_id
    Tool / RAG 表列名：request_id（**语义相同**，列名历史不同）
    Outcome 表列名：assistant_request_id（UNIQUE）

② Provider Request ID           = 一次 **LLM 调用** 的身份（chatcmpl-… 等）
    只存在于 llm_usage_record.request_id
    Trace 的 llm_usage[].request_id 暴露它（Step 37 语义）
    **Timeline 不暴露它**（Timeline 事件 9 字段里没有 request_id）

③ source_id（仅 Timeline）      = 该事件**来源表**的数据库主键（BIGINT）
    llm_events[].source_id      → llm_usage_record.id
    tool_events[].source_id     → tool_execution_record.id
    rag_events[].source_id      → rag_execution_record.id
    outcome_event.source_id     → assistant_outcome_record.id

Trace 的 Tool / RAG 段 **不暴露** DB 主键（Step 48 决定）：
    一致性只能按「数量 + 内容 + 顺序」验证（Step 71 已实测）。
```

---

## 3. Partial State Contract

```text
HTTP 200 ≠ 完整最终快照

合法示例（Step 72 实测，两个视图一致）：
    LLM 已写入、Outcome 尚未写入
        Trace   : llm_usage=1 · tool_executions=0 · rag_executions=0 · outcome=null
        Timeline: llm_events=1 · tool_events=0 · rag_events=0 · outcome_event=null

禁止（**一律不得推断**）：
    LLM 存在        → SUCCESS
    Tool success    → SUCCESS
    RAG result_count>0 → SUCCESS / result_count=0 → EMPTY
    HTTP 200        → SUCCESS
```

## 4. Outcome Contract

```text
枚举（只有四个，无 error_class）：SUCCESS · FAILED · REFUSED · EMPTY
唯一事实来源：ai_ops.assistant_outcome_record（request-level 终态，UNIQUE）
无记录        → Trace.outcome = null 且 Timeline.outcome_event = null
first-write-wins（重复写入 DO NOTHING；不引入状态机）
持久化失败    → 只 warning，**不**影响业务结果（HTTP / error body 不变）
语义独立      → LLM / Tool / RAG / Executor 的 success 均**不等于** Assistant outcome
```

## 5. Source ID Contract

```text
source_id = 当前 persistence record 的数据库主键。

它**不是**：event_id · sequence · 全局唯一时间线 ID · 排序号 · 数组下标 · UUID。
不得为当前 baseline 添加任何新的 ID（Step 65：event_id = NOT AVAILABLE）。
Timeline 事件不含 `request_id`；Trace 事件不含 `source_id`。
```

## 6. Ordering Contract

```text
LLM      : created_at ASC, id ASC      （Step 37 读路径排序键）
Tool     : id ASC                      （落库顺序）
RAG      : id ASC
Outcome  : 至多 1 条

**当前系统不提供跨 LLM / Tool / RAG / Outcome 的全局时间排序。**
文档与实现中不得用 first / second / third / chronological 描述跨组事件；
HTTP 层不得合并四组、不得生成 sequence。
钟域事实（Step 65）：LLM / Outcome 用 **DB 钟**（created_at = 落库时刻）；
                     Tool / RAG 用 **App 钟**（started_at / finished_at / duration_ms）
                     ⇒ 跨钟域比较 = UNSAFE_TO_DERIVE。
```

## 7. Read During Write Contract

```text
写入过程中读取 → **partial state 合法**（Step 72）
不保证：snapshot consistency（Trace 与 Timeline 在任意瞬间完全一致）
不保证：跨表原子可见
只要求：request isolation · source_id correctness（真实存在且组内不重复）
        · contract validity（字段 / 类型 / 枚举 / 无虚假完整性）
写入中两个视图数量**不**要求相等（两次独立读取，中间可能落库）
```

## 8. Security Contract

```text
Trace / Timeline JSON 均不得出现：
    prompt · messages · system_prompt · user_prompt · sql · query · content
    · embedding · similarity · arguments · tool_result · raw_response
    · exception · traceback · api_key · authorization · password · database_url

字段名级判定（`"<name>":`）+ 值/哨兵级判定（postgresql:// · sk- · Bearer · traceback）
注意：Trace 的 prompt_tokens / completion_tokens 是**合法** token 计数（Step 37），
      RAG 的 result_count 亦为合法观测字段 —— 不得被子串匹配误判。

unknown request 不得泄露其他 request 数据（响应只回显入参 id）。
错误响应（400 / 422 / 502 / 500）只含固定 detail，不含 DB 异常文本 / SQL / 路径 / 凭据。
```

---

## 9. 基线如何被回归守卫

```text
tests/test_assistant_trace_timeline_contract.py（Step 73 新增；offline · DB=0 · network=0）
    · DTO 字段集合 / 身份字段缺失 / 枚举取值 / source_id 类型
    · JSON Schema（DTD）无分页与身份字段、无敏感字段名
    · 路由契约（路径 / 方法 / 声明状态码 / 无分页查询参数）
    · partial state 与钟域互斥（DTO 级）
    · ordering 仅在组内（静态检查：无 enumerate / 无跨组 merge）
    · 本文件的必需章节与矩阵行存在（文档漂移守卫）
    · 自身 import 的 AST 审计（无 sqlalchemy / psycopg / backend.app.db / redis / kafka）
```
