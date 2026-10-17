/**
 * **本地开发专用**同源网关（Phase 7 Step 6；零依赖，Node 内置 http/fs）。
 *
 * 作用：把「静态页面」与「`/api/*`」放到**同一个 Origin** 下，使浏览器可以用
 * 既有 **M1 会话 Cookie**（`HttpOnly` / `SameSite=Lax`）访问真实 Workflow UI
 * 只读面。它解决的是**同源**问题，**不是**认证问题。
 *
 * ```text
 * Browser ──▶ http://127.0.0.1:<port>/            → 静态页面（白名单）
 *          └─▶ http://127.0.0.1:<port>/api/...    → 转发到 127.0.0.1:<apiPort>
 * ```
 *
 * ## 硬性安全边界（本文件的设计约束）
 *
 *  1. **仅回环**：只绑定 `127.0.0.1`；`--host` 一律拒绝；`--api-host` 必须是
 *     回环（否则拒绝启动）⇒ 不可能把该面暴露给外部网络。
 *  2. **不是认证旁路**：不校验、不注入、不剥离任何认证头。`Cookie` /
 *     `X-CSRF-Token` 等原样透传；**不**注入 `X-API-Key` 或任何服务端凭据
 *     （代码中不出现任何凭据头名称）。认证与项目授权**完全**由后端
 *     （M1 `require_session` + M2.4 `enforce_project_access`）执行——
 *     未认证请求仍然得到 401，未授权项目仍然得到 403。
 *  3. **不开放 CORS**：不写任何 `Access-Control-*` 头（同源即无需 CORS）。
 *  4. **路径白名单**：只转发 `/api/ui/` 与 `/api/auth/`（后者用于登录/登出）；
 *     其余 `/api/*` 一律 404（不转发 Agent / Chat / Tool / 命令写接口）。
 *  5. **方法白名单**：`/api/ui/*` 仅 `GET`/`HEAD`；`/api/auth/*` 允许
 *     `GET`/`HEAD`/`POST`（登录需要 POST）。其余方法 405。
 *  6. **不做生产用**：`NODE_ENV=production` 时拒绝启动；日志首行固定打印
 *     dev-only 警告。生产必须使用 **HTTPS + 受控网关**（见
 *     `docs/security.md` §7.1 与 `frontend/README.md` §6.3）。
 *  7. 不跟随重定向、不缓存（`no-store`）、不做压缩改写。
 *
 * 用法：
 *
 * ```powershell
 * node scripts/dev-gateway.mjs --port 8080 --api-port 8000
 * # → http://127.0.0.1:8080/index.html
 * ```
 */
/* eslint-env node */

import { createServer, request as httpRequest } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { dirname, extname, join, normalize, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = normalize(join(HERE, '..'));

const DEFAULT_PORT = 8080;
const DEFAULT_API_PORT = 8000;
const LOOPBACK = '127.0.0.1';
const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '::1']);

/** 仅这些**静态**路径可被服务（白名单；不含 tests / e2e / scripts / node_modules）。 */
const STATIC_ALLOWLIST = Object.freeze([
  '/index.html',
  '/styles.css',
]);

const STATIC_PREFIX_ALLOWLIST = Object.freeze(['/js/']);

/**
 * API 转发白名单：前缀 → 允许的方法集合。
 *
 * 只有工作台真正需要的两个前缀会被转发（登录用 `/api/auth/`，读数据用
 * `/api/ui/`）。Agent / Chat / Tool / Workflow 命令写接口**不转发**。
 */
const API_ALLOWLIST = Object.freeze([
  { prefix: '/api/ui/', methods: new Set(['GET', 'HEAD']) },
  { prefix: '/api/auth/', methods: new Set(['GET', 'HEAD', 'POST']) },
]);

const MIME = Object.freeze({
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
});

/** 逐跳头（RFC 7230 §6.1）——转发时必须剔除。 */
const HOP_BY_HOP = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
]);

/**
 * 解析参数；任何越界（非回环 host / 非法端口）⇒ 抛错（调用方退出）。
 * @param {string[]} argv
 * @returns {{port: number, apiPort: number, apiHost: string}}
 */
export function parseArgs(argv) {
  let port = DEFAULT_PORT;
  let apiPort = DEFAULT_API_PORT;
  let apiHost = LOOPBACK;

  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    const value = argv[i + 1];
    if (flag === '--host') {
      throw new Error(
        '本网关只允许绑定回环地址（127.0.0.1）；不接受 --host。' +
          '生产部署必须使用 HTTPS + 受控网关（见 frontend/README.md §6.3）。'
      );
    }
    if (flag === '--port' || flag === '--api-port') {
      const parsed = Number.parseInt(value, 10);
      if (!Number.isInteger(parsed) || parsed < 1 || parsed > 65535) {
        throw new Error(`${flag} 必须是 1..65535 的整数`);
      }
      if (flag === '--port') port = parsed;
      else apiPort = parsed;
      i += 1;
      continue;
    }
    if (flag === '--api-host') {
      if (!LOOPBACK_HOSTS.has(String(value))) {
        throw new Error(
          `--api-host 必须是回环地址（127.0.0.1 / localhost / ::1）；` +
            `实际收到 ${String(value)}`
        );
      }
      apiHost = LOOPBACK;
      i += 1;
    }
  }
  return { port, apiPort, apiHost };
}

/**
 * 静态白名单判定。
 * @param {string} pathname
 * @returns {boolean}
 */
export function isStaticAllowed(pathname) {
  return (
    STATIC_ALLOWLIST.includes(pathname) ||
    STATIC_PREFIX_ALLOWLIST.some((prefix) => pathname.startsWith(prefix))
  );
}

/**
 * API 白名单判定（前缀 + 方法）。
 * @param {string} pathname
 * @param {string} method
 * @returns {{allowed: boolean, reason?: string}}
 */
export function checkApiAllowed(pathname, method) {
  const entry = API_ALLOWLIST.find((item) => pathname.startsWith(item.prefix));
  if (!entry) {
    return { allowed: false, reason: 'path-not-allowed' };
  }
  if (!entry.methods.has(method)) {
    return { allowed: false, reason: 'method-not-allowed' };
  }
  return { allowed: true };
}

/**
 * 把 URL 路径解析为 ROOT 下的安全文件路径（防穿越）。
 * @param {string} urlPath
 * @returns {string|null}
 */
export function resolvePath(urlPath) {
  const decoded = decodeURIComponent(urlPath.split('?')[0].split('#')[0]);
  const target = normalize(join(ROOT, decoded));
  if (target !== ROOT && !target.startsWith(ROOT + sep)) return null;
  return target;
}

function sendText(res, status, text, headers = {}) {
  res.writeHead(status, {
    'Content-Type': 'text/plain; charset=utf-8',
    'Cache-Control': 'no-store',
    ...headers,
  });
  res.end(text);
}

/**
 * 过滤逐跳头（保留 `cookie` / `x-csrf-token` 等**原样**透传）。
 * @param {Record<string, string|string[]|undefined>} headers
 * @returns {Record<string, string|string[]>}
 */
export function filterHeaders(headers) {
  const out = {};
  for (const [name, value] of Object.entries(headers)) {
    if (value === undefined) continue;
    if (HOP_BY_HOP.has(name.toLowerCase())) continue;
    out[name] = value;
  }
  return out;
}

/**
 * 转发到后端（**不加任何认证头**、不跟随重定向、不缓存）。
 * @param {import('node:http').IncomingMessage} req
 * @param {import('node:http').ServerResponse} res
 * @param {{apiPort: number, apiHost: string}} target
 */
function proxy(req, res, target) {
  const headers = filterHeaders(req.headers);
  headers.host = `${target.apiHost}:${target.apiPort}`;

  const upstream = httpRequest(
    {
      host: target.apiHost,
      port: target.apiPort,
      method: req.method,
      path: req.url,
      headers,
    },
    (upstreamRes) => {
      const outHeaders = filterHeaders(upstreamRes.headers);
      // 同源：**不**添加任何 Access-Control-* 头
      outHeaders['cache-control'] = 'no-store';
      res.writeHead(upstreamRes.statusCode || 502, outHeaders);
      upstreamRes.pipe(res);
    }
  );

  upstream.on('error', (error) => {
    // 不泄漏内部细节（仅回环地址与结果）
    console.error(
      `[dev-gateway] 后端不可用：${target.apiHost}:${target.apiPort} ` +
        `(${error.code || 'ERROR'})`
    );
    if (!res.headersSent) {
      sendText(res, 502, 'Bad Gateway — 本地后端未运行（dev-gateway 非生产组件）');
    } else {
      res.end();
    }
  });

  req.pipe(upstream);
}

async function serveStatic(req, res, pathname) {
  if (!isStaticAllowed(pathname)) {
    sendText(res, 404, 'Not Found');
    return;
  }
  const filePath = resolvePath(pathname);
  if (!filePath) {
    sendText(res, 403, 'Forbidden');
    return;
  }
  try {
    const info = await stat(filePath);
    if (info.isDirectory()) {
      sendText(res, 403, 'Forbidden');
      return;
    }
    const body = await readFile(filePath);
    res.writeHead(200, {
      'Content-Type': MIME[extname(filePath)] || 'application/octet-stream',
      'Cache-Control': 'no-store',
      // 与 index.html 内 CSP 一致的保守来源限制（由页面自身声明，此处不重复）
    });
    res.end(req.method === 'HEAD' ? undefined : body);
  } catch {
    sendText(res, 404, 'Not Found');
  }
}

/**
 * 创建网关服务器（**只**监听回环，由调用方 listen 到 LOOPBACK）。
 * @param {{apiPort: number, apiHost: string}} target
 * @returns {import('node:http').Server}
 */
export function createGatewayServer(target) {
  return createServer((req, res) => {
    const pathname = (req.url || '/').split('?')[0];
    const method = (req.method || 'GET').toUpperCase();

    if (pathname === '/' || pathname === '') {
      serveStatic(req, res, '/index.html').catch(() => {
        sendText(res, 500, 'Internal Error');
      });
      return;
    }

    if (pathname.startsWith('/api/')) {
      const { allowed, reason } = checkApiAllowed(pathname, method);
      if (!allowed) {
        if (reason === 'method-not-allowed') {
          // 路径在允许前缀内、但方法不允许 ⇒ 405（语义正确）
          sendText(res, 405, 'Method Not Allowed', { Allow: 'GET, HEAD' });
          return;
        }
        // 白名单外的 API 路径**不转发**（404 而非 403，避免泄漏存在性）
        sendText(res, 404, `Not Found — dev-gateway 不转发该 API（${reason}）`);
        return;
      }
      proxy(req, res, target);
      return;
    }

    if (method !== 'GET' && method !== 'HEAD') {
      sendText(res, 405, 'Method Not Allowed', { Allow: 'GET, HEAD' });
      return;
    }
    serveStatic(req, res, pathname).catch(() => {
      sendText(res, 500, 'Internal Error');
    });
  });
}

const invokedDirectly =
  process.argv[1] &&
  normalize(process.argv[1]) === normalize(fileURLToPath(import.meta.url));

if (invokedDirectly) {
  if ((process.env.NODE_ENV || '').toLowerCase() === 'production') {
    console.error(
      '拒绝启动：dev-gateway 是**本地开发**组件，不得用于生产。' +
        '生产必须使用 HTTPS + 受控网关。'
    );
    process.exit(1);
  }

  let args;
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (error) {
    console.error(`启动失败：${error.message}`);
    process.exit(1);
  }

  const server = createGatewayServer(args);
  server.listen(args.port, LOOPBACK, () => {
    console.log(
      '⚠ 本地开发专用（dev-only）：本网关不得用于生产；' +
        '生产必须使用 HTTPS + 受控网关终结 TLS 并同源提供页面与 /api。'
    );
    console.log(
      `同源网关：http://${LOOPBACK}:${args.port}/  ` +
        `（静态页面 + /api/ui/ · /api/auth/ → ` +
        `http://${args.apiHost}:${args.apiPort}）`
    );
    console.log(
      '认证与项目授权**完全**由后端执行；本网关不注入、不剥离任何认证头，' +
        '也不开放 CORS。'
    );
  });
}
