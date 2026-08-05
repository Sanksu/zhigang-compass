import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { Textarea } from './textarea'

afterEach(cleanup)

describe('Textarea', () => {
  it('渲染并透传值', () => {
    render(<Textarea value="abc" onChange={() => {}} placeholder="备注" />)
    expect(screen.getByPlaceholderText('备注')).toHaveValue('abc')
  })

  it('onChange 触发', () => {
    const onChange = vi.fn()
    render(<Textarea onChange={onChange} />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'x' } })
    expect(onChange).toHaveBeenCalledTimes(1)
  })
})
