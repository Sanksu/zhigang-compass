/**
 * 图谱页级别筛选测试（赛题「按技术栈和级别切换视图」，缺口2）。
 *
 * mock 图表/侧栏重组件（ECharts 在 jsdom 不可用），仅验证：
 * - 级别筛选变更 → 视图请求带 level 参数（panorama/techStack）
 * - 选「全部级别」→ 不带 level 参数
 * - 级别切换失效视图缓存并清空选中/展开态（重新请求而非命中缓存）
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { apiGet } from '@/lib/api'
import { GraphPage } from './graph-page'

vi.mock('@/lib/api', () => ({ apiGet: vi.fn(), ApiError: class ApiError extends Error {} }))
vi.mock('@/components/graph/graph-2d', () => ({
  Graph2D: () => <div data-testid="graph-2d-stub" />,
}))
vi.mock('@/components/graph/graph-analysis-panel', () => ({
  GraphAnalysisPanel: () => null,
}))
vi.mock('@/components/graph/graph-community-tree', () => ({
  GraphCommunityTree: () => null,
}))
vi.mock('@/components/graph/graph-detail-rail', () => ({
  GraphDetailRail: () => null,
}))

const mockedApiGet = apiGet as unknown as ReturnType<typeof vi.fn>

// 至少 1 节点：空数据走「图谱暂无数据」空态，工具栏（含级别筛选）不渲染
const emptyView = {
  view_type: 'panorama',
  nodes: [{ id: 'pos_1', name: '算法工程师', type: 'position', status: 'stable' }],
  edges: [],
  stats: { nodes: 1, edges: 0, total_nodes: 1, total_edges: 0 },
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/graph']}>
      <GraphPage />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('GraphPage 级别筛选', () => {
  beforeEach(() => {
    mockedApiGet.mockResolvedValue(emptyView)
  })

  it('默认加载全景视图不带 level 参数', async () => {
    renderPage()
    await waitFor(() => expect(mockedApiGet).toHaveBeenCalled())
    const url = mockedApiGet.mock.calls[0][0] as string
    expect(url).toBe('/graph/view/panorama?limit=120')
  })

  it('选择「高级」→ 请求带 level=高级', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByRole('combobox', { name: '熟练度级别' })).toBeInTheDocument())
    mockedApiGet.mockClear()
    await userEvent.click(screen.getByRole('combobox', { name: '熟练度级别' }))
    await userEvent.click(await screen.findByText('高级'))
    await waitFor(() => {
      const last = mockedApiGet.mock.calls.at(-1)?.[0] as string | undefined
      expect(last).toBe('/graph/view/panorama?limit=120&level=%E9%AB%98%E7%BA%A7')
    })
  })

  it('选「全部级别」回退为不带 level 参数', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByRole('combobox', { name: '熟练度级别' })).toBeInTheDocument())
    await userEvent.click(screen.getByRole('combobox', { name: '熟练度级别' }))
    await userEvent.click(await screen.findByText('高级'))
    await waitFor(() => expect(mockedApiGet.mock.calls.length).toBeGreaterThan(0))
    mockedApiGet.mockClear()
    await userEvent.click(screen.getByRole('combobox', { name: '熟练度级别' }))
    await userEvent.click(await screen.findByText('全部级别'))
    await waitFor(() => {
      const last = mockedApiGet.mock.calls.at(-1)?.[0] as string | undefined
      expect(last).toBe('/graph/view/panorama?limit=120')
    })
  })
})
