# Phase 3.12 Step 101 — Repository Governance / Required Check Readiness Audit

> **READ ONLY**。本阶段不创建 / 修改任何 Branch Protection、Ruleset、Required Status Check、
> Merge Queue、PR Automation；不使用 GitHub Token 做任何写操作。
> 唯一问题：`Observability Matrix Gate` 是否具备成为 `main` 的 Required Status Check 的条件？

---

## 1. Repository

```text
full_name:       shuang54/wms-ai-assistant
id:              1379937273
visibility:      public（private = false）
default_branch:  main
fork:            false · archived: false · disabled: false
pushed_at:       2026-10-02T01:11:20Z
查询方式：GET /repos/shuang54/wms-ai-assistant（200 · 只读）
```

---

## 2. Main Branch

```text
default_branch = main

远端（origin/main）最新 commit：
    510ab81caf4d56152bbaa4f71be54702b94a99cc
    docs:Phase 3.12 Step 99：记录真实 GitHub Actions Run #2   ← **有 CI check（success）**

本地 HEAD（**尚未 push**）：
    bff93dba0df01258cd80d53ab53f6ff8f2014e6e
    feat:Phase 3.12 Step 100：CI Gate Contract Freeze & Repository Readiness Audit
    ⇒ GET /repos/…/commits/bff93db…/check-runs → **422**（远端不存在该 commit）
    ⇒ 该 commit 当前**没有**任何 check run；push 后才会触发 CI

分支保护读取：GET /repos/…/branches/main/protection → **401 Unauthorized**
```

---

## 3. Branch Protection

```text
Branch Protection = **NOT AUDITED**
Reason = GitHub API authorization unavailable（401 Unauthorized）

不因此推断 enabled，也不因此推断 disabled。
未读取到：required_status_checks · required_pull_request_reviews · enforce_admins ·
         restrictions · required_linear_history · allow_force_pushes · allow_deletions
（需要仓库管理员凭据；本阶段不申请、不使用 Token）
```

---

## 4. Ruleset

```text
查询：GET /repos/shuang54/wms-ai-assistant/rulesets → **200 OK**
结果：**`[]`（0 个 ruleset）**

⇒ 当前仓库**不存在**任何 repository ruleset
⇒ 因此不存在"target = main / enforcement = active / required_status_checks"的 ruleset 配置
注意：未认证读取返回的空集**可能**受权限过滤影响；据此只能得出
      "可见范围内无 ruleset"，不能替代管理员视角的确认
```

---

## 5. Required Status Check

```text
Required Status Check = **NOT ENABLED / NOT AUDITED**
    · Branch Protection 读取失败（401）⇒ 无法确认 required_status_checks 上下文
    · Ruleset 查询成功但为空 ⇒ 可见范围内无 required status check 规则
    · 本阶段**未**创建 / 启用任何 Required Status Check

候选 check 名称（冻结，§八）：
    Observability Matrix Gate        ← job name
    （对应 job key：observability-matrix-gate；**唯一**，无同名 job）
    GitHub Actions required check 名称来自 job name ⇒ 必须保持唯一
    （由 tests/test_github_actions_matrix_gate.py
     ::TestStep101GovernanceReadiness::test_required_check_name_is_stable_and_unique 强制）
```

---

## 6. Latest Successful Check

```text
查询：GET /repos/…/commits/510ab81caf4d56152bbaa4f71be54702b94a99cc/check-runs（200）

    total_count = 1
    name        = Observability Matrix Gate
    status      = completed
    conclusion  = **success**
    head_sha    = 510ab81caf4d56152bbaa4f71be54702b94a99cc（**与最新 commit 一致**）
    started_at  = 2026-10-02T01:11:26Z
    completed_at= 2026-10-02T01:12:29Z
    html_url    = …/actions/runs/36949724371/job/110659745580

⇒ Required Status Check 要求的"最新 commit 上成功"**已满足**
  （旧 commit 的成功不能代替最新 commit；此处即**远端最新** commit）

补充（审计中发现的事实，未修改任何设置）：
    本地 HEAD bff93db 尚未 push ⇒ 远端无该 commit（check-runs 查询 422）⇒ 无 check
    ⇒ 若将 bff93db push 到 main，CI 会重新触发；届时"最新 commit 成功"需重新确认
    （本阶段不执行 push；等待下一步指令）
```

---

## 7. Workflow Trigger

```yaml
on:
  push:
  pull_request:
```

```text
⇒ push + pull_request 均为有效触发（可用于 PR Required Check 评估）
⇒ 不是 workflow_dispatch only / schedule only
⇒ 无 merge_group（Merge Queue 未启用 ⇒ 按 §十一 **不得**新增 merge_group）
（由既有 test_2_triggers_are_pull_request_and_push_only +
  TestStep101GovernanceReadiness::test_triggers_support_push_and_pull_request 强制）
```

---

## 8. Merge Queue

```text
Merge Queue = **NOT AUDITED**
    · 仓库元数据（GET /repos/…）未暴露 merge queue 字段
    · 无管理员凭据读取 /repos/…/merge-queue 类配置
    · Workflow 未包含 `merge_group:` 触发（现状）

规则（§十一）：
    若未来启用 Merge Queue 且该 check 被 Required 使用
        → 需要单独授权为 Workflow 增加 `merge_group` 触发
    当前未启用 ⇒ **不新增** merge_group
```

---

## 9. CI Governance Readiness

```text
判定对象 = CI_READINESS（**不是** REPOSITORY_PROTECTION）

    Workflow PASS                  ✔  Run #2 / Run #3 success；本地 Gate PASS
    Latest Check PASS              ✔  510ab81 上 Observability Matrix Gate = success
    Required Check name stable     ✔  唯一 job · job name = Observability Matrix Gate
    No duplicate job               ✔  jobs = ["observability-matrix-gate"]（唯一）
    No workflow bypass             ✔  无 continue-on-error / || true / exit 0 /
                                      无 job 级或 step 级 `if` 条件跳过
    Trigger valid                  ✔  push + pull_request（非 manual/schedule-only）

⇒ **CI Governance Readiness = READY**
⇒ Repository Protection = **NOT AUDITED**（Branch Protection 401；Ruleset 可见范围为空）
```

---

## 10. Repository Protection Status

```text
Repository Protection Status = **NOT CHANGED / NOT AUDITED**

未执行（§三/§十四 明确禁止）：
    创建 Branch Protection · 创建 Ruleset · 启用 Required Status Check ·
    修改 main protection · 要求 PR review · Require branches up-to-date ·
    Merge Queue · Auto Merge · PR Comment · GitHub App / Token 写操作

真正启用保护应作为**后续独立 Phase**（需仓库管理员凭据与明确授权）。
```

---

## 11. Authorization Limitations

```text
* 无 GitHub Token / gh CLI 未安装 ⇒ 所有查询均为**未认证**公开读取
* 可读取：repository 元数据 · check-runs · workflow runs / jobs · rulesets（返回空集）
* 不可读取：branches/main/protection（401）· run logs（403）· merge queue 配置
* 因此：Branch Protection、Required Status Check、Merge Queue 三组结论均为 NOT AUDITED
  （不推断 enabled / disabled）
* 未认证读取的 rulesets 空集可能存在权限过滤；以管理员视角复核为准
```

---

## 12. 审计命令与证据

```powershell
GET /repos/shuang54/wms-ai-assistant                                  → 200（元数据）
GET /repos/shuang54/wms-ai-assistant/rulesets                         → 200 · []
GET /repos/shuang54/wms-ai-assistant/branches/main/protection         → 401 Unauthorized
GET /repos/shuang54/wms-ai-assistant/commits/510ab81…/check-runs      → 200 · 1 条 success
GET /repos/shuang54/wms-ai-assistant/actions/runs                     → 200（Run #1/#2/#3）
GET /repos/shuang54/wms-ai-assistant/actions/runs/<id>/logs           → 403（需登录）

python -m pytest -q tests/test_github_actions_matrix_gate.py          → 37 passed
python -m compileall -q backend tests scripts                         → OK（0 errors）
```
