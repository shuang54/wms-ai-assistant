# Phase 8 Step 2 — Production Gateway Validation Harness

项目：`D:\coding\ai\wms-ai-assistant`

## 一、阶段目标

在 Phase 8 Step 1 已有部署模板的基础上，建立一个**可重复运行、仅限本机的 Nginx 网关验证流程**。

本阶段需要验证：

1. 现有 Nginx 配置能否通过真实 `nginx -t`。
2. HTTP 到 HTTPS 的跳转行为是否正确。
3. 静态资源、UI 查询路由、认证路由和其他 API 的实际访问控制是否符合预期。
4. 不允许的请求是否会被网关拦截。
5. 请求转发路径和代理 Header 是否正确。
6. 验证环境是否严格限制在本机，不访问生产数据库、不暴露公网端口。

**本阶段不是生产部署，不得把本地测试结果描述为生产 HTTPS 或网络隔离已验证。**

## 二、开始前：保护现有工作区

先执行并记录：

```powershell
git status --short
git diff --check
git diff --stat
git log -5 --oneline
```

重点保护 Phase 8 Step 1 的未提交文件：

```text
.gitignore
deploy/
tests/test_deployment_config.py
docs/decisions/Phase/Phase 8 — Step 1：Production Deployment Foundation.md
```

实际文件状态必须以命令结果为准。

要求：

- 不得 `reset`、`clean`、`stash`。
- 不得覆盖或删除 Step 1 文件。
- 不得修改 Phase 7 冻结的业务逻辑、认证逻辑或架构治理门。
- 不得 commit / push。
- 不得连接生产服务器或生产数据库。
- 不得修改 DNS 或申请生产证书。

## 三、先阅读现有实现

阅读：

```text
deploy/README.md
deploy/nginx/wms-workflow-ui.conf.template
deploy/nginx/proxy-headers.conf
deploy/production.env.example
tests/test_deployment_config.py
.gitignore
frontend/
backend/app/config.py
backend/app/main.py
```

确认实际配置和路由，不要只按任务描述猜测。

重点核对：

- Nginx 模板使用的占位符和 include 路径。
- 静态文件真实目录及白名单。
- `/api/ui/projects/` 的实际 HTTP 方法约束。
- `/api/auth/login` 和 `/api/auth/logout` 的实际方法约束。
- `/api/` 未明确放行的路由是否 fail closed。
- `proxy_pass` 对 URI 的实际转发行为。
- `proxy-headers.conf` 对 `Host`、`X-Forwarded-For`、`X-Forwarded-Proto` 等 Header 的处理。
- `APP_HOST` 的实际配置来源；不要因模板设置为 `127.0.0.1` 就宣称程序默认值已经改变。

如发现 Step 1 模板存在问题，先用实际配置和请求结果证明问题，再做最小修改。

## 四、准备隔离的验证环境

### 4.1 检查环境能力

在运行任何容器前，先检查：

```powershell
docker --version
docker info
```

检查本机是否已有 Nginx：

```powershell
nginx -v
nginx -V
```

根据真实环境选择验证方式。

- 如果已有 Nginx，优先使用它验证配置。
- 如果本机没有 Nginx，但 Docker 可用，只有在项目现有开发环境允许的情况下，才可使用固定版本的 Nginx 容器进行本地验证。
- 如果两者都不可用，不得伪造 `nginx -t` 通过；应报告阻塞项及复现命令。

### 4.2 隔离规则

验证服务必须满足：

- 所有监听端口只绑定到 `127.0.0.1`。
- 不绑定 `0.0.0.0` 或公网地址。
- 使用独立的临时配置和临时目录。
- 不修改生产部署配置来塞入测试证书。
- 不使用真实生产证书或私钥。
- 不连接真实 PostgreSQL。
- 不调用真实 LLM、Tool 或业务 API。
- 不添加新的生产 Compose 服务。
- 不新增数据库表或 migration。

如需 HTTPS 测试，可在临时目录生成自签名测试证书。私钥必须只用于测试，不能纳入 Git、不能写入正式部署目录，测试结束后按项目现有临时文件清理方式处理。

## 五、真实 Nginx 配置验证

使用 Step 1 的模板生成一份**临时配置**：

```text
deploy/nginx/wms-workflow-ui.conf.template
                 ↓
        临时替换占位符
                 ↓
         临时 nginx.conf
                 ↓
             nginx -t
```

要求：

1. 保持正式模板不被测试变量污染。
2. 确保临时配置的所有 `include` 路径有效。
3. 确保证书路径存在且可读取。
4. 记录 Nginx 版本、命令、退出码和完整错误摘要。
5. 不把含有密钥或敏感路径的内容写入最终报告。
6. 如果 `nginx -t` 失败，先定位配置错误，不要通过放宽安全规则让测试通过。

**只有实际执行 `nginx -t` 且退出码为 0，才可报告 `nginx -t = PASS`。** 静态文本检查不等于真实 Nginx 配置验证。

## 六、建立实际 HTTP 请求测试

使用本机临时 Nginx 和一个无数据库、无真实业务依赖的 Stub Upstream。

Stub Upstream 只用于记录收到的：

```text
HTTP method
request path
允许用于测试的普通 Header
```

不要记录 Cookie、Authorization、密码、真实请求体或其他敏感内容。

通过实际 HTTP 请求验证以下矩阵：

| 场景 | 请求 | 预期 |
|---|---|---|
| HTTP 入口 | `GET /` | 重定向到 HTTPS |
| HTTPS 入口 | `GET /` | 按当前模板拒绝或返回明确的安全响应，不得意外代理整个后端 |
| UI 查询 | `GET /api/ui/projects/` 下已允许的路由 | 按设计转发 |
| UI 写方法 | `POST /api/ui/projects/` 下同一路由 | 网关拒绝，不应到达 Upstream |
| 登录 | `POST /api/auth/login` | 按设计转发 |
| 登录方法错误 | `GET /api/auth/login` | 网关拒绝 |
| 登出 | `POST /api/auth/logout` | 按设计转发 |
| 登出方法错误 | `GET /api/auth/logout` | 网关拒绝 |
| 其他认证路由 | `GET /api/auth/me` | 除非模板明确授权，否则拒绝 |
| 未授权 API | `GET /api/unknown` | 404 或项目明确规定的拒绝响应 |
| API 写请求 | 对未明确放行的 API 发起 `POST` / `PUT` / `DELETE` | 网关拒绝 |
| 静态白名单 | 访问模板明确允许的静态文件 | 按设计返回 |
| 未知静态路径 | 访问白名单外路径 | 拒绝，不得落入宽泛的后端代理 |
| 路径边界 | 测试缺少尾斜杠、额外路径段及相近前缀 | 不得意外匹配到宽泛授权规则 |

测试必须检查两项：

1. 客户端实际收到的 HTTP 状态码和必要响应 Header。
2. Stub Upstream 实际收到的请求方法和完整路径。

仅检查状态码不够。某些请求即使最终返回 404，也可能已经错误地转发给后端。

如果某项路由在真实应用中并不存在，不要为了测试通过而创建虚假业务路由。应使用 Stub Upstream 验证网关行为，并在报告中明确它不是业务端到端测试。

## 七、Proxy Header 验证

检查实际发往 Stub Upstream 的 Header，至少验证：

- 客户端传入伪造 `X-Forwarded-For` 时，网关不会无条件信任客户端提供的地址链。
- `X-Forwarded-Proto` 由网关按真实连接协议设置，而不是直接照抄客户端 Header。
- `Host` 使用预期的上游值。
- 未授权的自定义 Header 不会意外覆盖安全相关的代理信息。

不要只对配置文件做字符串断言，尽量通过实际请求验证。

如果应用后续需要信任代理 Header，必须单独定义可信代理地址范围和应用配置。本阶段不允许直接设置为信任任意来源。

## 八、特别检查 Nginx 方法语义

Nginx 的 `limit_except GET` 允许 `HEAD`，不能直接等同于“只允许 GET”。

请结合实际 UI 请求需求决定：

- 如果允许 `HEAD`，在配置文档和测试中明确记录。
- 如果要求严格禁止 `HEAD`，使用经过真实 Nginx 验证的明确方法限制方式。

不得因为静态测试预期与 Nginx 实际语义不一致，就把不正确的预期写进测试。

同样要核对 `proxy_pass` 是否带 URI，因为它会影响匹配的 location 前缀是否被替换。必须用 Stub Upstream 捕获到的路径来验证，不得只根据配置外观下结论。

## 九、后端绕过网关的边界

本阶段可以验证：

- 生产环境示例配置将 `APP_HOST` 设置为 `127.0.0.1`。
- 正式网关 upstream 指向预期的 loopback 地址。
- 配置文档明确要求外部访问只能经过网关。

但本地测试**不能证明真实生产主机的网络隔离**。

以下事项必须单独标记为 `NOT VERIFIED`：

- 真实服务器上的后端监听地址。
- 云安全组 / 主机防火墙是否阻止外部直连后端端口。
- 生产 TLS 证书和域名是否正确。
- 生产 HTTPS 是否可从外部访问。
- 实际部署环境是否存在其他监听地址或旁路入口。

不得修改 `backend/app/config.py` 的默认行为来绕过本阶段问题；若发现默认 `APP_HOST=0.0.0.0` 与部署要求存在冲突，应报告配置风险和最小后续方案。

## 十、测试与治理

新增或扩展测试，优先使用：

```text
tests/test_deployment_config.py
```

如果需要新建测试文件，遵循项目现有命名和测试结构。

测试必须覆盖：

- 实际 Nginx 配置语法验证（若环境支持）。
- HTTP→HTTPS 重定向。
- HTTPS 下的路由和方法矩阵。
- 路径前缀与 `proxy_pass` 转发路径。
- Proxy Header 的覆盖行为。
- 未授权路由 fail closed。
- 未知静态路径拒绝。
- Stub Upstream 收到的请求记录。
- 测试端口只绑定 loopback。
- 临时证书、密钥和配置不会进入 Git。
- Docker/Nginx 不可用时，测试结果明确为 skipped/blocked，而不是 PASS。

继续运行现有测试：

```powershell
python -m pytest -q tests/test_deployment_config.py
python -m pytest -q tests/test_workflow_ui_security.py
```

如果有实际配置或应用文件改动，继续运行项目既有架构治理门：

```powershell
python -m pytest -q tests/test_architecture_governance.py
```

同时运行：

```powershell
python -m compileall -q backend tests scripts
git diff --check
git status --short
git diff --stat
```

准确记录每条命令的退出码、passed、failed、skipped。不得把没有执行的验证写成 PASS。

## 十一、严格禁止

本阶段禁止：

- 连接生产服务器。
- 修改 DNS。
- 申请或替换正式 TLS 证书。
- 绑定公网监听地址。
- 连接生产数据库。
- 新增生产 HTTP API。
- 修改认证、授权、Router 或 Workflow Runtime。
- 修改 Phase 7 冻结范围。
- 新增数据库表、migration 或 runtime service。
- 删除或覆盖 Step 1 未提交文件。
- commit / push。
- 自动进入 Phase 8 Step 3。

## 十二、完成报告

```text
【Phase 8 Step 2 — Production Gateway Validation Report】

1. 开始时 Git 状态
2. 使用的 Nginx / Docker 版本
3. 临时验证环境与隔离方式
4. nginx -t 结果
5. HTTP→HTTPS 结果
6. 路由 / 方法矩阵结果
7. Proxy Header 结果
8. proxy_pass 路径验证
9. Upstream 实际收到的请求记录
10. 本地后端绕过网关的验证范围
11. 测试结果（passed / failed / skipped）
12. compileall / diff check
13. 新增与修改文件
14. 未验证的生产环境事项
15. 最终部署状态
16. Git 状态

最终状态必须明确区分：

- Local Gateway Validation = PASS / PARTIAL / BLOCKED
- Production HTTPS = NOT VERIFIED，除非有真实生产证据
- Production Network Isolation = NOT VERIFIED，除非有真实主机与网络证据
- Phase 8 Step 2 = COMPLETE / BLOCKED

完成后立即 STOP，等待审阅。
