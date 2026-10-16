# Phase 7 — Workflow UI Step 2：安全认证与真实数据接入

项目：`D:\coding\ai\wms-ai-assistant`

## 一、阶段目标

解决 Phase 7 Workflow UI Step 1 的唯一关键阻塞：

> 浏览器页面已经完成，但现有 Workflow Read API 使用服务间 `X-API-Key`，没有安全的浏览器认证接入路径。

本阶段交付：

1. 复用现有 Session 认证。
2. 复用现有项目成员授权。
3. 为浏览器建立受保护的 Workflow 只读访问路径。
4. 将已有 Workflow UI 接通真实 PostgreSQL 数据。
5. 通过认证、跨项目隔离和只读行为测试。

最终链路：

```text
Browser
   ↓
Existing Session Authentication
   ↓
Project Membership Authorization
   ↓
Workflow UI Read API
   ↓
WorkflowReadService
   ↓
Existing Workflow Store Ports
   ↓
Existing Store Adapters
   ↓
Existing Repositories
   ↓
PostgreSQL
```

本阶段不新增登录系统，不修改 Workflow Runtime 执行逻辑，不进入下一阶段。

## 二、开始前：保护当前工作区

先执行：

```powershell
git status --short
git diff --check
git diff --stat
git log -5 --oneline
```

当前已有以下工作需要保留：

- 上一轮 Workflow Read API 的未提交修改。
- `frontend/` 下的 Workflow Run Detail 页面及测试。
- `tests/test_workflow_ui_security.py`。
- 已有的 Phase 7 设计文档。

要求：

- 不得 reset、clean、stash。
- 不得覆盖或删除现有未提交文件。
- 不得擅自 commit、push 或 amend。
- 不得为了通过测试而修改冻结基线或治理门。
- 先检查真实代码，再确定新增文件和最小实现方案。

## 三、先阅读真实认证和授权实现

重点检查：

```text
backend/app/api/
backend/app/security/
backend/app/auth/
backend/app/services/
backend/app/workflow_runtime/read_service.py
backend/app/workflow_runtime/read_factory.py
backend/app/workflow_runtime/ports.py
backend/app/api/workflow_read.py
```

以及：

```text
backend/app/db/models/
backend/app/db/
tests/
docs/security.md
docs/api.md
```

以上是检查范围，不代表这些路径一定全部存在。

确认以下真实能力：

1. M1 Session 如何创建、读取、验证和失效。
2. `require_session` 的真实定义和依赖方式。
3. M2.4 `require_project_member` 的真实参数及授权逻辑。
4. `authorization_grant` 的真实角色和权限规则。
5. 当前用户如何关联可访问的 Project。
6. Workflow Read Service 的构造和依赖注入方式。
7. 现有 API 如何区分用户认证和服务间认证。
8. 当前测试使用的 Session、项目授权和数据库 fixture。

**必须复用现有机制，不要根据名称猜测接口。**

如果现有 Session 机制无法支持安全的浏览器访问，或者项目成员授权无法用于 Workflow Read API，先报告真实缺口和最小修复方案，不得擅自设计新的认证体系。

## 四、API 设计

优先新增独立的 UI 只读路由，例如：

```text
backend/app/api/workflow_ui_read.py
```

文件名可以根据项目实际组织方式调整。

保留现有服务间 API：

```http
GET /api/workflow-runs/{run_id}
GET /api/workflow-runs/{run_id}/nodes
GET /api/workflow-runs/{run_id}/timeline
GET /api/workflow-approvals/{approval_id}
GET /api/workflow-runs/{run_id}/approvals/{node_id}
```

这些已有路由的契约和服务间认证不能被本阶段破坏。

UI 访问路径可以采用带项目范围的路由，例如：

```http
GET /api/ui/projects/{project_id}/workflow-runs/{run_id}
GET /api/ui/projects/{project_id}/workflow-runs/{run_id}/nodes
GET /api/ui/projects/{project_id}/workflow-runs/{run_id}/timeline
GET /api/ui/projects/{project_id}/workflow-runs/{run_id}/approvals/{node_id}
GET /api/ui/projects/{project_id}/workflow-approvals/{approval_id}
```

以上路径是设计候选，不要求照抄。最终路由必须遵循现有 API 命名和项目约定。

### 每个 UI 查询入口必须满足

1. 通过现有 Session 机制认证用户。
2. 通过现有项目授权机制验证该用户有权读取指定项目。
3. 只调用已有 Workflow Read Service。
4. 使用已有只读 DTO 返回数据。
5. 不调用 Runtime 的 `start()`、`advance()`、`request_cancel()`。
6. 不调用 Tool、LLM 或 AI Orchestrator。
7. 不修改 Run、Node Attempt 或 Approval。
8. 不暴露 ORM 对象、数据库 Session 或 Connection。

### 项目隔离

调用方提供的 `project_id` 只能表示请求目标，不能直接作为授权结论。

服务端必须验证：

```text
Authenticated Session
        ↓
Authorized Project Membership
        ↓
Run belongs to requested project
        ↓
Node / Approval belongs to that Run
        ↓
Return safe DTO
```

对于不存在的 Run、无权访问的项目或跨项目资源，采用现有安全错误约定，避免泄漏其他项目是否存在该资源。

不得仅依赖前端隐藏按钮或前端传入的 Project ID。

## 五、Session Cookie 安全

先复用现有 Session Cookie，不得重复建立新的登录或 Token 系统。

如果现有机制支持 Cookie，检查并确保：

- HttpOnly。
- 生产 HTTPS 环境使用 Secure。
- 合理的 SameSite 策略。
- Session 有效期与失效行为沿用现有设计。
- 不把 Session ID 放进 URL。
- 不把 Session 或服务端 API Key 写进 Local Storage。
- 不把服务间 `X-API-Key` 暴露给浏览器。

如果现有 Cookie 配置不满足要求，只允许做必要的最小修复，并补充测试。

### CSRF

虽然本阶段所有 Workflow UI 查询都必须是 GET，但如果需要增加任何会修改服务端状态的认证或 Session 操作，应遵循现有 CSRF 防护设计。

本阶段不新增审批决策、取消、重试、启动等写接口。

## 六、前端接入

复用当前已有的：

```text
frontend/index.html
frontend/js/api-client.js
frontend/js/state.js
frontend/js/controller.js
frontend/js/render.js
frontend/js/app.js
```

以及现有测试。

不要为了本阶段重新编写页面，也不要擅自迁移到 Vue、React 或其他框架。

### API Client

修改现有 API Client，使它请求安全的 UI 只读路由。

如果使用同源 Session Cookie：

- 使用同源相对路径。
- 按现有认证机制配置 `credentials`。
- 不构造 `X-API-Key`。
- 不把认证信息放进 URL。
- 不接受任意后端主机地址。
- 不允许通过请求参数覆盖服务端授权范围。

如果前端使用的静态服务器无法访问 API，应按项目真实部署方式接入同源 FastAPI 或受控部署路由。不得把服务端密钥加入静态服务器，也不得建立未授权代理。

### 页面行为

保持现有页面功能：

- Run ID 查询。
- Run Overview。
- 全量 Node Attempts。
- Timeline。
- Approval Detail。
- Loading / Empty / Error。
- Refresh。

补充处理：

- 未登录：提示需要登录。
- Session 过期：提示重新登录。
- 无项目权限：显示安全的权限错误。
- Run 不存在或不可访问：按后端统一错误契约展示。
- 持久化数据不符合安全投影契约：显示安全错误，不尝试修复。

不得显示内部异常堆栈、数据库错误正文或凭证。

## 七、测试要求

### A. API 单元测试

新增或扩展对应测试，至少覆盖：

1. 未登录请求被拒绝。
2. 有效 Session 可以访问已授权项目。
3. 无效或过期 Session 被拒绝。
4. 没有项目授权的用户不能读取数据。
5. 用户不能读取其他项目的 Run。
6. Node Attempts 必须属于已授权 Run。
7. Approval 必须属于正确的 Run 和 Node。
8. 缺失资源与跨项目资源采用安全错误响应。
9. 所有 UI Workflow 路由都受到认证和授权依赖保护。
10. 既有服务间 `X-API-Key` 访问行为没有被破坏。

使用项目现有的 Fake Session 和 Fake Store 基础设施；不得为了测试而新增另一套认证机制。

### B. 只读边界测试

确认 UI 路由：

- 只允许 GET。
- 不调用 Runtime 执行入口。
- 不调用 Tool 或 LLM。
- 不写入 Run、Node Attempt 或 Approval。
- 不暴露 `input`、敏感 metadata、Session 或 Connection。

### C. 前端安全测试

扩展：

```text
frontend/tests/
tests/test_workflow_ui_security.py
```

至少验证：

1. 浏览器请求不携带服务端 `X-API-Key`。
2. 页面不存储服务端凭证。
3. API Client 使用安全的同源路径。
4. Session 失效和授权失败能够被正确处理。
5. 不存在启动、推进、取消、审批决策等写操作。
6. 现有页面的 Node Attempts、Timeline 和 Approval 展示没有回归。

### D. 真实 PostgreSQL 集成测试

沿用现有 DB-gated 测试机制。

运行前必须确认连接的是专用测试数据库：

```powershell
$env:RUN_DB_TESTS="1"
```

使用现有真实 Repository、Store Adapter、Read Service 和授权依赖，验证：

1. 已授权 Session 可以读取测试 Run。
2. 未授权 Session 不能读取。
3. 跨项目访问被拒绝。
4. 多个 Node Attempts 按正确顺序读取。
5. Approval 归属验证正确。
6. 查询前后 Run、Node Attempt 和 Approval 状态不变。
7. 测试清理后 DB residue 为零。

不得连接生产数据库，不得导入真实业务数据。

## 八、验证命令

先执行新增测试和相关回归：

```powershell
node --test frontend/tests/
python -m pytest -q tests/test_workflow_ui_security.py
python -m pytest -q tests/test_workflow_read_api.py tests/test_workflow_read_api_security_audit.py
python -m pytest -q tests/test_workflow_runtime_read_service.py
python -m compileall -q backend tests scripts
git diff --check
```

按项目现有机制运行相关 DB-gated 测试。

全量离线测试如果仍然因为本机外部 WMS 数据库不可连接而失败，必须保留真实失败信息，不得通过修改无关测试、跳过测试或放宽治理门来制造全绿。

准确记录：

- passed / failed / skipped / errors。
- 退出码。
- 新增失败与已有基线的差异。
- DB residue。
- 修改前后 Git 状态。

## 九、禁止事项

本阶段禁止：

- 把 `X-API-Key` 放进前端。
- 建立无认证的 `/api/ui/*` 代理。
- 关闭现有认证或项目授权。
- 修改 Workflow Runtime 执行行为。
- 修改 Store Ports 来迁就前端。
- 新增数据库表或 Migration。
- 新增审批、取消、重试或恢复写接口。
- 修改冻结测试基线或治理门。
- 删除、跳过或弱化现有测试。
- 擅自 commit、push、amend、reset、clean、stash。
- 进入 Phase 8。

## 十、最终报告

完成后按以下格式汇报：

```text
【Phase 7 — Workflow UI Step 2 Report】

1. 开始时 Git 状态
2. 现有 Session 与项目授权实现
3. 新增 UI Read API
4. Session / Project Isolation
5. 前端接入情况
6. API Key 安全
7. 新增 / 修改文件
8. 前端测试
9. 后端定向回归
10. DB 集成测试
11. DB residue
12. compileall / diff check
13. 全量测试差异
14. 已知限制
15. Git 状态

端到端状态必须明确标记：
- READY
- PARTIAL
- BLOCKED
```

如果现有认证体系无法提供安全的 Session 到项目授权链路，停止并报告具体缺口，不得伪造闭环。

完成后立即 STOP，等待审阅。

