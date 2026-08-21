/**
 * useTypewriter 单测 — fake timers 驱动逐字显示节奏：
 * 步进/完成停留/text 变化重播/reduced-motion 直出全文。
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useTypewriter } from './use-typewriter'

describe('useTypewriter', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('按步进逐字显示（40 字/秒 → 每 50ms 跳 2 字符）', () => {
    const { result, rerender } = renderHook(({ text }: { text: string }) => useTypewriter(text, 40), {
      initialProps: { text: '' },
    })
    rerender({ text: 'abcdefghij' })
    expect(result.current).toBe('')
    act(() => {
      vi.advanceTimersByTime(50)
    })
    expect(result.current).toBe('ab')
    act(() => {
      vi.advanceTimersByTime(200)
    })
    expect(result.current).toBe('abcdefghij')
  })

  it('完成后停留完整文本（定时器继续走也不越界）', () => {
    const { result } = renderHook(() => useTypewriter('短文本', 35))
    act(() => {
      vi.advanceTimersByTime(5000)
    })
    expect(result.current).toBe('短文本')
  })

  it('text 变化时重置重新播放（新报告到达）', () => {
    const { result, rerender } = renderHook(({ text }: { text: string }) => useTypewriter(text, 40), {
      initialProps: { text: '第一版报告' },
    })
    act(() => {
      vi.advanceTimersByTime(50)
    })
    expect(result.current.length).toBeGreaterThan(0)
    rerender({ text: '第二版更长的报告内容' })
    expect(result.current).toBe('')
  })

  it('prefers-reduced-motion 用户直接返回完整文本（无动画）', () => {
    vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: true } as MediaQueryList)
    const { result } = renderHook(() => useTypewriter('完整文本直接呈现', 35))
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(result.current).toBe('完整文本直接呈现')
  })
})
