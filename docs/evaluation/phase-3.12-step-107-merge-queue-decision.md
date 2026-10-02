# Phase 3.12 Step 107 — Merge Queue 启用决策审计

> **READ ONLY**。未启用 Merge Queue · 未修改 Workflow / Ruleset / Required Check /
> Gate / Adapter / CLI / Baseline / backend；**未创建 PR / 未 push / 未 merge**。

---

## 1. Current CI（事实）

```text
workflow:        .github/workflows/observability-matrix-gate.yml（唯一）
job:             observability-matrix-gate（唯一 · name = Observability Matrix Gate）
triggers:        push ✔ · pull_request ✔ · merge_group ✘（absent）
Required Check:  Observability Matrix Gate
                 （Ruleset 24348464「Protect Main」· active · refs/heads/main ·
                   integration_id 15368 · strict = false）
PR Blocking:     Step 105 已实测（PR #1：push + pull_request 两条 check = failure，
                 Merge Box Required ⇒ 合并被阻断）
```

## 2. Merge Queue

```text
enabled:  **NO**（Ruleset rules 4 条中无 `merge_queue` 规则）
audited:  YES（GET /repos/shuang54/wms-ai-assistant/rulesets/24348464 → 200 · 只读）
```

## 3. Current Evidence（只记录事实，不推测）

| 观察维度                     | 事实                                                                 | 数据质量 |
| ---------------------------- | -------------------------------------------------------------------- | -------- |
| 并发 PR（concurrent PR）     | PR 历史总数 = **1**（Step 105 测试 PR，#1，已于 2026-10-02T06:28:01Z **关闭且未合并**）；merged = 0 | 有数据 |
| merge conflict               | 无冲突记录（唯一 PR `mergeable = true`；历史为线性单线提交）           | 有数据（样本极小） |
| merge 后 main 重新失败       | **无 merge 记录**（0 merged）⇒ 无该现象数据                            | UNKNOWN |
| CI duration                  | 单次 workflow ≈ **1m22s ~ 1m40s**（Step 98/99/102/105 实测）           | 有数据 |
| 分支/提交活动                 | commits = 172 · 当前工作模式 = 单人顺序提交到 main，未使用 PR 合并流    | 有数据 |
| Required Check 阻止不合格 PR | **YES**（Step 105 已实测阻断）                                         | 已验证 |

## 4. Merge Queue 价值矩阵（只记状态）

| 能力                    | 当前状态  |
| ----------------------- | --------- |
| PR Required Check       | YES       |
| Push Gate               | YES       |
| Pull Request Gate       | YES       |
| Merge Queue             | NO        |
| merge_group trigger     | NO        |
| Merge Queue 实际问题证据 | UNKNOWN   |

## 5. Decision

```text
ENABLE NOW = **NO**
DEFER      = **YES**

理由（仅基于上面的可观测事实）：
    · 无并发 PR（历史 1 个 PR，0 合并）⇒ 无"多个 PR 同时等待合并"场景
    · 无 merge 记录 ⇒ 无"合并后 main 重新失败/互相覆盖"的证据
    · 无 merge conflict 记录
    · Required Check 已能阻止不合格 PR（Step 105 已验证）
    · CI 时长 ~1.5 分钟，规模小；Merge Queue 带来的收益当前无法用数据证明
不采用"以后可能需要"作为启用理由（§十三：无足够事实 ⇒ DEFER）
```

## 6. 未来最小变更（**本阶段不执行**，仅记录方向）

```yaml
# 未来若决定启用 Merge Queue，最小改动方向（最终写法留到启用阶段确定）
on:
  push:
  pull_request:
  merge_group:
    types: [checks_requested]          # 可按目标分支过滤（例如 branches: [main]）
```

必须保持不变（§八）：

```text
check name   = Observability Matrix Gate（继续使用同一 job，不新建 "Merge Queue Gate"）
job name     = observability-matrix-gate
Gate semantics / Adapter semantics / Baseline = 全部不变
```

## 7. Decision Contract（测试侧冻结）

```python
MERGE_QUEUE_DECISION_CONTRACT = {
    "current_enabled": False,
    "current_workflow_merge_group": False,
    "required_check": "Observability Matrix Gate",
    "future_required_change": "add merge_group trigger",
    "decision": "DEFER",           # 不表达"未来一定启用"
}
MERGE_QUEUE_UNKNOWN_DIMENSIONS = (
    "concurrent_pull_requests",
    "merge_conflicts",
    "post_merge_main_instability",
)
```

## 8. 测试

```text
tests/test_github_actions_matrix_gate.py → **43 passed**（Step 107 新增 2 项：
    test_decision_contract_is_frozen
    test_decision_does_not_promise_future_enablement）
未新增测试文件；未运行 full pytest / RUN_DB_TESTS / DeepSeek / GitHub Actions
python -m compileall -q backend tests scripts → OK（0 errors）
```

## 9. 结论

```text
CURRENT CI READY   = YES
MERGE QUEUE READY  = NO（workflow 无 merge_group 触发）
MERGE QUEUE DECISION = **DEFER**（无事实证据表明当前需要）
MERGE QUEUE ENABLED  = NO（本阶段未启用，未修改任何设置）
```
