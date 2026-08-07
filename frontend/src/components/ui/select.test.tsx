import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from './select'

afterEach(cleanup)

describe('Select', () => {
  it('open 状态渲染 trigger 与下拉项', () => {
    const { container } = render(
      <Select open>
        <SelectTrigger aria-label="学历">
          <SelectValue placeholder="选择学历" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectLabel>学历</SelectLabel>
            <SelectItem value="本科">本科</SelectItem>
            <SelectItem value="硕士">硕士</SelectItem>
            <SelectSeparator />
          </SelectGroup>
        </SelectContent>
      </Select>,
    )
    // open 时 Radix 对 Trigger 的 role/aria 计算与 closed 不同，getByRole 不可靠；
    // 直接用 container 内的 DOM 断言 trigger 存在，下拉项经 Portal 断言渲染
    expect(container.querySelector('[role="combobox"]')).toBeInTheDocument()
    expect(screen.getAllByText('本科').length).toBeGreaterThan(0)
    expect(screen.getAllByText('硕士').length).toBeGreaterThan(0)
  })

  it('disabled 状态透传', () => {
    render(
      <Select disabled>
        <SelectTrigger aria-label="学历">
          <SelectValue placeholder="选择学历" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="本科">本科</SelectItem>
        </SelectContent>
      </Select>,
    )
    expect(screen.getByRole('combobox')).toBeDisabled()
  })
})
