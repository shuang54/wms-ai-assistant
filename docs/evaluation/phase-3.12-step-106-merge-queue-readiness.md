# Phase 3.12 Step 106 — Merge Queue / merge_group Readiness Audit

> **READ ONLY**。未启用 Merge Queue · 未修改 Ruleset · 未修改 Workflow ·
> 未创建 PR · 未 push / merge。

---

## 1. Workflow（本地解析，只读）

```text
files:                      .github/workflows/observability-matrix-gate.yml（唯一 workflow）
workflow name:              Observability Matrix Gate
jobs:                       ["observability-matrix-gate"]（唯一）
job name:                   Observability Matrix Gate
triggers:                   push ✔ · pull_request ✔
merge_group:                **ABSENT** ✘
（无 continue-on-error · 无 `|| true` · 无 `exit 0` —— Step 97/100 起由测试强制）
```

## 2. Required Check（GitHub Ruleset，只读）

```text
Ruleset:        Protect Main（id 24348464）· target = branch · enforcement = active
conditions:     ref_name.include = ["refs/heads/main"] · exclude = []
required_status_checks:
    context        = "Observability Matrix Gate"     ← required ✔
    integration_id = 15368（GitHub Actions）
    strict_required_status_checks_policy = false
    do_not_enforce_on_create = false
其它规则（非本阶段范围，如实记录）：deletion · non_fast_forward · pull_request
created_at / updated_at = 2026-10-02T05:17:18Z（**未变化**）
```

## 3. Merge Queue 状态

```text
规则集 rules 数组（4 条）中**不存在** `merge_queue` 规则
⇒ Require merge queue = **NOT ENABLED**
审计方式：GET /repos/shuang54/wms-ai-assistant/rulesets/24348464（200 · 未认证只读）
（如未来管理员视角与此不一致，以管理员复核为准；本次为可读范围内的直接证据）
```

## 4. 兼容性判定

```text
Current CI               = READY（push + pull_request 触发 · Required Check 已生效 ·
                                   Step 105 已实测 PASS / FAIL 与合并阻断）
Current Merge Queue      = NOT ENABLED
Future Merge Queue ready = **NO**
    Gap：Required Check 存在，但 workflow **未声明 `merge_group` 触发**
          ⇒ 若启用 Merge Queue，merge group 中可能不会产生该 Required Check
Current form valid       = CURRENTLY VALID（因当前无 Merge Queue）
```

## 5. 本阶段不做（按 §八/§十五）

```text
· **未**添加 merge_group 触发（未来启用 Merge Queue 时单独起阶段处理）
· **未**启用 Merge Queue / **未**修改 Protect Main / Required Check / strict
· **未**修改 Gate / Adapter / CLI / Baseline / backend
```

## 6. 测试

```text
tests/test_github_actions_matrix_gate.py → **41 passed**（Step 106 新增 2 项：
    test_workflow_triggers_are_push_and_pull_request_only（无 merge_group）
    test_merge_queue_readiness_contract_is_frozen（MERGE_QUEUE_READINESS_CONTRACT 冻结））
未新增 tests/test_merge_group_readiness.py（既有 parser / 基础设施足够，§九 优先复用）
未运行 full pytest / RUN_DB_TESTS / DeepSeek / Matrix 执行 / GitHub Actions
python -m compileall -q backend tests scripts → OK（0 errors）
```

## 7. 契约快照（测试侧冻结）

```python
MERGE_QUEUE_READINESS_CONTRACT = {
    "merge_queue": "NOT ENABLED",
    "workflow_merge_group": "absent",
    "required_check": "Observability Matrix Gate",
    "readiness": "NOT READY FOR MERGE_QUEUE",
}
```

`NOT READY FOR MERGE_QUEUE` **不是代码失败**，仅表示尚未配置 `merge_group` 触发。

## 8. 结论

```text
CURRENT CI READY   = YES
MERGE_QUEUE READY  = NO
Reason             = Required Check 已存在且生效，但 workflow 无 merge_group 触发
（属**配置缺口发现**，本阶段不修复）
```
