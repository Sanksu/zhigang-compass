import { defineConfig, devices } from '@playwright/test'

/**
 * CI 可跑 E2E 配置（L0-2，无需 docker/后端/真实数据库）。
 *
 * 与 playwright.config.ts（真实基础设施 E2E）互补：本配置只起 Vite dev，
 * `/api/v1/**` 请求由 e2e/mocks/api.ts 在浏览器层 `page.route` 拦截并返回 fixture，
 * 因此全流程可在 CI 无基础设施环境运行（CI 门禁外预先验证前端核心链路）。
 *
 * 运行：pnpm e2e:ci（仅收集 mock-*.spec.ts；真实栈 spec 走 pnpm e2e）
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: /mock-.*\.spec\.ts/,
  fullyParallel: false,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [['list']],
  use: {
    // vite dev 绑定 localhost（IPv6 ::1），E2E 访问须与之一致
    baseURL: 'http://localhost:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    // 触控场景由 mock-graph-mobile.spec 用自身 Pixel 5 context 覆盖（与真实栈 spec 同构）
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: [
    {
      command: 'pnpm dev',
      url: 'http://localhost:5173',
      reuseExistingServer: true,
      timeout: 60_000,
    },
  ],
})
