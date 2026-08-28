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
    expect(screen.getByText('候选')).toBeInTheDocument()
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

  // ── 技能增减展示逻辑（08-28 优化：按名排序/未变折叠/空态/新岗位解读）──

  const deltaCandidate = {
    position_id: 'pos_1',
    position_name: '后端工程师',
    state: 'stable',
    detected_at: '2026-08-26T10:00:00+08:00',
    definition_draft: '',
    confidence: null,
    skills: { must: [], nice: [], soft: [] },
    skill_pending: false,
  }

  function mockDelta(delta: Record<string, unknown>) {
    mockedApiGet.mockImplementation((path: string) => {
      if (typeof path === 'string' && path.startsWith('/discovery/position-skills-delta')) {
        return Promise.resolve(delta)
      }
      return Promise.resolve({ total: 1, candidates: [deltaCandidate] })
    })
  }

  /** 进入技能增减 Tab 并选中岗位（Radix Select 在 jsdom 需关 pointerEvents 校验，0=跳过全部检查） */
  async function selectPosition(name = '后端工程师') {
    renderPage()
    await waitFor(() => expect(screen.getByText(name)).toBeInTheDocument())
    await userEvent.click(screen.getByText('技能增减'))
    await userEvent.click(screen.getByRole('combobox'), { pointerEventsCheck: 0 })
    await userEvent.click(screen.getByRole('option', { name }), { pointerEventsCheck: 0 })
    // delta 到达的通用标记：delta 请求已发且加载态消失（三组全空时无新增/移除区块）
    await waitFor(() =>
      expect(mockedApiGet).toHaveBeenCalledWith(
        expect.stringContaining('position-skills-delta'), expect.anything(),
      ),
    )
    await waitFor(() => expect(screen.queryByText('加载技能增减…')).not.toBeInTheDocument())
  }

  it('增减渲染：按技能名排序展示 + 快照日期 + 未变超量折叠/展开', async () => {
    mockDelta({
      position_id: 'pos_1',
      position_name: '后端工程师',
      from_version: 'gv_1',
      from_created_at: '2026-08-26T05:00:00+08:00',
      to_version: 'gv_2',
      to_created_at: '2026-08-28T05:00:00+08:00',
      added: [
        { skill_id: 'sk_2', skill_name: 'Zookeeper' },
        { skill_id: 'sk_1', skill_name: 'Alpine' },
      ],
      removed: [{ skill_id: 'sk_3', skill_name: '老技能' }],
      unchanged: Array.from({ length: 15 }, (_, i) => ({
        skill_id: `sk_u${i + 1}`,
        skill_name: `技能${String(i + 1).padStart(2, '0')}`,
      })),
    })
    await selectPosition()
    // 按技能名排序（后端按 skill_id）：Alpine 在 Zookeeper 之前
    const addedChips = screen.getAllByText(/^(Alpine|Zookeeper)$/)
    expect(addedChips[0]).toHaveTextContent('Alpine')
    expect(screen.getByText('移除（1）')).toBeInTheDocument()
    expect(screen.getByText('老技能')).toBeInTheDocument()
    // 未变折叠：默认仅展示前 12 个，展开后全量
    expect(screen.getByText('未变（15）')).toBeInTheDocument()
    expect(screen.getByText('技能01')).toBeInTheDocument()
    expect(screen.queryByText('技能13')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '展开全部 15 个' }))
    expect(screen.getByText('技能13')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '收起' })).toBeInTheDocument()
  })

  it('全新岗位：全部技能计为新增的解读提示 + 移除/未变空态', async () => {
    mockDelta({
      position_id: 'pos_1',
      position_name: '后端工程师',
      from_version: 'gv_1',
      from_created_at: '2026-08-26T05:00:00+08:00',
      to_version: 'gv_2',
      to_created_at: '2026-08-28T05:00:00+08:00',
      added: [{ skill_id: 'sk_1', skill_name: 'Python' }],
      removed: [],
      unchanged: [],
    })
    await selectPosition()
    expect(screen.getByText('该岗位为本期新入图谱，全部技能计为新增')).toBeInTheDocument()
    expect(screen.getByText('无移除')).toBeInTheDocument()
    expect(screen.getByText('无变化')).toBeInTheDocument()
  })

  it('三组全空 → 单行空态', async () => {
    mockDelta({
      position_id: 'pos_1',
      position_name: '后端工程师',
      from_version: 'gv_1',
      from_created_at: '2026-08-26T05:00:00+08:00',
      to_version: 'gv_2',
      to_created_at: '2026-08-28T05:00:00+08:00',
      added: [],
      removed: [],
      unchanged: [],
    })
    await selectPosition()
    expect(screen.getByText('该岗位最近两版快照间无技能数据')).toBeInTheDocument()
  })
})
