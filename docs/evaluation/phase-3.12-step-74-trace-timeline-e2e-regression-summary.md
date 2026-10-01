# Phase 3.12 Step 74 — Trace / Timeline E2E Contract Regression 汇总

> 性质：**回归汇总（read-only execution）**。本阶段**未修改任何生产代码**
> （`git diff -- backend` = 空），只执行既有 E2E / 契约测试并汇总结果。
> 基线：`docs/evaluation/phase-3.12-trace-timeline-contract-baseline.md`（Step 73 固化）
> 运行环境：本地 PostgreSQL 16（docker-compose）· `RUN_DB_TESTS=1` 第二轮 ·
> 全部 LLM 调用为 fixture / fake（**未调用 DeepSeek**）

---

## 1. 逐文件回归（离线）

| 文件 | 结果 | 覆盖 |
| --- | --- | --- |
| `tests/test_assistant_trace_multi_path_e2e.py` | 2 passed · 7 skipped | 多路径 Trace（RAG / Tool / T2SQL / 失败路径） |
| `tests/test_assistant_trace_correlation_e2e.py` | 8 passed | Step 40 关联冒烟 + 安全 |
| `tests/test_assistant_trace_timeline_read_during_write.py` | 15 skipped（DB-only） | Step 72 写入中读取 |
| `tests/test_assistant_trace_timeline_outcome_consistency.py` | 17 skipped（DB-only） | Step 71 Trace ↔ Timeline 一致性 |
| `tests/test_assistant_timeline_projection.py` | 33 passed | Step 66 分组投影（DTO / 无身份字段） |
| `tests/test_assistant_timeline_api.py` | 25 passed | Step 68 HTTP Read API |
| `tests/test_assistant_timeline_api_audit.py` | 26 passed | Step 67 API 可行性 / 无分页 |
| `tests/test_assistant_timeline_audit.py` | 30 passed | Step 65 Unified Timeline 可行性审计 |
| `tests/test_assistant_trace_timeline_contract.py` | 19 passed | Step 73 Contract Baseline 守卫 |

```text
离线合计（本集合）：143 passed · 39 skipped（DB-gated 部分） · 0 failed
```

## 2. 逐文件回归（DB-gated · `RUN_DB_TESTS=1`）

| 文件 | 结果 | 覆盖 |
| --- | --- | --- |
| `tests/test_assistant_trace_correlation_e2e_db.py` | 4 passed | Trace 真实 PG 关联 + unknown 200 |
| `tests/test_assistant_trace_api_db.py` | 4 passed | Trace HTTP + 真实读边界 |
| `tests/test_assistant_trace_persistent_tool_db.py` | 6 passed | Tool 段持久化关联 |
| `tests/test_assistant_trace_query_service_db.py` | 6 passed | Trace QueryService 真实读 |
| `tests/test_assistant_trace_rag_integration_e2e_db.py` | 6 passed | RAG 段持久化关联 |
| `tests/test_assistant_timeline_db_e2e.py` | 14 passed | Step 69 Timeline PostgreSQL E2E |
| `tests/test_assistant_timeline_concurrency_db_e2e.py` | 14 passed | Step 70 并发隔离 |
| `tests/test_assistant_trace_timeline_read_during_write.py` | 15 passed | Step 72 写入中读取（partial 合法） |
| `tests/test_assistant_trace_timeline_outcome_consistency.py` | 17 passed | Step 71 两视图一致性 + 安全 |
| `tests/test_assistant_outcome_persistence.py` | 32 passed | Step 64 Outcome 持久化（含并发 / 冲突 / 失败隔离） |
| `tests/test_llm_usage_trace_correlation_db.py` | 6 passed | Step 36/37 LLM 关联（provider id ≠ assistant id） |

```text
DB 合计（本集合）：124 passed · 0 failed
```

## 3. 全量回归（汇总结果）

```text
离线        python -m pytest -q -p no:randomly           → 1 failed · 4352 passed · 572 skipped
DB-gated    RUN_DB_TESTS=1 python -m pytest -q -p no:randomly
                                                          → 1 failed · 4882 passed ·  42 skipped

唯一失败（两轮相同，**与 Step 73/74 改动无关**）：
    tests/test_tool_observability_architecture_audit.py
        ::TestC25QuerySnapshotApi::test_c25_13_only_allowlisted_tool_observability_http_api
```

### 3.1 根因（已隔离复现）

```text
AssertionError: ('assistant_timeline.py',
                 '/observability/assistant-timeline/{assistant_request_id}')
assert '/observability/assistant-timeline/{assistant_request_id}' in {
    '/observability/tools', '/observability/tools/metrics',
    '/observability/tools/history', '/observability/tools/metrics/persistent',
    '/observability/assistant-trace/{assistant_request_id}',
}
tests\test_tool_observability_architecture_audit.py:524

判定：**测试侧白名单未同步**（state lock 漂移），不是实现 / 契约不一致：
    · 该断言扫描 backend/app/api/*.py 的装饰器路径，凡含 observability 的路径必须命中白名单；
    · 白名单在 Step 39 同步过 Trace 端点，但 **Step 68 新增的 Timeline 端点未同步**；
    · 单文件 / 单用例隔离运行同样失败（非顺序依赖、非随机插件、非并发漂移）；
    · 与 Step 71/72/73 无关：`git status -- backend` = 空，Timeline API 自 Step 68 起未改动。
```

### 3.2 建议的最小同步补丁（**待批准后再执行**；本阶段未改）

```python
# tests/test_tool_observability_architecture_audit.py  → allowed_paths 追加 1 行
            # Assistant Trace Read API（Phase 3.12 Step 39；只读组合，无新存储）
            "/observability/assistant-trace/{assistant_request_id}",
            # Assistant Timeline Read API（Phase 3.12 Step 68；只读分组投影，无新存储）
            "/observability/assistant-timeline/{assistant_request_id}",
```

```text
性质：tests/ 单行 additive 同步；白名单"仅限只读端点"的约束强度**不变**
     （其余任何 observability / metrics / snapshot 端点仍会失败）
影响：仅该 1 个测试用例；无生产代码 / 无 DB / 无网络
```

## 4. 运行卫生

```text
DB 写入残留（两轮全量运行后实测）：llm_usage_record = 0 · tool_execution_record = 0
                                   · rag_execution_record = 0 · assistant_outcome_record = 0
Network calls  = 0（无 DeepSeek / 无外部 HTTP；LLM 侧全部 fixture / fake）
真实 WMS 数据  = 未触碰（测试使用合成 id / 定向清理 + conftest 会话水位守卫）
compileall     = OK（0 errors）
```

## 5. 结论与限制

```text
结论：Trace / Timeline 相关的 20 个 E2E / 契约文件（离线 143 + DB 124 用例）**全绿**，
      与 Step 73 固化的 Contract Baseline 一致；无契约回退。
      全量套件存在 **1 个既存测试侧白名单漂移**（§3），需人工批准后按 §3.2 同步。

限制：
    * 本汇总为**时点快照**（2026-10-01 · 本机 PG 16 · 单进程），不含压测 / 多 worker；
    * `-p no:randomly` 固定顺序；未验证随机顺序组合（既有 CI 语义亦为固定顺序）；
    * 汇总不覆盖 lint（环境未安装 ruff / flake8）；
    * 1 个失败为**测试锁**层面，未修改（遵循"先报告、后决定"约定）。
```
