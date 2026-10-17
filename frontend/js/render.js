/**
 * Workflow Run Detail —— DOM 渲染层（Phase 7 Workflow UI Step 3）。
 *
 * 纪律：
 *   * **只**消费 `state.js` 的视图模型（`toViewModel` 输出），不直接读 API 原始
 *     对象 ⇒ 渲染层不承担字段决策，"真实字段 / 缺失占位"规则只有一处；
 *   * 一律用 `textContent` / `createElement` 构造节点（**不**使用 `innerHTML`
 *     拼接数据）⇒ 持久化输出**不可能**被当作 HTML 执行；
 *   * 长输出放进 `<details>` 折叠面板：折叠只影响展示，**不改动原始数据**；
 *   * 不包含任何写操作控件（无批准 / 拒绝 / 重试执行 / 取消 / 启动按钮）；
 *   * 筛选 / 选中 / 复制均为**纯前端**交互，不产生任何请求。
 */
/* eslint-env browser */

import {
  COPY_STATUS,
  OUTPUT_EMPTY,
  PHASE,
  formatNumber,
  isSelectedAttempt,
  label,
  NODE_STATUS_LABELS,
  attemptDomId,
  attemptForEvent,
  copyMessage,
  hasNodeIdentity,
  safeDomFragment,
  summarizeOutput,
  text,
  toAttemptRows,
} from './state.js';

const RUN_STATUS_CLASS = {
  PENDING: 'status-pending',
  RUNNING: 'status-running',
  WAITING: 'status-waiting',
  COMPLETED: 'status-completed',
  FAILED: 'status-failed',
  CANCELLED: 'status-cancelled',
};

const NODE_STATUS_CLASS = {
  RUNNING: 'status-running',
  WAITING_APPROVAL: 'status-waiting',
  SUCCEEDED: 'status-completed',
  FAILED: 'status-failed',
};

const APPROVAL_STATUS_CLASS = {
  PENDING: 'status-pending',
  APPROVED: 'status-completed',
  REJECTED: 'status-failed',
};

/** 时间线事件类型的视觉区分（类名；文字标签仍由 `label()` 提供）。 */
const TIMELINE_KIND_CLASS = {
  RUN_CREATED: 'kind-run',
  RUN_STARTED: 'kind-run',
  RUN_FINISHED: 'kind-run',
  NODE_ATTEMPT_STARTED: 'kind-node-start',
  NODE_ATTEMPT_FINISHED: 'kind-node-finish',
  APPROVAL_RESOLVED: 'kind-approval',
};

/**
 * 状态徽标：**文字标签 + 视觉区分**（不单靠颜色传递含义）。
 * @param {HTMLElement} doc
 * @param {string} value 原始枚举值
 * @param {Record<string, string>} labels
 * @param {Record<string, string>} classMap
 * @returns {HTMLElement}
 */
function statusBadge(doc, value, labels, classMap) {
  const span = doc.createElement('span');
  span.className = `badge ${classMap[value] || 'status-unknown'}`;
  span.textContent = label(value, labels);
  span.setAttribute('data-status', value || '');
  return span;
}

/**
 * 定义列表（label → value）。
 * @param {HTMLElement} doc
 * @param {Array<{label: string, value: string}>} rows
 * @returns {HTMLElement}
 */
function definitionList(doc, rows) {
  const dl = doc.createElement('dl');
  dl.className = 'kv';
  for (const row of rows) {
    const dt = doc.createElement('dt');
    dt.textContent = row.label;
    const dd = doc.createElement('dd');
    dd.textContent = row.value;
    dd.setAttribute('data-key', row.key || '');
    dl.append(dt, dd);
  }
  return dl;
}

/**
 * 输出面板：折叠 / 展开 + 完整原文 + 一键复制（**不解析内容**）。
 *
 * @param {HTMLElement} doc
 * @param {unknown} output 持久化原文（字符串或 null）
 * @param {{onCopy: Function}} handlers
 * @param {{nodeId: string, attempt: string}} identity
 * @returns {HTMLElement}
 */
function outputPanel(doc, output, handlers, identity) {
  const summary = summarizeOutput(output, 200);
  const wrapper = doc.createElement('div');
  wrapper.className = 'output-block';

  if (!summary.hasOutput) {
    const p = doc.createElement('p');
    p.className = 'muted output-empty';
    p.textContent = OUTPUT_EMPTY;
    wrapper.append(p);
    return wrapper;
  }

  const details = doc.createElement('details');
  details.className = 'output';
  details.setAttribute('data-output-chars', String(summary.length));

  const caption = doc.createElement('summary');
  caption.textContent = `持久化输出（${summary.length} 字符）`;
  const pre = doc.createElement('pre');
  // 完整原文，逐字符原样（不解析 / 不截断 / 不修复）
  pre.textContent = summary.full;
  pre.className = 'output-full';
  details.append(caption, pre);

  const bar = doc.createElement('div');
  bar.className = 'output-actions';
  const copyButton = doc.createElement('button');
  copyButton.type = 'button';
  copyButton.className = 'secondary small';
  copyButton.textContent = '复制原文';
  copyButton.setAttribute('data-copy-node', String(identity.nodeId));
  copyButton.setAttribute('data-copy-attempt', String(identity.attempt));
  copyButton.addEventListener('click', () => {
    handlers.onCopyOutput(identity.nodeId, identity.attempt);
  });
  const expandAll = doc.createElement('button');
  expandAll.type = 'button';
  expandAll.className = 'secondary small';
  expandAll.textContent = '展开';
  expandAll.addEventListener('click', () => {
    details.open = true;
  });
  const collapse = doc.createElement('button');
  collapse.type = 'button';
  collapse.className = 'secondary small';
  collapse.textContent = '折叠';
  collapse.addEventListener('click', () => {
    details.open = false;
  });
  bar.append(copyButton, expandAll, collapse);

  wrapper.append(details, bar);
  return wrapper;
}

/**
 * 渲染 Run Overview。
 * @param {HTMLElement} doc
 * @param {object} viewModel
 * @returns {HTMLElement}
 */
export function renderOverview(doc, viewModel) {
  const section = doc.createElement('section');
  section.id = 'run-overview';
  const h2 = doc.createElement('h2');
  h2.textContent = 'Run Overview';
  section.append(h2);
  if (!viewModel.hasRun) {
    const p = doc.createElement('p');
    p.className = 'muted';
    p.textContent = '尚未加载 Run 详情。';
    section.append(p);
    return section;
  }
  section.append(definitionList(doc, viewModel.overview));
  return section;
}

/**
 * 渲染筛选结果计数（在静态筛选条上更新；不重建筛选控件本身）。
 * @param {HTMLElement} doc
 * @param {Document|HTMLElement} root
 * @param {object} viewModel
 */
function paintFilterMeta(doc, root, viewModel) {
  const meta = root.querySelector('#filter-meta');
  if (!meta) return;
  const result = viewModel.filterResult;
  if (!viewModel.hasRun) {
    meta.textContent = '尚未加载数据';
    return;
  }
  if (!result.isFiltered) {
    meta.textContent =
      `共 ${result.totalNodes} 个节点 · ${result.totalAttempts} 次 attempt（全部状态）`;
    return;
  }
  meta.textContent =
    `筛选结果：${result.matchedAttempts} 次命中 / 共 ${result.totalAttempts} 次` +
    `（${result.visibleNodes} / ${result.totalNodes} 个节点；` +
    '入选节点仍显示其全部历史 Attempt）';
}

/**
 * 渲染 Node Attempts（**全量历史**；支持状态筛选与失败定位）。
 *
 * @param {HTMLElement} doc
 * @param {object} viewModel
 * @param {object} handlers
 * @returns {HTMLElement}
 */
export function renderNodeAttempts(doc, viewModel, handlers) {
  const section = doc.createElement('section');
  section.id = 'node-attempts';
  const h2 = doc.createElement('h2');
  h2.textContent = 'Node 执行记录（含历史 Attempts）';
  const meta = doc.createElement('p');
  meta.className = 'muted';
  const result = viewModel.filterResult;
  meta.textContent =
    `${viewModel.nodeCount} 个节点 · 共 ${viewModel.attemptCount} 次 attempt` +
    (result && result.isFiltered
      ? ` · 当前筛选命中 ${result.matchedAttempts} 次`
      : '');
  section.append(h2, meta);

  if (!viewModel.hasRun) {
    const p = doc.createElement('p');
    p.className = 'muted';
    p.textContent = '尚未加载该 Run 的节点执行记录。';
    section.append(p);
    return section;
  }
  if (result && result.totalAttempts === 0) {
    const p = doc.createElement('p');
    p.className = 'empty';
    p.textContent = '该 Run 暂无已持久化的节点执行记录。';
    section.append(p);
    return section;
  }
  if (viewModel.nodeGroups.length === 0) {
    // 有数据但被筛选全部过滤掉 ⇒ 明确空状态 + 清除入口
    const p = doc.createElement('p');
    p.className = 'empty';
    p.textContent = '当前筛选条件下没有匹配的 Attempt。';
    const clear = doc.createElement('button');
    clear.type = 'button';
    clear.className = 'secondary small';
    clear.id = 'empty-clear-filter';
    clear.textContent = '清除筛选';
    clear.addEventListener('click', handlers.onClearFilter);
    section.append(p, clear);
    return section;
  }

  for (const group of viewModel.nodeGroups) {
    const wrapper = doc.createElement('article');
    wrapper.className = group.anyFailed ? 'node-group has-failure' : 'node-group';
    wrapper.setAttribute('data-node-id', group.nodeId);

    const header = doc.createElement('header');
    const title = doc.createElement('h3');
    title.textContent = group.nodeId;
    const typeBadge = doc.createElement('span');
    typeBadge.className = 'badge';
    typeBadge.textContent = group.nodeType;
    header.append(title, typeBadge);
    if (group.attemptCount > 1) {
      const multi = doc.createElement('span');
      multi.className = 'badge badge-info';
      multi.textContent = `${group.attemptCount} 次 attempt（全部保留）`;
      header.append(multi);
    }
    if (group.failureCount > 0) {
      const fail = doc.createElement('span');
      fail.className = 'badge status-failed';
      fail.textContent = `失败 ${group.failureCount} 次`;
      header.append(fail);
    }
    if (result && result.isFiltered) {
      const matchInfo = doc.createElement('span');
      matchInfo.className = 'badge';
      matchInfo.textContent = `命中 ${group.matchedCount} 次`;
      header.append(matchInfo);
    }
    wrapper.append(header);

    const table = doc.createElement('table');
    const thead = doc.createElement('thead');
    const headRow = doc.createElement('tr');
    for (const name of [
      'Attempt',
      '类型',
      '状态',
      '错误码',
      '耗时(ms)',
      '开始',
      '完成',
      '持久化输出',
      '定位',
    ]) {
      const th = doc.createElement('th');
      th.textContent = name;
      headRow.append(th);
    }
    thead.append(headRow);
    const tbody = doc.createElement('tbody');

    group.attempts.forEach((attempt, index) => {
      const matches = Boolean(group.attemptMatches && group.attemptMatches[index]);
      const rows = toAttemptRows(attempt);
      const byKey = Object.fromEntries(rows.map((row) => [row.key, row]));
      const nodeId = attempt && attempt.node_id !== undefined
        ? String(attempt.node_id)
        : group.nodeId;
      const attemptNo = attempt && attempt.attempt !== undefined
        ? String(attempt.attempt)
        : '';

      const tr = doc.createElement('tr');
      tr.id = attemptDomId(nodeId, attemptNo);
      // 行级身份属性：便于审计、自动化与"Attempt ↔ Timeline"双向定位
      tr.setAttribute('data-node-id', nodeId);
      tr.setAttribute('data-node-type', group.nodeType);
      tr.setAttribute('data-attempt', attemptNo);
      tr.setAttribute('data-status', attempt && attempt.status ? attempt.status : '');
      tr.setAttribute('data-matches', matches ? 'true' : 'false');
      const classes = ['attempt-row'];
      if (attempt && attempt.status === 'FAILED') classes.push('is-failed');
      if (result && result.isFiltered && !matches) classes.push('is-dimmed');
      if (isSelectedAttempt(viewModel.selectedAttempt, nodeId, attemptNo)) {
        classes.push('is-selected');
      }
      // Step 4：Graph View 选中节点 ⇒ 高亮该节点**全部** attempt
      if (viewModel.selectedNode && viewModel.selectedNode === nodeId) {
        classes.push('is-node-selected');
      }
      tr.className = classes.join(' ');

      const attemptCell = doc.createElement('td');
      attemptCell.textContent = byKey.attempt.value;
      const typeCell = doc.createElement('td');
      typeCell.textContent = byKey.node_type.value;
      const statusCell = doc.createElement('td');
      statusCell.append(
        statusBadge(
          doc,
          attempt && attempt.status,
          NODE_STATUS_LABELS,
          NODE_STATUS_CLASS
        )
      );
      const errorCell = doc.createElement('td');
      errorCell.textContent = byKey.error_code.value;
      errorCell.className = 'cell-error-code';
      const elapsedCell = doc.createElement('td');
      elapsedCell.textContent = byKey.elapsed_ms.value;
      const startedCell = doc.createElement('td');
      startedCell.textContent = byKey.started_at.value;
      const completedCell = doc.createElement('td');
      completedCell.textContent = byKey.completed_at.value;
      const outputCell = doc.createElement('td');
      outputCell.append(
        outputPanel(doc, attempt && attempt.output, handlers, {
          nodeId,
          attempt: attemptNo,
        })
      );
      const locateCell = doc.createElement('td');
      const locate = doc.createElement('button');
      locate.type = 'button';
      locate.className = 'secondary small';
      locate.textContent = '在时间线中定位';
      locate.addEventListener('click', () => {
        handlers.onSelectAttempt(nodeId, attemptNo);
      });
      locateCell.append(locate);

      tr.append(
        attemptCell,
        typeCell,
        statusCell,
        errorCell,
        elapsedCell,
        startedCell,
        completedCell,
        outputCell,
        locateCell
      );
      tbody.append(tr);
    });

    table.append(thead, tbody);
    wrapper.append(table);
    section.append(wrapper);
  }
  return section;
}

/**
 * 渲染 Timeline（严格保持后端顺序；支持定位到对应 Attempt）。
 *
 * @param {HTMLElement} doc
 * @param {object} viewModel
 * @param {object} handlers
 * @returns {HTMLElement}
 */
export function renderTimeline(doc, viewModel, handlers) {
  const section = doc.createElement('section');
  section.id = 'run-timeline';
  const h2 = doc.createElement('h2');
  h2.textContent = '执行时间线（持久化事件）';
  section.append(h2);

  if (!viewModel.hasRun) {
    const p = doc.createElement('p');
    p.className = 'muted';
    p.textContent = '尚未加载时间线。';
    section.append(p);
    return section;
  }
  if (viewModel.timelineRows.length === 0) {
    const p = doc.createElement('p');
    p.className = 'empty';
    p.textContent = '该 Run 暂无持久化的时间线事件。';
    section.append(p);
    return section;
  }

  const note = doc.createElement('p');
  note.className = 'muted';
  note.textContent =
    '顺序由后端稳定排序决定，前端不重排、不推断未持久化的事件。';
  section.append(note);

  const ol = doc.createElement('ol');
  ol.className = 'timeline';
  for (const row of viewModel.timelineRows) {
    const identity = attemptForEvent(row);
    const related = identity
      ? isSelectedAttempt(
          viewModel.selectedAttempt,
          identity.nodeId,
          identity.attempt
        ) ||
        (viewModel.selectedNode && viewModel.selectedNode === identity.nodeId)
      : false;

    const li = doc.createElement('li');
    const classes = ['timeline-item'];
    if (row.timestampMissing) classes.push('no-time');
    if (related) classes.push('is-related');
    if (TIMELINE_KIND_CLASS[row.kind]) classes.push(TIMELINE_KIND_CLASS[row.kind]);
    li.className = classes.join(' ');
    li.setAttribute('data-kind', row.kind);
    li.setAttribute('data-index', String(row.index));
    if (identity) {
      li.setAttribute('data-node-id', identity.nodeId);
      li.setAttribute('data-attempt', identity.attempt);
    }

    const time = doc.createElement('span');
    time.className = 'timeline-time';
    time.textContent = row.timestamp;

    const kind = doc.createElement('span');
    kind.className = 'timeline-kind';
    kind.textContent = row.kindLabel;

    const detail = doc.createElement('span');
    detail.className = 'timeline-detail muted';
    const parts = [`Run ${row.workflowRunId}`];
    if (row.nodeId !== '—') parts.push(`节点 ${row.nodeId}`);
    if (row.attempt !== '—') parts.push(`Attempt ${row.attempt}`);
    if (row.approvalId !== '—') parts.push(`审批 ${row.approvalId}`);
    detail.textContent = parts.join(' · ');

    li.append(time, kind, detail);

    if (identity) {
      const locate = doc.createElement('button');
      locate.type = 'button';
      locate.className = 'secondary small';
      locate.textContent = '定位 Attempt';
      locate.addEventListener('click', () => {
        handlers.onFocusEvent(identity);
      });
      li.append(locate);
    } else if (hasNodeIdentity(row) === false && row.kind.startsWith('NODE_')) {
      // 节点类事件但缺少可定位身份 ⇒ 如实标注（不补造关联）
      const note2 = doc.createElement('span');
      note2.className = 'muted no-target';
      note2.textContent = '（无可定位的节点身份）';
      li.append(note2);
    }

    ol.append(li);
  }
  section.append(ol);
  return section;
}

/**
 * 渲染 Approval Detail（无审批 ⇒ 明确空状态；**绝不**显示为已通过）。
 * @param {HTMLElement} doc
 * @param {object} viewModel
 * @returns {HTMLElement}
 */
export function renderApprovals(doc, viewModel) {
  const section = doc.createElement('section');
  section.id = 'approval-detail';
  const h2 = doc.createElement('h2');
  h2.textContent = 'Approval Detail';
  section.append(h2);

  if (!viewModel.hasRun) {
    const p = doc.createElement('p');
    p.className = 'muted';
    p.textContent = '尚未加载审批信息。';
    section.append(p);
    return section;
  }
  if (viewModel.approvals.length === 0) {
    const p = doc.createElement('p');
    p.className = 'empty';
    p.textContent =
      '该 Run 没有 APPROVAL 类型节点，因此没有可查询的审批记录。';
    section.append(p);
    return section;
  }

  for (const item of viewModel.approvals) {
    const wrapper = doc.createElement('article');
    wrapper.className = 'approval';
    wrapper.setAttribute('data-node-id', item.nodeId);
    const header = doc.createElement('header');
    const title = doc.createElement('h3');
    title.textContent = `节点 ${item.nodeId}`;
    header.append(title);
    if (item.view.present) {
      header.append(
        statusBadge(
          doc,
          item.view.statusRaw,
          { PENDING: '待裁决', APPROVED: '已通过', REJECTED: '已拒绝' },
          APPROVAL_STATUS_CLASS
        )
      );
    }
    wrapper.append(header);

    if (item.view.empty) {
      const p = doc.createElement('p');
      p.className = 'empty';
      p.textContent = '暂无审批记录（该节点尚未创建审批行）。';
      wrapper.append(p);
    } else {
      wrapper.append(definitionList(doc, item.view.rows));
      const note = doc.createElement('p');
      note.className = 'muted';
      note.textContent =
        '裁决时间来自持久化字段；审批创建时刻当前不可读（后端 Store Port 未暴露），因此不展示。';
      wrapper.append(note);
    }
    section.append(wrapper);
  }
  return section;
}

/**
 * 渲染全局请求状态（Loading / Error / Empty / 复制结果）。
 *
 * @param {HTMLElement} doc
 * @param {object} state 页面状态
 * @param {object} ui UI 状态（筛选 / 复制提示）
 * @param {{onRetry: Function}} handlers
 * @returns {HTMLElement}
 */
export function renderStatus(doc, state, ui, handlers) {
  const box = doc.createElement('div');
  box.id = 'status-region';
  box.setAttribute('role', 'status');
  box.setAttribute('aria-live', 'polite');

  if (state.phase === PHASE.LOADING) {
    const p = doc.createElement('p');
    p.className = 'status loading';
    p.textContent =
      `正在读取项目 ${state.projectId} 中 Run ${state.runId} 的详情、` +
      '节点记录与时间线…';
    box.append(p);
    return box;
  }

  if (state.phase === PHASE.ERROR && state.error) {
    const p = doc.createElement('p');
    p.className = 'status error';
    p.textContent = state.error.message;
    const hint = doc.createElement('p');
    hint.className = 'muted';
    hint.textContent = `错误类别：${state.error.category}（HTTP ${state.error.status}）`;
    box.append(p, hint);

    const authHint = {
      'login-required': '请在其它页面完成登录后，回到本页重新查询。',
      'session-expired': '会话已失效——请重新登录后再次查询。',
    }[state.error.hint];
    if (authHint) {
      const extra = doc.createElement('p');
      extra.className = 'muted';
      extra.id = 'auth-hint';
      extra.textContent = authHint;
      box.append(extra);
    }

    const retry = doc.createElement('button');
    retry.type = 'button';
    retry.id = 'retry-button';
    retry.textContent = '重新加载';
    retry.addEventListener('click', handlers.onRetry);
    box.append(retry);
    return box;
  }

  if (state.phase === PHASE.IDLE) {
    const p = doc.createElement('p');
    p.className = 'status muted';
    p.textContent =
      '请输入 Project ID 与已有的 Workflow Run ID 并查询——页面只做只读展示，' +
      '不会触发任何执行。';
    box.append(p);
    return box;
  }

  // LOADED：显示复制结果等轻量提示（不占用主区块）
  if (ui && ui.copyStatus && ui.copyStatus !== COPY_STATUS.IDLE) {
    const p = doc.createElement('p');
    p.id = 'copy-status';
    p.className =
      ui.copyStatus === COPY_STATUS.COPIED
        ? 'status copy-ok'
        : ui.copyStatus === COPY_STATUS.EMPTY
          ? 'status muted'
          : 'status error';
    p.textContent = copyMessage(ui.copyStatus);
    box.append(p);
  }
  return box;
}

/**
 * 渲染全部区块（覆盖式替换；不累积旧节点）。
 *
 * @param {HTMLElement} doc
 * @param {Document|HTMLElement} root
 * @param {object} state
 * @param {object} viewModel
 * @param {object} ui
 * @param {object} handlers
 */
export function renderAll(doc, root, state, viewModel, ui, handlers) {
  paintFilterMeta(doc, root, viewModel);
  const container = root.querySelector('#panels');
  if (!container) return;
  container.replaceChildren(
    renderStatus(doc, state, ui, handlers),
    renderOverview(doc, viewModel),
    renderGraph(doc, viewModel.graph, handlers),
    renderNodeAttempts(doc, viewModel, handlers),
    renderTimeline(doc, viewModel, handlers),
    renderApprovals(doc, viewModel)
  );
}

export { formatNumber, text };


// ============================================================
// Phase 7 Step 4：Graph View（只读拓扑渲染）
// ============================================================

const SVG_NS = 'http://www.w3.org/2000/svg';

/**
 * 渲染 Graph View。
 *
 * 数据纪律：
 *   * 节点与边**只**来自后端 ``graph``（WorkflowDefinition 拓扑）——
 *     **绝不**按 attempt 先后顺序推断边；
 *   * 拓扑不可用（未注册 / 版本不匹配 / 无节点）⇒ 明确空状态，
 *     **不**画任何边（不用可视化误导用户）；
 *   * 节点执行状态由该节点**真实 attempt** 派生；未知状态原样标注；
 *   * 几何由 ``computeGraphGeometry`` 确定性计算（不依赖 DOM 测量）⇒
 *     SVG 路径与节点卡片坐标同源一致。
 *
 * @param {HTMLElement} doc
 * @param {object} graphModel ``toGraphModel`` 输出
 * @param {{onSelectNode: Function}} handlers
 * @returns {HTMLElement}
 */
export function renderGraph(doc, graphModel, handlers) {
  const section = doc.createElement('section');
  section.id = 'graph-view';
  const h2 = doc.createElement('h2');
  h2.textContent = 'Graph View（工作流拓扑）';
  section.append(h2);

  if (!graphModel || !graphModel.available) {
    const p = doc.createElement('p');
    p.className = 'empty';
    p.id = 'graph-empty';
    p.textContent = graphEmptyMessage(graphModel);
    section.append(p);
    if (graphModel && graphModel.reason === 'version_mismatch') {
      const note = doc.createElement('p');
      note.className = 'muted';
      note.id = 'graph-version-note';
      note.textContent =
        `Run 记录版本：${graphModel.workflowVersion || '不可用'}；` +
        `当前注册版本：${graphModel.definitionVersion || '不可用'}。` +
        '为避免展示可能已变化的拓扑，此处不绘制节点与连接线。';
      section.append(note);
    }
    return section;
  }

  const meta = doc.createElement('p');
  meta.className = 'muted';
  meta.id = 'graph-meta';
  meta.textContent =
    `${graphModel.nodeCount} 个节点 · ${graphModel.edgeCount} 条边` +
    ` · 已执行 ${graphModel.executedCount} 个` +
    (graphModel.failedCount > 0 ? ` · 失败 ${graphModel.failedCount} 个` : '') +
    ` · 定义 ${graphModel.workflowId}@${graphModel.definitionVersion}`;
  section.append(meta);

  // 图例（文字 + 颜色双重表达）
  const legend = doc.createElement('ul');
  legend.className = 'graph-legend';
  legend.id = 'graph-legend';
  for (const item of graphModel.legend) {
    const li = doc.createElement('li');
    const swatch = doc.createElement('span');
    swatch.className = `legend-swatch status-${item.key}`;
    const labelEl = doc.createElement('span');
    labelEl.textContent = item.label;
    li.append(swatch, labelEl);
    legend.append(li);
  }
  section.append(legend);

  const geometry = graphModel.geometry;
  if (!geometry) {
    const p = doc.createElement('p');
    p.className = 'empty';
    p.textContent = '该工作流没有可绘制的节点。';
    section.append(p);
    return section;
  }

  const scroller = doc.createElement('div');
  scroller.className = 'graph-scroller';
  scroller.id = 'graph-scroller';

  const canvas = doc.createElement('div');
  canvas.className = 'graph-canvas';
  canvas.id = 'graph-canvas';
  canvas.style.width = `${geometry.width}px`;
  canvas.style.height = `${geometry.height}px`;

  // ---- SVG 边层（先插入 ⇒ 节点卡片在其上层）----
  const svg = doc.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('class', 'graph-edges');
  svg.setAttribute('width', String(geometry.width));
  svg.setAttribute('height', String(geometry.height));
  svg.setAttribute('viewBox', `0 0 ${geometry.width} ${geometry.height}`);
  svg.setAttribute('aria-hidden', 'true');

  const defs = doc.createElementNS(SVG_NS, 'defs');
  const marker = doc.createElementNS(SVG_NS, 'marker');
  marker.setAttribute('id', 'graph-arrow');
  marker.setAttribute('viewBox', '0 0 10 10');
  marker.setAttribute('refX', '9');
  marker.setAttribute('refY', '5');
  marker.setAttribute('markerWidth', '6');
  marker.setAttribute('markerHeight', '6');
  marker.setAttribute('orient', 'auto-start-reverse');
  const head = doc.createElementNS(SVG_NS, 'path');
  head.setAttribute('d', 'M 0 0 L 10 5 L 0 10 z');
  head.setAttribute('class', 'graph-arrow-head');
  marker.append(head);
  defs.append(marker);
  svg.append(defs);

  // 边路径来自几何层；**related**（选中高亮）来自模型层 ⇒ 按 (from,to,kind) 合并
  const relatedKeys = new Set(
    (graphModel.edges || [])
      .filter((edge) => edge.related)
      .map((edge) => `${edge.from}>${edge.to}>${edge.kind}`)
  );
  for (const edge of geometry.edges) {
    const path = doc.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', edge.d);
    path.setAttribute('class', `graph-edge edge-${edge.kind}`);
    path.setAttribute('data-from', edge.from);
    path.setAttribute('data-to', edge.to);
    path.setAttribute('data-kind', edge.kind);
    path.setAttribute('marker-end', 'url(#graph-arrow)');
    if (relatedKeys.has(`${edge.from}>${edge.to}>${edge.kind}`)) {
      path.setAttribute('data-related', 'true');
    }
    svg.append(path);

    if (edge.label) {
      const text = doc.createElementNS(SVG_NS, 'text');
      text.setAttribute('x', String(edge.labelX));
      text.setAttribute('y', String(edge.labelY));
      text.setAttribute('class', 'graph-edge-label');
      text.setAttribute('text-anchor', 'middle');
      // 分支标签是定义中的固定字面量（true / false），非后端自由文本
      text.textContent = edge.label;
      svg.append(text);
    }
  }
  canvas.append(svg);

  // ---- 节点卡片层 ----
  const positions = new Map(geometry.nodes.map((n) => [n.nodeId, n]));
  for (const node of graphModel.nodes) {
    const pos = positions.get(node.nodeId);
    if (!pos) continue;
    canvas.append(renderGraphNode(doc, node, pos, handlers));
  }

  scroller.append(canvas);
  section.append(scroller);

  const hint = doc.createElement('p');
  hint.className = 'muted';
  hint.id = 'graph-hint';
  hint.textContent =
    '节点与连接线来自服务器端工作流定义（已做无环 / 可达性校验）；' +
    '节点执行状态由其真实 Attempt 记录派生。点击节点可定位 Node Attempts。';
  section.append(hint);
  return section;
}

/** 拓扑不可用时的**明确**空状态文案（不暗示成功）。 */
function graphEmptyMessage(graphModel) {
  const reason = graphModel ? graphModel.reason : '';
  switch (reason) {
    case 'not_registered':
      return '该工作流定义未在服务器端注册，无法获取真实拓扑（不绘制任何连接线）。';
    case 'version_mismatch':
      return '该 Run 的工作流定义版本与当前注册版本不一致，为避免展示可能已变化的拓扑，此处不绘制节点与连接线。';
    case 'no_nodes':
      return '该工作流定义没有节点。';
    default:
      return '工作流拓扑不可用。';
  }
}

/** 单个节点卡片（绝对定位；坐标与 SVG 同源）。 */
function renderGraphNode(doc, node, pos, handlers) {
  const card = doc.createElement('div');
  card.className = 'graph-node';
  card.id = `graph-node-${safeDomFragment(node.nodeId)}`;
  card.setAttribute('data-node-id', node.nodeId);
  card.setAttribute('data-node-type', node.nodeType);
  card.setAttribute('data-status', node.status);
  card.setAttribute('data-attempt-count', String(node.attemptCount));
  card.setAttribute('data-selected', node.selected ? 'true' : 'false');
  card.style.left = `${pos.x}px`;
  card.style.top = `${pos.y}px`;
  card.style.width = `${pos.w}px`;
  card.style.height = `${pos.h}px`;

  const inner = doc.createElement('div');
  const classes = ['graph-node-inner'];
  if (node.status === 'FAILED') classes.push('is-failed');
  if (node.status === 'not_run') classes.push('is-not-run');
  if (node.status !== 'not_run' && !node.known) classes.push('is-unknown');
  if (node.selected) classes.push('is-selected');
  inner.className = classes.join(' ');

  const idLine = doc.createElement('div');
  idLine.className = 'graph-node-id';
  idLine.textContent = node.nodeId;   // textContent：不作为 HTML 执行

  const typeLine = doc.createElement('div');
  typeLine.className = 'graph-node-type';
  typeLine.textContent = node.nodeType;

  const statusLine = doc.createElement('div');
  statusLine.className = 'graph-node-status';
  statusLine.textContent =
    node.status === 'not_run' ? '未执行' : label(node.status, NODE_STATUS_LABELS);

  const attemptLine = doc.createElement('div');
  attemptLine.className = 'graph-node-attempts';
  attemptLine.textContent =
    node.attemptCount > 0 ? `${node.attemptCount} 次 attempt` : '无 attempt';

  inner.append(idLine, typeLine, statusLine, attemptLine);

  if (node.isStart) {
    const start = doc.createElement('span');
    start.className = 'graph-node-start';
    start.textContent = '起点';
    inner.append(start);
  }
  if (node.errorCodes.length > 0) {
    const err = doc.createElement('span');
    err.className = 'graph-node-error';
    err.textContent = `错误码 ${node.errorCodes.length} 种`;
    inner.append(err);
  }

  // 点击卡片任意处即选中（键盘可达）
  card.setAttribute('role', 'button');
  card.setAttribute('tabindex', '0');
  card.setAttribute(
    'aria-label',
    `节点 ${node.nodeId}，类型 ${node.nodeType}，` +
      `${node.attemptCount} 次 attempt`
  );
  card.addEventListener('click', () => handlers.onSelectNode(node.nodeId));
  card.addEventListener('keydown', (event) => {
    if (event && (event.key === 'Enter' || event.key === ' ')) {
      handlers.onSelectNode(node.nodeId);
    }
  });

  card.append(inner);
  return card;
}
