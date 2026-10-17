# Phase 7 — Step 6：Same-Origin Browser Integration

项目：`D:\coding\ai\wms-ai-assistant`

## 目标

解决 Step 5 已确认的浏览器同源接入问题，使真实浏览器能够通过现有 Session Cookie 访问真实 Workflow UI Read API。

### 1. 先检查现有部署条件

- 阅读 `frontend/README.md`、`frontend/scripts/serve-static.mjs` 和后端启动说明。
- 检查仓库是否已有 Nginx、Caddy、Docker Compose 或其他受控网关配置。
- 保留现有 Step 2–5 所有未提交修改。
- 不得 reset、clean、stash、覆盖文件或提交代码。

### 2. 优先复用部署层同源方案

若已有受控网关，配置由同一 Origin 提供静态前端与 `/api/*`，并将 API 请求转发到后端。

若仓库没有现成网关，不要擅自新增未受保护的代理、开放宽泛 CORS 或修改认证机制。先提交最小方案及安全影响，确认后再实施。

### 3. 安全要求

- 保留 M1 Session 认证和 M2.4 项目授权。
- 浏览器不得获得 `X-API-Key` 或服务端凭据。
- 保留 `HttpOnly` Cookie。
- 不允许未经认证的 `/api/ui/*` 代理。
- 不修改 Workflow Runtime、数据库 Schema 或 Read API 业务逻辑。
- 生产部署必须使用 HTTPS；本地 HTTP 测试配置不得用于生产。

### 4. 真实浏览器验收

使用现有 `frontend/e2e/real-api.spec.js`：

1. 通过真实登录接口获取 Session。
2. 在真实浏览器中读取真实 Run Detail。
3. 读取 Nodes、Timeline、Graph、Approval。
4. 验证未登录返回 401、未授权项目返回 403、跨项目资源返回 404。
5. 确认浏览器请求不包含 API Key。
6. 确认读取操作不改变 Run、Node Attempt、Approval 状态。
7. 确认原有 Mock E2E 继续通过。

必须记录真实浏览器执行结果。未执行的测试继续标记为 skipped，不得算作 PASS。

### 5. 完成条件

- 本地真实浏览器可以登录并查看真实 Workflow Run。
- 原有前后端回归通过。
- 测试数据库 residue 为零。
- `git diff --check` 和 `compileall` 通过。
- 不 commit、不 push。

完成后报告实际修改、浏览器测试结果、安全边界和未解决项，然后 STOP，不进入 Phase 8。