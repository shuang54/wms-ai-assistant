# Phase 3.12 — Observability Allowlist Test Architecture（Step 78）

## 关系（唯一职责分工）

```text
Frozen Contract = single source of truth
    tests/test_observability_http_allowlist_regression.py :: EXPECTED_OBSERVABILITY_ROUTES

Route Scanner = single implementation
    tests/test_observability_http_allowlist_audit.py
        · discover_routes() / observability_routes() / frozen_allowlist()
        · 唯一可复用 AST 路由发现实现；唯一扫描 backend/app/api 的*路由*发现器

C25 Allowlist = consumer
    tests/test_tool_observability_architecture_audit.py :: allowed_paths
        · 只读端点白名单（drift gate），六条路径与 Frozen Contract 逐条一致

Regression = consumer
    tests/test_observability_http_allowlist_regression.py
        · Actual == Frozen == C25 Allowlist 三层一致性

Architecture Audit = protects the above relationship
    tests/test_observability_http_allowlist_architecture_audit.py
        · 只读源码（不 import 被测测试模块），AST 检测：
          第二份完整 allowlist / 第二个 route scanner / 循环 import /
          绕过 Frozen Contract / 凭据标识符 / DB·网络依赖
```

## 冻结登记表（Architecture Audit 内）

```text
路径集合字面量登记（module → 单字面量内 observability 路径数）
    regression                                  6   （Frozen Contract）
    test_tool_observability_architecture_audit  6   （C25 allowlist = consumer）
    test_observability_http_allowlist_audit     2   （两条参数化 route 的定点断言）

文件系统 traversal 登记（module → 次数；目标命中 backend/app/api）
    test_observability_http_allowlist_audit               1   （Route Discovery）
    test_tool_observability_architecture_audit            2   （白名单扫描 + 依赖方向扫描）
    test_tool_observability_persistence_architecture      2   （静态依赖审计，非路由发现）
    test_tool_chat_architecture_contract                  1   （静态契约审计，非路由发现）

可复用 route discovery 函数持有者 = { scanner 模块 }（3 个函数，唯一实现）
模块级 import 边 = { regression → scanner }；scanner → frozen 仅函数内延迟引用（无循环）
```

## 变更规则

```text
新增 Observability API / 新增 allowlist / 新增 scanner / 改变上述任一登记
    → Architecture Audit 先失败（并报告 duplicate scanner locations / registry diff）
    → 需未来单独的 Phase / Step 显式授权并同步登记表
```

## 边界

```text
DB = 0 · Network = 0 · DeepSeek = 0（纯静态 AST 审计；无需 RUN_DB_TESTS）
只审计测试架构（allowlist / scanner / import 方向），不审计 HTTP 契约本身
（契约由 Step 73 与 Step 76/77 负责）
```
