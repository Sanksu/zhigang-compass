import { describe, expect, it } from 'vitest'
import { APP_NAME, ROLES, TOKEN_REFRESH_MARGIN } from './constants'

describe('constants', () => {
  it('三角色中文映射', () => {
    expect(ROLES).toEqual({ guest: '访客', user: '用户', admin: '管理员' })
  })

  it('Token 提前 5 分钟刷新', () => {
    expect(TOKEN_REFRESH_MARGIN).toBe(5 * 60)
  })

  it('应用名', () => {
    expect(APP_NAME).toBe('智岗罗盘')
  })
})
