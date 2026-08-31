/**
 * 原始数据管理页测试（/admin/raw，#697 + #698 JD tab 并入）
 *
 * 覆盖：JD tab 默认态复用 /admin/jd 端点加载列表、JD 行操作列为
 * 「去 JD 数据页」跳转（带 ?q= 预填）、其余 tab 走通用端点且含编辑/删除。
 * 直接 mock @/lib/api + react-router navigate。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { apiGet } from '@/lib/api'
import { AdminRawPage } from './admin-raw-page'

vi.mock('@/lib/api', () => ({
  apiGet: vi.fn(),
  apiPut: vi.fn(),
  apiDelete: vi.fn(),
  errMsg: (_e: unknown, fallback: string) => fallback,
}))

const mockApiGet = vi.mocked(apiGet)

function jdItem(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    title: 'Python 开发工程师',
    company: '某科技',
    source: 'zhilian',
    source_id: 'z-1',
    source_url: 'https://example.com/1',
    crawled_at: '2026-08-30 10:00:00',
    is_desensitized: false,
    position: 'Python 开发工程师',
    needs_review: false,
    quality: 0.9,
    text_length: 100,
    updated_at: '2026-08-30T12:00:00',
    ...overrides,
  }
}

function courseItem() {
  return {
    id: 7,
    title: '机器学习入门',
    source: 'icourse163',
    source_id: 'c-7',
    source_url: 'https://www.icourse163.org/course/7',
    crawled_at: '2026-08-29 09:00:00',
    is_desensitized: false,
    text_length: 80,
    updated_at: '2026-08-29T09:00:00',
    extra: { quality: 0.95, institution: '某大学', skills_count: 3 },
  }
}

function setup() {
  mockApiGet.mockImplementation(async (url: string) => {
    if (url.startsWith('/admin/jd?')) {
      return { total: 1, page: 1, size: 20, items: [jdItem()] }
    }
    if (url.startsWith('/admin/raw/course?')) {
      return { total: 1, page: 1, size: 20, items: [courseItem()] }
    }
    throw new Error(`unexpected url ${url}`)
  })
  return render(
    <MemoryRouter>
      <AdminRawPage />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('AdminRawPage JD tab', () => {
  it('默认 JD tab 调用 /admin/jd 端点并渲染列表', async () => {
    setup()
    await waitFor(() => expect(mockApiGet).toHaveBeenCalledWith('/admin/jd?page=1&size=20'))
    await waitFor(() => expect(screen.getByText('Python 开发工程师')).toBeTruthy())
  })

  it('JD 行操作列为「去 JD 数据页」跳转（携带标题预填），无编辑/删除', async () => {
    setup()
    const link = await screen.findByText('去 JD 数据页')
    expect(link).toBeTruthy()
    expect(screen.queryByText('编辑')).toBeNull()
    // JD 行不触发 raw 详情/删除路径
    expect(mockApiGet).not.toHaveBeenCalledWith('/admin/raw/jd?page=1&size=20')
  })

  it('切到课程 tab 走通用端点且显示类型特有列', async () => {
    // Radix Tabs 需真实指针/事件序列才会触发 onValueChange（fireEvent.click 在
    // React 19 + 受控 Tabs 下不生效），故用 userEvent 模拟完整点击。
    const user = userEvent.setup()
    setup()
    // 先确保初始 JD 列表加载完成，再切 tab，避免点击落入首帧异步竞态
    await screen.findByText('Python 开发工程师')
    await user.click(screen.getByRole('tab', { name: '课程' }))
    await waitFor(() => expect(mockApiGet.mock.calls.some((c) => String(c[0]).startsWith('/admin/raw/course'))).toBe(true), { timeout: 3000 })
    await waitFor(() => expect(screen.getByText('机器学习入门')).toBeTruthy())
    expect(screen.getByText('0.95')).toBeTruthy()
    expect(screen.getByText('某大学')).toBeTruthy()
    // 非 JD 行恢复编辑/删除操作
    expect(screen.getByText('编辑')).toBeTruthy()
  })
})
