/**
 * 发现观察池 Tab 组件测试
 *
 * 覆盖：初始查询（page=1&size=50）、筛选重置分页并带 status/source 参数、
 * 分页翻页（total > 50 才出现）、请求失败错误态。直接 mock @/lib/api（未引入 MSW）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { apiGet } from '@/lib/api'
import { TechnologyWatchTab } from './technology-watch-tab'

vi.mock('@/lib/api', () => ({
  apiGet: vi.fn(),
  errMsg: (_e: unknown, fallback: string) => fallback,
}))

const mockApiGet = vi.mocked(apiGet)

// jsdom 缺 scrollIntoView，Radix Select 打开/选中项定位时会调用（引未捕获异常）
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}

function makeRow(overrides: Record<string, unknown> = {}) {
  return {
    skill_name: '大模型推理',
    signal_source: 'arxiv',
    signal_value: 0.123,
    period: '2026-W34',
    status: 'watch',
    last_signal_at: '2026-08-17T10:00:00Z',
    ...overrides,
  }
}

function watchData(items: unknown[], total = items.length) {
  return { items, total, page: 1, size: 50 }
}

afterEach(() => {
  cleanup()
  mockApiGet.mockReset()
})

describe('TechnologyWatchTab 发现观察池', () => {
  it('初始查询 page=1&size=50 并渲染信号行', async () => {
    mockApiGet.mockResolvedValue(watchData([makeRow()]))
    render(<TechnologyWatchTab />)
    await waitFor(() =>
      expect(mockApiGet).toHaveBeenCalledWith('/admin/discovery/watch?page=1&size=50'),
    )
    expect(await screen.findByText('大模型推理')).toBeInTheDocument()
    expect(screen.getByText('论文')).toBeInTheDocument() // arxiv -> 论文
    expect(screen.getByText('观察中')).toBeInTheDocument() // status=watch badge
  })

  it('状态筛选重置页码并带 status 参数重新查询', async () => {
    mockApiGet.mockResolvedValue(watchData([makeRow()]))
    render(<TechnologyWatchTab />)
    await screen.findByText('大模型推理')
    fireEvent.click(screen.getByText('状态筛选'))
    fireEvent.click(await screen.findByRole('option', { name: '观察中' }))
    await waitFor(() =>
      expect(mockApiGet).toHaveBeenLastCalledWith(
        expect.stringContaining('page=1&size=50'),
      ),
    )
    expect(mockApiGet).toHaveBeenLastCalledWith(
      expect.stringContaining('status=watch'),
    )
    // 来源筛选叠加 status 参数
    fireEvent.click(screen.getByText('来源筛选'))
    fireEvent.click(await screen.findByRole('option', { name: 'GitHub' }))
    await waitFor(() =>
      expect(mockApiGet).toHaveBeenLastCalledWith(
        expect.stringContaining('source=github'),
      ),
    )
    expect(mockApiGet).toHaveBeenLastCalledWith(expect.stringContaining('status=watch'))
  })

  it('total>50 显示分页并可翻页（page=2）', async () => {
    mockApiGet.mockResolvedValue(watchData([makeRow()], 120))
    render(<TechnologyWatchTab />)
    await screen.findByText('第 1 / 3 页 · 每页 50 条')
    const prev = screen.getByRole('button', { name: '上一页' })
    expect(prev).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() =>
      expect(mockApiGet).toHaveBeenLastCalledWith('/admin/discovery/watch?page=2&size=50'),
    )
    await screen.findByText('第 2 / 3 页 · 每页 50 条')
  })

  it('请求失败展示错误文案', async () => {
    mockApiGet.mockRejectedValue(new Error('network'))
    render(<TechnologyWatchTab />)
    expect(await screen.findByText('观察池加载失败')).toBeInTheDocument()
  })
})
