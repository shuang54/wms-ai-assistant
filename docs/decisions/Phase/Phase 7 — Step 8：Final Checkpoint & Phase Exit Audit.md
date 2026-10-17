# Phase 7 — Step 8：Final Checkpoint & Phase Exit Audit

项目：`D:\coding\ai\wms-ai-assistant`

## 一、目标

对 Phase 7 Step 2–7 的累计改动进行最终审阅、分组提交和退出审计。

不新增业务功能，不进入 Phase 8。

## 二、保护现有工作区

开始前执行：

```powershell
git status --short
git log -1 --oneline
git diff --check
git diff --stat
git ls-files --others --exclude-standard
```

确认当前分支与 HEAD，逐一清点 tracked 和 untracked 文件。

禁止：

- reset、clean、stash；
- 删除或覆盖已有修改；
- 将未审阅文件混入提交；
- 修改治理门或冻结基线；
- 将生产部署状态改为 READY。

## 三、最终变更审阅

逐一审阅：

- Step 2：Workflow UI Read API；
- Step 3 / 3B：工作台、Mock E2E、Playwright；
- Step 4：Workflow Graph；
- Step 5：真实 Run 数据库集成；
- Step 6：开发专用同源网关；
- Step 7：本地启动脚本和运维文档。

确认所有新增文件都被识别，特别是 untracked 文件；不能只看 `git diff`。

重点核对：

- `backend/app/main.py` 的路由注册；
- `frontend/scripts/dev-gateway.mjs` 的开发环境限制；
- `scripts/start-workflow-ui.ps1` 的启动、停止与 PID 校验；
- Session、项目授权与跨项目隔离；
- `.gitignore`、`package-lock.json` 和 README；
- 没有 secrets、数据库连接串或真实业务数据；
- 没有意外删除、Schema 修改或 Runtime 执行行为变化。

## 四、提交策略

先根据实际依赖确定最小、完整的提交分组。

注意：

- C1 后端 API/Graph 与 C2 前端存在测试契约耦合。
- `tests/test_workflow_ui_security.py` 和 `frontend/README.md` 横跨多个步骤。
- 启动脚本依赖已有开发网关。

如果无法保证分组提交后各自具有一致、可解释的状态，优先把完整 Workflow UI 特性作为一个原子提交，而不是强行拆成多个提交。

提交前必须向用户展示：

1. 最终提交分组；
2. 每组文件清单；
3. 是否包含此前未提交的改动；
4. 提交信息；
5. 每个提交的验证命令。

**在实际执行 `git add` 或 `git commit` 前，先输出最终分组及文件清单，等待用户确认。**

不得 push。

## 五、提交后的治理闭环

用户确认并完成提交后：

1. 运行 `git status --short`，确认工作区状态。
2. 重新运行此前因未提交 `backend/app/main.py` 而失败的架构闸门。
3. 运行 `git diff --check`。
4. 运行 `python -m compileall -q backend tests scripts`。
5. 检查每个提交的实际文件范围。
6. 如有失败，报告具体失败和根因，不修改治理门来规避。

不需要无差别重复全部历史测试；仅运行提交闭环所必需的测试与此前失败的门。

## 六、Phase 7 Exit 条件

满足以下条件后，才可将 Phase 7 标记为完成：

- Workflow UI 本地功能已交付；
- 真实浏览器 E2E 已执行；
- 相关 DB-gated 测试通过；
- 架构闸门恢复通过；
- 工作区和提交记录可审计；
- 生产部署仍明确标记为 BLOCKED，直至 HTTPS 和正式受控网关得到独立验证。

## 七、完成报告

```text
【Phase 7 — Step 8 Report】

1. 最终 Git 状态
2. 提交分组与文件清单
3. 提交记录
4. 架构闸门结果
5. compileall / diff check
6. 前端与后端关键回归
7. DB residue
8. 安全边界
9. 生产部署状态
10. 未解决问题
11. Phase 7 Exit 判定

Phase 7 完成后 STOP。
不进入 Phase 8。
不 push。
```