import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from './dropdown-menu'

afterEach(cleanup)

describe('DropdownMenu', () => {
  it('open 状态渲染菜单项（经 Portal）', () => {
    render(
      <DropdownMenu open>
        <DropdownMenuTrigger>更多</DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuLabel>账户</DropdownMenuLabel>
          <DropdownMenuItem>
            编辑 <DropdownMenuShortcut>E</DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuCheckboxItem checked>显示明细</DropdownMenuCheckboxItem>
          <DropdownMenuSeparator />
          <DropdownMenuRadioGroup value="a">
            <DropdownMenuRadioItem value="a">方案 A</DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="b">方案 B</DropdownMenuRadioItem>
          </DropdownMenuRadioGroup>
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>更多操作</DropdownMenuSubTrigger>
            <DropdownMenuSubContent>子菜单</DropdownMenuSubContent>
          </DropdownMenuSub>
        </DropdownMenuContent>
      </DropdownMenu>,
    )
    expect(screen.getByText('账户')).toBeInTheDocument()
    expect(screen.getByText('编辑')).toBeInTheDocument()
    expect(screen.getByText('显示明细')).toBeInTheDocument()
    expect(screen.getAllByText('方案 A').length).toBeGreaterThan(0)
    expect(screen.getByText('更多操作')).toBeInTheDocument()
  })
})
