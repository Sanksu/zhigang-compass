/**
 * 打字机效果 hook — 文本按 charsPerSecond 逐步显示（AI 生成感知呈现）。
 *
 * - prefers-reduced-motion 用户直接返回完整文本（无动画）
 * - text 变化（新报告到达）自动重置重新播放
 */
import { useEffect, useState } from 'react'
import { prefersReducedMotion } from '@/lib/utils'

/** 每 50ms 一跳，每跳步进 = charsPerSecond / 20 个字符 */
const TICK_MS = 50

export function useTypewriter(text: string, charsPerSecond = 35): string {
  // 动效偏好挂载时判定一次（useState 惰性初始化；渲染期不可读 ref）
  const [reduced] = useState(() => prefersReducedMotion())
  const [count, setCount] = useState(0)
  const [prevText, setPrevText] = useState(text)

  // 文本变化在渲染期对齐进度（React 官方"prop 变化调整状态"模式）：
  // 重置重播 / 补满全文，避免 effect 内同步 setState 触发级联渲染
  if (prevText !== text) {
    setPrevText(text)
    setCount(reduced ? text.length : 0)
  }

  useEffect(() => {
    if (reduced || !text) return
    const step = Math.max(1, Math.round(charsPerSecond / (1000 / TICK_MS)))
    const id = window.setInterval(() => {
      setCount((c) => {
        const next = c + step
        if (next >= text.length) window.clearInterval(id)
        return Math.min(next, text.length)
      })
    }, TICK_MS)
    return () => window.clearInterval(id)
  }, [text, charsPerSecond, reduced])

  return reduced ? text : text.slice(0, count)
}
