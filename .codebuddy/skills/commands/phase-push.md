---

description: 推送当前已 Release Ready 的 Phase branch，并准备 PR；不自动 Merge
allowed-tools: Read, Bash(git:*), Bash(powershell:*)
disable-model-invocation: true
------------------------------

请使用 `phase-git-governance` Skill。

用户已经明确确认 Push。

执行：

1. `git status --short`
2. `git branch --show-current`
3. `git log --oneline -5`
4. 确认当前不是 `main`。
5. 确认工作区 clean。
6. 确认当前 Phase 已经 `PHASE RELEASE READY`。
7. 检查最近 commit。
8. 执行：

```powershell
git push -u origin <current-branch>
```

9. Push 成功后确认：

```powershell
git status --short
git log -1 --oneline
```

10. 输出 PR 建议：

```text
PR Title:
PR Summary:
Tests:
Matrix:
Risk:
```

如果项目当前已经配置 GitHub PR 工具，并且用户明确要求“创建 PR”，可以创建 PR。

否则：

```text
PUSH COMPLETE
等待用户确认创建 PR
```

禁止：

```text
merge
squash
rebase
force push
删除 branch
进入下一 Phase
```

最终：

```text
PUSH COMPLETE
STOP
```
