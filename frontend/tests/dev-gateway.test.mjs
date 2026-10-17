/**
 * dev-gateway 安全边界测试（Phase 7 Step 6）—— `node --test`（零依赖）。
 *
 * 只测**纯逻辑**（参数解析 / 白名单 / 头过滤），不启动服务器：
 * 网关是本地开发组件，其安全性质必须可被静态与单元断言锁定。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  checkApiAllowed,
  filterHeaders,
  isStaticAllowed,
  parseArgs,
} from '../scripts/dev-gateway.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const SOURCE = readFileSync(
  join(HERE, '..', 'scripts', 'dev-gateway.mjs'),
  'utf8'
);

// ============================================================
// 1. 绑定与上游：只允许回环
// ============================================================

test('拒绝 --host（不可能绑到外部网卡）', () => {
  for (const value of ['0.0.0.0', '::', '192.168.1.10', 'example.com']) {
    assert.throws(() => parseArgs(['--host', value]), /只允许绑定回环/, value);
  }
});

test('--api-host 必须是回环，非回环一律拒绝', () => {
  assert.throws(
    () => parseArgs(['--api-host', '10.0.0.5']),
    /必须是回环地址/
  );
  assert.throws(
    () => parseArgs(['--api-host', 'api.example.com']),
    /必须是回环地址/
  );
  // 回环值被规范化为 127.0.0.1
  assert.equal(parseArgs(['--api-host', 'localhost']).apiHost, '127.0.0.1');
});

test('默认端口与自定义端口', () => {
  assert.deepEqual(parseArgs([]), {
    port: 8080,
    apiPort: 8000,
    apiHost: '127.0.0.1',
  });
  assert.deepEqual(parseArgs(['--port', '9090', '--api-port', '18000']), {
    port: 9090,
    apiPort: 18000,
    apiHost: '127.0.0.1',
  });
});

test('非法端口被拒绝', () => {
  for (const bad of ['0', '70000', 'abc', '-1']) {
    assert.throws(() => parseArgs(['--port', bad]), /1\.\.65535/, bad);
  }
});

// ============================================================
// 2. 静态白名单：不暴露 tests / e2e / scripts / 依赖
// ============================================================

test('静态白名单只含页面资源', () => {
  for (const allowed of [
    '/index.html',
    '/styles.css',
    '/js/app.js',
    '/js/api-client.js',
  ]) {
    assert.equal(isStaticAllowed(allowed), true, allowed);
  }
});

test('静态白名单拒绝非页面资源（含测试与依赖目录）', () => {
  for (const denied of [
    '/package.json',
    '/package-lock.json',
    '/README.md',
    '/tests/state.test.mjs',
    '/e2e/workbench.spec.js',
    '/scripts/serve-static.mjs',
    '/node_modules/some-package/package.json',
    '/test-results/x.png',
    '/.gitignore',
  ]) {
    assert.equal(isStaticAllowed(denied), false, denied);
  }
});

// ============================================================
// 3. API 白名单：只转发会话面，不转发写/服务间面
// ============================================================

test('只转发 /api/ui/ 与 /api/auth/ 前缀', () => {
  assert.equal(checkApiAllowed('/api/ui/projects/p/workflow-runs/r', 'GET').allowed, true);
  assert.equal(checkApiAllowed('/api/ui/projects/p/workflow-runs/r/nodes', 'GET').allowed, true);
  assert.equal(checkApiAllowed('/api/auth/login', 'POST').allowed, true);

  for (const denied of [
    '/api/workflow-runs/wf-1',              // 服务间 X-API-Key 面
    '/api/workflow-runs/wf-1/nodes',
    '/api/workflow-approvals/ap-1',
    '/api/workflow-definitions',
    '/api/agent/runs',
    '/api/chat',
    '/api/tools',
    '/api/observability/llm-usage',
  ]) {
    assert.equal(checkApiAllowed(denied, 'GET').allowed, false, denied);
  }
});

test('/api/ui/* 只允许 GET/HEAD（无任何写方法）', () => {
  const path = '/api/ui/projects/p/workflow-runs/r';
  for (const method of ['GET', 'HEAD']) {
    assert.equal(checkApiAllowed(path, method).allowed, true, method);
  }
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
    const result = checkApiAllowed(path, method);
    assert.equal(result.allowed, false, method);
    assert.equal(result.reason, 'method-not-allowed', method);
  }
});

test('/api/auth/* 只允许 GET/HEAD/POST（登录需要 POST）', () => {
  for (const method of ['GET', 'HEAD', 'POST']) {
    assert.equal(checkApiAllowed('/api/auth/login', method).allowed, true, method);
  }
  for (const method of ['PUT', 'PATCH', 'DELETE']) {
    assert.equal(checkApiAllowed('/api/auth/login', method).allowed, false, method);
  }
});

// ============================================================
// 4. 头处理：原样透传认证信息，不加任何认证头
// ============================================================

test('透传 Cookie 与 CSRF 头（不剥离、不改写）', () => {
  const filtered = filterHeaders({
    cookie: 'wms_ai_session=abc',
    'x-csrf-token': 'tok',
    accept: 'application/json',
  });

  assert.equal(filtered.cookie, 'wms_ai_session=abc');
  assert.equal(filtered['x-csrf-token'], 'tok');
  assert.equal(filtered.accept, 'application/json');
  assert.deepEqual(Object.keys(filtered).sort(), [
    'accept',
    'cookie',
    'x-csrf-token',
  ]);
});

test('剔除逐跳头（连接语义不跨转发链）', () => {
  const filtered = filterHeaders({
    connection: 'keep-alive',
    'transfer-encoding': 'chunked',
    upgrade: 'websocket',
    'proxy-authorization': 'Basic xxx',
    host: '127.0.0.1:8080',
    cookie: 'c=1',
  });

  for (const hop of [
    'connection',
    'transfer-encoding',
    'upgrade',
    'proxy-authorization',
  ]) {
    assert.equal(hop in filtered, false, hop);
  }
  assert.equal(filtered.host, '127.0.0.1:8080'); // host 由调用方改为上游
  assert.equal(filtered.cookie, 'c=1');          // 认证信息不被剥离
});

// ============================================================
// 5. 源码级约束（不开放 CORS / 不持有凭据 / 生产拒绝）
// ============================================================

test('网关**不**添加任何 CORS 头', () => {
  assert.doesNotMatch(SOURCE, /Access-Control-Allow-/);
  assert.doesNotMatch(SOURCE, /access-control-allow-/i);
});

test('网关不注入任何服务端凭据头', () => {
  // 代码中不得出现凭据头名的**赋值**（注释中的说明性提及允许）
  const code = SOURCE.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(
    /^\s*\*.*$/gm,
    ' '
  );
  assert.doesNotMatch(code, /['"]?x-api-key['"]?\s*[:=]/i);
  assert.doesNotMatch(code, /authorization\s*:/i);
});

test('生产环境拒绝启动（dev-only 硬约束）', () => {
  assert.match(SOURCE, /NODE_ENV/);
  assert.match(SOURCE, /不得用于生产/);
  assert.match(SOURCE, /process\.exit\(1\)/);
});

test('启动横幅声明 dev-only 与 HTTPS 生产要求', () => {
  assert.match(SOURCE, /本地开发专用/);
  assert.match(SOURCE, /HTTPS \+ 受控网关/);
});
