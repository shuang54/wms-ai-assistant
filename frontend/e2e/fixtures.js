/**
 * E2E 共享 fixtures（Phase 7 Step 3B）。
 *
 * 所有 API 响应均经 `page.route()` 注入：**只**拦截同源 `/api/**`，
 * 使用与后端 DTO **完全一致**的真实字段结构；不访问任何数据库、
 * 不发起外部请求、不写入任何持久化层。
 */
import { test as base, expect } from '@playwright/test';

export const PROJECT = 'e2e-proj';
export const RUN = 'e2e-run-1';
export const T0 = '2026-10-17T09:00:00Z';
export const T1 = '2026-10-17T09:00:05Z';
export const T2 = '2026-10-17T09:00:09Z';

/** 与 `backend/app/dto/workflow_read_api.py::WorkflowRunDetailResponse` 一致。 */
export function runDetail(overrides = {}) {
  return {
    workflow_run_id: RUN,
    workflow_id: 'wf-def-1',
    workflow_version: 'v1',
    project_id: PROJECT,
    status: 'RUNNING',
    current_node_id: 'tool',
    next_action: 'NODE_EXECUTION',
    state_version: 4,
    attempt_count: 4,
    cancel_requested: false,
    cancel_requested_at: null,
    input: '{"secret_input":"DO-NOT-EXPOSE"}',
    request_id: 'req-secret-trace',
    created_at: T0,
    started_at: T0,
    completed_at: null,
    updated_at: T2,
    ...overrides,
  };
}

/** 与 `WorkflowNodeRunResponse` 一致（10 个字段）。 */
export function nodeRun(overrides = {}) {
  return {
    workflow_run_id: RUN,
    node_id: 'tool',
    attempt: 1,
    node_type: 'TOOL',
    status: 'SUCCEEDED',
    output: null,
    error_code: null,
    elapsed_ms: null,
    started_at: null,
    completed_at: null,
    ...overrides,
  };
}

/** 与 `WorkflowTimelineEventResponse` 一致（6 个字段）。 */
export function timelineEvent(overrides = {}) {
  return {
    kind: 'RUN_CREATED',
    timestamp: T0,
    workflow_run_id: RUN,
    node_id: null,
    attempt: null,
    approval_id: null,
    ...overrides,
  };
}

/**
 * 与 `WorkflowGraphResponse` / `WorkflowGraphNodeResponse` 一致（Step 4）。
 *
 * 默认返回**真实线性拓扑**：``ok_node``(TOOL) → ``gate``(APPROVAL)，
 * 与 ``standardNodes()`` 的 node_id 对齐（便于状态派生）。
 */
export function standardGraph(overrides = {}) {
  return {
    status: 'available',
    workflow_id: 'inventory_check',
    workflow_version: 'v1',
    definition_version: 'v1',
    start_node_id: 'ok_node',
    nodes: [
      {
        node_id: 'ok_node',
        node_type: 'TOOL',
        next_node_id: 'gate',
        on_true_node_id: null,
        on_false_node_id: null,
      },
      {
        node_id: 'gate',
        node_type: 'APPROVAL',
        next_node_id: null,
        on_true_node_id: null,
        on_false_node_id: null,
      },
    ],
    ...overrides,
  };
}

/** 与 `WorkflowApprovalResponse` 一致（5 个字段；无 created_at）。 */
export function approval(overrides = {}) {
  return {
    approval_id: `${RUN}-ap`,
    workflow_run_id: RUN,
    node_id: 'gate',
    status: 'PENDING',
    resolved_at: null,
    ...overrides,
  };
}

/** 标准数据集：成功 / 失败 / 等待审批 / 未知状态 + 多 attempt + 审批。 */
export function standardNodes() {
  return [
    nodeRun({ node_id: 'ok_node', attempt: 1, status: 'SUCCEEDED' }),
    nodeRun({ node_id: 'tool', attempt: 1, status: 'FAILED', error_code: 'WORKFLOW_NODE_EXECUTION_FAILURE', completed_at: T1, elapsed_ms: 4200 }),
    nodeRun({ node_id: 'tool', attempt: 2, status: 'SUCCEEDED', output: '{"qty":3}', started_at: T1, completed_at: T2, elapsed_ms: 1800 }),
    nodeRun({ node_id: 'tool', attempt: 3, status: 'RUNNING' }),
    nodeRun({ node_id: 'gate', node_type: 'APPROVAL', attempt: 1, status: 'WAITING_APPROVAL' }),
    nodeRun({ node_id: 'odd', node_type: 'CONDITION', attempt: 1, status: 'TOTALLY_UNKNOWN' }),
  ];
}

export function standardTimeline() {
  return [
    timelineEvent({ kind: 'RUN_CREATED' }),
    timelineEvent({ kind: 'NODE_ATTEMPT_STARTED', timestamp: T0, node_id: 'tool', attempt: 1 }),
    timelineEvent({ kind: 'NODE_ATTEMPT_FINISHED', timestamp: T1, node_id: 'tool', attempt: 1 }),
    timelineEvent({ kind: 'NODE_ATTEMPT_STARTED', timestamp: T1, node_id: 'tool', attempt: 2 }),
    timelineEvent({ kind: 'NODE_ATTEMPT_FINISHED', timestamp: T2, node_id: 'tool', attempt: 2 }),
    // 注意：attempt#3（RUNNING）**故意不提供事件** —— 用于验证"不伪造事件"
    timelineEvent({ kind: 'APPROVAL_RESOLVED', timestamp: T2, node_id: 'gate', attempt: 1, approval_id: `${RUN}-ap` }),
  ];
}

/**
 * 路由拦截器：按路径注入响应；未覆盖路径一律 `abort()`（防止误触真实网络）。
 *
 * @param {import('@playwright/test').Page} page
 * @param {{run?: *, nodes?: *, timeline?: *, approvals?: Record<string, *>,
 *          delayMs?: number}} responses
 * @returns {Promise<{requests: Array<{url:string,method:string,headers:object}>}>}
 */
export async function mockApi(page, responses = {}) {
  const requests = [];
  const project = PROJECT;
  const run = RUN;
  const delay = typeof responses.delayMs === 'number' ? responses.delayMs : 0;

  const body = {
    run: responses.run !== undefined ? responses.run : runDetail(),
    nodes: responses.nodes !== undefined ? responses.nodes : standardNodes(),
    timeline:
      responses.timeline !== undefined
        ? responses.timeline
        : standardTimeline(),
    graph: responses.graph !== undefined ? responses.graph : standardGraph(),
  };
  const approvals = responses.approvals || { gate: approval() };

  await page.route('**/api/**', async (route, request) => {
    const url = new URL(request.url());
    const method = request.method().toUpperCase();

    requests.push({
      url: url.pathname + url.search,
      method,
      headers: request.headers(),
    });

    // 只允许 GET；写方法一律拒绝（页面本不应发起）
    if (method !== 'GET') {
      await route.abort();
      return;
    }

    const json = (payload, status = 200) =>
      route.fulfill({
        status,
        contentType: 'application/json',
        headers: { 'cache-control': 'no-store' },
        body: JSON.stringify(payload),
      });

    const wait = () =>
      delay ? new Promise((resolve) => setTimeout(resolve, delay)) : null;

    if (url.pathname === `/api/ui/projects/${project}/workflow-runs/${run}`) {
      await wait();
      await json(body.run);
      return;
    }
    if (
      url.pathname === `/api/ui/projects/${project}/workflow-runs/${run}/nodes`
    ) {
      await wait();
      await json(body.nodes);
      return;
    }
    if (
      url.pathname ===
      `/api/ui/projects/${project}/workflow-runs/${run}/timeline`
    ) {
      await wait();
      await json(body.timeline);
      return;
    }
    // Step 4：Graph 拓扑（只读；默认返回标准线性拓扑）
    if (
      url.pathname ===
      `/api/ui/projects/${project}/workflow-runs/${run}/graph`
    ) {
      await wait();
      await json(body.graph);
      return;
    }
    const approvalMatch = url.pathname.match(
      new RegExp(
        `^/api/ui/projects/${project}/workflow-runs/${run}/approvals/([^/]+)$`
      )
    );
    if (approvalMatch) {
      const nodeId = decodeURIComponent(approvalMatch[1]);
      await wait();
      if (Object.prototype.hasOwnProperty.call(approvals, nodeId)) {
        const value = approvals[nodeId];
        if (value === null) {
          await json({ detail: 'Workflow 审批不存在' }, 404);
        } else {
          await json(value);
        }
        return;
      }
      await json({ detail: 'Workflow 审批不存在' }, 404);
      return;
    }

    // 未覆盖的 API 路径：显式失败（不静默落到真实后端）
    await json({ detail: 'unmocked api path' }, 500);
  });

  return { requests };
}

/** 打开页面并完成一次标准查询。 */
export async function openAndQuery(page, responses = {}) {
  // 返回 mockApi 的结果（requests 记录数组）——**不**在 beforeEach 中调用时
  // 也必须可用（重复注册 page.route 会让先注册者先处理请求，
  // 因此 beforeEach 内不要再次调用 openAndQuery）
  const tracker = await mockApi(page, responses);
  await page.goto('/index.html');
  await page.fill('#project-id-input', PROJECT);
  await page.fill('#run-id-input', RUN);
  await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().includes(`/workflow-runs/${RUN}/timeline`) &&
        response.status() === 200
    ),
    page.click('#run-submit'),
  ]);
  // 注意：多个节点分组 ⇒ 多个 <table>；用 .first() 避免 strict mode 冲突
  await expect(page.locator('#node-attempts table').first()).toBeVisible();
  return tracker;
}

/** 绑定 fixture：每个测试自动安装 clipboard mock（可逐字符断言）。 */
export const test = base.extend({
  page: async ({ page }, use) => {
    // 剪贴板 mock：记录 writeText 调用；不依赖真实浏览器权限
    await page.addInitScript(() => {
      window.__copied = [];
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: {
          writeText: async (text) => {
            window.__copied.push(String(text));
          },
          readText: async () => window.__copied.join(''),
        },
      });
    });
    await use(page);
  },
});

export { expect };
