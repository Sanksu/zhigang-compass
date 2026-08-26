/**
 * 动态别名表页测试（方案① 补齐前端）。
 *
 * 覆盖：别名行渲染（variant→standard/状态徽标/置信度）、空态、错误态、
 * 决策页跳转入口存在。直接 mock @/lib/api。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { apiGet } from '@/lib/api'
import { AdminSkillAliasesPage } from './admin-skill-aliases-page'

vi.mock('@/lib/api', () => ({
  apiGet: vi.fn(),
  errMsg: (_e: unknown, fallback: string) => fallback,
}))

const mockApiGet = vi.mocked(apiGet)

function makeAlias(overrides: Record<string, unknown> = {}) {
  return {
    id: '11111111-1111-1111-1111-111111111111',
    variant: '.NET Framework',
    standard_name: '.NET',
    status: 'approved',
    proposal_id: '22222222-2222-2222-2222-222222222222',
    source: 'llm_review',
    reviewed_by: 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
    review_reason: '家族变体',
    confidence: 0.95,
    applied_to_graph: false,
    created_at: '2026-08-26T12:00:00+00:00',
    ...overrides,
  }
}

function afterRender(items: unknown[], total = items.length) {
  mockApiGet.mockImplementation(async () => ({ items, total, limit: 20, offset: 0 }))
  return render(
    <MemoryRouter>
      <AdminSkillAliasesPage />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('AdminSkillAliasesPage', () => {
  it('渲染别名行：变体 → 归并目标 + 状态徽标 + 置信度', async () => {
    afterRender([makeAlias()])
    expect(screen.getByText('动态别名表')).toBeTruthy()
    await waitFor(() => expect(screen.getByText('.NET Framework')).toBeTruthy())
    expect(screen.getByText('.NET')).toBeTruthy()
    expect(screen.getAllByText('已生效').length).toBeGreaterThan(1)  // 下拉选项 + 行徽标
    expect(screen.getByText('0.95')).toBeTruthy()
  })

  it('空态提示去决策页审批', async () => {
    afterRender([])
    await waitFor(() => expect(screen.getByText(/暂无别名记录/)).toBeTruthy())
  })

  it('接口失败渲染错误态', async () => {
    mockApiGet.mockRejectedValue(new Error('boom'))
    render(
      <MemoryRouter>
        <AdminSkillAliasesPage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText(/动态别名表加载失败/)).toBeTruthy())
  })

  it('提供决策页跳转入口', async () => {
    afterRender([])
    await waitFor(() => expect(screen.getByText('去决策页审批')).toBeTruthy())
  })
})
