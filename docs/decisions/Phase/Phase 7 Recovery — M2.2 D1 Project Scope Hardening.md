# Phase 7 Recovery — M2.2 D1 Project Scope Hardening

## 目标

修正 M2.2 中 `project_id` 作用域校验不完整的问题。

当前报告显示：已认证用户可以对任意已注册项目创建 Conversation。项目已注册不代表用户已获授权。

本阶段只做最小安全修正，不扩展 RBAC。

## 开始前

1. 检查当前分支、HEAD、工作区和暂存区。
2. 阅读 M2.2 当前未提交 diff。
3. 不覆盖或清理已有修改。
4. 不执行 reset、checkout、clean、stash。

## 实施要求

1. 保留现有请求 DTO 的 `project_id` 字段。
2. 创建 Conversation 时，服务端必须确定当前部署允许使用的项目。
3. 当前仅支持单项目配置时，只允许与服务端配置一致的项目 ID。
4. 即使其他项目已注册，也不得仅因其已注册就允许创建 Conversation。
5. 客户端提交未授权或不匹配的项目 ID 时，返回受控错误，不创建 Conversation，也不写入 resource_owner。
6. 不虚构用户项目成员关系，不宣称已实现 RBAC。
7. 如果当前配置无法可靠确定允许的项目，必须 fail-closed，并报告阻塞原因。
8. 保持 `resource_owner.project_id` 与服务端已验证的项目上下文一致。
9. 不修改 Conversation、Turn、Evidence 冻结表结构。
10. 不改变 Session、CSRF 和 API-Key 的既有安全边界。

## 测试要求

至少增加或更新以下测试：

- 配置中的允许项目创建成功。
- 其他已注册项目创建被拒绝。
- 未注册项目创建被拒绝。
- 项目配置无法解析时 fail-closed。
- 被拒绝的创建请求不产生 Conversation 或 resource_owner。
- `vietnam-wms` 既有测试链路继续通过。
- 原有所有权、CSRF、事务回滚测试继续通过。

不得跳过测试、放宽断言或修改历史基线来获得通过结果。

## 验证

先运行受影响的 Conversation 测试，再运行：

```powershell
python -m pytest tests/test_conversation_ownership.py -q
python -m pytest tests/test_conversation_message_api_architecture_audit.py -q
python -m pytest -q
python -m compileall backend tests scripts
```

记录完整测试失败，不要将既有 Step 8 失败描述成 M2.2 引起。

## Git 保护

- 不执行 git add。
- 不执行 git commit。
- 不执行 git push。
- 不创建 PR。
- 不部署。
- 不修改 M1 commit 或 Step 8 历史。

## 最终报告

说明修改文件、项目作用域来源、失败路径副作用、定向测试、完整回归、Matrix Gate、冻结表检查和剩余风险。

最后：

```text
D1 Project Scope: PASS / BLOCKED
Git add: NOT EXECUTED
Git commit: NOT EXECUTED
Git push: NOT EXECUTED
Deployment: NOT EXECUTED
M2.3: NOT STARTED
STOP
```