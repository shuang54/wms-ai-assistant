/**
 * Playwright 配置（Phase 7 Step 3B：真实 Chromium E2E 验收）。
 *
 * 约束（与阶段任务书一致）：
 *   * **只**运行 Chromium（不装 / 不跑 Firefox / WebKit）；
 *   * 复用现有 `scripts/serve-static.mjs`（仅绑定 127.0.0.1；不代理 /api）；
 *   * 独立端口（默认 4321），不占用开发端口；
 *   * **不**访问外部网络：`page.route()` 拦截所有 `/api/**` 请求并注入测试响应；
 *   * 不连接任何数据库、不需要真实 LLM；Mock 响应**只**在内存中；
 *   * 失败不重试掩盖问题：`retries: 0`；保留 trace / 截图 / video 便于定位。
 *
 * 注意：`frontend/package.json` 声明 `"type": "module"` ⇒ 本文件使用 ESM 语法
 * （与 `js/*.js`、`tests/*.test.mjs` 的模块体系一致）。
 */
import { defineConfig } from '@playwright/test';

const PORT = Number(process.env.E2E_PORT || 4321);

export default defineConfig({
  testDir: './e2e',
  // 每次运行使用独立输出目录，避免清理历史目录（本机 IDE 有删除保护）
  outputDir: `./test-results/run-${Date.now()}`,
  timeout: 45_000,
  expect: { timeout: 7_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0, // 不通过重试掩盖失败
  // 运行结束不删除任何历史输出目录
  preserveOutput: 'always',
  // 不在运行开始时清空 outputDir
  cleanup: false,

  reporter: [['line']],

  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    viewport: { width: 1280, height: 800 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: {
        browserName: 'chromium',
        // 只读工作台页面；剪贴板权限授予后仍以可控 mock 为主
        permissions: ['clipboard-read', 'clipboard-write'],
      },
    },
  ],

  // 复用既有静态服务（零依赖；只绑回环；不代理 API）
  webServer: {
    command: `node scripts/serve-static.mjs --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/index.html`,
    reuseExistingServer: false,
    timeout: 30_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
