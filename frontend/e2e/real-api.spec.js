/**
 * Phase 7 Step 5/6 —— **真实 API** 浏览器集成规格（环境门控）。
 *
 * ⚠️ 诚实声明（**不得**把 Mock 结果当真实集成结果）：
 *
 *   本文件默认 **跳过**。它要求一个已就绪的**真实同源环境**：
 *
 *   ```text
 *   同源：页面与 /api 必须同一 Origin
 *         （Step 6：本地用 scripts/dev-gateway.mjs；生产用 HTTPS + 受控网关）
 *   专用测试库：localhost:5433/wms_ai_test（禁止生产库）
 *   已登录会话：真实测试账号（已授予目标项目成员权限）
 *   已存在的真实 Run：workflow_id/version 在定义目录中（如 inventory_check v1）
 *   ```
 *
 *   环境变量（缺任一 ⇒ 整组跳过，且报告为"未执行"，**不**算 PASS）：
 *
 *   ```text
 *   WMS_REAL_API_BASE        同源基址（如 http://127.0.0.1:8080）
 *   WMS_REAL_USERNAME        真实测试账号
 *   WMS_REAL_PASSWORD        其口令
 *   WMS_REAL_PROJECT         已授权项目 ID
 *   WMS_REAL_RUN_ID          已存在的真实 Run ID
 *   WMS_REAL_OTHER_PROJECT   账号**未授权**的项目 ID（用于 403 验收）
 *   WMS_REAL_OTHER_RUN_ID    属于 OTHER_PROJECT 的真实 Run ID（用于 403 / 404）
 *   ```
 *
 *   本组**不注册任何 `page.route`**：所有请求真实打到同源网关 → 真实后端 →
 *   真实 PostgreSQL（由 pytest 断言锁定，见 `TestRealApiE2EGate`）。
 */
import { expect, test } from '@playwright/test';

const BASE = process.env.WMS_REAL_API_BASE || '';
const USERNAME = process.env.WMS_REAL_USERNAME || '';
const PASSWORD = process.env.WMS_REAL_PASSWORD || '';
const PROJECT = process.env.WMS_REAL_PROJECT || '';
const RUN = process.env.WMS_REAL_RUN_ID || '';
const OTHER_PROJECT = process.env.WMS_REAL_OTHER_PROJECT || '';
const OTHER_RUN = process.env.WMS_REAL_OTHER_RUN_ID || '';

const READY =
  Boolean(BASE) &&
  Boolean(USERNAME) &&
  Boolean(PASSWORD) &&
  Boolean(PROJECT) &&
  Boolean(RUN) &&
  Boolean(OTHER_PROJECT) &&
  Boolean(OTHER_RUN);

const SKIP_REASON =
  '真实 API 浏览器集成未执行：需要同源部署 + 专用测试库 + 已授权账号 + 真实 Run。' +
  '未设置 WMS_REAL_API_BASE / WMS_REAL_USERNAME / WMS_REAL_PASSWORD / ' +
  'WMS_REAL_PROJECT / WMS_REAL_RUN_ID / WMS_REAL_OTHER_PROJECT / ' +
  'WMS_REAL_OTHER_RUN_ID ⇒ **跳过（非通过）**。' +
  '本地同源可用 frontend/scripts/dev-gateway.mjs（dev-only）。';

/** 提交查询（页面只用 GET）。 */
async function query(page, projectId, runId) {
  await page.fill('#project-id-input', projectId);
  await page.fill('#run-id-input', runId);
  await page.click('#run-submit');
}

/** 真实登录（服务端签发 HttpOnly Cookie；与浏览器上下文共享 Cookie 存储）。 */
async function login(context) {
  const response = await context.request.post(`${BASE}/api/auth/login`, {
    data: { username: USERNAME, password: PASSWORD },
  });
  expect(response.status(), await response.text()).toBe(200);
}

test.describe('真实 API 集成（环境门控）', () => {
  test.skip(!READY, SKIP_REASON);

  // 真实环境不使用 Mock 路由：本组用例**不**注册 page.route。
  test.use({ baseURL: undefined });

  test('真实登录 → Run / Nodes / Timeline / Graph / Approval 全部渲染', async ({
    page,
    context,
  }) => {
    const apiCalls = [];
    const writeMethods = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.pathname.startsWith('/api/')) {
        apiCalls.push({ path: url.pathname, headers: request.headers() });
        if (request.method().toUpperCase() !== 'GET') {
          writeMethods.push(`${request.method().toUpperCase()} ${url.pathname}`);
        }
      }
    });

    await login(context);
    await page.goto(`${BASE}/index.html`);
    await expect(page.locator('h1')).toContainText('Workflow 运维工作台');

    await query(page, PROJECT, RUN);

    // ① Run Overview：真实持久化字段
    await expect(page.locator('#run-overview')).toContainText(RUN);
    await expect(page.locator('#run-overview')).toContainText(PROJECT);
    await expect(page.locator('#run-overview')).toContainText('inventory_check');
    await expect(page.locator('#status-region')).not.toContainText('需要登录');
    await expect(page.locator('#status-region')).not.toContainText(
      '不是该项目的成员'
    );
    await expect(page.locator('#status-region')).not.toContainText('未找到该资源');

    // ② Node Attempts：真实 attempt 行（TOOL 已执行 + APPROVAL 等待态）
    const rows = page.locator('tr[data-attempt]');
    await expect(rows.first()).toBeVisible();
    expect(await rows.count()).toBeGreaterThan(0);
    await expect(
      page.locator('tr[data-attempt][data-node-id="query_stock"]').first()
    ).toBeVisible();

    // ③ Timeline：持久化事件（RUN_CREATED 起首）
    const events = page.locator('#run-timeline li[data-kind]');
    await expect(events.first()).toBeVisible();
    expect(await events.count()).toBeGreaterThan(0);
    await expect(events.first()).toHaveAttribute('data-kind', 'RUN_CREATED');

    // ④ Graph：真实注册定义（2 节点 / 1 条边，来自 next_node_id）
    await expect(page.locator('#graph-view')).toBeVisible();
    await expect(page.locator('#graph-view .graph-node')).toHaveCount(2);
    await expect(
      page.locator('#graph-view path.graph-edge[data-from="query_stock"]')
    ).toHaveCount(1);
    await expect(page.locator('#graph-meta')).toContainText('2 个节点');

    // ⑤ Approval：真实审批行（PENDING；不得显示为已通过）
    await expect(page.locator('#approval-detail')).toContainText('confirm_stock');
    await expect(page.locator('#approval-detail')).not.toContainText('已通过');

    // ⑥ 安全边界：无服务端密钥头、无写请求
    expect(apiCalls.length).toBeGreaterThanOrEqual(5);
    for (const call of apiCalls) {
      expect(call.headers['x-api-key']).toBeUndefined();
      expect(call.headers['authorization']).toBeUndefined();
    }
    expect(writeMethods).toEqual([]);
  });

  test('读取两次 payload 完全一致（读取不改变持久化状态）', async ({
    page,
    context,
  }) => {
    await login(context);
    await page.goto(`${BASE}/index.html`);

    const readAll = async () => {
      const paths = [
        `/api/ui/projects/${PROJECT}/workflow-runs/${RUN}`,
        `/api/ui/projects/${PROJECT}/workflow-runs/${RUN}/nodes`,
        `/api/ui/projects/${PROJECT}/workflow-runs/${RUN}/timeline`,
        `/api/ui/projects/${PROJECT}/workflow-runs/${RUN}/graph`,
        `/api/ui/projects/${PROJECT}/workflow-runs/${RUN}/approvals/confirm_stock`,
      ];
      const out = [];
      for (const path of paths) {
        const response = await context.request.get(`${BASE}${path}`);
        expect(response.status(), path).toBe(200);
        out.push(await response.json());
      }
      return out;
    };

    const before = await readAll();
    const after = await readAll();

    // 读取（含重复读取）不得改变任何持久化字段
    expect(after).toEqual(before);
  });

  test('未登录 ⇒ 401（页面提示需要登录，无任何数据行）', async ({ page, context }) => {
    await context.clearCookies();

    await page.goto(`${BASE}/index.html`);
    await query(page, PROJECT, RUN);

    await expect(page.locator('#status-region')).toContainText('需要登录');
    await expect(page.locator('#auth-hint')).toBeVisible();
    expect(await page.locator('tr[data-attempt]').count()).toBe(0);
    expect(await page.locator('#graph-view .graph-node').count()).toBe(0);
  });

  test('未授权项目 ⇒ 403（不诱导重新登录，不泄漏数据）', async ({
    page,
    context,
  }) => {
    await login(context);

    await page.goto(`${BASE}/index.html`);
    await query(page, OTHER_PROJECT, OTHER_RUN);

    await expect(page.locator('#status-region')).toContainText(
      '不是该项目的成员'
    );
    // 403 不得被误显示为"需要登录"
    await expect(page.locator('#status-region')).not.toContainText('需要登录');
    await expect(page.locator('#status-region')).not.toContainText(OTHER_RUN);
    expect(await page.locator('tr[data-attempt]').count()).toBe(0);
  });

  test('跨项目 Run ⇒ 404（与不存在同构，不泄漏存在性）', async ({
    page,
    context,
  }) => {
    await login(context);

    await page.goto(`${BASE}/index.html`);
    // 已授权项目 + 属于其它项目的 Run
    await query(page, PROJECT, OTHER_RUN);

    await expect(page.locator('#status-region')).toContainText('未找到该资源');
    await expect(page.locator('#status-region')).not.toContainText(OTHER_RUN);
    await expect(page.locator('#status-region')).not.toContainText(OTHER_PROJECT);
    expect(await page.locator('tr[data-attempt]').count()).toBe(0);
  });

  test('浏览器不持有服务端凭据（无浏览器存储写入）', async ({
    page,
    context,
  }) => {
    await login(context);
    await page.goto(`${BASE}/index.html`);
    await query(page, PROJECT, RUN);
    await expect(page.locator('#run-overview')).toContainText(RUN);

    const stores = await page.evaluate(() => {
      const local = [];
      for (let i = 0; i < localStorage.length; i += 1) {
        local.push(localStorage.key(i));
      }
      const session = [];
      for (let i = 0; i < sessionStorage.length; i += 1) {
        session.push(sessionStorage.key(i));
      }
      return { local, session, cookie: document.cookie };
    });

    expect(stores.local).toEqual([]);
    expect(stores.session).toEqual([]);
    // 仅 HttpOnly 会话 Cookie ⇒ JS 读不到（CSRF Cookie 也不含凭据）
    expect(stores.cookie).not.toContain('wms_ai_session');
  });
});
