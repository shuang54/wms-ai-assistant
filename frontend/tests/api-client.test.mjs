/**
 * API client 测试 —— `node --test`（Node 内置，**零依赖**）。
 *
 * Phase 7 Workflow UI Step 2：客户端改为调用**受保护**的 UI 只读面
 * `/api/ui/projects/{project_id}/...`，认证靠同源 **HttpOnly 会话 Cookie**，
 * 授权由服务端（M2.4 项目成员）判定。
 *
 * 覆盖（Step 2 §七.C 1/2/3 + §八）：
 *   路径与真实路由一致 · 参数转义 · 只发 GET · **不携带任何服务端密钥** ·
 *   同源相对基址 + `credentials: 'same-origin'` · 状态码 → 错误类别映射 ·
 *   401（需登录）/ 403（非成员）/ 404 / 502 · 非法 JSON · 网络错误 ·
 *   审批 `null` 语义 · 只读面（无写方法导出）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  API_BASE,
  ERROR_CATEGORIES,
  READ_ROUTES,
  ReadError,
  SESSION_EXPIRED_MESSAGE,
  categorizeStatus,
  readJson,
  readRunBundle,
} from '../js/api-client.js';

const PROJECT = 'proj-a';
const RUN = 'wf-run-1';

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

test('路由与后端真实 UI 路径一致且参数已转义', () => {
  assert.equal(
    READ_ROUTES.runDetail(PROJECT, RUN),
    `/api/ui/projects/${PROJECT}/workflow-runs/${RUN}`
  );
  assert.equal(
    READ_ROUTES.runNodes(PROJECT, RUN),
    `/api/ui/projects/${PROJECT}/workflow-runs/${RUN}/nodes`
  );
  assert.equal(
    READ_ROUTES.runTimeline(PROJECT, RUN),
    `/api/ui/projects/${PROJECT}/workflow-runs/${RUN}/timeline`
  );
  assert.equal(
    READ_ROUTES.runApprovalByNode(PROJECT, RUN, 'gate'),
    `/api/ui/projects/${PROJECT}/workflow-runs/${RUN}/approvals/gate`
  );
  assert.equal(
    READ_ROUTES.approvalById(PROJECT, 'ap-1'),
    `/api/ui/projects/${PROJECT}/workflow-approvals/ap-1`
  );

  // 转义：路径穿越 / 注入字符不得被直接拼接
  assert.equal(
    READ_ROUTES.runDetail('p/../q', RUN),
    '/api/ui/projects/p%2F..%2Fq/workflow-runs/wf-run-1'
  );
  assert.equal(
    READ_ROUTES.runApprovalByNode(PROJECT, RUN, 'x y?z=1'),
    `/api/ui/projects/${PROJECT}/workflow-runs/${RUN}/approvals/x%20y%3Fz%3D1`
  );
});

test('路径中不出现任何凭据（无 key / token 查询参数）', () => {
  const paths = [
    READ_ROUTES.runDetail(PROJECT, RUN),
    READ_ROUTES.runNodes(PROJECT, RUN),
    READ_ROUTES.runTimeline(PROJECT, RUN),
    READ_ROUTES.runGraph(PROJECT, RUN),
    READ_ROUTES.runApprovalByNode(PROJECT, RUN, 'gate'),
    READ_ROUTES.approvalById(PROJECT, 'ap-1'),
  ];
  for (const path of paths) {
    assert.equal(path.includes('?'), false, path);
    assert.equal(/api[-_]?key|token|secret|auth/i.test(path), false, path);
  }
  // Step 4：graph 路径同源相对
  assert.equal(
    READ_ROUTES.runGraph(PROJECT, RUN),
    `/api/ui/projects/${PROJECT}/workflow-runs/${RUN}/graph`
  );
});

test('readJson 只发 GET、不带凭据、不使用任何请求体', async () => {
  const { impl, calls } = makeFetch(() => jsonResponse(200, { ok: true }));

  const body = await readJson('/api/ui/projects/p/workflow-runs/r', {
    fetchImpl: impl,
  });

  assert.deepEqual(body, { ok: true });
  assert.equal(calls.length, 1);
  const { url, init } = calls[0];
  assert.equal(url, '/api/ui/projects/p/workflow-runs/r'); // 相对 = 同源
  assert.equal(init.method, 'GET');
  assert.equal(init.body, undefined);
  assert.deepEqual(init.headers, { Accept: 'application/json' });
  // 同源时才由浏览器携带 HttpOnly 会话 Cookie（JS 不接触其内容）
  assert.equal(init.credentials, 'same-origin');

  // **绝无**任何凭据头
  const headerNames = Object.keys(init.headers).map((k) => k.toLowerCase());
  assert.deepEqual(headerNames, ['accept']);
  const serialized = JSON.stringify(init).toLowerCase();
  assert.equal(serialized.includes('api-key'), false);
  assert.equal(serialized.includes('authorization'), false);
  assert.equal(serialized.includes('bearer'), false);
  assert.equal(serialized.includes('x-api-key'), false);
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
  assert.equal(categorizeStatus(200), 'invalidPayload');
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
        assert.doesNotMatch(error.message, /SECRET-INTERNAL-DETAIL/);
        return true;
      }
    );
  }
});

test('401 文案指向"需要登录"（不暗示资源是否存在）', async () => {
  const { impl } = makeFetch(() => jsonResponse(401, {}, false));

  await assert.rejects(
    () => readJson('/x', { fetchImpl: impl }),
    (error) => {
      assert.match(error.message, /需要登录/);
      assert.doesNotMatch(error.message, /不存在|无权/);
      return true;
    }
  );
});

test('403 文案指向"非该项目成员"（授权失败）', async () => {
  const { impl } = makeFetch(() => jsonResponse(403, {}, false));

  await assert.rejects(
    () => readJson('/x', { fetchImpl: impl }),
    (error) => {
      assert.match(error.message, /不是该项目的成员/);
      return true;
    }
  );
});

test('会话过期文案可由 SESSION_EXPIRED_MESSAGE 表达', () => {
  assert.match(SESSION_EXPIRED_MESSAGE, /重新登录/);
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
        { kind: 'RUN_CREATED', timestamp: '2026-10-17T09:00:00Z', workflow_run_id: RUN },
      ]);
    }
    if (url.endsWith('/graph')) {
      return jsonResponse(200, {
        status: 'available',
        workflow_id: 'inventory_check',
        workflow_version: 'v1',
        definition_version: 'v1',
        start_node_id: 'query_stock',
        nodes: [
          {
            node_id: 'query_stock',
            node_type: 'TOOL',
            next_node_id: 'confirm_stock',
            on_true_node_id: null,
            on_false_node_id: null,
          },
        ],
      });
    }
    if (url.endsWith('/approvals/gate')) {
      return jsonResponse(200, { approval_id: 'ap-1', status: 'PENDING' });
    }
    return jsonResponse(200, { workflow_run_id: RUN });
  });

  const bundle = await readRunBundle(PROJECT, RUN, { fetchImpl: impl });

  assert.equal(bundle.run.workflow_run_id, RUN);
  assert.equal(bundle.nodes.length, 2);
  assert.ok(Array.isArray(bundle.timeline));
  // Step 4：Graph 拓扑一并拉取
  assert.equal(bundle.graph.status, 'available');
  assert.equal(bundle.graph.nodes.length, 1);
  assert.equal(bundle.graph.start_node_id, 'query_stock');
  assert.deepEqual(bundle.approvalsByNode, {
    gate: { approval_id: 'ap-1', status: 'PENDING' },
  });

  // 非 APPROVAL 节点不发审批请求（避免盲目请求）
  const approvalCalls = routes.filter((u) => u.includes('/approvals/'));
  assert.deepEqual(approvalCalls, [
    `/api/ui/projects/${PROJECT}/workflow-runs/${RUN}/approvals/gate`,
  ]);

  // 全部为 GET，且同一项目作用域、无凭据
  // Run + Nodes + Timeline + **Graph** + 审批
  assert.equal(routes.length, 5);
  assert.equal(routes.filter((u) => u.endsWith('/graph')).length, 1);
  for (const url of routes) {
    assert.ok(url.startsWith(`/api/ui/projects/${PROJECT}/`), url);
    assert.equal(url.includes('http://') || url.includes('https://'), false);
    assert.equal(url.includes('?'), false);
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
    if (url.endsWith('/graph')) {
      return jsonResponse(200, { status: 'not_registered', nodes: [] });
    }
    return jsonResponse(200, { workflow_run_id: RUN });
  });

  const bundle = await readRunBundle(PROJECT, RUN, { fetchImpl: impl });

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
    if (url.endsWith('/graph')) {
      return jsonResponse(200, { status: 'available', nodes: [] });
    }
    return jsonResponse(200, { workflow_run_id: RUN });
  });

  await assert.rejects(
    () => readRunBundle(PROJECT, RUN, { fetchImpl: impl }),
    (error) => {
      assert.equal(error.category, 'projection');
      return true;
    }
  );
});

test('readRunBundle：401 / 403 直接向上抛出（不降级为"空数据"）', async () => {
  for (const [status, category] of [
    [401, 'unauthorized'],
    [403, 'forbidden'],
  ]) {
    const { impl } = makeFetch(() => jsonResponse(status, {}, false));
    await assert.rejects(
      () => readRunBundle(PROJECT, RUN, { fetchImpl: impl }),
      (error) => {
        assert.equal(error.category, category);
        return true;
      }
    );
  }
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
