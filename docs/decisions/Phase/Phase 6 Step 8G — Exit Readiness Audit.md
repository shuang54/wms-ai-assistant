# Phase 6 Step 8G — Exit Readiness Audit

项目路径：

`D:\coding\ai\wms-ai-assistant`

## 一、目标

基于当前 Git HEAD 和已有测试/审计证据，判断 Phase 6 是否满足退出条件。

本步骤只做只读审计。

不修改代码、不写数据库、不运行真实 LLM、不提交、不推送、不进入 Phase 7。

## 二、先核对 Git

预期 HEAD：

`bc76af763f5dfc236e871c591ebf43fe1e28c716`

运行：

```powershell
git status --short
git rev-parse HEAD
git log -8 --oneline
git show --check --oneline HEAD
```

若 HEAD 不一致，先报告实际状态，不要重置或覆盖工作区。

## 三、收集现有证据

阅读现有 Phase 6 相关文档、能力矩阵、退出条件和审计报告。

核对以下能力是否有可追溯的实现及测试证据：

1. Agent Decision
2. Tool Authorization
3. Tool Execution
4. Tool Result Persistence
5. AgentRun
6. AgentStep
7. AgentDecision
8. AgentCheckpoint
9. Tool Replay
10. Resume
11. HITL Approval
12. Cooperative Cancellation
13. Agent Read API
14. Workflow Read API

每项记录：

* 实现位置
* 测试证据
* 测试运行环境
* 是否通过
* 已知限制

不能只根据能力矩阵中的 `READY` 标签判定完成。

## 四、测试证据

以现有真实报告为依据，核对：

* Offline suite：7297 → 7304 passed，0 failed，919 skipped。
* DB suite：Step 8F 修复前 8170 passed / 2 failed / 44 skipped；修复后 8179 passed / 0 failed / 44 skipped。
* Matrix residue fix：`bc76af7`。
* 历史 baseline 未修改。
* 会话结束的绝对残留检查仍然存在。
* DB 收尾计数为零的证据是否有可追溯记录。

区分：

* 有实际运行日志支持的事实；
* 仅由源码检查支持的结论；
* 尚未验证的事项。

不要为了刷新数字而自动重跑完整 DB 套件。

## 五、安全与部署门槛

检查当前证据是否覆盖：

* API Key 缺失或错误时的拒绝行为；
* Project Scope 隔离；
* 跨项目资源返回行为；
* Tool Authorization；
* Read-only SQL 安全边界；
* 取消和恢复路径；
* HTTPS/TLS；
* 网络边界和反向代理配置；
* 凭据和日志安全。

必须明确区分：

`Feature Implemented`

`Automated Tests Passed`

`Deployment Verified`

三者不是同一个状态。

没有证据就标记 `NOT VERIFIED`，不得推断已部署安全。

## 六、历史卫生问题

记录：

`efbc8cb` 中 `docs/roadmap/Project-Recovery-Audit.md:146` 的 trailing whitespace。

不要在本步骤修复，也不要 amend 历史提交。

## 七、最终结论

只允许输出以下一种结论：

* `PHASE 6 EXIT READY`
* `PHASE 6 EXIT READY WITH DOCUMENTED LIMITATIONS`
* `PHASE 6 EXIT BLOCKED`

如果 HTTPS 或网络边界尚未验证，不得声称生产部署已就绪。

## 八、最终报告格式

```text
【Phase 6 Step 8G — Exit Readiness Audit】

1. Git 状态
2. 14 项能力证据矩阵
3. Offline 测试证据
4. DB 测试证据
5. Matrix Residue Fix
6. DB Residue 状态
7. API 与项目隔离安全
8. HTTPS / Network Boundary
9. 未验证事项
10. 历史卫生问题
11. Phase 6 Exit Decision
12. 下一步建议

本步骤只读，不修改文件、不提交、不推送。
STOP。
```

## 九、停止条件

完成报告后立即停止。

不要自动进入 Phase 7，不要推送远程仓库，不要将“功能测试通过”表述为“生产部署安全已验证”。
