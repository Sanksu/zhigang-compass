/**
 * 演化审核 Tab 组件测试
 *
 * 覆盖：初始双请求（evolution/pending + positions/declining）、演化 approve/reject PUT、
 * 衰退归档 reason 必填 + PUT + 刷新。直接 mock @/lib/api（未引入 MSW）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { apiGet, apiPut } from '@/lib/api'
import { EvolutionReviewTab } from './evolution-review-tab'

vi.mock('@/lib/api', () => ({
  apiGet: vi.fn(),
  apiPut: vi.fn(),
  errMsg: (_e: unknown, fallback: string) => fallback,
}))

const mockApiGet = vi.mocked(apiGet)
const mockApiPut = vi.mocked(apiPut)

function makeEvo() {
  return {
    id: 'pos_e1',
    position_name: 'AI 推理优化工程师',
    state: 'emerging',
    confidence: { final_confidence: 0.72 },
    evidence_refs: ['jd_2'],
    seed_matched: false,
    rag_matched: true,
    definition_draft: '推理性能优化岗位画像',
    detected_at: '2026-08-02T00:00:00Z',
  }
}

function makeDeclining() {
  return {
    id: 'pos_d1',
    position_name: '传统 BI 报表工程师',
    state: 'declining',
    confidence: { final_confidence: 0.42 },
    evidence_refs: ['jd_3'],
    detected_at: '2026-07-20T00:00:00Z',
  }
}

/** 按 URL 分发两种 GET 响应 */
function mockBoth(evo = [makeEvo()], declining = [makeDeclining()]) {
  mockApiGet.mockImplementation((url: string) => {
    if (String(url).includes('/admin/evolution/pending')) {
      return Promise.resolve({ items: evo, total: evo.length, page: 1, size: 50 })
    }
    return Promise.resolve({ items: declining, total: declining.length, page: 1, size: 50 })
  })
}

afterEach(() => {
  cleanup()
  mockApiGet.mockReset()
  mockApiPut.mockReset()
})

describe('EvolutionReviewTab 演化审核 + 衰退归档', () => {
  it('初始并发请求演化队列与衰退归档', async () => {
    mockBoth()
    render(<EvolutionReviewTab />)
    await waitFor(() => expect(mockApiGet).toHaveBeenCalledTimes(2))
    const urls = mockApiGet.mock.calls.map((c) => String(c[0]))
    expect(urls).toEqual(
      expect.arrayContaining(['/admin/evolution/pending', '/admin/positions/declining']),
    )
    expect(await screen.findByText('AI 推理优化工程师')).toBeInTheDocument()
    expect(screen.getByText('传统 BI 报表工程师')).toBeInTheDocument()
  })

  it('演化 approve：PUT 正确 URL/body 并刷新演化队列', async () => {
    mockBoth()
    mockApiPut.mockResolvedValue({})
    render(<EvolutionReviewTab />)
    await screen.findByText('AI 推理优化工程师')
    fireEvent.click(screen.getByRole('button', { name: '审核' }))
    await screen.findByText('演化审核：AI 推理优化工程师')
    fireEvent.change(screen.getByPlaceholderText('填写晋级/衰退的原因（可留空）'), {
      target: { value: '连续两期交易量上升' },
    })
    fireEvent.click(screen.getByRole('button', { name: /确认晋级 stable/ }))
    await waitFor(() =>
      expect(mockApiPut).toHaveBeenCalledWith('/admin/evolution/pos_e1/review', {
        action: 'approve',
        reason: '连续两期交易量上升',
      }),
    )
    expect(await screen.findByText(/已确认晋级 stable：AI 推理优化工程师/)).toBeInTheDocument()
  })

  it('演化 reject：PUT action=reject 并提示确认衰退', async () => {
    mockBoth()
    mockApiPut.mockResolvedValue({})
    render(<EvolutionReviewTab />)
    await screen.findByText('AI 推理优化工程师')
    fireEvent.click(screen.getByRole('button', { name: '审核' }))
    await screen.findByText('演化审核：AI 推理优化工程师')
    fireEvent.click(screen.getByRole('button', { name: /确认衰退/ }))
    await waitFor(() =>
      expect(mockApiPut).toHaveBeenCalledWith('/admin/evolution/pos_e1/review', {
        action: 'reject',
        reason: '',
      }),
    )
    expect(await screen.findByText(/已确认衰退 declining：AI 推理优化工程师/)).toBeInTheDocument()
  })

  it('归档 reason 必填：空原因仅提示，不发 PUT', async () => {
    mockBoth()
    render(<EvolutionReviewTab />)
    await screen.findByText('传统 BI 报表工程师')
    fireEvent.click(screen.getByRole('button', { name: /确认归档/ }))
    await screen.findByText('确认衰退归档：传统 BI 报表工程师')
    fireEvent.click(screen.getByRole('button', { name: /确认归档（终态）/ }))
    expect(await screen.findByText('归档必须填写 reason')).toBeInTheDocument()
    expect(mockApiPut).not.toHaveBeenCalled()
  })

  it('归档成功：PUT archive 并刷新衰退列表', async () => {
    mockBoth()
    mockApiPut.mockResolvedValue({})
    render(<EvolutionReviewTab />)
    await screen.findByText('传统 BI 报表工程师')
    fireEvent.click(screen.getByRole('button', { name: /确认归档/ }))
    await screen.findByText('确认衰退归档：传统 BI 报表工程师')
    fireEvent.change(screen.getByPlaceholderText('填写归档原因（状态机强制要求，写入审计日志）'), {
      target: { value: '三个窗口连续衰减' },
    })
    fireEvent.click(screen.getByRole('button', { name: /确认归档（终态）/ }))
    await waitFor(() =>
      expect(mockApiPut).toHaveBeenCalledWith('/admin/positions/pos_d1/archive', {
        reason: '三个窗口连续衰减',
      }),
    )
    expect(await screen.findByText(/已归档（终态）：传统 BI 报表工程师/)).toBeInTheDocument()
    // loadDeclining() 刷新 → declining GET 被调用两次（初始 + 刷新）
    const decliningCalls = mockApiGet.mock.calls.filter((c) =>
      String(c[0]).includes('/admin/positions/declining'),
    )
    await waitFor(() => expect(decliningCalls.length).toBe(2))
  })
})
