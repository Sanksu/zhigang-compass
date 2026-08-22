/**
 * 图谱算法分析面板组件测试
 *
 * 覆盖：PageRank 排行渲染、技能簇 LLM 命名优先/规则标签兜底、LLM 徽标、
 * rationale/splits 展示、展开更多、最短路径查询渲染、onFocusSkill 回调。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { apiGet } from '@/lib/api'
import { GraphAnalysisPanel } from './graph-analysis-panel'

vi.mock('@/lib/api', () => ({
  apiGet: vi.fn(),
}))

const mockApiGet = vi.mocked(apiGet)

function mockClusters(clusters: unknown[], clusterCount?: number) {
  mockApiGet.mockImplementation((url: string) => {
    if (String(url).includes('/graph/algorithms/pagerank')) {
      return Promise.resolve({ skills: [{ id: 'sk_1', name: 'Python', score: 0.123 }] })
    }
    if (String(url).includes('/graph/algorithms/skill-clusters')) {
      return Promise.resolve({ clusters, cluster_count: clusterCount ?? clusters.length })
    }
    return Promise.resolve({ from: '', to: '', path: [] })
  })
}

afterEach(() => {
  cleanup()
  mockApiGet.mockReset()
})

describe('GraphAnalysisPanel', () => {
  beforeEach(() => {
    mockClusters([])
  })

  it('簇标题优先展示 LLM 语义命名，其次规则标签', async () => {
    mockClusters([
      {
        id: 1,
        size: 3,
        label: 'Python·Django·PostgreSQL',
        needs_llm: true,
        triggers: ['no_dominant_skill'],
        llm: { coherent: true, cluster_name: 'Web 后端技术栈', rationale: '技能高度共现', splits: [] },
        skills: [
          { id: 'sk_1', name: 'Python' },
          { id: 'sk_2', name: 'Django' },
          { id: 'sk_3', name: 'PostgreSQL' },
        ],
      },
      {
        id: 2,
        size: 2,
        label: 'Kubernetes·Docker',
        needs_llm: false,
        triggers: [],
        llm: null,
        skills: [
          { id: 'sk_4', name: 'Kubernetes' },
          { id: 'sk_5', name: 'Docker' },
        ],
      },
    ])
    render(<GraphAnalysisPanel skills={[{ id: 'sk_1', name: 'Python' }]} onFocusSkill={vi.fn()} />)
    // 有 LLM 命名 → 优先展示 LLM 名称
    expect(await screen.findByText('Web 后端技术栈')).toBeInTheDocument()
    // 无 LLM 命名 → 规则标签兜底
    expect(screen.getByText('Kubernetes·Docker')).toBeInTheDocument()
  })

  it('needs_llm 簇渲染 LLM 徽标，普通簇不渲染', async () => {
    mockClusters([
      {
        id: 1,
        size: 2,
        label: 'Python·Django',
        needs_llm: true,
        triggers: ['no_dominant_skill'],
        llm: { coherent: true, cluster_name: '后端栈', rationale: null, splits: [] },
        skills: [
          { id: 'sk_1', name: 'Python' },
          { id: 'sk_2', name: 'Django' },
        ],
      },
      {
        id: 2,
        size: 2,
        label: 'React·Vue',
        needs_llm: false,
        triggers: [],
        llm: null,
        skills: [
          { id: 'sk_6', name: 'React' },
          { id: 'sk_7', name: 'Vue' },
        ],
      },
    ])
    render(<GraphAnalysisPanel skills={[]} onFocusSkill={vi.fn()} />)
    await screen.findByText('后端栈')
    const badges = screen.getAllByText('LLM')
    expect(badges).toHaveLength(1)
  })

  it('展开簇展示 LLM rationale 与建议拆分', async () => {
    mockClusters([
      {
        id: 1,
        size: 3,
        label: 'Python·ROS·目标检测',
        needs_llm: true,
        triggers: ['cross_category'],
        llm: {
          coherent: false,
          cluster_name: null,
          rationale: '混合了后端与机器人视觉技能',
          splits: ['Web 后端', '机器视觉'],
        },
        skills: [
          { id: 'sk_1', name: 'Python' },
          { id: 'sk_8', name: 'ROS' },
          { id: 'sk_9', name: '目标检测' },
        ],
      },
    ])
    render(<GraphAnalysisPanel skills={[]} onFocusSkill={vi.fn()} />)
    // 无 LLM 命名（coherent=false）→ 规则标签兜底
    fireEvent.click(await screen.findByText('Python·ROS·目标检测'))
    expect(await screen.findByText('混合了后端与机器人视觉技能')).toBeInTheDocument()
    expect(screen.getByText(/建议拆分：Web 后端、机器视觉/)).toBeInTheDocument()
  })

  it('超过 12 个簇显示「展开更多」并支持全量展开', async () => {
    const many = Array.from({ length: 16 }, (_, i) => ({
      id: i + 1,
      size: 2,
      label: `技能簇${i + 1}`,
      needs_llm: false,
      triggers: [],
      llm: null,
      skills: [
        { id: `sk_${i}a`, name: `技能${i + 1}A` },
        { id: `sk_${i}b`, name: `技能${i + 1}B` },
      ],
    }))
    mockClusters(many, 16)
    render(<GraphAnalysisPanel skills={[]} onFocusSkill={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('技能簇1')).toBeInTheDocument())
    // 默认只展示前 12 个
    expect(screen.queryByText('技能簇13')).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('展开更多（4）'))
    expect(await screen.findByText('技能簇13')).toBeInTheDocument()
    expect(screen.getByText('技能簇16')).toBeInTheDocument()
  })

  it('层级元数据渲染选择器，切换后带 level 重新请求', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (String(url).includes('/graph/algorithms/pagerank')) {
        return Promise.resolve({ skills: [] })
      }
      if (String(url).includes('level=1')) {
        // 切换到 L1：返回更粗层簇
        return Promise.resolve({
          clusters: [{ id: 9, size: 3, label: '粗层簇', needs_llm: false, triggers: [], llm: null, skills: [{ id: 'sk_9', name: '粗层技能' }] }],
          cluster_count: 1,
          levels: [
            { level: 0, cluster_count: 4, modularity: 0.1 },
            { level: 1, cluster_count: 2, modularity: 0.2 },
          ],
        })
      }
      return Promise.resolve({
        clusters: [
          { id: 1, size: 2, label: 'Python·Django', needs_llm: false, triggers: [], llm: null, skills: [{ id: 'sk_1', name: 'Python' }] },
        ],
        cluster_count: 1,
        levels: [
          { level: 0, cluster_count: 4, modularity: 0.1 },
          { level: 1, cluster_count: 2, modularity: 0.2 },
        ],
      })
    })
    render(<GraphAnalysisPanel skills={[]} onFocusSkill={vi.fn()} />)
    // 层级选择器渲染（最优层 + L0 + L1）；DOM 顺序为 [层级, 起点, 终点]，层级为第 1 个
    await waitFor(() => expect(screen.getAllByRole('combobox')).toHaveLength(3))
    const selects = screen.getAllByRole('combobox')
    expect(screen.getByText('层级（dendrogram 粗→细）')).toBeInTheDocument()
    // 切到 L1 → 请求带 level=1 且簇列表刷新
    fireEvent.change(selects[0], { target: { value: '1' } })
    expect(await screen.findByText('粗层簇')).toBeInTheDocument()
    expect(mockApiGet).toHaveBeenCalledWith(expect.stringContaining('level=1'))
  })

  it('PageRank 排行渲染并触发 onFocusSkill', async () => {
    mockClusters([])
    mockApiGet.mockImplementation((url: string) => {
      if (String(url).includes('/graph/algorithms/pagerank')) {
        return Promise.resolve({
          skills: [
            { id: 'sk_1', name: 'Python', score: 0.345 },
            { id: 'sk_2', name: 'Kubernetes', score: 0.123 },
          ],
        })
      }
      return Promise.resolve({ clusters: [], cluster_count: 0 })
    })
    const onFocus = vi.fn()
    render(<GraphAnalysisPanel skills={[]} onFocusSkill={onFocus} />)
    fireEvent.click(await screen.findByText('Python'))
    expect(onFocus).toHaveBeenCalledWith('sk_1', 'Python')
  })

  it('最短路径查询渲染路径节点序列', async () => {
    mockClusters([])
    render(
      <GraphAnalysisPanel
        skills={[
          { id: 'sk_1', name: 'Python' },
          { id: 'sk_2', name: 'Django' },
        ]}
        onFocusSkill={vi.fn()}
      />,
    )
    // 等待面板就绪后选择起止技能（两个 select 为 combobox，按顺序取）
    await waitFor(() => expect(screen.getByText('PageRank Top-20')).toBeInTheDocument())
    const [fromSelect, toSelect] = screen.getAllByRole('combobox')
    fireEvent.change(fromSelect, { target: { value: 'sk_1' } })
    fireEvent.change(toSelect, { target: { value: 'sk_2' } })
    mockApiGet.mockResolvedValueOnce({
      from: 'sk_1',
      to: 'sk_2',
      path: [
        { id: 'sk_1', name: 'Python', type: 'Skill' },
        { id: 'pos_1', name: '后端开发工程师', type: 'Position' },
        { id: 'sk_2', name: 'Django', type: 'Skill' },
      ],
    })
    fireEvent.click(screen.getByText('查询路径'))
    expect(await screen.findByText('后端开发工程师')).toBeInTheDocument()
    expect(screen.getByText('可达路径（3 个节点）：')).toBeInTheDocument()
  })

  it('最短路径无路径时展示错误文案', async () => {
    mockClusters([])
    render(
      <GraphAnalysisPanel
        skills={[
          { id: 'sk_1', name: 'Python' },
          { id: 'sk_9', name: 'ROS' },
        ]}
        onFocusSkill={vi.fn()}
      />,
    )
    await waitFor(() => expect(screen.getByText('PageRank Top-20')).toBeInTheDocument())
    const [fromSelect, toSelect] = screen.getAllByRole('combobox')
    fireEvent.change(fromSelect, { target: { value: 'sk_1' } })
    fireEvent.change(toSelect, { target: { value: 'sk_9' } })
    mockApiGet.mockRejectedValueOnce(new Error('404'))
    fireEvent.click(screen.getByText('查询路径'))
    expect(await screen.findByText('两技能间不存在 ≤6 跳的可达路径')).toBeInTheDocument()
  })
})
