import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { Badge } from './badge'

afterEach(cleanup)

describe('Badge', () => {
  it('默认 variant 渲染内容', () => {
    render(<Badge>稳定</Badge>)
    expect(screen.getByText('稳定')).toBeInTheDocument()
  })

  it('状态 variant 应用对应类名', () => {
    render(<Badge variant="candidate">候选</Badge>)
    expect(screen.getByText('候选').className).toContain('bg-state-candidate/10')
  })

  it('自定义 className 合并', () => {
    render(<Badge className="custom-x">新</Badge>)
    expect(screen.getByText('新').className).toContain('custom-x')
  })
})
