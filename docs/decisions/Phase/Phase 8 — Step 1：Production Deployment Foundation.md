# Phase 8 — Step 1：Production Deployment Foundation

项目：`D:\coding\ai\wms-ai-assistant`

## 一、阶段目标

在 Phase 7 Workflow UI 本地交付完成的基础上，为生产部署建立可审查、可验证的基础设施方案。

当前明确状态：

```text
Phase 7 Local Delivery = COMPLETE
Production Deployment = BLOCKED
HTTPS = NOT VERIFIED
Controlled Production Gateway = NOT VERIFIED
```

本阶段目标：

1. 检查现有项目部署结构。
2. 明确生产流量的 HTTPS 与网关边界。
3. 建立可审查的部署配置模板和配置说明。
4. 增加必要的静态配置检查与安全测试。
5. 保持生产部署状态为 `BLOCKED`，直到真实环境验证完成。

本阶段不实际连接生产服务器、不申请真实证书、不修改生产 DNS，也不开放公网访问。

---

## 二、开始前保护现有工作区

项目此前仍可能有一个未跟踪文档：

```text
docs/decisions/Phase/Phase 7 — Final Documentation Checkpoint.md
```

先执行：

```powershell
git status --short
git log -5 --oneline
git diff --check
```

确认当前分支、HEAD 和未跟踪文件。

要求：

- 不得 `reset`、`clean`、`stash`。
- 不得覆盖或删除 Phase 7 的任何文件。
- 不得擅自提交或 push。
- 不得因为存在未跟踪的任务书而清理工作区。
- 所有新增文件必须与当前未提交状态区分清楚。

---

## 三、先审查现有部署结构

先阅读真实文件，不要假设项目已经使用 Docker Compose、Nginx 或 Caddy。

检查：

```text
backend/app/main.py
backend/app/core/
backend/app/api/
frontend/
frontend/scripts/dev-gateway.mjs
scripts/start-workflow-ui.ps1
frontend/README.md
docker-compose.yml
docker-compose.test.yml
Dockerfile
.dockerignore
.gitignore
docs/architecture.md
docs/security.md
docs/api.md
```

其中不存在的文件只记录，不得为了满足清单而随意创建。

同时检查：

- 现有环境变量及配置加载方式。
- 当前 Session Cookie 的 `Secure`、`HttpOnly`、`SameSite` 设置。
- 现有认证、项目授权和 CORS 配置。
- 当前 Uvicorn 的启动方式。
- 数据库连接配置和迁移入口。
- 是否已有部署平台或生产网关约定。
- `frontend/scripts/dev-gateway.mjs` 的开发环境限制。
- `scripts/start-workflow-ui.ps1` 是否只用于本地开发。

**不得把 dev-gateway 直接升级为生产网关。**

---

## 四、生产部署边界

目标架构：

```text
Browser
   │
   │ HTTPS
   ▼
Production TLS / Reverse Proxy
   │
   ├── Static Workflow UI
   │
   └── Allowed API Paths
           │
           ▼
     FastAPI Application
           │
           ▼
     Existing Authentication
           │
           ▼
     Project Authorization
           │
           ▼
     Workflow Read Layer
           │
           ▼
       PostgreSQL
```

必须满足：

1. TLS 由明确的 HTTPS 终止层处理。
2. 后端应用端口不得无意暴露到公网。
3. 生产网关必须有明确的 Host、Path、Method 和上游限制。
4. `/api/ui/` 的只读接口不得因部署而变成写接口。
5. `/api/auth/` 只允许项目真实存在且需要的认证路由。
6. 不允许未经审查的 `/api/` 全路径转发。
7. 不允许生产环境启用开发网关。
8. 不允许把 CORS、Session 或项目授权安全检查作为部署便利措施关闭。
9. 数据库只允许后端应用通过既有连接方式访问，不允许浏览器直连。
10. 不得让反向代理转发头成为绕过认证、授权或来源校验的手段。

---

## 五、可信代理与转发头

如果生产架构使用 TLS 终止代理，必须审查：

```text
X-Forwarded-For
X-Forwarded-Proto
X-Forwarded-Host
```

要求：

- 只有来自明确可信代理的转发头才可被后端信任。
- 不得无条件信任任意客户端提供的 `X-Forwarded-*`。
- 不得为了部署方便直接设置全范围信任。
- 如果后端可以被绕过代理直接访问，必须明确指出风险并阻止进入 READY。
- 如果现有部署模型无法安全限定可信代理地址，先记录缺口，不要伪造安全保证。

参考 FastAPI 官方说明：

https://fastapi.tiangolo.com/deployment/https/

https://fastapi.tiangolo.com/advanced/behind-a-proxy/

---

## 六、最小部署配置交付

先根据现有仓库决定是否具备创建生产部署模板的条件。

如果项目已有明确的 Docker/Compose 部署方式，优先复用。

如果没有，不得为了完成阶段而直接假设某个生产平台。

可以新增一个经过审查的部署配置模板，例如：

```text
deploy/
    README.md
    production-gateway/
```

具体文件名、网关选型与配置格式必须以现有项目和依赖情况为依据。

配置模板要求：

- 使用占位域名，不得伪造真实域名。
- 不包含私钥、证书文件、真实密码或 API Key。
- 不包含开发网关的启动命令。
- 不将后端端口直接映射到公网。
- 不默认开启任何额外 API 路由。
- 明确 TLS 证书来源和续期责任。
- 明确上游服务地址及可信代理边界。
- 明确生产 Cookie 必须使用 HTTPS 安全配置。
- 明确生产数据库必须与测试数据库隔离。
- 不自动执行数据库初始化、迁移或写入测试数据。

如果无法根据现有部署条件生成安全、可验证的配置，应停止新增部署文件并报告缺少的环境信息。

---

## 七、环境变量与凭据安全

不得在代码或文档中写入：

```text
真实 DATABASE_URL
生产用户名 / 密码
API Key
Authorization Header
TLS 私钥
Session Secret
生产服务器 IP 或内部敏感地址
```

文档中的连接串只能使用占位符，例如：

```text
<PRODUCTION_DATABASE_URL>
```

不得修改当前 `.env` 或覆盖用户现有配置。

不得将测试数据库地址或测试凭据复制成生产配置。

---

## 八、测试要求

新增与部署基础相关的离线测试，具体位置遵循项目现有测试习惯。

至少验证：

1. 开发网关在 `NODE_ENV=production` 时拒绝启动。
2. 生产配置模板不存在密钥或内联凭据。
3. 网关只允许明确配置的路径和方法。
4. 后端监听地址与端口配置符合部署设计。
5. 转发头的信任范围有明确限制。
6. 不存在把生产网关指向测试数据库的配置。
7. 不存在将 `/api/` 全路径无条件暴露给外部的规则。
8. 生产模板不依赖 `scripts/start-workflow-ui.ps1`。
9. 不会因部署配置而关闭 Session、认证或项目授权。
10. 所有新增检查均可离线执行，不需要真实证书或公网访问。

不得用字符串检查代替必要的配置解析或真实行为验证。

如果当前工具链无法验证某个部署行为，应明确标记为 `NOT VERIFIED`。

---

## 九、严格禁止

本阶段禁止：

```text
❌ 修改 Workflow Runtime
❌ 修改 Workflow Store Ports
❌ 修改数据库 Schema
❌ 修改现有认证与项目授权逻辑
❌ 新增 HTTP API
❌ 将开发网关用于生产
❌ 开放公网监听端口
❌ 申请或写入真实 TLS 私钥
❌ 修改真实 DNS
❌ 连接生产数据库
❌ 执行生产迁移
❌ 创建生产用户或授权
❌ 修改 Phase 7 的冻结基线
❌ 修改现有治理门以让测试通过
❌ Git push
❌ 自动进入 Phase 8 Step 2
```

---

## 十、验证命令

根据实际新增文件执行相关测试。

至少：

```powershell
python -m compileall -q backend tests scripts
git diff --check
git status --short
```

执行新增的部署配置测试、网关安全测试，以及必要的 Phase 7 安全回归。

准确报告 passed、failed、skipped 和未执行项。

不得把本地模板检查描述为真实 HTTPS 部署验证。

---

## 十一、最终报告

完成后报告：

```text
【Phase 8 — Step 1 Report】

1. 开始时 Git 状态
2. 当前部署架构审查
3. 生产网关设计
4. HTTPS / TLS 责任边界
5. 可信代理与转发头策略
6. 新增文件
7. 修改文件
8. 安全配置检查
9. 测试结果
10. compileall / diff check
11. 认证与授权是否修改
12. Schema / Runtime 是否修改
13. 生产环境是否实际验证
14. 未解决问题
15. Git 状态
16. Production Deployment 状态
```

最终状态只能根据证据判定：

```text
Production Deployment = READY
```

必须具备真实部署环境的独立证据才能使用。

如果只完成配置模板和离线测试：

```text
Production Deployment = BLOCKED
```

完成本阶段后立即停止，不进入 Phase 8 Step 2。