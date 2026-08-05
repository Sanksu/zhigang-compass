import { describe, expect, it } from 'vitest'
import { cn, escapeHtml } from './utils'

describe('cn', () => {
  it('合并多个类名', () => {
    expect(cn('a', 'b', 'c')).toBe('a b c')
  })

  it('过滤 falsy 值', () => {
    expect(cn('a', false, null, undefined, 0, 'b')).toBe('a b')
  })

  it('tailwind-merge 去冲突（后者优先）', () => {
    expect(cn('px-2', 'px-4')).toBe('px-4')
    expect(cn('text-red-500', 'text-blue-500')).toBe('text-blue-500')
  })
})

describe('escapeHtml（§10.6 ECharts tooltip XSS 转义）', () => {
  it('转义 HTML 特殊字符', () => {
    expect(escapeHtml(`<script>"x"&'y'</script>`)).toBe(
      '&lt;script&gt;&quot;x&quot;&amp;&#39;y&#39;&lt;/script&gt;',
    )
  })

  it('无特殊字符时原样返回', () => {
    expect(escapeHtml('Java 工程师')).toBe('Java 工程师')
  })
})
