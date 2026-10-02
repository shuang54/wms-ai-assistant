# Phase 3.12 Step 99 — Timeline Source ID 隔离断言修正

> 只修正**测试判据**；生产代码 / Gate / Adapter / Baseline / Workflow **全部未改动**。

---

## 1. 问题

```text
Step 98：GitHub Actions 首次真实运行（run_id 36859788376）
        ↓
全新 PostgreSQL（pgvector/pgvector:pg16）+ init_db
        ↓
FAILED tests/test_assistant_timeline_concurrency_db_e2e.py
       ::TestTimelineConcurrency::test_case_e_ten_concurrent_requests

    assert source_ids.isdisjoint(all_source_ids)   # 无跨请求 PK 复用
E   assert False
E    where False = {1}.isdisjoint({1, 2, 3, 4, 5, 6, ...})
```

本地开发库该用例一直 PASS（序列已被历史运行推进，恰好不重叠）⇒ **只在空库暴露**。

---

## 2. 根因

```text
测试错误地要求 source_id 跨
    LLM · Tool · RAG · Outcome
四张表**全局唯一**。

但空库上四张表的 id 序列均从 1 开始：

    llm_usage_record.id        = 1
    tool_execution_record.id   = 1
    rag_execution_record.id    = 1
    assistant_outcome_record.id = 1

按 Step 73 §八 Source ID Contract —— 这**完全合法**。
```

---

## 3. 正确 Contract（Step 73 冻结，未修改）

```text
① source_id = 对应 persistence record 的**source-table PK**
   不是 event_id · 不是全局唯一时间线 ID · 不是排序号 · 不是数组下标 · 不是 UUID

② source record identity = (source, source_id)
       ("llm_usage", 1) · ("tool_execution", 1) · ("rag_execution", 1)
       ("assistant_outcome", 1)
   → 四条**不同**的 source record

③ 跨请求隔离判据 = assistant_request_id
   （不是 source_id 的数值大小 / 不是跨表唯一性）
```

---

## 4. 修复（仅 `tests/test_assistant_timeline_concurrency_db_e2e.py`）

```text
新增测试辅助（不新增 test node，避免改变 Matrix 计数）：
    _source_keys(payload)                → set[(source, source_id)] 来源身份
    _cross_request_duplicates(owners)    → 同一 (source, source_id) 出现在 ≥2 个 request
                                           才判定为跨请求污染

test_case_e_ten_concurrent_requests（原错误断言已删除）：
    ① _assert_isolation(payload, request_id)      —— assistant_request_id 隔离（保留）
    ② owners[(source, source_id)] = {request_id}  —— 同一来源身份不得归属两个 request
       assert _cross_request_duplicates(owners) == {}
    ③ 反例守卫（新增）：
       · llm id=1 属 A、tool id=1 属 B  ⇒ **不得**判为污染（合成反例断言）
       · 同一 (llm_usage, 1) 出现在 A 与 B ⇒ **必须**判为污染（合成正例断言）
       · assert len(per_source) >= 2（本次确实覆盖多个 source）

test_case_b_mixed_rag_and_tool（同类缺陷一并修正）：
    删除跨表 `tool_source_ids.isdisjoint(rag_source_ids)`；
    改为**同一 source 内** PK 不跨请求复用（工具组 / RAG 组各自判定）；
    "无跨类型串线"仍由既有 tool_events == [] / rag_events == [] 断言覆盖。

未改动：事件数量期望 · 组内顺序期望 · Outcome 期望 · 其它 12 个用例
未引入：event_id · sequence · span_id · UUID · 全局 ID
```

---

## 5. 验证

```text
全新库等价复现（CREATE DATABASE wms_ai_ci99 → init_db → 测试 → DROP）：
    tests/test_assistant_timeline_concurrency_db_e2e.py   → 14 passed（**空库通过**）
    python scripts/run_matrix_gate.py                     → Status: PASS · exit 0
        （修复前同一空库：DRIFT / DB_EXECUTION_DRIFT + MATRIX_STATUS_DRIFT / exit 1）

本地库：
    tests/test_assistant_trace_timeline_contract.py       → 19 passed
    tests/test_assistant_trace_timeline_regression.py     → 154 passed · 2 skipped
    tests/test_assistant_timeline_concurrency_db_e2e.py   → 14 passed
    python scripts/run_matrix_gate.py（RUN_DB_TESTS=1）    → Status: PASS · exit 0

DB residue（运行后实测）：
    llm_usage_record = 0 · tool_execution_record = 0
    rag_execution_record = 0 · assistant_outcome_record = 0
```

---

## 6. Production code / Baseline / Workflow

```text
Production code:    unchanged（backend/ 与 .github 的 git diff 均为空）
                    未触碰 Timeline / Trace QueryService · DTO · 四张 ORM Model
Baseline:           unchanged
                    offline = 375/356/19 · db = 180/180 · matrix_total = 555
                    status = PASS · residue = 0
                    （修复未新增 test node ⇒ 计数不变，无需 baseline 变更）
Gate / Adapter:     unchanged（evaluate_matrix_baseline_gate /
                    compare_matrix_execution_baseline / adapt_gate_result_to_exit_code
                    / scripts/run_matrix_gate.py 均未改动）
Workflow:           unchanged（配合 §十：提交后由 push 触发真实 CI）
```

---

## 7. 真实 GitHub Actions（Step 99 提交后由 push 触发）

```text
Run:        #2 · run_id 36949287263 · commit 73b3c71eacaeaff4d432453f39e73c7a75b8ef2b
Trigger:    push（main）· Workflow/Job：Observability Matrix Gate
Result:     **SUCCESS**（job conclusion = success · 01:05:58 → 01:06:57 ≈ 59s）

Step 1 Set up job                     success
Step 2 Initialize containers          success   （pgvector/pgvector:pg16）
Step 3 Checkout                       success
Step 4 Setup Python                   success   （3.13）
Step 5 Install dependencies           success
Step 6 Initialize database schema     success   （backend.app.db.init_db）
Step 7 Run observability matrix gate  **success**（23s → exit 0）

⇒ 全新库上的 DB suite = 180 passed / 0 failed（等于冻结 baseline）
⇒ Gate = PASS · Adapter exit 0 · job SUCCESS
（对比 Run #1：Step 7 failure / exit 1）
URL: https://github.com/shuang54/wms-ai-assistant/actions/runs/36949287263
```

---

## 8. 结论

```text
问题：      Step 98 真实 CI 在全新 PostgreSQL 上发现 concurrency test failure
根因：      测试错误要求 source_id 跨 source table 全局唯一
正确 Contract：(source, source_id) 才是 source record identity；
              assistant_request_id 才是跨请求隔离依据
修复：      只修改测试断言（同一文件内新增两个辅助函数）
Production code:  unchanged
Baseline:         unchanged
```
