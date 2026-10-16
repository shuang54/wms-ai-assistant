# Workflow Run Detail（只读运维页面）

Phase 7 — Workflow UI Step 1：Workflow 运行详情与执行时间线。

通过输入已有的 `workflow_run_id`，只读查看：Run 概览 · 节点执行记录（含
全部历史 Attempt）· 执行时间线 · 审批详情；并具备完整 Loading / Empty /
Error / 重新加载 状态。

**本页面不提供任何写操作**：没有启动、推进、取消、重试、恢复或审批裁决入口。

---

## 1. 技术栈（现状说明）

仓库此前**没有**前端项目，也**没有**既定的 UI 技术栈决策（无 `package.json`、
无 `vite.config.*`、无前端 ADR）。按阶段任务书"**不要未经检查直接引入新的
框架、UI 库或大量依赖**"，本页面采用：

| 项 | 选择 | 理由 |
| --- | --- | --- |
| 页面 | 静态 **HTML + CSS** | 零构建、零依赖；内部运维系统足够 |
| 逻辑 | **原生 ES Module**（`js/*.js`） | 无打包器；浏览器直接加载 |
| 依赖 | **无**（`dependencies: {}` / `devDependencies: {}`） | 不引入框架 / UI 库 / 打包器 |
| 测试 | **Node 内置测试运行器**（`node --test`） | 零依赖；不需要安装任何包 |
| 本地服务 | `scripts/serve-static.mjs`（Node 内置 `http`/`fs`） | 零依赖；**仅绑定回环** |

`frontend/package.json` **只**用于声明 `"type": "module"`（Node 的 ES Module
语义标记）与测试入口，**不安装任何依赖**。

## 2. 运行

```bash
cd frontend
node scripts/serve-static.mjs --port 5173
# 打开 http://127.0.0.1:5173/
```

## 3. 测试

```bash
cd frontend
node --test tests/          # JS 逻辑：状态机 / 视图模型 / API client / 控制器
```

后端侧另有静态安全与契约测试（纯 Python）：

```bash
python -m pytest -q tests/test_workflow_ui_security.py
```

## 4. 文件结构

```text
frontend/
├── index.html                  页面外壳（含 CSP；标注数据访问未接通）
├── styles.css                  内部运维风格样式（无外部资源）
├── package.json                仅声明 ESM + 测试入口（零依赖）
├── js/
│   ├── api-client.js           只读 GET 客户端（同源相对路径；**无凭证**）
│   ├── state.js                纯状态机与视图模型（零虚构 / 全量 attempt / 保序）
│   ├── controller.js           纯控制器（防重复提交 / 防竞态 / 错误规范化，无 DOM）
│   ├── render.js               DOM 渲染（只用 textContent / createElement）
│   └── app.js                  DOM 接线（表单 / 按钮 / 重绘）
├── scripts/serve-static.mjs    本地静态服务（回环 only；**不代理 API**）
└── tests/*.test.mjs            node --test 用例（45 例）
```

## 5. ★ 安全边界与**尚未解决**的数据访问缺口

### 5.1 已实施的硬约束

- 浏览器中**不存在**任何服务端凭据：不写入源码 / 构建产物 / `localStorage` /
  `sessionStorage` / Cookie；请求只带 `Accept: application/json`；
- 客户端**只**发 `GET`（`readJson` 无 `method` 形参 ⇒ 结构上不可能发写请求）；
- 只使用**同源相对路径**（`API_BASE = ''`），不接受通过查询参数指定后端；
- 本地静态服务**只绑定 `127.0.0.1`**、显式拒绝 `--host`；**不代理 `/api/*`**
  （对 API 路径返回 501），因此它**不是**一个"未受保护的代理"；
- 页面设置 CSP（`default-src 'self'`、`connect-src 'self'`、
  `frame-ancestors 'none'`），不引用任何第三方来源；
- 渲染只用 `textContent`，不拼 `innerHTML`（结构上避免输出被当 HTML 注入）；
- 错误只显示固定文案与错误类别，不透出后端 detail 正文 / 堆栈 / SQL。

### 5.2 缺口（**阻塞端到端**，如实标记）

```text
Workflow Read API 使用**服务到服务**的 X-API-Key（docs/security.md §7）
仓库内没有：面向浏览器的会话化读取路径 / 受信任的服务端代理 / CORS 配置
```

因此：

1. 页面**不会**为了"看起来能用"而在浏览器持有密钥 —— 任务书明确禁止；
2. 在真实环境中查询会得到 **401**，页面显示对应错误状态（**这是预期行为**，
   `index.html` 顶部已如实标注"数据访问尚未接通"）；
3. 后端**未做任何修改**：没有关闭鉴权、没有放宽项目作用域、没有新增代理端点。

### 5.3 最小安全接入方案（**建议，本轮未实施**）

按"不降低安全边界"的前提，任选其一（均需独立任务书 + 安全审查）：

| 方案 | 做法 | 代价 / 边界 |
| --- | --- | --- |
| **A（推荐）** | 新增**会话化**只读端点：复用 M1 `require_session` + M2.4 授权依赖（`require_project_member`）暴露同一组只读数据；浏览器走 Cookie（`HttpOnly` / `Secure` / `SameSite`），密钥不进浏览器 | 需新增端点 + M2.4 授权接线；须定义"用户 → 项目成员"的登记（CLI `grant_role`） |
| **B** | 同源**服务端代理**：在应用进程内提供 `/ui-api/*` 只读转发，密钥仅在服务端读取；**代理必须**同时要求 M1 会话 + M2.4 项目授权，且**只**允许白名单只读路径 | 引入代理层；必须防 SSRF / 路径白名单 / 会话校验 |
| **C** | 仅内网：静态服务与 API 同源部署在受控网关后（需要网关侧会话与 TLS 证据） | 依赖部署层证据；当前 **NOT VERIFIED** |

**明确不做**：把 `X-API-Key` 放进 `VITE_*` / 前端配置 / 浏览器存储；
提供不带任何认证的转发代理；为演示而关闭后端鉴权。

### 5.4 与 `PRODUCTION_DEPLOYMENT` 的关系

本页面的接入还受既有部署阻塞项约束：**HTTPS / 网络边界 = NOT VERIFIED** ⇒
`PRODUCTION_DEPLOYMENT` 仍 **BLOCKED**（见 `docs/security.md` §7 / §11）。
