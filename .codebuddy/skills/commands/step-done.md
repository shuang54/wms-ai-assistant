---

description: 验收当前 Phase Step，运行测试、检查 diff，并自动创建 Step Commit
allowed-tools: Read, Bash(git:*), Bash(python:*), Bash(pytest:*), Bash(powershell:*)
disable-model-invocation: true
------------------------------

请使用 `phase-git-governance` Skill。

执行当前 Step 验收。

严格执行：

1. 确认当前 branch。
2. `git status --short`
3. `git diff --check`
4. `git diff --stat`
5. 检查当前 Step 修改是否越界。
6. 根据当前 Step 要求运行 targeted tests。
7. 如果存在 compileall 要求，运行。
8. 检查 secrets。
9. 检查 DB migration / DB writes / network / LLM 是否违反当前 Step。
10. 如果失败：

* 不 commit
* 输出失败原因
* STOP

11. 如果全部通过：

* 只 stage 当前 Step 修改文件
* 创建 Step Commit
* Commit message 使用：
  `feat: Phase X.Y Step N：<描述>`
* `git status --short`
* `git log -1 --oneline`

禁止：

```text
git push
创建 PR
merge
删除 branch
进入下一个 Step
```

最后输出：

```text
STEP COMMIT COMPLETE
Commit:
Tests:
Files:
STOP
```
