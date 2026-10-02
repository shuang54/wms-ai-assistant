# Phase 3.12 Step 104 — Enable Observability Matrix Gate Required Check

> 我这一侧执行结果：**BLOCKED**（GitHub Administration write permission unavailable）
> **No repository settings changed by me.**
> 随后由你在 GitHub Web UI 手动完成配置；我做了**只读复核**：
> 结果 = **VERIFIED**（Required Status Check = ENABLED · Observability Matrix Gate = REQUIRED）
> —— 见文末「12. 只读复核（VERIFIED）」。

---

## 1. Before（启用前重新确认，§四）

```text
GET /repos/shuang54/wms-ai-assistant/branches/main → 200

    name          = main
    commit.sha    = 4b8a19f468fb2ae40208cb6c9e878b2d4056000b
    protected     = false
    protection.enabled = false
    protection.required_status_checks.enforcement_level = "off"
    protection.required_status_checks.contexts = []
    protection.required_status_checks.checks   = []

main HEAD = 4b8a19f ✔（与 §四 要求一致）
```

---

## 2. Check Identity（§五）

```text
.github/workflows/observability-matrix-gate.yml（**未修改**）
    job key  = observability-matrix-gate（唯一）
    job name = Observability Matrix Gate（唯一 · 稳定）
Required Check 将使用：Observability Matrix Gate
**不**使用 job key（observability-matrix-gate）；未为适配而修改 workflow
```

---

## 3. Latest CI Result（§六）

```text
GET /repos/shuang54/wms-ai-assistant/commits/main/check-runs → 200
    name = Observability Matrix Gate · status = completed · conclusion = **success**
    head_sha = 4b8a19f468fb2ae40208cb6c9e878b2d4056000b
⇒ "最新 commit 成功"这一前置条件**满足**（Run #4 / 36964609587）
```

---

## 4. Ruleset（§十）

```text
GET /repos/shuang54/wms-ai-assistant/rulesets → 200 · []
⇒ 未发现覆盖 main 且含 required_status_checks 的 ruleset
⇒ 不存在"Branch Protection 与 Ruleset 同时操作"的冲突
```

---

## 5. Authorization（§三 · 关键阻塞点）

```text
GitHub Token 环境变量：GITHUB_TOKEN / GH_TOKEN / GITHUB_PAT / GH_PAT /
                      GITHUB_ACCESS_TOKEN —— **全部 absent**
gh CLI：**NOT available**（未安装）
可用凭据：仅 git credential manager（manager）中 1 条 github.com 条目
          —— 只用于 git push，**不是**可用于 REST API 的 Token；
             scopes 未经确认且不应被提取 / 打印
可用工具：web_fetch = **GET only**（无 PUT / POST / PATCH / DELETE 能力）

⇒ GitHub Administration write permission = **UNAVAILABLE**
```

---

## 6. API Result

```text
**未执行任何写操作**

HTTP method  : 无
API endpoint : 无
HTTP status  : 无
target branch: main（未变更）
required check name: Observability Matrix Gate（未写入）

（只读查询均为 GET：/branches/main · /rulesets · /commits/main/check-runs）
```

---

## 7. After

```text
与 Before **完全一致**（未发生任何变更）：
    protected = false
    protection.enabled = false
    required_status_checks.enforcement_level = off · contexts = [] · checks = []
```

---

## 8. Other Protection Settings

```text
未修改（按 §八 要求保持原样）：
    required_pull_request_reviews · enforce_admins · restrictions ·
    required_linear_history · allow_force_pushes · allow_deletions ·
    required_conversation_resolution · Merge Queue · Auto Merge
（这些字段的当前值因 /branches/main/protection 401 而 NOT AUDITED；本阶段未尝试写入）
```

---

## 9. Workflow / Backend Changes

```text
Workflow：.github/workflows/observability-matrix-gate.yml = **unchanged**
          未新增 merge_group（§十二）
Backend / Gate / Adapter / CLI / Baseline = **unchanged**
```

---

## 10. 目标配置（待授权后执行，仅供记录）

```text
required_status_checks:
    strict = false
    checks:
        - context = "Observability Matrix Gate"

写入策略（§八/§九）：
    先读取现有 protection 配置 → 只改 required_status_checks → 其余字段保持原值
    不一次性打开整个 Branch Protection
验证（§十一）：写入后重读 /branches/main 与 /branches/main/protection
```

---

## 11. 结论（我的执行侧）

```text
Phase 3.12 Step 104 = BLOCKED（我这一侧）
Reason = GitHub Administration write permission unavailable
         （无 GitHub Token · 无 gh CLI · 工具仅支持 GET）
No repository settings changed by me.

解除阻塞的最小条件（任择其一，由你决定，不要将 Token 明文贴入聊天）：
    ① 安装并登录 gh CLI：`gh auth login`（需 repo administration 权限）
    ② 在环境中提供具备 administration:write 的 Token（环境变量形式即可）
    ③ 由你在 GitHub Web UI 手动配置（**你已采用本条**）
```

---

## 12. 只读复核（VERIFIED）

复核时间：2026-10-02（UTC）· 全部为 **GET**，无写操作。

```text
① GET /repos/shuang54/wms-ai-assistant/branches/main → 200
      name = main
      commit.sha = 4b8a19f468fb2ae40208cb6c9e878b2d4056000b
      protected = **true**（此前 false ⇒ 保护已生效）
      protection.enabled = false · required_status_checks = off/[]/[]
      （legacy branch protection 对象**未**承载该配置；实际由 Ruleset 承载，
        故该摘要仍显示 off —— 不能据此判断未启用）

② GET /repos/shuang54/wms-ai-assistant/rulesets → 200
      [ { id: 24348464, name: "Protect Main", target: "branch",
          enforcement: "active", created_at: 2026-10-02T05:17:18Z } ]

③ GET /repos/shuang54/wms-ai-assistant/rulesets/24348464 → 200
      conditions.ref_name.include = ["refs/heads/main"]     ← 覆盖 main ✔
      enforcement = active ✔
      rules:
        - deletion
        - non_fast_forward
        - required_status_checks:
              strict_required_status_checks_policy = false
              do_not_enforce_on_create = false
              required_status_checks = [
                  { context: "Observability Matrix Gate", integration_id: 15368 }
              ]                                              ← **REQUIRED ✔**
        - pull_request:
              required_approving_review_count = 0
              dismiss_stale_reviews_on_push = false
              require_code_owner_review = false
              require_last_push_approval = false
              required_review_thread_resolution = false
              require_extra_approval_for_unattributed_changes = true
              allowed_merge_methods = [merge, squash, rebase]

④ GET /repos/shuang54/wms-ai-assistant/commits/main/check-runs → 200
      Observability Matrix Gate · completed · **success**
      head_sha = 4b8a19f468fb2ae40208cb6c9e878b2d4056000b（= 当前 main HEAD）
      ⇒ Latest CI 在当前 main HEAD 上已验证（自该 commit 以来无新提交）

⑤ GET /repos/shuang54/wms-ai-assistant/branches/main/protection → **401**
      ⇒ legacy 字段（enforce_admins / restrictions / required_linear_history /
        allow_force_pushes / allow_deletions / required_conversation_resolution /
        required_pull_request_reviews）= NOT AUDITED
```

```text
Check exists（是）  —— 4b8a19f 上有 success 的 Observability Matrix Gate
Check is required（是）—— Ruleset 24348464 的 required_status_checks 中
                        明确要求 context = "Observability Matrix Gate"
```

差异说明（如实记录）：

```text
你同时启用了 Ruleset 中的 deletion / non_fast_forward / pull_request 规则
（非本阶段我请求的范围；本阶段未做任何修改，仅如实记录其存在）
Required Check 的 strict = false —— 与 §八 目标一致
```
```
