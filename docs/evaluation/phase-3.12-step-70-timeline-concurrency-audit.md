# Phase 3.12 Step 70 — Assistant Timeline 并发隔离与一致性 Audit

> 只审计 / E2E 验证：**未改**任何生产代码、DB schema、persistence 或 API contract。
> 真实 PostgreSQL + 真实 API + 真实 Query Service；Fake LLM 仅替换传输层（零网络）。
> 测试：`tests/test_assistant_timeline_concurrency_db_e2e.py`（14 项 DB-gated）。

---

## 1. 并发模型

```text
httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) + asyncio.gather(...)
    → 单个事件循环内**真并发**调用 ASGI app（非串行 for 循环）
    → 真实 persistence（LLM usage / Tool / RAG / Outcome）→ 真实 PostgreSQL
    → 真实 AssistantTimelineQueryService → 真实 API DTO

Fake LLM：OpenAICompatibleClient + httpx.MockTransport（**不调用** DeepSeek）
唯一性约束（测试替身要求）：
    llm_usage_record.request_id 上有 UNIQUE partial index（ON CONFLICT DO NOTHING），
    因此每个并发请求的 provider request_id 必须**全局唯一**，否则只有首条落库
    （这是测试替身约束，不是生产缺陷）。
Text-to-SQL 重试判定：由 **prompt 是否携带上一次 SQL** 判定（不依赖调用计数），
    因此并发交错下每个请求仍各自经历 attempt1(bad) → attempt2(good)。
规模：5 并发 + 10 并发（**不是**压力/基准测试）。
```

## 2. 四类 ID 的真实关系（只读确认，未修改）

```text
assistant_request_id  Orchestrator 生成（UUID）→ 四张表的关联键
                      （LLM 列名就是 assistant_request_id；Tool/RAG 列名为 request_id
                       但语义相同；Outcome UNIQUE）
request_id(LLM)       **Provider** 请求 ID（chatcmpl-… / step70-…）→ 与 assistant
                      request_id 是两个维度，互不覆盖；**不出现在** Timeline 响应
provider request_id   = 上一条（LLM Usage 表内），即测试中的 step70-* 值
source_id             Timeline 事件身份 = 对应来源表的 **BIGINT 主键**
                      （llm/tool/rag/outcome .id）—— 非 uuid / 非下标 / 非 sequence
```

## 3. Case A — 5 个 RAG 并发

```text
5× POST（并发）→ 5 个互异 assistant_request_id，HTTP 全 200
GET timeline（并发）→ 每个：llm_events=1 · rag_events=1 · tool_events=0 ·
                     outcome=SUCCESS；事件 assistant_request_id 全部自洽
```

## 4. Case B — RAG + Tool 混合并发（R1,T1,R2,T2,R3）

```text
RAG 请求：llm=1 · rag=1 · tool=0 · SUCCESS
Tool 请求：llm=0 · rag=0 · tool=1 · SUCCESS
跨类型断言：tool source_id 集合 ∩ rag source_id 集合 = ∅；
           RAG Timeline 无 tool 事件，Tool Timeline 无 rag 事件
```

## 5. Case C — 并发 T2SQL Retry（3 请求）

```text
每个请求：llm_events=2 · tool=0 · rag=0 · outcome=SUCCESS
组内顺序 == DB (created_at, id) 升序
**不**断言 attempt=1/2（Timeline 无 attempt 字段）；DB LLM 行数 == 2
```

## 6. Case D — 并发 Refusal（3 请求）

```text
每个请求：llm_events=1 · tool=0 · rag=0 · outcome=REFUSED；metadata.refused=true
DB 侧：tool_execution_record=0（Validator/Executor 未执行）· rag=0 · llm=1
隔离：其它请求的 assistant_request_id 不出现在任一 Timeline；
      provider request_id（step70-refuse-llm-*）不出现在任何 Timeline 响应
```

## 7. 10 并发

```text
5 RAG + 5 Tool 同时 → 10 个互异 request_id；每个 Timeline 的 source_id 集合
与其它所有请求**两两不相交**（无 PK 复用 / 无串线）
```

## 8. Cross-request Isolation（HTTP + DB）

```text
HTTP：每个 Timeline 的所有事件 assistant_request_id == 当前 id；
      source_id ∈ 本请求 DB PK 集合；其它请求 id 不出现在该 Timeline JSON
DB  ：按 assistant_request_id / request_id 分组比对 ——
      A ∩ B = ∅ 对 LLM / Tool / RAG / Outcome **四张表分别成立**
```

## 9. Count Consistency

```text
RAG    ：DB llm == timeline.llm_events · DB rag == rag_events ·
         DB tool == tool_events · DB outcome == 1
Tool   ：DB llm=0 · rag=0 · tool=1 · outcome=1
T2SQL  ：DB llm=2 · tool=0 · rag=0 · outcome=1
Refusal：DB llm=1 · tool=0 · rag=0 · outcome=1
（全部按 DB 实际行数与 API 段长度逐项比对，不写死期望值之外的假设）
```

## 10. Read Consistency / No Cross-request Mutation

```text
同一 request 连续两次 GET → JSON 结构完全相等（timeline_1 == timeline_2）
deepcopy(A) → GET(B) → 再 GET(A)：A 与快照一致（读 B 不会改变 A）
```

## 11. Source ID Isolation

```text
每个事件 source_id：isinstance(int) 且 ∈ 本请求 DB 主键集合
非 UUID / 非 hash / 非数组下标 / 无 sequence 字段
```

## 12. Ordering

```text
LLM：created_at ASC, id ASC（并发下仍由 DB 排序键保证，与 _llm_ids 顺序一致）
Tool / RAG：id ASC；Outcome：至多 1 条
**不**要求跨请求全局顺序（无 sequence contract）—— 审计明确不做此断言
```

## 13. Security Regression

```text
并发产生的全部 Timeline JSON 扫描：prompt / messages / system_prompt / user_prompt /
sql / query / content / embedding / similarity / arguments / tool_result / api_key /
authorization / password / database_url / postgresql:// / sk- / Bearer /
raw_response / traceback / exception / 内部哨兵 / 测试 chunk 正文 —— **全部不存在**
sentinel（assistant_request_id）隔离：A 的 id 不出现在 B 的 Timeline
```

## 14. Failure Isolation

```text
并发：[RAG 成功 A] + [RAG 上游失败 B] + [Tool 成功 C] → HTTP [200, 500, 200]
A / C Timeline 不受影响（SUCCESS + 各自段完整）
B 的终态 = FAILED，且不污染 A / C（B 的 request_id 不出现在 A / C 的 Timeline）
失败请求通过 outcome 行差集定位（HTTP 500 不返回 request_id）
```

## 15. Cleanup / Residue

```text
fixture teardown：定向 DELETE（四张表，按 tracked request_id + step70- 前缀）
                 + 清空 Tool / RAG Runtime Collector
                 + 断言 step70 残留 == {llm:0, tool:0, rag:0, outcome:0}
不使用 TRUNCATE / DELETE all / DROP；不触碰历史数据
最终实测：四张表均为 0 行
（审计过程中调试探针曾留下 rag=7 / outcome=3 行，已按 id 定向删除并复核为 0）
```

## 16. 发现的问题

```text
无生产 bug（并发隔离与一致性全部符合当前 Contract）。
仅记录测试替身层面的约束（非生产问题）：
    * 并发测试中 LLM usage 的 provider request_id 必须唯一，否则 UNIQUE partial
      index 的 ON CONFLICT DO NOTHING 会静默丢行（生产语义即如此，是有意设计）
    * Text-to-SQL 重试判定若依赖"全局调用计数"，在并发交错下会把某请求的首轮
      误判为重试 → 测试改用 prompt 内容判定（生产无此问题）
```

## 17. Limitations

```text
* 单进程 / 单事件循环并发（ASGI + asyncio.gather）；未覆盖多进程 / 多 worker /
  多副本并发写入与读取
* 未做负载 / 压力 / 基准测试（仅 5 与 10 并发）
* 连接池容量、锁等待、复制延迟未评估
* Fake LLM 只覆盖 OpenAI 兼容响应形状；未覆盖真实超时 / 429 / 流式
* 失败路径仅覆盖 RAG 上游失败一种（未并发覆盖 T2SQL 预算耗尽等）
* 本阶段未运行 full pytest（按任务书只跑并发 DB 测试 + compileall）
```
