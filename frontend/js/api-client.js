/**
 * Workflow UI Read API client —— **只读、同源、会话 Cookie、零凭证**。
 *
 * Phase 7 Workflow UI Step 2：接入**受保护**的浏览器只读面
 * （`backend/app/api/workflow_ui_read.py`）：
 *
 * ```text
 * GET /api/ui/projects/{project_id}/workflow-runs/{run_id}
 * GET /api/ui/projects/{project_id}/workflow-runs/{run_id}/nodes
 * GET /api/ui/projects/{project_id}/workflow-runs/{run_id}/timeline
 * GET /api/ui/projects/{project_id}/workflow-runs/{run_id}/approvals/{node_id}
 * GET /api/ui/projects/{project_id}/workflow-approvals/{approval_id}
 * ```
 *
 * 认证与授权**完全由服务端**执行（M1 会话 Cookie + M2.4 项目成员授权）；
 * 客户端**不参与**授权决策：
 *
 *   * **无任何服务端密钥**：不读取 / 不构造 `X-API-Key`，不写
 *     `localStorage` / `sessionStorage` / `document.cookie`；
 *   * ``credentials: 'same-origin'``：由浏览器按同源语义自动携带
 *     **HttpOnly 会话 Cookie**（JS 无法读取其内容，也无需读取）；
 *   * 路径中的 ``project_id`` 仅是**请求目标**——服务端会独立校验
 *     "该会话用户是否是该项目的成员"，**不**采信客户端声明；
 *   * 只有 `GET`（`readJson` 无 `method` 形参）⇒ 结构上不可能发写请求；
 *   * 同源相对路径（`API_BASE = ''`）；不接受任意后端主机；不把认证信息
 *     放进 URL（无 token / key 查询参数）。
 */
/* eslint-env browser */

/** 同源相对基址（**空值**：只允许同源；不得改成携带凭证的绝对地址）。 */
export const API_BASE = '';

/**
 * 真实存在的五个 UI 只读端点（与 `backend/app/api/workflow_ui_read.py` 一致）。
 *
 * 每行一个**完整**模板字面量：便于静态审计（路径不会被跨行拼接拆开）。
 * 路径参数一律经 `encodeURIComponent` 转义。
 */
export const READ_ROUTES = Object.freeze({
  runDetail: (projectId, runId) => `/api/ui/projects/${encodeURIComponent(projectId)}/workflow-runs/${encodeURIComponent(runId)}`,
  runNodes: (projectId, runId) => `/api/ui/projects/${encodeURIComponent(projectId)}/workflow-runs/${encodeURIComponent(runId)}/nodes`,
  runTimeline: (projectId, runId) => `/api/ui/projects/${encodeURIComponent(projectId)}/workflow-runs/${encodeURIComponent(runId)}/timeline`,
  runGraph: (projectId, runId) => `/api/ui/projects/${encodeURIComponent(projectId)}/workflow-runs/${encodeURIComponent(runId)}/graph`,
  runApprovalByNode: (projectId, runId, nodeId) => `/api/ui/projects/${encodeURIComponent(projectId)}/workflow-runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(nodeId)}`,
  approvalById: (projectId, approvalId) => `/api/ui/projects/${encodeURIComponent(projectId)}/workflow-approvals/${encodeURIComponent(approvalId)}`,
});

/**
 * Graph 拓扑状态（与后端 ``WORKFLOW_GRAPH_STATUSES`` 逐字一致）。
 *
 * 非 ``available`` 时后端返回空 ``nodes`` ⇒ 前端**不**画任何边
 * （不用可能已变化的拓扑冒充历史版本的拓扑）。
 */
export const GRAPH_STATUS = Object.freeze({
  AVAILABLE: 'available',
  NOT_REGISTERED: 'not_registered',
  VERSION_MISMATCH: 'version_mismatch',
});

/**
 * 错误类别 —— 与后端**真实**状态码契约一一对应（不得臆造映射）。
 *
 * ```text
 * unauthorized    401  无会话 / 会话过期或吊销（需登录）
 * forbidden       403  已认证但**不是**该项目成员（M2.4 项目授权）
 * notFound        404  资源不存在 / 不属于该项目（同构，不区分）
 * invalid         400 / 422  参数非法 / 长度越界
 * projection      502  持久化数据无法安全投影（**不由前端修复**）
 * unavailable     503  会话服务 / 授权存储不可用（fail closed）
 * server          5xx  其它服务端错误
 * invalidPayload  2xx 但响应不是合法 JSON / 形状不符
 * network         网络中断 / 请求被中止
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
  unauthorized: '需要登录：当前没有有效会话，请先登录后再查询。',
  forbidden: '无权访问：当前账号不是该项目的成员，无法读取该项目的 Workflow 数据。',
  notFound: '未找到该资源：Run 不存在，或不属于所选项目。',
  invalid: '请求参数非法（项目 ID 或 Run ID 为空 / 长度越界）。',
  projection:
    '持久化数据无法安全投影（后端 fail closed）。数据不会被修复或截断，请检查运行记录。',
  unavailable: '读取服务不可用（会话服务或授权存储异常）。请稍后重试。',
  server: '读取失败：服务端错误。可稍后重试。',
  invalidPayload: '响应格式不符合预期，已拒绝展示该数据。',
  network: '网络错误：无法连接到读取服务。可稍后重试。',
});

/** 会话过期（曾成功加载过，随后 401）时的文案——与"从未登录"区分。 */
export const SESSION_EXPIRED_MESSAGE =
  '会话已过期或被吊销：请重新登录后再查询。';

/** 读取错误（携带**类别**与 HTTP 状态；不携带响应正文的原始内容）。 */
export class ReadError extends Error {
  /**
   * @param {string} category ERROR_CATEGORIES 之一
   * @param {number} status HTTP 状态码（网络错误为 0）
   * @param {'login-required'|'session-expired'|''} [hint] 仅 401 使用
   */
  constructor(category, status, hint = '') {
    super(
      hint === 'session-expired' ? SESSION_EXPIRED_MESSAGE : ERROR_MESSAGES[category] || '读取失败。'
    );
    this.name = 'ReadError';
    this.category = category;
    this.status = status;
    this.hint = hint;
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
      // 同源时才由浏览器携带 HttpOnly 会话 Cookie（JS 不接触其内容）
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
 * 读取 Run 详情页所需的**全部**只读数据（同一项目作用域内）。
 *
 * 审批按 Node 单独读取：只有真实存在的 APPROVAL 节点才发起该请求
 * （`node_type === 'APPROVAL'`），避免对每个节点盲目打请求。
 *
 * @param {string} projectId 项目作用域（请求目标；由服务端独立授权校验）
 * @param {string} runId
 * @param {{ fetchImpl?: Function, signal?: AbortSignal }} [options]
 * @returns {Promise<{run: unknown, nodes: unknown, timeline: unknown,
 *   approvalsByNode: Record<string, unknown>}>}
 */
export async function readRunBundle(projectId, runId, options = {}) {
  const [run, nodes, timeline, graph] = await Promise.all([
    readJson(READ_ROUTES.runDetail(projectId, runId), options),
    readJson(READ_ROUTES.runNodes(projectId, runId), options),
    readJson(READ_ROUTES.runTimeline(projectId, runId), options),
    readJson(READ_ROUTES.runGraph(projectId, runId), options),
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
          READ_ROUTES.runApprovalByNode(projectId, runId, nodeId),
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

  return { run, nodes: list, timeline, graph, approvalsByNode };
}
