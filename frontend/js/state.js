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
 * @returns {{phase: string, projectId: string, runId: string,
 *   data: object|null, error: object|null}}
 */
export function createInitialState() {
  return {
    phase: PHASE.IDLE,
    projectId: '',
    runId: '',
    data: null,
    error: null,
  };
}

/**
 * 是否应阻止提交（防空提交 + 防重复提交）。
 *
 * 项目 ID 与 Run ID **都**必填：项目 ID 是请求目标（服务端会独立校验
 * 调用者是否为该项目成员），Run ID 是资源标识。
 *
 * @param {{phase: string}} state
 * @param {string} projectId
 * @param {string} runId
 * @returns {boolean}
 */
export function isSubmitBlocked(state, projectId, runId) {
  if (state.phase === PHASE.LOADING) return true;
  const hasProject = Boolean(projectId && projectId.trim());
  const hasRun = Boolean(runId && runId.trim());
  return !hasProject || !hasRun;
}

/**
 * 开始加载：**清空**上一次的数据与错误（避免旧数据被误认为最新）。
 *
 * @param {object} state
 * @param {string} projectId 已 trim 的项目作用域
 * @param {string} runId 已 trim 的 Run ID
 * @returns {object} 新状态（不修改入参）
 */
export function beginLoad(state, projectId, runId) {
  return {
    phase: PHASE.LOADING,
    projectId,
    runId,
    data: null,
    error: null,
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
 *
 * @param {object|null} data 已加载的 Run 数据包
 * @param {{filter?: string, selectedAttempt?: object|null}} [options]
 *   ``filter`` = :data:`NODE_FILTER` 之一（默认 ``ALL``）；
 *   ``selectedAttempt`` = ``{nodeId, attempt}``（用于 Attempt ↔ Timeline 联动高亮）。
 * @returns {object}
 */
export function toViewModel(data, options = {}) {
  const filter = normalizeFilter(options.filter);
  const selectedAttempt = options.selectedAttempt || null;

  if (!data) {
    return {
      hasRun: false,
      overview: [],
      nodeGroups: [],
      nodeCount: 0,
      attemptCount: 0,
      timelineRows: [],
      approvals: [],
      filter,
      filterResult: emptyFilterResult(filter),
      selectedAttempt,
    };
  }
  const allGroups = groupAttemptsByNode(data.nodes);
  const filterResult = applyNodeFilter(allGroups, filter);
  const nodeGroups = filterResult.groups;
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
    nodeCount: filterResult.visibleNodes,
    attemptCount: filterResult.totalAttempts,
    timelineRows: toTimelineRows(data.timeline),
    approvals,
    filter,
    filterResult,
    selectedAttempt,
    selectedNode: options.selectedNode || null,
    graph: toGraphModel(data, {
      selectedNode: options.selectedNode || null,
    }),
  };
}


// ============================================================
// Phase 7 Workflow UI Step 3：筛选 / Attempt ↔ Timeline 联动 / 复制
// ============================================================

/**
 * 节点状态筛选值（**只作前端展示过滤**；不改变任何服务端数据）。
 *
 * 取值与后端真实枚举 ``NODE_STATUSES`` 一一对应，外加 ``unknown``
 * （用于"后端返回了不在域白名单内的状态"——此时**不得**映射为成功）。
 */
export const NODE_FILTER = Object.freeze({
  ALL: 'all',
  RUNNING: 'running',
  SUCCEEDED: 'succeeded',
  FAILED: 'failed',
  WAITING_APPROVAL: 'waiting_approval',
  UNKNOWN: 'unknown',
});

/** 筛选选项（顺序即 UI 顺序；``match`` 为对应的原始状态值集合）。 */
export const NODE_FILTER_OPTIONS = Object.freeze([
  { value: NODE_FILTER.ALL, label: '全部状态', match: null },
  { value: NODE_FILTER.RUNNING, label: '运行中', match: ['RUNNING'] },
  { value: NODE_FILTER.SUCCEEDED, label: '已完成', match: ['SUCCEEDED'] },
  { value: NODE_FILTER.FAILED, label: '失败', match: ['FAILED'] },
  {
    value: NODE_FILTER.WAITING_APPROVAL,
    label: '等待审批',
    match: ['WAITING_APPROVAL'],
  },
  { value: NODE_FILTER.UNKNOWN, label: '未知状态', match: 'unknown' },
]);

/** 已知节点状态集合（来自后端域白名单；用于识别"未知状态"）。 */
export const KNOWN_NODE_STATUSES = Object.freeze(
  Object.keys(NODE_STATUS_LABELS)
);

/**
 * 规范化筛选值（非法 / 缺省 ⇒ ``ALL``）。
 * @param {unknown} raw
 * @returns {string}
 */
export function normalizeFilter(raw) {
  const values = NODE_FILTER_OPTIONS.map((option) => option.value);
  return typeof raw === 'string' && values.includes(raw)
    ? raw
    : NODE_FILTER.ALL;
}

/**
 * 单个 attempt 是否命中筛选条件。
 *
 * @param {object} attempt Port 投影后的 attempt
 * @param {string} filter :data:`NODE_FILTER` 之一
 * @returns {boolean}
 */
export function attemptMatchesFilter(attempt, filter) {
  const normalized = normalizeFilter(filter);
  if (normalized === NODE_FILTER.ALL) return true;

  const status =
    attempt && typeof attempt.status === 'string' ? attempt.status : '';
  const option = NODE_FILTER_OPTIONS.find(
    (item) => item.value === normalized
  );
  if (!option) return true;

  if (option.match === 'unknown') {
    // "未知状态" = 非空但不在域白名单内（空 / 缺失不算"未知状态"，
    // 而是数据缺失——仍以"不可用"展示，不归入任何具体状态）
    return status !== '' && !KNOWN_NODE_STATUSES.includes(status);
  }
  return Array.isArray(option.match) && option.match.includes(status);
}

/**
 * 空筛选结果（未加载数据时使用）。
 * @param {string} filter
 * @returns {object}
 */
export function emptyFilterResult(filter) {
  return {
    filter: normalizeFilter(filter),
    isFiltered: normalizeFilter(filter) !== NODE_FILTER.ALL,
    groups: [],
    matchedAttempts: 0,
    totalAttempts: 0,
    visibleNodes: 0,
    totalNodes: 0,
  };
}

/**
 * 应用节点状态筛选（**纯前端**；不修改入参、不发请求）。
 *
 * 设计取舍（重要）：**按 attempt 命中，但组内展示全部历史**。
 *   * 组入选条件 = 该节点**任一** attempt 命中 ⇒ 用户能找到目标；
 *   * 组内**始终**渲染全部 attempt（``matches`` 标记命中项）⇒
 *     "同一节点的多次 Attempt 不得折叠或丢失"在**任何**筛选下都成立；
 *   * 因此 ``matchedAttempts`` 与 ``totalAttempts`` 都如实给出（用于计数展示）。
 *
 * @param {Array<object>} groups ``groupAttemptsByNode`` 的输出
 * @param {string} filter
 * @returns {{filter: string, isFiltered: boolean, groups: Array<object>,
 *   matchedAttempts: number, totalAttempts: number,
 *   visibleNodes: number, totalNodes: number}}
 */
export function applyNodeFilter(groups, filter) {
  const normalized = normalizeFilter(filter);
  const isFiltered = normalized !== NODE_FILTER.ALL;
  const source = Array.isArray(groups) ? groups : [];

  let matchedAttempts = 0;
  let totalAttempts = 0;
  const result = [];

  for (const group of source) {
    const attempts = Array.isArray(group.attempts) ? group.attempts : [];
    totalAttempts += attempts.length;

    const annotated = attempts.map((attempt) => ({
      attempt,
      matches: attemptMatchesFilter(attempt, normalized),
    }));
    const matched = annotated.filter((item) => item.matches).length;
    if (isFiltered && matched === 0) continue;

    matchedAttempts += matched;
    result.push({
      ...group,
      attempts: annotated.map((item) => item.attempt),
      attemptMatches: annotated.map((item) => item.matches),
      matchedCount: matched,
      hasMultipleAttempts: attempts.length > 1,
      attemptCount: attempts.length,
      anyFailed: attempts.some(
        (item) => item && item.status === 'FAILED'
      ),
      failureCount: attempts.filter(
        (item) => item && item.status === 'FAILED'
      ).length,
    });
  }

  return {
    filter: normalized,
    isFiltered,
    groups: result,
    matchedAttempts: isFiltered ? matchedAttempts : totalAttempts,
    totalAttempts,
    visibleNodes: result.length,
    totalNodes: source.length,
  };
}

// ---- Attempt ↔ Timeline 联动 ------------------------------------------

/**
 * 生成**安全**的 DOM id 片段（仅 ``[A-Za-z0-9_-]``）。
 *
 * 后端标识可能含任意字符（含空格 / 引号 / 非 ASCII）⇒ 一律编码为可控字符，
 * 避免把持久化字符串直接塞进 ``id`` / 选择器。
 *
 * @param {unknown} value
 * @returns {string}
 */
export function safeDomFragment(value) {
  const raw = typeof value === 'string' ? value : String(value ?? '');
  let out = '';
  for (const ch of raw) {
    if (/[A-Za-z0-9_-]/.test(ch)) {
      out += ch;
    } else {
      out += `_u${ch.codePointAt(0).toString(16)}`;
    }
  }
  return out || '_empty';
}

/**
 * attempt 行的 DOM id。
 * @param {unknown} nodeId
 * @param {unknown} attempt
 * @returns {string}
 */
export function attemptDomId(nodeId, attempt) {
  return `attempt-${safeDomFragment(nodeId)}-${safeDomFragment(attempt)}`;
}

/**
 * 事件是否带有可定位的节点身份（``node_id`` + ``attempt``）。
 * @param {object} row ``toTimelineRows`` 的产物
 * @returns {boolean}
 */
export function hasNodeIdentity(row) {
  if (!row || typeof row !== 'object') return false;
  const nodeId = row.nodeId;
  const attempt = row.attempt;
  return (
    typeof nodeId === 'string' &&
    nodeId !== '—' &&
    nodeId !== UNAVAILABLE &&
    typeof attempt === 'string' &&
    attempt !== '—' &&
    attempt !== UNAVAILABLE
  );
}

/**
 * 事件 → 目标 attempt 的身份（不可定位 ⇒ ``null``）。
 * @param {object} row
 * @returns {{nodeId: string, attempt: string}|null}
 */
export function attemptForEvent(row) {
  if (!hasNodeIdentity(row)) return null;
  return { nodeId: row.nodeId, attempt: row.attempt };
}

/**
 * 给定 attempt，返回**真实存在**的相关时间线事件下标（保持后端顺序）。
 *
 * ⚠️ 若该 attempt 在持久化时间线中没有事件 ⇒ 返回空数组
 * （**不**补造事件、**不**推断历史）。
 *
 * @param {Array<object>} rows ``toTimelineRows`` 的产物
 * @param {string} nodeId
 * @param {string} attempt
 * @returns {Array<number>}
 */
export function eventIndexesForAttempt(rows, nodeId, attempt) {
  const list = Array.isArray(rows) ? rows : [];
  const indexes = [];
  for (const row of list) {
    const target = attemptForEvent(row);
    if (!target) continue;
    if (target.nodeId === nodeId && target.attempt === attempt) {
      indexes.push(row.index);
    }
  }
  return indexes;
}

/**
 * 该 attempt 是否存在可定位的时间线事件。
 * @param {Array<object>} rows
 * @param {string} nodeId
 * @param {string} attempt
 * @returns {boolean}
 */
export function hasEventsForAttempt(rows, nodeId, attempt) {
  return eventIndexesForAttempt(rows, nodeId, attempt).length > 0;
}

/**
 * 选中 attempt 的规范化键（用于比较）。
 * @param {{nodeId: unknown, attempt: unknown}|null} selection
 * @returns {string}
 */
export function selectionKey(selection) {
  if (!selection || typeof selection !== 'object') return '';
  return `${safeDomFragment(selection.nodeId)}#${safeDomFragment(
    selection.attempt
  )}`;
}

/**
 * attempt 是否为当前选中项。
 * @param {object} selection
 * @param {unknown} nodeId
 * @param {unknown} attempt
 * @returns {boolean}
 */
export function isSelectedAttempt(selection, nodeId, attempt) {
  if (!selection) return false;
  return (
    selectionKey(selection) ===
    selectionKey({ nodeId, attempt })
  );
}

// ---- 输出复制 ----------------------------------------------------------

/** 复制结果状态。 */
export const COPY_STATUS = Object.freeze({
  IDLE: 'idle',
  COPIED: 'copied',
  FAILED: 'failed',
  EMPTY: 'empty',
});

/** 复制状态文案（**不**含输出内容本体）。 */
export const COPY_MESSAGES = Object.freeze({
  copied: '已复制到剪贴板。',
  failed: '复制失败（浏览器拒绝或无剪贴板权限）。可手动选中文本复制。',
  empty: '该 Attempt 没有持久化输出，无需复制。',
});

/**
 * 计算复制结果文案（纯函数；供 UI 与测试共用）。
 * @param {string} status :data:`COPY_STATUS`
 * @returns {string}
 */
export function copyMessage(status) {
  return COPY_MESSAGES[status] || '';
}

/**
 * 复制文本（可注入 clipboard，便于测试；**不**解析内容）。
 *
 * @param {unknown} value 待复制的原文（非字符串 / 空 ⇒ ``EMPTY``）
 * @param {{writeText?: Function}} [clipboard] 默认 ``navigator.clipboard``
 * @returns {Promise<string>} :data:`COPY_STATUS` 之一
 */
export async function copyOutput(value, clipboard = undefined) {
  if (typeof value !== 'string' || value === '') {
    return COPY_STATUS.EMPTY;
  }
  const api =
    clipboard ||
    (typeof navigator !== 'undefined' && navigator ? navigator.clipboard : null);
  if (!api || typeof api.writeText !== 'function') {
    return COPY_STATUS.FAILED;
  }
  try {
    await api.writeText(value);
    return COPY_STATUS.COPIED;
  } catch (_error) {
    return COPY_STATUS.FAILED;
  }
}


// ============================================================
// Phase 7 Step 4：Workflow Graph View（**只读拓扑**）
// ============================================================

/**
 * 边类型（来自 WorkflowDefinition 的真实字段；**不**存在其它边）。
 *
 * ```text
 * next   顺序边        node.next_node_id
 * true   CONDITION 真分支  node.on_true_node_id
 * false  CONDITION 假分支  node.on_false_node_id
 * ```
 */
export const GRAPH_EDGE_KIND = Object.freeze({
  NEXT: 'next',
  TRUE: 'true',
  FALSE: 'false',
});

/** 边的展示标签（CONDITION 分支必须标注，避免歧义）。 */
export const GRAPH_EDGE_LABELS = Object.freeze({
  next: '',
  true: 'true',
  false: 'false',
});

/** 图例（节点状态 + 边类型；**文字 + 颜色**双重表达）。 */
export const GRAPH_LEGEND = Object.freeze([
  { key: 'running', label: '运行中', status: 'RUNNING' },
  { key: 'succeeded', label: '已完成', status: 'SUCCEEDED' },
  { key: 'failed', label: '失败', status: 'FAILED' },
  { key: 'waiting', label: '等待审批', status: 'WAITING_APPROVAL' },
  { key: 'unknown', label: '未知 / 未执行', status: null },
]);

/** 布局常量（确定性几何 ⇒ 可在 DOM 桩中测试）。 */
export const GRAPH_GEOMETRY = Object.freeze({
  nodeWidth: 176,
  nodeHeight: 68,
  gapX: 44,
  gapY: 64,
  padding: 24,
});

/**
 * 从后端 graph 响应中取状态（未知值按"不可用"处理——不假设成功）。
 * @param {object|null} graph
 * @returns {string}
 */
export function graphStatus(graph) {
  if (!graph || typeof graph !== 'object') return 'unavailable';
  const status = graph.status;
  return typeof status === 'string' && status ? status : 'unavailable';
}

/**
 * 由 attempt 记录派生**单个节点**的展示状态。
 *
 * 规则（**不**猜测、**不**把未知当成功）：
 *   1. 无任何 attempt        ⇒ 'not_run'（未执行）
 *   2. 任一 attempt RUNNING  ⇒ 'RUNNING'
 *   3. 任一 attempt FAILED   ⇒ 'FAILED'（失败必须显眼）
 *   4. 任一 WAITING_APPROVAL ⇒ 'WAITING_APPROVAL'
 *   5. 否则取**最后一次** attempt 的 status（真实值；未知值原样返回）
 *
 * @param {Array<object>} attempts 该节点全部 attempt（任意顺序）
 * @returns {{status: string, known: boolean, attemptCount: number,
 *   errorCodes: Array<string>, latestAttempt: object|null}}
 */
export function nodeStatusFromAttempts(attempts) {
  const list = Array.isArray(attempts) ? attempts : [];
  if (list.length === 0) {
    return {
      status: 'not_run',
      known: true,
      attemptCount: 0,
      errorCodes: [],
      latestAttempt: null,
    };
  }

  const errorCodes = [
    ...new Set(
      list
        .map((item) => item && item.error_code)
        .filter((code) => typeof code === 'string' && code !== '')
    ),
  ];

  const statuses = list.map((item) => (item && item.status) || '');
  if (statuses.includes('RUNNING')) {
    return { status: 'RUNNING', known: true, attemptCount: list.length, errorCodes, latestAttempt: pickLatest(list) };
  }
  if (statuses.includes('FAILED')) {
    return { status: 'FAILED', known: true, attemptCount: list.length, errorCodes, latestAttempt: pickLatest(list) };
  }
  if (statuses.includes('WAITING_APPROVAL')) {
    return { status: 'WAITING_APPROVAL', known: true, attemptCount: list.length, errorCodes, latestAttempt: pickLatest(list) };
  }

  const latest = pickLatest(list);
  const status = latest && typeof latest.status === 'string' ? latest.status : '';
  return {
    status: status || 'unknown',
    known: status !== '' && KNOWN_NODE_STATUSES.includes(status),
    attemptCount: list.length,
    errorCodes,
    latestAttempt: latest,
  };
}

/** 取 attempt 序号最大的 attempt（并列时保持后端顺序）。 */
function pickLatest(list) {
  let best = null;
  let bestNo = Number.NEGATIVE_INFINITY;
  for (const item of list) {
    const no = item && Number.isInteger(item.attempt) ? item.attempt : null;
    if (no === null) continue;
    if (no > bestNo) {
      bestNo = no;
      best = item;
    }
  }
  return best || list[list.length - 1] || null;
}

/**
 * 计算**分层布局**（Kahn 拓扑层；确定性，不依赖 DOM 测量）。
 *
 * 层号 = 从 ``start_node_id`` 出发的**最长路径深度**（保证边尽量向前；
 * 对 DAG 确定且稳定）。孤立 / 不可达节点放在最后一层（真实定义已保证
 * 全节点可达；此处仍是防御性兜底，不抛异常）。
 *
 * @param {Array<object>} nodes 拓扑节点
 * @param {string|null} startNodeId
 * @returns {Map<string, number>} node_id → level
 */
export function computeGraphLevels(nodes, startNodeId) {
  const list = Array.isArray(nodes) ? nodes : [];
  const byId = new Map(list.map((n) => [n.node_id, n]));
  const level = new Map();

  const successors = (node) => {
    const out = [];
    for (const key of ['next_node_id', 'on_true_node_id', 'on_false_node_id']) {
      const target = node && node[key];
      if (typeof target === 'string' && byId.has(target)) out.push(target);
    }
    return out;
  };

  // BFS 赋层：层号 = max(前驱层) + 1（DAG 无环 ⇒ 收敛）
  if (typeof startNodeId === 'string' && byId.has(startNodeId)) {
    level.set(startNodeId, 0);
    const queue = [startNodeId];
    while (queue.length > 0) {
      const current = queue.shift();
      const node = byId.get(current);
      const currentLevel = level.get(current) ?? 0;
      for (const target of successors(node)) {
        const next = currentLevel + 1;
        if (!level.has(target) || level.get(target) < next) {
          level.set(target, next);
          queue.push(target);
        }
      }
    }
  }

  // 兜底：未赋层节点放到最后（真实 DAG 定义不会走到这里）
  let maxLevel = 0;
  for (const value of level.values()) {
    if (value > maxLevel) maxLevel = value;
  }
  for (const node of list) {
    if (!level.has(node.node_id)) level.set(node.node_id, maxLevel + 1);
  }
  return level;
}

/**
 * 构建 Graph View 模型（**纯函数**；可单测）。
 *
 * @param {object|null} data 已加载数据（含 run / nodes / timeline / graph）
 * @param {{selectedNode?: string|null}} [ui]
 * @returns {object}
 */
export function toGraphModel(data, ui = {}) {
  const selectedNode = ui.selectedNode || null;
  const base = {
    available: false,
    status: 'unavailable',
    reason: '',
    workflowId: '',
    workflowVersion: '',
    definitionVersion: '',
    startNodeId: '',
    nodes: [],
    edges: [],
    levels: [],
    selectedNode,
    nodeCount: 0,
    edgeCount: 0,
    executedCount: 0,
    failedCount: 0,
    geometry: null,
    legend: GRAPH_LEGEND,
  };

  if (!data || !data.graph) return base;

  const graph = data.graph;
  const status = graphStatus(graph);
  const run = data.run && typeof data.run === 'object' ? data.run : {};
  const shared = {
    ...base,
    status,
    workflowId: text(graph.workflow_id || run.workflow_id),
    workflowVersion: text(graph.workflow_version || run.workflow_version),
    definitionVersion: text(graph.definition_version),
    startNodeId: text(graph.start_node_id),
  };

  if (status !== 'available' || !Array.isArray(graph.nodes)) {
    return { ...shared, reason: graphUnavailableReason(status) };
  }

  const graphNodes = graph.nodes.filter(
    (item) => item && typeof item === 'object' && typeof item.node_id === 'string'
  );
  if (graphNodes.length === 0) {
    return { ...shared, reason: 'no_nodes' };
  }

  // 按 node_id 聚合真实 attempt（保留全部历史；不折叠）
  const attemptsByNode = new Map();
  for (const item of Array.isArray(data.nodes) ? data.nodes : []) {
    if (!item || typeof item !== 'object') continue;
    const key = typeof item.node_id === 'string' ? item.node_id : '';
    if (!key) continue;
    if (!attemptsByNode.has(key)) attemptsByNode.set(key, []);
    attemptsByNode.get(key).push(item);
  }

  const levelOf = computeGraphLevels(graphNodes, graph.start_node_id);

  const nodes = graphNodes.map((node) => {
    const attempts = attemptsByNode.get(node.node_id) || [];
    const summary = nodeStatusFromAttempts(attempts);
    return {
      nodeId: node.node_id,
      nodeType: text(node.node_type),
      level: levelOf.get(node.node_id) ?? 0,
      isStart: node.node_id === graph.start_node_id,
      hasCondition: node.node_type === 'CONDITION',
      ...summary,
      attempts,
      selected: selectedNode === node.node_id,
    };
  });

  // 边：**只**来自定义字段（next / on_true / on_false）；目标必须存在
  const ids = new Set(nodes.map((n) => n.nodeId));
  const edges = [];
  for (const node of graphNodes) {
    for (const [field, kind] of [
      ['next_node_id', GRAPH_EDGE_KIND.NEXT],
      ['on_true_node_id', GRAPH_EDGE_KIND.TRUE],
      ['on_false_node_id', GRAPH_EDGE_KIND.FALSE],
    ]) {
      const target = node[field];
      if (typeof target !== 'string' || !ids.has(target)) continue;
      edges.push({
        from: node.node_id,
        to: target,
        kind,
        label: GRAPH_EDGE_LABELS[kind] || '',
        related: selectedNode
          ? selectedNode === node.node_id || selectedNode === target
          : false,
      });
    }
  }

  // 分层（层内保持定义顺序 ⇒ 稳定）
  const byLevel = new Map();
  for (const node of nodes) {
    if (!byLevel.has(node.level)) byLevel.set(node.level, []);
    byLevel.get(node.level).push(node);
  }
  const levels = [...byLevel.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([level, items]) => ({ level, nodes: items }));

  const geometry = computeGraphGeometry(levels, graphNodes);

  return {
    ...shared,
    available: true,
    reason: '',
    nodes,
    edges,
    levels,
    nodeCount: nodes.length,
    edgeCount: edges.length,
    executedCount: nodes.filter((n) => n.attemptCount > 0).length,
    failedCount: nodes.filter((n) => n.status === 'FAILED').length,
    selectedNode,
    geometry,
  };
}

/** 拓扑不可用的原因码（前端据此给出**明确**空状态文案）。 */
export function graphUnavailableReason(status) {
  if (status === 'not_registered') return 'not_registered';
  if (status === 'version_mismatch') return 'version_mismatch';
  if (status === 'no_nodes') return 'no_nodes';
  return 'unavailable';
}

/**
 * 计算确定性几何（节点坐标 + 边路径）。
 *
 * 布局：层 = 行（自上而下），层内水平排列并**居中**。
 * 边：源底边中点 → 目标顶边中点的正交折线（同层相邻 ⇒ 水平线）。
 *
 * @param {Array<{level: number, nodes: Array<object>}>} levels 分层结果
 * @param {Array<object>} [nodes] 原始拓扑节点（提供边字段）
 * @returns {{width: number, height: number,
 *   nodes: Array<{nodeId: string, x: number, y: number, w: number, h: number}>,
 *   edges: Array<{from: string, to: string, d: string, labelX: number,
 *     labelY: number, kind: string}>}|null}
 */
export function computeGraphGeometry(levels, topologyNodes = []) {
  if (!Array.isArray(levels) || levels.length === 0) return null;
  const { nodeWidth: W, nodeHeight: H, gapX, gapY, padding } = GRAPH_GEOMETRY;

  const positions = new Map();
  let maxRowWidth = 0;
  const rowCount = levels.length;
  const height = padding * 2 + rowCount * H + (rowCount - 1) * gapY;

  for (const row of levels) {
    const items = Array.isArray(row.nodes) ? row.nodes : [];
    const rowWidth = items.length * W + (items.length - 1) * gapX;
    if (rowWidth > maxRowWidth) maxRowWidth = rowWidth;
    const startX = padding + Math.max(0, (maxRowWidth - rowWidth) / 2);
    items.forEach((node, index) => {
      positions.set(node.nodeId, {
        x: startX + index * (W + gapX),
        y: padding + row.level * (H + gapY),
        w: W,
        h: H,
      });
    });
  }

  const width = padding * 2 + Math.max(maxRowWidth, W);
  const nodes = [...positions.entries()].map(([nodeId, pos]) => ({
    nodeId,
    ...pos,
  }));

  const edges = [];
  // 边的字段来自**原始拓扑节点**（levels 中的行只含 nodeId / level）
  const topologyById = new Map(
    (Array.isArray(topologyNodes) ? topologyNodes : []).map((node) => [
      node.node_id,
      node,
    ])
  );
  for (const row of levels) {
    for (const rowNode of row.nodes || []) {
      const node = topologyById.get(rowNode.nodeId);
      const source = positions.get(rowNode.nodeId);
      if (!node || !source) continue;
      for (const [field, kind] of [
        ['next_node_id', GRAPH_EDGE_KIND.NEXT],
        ['on_true_node_id', GRAPH_EDGE_KIND.TRUE],
        ['on_false_node_id', GRAPH_EDGE_KIND.FALSE],
      ]) {
        const targetId = node[field];
        const target = positions.get(targetId);
        if (!target) continue;
        edges.push({
          from: rowNode.nodeId,
          to: targetId,
          kind,
          label: GRAPH_EDGE_LABELS[kind] || '',
          // related 由调用方（toGraphModel）按选中节点标注；
          // 几何层只负责坐标，故此处透传（缺省 false）
          related: false,
          ...edgePath(source, target),
        });
      }
    }
  }

  return { width, height, nodes, edges };
}

/** 源 → 目标的正交折线（SVG path `d` + 标签坐标）。 */
function edgePath(source, target) {
  const sx = source.x + source.w / 2;
  const sy = source.y + source.h;
  const tx = target.x + target.w / 2;
  const ty = target.y;
  const midY = (sy + ty) / 2;

  let d;
  let labelX;
  let labelY;
  if (Math.abs(target.y - source.y) < 1) {
    // 同层：水平连线（从源右侧到目标左侧）
    const dir = target.x > source.x ? 1 : -1;
    const fromX = dir > 0 ? source.x + source.w : source.x;
    const toX = dir > 0 ? target.x : target.x + target.w;
    d = `M ${fromX} ${sy - source.h / 2} L ${toX} ${ty + target.h / 2}`;
    labelX = (fromX + toX) / 2;
    labelY = sy - source.h / 2 - 8;
  } else {
    d = `M ${sx} ${sy} L ${sx} ${midY} L ${tx} ${midY} L ${tx} ${ty}`;
    labelX = (sx + tx) / 2;
    labelY = midY - 6;
  }
  return { d, labelX, labelY };
}
