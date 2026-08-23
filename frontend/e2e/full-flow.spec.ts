import { expect, test } from '@playwright/test'

/**
 * E2E 全流程闭环（TE-M4-02，设计文档 §13.2）。
 *
 * 覆盖：登录 → 仪表盘 → 能力图谱（真实 panorama）→ 简历匹配 → 管理后台 → 登出。
 * 前置：docker compose 基础设施 + webServer（后端 8000 / 前端 5173）由
 * playwright.config.ts 自动拉起；真实库含 admin 用户（bootstrap 口令 admin123）。
 *
 * M4 修复:口令改 env 注入,不再硬编码疑似真实管理员口令。本地默认走
 * bootstrap 弱口令 admin123(development 兼容);CI/生产通过 E2E_ADMIN_PASSWORD
 * 环境变量注入(见 playwright.config.ts/CI secrets),原硬编码口令已泄露须轮换。
 */
const ADMIN = {
  username: process.env.E2E_ADMIN_USERNAME ?? 'admin',
  password: process.env.E2E_ADMIN_PASSWORD ?? 'admin123',
}

test('登录 → 图谱 → 匹配 → 后台 → 登出 全流程', async ({ page }) => {
  // ---- 登录页 ----
  await page.goto('/login')
  await expect(page.getByRole('heading', { name: '登录' })).toBeVisible()

  await page.getByLabel('用户名').fill(ADMIN.username)
  await page.getByLabel('密码').fill(ADMIN.password)
  await page.getByRole('button', { name: '登录' }).click()

  // ---- 登录成功 → 仪表盘（顶栏 banner 显示用户名） ----
  await expect(page.getByRole('banner').getByText('admin')).toBeVisible({ timeout: 20_000 })

  // ---- 能力图谱（真实 panorama 数据） ----
  await page.goto('/graph')
  await expect(page.getByRole('heading', { name: '能力图谱' })).toBeVisible()
  // 图例整体可见 = panorama 渲染成功（Career Atlas 重构后独立计数行并入图例）
  await expect(page.locator('[aria-label="图谱图例"]')).toBeVisible({ timeout: 20_000 })

  // ---- 简历匹配页 ----
  await page.goto('/resume-match')
  await expect(page.getByRole('heading', { name: /简历匹配|岗位推荐/ })).toBeVisible({ timeout: 20_000 })

  // ---- 管理后台 ----
  await page.goto('/admin')
  await expect(page.getByRole('heading', { name: '管理后台' })).toBeVisible({ timeout: 20_000 })

  // ---- 登出 ----
  await page.getByRole('button', { name: '登出' }).click()
  await expect(page).toHaveURL(/\/login/, { timeout: 15_000 })
})
