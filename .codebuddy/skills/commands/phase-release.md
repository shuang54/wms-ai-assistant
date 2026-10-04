---

description: 执行当前 Phase 发布前完整验收，不 Push、不 Merge
allowed-tools: Read, Bash(git:*), Bash(python:*), Bash(pytest:*), Bash(powershell:*)
disable-model-invocation: true
------------------------------

请使用 `phase-git-governance` Skill。

执行当前 Phase Release Gate。

严格执行：

1. 确认当前 Phase branch。
2. 确认工作区状态。
3. 运行项目要求的完整 pytest。
4. 运行项目要求的 DB regression（如果当前 Phase 要求）。
5. 运行：
   `python -m compileall -q backend tests scripts`
6. 运行项目已有 lint（如果存在）。
7. 运行当前项目 Regression Matrix / Matrix Gate。
8. 检查 DB residue。
9. 检查：

   * secrets
   * migrations
   * baseline drift
   * 无关文件
   * 范围外修改
10. 检查：
    `git diff --check`
11. 检查：
    `git diff main...HEAD --stat`
12. 检查：
    `git diff main...HEAD`

如果任何一项失败：

```text
PHASE RELEASE BLOCKED
```

不要 commit。
不要 push。
不要创建 PR。
不要 merge。

如果全部通过：

```text
PHASE RELEASE READY
```

输出：

```text
Phase:
Branch:
Tests:
DB:
Matrix:
Compile:
Lint:
Diff:
Secrets:
DB residue:
Release status:

PHASE RELEASE READY
STOP
```

禁止：

```text
git push
PR
merge
delete branch
进入下一 Phase
```
