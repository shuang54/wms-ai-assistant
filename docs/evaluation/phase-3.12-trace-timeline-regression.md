# Phase 3.12 — Trace / Timeline Contract Regression（Step 74）

> 性质：**回归入口（suite collector / gate）**。本阶段不新增生产能力、不新增测试语义、
> 不修改 `backend/`；只把 Step 64～73 已验证的契约收口为**可长期重复运行**的入口。
> 契约冻结来源：`docs/evaluation/phase-3.12-trace-timeline-contract-baseline.md`（Step 73）
> 入口文件：`tests/test_assistant_trace_timeline_regression.py`

---

## 1. 运行方式（两种模式，显式）

```powershell
# 模式 1：offline（默认；嵌套 suite 强制清空 RUN_DB_TESTS）
python -m pytest -q tests/test_assistant_trace_timeline_regression.py

# 模式 2：DB-gated（真实**测试** PostgreSQL；不使用生产 / 开发库）
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_assistant_trace_timeline_regression.py

# 亦可单独跑闸门
python -m pytest -q tests/test_assistant_trace_timeline_contract.py         # 离线契约闸门
python -m pytest -q tests/test_assistant_trace_timeline_read_during_write.py # Step 72（DB-only）
```

模式语义：

| 模式 | 包含内容 | 说明 |
| --- | --- | --- |
| offline | 注册表静态校验 + 入口自身卫生 + **离线 suite**（`db ∈ {no, partial}`） | 嵌套执行时清空 `RUN_DB_TESTS`，partial 文件的 DB 段自动 skip |
| DB-gated | 上述全部 + **DB suite**（`db ∈ {yes, partial}`） | 真实测试 PG；嵌套执行 `RUN_DB_TESTS=1` |

入口**不含**任何 E2E 逻辑：不建 HTTP 客户端、不构造请求、不写库（仅 `step74-` 命名空间的定向清理）。

## 2. Regression Matrix

`DB` 列：`yes` = 需要 `RUN_DB_TESTS=1`；`partial` = 文件内同时含离线与 DB 门控用例；
`no` = 纯离线。`Network` 列：全部为 `no`（无外部网络；LLM 侧全为 Fake / Stub）。

| Area | Existing Test | DB | Network | Coverage |
| --- | --- | --- | --- | --- |
| API Contract | `tests/test_assistant_timeline_api.py` | no | no | Timeline HTTP 200 / 空段 / 错误映射 |
| API Contract | `tests/test_assistant_timeline_api_audit.py` | no | no | 路由与无分页（Step 67） |
| API Contract | `tests/test_assistant_trace_api.py` | no | no | Trace HTTP + OpenAPI（Step 39） |
| API Contract | `tests/test_assistant_trace_api_db.py` | yes | no | Trace HTTP + 真实读边界 |
| API Contract | `tests/test_assistant_trace_pagination_audit.py` | partial | no | 无分页字段（Step 59） |
| Trace Contract | `tests/test_assistant_trace_query_service.py` | no | no | Trace 读模型（Step 38） |
| Trace Contract | `tests/test_assistant_trace_query_service_db.py` | yes | no | Trace QueryService 真实读 |
| Trace Contract | `tests/test_assistant_trace_correlation_e2e.py` | no | no | 关联 + 安全（Step 40） |
| Trace Contract | `tests/test_assistant_trace_correlation_e2e_db.py` | yes | no | 真实 PG 关联 + unknown 200 |
| Trace Contract | `tests/test_llm_usage_trace_correlation_db.py` | yes | no | provider id ≠ assistant id（Step 36/37） |
| Timeline Contract | `tests/test_assistant_timeline_audit.py` | no | no | 可行性审计（Step 65） |
| Timeline Contract | `tests/test_assistant_timeline_projection.py` | no | no | 分组投影 / source_id / 无全局顺序（Step 66） |
| Timeline Contract | `tests/test_assistant_timeline_api.py` | no | no | 分组 HTTP DTO（Step 68） |
| Timeline Contract | `tests/test_assistant_timeline_db_e2e.py` | yes | no | PostgreSQL E2E / DB PK（Step 69） |
| Outcome | `tests/test_assistant_outcome.py` | no | no | Outcome 契约（Step 62） |
| Outcome | `tests/test_assistant_outcome_contract_audit.py` | partial | no | 契约审计（Step 62） |
| Outcome | `tests/test_assistant_trace_outcome_audit.py` | partial | no | Outcome × Trace（Step 61） |
| Outcome | `tests/test_assistant_outcome_persistence.py` | partial | no | 持久化 / 幂等 / 冲突 / 失败隔离（Step 64） |
| Partial State | `tests/test_assistant_trace_multi_path_e2e.py` | partial | no | 多路径 / partial（Step 60） |
| Read During Write | `tests/test_assistant_trace_timeline_read_during_write.py` | yes | no | 写入中读取（Step 72） |
| Cross-request Isolation | `tests/test_assistant_timeline_concurrency_db_e2e.py` | yes | no | 并发 A/B/C 隔离（Step 70） |
| Cross-request Isolation | `tests/test_assistant_trace_timeline_outcome_consistency.py` | yes | no | 跨请求 outcome 隔离（Step 71） |
| Cross-request Isolation | `tests/test_assistant_trace_persistent_tool_db.py` | yes | no | Tool 段归属（Step 41） |
| Concurrency | `tests/test_assistant_timeline_concurrency_db_e2e.py` | yes | no | 5 RAG / 5 mixed / 3 retry / 3 refusal / 10 mixed（Step 70） |
| Concurrency | `tests/test_assistant_outcome_persistence.py` | partial | no | 5 并发 outcome 写入（Step 64） |
| Trace ↔ Timeline Consistency | `tests/test_assistant_trace_timeline_outcome_consistency.py` | yes | no | LLM 段 id ↔ source_id；Tool / RAG 按数量·内容·顺序（Step 71） |
| Security | `tests/test_assistant_trace_timeline_outcome_consistency.py` | yes | no | 响应无敏感键 / 哨兵（Step 71） |
| Security | `tests/test_assistant_trace_correlation_e2e.py` | no | no | Trace 响应安全（Step 40） |
| Security | `tests/test_assistant_timeline_api.py` | no | no | Timeline 响应安全（Step 68） |
| Security | `tests/test_assistant_trace_timeline_contract.py` | no | no | 敏感字段名缺失（Step 73） |
| Security | `tests/test_assistant_trace_rag_integration_e2e_db.py` | yes | no | RAG 段无敏感内容（Step 48） |
| Source ID | `tests/test_assistant_timeline_projection.py` | no | no | int / 非 bool·str·UUID（Step 66） |
| Source ID | `tests/test_assistant_timeline_db_e2e.py` | yes | no | 属当前 request 的 PK 集合（Step 69） |
| Source ID | `tests/test_assistant_trace_timeline_contract.py` | no | no | 类型与身份字段缺失（Step 73） |
| Ordering | `tests/test_assistant_timeline_db_e2e.py` | yes | no | LLM `created_at,id` / Tool·RAG `id`（Step 69） |
| Ordering | `tests/test_assistant_timeline_projection.py` | no | no | 组内顺序稳定（Step 66） |
| Ordering | `tests/test_assistant_trace_timeline_contract.py` | no | no | 无全局顺序 / 无 sequence（Step 73） |
| Observability HTTP Allowlist | `tests/test_observability_http_allowlist_audit.py` | no | no | 真实 route 发现 + 白名单双向比对（Step 76） |
| Observability HTTP Allowlist | `tests/test_observability_http_allowlist_regression.py` | no | no | Frozen Contract 六路由精确集合（Step 77） |
| Observability HTTP Allowlist | `tests/test_observability_http_allowlist_architecture_audit.py` | no | no | 测试架构自审（单一来源 / 单一 scanner）（Step 78） |
| Observability HTTP Allowlist | `tests/test_tool_observability_architecture_audit.py` | no | no | C25 Allowlist consumer（只读端点漂移闸门） |

## 2.1 Observability HTTP Allowlist（Step 79 注册）

```text
Purpose:
    确保 Observability HTTP API 的实际 route、Frozen Contract、C25 Allowlist 三者保持一致。

覆盖：route discovery · allowlist completeness · frozen contract ·
      single source of truth · scanner uniqueness · security

条目（file-level registration；矩阵**不重复**断言逻辑）：
    Step 76  tests/test_observability_http_allowlist_audit.py
    Step 77  tests/test_observability_http_allowlist_regression.py
    Step 78  tests/test_observability_http_allowlist_architecture_audit.py
    C25      tests/test_tool_observability_architecture_audit.py

元数据（每条目）：
    category = OBSERVABILITY_HTTP_ALLOWLIST
    db_required = false · network_required = false
    llm_required = false · production_code_required = false

代表性 node（REPRESENTATIVE_CONTRACT_NODES；只登记 node id，不复制断言）：
    Step 76  ..._audit.py::TestBaseline::test_10_six_route_baseline_is_exact
    Step 77  ..._regression.py::TestFrozenRouteContract::test_exact_observability_route_set
    Step 78  ..._architecture_audit.py::TestFrozenContractUniqueness
             ::test_frozen_route_contract_has_single_declaration
    Step 78  ..._architecture_audit.py::TestScannerUniqueness
             ::test_api_route_scanner_has_single_implementation
    C25      tests/test_tool_observability_architecture_audit.py::TestC25QuerySnapshotApi
             ::test_c25_13_only_allowlisted_tool_observability_http_api

入口结构名（禁止第二套回归框架）：仅 `test_assistant_trace_timeline_regression.py` 持有
    FILES · CATEGORIES · _REQUIRED_CATEGORIES · _offline_suite · _db_suite ·
    REPRESENTATIVE_CONTRACT_NODES（由 test_no_duplicate_regression_framework 强制）
```

## 3. 覆盖类别（13 项，全部可追溯）

```text
API Contract · Trace Contract · Timeline Contract · Outcome · Partial State ·
Read During Write · Cross-request Isolation · Concurrency ·
Trace ↔ Timeline Consistency · Security · Source ID · Ordering ·
OBSERVABILITY_HTTP_ALLOWLIST（Step 79 新增）
```

入口内置 `_REQUIRED_CATEGORIES` 常量 + 注册表一致性断言：类别缺失、文件被删、
文件未被任何类别引用、DB 文件缺少 `RUN_DB_TESTS` 门控 → **测试直接失败**。

## 4. 契约边界（Step 73 冻结；本阶段不新增）

```text
partial state 合法          HTTP 200 ≠ complete final snapshot
outcome 不推断              仅 assistant_outcome_record 存在时表达（否则 null）
source_id = persistence PK  int · 非 bool/str/UUID · 非 sequence/index
group ordering              LLM created_at ASC,id ASC · Tool/RAG id ASC · Outcome ≤1
request isolation           A/B/C 响应互不包含；source_id 属各自 request
security                    Trace / Timeline / Outcome 无 prompt·SQL·arguments·
                            tool_result·exception·credential 等；保留 prompt_tokens /
                            completion_tokens / result_count 合法字段
不提供                      event_id · sequence · span_id · parent_event_id ·
                            global ordering · pagination · transaction snapshot
```

## 5. 实测结果（2026-10-01 · 本机 PG 16 · 单进程 · `-p no:randomly`）

```text
离线 suite（18 文件 · Step 79 后）  → 356 passed · 19 skipped（partial 文件的 DB 段）· 0 failed
    （Step 74 基线 14 文件 289 passed；+4 文件 +67 用例 = Step 76/77/78 + C25）
DB suite（15 文件 · RUN_DB_TESTS=1）→ 180 passed · 0 skipped · 0 failed（未变）

入口自身
    python -m pytest -q tests/test_assistant_trace_timeline_regression.py          → 16 passed · 2 skipped
    $env:RUN_DB_TESTS="1"; …（Step 74 实测 14 passed；Step 79 新增 4 个注册用例 ⇒ 预期 18 passed；
                              本阶段按规约未开启 RUN_DB_TESTS，故未复测）
单独闸门
    tests/test_assistant_trace_timeline_contract.py                                 → 19 passed
    tests/test_assistant_trace_timeline_read_during_write.py（离线 / DB）           → 15 skipped / 15 passed
    tests/test_observability_http_allowlist_audit.py                                → 19 passed
    tests/test_observability_http_allowlist_regression.py                           → 10 passed
    tests/test_observability_http_allowlist_architecture_audit.py                   → 18 passed
    tests/test_tool_observability_architecture_audit.py                             → 20 passed
```

## 6. DB 残留与清理

```text
实测（两套 suite + 入口 DB 模式运行后）：
    ai_ops.llm_usage_record        total = 0   step74- = 0
    ai_ops.tool_execution_record   total = 0   step74- = 0
    ai_ops.rag_execution_record    total = 0   step74- = 0
    ai_ops.assistant_outcome_record total = 0  step74- = 0

清理规则（入口内置）：
    * 只删 `step74-` 前缀命名空间（DELETE … WHERE <request 列> LIKE 'step74-%'）；
    * 禁止 TRUNCATE / DELETE ALL / DROP（由入口自身的 AST 卫生断言强制）；
    * 既有测试自带的定向清理与 tests/conftest.py 会话水位守卫继续生效；
    * 本入口**不创建**任何请求（不新增语义），故不会产生新残留。
```

## 7. 安全 / 网络 / LLM

```text
Network calls          = 0（无外部 HTTP；无 socket 客户端 import）
Real LLM（DeepSeek）   = 0 次（全部沿用既有 Fake / Stub；未改 API Key / 未改 .env）
生产 / 开发数据库      = 未使用（仅项目既有测试 PostgreSQL）
```

## 7.1 Matrix Contract（Step 80 冻结）

```text
规模快照 EXPECTED_MATRIX_SCALE（任何增减需显式授权）
    categories        = 13
    registered_files  = 28
    offline_files     = 18   （db == "no" | "partial"）
    db_files          = 15   （db == "yes" | "partial"）
    自洽：offline + DB = registered + partial

冻结检查（tests/test_assistant_trace_timeline_regression.py::TestRegressionMatrixContract，12 项）
    matrix_scale_is_frozen · required_categories_match_actual_exactly ·
    required_categories_have_no_duplicates · registered_file_paths_are_unique ·
    filespec_metadata_is_valid（path/coverage 非空 · db ∈ no|partial|yes · 三个开关 bool）·
    observability_allowlist_category_metadata_is_all_false ·
    db_and_offline_suite_partition · category_to_file_completeness（无 dangling）·
    file_to_category_completeness（无 orphan）· no_self_registration ·
    representative_nodes_are_unique_and_registered · representative_nodes_are_collectable
        （`--collect-only` 只收集不执行）

FileSpec.db 语义：db_required = (db != "no")；coverage = scope
```

## 7.2 Matrix 可执行性审计（Step 81 · `--collect-only`）

```text
TestRegressionMatrixCollectability（5 项；只收集不执行，不开启 RUN_DB_TESTS）
    collectability_report_covers_every_registered_file · all_registered_files_are_collectable ·
    offline_registered_files_are_collectable · db_registered_files_are_collectable ·
    collectability_does_not_require_db_gate

实测（每题一次单文件 `python -m pytest --collect-only -q <file>`，30s 超时，无 retry）
    registered 28 → collect ok **28 / 28** · failed 0 · timeout 0 · 零用例文件 0
    offline 18 / 18 · DB 15 / 15（**未启动 PostgreSQL**：仅收集）
    合计收集 node = **467**
结果性质：**审计信息**，不写入 FileSpec（FileSpec 结构未变）
```

## 8. 漂移检测

```text
谁改坏契约，谁先失败：
    DTO / API / route / event fields 改动
        → tests/test_assistant_trace_timeline_contract.py（离线契约闸门）先失败
        → 入口的离线 suite 立即红灯（无需 DB）
    DB 读路径 / 持久化 / 并发隔离改动
        → DB suite 红灯（RUN_DB_TESTS=1）
```

## 9. 限制

```text
* offline 模式**不等于**"零 DB 写入"：生产接线真实，驱动 /api/ai/chat 的离线 E2E 在
  DATABASE_URL 已配置时仍会写合成行（由 conftest 会话水位守卫清理）；严格 DB=0 /
  network=0 的强保证只施加于 Step 73 契约闸门（入口对其有专门断言）
* 入口以嵌套 pytest 子进程执行既有 suite（约 2.5s / 12s），非 pytest 原生 merge；
  失败时通过 stdout tail 暴露细节
* `-p no:randomly` 固定顺序；未验证随机顺序组合
* 时点快照（本机 PG 16 · 单进程），不含压测 / 多 worker / 复制延迟
* lint unavailable（环境未安装 ruff / flake8）
```
