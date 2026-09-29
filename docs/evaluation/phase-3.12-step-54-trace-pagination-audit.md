# Phase 3.12 Step 54 — Assistant Trace 数据量与响应上限 Audit

> **Audit / Design only**：未改 API contract · 未加 `page` / `page_size` / `cursor` /
> `LIMIT` / `OFFSET` · 未改 QueryService / Repository / DB Schema · 未新增 index。
> DB writes = 0（仅只读统计）；生产代码改动 = 0。
>
> 结论速览：**Pagination Status: DEFER** —— 三条数据源在**当前代码**下均为**有界**，
> 单 `assistant_request_id` 理论上限 **≤ 6 条记录（默认配置）**；
> 但已列出 6 条明确 Trigger，触发任一即需先设计分页再扩展 Trace API。

---

## 1. Current Trace API

```text
GET /api/observability/assistant-trace/{assistant_request_id}
    Path: min_length=1 · max_length=128（FastAPI Path 校验 → 422）

Response（3 顶层字段 + 3 个列表）:
    assistant_request_id : str（回显，逐字符一致）
    llm_usage[]          : 9 字段（**含** id）
    tool_executions[]    : 11 字段（无 DB 主键）
    rag_executions[]     : 13 字段（无 DB 主键）

分页相关字段：**无**（无 page / page_size / cursor / total / has_more / truncated / next）
空 Trace（三段皆 []）→ 200（合法，不是 404）
```

## 2. Current SQL

三个数据源**各自一次显式 SELECT**（三次查询，非 N+1、无 JOIN、无 lazy loading）：

| 段 | 查询位置 | SQL 形态 | 排序 | LIMIT/OFFSET |
| --- | --- | --- | --- | --- |
| LLM Usage | `LLMUsageRepository.build_trace_select()` ← `list_by_assistant_request_id()` | `SELECT id, assistant_request_id, request_id, provider, model, prompt_tokens, completion_tokens, total_tokens, created_at FROM ai_ops.llm_usage_record WHERE assistant_request_id = :p` | `created_at ASC, id ASC` | **无**（docstring 明确） |
| Tool | `ToolExecutionRepository.build_request_select()` ← `get_by_request_id()` | `SELECT <11 列> FROM ai_ops.tool_execution_record WHERE request_id = :p` | `id ASC` | **无** |
| RAG | `RagExecutionRepository.build_request_select()` ← `get_by_request_id()` | `SELECT <14 列> FROM ai_ops.rag_execution_record WHERE request_id = :p` | `id ASC` | **无** |

```text
显式 SELECT：✅ 三者均为 select(显式列)
JOIN：       ✗ 无（单表）
N+1：        ✗ 无（每段 1 次查询；组合层只调用 3 个 Query Service）
lazy loading：✗ 无（不用关系属性；只取列）
bound parameter：✅ 全部走 :param（无字符串拼接 / 无 text() 拼接）
LIMIT/OFFSET/cursor：✗ 均未使用（分页属未来阶段）
注意：同一仓储里的**历史分页查询**另有 LIMIT/OFFSET（LLM `list_records` 1~100、
      Tool `list_recent` 1~1000）——**与 Trace 路径无关**，Trace 不复用它们。
```

## 3. Record Bounds（每条上限的来源）

```text
一次 /api/ai/chat = 一次 execute() = **一种** 路由（无 Agent / 无 Loop / 无重规划；
                                              Orchestrator docstring 明确 "单次执行"）
request_id = new_request_id()（唯一生成点）→ assistant_trace_scope(request_id) 覆盖
Router + 全部路由内部 LLM 调用（Step 35 / 36）
```

## 4. RAG max records

```text
路径：_run_rag() → RagService.answer()（**1 次**）→ 1 条 RagExecutionObservation
      → 持久化 1 行 ai_ops.rag_execution_record
上限：**1 / request**（无重试 / 无 loop / 无 multi-query）
空检索同样产生 1 条（result_count=0）——空检索 ≠ 没有执行
RAG 路由不产生 Tool 记录（实测 0，Step 42/44）
```

## 5. Tool max records

```text
① /api/ai/chat（TOOL 路由）→ AIOrchestratorService._run_tool()
     round=1 固定 · "one execute = one Tool（无 round loop / 无 multi-step / 无 retry）"
     成功 / 失败均 emit 1 条（ToolExecutionService.emit_record）
     上限：**1 / request**

② /api/chat/with-tools → ToolChatService（**独立 request_id 生命周期**）
     max_rounds = settings.tool.max_rounds（默认 **5**，钳制 [1, 20]）
     上限：**max_rounds 条 Tool 记录 + max_rounds+1 次 LLM 调用**
     ⚠ 该 request_id 由 ToolChatService.new_request_id() 生成，**不进入
       assistant_trace_scope** → 它不是 assistant_request_id；
       其 LLM usage 若落库则 assistant_request_id 为 NULL（Trace 不匹配）
     ⇒ 不并入 §7 的 assistant_request_id 上限；但若客户端把该 ID 当
       assistant_request_id 查询，会看到 tool_executions（≤5）而 LLM 段为空
```

## 6. LLM max records

```text
每次 LLM provider 调用 = 1 条 usage（LLMClient._emit_accounting，**无内部 retry loop**）

Router：规则优先；规则无法判断 → LLM fallback **≤ 1 次**
        （settings.ai_router.llm_fallback_enabled 默认 **true**）
RAG：  1 次（仅当检索非空；空检索 → 0 次 LLM）
Tool（/api/ai/chat）：**0 次**（Tool 名称由 Router 决策；参数由正则 Extractor 提取；
        Tool Handler 全部不调用 LLM —— 已检索确认）
Text-to-SQL：≤ max_attempts 次（`TextToSQLService` 构造默认 **3**，钳制 [1, 10]）
        refusal 首次识别即返回（llm_calls=1）

单 /api/ai/chat 上限：
    RAG 路由           ≤ 1（router fallback）+ 1（rag）      = **2**
    TOOL 路由          ≤ 1（router fallback）                = **1**
    TEXT_TO_SQL 路由   ≤ 1（router fallback）+ 3（attempts） = **4**
```

## 7. Theoretical maximum

| Route | LLM Usage | Tool | RAG | 合计 |
| --- | --- | --- | --- | --- |
| RAG | ≤ 2 | 0 | 1 | **3** |
| TOOL | ≤ 1 | 1 | 0 | **2** |
| TEXT_TO_SQL | ≤ 4 | 0 | 0 | **4** |
| （任意路由）绝对上限 | ≤ 4 | ≤ 1 | ≤ 1 | **6** |

```text
maximum records per assistant_request_id（当前默认配置）= **6**
最坏配置（TextToSQLService 显式 max_attempts=10）：
    TEXT_TO_SQL = 1 + 10 = 11 → 绝对上限 **12**

是否存在 UNBOUNDED 项？ —— **否**（三项都有代码级上限）：
    LLM : clamp [1, 10] attempts + 1 router fallback
    Tool: /api/ai/chat = 1；ToolChat = clamp [1, 20] max_rounds（不同 ID 体系）
    RAG : 1
（不写入 100 / 1000 之类的"安全数字"：上表每格均来自上文的真实常量与调用结构）
```

## 8. Current DB volume

```text
只读实测（本地 docker-compose 开发库 · PostgreSQL 16.15）：
    ai_ops.llm_usage_record      rows = 0 · total_size = 32 kB
    ai_ops.tool_execution_record rows = 0 · total_size = 72 kB
    ai_ops.rag_execution_record  rows = 0 · total_size = 80 kB
    每 id 最大记录数（全库历史）：LLM 0 · Tool 0 · RAG 0

current dev volume = 0
（空库不能推导生产分布；本环境**没有**任何真实 Trace 数据可供统计每请求记录数分布）
```

## 9. Response size

```text
测量方式：**真实 Pydantic DTO**（api/assistant_trace.py）+ 合成字段值，
          序列化 model_dump_json() 计字节；**不是生产数据**，非 DB 取样。

单条：LLM 257 B · Tool 280 B · RAG 347 B（RAG 含 chunk_ids×5 + document_ids×2）

场景                                    记录数(llm/tool/rag)      体积
空 Trace                                0/0/0                    119 B  (0.12 KiB)
典型 RAG 请求                           2/0/1                    981 B  (0.96 KiB)
典型 TOOL 请求                          1/1/0                    656 B  (0.64 KiB)
典型 TEXT_TO_SQL 请求                   4/0/0                   1150 B  (1.12 KiB)
10 records                              3/3/4                   3125 B  (3.05 KiB)
50 records                              20/10/20               15046 B (14.69 KiB)
100 records                             40/30/30               29306 B (28.62 KiB)
100 records（单段）                     LLM 100 / Tool 100 / RAG 100
                                        = 25.31 / 27.56 / 34.10 KiB
极端 300 records（当下不可达）           100/100/100            88816 B (86.73 KiB)

当前可达上限（≤6 条）对应体积：< 1.5 KiB —— 距 64 KiB 量级尚有 2 个数量级余量
```

## 10. Hidden growth paths

```text
已检索并确认的循环 / 重试 / 回退点：
    ✅ Router LLM fallback —— 1 次（受 AI_ROUTER_LLM_FALLBACK_ENABLED 控制）
    ✅ Text-to-SQL attempts —— `for attempt in range(1, max_attempts+1)`，clamp [1, 10]
    ✅ ToolChat rounds —— `while` 形态，clamp [1, 20]（独立 request_id）
    ✅ Tool 执行 —— 无 retry（Registry 归一化失败为结果）
    ✗ 无 parallel execution（无 asyncio.gather 扇出 LLM / Tool）
    ✗ 无 Agent / multi-step / replanning（Orchestrator 单次执行）
    ✗ Tool Handler 不调用 LLM（已检索 tools/*）
    ✗ LLM Client 内部无 retry loop（每次 chat = 1 次 provider 调用）
    ✗ RAG 无 multi-query / 无 rerank 多次调用

结论：**当前不存在"随业务逻辑放大记录数"的隐藏路径**。
      唯一的放大因子是配置项（router fallback · text_to_sql max_attempts ·
      tool max_rounds），且都带 clamp。
      未修改任何上限（本阶段只记录现状）。
```

## 11. Option A — 固定上限 + truncated

```text
形态：llm_usage: max N · tool_executions: max N · rag_executions: max N
      + 响应增加 flags（如 truncated: true）
优点：实现最小（3 个 LIMIT + 1 个布尔）· 与当前单请求 Trace 语义一致 ·
      下行解析不受影响（additive）
缺点：**超过上限的部分无法继续读取**（不是分页，是截断）·
      需要引入"哪一段被截断"的表达（三个独立列表 → 三个 flag？一个总 flag？）
      · 一旦触发就是把接口能力永久降级（需另设计补读）
当前必要性：**不需要**（上限 6 条 < 任何合理 N）
```

## 12. Option B — page / page_size

```text
形态：?page=1&page_size=50
优点：直观 · 实现成本低（OFFSET/LIMIT）
缺点（本项目特有，**三个独立数据源**）：
    · 三段排序键不同（LLM created_at,id / Tool id / RAG id）→ "第 2 页"如何定义？
    · 每段独立分页 → 客户端要发 3 组请求（3 个 page 参数？参数名爆炸）
    · 单 page 覆盖三段 → 需人为主导"跨源切片"（合并语义本层明确拒绝：
      不合并 / 不统一 timeline）
    · 与"空 Trace 合法"、"顺序 = 下游稳定顺序"的既有契约冲突面大
    · 天然引导 OFFSET（见 §14 风险）
当前必要性：**不需要**
```

## 13. Option C — Cursor / Keyset Pagination

```text
当前事实（必须先解决的前置问题）：
    · 三段排序键不同：LLM `(created_at ASC, id ASC)` · Tool `id ASC` · RAG `id ASC`
    · `ToolExecutionTraceView` / `RagExecutionTraceView` **不暴露 DB 主键**
      （`LLMUsageTraceResponse` 恰好暴露 `id` —— 三段**不对称**）
    · 无统一事件 id / 无跨源稳定序（拒绝把三段合成 timeline）

若未来采用 cursor，需要先决定：
    1) 稳定排序键：每段各自的 (sort_key, tie_breaker)；LLM 已有 id；
       Tool / RAG 需要"暴露 id 或引入等价不透明键"（二者都不在当前契约内）
    2) 是否暴露 **opaque cursor**（base64 编码 + 服务端校验），
       而不是把 DB 主键直接写进 query string
    3) **是否**需要统一 event id（= 统一 timeline 的前置条件；
       本项目当前明确不做 → 若做 cursor 也必须**每段独立 cursor**）
    4) 每段独立 cursor 的交互语义（3 个 cursor 参数）
结论：**本阶段不设计 cursor contract**（前置条件不足，且当前无限界数据）
```

## 14. OFFSET risk

```text
PostgreSQL 官方语义：`OFFSET` 之前的行仍必须由服务器**计算并跳过**，
因此大 OFFSET 的代价随偏移量线性增长（不是"免费跳过"）。

本项目特定风险：
    · 三段列表长度在**当前**上限内极小（≤6），OFFSET 代价可忽略；
    · 但一旦引入 page/page_size，就等价于承诺 OFFSET 语义；
      若未来某段增长到千级（例如多轮 Tool / Agent 阶段），
      `OFFSET 1000` 会退化为"扫描并丢弃 1000 行" → 与 keyset 的差距放大；
    · Trace 查询已有**精确过滤键**（request_id / assistant_request_id），
      因此 keyset 的实现条件天然具备（有稳定 tie-breaker：id）。

明确记录：**不要因为"分页"这个词就默认使用 OFFSET**。
          增长后重新评估：OFFSET vs keyset/cursor（本阶段只记录，不实现）。
```

## 15. Pagination trigger（proposed engineering trigger）

> 以下数字除标注外均为 **proposed engineering trigger**（工程建议阈值），
> **不是**当前系统限制；当前系统限制见 §7（≤6 条 / < 1.5 KiB）。

```text
Trigger A  单 assistant_request_id > 10 records            （proposed；当前上限 6）
Trigger B  Trace JSON response > 64 KiB                    （proposed；当前 < 1.5 KiB）
Trigger C  客户端需要"浏览完整历史"（跨请求 / 跨会话列表）  （当前无此需求）
Trigger D  出现 multi-round Tool Calling（>1 round / request）——**已存在于
           /api/chat/with-tools**，但属独立 request_id 生命周期，不构成
           assistant_request_id 增长；若未来 /api/ai/chat 引入多轮 Tool → 触发
Trigger E  出现 Agent / loop / replanning（可重复路由 + 可重复 LLM）
Trigger F  出现真正统一 timeline（跨源合并 → 需要单一稳定序 + 统一事件 id）
Trigger G  单段列表出现 > 100 条（与项目既有分页上限 MAX_QUERY_LIMIT=100 对齐）
Trigger H  Trace 读路径 P95 延迟出现 P95 后端耗时（DB 扫描）显著上升
```

## 16. Security boundary（未来分页的设计要求，本阶段不实现）

```text
1) **分页不得改变作用域**：任何 page / offset / cursor 都必须与
   `assistant_request_id` **同源绑定** —— 查询恒为
   `WHERE assistant_request_id = :A`（三段同理 request_id = :A）；
   **禁止**出现"无 request 过滤的全局分页"（跨 request_id 读取 = 数据泄漏）。

2) **cursor 必须是 request 绑定 + 不透明**：
   · 若未来引入 cursor，其内容必须包含（或由服务端推导出）当前
     `assistant_request_id`，并在服务端校验与 URL 中的 ID 一致；
     不一致 → 拒绝（400），**绝不**用 cursor 里的 ID 覆盖 URL 的 ID；
   · 禁止把 DB 主键 / SQL 片段 / 表名以明文放入 cursor（避免存储细节泄漏）。

3) **不得放大字段**：分页参数不得引入 `fields`/`expand`/`include` 等
   选择器（会破坏当前三段白名单；Tool/RAG 目前**不暴露**主键，
   分页若需要 tie-breaker，需单独评审是否暴露）。

4) **越界与非法输入**：page/offset/cursor 非法必须**在触达 DB 之前**拒绝
   （沿用 Step 37/38/43 校验先于查询的既有原则），且错误信息不含 SQL / 表名。

5) **空结果语义不变**：任意一段耗尽 → `[]`，200（不是 404 / 不是错误）。

6) 三段仍**不合并**：分页不能成为引入"统一 timeline"的借口（Step 38 边界）。
```

## 17. Recommendation

```text
Pagination Status: **DEFER**

理由（全部来自 §3~§10 的真实代码证据）：
    · 三段数据源均有代码级上限；单 assistant_request_id 理论最大 **6 条**
      （最坏配置 12 条），不可能出现"大列表"；
    · 实测响应体积：典型 < 1.2 KiB，上限场景 < 1.5 KiB（64 KiB 阈值的 2%）；
    · 三段排序键不同 + 两段不暴露主键 → 现在设计分页会**先于需求**
      固化错误的契约（尤其 cursor）；
    · 数据库当前 0 行（dev），无任何真实分布可供选择 page_size / 阈值。

⇒ 现在不引入 page / page_size / cursor / LIMIT / OFFSET / truncated。

若 Trigger A~H 任一成立：**Pagination design should be scheduled before
further Trace API expansion**（先设计分页边界，再扩展 Trace API；
优先 Option A（截断 + 明确 flag）作为最小护栏，只有 Trigger C/F 成立时
才进入 Option C（每段独立 keyset cursor），且必须满足 §16 全部要求）。
```

## 18. Limitations

```text
* 上限推导基于**静态阅读 + 代码常量**（未做压力/实测）：未在真实多路由混合
  流量下验证；生产 sink 未接线（见下）意味着真实数据分布不可得
* **LLM usage 持久化未在生产装配**（`get_default_llm_client()` 未注入
  `DatabaseLLMAccountingSink`，默认 Noop）⇒ 生产默认 `llm_usage[]` 为空；
  本节 LLM 上限是"接线后"的上限（既有已知限制，本阶段未修改）
* 响应体积为**真实 DTO + 合成字段值**测量（非生产取样；chunk_ids 固定 5 个，
  实际随 top_k / 命中数变化）
* ToolChat（/api/chat/with-tools）的 request_id 与 assistant_request_id
  属不同命名空间，未做统一（本阶段仅记录）
* 未评估：客户端超时 / 网关 body 限制 / 压缩（gzip）对体积的影响
* 未评估真实 P95 延迟（Trigger H 目前无基线数据）
* 本阶段未新增测试、未改生产代码；分页方案均为**设计提案**，未实现
```
