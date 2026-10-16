# Phase 7 Recovery M2.4 — Pre-Commit Security Review

项目：`D:\coding\ai\wms-ai-assistant`

## 一、目标

对 M2.4 的全部未提交变更进行安全审查，确认授权模型、白名单兼容逻辑和项目隔离没有引入安全回归。

本轮只审查、修复经确认的问题并验证，不提交 Git。

## 二、先检查工作区

执行：

```powershell
git status --short
git diff --check
git diff --stat
git diff
```

同时审查新增文件：

- `backend/app/db/models/authorization_grant.py`
- `backend/app/db/authorization_grant_repository.py`
- `backend/app/services/authorization_service.py`
- `backend/app/api/authorization_dependency.py`
- `backend/app/cli/grant_role.py`

不要覆盖或删除现有改动。

## 三、重点安全审查

### 1. 系统级与项目级权限

确认：

- `PROJECT_OPS` 不能访问系统级运维端点。
- `SYSTEM_OPS` 不会自动成为任意项目的成员。
- 客户端传入的 role、user_id 或 project_id 不会覆盖可信 Principal 和服务端项目作用域。
- 授权数据损坏、查询失败时按契约 fail closed。
- 缺少授权记录不能默认放行。

### 2. 旧白名单兼容

确认：

- 旧白名单只适用于原来的 5 个系统级端点。
- 旧白名单不可用时仍然返回 503。
- 授权表与旧白名单的并集逻辑不会绕过配置故障检查。
- 不会自动为旧白名单用户授予数据库角色。
- CLI 不会允许普通用户自行提升为 `SYSTEM_OPS`。

### 3. 数据库约束

确认：

- CHECK 约束覆盖所有合法及非法 Scope/Role 组合。
- 两个 partial unique indexes 能防止重复授权。
- Repository 对不符合授权契约的记录会拒绝使用。
- `init_db()` 可重复执行。
- 不修改任何既有观测表 Schema。
- 不引入未经授权的历史数据回填。

### 4. 端点回归

核对 A2 的 7 个只读端点，特别确认：

- 两个项目级端点确实校验当前部署项目的 membership。
- 其余 5 个系统级端点只接受 `SYSTEM_OPS` 或过渡期旧白名单。
- 未授权响应不泄漏其他用户、项目或资源是否存在。
- 没有通过弱化业务断言来让测试通过。

### 5. 冻结矩阵

确认冻结矩阵 555、冻结常量和 Gate 测试没有被修改。

`test_11_backend_working_tree_is_unmodified` 因当前未提交 backend 变更而失败，必须如实保留；不得通过修改 Gate 或基线来规避。

## 四、验证

先执行相关授权测试、M2.3 回归和 DB 集成测试。DB 测试必须硬断言连接为：

`localhost:5433/wms_ai_test`

确认四张观测表的绝对 residue 仍为 0。

本轮不要求 Matrix Gate 变绿，因为 backend 改动尚未提交。记录它的准确状态即可。

## 五、Git 与停止条件

- 不执行 `git add`、`git commit`、`git push`。
- 不修改冻结基线。
- 不访问生产数据库。
- 不进入 M2.5。

完成后输出：

1. 审查文件清单。
2. 授权边界审查结果。
3. 发现的问题及修复。
4. 测试结果。
5. 四表绝对 residue。
6. Git 状态与 Gate 状态。
7. 是否具备提交条件。
8. 尚需项目方决定的事项。

然后停止，等待提交授权。