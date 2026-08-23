import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { GraphFilterPanel } from './graph-filter-panel'

afterEach(cleanup)

describe('GraphFilterPanel', () => {
  it('展开后提供 active 岗位图层与可访问的关系强度控件', () => {
    const onToggleStatus = vi.fn()
    render(
      <GraphFilterPanel
        minWeight={0}
        onMinWeightChange={vi.fn()}
        hiddenStatuses={new Set()}
        onToggleStatus={onToggleStatus}
        visibleCount={12}
        hiddenCount={0}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /图层探索器/ }))
    fireEvent.click(screen.getByRole('checkbox', { name: '显示活跃岗位' }))

    expect(onToggleStatus).toHaveBeenCalledWith('active')
    expect(screen.getByRole('slider', { name: '关系强度' })).toBeInTheDocument()
  })

  it('显示有效过滤数并支持一键重置', () => {
    const onReset = vi.fn()
    render(
      <GraphFilterPanel
        minWeight={20}
        onMinWeightChange={vi.fn()}
        hiddenStatuses={new Set(['archived'])}
        showOnlyMustEdges
        hideSoftSkills
        onReset={onReset}
        visibleCount={5}
        hiddenCount={7}
      />,
    )

    expect(screen.getByLabelText('4 个过滤条件已启用')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /图层探索器/ }))
    fireEvent.click(screen.getByRole('button', { name: '重置' }))

    expect(onReset).toHaveBeenCalledOnce()
    expect(screen.getByText('淡出 7 个节点')).toBeInTheDocument()
  })
})
