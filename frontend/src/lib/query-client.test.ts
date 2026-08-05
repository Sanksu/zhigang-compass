import { describe, expect, it } from 'vitest'
import { queryClient } from './query-client'

describe('queryClient 默认配置', () => {
  it('30s staleTime、失败重试 1 次、窗口聚焦不重取', () => {
    const queries = queryClient.getDefaultOptions().queries
    expect(queries?.staleTime).toBe(30_000)
    expect(queries?.retry).toBe(1)
    expect(queries?.refetchOnWindowFocus).toBe(false)
  })
})
