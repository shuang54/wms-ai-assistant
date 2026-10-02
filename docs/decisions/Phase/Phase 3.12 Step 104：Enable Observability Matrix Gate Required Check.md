继续 Phase 3.12。

当前状态：

```text
Phase 3.12 Step 103 = COMPLETE

origin/main = 4b8a19f468fb2ae40208cb6c9e878b2d4056000b

CI Health
= PASS

Repository Protection
= NOT ENABLED

Required Status Check
= NOT ENABLED

Observability Matrix Gate
= 存在且最新 commit 已 success

Ruleset
= NO VISIBLE RULESET / LIMITED VISIBILITY

Merge Queue
= NOT AUDITED
```

# Phase 3.12 Step 104：Enable Observability Matrix Gate Required Check

## 一、唯一目标

只完成：

```text
main
  ↓
Required Status Check
  ↓
Observability Matrix Gate
```

目标：

> 将已经验证稳定的 `Observability Matrix Gate` 设置为 `main` 的 Required Status Check。

GitHub 官方要求配置 Required Status Check 需要仓库管理员/Owner 权限，并且 Required Check 必须成功通过才能满足保护条件。

---

# 二、严格范围

允许：

```text
GitHub main branch
Required Status Check
Observability Matrix Gate
```

禁止同时修改：

```text
Branch Protection 其他策略
PR Review
Required approving reviews
Code Owner review
Dismiss stale reviews
Enforce admins
Restrict pushes
Restrict deletions
Linear history
Force push policy
Conversation resolution
Merge Queue
Auto Merge
PR Comment
Dashboard
Telemetry
OpenTelemetry
Prometheus
Langfuse
```

特别注意：

**不要把整个 Branch Protection 一次性打开。**

本阶段只处理 Required Status Check。

---

# 三、开始前先做权限检查

不要直接写 API。

先检查：

```text
GitHub Token 是否存在
gh CLI 是否可用
```

如果存在 GitHub Token：

只检查其权限，不输出 Token 内容。

如果没有：

```text
Phase 3.12 Step 104 = BLOCKED
Reason = GitHub Administration write permission unavailable
```

不要要求用户把 Token 明文贴到聊天里。

---

# 四、启用前必须重新确认当前状态

重新读取：

```text
GET /repos/shuang54/wms-ai-assistant/branches/main
```

确认：

```text
protected = false
protection.enabled = false
required_status_checks.enforcement_level = off
contexts = []
checks = []
```

同时确认：

```text
main HEAD = 4b8a19f...
```

---

# 五、确认 Check Identity

从当前 Workflow：

```text
.github/workflows/observability-matrix-gate.yml
```

确认：

```text
job key:
observability-matrix-gate

job name:
Observability Matrix Gate
```

确认只有一个同名 job。

Required Check 使用：

```text
Observability Matrix Gate
```

不要使用：

```text
observability-matrix-gate
```

不要修改 workflow 来适配。

---

# 六、确认最新 Check 成功

针对：

```text
4b8a19f468fb2ae40208cb6c9e878b2d4056000b
```

读取 Check Runs。

必须存在：

```text
name = Observability Matrix Gate
status = completed
conclusion = success
head_sha = 4b8a19f...
```

如果不存在：

```text
STOP
```

不要启用 Required Check。

GitHub 官方明确要求 Required Check 满足最新 commit 的成功状态；历史 commit 的成功不能替代最新 commit。

---

# 七、Required Check 写入策略

只有以下条件全部满足才允许写：

```text
□ 当前 HEAD = 4b8a19f
□ origin/main = 4b8a19f
□ 最新 Check = success
□ Check name = Observability Matrix Gate
□ Job 唯一
□ GitHub Administration write permission available
□ 当前 Required Check = off
```

---

# 八、只修改 Required Status Check

优先使用 GitHub 官方支持的 Branch Protection API。

需要注意：

GitHub 的 Required Status Check 更新属于 Branch Protection 管理权限范围。

本阶段目标配置：

```text
required_status_checks:
    strict = false
    checks:
        - context = "Observability Matrix Gate"
```

或者使用当前 API 可接受的等价 `contexts` 表达。

### 重要

不要因为启用 Required Check 顺便修改：

```text
required_pull_request_reviews
enforce_admins
restrictions
required_linear_history
allow_force_pushes
allow_deletions
required_conversation_resolution
```

保持其他设置原样。

---

# 九、不要误覆盖现有 Branch Protection

如果 Branch Protection API 返回：

```text
existing protection configuration
```

必须先读取并保存当前配置。

如果 API 只能通过整体 PUT 更新：

**不要直接猜参数。**

必须：

```text
读取现有配置
+
只改变 required_status_checks
+
其余字段保持原值
```

如果无法安全做到：

```text
Phase 3.12 Step 104 = BLOCKED
```

不要执行写操作。

---

# 十、Ruleset 情况

如果在 Step 104 开始前发现已经出现：

```text
Ruleset
```

且它覆盖：

```text
main
```

并包含：

```text
required_status_checks
```

立即停止。

不要同时操作 Branch Protection 和 Ruleset。

报告：

```text
Existing governance rule detected.
Manual governance decision required.
```

---

# 十一、写入后立即重新读取

写入成功后必须重新查询：

```text
GET /repos/shuang54/wms-ai-assistant/branches/main
```

以及：

```text
GET /repos/shuang54/wms-ai-assistant/branches/main/protection
```

如果权限允许。

验证：

```text
Required Status Check = enabled

Observability Matrix Gate
    ↓
required
```

如果 Branch Protection 主接口仍然 401：

使用能够读取到的公开/可授权状态进行交叉验证。

如果无法确认：

```text
Step 104 = NOT VERIFIED
```

不要声称成功。

---

# 十二、不要修改 Workflow

本阶段：

```text
.github/workflows/observability-matrix-gate.yml
```

必须保持：

```text
unchanged
```

特别不要为了 Required Check 添加：

```yaml
merge_group:
```

Merge Queue 不是本阶段目标。

---

# 十三、不要修改代码

必须保持：

```text
backend = unchanged
Gate = unchanged
Adapter = unchanged
CLI = unchanged
Baseline = unchanged
```

---

# 十四、测试

本阶段不需要重新执行完整 DB Matrix。

只运行：

```powershell
python -m pytest -q tests/test_github_actions_matrix_gate.py
```

以及：

```powershell
python -m compileall -q backend tests scripts
```

如果 GitHub 配置写入成功：

重新确认最新 `main` Check：

```text
Observability Matrix Gate
= success
```

---

# 十五、GitHub 写操作审计

必须记录：

```text
HTTP method
API endpoint
HTTP status
target branch
required check name
```

不要记录：

```text
Authorization header
GitHub Token
Secret
```

---

# 十六、最终状态模型

成功时：

```text
CI Health
= PASS

Repository Protection
= 视实际 API 返回

Required Status Check
= ENABLED

Required Check
= Observability Matrix Gate

Latest Commit
= 4b8a19f...

Latest Check
= SUCCESS

Workflow
= unchanged

Backend
= unchanged
```

注意：

不要把：

```text
Required Status Check = ENABLED
```

扩大解释成：

```text
完整 Branch Protection 已启用
```

本阶段只启用了 Required Check。

---

# 十七、失败处理

### 权限不足

```text
BLOCKED
```

不要修改。

### API 403

```text
BLOCKED
Reason = insufficient administration permission
```

### API 422

```text
BLOCKED
```

记录 GitHub 返回的安全错误摘要，但不要泄露 Token。

### 写入成功但读取无法验证

```text
NOT VERIFIED
```

不要报告 COMPLETE。

### 检测到已有 Ruleset

```text
BLOCKED
```

等待人工决策。

---

# 十八、Documentation

新增：

```text
docs/evaluation/phase-3.12-step-104-required-check.md
```

记录：

```text
Before
After
Required Check Name
Target Branch
Latest Commit
Latest CI Result
Authorization
API Result
Other Protection Settings
Workflow Changes
Backend Changes
```

如果失败，也记录失败原因。

---

# 十九、最终报告

成功：

```text
Phase 3.12 Step 104 COMPLETE

1. Target Repository
2. Target Branch
3. Required Check
4. Before
5. Write Operation
6. After
7. Latest Commit
8. Latest CI Check
9. Other Branch Protection Settings
10. Ruleset
11. Workflow
12. Backend
13. Tests
14. compileall
15. Git Diff
16. Authorization
17. Limitations

Required Status Check:
ENABLED

Phase 3.12 Step 104 READY
Phase 3.12 Step 104 STOP
```

权限不足：

```text
Phase 3.12 Step 104 BLOCKED

Reason:
GitHub Administration write permission unavailable.

No repository settings changed.

Phase 3.12 Step 104 STOP
```

---

# 二十、硬停止

完成后立即 STOP。

不要进入：

```text
Step 105 Branch Protection
Step 106 Merge Queue
PR Automation
PR Comment
Dashboard
Telemetry
OpenTelemetry
Prometheus
Langfuse
```

等待下一步明确指令。
