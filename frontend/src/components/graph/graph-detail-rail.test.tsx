import { useState } from 'react'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { GraphDetailRail } from './graph-detail-rail'

function mockDesktop(initial: boolean) {
  let matches = initial
  const listeners = new Set<() => void>()
  const mediaQuery = {
    get matches() { return matches },
    media: '(min-width: 1024px)',
    addEventListener: vi.fn((_type: string, listener: () => void) => listeners.add(listener)),
    removeEventListener: vi.fn((_type: string, listener: () => void) => listeners.delete(listener)),
  } as unknown as MediaQueryList
  vi.spyOn(window, 'matchMedia').mockReturnValue(mediaQuery)
  return (next: boolean) => {
    matches = next
    listeners.forEach((listener) => listener())
  }
}

function Harness() {
  const [ready, setReady] = useState(false)
  return <><button onClick={() => setReady(true)}>打开详情</button><GraphDetailRail rightTab="detail" onRightTabChange={vi.fn()} ready={ready} onClose={() => setReady(false)}><div>详情内容</div></GraphDetailRail></>
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  document.body.style.overflow = ''
})

describe('GraphDetailRail', () => {
  it('断点切换时始终只挂载一份详情内容', () => {
    const setDesktop = mockDesktop(false)
    render(<GraphDetailRail rightTab="detail" onRightTabChange={vi.fn()} ready onClose={vi.fn()}><div>唯一详情</div></GraphDetailRail>)
    expect(screen.getAllByText('唯一详情')).toHaveLength(1)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    act(() => setDesktop(true))
    expect(screen.getAllByText('唯一详情')).toHaveLength(1)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('移动详情打开时聚焦关闭按钮，关闭后恢复焦点', () => {
    mockDesktop(false)
    render(<Harness />)
    const opener = screen.getByRole('button', { name: '打开详情' })
    opener.focus()
    fireEvent.click(opener)
    expect(screen.getByRole('dialog', { name: '详情' })).toHaveAttribute('aria-modal', 'true')
    expect(screen.getByRole('button', { name: '关闭详情抽屉' })).toHaveFocus()
    expect(document.body.style.overflow).toBe('hidden')
    fireEvent.click(screen.getByRole('button', { name: '关闭详情抽屉' }))
    expect(opener).toHaveFocus()
    expect(document.body.style.overflow).toBe('')
  })

  it('Escape 关闭详情并阻止同一次事件退出大屏', () => {
    mockDesktop(false)
    const onClose = vi.fn()
    const outerEscape = vi.fn()
    window.addEventListener('keydown', outerEscape)
    render(<GraphDetailRail rightTab="detail" onRightTabChange={vi.fn()} ready onClose={onClose}><div>详情</div></GraphDetailRail>)
    fireEvent.keyDown(document.activeElement ?? document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()
    expect(outerEscape).not.toHaveBeenCalled()
    window.removeEventListener('keydown', outerEscape)
  })
})
