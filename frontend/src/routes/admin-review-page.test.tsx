/**
 * 岗位审核页路由壳测试（拆分后）
 *
 * 三类审核 Tab 已拆至 components/admin/review/*，本测试把子组件 mock 掉，
 * 验证受控 Tabs 壳与旧 ?tab=watch/dict 重定向（08-27 watch/dict 迁独立路由）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { AdminReviewPage } from './admin-review-page'

vi.mock('@/components/admin/review/approval-overview-tab', () => ({
  ApprovalOverviewTab: () => <div>overview-tab-mock</div>,
}))
vi.mock('@/components/admin/review/candidate-review-tab', () => ({
  CandidateReviewTab: () => <div>candidate-tab-mock</div>,
}))
vi.mock('@/components/admin/review/evolution-review-tab', () => ({
  EvolutionReviewTab: () => <div>evolution-tab-mock</div>,
}))
vi.mock('@/components/admin/review/position-editor-tab', () => ({
  PositionEditorTab: () => <div>position-editor-tab-mock</div>,
}))

/** AdminReviewPage 依赖 useSearchParams，需 Router 上下文 */
const renderPage = (entry = '/admin/review') =>
  render(
    <MemoryRouter initialEntries={[entry]}>
      <AdminReviewPage />
    </MemoryRouter>,
  )

afterEach(cleanup)

describe('AdminReviewPage 路由壳', () => {
  it('渲染四个 Tab 标签（总览 + 三类审核；观察池/字典守卫已迁独立路由）', () => {
    renderPage()
    expect(screen.getByText('总览')).toBeInTheDocument()
    expect(screen.getByText('候选晋升审核')).toBeInTheDocument()
    expect(screen.getByText('演化审核（emerging）')).toBeInTheDocument()
    expect(screen.getByText('岗位人工编辑')).toBeInTheDocument()
    expect(screen.queryByText('发现观察池')).not.toBeInTheDocument()
    expect(screen.queryByText('字典守卫')).not.toBeInTheDocument()
  })

  it('默认挂载总览 Tab，切换时挂载对应子组件（仅激活 Tab 渲染）', () => {
    renderPage()
    expect(screen.getByText('overview-tab-mock')).toBeInTheDocument()
    expect(screen.queryByText('candidate-tab-mock')).not.toBeInTheDocument()
    fireEvent.mouseDown(screen.getByText('候选晋升审核'))
    expect(screen.getByText('candidate-tab-mock')).toBeInTheDocument()
    expect(screen.queryByText('overview-tab-mock')).not.toBeInTheDocument()
    fireEvent.mouseDown(screen.getByText('演化审核（emerging）'))
    expect(screen.getByText('evolution-tab-mock')).toBeInTheDocument()
    expect(screen.queryByText('candidate-tab-mock')).not.toBeInTheDocument()
    fireEvent.mouseDown(screen.getByText('岗位人工编辑'))
    expect(screen.getByText('position-editor-tab-mock')).toBeInTheDocument()
    expect(screen.queryByText('evolution-tab-mock')).not.toBeInTheDocument()
    fireEvent.mouseDown(screen.getByText('候选晋升审核'))
    expect(screen.getByText('candidate-tab-mock')).toBeInTheDocument()
  })

  it('旧 ?tab=dict 快捷链接重定向到独立路由 /admin/review/dict', () => {
    render(
      <MemoryRouter initialEntries={['/admin/review?tab=dict']}>
        <Routes>
          <Route path="/admin/review" element={<AdminReviewPage />} />
          <Route path="/admin/review/dict" element={<div>dict-route-marker</div>} />
        </Routes>
      </MemoryRouter>,
    )
    expect(screen.getByText('dict-route-marker')).toBeInTheDocument()
  })
})