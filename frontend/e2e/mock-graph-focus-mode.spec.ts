import { expect, test } from '@playwright/test'
import { installApiMock, mockLogin } from './mocks/api'

/**
 * 大屏演示模式（CI mock：无需 docker 验证 §6.3 演示模式）。
 * 断言 focus 模式下 AppShell 跳过顶导/侧栏渲染、画布操作组出现「退出演示」，
 * Esc 退出后导航恢复——答辩/录屏场景的核心链路。
 */
test('大屏演示：隐藏顶导/侧栏，Esc 退出恢复', async ({ page }) => {
  installApiMock(page)

  await page.goto('/login')
  await mockLogin(page)
  await page.goto('/graph')
  await expect(page.getByRole('heading', { name: '能力图谱' })).toBeVisible()

  // 进入前：顶导（header）与双份侧栏（移动抽屉 + 桌面栏，共 2 个 aside）均渲染
  await expect(page.locator('header')).toBeVisible()
  const asides = page.locator('aside')
  await expect(asides).toHaveCount(2)

  await page.getByRole('button', { name: '大屏演示' }).click()
  // focus 模式：TopNav/Sidebar 不再渲染（而非仅视觉隐藏）；画布操作组切换为退出
  await expect(page.locator('header')).toHaveCount(0)
  await expect(asides).toHaveCount(0)
  await expect(page.getByRole('button', { name: '退出演示' })).toBeVisible()

  // Esc 退出 → 导航恢复
  await page.keyboard.press('Escape')
  await expect(page.locator('header')).toBeVisible()
  await expect(asides).toHaveCount(2)
  await expect(page.getByRole('button', { name: '大屏演示' })).toBeVisible()
})
