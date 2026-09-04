/**
 * 原始数据管理页测试（/admin/raw，JD 完整能力并入）
 *
 * 覆盖：JD tab 默认态复用 /admin/jd 端点加载列表并渲染归一化岗位/质量复核/正文字数列、
 * 待复核行含放行与编辑（无跳转按钮）；切课程 tab 走通用端点且显示类型特有列。
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
    needs_review: true,
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
    // 标题列 + 归一化岗位列各渲染一次岗位名
    await waitFor(() => expect(screen.getAllByText('Python 开发工程师').length).toBeGreaterThanOrEqual(2))
  })

  it('JD 行渲染归一化岗位/质量复核/正文字数列，待复核含放行与编辑（无跳转）', async () => {
    setup()
    await screen.findByText('待复核')
    // 类型特有列表头
    expect(screen.getByText('公司')).toBeTruthy()
    expect(screen.getByText('归一化岗位')).toBeTruthy()
    expect(screen.getByText('质量/复核')).toBeTruthy()
    expect(screen.getByText('正文字数')).toBeTruthy()
    // 质量分 0.9 → 0.90；正文长度 100
    expect(screen.getByText('0.90')).toBeTruthy()
    expect(screen.getByText('100')).toBeTruthy()
    // 待复核行：放行 + 编辑，不再出现「去 JD 数据页」跳转
    expect(screen.getByText('放行')).toBeTruthy()
    expect(screen.getByText('编辑')).toBeTruthy()
    expect(screen.queryByText('去 JD 数据页')).toBeNull()
  })

  it('切到课程 tab 走通用端点且显示类型特有列', async () => {
    // Radix Tabs 需真实指针/事件序列才会触发 onValueChange（fireEvent.click 在
    // React 19 + 受控 Tabs 下不生效），故用 userEvent 模拟完整点击。
    const user = userEvent.setup()
    setup()
    // 先确保初始 JD 列表加载完成，再切 tab，避免点击落入首帧异步竞态
    await screen.findByText('待复核')
    await user.click(screen.getByRole('tab', { name: '课程' }))
    await waitFor(() => expect(mockApiGet.mock.calls.some((c) => String(c[0]).startsWith('/admin/raw/course'))).toBe(true), { timeout: 3000 })
    await waitFor(() => expect(screen.getByText('机器学习入门')).toBeTruthy())
    // 课程类型特有列：质量 quality、机构 institution
    expect(screen.getByText('0.95')).toBeTruthy()
    expect(screen.getByText('某大学')).toBeTruthy()
    // 非 JD 行恢复编辑操作
    expect(screen.getByText('编辑')).toBeTruthy()
  })
})