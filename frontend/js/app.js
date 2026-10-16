/**
 * Workflow Run Detail —— DOM 接线（把纯控制器接到页面元素上）。
 *
 * 本模块**只**做三件事：
 *   1. 读取表单 / 按钮 / 状态标签等元素；
 *   2. 把用户操作转成控制器调用（查询 / 重新加载）；
 *   3. 每次状态变化后重绘（`render.js`）。
 *
 * 安全相关交互约束（防重复提交、防竞态、错误规范化）都在 `controller.js`
 * 的纯逻辑里，并由 `frontend/tests/controller.test.mjs` 覆盖。
 */
/* eslint-env browser */

import { readRunBundle } from './api-client.js';
import { createController } from './controller.js';
import { PHASE, isSubmitBlocked, toViewModel } from './state.js';
import { renderAll } from './render.js';

/**
 * 绑定页面元素。
 * @param {Document} doc
 * @param {{ readRun?: Function }} [deps] 注入读取实现（默认真实只读 GET）
 * @returns {object} 控制器
 */
export function bindApp(doc, deps = {}) {
  const form = doc.querySelector('#run-form');
  const input = doc.querySelector('#run-id-input');
  const submitButton = doc.querySelector('#run-submit');
  const refreshButton = doc.querySelector('#run-refresh');
  const currentRunLabel = doc.querySelector('#current-run');
  const root = doc.body || doc;

  const handlers = {
    onRetry: () => {
      void controller.refresh();
    },
  };

  function paint(state) {
    if (currentRunLabel) {
      currentRunLabel.textContent = state.runId
        ? `当前查询：${state.runId}`
        : '尚未查询任何 Run';
    }
    if (submitButton) {
      submitButton.disabled = isSubmitBlocked(state, input ? input.value : '');
    }
    if (refreshButton) {
      refreshButton.disabled = !state.runId || state.phase === PHASE.LOADING;
    }
    if (input) {
      // 加载期间禁用输入（防重复提交）；失败后仍保留用户输入
      input.disabled = state.phase === PHASE.LOADING;
      if (state.runId && input.value !== state.runId) {
        input.value = state.runId; // 规范化后回写（失败时保留输入）
      }
    }
    renderAll(doc, root, state, toViewModel(state.data), handlers);
  }

  const controller = createController({
    readRun: deps.readRun || readRunBundle,
    onChange: paint,
  });

  if (form) {
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      void controller.submit(input ? input.value : '');
    });
  }
  if (input) {
    input.addEventListener('input', () => {
      if (submitButton) {
        submitButton.disabled = isSubmitBlocked(
          controller.getState(),
          input.value
        );
      }
    });
  }
  if (refreshButton) {
    refreshButton.addEventListener('click', () => {
      void controller.refresh();
    });
  }

  paint(controller.getState());

  return controller;
}

/* 浏览器直接加载时自动绑定；`node --test` 下无 `document` ⇒ 不执行。 */
if (typeof document !== 'undefined' && document.querySelector('#run-form')) {
  bindApp(document);
}
