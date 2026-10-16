/**
 * API client 测试 —— `node --test`（Node 内置，**零依赖**）。
 *
 * 覆盖（Phase 7 Workflow UI Step 1 §八 10/13/14/15/16）：
 *   路径与真实路由一致 · 只发 GET · **不携带任何凭据** · 同源相对基址 ·
 *   状态码 → 错误类别映射 · 非法 JSON · 网络错误 · 按 Node 读取审批的
 *   `null` 语义 · 只读面（无写方法导出）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  API_BASE,
  ERROR_CATEGORIES,
  READ_ROUTES,
  ReadError,
  categorizeStatus,
  readJson,
  readRunBundle,
} from '../js/api-client.js';

/** 构造 fetch 替身；记录每次调用的 (url, init)。 */
function makeFetch(handler) {
  const calls = [];
  const impl = async (url, init) => {
    calls.push({ url, init });
    return handler(url, init);
  };
  return { impl, calls };
}

function jsonResponse(status, body, ok = status >= 200 && status < 300) {
  return { status, ok, json: async () => body };
}

test('API_BASE 固定为空（同源相对路径，不允许携带凭证的绝对地址）', () => {
  assert.equal(API_BASE, '');
});

test('路由与后端真实路径一致且参数已转义', () => {
  assert.equal(READ_ROUTES.runDetail('r1'), '/api/workflow-runs/r1');
  assert.equal(READ_ROUTES.runNodes('r1'), '/api/workflow-runs/r1/nodes');
  assert.equal(READ_ROUTES.runTimeline('r1'), '/api/workflow-runs/r1/timeline');
  assert.equal(
    READ_ROUTES.runApprovalByNode('r1', 'gate'),
    '/api/workflow-runs/r1/approvals/gate'
  );
  assert.equal(
    READ_ROUTES.approvalById('ap-1'),
    '/api/workflow-approvals/ap-1'
  );

  // 转义：路径穿越 / 注入字符不得被直接拼接
  assert.equal(
    READ_ROUTES.runDetail('a/../b'),
    '/api/workflow-runs/a%2F..%2Fb'
  );
  assert.equal(
    READ_ROUTES.runApprovalByNode('r1', 'x y?z=1'),
    '/api/workflow-runs/r1/approvals/x%20y%3Fz%3D1'
  );
});

test('readJson 只发 GET、不带凭据、不使用任何请求体', async () => {
  const { impl, calls } = makeFetch(() => jsonResponse(200, { ok: true }));

  const body = await readJson('/api/workflow-runs/r1', { fetchImpl: impl });

  assert.deepEqual(body, { ok: true });
  assert.equal(calls.length, 1);
  const { url, init } = calls[0];
  assert.equal(url, '/api/workflow-runs/r1'); // 相对 = 同源
  assert.equal(init.method, 'GET');
  assert.equal(init.body, undefined);
  assert.deepEqual(init.headers, { Accept: 'application/json' });
  assert.equal(init.credentials, 'same-origin');

  // **绝无**任何凭据头
  const headerNames = Object.keys(init.headers).map((k) => k.toLowerCase());
  assert.deepEqual(headerNames, ['accept']);
  assert.equal(JSON.stringify(init).includes('api-key'), false);
  assert.equal(JSON.stringify(init).includes('authorization'), false);
  assert.equal(JSON.stringify(init).includes('bearer'), false);
});

test('状态码 → 错误类别（唯一映射处）', () => {
  assert.equal(categorizeStatus(400), 'invalid');
  assert.equal(categorizeStatus(401), 'unauthorized');
  assert.equal(categorizeStatus(403), 'forbidden');
  assert.equal(categorizeStatus(404), 'notFound');
  assert.equal(categorizeStatus(422), 'invalid');
  assert.equal(categorizeStatus(502), 'projection');
  assert.equal(categorizeStatus(503), 'unavailable');
  assert.equal(categorizeStatus(500), 'server');
  assert.equal(categorizeStatus(504), 'server');
  assert.equal(categorizeStatus(200), 'invalidPayload'); // 非 ok 的 2xx 视为形状异常
});

test('所有映射结果都在 ERROR_CATEGORIES 白名单内', () => {
  for (const status of [0, 200, 400, 401, 403, 404, 422, 500, 502, 503, 599]) {
    assert.ok(ERROR_CATEGORIES.includes(categorizeStatus(status)), String(status));
  }
});

test('非 2xx：抛出对应类别的 ReadError（不含响应正文）', async () => {
  for (const [status, category] of [
    [401, 'unauthorized'],
    [403, 'forbidden'],
    [404, 'notFound'],
    [422, 'invalid'],
    [502, 'projection'],
    [503, 'unavailable'],
    [500, 'server'],
  ]) {
    const { impl } = makeFetch(() =>
      jsonResponse(status, { detail: 'SECRET-INTERNAL-DETAIL' }, false)
    );
    await assert.rejects(
      () => readJson('/x', { fetchImpl: impl }),
      (error) => {
        assert.ok(error instanceof ReadError);
        assert.equal(error.category, category);
        assert.equal(error.status, status);
        // 后端 detail 正文不得进入错误消息
        assert.doesNotMatch(error.message, /SECRET-INTERNAL-DETAIL/);
        return true;
      }
    );
  }
});

test('2xx 但响应不是合法 JSON ⇒ invalidPayload', async () => {
  const { impl } = makeFetch(() => ({
    status: 200,
    ok: true,
    json: async () => {
      throw new SyntaxError('Unexpected token < in JSON');
    },
  }));

  await assert.rejects(
    () => readJson('/x', { fetchImpl: impl }),
    (error) => {
      assert.equal(error.category, 'invalidPayload');
      assert.doesNotMatch(error.message, /Unexpected token/);
      return true;
    }
  );
});

test('网络错误 ⇒ network（不透出底层异常正文）', async () => {
  const { impl } = makeFetch(() => {
    throw new TypeError('Failed to fetch https://internal-host/db');
  });

  await assert.rejects(
    () => readJson('/x', { fetchImpl: impl }),
    (error) => {
      assert.equal(error.category, 'network');
      assert.equal(error.status, 0);
      assert.doesNotMatch(error.message, /internal-host|Failed to fetch/);
      return true;
    }
  );
});

test('无 fetch 可用 ⇒ network（不抛原始异常）', async () => {
  await assert.rejects(
    () => readJson('/x', { fetchImpl: undefined }),
    (error) => {
      assert.ok(error instanceof ReadError);
      assert.equal(error.category, 'network');
      return true;
    }
  );
});

test('readRunBundle：读取 Run / Nodes / Timeline，并只对 APPROVAL 节点查审批', async () => {
  const routes = [];
  const { impl } = makeFetch((url) => {
    routes.push(url);
    if (url.endsWith('/nodes')) {
      return jsonResponse(200, [
        { node_id: 'n1', node_type: 'TOOL', attempt: 1 },
        { node_id: 'gate', node_type: 'APPROVAL', attempt: 1 },
      ]);
    }
    if (url.endsWith('/timeline')) {
      return jsonResponse(200, [
        { kind: 'RUN_CREATED', timestamp: '2026-10-16T09:00:00Z', workflow_run_id: 'r1' },
      ]);
    }
    if (url.endsWith('/approvals/gate')) {
      return jsonResponse(200, { approval_id: 'ap-1', status: 'PENDING' });
    }
    return jsonResponse(200, { workflow_run_id: 'r1' });
  });

  const bundle = await readRunBundle('r1', { fetchImpl: impl });

  assert.equal(bundle.run.workflow_run_id, 'r1');
  assert.equal(bundle.nodes.length, 2);
  assert.ok(Array.isArray(bundle.timeline));
  assert.deepEqual(bundle.approvalsByNode, {
    gate: { approval_id: 'ap-1', status: 'PENDING' },
  });

  // 非 APPROVAL 节点不发审批请求（避免盲目请求）
  const approvalCalls = routes.filter((u) => u.includes('/approvals/'));
  assert.deepEqual(approvalCalls, ['/api/workflow-runs/r1/approvals/gate']);

  // 全部为 GET，且无凭据
  assert.equal(routes.length, 4);
  for (const url of routes) {
    assert.ok(url.startsWith('/api/'));
    assert.equal(url.includes('http://') || url.includes('https://'), false);
  }
});

test('readRunBundle：审批 404（尚无审批行）⇒ null，不伪造成已通过', async () => {
  const { impl } = makeFetch((url) => {
    if (url.endsWith('/nodes')) {
      return jsonResponse(200, [{ node_id: 'gate', node_type: 'APPROVAL' }]);
    }
    if (url.endsWith('/approvals/gate')) {
      return jsonResponse(404, { detail: 'Workflow 审批不存在' }, false);
    }
    return jsonResponse(200, { workflow_run_id: 'r1' });
  });

  const bundle = await readRunBundle('r1', { fetchImpl: impl });

  assert.equal(bundle.approvalsByNode.gate, null);
});

test('readRunBundle：审批读取的其它错误（如 502）向上抛出，不静默吞掉', async () => {
  const { impl } = makeFetch((url) => {
    if (url.endsWith('/nodes')) {
      return jsonResponse(200, [{ node_id: 'gate', node_type: 'APPROVAL' }]);
    }
    if (url.endsWith('/approvals/gate')) {
      return jsonResponse(502, { detail: 'x' }, false);
    }
    return jsonResponse(200, { workflow_run_id: 'r1' });
  });

  await assert.rejects(
    () => readRunBundle('r1', { fetchImpl: impl }),
    (error) => {
      assert.equal(error.category, 'projection');
      return true;
    }
  );
});

test('导出面只读：不存在任何写 / 执行方法', async () => {
  const module = await import('../js/api-client.js');
  const forbidden = [
    'postJson',
    'putJson',
    'deleteJson',
    'start',
    'advance',
    'cancel',
    'requestCancel',
    'decide',
    'submitDecision',
    'retry',
    'resume',
  ];
  for (const name of forbidden) {
    assert.equal(
      Object.prototype.hasOwnProperty.call(module, name),
      false,
      `不得导出写操作: ${name}`
    );
  }
});
