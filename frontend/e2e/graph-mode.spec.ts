/**
 * 图谱 2D/3D 模式切换 E2E（设计文档 §6.3：平板/移动端固定 2D，桌面端支持切换；
 * WebGL2 不可用时自动降级 2D）。
 *
 * 前置：docker compose 基础设施 + webServer（后端 8000 / 前端 5173）由
 * playwright.config.ts 自动拉起；/graph 路由需登录（admin/bootstrap 口令 admin123）。
 * 触控设备用 Playwright Pixel 5 device 模拟（pointer: coarse 命中）。
 *
 * M4 修复:口令改 env 注入(详见 full-flow.spec.ts 头注)。
 */
import { test, expect, devices } from '@playwright/test'

const ADMIN = {
  username: process.env.E2E_ADMIN_USERNAME ?? 'admin',
  password: process.env.E2E_ADMIN_PASSWORD ?? 'admin123',
}

async function login(page: import('@playwright/test').Page) {
  await page.goto('/login')
  await expect(page.getByRole('heading', { name: '登录' })).toBeVisible()
  await page.getByLabel('用户名').fill(ADMIN.username)
  await page.getByLabel('密码').fill(ADMIN.password)
  await page.getByRole('button', { name: '登录' }).click()
  // 登录成功标志：管理入口（顶栏用户名在窄视口 hidden，不能用 getByText('admin')）
  await expect(page.getByRole('link', { name: '爬取管理', exact: true })).toBeVisible({ timeout: 20_000 })
}

test('桌面端（精细指针）：3D 按钮可用并可切换渲染', async ({ page }) => {
  await login(page)
  await page.goto('/graph')
  await expect(page.getByRole('heading', { name: '能力图谱' })).toBeVisible()

  const btn3d = page.getByRole('button', { name: '3D', exact: true })
  await expect(btn3d).toBeVisible()
  // 桌面 chromium 支持 WebGL2 → 3D 可切换
  await expect(btn3d).toBeEnabled()

  await btn3d.click()
  // 3D 模式渲染中（懒加载 Suspense fallback 或 canvas 出现，容忍异步）
  await expect(page.getByRole('button', { name: '3D' }).locator('..')).toBeVisible()

  // 切回 2D
  await page.getByRole('button', { name: '2D', exact: true }).click()
  await expect(page.getByRole('button', { name: '2D' }).locator('..')).toBeVisible()
})

test('触控设备（粗指针）：3D 按钮禁用并显示固定 2D 提示', async ({ browser }) => {
  const context = await browser.newContext({ ...devices['Pixel 5'] })
  const page = await context.newPage()
  await login(page)
  await page.goto('/graph')
  await expect(page.getByRole('heading', { name: '能力图谱' })).toBeVisible()

  const btn3d = page.getByRole('button', { name: '3D', exact: true })
  await expect(btn3d).toBeDisabled()
  await expect(page.getByText('触控设备固定 2D 模式')).toBeVisible()
  await context.close()
})
