/**
 * 岗位审核页路由壳测试（拆分后）
 *
 * 四类审核 Tab 已拆至 components/admin/review/*，本测试把四个子组件 mock 掉，
 * 验证受控 Tabs 壳：四个 Tab 标签齐全、仅激活 Tab 挂载、切换时挂载对应子组件
 * （保持原有"仅生效 Tab 渲染"的条件挂载语义）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { AdminReviewPage } from './admin-review-page'

vi.mock('@/components/admin/review/candidate-review-tab', () => ({
  CandidateReviewTab: () => <div>candidate-tab-mock</div>,
}))
vi.mock('@/components/admin/review/evolution-review-tab', () => ({
  EvolutionReviewTab: () => <div>evolution-tab-mock</div>,
}))
vi.mock('@/components/admin/review/position-editor-tab', () => ({
  PositionEditorTab: () => <div>position-editor-tab-mock</div>,
}))
vi.mock('@/components/admin/review/technology-watch-tab', () => ({
  TechnologyWatchTab: () => <div>technology-watch-tab-mock</div>,
}))

afterEach(cleanup)

describe('AdminReviewPage 路由壳', () => {
  it('渲染四个 Tab 标签', () => {
    render(<AdminReviewPage />)
    expect(screen.getByText('候选晋升审核')).toBeInTheDocument()
    expect(screen.getByText('演化审核（emerging）')).toBeInTheDocument()
    expect(screen.getByText('岗位人工编辑')).toBeInTheDocument()
    expect(screen.getByText('发现观察池')).toBeInTheDocument()
  })

  it('默认挂载候选晋升 Tab，切换时挂载对应子组件（仅激活 Tab 渲染）', () => {
    render(<AdminReviewPage />)
    // 默认 candidate 激活
    expect(screen.getByText('candidate-tab-mock')).toBeInTheDocument()
    expect(screen.queryByText('evolution-tab-mock')).not.toBeInTheDocument()
    // Radix Tabs 在 onMouseDown 激活（v1.1.x），用 mouseDown 驱动切换
    fireEvent.mouseDown(screen.getByText('演化审核（emerging）'))
    expect(screen.getByText('evolution-tab-mock')).toBeInTheDocument()
    expect(screen.queryByText('candidate-tab-mock')).not.toBeInTheDocument()
    // 切到人工编辑
    fireEvent.mouseDown(screen.getByText('岗位人工编辑'))
    expect(screen.getByText('position-editor-tab-mock')).toBeInTheDocument()
    expect(screen.queryByText('evolution-tab-mock')).not.toBeInTheDocument()
    // 切到观察池
    fireEvent.mouseDown(screen.getByText('发现观察池'))
    expect(screen.getByText('technology-watch-tab-mock')).toBeInTheDocument()
    // 切回候选晋升
    fireEvent.mouseDown(screen.getByText('候选晋升审核'))
    expect(screen.getByText('candidate-tab-mock')).toBeInTheDocument()
    expect(screen.queryByText('technology-watch-tab-mock')).not.toBeInTheDocument()
  })
})
