/**
 * 社区层级树组件测试（阶段三：层次化提取可视化）
 *
 * 覆盖：树数据渲染（mock echarts）、未同步空态提示、层级数展示。
 * echarts 在 jsdom 无 canvas，init 需 mock。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { apiGet } from '@/lib/api'
import { GraphCommunityTree } from './graph-community-tree'

vi.mock('@/lib/api', () => ({
  apiGet: vi.fn(),
}))

// echarts 在 jsdom 环境不可用（无 canvas），mock init/setOption/dispose。
// 组件已按需导入（echarts/core + TreeChart），mock 须对齐各模块路径
const chartMock = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }
vi.mock('echarts/core', () => ({
  init: vi.fn(() => chartMock),
  use: vi.fn(),
}))
vi.mock('echarts/charts', () => ({ TreeChart: {} }))
vi.mock('echarts/components', () => ({ TooltipComponent: {} }))
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }))

const mockApiGet = vi.mocked(apiGet)

const TREE = [
  {
    id: 'comm_1_0',
    name: 'Python·Django',
    level: 1,
    cluster_count: 1,
    modularity: 0.25,
    top_skills: ['Python'],
    children: [
      {
        id: 'comm_0_0',
        name: 'Python·Django',
        level: 0,
        cluster_count: 2,
        modularity: 0.1,
        top_skills: ['Python', 'Django'],
        children: [],
      },
    ],
  },
]

afterEach(() => {
  cleanup()
  mockApiGet.mockReset()
  chartMock.setOption.mockClear()
})

describe('GraphCommunityTree', () => {
  beforeEach(() => {
    mockApiGet.mockResolvedValue({ tree: [], levels: [] })
  })

  it('未同步时展示 sync 脚本提示', async () => {
    render(<GraphCommunityTree />)
    expect(await screen.findByText(/scripts\/sync_communities\.py/)).toBeInTheDocument()
  })

  it('有树数据时渲染 ECharts 并传入 tree series', async () => {
    mockApiGet.mockResolvedValue({ tree: TREE, levels: [0, 1] })
    render(<GraphCommunityTree />)
    // 层级数展示在描述中
    expect(await screen.findByText(/2 层/)).toBeInTheDocument()
    await waitFor(() => {
      expect(chartMock.setOption).toHaveBeenCalled()
    })
    const option = chartMock.setOption.mock.calls[0][0]
    expect(option.series[0].type).toBe('tree')
    expect(option.series[0].data).toEqual(TREE)
  })

  it('请求失败降级为空态（不阻塞图谱主功能）', async () => {
    mockApiGet.mockRejectedValueOnce(new Error('network'))
    render(<GraphCommunityTree />)
    expect(await screen.findByText(/scripts\/sync_communities\.py/)).toBeInTheDocument()
  })
})
