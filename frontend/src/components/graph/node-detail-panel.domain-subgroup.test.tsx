/**
 * NodeDetailPanel 域超节点「技术栈二级分组」区块测试。
 * 实证：domainSubgroups 传入时域超节点面板渲染分组区块（回应「面板文字未显示」排查）。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { NodeDetailPanel } from './node-detail-panel'
import type { NodeDetail } from './types'

vi.mock('@/lib/api', () => ({ apiGet: vi.fn().mockResolvedValue({ positions: [], prerequisites: [], courses: [], evidence: [] }) }))

afterEach(() => cleanup())

const domainNode: NodeDetail = {
  id: 'dom_2',
  name: '算法与智能系统',
  type: 'position',
  isDomain: true,
  memberCount: 14,
}

const baseProps = {
  node: domainNode,
  onToggleDomain: vi.fn(),
}

function renderPanel(overrides: Partial<Parameters<typeof NodeDetailPanel>[0]> = {}) {
  return render(<NodeDetailPanel {...baseProps} {...overrides} />)
}

describe('NodeDetailPanel 域超节点二级分组', () => {
  it('传入 domainSubgroups 时渲染「域内岗位 · 技术栈分组」区块与成员', () => {
    renderPanel({
      domainSubgroups: [
        { label: '大模型/LLM', positions: [{ id: 'p1', name: '大模型算法工程师' }] },
        { label: '计算机视觉', positions: [{ id: 'p2', name: '机器视觉算法工程师' }] },
      ],
      domainExpanded: true,
    })
    expect(screen.getByText('域内岗位 · 技术栈分组')).toBeInTheDocument()
    expect(screen.getByText('大模型/LLM')).toBeInTheDocument()
    expect(screen.getByText('大模型算法工程师')).toBeInTheDocument()
    expect(screen.getByText('计算机视觉')).toBeInTheDocument()
  })

  it('domainExpanded 时标题带「画布已展开」提示', () => {
    renderPanel({
      domainSubgroups: [{ label: 'Web前端', positions: [{ id: 'p9', name: '前端开发工程师' }] }],
      domainExpanded: true,
    })
    expect(screen.getByText(/画布已展开/)).toBeInTheDocument()
  })

  it('未传 domainSubgroups 时不渲染区块（非域超节点/数据未就绪）', () => {
    renderPanel({})
    expect(screen.queryByText('域内岗位 · 技术栈分组')).not.toBeInTheDocument()
  })

  it('普通岗位节点不渲染分组区块（仅域超节点分支）', () => {
    renderPanel({
      node: { id: 'p1', name: '大模型算法工程师', type: 'position' },
      domainSubgroups: [{ label: '大模型/LLM', positions: [{ id: 'p1', name: 'x' }] }],
    })
    expect(screen.queryByText('域内岗位 · 技术栈分组')).not.toBeInTheDocument()
  })
})
