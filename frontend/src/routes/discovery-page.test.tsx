/**
 * 新岗位发现页测试（/discovery）— mock apiGet，验证空态/有数据态/状态徽标/技能增减 Tab。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { apiGet } from '@/lib/api'
import { DiscoveryPage } from './discovery-page'

vi.mock('@/lib/api', () => ({ apiGet: vi.fn() }))

const mockedApiGet = apiGet as unknown as ReturnType<typeof vi.fn>

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/discovery']}>
      <DiscoveryPage />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('DiscoveryPage', () => {
  beforeEach(() => {
    mockedApiGet.mockResolvedValue({ candidates: [], total: 0 })
  })

  it('加载完成且无候选 → 空态', async () => {
    renderPage()
    expect(screen.getByText('加载近期新岗位…')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('近 30 天无新岗位候选')).toBeInTheDocument())
    expect(mockedApiGet).toHaveBeenCalledWith('/discovery/recent', expect.anything())
  })

  it('有候选 → 展示岗位名 + 状态徽标 + 技能', async () => {
    mockedApiGet.mockResolvedValue({
      total: 1,
      candidates: [
        {
          position_id: 'pos_1',
          position_name: '后端工程师',
          state: 'stable',
          detected_at: '2026-08-26T10:00:00+08:00',
          definition_draft: '定义',
          confidence: { grounding: 0.8 },
          skills: { must: [{ skill_id: 'sk_1', skill_name: 'Python', necessity: 'must' }], nice: [], soft: [] },
          skill_pending: false,
        },
      ],
    })
    renderPage()
    await waitFor(() => expect(screen.getByText('后端工程师')).toBeInTheDocument())
    expect(screen.getByText('稳定')).toBeInTheDocument()
    // 展开技能明细
    await userEvent.click(screen.getByText('后端工程师'))
    expect(screen.getByText('Python')).toBeInTheDocument()
  })

  it('candidate 态显示技能待审核标注', async () => {
    mockedApiGet.mockResolvedValue({
      total: 1,
      candidates: [
        {
          position_id: null,
          position_name: '量子运维工程师',
          state: 'candidate',
          detected_at: '2026-08-26T10:00:00+08:00',
          definition_draft: '',
          confidence: null,
          skills: null,
          skill_pending: true,
        },
      ],
    })
    renderPage()
    await waitFor(() =>
      expect(screen.getByText('量子运维工程师')).toBeInTheDocument(),
    )
    // 「技能待聚合/待审核」为跨 span 文案，getAllByText 宽松匹配取首个
    expect(screen.getAllByText(/技能待聚合/).length).toBeGreaterThan(0)
    expect(screen.getByText('候选·待审核')).toBeInTheDocument()
  })

  it('切到技能增减 Tab → 显示选择岗位下拉', async () => {
    mockedApiGet.mockResolvedValue({
      total: 1,
      candidates: [
        {
          position_id: 'pos_1',
          position_name: '后端工程师',
          state: 'stable',
          detected_at: '2026-08-26T10:00:00+08:00',
          definition_draft: '',
          confidence: null,
          skills: { must: [], nice: [], soft: [] },
          skill_pending: false,
        },
      ],
    })
    renderPage()
    await waitFor(() => expect(screen.getByText('后端工程师')).toBeInTheDocument())
    await userEvent.click(screen.getByText('技能增减'))
    expect(screen.getByText('选择岗位…')).toBeInTheDocument()
  })
})
