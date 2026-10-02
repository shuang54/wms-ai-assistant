# Phase 3.12 Step 103 — Repository Protection / Required Check 只读治理审计

> **READ ONLY**。本阶段未执行任何 POST / PUT / PATCH / DELETE；未创建或修改
> Branch Protection / Ruleset / Required Status Check / Merge Queue / PR 设置。

---

## 1. Branch Protection

```text
GET /repos/shuang54/wms-ai-assistant/branches/main/protection
    → **401 Unauthorized**（多次一致）
    ⇒ 该端点的完整字段（required_pull_request_reviews / enforce_admins / restrictions /
       required_linear_history / allow_force_pushes / allow_deletions /
       required_conversation_resolution）= **NOT AUDITED**
```

```text
但公开可读的分支元数据提供了直接证据（见 §5）：
    protected = false
    protection.enabled = false
    protection.required_status_checks.enforcement_level = "off"
```

---

## 2. Rulesets

```text
GET /repos/shuang54/wms-ai-assistant/rulesets → **200 OK · []**
    visible rulesets = 0
    ⇒ 可见范围内：无 target=branch / enforcement=active / required_status_checks 的 ruleset
注意：未认证读取可能受权限过滤 ⇒ `[]` **不能单独证明**"仓库没有任何 ruleset"
```

---

## 3. Required Status Check

```text
GET …/branches/main/protection                              → 401（NOT AUDITED）
GET …/branches/main/protection/required_status_checks       → 401（NOT AUDITED）

公开分支元数据（200）给出的直接证据：
    protection.required_status_checks = {
        "enforcement_level": "off",
        "contexts": [],
        "checks": []
    }
⇒ **Observability Matrix Gate 目前不是 main 的 Required Check**
  （checks / contexts 均为空，enforcement_level = off）

必须区分：
    Check exists（是）      —— 4b8a19f 上有 success 的 Observability Matrix Gate
    Check is required（否） —— 未出现在任何 required 配置中
历史 Check 成功 ≠ Required Check 配置
```

---

## 4. Latest CI Check

```text
GET /repos/shuang54/wms-ai-assistant/commits/main/check-runs → 200

    total_count = 1
    name        = Observability Matrix Gate
    status      = completed
    conclusion  = **success**
    head_sha    = 4b8a19f468fb2ae40208cb6c9e878b2d4056000b（= main 头，与本阶段目标一致）
    started_at  = 2026-10-02T04:27:12Z
    completed_at= 2026-10-02T04:28:48Z
    （job 110705518409 · run 36964609587）

性质：**CI success evidence**，**不是** Required Check configuration evidence
```

---

## 5. main Branch Metadata

```text
GET /repos/shuang54/wms-ai-assistant/branches/main → **200 OK**

    name       = main
    commit.sha = 4b8a19f468fb2ae40208cb6c9e878b2d4056000b
    protected  = **false**
    protection = {
        "enabled": **false**,
        "required_status_checks": {
            "enforcement_level": "off",
            "contexts": [],
            "checks": []
        }
    }

⇒ 记录事实：Branch metadata reports protected = false
⇒ 不把该字段扩大解释为"完整 Required Check 配置"（完整配置仍需管理员凭据）
```

---

## 6. Workflow Identity

```text
.github/workflows/observability-matrix-gate.yml（本地读取，未修改）
    jobs: observability-matrix-gate（**唯一**）
    job name: Observability Matrix Gate（稳定 · 唯一）
    on: push + pull_request（仍存在；无 schedule / workflow_dispatch / merge_group）
（由 tests/test_github_actions_matrix_gate.py::TestStep103GovernanceIdentity 与
  TestStep101GovernanceReadiness 强制）
```

---

## 7. Merge Queue

```text
Merge Queue = **NOT AUDITED**
    · 仓库元数据未暴露 merge queue 字段；无管理员凭据
    · 仅记录事实：**Workflow currently has no merge_group trigger.**
    · 不得据此判定 "Merge Queue disabled"

若未来启用 Merge Queue 且该 Workflow 成为 Required Check
    → 需单独评估为 Workflow 增加 `merge_group` 触发
      （GitHub 文档：merge queue 中的 Required Check 需要 workflow 响应 merge_group）
    → 当前**不新增** merge_group
```

---

## 8. Authorization Limitations

```text
GitHub Token：不存在（未配置、未使用）
gh CLI：未安装
Branch Protection API（/branches/main/protection）：**不可读**（401）→ NOT AUDITED
Required Status Checks 端点：**不可读**（401）→ NOT AUDITED
Branch metadata（/branches/main）：**可读**（200，含 protected / protection 摘要）
Ruleset API（/rulesets）：**可读**（200 · []），但可能受权限过滤
Check Runs：**可读**（200）
Merge Queue：**不可读** → NOT AUDITED
Run logs：**不可读**（403，需登录）
```

---

## 9. Governance State（分别给出，不合并为一个 READY）

```text
CI Health              = **PASS**（4b8a19f · Observability Matrix Gate · completed/success）
Repository Protection  = **NOT ENABLED**（branch metadata: protected=false /
                         protection.enabled=false；完整字段仍 NOT AUDITED）
Required Status Check  = **NOT ENABLED**（enforcement_level=off · contexts=[] · checks=[]；
                         端点级仍 NOT AUDITED）
Ruleset                = **NO VISIBLE RULESET / LIMITED VISIBILITY**（200 · []）
Merge Queue            = **NOT AUDITED**（Workflow 当前无 merge_group 触发）
```

---

## 10. 审计命令

```text
GET /repos/shuang54/wms-ai-assistant/branches/main                        → 200
GET /repos/shuang54/wms-ai-assistant/branches/main/protection             → 401
GET /repos/shuang54/wms-ai-assistant/branches/main/protection/required_status_checks → 401
GET /repos/shuang54/wms-ai-assistant/rulesets                             → 200 · []
GET /repos/shuang54/wms-ai-assistant/commits/main/check-runs              → 200 · 1 · success

python -m pytest -q tests/test_github_actions_matrix_gate.py              → 39 passed
python -m compileall -q backend tests scripts                             → OK（0 errors）
```
