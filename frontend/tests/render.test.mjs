/**
 * 渲染层测试 —— **手写 DOM 桩**（`node --test`，零依赖）。
 *
 * ⚠️ 说明（如实标注）：**这不是真实浏览器测试**。本文件用共享的最小 DOM 桩
 * （`./dom-stub.mjs`）驱动 `render.js`，用于断言**生成的 DOM 结构**
 * （id / data-* / 类名 / 按钮接线 / 文本内容）。真实浏览器渲染、CSS 布局、
 * 剪贴板权限等**未**在此验证。
 *
 * 该桩**故意不实现** `innerHTML` ⇒ 如果 `render.js` 试图用 HTML 注入，
 * 测试会直接抛错（结构性地证明渲染层只用 `textContent` / DOM API）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  COPY_STATUS,
  NODE_FILTER,
  PHASE,
  applyNodeFilter,
  attemptDomId,
  groupAttemptsByNode,
  toViewModel,
} from '../js/state.js';
import {
  renderAll,
  renderNodeAttempts,
  renderStatus,
  renderTimeline,
} from '../js/render.js';
import { createDoc } from './dom-stub.mjs';

// ============================================================
// 夹具
// ============================================================

const T0 = '2026-10-17T09:00:00Z';
const RUN = 'wf-run-1';

function node({ nodeId = 'n1', attempt = 1, type = 'TOOL', status = 'SUCCEEDED', output = null, error = null } = {}) {
  return {
    workflow_run_id: RUN,
    node_id: nodeId,
    attempt,
    node_type: type,
    status,
    output,
    error_code: error,
    elapsed_ms: 12.5,
    started_at: T0,
    completed_at: null,
  };
}

function event({ kind = 'RUN_CREATED', nodeId = null, attempt = null } = {}) {
  return {
    kind,
    timestamp: T0,
    workflow_run_id: RUN,
    node_id: nodeId,
    attempt,
    approval_id: null,
  };
}

const DATA = {
  run: { workflow_run_id: RUN, project_id: 'proj-a', status: 'RUNNING' },
  nodes: [
    node({ nodeId: 'tool', attempt: 1, status: 'FAILED', error: 'WF_FAIL' }),
    node({ nodeId: 'tool', attempt: 2, status: 'SUCCEEDED', output: '{"qty":3}' }),
    node({ nodeId: 'tool', attempt: 3, status: 'RUNNING' }),
    node({ nodeId: 'gate', attempt: 1, type: 'APPROVAL', status: 'WAITING_APPROVAL' }),
  ],
  timeline: [
    event({ kind: 'RUN_CREATED' }),
    event({ kind: 'NODE_ATTEMPT_STARTED', nodeId: 'tool', attempt: 1 }),
    event({ kind: 'NODE_ATTEMPT_FINISHED', nodeId: 'tool', attempt: 1 }),
  ],
  approvalsByNode: {
    gate: { approval_id: 'ap-1', node_id: 'gate', status: 'PENDING', resolved_at: null },
  },
};

function makeHandlers(overrides = {}) {
  const calls = { copy: [], select: [], focus: [], clear: [], retry: 0 };
  const handlers = {
    onRetry: () => {
      calls.retry += 1;
    },
    onSelectAttempt: (nodeId, attempt) => calls.select.push([nodeId, attempt]),
    onFocusEvent: (identity) => calls.focus.push(identity),
    onCopyOutput: (nodeId, attempt) => calls.copy.push([nodeId, attempt]),
    onClearFilter: () => calls.clear.push(true),
    ...overrides,
  };
  return { handlers, calls };
}

function viewModel(filter = NODE_FILTER.ALL, selectedAttempt = null) {
  return toViewModel(DATA, { filter, selectedAttempt });
}

// ============================================================
// Node Attempts
// ============================================================

test('Attempts：全部历史 attempt 均渲染（不折叠为最新）', () => {
  const doc = createDoc();
  const { handlers } = makeHandlers();
  const section = renderNodeAttempts(doc, viewModel(), handlers);

  const rows = section.querySelectorAll('tr[data-attempt]');
  assert.equal(rows.length, 4);
  const toolRows = rows.filter((row) => row.getAttribute('data-node-id') === null
    ? false
    : true);
  // 通过 id 前缀判断归属（id = attempt-<node>-<attempt>）
  const toolAttempts = rows
    .map((row) => row.id)
    .filter((id) => id.startsWith('attempt-tool-'));
  assert.deepEqual(toolAttempts, [
    'attempt-tool-1',
    'attempt-tool-2',
    'attempt-tool-3',
  ]);
  assert.equal(toolRows.length >= 3, true);
});

test('Attempts：每行有稳定 id 与真实状态属性', () => {
  const doc = createDoc();
  const { handlers } = makeHandlers();
  const section = renderNodeAttempts(doc, viewModel(), handlers);
  const rows = section.querySelectorAll('tr[data-attempt]');

  const failed = rows.find((row) => row.id === attemptDomId('tool', 1));
  assert.equal(failed.getAttribute('data-status'), 'FAILED');
  assert.match(failed.className, /is-failed/);
  assert.equal(failed.getAttribute('data-attempt'), '1');

  // 失败行显示真实错误码（原样）
  assert.match(failed.textContent, /WF_FAIL/);
});

test('Attempts：筛选命中 / 灰化标记正确（历史仍全部渲染）', () => {
  const doc = createDoc();
  const { handlers } = makeHandlers();
  const section = renderNodeAttempts(
    doc,
    viewModel(NODE_FILTER.FAILED),
    handlers
  );
  const rows = section.querySelectorAll('tr[data-attempt]');

  // 仅 tool 组入选，但 3 个 attempt 全在（不丢失历史）
  assert.equal(rows.length, 3);
  const byId = Object.fromEntries(rows.map((row) => [row.id, row]));
  assert.equal(byId[attemptDomId('tool', 1)].getAttribute('data-matches'), 'true');
  assert.equal(byId[attemptDomId('tool', 2)].getAttribute('data-matches'), 'false');
  assert.match(byId[attemptDomId('tool', 2)].className, /is-dimmed/);
  assert.doesNotMatch(byId[attemptDomId('tool', 2)].className, /is-dimmed is-failed/);
});

test('Attempts：无匹配 ⇒ 空状态 + 清除筛选按钮', () => {
  const doc = createDoc();
  const { handlers, calls } = makeHandlers();
  const onlyOk = {
    ...DATA,
    nodes: [node({ status: 'SUCCEEDED' })],
  };
  const vm = toViewModel(onlyOk, { filter: NODE_FILTER.FAILED });
  const section = renderNodeAttempts(doc, vm, handlers);

  assert.match(section.textContent, /没有匹配的 Attempt/);
  const clear = section.querySelector('#empty-clear-filter');
  assert.ok(clear, '缺少清除筛选按钮');
  clear.dispatch('click');
  assert.equal(calls.clear.length, 1);
});

test('Attempts：选中行带 is-selected（联动高亮）', () => {
  const doc = createDoc();
  const { handlers } = makeHandlers();
  const section = renderNodeAttempts(
    doc,
    viewModel(NODE_FILTER.ALL, { nodeId: 'tool', attempt: '2' }),
    handlers
  );
  const row = section
    .querySelectorAll('tr[data-attempt]')
    .find((item) => item.id === attemptDomId('tool', 2));

  assert.match(row.className, /is-selected/);
});

// ============================================================
// 输出
// ============================================================

test('输出：空输出显示占位，不显示复制按钮', () => {
  const doc = createDoc();
  const { handlers } = makeHandlers();
  const section = renderNodeAttempts(doc, viewModel(), handlers);

  const emptyBlocks = section.querySelectorAll('p[class]');
  const hasEmptyText = emptyBlocks.some((el) =>
    el.textContent.includes('（无持久化输出）')
  );
  assert.equal(hasEmptyText, true);
  // attempt#1 无输出 ⇒ 该行无复制按钮
  const row = section
    .querySelectorAll('tr[data-attempt]')
    .find((item) => item.id === attemptDomId('tool', 1));
  assert.equal(row.querySelectorAll('button[data-copy-node]').length, 0);
});

test('输出：完整原文写入 <pre> 的 textContent（HTML 不执行、不截断）', () => {
  const doc = createDoc();
  const { handlers } = makeHandlers();
  const payload = '<script>alert(1)</script>{"qty":3}';
  const data = { ...DATA, nodes: [node({ output: payload })] };
  const section = renderNodeAttempts(
    doc,
    toViewModel(data, {}),
    handlers
  );

  const pre = section.querySelectorAll('pre[class]')[0];
  assert.ok(pre, '缺少 <pre> 输出容器');
  // 逐字符原样（桩无 innerHTML ⇒ 渲染层不可能注入 HTML）
  assert.equal(pre.textContent, payload);
  assert.equal(pre.className, 'output-full');

  // 折叠容器存在
  const details = section.querySelectorAll('details[class]')[0];
  assert.ok(details);
  assert.equal(details.tagName, 'DETAILS');
});

test('输出：复制按钮回传真实 (nodeId, attempt)', () => {
  const doc = createDoc();
  const { handlers, calls } = makeHandlers();
  const section = renderNodeAttempts(doc, viewModel(), handlers);

  const buttons = section.querySelectorAll('button[data-copy-node]');
  assert.equal(buttons.length, 1);              // 只有 attempt#2 有输出
  assert.equal(buttons[0].getAttribute('data-copy-node'), 'tool');
  assert.equal(buttons[0].getAttribute('data-copy-attempt'), '2');

  buttons[0].dispatch('click');
  assert.deepEqual(calls.copy, [['tool', '2']]);
});

// ============================================================
// Timeline
// ============================================================

test('Timeline：保持后端顺序且带节点身份属性', () => {
  const doc = createDoc();
  const { handlers } = makeHandlers();
  const section = renderTimeline(doc, viewModel(), handlers);

  const items = section.querySelectorAll('li[data-kind]');
  assert.deepEqual(
    items.map((item) => item.getAttribute('data-kind')),
    ['RUN_CREATED', 'NODE_ATTEMPT_STARTED', 'NODE_ATTEMPT_FINISHED']
  );
  assert.deepEqual(items.map((item) => item.getAttribute('data-index')), ['0', '1', '2']);

  assert.equal(items[0].getAttribute('data-node-id'), null);   // Run 级事件
  assert.equal(items[1].getAttribute('data-node-id'), 'tool');
  assert.equal(items[1].getAttribute('data-attempt'), '1');
});

test('Timeline：点击"定位 Attempt"回传真实身份', () => {
  const doc = createDoc();
  const { handlers, calls } = makeHandlers();
  const section = renderTimeline(doc, viewModel(), handlers);

  const items = section.querySelectorAll('li[data-kind]');
  const buttons = items[1].querySelectorAll('button');
  assert.ok(buttons.length > 0);
  buttons[0].dispatch('click');

  assert.deepEqual(calls.focus, [{ nodeId: 'tool', attempt: '1' }]);
});

test('Timeline：选中 attempt 的相关事件被高亮', () => {
  const doc = createDoc();
  const { handlers } = makeHandlers();
  const section = renderTimeline(
    doc,
    viewModel(NODE_FILTER.ALL, { nodeId: 'tool', attempt: '1' }),
    handlers
  );
  const items = section.querySelectorAll('li[data-kind]');

  assert.match(items[1].className, /is-related/);
  assert.match(items[2].className, /is-related/);
  assert.doesNotMatch(items[0].className, /is-related/);
});

test('Timeline：节点事件缺少可定位身份时如实标注（不补造关联）', () => {
  const doc = createDoc();
  const { handlers } = makeHandlers();
  const data = {
    ...DATA,
    timeline: [event({ kind: 'NODE_ATTEMPT_STARTED', nodeId: null, attempt: null })],
  };
  const section = renderTimeline(doc, toViewModel(data, {}), handlers);

  assert.match(section.textContent, /无可定位的节点身份/);
  // 无可定位身份 ⇒ 不渲染"定位 Attempt"按钮
  assert.equal(section.querySelectorAll('button').length, 0);
});

// ============================================================
// 状态区
// ============================================================

test('状态区：Loading 提示含项目与 Run', () => {
  const doc = createDoc();
  const { handlers } = makeHandlers();
  const state = {
    phase: PHASE.LOADING,
    projectId: 'proj-a',
    runId: RUN,
    data: null,
    error: null,
  };

  const box = renderStatus(doc, state, {}, handlers);
  assert.match(box.textContent, /正在读取项目 proj-a/);
  assert.match(box.textContent, new RegExp(RUN));
});

test('状态区：各错误类别显示固定文案与错误类别（无内部细节）', () => {
  const doc = createDoc();
  const { handlers } = makeHandlers();
  const cases = [
    ['unauthorized', 401, 'login-required', /需要登录/],
    ['forbidden', 403, '', /不是该项目的成员/],
    ['notFound', 404, '', /未找到该资源/],
    ['projection', 502, '', /无法安全投影/],
    ['unavailable', 503, '', /不可用/],
  ];

  for (const [category, status, hint, pattern] of cases) {
    const state = {
      phase: PHASE.ERROR,
      projectId: 'proj-a',
      runId: RUN,
      data: null,
      error: { category, status, hint, message: messageFor(category, hint) },
    };
    const box = renderStatus(doc, state, {}, handlers);

    assert.match(box.textContent, pattern, category);
    assert.match(box.textContent, new RegExp(`HTTP ${status}`));
    assert.equal(box.querySelector('#retry-button') !== null, true);
    for (const forbidden of ['Traceback', 'psycopg', 'SELECT', 'X-API-Key']) {
      assert.equal(box.textContent.includes(forbidden), false, category);
    }
  }
});

function messageFor(category, hint) {
  if (category === 'unauthorized') {
    return hint === 'session-expired'
      ? '会话已过期或被吊销：请重新登录后再查询。'
      : '需要登录：当前没有有效会话，请先登录后再查询。';
  }
  return {
    forbidden: '无权访问：当前账号不是该项目的成员，无法读取该项目的 Workflow 数据。',
    notFound: '未找到该资源：Run 不存在，或不属于所选项目。',
    projection: '持久化数据无法安全投影（后端 fail closed）。数据不会被修复或截断，请检查运行记录。',
    unavailable: '读取服务不可用（会话服务或授权存储异常）。请稍后重试。',
  }[category];
}

test('状态区：复制结果提示（成功 / 失败 / 空）', () => {
  const doc = createDoc();
  const { handlers } = makeHandlers();
  const base = {
    phase: PHASE.LOADED,
    projectId: 'proj-a',
    runId: RUN,
    data: DATA,
    error: null,
  };

  for (const [status, pattern] of [
    [COPY_STATUS.COPIED, /已复制/],
    [COPY_STATUS.FAILED, /复制失败/],
    [COPY_STATUS.EMPTY, /没有持久化输出/],
  ]) {
    const box = renderStatus(doc, base, { filter: NODE_FILTER.ALL, copyStatus: status }, handlers);
    const line = box.querySelector('#copy-status');
    assert.ok(line, `缺少 copy-status: ${status}`);
    assert.match(line.textContent, pattern);
  }

  // IDLE ⇒ 不显示
  const idle = renderStatus(doc, base, { filter: NODE_FILTER.ALL, copyStatus: COPY_STATUS.IDLE }, handlers);
  assert.equal(idle.querySelector('#copy-status'), null);
});

// ============================================================
// 整体渲染
// ============================================================

test('renderAll：五个区块齐全 + 筛选计数更新', () => {
  const doc = createDoc();
  const { handlers } = makeHandlers();

  renderAll(doc, doc.body, { phase: PHASE.LOADED, projectId: 'proj-a', runId: RUN, data: DATA, error: null }, viewModel(), { filter: NODE_FILTER.ALL, copyStatus: COPY_STATUS.IDLE }, handlers);

  for (const id of [
    'status-region',
    'run-overview',
    'node-attempts',
    'run-timeline',
    'approval-detail',
  ]) {
    assert.ok(doc.panels.querySelector(`#${id}`), `缺少区块 #${id}`);
  }
  assert.match(doc.filterMeta.textContent, /4 次 attempt/);
});

test('renderAll：筛选时计数区如实给出命中 / 总数', () => {
  const doc = createDoc();
  const { handlers } = makeHandlers();
  const vm = viewModel(NODE_FILTER.FAILED);

  renderAll(doc, doc.body, { phase: PHASE.LOADED, projectId: 'proj-a', runId: RUN, data: DATA, error: null }, vm, { filter: NODE_FILTER.FAILED, copyStatus: COPY_STATUS.IDLE }, handlers);

  assert.match(doc.filterMeta.textContent, /筛选结果：1 次命中 \/ 共 4 次/);
});

test('renderAll：未加载时不渲染任何伪造数据', () => {
  const doc = createDoc();
  const { handlers } = makeHandlers();

  renderAll(doc, doc.body, { phase: PHASE.IDLE, projectId: '', runId: '', data: null, error: null }, toViewModel(null, {}), { filter: NODE_FILTER.ALL, copyStatus: COPY_STATUS.IDLE }, handlers);

  assert.equal(doc.panels.querySelectorAll('tr[data-attempt]').length, 0);
  assert.equal(doc.panels.querySelectorAll('li[data-kind]').length, 0);
  assert.equal(doc.filterMeta.textContent, '尚未加载数据');
  assert.match(doc.panels.textContent, /尚未加载/);
});
