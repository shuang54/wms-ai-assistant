# Phase 7 Recovery — M1 Pre-Commit Audit

项目：`D:\coding\ai\wms-ai-assistant`

## 目标

为 M1 用户身份与认证基础准备一个可审查、范围明确的本地提交。

本轮只做提交前审计与文件分类，**不执行 git add、commit、push，不创建 PR，不部署**。

## 1. 保护当前工作区

先执行并记录：

```powershell
git branch --show-current
git rev-parse HEAD
git status --short
git diff --stat
git diff --check
git ls-files --others --exclude-standard
```

不得执行：

```text
git reset
git checkout -- .
git clean
git stash
```

不得覆盖或清理其他阶段的未提交工作。

## 2. 对 36 项变更逐项分类

将当前工作区内容分成：

- M1 用户认证与会话实现
- M1 认证测试
- M1 相关治理测试调整
- Phase 7 Step 8 遗留验证文件
- 其他阶段任务文档或未跟踪文件

逐项列出文件路径和归属。

重点检查：

```text
backend/app/auth/
backend/app/api/auth.py
backend/app/api/auth_dependency.py
backend/app/config.py

backend/app/db/models/
backend/app/db/*user*repository.py
backend/app/services/user_service.py
backend/app/services/session_service.py
backend/app/services/resource_ownership_service.py
backend/app/services/agent_command_service.py
backend/app/services/agent_read_service.py

tests/test_auth_*
tests/test_user_service.py
tests/test_session_service.py
tests/test_resource_ownership_service.py
```

不要仅根据文件名判断归属，必须查看实际 diff。

## 3. 安全检查

确认：

- 没有 API Key、数据库密码、真实 Cookie、Session Token 或其他凭据被写入代码、测试样例或文档。
- `.env` 没有被意外纳入提交。
- `.env.example` 只包含占位值。
- 冻结表结构未被修改。
- 没有新增 migration 或依赖。
- CSRF 签名方案与刚完成的 M1 安全审计一致。
- 原有 API Key 兼容性未被破坏。

## 4. 测试与治理证据

复用 M1 已经执行的测试结果，不要无理由重复全量测试。

记录：

- M1 离线测试：68 passed / 12 skipped。
- M1 DB 测试：12 passed。
- 相关回归：282 passed，1 failed。
- 治理门 DRIFT 的实际原因。
- compileall 结果。
- 当前测试结果是否仍然对应当前工作区。

如果任何文件在上次测试后又发生变化，标明需要重跑的测试。

## 5. 提交范围建议

给出建议的 M1 文件清单，并明确哪些文件不应该与 M1 一起提交。

如果治理测试修改是 M1 必需内容，解释原因；如果属于之前 Phase 7 的验证设施，则单独分类。

**不要为了让治理门变绿而修改、跳过或删除 `test_11`。**

## 6. 最终报告

```text
Phase 7 Recovery — M1 Pre-Commit Audit

1. 当前分支与 HEAD
2. 工作区变更数量
3. M1 文件清单
4. 其他阶段文件清单
5. Secrets / .env 检查
6. 冻结表与依赖检查
7. 测试与 compileall 证据
8. 治理门 DRIFT 说明
9. 建议提交范围
10. 剩余风险

Git add: NOT EXECUTED
Git commit: NOT EXECUTED
Git push: NOT EXECUTED
Deployment: NOT EXECUTED

STOP — 等待确认，不进入 M2。
```

