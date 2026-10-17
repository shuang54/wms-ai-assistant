/**
 * Graph View 纯逻辑测试（Step 4）—— `node --test`（零依赖）。
 *
 * 覆盖任务书 §五 1/2/3/4/5/6/7/8/9/10 的前端纯逻辑部分：
 *   真实拓扑 ⇒ 节点 + 边 · 缺拓扑 ⇒ 无虚假边 · 状态映射 · 未知状态不显示成功 ·
 *   节点联动索引 · 多 Attempt 不丢失 · 空工作流 · 文本不作 HTML ·
 *   无写操作（controller 层）· 几何确定性。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  GRAPH_EDGE_KIND,
  GRAPH_GEOMETRY,
  GRAPH_LEGEND,
  computeGraphGeometry,
  computeGraphLevels,
  graphStatus,
  graphUnavailableReason,
  nodeStatusFromAttempts,
  toGraphModel,
} from '../js/state.js';

const RUN = 'wf-run-1';
const T0 = '2026-10-17T09:00:00Z';

/** 真实形态的拓扑节点（WorkflowGraphNodeResponse）。 */
function gnode(nodeId, type, next = null, onTrue = null, onFalse = null) {
  return {
    node_id: nodeId,
    node_type: type,
    next_node_id: next,
    on_true_node_id: onTrue,
    on_false_node_id: onFalse,
  };
}

/** 真实形态的 attempt 记录。 */
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
    started_at: T0,
    completed_at: null,
  };
}

/** 可用拓扑的 graph 响应（线性 TOOL → APPROVAL）。 */
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

/** 分支拓扑（CONDITION 双分支 + 汇聚 ⇒ 多入边）。 */
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

function bundle(graph, nodes = [], run = { workflow_run_id: RUN }) {
  return { run, nodes, timeline: [], graph, approvalsByNode: {} };
}

// ============================================================
// 1 / 3：真实拓扑与状态映射
// ============================================================

test('有真实拓扑时节点与边正确生成（边来自定义，非时间顺序）', () => {
  const model = toGraphModel(bundle(LINEAR_GRAPH, [
    attempt('query_stock', 1, 'SUCCEEDED'),
    attempt('confirm_stock', 1, 'WAITING_APPROVAL'),
  ]));

  assert.equal(model.available, true);
  assert.equal(model.status, 'available');
  assert.equal(model.nodeCount, 2);
  assert.equal(model.edgeCount, 1);
  assert.equal(model.startNodeId, 'query_stock');

  assert.deepEqual(
    model.nodes.map((n) => [n.nodeId, n.nodeType, n.level]),
    [
      ['query_stock', 'TOOL', 0],
      ['confirm_stock', 'APPROVAL', 1],
    ]
  );
  assert.deepEqual(model.edges, [
    {
      from: 'query_stock',
      to: 'confirm_stock',
      kind: GRAPH_EDGE_KIND.NEXT,
      label: '',
      related: false,
    },
  ]);
  assert.equal(model.executedCount, 2);
  assert.equal(model.failedCount, 0);
});

test('分支拓扑：CONDITION 双分支 + 汇聚节点的多条入边', () => {
  const model = toGraphModel(bundle(BRANCH_GRAPH));

  assert.equal(model.nodeCount, 6);
  // answer→review_answer、review_answer→branch（2 条 next）
  // + branch 双分支（2 条条件边）
  // + done→merge、fallback→merge（2 条 next）= 6 条边
  assert.equal(model.edgeCount, 6);

  const branch = model.edges.filter((e) => e.from === 'branch');
  assert.deepEqual(
    branch.map((e) => [e.kind, e.to, e.label]),
    [
      ['true', 'done', 'true'],
      ['false', 'fallback', 'false'],
    ]
  );

  // 汇聚节点 merge 有两条入边（done → merge、fallback → merge）
  const intoMerge = model.edges.filter((e) => e.to === 'merge');
  assert.equal(intoMerge.length, 2);
  assert.deepEqual(intoMerge.map((e) => e.from).sort(), ['done', 'fallback']);
});

test('分层布局：层号由真实拓扑深度决定（确定性）', () => {
  const levels = computeGraphLevels(BRANCH_GRAPH.nodes, 'answer');

  assert.equal(levels.get('answer'), 0);
  assert.equal(levels.get('review_answer'), 1);
  assert.equal(levels.get('branch'), 2);
  assert.equal(levels.get('done'), 3);
  assert.equal(levels.get('fallback'), 3);
  assert.equal(levels.get('merge'), 4);
});

test('节点状态映射：来自真实 attempt（RUNNING / FAILED / 等待优先）', () => {
  assert.equal(
    nodeStatusFromAttempts([attempt('n', 1, 'SUCCEEDED')]).status,
    'SUCCEEDED'
  );
  // 任一 RUNNING ⇒ RUNNING（不被历史成功掩盖）
  assert.equal(
    nodeStatusFromAttempts([
      attempt('n', 1, 'SUCCEEDED'),
      attempt('n', 2, 'RUNNING'),
    ]).status,
    'RUNNING'
  );
  // 任一 FAILED ⇒ FAILED（失败必须显眼，不被后续成功掩盖）
  assert.equal(
    nodeStatusFromAttempts([
      attempt('n', 1, 'FAILED', null, 'WF_FAIL'),
      attempt('n', 2, 'SUCCEEDED'),
    ]).status,
    'FAILED'
  );
  assert.equal(
    nodeStatusFromAttempts([
      attempt('n', 1, 'SUCCEEDED'),
      attempt('n', 2, 'WAITING_APPROVAL'),
    ]).status,
    'WAITING_APPROVAL'
  );
});

test('节点状态：无 attempt ⇒ not_run（未执行，不假装成功）', () => {
  const summary = nodeStatusFromAttempts([]);
  assert.equal(summary.status, 'not_run');
  assert.equal(summary.attemptCount, 0);
  assert.equal(summary.known, true);

  const model = toGraphModel(bundle(LINEAR_GRAPH, []));
  assert.deepEqual(
    model.nodes.map((n) => n.status),
    ['not_run', 'not_run']
  );
  assert.equal(model.executedCount, 0);
});

test('未知状态：原样保留并标注，绝不映射为成功', () => {
  const summary = nodeStatusFromAttempts([
    attempt('n', 1, 'TOTALLY_UNKNOWN'),
  ]);
  assert.equal(summary.status, 'TOTALLY_UNKNOWN');
  assert.equal(summary.known, false);

  const model = toGraphModel(
    bundle(LINEAR_GRAPH, [attempt('query_stock', 1, 'WEIRD_STATUS')])
  );
  const node = model.nodes.find((n) => n.nodeId === 'query_stock');
  assert.equal(node.status, 'WEIRD_STATUS');
  assert.equal(node.known, false);
  assert.notEqual(node.status, 'SUCCEEDED');
});

test('错误码聚合：真实 error_code 去重收集', () => {
  const model = toGraphModel(
    bundle(LINEAR_GRAPH, [
      attempt('query_stock', 1, 'FAILED', null, 'WF_A'),
      attempt('query_stock', 2, 'FAILED', null, 'WF_A'),
      attempt('query_stock', 3, 'FAILED', null, 'WF_B'),
    ])
  );
  const node = model.nodes.find((n) => n.nodeId === 'query_stock');
  assert.equal(node.attemptCount, 3);       // 多 Attempt 不丢失
  assert.deepEqual(node.errorCodes, ['WF_A', 'WF_B']);
  assert.equal(model.failedCount, 1);
});

// ============================================================
// 2：缺拓扑 ⇒ 无虚假边
// ============================================================

test('拓扑不可用 ⇒ 无节点、无边、明确原因', () => {
  for (const [status, reason] of [
    ['not_registered', 'not_registered'],
    ['version_mismatch', 'version_mismatch'],
    ['unavailable', 'unavailable'],
  ]) {
    const model = toGraphModel(
      bundle({ ...LINEAR_GRAPH, status, nodes: [], start_node_id: null })
    );
    assert.equal(model.available, false, status);
    assert.equal(model.nodes.length, 0, status);
    assert.equal(model.edges.length, 0, status);
    assert.equal(model.reason, reason, status);
    assert.equal(model.edgeCount, 0, status);
    assert.equal(graphUnavailableReason(status), reason);
  }
});

test('available 但 nodes 为空 ⇒ no_nodes（仍不画边）', () => {
  const model = toGraphModel(
    bundle({ ...LINEAR_GRAPH, status: 'available', nodes: [], start_node_id: null })
  );
  assert.equal(model.available, false);
  assert.equal(model.reason, 'no_nodes');
  assert.equal(model.edges.length, 0);
});

test('无 graph 字段 ⇒ unavailable（不抛异常、不画边）', () => {
  const model = toGraphModel({ run: {}, nodes: [], timeline: [] });
  assert.equal(model.available, false);
  assert.equal(model.status, 'unavailable');
  assert.equal(graphStatus(null), 'unavailable');
  assert.equal(graphStatus({ status: '' }), 'unavailable');
  assert.equal(graphStatus({ status: 'available' }), 'available');
});

test('边目标必须存在于节点集合（不生成悬空边）', () => {
  const model = toGraphModel(
    bundle({
      ...LINEAR_GRAPH,
      nodes: [gnode('a', 'TOOL', 'ghost'), gnode('b', 'APPROVAL')],
    })
  );
  assert.equal(model.nodeCount, 2);
  assert.equal(model.edges.length, 0);   // ghost 不存在 ⇒ 不画
});

test('不按 attempt 时间顺序推断边（无拓扑时绝不生成连接）', () => {
  // 有 3 个先后执行的 attempt，但拓扑不可用 ⇒ 0 条边
  const model = toGraphModel(
    bundle(
      { ...LINEAR_GRAPH, status: 'version_mismatch', nodes: [] },
      [
        attempt('a', 1, 'SUCCEEDED'),
        attempt('b', 1, 'SUCCEEDED'),
        attempt('c', 1, 'SUCCEEDED'),
      ]
    )
  );
  assert.equal(model.available, false);
  assert.equal(model.edges.length, 0);
  assert.equal(model.nodes.length, 0);
});

// ============================================================
// 5 / 6 / 7：联动与多 Attempt
// ============================================================

test('选中节点 ⇒ 该节点与相邻边标记 related', () => {
  const model = toGraphModel(
    bundle(BRANCH_GRAPH, [attempt('branch', 1, 'FAILED', null, 'WF_X')]),
    { selectedNode: 'branch' }
  );

  const selected = model.nodes.find((n) => n.nodeId === 'branch');
  assert.equal(selected.selected, true);
  assert.equal(selected.status, 'FAILED');

  const related = model.edges.filter((e) => e.related);
  assert.deepEqual(
    related.map((e) => [e.from, e.to]).sort(),
    [
      ['branch', 'done'],
      ['branch', 'fallback'],
      ['review_answer', 'branch'],
    ]
  );
});

test('多 Attempt 历史在 graph 模型中完整保留（不折叠）', () => {
  const model = toGraphModel(
    bundle(LINEAR_GRAPH, [
      attempt('query_stock', 3, 'SUCCEEDED'),
      attempt('query_stock', 1, 'FAILED', null, 'WF_A'),
      attempt('query_stock', 2, 'RUNNING'),
    ])
  );
  const node = model.nodes.find((n) => n.nodeId === 'query_stock');
  assert.equal(node.attemptCount, 3);
  assert.deepEqual(
    node.attempts.map((a) => a.attempt),
    [3, 1, 2]
  );                                   // 后端顺序原样保留
  assert.equal(node.status, 'RUNNING'); // 聚合状态真实
});

test('图例覆盖全部已知状态 + 未知（文字 + 颜色双通道）', () => {
  const keys = GRAPH_LEGEND.map((item) => item.key);
  assert.deepEqual(keys, [
    'running',
    'succeeded',
    'failed',
    'waiting',
    'unknown',
  ]);
  for (const item of GRAPH_LEGEND) {
    assert.ok(item.label && item.label.length > 0);
  }
  // 未知项无固定 status 映射（避免把未知当成功）
  assert.equal(GRAPH_LEGEND.at(-1).status, null);
});

// ============================================================
// 8 / 9 / 10：空态、文本安全、几何
// ============================================================

test('几何确定性：同输入 ⇒ 同坐标（可在 DOM 桩中断言）', () => {
  const a = toGraphModel(bundle(BRANCH_GRAPH));
  const b = toGraphModel(bundle(BRANCH_GRAPH));

  assert.deepEqual(a.geometry, b.geometry);
  assert.equal(a.geometry.nodes.length, 6);
  assert.equal(a.geometry.edges.length, 6);

  // 起点的 y 在最上层；汇聚节点在最下层
  const byId = Object.fromEntries(
    a.geometry.nodes.map((n) => [n.nodeId, n])
  );
  assert.equal(byId.answer.y, GRAPH_GEOMETRY.padding);
  assert.ok(byId.merge.y > byId.branch.y);
  // 同层节点水平排列
  assert.equal(byId.done.y, byId.fallback.y);
  assert.notEqual(byId.done.x, byId.fallback.x);
});

test('几何：空 levels ⇒ null（不画空画布）', () => {
  assert.equal(computeGraphGeometry([]), null);
  assert.equal(computeGraphGeometry(null), null);
});

test('computeGraphLevels：防御性处理未知 start / 环输入', () => {
  // start 不存在 ⇒ 全部落到兜底层（不抛异常）
  const levels = computeGraphLevels(LINEAR_GRAPH.nodes, 'nope');
  assert.equal(levels.get('query_stock'), 1);
  assert.equal(levels.get('confirm_stock'), 1);

  // 空输入
  assert.equal(computeGraphLevels([], 'a').size, 0);
});

test('节点 ID 作为 DOM id 片段时安全（任意后端标识）', async () => {
  const { safeDomFragment } = await import('../js/state.js');
  assert.match(safeDomFragment('node with space/"q"'), /^[A-Za-z0-9_-]+$/);
  assert.match(safeDomFragment('节点-一'), /^[A-Za-z0-9_-]+$/);
  assert.equal(safeDomFragment(''), '_empty');
});

test('模型不包含任何 HTML 字符串（节点 ID / 类型均为纯文本字段）', () => {
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
  // 原样保留为字符串（渲染层用 textContent ⇒ 不执行）
  assert.equal(model.nodes[0].nodeId, '<script>alert(1)</script>');
  assert.equal(typeof model.nodes[0].nodeId, 'string');
});
