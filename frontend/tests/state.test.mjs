/**
 * 状态与视图模型测试 —— `node --test`（Node 内置，**零依赖**）。
 *
 * 覆盖（Phase 7 Workflow UI Step 1 §八 2/3/4/5/6）：
 *   Overview 真实字段 · 全量 attempt 历史保留 · Timeline 保序与缺失时间标注 ·
 *   Approval 空状态（**不**显示为已通过）· **零虚构**（缺失 ⇒ 显式占位）·
 *   损坏值原样呈现（不修复 / 不截断原始数据）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  OUTPUT_EMPTY,
  PHASE,
  TIME_UNAVAILABLE,
  UNAVAILABLE,
  applyFailure,
  applySuccess,
  beginLoad,
  createInitialState,
  formatTimestamp,
  groupAttemptsByNode,
  isSubmitBlocked,
  summarizeOutput,
  toApprovalView,
  toRunOverview,
  toTimelineRows,
  toViewModel,
} from '../js/state.js';

const RUN = {
  workflow_run_id: 'wf-r1',
  workflow_id: 'flow-a',
  workflow_version: 'v1',
  project_id: 'proj-a',
  status: 'RUNNING',
  current_node_id: 'n1',
  next_action: 'NODE_EXECUTION',
  state_version: 3,
  attempt_count: 2,
  cancel_requested: false,
  cancel_requested_at: null,
  created_at: '2026-10-16T09:00:00Z',
  started_at: '2026-10-16T09:00:01Z',
  completed_at: null,
  updated_at: '2026-10-16T09:00:05Z',
  input: '{"secret":"DO-NOT-EXPOSE"}',   // 后端不返回；即便存在也不得进入视图
  request_id: 'req-secret',
};

test('初始状态为 idle，无数据无错误', () => {
  const state = createInitialState();
  assert.equal(state.phase, PHASE.IDLE);
  assert.equal(state.runId, '');
  assert.equal(state.data, null);
  assert.equal(state.error, null);
});

test('提交阻止：空输入阻止；加载中阻止', () => {
  assert.equal(isSubmitBlocked(createInitialState(), ''), true);
  assert.equal(isSubmitBlocked(createInitialState(), '   '), true);
  assert.equal(isSubmitBlocked(createInitialState(), 'r1'), false);

  const loading = beginLoad(createInitialState(), 'r1');
  assert.equal(loading.phase, PHASE.LOADING);
  assert.equal(isSubmitBlocked(loading, 'r1'), true);
});

test('beginLoad 清空旧数据与旧错误（避免旧数据被误认为最新）', () => {
  const loaded = applySuccess(beginLoad(createInitialState(), 'r1'), {
    run: RUN, nodes: [], timeline: [], approvalsByNode: {},
  });
  assert.equal(loaded.phase, PHASE.LOADED);

  const again = beginLoad(loaded, 'r2');
  assert.equal(again.data, null);
  assert.equal(again.error, null);
  assert.equal(again.runId, 'r2');
});

test('applyFailure 保留 runId（可重试）并清空数据', () => {
  const loaded = applySuccess(beginLoad(createInitialState(), 'r1'), {
    run: RUN, nodes: [], timeline: [], approvalsByNode: {},
  });
  const failed = applyFailure(loaded, {
    category: 'notFound', status: 404, message: '未找到',
  });

  assert.equal(failed.phase, PHASE.ERROR);
  assert.equal(failed.runId, 'r1');
  assert.equal(failed.data, null);
  assert.equal(failed.error.category, 'notFound');
});

test('Overview：展示真实字段，且不含 input / request_id', () => {
  const rows = toRunOverview(RUN);
  const byKey = Object.fromEntries(rows.map((row) => [row.key, row.value]));

  assert.equal(byKey.workflow_run_id, 'wf-r1');
  assert.equal(byKey.project_id, 'proj-a');
  assert.equal(byKey.workflow_id, 'flow-a');
  assert.equal(byKey.workflow_version, 'v1');
  assert.equal(byKey.next_action, 'NODE_EXECUTION');
  assert.equal(byKey.state_version, '3');
  assert.match(byKey.status, /运行中/);       // 中文标签 + 原始枚举
  assert.match(byKey.status, /RUNNING/);
  assert.equal(byKey.created_at, '2026-10-16T09:00:00Z');

  const keys = rows.map((row) => row.key);
  assert.equal(keys.includes('input'), false);
  assert.equal(keys.includes('request_id'), false);
  // 任何值都不得包含敏感 input 内容
  for (const row of rows) {
    assert.doesNotMatch(String(row.value), /DO-NOT-EXPOSE|secret/);
  }
});

test('Overview：缺失字段 ⇒ 显式"不可用"，不虚构', () => {
  const rows = toRunOverview({ workflow_run_id: 'r1' });
  const byKey = Object.fromEntries(rows.map((row) => [row.key, row.value]));

  assert.equal(byKey.project_id, UNAVAILABLE);
  assert.equal(byKey.next_action, UNAVAILABLE);
  assert.equal(byKey.state_version, UNAVAILABLE);
  assert.equal(byKey.created_at, TIME_UNAVAILABLE);  // 不补当前时间
  assert.equal(byKey.started_at, TIME_UNAVAILABLE);
  assert.equal(byKey.completed_at, TIME_UNAVAILABLE);
  assert.equal(byKey.cancel_requested, UNAVAILABLE); // 不假设为 false
});

test('Overview：未知枚举原样呈现（不映射、不吞掉）', () => {
  const rows = toRunOverview({ status: 'WEIRD_STATUS' });
  const status = rows.find((row) => row.key === 'status');
  assert.equal(status.value, 'WEIRD_STATUS（未在域白名单内）');
});

test('全量 attempt 历史：同一节点多次 attempt 全部保留且升序', () => {
  const groups = groupAttemptsByNode([
    { node_id: 'n1', attempt: 3, node_type: 'TOOL', status: 'SUCCEEDED' },
    { node_id: 'n1', attempt: 1, node_type: 'TOOL', status: 'FAILED' },
    { node_id: 'n1', attempt: 2, node_type: 'TOOL', status: 'FAILED' },
  ]);

  assert.equal(groups.length, 1);
  assert.equal(groups[0].nodeId, 'n1');
  assert.equal(groups[0].attemptCount, 3);          // 不折叠为最新一条
  assert.deepEqual(
    groups[0].attempts.map((a) => a.attempt),
    [1, 2, 3]
  );
  assert.equal(groups[0].hasMultipleAttempts, true);
});

test('多节点分组：顺序稳定，组内保持历史', () => {
  const groups = groupAttemptsByNode([
    { node_id: 'a', attempt: 1, node_type: 'AI', status: 'SUCCEEDED' },
    { node_id: 'b', attempt: 1, node_type: 'APPROVAL', status: 'WAITING_APPROVAL' },
    { node_id: 'a', attempt: 2, node_type: 'AI', status: 'SUCCEEDED' },
  ]);

  assert.deepEqual(groups.map((g) => g.nodeId), ['a', 'b']);
  assert.equal(groups[0].attemptCount, 2);
  assert.equal(groups[1].attemptCount, 1);
  assert.equal(groups[1].hasMultipleAttempts, false);
});

test('空节点列表 ⇒ 空分组（不是错误、不造假数据）', () => {
  assert.deepEqual(groupAttemptsByNode([]), []);
  assert.deepEqual(groupAttemptsByNode(null), []);
  assert.deepEqual(groupAttemptsByNode(undefined), []);
});

test('Timeline：严格保持后端顺序（前端不重排）', () => {
  const events = [
    { kind: 'RUN_CREATED', timestamp: '2026-10-16T09:00:00Z', workflow_run_id: 'r1' },
    { kind: 'NODE_ATTEMPT_STARTED', timestamp: '2026-10-16T09:00:01Z', workflow_run_id: 'r1', node_id: 'n1', attempt: 1 },
    { kind: 'APPROVAL_RESOLVED', timestamp: '2026-10-16T09:00:05Z', workflow_run_id: 'r1', node_id: 'gate', approval_id: 'ap-1' },
    { kind: 'RUN_FINISHED', timestamp: '2026-10-16T09:00:09Z', workflow_run_id: 'r1' },
  ];
  const rows = toTimelineRows(events);

  assert.deepEqual(
    rows.map((row) => row.kind),
    events.map((event) => event.kind)
  );
  assert.deepEqual(rows.map((row) => row.index), [0, 1, 2, 3]);
  assert.equal(rows[1].nodeId, 'n1');
  assert.equal(rows[1].attempt, '1');
  assert.equal(rows[2].approvalId, 'ap-1');
  assert.match(rows[0].kindLabel, /Run 创建/);
});

test('Timeline：相同时间的事件保持后端给定顺序（稳定）', () => {
  const same = '2026-10-16T09:00:00Z';
  const rows = toTimelineRows([
    { kind: 'RUN_CREATED', timestamp: same, workflow_run_id: 'r1' },
    { kind: 'RUN_STARTED', timestamp: same, workflow_run_id: 'r1' },
    { kind: 'NODE_ATTEMPT_STARTED', timestamp: same, workflow_run_id: 'r1' },
  ]);
  assert.deepEqual(
    rows.map((row) => row.kind),
    ['RUN_CREATED', 'RUN_STARTED', 'NODE_ATTEMPT_STARTED']
  );
});

test('Timeline：时间缺失 ⇒ 显式"时间不可用"并标记', () => {
  const rows = toTimelineRows([
    { kind: 'RUN_CREATED', timestamp: null, workflow_run_id: 'r1' },
    { kind: 'RUN_STARTED', workflow_run_id: 'r1' },
    { kind: 'RUN_FINISHED', timestamp: 'not-a-date', workflow_run_id: 'r1' },
  ]);

  for (const row of rows) {
    assert.equal(row.timestamp, TIME_UNAVAILABLE);
    assert.equal(row.timestampMissing, true);
  }
});

test('formatTimestamp：null / 空白 / 非法 ⇒ 占位（不补造时间）', () => {
  assert.equal(formatTimestamp(null), TIME_UNAVAILABLE);
  assert.equal(formatTimestamp(undefined), TIME_UNAVAILABLE);
  assert.equal(formatTimestamp(''), TIME_UNAVAILABLE);
  assert.equal(formatTimestamp('  '), TIME_UNAVAILABLE);
  assert.equal(formatTimestamp('nope'), TIME_UNAVAILABLE);
  assert.equal(formatTimestamp(123), TIME_UNAVAILABLE);
  assert.equal(formatTimestamp('2026-10-16T09:00:00Z'), '2026-10-16T09:00:00Z');
});

test('Approval：无审批 ⇒ 明确空状态，绝不显示为"已通过"', () => {
  for (const value of [null, undefined, {}, 'PENDING']) {
    const view = toApprovalView(value);
    assert.equal(view.empty, true);
    assert.equal(view.present, false);
    assert.deepEqual(view.rows, []);
    assert.equal(view.statusRaw, '');
  }
});

test('Approval：PENDING ⇒ 未裁决（不显示裁决时间）', () => {
  const view = toApprovalView({
    approval_id: 'ap-1',
    workflow_run_id: 'r1',
    node_id: 'gate',
    status: 'PENDING',
    resolved_at: null,
  });
  const byKey = Object.fromEntries(view.rows.map((row) => [row.key, row.value]));

  assert.equal(view.present, true);
  assert.equal(view.empty, false);
  assert.equal(view.statusRaw, 'PENDING');
  assert.match(byKey.status, /待裁决/);
  assert.equal(byKey.resolved_at, '（未裁决）');
  assert.equal(view.rows.some((row) => row.key === 'created_at'), false);
});

test('Approval：已裁决 ⇒ 真实裁决时间原样展示', () => {
  const view = toApprovalView({
    approval_id: 'ap-1',
    workflow_run_id: 'r1',
    node_id: 'gate',
    status: 'APPROVED',
    resolved_at: '2026-10-16T10:00:00Z',
  });
  const byKey = Object.fromEntries(view.rows.map((row) => [row.key, row.value]));

  assert.match(byKey.status, /已通过/);
  assert.equal(byKey.resolved_at, '2026-10-16T10:00:00Z');
});

test('summarizeOutput：无输出 ⇒ 占位；有输出 ⇒ 完整原文不修改', () => {
  assert.deepEqual(summarizeOutput(null), {
    hasOutput: false,
    length: 0,
    preview: OUTPUT_EMPTY,
    full: '',
  });

  const corrupt = '{"truncated": "abc';
  const summary = summarizeOutput(corrupt);
  assert.equal(summary.hasOutput, true);
  assert.equal(summary.full, corrupt);      // 原样（不解析 / 不修复）
  assert.equal(summary.length, corrupt.length);

  const long = 'x'.repeat(5000);
  const longSummary = summarizeOutput(long);
  assert.equal(longSummary.full, long);     // 完整原文
  assert.equal(longSummary.length, 5000);
  assert.ok(longSummary.preview.length < 5000); // 预览仅用于标题
});

test('toViewModel：无数据 ⇒ 全部空（不造假）', () => {
  const vm = toViewModel(null);
  assert.equal(vm.hasRun, false);
  assert.deepEqual(vm.overview, []);
  assert.deepEqual(vm.nodeGroups, []);
  assert.deepEqual(vm.timelineRows, []);
  assert.deepEqual(vm.approvals, []);
  assert.equal(vm.nodeCount, 0);
  assert.equal(vm.attemptCount, 0);
});

test('toViewModel：端到端派生（含审批按节点）', () => {
  const vm = toViewModel({
    run: RUN,
    nodes: [
      { node_id: 'n1', attempt: 1, node_type: 'TOOL', status: 'FAILED' },
      { node_id: 'n1', attempt: 2, node_type: 'TOOL', status: 'SUCCEEDED', output: '{"qty":3}' },
      { node_id: 'gate', attempt: 1, node_type: 'APPROVAL', status: 'WAITING_APPROVAL' },
    ],
    timeline: [{ kind: 'RUN_CREATED', timestamp: '2026-10-16T09:00:00Z', workflow_run_id: 'wf-r1' }],
    approvalsByNode: { gate: { approval_id: 'ap-1', status: 'PENDING', node_id: 'gate' } },
  });

  assert.equal(vm.hasRun, true);
  assert.equal(vm.nodeCount, 2);
  assert.equal(vm.attemptCount, 3);           // 全部 attempt 计入
  assert.equal(vm.timelineRows.length, 1);

  assert.equal(vm.approvals.length, 1);
  assert.equal(vm.approvals[0].nodeId, 'gate');
  assert.equal(vm.approvals[0].view.statusRaw, 'PENDING');

  const n1 = vm.nodeGroups.find((g) => g.nodeId === 'n1');
  assert.equal(n1.attempts.length, 2);
  assert.equal(n1.errorCodes.length, 0);
});

test('toViewModel：APPROVAL 节点无审批行 ⇒ 空状态视图', () => {
  const vm = toViewModel({
    run: RUN,
    nodes: [{ node_id: 'gate', attempt: 1, node_type: 'APPROVAL', status: 'WAITING_APPROVAL' }],
    timeline: [],
    approvalsByNode: { gate: null },
  });

  assert.equal(vm.approvals.length, 1);
  assert.equal(vm.approvals[0].view.empty, true);
  assert.equal(vm.approvals[0].view.present, false);
});
