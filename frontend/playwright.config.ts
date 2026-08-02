import { defineConfig, devices } from '@playwright/test'

/**
 * E2E 测试（TE-M4-02，设计文档 §13.2 E2E Playwright 全流程闭环）。
 *
 * 依赖真实基础设施（docker compose 的 postgres/neo4j/redis）与后端：
 * webServer 同时拉起 后端 uvicorn(8000) + 前端 Vite dev(5173)，
 * 前后端通过 Vite proxy（/api → 8000）联通。
 *
 * 运行：pnpm e2e（需先 docker compose up -d postgres redis neo4j）
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  retries: 0,
  reporter: [['list']],
  use: {
    // vite dev 绑定 localhost（IPv6 ::1），E2E 访问须与之一致
    baseURL: 'http://localhost:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: [
    {
      command: 'uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000',
      cwd: '../backend',
      url: 'http://127.0.0.1:8000/health',
      // 本地已验证基础设施常驻（docker 三件套 + uvicorn 可复用），E2E 非 CI 门禁
      reuseExistingServer: true,
      timeout: 60_000,
    },
    {
      command: 'pnpm dev',
      url: 'http://localhost:5173',
      reuseExistingServer: true,
      timeout: 60_000,
    },
  ],
})
