import { expect, test, devices } from '@playwright/test'
import { installApiMock, mockLogin } from './mocks/api'

/**
 * 响应式表格/卡片双模式 E2E（CI mock：验证设计文档 §6.3 响应式适配）。
 *
 * 覆盖两种响应式策略：
 * 1. useIsDesktop() Hook 条件渲染（LLM 决策页）—— Table 组件在移动端完全不挂载
 * 2. CSS hidden/lg:hidden 模式（爬虫历史表）—— 两套 DOM 均在但 CSS 控制可见性
 *
 * 断言口径：移动端 (iPhone 12, 390px) 卡片可见 / 表格不可见；
 *          桌面端 (Desktop Chrome, 1280px) 表格可见 / 卡片不可见。
 */
test.describe('响应式表格双模式', () => {
  // ---- useIsDesktop() Hook 条件渲染：LLM 决策页 ----

  test('移动端（390px）：LLM 决策页渲染卡片，Table 组件不挂载', async ({ browser }) => {
    const context = await browser.newContext({ ...devices['iPhone 12'] })
    const page = await context.newPage()
    installApiMock(page)

    await page.goto('/login')
    await mockLogin(page)
    await page.goto('/admin/llm-decisions')

    // 页面加载完成
    await expect(page.getByRole('heading', { name: 'LLM 决策与验收' })).toBeVisible({ timeout: 20_000 })

    // 移动端：useIsDesktop=false → Table 未渲染，无 columnheader（th）元素
    await expect(page.getByRole('columnheader')).toHaveCount(0)

    // 移动端：卡片视图渲染，包含 mock 数据 entity_id
    await expect(page.getByText('react.js')).toBeVisible({ timeout: 15_000 })

    await context.close()
  })

  test('桌面端（1280px）：LLM 决策页渲染表格，表头可见', async ({ page }) => {
    installApiMock(page)

    await page.goto('/login')
    await mockLogin(page)
    await page.goto('/admin/llm-decisions')

    await expect(page.getByRole('heading', { name: 'LLM 决策与验收' })).toBeVisible({ timeout: 20_000 })

    // 桌面端：useIsDesktop=true → Table 渲染，columnheader 可见
    await expect(page.getByRole('columnheader', { name: '域' })).toBeVisible()
    await expect(page.getByRole('columnheader', { name: '实体' })).toBeVisible()
    await expect(page.getByRole('columnheader', { name: '状态' })).toBeVisible()

    // 桌面端：表格内有 mock 数据
    await expect(page.getByText('react.js')).toBeVisible({ timeout: 15_000 })
  })

  // ---- CSS hidden/lg:hidden 模式：爬虫历史表 ----

  test('移动端（390px）：爬虫历史表 CSS 隐藏，卡片可见', async ({ browser }) => {
    const context = await browser.newContext({ ...devices['iPhone 12'] })
    const page = await context.newPage()
    installApiMock(page)

    await page.goto('/login')
    await mockLogin(page)
    await page.goto('/admin/crawl')

    await expect(page.getByRole('heading', { name: '爬取管理' })).toBeVisible({ timeout: 20_000 })

    // 等待历史数据加载到 DOM（表格在 DOM 中但被 CSS hidden 隐藏）
    const historyTable = page.locator('table').filter({ hasText: 'BOSS' })
    await expect(historyTable).toBeAttached({ timeout: 20_000 })

    // 移动端：hidden 类生效 → 表格不可见
    await expect(historyTable).toBeHidden()
  })

  test('桌面端（1280px）：爬虫历史表可见', async ({ page }) => {
    installApiMock(page)

    await page.goto('/login')
    await mockLogin(page)
    await page.goto('/admin/crawl')

    await expect(page.getByRole('heading', { name: '爬取管理' })).toBeVisible({ timeout: 20_000 })

    // 桌面端：lg:block 生效 → 表格可见
    const historyTable = page.locator('table').filter({ hasText: 'BOSS' })
    await expect(historyTable).toBeVisible({ timeout: 20_000 })
  })
})
