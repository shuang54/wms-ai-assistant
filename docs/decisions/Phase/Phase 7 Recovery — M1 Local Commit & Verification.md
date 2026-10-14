# Phase 7 Recovery — M1 Local Commit & Verification

## 一、执行目标

在当前 `phase7` 分支上，将已经审计通过的 M1 用户认证与会话实现单独提交到本地 Git，并在提交后重新运行治理门禁。

当前预期 HEAD：

`4a537f9e29dbaf55a8e2068dcec5ef3c38a75f53`

## 二、执行前保护

1. 再次检查 `git branch --show-current`、`git rev-parse HEAD`、`git status --short`。
2. 确认当前仍处于 `phase7`，且 HEAD 与审计记录一致。
3. 如果 HEAD 已变化、出现冲突或发现新的未审计修改，立即停止并报告。
4. 不执行 `reset`、`checkout`、`clean`、`stash`。
5. 不执行 `git add .` 或 `git add -A`。
6. 不修改、不删除、不覆盖 Phase 7 Step 8 文件及其他无关工作。

## 三、仅暂存 M1 文件

严格使用 Pre-Commit Audit 第 9 节的 M1 文件清单，逐个指定路径暂存。

包含：

- M1 认证原语、API、CLI、配置与会话实现。
- 三个新增用户/会话/资源归属模型及其注册。
- 三个 Repository、三个 Service 和认证 DTO。
- `agent_command_service.py`、`agent_read_service.py` 中经审计确认的 M1 变更。
- `.env.example` 中的 AUTH 配置说明。
- 六个 M1 认证测试。
- `test_observability_http_allowlist_audit.py` 中的新写路由登记。
- `test_runtime_e2e_closure_db.py` 中的 `agent_approval` 清理修复。

禁止暂存：

- `scripts/run_agent_llm_smoke.py`
- `tests/test_agent_command_real_llm_smoke.py`
- `.env`
- 未经明确批准的任务书文档
- 其他非 M1 文件

## 四、暂存区复核

暂存后执行并检查：

```powershell
git status --short
git diff --cached --check
git diff --cached --stat
git diff --cached --name-status
git diff --cached
```

必须确认：

1. 暂存区只包含 M1 清单中的文件。
2. 没有密钥、密码、真实 Cookie、Session Token、数据库连接串。
3. 没有 Prompt、Validator、Executor、Router 策略或冻结业务模型的意外修改。
4. Step 8 两个文件没有进入暂存区。
5. 暂存区差异与已审核内容一致。

**若有任何额外文件或未经审核的差异，停止，不提交。**

## 五、提交

确认暂存区无误后，执行一次本地提交：

```text
feat(auth): add user identity and session foundation
```

提交后记录完整 commit SHA。

本次授权只包括本地 `git add`、`git commit` 和验证，不包括远程操作。

## 六、提交后验证

首先检查：

```powershell
git status --short
git log -1 --oneline
git show --stat --oneline HEAD
```

然后运行之前失败的治理测试：

```powershell
$env:PYTHONUTF8="1"
python -m pytest tests/test_observability_http_allowlist_audit.py -q
```

再按照项目实际命令运行 Matrix Gate 和相关离线回归。

要求：

- 不跳过 `test_11_backend_working_tree_is_unmodified`。
- 不修改测试基线来消除失败。
- 不为通过门禁而放宽治理规则。
- 如果门禁仍失败，记录具体失败原因，停止进一步扩展工作。

若测试需要数据库，使用项目既有测试配置，不得连接生产业务数据库或写入 WMS/ERP 数据。

## 七、停止条件

完成提交和验证后立即停止。

不要：

- Push
- 创建 PR
- 部署
- 提交 Phase 7 Step 8 文件
- 进入 M2
- 修改其他阶段实现

## 八、最终报告

按以下格式汇报：

1. 提交前分支与 HEAD
2. 实际提交文件清单
3. Commit SHA 与提交信息
4. 是否确认无 secrets
5. 暂存区是否仅包含 M1
6. 治理测试结果
7. Matrix Gate 结果
8. 相关回归结果
9. 提交后工作区状态
10. 未解决问题

最后明确：

```text
M1 local commit: PASS / FAIL
Post-commit governance: PASS / FAIL
Git push: NOT EXECUTED
PR: NOT CREATED
Deployment: NOT EXECUTED
M2: NOT STARTED
```

