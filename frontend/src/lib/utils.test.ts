import { describe, expect, it } from 'vitest'
import { cn } from './utils'

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
