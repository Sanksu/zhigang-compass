import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './dialog'

afterEach(cleanup)

describe('Dialog', () => {
  it('open 状态渲染标题/描述/内容（经 Portal）', () => {
    render(
      <Dialog open>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认操作</DialogTitle>
            <DialogDescription>此操作不可撤销</DialogDescription>
          </DialogHeader>
          <div>对话框正文</div>
          <DialogFooter>
            <button>确定</button>
          </DialogFooter>
        </DialogContent>
      </Dialog>,
    )
    expect(screen.getByText('确认操作')).toBeInTheDocument()
    expect(screen.getByText('此操作不可撤销')).toBeInTheDocument()
    expect(screen.getByText('对话框正文')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '关闭' })).toBeInTheDocument()
  })

  it('close 状态不渲染内容', () => {
    render(
      <Dialog>
        <DialogContent>
          <DialogTitle>不可见</DialogTitle>
        </DialogContent>
      </Dialog>,
    )
    expect(screen.queryByText('不可见')).not.toBeInTheDocument()
  })
})
