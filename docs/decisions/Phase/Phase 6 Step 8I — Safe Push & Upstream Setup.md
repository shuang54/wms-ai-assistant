# Phase 6 Step 8I — Safe Push & Upstream Setup

请在本地仓库执行：

`D:\coding\ai\wms-ai-assistant`

## 一、执行目标

仅将本地 `phase5-1` 推送到 `origin/phase5-1`，并设置 upstream。

预期本地 HEAD：

`bc76af763f5dfc236e871c591ebf43fe1e28c716`

预期提交：

`test: fix matrix DB residue attribution`

## 二、第一步：只读预检查

先确认仓库状态：

```powershell
git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git status --short
git remote -v
git remote get-url --push --all origin
git config --get remote.origin.mirror
git config --get-all remote.origin.push
```

必须满足：

- 仓库路径正确。
- 当前分支是 `phase5-1`。
- HEAD 与预期完全一致。
- 不存在已跟踪文件的未提交修改。
- `origin` 的推送目标唯一，且确认为预期 GitHub 仓库。
- 没有会扩大推送范围或改变推送行为的异常配置。

现有 22 个未跟踪任务书副本可以保留，不得清理或添加到提交。

**任何一项不符合预期，立即停止并报告，不要自行修复。**

## 三、第二步：检查远程同名分支

先读取远程分支：

```powershell
git ls-remote --heads origin refs/heads/phase5-1
```

### 情况 A：远程分支不存在

记录远程分支不存在，继续进行推送前检查。

### 情况 B：远程分支已存在

先获取该分支的远程提交信息，再检查提交关系：

```powershell
git fetch origin refs/heads/phase5-1:refs/remotes/origin/phase5-1
git rev-parse refs/remotes/origin/phase5-1
git merge-base --is-ancestor refs/remotes/origin/phase5-1 refs/heads/phase5-1
```

规则：

- 远程提交与本地 HEAD 完全一致：可以视为已经同步；只需在确认安全后设置 upstream。
- 远程提交是本地 HEAD 的祖先：属于可快进更新，可以继续。
- 远程分支包含本地没有的提交，或者提交关系无法确认：**立即停止，不推送、不合并、不变基。**
- 出现权限、网络或其他异常：停止并报告。

不得通过 `reset`、`rebase`、`merge` 或强制推送解决冲突。

## 四、第三步：推送前 dry-run

只有第二步确认安全后，才执行：

```powershell
git push --dry-run --porcelain origin refs/heads/phase5-1:refs/heads/phase5-1
```

检查输出，确保只涉及：

`refs/heads/phase5-1 → refs/heads/phase5-1`

如果 dry-run 显示其他分支、非快进拒绝、异常推送目标或无法确认的情况，立即停止。

## 五、第四步：执行实际推送

只有所有检查均通过后，执行：

```powershell
git push -u origin refs/heads/phase5-1:refs/heads/phase5-1
```

禁止使用：

```text
--force
--force-with-lease
--all
--mirror
--tags
```

不得推送其他分支，不得创建 PR，不得部署。

如果推送失败，不要尝试绕过保护规则；记录原始错误并停止。

## 六、第五步：验证结果

```powershell
git rev-parse HEAD
git status -sb
git branch -vv
git ls-remote --heads origin refs/heads/phase5-1
git status --short
```

验收标准：

1. 本地 HEAD 未改变。
2. 远程 `phase5-1` 指向预期提交。
3. 本地 `phase5-1` 的 upstream 为 `origin/phase5-1`。
4. 没有产生额外提交或修改。
5. 未跟踪任务书副本保持原状。
6. 没有创建 PR，也没有进行部署。

## 七、最终报告

只汇报：

1. 推送前 HEAD。
2. 远程分支是否原先存在。
3. 远程提交与本地提交的关系。
4. dry-run 结果。
5. 实际 push 结果。
6. 最终远程 HEAD。
7. upstream 配置。
8. 工作区状态。
9. 是否触及其他分支、PR 或部署。

**如果任何安全条件不满足，报告 STOP 原因，不要为了完成任务而绕过检查。完成后立即停止，不进入 Phase 7。**