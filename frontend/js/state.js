/**
 * Workflow Run Detail —— **纯逻辑**状态机与视图模型（无 DOM 依赖）。
 *
 * 设计原则（Phase 7 Workflow UI Step 1 §四 / §六）：
 *
 *   * **零虚构**：只展示 API 真实返回的字段；缺失 ⇒ 显式"不可用"占位，
 *     绝不推断执行状态、绝不伪造时间；
 *   * **全量历史**：同一节点的所有 attempt 全部保留（不折叠为最新一条）；
 *   * **保序**：Timeline 严格保持后端顺序（后端已做稳定次级排序）；
 *   * **不修复数据**：损坏输出 / 非法枚举**原样**呈现为"未知值"，不做截断、
 *     不做映射补造（后端 502 时根本不会到达此处）；
 *   * **可测**：全部为纯函数 ⇒ 无需 DOM / 浏览器即可用 `node --test` 覆盖。
 */

/** 页面加载状态（唯一状态来源）。 */
export const PHASE = Object.freeze({
  IDLE: 'idle',
  LOADING: 'loading',
  LOADED: 'loaded',
  ERROR: 'error',
});

/** 节点类型（后端域白名单 `NODE_TYPES`）。未知值不映射，原样显示。 */
export const NODE_TYPE_LABELS = Object.freeze({
  AI: 'AI（模型调用）',
  TOOL: 'TOOL（工具调用）',
  CONDITION: 'CONDITION（条件分支）',
  APPROVAL: 'APPROVAL（人工审批）',
});

/** Run 状态（后端域白名单 `WORKFLOW_STATUSES`）。 */
export const RUN_STATUS_LABELS = Object.freeze({
  PENDING: '待启动',
  RUNNING: '运行中',
  WAITING: '等待审批',
  COMPLETED: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
});

/** Node attempt 状态（后端域白名单 `NODE_STATUSES`）。 */
export const NODE_STATUS_LABELS = Object.freeze({
  RUNNING: '运行中',
  WAITING_APPROVAL: '等待审批',
  SUCCEEDED: '成功',
  FAILED: '失败',
});

/** 审批状态（后端域白名单 `APPROVAL_STATUSES`）。 */
export const APPROVAL_STATUS_LABELS = Object.freeze({
  PENDING: '待裁决',
  APPROVED: '已通过',
  REJECTED: '已拒绝',
});

/** Timeline 事件类型（后端 `TIMELINE_EVENT_KINDS`）。 */
export const TIMELINE_KIND_LABELS = Object.freeze({
  RUN_CREATED: 'Run 创建',
  RUN_STARTED: 'Run 开始',
  RUN_FINISHED: 'Run 结束',
  NODE_ATTEMPT_STARTED: '节点尝试开始',
  NODE_ATTEMPT_FINISHED: '节点尝试结束',
  APPROVAL_RESOLVED: '审批已裁决',
});

/** 字段缺失时的**显式**占位（不得替换为猜测值）。 */
export const UNAVAILABLE = '不可用';

/** 时间缺失文案（不伪造时间）。 */
export const TIME_UNAVAILABLE = '时间不可用';

/** 输出缺失文案。 */
export const OUTPUT_EMPTY = '（无持久化输出）';

/**
 * 创建初始状态。
 * @returns {{phase: string, runId: string, data: object|null, error: object|null}}
 */
export function createInitialState() {
  return { phase: PHASE.IDLE, runId: '', data: null, error: null };
}

/**
 * 是否应阻止提交（防空提交 + 防重复提交）。
 * @param {{phase: string}} state
 * @param {string} runId
 * @returns {boolean}
 */
export function isSubmitBlocked(state, runId) {
  if (state.phase === PHASE.LOADING) return true;
  return !runId || !runId.trim();
}

/**
 * 开始加载：**清空**上一次的数据与错误（避免旧数据被误认为最新）。
 *
 * @param {object} state
 * @param {string} runId 已 trim 的 Run ID
 * @returns {object} 新状态（不修改入参）
 */
export function beginLoad(state, runId) {
  return {
    phase: PHASE.LOADING,
    runId,
    data: null,
    error: null,
    previousRunId: state.runId || '',
  };
}

/**
 * 加载成功。
 * @param {object} state
 * @param {{run: object, nodes: Array, timeline: Array,
 *   approvalsByNode: object}} data
 * @returns {object}
 */
export function applySuccess(state, data) {
  return { ...state, phase: PHASE.LOADED, data, error: null };
}

/**
 * 加载失败（保留 runId 以便用户重试；**不**保留半成品数据）。
 * @param {object} state
 * @param {{category: string, status: number, message: string}} error
 * @returns {object}
 */
export function applyFailure(state, error) {
  return { ...state, phase: PHASE.ERROR, data: null, error };
}

/**
 * 该响应是否仍与当前输入一致（防竞态：过期响应不得覆盖新查询）。
 * @param {object} state
 * @param {string} runId
 * @returns {boolean}
 */
export function isCurrentRun(state, runId) {
  return state.runId === runId;
}

// ============================================================
// 视图模型（纯函数：真实字段 → 展示行）
// ============================================================

/**
 * 安全取值：非字符串 / 空串 ⇒ 显式占位（不猜测、不补造）。
 * @param {unknown} value
 * @returns {string}
 */
export function text(value) {
  if (typeof value === 'string' && value.trim() !== '') return value;
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return UNAVAILABLE;
}

/**
 * 时间戳展示：`null` / 非法 ⇒ "时间不可用"（**不**推断、**不**补当前时间）。
 * @param {unknown} value
 * @returns {string}
 */
export function formatTimestamp(value) {
  if (typeof value !== 'string' || value.trim() === '') return TIME_UNAVAILABLE;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return TIME_UNAVAILABLE;
  return value;
}

/**
 * 布尔展示（后端提供 ⇒ 真实值；缺失 ⇒ 占位）。
 * @param {unknown} value
 * @returns {string}
 */
export function formatBoolean(value) {
  if (value === true) return '是';
  if (value === false) return '否';
  return UNAVAILABLE;
}

/**
 * 数字展示（`null` ⇒ 占位）。
 * @param {unknown} value
 * @returns {string}
 */
export function formatNumber(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return UNAVAILABLE;
}

/**
 * 已知枚举 ⇒ 中文标签；**未知枚举原样显示**（不映射、不吞掉）。
 * @param {unknown} value
 * @param {Record<string, string>} labels
 * @returns {string}
 */
export function label(value, labels) {
  if (typeof value !== 'string' || value.trim() === '') return UNAVAILABLE;
  const mapped = labels[value];
  return mapped ? `${mapped}（${value}）` : `${value}（未在域白名单内）`;
}

/**
 * Run Overview 行（真实字段；缺失 ⇒ 占位）。
 * @param {object} run
 * @returns {Array<{key: string, label: string, value: string}>}
 */
export function toRunOverview(run) {
  if (!run || typeof run !== 'object') return [];
  return [
    { key: 'workflow_run_id', label: 'Run ID', value: text(run.workflow_run_id) },
    { key: 'project_id', label: 'Project ID', value: text(run.project_id) },
    { key: 'workflow_id', label: 'Workflow ID', value: text(run.workflow_id) },
    {
      key: 'workflow_version',
      label: 'Workflow 版本',
      value: text(run.workflow_version),
    },
    {
      key: 'status',
      label: '状态',
      value: label(run.status, RUN_STATUS_LABELS),
      raw: typeof run.status === 'string' ? run.status : '',
    },
    {
      key: 'current_node_id',
      label: '当前节点',
      value: run.current_node_id === null || run.current_node_id === undefined
        ? '（无）'
        : text(run.current_node_id),
    },
    {
      key: 'next_action',
      label: 'Next Action',
      value: text(run.next_action),
    },
    {
      key: 'state_version',
      label: 'State Version',
      value: formatNumber(run.state_version),
    },
    {
      key: 'attempt_count',
      label: '累计 Attempt 数（run 行）',
      value: formatNumber(run.attempt_count),
    },
    {
      key: 'cancel_requested',
      label: '已请求取消',
      value: formatBoolean(run.cancel_requested),
    },
    {
      key: 'cancel_requested_at',
      label: '取消请求时间',
      value: formatTimestamp(run.cancel_requested_at),
    },
    { key: 'created_at', label: '创建时间', value: formatTimestamp(run.created_at) },
    { key: 'started_at', label: '开始时间', value: formatTimestamp(run.started_at) },
    {
      key: 'completed_at',
      label: '完成时间',
      value: formatTimestamp(run.completed_at),
    },
    { key: 'updated_at', label: '更新时间', value: formatTimestamp(run.updated_at) },
  ];
}

/**
 * 按节点分组，**保留全部**历史 attempt（升序，不折叠为最新一条）。
 *
 * @param {Array<object>} nodes
 * @returns {Array<{nodeId: string, nodeType: string,
 *   attempts: Array<object>, attemptCount: number,
 *   hasMultipleAttempts: boolean, statuses: Array<string>,
 *   errorCodes: Array<string>}>}
 */
export function groupAttemptsByNode(nodes) {
  const list = Array.isArray(nodes) ? nodes : [];
  /** @type {Map<string, Array<object>>} */
  const grouped = new Map();
  for (const item of list) {
    if (!item || typeof item !== 'object') continue;
    const nodeId = typeof item.node_id === 'string' ? item.node_id : UNAVAILABLE;
    if (!grouped.has(nodeId)) grouped.set(nodeId, []);
    grouped.get(nodeId).push(item);
  }
  return [...grouped.entries()].map(([nodeId, attempts]) => {
    // 稳定排序：按 attempt 升序；attempt 非法时保持后端返回顺序
    const sorted = [...attempts].sort((a, b) => {
      const left = Number.isInteger(a && a.attempt) ? a.attempt : Number.MAX_SAFE_INTEGER;
      const right = Number.isInteger(b && b.attempt) ? b.attempt : Number.MAX_SAFE_INTEGER;
      return left - right;
    });
    const first = sorted[0] || {};
    return {
      nodeId,
      nodeType: text(first.node_type),
      attempts: sorted,
      attemptCount: sorted.length,
      hasMultipleAttempts: sorted.length > 1,
      statuses: sorted.map((item) => text(item && item.status)),
      errorCodes: sorted
        .map((item) => item && item.error_code)
        .filter((code) => typeof code === 'string' && code !== ''),
    };
  });
}

/**
 * 单条 attempt 的展示行（真实字段；损坏值原样呈现，**不修复**）。
 * @param {object} attempt
 * @returns {Array<{key: string, label: string, value: string}>}
 */
export function toAttemptRows(attempt) {
  if (!attempt || typeof attempt !== 'object') return [];
  return [
    { key: 'attempt', label: 'Attempt', value: formatNumber(attempt.attempt) },
    { key: 'node_id', label: 'Node ID', value: text(attempt.node_id) },
    {
      key: 'node_type',
      label: 'Node 类型',
      value: label(attempt.node_type, NODE_TYPE_LABELS),
    },
    {
      key: 'status',
      label: 'Attempt 状态',
      value: label(attempt.status, NODE_STATUS_LABELS),
    },
    {
      key: 'error_code',
      label: '错误码',
      value:
        typeof attempt.error_code === 'string' && attempt.error_code !== ''
          ? attempt.error_code
          : '（无）',
    },
    {
      key: 'elapsed_ms',
      label: '耗时（ms）',
      value: formatNumber(attempt.elapsed_ms),
    },
    {
      key: 'started_at',
      label: '开始时间',
      value: formatTimestamp(attempt.started_at),
    },
    {
      key: 'completed_at',
      label: '完成时间',
      value: formatTimestamp(attempt.completed_at),
    },
  ];
}

/**
 * 输出摘要（**不修改原始数据**；仅用于折叠面板的标题行）。
 *
 * 规则：无输出 ⇒ 占位；有输出 ⇒ 原样给出长度与预览（预览只是显示层截取，
 * 展开后展示**完整原文**，且绝不回写 / 不解析 / 不修复）。
 *
 * @param {unknown} output
 * @param {number} [previewLength]
 * @returns {{hasOutput: boolean, length: number, preview: string, full: string}}
 */
export function summarizeOutput(output, previewLength = 120) {
  if (typeof output !== 'string' || output === '') {
    return { hasOutput: false, length: 0, preview: OUTPUT_EMPTY, full: '' };
  }
  const preview =
    output.length > previewLength
      ? `${output.slice(0, previewLength)}…（共 ${output.length} 字符，展开查看完整原文）`
      : output;
  return { hasOutput: true, length: output.length, preview, full: output };
}

/**
 * Timeline 行：**严格保持后端顺序**（后端已做稳定次级排序，前端不重排）。
 * @param {Array<object>} events
 * @returns {Array<{index: number, kind: string, kindLabel: string,
 *   timestamp: string, timestampMissing: boolean, nodeId: string,
 *   attempt: string, approvalId: string, workflowRunId: string}>}
 */
export function toTimelineRows(events) {
  const list = Array.isArray(events) ? events : [];
  return list.map((event, index) => {
    const item = event && typeof event === 'object' ? event : {};
    const timestamp = formatTimestamp(item.timestamp);
    return {
      index,
      kind: text(item.kind),
      kindLabel: label(item.kind, TIMELINE_KIND_LABELS),
      timestamp,
      // 与展示值**同源**：无法渲染真实时间（缺失 / 空白 / 非法）即标记为缺失，
      // 避免"显示时间不可用但未标记"的不一致。
      timestampMissing: timestamp === TIME_UNAVAILABLE,
      nodeId: item.node_id ? text(item.node_id) : '—',
      attempt: item.attempt === null || item.attempt === undefined
        ? '—'
        : formatNumber(item.attempt),
      approvalId: item.approval_id ? text(item.approval_id) : '—',
      workflowRunId: text(item.workflow_run_id),
    };
  });
}

/**
 * 审批视图（`null` = 该节点**尚无审批行** ⇒ 明确空状态；
 * **绝不**显示为"已通过"）。
 *
 * @param {object|null|undefined} approval
 * @returns {{present: boolean, empty: boolean, rows: Array<object>,
 *   statusRaw: string, approvalId: string}}
 */
export function toApprovalView(approval) {
  // 非对象、或缺失可识别身份（`approval_id`）⇒ 一律按"无审批记录"处理：
  // 后端契约中 `approval_id` 必填，缺失即无法识别该记录 ⇒ **不**展示半空白
  // 的"审批已存在"假象（更不可能是"已通过"）。
  const hasIdentity =
    approval &&
    typeof approval === 'object' &&
    typeof approval.approval_id === 'string' &&
    approval.approval_id.trim() !== '';
  if (!hasIdentity) {
    return { present: false, empty: true, rows: [], statusRaw: '', approvalId: '' };
  }
  const rows = [
    {
      key: 'approval_id',
      label: 'Approval ID',
      value: text(approval.approval_id),
    },
    {
      key: 'workflow_run_id',
      label: 'Run ID',
      value: text(approval.workflow_run_id),
    },
    { key: 'node_id', label: 'Node ID', value: text(approval.node_id) },
    {
      key: 'status',
      label: '审批状态',
      value: label(approval.status, APPROVAL_STATUS_LABELS),
    },
    {
      key: 'resolved_at',
      label: '裁决时间',
      value:
        approval.resolved_at === null || approval.resolved_at === undefined
          ? '（未裁决）'
          : formatTimestamp(approval.resolved_at),
    },
  ];
  return {
    present: true,
    empty: false,
    rows,
    statusRaw: typeof approval.status === 'string' ? approval.status : '',
    approvalId: typeof approval.approval_id === 'string' ? approval.approval_id : '',
  };
}

/**
 * 由已加载数据派生**全部**展示模型（页面渲染只消费本函数的输出）。
 * @param {object|null} data
 * @returns {object}
 */
export function toViewModel(data) {
  if (!data) {
    return {
      hasRun: false,
      overview: [],
      nodeGroups: [],
      nodeCount: 0,
      attemptCount: 0,
      timelineRows: [],
      approvals: [],
    };
  }
  const nodeGroups = groupAttemptsByNode(data.nodes);
  const approvalsByNode =
    data.approvalsByNode && typeof data.approvalsByNode === 'object'
      ? data.approvalsByNode
      : {};
  const approvals = nodeGroups
    .filter((group) => group.nodeType.startsWith('APPROVAL'))
    .map((group) => ({
      nodeId: group.nodeId,
      view: toApprovalView(approvalsByNode[group.nodeId]),
    }));
  return {
    hasRun: Boolean(data.run),
    overview: toRunOverview(data.run),
    nodeGroups,
    nodeCount: nodeGroups.length,
    attemptCount: nodeGroups.reduce((sum, g) => sum + g.attemptCount, 0),
    timelineRows: toTimelineRows(data.timeline),
    approvals,
  };
}
