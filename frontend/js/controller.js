/**
 * Workflow Run Detail —— **纯控制器**（无 DOM、无浏览器 API）。
 *
 * 从 `app.js` 分离的目的：把"防重复提交、防竞态、状态流转"这些**安全相关**
 * 的交互约束放在可被 `node --test` 直接覆盖的纯逻辑里，而不必依赖 DOM 桩。
 *
 * 只读铁律：控制器**只**调用注入的 `readRun`（生产 = `readRunBundle`，内部
 * 固定 `GET`）。不存在任何写操作入口。
 */
/* eslint-env browser, node */

import { ReadError } from './api-client.js';
import {
  PHASE,
  applyFailure,
  applySuccess,
  beginLoad,
  createInitialState,
  isSubmitBlocked,
} from './state.js';

/** 前端未预期错误的安全兜底（不透出堆栈 / 内部对象）。 */
const UNEXPECTED_ERROR = Object.freeze({
  category: 'server',
  status: 0,
  message: '读取失败：发生未预期的前端错误。',
});

/**
 * 把任意异常规范化为 UI 可展示的错误对象（**不**透出原始正文 / 堆栈）。
 * @param {unknown} error
 * @returns {{category: string, status: number, message: string}}
 */
export function normalizeError(error) {
  if (error instanceof ReadError) {
    return {
      category: error.category,
      status: error.status,
      message: error.message,
    };
  }
  return { ...UNEXPECTED_ERROR };
}

/**
 * 创建控制器。
 *
 * @param {{
 *   readRun: (runId: string) => Promise<object>,
 *   onChange?: (state: object) => void
 * }} deps
 * @returns {{getState: Function, submit: Function, refresh: Function}}
 */
export function createController(deps) {
  const readRun = deps.readRun;
  const onChange = deps.onChange || (() => {});

  let state = createInitialState();
  let requestSeq = 0;

  function setState(next) {
    state = next;
    onChange(state);
  }

  /**
   * 是否接受本次提交（空白 ⇒ 拒绝；加载中 ⇒ 拒绝重复提交）。
   * @param {string} runId
   * @returns {boolean}
   */
  function shouldReject(runId) {
    return isSubmitBlocked(state, runId);
  }

  /**
   * 执行一次只读加载。
   * @param {unknown} rawRunId
   * @returns {Promise<{accepted: boolean, applied: boolean}>}
   */
  async function submit(rawRunId) {
    const runId = typeof rawRunId === 'string' ? rawRunId.trim() : '';
    if (shouldReject(runId)) {
      // 空提交 / 加载中的重复提交：不发起请求、不改状态
      return { accepted: false, applied: false };
    }

    setState(beginLoad(state, runId));
    const seq = (requestSeq += 1);
    let applied = false;

    try {
      const data = await readRun(runId);
      if (seq !== requestSeq) {
        return { accepted: true, applied: false }; // 过期响应：丢弃
      }
      setState(applySuccess(state, data));
      applied = true;
    } catch (error) {
      if (seq !== requestSeq) {
        return { accepted: true, applied: false };
      }
      setState(applyFailure(state, normalizeError(error)));
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
    return submit(state.runId);
  }

  return {
    getState: () => state,
    submit,
    refresh,
  };
}
