# Phase 7 Recovery M2.4 — Deployment Readiness Audit

项目：`D:\coding\ai\wms-ai-assistant`

## 一、当前基线

当前已知状态（以此前提交报告为依据，执行前必须重新核实）：

- Branch：`phase7`
- M2.4 Commit：`5edcab345bc576747c6527c778d096ecfc10880b`
- Matrix Gate：555 项 PASS，与冻结基线一致
- 五张观测表及授权表：上次审计 residue 全为 0
- Push：未执行
- M2.5：未启动
- 生产部署：仍 BLOCKED，G5 资源级项目归属与 HTTPS 边界尚未验证

## 二、本轮目标

仅回答：

1. M2.4 如何安全部署到目标环境？
2. 既有系统运维人员如何完成显式角色登记？
3. 旧白名单过渡期如何管理？
4. 授权表创建、幂等性和失败恢复是否有明确操作步骤？
5. 哪些生产部署阻塞项仍未解决？

**本轮只读审查，不实施部署。**

## 三、开始前核实

执行：

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git rev-parse origin/phase7
git show --stat --oneline HEAD
```

确认 HEAD 仍为 M2.4 提交。如果分支或 HEAD 已变化，记录实际情况，不覆盖其他工作。

阅读：

```text
backend/app/cli/grant_role.py
backend/app/services/authorization_service.py
backend/app/db/authorization_grant_repository.py
backend/app/db/models/authorization_grant.py
backend/app/api/authorization_dependency.py
backend/app/api/observability_read_auth.py
docs/security.md
docs/api.md
.env.example
```

重点确认真实 CLI 参数、角色值、数据库连接来源、授权表初始化方式和错误处理行为。不得根据之前的任务书假设接口。

## 四、角色登记审查

检查现有 `grant_role` CLI，形成操作说明，但不得实际执行授权命令。

必须明确：

- `MEMBER`、`PROJECT_OPS`、`SYSTEM_OPS` 的真实合法参数。
- `SYSTEM_OPS` 是否禁止同时传入 `--project`。
- `--user-id` 的来源和格式。
- 是否有用户存在性校验；若没有，明确记录孤儿授权风险。
- 重复授权是幂等成功、明确拒绝还是报错。
- 错误角色、非法项目标识、数据库不可用时的行为。
- 谁可以在部署机器上执行命令，以及应通过什么审批流程授权。

不得输出真实密码、连接字符串或其他凭证。

### 角色登记原则

1. 不从旧白名单自动生成角色。
2. 不猜测旧白名单中的用户应该属于哪个项目。
3. 不将项目运维角色提升为系统运维角色。
4. 只登记经项目方确认的真实用户 ID、项目和角色。
5. 本轮不新增或修改任何授权记录。

## 五、旧白名单过渡策略

根据真实代码确认当前旧白名单和授权表的判定顺序。

必须确认：

- 旧白名单仅对批准的系统级端点生效。
- 旧白名单缺失或格式错误时是否仍然 fail closed。
- 授权表不可用是否仍返回安全错误。
- 旧白名单与显式 `SYSTEM_OPS` 授权之间的并集规则。
- 项目级端点不会因旧白名单而放行。

提出一个分阶段退役方案：

1. 盘点旧白名单用户。
2. 由项目方确认每个用户的实际系统级角色。
3. 通过 CLI 显式登记。
4. 对比白名单与授权表，记录差异。
5. 单独审批退役旧白名单的代码变更。
6. 退役后重新运行授权回归和 Matrix Gate。

本轮不得修改 `.env`、代码或角色数据。

## 六、数据库部署与恢复审查

确认：

- `authorization_grant` 是否由既有 `init_db()` 创建。
- `init_db()` 是否重复执行安全、幂等。
- 应用启动是否会自动创建表。
- 应用数据库账号是否具备必要 DDL 权限。
- 目标环境是否有独立数据库、备份和恢复方案。
- 创建表失败时应用的真实启动行为。
- 是否存在 Schema 版本管理机制；如果没有，明确说明限制。

禁止：

- 连接生产数据库；
- 执行 DDL 或 migration；
- 写入角色；
- 清理数据库；
- 修改既有观测表。

如果无法确认目标环境的实际数据库配置，标记为 `NOT VERIFIED`，不得推断部署安全。

## 七、生产部署阻塞项

逐项列出：

### G5：资源级项目归属

确认 `llm_usage_record`、`assistant_outcome_record` 等记录是否仍缺少 `project_id`，以及当前 API 是否能在资源读取时可靠验证项目归属。

不得把 M2.4 的项目级授权误认为已经解决资源级归属。

### HTTPS / 网络边界

确认是否存在实际部署证据证明：

- 外部请求强制 HTTPS；
- 反向代理与应用之间的信任边界明确；
- Cookie / Session 的安全属性正确；
- 非可信客户端无法伪造代理头绕过边界。

没有真实部署证据就标记 `NOT VERIFIED`。

### 其他限制

记录：

- 旧白名单尚未完成角色映射；
- CLI `--user-id` 若没有用户存在性校验的影响；
- 未使用授权依赖是否只是预留；
- 生产环境初始化、回滚与备份尚缺少哪些证据。

本轮不修复这些问题，只报告并提出最小方案。

## 八、验证要求

本轮仅可运行不访问生产环境的只读检查，例如：

```powershell
git status --short
git diff --check
```

如需执行测试，先确认测试数据库仍被硬性限制为：

```text
localhost:5433/wms_ai_test
```

不要运行会连接生产数据库的命令。

不要因为本轮审查而修改测试、Gate 或冻结基线。

## 九、最终报告

```text
【Phase 7 Recovery M2.4 — Deployment Readiness Audit】

1. 当前 HEAD / branch / worktree
2. CLI 角色登记契约
3. 旧白名单过渡与退役方案
4. 授权表初始化及恢复方案
5. 生产部署检查清单
6. G5 资源级归属状态
7. HTTPS / 网络边界状态
8. 其他部署阻塞项
9. 本轮执行的命令与结果
10. 文件修改 / Git 状态
11. 是否访问数据库
12. 是否写入角色或修改数据
13. 是否 push
14. 总体状态：READY / BLOCKED / NOT VERIFIED

完成后立即 STOP，不进入 M2.5。
```

