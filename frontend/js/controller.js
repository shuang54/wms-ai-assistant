/**
 * Workflow Run Detail —— **纯控制器**（无 DOM、无浏览器 API）。
 *
 * 从 `app.js` 分离的目的：把"防重复提交、防竞态、会话/授权错误归类、
 * 状态筛选、Attempt ↔ Timeline 选中、输出复制结果"这些交互约束放在可被
 * `node --test` 直接覆盖的纯逻辑里。
 *
 * 只读铁律：控制器**只**调用注入的 `readRun(projectId, runId)`（生产 =
 * `readRunBundle`，内部固定 `GET` + 同源 Cookie）。不存在任何写操作入口，
 * 也不存在任何 Runtime / Tool / LLM 调用。
 *
 * 认证与授权**由服务端**决定；本层只把服务端结果映射为可展示的安全状态：
 *
 * ```text
 * 401 ⇒ 需要登录 / 会话已过期（若本会话曾成功加载过 ⇒ 过期）
 * 403 ⇒ 非该项目成员（M2.4 项目授权）
 * 404 ⇒ 资源不存在 / 不属于该项目（同构，不区分）
 * 502 ⇒ 持久化数据无法安全投影（不尝试修复）
 * 503 ⇒ 会话服务 / 授权存储不可用（fail closed）
 * ```
 *
 * Step 3 新增（**纯前端**，不产生任何请求）：
 *   * ``setFilter`` / ``clearFilter`` —— 节点状态筛选（已加载数据内过滤）；
 *   * ``selectAttempt`` / ``clearSelection`` / ``focusNodeEvent`` ——
 *     Attempt ↔ Timeline 双向定位（只改变高亮与滚动目标）；
 *   * ``copyAttemptOutput`` —— 复制持久化输出原文（不解析、不改写）。
 */
/* eslint-env browser, node */

import { ReadError } from './api-client.js';
import {
  COPY_STATUS,
  NODE_FILTER,
  PHASE,
  applyFailure,
  applySuccess,
  attemptDomId,
  beginLoad,
  copyOutput,
  createInitialState,
  hasEventsForAttempt,
  isSubmitBlocked,
  normalizeFilter,
  toTimelineRows,
} from './state.js';

/** 前端未预期错误的安全兜底（不透出堆栈 / 内部对象）。 */
const UNEXPECTED_ERROR = Object.freeze({
  category: 'server',
  status: 0,
  hint: '',
  message: '读取失败：发生未预期的前端错误。',
});

/** UI 交互状态的初始值（与 Run 数据分离，便于独立测试）。 */
function createInitialUi() {
  return {
    filter: NODE_FILTER.ALL,
    selectedNode: null,
    selectedAttempt: null,
    copyStatus: COPY_STATUS.IDLE,
  };
}

/**
 * 把任意异常规范化为 UI 可展示的错误对象（**不**透出原始正文 / 堆栈）。
 *
 * @param {unknown} error
 * @param {boolean} hitBefore 本页面会话是否**曾经**成功加载过
 * @returns {{category: string, status: number, hint: string, message: string}}
 */
export function normalizeError(error, hitBefore = false) {
  if (error instanceof ReadError) {
    let message = error.message;
    let hint = error.hint || '';
    if (error.category === 'unauthorized' && !hint) {
      hint = hitBefore ? 'session-expired' : 'login-required';
      if (hint === 'session-expired') {
        message = '会话已过期或被吊销：请重新登录后再查询。';
      }
    }
    return {
      category: error.category,
      status: error.status,
      hint,
      message,
    };
  }
  return { ...UNEXPECTED_ERROR };
}

/**
 * 创建控制器。
 *
 * @param {{
 *   readRun: (projectId: string, runId: string) => Promise<object>,
 *   onChange?: (state: object, ui: object) => void,
 *   clipboard?: {writeText: Function}
 * }} deps
 * @returns {object} 控制器（见返回值说明）
 */
export function createController(deps) {
  const readRun = deps.readRun;
  const onChange = deps.onChange || (() => {});
  const clipboard = deps.clipboard;

  let state = createInitialState();
  let ui = createInitialUi();
  let requestSeq = 0;
  /** 本页面会话内是否曾成功加载过（用于区分 401 的两种语义）。 */
  let hitBefore = false;

  function emit() {
    onChange(state, ui);
  }

  function setState(next) {
    state = next;
    emit();
  }

  function setUi(patch) {
    ui = { ...ui, ...patch };
    emit();
  }

  /**
   * 执行一次只读加载。
   * @param {unknown} rawProjectId
   * @param {unknown} rawRunId
   * @returns {Promise<{accepted: boolean, applied: boolean}>}
   */
  async function submit(rawProjectId, rawRunId) {
    const projectId =
      typeof rawProjectId === 'string' ? rawProjectId.trim() : '';
    const runId = typeof rawRunId === 'string' ? rawRunId.trim() : '';
    if (isSubmitBlocked(state, projectId, runId)) {
      // 空提交 / 加载中的重复提交：不发起请求、不改状态
      return { accepted: false, applied: false };
    }

    // 新查询：清空选中与复制提示（筛选条件**保留**——运维常连续排查同类问题）
    ui = {
      ...ui,
      selectedNode: null,
      selectedAttempt: null,
      copyStatus: COPY_STATUS.IDLE,
    };
    setState(beginLoad(state, projectId, runId));
    const seq = (requestSeq += 1);
    let applied = false;

    try {
      const data = await readRun(projectId, runId);
      if (seq !== requestSeq) {
        return { accepted: true, applied: false }; // 过期响应：丢弃
      }
      hitBefore = true;
      setState(applySuccess(state, data));
      applied = true;
    } catch (error) {
      if (seq !== requestSeq) {
        return { accepted: true, applied: false };
      }
      setState(applyFailure(state, normalizeError(error, hitBefore)));
    }
    return { accepted: true, applied };
  }

  /**
   * 重新加载当前 Run（**同一组 GET**；不推进任何状态）。
   * @returns {Promise<{accepted: boolean, applied: boolean}>}
   */
  async function refresh() {
    if (!state.runId || state.phase === PHASE.LOADING) {
      return { accepted: false, applied: false };
    }
    return submit(state.projectId, state.runId);
  }

  // ---- Step 3：筛选 ---------------------------------------------------

  /**
   * 设置节点状态筛选（**纯前端**：不重新请求、不改服务端数据）。
   * @param {unknown} rawFilter
   * @returns {{filter: string}}
   */
  function setFilter(rawFilter) {
    setUi({ filter: normalizeFilter(rawFilter) });
    return { filter: ui.filter };
  }

  /** 清除筛选（恢复"全部状态"）。 */
  function clearFilter() {
    return setFilter(NODE_FILTER.ALL);
  }

  // ---- Step 4：Graph View 选中节点 ------------------------------------

  /**
   * 选中 / 取消选中 Graph 中的节点（**纯前端**：不重新请求）。
   *
   * 选中节点会：
   *   * 高亮图中该节点及其直接相连的边；
   *   * 在 Node Attempts 中高亮该节点的**全部** attempt（不折叠历史）；
   *   * 在 Timeline 中高亮该节点的相关事件。
   *
   * @param {unknown} nodeId
   * @returns {{selected: string|null, attemptCount: number, domId: string}}
   */
  function selectGraphNode(nodeId) {
    const target = typeof nodeId === 'string' ? nodeId : '';
    const same = ui.selectedNode === target;
    setUi({
      selectedNode: same ? null : target,
      selectedAttempt: null,
      copyStatus: COPY_STATUS.IDLE,
    });
    const attempts = findAttemptsForNode(state.data, ui.selectedNode);
    return {
      selected: ui.selectedNode,
      attemptCount: attempts.length,
      domId: ui.selectedNode
        ? attemptDomId(ui.selectedNode, attempts[0]?.attempt ?? 1)
        : '',
    };
  }

  /** 取消 Graph 节点选中。 */
  function clearGraphSelection() {
    setUi({ selectedNode: null });
  }

  // ---- Step 3：Attempt ↔ Timeline 联动 --------------------------------

  /**
   * 选中某个 attempt（再次点击同一项 ⇒ 取消选中）。
   *
   * @param {unknown} nodeId
   * @param {unknown} attempt
   * @returns {{selected: object|null, domId: string, hasTimelineEvents: boolean}}
   *   ``domId`` 供 DOM 层滚动定位（纯逻辑层不接触 DOM）。
   */
  function selectAttempt(nodeId, attempt) {
    const target = { nodeId: String(nodeId), attempt: String(attempt) };
    const same =
      ui.selectedAttempt &&
      String(ui.selectedAttempt.nodeId) === target.nodeId &&
      String(ui.selectedAttempt.attempt) === target.attempt;

    setUi({ selectedAttempt: same ? null : target });
    const rows = toTimelineRows(state.data ? state.data.timeline : []);
    return {
      selected: ui.selectedAttempt,
      domId: attemptDomId(target.nodeId, target.attempt),
      hasTimelineEvents: hasEventsForAttempt(
        rows,
        target.nodeId,
        target.attempt
      ),
    };
  }

  /** 取消选中（不高亮、不滚动）。 */
  function clearSelection() {
    setUi({ selectedAttempt: null });
  }

  /**
   * 从 Timeline 事件定位到对应 Attempt（事件无节点身份 ⇒ 不改选中）。
   *
   * @param {{nodeId?: string, attempt?: string}|null} identity
   * @returns {{selected: object|null, domId: string}}
   */
  function focusNodeEvent(identity) {
    if (!identity || !identity.nodeId || attemptMissing(identity.attempt)) {
      return { selected: ui.selectedAttempt, domId: '' };
    }
    return selectAttempt(identity.nodeId, identity.attempt);
  }

  // ---- Step 3：输出复制 ------------------------------------------------

  /**
   * 复制指定 attempt 的**持久化输出原文**（不解析、不改写、不截断）。
   *
   * @param {unknown} nodeId
   * @param {unknown} attempt
   * @returns {Promise<{status: string}>} :data:`COPY_STATUS`
   */
  async function copyAttemptOutput(nodeId, attempt) {
    const output = findAttemptOutput(state.data, nodeId, attempt);
    const status = await copyOutput(output, clipboard);
    setUi({ copyStatus: status });
    return { status };
  }

  /** 清除复制提示。 */
  function clearCopyStatus() {
    setUi({ copyStatus: COPY_STATUS.IDLE });
  }

  return {
    getState: () => state,
    getUi: () => ({ ...ui }),
    submit,
    refresh,
    setFilter,
    clearFilter,
    selectGraphNode,
    clearGraphSelection,
    selectAttempt,
    clearSelection,
    focusNodeEvent,
    copyAttemptOutput,
    clearCopyStatus,
  };
}

/** ``attempt`` 是否缺失（``'—'`` / 空 ⇒ 不可定位）。 */
function attemptMissing(attempt) {
  return (
    attempt === undefined ||
    attempt === null ||
    attempt === '' ||
    attempt === '—'
  );
}

/**
 * 在已加载数据中查找某个 attempt 的输出原文（找不到 ⇒ ``null``）。
 * @param {object|null} data
 * @param {unknown} nodeId
 * @param {unknown} attempt
 * @returns {string|null}
 */
export function findAttemptOutput(data, nodeId, attempt) {
  const nodes = data && Array.isArray(data.nodes) ? data.nodes : [];
  const wantedNode = String(nodeId);
  const wantedAttempt = String(attempt);
  for (const item of nodes) {
    if (!item || typeof item !== 'object') continue;
    if (String(item.node_id) !== wantedNode) continue;
    if (String(item.attempt) !== wantedAttempt) continue;
    return typeof item.output === 'string' ? item.output : null;
  }
  return null;
}

/**
 * 取某节点在已加载数据中的**全部** attempt（保持后端顺序；找不到 ⇒ 空数组）。
 * @param {object|null} data
 * @param {string|null} nodeId
 * @returns {Array<object>}
 */
export function findAttemptsForNode(data, nodeId) {
  if (!nodeId) return [];
  const nodes = data && Array.isArray(data.nodes) ? data.nodes : [];
  return nodes.filter(
    (item) => item && typeof item === 'object' && String(item.node_id) === nodeId
  );
}
