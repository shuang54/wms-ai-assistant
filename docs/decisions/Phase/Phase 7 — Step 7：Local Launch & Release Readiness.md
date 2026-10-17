# Phase 7 — Step 7：Local Launch & Release Readiness

项目：`D:\coding\ai\wms-ai-assistant`

## 一、阶段目标

本阶段完成两个实际交付目标：

1. 提供安全、可复现的本地 Workflow UI 启动入口。
2. 对 Phase 7 Step 2–6 的累计改动进行完整交付审阅，形成明确的 Release Readiness 报告。

本阶段不新增业务功能，不进入 Phase 8。

## 二、第一步：保护现有工作区

执行：

```powershell
git status --short
git log -1 --oneline
git diff --check
git diff --stat
```

开始时预期：

```text
Branch: phase7
HEAD: 41b9ff22371f5738ab282324ef0fad047eae046e
```

以实际结果为准，不要为了匹配预期而修改 Git 状态。

要求：

- 保留 Step 2–6 的全部 tracked 和 untracked 修改。
- 不执行 `reset`、`clean`、`stash`。
- 不覆盖、不删除已有文件。
- 不修改历史冻结基线、治理门或无关文件。
- 不执行 commit、push、amend。

## 三、交付一：本地一键启动入口

先阅读现有：

```text
frontend/README.md
frontend/package.json
frontend/scripts/serve-static.mjs
frontend/scripts/dev-gateway.mjs
backend/app/main.py
backend/app/config.py
docker-compose.yml
```

确认真实的端口、环境变量、数据库配置和启动方式，再设计最小实现。

### 3.1 目标体验

尽可能让开发者通过一条 PowerShell 命令启动本地 Workflow UI，例如：

```powershell
.\scripts\start-workflow-ui.ps1
```

实际脚本名称可遵循现有项目约定。

启动入口应当：

1. 检查本地 PostgreSQL 是否可用；不可用时明确提示，不得自动切换数据库。
2. 检查后端端口与网关端口是否被占用。
3. 启动后端 Uvicorn。
4. 等待后端健康检查成功。
5. 启动现有 `frontend/scripts/dev-gateway.mjs`。
6. 输出实际访问地址及登录说明。
7. 让用户能够明确看到两个子进程的启动失败原因。
8. 支持安全退出，关闭本次启动的子进程，不误杀其他进程。

### 3.2 安全边界

- 只用于本地开发。
- 继续使用现有同源开发网关，不新建第二套代理。
- 默认只绑定 `127.0.0.1`。
- 不自动设置 `AUTH_COOKIE_SECURE=false`，如本地 HTTP 确实需要该设置，必须明确提示用户。
- 不打印 API Key、密码、数据库连接串或 Cookie。
- 不自动创建用户、授权项目或创建 Workflow Run。
- 不自动执行数据库迁移或初始化写操作。
- 不连接生产数据库。
- 不修改认证、授权、Runtime、Schema、Read API。
- 不得让开发启动脚本绕过 `NODE_ENV=production` 安全门。

如果项目现有结构不适合新增启动脚本，先说明原因并选择最小替代方案；不要为了形式强行新增。

## 四、交付二：启动与运维文档

更新 `frontend/README.md`，使其同时包含：

- 一键启动方法。
- 手动启动后端和前端的方法。
- PostgreSQL 未启动时的处理方式。
- 用户登录与项目授权的真实流程。
- 如何获取真实 Run ID。
- 如何访问 Workflow UI。
- 401、403、404、502、503 与网络错误的排查方法。
- 本地开发网关与生产网关的区别。

文档不得把测试数据库、Mock 数据或开发网关描述成生产环境。

## 五、交付三：Release Readiness 审阅

对 Step 2–6 的累计变更进行一次完整审阅。

重点检查：

1. 后端 API 路由是否正确注册。
2. Session 与项目授权是否仍由现有后端机制执行。
3. Graph 是否严格匹配 Run 的 `workflow_id` 和 `workflow_version`。
4. 浏览器是否只请求允许的 API 路径与方法。
5. `dev-gateway.mjs` 是否保持开发专用。
6. 是否存在凭据、Cookie、数据库连接串或敏感测试数据进入前端文件。
7. `.gitignore` 是否正确忽略 `node_modules/`、测试输出及本地秘密文件。
8. `package-lock.json` 与 `package.json` 是否一致。
9. README 中的命令、环境变量和路由是否与真实代码一致。
10. 新增文件是否全部纳入交付清单。
11. 是否存在意外删除、无关修改或重复实现。

检查 Git 时必须同时审阅：

```powershell
git status --short
git diff --check
git diff --stat
git diff
git ls-files --others --exclude-standard
```

注意：`git diff` 不会自动展示全部 untracked 文件，必须单独审阅新增文件。

## 六、测试策略

优先运行：

```powershell
cd frontend
npm test
npm run test:e2e
cd ..
```

再运行 Workflow UI 相关后端离线回归：

```powershell
python -m pytest -q tests/test_workflow_ui_security.py tests/test_workflow_ui_read_api.py tests/test_workflow_ui_graph_api.py tests/test_workflow_read_api_security_audit.py tests/test_workflow_runtime_read_service.py
```

运行 DB-gated 测试前，必须硬断言目标为专用测试库：

```text
localhost:5433/wms_ai_test
```

使用项目已有机制运行相关 DB 集成测试。

最后：

```powershell
python -m compileall -q backend tests scripts
git diff --check
```

如遇历史全量测试失败：

- 记录实际失败及其所属范围。
- 不得通过跳过测试、弱化断言或修改治理门来制造绿色结果。
- 不得将未执行的真实浏览器或 DB 测试标记为 PASS。

## 七、提交边界

本阶段只完成交付审阅，不自动提交。

完成后提供：

- 推荐的提交文件分组。
- 每组包含的文件。
- 是否混入此前已有改动。
- 尚未解决的问题。
- 建议的 checkpoint 顺序。

**不要执行 `git add`、`git commit`、`git push`。**

提交与否由用户审阅后决定。

## 八、完成报告

```text
【Phase 7 — Step 7 Report】

1. 开始时 Git 状态
2. 新增/修改文件
3. 本地一键启动方式
4. 运维文档
5. 安全边界审阅
6. 前端单测与 Mock E2E
7. 真实浏览器 E2E
8. 后端离线回归
9. DB-gated 回归
10. DB residue
11. compileall / diff check
12. Git 变更分组建议
13. 生产部署限制
14. 未解决问题
15. Git 状态与 HEAD

Phase 7 Step 7 完成后 STOP。
不进入 Phase 8，不自动 commit 或 push。
```

**核心原则：让现有功能更容易启动和交付，不要再造一套架构，也不要为了通过测试而改变业务行为。**