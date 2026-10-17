/**
 * Phase 7 Step 3B —— 真实 Chromium E2E：Workflow 运维工作台。
 *
 * 场景对应任务书 §四 1–8：
 *   1. 页面启动与布局（桌面 / 窄屏）        2. 查询真实接口契约
 *   3. Node Attempts 状态筛选               4. Timeline 双向定位
 *   5. 输出展开、折叠与复制                 6. 错误状态（401/403/404/502/503/网络）
 *   7. 请求竞态                             8. Session 与安全边界
 *
 * 所有 API 响应均由 `page.route()` 注入（fixtures.js）；不访问数据库、
 * 不访问外部网络、不需要真实 LLM；不写入任何持久化层。
 */
import {
  expect,
  PROJECT,
  RUN,
  T0,
  T1,
  T2,
  openAndQuery,
  mockApi,
  approval,
  nodeRun,
  runDetail,
  standardGraph,
  standardNodes,
  timelineEvent,
  standardTimeline,
  test,
} from './fixtures.js';

// ============================================================
// 1. 页面启动与布局
// ============================================================

test.describe('页面启动与布局', () => {
  test('标题、输入框与查询按钮可用，初始无虚构成功数据', async ({ page }) => {
    await mockApi(page);
    await page.goto('/index.html');

    await expect(page).toHaveTitle(/Workflow 运维工作台/);
    await expect(page.locator('h1')).toHaveText('Workflow 运维工作台');

    await expect(page.locator('#project-id-input')).toBeEditable();
    await expect(page.locator('#run-id-input')).toBeEditable();
    await expect(page.locator('#run-submit')).toBeEnabled();

    // 初始状态：bindApp 的首次 paint() 会渲染**空态**区块（存在但无数据）。
    // 关键断言：**没有任何** attempt 行 / 时间线项 / 审批记录（不伪造成功数据）。
    await expect(page.locator('#status-region')).toContainText('请输入 Project ID');
    expect(await page.locator('tr[data-attempt]').count()).toBe(0);
    expect(await page.locator('li[data-kind]').count()).toBe(0);
    await expect(page.locator('#node-attempts')).toContainText('尚未加载');
    await expect(page.locator('#run-timeline')).toContainText('尚未加载');
    await expect(page.locator('#approval-detail')).toContainText('尚未加载');
    await expect(page.locator('#current-run')).toHaveText('尚未查询任何 Run');
    await expect(page.locator('#current-run')).toHaveText('尚未查询任何 Run');
    expect(await page.locator('tr[data-attempt]').count()).toBe(0);
    // 初始也不得有任何时间线项
    expect(await page.locator('li[data-kind]').count()).toBe(0);
  });

  test('桌面视口 1280x800：无页面级横向溢出', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await openAndQuery(page);

    const overflow = await page.evaluate(() => {
      const doc = document.documentElement;
      return {
        scrollWidth: doc.scrollWidth,
        clientWidth: doc.clientWidth,
        overflow: doc.scrollWidth - doc.clientWidth,
      };
    });
    expect(overflow.overflow, JSON.stringify(overflow)).toBeLessThanOrEqual(0);
  });

  test('窄屏视口 390x844：无页面级横向溢出（表格内部可滚动）', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openAndQuery(page);

    const doc = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    expect(doc.scrollWidth - doc.clientWidth).toBeLessThanOrEqual(0);

    await expect(page.locator('#node-attempts table').first()).toBeVisible();
    expect(await page.locator('tr[data-attempt]').count()).toBeGreaterThan(0);
  });

  test('窄屏下查询区改为单列布局', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockApi(page);
    await page.goto('/index.html');

    const columns = await page.evaluate(() => {
      const form = document.querySelector('#run-form');
      return getComputedStyle(form).gridTemplateColumns.split(' ').length;
    });
    expect(columns).toBe(1);
  });
});

// ============================================================
// 2. 查询真实接口契约
// ============================================================

test.describe('查询接口契约', () => {
  test('发出预期 GET 请求，URL 正确编码，credentials=same-origin', async ({ page }) => {
    // 用 page.on('request') 监听（不受 page.route 叠加影响）
    const seen = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.pathname.startsWith('/api/')) {
        seen.push({
          url: url.pathname,
          method: request.method().toUpperCase(),
          headers: request.headers(),
        });
      }
    });

    await openAndQuery(page);
    const apiCalls = seen.filter((r) => r.url.startsWith('/api/'));
    // Run + Nodes + Timeline + Graph + 审批 = 5
    expect(apiCalls.length).toBe(5);
    expect(apiCalls.length).toBe(5);
    for (const call of apiCalls) {
      expect(call.method).toBe('GET');
      expect(call.headers['x-api-key']).toBeUndefined();
      expect(call.headers['authorization']).toBeUndefined();
    }
    expect(apiCalls.map((c) => c.url).sort()).toEqual([
      `/api/ui/projects/${PROJECT}/workflow-runs/${RUN}`,
      `/api/ui/projects/${PROJECT}/workflow-runs/${RUN}/approvals/gate`,
      `/api/ui/projects/${PROJECT}/workflow-runs/${RUN}/graph`,
      `/api/ui/projects/${PROJECT}/workflow-runs/${RUN}/nodes`,
      `/api/ui/projects/${PROJECT}/workflow-runs/${RUN}/timeline`,
    ]);

    // credentials 语义：同源才携带 Cookie（fetch 默认 same-origin）
    const credentials = await page.evaluate(async () => {
      const seen = [];
      const original = window.fetch;
      window.fetch = (url, init) => {
        seen.push({ url: String(url), credentials: init && init.credentials });
        return original(url, init);
      };
      document.querySelector('#run-refresh').click();
      await new Promise((r) => setTimeout(r, 800));
      window.fetch = original;
      return seen;
    });
    expect(credentials.length).toBeGreaterThan(0);
    for (const call of credentials) {
      expect(call.credentials).toBe('same-origin');
      expect(call.url.startsWith('/api/')).toBe(true);
    }
  });

  test('含特殊字符的 Run ID 被正确转义', async ({ page }) => {
    const weird = 'wf/run with space';
    await mockApi(page);
    await page.goto('/index.html');
    await page.fill('#project-id-input', PROJECT);
    await page.fill('#run-id-input', weird);

    const requested = [];
    await page.route('**/api/**', async (route, request) => {
      requested.push(new URL(request.url()).pathname);
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(runDetail({ workflow_run_id: weird })),
      });
    });

    await page.click('#run-submit');
    await page.waitForTimeout(600);

    const paths = requested.filter((p) => p.includes('workflow-runs'));
    expect(paths.length).toBeGreaterThan(0);
    for (const path of paths) {
      expect(path).toContain(encodeURIComponent(weird));
    }
  });

  test('页面渲染 Run / Node Attempt / Timeline 真实字段', async ({ page }) => {
    await openAndQuery(page);

    const overview = page.locator('#run-overview dl.kv');
    await expect(overview).toContainText(RUN);
    await expect(overview).toContainText(PROJECT);
    await expect(overview).toContainText('wf-def-1');
    await expect(overview).toContainText('NODE_EXECUTION');
    await expect(overview).toContainText('4');
    await expect(overview).not.toContainText('DO-NOT-EXPOSE');
    await expect(overview).not.toContainText('req-secret-trace');

    expect(await page.locator('tr[data-attempt]').count()).toBe(6);

    const kinds = await page
      .locator('#run-timeline li[data-kind]')
      .evaluateAll((items) => items.map((item) => item.getAttribute('data-kind')));
    expect(kinds.length).toBe(6);
    expect(kinds[0]).toBe('RUN_CREATED');
    expect(kinds[kinds.length - 1]).toBe('APPROVAL_RESOLVED');

    // 状态以**文字标签 + 原始枚举**呈现（不单靠颜色）
    const statusCell = page.locator('#run-overview dd[data-key="status"]');
    await expect(statusCell).toContainText('运行中');
    await expect(statusCell).toContainText('RUNNING');
  });

  test('不发起任何写方法请求', async ({ page }) => {
    const methods = [];
    page.on('request', (request) => {
      if (new URL(request.url()).pathname.startsWith('/api/')) {
        methods.push(request.method().toUpperCase());
      }
    });

    await openAndQuery(page);
    expect(methods.length).toBe(5);
    expect(methods.every((m) => m === 'GET')).toBe(true);
  });
});

// ============================================================
// 3. Node Attempts 状态筛选
// ============================================================

test.describe('Node Attempts 状态筛选', () => {
  test.beforeEach(async ({ page }) => {
    await openAndQuery(page);
  });

  test('默认显示全部历史 Attempt（多 Attempt 不折叠）', async ({ page }) => {
    expect(await page.locator('tr[data-attempt]').count()).toBe(6);
    await expect(page.locator('#filter-meta')).toContainText('全部状态');
    const toolAttempts = await page
      .locator('tr[data-attempt][data-node-id="tool"]')
      .evaluateAll((rows) => rows.map((r) => r.getAttribute('data-attempt')));
    expect(toolAttempts).toEqual(['1', '2', '3']);
  });

  test('筛选"失败"：命中计数正确且未命中 attempt 仍保留（灰化）', async ({ page }) => {
    await page.selectOption('#node-filter', 'failed');

    await expect(page.locator('#filter-meta')).toContainText('筛选结果：1 次命中 / 共 6 次');
    expect(await page.locator('tr[data-attempt][data-node-id="tool"]').count()).toBe(3);
    expect(await page.locator('tr[data-attempt][data-node-id="ok_node"]').count()).toBe(0);
    expect(await page.locator('tr[data-attempt][data-node-id="gate"]').count()).toBe(0);

    const matches = await page
      .locator('tr[data-attempt][data-node-id="tool"]')
      .evaluateAll((rows) => rows.map((r) => r.getAttribute('data-matches')));
    expect(matches).toEqual(['true', 'false', 'false']);

    // 灰化样式施加在 td 上（CSS: tr.is-dimmed td { opacity: .45 }）
    const dimmedTd = page.locator('tr[data-attempt][data-matches="false"] td').first();
    const opacity = await dimmedTd.evaluate((el) => getComputedStyle(el).opacity);
    expect(Number(opacity)).toBeLessThan(1);

    const hitTd = page.locator('tr[data-attempt][data-matches="true"] td').first();
    const hitOpacity = await hitTd.evaluate((el) => getComputedStyle(el).opacity);
    expect(Number(hitOpacity)).toBe(1);
  });

  test('筛选"已完成" / "等待审批" / "运行中" 命中正确', async ({ page }) => {
    await page.selectOption('#node-filter', 'succeeded');
    await expect(page.locator('#filter-meta')).toContainText('2 次命中');
    expect(await page.locator('tr[data-attempt][data-node-id="ok_node"]').count()).toBe(1);
    expect(await page.locator('tr[data-attempt][data-node-id="tool"]').count()).toBe(3);

    await page.selectOption('#node-filter', 'waiting_approval');
    await expect(page.locator('#filter-meta')).toContainText('1 次命中');
    expect(await page.locator('tr[data-attempt][data-node-id="gate"]').count()).toBe(1);

    await page.selectOption('#node-filter', 'running');
    await expect(page.locator('#filter-meta')).toContainText('1 次命中');
  });

  test('筛选"未知状态"：不映射为成功，原样显示', async ({ page }) => {
    await page.selectOption('#node-filter', 'unknown');

    expect(await page.locator('tr[data-attempt][data-node-id="odd"]').count()).toBe(1);
    await expect(page.locator('tr[data-attempt][data-node-id="odd"]')).toContainText(
      'TOTALLY_UNKNOWN'
    );
    await expect(page.locator('tr[data-attempt][data-node-id="odd"]')).toContainText(
      '未在域白名单内'
    );
    await expect(page.locator('tr[data-attempt][data-node-id="odd"]')).not.toContainText(
      '成功'
    );
  });

  test('清除筛选恢复完整展示', async ({ page }) => {
    await page.selectOption('#node-filter', 'failed');
    expect(await page.locator('tr[data-attempt]').count()).toBe(3);

    await page.click('#node-filter-clear');
    expect(await page.locator('tr[data-attempt]').count()).toBe(6);
    await expect(page.locator('#node-filter')).toHaveValue('all');
    await expect(page.locator('#filter-meta')).toContainText('全部状态');
  });

  test('无匹配项时显示明确空状态', async ({ page }) => {
    await mockApi(page, { nodes: [nodeRun({ status: 'SUCCEEDED' })] });
    await page.goto('/index.html');
    await page.fill('#project-id-input', PROJECT);
    await page.fill('#run-id-input', RUN);
    await page.click('#run-submit');
    await page.waitForTimeout(500);

    await page.selectOption('#node-filter', 'failed');
    await expect(page.locator('#node-attempts')).toContainText('没有匹配的 Attempt');
    await expect(page.locator('#empty-clear-filter')).toBeVisible();
  });

  test('筛选不产生新的网络请求', async ({ page }) => {
    let count = 0;
    await page.route('**/api/**', async (route) => {
      count += 1;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
      });
    });

    await page.selectOption('#node-filter', 'failed');
    await page.waitForTimeout(200);
    await page.selectOption('#node-filter', 'succeeded');
    await page.waitForTimeout(200);

    expect(count).toBe(0);
  });
});

// ============================================================
// 4. Timeline 双向定位
// ============================================================

test.describe('Timeline 双向定位', () => {
  test.beforeEach(async ({ page }) => {
    await openAndQuery(page);
  });

  test('Attempt -> Timeline：相关事件获得真实高亮样式', async ({ page }) => {
    const row = page.locator(
      'tr[data-attempt][data-node-id="tool"][data-attempt="1"]'
    );
    await row.getByRole('button', { name: '在时间线中定位' }).click();

    const related = page.locator('#run-timeline li.timeline-item.is-related');
    expect(await related.count()).toBe(2);
    const bg = await related
      .first()
      .evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(bg).not.toBe('rgba(0, 0, 0, 0)');

    await expect(row).toHaveClass(/is-selected/);
  });

  test('Timeline -> Attempt：目标行选中并可进入可视区域', async ({ page }) => {
    const event = page
      .locator('#run-timeline li[data-node-id="tool"][data-attempt="2"]')
      .first();
    await event.getByRole('button', { name: '定位 Attempt' }).click();

    const target = page.locator('tr[data-attempt]#attempt-tool-2');
    await expect(target).toHaveClass(/is-selected/);

    // 真实浏览器滚动定位：scrollIntoView({behavior:'smooth'}) 是**动画**，
    // 需等待其结束后再判定。断言目标行与视口有交集（部分可见即算进入可视区域）。
    await page.waitForTimeout(900);
    const inViewport = await target.evaluate((el) => {
      const rect = el.getBoundingClientRect();
      const viewHeight = window.innerHeight || document.documentElement.clientHeight;
      return rect.bottom > 0 && rect.top < viewHeight;
    });
    expect(inViewport).toBe(true);
  });

  test('多次 Attempt 不会错误定位到同一节点的其它历史记录', async ({ page }) => {
    await page
      .locator('tr[data-attempt][data-node-id="tool"][data-attempt="1"]')
      .getByRole('button', { name: '在时间线中定位' })
      .click();
    expect(await page.locator('#run-timeline li.timeline-item.is-related').count()).toBe(2);

    await page
      .locator('tr[data-attempt][data-node-id="tool"][data-attempt="2"]')
      .getByRole('button', { name: '在时间线中定位' })
      .click();
    const second = page.locator('#run-timeline li.timeline-item.is-related');
    expect(await second.count()).toBe(2);

    const times = await second.evaluateAll((items) =>
      items.map((item) =>
        item.querySelector('.timeline-time').textContent.trim()
      )
    );
    expect(times).toEqual([T1, T2]);
  });

  test('缺少关联事件时不伪造事件', async ({ page }) => {
    const before = await page.locator('#run-timeline li[data-kind]').count();
    await page
      .locator('tr[data-attempt][data-node-id="tool"][data-attempt="3"]')
      .getByRole('button', { name: '在时间线中定位' })
      .click();
    const after = await page.locator('#run-timeline li[data-kind]').count();

    expect(after).toBe(before);
    expect(await page.locator('#run-timeline li.timeline-item.is-related').count()).toBe(0);
  });

  test('Run 级事件无可定位身份时不渲染定位按钮', async ({ page }) => {
    const runEvent = page.locator('#run-timeline li[data-kind="RUN_CREATED"]').first();
    await expect(runEvent).toContainText('Run');
    await expect(runEvent.getByRole('button', { name: '定位 Attempt' })).toHaveCount(0);
  });
});

// ============================================================
// 5. 输出展开、折叠与复制
// ============================================================

test.describe('输出查看与复制', () => {
  test.beforeEach(async ({ page }) => {
    await openAndQuery(page);
  });

  test('展开与折叠切换可见性', async ({ page }) => {
    const row = page.locator(
      'tr[data-attempt][data-node-id="tool"][data-attempt="2"]'
    );
    const pre = row.locator('pre.output-full');

    await expect(pre).toBeHidden();
    await row.getByRole('button', { name: '展开' }).click();
    await expect(pre).toBeVisible();
    await row.getByRole('button', { name: '折叠' }).click();
    await expect(pre).toBeHidden();

    await row.locator('details.output summary').click();
    await expect(pre).toBeVisible();
  });

  test('输出原文完整保留（换行 / 引号 / 类 HTML 字符串）', async ({ page }) => {
    await page.addInitScript(() => {
      window.__alerted = false;
      window.alert = () => {
        window.__alerted = true;
      };
    });
    const payload =
      '{"qty": 3,\n  "note": "he said \\"hi\\"",\n  "html": "<script>alert(1)</script>"}';
    await mockApi(page, {
      nodes: [nodeRun({ node_id: 'x', attempt: 1, status: 'SUCCEEDED', output: payload })],
    });
    await page.goto('/index.html');
    await page.fill('#project-id-input', PROJECT);
    await page.fill('#run-id-input', RUN);
    await page.click('#run-submit');
    await page.waitForTimeout(500);

    const row = page.locator('tr[data-attempt][data-node-id="x"]');
    await row.getByRole('button', { name: '展开' }).click();

    const text = await row.locator('pre.output-full').textContent();
    expect(text).toBe(payload);
    expect(await page.evaluate(() => window.__alerted)).toBe(false);
  });

  test('复制：内容逐字符一致（可控 Clipboard API mock）', async ({ page }) => {
    const row = page.locator(
      'tr[data-attempt][data-node-id="tool"][data-attempt="2"]'
    );
    await row.getByRole('button', { name: '复制原文' }).click();

    await expect(page.locator('#copy-status')).toContainText('已复制');
    const copied = await page.evaluate(() => window.__copied);
    expect(copied).toEqual(['{"qty":3}']);
  });

  test('复制：被拒绝时显示失败提示（不含内部异常原文）', async ({ page }) => {
    await page.evaluate(() => {
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: {
          writeText: async () => {
            throw new Error('NotAllowedError: permission denied');
          },
        },
      });
    });

    const row = page.locator(
      'tr[data-attempt][data-node-id="tool"][data-attempt="2"]'
    );
    await row.getByRole('button', { name: '复制原文' }).click();

    await expect(page.locator('#copy-status')).toContainText('复制失败');
    await expect(page.locator('#copy-status')).not.toContainText('NotAllowedError');
  });

  test('空输出：显示占位且无复制按钮', async ({ page }) => {
    const row = page.locator(
      'tr[data-attempt][data-node-id="tool"][data-attempt="1"]'
    );
    await expect(row).toContainText('（无持久化输出）');
    await expect(row.getByRole('button', { name: '复制原文' })).toHaveCount(0);
  });

  test('未使用 execCommand', async ({ page }) => {
    const used = await page.evaluate(async () => {
      const original = document.execCommand;
      let called = false;
      document.execCommand = (...args) => {
        called = true;
        return original ? original.apply(document, args) : false;
      };
      const row = document.querySelector(
        'tr[data-attempt][data-node-id="tool"][data-attempt="2"]'
      );
      const button = Array.from(row.querySelectorAll('button')).find((b) =>
        b.textContent.includes('复制原文')
      );
      button.click();
      await new Promise((r) => setTimeout(r, 300));
      document.execCommand = original;
      return called;
    });
    expect(used).toBe(false);
  });
});

// ============================================================
// 6. 错误状态
// ============================================================

test.describe('错误状态', () => {
  const cases = [
    {
      name: '401 未登录',
      status: 401,
      pattern: /需要登录/,
      forbidden: [/不是该项目的成员/],
    },
    {
      name: '403 非项目成员',
      status: 403,
      pattern: /不是该项目的成员/,
      forbidden: [/需要登录/],
    },
    { name: '404 资源不存在', status: 404, pattern: /未找到该资源/, forbidden: [/其它项目/] },
    { name: '502 投影失败', status: 502, pattern: /无法安全投影/, forbidden: [/psycopg/, /SELECT/] },
    { name: '503 服务不可用', status: 503, pattern: /不可用/, forbidden: [/internal/] },
  ];

  for (const item of cases) {
    test(`${item.name}：固定提示且不泄漏内部细节`, async ({ page }) => {
      await page.route('**/api/**', async (route) => {
        await route.fulfill({
          status: item.status,
          contentType: 'application/json',
          body: JSON.stringify({
            detail: 'SECRET-INTERNAL-DETAIL: psycopg.OperationalError SELECT 1',
          }),
        });
      });

      await page.goto('/index.html');
      await page.fill('#project-id-input', PROJECT);
      await page.fill('#run-id-input', RUN);
      await page.click('#run-submit');

      const status = page.locator('#status-region');
      await expect(status).toContainText(item.pattern);
      await expect(status).toContainText(`HTTP ${item.status}`);
      await expect(status).not.toContainText('SECRET-INTERNAL-DETAIL');

      for (const forbidden of item.forbidden) {
        await expect(status).not.toContainText(forbidden);
      }

      if (item.status === 502) {
        expect(await page.locator('tr[data-attempt]').count()).toBe(0);
      }
    });
  }

  test('网络错误：提供重试入口，且重试只发 GET', async ({ page }) => {
    const requests = [];
    await page.route('**/api/**', async (route, request) => {
      requests.push(request.method().toUpperCase());
      await route.abort('failed');
    });
    await page.goto('/index.html');
    await page.fill('#project-id-input', PROJECT);
    await page.fill('#run-id-input', RUN);
    await page.click('#run-submit');

    await expect(page.locator('#status-region')).toContainText('网络错误');

    await page.click('#retry-button');
    await page.waitForTimeout(500);

    expect(requests.length).toBeGreaterThan(0);
    expect(requests.every((m) => m === 'GET')).toBe(true);
  });

  test('错误后保留用户输入', async ({ page }) => {
    await page.route('**/api/**', async (route) => {
      await route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: '{"detail":"x"}',
      });
    });
    await page.goto('/index.html');
    await page.fill('#project-id-input', PROJECT);
    await page.fill('#run-id-input', RUN);
    await page.click('#run-submit');

    await expect(page.locator('#status-region')).toContainText('未找到');
    await expect(page.locator('#project-id-input')).toHaveValue(PROJECT);
    await expect(page.locator('#run-id-input')).toHaveValue(RUN);
  });

  test('404 不泄漏跨项目资源存在性', async ({ page }) => {
    await page.route('**/api/**', async (route) => {
      await route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Workflow Run 不存在' }),
      });
    });
    await page.goto('/index.html');
    await page.fill('#project-id-input', 'other-project');
    await page.fill('#run-id-input', 'run-in-other-project');
    await page.click('#run-submit');

    const text = await page.locator('#status-region').textContent();
    expect(text).toContain('未找到该资源');
    expect(text).not.toContain('other-project');
    expect(text).not.toContain('run-in-other-project');
  });
});

// ============================================================
// 7. 请求竞态
// ============================================================

test.describe('请求竞态', () => {
  test('旧响应不会覆盖新查询（Run A 慢、Run B 快）', async ({ page }) => {
    const RUN_A = 'run-slow-A';
    const RUN_B = 'run-fast-B';

    await page.route('**/api/**', async (route, request) => {
      const path = new URL(request.url()).pathname;
      if (path.includes(RUN_A)) {
        await new Promise((r) => setTimeout(r, 1500));
      }
      const isA = path.includes(RUN_A);
      const runId = isA ? RUN_A : RUN_B;
      if (path.endsWith(runId)) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(runDetail({ workflow_run_id: runId })),
        });
        return;
      }
      if (path.endsWith('/nodes') || path.endsWith('/timeline')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(isA ? standardNodes() : []),
        });
        return;
      }
      await route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: '{"detail":"x"}',
      });
    });

    await page.goto('/index.html');

    await page.fill('#project-id-input', PROJECT);
    await page.fill('#run-id-input', RUN_A);
    await page.click('#run-submit');
    await page.waitForTimeout(200);

    await page.fill('#run-id-input', RUN_B);
    await page.click('#run-submit');
    await expect(page.locator('#current-run')).toContainText(RUN_B);
    await expect(page.locator('#node-attempts')).toContainText('尚未加载该 Run 的节点执行记录');

    await page.waitForTimeout(1800);

    await expect(page.locator('#current-run')).toContainText(RUN_B);
    await expect(page.locator('#current-run')).not.toContainText(RUN_A);
    await expect(page.locator('#node-attempts')).toContainText('尚未加载该 Run 的节点执行记录');
    expect(await page.locator('tr[data-attempt]').count()).toBe(0);
  });
});

// ============================================================
// 8. Session 与安全边界
// ============================================================

test.describe('安全边界', () => {
  test('不构造 X-API-Key / Authorization 头；不调用服务间端点', async ({ page }) => {
    const seen = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.pathname.startsWith('/api/')) {
        seen.push({ url: url.pathname, headers: request.headers() });
      }
    });

    await openAndQuery(page);
    expect(seen.length).toBe(5);

    for (const call of seen) {
      expect(call.headers['x-api-key']).toBeUndefined();
      expect(call.headers['authorization']).toBeUndefined();
      expect(call.url.startsWith('/api/ui/projects/')).toBe(true);
      expect(call.url.includes('/api/workflow-runs/')).toBe(false);
      expect(call.url.includes('/api/workflow-approvals/')).toBe(false);
    }
  });

  test('不在 localStorage / sessionStorage / cookie 写入任何凭证', async ({ page }) => {
    await openAndQuery(page);
    expect(await page.locator('tr[data-attempt]').count()).toBe(6);

    const stores = await page.evaluate(() => {
      const local = [];
      for (let i = 0; i < localStorage.length; i += 1) local.push(localStorage.key(i));
      const session = [];
      for (let i = 0; i < sessionStorage.length; i += 1) {
        session.push(sessionStorage.key(i));
      }
      return { local, session, cookies: document.cookie };
    });

    expect(stores.local).toEqual([]);
    expect(stores.session).toEqual([]);
    expect(stores.cookies).toBe('');
  });

  test('Cookie 为 HttpOnly 语义（页面 JS 读不到，但请求可正常发出）', async ({
    page,
    context,
  }) => {
    await context.addCookies([
      {
        name: 'wms_ai_session',
        value: 'fictitious-test-cookie-value',
        domain: '127.0.0.1',
        path: '/',
        httpOnly: true,
        secure: false,
        sameSite: 'Lax',
      },
    ]);
    // 用 page.on('request') 统计请求数（不依赖 route 叠加顺序）
    let apiRequests = 0;
    page.on('request', (request) => {
      if (new URL(request.url()).pathname.startsWith('/api/')) apiRequests += 1;
    });

    await openAndQuery(page);
    const afterQuery = apiRequests;
    expect(afterQuery).toBe(5);

    // 页面脚本读不到 HttpOnly Cookie
    expect(await page.evaluate(() => document.cookie)).toBe('');

    // 但请求仍可正常发出（浏览器自动携带；只读 GET）
    await page.click('#run-refresh');
    await page.waitForTimeout(800);
    expect(apiRequests).toBeGreaterThan(afterQuery);
  });

  test('后端输出不执行为 HTML 或脚本', async ({ page }) => {
    await page.addInitScript(() => {
      window.__alerted = false;
      window.alert = () => {
        window.__alerted = true;
      };
    });
    const payload = '<img src=x onerror="window.__alerted=true">';
    await mockApi(page, {
      nodes: [nodeRun({ node_id: 'img', attempt: 1, status: 'SUCCEEDED', output: payload })],
    });
    await page.goto('/index.html');
    await page.fill('#project-id-input', PROJECT);
    await page.fill('#run-id-input', RUN);
    await page.click('#run-submit');
    await page.waitForTimeout(500);

    const row = page.locator('tr[data-attempt][data-node-id="img"]');
    await row.getByRole('button', { name: '展开' }).click();

    expect(await page.evaluate(() => window.__alerted)).toBe(false);
    const text = await row.locator('pre.output-full').textContent();
    expect(text).toBe(payload);
  });

  test('审批无记录时显示"暂无审批记录"（不显示为成功）', async ({ page }) => {
    await mockApi(page, { approvals: { gate: null } });
    await page.goto('/index.html');
    await page.fill('#project-id-input', PROJECT);
    await page.fill('#run-id-input', RUN);
    await page.click('#run-submit');
    await page.waitForTimeout(600);

    const section = page.locator('#approval-detail');
    await expect(section).toContainText('暂无审批记录');
    await expect(section).not.toContainText('已通过');
  });
});


// ============================================================
// Step 4：Graph View（只读拓扑）
// ============================================================

/** 真实形态的 graph 响应（线性：TOOL → APPROVAL）。 */
const LINEAR_GRAPH = {
  status: 'available',
  workflow_id: 'inventory_check',
  workflow_version: 'v1',
  definition_version: 'v1',
  start_node_id: 'query_stock',
  nodes: [
    {
      node_id: 'query_stock',
      node_type: 'TOOL',
      next_node_id: 'confirm_stock',
      on_true_node_id: null,
      on_false_node_id: null,
    },
    {
      node_id: 'confirm_stock',
      node_type: 'APPROVAL',
      next_node_id: null,
      on_true_node_id: null,
      on_false_node_id: null,
    },
  ],
};

/** 真实形态的 graph 响应（CONDITION 双分支 + 汇聚）。 */
const BRANCH_GRAPH = {
  status: 'available',
  workflow_id: 'answer_review',
  workflow_version: 'v1',
  definition_version: 'v1',
  start_node_id: 'answer',
  nodes: [
    { node_id: 'answer', node_type: 'AI', next_node_id: 'review_answer', on_true_node_id: null, on_false_node_id: null },
    { node_id: 'review_answer', node_type: 'APPROVAL', next_node_id: 'branch', on_true_node_id: null, on_false_node_id: null },
    { node_id: 'branch', node_type: 'CONDITION', next_node_id: null, on_true_node_id: 'done', on_false_node_id: 'fallback' },
    { node_id: 'done', node_type: 'TOOL', next_node_id: 'merge', on_true_node_id: null, on_false_node_id: null },
    { node_id: 'fallback', node_type: 'AI', next_node_id: 'merge', on_true_node_id: null, on_false_node_id: null },
    { node_id: 'merge', node_type: 'APPROVAL', next_node_id: null, on_true_node_id: null, on_false_node_id: null },
  ],
};

/** 构造与 `WorkflowNodeRunResponse` 一致的 attempt 记录（Step 4 E2E 用）。 */
function mkNode(nodeId, attempt, status, errorCode = null, output = null) {
  return {
    workflow_run_id: RUN,
    node_id: nodeId,
    attempt,
    node_type: 'TOOL',
    status,
    output,
    error_code: errorCode,
    elapsed_ms: 12,
    started_at: '2026-10-17T09:00:00Z',
    completed_at: null,
  };
}

test.describe('Graph View', () => {
  test.beforeEach(async ({ page }) => {
    // 在导航前注入 alert 探针（检测后端文本是否被当作脚本执行）
    await page.addInitScript(() => {
      window.__alerted = false;
      window.alert = () => {
        window.__alerted = true;
      };
    });
    await mockApi(page);
    await page.goto('/index.html');
  });

  test('有真实拓扑时节点与边正确显示（边来自定义，非时间顺序）', async ({ page }) => {
    await page.route('**/api/**/graph', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(standardGraph()),
      })
    );
    await page.fill('#project-id-input', PROJECT);
    await page.fill('#run-id-input', RUN);
    await page.click('#run-submit');
    await expect(page.locator('#graph-view')).toBeVisible();
    await expect(page.locator('#graph-meta')).toContainText('2 个节点');
    await expect(page.locator('#graph-meta')).toContainText('1 条边');

    // 两个节点卡片（真实 ID / 类型 / attempt 数）
    const cards = page.locator('#graph-view .graph-node');
    await expect(cards).toHaveCount(2);
    await expect(page.locator('#graph-view .graph-node[data-node-id="ok_node"]'))
      .toContainText('TOOL');
    // 默认 nodes mock：ok_node 有 1 次 attempt
    await expect(page.locator('#graph-view .graph-node[data-node-id="ok_node"]'))
      .toContainText('1 次 attempt');

    // 起点标记
    await expect(page.locator('#graph-view .graph-node-start')).toHaveCount(1);

    // 恰好 1 条 SVG 边（next），无悬空边
    const edges = page.locator('#graph-view path.graph-edge');
    await expect(edges).toHaveCount(1);
    await expect(edges.first()).toHaveAttribute('data-from', 'ok_node');
    await expect(edges.first()).toHaveAttribute('data-to', 'gate');
    await expect(edges.first()).toHaveAttribute('data-kind', 'next');

    // 真实几何：边路径是非空 SVG path
    const d = await edges.first().getAttribute('d');
    expect(d).toMatch(/^M [\d.-]+ [\d.-]+ L/);
  });

  test('分支拓扑：CONDITION 双分支与汇聚节点的多条入边', async ({ page }) => {
    await page.route('**/api/**/graph', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(BRANCH_GRAPH),
      })
    );
    await page.fill('#project-id-input', PROJECT);
    await page.fill('#run-id-input', RUN);
    await page.click('#run-submit');

    await expect(page.locator('#graph-view .graph-node')).toHaveCount(6);
    await expect(page.locator('#graph-view path.graph-edge')).toHaveCount(6);

    // 两条条件分支边带 true / false 标签
    const conditional = page.locator('#graph-view path.graph-edge[data-kind="true"]');
    await expect(conditional).toHaveCount(1);
    await expect(page.locator('#graph-view path.graph-edge[data-kind="false"]')).toHaveCount(1);
    await expect(page.locator('#graph-view text.graph-edge-label')).toHaveCount(2);

    // 汇聚节点 merge 有两条入边
    const intoMerge = page.locator('#graph-view path.graph-edge[data-to="merge"]');
    await expect(intoMerge).toHaveCount(2);
  });

  test('缺少拓扑时不生成虚假边（未注册 / 版本不匹配）', async ({ page }) => {
    for (const status of ['not_registered', 'version_mismatch']) {
      await page.route('**/api/**/graph', (route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            status,
            workflow_id: 'inventory_check',
            workflow_version: 'v1',
            definition_version: status === 'version_mismatch' ? 'v2' : null,
            start_node_id: null,
            nodes: [],
          }),
        })
      );
      await page.fill('#project-id-input', PROJECT);
      await page.fill('#run-id-input', RUN);
      await page.click('#run-submit');

      // 无节点、无边，但有明确空状态
      await expect(page.locator('#graph-view .graph-node')).toHaveCount(0);
      await expect(page.locator('#graph-view path.graph-edge')).toHaveCount(0);
      await expect(page.locator('#graph-empty')).toBeVisible();
    }

    // 版本不匹配额外说明两个版本
    await expect(page.locator('#graph-version-note')).toContainText('v1');
    await expect(page.locator('#graph-version-note')).toContainText('v2');
    await expect(page.locator('#graph-version-note')).toContainText('不绘制节点与连接线');
  });

  test('节点状态映射与未知状态（不显示为成功）', async ({ page }) => {
    // 节点 attempt：ok_node 成功、gate 失败、weird_node 无 attempt
    await page.route('**/api/**/nodes', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          mkNode('ok_node', 1, 'SUCCEEDED'),
          mkNode('gate', 1, 'FAILED', 'WORKFLOW_NODE_EXECUTION_FAILURE'),
        ]),
      })
    );
    await page.route('**/api/**/graph', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ...standardGraph(),
          nodes: [
            ...standardGraph().nodes,
            {
              node_id: 'weird_node',
              node_type: 'CONDITION',
              next_node_id: null,
              on_true_node_id: null,
              on_false_node_id: null,
            },
          ],
        }),
      })
    );
    await page.fill('#project-id-input', PROJECT);
    await page.fill('#run-id-input', RUN);
    await page.click('#run-submit');

    await expect(page.locator('.graph-node[data-node-id="ok_node"]'))
      .toHaveAttribute('data-status', 'SUCCEEDED');
    await expect(page.locator('.graph-node[data-node-id="gate"]'))
      .toHaveAttribute('data-status', 'FAILED');
    // 无 attempt ⇒ 未执行
    await expect(page.locator('.graph-node[data-node-id="weird_node"]'))
      .toHaveAttribute('data-status', 'not_run');
    await expect(page.locator('.graph-node[data-node-id="weird_node"]'))
      .toContainText('未执行');

    // 失败节点视觉突出（左边框类）
    const failedInner = page.locator(
      '.graph-node[data-node-id="gate"] .graph-node-inner'
    );
    await expect(failedInner).toHaveClass(/is-failed/);
    const borderColor = await failedInner.evaluate(
      (el) => getComputedStyle(el).borderLeftColor
    );
    expect(borderColor).not.toBe('rgba(0, 0, 0, 0)');
  });

  test('未知状态原样显示，绝不映射为成功', async ({ page }) => {
    await page.route('**/api/**/nodes', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([mkNode('ok_node', 1, 'TOTALLY_UNKNOWN')]),
      })
    );
    await page.route('**/api/**/graph', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(standardGraph()),
      })
    );
    await page.fill('#project-id-input', PROJECT);
    await page.fill('#run-id-input', RUN);
    await page.click('#run-submit');

    const card = page.locator('.graph-node[data-node-id="ok_node"]');
    await expect(card).toHaveAttribute('data-status', 'TOTALLY_UNKNOWN');
    await expect(card).toContainText('TOTALLY_UNKNOWN');
    await expect(card).not.toContainText('成功');
    await expect(card).toContainText('未在域白名单内');
  });

  test('点击节点 ⇒ 定位 Node Attempts 并高亮其全部历史 Attempt', async ({ page }) => {
    await page.route('**/api/**/nodes', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          mkNode('ok_node', 1, 'FAILED', 'WORKFLOW_NODE_EXECUTION_FAILURE'),
          mkNode('ok_node', 2, 'SUCCEEDED'),
          mkNode('gate', 1, 'WAITING_APPROVAL'),
        ]),
      })
    );
    await page.route('**/api/**/graph', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(standardGraph()),
      })
    );
    await page.fill('#project-id-input', PROJECT);
    await page.fill('#run-id-input', RUN);
    await page.click('#run-submit');

    await page.locator('.graph-node[data-node-id="ok_node"]').click();

    // 该节点全部 2 次 attempt 行被高亮（历史不丢失）
    const highlighted = page.locator('tr.attempt-row.is-node-selected');
    await expect(highlighted).toHaveCount(2);
    await expect(page.locator('#attempt-ok_node-1')).toHaveClass(/is-node-selected/);
    await expect(page.locator('#attempt-ok_node-2')).toHaveClass(/is-node-selected/);

    // 已滚动到可视区域（scrollIntoView 为 smooth 动画 ⇒ 等待其结束）
    await page.waitForTimeout(900);
    const inView = await page.locator('#attempt-ok_node-1').evaluate((el) => {
      const rect = el.getBoundingClientRect();
      return rect.bottom > 0 && rect.top < window.innerHeight;
    });
    expect(inView).toBe(true);
  });

  test('点击节点 ⇒ Timeline 中相关事件高亮', async ({ page }) => {
    // timeline 含 ok_node 的两个节点事件（真实持久化事件）
    await page.route('**/api/**/timeline', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          timelineEvent({ kind: 'RUN_CREATED' }),
          timelineEvent({
            kind: 'NODE_ATTEMPT_STARTED',
            node_id: 'ok_node',
            attempt: 1,
          }),
          timelineEvent({
            kind: 'NODE_ATTEMPT_FINISHED',
            node_id: 'ok_node',
            attempt: 1,
          }),
        ]),
      })
    );
    await page.route('**/api/**/graph', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(standardGraph()),
      })
    );
    await page.fill('#project-id-input', PROJECT);
    await page.fill('#run-id-input', RUN);
    await page.click('#run-submit');

    await page.locator('.graph-node[data-node-id="ok_node"]').click();

    const related = page.locator('#run-timeline li.timeline-item.is-related');
    await expect(related).toHaveCount(2);   // NODE_ATTEMPT_STARTED + FINISHED
    const bg = await related.first().evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(bg).not.toBe('rgba(0, 0, 0, 0)');
  });

  test('图中节点文本不作为 HTML 执行', async ({ page }) => {
    await page.route('**/api/**/graph', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ...LINEAR_GRAPH,
          nodes: [
            {
              node_id: 'ok_node',
              node_type: 'TOOL',
              next_node_id: null,
              on_true_node_id: null,
              on_false_node_id: null,
            },
            {
              node_id: '<script>alert(1)</script>',
              node_type: 'APPROVAL',
              next_node_id: null,
              on_true_node_id: null,
              on_false_node_id: null,
            },
          ],
          start_node_id: 'ok_node',
        }),
      })
    );
    await page.fill('#project-id-input', PROJECT);
    await page.fill('#run-id-input', RUN);
    await page.click('#run-submit');

    await expect(page.locator('#graph-view .graph-node')).toHaveCount(2);
    // 含 <script> 的 node_id 以纯文本呈现（未被解析执行）
    await expect(
      page.locator('#graph-view .graph-node[data-node-id="<script>alert(1)</script>"]')
    ).toHaveCount(1);
    expect(await page.evaluate(() => window.__alerted)).toBe(false);
  });

  test('Graph View 不产生任何写请求', async ({ page }) => {
    const methods = [];
    page.on('request', (request) => {
      if (new URL(request.url()).pathname.startsWith('/api/')) {
        methods.push(request.method().toUpperCase());
      }
    });
    await page.route('**/api/**/graph', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(BRANCH_GRAPH),
      })
    );

    await page.fill('#project-id-input', PROJECT);
    await page.fill('#run-id-input', RUN);
    await page.click('#run-submit');
    await expect(page.locator('#graph-view .graph-node')).toHaveCount(6);

    // 点击多个节点 + 切换筛选（均不得产生新请求）
    await page.locator('.graph-node[data-node-id="branch"]').click();
    await page.selectOption('#node-filter', 'failed');
    await page.locator('.graph-node[data-node-id="merge"]').click();
    await page.waitForTimeout(400);

    expect(methods.every((m) => m === 'GET')).toBe(true);
  });

  test('窄屏下 Graph View 容器内滚动，不导致页面级横向溢出', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.route('**/api/**/graph', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(BRANCH_GRAPH),
      })
    );
    await page.fill('#project-id-input', PROJECT);
    await page.fill('#run-id-input', RUN);
    await page.click('#run-submit');
    await expect(page.locator('#graph-view .graph-node')).toHaveCount(6);

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth
    );
    expect(overflow).toBe(0);

    // 图形容器自身可横向滚动
    const scrollable = await page
      .locator('#graph-scroller')
      .evaluate((el) => el.scrollWidth > el.clientWidth);
    expect(scrollable).toBe(true);
  });
});
