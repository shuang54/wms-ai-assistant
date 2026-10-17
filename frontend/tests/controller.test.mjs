/**
 * 控制器纯逻辑测试 —— `node --test`（Node 内置，**零依赖**）。
 *
 * 覆盖（Step 2 §七.C + §八）：
 *   查询正常 · Loading · 401（需登录 / 会话过期）· 403（非成员）· 404 · 502 ·
 *   刷新 · 重复提交防护 · 竞态（过期响应不得覆盖新查询）·
 *   错误不泄漏内部信息 · 只以 (projectId, runId) 调用读取实现。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ReadError } from '../js/api-client.js';
import { createController, normalizeError } from '../js/controller.js';
import { PHASE } from '../js/state.js';

const PROJECT = 'proj-a';
const RUN = 'wf-run-1';

const SAMPLE = {
  run: { workflow_run_id: RUN },
  nodes: [],
  timeline: [],
  approvalsByNode: {},
};

/** 受控的读取替身（记录调用；可手动 settle）。 */
function makeReader() {
  const calls = [];
  let resolveNext;
  let rejectNext;
  const reader = (projectId, runId) => {
    calls.push([projectId, runId]);
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

test('正常查询：loading → loaded，并记录（项目, Run）', async () => {
  const reader = makeReader();
  const phases = [];
  const controller = createController({
    readRun: reader.reader,
    onChange: (state) => phases.push(state.phase),
  });

  const pending = controller.submit(`  ${PROJECT}  `, `  ${RUN}  `);
  assert.equal(controller.getState().phase, PHASE.LOADING);
  reader.resolve(SAMPLE);
  const result = await pending;

  assert.deepEqual(result, { accepted: true, applied: true });
  assert.equal(controller.getState().phase, PHASE.LOADED);
  assert.equal(controller.getState().projectId, PROJECT); // 已 trim
  assert.equal(controller.getState().runId, RUN);
  assert.deepEqual(phases, [PHASE.LOADING, PHASE.LOADED]);
  assert.deepEqual(reader.calls, [[PROJECT, RUN]]);       // 只发一次
});

test('空输入：项目 ID 或 Run ID 缺失即拒绝提交且不发请求', async () => {
  const reader = makeReader();
  const controller = createController({ readRun: reader.reader });

  const cases = [
    ['', ''],
    ['   ', RUN],
    [PROJECT, '   '],
    [null, RUN],
    [PROJECT, undefined],
    [42, RUN],
  ];
  for (const [project, run] of cases) {
    const result = await controller.submit(project, run);
    assert.deepEqual(result, { accepted: false, applied: false });
  }
  assert.equal(reader.calls.length, 0);
  assert.equal(controller.getState().phase, PHASE.IDLE);
});

test('重复提交防护：加载中再次提交被拒绝，且不产生第二个请求', async () => {
  const reader = makeReader();
  const controller = createController({ readRun: reader.reader });

  const first = controller.submit(PROJECT, RUN);
  const second = await controller.submit(PROJECT, RUN);
  const third = await controller.submit(PROJECT, 'wf-run-2');

  assert.deepEqual(second, { accepted: false, applied: false });
  assert.deepEqual(third, { accepted: false, applied: false });
  assert.deepEqual(reader.calls, [[PROJECT, RUN]]);

  reader.resolve(SAMPLE);
  await first;
  assert.equal(controller.getState().phase, PHASE.LOADED);
});

test('竞态：过期响应不得覆盖较新的查询', async () => {
  const resolvers = [];
  const controller = createController({
    readRun: (projectId, runId) =>
      new Promise((resolve) => resolvers.push({ projectId, runId, resolve })),
  });

  const first = controller.submit(PROJECT, 'run-old');
  resolvers[0].resolve({
    ...SAMPLE,
    run: { workflow_run_id: 'run-old' },
  });
  await first;

  const second = controller.submit(PROJECT, 'run-new');
  assert.equal(controller.getState().phase, PHASE.LOADING);
  assert.equal(controller.getState().data, null); // 旧数据已清空

  resolvers[1].resolve({
    ...SAMPLE,
    run: { workflow_run_id: 'run-new' },
  });
  await second;

  assert.equal(controller.getState().runId, 'run-new');
  assert.equal(controller.getState().data.run.workflow_run_id, 'run-new');
});

test('401 首次失败 ⇒ "需要登录"（不是"会话过期"）', async () => {
  const reader = makeReader();
  const controller = createController({ readRun: reader.reader });

  const pending = controller.submit(PROJECT, RUN);
  reader.reject(new ReadError('unauthorized', 401));
  await pending;

  const { error, phase, projectId, runId } = controller.getState();
  assert.equal(phase, PHASE.ERROR);
  assert.equal(error.category, 'unauthorized');
  assert.equal(error.status, 401);
  assert.equal(error.hint, 'login-required');
  assert.match(error.message, /需要登录/);
  // 失败后保留输入（可重试）
  assert.equal(projectId, PROJECT);
  assert.equal(runId, RUN);
});

test('401 在曾经成功加载后出现 ⇒ "会话已过期"', async () => {
  const reader = makeReader();
  const controller = createController({ readRun: reader.reader });

  const first = controller.submit(PROJECT, RUN);
  reader.resolve(SAMPLE);
  await first;
  assert.equal(controller.getState().phase, PHASE.LOADED);

  const second = controller.refresh();
  reader.reject(new ReadError('unauthorized', 401));
  await second;

  const { error } = controller.getState();
  assert.equal(error.category, 'unauthorized');
  assert.equal(error.hint, 'session-expired');
  assert.match(error.message, /会话已过期|重新登录/);
});

test('403 ⇒ 非该项目成员（授权失败，不提示登录）', async () => {
  const reader = makeReader();
  const controller = createController({ readRun: reader.reader });

  const pending = controller.submit(PROJECT, RUN);
  reader.reject(new ReadError('forbidden', 403));
  await pending;

  const { error } = controller.getState();
  assert.equal(error.category, 'forbidden');
  assert.equal(error.status, 403);
  assert.match(error.message, /不是该项目的成员/);
  assert.doesNotMatch(error.message, /需要登录/);
});

test('404：进入 error 且保留（项目, Run）（可重试）', async () => {
  const reader = makeReader();
  const controller = createController({ readRun: reader.reader });

  const pending = controller.submit(PROJECT, 'missing');
  reader.reject(new ReadError('notFound', 404));
  await pending;

  const state = controller.getState();
  assert.equal(state.phase, PHASE.ERROR);
  assert.equal(state.error.category, 'notFound');
  assert.equal(state.error.status, 404);
  assert.equal(state.projectId, PROJECT);
  assert.equal(state.runId, 'missing');
  assert.equal(state.data, null); // 不留半成品数据
});

test('502：投影失败按"数据无法安全投影"处理，不尝试修复', async () => {
  const reader = makeReader();
  const controller = createController({ readRun: reader.reader });

  const pending = controller.submit(PROJECT, RUN);
  reader.reject(new ReadError('projection', 502));
  await pending;

  const { error } = controller.getState();
  assert.equal(error.category, 'projection');
  assert.equal(error.status, 502);
  assert.match(error.message, /无法安全投影/);
});

test('刷新：对当前（项目, Run）重发 GET；无当前 Run 时不动作', async () => {
  const reader = makeReader();
  const controller = createController({ readRun: reader.reader });

  assert.deepEqual(await controller.refresh(), {
    accepted: false,
    applied: false,
  });
  assert.equal(reader.calls.length, 0);

  const first = controller.submit(PROJECT, RUN);
  reader.resolve(SAMPLE);
  await first;

  const second = controller.refresh();
  assert.deepEqual(reader.calls, [
    [PROJECT, RUN],
    [PROJECT, RUN],
  ]);
  reader.resolve(SAMPLE);
  await second;
  assert.equal(controller.getState().phase, PHASE.LOADED);
});

test('刷新：加载中不重复触发', async () => {
  const reader = makeReader();
  const controller = createController({ readRun: reader.reader });

  const pending = controller.submit(PROJECT, RUN);
  assert.deepEqual(await controller.refresh(), {
    accepted: false,
    applied: false,
  });
  reader.resolve(SAMPLE);
  await pending;
  assert.deepEqual(reader.calls, [[PROJECT, RUN]]);
});

test('未预期异常：规范化为安全文案，不透出原始消息', async () => {
  const leaky = new Error(
    'psycopg.OperationalError: could not connect to postgresql://u:p@host/db'
  );
  const reader = makeReader();
  const controller = createController({ readRun: reader.reader });

  const pending = controller.submit(PROJECT, RUN);
  reader.reject(leaky);
  await pending;

  const { error } = controller.getState();
  assert.equal(error.category, 'server');
  assert.equal(error.status, 0);
  assert.doesNotMatch(error.message, /psycopg|postgresql|host\/db/);

  const normalized = normalizeError(leaky);
  assert.equal(normalized.message, '读取失败：发生未预期的前端错误。');
  assert.doesNotMatch(JSON.stringify(normalized), /psycopg|postgresql/);
});

test('normalizeError：401 的 hint 由"是否成功过"决定', () => {
  const unauthorized = new ReadError('unauthorized', 401);

  assert.equal(normalizeError(unauthorized, false).hint, 'login-required');
  assert.equal(normalizeError(unauthorized, true).hint, 'session-expired');
  assert.match(
    normalizeError(unauthorized, true).message,
    /会话已过期|重新登录/
  );
  // 非 401 不受影响
  assert.equal(normalizeError(new ReadError('forbidden', 403), true).hint, '');
});

test('读取实现只被以 (projectId, runId) 调用（无额外参数 / 无 project 覆盖）', async () => {
  const seen = [];
  const controller = createController({
    readRun: async (...args) => {
      seen.push(args);
      return SAMPLE;
    },
  });

  await controller.submit(PROJECT, RUN);

  assert.deepEqual(seen, [[PROJECT, RUN]]); // 仅两个位置参数
});
