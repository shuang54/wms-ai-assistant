# Phase 7 — Step 3B：真实浏览器 E2E 验收

项目：`D:\coding\ai\wms-ai-assistant`

## 一、目标

使用真实 Chromium 浏览器验证 Phase 7 Workflow 运维工作台的实际交互。

本阶段是**浏览器级验收**，不继续扩展业务功能，不重写前端框架。

验证现有功能：

1. 页面真实渲染与响应式布局。
2. Node Attempts 状态筛选。
3. Attempt 与 Timeline 双向定位。
4. 输出展开、折叠与复制。
5. Loading、401、403、404、502、503 和网络错误。
6. 同源 Session Cookie 与只读 API 边界。
7. 页面不存在伪造的成功状态。

## 二、保护工作区

开始前执行：

```powershell
git status --short
git diff --check
git diff --stat
git log -3 --oneline
```

保留 Step 2、Step 3 的所有 tracked 和 untracked 修改。

禁止：

- reset / clean / stash
- 覆盖现有文件
- 删除现有测试
- 修改冻结基线或治理门
- commit / push / amend

先阅读当前 `frontend/package.json`、静态服务脚本、`frontend/js/app.js`、`frontend/js/api-client.js` 及已有测试。

## 三、安装 Playwright

在 `frontend` 目录执行最小安装：

```powershell
cd frontend
npm install --save-dev @playwright/test
npx playwright install chromium
```

不要执行 `npm init playwright@latest`，避免自动生成大量模板或覆盖现有配置。

新增一个独立的 Playwright 配置和测试目录，例如：

```text
frontend/playwright.config.js
frontend/e2e/workbench.spec.js
```

配置要求：

- 默认只运行 Chromium。
- 使用独立测试端口，避免与开发服务器冲突。
- 优先复用现有 `scripts/serve-static.mjs`。
- 测试服务器仅绑定 `127.0.0.1`。
- 不代理或访问真实业务数据库。
- 不访问外部网络。
- 不需要真实 LLM。
- API 响应通过 Playwright `page.route()` 拦截并注入测试响应。
- 测试不得将 Mock 响应写入数据库。
- 浏览器测试失败时保留足够的错误上下文；不要通过重试或跳过来掩盖失败。

浏览器依赖下载失败时，记录实际错误并停止该项，不得声称测试通过。

## 四、真实浏览器测试场景

### 1. 页面启动与布局

验证：

- 页面可以打开。
- 标题显示为“Workflow 运维工作台”。
- Project ID、Run ID 输入框可操作。
- 查询按钮可用。
- 初始页面不会显示虚构的 Run 成功数据。
- 在桌面视口和窄屏视口下，没有页面级横向溢出。

至少测试：

```text
1280 × 800
390 × 844
```

窄屏允许表格容器内部横向滚动，但不应导致整个页面横向溢出。

### 2. 查询真实接口契约

通过 `page.route()` 为以下端点注入符合现有 DTO 的测试响应：

```text
/api/ui/projects/{project_id}/workflow-runs/{run_id}
/api/ui/projects/{project_id}/workflow-runs/{run_id}/nodes
/api/ui/projects/{project_id}/workflow-runs/{run_id}/timeline
/api/ui/projects/{project_id}/workflow-runs/{run_id}/approvals/{node_id}
```

严格使用当前项目真实响应结构，不自行发明字段。

验证：

- 查询时发出预期 GET 请求。
- URL 中的 Project ID 和 Run ID 正确编码。
- 请求采用 `credentials: same-origin`。
- 页面成功渲染 Run、节点 Attempt 和 Timeline。
- 不会调用写操作接口。

### 3. Node Attempts 筛选

构造包含：

- 一个成功节点。
- 一个失败节点。
- 一个等待审批节点。
- 一个未知状态节点。
- 同一节点的多个历史 Attempt。

验证：

- 选择不同状态后，命中数量与节点数量正确。
- 未命中的历史 Attempt 仍然保留，只做灰化展示。
- 清除筛选后恢复完整展示。
- 没有匹配项时出现空状态。
- 筛选不会产生新的网络请求。

### 4. Timeline 双向定位

验证：

- 点击 Attempt 的“在时间线中定位”按钮。
- 对应 Timeline 事件获得真实高亮样式。
- 点击 Timeline 的“定位 Attempt”按钮。
- 目标 Attempt 获得选中样式。
- 真实浏览器中调用滚动定位后，目标元素可以进入可视区域。
- 缺少关联事件时，不伪造事件。
- 多次 Attempt 不会错误定位到同一节点的错误历史记录。

### 5. 输出展开与复制

验证：

- 点击展开按钮后输出可见。
- 再次点击后输出折叠。
- 输出原文完整保留，包括换行、引号和类似 HTML 的字符串。
- 使用浏览器剪贴板权限或可控 Clipboard API mock 验证复制内容逐字符一致。
- 复制成功、拒绝和空输出分别显示正确提示。
- 不使用 `execCommand`。
- 输出不会作为 HTML 执行。

剪贴板测试需要明确区分浏览器真实权限验证与 API mock 验证，不得混为一谈。

### 6. 错误状态

逐个注入响应：

```text
401
403
404
502
503
```

另测试网络请求失败。

验证：

- 页面显示对应的固定提示。
- 401 不会被误显示为项目权限不足。
- 403 不会诱导用户重新登录。
- 404 不泄漏其他项目是否存在 Run。
- 502 不会自动修复或截断数据。
- 503 和网络错误提供重试入口。
- 重试只重新发送 GET。
- 错误提示不包含内部异常正文、SQL、连接串或服务端密钥。

### 7. 防止请求竞态

构造两个延迟不同的查询响应。

先提交 Run A，再提交 Run B，并让 A 最后返回。

验证旧响应不会覆盖当前 Run B 的页面数据。

如果现有实现不能通过此测试，记录为实际发现的问题，优先最小修复前端状态处理，不修改后端业务逻辑。

### 8. Session 与安全边界

验证：

- 浏览器不构造 `X-API-Key`。
- 不将 API Key、Authorization Header 或 Session Cookie 写入 localStorage/sessionStorage。
- 只调用 `/api/ui/projects/...` 会话认证端点。
- 不调用 `/api/workflow-runs/...` 服务间端点。
- 不发起 POST、PUT、PATCH、DELETE 等写请求。
- 后端输出不会执行为 HTML 或脚本。

测试 Cookie 时只使用虚构的测试值，不读取真实用户凭证。

## 五、测试命令

新增脚本，例如：

```json
{
  "scripts": {
    "test": "node --test tests/",
    "test:e2e": "playwright test"
  }
}
```

保留现有 `test` 命令行为。

运行：

```powershell
npm test
npm run test:e2e
```

随后回到项目根目录：

```powershell
cd ..
python -m pytest -q tests/test_workflow_ui_security.py
python -m pytest -q tests/test_workflow_ui_read_api.py tests/test_workflow_read_api_security_audit.py
python -m compileall -q backend tests scripts
git diff --check
```

如需运行 DB-gated 测试，必须先确认连接的是专用测试数据库。该阶段默认不需要数据库。

## 六、验收标准

必须区分以下结果：

- Node 单元测试。
- DOM 桩测试。
- 真实 Chromium E2E。
- 后端 API / 安全回归。
- DB-gated 集成测试。

不得将前三类混为一谈。

如果真实浏览器测试通过，可报告：

```text
Chromium E2E = PASS
```

如果浏览器未能启动、依赖下载失败或部分场景未覆盖，必须明确列出限制，不得报告完整 PASS。

## 七、最终报告

```text
【Phase 7 — Step 3B Browser E2E Report】

1. 开始时 Git 状态
2. 安装的测试依赖与版本
3. 浏览器版本
4. 新增/修改文件
5. 页面启动与布局结果
6. 状态筛选结果
7. Timeline 双向定位结果
8. 输出与剪贴板结果
9. 错误状态结果
10. 请求竞态结果
11. 安全边界结果
12. Node 单元测试结果
13. Chromium E2E 测试结果
14. 后端回归结果
15. compileall / diff check
16. 未解决问题
17. Git 状态

不修改 Runtime、Schema 或授权模型。
不 commit / push。
完成后立即 STOP，等待审阅。
```

