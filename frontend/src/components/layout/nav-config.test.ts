import { describe, expect, it } from 'vitest'
import { adminNavGroups, mainNav } from './nav-config'

describe('nav-config 与设计文档 §10.2 路由表对齐', () => {
  it('主导航 6 项，guest 无需登录（08-27 加新岗位发现）', () => {
    expect(mainNav).toHaveLength(6)
    expect(mainNav.map((i) => i.to)).toEqual([
      '/', '/graph', '/resume-match', '/evolution', '/discovery', '/profile',
    ])
    // 简历匹配/个人中心需登录，其余公开；新岗位发现登录可见
    expect(mainNav.find((i) => i.to === '/')?.requireRole).toBeUndefined()
    expect(mainNav.find((i) => i.to === '/resume-match')?.requireRole).toEqual(['user', 'admin'])
    expect(mainNav.find((i) => i.to === '/discovery')?.requireRole).toBeUndefined()
  })

  it('管理导航三组：管理 + LLM 驱动 + 配置中心，共 11 项全部仅 admin（08-16 层级化，08-21 加 ETL 队列与数据血缘，08-26 加 LLM 驱动组，08-27 系统节流取代采集与限频）', () => {
    const adminNav = adminNavGroups.flatMap((g) => g.items)
    expect(adminNavGroups.map((g) => g.label)).toEqual(['管理', 'LLM 驱动', '配置中心'])
    expect(adminNav).toHaveLength(11)
    expect(adminNav.every((i) => i.requireRole?.includes('admin'))).toBe(true)
    expect(adminNav.map((i) => i.to)).toEqual([
      '/admin', '/admin/users', '/admin/crawl', '/admin/review', '/admin/lineage',
      '/admin/llm-decisions', '/admin/skill-aliases',
      '/admin/llm',
      '/admin/settings/tasks', '/admin/settings/system', '/admin/settings/etl',
    ])
  })

  it('每项都有标签与图标', () => {
    for (const item of [...mainNav, ...adminNavGroups.flatMap((g) => g.items)]) {
      expect(item.label).toBeTruthy()
      expect(item.icon).toBeDefined()
    }
  })
})
