/**
 * 控制器纯逻辑测试 —— `node --test`（Node 内置，**零依赖**）。
 *
 * 覆盖（Phase 7 Workflow UI Step 1 §八 1/7/8/9/10/11/12）：
 *   查询正常 · Loading · 404 · 401 · 502 · 刷新 · 重复提交防护 ·
 *   竞态（过期响应不得覆盖新查询）· 错误不泄漏内部信息。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ReadError } from '../js/api-client.js';
import { createController, normalizeError } from '../js/controller.js';
import { PHASE } from '../js/state.js';

/** 受控的读取替身（记录调用；可手动 settle）。 */
function makeReader() {
  const calls = [];
  let resolveNext;
  let rejectNext;
  const reader = (runId) => {
    calls.push(runId);
    return new Promise((resolve, reject) => {
      resolveNext = resolve;
      rejectNext = reject;
    });
  };
  return {
    reader,
    calls,
    resolve: (value) => resolveNext(value),
    reject: (error) => rejectNext(error),
  };
}

const SAMPLE = { run: { workflow_run_id: 'r1' }, nodes: [], timeline: [], approvalsByNode: {} };

test('正常查询：loading → loaded，并记录 Run ID', async () => {
  const reader = makeReader();
  const states = [];
  const controller = createController({
    readRun: reader.reader,
    onChange: (state) => states.push(state.phase),
  });

  const pending = controller.submit('  r1  ');
  assert.equal(controller.getState().phase, PHASE.LOADING);
  reader.resolve(SAMPLE);
  const result = await pending;

  assert.deepEqual(result, { accepted: true, applied: true });
  assert.equal(controller.getState().phase, PHASE.LOADED);
  assert.equal(controller.getState().runId, 'r1'); // 已 trim
  assert.deepEqual(states, [PHASE.LOADING, PHASE.LOADED]);
  assert.deepEqual(reader.calls, ['r1']); // 只发一次请求
});

test('空输入：拒绝提交且不发请求', async () => {
  const reader = makeReader();
  const controller = createController({ readRun: reader.reader });

  for (const value of ['', '   ', null, undefined, 42]) {
    const result = await controller.submit(value);
    assert.deepEqual(result, { accepted: false, applied: false });
  }
  assert.equal(reader.calls.length, 0);
  assert.equal(controller.getState().phase, PHASE.IDLE);
});

test('重复提交防护：加载中再次提交被拒绝，且不产生第二个请求', async () => {
  const reader = makeReader();
  const controller = createController({ readRun: reader.reader });

  const first = controller.submit('r1');
  const second = await controller.submit('r1'); // 加载中
  const third = await controller.submit('r2');  // 加载中

  assert.deepEqual(second, { accepted: false, applied: false });
  assert.deepEqual(third, { accepted: false, applied: false });
  assert.deepEqual(reader.calls, ['r1']); // 只有一个请求

  reader.resolve(SAMPLE);
  await first;
  assert.equal(controller.getState().phase, PHASE.LOADED);
});

test('竞态：过期响应不得覆盖较新的查询', async () => {
  const pendingResolvers = [];
  const controller = createController({
    readRun: (runId) =>
      new Promise((resolve) => {
        pendingResolvers.push({ runId, resolve });
      }),
  });

  const firstRun = controller.submit('run-old');
  pendingResolvers[0].resolve({ ...SAMPLE, run: { workflow_run_id: 'run-old' } });
  await firstRun; // 第一次完成（loaded）

  const secondRun = controller.submit('run-new');
  assert.equal(controller.getState().phase, PHASE.LOADING);
  assert.equal(controller.getState().data, null); // 旧数据已清空

  pendingResolvers[1].resolve({ ...SAMPLE, run: { workflow_run_id: 'run-new' } });
  await secondRun;

  assert.equal(controller.getState().runId, 'run-new');
  assert.equal(controller.getState().data.run.workflow_run_id, 'run-new');
});

test('404：进入 error 且保留 runId（可重试）', async () => {
  const reader = makeReader();
  const controller = createController({ readRun: reader.reader });

  const pending = controller.submit('missing');
  reader.reject(new ReadError('notFound', 404));
  await pending;

  const state = controller.getState();
  assert.equal(state.phase, PHASE.ERROR);
  assert.equal(state.error.category, 'notFound');
  assert.equal(state.error.status, 404);
  assert.equal(state.runId, 'missing');
  assert.equal(state.data, null); // 不留半成品数据
});

test('401：错误类别正确，且不带任何凭据提示', async () => {
  const reader = makeReader();
  const controller = createController({ readRun: reader.reader });

  const pending = controller.submit('r1');
  reader.reject(new ReadError('unauthorized', 401));
  await pending;

  const { error } = controller.getState();
  assert.equal(error.category, 'unauthorized');
  assert.equal(error.status, 401);
  assert.match(error.message, /无权访问/);
  assert.doesNotMatch(error.message, /API-?Key|X-API-Key|token/i);
});

test('502：投影失败按"数据无法安全投影"处理，不尝试修复', async () => {
  const reader = makeReader();
  const controller = createController({ readRun: reader.reader });

  const pending = controller.submit('r1');
  reader.reject(new ReadError('projection', 502));
  await pending;

  const { error } = controller.getState();
  assert.equal(error.category, 'projection');
  assert.equal(error.status, 502);
  assert.match(error.message, /无法安全投影/);
});

test('刷新：对当前 Run 重发 GET；无当前 Run 时不动作', async () => {
  const reader = makeReader();
  const controller = createController({ readRun: reader.reader });

  assert.deepEqual(await controller.refresh(), {
    accepted: false,
    applied: false,
  });
  assert.equal(reader.calls.length, 0);

  const first = controller.submit('r1');
  reader.resolve(SAMPLE);
  await first;

  const second = controller.refresh();
  assert.deepEqual(reader.calls, ['r1', 'r1']); // 同一 Run ID 再次读取
  reader.resolve(SAMPLE);
  await second;
  assert.equal(controller.getState().phase, PHASE.LOADED);
});

test('刷新：加载中不重复触发', async () => {
  const reader = makeReader();
  const controller = createController({ readRun: reader.reader });

  const pending = controller.submit('r1');
  assert.deepEqual(await controller.refresh(), {
    accepted: false,
    applied: false,
  });
  reader.resolve(SAMPLE);
  await pending;
  assert.deepEqual(reader.calls, ['r1']);
});

test('未预期异常：规范化为安全文案，不透出原始消息', async () => {
  const leaky = new Error(
    'psycopg.OperationalError: could not connect to postgresql://u:p@host/db'
  );
  const reader = makeReader();
  const controller = createController({ readRun: reader.reader });

  const pending = controller.submit('r1');
  reader.reject(leaky);
  await pending;

  const { error } = controller.getState();
  assert.equal(error.category, 'server');
  assert.equal(error.status, 0);
  assert.doesNotMatch(error.message, /psycopg|postgresql|host\/db/);

  // normalizeError 本身也不透出原始正文
  const normalized = normalizeError(leaky);
  assert.equal(normalized.message, '读取失败：发生未预期的前端错误。');
  assert.doesNotMatch(JSON.stringify(normalized), /psycopg|postgresql/);
});

test('读取实现只被以 Run ID 调用（无写入参数 / 无 project_id）', async () => {
  const seen = [];
  const controller = createController({
    readRun: async (...args) => {
      seen.push(args);
      return SAMPLE;
    },
  });

  await controller.submit('r1');

  assert.deepEqual(seen, [['r1']]); // 仅一个位置参数：Run ID
});
