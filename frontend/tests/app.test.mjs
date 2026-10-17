/**
 * 页面接线测试（`bindApp`）—— **手写 DOM 桩**（`node --test`，零依赖）。
 *
 * ⚠️ 说明（如实标注）：**不是真实浏览器测试**。用 `tests/dom-stub.mjs` 的最小
 * DOM 桩驱动 `app.js`，验证"用户操作 → 控制器 → 重绘"的**接线**是否成立：
 *   * 查询（含防重复提交）；
 *   * 节点状态筛选**真实生效**（DOM 层级：灰化 / 隐藏 / 计数）；
 *   * 清除筛选；
 *   * Attempt ↔ Timeline 双向定位（选中高亮 + 滚动目标）；
 *   * 输出复制走注入的剪贴板；
 *   * 失败后保留输入。
 *
 * 未验证：真实浏览器渲染、CSS、剪贴板权限、真实网络。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { bindApp } from '../js/app.js';
import { buildPage, fillQuery } from './dom-stub.mjs';
import { attemptDomId } from '../js/state.js';

const PROJECT = 'proj-a';
const RUN = 'wf-run-1';
const T0 = '2026-10-17T09:00:00Z';

function node({ nodeId = 'n1', attempt = 1, type = 'TOOL', status = 'SUCCEEDED', output = null } = {}) {
  return {
    workflow_run_id: RUN,
    node_id: nodeId,
    attempt,
    node_type: type,
    status,
    output,
    error_code: status === 'FAILED' ? 'WF_FAIL' : null,
    elapsed_ms: 5,
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
  run: { workflow_run_id: RUN, project_id: PROJECT, status: 'RUNNING' },
  nodes: [
    node({ nodeId: 'tool', attempt: 1, status: 'FAILED' }),
    node({ nodeId: 'tool', attempt: 2, status: 'SUCCEEDED', output: '{"qty":3}' }),
    node({ nodeId: 'gate', attempt: 1, type: 'APPROVAL', status: 'WAITING_APPROVAL' }),
  ],
  timeline: [
    event({ kind: 'RUN_CREATED' }),
    event({ kind: 'NODE_ATTEMPT_STARTED', nodeId: 'tool', attempt: 1 }),
  ],
  approvalsByNode: {
    gate: { approval_id: 'ap-1', node_id: 'gate', status: 'PENDING', resolved_at: null },
  },
};

/** 构造已绑定的页面（注入 readRun / clipboard）。 */
async function setup({ data = DATA, clipboard } = {}) {
  const { doc, els } = buildPage();
  const calls = [];
  const controller = bindApp(doc, {
    readRun: async (projectId, runId) => {
      calls.push([projectId, runId]);
      return data;
    },
    clipboard: clipboard || { writeText: async () => {} },
  });
  fillQuery(els, PROJECT, RUN);
  els.form.dispatch('submit', { preventDefault() {} });
  await new Promise((resolve) => setImmediate(resolve));
  return { doc, els, controller, calls };
}

function rows(els) {
  return els.panels.querySelectorAll('tr[data-attempt]');
}

test('接线：提交后渲染五个区块并显示当前项目 / Run', async () => {
  const { els, calls } = await setup();

  assert.deepEqual(calls, [[PROJECT, RUN]]);
  assert.match(els.currentProject.textContent, /proj-a/);
  assert.match(els.currentRun.textContent, new RegExp(RUN));

  for (const id of [
    'status-region',
    'run-overview',
    'node-attempts',
    'run-timeline',
    'approval-detail',
  ]) {
    assert.ok(els.panels.querySelector(`#${id}`), `缺少区块 #${id}`);
  }
  assert.equal(rows(els).length, 3);
});

test('接线：筛选真实生效（DOM 层灰化 + 计数 + 仅命中组保留）', async () => {
  const { els } = await setup();

  els.filterSelect.value = 'failed';
  els.filterSelect.dispatch('change');
  await new Promise((resolve) => setImmediate(resolve));

  const visible = rows(els);
  assert.equal(visible.length, 2);                       // 仅 tool 组（2 个 attempt）
  assert.deepEqual(
    visible.map((row) => row.id),
    [attemptDomId('tool', 1), attemptDomId('tool', 2)]
  );
  // 命中 / 未命中标记
  const byId = Object.fromEntries(visible.map((row) => [row.id, row]));
  assert.equal(byId[attemptDomId('tool', 1)].getAttribute('data-matches'), 'true');
  assert.equal(byId[attemptDomId('tool', 2)].getAttribute('data-matches'), 'false');
  assert.match(byId[attemptDomId('tool', 2)].className, /is-dimmed/);

  // 计数如实
  assert.match(els.filterMeta.textContent, /筛选结果：1 次命中 \/ 共 3 次/);
  // 清理按钮可用
  assert.equal(els.filterClear.disabled, false);
});

test('接线：清除筛选恢复全部', async () => {
  const { els } = await setup();

  els.filterSelect.value = 'failed';
  els.filterSelect.dispatch('change');
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(rows(els).length, 2);

  els.filterClear.dispatch('click');
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(rows(els).length, 3);
  assert.equal(els.filterSelect.value, 'all');
  assert.equal(els.filterClear.disabled, true);
  assert.match(els.filterMeta.textContent, /全部状态/);
});

test('接线：筛选为纯前端（不触发新请求）', async () => {
  const { els, calls } = await setup();

  els.filterSelect.value = 'failed';
  els.filterSelect.dispatch('change');
  els.filterSelect.value = 'waiting_approval';
  els.filterSelect.dispatch('change');
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(calls, [[PROJECT, RUN]]);   // 仍只有 1 次读取
});

test('接线：Attempt → Timeline 定位（选中高亮 + 滚动）', async () => {
  const { doc, els } = await setup();

  const row = rows(els).find((item) => item.id === attemptDomId('tool', 1));
  const locateButton = row.querySelectorAll('button')[0];
  locateButton.dispatch('click');
  await new Promise((resolve) => setImmediate(resolve));

  // 重绘后：该行带 is-selected
  const refreshed = rows(els).find((item) => item.id === attemptDomId('tool', 1));
  assert.match(refreshed.className, /is-selected/);

  // 相关时间线事件被高亮
  const timelineItems = els.panels.querySelectorAll('li[data-kind]');
  const related = timelineItems.filter((item) =>
    /is-related/.test(item.className)
  );
  assert.equal(related.length, 1);
  assert.equal(related[0].getAttribute('data-node-id'), 'tool');
});

test('接线：Timeline → Attempt 定位（滚动到目标行）', async () => {
  const { els } = await setup();

  const items = els.panels.querySelectorAll('li[data-kind]');
  const nodeItem = items.find(
    (item) => item.getAttribute('data-node-id') === 'tool'
  );
  assert.ok(nodeItem, '缺少可定位的节点事件');

  const button = nodeItem.querySelectorAll('button')[0];
  button.dispatch('click');
  await new Promise((resolve) => setImmediate(resolve));

  const row = rows(els).find((item) => item.id === attemptDomId('tool', 1));
  assert.match(row.className, /is-selected/);
  assert.equal(row.getAttribute('data-revealed'), 'true');   // 已滚动定位
});

test('接线：输出复制使用注入的剪贴板（内容原样）', async () => {
  const written = [];
  const { els } = await setup({
    clipboard: {
      writeText: async (value) => {
        written.push(value);
      },
    },
  });

  const copyButton = els.panels
    .querySelectorAll('button[data-copy-node]')
    .find((button) => button.getAttribute('data-copy-attempt') === '2');
  assert.ok(copyButton, '缺少复制按钮');

  copyButton.dispatch('click');
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(written, ['{"qty":3}']);
  const status = els.panels.querySelector('#copy-status');
  assert.ok(status, '缺少复制结果提示');
  assert.match(status.textContent, /已复制/);
});

test('接线：空输出不产生复制按钮（无可复制内容）', async () => {
  const { els } = await setup();

  const copyButtons = els.panels.querySelectorAll('button[data-copy-node]');
  assert.equal(copyButtons.length, 1);        // 仅 attempt#2 有输出
  assert.equal(copyButtons[0].getAttribute('data-copy-attempt'), '2');
});

test('接线：空输入禁止提交（不发起请求），但按钮保持可用', async () => {
  const { doc, els } = buildPage();
  const calls = [];
  bindApp(doc, {
    readRun: async (projectId, runId) => {
      calls.push([projectId, runId]);
      return DATA;
    },
  });

  // 初始：按钮可用（不预先禁用）——由控制器在提交时拒绝空输入
  assert.equal(els.submit.disabled, false);

  els.form.dispatch('submit', { preventDefault() {} });
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(calls, []);
  assert.equal(els.submit.disabled, false);
});

test('接线：查询失败后保留输入且显示错误状态', async () => {
  const { doc, els } = buildPage();
  bindApp(doc, {
    readRun: async () => {
      const { ReadError } = await import('../js/api-client.js');
      throw new ReadError('forbidden', 403);
    },
  });
  fillQuery(els, PROJECT, RUN);
  els.form.dispatch('submit', { preventDefault() {} });
  await new Promise((resolve) => setImmediate(resolve));

  // 输入保留
  assert.equal(els.projectInput.value, PROJECT);
  assert.equal(els.runInput.value, RUN);
  // 错误提示
  const status = els.panels.querySelector('#status-region');
  assert.match(status.textContent, /不是该项目的成员/);
  assert.ok(status.querySelector('#retry-button'));
  // 无伪造数据
  assert.equal(rows(els).length, 0);
});

test('接线：加载中禁用输入与筛选（防重复提交）', async () => {
  const { doc, els } = buildPage();
  let release = null;
  bindApp(doc, {
    readRun: () =>
      new Promise((resolve) => {
        release = resolve;
      }),
  });
  fillQuery(els, PROJECT, RUN);

  els.form.dispatch('submit', { preventDefault() {} });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(els.runInput.disabled, true);
  assert.equal(els.projectInput.disabled, true);
  assert.equal(els.filterSelect.disabled, true);

  release(DATA);
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(els.runInput.disabled, false);
  assert.equal(els.filterSelect.disabled, false);
});

test('接线：未加载时筛选条禁用且计数为"尚未加载数据"', () => {
  const { doc, els } = buildPage();
  bindApp(doc, { readRun: async () => DATA });

  assert.equal(els.filterSelect.disabled, true);
  assert.equal(els.filterClear.disabled, true);
  assert.equal(els.filterMeta.textContent, '尚未加载数据');
});
