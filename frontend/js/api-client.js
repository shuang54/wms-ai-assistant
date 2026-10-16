/**
 * Workflow Read API client —— **只读、同源、零凭证**。
 *
 * 安全边界（**硬性**，Phase 7 Workflow UI Step 1 §五）：
 *
 *   * 本模块**没有任何地方**读取或发送 `X-API-Key`：请求只带 `Accept`；
 *   * 密钥**绝不**进入前端源码 / 打包产物 / `localStorage` /
 *     `sessionStorage` / Cookie；浏览器请求**绝不**携带服务端密钥；
 *   * `API_BASE` 固定为**空字符串**（同源相对路径）——不允许硬编码主机、
 *     不允许通过查询参数指定后端、不允许通过请求参数覆盖服务端
 *     `project_id`（后端本就不接受 `project_id` 参数）；
 *   * 只暴露 `readJson`（内部固定 `method: 'GET'`）——**不存在**任何写入 /
 *     执行方法（无 start / advance / cancel / decision / retry）；
 *   * `credentials: 'same-origin'`：仅在**同源**时携带浏览器自身的 Cookie
 *     （浏览器默认语义），绝不跨源发送；这不涉及任何密钥写入。
 *
 * 当前阻塞项（未接通真实数据，**如实标记**）：
 *   后端 Workflow Read API 使用**服务到服务**的 `X-API-Key`；仓库内没有
 *   受信任的服务端代理、没有面向浏览器的会话化读取路径、也没有 CORS 配置。
 *   因此本客户端在**真实**环境发出的请求会得到 `401`（缺少凭据）—— 这正是
 *   页面的 error 状态之一；页面**不会**为了"看起来能用"而绕过该边界。
 */
/* eslint-env browser */

/** 同源相对基址（**空值**：只允许同源；不得改成携带凭证的绝对地址）。 */
export const API_BASE = '';

/**
 * 真实存在的五个只读端点（与 `backend/app/api/workflow_read.py` 一致）。
 *
 * 路径参数一律经 `encodeURIComponent` 转义——不允许拼接未转义的用户输入。
 */
export const READ_ROUTES = Object.freeze({
  // 每行一个**完整**模板字面量：便于静态审计（路径不会被跨行拼接拆开）
  runDetail: (runId) => `/api/workflow-runs/${encodeURIComponent(runId)}`,
  runNodes: (runId) => `/api/workflow-runs/${encodeURIComponent(runId)}/nodes`,
  runTimeline: (runId) => `/api/workflow-runs/${encodeURIComponent(runId)}/timeline`,
  runApprovalByNode: (runId, nodeId) => `/api/workflow-runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(nodeId)}`,
  approvalById: (approvalId) => `/api/workflow-approvals/${encodeURIComponent(approvalId)}`,
});

/**
 * 错误类别 —— 与后端**真实**状态码契约一一对应（不得臆造映射）。
 *
 * ```
 * unauthorized  401  缺少 / 错误凭据（当前无安全浏览器路径 ⇒ 最常见结果）
 * forbidden     403  已认证但无权（当前读面不返回；保留契约位）
 * notFound      404  资源不存在 **或** 不属于授权项目（同构，不区分）
 * invalid       400 / 422  参数非法 / 长度越界
 * projection    502  持久化数据无法安全投影（**不由前端修复**）
 * unavailable   503  服务端认证 / 作用域未配置（fail closed）
 * server        5xx  其它服务端错误
 * invalidPayload 2xx 但响应不是合法 JSON / 形状不符
 * network       网络中断 / 请求被中止
 * ```
 */
export const ERROR_CATEGORIES = Object.freeze([
  'unauthorized',
  'forbidden',
  'notFound',
  'invalid',
  'projection',
  'unavailable',
  'server',
  'invalidPayload',
  'network',
]);

/** 面向用户的固定文案（**不**透出后端 detail 正文 / 堆栈 / SQL）。 */
export const ERROR_MESSAGES = Object.freeze({
  unauthorized: '无权访问：缺少或无效的访问凭据。',
  forbidden: '无权访问：当前身份没有读取该 Run 的权限。',
  notFound: '未找到该资源：Run 不存在，或不在当前读取授权范围内。',
  invalid: '请求参数非法（Run ID 为空或长度越界）。',
  projection:
    '持久化数据无法安全投影（后端 fail closed）。数据不会被修复或截断，请检查运行记录。',
  unavailable: '读取服务不可用（服务端未完成认证 / 作用域配置）。',
  server: '读取失败：服务端错误。可稍后重试。',
  invalidPayload: '响应格式不符合预期，已拒绝展示该数据。',
  network: '网络错误：无法连接到读取服务。可稍后重试。',
});

/** 读取错误（携带**类别**与 HTTP 状态；不携带响应正文的原始内容）。 */
export class ReadError extends Error {
  /**
   * @param {string} category ERROR_CATEGORIES 之一
   * @param {number} status HTTP 状态码（网络错误为 0）
   */
  constructor(category, status) {
    super(ERROR_MESSAGES[category] || '读取失败。');
    this.name = 'ReadError';
    this.category = category;
    this.status = status;
  }
}

/**
 * 把 HTTP 状态码映射为错误类别（**唯一**映射处，便于测试与审计）。
 * @param {number} status
 * @returns {string}
 */
export function categorizeStatus(status) {
  if (status === 401) return 'unauthorized';
  if (status === 403) return 'forbidden';
  if (status === 404) return 'notFound';
  if (status === 400 || status === 422) return 'invalid';
  if (status === 502) return 'projection';
  if (status === 503) return 'unavailable';
  if (status >= 500) return 'server';
  return 'invalidPayload';
}

/**
 * 只读 GET 并解析 JSON（**唯一**的出站请求函数；无 method 形参 ⇒ 结构上
 * 不可能发出写请求）。
 *
 * @param {string} path 同源相对路径（由 READ_ROUTES 构造）
 * @param {{ fetchImpl?: Function, signal?: AbortSignal }} [options]
 * @returns {Promise<unknown>} 解析后的 JSON（形状校验由调用方的 view-model 负责）
 * @throws {ReadError}
 */
export async function readJson(path, options = {}) {
  const fetchImpl = options.fetchImpl || globalThis.fetch;
  if (typeof fetchImpl !== 'function') {
    throw new ReadError('network', 0);
  }

  let response;
  try {
    response = await fetchImpl(API_BASE + path, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
      signal: options.signal,
    });
  } catch (_error) {
    // 不包装原始异常对象（避免把底层细节带进 UI 层）
    throw new ReadError('network', 0);
  }

  if (!response || typeof response.status !== 'number') {
    throw new ReadError('invalidPayload', 0);
  }
  if (!response.ok) {
    throw new ReadError(categorizeStatus(response.status), response.status);
  }

  try {
    return await response.json();
  } catch (_error) {
    throw new ReadError('invalidPayload', response.status);
  }
}

/**
 * 读取 Run 详情页所需的**全部**只读数据。
 *
 * 审批按 Node 单独读取：只有真实存在的 APPROVAL 节点才发起该请求
 * （`node_type === 'APPROVAL'`），避免对每个节点盲目打请求。
 *
 * @param {string} runId
 * @param {{ fetchImpl?: Function, signal?: AbortSignal }} [options]
 * @returns {Promise<{run: unknown, nodes: unknown, timeline: unknown,
 *   approvalsByNode: Record<string, unknown>}>}
 */
export async function readRunBundle(runId, options = {}) {
  const run = await readJson(READ_ROUTES.runDetail(runId), options);
  const [nodes, timeline] = await Promise.all([
    readJson(READ_ROUTES.runNodes(runId), options),
    readJson(READ_ROUTES.runTimeline(runId), options),
  ]);

  const approvalsByNode = {};
  const list = Array.isArray(nodes) ? nodes : [];
  const approvalNodeIds = [
    ...new Set(
      list
        .filter((item) => item && item.node_type === 'APPROVAL' && item.node_id)
        .map((item) => item.node_id)
    ),
  ];
  await Promise.all(
    approvalNodeIds.map(async (nodeId) => {
      try {
        approvalsByNode[nodeId] = await readJson(
          READ_ROUTES.runApprovalByNode(runId, nodeId),
          options
        );
      } catch (error) {
        // 该节点尚无审批行 ⇒ 后端 404；记录为"无审批"（**不**伪造成已通过）
        if (error instanceof ReadError && error.category === 'notFound') {
          approvalsByNode[nodeId] = null;
          return;
        }
        throw error;
      }
    })
  );

  return { run, nodes: list, timeline, approvalsByNode };
}
