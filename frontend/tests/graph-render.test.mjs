/**
 * Graph View 渲染层测试（Step 4）—— **手写 DOM 桩**（`node --test`，零依赖）。
 *
 * ⚠️ 不是真实浏览器测试：用共享最小 DOM 桩断言**生成的 DOM 结构**
 * （节点卡片 / SVG 边 / data-* / 点击接线 / textContent）。
 * 真实浏览器行为由 `e2e/workbench.spec.js` 覆盖。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { toGraphModel } from '../js/state.js';
import { renderGraph, renderAll } from '../js/render.js';
import { createDoc } from './dom-stub.mjs';

const RUN = 'wf-run-1';

function gnode(nodeId, type, next = null, onTrue = null, onFalse = null) {
  return {
    node_id: nodeId,
    node_type: type,
    next_node_id: next,
    on_true_node_id: onTrue,
    on_false_node_id: onFalse,
  };
}

function attempt(nodeId, no, status, output = null, error = null) {
  return {
    workflow_run_id: RUN,
    node_id: nodeId,
    attempt: no,
    node_type: 'TOOL',
    status,
    output,
    error_code: error,
    elapsed_ms: 5,
    started_at: '2026-10-17T09:00:00Z',
    completed_at: null,
  };
}

const LINEAR_GRAPH = {
  status: 'available',
  workflow_id: 'inventory_check',
  workflow_version: 'v1',
  definition_version: 'v1',
  start_node_id: 'query_stock',
  nodes: [
    gnode('query_stock', 'TOOL', 'confirm_stock'),
    gnode('confirm_stock', 'APPROVAL'),
  ],
};

const BRANCH_GRAPH = {
  ...LINEAR_GRAPH,
  workflow_id: 'answer_review',
  start_node_id: 'answer',
  nodes: [
    gnode('answer', 'AI', 'review_answer'),
    gnode('review_answer', 'APPROVAL', 'branch'),
    gnode('branch', 'CONDITION', null, 'done', 'fallback'),
    gnode('done', 'TOOL', 'merge'),
    gnode('fallback', 'AI', 'merge'),
    gnode('merge', 'APPROVAL'),
  ],
};

function bundle(graph, nodes = []) {
  return { run: { workflow_run_id: RUN }, nodes, timeline: [], graph, approvalsByNode: {} };
}

function handlers(overrides = {}) {
  const calls = { nodes: [] };
  return {
    calls,
    handlers: {
      onRetry: () => {},
      onSelectAttempt: () => {},
      onFocusEvent: () => {},
      onCopyOutput: () => {},
      onClearFilter: () => {},
      onSelectNode: (nodeId) => calls.nodes.push(nodeId),
      ...overrides,
    },
  };
}

// ============================================================
// 节点与边渲染
// ============================================================

test('可用拓扑：渲染全部节点卡片（真实 ID / 类型 / 状态 / attempt 数）', () => {
  const doc = createDoc();
  const { handlers: h } = handlers();
  const model = toGraphModel(bundle(LINEAR_GRAPH, [
    attempt('query_stock', 1, 'SUCCEEDED'),
    attempt('confirm_stock', 1, 'WAITING_APPROVAL'),
  ]));

  const section = renderGraph(doc, model, h);
  const cards = section.querySelectorAll('.graph-node');

  assert.equal(cards.length, 2);
  const first = cards.find((c) => c.getAttribute('data-node-id') === 'query_stock');
  assert.equal(first.getAttribute('data-node-type'), 'TOOL');
  assert.equal(first.getAttribute('data-status'), 'SUCCEEDED');
  assert.equal(first.getAttribute('data-attempt-count'), '1');
  assert.match(first.textContent, /query_stock/);
  assert.match(first.textContent, /TOOL/);
  assert.match(first.textContent, /1 次 attempt/);

  // 起点标记
  const start = cards.filter((c) => c.textContent.includes('起点'));
  assert.equal(start.length, 1);
  assert.equal(start[0].getAttribute('data-node-id'), 'query_stock');
});

test('可用拓扑：SVG 边数量与属性正确（含条件分支标签）', () => {
  const doc = createDoc();
  const { handlers: h } = handlers();
  const model = toGraphModel(bundle(BRANCH_GRAPH));

  const section = renderGraph(doc, model, h);
  const edges = section.querySelectorAll('path.graph-edge');

  assert.equal(edges.length, 6);
  const conditional = edges.filter((e) =>
    ['true', 'false'].includes(e.getAttribute('data-kind'))
  );
  assert.equal(conditional.length, 2);
  assert.deepEqual(
    conditional.map((e) => [e.getAttribute('data-from'), e.getAttribute('data-to'), e.getAttribute('data-kind')]),
    [
      ['branch', 'done', 'true'],
      ['branch', 'fallback', 'false'],
    ]
  );
  // 每条边都有 path 数据与箭头
  for (const edge of edges) {
    assert.match(edge.getAttribute('d'), /^M [\d.-]+ [\d.-]+ L/);
    assert.equal(edge.getAttribute('marker-end'), 'url(#graph-arrow)');
  }

  // 分支标签文本（定义中的固定字面量）
  const labels = section.querySelectorAll('text.graph-edge-label');
  assert.deepEqual(labels.map((l) => l.textContent), ['true', 'false']);
});

test('节点坐标与 SVG 画布尺寸一致（确定性几何）', () => {
  const doc = createDoc();
  const { handlers: h } = handlers();
  const model = toGraphModel(bundle(BRANCH_GRAPH));

  const section = renderGraph(doc, model, h);
  const svg = section.querySelectorAll('svg.graph-edges')[0];
  const canvas = section.querySelectorAll('.graph-canvas')[0];

  assert.ok(svg, '缺少 SVG 边层');
  assert.equal(svg.getAttribute('width'), String(model.geometry.width));
  assert.equal(svg.getAttribute('height'), String(model.geometry.height));
  assert.equal(canvas.style.width, `${model.geometry.width}px`);

  // 每个节点卡片坐标都在画布内
  for (const card of section.querySelectorAll('.graph-node')) {
    const x = Number(card.style.left.replace('px', ''));
    const y = Number(card.style.top.replace('px', ''));
    assert.ok(x >= 0 && y >= 0, card.getAttribute('data-node-id'));
    assert.ok(x < model.geometry.width, card.getAttribute('data-node-id'));
    assert.ok(y < model.geometry.height, card.getAttribute('data-node-id'));
  }
});

// ============================================================
// 2 / 8：缺拓扑与空状态
// ============================================================

test('拓扑不可用：渲染明确空状态且**无**节点卡片与边', () => {
  for (const [status, expectedText] of [
    ['not_registered', /未在服务器端注册/],
    ['version_mismatch', /版本与当前注册版本不一致/],
  ]) {
    const doc = createDoc();
    const { handlers: h } = handlers();
    const model = toGraphModel(
      bundle({ ...LINEAR_GRAPH, status, nodes: [], start_node_id: null })
    );

    const section = renderGraph(doc, model, h);

    assert.equal(section.querySelectorAll('.graph-node').length, 0, status);
    assert.equal(section.querySelectorAll('path.graph-edge').length, 0, status);
    const empty = section.querySelectorAll('#graph-empty')[0];
    assert.ok(empty, status);
    assert.match(empty.textContent, expectedText, status);
  }
});

test('版本不匹配：额外展示两个版本号（便于理解原因）', () => {
  const doc = createDoc();
  const { handlers: h } = handlers();
  const model = toGraphModel(
    bundle({
      ...LINEAR_GRAPH,
      status: 'version_mismatch',
      workflow_version: 'v1',
      definition_version: 'v2',
      nodes: [],
      start_node_id: null,
    })
  );

  const section = renderGraph(doc, model, h);
  const note = section.querySelectorAll('#graph-version-note')[0];

  assert.ok(note);
  assert.match(note.textContent, /v1/);
  assert.match(note.textContent, /v2/);
  assert.match(note.textContent, /不绘制节点与连接线/);
});

test('无节点 attempt：节点显示"未执行"（不假装成功）', () => {
  const doc = createDoc();
  const { handlers: h } = handlers();
  const model = toGraphModel(bundle(LINEAR_GRAPH, []));

  const section = renderGraph(doc, model, h);
  const card = section
    .querySelectorAll('.graph-node')
    .find((c) => c.getAttribute('data-node-id') === 'query_stock');

  assert.equal(card.getAttribute('data-status'), 'not_run');
  assert.match(card.textContent, /未执行/);
  assert.match(card.textContent, /无 attempt/);
  assert.doesNotMatch(card.textContent, /成功/);
});

test('未知状态：原样显示并标注（不映射为成功）', () => {
  const doc = createDoc();
  const { handlers: h } = handlers();
  const model = toGraphModel(
    bundle(LINEAR_GRAPH, [attempt('query_stock', 1, 'WEIRD_STATUS')])
  );

  const section = renderGraph(doc, model, h);
  const card = section
    .querySelectorAll('.graph-node')
    .find((c) => c.getAttribute('data-node-id') === 'query_stock');

  assert.equal(card.getAttribute('data-status'), 'WEIRD_STATUS');
  assert.match(card.textContent, /WEIRD_STATUS/);
  assert.doesNotMatch(card.textContent, /成功/);
});

test('图例渲染（5 项：4 个已知状态 + 未知/未执行）', () => {
  const doc = createDoc();
  const { handlers: h } = handlers();
  const model = toGraphModel(bundle(LINEAR_GRAPH, []));

  const section = renderGraph(doc, model, h);
  const legend = section.querySelectorAll('#graph-legend')[0];
  assert.ok(legend, '缺少图例容器');
  const items = legend.children.filter((child) => child.tagName === 'LI');

  assert.equal(items.length, 5);
  assert.deepEqual(
    items.map((li) => li.textContent),
    ['运行中', '已完成', '失败', '等待审批', '未知 / 未执行']
  );
});

// ============================================================
// 5 / 9：点击联动与文本安全
// ============================================================

test('点击节点卡片 ⇒ 回传真实 nodeId', () => {
  const doc = createDoc();
  const { handlers: h, calls } = handlers();
  const model = toGraphModel(bundle(LINEAR_GRAPH, [
    attempt('query_stock', 1, 'SUCCEEDED'),
  ]));

  const section = renderGraph(doc, model, h);
  const card = section
    .querySelectorAll('.graph-node')
    .find((c) => c.getAttribute('data-node-id') === 'query_stock');

  card.dispatch('click');
  assert.deepEqual(calls.nodes, ['query_stock']);
});

test('键盘可达：Enter / Space 也可选中节点', () => {
  const doc = createDoc();
  const { handlers: h, calls } = handlers();
  const model = toGraphModel(bundle(LINEAR_GRAPH, []));

  const section = renderGraph(doc, model, h);
  const card = section.querySelectorAll('.graph-node')[0];

  card.dispatch('keydown', { key: 'Enter' });
  card.dispatch('keydown', { key: ' ' });
  assert.equal(calls.nodes.length, 2);
  // 其它键不触发
  card.dispatch('keydown', { key: 'a' });
  assert.equal(calls.nodes.length, 2);
});

test('选中节点：卡片与相关边高亮（data-selected / data-related）', () => {
  const doc = createDoc();
  const { handlers: h } = handlers();
  const model = toGraphModel(bundle(BRANCH_GRAPH), {
    selectedNode: 'branch',
  });

  const section = renderGraph(doc, model, h);
  const card = section
    .querySelectorAll('.graph-node')
    .find((c) => c.getAttribute('data-node-id') === 'branch');

  assert.equal(card.getAttribute('data-selected'), 'true');
  const inner = card.children.find((c) => /graph-node-inner/.test(c.className));
  assert.ok(inner, '缺少节点卡片内部容器');
  assert.match(inner.className, /is-selected/);

  const related = section.querySelectorAll('path.graph-edge[data-related="true"]');
  assert.equal(related.length, 3);   // review→branch、branch→done、branch→fallback
});

test('后端文本不作为 HTML 执行（textContent 写入）', () => {
  const doc = createDoc();
  const { handlers: h } = handlers();
  const model = toGraphModel(
    bundle(
      {
        ...LINEAR_GRAPH,
        nodes: [gnode('<script>alert(1)</script>', 'TOOL')],
        start_node_id: '<script>alert(1)</script>',
      },
      []
    )
  );

  const section = renderGraph(doc, model, h);
  const card = section.querySelectorAll('.graph-node')[0];

  // 原样字符串（桩无 innerHTML ⇒ 渲染层不可能注入 HTML）
  assert.equal(card.getAttribute('data-node-id'), '<script>alert(1)</script>');
  // 卡片结构：card > .graph-node-inner > .graph-node-id
  const inner = card.children.find((c) => /graph-node-inner/.test(c.className));
  assert.ok(inner, '缺少节点卡片内部容器');
  const idLine = inner.children.find((c) => /graph-node-id/.test(c.className));
  assert.ok(idLine, '缺少节点 ID 行');
  assert.match(idLine.textContent, /<script>/);
});

test('renderAll：Graph View 区块位于 Overview 与 Node Attempts 之间', () => {
  const doc = createDoc();
  const { handlers: h } = handlers();
  const model = toGraphModel(bundle(LINEAR_GRAPH, []));

  renderAll(
    doc,
    doc.body,
    {
      phase: 'loaded',
      projectId: 'proj-a',
      runId: RUN,
      data: bundle(LINEAR_GRAPH, []),
      error: null,
    },
    model,
    { filter: 'all', copyStatus: 'idle' },
    h
  );

  const ids = doc.panels.children
    .filter((child) => child.id)
    .map((child) => child.id);
  assert.deepEqual(ids, [
    'status-region',
    'run-overview',
    'graph-view',
    'node-attempts',
    'run-timeline',
    'approval-detail',
  ]);
});
