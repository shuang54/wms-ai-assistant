# Phase 3.12 Step 50 — Assistant Trace Read Scalability Design

> **Design / Audit only**：未实现分页 / 上限 / cursor；未改
> `AssistantTraceResponse` / `AssistantTraceQueryService` / Repository /
> DB Schema / 索引；未引入缓存或新基础设施。Production Code = 0。
>
> 依据：真实代码（Step 48 之后的当前状态）。Step 49 已判定
> Unified Timeline = DEFER，本阶段不再讨论统一时间线。

---

## 1. Current State

```text
GET /api/observability/assistant-trace/{assistant_request_id}
        ↓ 一次 HTTP 请求 = 三次独立只读 SQL（无 JOIN / 无 N+1）
    ├── LLMUsageQueryService.list_by_assistant_request_id(A)
    ├── ToolExecutionPersistentQueryService.list_by_request_id(A)
    └── RagExecutionPersistentQueryService.list_by_request_id(A)
        ↓
AssistantTraceResponse{ assistant_request_id, llm_usage[], tool_executions[],
                        rag_executions[] }
    · 无 page / page_size / cursor / limit 参数（路径参数只有 assistant_request_id）
    · 空段 → []（200）；DB 失败 → 502；非法输入 → 400 / 422
```

## 2. Read Path（真实 SQL 形态）

| 段 | SQL 形态 | 排序 | LIMIT/OFFSET | 事务 |
| --- | --- | --- | --- | --- |
| LLM | `SELECT id, assistant_request_id, request_id, provider, model, … FROM ai_ops.llm_usage_record WHERE assistant_request_id = :p ORDER BY created_at ASC, id ASC` | 稳定（时间 + 主键 tie-break） | **无**（源码注释：分页属未来阶段） | 读（无 `begin()`） |
| Tool | `SELECT id, request_id, round, … FROM ai_ops.tool_execution_record WHERE request_id = :p ORDER BY id ASC` | 稳定（落库顺序 = 执行顺序） | **无** | 读 |
| RAG | `SELECT id, request_id, started_at, … FROM ai_ops.rag_execution_record WHERE request_id = :p ORDER BY id ASC` | 稳定（落库顺序） | **无** | 读 |

```text
共同点：显式列（无 SELECT *）· bound parameters（无字符串拼接）·
        过滤下推 PostgreSQL（不在 Python 里全表过滤）·
        Row → 内部/Trace DTO 显式映射（ORM 对象不出 db 层）·
        无 lazy loading / 无关联加载 / 无 N+1。
```

## 3. Record Growth Analysis（当前架构下一个 request_id 最多多少行）

```text
RAG route（Router 规则命中 RAG）：
    rag_executions = 1        （一次 execute → 一条 RAG 观测；无 retry）
    llm_usage      ≤ 1        （RAG 内部一次 LLM 调用；规则命中时不触发 Router fallback）
    tool_executions = 0

Tool route（Router 规则命中 TOOL）：
    tool_executions = 1       （Orchestrator：one execute = one Tool；round=1）
    llm_usage      ≤ 1        （仅 Router LLM fallback：规则未命中时才会调用）
    rag_executions = 0

Text-to-SQL route：
    llm_usage      ≤ 4        （TextToSQLService DEFAULT_MAX_ATTEMPTS = 3，钳制 [1,10]
                                + 可能的 Router fallback 1）
    tool_executions = 0 · rag_executions = 0

特例（非 Assistant 链路，但同一张 Tool 表）：
    ToolChatService（/api/chat/with-tools）自有一个 request_id，
    一次 chat 内多轮共享 → 若用该 id 查 Trace，tool_executions ≤ MAX_TOOL_ROUNDS（5）；
    其 LLM usage 的 assistant_request_id = NULL（不在 trace scope 内）→ 不参与匹配。

⇒ 当前**单个 request_id** 的行数上界约：LLM ≤ 4 · Tool ≤ 1（或 ≤5）· RAG ≤ 1
   合计约 ≤ 6~10 行；单行体积数百字节 → 响应体 KB 级。
⇒ 表的增长主要来自**请求数量**（O(requests)），不是单请求行数。
```

## 4. Current Query Bounds（是否存在 unbounded query）

```text
三个 Repository 的 Trace 查询均：
    无 LIMIT · 无 OFFSET · 无 cursor · 无固定 max records

⇒ 记录现状：
    Current Read Boundary: **unbounded by request_id**
    （没有 SQL 层上限；上界来自架构：单路由 + 有限 retry，不是来自查询）
```

## 5. Pagination Options（仅比较，不实现）

### Option A：固定上限（内部 MAX_RECORDS）

```text
做法：三个读边界各自 LIMIT N（如 100），响应不变或增加 additive 的 truncated 标记
优点：最简单；不引入新查询参数；兼容现有客户端；把"最坏情况"变成常数
缺点：超限后静默丢数据（必须配合显式 truncated 标记，否则是隐性数据丢失）；
      不能真正"翻页"
适用：作为**防御性护栏**（safety cap），不是分页方案
```

### Option B：`page` + `page_size`

```text
做法：每个段各自 ?page=&page_size=（或整条 Trace 统一分页）
代价：① API contract 变化（新增查询参数 + 可能的响应字段）；
      ② Repository 需要 LIMIT/OFFSET + 稳定排序（排序已具备）；
      ③ 三段语义独立 → 要么引入 3 组参数（复杂），要么统一分页
         （与 Step 49"三段独立语义、无统一时间线"冲突）；
      ④ OFFSET 深翻页在大表上成本高
结论：与当前"每请求数行"的现实不匹配，成本 > 收益
```

### Option C：cursor 分页

```text
可行性检查（关键）：
    LLM：DTO **暴露** id（LLMUsageTraceResponse.id），created_at 也在 → 技术上可构造
         (created_at, id) cursor
    Tool / RAG：Trace DTO **刻意不含**数据库主键（Step 48 安全裁剪）
         → 无法构造稳定 cursor，除非重新暴露 DB id
    ⇒ 红线：不能为了 cursor 方便把数据库主键重新暴露给 API（Step 50 §六 明确禁止）
结论：**不可接受**（与既有安全裁剪冲突）
```

## 6. Sorting

```text
LLM：created_at ASC, id ASC —— 稳定（时间 + 主键 tie-break；id 仅用于排序稳定性）
Tool：id ASC —— 稳定（落库顺序 = 执行顺序）
RAG ：id ASC —— 稳定（落库顺序）
原则：内部可继续使用 DB id 作为稳定排序键，**但不得把 id 放进 HTTP Response**
      （Tool / RAG 已遵守；LLM 侧 id 是既有契约，不改）
任何分页都必须保持上述排序语义（不允许按 JSON 大小 / 插入时间近似值重排）
```

## 7. Cursor Safety

```text
· cursor 必须由**已暴露的安全字段**构成，不能依赖未暴露的主键
· Tool / RAG 当前没有可暴露的稳定唯一键 → 无安全 cursor
· 若未来必须要 cursor：应先设计"安全排序键"（例如显式暴露的序号字段），
  而不是回退到暴露数据库主键
· 本阶段结论：不引入 cursor；不暴露主键
```

## 8. Security Boundary

```text
可暴露（不变）：assistant_request_id · timestamps · duration_ms · counts ·
    provider · model · token usage · tool_name · round · success ·
    error_code / error_type · RAG counts · chunk_ids · document_ids ·
    （LLM 侧既有 id 属历史契约，不改）
禁止暴露：query / question · answer · prompt · messages · raw_response · SQL ·
    embedding · similarity · password · API key · database_url · authorization ·
    ORM · Session · traceback · **新增的数据库主键**
分页相关红线：
    ① 不得为 cursor 重新暴露数据库主键；
    ② 分页字段本身（page / cursor / total）不得携带任何业务数据；
    ③ truncated / total 等元数据也走显式映射
```

## 9. Performance Analysis

```text
1. 三次独立 SQL（无 JOIN / 无 N+1 / 无 lazy loading）✓
2. 索引现状（真实）：
      Tool：ix_tool_execution_record_request_id ✓
      RAG ：ix_rag_execution_record_request_id ✓
      LLM ：**没有** assistant_request_id 索引
            （现有：ix_llm_usage_record_created_at、uq_llm_usage_record_request_id
              partial unique —— 后者键是 Provider request_id）
      ⇒ LLM 段按 assistant_request_id 过滤目前**没有索引支撑**，
        随 llm_usage_record 增长有全表扫描风险 —— 这是当前**唯一实质**的
        扩展性风险点（不是分页问题）
3. 显式 SELECT 列 ✓；Row → DTO 显式映射，ORM 不出 db 层 ✓
4. JSONB：仅 chunk_ids / document_ids（数值 ID 小数组；无正文）→ 单行体积可控
5. 读路径不开启写事务 ✓
6. 不引入：Redis / cache / Elasticsearch / OTel / Event Bus / Kafka（本阶段禁止）
```

## 10. API Compatibility

```text
方案 1（若未来做）：保持 AssistantTraceResponse 结构，内部固定 MAX_RECORDS，
    超限通过 **additive** 标记（如 truncated）表达 → 旧字段不变，向后兼容
方案 2（若未来做）：新增 pagination DTO 或每段独立分页参数
    → 明显扩大 API contract；且"整条 Trace 统一分页"与三段独立语义冲突
本阶段：两者**均不实现**（DTO 不变）
```

## 11. Recommendation

```text
**DEFER**（不实现分页 / 上限 / cursor）

理由：
  ① 单 request_id 行数被架构天然约束（单路由 + T2S max_attempts=3）：
     实测上界 ~6~10 行、KB 级响应 —— 没有现实的分页需求；
  ② cursor 与"Tool/RAG 不暴露主键"冲突 → 只能牺牲安全裁剪才能做；
  ③ page/page_size 与 Step 49 的"三段独立语义"冲突，且深翻页成本更高；
  ④ 提前引入 pagination 会扩大 contract 与测试面，却换不到实际收益；
  ⑤ 真正值得关注的不是分页，而是 **llm_usage_record.assistant_request_id 缺索引**
     —— 建议作为**独立的最小后续步骤**（加索引，不改 API / 不改代码语义）。

若未来必须分页：优先 Option A（内部固定上限 + additive truncated 标记），
    其次"每段独立"的 Option B；**永不**采用依赖数据库主键的 cursor。
```

## 12. Future Trigger

```text
任一项成立时重新评估分页：
    · 一次 Assistant 请求可能产生 > ~10 行（多步 / Agent / 并行链路出现）；
    · 单响应体经常超过 ~64 KB（例如 RAG chunk_ids 大幅增长）；
    · UI 需要"翻页查看某次请求的全部 Tool / LLM 记录"；
    · Tool / RAG 侧出现多轮 / 重试，导致单请求行数不再恒定。
索引（独立于分页）的触发条件：
    · llm_usage_record 行数增长到全表扫描不可接受（运维可测）
      → 最小动作：为 assistant_request_id 增加索引（不改 API / 不改 DTO）。
```

## 13. Verification

```text
python -m compileall -q backend tests → OK（0 errors）
production code = 0 修改 · DB schema = 0 · 索引 = 0 · API contract = 0 · Runtime = 0
```

## 14. Test Design（未来若实施，应覆盖；本阶段不新增测试）

```text
未来实施分页/上限时应覆盖：
    empty trace · single record · multiple records · exact page boundary ·
    over page boundary · stable ordering（LLM created_at,id / Tool,RAG id ASC）·
    cross-request isolation · invalid page · invalid page_size · max page_size ·
    database failure（502，不是 []）· security fields（无主键 / 无敏感值）

Existing coverage（当前已有，不重复写）：
    tests/test_assistant_trace_api.py（空 Trace 200 / 排序保持 / 不混用 /
        502·500·400 / 安全键 / OpenAPI schema）
    tests/test_assistant_trace_query_service.py（组合语义 / 校验先于下游 /
        Read Model 白名单 / 无写能力）
    tests/test_assistant_trace_rag_integration.py（RAG 段：empty / ordering /
        correlation / failure / security / composition / backward compat）
    tests/test_assistant_trace_rag_integration_e2e_db.py（真实 DB：RAG only /
        LLM+RAG / Tool only / empty / A·B isolation / 仓储失败 → 502）
    tests/test_assistant_trace_correlation_e2e_db.py（LLM+Tool 真实链路 correlation）
    tests/test_assistant_trace_api_db.py / test_assistant_trace_persistent_tool_db.py
        （DB：空 Trace / 跨请求隔离 / 持久化边界）
```
