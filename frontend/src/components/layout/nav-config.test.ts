import { describe, expect, it } from 'vitest'
import { adminNavGroups, mainNav } from './nav-config'

describe('nav-config 与设计文档 §10.2 路由表对齐', () => {
  it('主导航 5 项，guest 无需登录', () => {
    expect(mainNav).toHaveLength(5)
    expect(mainNav.map((i) => i.to)).toEqual([
      '/', '/graph', '/resume-match', '/evolution', '/profile',
    ])
    // 简历匹配/个人中心需登录，其余公开
    expect(mainNav.find((i) => i.to === '/')?.requireRole).toBeUndefined()
    expect(mainNav.find((i) => i.to === '/resume-match')?.requireRole).toEqual(['user', 'admin'])
  })

  it('管理导航两组：管理 + 配置中心，共 8 项全部仅 admin（08-16 层级化）', () => {
    const adminNav = adminNavGroups.flatMap((g) => g.items)
    expect(adminNavGroups.map((g) => g.label)).toEqual(['管理', '配置中心'])
    expect(adminNav).toHaveLength(8)
    expect(adminNav.every((i) => i.requireRole?.includes('admin'))).toBe(true)
    expect(adminNav.map((i) => i.to)).toEqual([
      '/admin', '/admin/users', '/admin/crawl', '/admin/review',
      '/admin/llm',
      '/admin/settings/tasks', '/admin/settings/crawl', '/admin/settings/evolution',
    ])
  })

  it('每项都有标签与图标', () => {
    for (const item of [...mainNav, ...adminNavGroups.flatMap((g) => g.items)]) {
      expect(item.label).toBeTruthy()
      expect(item.icon).toBeDefined()
    }
  })
})
