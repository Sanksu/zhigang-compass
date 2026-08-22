/**
 * 数据血缘溯源页测试（P13 管理端可视化）
 *
 * 覆盖：positions GET 渲染总览统计与岗位表、空态/错误态、
 * 过滤按钮触发带参重载、详情弹窗加载血缘链明细（records）。
 * 直接 mock @/lib/api（未引入 MSW）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { apiGet } from '@/lib/api'
import { AdminLineagePage } from './admin-lineage-page'

vi.mock('@/lib/api', () => ({
  apiGet: vi.fn(),
  errMsg: (_e: unknown, fallback: string) => fallback,
}))

const mockApiGet = vi.mocked(apiGet)

function makePos(overrides: Record<string, unknown> = {}) {
  return {
    position_name: '提示词工程师',
    jd_count: 3,
    source_count: 2,
    sources: ['boss', 'zhilian'],
    cities: ['上海', '杭州'],
    verified: true,
    confidence: 0.82,
    verified_skill_ratio: 0.9,
    unverified_skills: [],
    salary_median: 28000,
    salary_outlier: false,
    experience_divergence: 0.1,
    ...overrides,
  }
}

function positionsData(...items: unknown[]) {
  return {
    items,
    total: items.length,
    page: 1,
    size: 20,
    summary: { groups: 1, jd_count: 3, multi_source: 1, verified: 1, below_confidence: 0 },
  }
}

const detailData = {
  position_name: '提示词工程师',
  jd_count: 2,
  source_count: 2,
  sources: ['boss', 'zhilian'],
  cities: ['上海'],
  verified: true,
  confidence: 0.82,
  verified_skill_ratio: 0.9,
  unverified_skills: [],
  salary_median: 28000,
  salary_outlier: false,
  experience_divergence: 0.1,
  records: [
    {
      jd_id: 1,
      source: 'boss',
      source_url: 'https://boss.example/jd/1',
      crawled_at: '2026-08-20T10:00:00+08:00',
      city: '上海',
      salary: '25-35K·14薪',
      skills: ['Prompt 工程', '大模型评测'],
      is_duplicate: false,
    },
    {
      jd_id: 2,
      source: 'zhilian',
      source_url: '',
      crawled_at: '2026-08-19T09:30:00+08:00',
      city: '上海',
      salary: '24-32K',
      skills: ['提示词'],
      is_duplicate: true,
    },
  ],
}

afterEach(() => {
  cleanup()
  mockApiGet.mockReset()
})

describe('AdminLineagePage 数据血缘', () => {
  it('positions GET 渲染总览统计与岗位行', async () => {
    mockApiGet.mockResolvedValue(positionsData(makePos()))
    render(<AdminLineagePage />)
    expect(await screen.findByText('提示词工程师')).toBeInTheDocument()
    // 总览统计值
    expect(screen.getByText('82%')).toBeInTheDocument()
    expect(screen.getByText('2 源')).toBeInTheDocument()
    // 薪资平滑口径（跨城市平滑后市场月薪）
    expect(screen.getByText('28,000/月')).toBeInTheDocument()
  })

  it('空数据展示占位文案', async () => {
    mockApiGet.mockResolvedValue(positionsData())
    render(<AdminLineagePage />)
    expect(await screen.findByText('暂无血缘分组')).toBeInTheDocument()
  })

  it('请求失败展示错误文案', async () => {
    mockApiGet.mockRejectedValue(new Error('network'))
    render(<AdminLineagePage />)
    expect(await screen.findByText(/血缘数据加载失败/)).toBeInTheDocument()
  })

  it('仅已验证过滤：点击后带 verified=true 重新请求', async () => {
    mockApiGet.mockResolvedValue(positionsData(makePos()))
    render(<AdminLineagePage />)
    await screen.findByText('提示词工程师')
    fireEvent.click(screen.getByRole('button', { name: /仅已验证/ }))
    await waitFor(() =>
      expect(mockApiGet).toHaveBeenLastCalledWith(
        expect.stringContaining('verified=true'),
      ),
    )
  })

  it('详情：点击溯源加载组级校验 + 证据 JD 血缘链', async () => {
    mockApiGet
      .mockResolvedValueOnce(positionsData(makePos()))
      .mockResolvedValueOnce(detailData)
    const { container } = render(<AdminLineagePage />)
    await screen.findByText('提示词工程师')
    fireEvent.click(screen.getByRole('button', { name: '溯源' }))
    // 详情弹窗：组级摘要 + 证据记录（薪资/来源/去重标记/溯源链接）
    expect(await screen.findByText('数据血缘 · 提示词工程师')).toBeInTheDocument()
    expect(screen.getByText('25-35K·14薪')).toBeInTheDocument()
    expect(screen.getByText('重复')).toBeInTheDocument()
    expect(screen.getByText('原始 JD ↗')).toHaveAttribute('href', 'https://boss.example/jd/1')
    // 多个证据时证据链表格可滚动（max-h + overflow-auto；DialogContent 经 portal 渲染到 body）
    const hasScroll = Array.from(document.querySelectorAll('div')).some(
      (d) =>
        typeof d.className === 'string' &&
        d.className.includes('max-h-') &&
        d.className.includes('overflow-auto'),
    )
    expect(hasScroll).toBe(true)
  })

  it('详情加载失败展示错误文案', async () => {
    mockApiGet
      .mockResolvedValueOnce(positionsData(makePos()))
      .mockRejectedValueOnce(new Error('network'))
    render(<AdminLineagePage />)
    await screen.findByText('提示词工程师')
    fireEvent.click(screen.getByRole('button', { name: '溯源' }))
    expect(await screen.findByText('详情加载失败，请重试')).toBeInTheDocument()
  })
})
