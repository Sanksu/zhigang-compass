/**
 * 岗位审核「总览」Tab 测试：只读聚合面板渲染 + 点击深链
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { ApprovalOverviewTab } from './approval-overview-tab'

const navigate = vi.fn()
vi.mock('react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router')>()
  return { ...actual, useNavigate: () => navigate }
})

vi.mock('@/lib/api', () => ({
  apiGet: vi.fn().mockResolvedValue({
    summary: { total_pending: 13, total_review: 4, total_approved: 107 },
    streams: [
      { id: 'candidate_promotion', label: '候选晋升', route: '/admin/review?tab=candidate', description: 'candidate → emerging / rejected', pending: 8, review: 4, approved: 58 },
      { id: 'dict_guard', label: '字典守卫提案', route: '/admin/review/dict', description: 'stopword/protect/清理提案', pending: 3, review: 0, approved: 18 },
      { id: 'evolution', label: '演化晋级', route: '/admin/review?tab=evolution', description: 'emerging → stable / declining', pending: 2, review: 0, approved: 31 },
    ],
  }),
  errMsg: () => 'mock-error',
}))

afterEach(cleanup)

describe('ApprovalOverviewTab', () => {
  it('渲染汇总 KPI 与三阶段金额（按 stage 计数过滤各流）', async () => {
    render(
      <MemoryRouter>
        <ApprovalOverviewTab />
      </MemoryRouter>,
    )
    // 等待异步加载（KPI 标签文案唯一）
    await screen.findByText('低置信阻断 · 证据不足')
    // 顶栏汇总 / 阶段列头数字（同值可跨卡片/列出现，用 getAllByText）
    expect(screen.getAllByText('13').length).toBeGreaterThan(0)
    expect(screen.getAllByText('4').length).toBeGreaterThan(0)
    expect(screen.getAllByText('107').length).toBeGreaterThan(0)
    // 待办列：包含全部 pending>0 的三条流（同流可跨阶段出现）
    expect(screen.getAllByText('候选晋升').length).toBeGreaterThan(0)
    expect(screen.getAllByText('字典守卫提案').length).toBeGreaterThan(0)
    expect(screen.getAllByText('演化晋级').length).toBeGreaterThan(0)
  })

  it('点击待办芯片深链到该审批流原审核页路由', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <ApprovalOverviewTab />
      </MemoryRouter>,
    )
    await screen.findByText('低置信阻断 · 证据不足')
    await user.click(screen.getAllByText('候选晋升')[0])
    expect(navigate).toHaveBeenCalledWith('/admin/review?tab=candidate')
  })
})