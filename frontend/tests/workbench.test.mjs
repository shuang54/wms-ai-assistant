/**
 * 运维工作台（Step 3）测试 —— `node --test`（Node 内置，**零依赖**）。
 *
 * 覆盖任务书 §六 1–15：
 *   状态筛选 · 多 Attempt 保留 · 清除/无匹配 · Timeline ↔ Attempt 双向定位 ·
 *   缺失关联事件 · 输出展开/折叠/复制 · 空输出 · 未知状态安全展示 ·
 *   重复提交防护 · 失败后状态恢复 · 401/403/404/502/503 文案 ·
 *   无审批 ≠ 成功 · 写操作不可调用 · 输出不被当作 HTML · 无伪造成功数据。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ReadError } from '../js/api-client.js';
import { createController, findAttemptOutput } from '../js/controller.js';
import {
  COPY_MESSAGES,
  COPY_STATUS,
  NODE_FILTER,
  NODE_STATUS_LABELS,
  PHASE,
  applyNodeFilter,
  attemptDomId,
  attemptForEvent,
  attemptMatchesFilter,
  copyMessage,
  copyOutput,
  eventIndexesForAttempt,
  groupAttemptsByNode,
  hasEventsForAttempt,
  isSelectedAttempt,
  label,
  normalizeFilter,
  safeDomFragment,
  summarizeOutput,
  toTimelineRows,
  toViewModel,
} from '../js/state.js';

const PROJECT = 'proj-a';
const RUN = 'wf-run-1';
const T0 = '2026-10-17T09:00:00Z';

/** 真实形态的 Node Attempt 记录（与 DTO 字段一致）。 */
function node({ nodeId = 'n1', attempt = 1, type = 'TOOL', status = 'SUCCEEDED', output = null, error = null, started = T0, completed = null, elapsed = null } = {}) {
  return {
    workflow_run_id: RUN,
    node_id: nodeId,
    attempt,
    node_type: type,
    status,
    output,
    error_code: error,
    elapsed_ms: elapsed,
    started_at: started,
    completed_at: completed,
  };
}

/** 真实形态的 Timeline 事件。 */
function event({ kind = 'RUN_CREATED', timestamp = T0, nodeId = null, attempt = null, approvalId = null } = {}) {
  return {
    kind,
    timestamp,
    workflow_run_id: RUN,
    node_id: nodeId,
    attempt,
    approval_id: approvalId,
  };
}

const MIXED_NODES = [
  node({ nodeId: 'ai', attempt: 1, type: 'AI', status: 'SUCCEEDED' }),
  node({ nodeId: 'tool', attempt: 1, type: 'TOOL', status: 'FAILED', error: 'WORKFLOW_NODE_EXECUTION_FAILURE' }),
  node({ nodeId: 'tool', attempt: 2, type: 'TOOL', status: 'SUCCEEDED', output: '{"qty":3}' }),
  node({ nodeId: 'tool', attempt: 3, type: 'TOOL', status: 'RUNNING' }),
  node({ nodeId: 'gate', attempt: 1, type: 'APPROVAL', status: 'WAITING_APPROVAL' }),
  node({ nodeId: 'weird', attempt: 1, type: 'CONDITION', status: 'TOTALLY_UNKNOWN' }),
];

const MIXED_TIMELINE = [
  event({ kind: 'RUN_CREATED' }),
  event({ kind: 'NODE_ATTEMPT_STARTED', nodeId: 'tool', attempt: 1 }),
  event({ kind: 'NODE_ATTEMPT_FINISHED', nodeId: 'tool', attempt: 1 }),
  event({ kind: 'NODE_ATTEMPT_STARTED', nodeId: 'tool', attempt: 2 }),
  // 注意：attempt 3 没有对应事件（用于验证"缺失关联事件"）
  event({ kind: 'APPROVAL_RESOLVED', nodeId: 'gate', attempt: 1, approvalId: 'ap-1' }),
];

const DATA = {
  run: { workflow_run_id: RUN, project_id: PROJECT, status: 'RUNNING' },
  nodes: MIXED_NODES,
  timeline: MIXED_TIMELINE,
  approvalsByNode: { gate: { approval_id: 'ap-1', node_id: 'gate', status: 'PENDING', resolved_at: null } },
};

// ============================================================
// 1 / 2 / 3：筛选
// ============================================================

test('筛选：默认「全部」保留所有 attempt（多 Attempt 不折叠）', () => {
  const groups = groupAttemptsByNode(MIXED_NODES);
  const result = applyNodeFilter(groups, NODE_FILTER.ALL);

  assert.equal(result.isFiltered, false);
  assert.equal(result.totalNodes, groups.length);
  assert.equal(result.totalAttempts, MIXED_NODES.length);
  assert.equal(result.matchedAttempts, MIXED_NODES.length);

  const tool = result.groups.find((g) => g.nodeId === 'tool');
  assert.equal(tool.attemptCount, 3);                       // 全部保留
  assert.deepEqual(tool.attempts.map((a) => a.attempt), [1, 2, 3]);
  assert.equal(tool.failureCount, 1);
  assert.equal(tool.anyFailed, true);
});

test('筛选：各状态真实命中（按后端枚举）', () => {
  const groups = groupAttemptsByNode(MIXED_NODES);

  const failed = applyNodeFilter(groups, NODE_FILTER.FAILED);
  assert.equal(failed.matchedAttempts, 1);
  assert.deepEqual(failed.groups.map((g) => g.nodeId), ['tool']);
  assert.deepEqual(failed.groups[0].attemptMatches, [true, false, false]);

  const running = applyNodeFilter(groups, NODE_FILTER.RUNNING);
  assert.equal(running.matchedAttempts, 1);
  assert.deepEqual(running.groups.map((g) => g.nodeId), ['tool']);

  const waiting = applyNodeFilter(groups, NODE_FILTER.WAITING_APPROVAL);
  assert.deepEqual(waiting.groups.map((g) => g.nodeId), ['gate']);

  const succeeded = applyNodeFilter(groups, NODE_FILTER.SUCCEEDED);
  assert.equal(succeeded.matchedAttempts, 2);               // ai + tool#2
  assert.deepEqual(succeeded.groups.map((g) => g.nodeId), ['ai', 'tool']);
});

test('筛选：「未知状态」只匹配域白名单外的非空状态（不映射为成功）', () => {
  const groups = groupAttemptsByNode(MIXED_NODES);
  const unknown = applyNodeFilter(groups, NODE_FILTER.UNKNOWN);

  assert.deepEqual(unknown.groups.map((g) => g.nodeId), ['weird']);
  assert.equal(unknown.matchedAttempts, 1);

  // 空白 / 缺失不算"未知状态"（属数据缺失）
  assert.equal(attemptMatchesFilter(node({ status: '' }), NODE_FILTER.UNKNOWN), false);
  assert.equal(attemptMatchesFilter(node({ status: undefined }), NODE_FILTER.UNKNOWN), false);
  // 已知状态绝不被算作未知
  for (const status of Object.keys(NODE_STATUS_LABELS)) {
    assert.equal(attemptMatchesFilter(node({ status }), NODE_FILTER.UNKNOWN), false, status);
  }
});

test('筛选：入选节点仍展示其**全部**历史 attempt（命中项被标记）', () => {
  const result = applyNodeFilter(groupAttemptsByNode(MIXED_NODES), NODE_FILTER.FAILED);
  const tool = result.groups.find((g) => g.nodeId === 'tool');

  // 只有 attempt#1 命中，但 #1/#2/#3 全部返回（历史不丢失）
  assert.equal(tool.attemptCount, 3);
  assert.deepEqual(tool.attempts.map((a) => a.attempt), [1, 2, 3]);
  assert.deepEqual(tool.attemptMatches, [true, false, false]);
  assert.equal(tool.matchedCount, 1);
});

test('筛选：无匹配 ⇒ 空 groups + 计数为 0（不伪造数据）', () => {
  const onlySucceeded = [node({ status: 'SUCCEEDED' })];
  const result = applyNodeFilter(groupAttemptsByNode(onlySucceeded), NODE_FILTER.FAILED);

  assert.deepEqual(result.groups, []);
  assert.equal(result.matchedAttempts, 0);
  assert.equal(result.visibleNodes, 0);
  assert.equal(result.totalAttempts, 1);   // 总量仍如实
});

test('筛选：清除筛选恢复全部（normalizeFilter 容错）', () => {
  assert.equal(normalizeFilter(NODE_FILTER.FAILED), NODE_FILTER.FAILED);
  assert.equal(normalizeFilter('bogus'), NODE_FILTER.ALL);
  assert.equal(normalizeFilter(null), NODE_FILTER.ALL);
  assert.equal(normalizeFilter(undefined), NODE_FILTER.ALL);

  const groups = groupAttemptsByNode(MIXED_NODES);
  const cleared = applyNodeFilter(groups, normalizeFilter('bogus'));
  assert.equal(cleared.isFiltered, false);
  assert.equal(cleared.groups.length, groups.length);
});

test('筛选：不修改入参（纯函数）', () => {
  const groups = groupAttemptsByNode(MIXED_NODES);
  const snapshot = JSON.stringify(groups);

  applyNodeFilter(groups, NODE_FILTER.FAILED);

  assert.equal(JSON.stringify(groups), snapshot);
});

test('筛选：toViewModel 透传筛选结果（未加载数据时安全空值）', () => {
  const vm = toViewModel(DATA, { filter: NODE_FILTER.FAILED });
  assert.equal(vm.filter, NODE_FILTER.FAILED);
  assert.equal(vm.filterResult.isFiltered, true);
  assert.equal(vm.nodeCount, 1);            // 可见节点数
  assert.equal(vm.attemptCount, MIXED_NODES.length); // 总 attempt 数

  const empty = toViewModel(null, { filter: NODE_FILTER.FAILED });
  assert.equal(empty.hasRun, false);
  assert.deepEqual(empty.nodeGroups, []);
  assert.equal(empty.filterResult.matchedAttempts, 0);
  assert.equal(empty.filter, NODE_FILTER.FAILED);
});

// ============================================================
// 4 / 5：Timeline ↔ Attempt 双向定位
// ============================================================

test('联动：Attempt → 时间线事件（真实存在的事件下标，保持后端顺序）', () => {
  const rows = toTimelineRows(MIXED_TIMELINE);
  const indexes = eventIndexesForAttempt(rows, 'tool', '1');

  assert.deepEqual(indexes, [1, 2]);
  assert.equal(hasEventsForAttempt(rows, 'tool', '1'), true);
});

test('联动：时间线事件 → Attempt 身份', () => {
  const rows = toTimelineRows(MIXED_TIMELINE);

  assert.deepEqual(attemptForEvent(rows[1]), { nodeId: 'tool', attempt: '1' });
  assert.deepEqual(attemptForEvent(rows[4]), { nodeId: 'gate', attempt: '1' });
  // Run 级事件无可定位节点身份
  assert.equal(attemptForEvent(rows[0]), null);
});

test('联动：缺失关联事件 ⇒ 空（**不**补造）', () => {
  const rows = toTimelineRows(MIXED_TIMELINE);

  // attempt#3 在时间线中没有事件
  assert.deepEqual(eventIndexesForAttempt(rows, 'tool', '3'), []);
  assert.equal(hasEventsForAttempt(rows, 'tool', '3'), false);

  // 完全不存在的节点
  assert.deepEqual(eventIndexesForAttempt(rows, 'ghost', '1'), []);
});

test('联动：DOM id 稳定且仅含安全字符（任意后端标识）', () => {
  const id = attemptDomId('node with space/"quote"', '1');
  assert.match(id, /^attempt-[A-Za-z0-9_-]+$/);
  assert.equal(id, attemptDomId('node with space/"quote"', '1'));  // 稳定
  assert.notEqual(id, attemptDomId('node with space/"quote"', '2'));

  // 非 ASCII 也被编码为安全字符
  const zh = attemptDomId('节点-一', 1);
  assert.match(zh, /^attempt-[A-Za-z0-9_-]+$/);

  assert.equal(safeDomFragment(''), '_empty');
  assert.equal(safeDomFragment(null), '_empty');
});

test('联动：选中比较（isSelectedAttempt）', () => {
  const selection = { nodeId: 'tool', attempt: '2' };
  assert.equal(isSelectedAttempt(selection, 'tool', '2'), true);
  assert.equal(isSelectedAttempt(selection, 'tool', 2), true);      // 数字等价
  assert.equal(isSelectedAttempt(selection, 'tool', '1'), false);
  assert.equal(isSelectedAttempt(null, 'tool', '2'), false);
});

// ============================================================
// 6 / 7：输出查看与复制
// ============================================================

test('输出：展开/折叠文案与完整原文（不截断、不解析）', () => {
  const corrupt = '{"truncated": "abc';
  const summary = summarizeOutput(corrupt, 10);

  assert.equal(summary.hasOutput, true);
  assert.equal(summary.full, corrupt);           // 完整原文，原样
  assert.equal(summary.length, corrupt.length);
  assert.ok(summary.preview.length < 1000);      // 预览只是标题

  const long = 'x'.repeat(5000);
  assert.equal(summarizeOutput(long).full, long); // 不截断
});

test('输出：空输出 ⇒ EMPTY 状态且不写剪贴板', async () => {
  let writes = 0;
  const clipboard = {
    writeText: async () => {
      writes += 1;
    },
  };

  for (const value of [null, undefined, '', 123]) {
    assert.equal(await copyOutput(value, clipboard), COPY_STATUS.EMPTY);
  }
  assert.equal(writes, 0);
  assert.match(copyMessage(COPY_STATUS.EMPTY), /没有持久化输出/);
});

test('输出：复制成功 ⇒ COPIED（内容**原样**写入，不改写）', async () => {
  const raw = '{"qty": 3}\n第二行\t<tag>';
  let written = null;
  const clipboard = {
    writeText: async (value) => {
      written = value;
    },
  };

  assert.equal(await copyOutput(raw, clipboard), COPY_STATUS.COPIED);
  assert.equal(written, raw);                    // 逐字符原样
  assert.match(copyMessage(COPY_STATUS.COPIED), /已复制/);
});

test('输出：剪贴板缺失 / 拒绝 ⇒ FAILED（不抛异常）', async () => {
  assert.equal(await copyOutput('x', null), COPY_STATUS.FAILED);
  assert.equal(await copyOutput('x', {}), COPY_STATUS.FAILED);

  const denying = {
    writeText: async () => {
      throw new Error('NotAllowedError');
    },
  };
  assert.equal(await copyOutput('x', denying), COPY_STATUS.FAILED);
  assert.match(copyMessage(COPY_STATUS.FAILED), /复制失败/);
  assert.doesNotMatch(copyMessage(COPY_STATUS.FAILED), /NotAllowedError/);
});

test('输出：复制内容来自已加载数据（找不到 ⇒ EMPTY 语义）', () => {
  assert.equal(findAttemptOutput(DATA, 'tool', 2), '{"qty":3}');
  assert.equal(findAttemptOutput(DATA, 'tool', '2'), '{"qty":3}');
  assert.equal(findAttemptOutput(DATA, 'tool', 1), null);   // 无输出
  assert.equal(findAttemptOutput(DATA, 'ghost', 1), null);
  assert.equal(findAttemptOutput(null, 'tool', 2), null);
});

// ============================================================
// 8：未知状态安全展示
// ============================================================

test('未知状态：原样显示并标注，不映射为成功', () => {
  const shown = label('TOTALLY_UNKNOWN', NODE_STATUS_LABELS);

  assert.match(shown, /TOTALLY_UNKNOWN/);
  assert.match(shown, /未在域白名单内/);
  assert.doesNotMatch(shown, /成功|已完成/);
});

test('已知状态仍给出中文标签（含原始值）', () => {
  const shown = label('FAILED', NODE_STATUS_LABELS);
  assert.match(shown, /失败/);
  assert.match(shown, /FAILED/);
});

// ============================================================
// 9 / 10：重复提交与失败后恢复
// ============================================================

function makeReader() {
  const calls = [];
  let settle = null;
  return {
    reader: (projectId, runId) => {
      calls.push([projectId, runId]);
      return new Promise((resolve, reject) => {
        settle = { resolve, reject };
      });
    },
    calls,
    resolve: (value) => settle.resolve(value),
    reject: (error) => settle.reject(error),
  };
}

test('重复提交防护：加载中再次提交被拒绝（不产生第二个请求）', async () => {
  const r = makeReader();
  const controller = createController({ readRun: r.reader });

  const first = controller.submit(PROJECT, RUN);
  assert.deepEqual(await controller.submit(PROJECT, RUN), {
    accepted: false,
    applied: false,
  });
  assert.deepEqual(await controller.refresh(), { accepted: false, applied: false });
  assert.deepEqual(r.calls, [[PROJECT, RUN]]);

  r.resolve(DATA);
  await first;
  assert.equal(controller.getState().phase, PHASE.LOADED);
});

test('失败后恢复：保留输入，重新加载成功后清除错误与选中', async () => {
  const r = makeReader();
  const controller = createController({ readRun: r.reader });

  const pending = controller.submit(PROJECT, RUN);
  r.reject(new ReadError('notFound', 404));
  await pending;

  assert.equal(controller.getState().phase, PHASE.ERROR);
  assert.equal(controller.getState().runId, RUN);

  const retry = controller.refresh();
  assert.equal(controller.getState().phase, PHASE.LOADING);
  r.resolve(DATA);
  await retry;

  const state = controller.getState();
  assert.equal(state.phase, PHASE.LOADED);
  assert.equal(state.error, null);
  assert.equal(controller.getUi().selectedAttempt, null);
  assert.equal(controller.getUi().copyStatus, COPY_STATUS.IDLE);
});

// ============================================================
// 11：错误文案（401 / 403 / 404 / 502 / 503）
// ============================================================

test('错误文案：五类状态均有明确、无内部细节的提示', async () => {
  const cases = [
    [401, 'unauthorized', /需要登录/],
    [403, 'forbidden', /不是该项目的成员/],
    [404, 'notFound', /未找到该资源/],
    [502, 'projection', /无法安全投影/],
    [503, 'unavailable', /不可用/],
  ];

  for (const [status, category, pattern] of cases) {
    const r = makeReader();
    const controller = createController({ readRun: r.reader });
    const pending = controller.submit(PROJECT, RUN);
    r.reject(new ReadError(category, status));
    await pending;

    const { error } = controller.getState();
    assert.equal(error.status, status);
    assert.equal(error.category, category);
    assert.match(error.message, pattern, String(status));

    // 不得泄漏内部细节
    for (const forbidden of [
      'Traceback',
      'psycopg',
      'postgresql',
      'SELECT',
      'X-API-Key',
      'authorization_grant',
    ]) {
      assert.equal(error.message.includes(forbidden), false, `${status}:${forbidden}`);
    }
  }
});

// ============================================================
// 12：无审批记录 ≠ 成功
// ============================================================

test('审批：无记录 ⇒ 空状态，绝不显示为已通过', () => {
  const vm = toViewModel(
    {
      run: { workflow_run_id: RUN },
      nodes: [node({ nodeId: 'gate', type: 'APPROVAL', status: 'WAITING_APPROVAL' })],
      timeline: [],
      approvalsByNode: { gate: null },
    },
    {}
  );

  assert.equal(vm.approvals.length, 1);
  assert.equal(vm.approvals[0].view.empty, true);
  assert.equal(vm.approvals[0].view.present, false);
  assert.deepEqual(vm.approvals[0].view.rows, []);
  assert.equal(vm.approvals[0].view.statusRaw, '');
});

// ============================================================
// 13：写操作不可调用
// ============================================================

test('控制器不暴露任何写 / 执行方法', () => {
  const controller = createController({ readRun: async () => DATA });
  for (const name of [
    'start',
    'advance',
    'cancel',
    'requestCancel',
    'decide',
    'approve',
    'reject',
    'retry',
    'resume',
    'decideApproval',
  ]) {
    assert.equal(name in controller, false, name);
  }
});

test('筛选 / 选中 / 复制**不产生任何请求**', async () => {
  const r = makeReader();
  const controller = createController({
    readRun: r.reader,
    clipboard: { writeText: async () => {} },
  });

  const pending = controller.submit(PROJECT, RUN);
  r.resolve(DATA);
  await pending;
  assert.equal(r.calls.length, 1);

  controller.setFilter(NODE_FILTER.FAILED);
  controller.selectAttempt('tool', 1);
  controller.focusNodeEvent({ nodeId: 'gate', attempt: '1' });
  controller.clearSelection();
  controller.clearFilter();
  await controller.copyAttemptOutput('tool', 2);

  assert.equal(r.calls.length, 1, '交互不得触发额外请求');
});

// ============================================================
// 14 / 15：输出不作为 HTML 执行 · 无伪造成功数据
// ============================================================

test('输出文本被当作纯文本保存（不解析、不转义篡改）', async () => {
  const htmlish = '<script>alert(1)</script><img src=x onerror=alert(2)>';
  let written = null;

  assert.equal(
    await copyOutput(htmlish, {
      writeText: async (value) => {
        written = value;
      },
    }),
    COPY_STATUS.COPIED
  );
  // 逐字符原样（渲染层用 textContent ⇒ 不会执行）
  assert.equal(written, htmlish);
});

test('无伪造成功数据：未加载时所有区块为空', () => {
  const vm = toViewModel(null, {});

  assert.equal(vm.hasRun, false);
  assert.deepEqual(vm.overview, []);
  assert.deepEqual(vm.nodeGroups, []);
  assert.deepEqual(vm.timelineRows, []);
  assert.deepEqual(vm.approvals, []);
  assert.equal(vm.nodeCount, 0);
  assert.equal(vm.attemptCount, 0);
});
