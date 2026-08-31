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

function afterRender(
  decisions: unknown[],
  totals: Record<string, number>,
  byDomain?: { domain: string; by_status: Record<string, number>; total: number }[],
) {
  mockApiGet.mockImplementation(async (url: string) => {
    if (url.startsWith('/admin/llm-decisions/summary')) {
      return {
        by_domain: byDomain ?? [{ domain: 'governance', by_status: { auto_applied: 1 }, total: 1 }],
        totals,
      }
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

describe('AdminLlmDecisionsPage 真实口径卡片与治理入口', () => {
  const byDomainFull: { domain: string; by_status: Record<string, number>; total: number }[] = [
    { domain: 'governance', by_status: { proposal: 51, auto_applied: 11 }, total: 62 },
    { domain: 'skill_relation', by_status: { proposal: 69 }, total: 69 },
    { domain: 'position_normalize', by_status: { proposal: 38 }, total: 38 },
    { domain: 'skill_normalize', by_status: { proposal: 10 }, total: 10 },
    { domain: 'skill_classify', by_status: { shadow: 38 }, total: 38 },
  ]

  it('治理提案单独成卡，不计入本页待审提案', async () => {
    afterRender([], { proposal: 168, auto_applied: 11, blocked: 3, shadow: 70, records: 250 }, byDomainFull)
    await waitFor(() => expect(screen.getByText('治理提案（字典治理页处理）')).toBeTruthy())
    expect(screen.getByText('待审提案（本页可批驳）')).toBeTruthy()
    // 本页可批驳 = 69+38+10+38（skill_classify shadow）= 155；治理 51 单列不混入
    expect(screen.getByText('155')).toBeTruthy()
    expect(screen.getByText('51')).toBeTruthy()
  })

  it('governance proposal 行显示字典治理审核入口而非批准/驳回', async () => {
    afterRender(
      [makeDecision({ domain: 'governance', entity_type: 'skill', entity_id: '性能调优', status: 'proposal', risk_tier: 'R2' })],
      { proposal: 1, auto_applied: 0, blocked: 0, shadow: 0, records: 1 },
    )
    await waitFor(() => expect(screen.getByText('skill:性能调优')).toBeTruthy())
    expect(screen.queryByText('批准')).toBeNull()
    expect(screen.getByText('字典治理审核')).toBeTruthy()
  })

  it('可审批域 proposal 行仍显示批准/驳回', async () => {
    afterRender(
      [makeDecision({ domain: 'skill_relation', entity_type: 'skill', entity_id: 'Python', status: 'proposal', risk_tier: 'R2' })],
      { proposal: 1, auto_applied: 0, blocked: 0, shadow: 0, records: 1 },
    )
    await waitFor(() => expect(screen.getByText('批准')).toBeTruthy())
    expect(screen.getByText('驳回')).toBeTruthy()
  })
})

describe('AdminLlmDecisionsPage 岗位归一审批上下文', () => {
  it('position_normalize 行显示归一目标与动作徽标（并入）', async () => {
    afterRender(
      [
        makeDecision({
          domain: 'position_normalize',
          entity_type: 'position',
          entity_id: '数据库管理员',
          status: 'proposal',
          risk_tier: 'R2',
          structured_output: {
            is_new: false,
            keep_original: false,
            canonical_name: 'Java开发工程师',
            reason: '岗位标题为 Java 开发，应归为 Java开发工程师',
          },
        }),
      ],
      { proposal: 1, auto_applied: 0, blocked: 0, shadow: 0, records: 1 },
    )
    await waitFor(() => expect(screen.getByText('position:数据库管理员')).toBeTruthy())
    expect(screen.getByText('Java开发工程师')).toBeTruthy()
    expect(screen.getByText('并入')).toBeTruthy()
  })

  it('keep_original 行显示确认原样徽标且不显示归一目标', async () => {
    afterRender(
      [
        makeDecision({
          domain: 'position_normalize',
          entity_type: 'position',
          entity_id: '7104',
          status: 'proposal',
          risk_tier: 'R2',
          structured_output: { is_new: true, keep_original: true, canonical_name: 'Oracle MSCA', reason: 'x' },
        }),
      ],
      { proposal: 1, auto_applied: 0, blocked: 0, shadow: 0, records: 1 },
    )
    await waitFor(() => expect(screen.getByText('position:7104')).toBeTruthy())
    expect(screen.getByText('确认原样')).toBeTruthy()
    expect(screen.queryByText('Oracle MSCA')).toBeNull()
  })

  it('归一证据按来源+原文链接渲染（不再出现空标签「：-」）', async () => {
    afterRender(
      [
        makeDecision({
          domain: 'position_normalize',
          entity_type: 'position',
          entity_id: '数据库管理员',
          status: 'proposal',
          risk_tier: 'R2',
          evidence_refs: [{ source: 'monster', source_url: 'https://example.com/jd/1' }],
          structured_output: { is_new: false, keep_original: false, canonical_name: 'Java开发工程师', reason: 'x' },
        }),
      ],
      { proposal: 1, auto_applied: 0, blocked: 0, shadow: 0, records: 1 },
    )
    await waitFor(() => expect(screen.getByText('position:数据库管理员')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: '展开详情' }))
    expect(screen.getByText('证据引用')).toBeTruthy()
    expect(screen.getByText(/来源：monster/)).toBeTruthy()
    expect(screen.getByRole('link', { name: '查看原文' }).getAttribute('href')).toBe('https://example.com/jd/1')
  })
})
