# Phase 3.12 CI Governance Self-Consistency Audit（Step 109）

> **离线自洽审计**：只比较**本地** Workflow / Contract / 测试三者，不访问 GitHub API、
> 不读环境变量、不做写操作。本阶段未修改 Workflow / Ruleset / Gate / Adapter / CLI /
> Baseline / backend。

---

## 1. 审计对象

```text
CI_GOVERNANCE_CONTRACT（tests/test_github_actions_matrix_gate.py · Step 108 冻结）
    workflow_name        = "Observability Matrix Gate"
    job_name             = "observability-matrix-gate"
    required_check       = "Observability Matrix Gate"
    required_branch      = "main"
    merge_queue_enabled  = False
    merge_group_trigger  = False
    decision             = "DEFER"
```

## 2. 逐项自洽结论

| 审计项                 | 期望（Contract）                     | 本地事实（Workflow / 测试）                          | 结果 |
| ---------------------- | ------------------------------------ | --------------------------------------------------- | ---- |
| Workflow Identity      | `Observability Matrix Gate`          | `.github/workflows/observability-matrix-gate.yml` → `name` 相同 | PASS |
| Job Identity           | `observability-matrix-gate`          | `jobs` 键集 = `["observability-matrix-gate"]`（唯一） | PASS |
| Required Check Identity | `Observability Matrix Gate`         | job `name` 相同；无第二套名（CI Gate / Merge Gate / Observability Gate / Merge Queue Gate 均不存在） | PASS |
| Trigger Identity       | push ✔ · pull_request ✔ · merge_group ✘ | `on` = `{push, pull_request}`；文本无 `merge_group` | PASS |
| Branch Scope           | `main`（Ruleset 作用域）             | Contract 值为 `main`；未扩大为 `*` / `all branches` / `refs/heads/*` | PASS |
| Gate Ownership         | 不含 Gate/Adapter 语义               | Contract 与 run 体均无 `PASS` / `DRIFT` / `exit_code` / `MATRIX_EXECUTION_BASELINE` / `evaluate_matrix_baseline_gate` / `adapt_gate_result_to_exit_code` | PASS |
| Workflow Ownership     | 仅 checkout → setup → install → init_db → CLI | 步骤名精确匹配 5 项；run 体只含 `backend.app.db.init_db` + `scripts/run_matrix_gate.py` | PASS |
| Merge Queue Boundary   | `False` / `False` / `DEFER`          | 三者自洽；无 future_enablement / will_enable / must_enable 键 | PASS |
| Dynamic GitHub Dep.    | 无                                   | AST 检查：import 无 requests / httpx / urllib / aiohttp / github / PyGithub；无 `os` / `environ` / `getenv`；无 `GITHUB_TOKEN` / `GH_TOKEN` 标识符 | PASS |
| Contract Immutability  | 审计前后不变                         | `run_governance_self_consistency_audit()` 前后 `dict(CONTRACT)` 相等；重复执行结果幂等且全部 finding 为 True | PASS |

## 3. 关键概念区分（避免误判）

```text
Workflow trigger（分支无关：push / pull_request 在所有分支触发 CI）
    ≠
Ruleset branch scope（refs/heads/main：Required Check 只作用于 main）

因此"Workflow 未声明 branches 过滤"**不**意味着把保护范围扩大到所有分支；
Ruleset 作用域仍由 Ruleset 定义（本审计只描述 Contract，不改 Ruleset）。
```

## 4. 审计实现（离线）

```python
run_governance_self_consistency_audit() -> dict[str, bool]
    workflow_name · job_id · job_name · triggers · required_check_single_name ·
    branch_scope · merge_queue_boundary · governance_boundary
# 纯本地（解析 YAML + 读本文件 AST），无网络 / 无环境变量 / 无写操作
```

## 5. 测试

```text
tests/test_github_actions_matrix_gate.py → **57 passed**（Step 109 新增 7 项）
    test_workflow_and_job_identity_match_contract
    test_trigger_identity_matches_contract
    test_branch_scope_is_not_expanded
    test_ownership_boundary_has_no_gate_semantics
    test_merge_queue_boundary_is_self_consistent
    test_audit_has_no_dynamic_github_dependency（AST 级，非全文扫描）
    test_contract_is_immutable_across_audit
未新增第二个 CI Governance Framework；未运行 full pytest / RUN_DB_TESTS / DeepSeek /
GitHub Actions；python -m compileall -q backend tests scripts → OK（0 errors）
```

## 6. 结论

```text
CI GOVERNANCE SELF-CONSISTENCY = **PASS**
（Contract ↔ Workflow ↔ 测试三者一致；离线可重复；无 GitHub 动态依赖）
```
