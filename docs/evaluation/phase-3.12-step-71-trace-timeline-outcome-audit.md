# Phase 3.12 Step 71 — Assistant Trace / Timeline Outcome 一致性 Audit

> 只审计 / Contract 验证：**未改**任何生产代码、DTO、QueryService、persistence、
> DB schema 或 API contract。发现真实 bug 会**停止并报告**（未发现）。
> 测试：`tests/test_assistant_trace_timeline_outcome_consistency.py`（17 DB-gated；
> 离线全 SKIP）。

---

## 1. 审计问题

```text
同一个 assistant_request_id 上：
    GET /api/observability/assistant-trace/{id}      → outcome
    GET /api/observability/assistant-timeline/{id}   → outcome_event.status
二者是否表达**同一个业务终态**？两个视图的事件集合（数量 / 身份 / 顺序）是否一致？
判定依据是 **outcome persistence**（ai_ops.assistant_outcome_record），
**不是** HTTP status。
```

## 2. 结果矩阵（真实 PostgreSQL）

| 场景 | Trace.outcome | Timeline.outcome_event.status | 一致 |
| --- | --- | --- | --- |
| RAG success | SUCCESS | SUCCESS | ✅ |
| RAG empty（空检索） | EMPTY | EMPTY | ✅ |
| Tool success | SUCCESS | SUCCESS | ✅ |
| Tool failure（HTTP 200） | FAILED | FAILED | ✅ |
| T2SQL success | SUCCESS | SUCCESS | ✅ |
| T2SQL retry success | SUCCESS | SUCCESS | ✅ |
| T2SQL execution failure（HTTP 500） | FAILED | FAILED | ✅ |
| Refusal | REFUSED | REFUSED | ✅ |
| RAG upstream failure（HTTP 500） | FAILED | FAILED | ✅ |
| 历史缺失 Outcome | null | null | ✅ |

```text
关键反例已覆盖：
    * Tool failure：HTTP **200** 但 outcome=FAILED（不因 200 判 SUCCESS）
    * RAG empty：HTTP 200 但 outcome=EMPTY（以 persistence 为准，不从 200 推导）
    * 历史请求：无 outcome 行 → 两侧均为 null（不推断 SUCCESS）
```

## 3. Outcome Source ID（§十五）

```text
Trace.outcome                      → 纯枚举字符串，**无** source_id（DTO 不含该字段）
Timeline.outcome_event.source_id   → int，且 == assistant_outcome_record.id（DB 实测）
两个视图的 outcome 语义同源：均来自 assistant_outcome_query_service → 同一张表
```

## 4. Count / Ordering / 身份一致性（§十七 ~ §十九）

```text
数量：Trace.llm_usage == Timeline.llm_events；tool/rag 同理；
      (Trace.outcome != null) ↔ (Timeline.outcome_event != null)
身份：
    * LLM：Trace.llm_usage[].id **逐一等于** Timeline.llm_events[].source_id
           （Trace 的 LLM 段暴露 DB 主键，Step 37 决定）
    * Tool / RAG：Trace **不暴露**主键（Step 48 决定）→ 只能按数量 + 内容 +
      顺序比对；Timeline 侧 source_id == 真实 DB 主键（int）
顺序：LLM 两个视图顺序一致（created_at, id）；
      Tool / RAG 顺序一致（started_at / success 逐项比对）
      **不**要求跨组全局排序（无 sequence contract）
```

## 5. Cross-request Consistency（§十六）

```text
A=RAG(SUCCESS) · B=Tool(SUCCESS) · C=Refusal(REFUSED) · D=T2SQL(SUCCESS)
每个请求 Trace.outcome == Timeline outcome；4 个 request_id 互异；
任一响应的文本中不出现其它请求的 request_id（无串线）
```

## 6. Security（§二十）

```text
同时扫描 Trace JSON 与 Timeline JSON：
    字段名级（`"xxx":`）：prompt / messages / system_prompt / user_prompt / sql /
        query / content / embedding / similarity / arguments / tool_result /
        raw_response / exception / database_url / result / answer —— 均不存在
    值 / 哨兵级（子串）：api_key / authorization / password / postgresql:// /
        sk- / Bearer / traceback / 内部哨兵 / chunk 正文 / SQL 文本 —— 均不存在
失败请求的 HTTP error body 仅 {"detail": ...}，不含内部异常文本
注：Trace 的 ``prompt_tokens`` / RAG 的 ``result_count`` 是**合法**观测字段，
    审计改用 JSON key 形式判定，避免子串误报。
```

## 7. API Contract Lock（§二十一）

```text
AssistantTraceResponse：assistant_request_id / outcome / llm_usage /
                        tool_executions / rag_executions（5）
AssistantTimelineResponse：assistant_request_id / llm_events / tool_events /
                           rag_events / outcome_event（5）
AssistantTimelineEventResponse：9 字段
Trace schema 与 Timeline schema 均无：timeline / events / sequence / event_id /
                                     trace_id / span_id
```

## 8. Cleanup / Residue

```text
fixture teardown：定向 DELETE（四张表，tracked request_id + step71- 前缀）
                 + 清空 Tool / RAG Runtime Collector
                 + 断言 step71 残留 == {llm:0, tool:0, rag:0, outcome:0}
无 TRUNCATE / DELETE all / DROP；最终实测四张表均为 0
```

## 9. 发现的问题

```text
生产 bug：**无**。两个视图在所有场景下表达一致的终态与事件集合。

Contract 事实（**不是**缺陷，记录以免后续误判）：
    * Trace 的 Tool / RAG 段**不**暴露数据库主键（Step 48 有意为之），
      因此"Trace[].id == Timeline[].source_id"只对 **LLM 段**成立；
      Tool / RAG 的一致性只能按数量 + 内容 + 顺序验证。
    * Trace 的 LLM 段含 ``prompt_tokens`` / ``completion_tokens``（token 计数），
      不是 prompt 文本；安全扫描需按 JSON key 判定。
    * 失败请求（HTTP 500）不返回 outcome / request_id —— 审计通过 outcome 行
      差集定位 request_id，再读取两个视图（HTTP error contract 未改）。
```

## 10. Limitations

```text
* 场景为**串行**构造（本阶段关注一致性而非并发）——并发一致性见 Step 70
* T2SQL 执行失败用 Fake Executor 抛错模拟；未覆盖真实 PG 执行失败
* RAG upstream failure 用检索层抛错模拟；未覆盖 LLM 传输层真实故障
* 历史缺失 Outcome 通过"删除 outcome 行"模拟（非真实历史数据）
* 未比较两个视图的响应体积 / 延迟 / 缓存行为
* 本阶段未运行 full pytest（按任务书只跑本文件 + compileall）
```
