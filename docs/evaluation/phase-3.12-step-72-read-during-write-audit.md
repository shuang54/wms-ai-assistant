# Phase 3.12 Step 72 — Trace / Timeline 写入中读取一致性 Audit

> 只审计 / E2E 验证：**未改**任何生产代码、DTO、QueryService、persistence、
> DB schema 或 API contract。发现真实 bug 会停止并报告（未发现生产 bug）。
> 测试：`tests/test_assistant_trace_timeline_read_during_write.py`（15 DB-gated；离线全 SKIP）。

---

## 1. 审计问题（明确不做什么）

```text
回答：当一个 Assistant Request 的 LLM / Tool / RAG / Outcome 正在陆续写入
      PostgreSQL 时，同时读取 Trace / Timeline，是否出现
          ① 跨 request 污染  ② 不存在的 source_id  ③ 重复 source_id
          ④ 违反当前 Contract 的状态
      并且最终稳定状态是否与 Step 71 一致。

**不**证明"读取到的是完整最终快照"：当前系统没有 event_id / sequence /
transaction_id / 全局排序 / 统一 started_at ⇒ **partial state 本身是合法的**。
```

## 2. 构造方式（未改生产代码、未加 test seam）

```text
直接用**真实 Repository** 按阶段写入真实持久化记录（与生产同一条 persistence 路径）：
    LLMUsageRepository().create(...)            → ai_ops.llm_usage_record
    RagExecutionRepository().create(...)        → ai_ops.rag_execution_record
    ToolExecutionRepository().create(...)       → ai_ops.tool_execution_record
    AssistantOutcomeRepository().create(...)    → ai_ops.assistant_outcome_record
每个阶段用真实 HTTP（TestClient）读取 Trace 与 Timeline 两个 Read API。

另设"后台线程写入 + 前台轮询读取"用例（threading.Thread）模拟真实写入中读取。
禁止并**未**使用：读 Session 内部事务 / 改 isolation level / SELECT FOR UPDATE /
TRUNCATE / DELETE all / DROP。
```

## 3. Case A ~ C：Partial State（Outcome 尚未写入）

```text
A. LLM only        → Trace.llm_usage=1 ↔ Timeline.llm_events=1；
                     Trace.llm_usage[].id == Timeline.llm_events[].source_id == DB PK；
                     tool/rag 两段均为空；outcome **null**（不推断 SUCCESS/FAILED/REFUSED）
B. LLM + RAG       → llm 数量一致 · rag 数量一致 · rag source_id == DB PK ·
                     started_at 两侧一致 · outcome 仍为 null
C. Tool only       → tool 数量一致 · Trace.success=true ↔ Timeline.status="success" ·
                     outcome 仍为 null（**不**因 Tool 成功推断 SUCCESS）
```

## 4. Case D ~ G：Outcome 已写入（稳定终态）

```text
D. SUCCESS（LLM+RAG+Tool+Outcome）→ Trace.outcome == Timeline.outcome_event.status
                                     == "SUCCESS"；source_id == 真实 outcome PK
E. FAILED（含 Tool success=false）→ 两侧 FAILED；Tool 段独立表达 failed；
                                     Outcome 不因前置事件缺失而改变
F. REFUSED（LLM 1 条 + Outcome）  → 两侧 REFUSED（不推断 SUCCESS / EMPTY）
G. EMPTY（RAG result_count=0、无 LLM）→ 两侧 EMPTY；llm_usage=[] 是当前真实语义
```

## 5. Case H：Cross-request Read During Write

```text
A=LLM only · B=LLM+RAG · C=Tool+Outcome(SUCCESS) —— 三者处于**不同持久化阶段**
同时读取三个 request 的两个视图：
    每个响应只含自己的 request_id；其它 request_id 不出现在响应文本
    source_id 集合两两不相交（无 PK 复用 / 无串线）
```

## 6. 实时读写交错（Live Read During Write）

```text
后台线程：LLM → (50ms) → RAG → (50ms) → Tool → (50ms) → Outcome(SUCCESS)
前台：每 20ms 读取一次 Trace + Timeline（最多 40 次），每次做完整 Contract 校验
本轮不要求两个视图数量相等（两次读取之间可能刚好落库 —— §十五 明确不要求 equality）

结果：每次读取都满足
    ① 无跨 request ② source_id 为 int 且 ∈ 该 request 的 DB PK（读取后查询，允许子集）
    ③ 组内无重复 source_id ④ outcome ∈ {null, "SUCCESS"}（不出现未写入的值）
    ⑤ 无 merged events / sequence / event_id
最终稳定状态 = 完整（llm1 + rag1 + tool1 + SUCCESS），观察到的累计事件数**单调不减**
（只增不减 ⇒ 无重复、无回退）
```

## 7. Read Consistency（稳定阶段）

```text
同一稳定阶段连续两次读取：Trace1 == Trace2 · Timeline1 == Timeline2（JSON 结构相等）
写入进行中**不**要求 equality（§十五）
```

## 8. Source ID Validation（§十六）

```text
所有 source_id：isinstance(int) 且非 bool / 非 str（排除 UUID）
               ∈ 该 request 对应表的 DB 主键集合（读取后查询）
               组内无重复
Trace 侧 LLM 主键 == Timeline.llm_events[].source_id（LLM 段 Trace 暴露主键）
Outcome：Timeline.outcome_event.source_id == assistant_outcome_record.id
```

## 9. Security（§十七）

```text
对所有 read-during-write 响应做 JSON 扫描：
    字段名级（`"xxx":`）：prompt / messages / system_prompt / user_prompt / sql /
        query / content / embedding / similarity / arguments / tool_result /
        raw_response / exception / database_url / result / answer —— 均不存在
    值/哨兵级：api_key / authorization / password / postgresql:// / sk- /
        Bearer / traceback / 内部哨兵 —— 均不存在
未知 request：Trace → outcome=null；Timeline → 200 + 四段空（既有契约，未改）
```

## 10. 发现的问题

```text
生产 bug：**无**。

测试实现层面的两个坑（已修正，记录以免复现）：
    ① 后台写入线程必须 **join 之后** 才能结束 fixture（否则 teardown 删完行，
       线程继续写入 → 残留）；本轮改为 try/finally + join。
    ② 写入进行中的两个视图是**两次独立读取**，若强求数量相等会产生 flaky；
       已按 §十五 的语义放宽（只在稳定阶段要求 equality）。
    ③ 误用断言：Trace 的 **LLM 段** ``request_id`` 是 Provider 请求 ID
       （Assistant 关联键是 ``assistant_request_id``）；Tool/RAG 段的
       ``request_id`` 才是 Assistant Trace ID —— 校验器已按此区分。
```

## 11. Limitations

```text
* 写入时机由测试控制（Repository 直接写入 / 后台线程），不是真实端到端并发流量
* 轮询间隔 20ms、写入间隔 50ms 属人为时序；未覆盖极端竞态（毫秒级同时）
* 未在"两个视图之间存在写入"时尝试断言相等（按设计不做 —— 当前无快照语义）
* 未评估连接池 / 锁等待 / 复制延迟对读取可见性的影响
* 未覆盖多进程 / 多 worker 的写入可见性（Step 70 仅覆盖单进程并发）
* 本阶段未运行 full pytest（按任务书只跑本文件 + compileall）
```
