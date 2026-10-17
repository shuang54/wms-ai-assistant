# Workflow Run Detail（只读运维页面）

Phase 7 — Workflow UI Step 1（页面与状态）+ **Step 2（安全接入真实数据）**。

通过输入 `project_id` 与已有的 `workflow_run_id`，只读查看：Run 概览 ·
节点执行记录（含全部历史 Attempt）· 执行时间线 · 审批详情；并具备完整
Loading / Empty / Error / 重新加载 状态。

**本页面不提供任何写操作**：没有启动、推进、取消、重试、恢复或审批裁决入口。

## 数据访问（Step 2 已接通）

```text
Browser（同源 HttpOnly 会话 Cookie；无任何服务端密钥）
    ↓  GET /api/ui/projects/{project_id}/workflow-runs/…
M1 会话认证（require_session）                      401 / 503
    ↓
M2.4 项目成员授权（enforce_project_access → ai_ops.authorization_grant）  403 / 503
    ↓
WorkflowReadService（复用于服务间读面）→ Store Ports → Repositories → PostgreSQL
```

- 认证/授权**全部由服务端**执行；路径里的 `project_id` 只是**请求目标**，
  **不**构成授权结论。
- **不需要**任何前端环境变量或密钥；`frontend/package.json` 保持零依赖。
- 未登录 ⇒ 提示需要登录；会话过期 ⇒ 提示重新登录；非该项目成员 ⇒ 权限错误；
  Run 不存在 / 属于其它项目 ⇒ 统一"未找到"（同构，不泄漏存在性）。

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

## 1.1 Step 3：运维工作台交互

| 功能 | 行为 | 纯逻辑位置 |
| --- | --- | --- |
| **节点状态筛选** | 全部 / 运行中 / 已完成 / 失败 / 等待审批 / **未知状态**（= 域白名单外的非空状态，**不**映射为成功）；显示"命中 X 次 / 共 Y 次"与节点数；无匹配 ⇒ 明确空状态 + 清除按钮 | `state.js::applyNodeFilter` |
| **多 Attempt 保留** | 入选节点**始终渲染其全部历史 attempt**（命中项高亮、未命中项灰化）⇒ 任何筛选下都不折叠/丢失历史 | `state.js::applyNodeFilter` |
| **失败节点定位** | 失败行标红、失败组左侧红条、组头显示"失败 N 次"；行内含真实错误码 / 开始 / 完成 / 耗时；"在时间线中定位"按钮高亮相关事件 | `render.js` + `state.js::eventIndexesForAttempt` |
| **Timeline 联动** | 按事件类型视觉区分；节点事件可"定位 Attempt"（滚动 + 高亮）；缺可定位身份时如实标注"无可定位的节点身份"（**不**补造） | `state.js::attemptForEvent` / `hasNodeIdentity` |
| **输出查看** | `<details>` 展开/折叠 + 完整原文（`textContent`）+ 一键复制（`navigator.clipboard`）+ 空输出提示 + 复制成功/失败提示 | `state.js::summarizeOutput` / `copyOutput` |
| **稳定 DOM id** | `attempt-<node>-<attempt>`，非安全字符按 `_u<hex>` 编码 ⇒ 任意后端标识都能安全定位 | `state.js::attemptDomId` |

筛选 / 选中 / 复制**全部为纯前端**：不产生任何网络请求、不修改服务端数据。

## 1.2 Step 4：Graph View（只读工作流拓扑）

| 功能 | 行为 | 纯逻辑位置 |
| --- | --- | --- |
| **节点展示** | 真实 Node ID / 节点类型 / 执行状态 / Attempt 数量 / 错误码种类 / 起点标记 | `state.js::toGraphModel` |
| **连接线** | **只**来自服务器端工作流定义的 `next_node_id` / `on_true_node_id` / `on_false_node_id`（已做无环 / 可达性校验）——**绝不**按 Attempt 先后顺序推断 | `state.js::toGraphModel` |
| **分支可视化** | CONDITION 双分支带 `true` / `false` 标签；汇聚节点多条入边 | `state.js::computeGraphGeometry` |
| **拓扑不可用** | 未注册 ⇒ `not_registered`；版本不匹配 ⇒ `version_mismatch`（并展示两个版本号）⇒ **不绘制任何节点与边** | `render.js::graphEmptyMessage` |
| **节点联动** | 点击节点 ⇒ 高亮该节点**全部** attempt + 滚动定位 + Timeline 相关事件高亮；选中节点与相邻边高亮 | `controller.js::selectGraphNode` |
| **状态图例** | 运行中 / 已完成 / 失败 / 等待审批 / 未知·未执行（文字 + 颜色双通道） | `state.js::GRAPH_LEGEND` |
| **布局** | 确定性分层（Kahn 拓扑层）+ SVG 正交折线；容器内横向滚动；窄屏不导致页面级溢出 | `state.js::computeGraphGeometry` |

数据来源：`GET /api/ui/projects/{project_id}/workflow-runs/{run_id}/graph`（Step 4 新增；
只读拓扑 DTO `backend/app/dto/workflow_graph_api.py`，**不**含模板 / 参数映射 /
审批文案 / tool 目标）。

## 2. 运行

```bash
cd frontend
node scripts/serve-static.mjs --port 5173
# 打开 http://127.0.0.1:5173/
```

## 3. 测试

```bash
cd frontend
npm test              # 160 例：Node 内置测试（纯逻辑 + 手写 DOM 桩）
npm run test:e2e      # 56 例：真实 Chromium E2E（Mock 50 + 真实 API 6）
```

测试文件说明：

| 文件 | 内容 | 类型 |
| --- | --- | --- |
| `state.test.mjs` | 视图模型、占位规则、Timeline 保序、审批空状态 | Node 单元 |
| `api-client.test.mjs` | 只读 GET、同源 Cookie、错误映射、无凭证 | Node 单元 |
| `controller.test.mjs` | 状态流转、防重复提交、竞态、401 两种语义 | Node 单元 |
| `workbench.test.mjs` | 筛选、双向联动、复制、未知状态、无审批、写操作不可调用 | Node 单元 |
| `render.test.mjs` | 手写 DOM 桩：生成的 DOM 结构 / `data-*` / 按钮接线 / `textContent` | **DOM 桩**（非浏览器） |
| `app.test.mjs` | `bindApp` 接线：筛选真实生效、双向滚动、复制、防重复提交 | **DOM 桩**（非浏览器） |
| `graph.test.mjs` | Graph 纯逻辑：真实拓扑 / 缺拓扑不画边 / 状态映射 / 分层 / 几何确定性 | Node 单元 |
| `graph-render.test.mjs` | Graph 渲染：节点卡片 / SVG 边 / `data-*` / 点击接线 / 空状态 | **DOM 桩**（非浏览器） |
| `e2e/workbench.spec.js` | 真实 Chromium：布局/筛选/联动/复制/错误/竞态/安全边界/**Graph View** | **真实浏览器 E2E** |

### 三层测试的区分（**不得混为一谈**）

| 层 | 工具 | 覆盖 | 不覆盖 |
| --- | --- | --- | --- |
| **Node 单元** | `node --test` | 纯逻辑（状态机 / 视图模型 / 筛选算法 / 联动索引 / 复制结果） | DOM、CSS、真实渲染 |
| **DOM 桩** | `node --test` + `tests/dom-stub.mjs` | 生成的 DOM 结构、`data-*`、事件接线、`textContent` | 真实 CSS 布局、真实滚动、真实剪贴板权限 |
| **真实浏览器 E2E** | Playwright + Chromium | 页面渲染、响应式布局、真实点击、真实滚动定位、真实计算样式、剪贴板 mock、请求监听、Cookie 语义 | 真实后端 / 数据库（API 由 `page.route()` 注入） |

⚠️ `render.test.mjs` / `app.test.mjs` / `graph-render.test.mjs` **不是真实浏览器测试**：它们用手写 DOM 桩
（故意**不实现** `innerHTML`，从而在结构上证明渲染层只用 `textContent`/DOM API）。
真实浏览器行为由 `e2e/workbench.spec.js` 覆盖。

### E2E 配置与安全边界

`playwright.config.js`：

- **只**运行 Chromium（未安装 Firefox / WebKit）；
- 复用 `scripts/serve-static.mjs`（**仅绑 `127.0.0.1`**，不代理 `/api`）；
- 独立端口（默认 `4321`），`retries: 0`（不通过重试掩盖失败）；
- 不使用外部代理；**不**访问外部网络；
- 所有 API 响应经 `page.route()` 注入（**只**拦截同源 `/api/**`），
  **不**连接任何数据库、不需要真实 LLM、Mock 响应**只**在内存中；
- Cookie 测试只使用虚构值（`fictitious-test-cookie-value`），**不**读取真实凭证。

### 安装（E2E）

```powershell
cd frontend
npm install                 # 仅安装 @playwright/test（devDependency）
npx playwright install chromium
```

> 运行时依赖仍为**零**：`dependencies` 为空；`@playwright/test` 仅用于 E2E，
> 不被页面 / 静态服务 / Node 单元测试引用（由 pytest 断言保证）。

后端侧另有静态安全与契约测试（纯 Python）：

```bash
python -m pytest -q tests/test_workflow_ui_security.py
```

## 4. 文件结构

```text
frontend/
├── index.html                  页面外壳（含 CSP；访问方式如实标注）
├── styles.css                  内部运维风格样式（无外部资源）
├── package.json                声明 ESM + 测试入口（运行时零依赖）
├── playwright.config.js        Chromium-only E2E 配置（回环 / 不代理 API）
├── .gitignore                  node_modules/ 与 test-results/ 不入库
├── js/
│   ├── api-client.js           只读 GET 客户端（同源相对路径；**无凭证**）
│   ├── state.js                纯状态机 / 视图模型 / 筛选 / 联动 / 复制 / **Graph 模型**
│   ├── controller.js           纯控制器（防重复提交 / 防竞态 / 错误规范化 / 节点选中）
│   ├── render.js               DOM 渲染（只用 textContent / createElement / SVG NS）
│   └── app.js                  DOM 接线（表单 / 筛选 / 复制 / 滚动定位 / 重绘）
├── scripts/serve-static.mjs    本地静态服务（回环 only；**不代理 API**）
├── tests/*.test.mjs            node --test（160 例：Node 单元 + DOM 桩）
└── e2e/
    ├── fixtures.js             共享 fixtures（DTO 一致 mock + clipboard mock）
    └── workbench.spec.js       Playwright 真实 Chromium（50 例；含 Graph View）
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

### 5.2 认证 / 授权结果 → 页面状态

| 服务端结果 | 页面表现 |
| --- | --- |
| `401`（无会话） | "需要登录"提示 + 重新加载按钮（**保留**用户输入） |
| `401`（**曾成功加载过**后出现） | "会话已过期或被吊销，请重新登录" |
| `403`（非该项目成员） | 权限错误（固定文案），不提示登录 |
| `404`（Run 不存在 / 属于其它项目 / 无审批行） | "未找到该资源"（三者在同一端点内**同码同文案**） |
| `502`（持久化数据无法安全投影） | 安全错误提示；**不**尝试修复或截断数据 |
| `503`（会话服务 / 授权存储不可用） | "读取服务不可用"，可重试 |
| 网络错误 / 非法 JSON | 安全文案；不透出底层异常正文 |

### 5.3 仍然存在的限制（**如实记录**）

1. **无项目成员登记则无法读取**：`ai_ops.authorization_grant` 中必须有该用户
   的显式成员授权（由管理员经 `python -m backend.app.cli.grant_role --user-id
   <uuid> --project <pid> --role MEMBER` 写入）。仓库内**没有**管理 UI。
2. **真实浏览器 E2E 需本地同源网关**（Step 6 已提供 `scripts/dev-gateway.mjs`，
   仅本地开发）：Mock E2E 默认跑；真实 API E2E 需 §6.3.1 的 7 个环境变量，
   否则 **skipped**（未执行，非通过）。
3. **未引入类型检查 / lint 工具**（保持零运行时依赖；靠 `node --test` 与
   pytest 静态检查）。
4. **审批创建时刻仍不可读**（后端 Store Port 未暴露 `created_at`）⇒ 页面不展示。
5. **无全局 Run 列表**（后端无该能力）；须已知 Run ID。
6. **本地网关不可用于生产**：`dev-gateway.mjs` 是 dev-only（`NODE_ENV=production`
   拒绝启动、只绑回环）。生产必须 **HTTPS + 受控网关**（仓库内**无**该配置）；
   `HTTPS / 网络边界 = NOT VERIFIED` ⇒ `PRODUCTION_DEPLOYMENT` 仍 **BLOCKED**
   （见 `docs/security.md` §7 / §11）。

**明确不做**：把任何服务端密钥放进浏览器（`VITE_*` / 前端配置 / 浏览器存储）；
提供不带认证的转发代理；为演示而降低后端鉴权或放宽项目作用域。

## 6. 本地运维手册（Step 5–7）

> 本节命令均来自仓库**真实**脚本与配置（根 `README.md` §启动 /
> `backend/app/cli/*` / `frontend/scripts/serve-static.mjs` /
> `backend/app/api/*`），不是猜测。

### 6.0 一键启动（推荐；Phase 7 Step 7）

```powershell
# 仓库根目录
.\scripts\start-workflow-ui.ps1
# → 页面 http://127.0.0.1:8080/index.html
```

脚本按顺序执行（全部为**本地开发**用途）：

```text
1. NODE_ENV=production           ⇒ 拒绝启动（不绕过网关的生产安全门）
2. 检查 python / node 与必需文件
3. 检查端口占用（后端 8000 / 网关 8080）⇒ 占用则报出 pid + 进程名并退出
4. 检查数据库可达性（**纯 TCP 探测**，不执行 SQL、不携带凭据）
                                 ⇒ 不可达则提示并退出
5. 启动 Uvicorn（仅 127.0.0.1）
6. 轮询 /api/health 直到通过     ⇒ 报告 database=ok / disabled / error
7. 启动既有同源网关 dev-gateway.mjs
8. 打印访问地址 + 登录 / 授权说明
```

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `-BackendPort` | `8000` | Uvicorn 端口 |
| `-GatewayPort` | `8080` | 同源网关端口 |
| `-HealthTimeoutSeconds` | `45` | 等待 `/api/health` 的超时 |
| `-AllowNonLocalDatabase` | 关闭 | 显式允许 `DATABASE_URL` 指向**非回环**主机（默认拒绝，避免误连生产库） |
| `-Stop` | — | 停止上次由本脚本启动的两个子进程 |

**安全边界（脚本明确不做的事）**：

```text
不设置 DATABASE_URL（沿用进程环境变量 / 仓库 .env）
不自动设置 AUTH_COOKIE_SECURE=false（只提示；本地 http 需你**显式**设置）
不创建用户 / 不授予项目 / 不创建 Workflow Run
不执行 init_db、迁移或任何写库操作
不打印连接串 / 口令 / Cookie（只打印 host:port）
不修改认证、授权、Runtime、Schema、Read API
不绕过 dev-gateway 的 NODE_ENV=production 安全门
```

**停止**：`Ctrl+C`，或另开终端执行 `-Stop`。

```powershell
.\scripts\start-workflow-ui.ps1 -Stop
# 只停止"本次启动"记录的两个 PID，并校验进程名 + 命令行 ⇒ 不会误杀其它进程
```

### 6.1 启动后端（FastAPI）

```powershell
# 仓库根目录（依赖见根 README「安装」）
python -m uvicorn backend.app.main:app --reload
# → http://127.0.0.1:8000   （OpenAPI: /docs）
```

表结构需先初始化：

```powershell
python -m backend.app.db.init_db
```

### 6.2 启动前端静态服务

```powershell
cd frontend
node scripts/serve-static.mjs --port 5173
# → http://127.0.0.1:5173/index.html
```

该服务**只绑定 `127.0.0.1`**、**不代理 `/api`**（对 API 路径返回 501）——
它是页面外壳，不是网关。

#### 6.2.1 PostgreSQL 未启动时

一键脚本会在启动前做 TCP 探测并**直接提示原因**（不会自动切换数据库）：

```text
[XX] 数据库不可达：localhost:5433
     docker compose -f docker-compose.test.yml up -d
     本脚本**不会**自动切换数据库、不会执行 init_db 或任何迁移。
```

手动启动后端时，即使数据库不可达后端也会起来，但：

```text
GET /api/health  → {"status":"ok","database":"error"}
所有 Workflow 读接口 → 503（会话服务 / 授权存储不可用，fail-closed）
```

处理顺序：

1. 启动**本地**数据库（测试库容器，与应用库隔离）：
   `docker compose -f docker-compose.test.yml up -d`
2. 确认 `DATABASE_URL` 指向该库（本地 `5433`；**不要**指向生产 / 共享库）。
3. 首次建表（**手工执行**，脚本不会代劳）：`python -m backend.app.db.init_db`
4. 重跑一键启动脚本。

#### 6.2.2 访问 Workflow UI

```text
http://127.0.0.1:8080/index.html
```

页面需要两个输入：**Project ID**（你已获授权的项目）+ **真实 Workflow Run ID**。
页面**不提供** Run 列表，也不会创建 Run。


### 6.3 同源配置（Step 6 已解决：本地 dev-only 网关）

浏览器的 Workflow 只读面**必须同源**（会话 Cookie 为 `SameSite=Lax`，跨源不携带；
后端也**没有** CORS 中间件）。因此：

```text
❌ 页面 127.0.0.1:5173 + API 127.0.0.1:8000  → 不同源，浏览器拦截
✅ 页面与 /api 同一 Origin                    → 会话 Cookie 正常携带
```

**本地开发**（Step 6 交付，`scripts/dev-gateway.mjs`）：

```powershell
# 终端 1：后端（专用测试库；本地 http 需关掉 Secure）
# 连接串按 docker-compose.test.yml 的实际配置填写（**不要**指向生产 / 共享库）
$env:DATABASE_URL = "<测试数据库连接串，参照 docker-compose.test.yml 配置>"
$env:AUTH_COOKIE_SECURE = "false"      # 仅本地 http；生产必须 true
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

# 终端 2：同源网关（页面 + /api 同一 Origin）
cd frontend
npm run dev:same-origin               # = node scripts/dev-gateway.mjs --port 8080 --api-port 8000
# → http://127.0.0.1:8080/index.html
```

网关的硬性边界（由 `tests/dev-gateway.test.mjs` + pytest 锁定）：

| 约束 | 实现 |
| --- | --- |
| 只绑回环 | 固定 `127.0.0.1`；`--host` 一律拒绝启动；`--api-host` 必须是回环 |
| **不是认证旁路** | 不校验、不注入、不剥离任何认证头；`Cookie` / `X-CSRF-Token` 原样透传。认证与项目授权**完全**由后端执行 ⇒ 未登录仍 401、未授权仍 403 |
| 不注入凭据 | 代码中无任何服务端凭据头；后端密钥不进浏览器、不进网关 |
| 不开放 CORS | 不写任何 `Access-Control-*` 头（同源即无需） |
| 路径白名单 | 只转发 `/api/ui/`（读）与 `/api/auth/`（登录/登出）；其它 `/api/*` 一律 404，**不转发** Agent / Chat / Tool / 命令写接口 |
| 方法白名单 | `/api/ui/*` 仅 `GET`/`HEAD`（其它 405）；`/api/auth/*` 允许 `GET`/`HEAD`/`POST` |
| 静态白名单 | 仅 `index.html` / `styles.css` / `js/*`；不暴露 `tests/` `e2e/` `scripts/` `node_modules/` `package*.json` |
| 生产禁用 | `NODE_ENV=production` 拒绝启动；启动横幅固定打印 dev-only 警告 |

**生产部署**（仓库内**无**网关配置，须由部署层提供）：

```text
https://<host>/            → 静态页面（index.html + js/ + styles.css）
https://<host>/api/...     → 反向代理到 FastAPI（终结 TLS）
```

生产要求：**HTTPS**（`AUTH_COOKIE_SECURE=true`）、受控网关、网关侧覆盖而非盲信
客户端身份头。当前 `PRODUCTION_DEPLOYMENT` 仍 **BLOCKED**（HTTPS / 网络边界
= **NOT VERIFIED**，见 `docs/security.md` §7）。

### 6.3.1 真实浏览器验收（如何执行）

```powershell
# 依赖上面的后端 + 网关，且测试库中已有真实 Run（见 §6.6）
cd frontend
$env:WMS_REAL_API_BASE       = "http://127.0.0.1:8080"
$env:WMS_REAL_USERNAME       = "p7s6-alice"
$env:WMS_REAL_PASSWORD       = "<该账号口令>"
$env:WMS_REAL_PROJECT        = "p7s6-proj-a"
$env:WMS_REAL_RUN_ID         = "<真实 Run ID>"
$env:WMS_REAL_OTHER_PROJECT  = "p7s6-proj-b"   # 该账号**未授权**的项目
$env:WMS_REAL_OTHER_RUN_ID   = "<属于其它项目的 Run ID>"
npx playwright test e2e/real-api.spec.js
```

缺任一变量 ⇒ 整组 **skipped**（报告为"未执行"，**不**算 PASS）。

**不经浏览器**的真实链路另由 `tests/test_workflow_ui_real_run_db.py` 逐跳验证
（真实登录 → 会话 → 项目授权 → Read Service → PostgreSQL）。

### 6.4 登录并获取有效会话

```http
POST /api/auth/login
Content-Type: application/json

{"username": "<username>", "password": "<password>"}
```

成功即 `Set-Cookie: wms_ai_session=<token>; HttpOnly; SameSite=Lax`
（浏览器同源自动携带，JS 读不到）。

- 本地 `http://` 调试需关闭 Secure（否则浏览器不保存 Cookie）：
  `.env` 设 `AUTH_COOKIE_SECURE=false`（生产必须 `true`）。
- 会话绝对有效期：`AUTH_SESSION_TTL_HOURS`。
- 会话失效后所有 `/api/ui/...` 返回 **401**，页面提示"会话已过期，请重新登录"。

### 6.5 创建测试用户并授予项目成员

```powershell
# 1) 创建用户（口令经环境变量读取；不落盘）
$env:WMS_NEW_USER_PASSWORD = "<强口令>"
python -m backend.app.cli.create_user --username p7s5-alice --display-name "P7 Alice"
#   → 输出 user_id

# 2) 授予项目成员（M2.4；幂等；只接受 user_id，不做用户名模糊匹配）
python -m backend.app.cli.grant_role --user-id <user_id> --project <project_id> --role MEMBER
```

- 角色：`MEMBER`（读）/ `PROJECT_OPS`（读 + 项目运维）；
  `SYSTEM_OPS` **禁止**携带 `--project`。
- **没有**公开授权接口，也**不**自动授予 —— 未授权用户读取任何项目一律 **403**。
- 撤销：删除 `ai_ops.authorization_grant` 对应行（仓库无撤销 CLI）。

### 6.6 获得 Run ID 并查询

启动 Run（**服务间**命令 API，需要 `X-API-Key`，**不得**在浏览器使用）：

```powershell
$env:WORKFLOW_COMMAND_API_KEY = "<服务端强随机值>"
$env:WORKFLOW_COMMAND_API_PROJECT_ID = "<project_id>"
```

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/workflow-runs `
  -H "X-API-Key: $env:WORKFLOW_COMMAND_API_KEY" `
  -H "Content-Type: application/json" `
  -d '{\"workflow_id\":\"inventory_check\",\"input\":\"{\\\"material_code\\\":\\\"MAT-1\\\"}\"}'
```

响应中的 `workflow_run_id` 即页面所需 Run ID。

已注册定义（真实目录，只读）：`GET /api/workflow-definitions`

```text
inventory_check  v1   TOOL get_inventory → APPROVAL
answer_review    v1   AI（问题模板）→ APPROVAL
```

页面查询：填 **Project ID**（Run 所属项目，且当前账号须为其成员）→ 填 **Run ID**
→ 查询。

### 6.7 常见错误排查

| 状态 | 页面提示 | 排查 |
| --- | --- | --- |
| **401** | 需要登录 / 会话已过期 | 是否已 `POST /api/auth/login`；Cookie 是否被保存（本地 http 需 `AUTH_COOKIE_SECURE=false`）；会话是否过期 |
| **403** | 不是该项目的成员 | `ai_ops.authorization_grant` 是否有 `(user_id, project_id) → MEMBER/PROJECT_OPS`；项目 ID 是否与授权一致 |
| **404** | 未找到该资源 | Run ID 是否拼错；Run 是否属于该 `project_id`（跨项目故意与"不存在"同构）；审批 404 也可能是"该节点尚无审批行" |
| **502** | 数据无法安全投影 | 持久化数据损坏（非法 JSON `output` / 越白名单状态·类型·错误码）——**不会**自动修复，检查 `ai_ops.workflow_node_run` |
| **503** | 读取服务不可用 | 会话服务或授权存储不可用（DB 连接 / `authorization_grant` 表缺失）；**不是**"无权限" |
| 网络错误 | 无法连接读取服务 | 前后端是否同源（§6.3）；后端是否运行；静态服务是否只绑定了回环 |

### 6.8 验收自检（真实数据）

```powershell
cd frontend
npm test            # 160 例（Node 单元 + DOM 桩）
npm run test:e2e    # 56 例（Mock 50 + 真实 API 6；未配置真实环境时真实组跳过）

cd ..
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_workflow_ui_real_run_db.py   # 16 例（真实 Run）
```
