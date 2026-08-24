/**
 * LLM 决策与验收页测试（PR7b：只读管理页）
 *
 * 覆盖：汇总卡片渲染、决策列表行展示（域/状态/风险档/置信度）、空态与错误态。
 * 直接 mock @/lib/api（未引入 MSW）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { apiGet } from '@/lib/api'
import { AdminLlmDecisionsPage } from './admin-llm-decisions-page'

vi.mock('@/lib/api', () => ({
  apiGet: vi.fn(),
  errMsg: (_e: unknown, fallback: string) => fallback,
}))

const mockApiGet = vi.mocked(apiGet)

function makeDecision(overrides: Record<string, unknown> = {}) {
  return {
    id: '11111111-1111-1111-1111-111111111111',
    domain: 'governance',
    entity_type: 'skill',
    entity_id: '测试词A',
    run_id: 'dict_guard:2026-08-24',
    env: 'production',
    input_hash: 'a'.repeat(64),
    provider: 'deepseek',
    model: 'deepseek-v4-flash',
    structured_output: { action: 'add_stopword' },
    confidence: 0.95,
    gate_result: 'pass',
    risk_tier: 'R1',
    status: 'auto_applied',
    created_at: '2026-08-24T12:00:00+00:00',
    ...overrides,
  }
}

function afterRender(decisions: unknown[], totals: Record<string, number>) {
  mockApiGet.mockImplementation(async (url: string) => {
    if (url.startsWith('/admin/llm-decisions/summary')) {
      return { by_domain: [{ domain: 'governance', by_status: { auto_applied: 1 }, total: 1 }], totals }
    }
    return { items: decisions, total: decisions.length, limit: 20, offset: 0 }
  })
  return render(<AdminLlmDecisionsPage />)
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('AdminLlmDecisionsPage', () => {
  it('渲染汇总卡片与决策行', async () => {
    afterRender([makeDecision()], { proposal: 0, auto_applied: 1, blocked: 0, shadow: 2, records: 3 })
    expect(screen.getByText('LLM 决策与验收')).toBeTruthy()
    await waitFor(() => expect(screen.getByText('自动化治理')).toBeTruthy())
    expect(screen.getAllByText('auto_applied').length).toBeGreaterThan(0)
    expect(screen.getByText('R1')).toBeTruthy()
    expect(screen.getByText('skill:测试词A')).toBeTruthy()
    expect(screen.getByText('deepseek')).toBeTruthy()
  })

  it('空态提示', async () => {
    afterRender([], { proposal: 0, auto_applied: 0, blocked: 0, shadow: 0, records: 0 })
    await waitFor(() => expect(screen.getByText('无匹配的决策记录')).toBeTruthy())
  })

  it('错误态提示', async () => {
    mockApiGet.mockRejectedValue(new Error('network'))
    render(<AdminLlmDecisionsPage />)
    await waitFor(() =>
      expect(screen.getByText(/决策记录加载失败/)).toBeTruthy(),
    )
  })
})