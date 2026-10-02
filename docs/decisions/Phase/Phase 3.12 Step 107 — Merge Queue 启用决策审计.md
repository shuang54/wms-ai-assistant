# Phase 3.12 Step 107 — Merge Queue 启用决策审计

## 一、目标

基于 Step 106：

```text
Current CI
    ↓
Required Check = YES
    ↓
PR Blocking = VERIFIED

Merge Queue
    ↓
NOT ENABLED

Workflow
    ↓
merge_group = ABSENT
```

本 Step 不修改任何代码。

只回答：

> **当前项目是否已经有足够理由启用 Merge Queue？**

以及：

> **如果暂时不启用，未来启用时最小改动是什么？**

---

# 二、严格范围

本阶段只允许：

```text
Read-only analysis
Documentation
Tests for decision contract
```

禁止：

```text
修改 Workflow
修改 Ruleset
启用 Merge Queue
创建 PR
push
merge
修改 Required Check
修改 Gate
修改 Adapter
修改 CLI
修改 Baseline
修改 backend
```

---

# 三、Step 1：读取当前 CI 架构

确认：

```text
.github/workflows/observability-matrix-gate.yml
```

当前：

```text
push
pull_request
```

没有：

```text
merge_group
```

不要修改。

---

# 四、Step 2：确认 Required Check

继续确认：

```text
Protect Main
    ↓
Observability Matrix Gate
    ↓
Required
```

当前：

```text
strict = false
```

保持不变。

---

# 五、Step 3：评估当前项目是否需要 Merge Queue

只基于事实判断，不做“最佳实践”式扩展。

检查：

```text
1. main 是否存在高并发 PR
2. 是否存在频繁 merge conflict
3. 是否存在多个 PR 同时等待 merge
4. 是否存在 merge 后 main 经常重新失败的问题
5. 当前是否已经有较长 CI pipeline
6. Required Check 是否已经能够阻止不合格 PR
```

如果没有真实数据：

```text
UNKNOWN
```

不要猜。

---

# 六、Step 4：Merge Queue 的实际价值

建立一个简单矩阵：

| 能力                  | 当前状态    |
| ------------------- | ------- |
| PR Required Check   | YES     |
| Push Gate           | YES     |
| Pull Request Gate   | YES     |
| Merge Queue         | NO      |
| merge_group trigger | NO      |
| Merge Queue 实际问题证据  | UNKNOWN |

不要给：

```text
1~10 分
BEST / WORST
```

只记录事实。

---

# 七、Step 5：定义未来最小变更

如果未来决定启用 Merge Queue：

Workflow 最小变化原则：

```yaml
on:
  push:
  pull_request:
  merge_group:
    types: [checks_requested]
```

如果只针对 main：

```yaml
merge_group:
  types: [checks_requested]
  branches: [main]
```

具体最终写法留到真正启用阶段。

GitHub 官方文档显示，`merge_group` 的 `checks_requested` 是用于 Merge Queue 检查触发的事件，并且可以按目标分支过滤。

---

# 八、Step 6：未来启用时必须保持

不能改变：

```text
Observability Matrix Gate
```

的：

```text
job name
check name
Gate semantics
Adapter semantics
Baseline
```

即：

```text
merge_group
    ↓
同一个 Observability Matrix Gate
```

而不是创建第二套：

```text
Merge Queue Gate
```

---

# 九、Step 7：Decision Contract

在现有测试文件中增加一个非常小的 Contract：

```text
MERGE_QUEUE_DECISION_CONTRACT
```

表达：

```text
current_enabled = false
current_workflow_merge_group = false
required_check = Observability Matrix Gate
future_required_change = add merge_group trigger
```

不要把“未来一定启用”写死。

---

# 十、Step 8：测试

只运行：

```powershell
python -m pytest -q tests/test_github_actions_matrix_gate.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

不要：

```text
full pytest
RUN_DB_TESTS
DeepSeek
GitHub Actions
修改 Workflow
```

---

# 十一、Git Diff

确认：

```text
.github/workflows/observability-matrix-gate.yml
    0 modified

backend/
    0 modified

Gate
    0 modified

Adapter
    0 modified

CLI
    0 modified

Baseline
    0 modified
```

允许：

```text
tests/test_github_actions_matrix_gate.py
docs/evaluation/phase-3.12-step-107-merge-queue-decision.md
```

---

# 十二、最终报告

严格：

```text
【Phase 3.12 Step 107 COMPLETE】

1. Current CI
- push:
- pull_request:
- Required Check:

2. Merge Queue
- enabled:
- audited:

3. Current Evidence
- concurrent PR:
- merge conflict:
- post-merge instability:
- CI duration:

4. Decision
- ENABLE NOW:
- DEFER:

5. Future Minimum Change
- ...

6. Contract
- Required Check:
- Workflow:
- merge_group:

7. Tests
- ...
- compileall:

8. Git Diff
- workflow:
- backend:
- gate:
- adapter:
- cli:
- baseline:

9. Network / DB / LLM
- ...

10. Conclusion
```

---

# 十三、默认 STOP 条件

如果没有足够事实证明需要 Merge Queue：

```text
Decision = DEFER
```

不要因为“以后可能需要”就提前修改 Workflow。

**Step 107 完成后立即 STOP。**

不进入：

```text
Step 108
Merge Queue Enablement
merge_group implementation
PR Automation
Dashboard
Telemetry
```
