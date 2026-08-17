/**
 * 候选晋升审核 Tab 组件测试
 *
 * 覆盖：pending GET 渲染队列与统计、空态/错误态、approve/reject POST、
 * reason 必填校验、成功后队列刷新（loadQueue 二次请求）。
 * 直接 mock @/lib/api（未引入 MSW）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { apiGet, apiPost } from '@/lib/api'
import { CandidateReviewTab } from './candidate-review-tab'

vi.mock('@/lib/api', () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  errMsg: (_e: unknown, fallback: string) => fallback,
}))

const mockApiGet = vi.mocked(apiGet)
const mockApiPost = vi.mocked(apiPost)

function makeItem(overrides: Record<string, unknown> = {}) {
  return {
    id: 'pos_1',
    position_name: '提示词工程师',
    state: 'candidate',
    confidence: { final_confidence: 0.82 },
    evidence_refs: ['jd_1'],
    seed_matched: true,
    rag_matched: true,
    definition_draft: '负责大模型提示词设计与评测',
    detected_at: '2026-08-01T00:00:00Z',
    ...overrides,
  }
}

function pendingData(...items: unknown[]) {
  return { items, total: items.length, page: 1, size: 50 }
}

afterEach(() => {
  cleanup()
  mockApiGet.mockReset()
  mockApiPost.mockReset()
})

describe('CandidateReviewTab 候选晋升审核', () => {
  it('pending GET 成功渲染队列与来源徽标', async () => {
    mockApiGet.mockResolvedValue(
      pendingData(
        makeItem(),
        makeItem({
          id: 'pos_2',
          position_name: 'AI 产品经理',
          rag_matched: false,
          seed_matched: false,
          confidence: { final_confidence: 0.52 },
        }),
      ),
    )
    render(<CandidateReviewTab />)
    expect(await screen.findByText('提示词工程师')).toBeInTheDocument()
    expect(screen.getByText('AI 产品经理')).toBeInTheDocument()
    // 种子徽标仅在命中行渲染
    expect(screen.getByText('种子')).toBeInTheDocument()
    // RAG：统计卡标签 1 处 + 命中行徽标 1 处 = 2（仅 pos_1 rag_matched）
    expect(screen.getAllByText('RAG')).toHaveLength(2)
  })

  it('空队列展示占位文案', async () => {
    mockApiGet.mockResolvedValue(pendingData())
    render(<CandidateReviewTab />)
    expect(await screen.findByText('暂无待审核岗位')).toBeInTheDocument()
  })

  it('请求失败展示错误文案', async () => {
    mockApiGet.mockRejectedValue(new Error('network'))
    render(<CandidateReviewTab />)
    expect(await screen.findByText('审核队列加载失败')).toBeInTheDocument()
  })

  it('reason 必填：空原因点击批准仅提示，不发 POST', async () => {
    mockApiGet.mockResolvedValue(pendingData(makeItem()))
    render(<CandidateReviewTab />)
    await screen.findByText('提示词工程师')
    fireEvent.click(screen.getByRole('button', { name: '审核' }))
    expect(await screen.findByText('审核岗位：提示词工程师')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /批准晋升/ }))
    expect(await screen.findByText('请填写审核原因（reason 必填）')).toBeInTheDocument()
    expect(mockApiPost).not.toHaveBeenCalled()
  })

  it('批准晋升：POST 正确 URL/body 并刷新队列', async () => {
    mockApiGet.mockResolvedValue(pendingData(makeItem()))
    mockApiPost.mockResolvedValue({})
    render(<CandidateReviewTab />)
    await screen.findByText('提示词工程师')
    fireEvent.click(screen.getByRole('button', { name: '审核' }))
    await screen.findByText('审核岗位：提示词工程师')
    fireEvent.change(screen.getByPlaceholderText('填写批准或驳回的原因（必填）'), {
      target: { value: '技术栈前沿' },
    })
    fireEvent.click(screen.getByRole('button', { name: /批准晋升/ }))
    await waitFor(() =>
      expect(mockApiPost).toHaveBeenCalledWith('/admin/positions/pos_1/review', {
        action: 'approve',
        reason: '技术栈前沿',
      }),
    )
    expect(await screen.findByText(/已批准晋升 emerging：提示词工程师/)).toBeInTheDocument()
    // 成功后 loadQueue() 再次请求（初始 1 次 + 刷新 1 次）
    await waitFor(() => expect(mockApiGet).toHaveBeenCalledTimes(2))
  })

  it('驳回：POST action=reject 并显示驳回通知', async () => {
    mockApiGet.mockResolvedValue(pendingData(makeItem()))
    mockApiPost.mockResolvedValue({})
    render(<CandidateReviewTab />)
    await screen.findByText('提示词工程师')
    fireEvent.click(screen.getByRole('button', { name: '审核' }))
    await screen.findByText('审核岗位：提示词工程师')
    fireEvent.change(screen.getByPlaceholderText('填写批准或驳回的原因（必填）'), {
      target: { value: '需求口径不符' },
    })
    fireEvent.click(screen.getByRole('button', { name: /驳回/ }))
    await waitFor(() =>
      expect(mockApiPost).toHaveBeenCalledWith('/admin/positions/pos_1/review', {
        action: 'reject',
        reason: '需求口径不符',
      }),
    )
    expect(await screen.findByText(/已驳回（rejected）：提示词工程师/)).toBeInTheDocument()
  })
})
