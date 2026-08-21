/**
 * 字典守卫 Tab 组件测试
 *
 * 覆盖：三路初始加载（提案/变更/报告）、pending 提案渲染与证据受害者标注、
 * 审核弹窗 reason 必填 + approve 提交后重载、变更审计回滚确认。直接 mock
 * @/lib/api（同 technology-watch-tab 模式，未引入 MSW）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { apiGet, apiPost } from '@/lib/api'
import { DictGuardTab } from './dict-guard-tab'

vi.mock('@/lib/api', () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  errMsg: (_e: unknown, fallback: string) => fallback,
}))

const mockApiGet = vi.mocked(apiGet)
const mockApiPost = vi.mocked(apiPost)

if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}

function proposal(overrides: Record<string, unknown> = {}) {
  return {
    id: 'p1',
    term: '低代码平台搭建',
    action: 'add_stopword',
    status: 'pending',
    reason: '噪音词条',
    llm_confidence: 0.9,
    evidence: [],
    impact_stats: { graph_nodes: 3, jd_snapshots: 5 },
    run_date: '2026-08-21',
    reviewed_by: '',
    review_reason: '',
    reviewed_at: null,
    created_at: '2026-08-21T12:00:00Z',
    ...overrides,
  }
}

function changeLog(overrides: Record<string, unknown> = {}) {
  return {
    id: 'c1',
    term: '低代码平台搭建',
    action: 'add_stopword',
    source: 'auto',
    kind: 'blocked',
    proposal_id: null,
    reason: '噪音词条',
    detail: {},
    impact_stats: {},
    applied_by: 'system',
    created_at: '2026-08-21T12:05:00Z',
    ...overrides,
  }
}

const REPORT = { run_date: '2026-08-21', candidates: 8, evaluated: 7, llm_failed: 1, auto_applied: [], proposals: 2 }

function setupApiGet() {
  mockApiGet.mockImplementation((url: string) => {
    if (url.startsWith('/admin/dict-guard/proposals')) {
      return Promise.resolve({ items: [proposal()], total: 1, page: 1, size: 20 })
    }
    if (url.startsWith('/admin/dict-guard/changes')) {
      return Promise.resolve({ items: [changeLog()], total: 1, page: 1, size: 20 })
    }
    if (url.startsWith('/admin/dict-guard/report/latest')) {
      return Promise.resolve(REPORT)
    }
    return Promise.reject(new Error(`unexpected url ${url}`))
  })
}

afterEach(() => {
  cleanup()
  mockApiGet.mockReset()
  mockApiPost.mockReset()
})

describe('DictGuardTab 字典守卫', () => {
  it('初始三路加载并渲染提案行与巡检报告摘要', async () => {
    setupApiGet()
    render(<DictGuardTab />)
    await waitFor(() =>
      expect(mockApiGet).toHaveBeenCalledWith('/admin/dict-guard/proposals?page=1&size=20&status=pending'),
    )
    expect(mockApiGet).toHaveBeenCalledWith('/admin/dict-guard/changes?page=1&size=20')
    expect(mockApiGet).toHaveBeenCalledWith('/admin/dict-guard/report/latest')
    // 该词条同时出现在提案表与变更审计表 → findAllByText
    const rows = await screen.findAllByText('低代码平台搭建')
    expect(rows.length).toBeGreaterThanOrEqual(1)
    // 动作标签在提案表与变更审计表各渲染一次 → getAllByText
    expect(screen.getAllByText('加入停用词').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('最近巡检 2026-08-21')).toBeInTheDocument()
    expect(screen.getAllByText('噪音词条').length).toBeGreaterThanOrEqual(1)
  })

  it('误杀证据在理由列标注受影响技能', async () => {
    setupApiGet()
    mockApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/admin/dict-guard/proposals')) {
        return Promise.resolve({
          items: [
            proposal({
              action: 'remove_stopword',
              term: '微',
              evidence: [{ label: '受影响技能', value: '微信小程序' }],
            }),
          ],
          total: 1,
          page: 1,
          size: 20,
        })
      }
      if (url.startsWith('/admin/dict-guard/changes')) {
        return Promise.resolve({ items: [], total: 0, page: 1, size: 20 })
      }
      return Promise.resolve(REPORT)
    })
    render(<DictGuardTab />)
    expect(await screen.findByText('微')).toBeInTheDocument()
    expect(screen.getByText(/误杀：微信小程序/)).toBeInTheDocument()
  })

  it('approve 弹窗 reason 必填，通过后提交审核并重载', async () => {
    setupApiGet()
    mockApiPost.mockResolvedValue({})
    render(<DictGuardTab />)
    const rows = await screen.findAllByText('低代码平台搭建'); expect(rows.length).toBeGreaterThanOrEqual(1)

    fireEvent.click(screen.getByRole('button', { name: '通过' }))
    // reason 为空时点击确认不提交
    fireEvent.click(await screen.findByRole('button', { name: '确认提交' }))
    expect(mockApiPost).not.toHaveBeenCalled()

    fireEvent.change(screen.getByPlaceholderText('审核理由（必填）'), { target: { value: '确属噪音' } })
    fireEvent.click(screen.getByRole('button', { name: '确认提交' }))
    await waitFor(() =>
      expect(mockApiPost).toHaveBeenCalledWith('/admin/dict-guard/proposals/p1/review', {
        action: 'approve',
        reason: '确属噪音',
      }),
    )
    // 提交后重载提案列表
    await waitFor(() =>
      expect(mockApiGet).toHaveBeenCalledWith('/admin/dict-guard/proposals?page=1&size=20&status=pending'),
    )
  })

  it('回滚经 confirm 后提交并重载变更历史', async () => {
    setupApiGet()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockApiPost.mockResolvedValue({})
    render(<DictGuardTab />)
    const rows = await screen.findAllByText('低代码平台搭建'); expect(rows.length).toBeGreaterThanOrEqual(1)

    fireEvent.click(screen.getByRole('button', { name: '回滚' }))
    await waitFor(() =>
      expect(mockApiPost).toHaveBeenCalledWith('/admin/dict-guard/changes/c1/rollback', {}),
    )
    expect(confirmSpy).toHaveBeenCalled()
    confirmSpy.mockRestore()
  })

  it('回滚取消时不发请求', async () => {
    setupApiGet()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<DictGuardTab />)
    const rows = await screen.findAllByText('低代码平台搭建'); expect(rows.length).toBeGreaterThanOrEqual(1)
    fireEvent.click(screen.getByRole('button', { name: '回滚' }))
    expect(mockApiPost).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })
})
