import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from './tooltip'

afterEach(cleanup)

describe('Tooltip', () => {
  it('open 状态渲染 tooltip 内容（经 Portal）', () => {
    render(
      <TooltipProvider>
        <Tooltip open>
          <TooltipTrigger>悬停区域</TooltipTrigger>
          <TooltipContent>提示文本</TooltipContent>
        </Tooltip>
      </TooltipProvider>,
    )
    expect(screen.getByText('悬停区域')).toBeInTheDocument()
    expect(screen.getAllByText('提示文本').length).toBeGreaterThan(0)
  })

  it('关闭状态不渲染内容', () => {
    render(
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger>悬停区域</TooltipTrigger>
          <TooltipContent>提示文本</TooltipContent>
        </Tooltip>
      </TooltipProvider>,
    )
    expect(screen.queryByText('提示文本')).not.toBeInTheDocument()
  })
})
