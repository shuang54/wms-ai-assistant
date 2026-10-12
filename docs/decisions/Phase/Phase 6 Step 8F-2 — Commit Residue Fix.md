# Phase 6 Step 8F-2 — Commit Residue Fix

项目：

`D:\coding\ai\wms-ai-assistant`

## 一、目标

将已经通过 Step 8F-1 只读审查的修改单独提交。

预期唯一业务修改文件：

```text
tests/test_assistant_trace_timeline_regression.py
```

当前预期 HEAD：

```text
efbc8cb58220550ca726ccb5e9241c15d0d59074
```

## 二、提交前检查

运行：

```powershell
git status --short
git rev-parse HEAD
git diff --check
git diff --stat
git diff -- tests/test_assistant_trace_timeline_regression.py
```

确认：

1. HEAD 与预期一致。
2. 该 tracked 文件是唯一需要提交的业务修改。
3. 不包含其他阶段的修改。
4. 不包含 untracked 任务书副本。
5. `git diff --check` 通过。

如果 HEAD 已变化，或者发现其他 tracked 文件被修改，立即停止并报告。

## 三、提交

只 stage 指定文件：

```powershell
git add tests/test_assistant_trace_timeline_regression.py
git diff --cached --check
git diff --cached --stat
git diff --cached --name-only
```

确认 staged 文件清单只有：

```text
tests/test_assistant_trace_timeline_regression.py
```

然后提交：

```powershell
git commit -m "test: fix matrix DB residue attribution"
```

不得使用：

```text
git add .
git add -A
```

不得将任务书副本或其他文件加入提交。

## 四、提交后验证

运行：

```powershell
git rev-parse HEAD
git log -1 --oneline
git show --stat --oneline HEAD
git show --check --oneline HEAD
git status --short
```

确认：

* 新提交只包含目标文件。
* 提交成功。
* 没有推送。
* 没有修改历史 baseline。
* 没有数据库写入。
* 没有修改生产代码。
* 未提交的任务书副本仍保持原状。

注意：`git show --check --oneline HEAD` 可能仍报告旧提交 `efbc8cb` 中的：

```text
docs/roadmap/Project-Recovery-Audit.md:146
```

尾随空格。这是历史提交中的独立卫生问题，不要在本步骤顺手修改或 amend 旧提交。

## 五、最终报告

```text
【Phase 6 Step 8F-2 — Commit Report】

1. Commit SHA
2. Commit message
3. 提交文件清单
4. 提交前检查
5. 提交后检查
6. 工作区状态
7. 数据库写入
8. Push 状态
9. 历史 trailing whitespace
10. 最终结论

Phase 6 Step 8F-2 完成后立即 STOP。
不推送，不进入 Phase 7。
```

## 六、严格边界

本步骤只提交已经审查通过的修改。

不重跑全量测试；Step 8F 已报告全量 DB 和离线回归通过。若提交前发现代码与审查版本不同，停止并重新审查。

不得自动推送远程仓库，不得进入 Phase 7。
