# Phase 3.12 Step 115 — Merge Queue Readiness Audit

> **只读审计**。本阶段**未**开启 Merge Queue · **未**修改 Ruleset / Required Check /
> Workflow / Gate / Adapter / CLI / Baseline / backend · 未 push / merge / rebase / reset /
> clean / stash。Production code = 0 · DB writes = 0 · DeepSeek = 0 · Workflow 修改 = 0 ·
> Ruleset 修改 = 0。

---

## 1. Ruleset（只读 API 审计）

```text
GET /repos/shuang54/wms-ai-assistant/rulesets/24348464 → 200

id = 24348464 · name = Protect Main · target = branch · source_type = Repository
enforcement = **active** · conditions.ref_name.include = ["refs/heads/main"] · exclude = []

rules（4 条）：
    deletion
    non_fast_forward
    required_status_checks:
        strict_required_status_checks_policy = false · do_not_enforce_on_create = false
        required_status_checks = [{ context: "Observability Matrix Gate",
                                    integration_id: 15368 }]
    pull_request（required_approving_review_count = 0 · allowed_merge_methods =
                  [merge, squash, rebase] · require_extra_approval_for_unattributed_changes = true）

merge_queue rule = **ABSENT**（不存在）
created_at / updated_at = 2026-10-02T05:17:18Z（未变化）
```

## 2. Required Check

```text
Required Check  = **Observability Matrix Gate**（integration_id 15368 · GitHub Actions）
Required Check  = **ENABLED / ACTIVE**（enforcement = active · 作用于 main）
strict          = false
```

## 3. Merge Queue

```text
Current Merge Queue = **OFF**（Ruleset 无 merge_queue 规则）
```

## 4. merge_group Trigger

```text
Workflow: .github/workflows/observability-matrix-gate.yml（未修改）
    workflow name = Observability Matrix Gate
    job           = observability-matrix-gate（唯一）· job name = Observability Matrix Gate
    push          = present ✔
    pull_request  = present ✔
    merge_group   = **ABSENT** ✘
Required Check identity（check 名 = job name）与 workflow 一致 ✔
```

## 5. Current Readiness

```text
Merge Queue Readiness = **NOT READY**
原因：Required Check 存在，但 workflow **没有 `merge_group` 触发**
      ⇒ 若启用 Merge Queue，merge group 中可能不会报告该 Required Check
      （GitHub 要求：Merge Queue 场景下 Required Check 的 workflow 必须响应 merge_group）
当前状态合法性：**CURRENTLY VALID**（未启用 Merge Queue，故无需该触发）
```

## 6. Future Minimum Change（**DO NOT IMPLEMENT NOW**）

```yaml
on:
  push:
  pull_request:
  merge_group:
    types: [checks_requested]
# 是否需要 branch filter 以未来真实 Ruleset 为准
```

```text
约束：继续使用**同一个** Observability Matrix Gate（job name / check name / Gate semantics /
      Adapter semantics / Baseline 全部不变），不新建第二套 Gate。
```

## 7. Tests

```text
tests/test_github_actions_matrix_gate.py → **66 passed**（Step 115 新增 5 项
    TestStep115MergeQueueReadiness + 2 个纯函数判据）
        test_merge_queue_is_currently_disabled
        test_merge_group_trigger_is_absent
        test_current_decision_is_defer
        test_future_enablement_requires_merge_group_trigger（真值表：启用而无触发 ⇒ 不自洽）
        test_required_check_identity_is_unchanged
    纯函数：merge_queue_state_is_consistent() · merge_queue_enablement_is_ready()
未新建第二个 governance framework；未运行全量 pytest / DB Matrix / DeepSeek / 网络测试
python -m compileall -q backend tests scripts → OK（0 errors）
```

## 8. 结论

```text
Current Merge Queue:      OFF
Current merge_group:      ABSENT
Required Check:           Observability Matrix Gate（ENABLED · ACTIVE）
Current Decision:         DEFER
Merge Queue Ready:        NO
Future minimum change:    Add merge_group trigger —— **DO NOT IMPLEMENT NOW**
```
