/**
 * Workflow Run Detail —— DOM 接线（把纯控制器接到页面元素上；Step 3）。
 *
 * 本模块**只**做四件事：
 *   1. 读取表单 / 筛选条 / 按钮 / 状态标签等元素；
 *   2. 把用户操作转成控制器调用（查询 / 刷新 / 筛选 / 定位 / 复制）；
 *   3. 每次状态变化后重绘（`render.js`）；
 *   4. 滚动定位（DOM 关注点，不属于纯逻辑层）。
 *
 * 安全相关交互约束（防重复提交、防竞态、错误规范化、筛选与联动计算、
 * 复制结果）都在 `controller.js` / `state.js` 的纯逻辑里并被单元测试覆盖。
 */
/* eslint-env browser */

import { readRunBundle } from './api-client.js';
import { createController } from './controller.js';
import {
  NODE_FILTER,
  PHASE,
  attemptDomId,
  isSubmitBlocked,
  toViewModel,
} from './state.js';
import { renderAll } from './render.js';

/**
 * 安全滚动 + 短暂高亮（容器可能不可见 / 无 ``scrollIntoView``）。
 * @param {Document|HTMLElement} root
 * @param {string} domId
 */
function revealElement(root, domId) {
  if (!domId || typeof root.querySelector !== 'function') return;
  const element = root.querySelector(`#${domId}`);
  if (!element) return;
  if (typeof element.scrollIntoView === 'function') {
    element.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }
  element.setAttribute('data-revealed', 'true');
}

/**
 * 绑定页面元素。
 * @param {Document} doc
 * @param {{readRun?: Function, clipboard?: object}} [deps]
 * @returns {object} 控制器
 */
export function bindApp(doc, deps = {}) {
  const form = doc.querySelector('#run-form');
  const projectInput = doc.querySelector('#project-id-input');
  const input = doc.querySelector('#run-id-input');
  const submitButton = doc.querySelector('#run-submit');
  const refreshButton = doc.querySelector('#run-refresh');
  const filterSelect = doc.querySelector('#node-filter');
  const filterClear = doc.querySelector('#node-filter-clear');
  const currentRunLabel = doc.querySelector('#current-run');
  const projectLabel = doc.querySelector('#current-project');
  const root = doc.body || doc;

  const handlers = {
    onRetry: () => {
      void controller.refresh();
    },
    onSelectAttempt: (nodeId, attempt) => {
      const result = controller.selectAttempt(nodeId, attempt);
      paint(controller.getState(), controller.getUi());
      if (result.selected) {
        revealElement(root, result.domId);
      }
    },
    onFocusEvent: (identity) => {
      const result = controller.focusNodeEvent(identity);
      paint(controller.getState(), controller.getUi());
      if (result.domId) {
        revealElement(root, result.domId);
      }
    },
    onCopyOutput: (nodeId, attempt) => {
      void controller.copyAttemptOutput(nodeId, attempt);
    },
    onClearFilter: () => {
      controller.clearFilter();
      if (filterSelect) filterSelect.value = NODE_FILTER.ALL;
    },
    // Step 4：点击图中节点 ⇒ 选中 + 滚动到 Node Attempts 对应分组
    onSelectNode: (nodeId) => {
      const result = controller.selectGraphNode(nodeId);
      paint(controller.getState(), controller.getUi());
      if (result.selected && result.domId) {
        revealElement(root, result.domId);
      }
    },
  };

  function projectValue() {
    return projectInput ? projectInput.value : '';
  }

  function runValue() {
    return input ? input.value : '';
  }

  function paint(state, ui) {
    if (projectLabel) {
      projectLabel.textContent = state.projectId
        ? `项目：${state.projectId}`
        : '项目：未指定';
    }
    if (currentRunLabel) {
      currentRunLabel.textContent =
        state.projectId && state.runId
          ? `当前查询：项目 ${state.projectId} · Run ${state.runId}`
          : '尚未查询任何 Run';
    }
    // 查询按钮：**仅在加载中禁用**（防重复提交）。
    //
    // 初始 / 空闲态保持可用：空输入由控制器在提交时拒绝（返回
    // `{accepted:false}` 且不发起请求），**不**预先禁用按钮。
    // （真实浏览器验收要求：初始页面"查询按钮可用"；预先禁用会让用户
    //   误以为页面不可交互，且空提交防护本就在控制器内。）
    if (submitButton) {
      submitButton.disabled = state.phase === PHASE.LOADING;
    }
    if (refreshButton) {
      // 重新加载需有当前 Run；加载中禁用
      refreshButton.disabled = !state.runId || state.phase === PHASE.LOADING;
    }
    const loading = state.phase === PHASE.LOADING;
    for (const [element, normalized] of [
      [projectInput, state.projectId],
      [input, state.runId],
    ]) {
      if (!element) continue;
      element.disabled = loading;
      if (normalized && element.value !== normalized) {
        element.value = normalized;
      }
    }
    if (filterSelect) {
      filterSelect.disabled = loading || !state.runId;
      filterSelect.value = ui.filter;
    }
    if (filterClear) {
      filterClear.disabled = loading || ui.filter === NODE_FILTER.ALL;
    }
    renderAll(
      doc,
      root,
      state,
      toViewModel(state.data, {
        filter: ui.filter,
        selectedAttempt: ui.selectedAttempt,
        selectedNode: ui.selectedNode,
      }),
      ui,
      handlers
    );
  }

  const controller = createController({
    readRun: deps.readRun || readRunBundle,
    clipboard: deps.clipboard,
    onChange: paint,
  });

  if (form) {
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      void controller.submit(projectValue(), runValue());
    });
  }
  for (const element of [projectInput, input]) {
    if (!element) continue;
    // 输入变化只影响"重新加载"的可用性（需要已有 Run）；
    // 查询按钮的可用性只由加载态决定（见 paint 内注释）。
    element.addEventListener('input', () => {
      if (refreshButton) {
        const state = controller.getState();
        refreshButton.disabled =
          !state.runId || state.phase === PHASE.LOADING;
      }
    });
  }
  if (refreshButton) {
    refreshButton.addEventListener('click', () => {
      void controller.refresh();
    });
  }
  if (filterSelect) {
    filterSelect.addEventListener('change', () => {
      controller.setFilter(filterSelect.value);
    });
  }
  if (filterClear) {
    filterClear.addEventListener('click', handlers.onClearFilter);
  }

  paint(controller.getState(), controller.getUi());

  return controller;
}

/* 浏览器直接加载时自动绑定；`node --test` 下无 `document` ⇒ 不执行。 */
if (typeof document !== 'undefined' && document.querySelector('#run-form')) {
  bindApp(document);
}

export { attemptDomId };
