# Phase 3.12 — Observability HTTP Allowlist Regression Freeze（Step 77）

## Frozen Contract

```text
exactly 6 routes（全部 GET；decorator 原始 path，不含 /api 前缀）

GET /observability/tools                              （tool_observability.py）
GET /observability/tools/metrics                      （tool_observability.py）
GET /observability/tools/history                      （tool_observability.py）
GET /observability/tools/metrics/persistent           （tool_observability.py）
GET /observability/assistant-trace/{assistant_request_id}     （assistant_trace.py）
GET /observability/assistant-timeline/{assistant_request_id}  （assistant_timeline.py）
```

唯一测试事实来源：`tests/test_observability_http_allowlist_regression.py`
`::EXPECTED_OBSERVABILITY_ROUTES`；Step 76 审计侧的六路由基线改为**引用**该常量
（不再持有第二份字面量）。

三层一致性：`Actual Routes == Frozen Contract == C25 Allowlist`。

变更规则：新增 / 删除 / 改 method / 改 path 均需未来单独的 Phase / Step 显式授权
（即先修改冻结常量，再同步 C25 白名单；否则回归失败）。

运行：

```powershell
python -m pytest -q tests/test_observability_http_allowlist_regression.py   # 冻结回归
python -m pytest -q tests/test_observability_http_allowlist_audit.py        # 发现 + 双向比对
python -m pytest -q tests/test_tool_observability_architecture_audit.py     # C25 架构审计
```

## Purpose

```text
防止新增 Observability API 后遗漏 C25 allowlist（Step 74/75 曾出现的 drift 类问题）。
把 Step 76 已核实的结果锁定为可长期重复运行的回归契约：
    发现漂移时 → 先失败，再人工决定是否授权变更契约。
```

## Scope

```text
HTTP route path / method only（decorator 原始字面量）
+ 只读原则（GET only）+ 三层（Actual / Frozen / Allowlist）一致
+ 敏感标识符（api_key / password / authorization / database_url）由 Step 76 扫描器负责
```

## Non-goals

```text
No Timeline redesign
No event identity（event_id / sequence / span_id / parent_event_id）
No pagination
No auth redesign
No persistence change
No Unified Timeline
```

## 运行边界

```text
DB = 0 · Network = 0 · DeepSeek = 0（纯静态 AST 回归；无需 RUN_DB_TESTS）
backend/ 只读（本阶段不修改生产代码）
```
