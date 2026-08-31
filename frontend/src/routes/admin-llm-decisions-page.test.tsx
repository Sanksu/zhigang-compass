/**
 * LLM 决策与验收页测试（PR7b：只读管理页）
 *
 * 覆盖：汇总卡片渲染、决策列表行展示（域/状态/风险档/置信度）、空态与错误态。
 * 直接 mock @/lib/api（未引入 MSW）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
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
  return render(
    <MemoryRouter>
      <AdminLlmDecisionsPage />
    </MemoryRouter>,
  )
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
    render(
      <MemoryRouter>
        <AdminLlmDecisionsPage />
      </MemoryRouter>,
    )
    await waitFor(() =>
      expect(screen.getByText(/决策记录加载失败/)).toBeTruthy(),
    )
  })
})
describe('AdminLlmDecisionsPage 建议目标展示（方案①）', () => {
  it('skill_normalize 别名提案显示 → 目标与别名徽标', async () => {
    afterRender(
      [
        makeDecision({
          domain: 'skill_normalize',
          entity_type: 'skill',
          entity_id: '.NET Framework',
          status: 'proposal',
          structured_output: { action: 'merge', target_standard: '.NET', kind: 'alias', confidence: 0.95 },
        }),
      ],
      { proposal: 1, auto_applied: 0, blocked: 0, shadow: 0, records: 1 },
    )
    await waitFor(() =>
      expect(
        screen.getAllByText((_, el) => el?.textContent?.includes('.NET Framework') ?? false).length,
      ).toBeGreaterThan(0),
    )
    expect(screen.getAllByText('.NET').length).toBeGreaterThan(0)
    expect(screen.getByText('别名')).toBeTruthy()
  })

  it('决策页提供动态别名表跳转入口', async () => {
    afterRender([], { proposal: 0, auto_applied: 0, blocked: 0, shadow: 0, records: 0 })
    await waitFor(() => expect(screen.getByText('动态别名表')).toBeTruthy())
  })
})

describe('AdminLlmDecisionsPage 详情展开', () => {
  it('governance auto_applied 展开显示已执行副作用与决策输出', async () => {
    afterRender(
      [
        makeDecision({
          entity_type: 'course',
          entity_id: '2027年山西专升本系统督学班',
          structured_output: {
            action: 'remove_node',
            reason: '专升本督学班非技能培训且完全孤立',
            impact: { graph_nodes: 1, jd_snapshots: 0 },
          },
          evidence_refs: [{ label: '边数(入+出)', value: 0 }],
        }),
      ],
      { proposal: 0, auto_applied: 1, blocked: 0, shadow: 0, records: 1 },
    )
    await waitFor(() => expect(screen.getByText('course:2027年山西专升本系统督学班')).toBeTruthy())

    // 默认收起：副作用说明不可见
    expect(screen.queryByText('已执行的副作用')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '展开详情' }))
    expect(screen.getByText('已执行的副作用')).toBeTruthy()
    expect(screen.getByText(/删除图谱 课程 Course 节点/)).toBeTruthy()
    expect(screen.getByText('专升本督学班非技能培训且完全孤立')).toBeTruthy()
    expect(screen.getByText(/状态说明/)).toBeTruthy()
    expect(screen.getByText(/边数\(入\+出\)/)).toBeTruthy()

    // 收起后详情消失
    fireEvent.click(screen.getByRole('button', { name: '收起详情' }))
    expect(screen.queryByText('已执行的副作用')).toBeNull()
  })

  it('proposal 域展开显示批准后将执行的影响', async () => {
    afterRender(
      [
        makeDecision({
          domain: 'skill_normalize',
          entity_type: 'skill',
          entity_id: '.NET Framework',
          status: 'proposal',
          risk_tier: 'R2',
          structured_output: { action: 'merge', target_standard: '.NET', kind: 'alias' },
        }),
      ],
      { proposal: 1, auto_applied: 0, blocked: 0, shadow: 0, records: 1 },
    )
    await waitFor(() =>
      expect(
        screen.getAllByText((_, el) => el?.textContent?.includes('.NET Framework') ?? false).length,
      ).toBeGreaterThan(0),
    )

    fireEvent.click(screen.getByRole('button', { name: '展开详情' }))
    expect(screen.getByText('批准后将执行')).toBeTruthy()
    expect(screen.getByText(/回写别名词典/)).toBeTruthy()
    expect(screen.getByText('待人工审批，尚未产生任何变更（操作列可批准/驳回）')).toBeTruthy()
  })

  it('未知动作组合回退为通用字段展示（不渲染影响小节）', async () => {
    afterRender(
      [makeDecision({ status: 'shadow', structured_output: { custom_field: 'x' } })],
      { proposal: 0, auto_applied: 0, blocked: 0, shadow: 1, records: 1 },
    )
    await waitFor(() => expect(screen.getByText('skill:测试词A')).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: '展开详情' }))
    expect(screen.queryByText(/副作用/)).toBeNull()
    expect(screen.getByText('决策输出（structured_output）')).toBeTruthy()
    expect(screen.getByText('x')).toBeTruthy()
  })
})
