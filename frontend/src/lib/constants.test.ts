import { describe, expect, it } from 'vitest'
import { ROLES } from './constants'

describe('constants', () => {
  it('三角色中文映射', () => {
    expect(ROLES).toEqual({ guest: '访客', user: '用户', admin: '管理员' })
  })
})
