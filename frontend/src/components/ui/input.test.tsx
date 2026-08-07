import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { Input } from './input'

afterEach(cleanup)

describe('Input', () => {
  it('渲染默认 input 并透传 value', () => {
    render(<Input value="abc" onChange={() => {}} placeholder="搜索" />)
    const el = screen.getByPlaceholderText('搜索') as HTMLInputElement
    expect(el.value).toBe('abc')
  })

  it('onChange 触发', () => {
    const onChange = vi.fn()
    render(<Input onChange={onChange} />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'x' } })
    expect(onChange).toHaveBeenCalledTimes(1)
  })

  it('disabled 生效', () => {
    render(<Input disabled aria-label="输入" />)
    expect(screen.getByLabelText('输入')).toBeDisabled()
  })

  it('type 透传', () => {
    render(<Input type="password" aria-label="密码" />)
    expect(screen.getByLabelText('密码')).toHaveAttribute('type', 'password')
  })
})
