/**
 * Workflow Run Detail —— DOM 渲染层。
 *
 * 纪律：
 *   * **只**消费 `state.js` 的视图模型（`toViewModel` 输出），不直接读 API 原始
 *     对象 ⇒ 渲染层不承担字段决策，"真实字段 / 缺失占位"规则只有一处；
 *   * 一律用 `textContent` / `createElement` 构造节点（**不**使用 `innerHTML`
 *     拼接数据），从结构上避免把持久化输出当 HTML 注入；
 *   * 长输出放进 `<details>` 折叠面板：折叠只影响展示，**不改动原始数据**；
 *   * 不包含任何写操作控件（无批准 / 拒绝 / 重试 / 取消 / 启动按钮）。
 */
/* eslint-env browser */

import {
  OUTPUT_EMPTY,
  PHASE,
  formatNumber,
  label,
  NODE_STATUS_LABELS,
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
 * 输出折叠面板（原始文本原样呈现；仅展示层截取预览）。
 * @param {HTMLElement} doc
 * @param {unknown} output
 * @returns {HTMLElement}
 */
function outputPanel(doc, output) {
  const summary = summarizeOutput(output);
  if (!summary.hasOutput) {
    const p = doc.createElement('p');
    p.className = 'muted';
    p.textContent = OUTPUT_EMPTY;
    return p;
  }
  const details = doc.createElement('details');
  details.className = 'output';
  const caption = doc.createElement('summary');
  caption.textContent = `持久化输出（${summary.length} 字符）`;
  const pre = doc.createElement('pre');
  // 完整原文，逐字符原样（不解析 / 不截断 / 不修复）
  pre.textContent = summary.full;
  details.append(caption, pre);
  return details;
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
 * 渲染 Node Attempts（**全量历史**；同一节点分组展示）。
 * @param {HTMLElement} doc
 * @param {object} viewModel
 * @returns {HTMLElement}
 */
export function renderNodeAttempts(doc, viewModel) {
  const section = doc.createElement('section');
  section.id = 'node-attempts';
  const h2 = doc.createElement('h2');
  h2.textContent = 'Node 执行记录（含历史 Attempts）';
  const meta = doc.createElement('p');
  meta.className = 'muted';
  meta.textContent =
    `${viewModel.nodeCount} 个节点 · 共 ${viewModel.attemptCount} 次 attempt`;
  section.append(h2, meta);

  if (!viewModel.hasRun) {
    const p = doc.createElement('p');
    p.className = 'muted';
    p.textContent = '尚未加载该 Run 的节点执行记录。';
    section.append(p);
    return section;
  }
  if (viewModel.nodeGroups.length === 0) {
    const p = doc.createElement('p');
    p.className = 'empty';
    p.textContent = '该 Run 暂无已持久化的节点执行记录。';
    section.append(p);
    return section;
  }

  for (const group of viewModel.nodeGroups) {
    const wrapper = doc.createElement('article');
    wrapper.className = 'node-group';
    wrapper.setAttribute('data-node-id', group.nodeId);

    const header = doc.createElement('header');
    const title = doc.createElement('h3');
    title.textContent = group.nodeId;
    const typeBadge = doc.createElement('span');
    typeBadge.className = 'badge';
    typeBadge.textContent = group.nodeType;
    header.append(title, typeBadge);
    if (group.hasMultipleAttempts) {
      const multi = doc.createElement('span');
      multi.className = 'badge badge-info';
      multi.textContent = `${group.attemptCount} 次 attempt（全部保留）`;
      header.append(multi);
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
    ]) {
      const th = doc.createElement('th');
      th.textContent = name;
      headRow.append(th);
    }
    thead.append(headRow);
    const tbody = doc.createElement('tbody');

    for (const attempt of group.attempts) {
      const rows = toAttemptRows(attempt);
      const byKey = Object.fromEntries(rows.map((row) => [row.key, row]));
      const tr = doc.createElement('tr');
      tr.setAttribute(
        'data-attempt',
        attempt && attempt.attempt !== undefined ? String(attempt.attempt) : ''
      );

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
      const elapsedCell = doc.createElement('td');
      elapsedCell.textContent = byKey.elapsed_ms.value;
      const startedCell = doc.createElement('td');
      startedCell.textContent = byKey.started_at.value;
      const completedCell = doc.createElement('td');
      completedCell.textContent = byKey.completed_at.value;
      const outputCell = doc.createElement('td');
      outputCell.append(outputPanel(doc, attempt && attempt.output));

      tr.append(
        attemptCell,
        typeCell,
        statusCell,
        errorCell,
        elapsedCell,
        startedCell,
        completedCell,
        outputCell
      );
      tbody.append(tr);
    }
    table.append(thead, tbody);
    wrapper.append(table);
    section.append(wrapper);
  }
  return section;
}

/**
 * 渲染 Timeline（严格保持后端顺序；时间缺失显式标注）。
 * @param {HTMLElement} doc
 * @param {object} viewModel
 * @returns {HTMLElement}
 */
export function renderTimeline(doc, viewModel) {
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
    const li = doc.createElement('li');
    li.className = row.timestampMissing ? 'timeline-item no-time' : 'timeline-item';
    li.setAttribute('data-kind', row.kind);

    const kind = doc.createElement('span');
    kind.className = 'timeline-kind';
    kind.textContent = row.kindLabel;

    const time = doc.createElement('span');
    time.className = 'timeline-time';
    time.textContent = row.timestamp;

    const detail = doc.createElement('span');
    detail.className = 'timeline-detail muted';
    const parts = [`Run ${row.workflowRunId}`];
    if (row.nodeId !== '—') parts.push(`节点 ${row.nodeId}`);
    if (row.attempt !== '—') parts.push(`Attempt ${row.attempt}`);
    if (row.approvalId !== '—') parts.push(`审批 ${row.approvalId}`);
    detail.textContent = parts.join(' · ');

    li.append(time, kind, detail);
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
      p.textContent = '该节点尚无审批记录（未创建审批行）。';
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
 * 渲染错误 / 加载 / 空状态区（含重试按钮；重试只触发 GET）。
 * @param {HTMLElement} doc
 * @param {object} state 页面状态
 * @param {{onRetry: Function}} handlers
 * @returns {HTMLElement}
 */
export function renderStatus(doc, state, handlers) {
  const box = doc.createElement('div');
  box.id = 'status-region';
  box.setAttribute('role', 'status');
  box.setAttribute('aria-live', 'polite');

  if (state.phase === PHASE.LOADING) {
    const p = doc.createElement('p');
    p.className = 'status loading';
    p.textContent = `正在读取 Run ${state.runId} 的详情、节点记录与时间线…`;
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
    const retry = doc.createElement('button');
    retry.type = 'button';
    retry.id = 'retry-button';
    retry.textContent = '重新加载';
    retry.addEventListener('click', handlers.onRetry);
    box.append(p, hint, retry);
    return box;
  }
  if (state.phase === PHASE.IDLE) {
    const p = doc.createElement('p');
    p.className = 'status muted';
    p.textContent =
      '请输入已有的 Workflow Run ID 并查询——页面只做只读展示，不会触发任何执行。';
    box.append(p);
    return box;
  }
  return box;
}

/**
 * 渲染全部区块（覆盖式替换；不累积旧节点）。
 * @param {HTMLElement} doc
 * @param {Document|HTMLElement} root
 * @param {object} state
 * @param {object} viewModel
 * @param {{onRetry: Function}} handlers
 */
export function renderAll(doc, root, state, viewModel, handlers) {
  const container = root.querySelector('#panels');
  if (!container) return;
  container.replaceChildren(
    renderStatus(doc, state, handlers),
    renderOverview(doc, viewModel),
    renderNodeAttempts(doc, viewModel),
    renderTimeline(doc, viewModel),
    renderApprovals(doc, viewModel)
  );
}

export { formatNumber, text };
