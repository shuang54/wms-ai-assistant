# Phase 3.12 CI Governance Contract（Step 108 — Frozen）

> **只读冻结**：本文件与 `tests/test_github_actions_matrix_gate.py::CI_GOVERNANCE_CONTRACT`
> 共同描述**当前** CI 治理事实；本阶段未修改 Workflow / Ruleset / Gate / Adapter / CLI /
> Baseline / backend，未启用 Merge Queue，未添加 merge_group，未创建 PR / 未 push / 未 merge。

---

## 1. Current CI

```text
workflow:        Observability Matrix Gate
                 （.github/workflows/observability-matrix-gate.yml · 唯一 workflow）
job:             observability-matrix-gate（唯一 · job name = Observability Matrix Gate）
triggers:        **push** + **pull_request**
                 merge_group = **ABSENT**
steps:           Checkout → Setup Python → Install dependencies →
                 Initialize database schema → Run observability matrix gate
```

## 2. Required

```text
Required Check:  **Observability Matrix Gate**
                 （= job name；GitHub required check 名来自 job name，必须唯一稳定）
提供者:          GitHub Actions（integration_id 15368）
判断者:          Ruleset（GitHub 侧），不是 Workflow
```

## 3. Protected branch

```text
Protected branch: **main**（refs/heads/main）
Ruleset:          Protect Main（id 24348464 · target = branch · enforcement = **active**）
规则：            required_status_checks（Observability Matrix Gate · strict = false）·
                 pull_request · deletion · non_fast_forward
不扩大为 * / all branches
```

## 4. Merge Queue

```text
Merge Queue:      **NOT ENABLED**（Ruleset 中无 merge_queue 规则）
merge_group:      **ABSENT**（Workflow 未声明该触发）
Decision:         **DEFER**（无事实证据表明当前需要；见 Step 107 决策审计）
```

## 5. 所有权边界（冻结）

```text
应用侧（唯一判断链，不得复制）：
    MatrixExecutionBaseline
        ↓ evaluate_matrix_baseline_gate()
    MatrixBaselineGateResult
        ↓ adapt_gate_result_to_exit_code()
    0 / 1（CLI → 进程 exit code）

GitHub 侧（只负责"必须满足哪个 check"）：
    Ruleset Protect Main → requires → Observability Matrix Gate → PR Merge Blocking

Workflow 侧（只负责编排，不判断）：
    checkout · setup python · install deps · init_db · run CLI

Governance Contract 只描述：谁负责 · 叫什么 · 保护什么 · 当前是否启用
（**不**复制 PASS / DRIFT / exit code / baseline / residue 逻辑）
```

## 6. 已验证行为（Step 105 Evidence）

```text
Gate PASS → Required Check PASS → PR 可满足检查
Gate FAIL → Required Check FAIL → PR Merge Blocking（push + pull_request 两条 check 均 failure）
（Required Check 必须在 PR 的**最新 commit** 上成功；旧 commit 的成功不能代替）
```

## 7. 未来启用 Merge Queue 的前置条件

> **若未来启用 Merge Queue，必须先单独完成 Workflow 的 `merge_group` readiness，
> 再启用 Merge Queue。**

```text
原因（GitHub 要求）：Merge Queue 场景下，Required Check 对应的 workflow 必须监听
                    `merge_group`（否则 merge group 中可能不产生该 Required Check）
最小改动方向（**本阶段不执行**）：
    on:
      push:
      pull_request:
      merge_group:
        types: [checks_requested]        # 可按目标分支过滤（如 branches: [main]）
必须保持不变：check name / job name / Gate semantics / Adapter semantics / Baseline
             （继续使用同一个 Observability Matrix Gate，不新建第二套 Gate）
```

## 8. 测试

```text
tests/test_github_actions_matrix_gate.py → **50 passed**
    Step 108 新增 7 项（TestStep108CIGovernanceContract）：
        ci_governance_contract_is_frozen
        decision_does_not_promise_future_merge_queue
        required_check_identity_is_a_single_name
        branch_scope_is_main_only
        merge_queue_boundary_is_frozen
        governance_contract_does_not_duplicate_gate_logic
        workflow_ownership_is_boundary_only
未新增第二个 CI regression framework；未运行 full pytest / RUN_DB_TESTS / DeepSeek /
GitHub Actions；python -m compileall -q backend tests scripts → OK（0 errors）
```

## 9. 结论

```text
CURRENT CI GOVERNANCE = **FROZEN**
MERGE QUEUE           = **DEFER**
```
