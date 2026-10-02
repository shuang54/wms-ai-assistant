继续执行 **Phase 3.12 Step 114**。

当前 Step 113 已完成。

当前状态：

```text
branch:
ci/phase-3.12-steps-102-107

HEAD:
1e0b0be9ed2663fc7ffede4ef910181e43f05b95

latest commit:
test: Phase 3.12 Step 109-110 governance audit

origin/main:
07e92868d1952c5c879d0625c70bd633c59d1191
```

当前未推送 commit：

```text
1e0b0be  Step 113
2d93d5f  Step 112 merge
fdefcea  Step 108
```

当前唯一未跟踪文件：

```text
docs/decisions/Phase/Phase 3.12 Step 113 提交边界审计与本地Commit.md
```

**这个文件不要加入本次 commit。**

---

# 一、Step 114 唯一目标

完成：

```text
确认 HEAD
 ↓
确认 working tree
 ↓
Push branch
 ↓
确认 GitHub Actions
 ↓
创建/确认 PR
 ↓
确认 Required Check
 ↓
STOP
```

本阶段不修改生产代码。

---

# 二、Push 前检查

执行：

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git rev-parse origin/main
```

确认：

```text
HEAD = 1e0b0be
branch = ci/phase-3.12-steps-102-107
```

---

# 三、确认未跟踪文件不会进入 Push

执行：

```powershell
git ls-files --others --exclude-standard
```

必须看到：

```text
docs/decisions/Phase/Phase 3.12 Step 113 提交边界审计与本地Commit.md
```

确认它只是：

```text
untracked
```

不要：

```text
git add
git commit
```

---

# 四、再次确认 Production Safety

执行：

```powershell
git diff origin/main..HEAD -- backend
```

必须为空。

执行：

```powershell
git diff origin/main..HEAD -- .github/workflows
```

必须为空。

确认：

```text
backend = unchanged
workflow = unchanged
Gate = unchanged
Adapter = unchanged
CLI = unchanged
Baseline = unchanged
```

---

# 五、Push

现在执行：

```powershell
git push origin ci/phase-3.12-steps-102-107
```

允许：

```text
push = YES
```

禁止：

```text
force push
--force
--force-with-lease
```

---

# 六、Push 后验证

执行：

```powershell
git rev-parse HEAD
git rev-parse origin/ci/phase-3.12-steps-102-107
```

要求：

```text
HEAD == origin/ci/phase-3.12-steps-102-107
```

然后：

```powershell
git status --short
```

注意：

未跟踪的 Step 113 任务副本仍然可以存在。

不要为了 clean 工作区而：

```text
clean
```

---

# 七、GitHub Actions

等待：

```text
Observability Matrix Gate
```

至少检查：

```text
push
```

对应：

```text
HEAD = 1e0b0be
```

如果存在 PR：

检查：

```text
pull_request
```

对应的最新 PR commit / merge evaluation。

---

# 八、PR

如果 GitHub 上已经存在当前分支 PR：

**不要创建第二个 PR。**

直接使用现有 PR。

如果不存在：

创建一个 PR：

```text
base:
main

head:
ci/phase-3.12-steps-102-107
```

建议标题：

```text
Phase 3.12 — CI Governance Contract Audit
```

Body 简洁说明：

```text
Phase 3.12 CI Governance / Required Check work.

Includes:
- Step 108 CI Governance Contract
- Step 109 Self-Consistency Audit
- Step 110 Commit Boundary Audit
- Step 111 Branch Synchronization Audit
- Step 112 Safe Merge Synchronization
- Step 113 Local Commit Boundary

Production backend unchanged.
GitHub Actions workflow unchanged.
Gate / Adapter / CLI / Baseline unchanged.
```

不要加入：

```text
API Key
token
credentials
database URL
```

---

# 九、Required Check 验证

这是本阶段最重要的部分。

确认 PR Checks 中出现：

```text
Observability Matrix Gate
```

并且标记：

```text
Required
```

检查：

```text
push check
pull_request check
```

如果两个都存在，分别记录。

---

# 十、Required Check 成功条件

只有当最新 PR HEAD 对应的：

```text
Observability Matrix Gate
```

最终为：

```text
SUCCESS
```

才算：

```text
Step 114 READY
```

GitHub 的 Required Check 必须针对最新 commit 成功，旧 commit 的成功不能替代最新 commit。

---

# 十一、如果 Required Check 失败

**立即 STOP。**

不要：

```text
修改 production
修改 Gate
修改 Adapter
修改 CLI
修改 Baseline
修改 Workflow
修改 Ruleset
```

只报告：

```text
Required Check FAILED
```

并记录：

```text
run id
commit SHA
failed step
failure message
```

等待下一步。

---

# 十二、如果 Push 失败

如果：

```text
git push
```

失败：

不要：

```text
force push
rebase
reset
```

只报告：

```text
push failed
```

以及 Git 输出。

---

# 十三、如果 PR 创建失败

不要修改代码。

报告：

```text
PR creation failed
```

以及具体原因。

---

# 十四、不要 Merge

本阶段：

```text
禁止 merge PR
```

只验证：

```text
Push
→ GitHub Actions
→ Required Check
→ PR
```

GitHub 文档明确要求保护分支上的 Required Check 在合并前成功。

---

# 十五、不要处理 Merge Queue

当前 Phase 3.12 已明确：

```text
Merge Queue = DEFER
merge_group trigger = absent
```

本阶段不要：

```text
enable Merge Queue
修改 workflow 增加 merge_group
```

---

# 十六、最终报告

严格：

```text
【Phase 3.12 Step 114 COMPLETE】

1. Branch
- branch:
- local HEAD:
- remote HEAD:

2. Push
- result:
- force push: NO

3. PR
- existing/new:
- PR number:
- base:
- head:

4. GitHub Actions
- push check:
- pull_request check:
- latest SHA:

5. Required Check
- name: Observability Matrix Gate
- Required: YES/NO
- status: SUCCESS/FAILURE/PENDING

6. Production Safety
- backend: unchanged
- workflow: unchanged
- Gate: unchanged
- Adapter: unchanged
- CLI: unchanged
- Baseline: unchanged

7. Untracked Files
- Step113 task copy: preserved/not preserved

8. Git Operations
- push: YES
- merge: NO
- rebase: NO
- reset: NO
- clean: NO

9. Next Step
- NOT DECIDED

Phase 3.12 Step 114 STOP
```

**如果 Required Check 成功，立即 STOP。**

**如果 Required Check 失败，也立即 STOP，不自行修复。**
