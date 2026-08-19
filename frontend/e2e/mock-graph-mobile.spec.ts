import { expect, test, devices } from '@playwright/test'
import { installApiMock, mockLogin } from './mocks/api'

/**
 * 触控设备图谱模式（CI mock：无需 docker 验证 §6.3 平板/移动端固定 2D）。
 * 与 graph-mode.spec.ts（真实栈）同断言口径，数据源为 fixture。
 */
test('触控设备（粗指针）：3D 禁用并显示固定 2D 提示', async ({ browser }) => {
  const context = await browser.newContext({ ...devices['Pixel 5'] })
  const page = await context.newPage()
  installApiMock(page)

  await page.goto('/login')
  await mockLogin(page)
  await page.goto('/graph')
  await expect(page.getByRole('heading', { name: '能力图谱' })).toBeVisible()

  const btn3d = page.getByRole('button', { name: '3D', exact: true })
  await expect(btn3d).toBeDisabled()
  await expect(page.getByText('触控设备固定 2D 模式')).toBeVisible()
  await context.close()
})
