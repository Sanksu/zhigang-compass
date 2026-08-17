/**
 * 岗位人工编辑 Tab 组件测试
 *
 * 覆盖：岗位名 URL 编码 GET、表单回填、清洗后保存 payload、weight 范围校验、
 * 保存成功通知与 diff 摘要展示。直接 mock @/lib/api（未引入 MSW）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { apiGet, apiPut } from '@/lib/api'
import { PositionEditorTab } from './position-editor-tab'

vi.mock('@/lib/api', () => ({
  apiGet: vi.fn(),
  apiPut: vi.fn(),
  errMsg: (_e: unknown, fallback: string) => fallback,
}))

const mockApiGet = vi.mocked(apiGet)
const mockApiPut = vi.mocked(apiPut)

function makeDetail(overrides: Record<string, unknown> = {}) {
  return {
    id: 'pos_x',
    name: '提示词工程师',
    level: 'P6',
    industry: 'AI',
    salary_range: '30-50K',
    status: 'emerging',
    core_duties: ['提示词设计', '评测'],
    scenarios: ['智能客服', '内容生成'],
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    skills: [
      { name: 'Python', necessity: 'must', weight: 0.9, level: 'L3' },
      { name: 'Django', necessity: 'nice', weight: 0.7, level: 'L2' },
    ],
    education: [{ name: '本科', necessity: 'required' }],
    certifications: [{ name: '软考高项' }],
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  mockApiGet.mockReset()
  mockApiPut.mockReset()
})

describe('PositionEditorTab 岗位人工编辑', () => {
  it('空岗位名点击加载仅提示，不发 GET', async () => {
    render(<PositionEditorTab />)
    fireEvent.click(screen.getByRole('button', { name: /加载详情/ }))
    expect(await screen.findByText('请输入岗位名')).toBeInTheDocument()
    expect(mockApiGet).not.toHaveBeenCalled()
  })

  it('岗位名在 URL 中做 encodeURIComponent 后再 GET', async () => {
    mockApiGet.mockResolvedValue(makeDetail())
    render(<PositionEditorTab />)
    fireEvent.change(screen.getByPlaceholderText('输入岗位名后加载详情（如：提示词工程师）'), {
      target: { value: '提示词工程师' },
    })
    fireEvent.click(screen.getByRole('button', { name: /加载详情/ }))
    await waitFor(() => expect(mockApiGet).toHaveBeenCalledTimes(1))
    const url = String(mockApiGet.mock.calls[0][0])
    expect(url).toContain('/admin/positions/')
    expect(url).toContain(encodeURIComponent('提示词工程师'))
    expect(url).not.toContain('/admin/positions/提示词工程师')
  })

  it('详情回填技能表单与文本域', async () => {
    mockApiGet.mockResolvedValue(makeDetail())
    render(<PositionEditorTab />)
    fireEvent.change(screen.getByPlaceholderText('输入岗位名后加载详情（如：提示词工程师）'), {
      target: { value: 'x' },
    })
    fireEvent.click(screen.getByRole('button', { name: /加载详情/ }))
    await screen.findByDisplayValue('Python')
    expect(screen.getByDisplayValue('Django')).toBeInTheDocument()
    // 文本域为多行值（core_duties.join('\n')），getByDisplayValue 用 \s+ 匹配换行折叠
    expect(screen.getByDisplayValue(/提示词设计\s+评测/)).toBeInTheDocument()
    expect(screen.getByDisplayValue(/智能客服\s+内容生成/)).toBeInTheDocument()
    expect(screen.getByText('提示词工程师')).toBeInTheDocument()
  })

  it('保存提交清洗后 payload：trim 名称、过滤空行、weight 转数值', async () => {
    mockApiGet.mockResolvedValue(makeDetail())
    mockApiPut.mockResolvedValue({ position_name: '提示词工程师', updated: true, diff_summary: 'skills +0' })
    render(<PositionEditorTab />)
    fireEvent.change(screen.getByPlaceholderText('输入岗位名后加载详情（如：提示词工程师）'), {
      target: { value: '提示词工程师' },
    })
    fireEvent.click(screen.getByRole('button', { name: /加载详情/ }))
    await screen.findByDisplayValue('Python')
    // 技能名带空格 → 保存 trim；Django 改为纯空格 → 过滤
    fireEvent.change(screen.getByDisplayValue('Python'), { target: { value: 'Python ' } })
    fireEvent.change(screen.getByDisplayValue('Django'), { target: { value: '   ' } })
    // 文本域换行清洗
    fireEvent.change(screen.getByDisplayValue(/提示词设计\s+评测/), {
      target: { value: '职责一\n\n职责二 ' },
    })
    fireEvent.change(screen.getByDisplayValue(/智能客服\s+内容生成/), {
      target: { value: '场景一\n  ' },
    })
    fireEvent.click(screen.getByRole('button', { name: /保存编辑/ }))
    await waitFor(() => expect(mockApiPut).toHaveBeenCalledTimes(1))
    const [url, body] = mockApiPut.mock.calls[0] as [string, { skills: unknown[]; core_duties: unknown[]; scenarios: unknown[] }]
    expect(url).toContain(encodeURIComponent('提示词工程师'))
    expect(body.skills).toEqual([{ name: 'Python', necessity: 'must', weight: 0.9 }])
    expect(body.core_duties).toEqual(['职责一', '职责二'])
    expect(body.scenarios).toEqual(['场景一'])
  })

  it('weight 超出 0-1 范围提示且不发 PUT', async () => {
    mockApiGet.mockResolvedValue(makeDetail())
    render(<PositionEditorTab />)
    fireEvent.change(screen.getByPlaceholderText('输入岗位名后加载详情（如：提示词工程师）'), {
      target: { value: '提示词工程师' },
    })
    fireEvent.click(screen.getByRole('button', { name: /加载详情/ }))
    await screen.findByDisplayValue('Python')
    fireEvent.change(screen.getByDisplayValue('0.9'), { target: { value: '1.5' } })
    fireEvent.click(screen.getByRole('button', { name: /保存编辑/ }))
    expect(await screen.findByText('技能 weight 必须在 0.0-1.0 之间')).toBeInTheDocument()
    expect(mockApiPut).not.toHaveBeenCalled()
  })

  it('保存成功显示通知与 diff 摘要', async () => {
    mockApiGet.mockResolvedValue(makeDetail())
    mockApiPut.mockResolvedValue({ position_name: '提示词工程师', updated: true, diff_summary: 'skills +0' })
    render(<PositionEditorTab />)
    fireEvent.change(screen.getByPlaceholderText('输入岗位名后加载详情（如：提示词工程师）'), {
      target: { value: '提示词工程师' },
    })
    fireEvent.click(screen.getByRole('button', { name: /加载详情/ }))
    await screen.findByDisplayValue('Python')
    fireEvent.click(screen.getByRole('button', { name: /保存编辑/ }))
    expect(await screen.findByText('已保存编辑（变更已写入 PositionEditLog）')).toBeInTheDocument()
    expect(screen.getByText('skills +0')).toBeInTheDocument()
  })
})
