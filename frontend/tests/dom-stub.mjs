/**
 * 最小 DOM 桩（**仅测试用**；零依赖）。
 *
 * ⚠️ 这**不是**真实浏览器 / jsdom：它只实现本页面用到的 DOM 子集，
 * 用于断言"生成的 DOM 结构"与"事件接线"，不验证 CSS 布局、真实渲染、
 * 剪贴板权限或浏览器安全策略。
 *
 * 安全性质（有意）：**不实现** `innerHTML` / `outerHTML` ⇒ 若渲染层试图
 * 注入 HTML，测试会直接抛错——结构性地证明只用 `textContent` + DOM API。
 */
/* eslint-env node */

export class FakeElement {
  constructor(tagName) {
    this.tagName = String(tagName).toUpperCase();
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this._text = '';
    this.className = '';
    this.id = '';
    this.open = false;
    this.disabled = false;
    this.value = '';
    this.scrollCalls = 0;
    // 最小 style 代理（记录 CSS 属性；测试断言 left/top/width/height）
    this.style = new Proxy(
      {},
      {
        get: (_target, prop) =>
          typeof prop === 'string' ? this.attributes[`style:${prop}`] ?? '' : '',
        set: (_target, prop, value) => {
          if (typeof prop === 'string') {
            this.attributes[`style:${prop}`] = String(value);
          }
          return true;
        },
      }
    );
  }

  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }

  get textContent() {
    return (
      this._text + this.children.map((child) => child.textContent).join('')
    );
  }

  /** ``className`` 与 ``class`` 属性等价（真实 DOM 语义）。 */
  get className() {
    if (this._className) return this._className;
    return this.attributes.class || '';
  }

  set className(value) {
    this._className = String(value);
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === 'id') this.id = String(value);
  }

  getAttribute(name) {
    if (Object.prototype.hasOwnProperty.call(this.attributes, name)) {
      return this.attributes[name];
    }
    // 真实 DOM 中 `className` 与 `class` 属性等价 ⇒ 桩保持一致
    if (name === 'class' && this.className) return this.className;
    return null;
  }

  append(...nodes) {
    for (const node of nodes) {
      if (node !== null && node !== undefined) this.children.push(node);
    }
  }

  replaceChildren(...nodes) {
    this.children = [];
    this.append(...nodes);
  }

  addEventListener(type, handler) {
    (this.listeners[type] ||= []).push(handler);
  }

  /** 触发监听器（可选传入事件对象）。 */
  dispatch(type, event = {}) {
    for (const handler of this.listeners[type] || []) handler(event);
  }

  scrollIntoView() {
    this.scrollCalls += 1;
  }

  querySelector(selector) {
    if (typeof selector !== 'string' || !selector.startsWith('#')) return null;
    return findById(this, selector.slice(1));
  }

  /**
   * 最小选择器支持（仅覆盖本页面测试所需子集）：
   *   ``tag`` · ``.class`` · ``#id`` · ``tag.class`` · ``#id tag`` ·
   *   ``tag[attr]`` · ``tag[attr="v"]``
   */
  querySelectorAll(selector) {
    if (typeof selector !== 'string' || !selector.trim()) return [];
    const out = [];
    walk(this, (node) => {
      if (matchesSelector(node, selector)) out.push(node);
    });
    return out;
  }
}

export function walk(node, visit) {
  for (const child of node.children) {
    if (child instanceof FakeElement) {
      visit(child);
      walk(child, visit);
    }
  }
}

export function findById(node, id) {
  let found = null;
  walk(node, (child) => {
    if (!found && child.id === id) found = child;
  });
  return found;
}

/** 最小选择器匹配（见 FakeElement.querySelectorAll 的说明）。 */
function matchesSelector(node, selector) {
  const trimmed = selector.trim();

  // 后代选择器：『#id tag』 / 『#id .class』（仅支持两段）
  if (/\s/.test(trimmed)) {
    const [ancestorPart, descendantPart] = trimmed.split(/\s+/);
    if (ancestorPart.startsWith('#')) {
      const root = findById(node, ancestorPart.slice(1));
      if (!root) return false;
      return walkCollect(root, descendantPart).includes(node);
    }
    return false;
  }

  // 属性选择器：tag[attr] / tag[attr="v"] / tag.class[attr="v"]
  const attrMatch = trimmed.match(
    /^([a-zA-Z]*)(?:\.([a-zA-Z0-9_-]+))?\[([^\]=]+)(?:="([^"]*)")?\]$/
  );
  if (attrMatch) {
    const [, tag, cls, attr, value] = attrMatch;
    if (tag && node.tagName !== tag.toUpperCase()) return false;
    if (cls && !String(node.className || '').split(/\s+/).includes(cls)) {
      return false;
    }
    const actual = node.getAttribute(attr);
    if (actual === null) return false;
    return value === undefined || actual === value;
  }

  // tag.class / .class / tag / #id
  const dot = trimmed.indexOf('.');
  if (dot > 0) {
    const tag = trimmed.slice(0, dot);
    const cls = trimmed.slice(dot + 1);
    if (tag && node.tagName !== tag.toUpperCase()) return false;
    return String(node.className || '').split(/\s+/).includes(cls);
  }
  if (trimmed.startsWith('.')) {
    return String(node.className || '').split(/\s+/).includes(trimmed.slice(1));
  }
  if (trimmed.startsWith('#')) {
    return node.id === trimmed.slice(1);
  }
  return node.tagName === trimmed.toUpperCase();
}

function walkCollect(root, selector) {
  const out = [];
  walk(root, (node) => {
    if (matchesSelector(node, selector)) out.push(node);
  });
  return out;
}

/**
 * 最小文档（`#panels` + `#filter-meta`），供渲染层测试使用。
 * @returns {object}
 */
export function createDoc() {
  const body = new FakeElement('body');
  const panels = new FakeElement('main');
  panels.id = 'panels';
  const filterMeta = new FakeElement('span');
  filterMeta.id = 'filter-meta';
  body.append(panels, filterMeta);

  const doc = {
    body,
    panels,
    filterMeta,
    createElement: (tag) => new FakeElement(tag),
    createElementNS: (_ns, tag) => new FakeElement(tag),
    querySelector: (selector) => body.querySelector(selector),
  };
  return doc;
}

/**
 * 页面骨架（与 `index.html` 的 id 一致），供 `bindApp` 接线测试使用。
 *
 * 只构建 `app.js` 会查询的元素；不还原真实 HTML 的全部结构。
 *
 * @returns {object} ``{doc, els}``
 */
export function buildPage() {
  const body = new FakeElement('body');

  const make = (tag, id) => {
    const element = new FakeElement(tag);
    element.id = id;
    return element;
  };

  const form = make('form', 'run-form');
  const projectInput = make('input', 'project-id-input');
  const runInput = make('input', 'run-id-input');
  const submit = make('button', 'run-submit');
  const refresh = make('button', 'run-refresh');
  const filterSelect = make('select', 'node-filter');
  const filterClear = make('button', 'node-filter-clear');
  const filterMeta = make('span', 'filter-meta');
  const currentRun = make('p', 'current-run');
  const currentProject = make('span', 'current-project');
  const panels = make('main', 'panels');

  form.append(projectInput, runInput, submit, refresh);
  body.append(
    form,
    projectInput,
    runInput,
    submit,
    refresh,
    filterSelect,
    filterClear,
    filterMeta,
    currentRun,
    currentProject,
    panels
  );

  const doc = {
    body,
    createElement: (tag) => new FakeElement(tag),
    createElementNS: (_ns, tag) => new FakeElement(tag),
    querySelector: (selector) => body.querySelector(selector),
  };

  return {
    doc,
    els: {
      form,
      projectInput,
      runInput,
      submit,
      refresh,
      filterSelect,
      filterClear,
      filterMeta,
      currentRun,
      currentProject,
      panels,
    },
  };
}

/** 便捷：构造已填充查询输入的页面。 */
export function fillQuery(els, projectId, runId) {
  els.projectInput.value = projectId;
  els.runInput.value = runId;
}
