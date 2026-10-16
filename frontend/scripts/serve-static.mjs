/**
 * 本地静态文件服务（**仅**用于查看页面外壳；零依赖，Node 内置 http/fs）。
 *
 * 硬性边界（Phase 7 Workflow UI Step 1 §五）：
 *
 *   1. **只**绑定回环地址（`127.0.0.1`）——不监听外部网卡；显式拒绝非回环
 *      的 `--host`，避免把页面暴露给任意网络访问者；
 *   2. **不代理任何 API 请求**：本服务不转发 `/api/*` 到后端、**不注入**
 *      `X-API-Key`、不读取任何后端凭据。它只是一个静态文件服务器。
 *      （任务书明确禁止"为让 UI 可用而建立一个未受保护的代理"。）
 *   3. 因此浏览器在此页面发起的 `/api/...` 请求会打到**静态服务本身**
 *      （而非后端）→ 返回 404 / 非 JSON → 页面显示 `invalidPayload` 错误状态。
 *      这是**预期**的：真实数据访问尚未接通（见 index.html 顶部提示与阶段报告）。
 *   4. 不做目录列目录、不做重定向拼接；路径穿越一律 403。
 *
 * 用法：`node scripts/serve-static.mjs [--port 5173]`
 */
/* eslint-env node */

import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { extname, join, normalize, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { dirname } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = normalize(join(HERE, '..'));

const DEFAULT_PORT = 5173;
const LOOPBACK = '127.0.0.1';

const MIME = Object.freeze({
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
});

/** 解析 `--port`；`--host` 只接受回环（其余一律拒绝启动）。 */
export function parseArgs(argv) {
  let port = DEFAULT_PORT;
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--port' && argv[i + 1]) {
      const parsed = Number.parseInt(argv[i + 1], 10);
      if (!Number.isInteger(parsed) || parsed < 1 || parsed > 65535) {
        throw new Error('--port 必须是 1..65535 的整数');
      }
      port = parsed;
      i += 1;
    } else if (argv[i] === '--host') {
      throw new Error(
        '本服务只允许绑定回环地址（127.0.0.1）；不接受 --host。' +
          '如需远程访问，请改用受信任的反向代理与会话化读取路径（见阶段报告）。'
      );
    }
  }
  return { port, host: LOOPBACK };
}

/**
 * 把 URL 路径解析为 ROOT 下的安全文件路径。
 * @param {string} urlPath
 * @returns {string|null} 非法（穿越 / 空）⇒ null
 */
export function resolvePath(urlPath) {
  const decoded = decodeURIComponent(urlPath.split('?')[0].split('#')[0]);
  const relative = decoded === '/' ? '/index.html' : decoded;
  const target = normalize(join(ROOT, relative));
  if (target !== ROOT && !target.startsWith(ROOT + sep)) {
    return null; // 路径穿越
  }
  return target;
}

async function send(res, status, body, headers = {}) {
  res.writeHead(status, { 'Cache-Control': 'no-store', ...headers });
  res.end(body);
}

async function handle(req, res) {
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    await send(res, 405, 'Method Not Allowed', { Allow: 'GET, HEAD' });
    return;
  }
  // 明确不处理 API：本服务不是代理，也不持有任何后端凭据
  // （代码中**不出现**任何凭据头名称——避免被误认为凭据处理方）
  if (req.url && req.url.startsWith('/api/')) {
    await send(
      res,
      501,
      'Not Implemented — 本静态服务不代理 API，也不持有后端凭据。',
      { 'Content-Type': 'text/plain; charset=utf-8' }
    );
    return;
  }

  let filePath;
  try {
    filePath = resolvePath(req.url || '/');
  } catch {
    await send(res, 400, 'Bad Request');
    return;
  }
  if (!filePath) {
    await send(res, 403, 'Forbidden');
    return;
  }

  try {
    const info = await stat(filePath);
    if (info.isDirectory()) {
      await send(res, 403, 'Forbidden'); // 不做目录列目录
      return;
    }
    const body = await readFile(filePath);
    await send(res, 200, body, {
      'Content-Type': MIME[extname(filePath)] || 'application/octet-stream',
    });
  } catch {
    await send(res, 404, 'Not Found');
  }
}

export function createStaticServer() {
  return createServer((req, res) => {
    handle(req, res).catch(() => {
      send(res, 500, 'Internal Error').catch(() => {});
    });
  });
}

const invokedDirectly =
  process.argv[1] && normalize(process.argv[1]) === normalize(fileURLToPath(import.meta.url));

if (invokedDirectly) {
  const { host, port } = parseArgs(process.argv.slice(2));
  createStaticServer().listen(port, host, () => {
    console.log(
      `静态页面服务：http://${host}:${port}/  ` +
        '（只读页面外壳；不代理 /api，不持有任何后端凭据）'
    );
  });
}
