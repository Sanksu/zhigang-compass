import { expect, test } from '@playwright/test'
import { installApiMock, mockLogin } from './mocks/api'

/**
 * CI mock 全流程（L0-2：无基础设施环境下验证 登录→仪表盘→图谱→登出 核心链路）。
 *
 * 与 full-flow.spec.ts（真实栈）互补：同一断言口径，数据源为 fixture；
 * 运行：pnpm e2e:ci。
 */
test('登录 → 仪表盘渲染 mock 统计 → 图谱渲染 → 登出', async ({ page }) => {
  installApiMock(page)

  // 冷启动未登录（/auth/refresh mock 无 token）→ 登录页可见
  await page.goto('/login')
  await expect(page.getByRole('heading', { name: '登录' })).toBeVisible()

  await mockLogin(page)

  // ---- 仪表盘：mock 数据已注入（图谱节点 7 / 5 边 / 采集统计 1 源） ----
  await expect(page.getByRole('heading', { name: '仪表盘' })).toBeVisible()
  await expect(page.getByText('智联招聘')).toBeVisible()
  await expect(page.getByText('图谱节点', { exact: true })).toBeVisible()
  // 图谱节点卡 value=stats.nodes=7、delta="5 边"（mock fixture 注入证明）
  await expect(page.getByText('5 边', { exact: true })).toBeVisible()

  // ---- 图谱：panorama 渲染（节点/边统计行） ----
  await page.goto('/graph')
  await expect(page.getByRole('heading', { name: '能力图谱' })).toBeVisible()
  await expect(page.getByText('节点', { exact: true })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('边', { exact: true })).toBeVisible()
  // 软技能/技术栈区分（fixture sk-4 沟通能力=软技能）：HTML 图例含「软技能」项
  await expect(
    page.locator('[aria-label="图谱图例"]').getByText('软技能'),
  ).toBeVisible()

  // ---- 登出 → 回到登录页 ----
  await page.getByRole('button', { name: '登出' }).click()
  await expect(page).toHaveURL(/\/login/, { timeout: 15_000 })
})

test('未登录访问仪表盘数据静默降级不崩溃', async ({ page }) => {
  installApiMock(page)
  // 不登录直接进首页：dashboard 各 skipAuthRedirect 请求要么 mock 成功要么 404 降级，
  // 页面应正常渲染（空态文案或 mock 数据均可，不得白屏/抛错）
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '仪表盘' })).toBeVisible()
  // 采集统计未登录显示引导文案（crawlAvailable=false 路径）
  await expect(page.getByText('采集统计 · 登录后查看')).toBeVisible()
})
