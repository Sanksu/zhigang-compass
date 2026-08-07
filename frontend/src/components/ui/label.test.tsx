import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { Label } from './label'

afterEach(cleanup)

describe('Label', () => {
  it('渲染文本', () => {
    render(<Label>用户名</Label>)
    expect(screen.getByText('用户名')).toBeInTheDocument()
  })

  it('htmlFor 透传（关联表单控件）', () => {
    render(
      <>
        <Label htmlFor="name">姓名</Label>
        <input id="name" />
      </>,
    )
    expect(screen.getByText('姓名')).toHaveAttribute('for', 'name')
  })
})
