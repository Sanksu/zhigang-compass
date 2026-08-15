/**
 * 管理仪表盘快捷操作面板结构测试（08-15 补全后锁定）。
 *
 * 背景：快捷操作区曾只有 1 个"触发全量爬取"，管理入口（审核/爬取管理/
 * LLM/用户）需跳转侧边栏——补全为 1 触发 + 4 导航后，本测试防止面板
 * 再次退化或导航目标指向不存在的路由。
 */
import { describe, expect, it } from 'vitest'
import { QUICK_ACTIONS } from './admin-dashboard-page'

describe('QUICK_ACTIONS 快捷操作面板', () => {
  it('补全为 5 项：1 触发 + 4 导航', () => {
    expect(QUICK_ACTIONS).toHaveLength(5)
    const triggers = QUICK_ACTIONS.filter((a) => !a.to)
    const navs = QUICK_ACTIONS.filter((a) => a.to)
    expect(triggers).toHaveLength(1)
    expect(navs).toHaveLength(4)
    expect(triggers[0].id).toBe('crawl')
  })

  it('id 唯一（渲染 key 依赖）', () => {
    const ids = QUICK_ACTIONS.map((a) => a.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('导航目标均为已注册的管理路由', () => {
    const navs = QUICK_ACTIONS.filter((a) => a.to).map((a) => a.to)
    expect(navs).toEqual(
      expect.arrayContaining(['/admin/review', '/admin/crawl', '/admin/llm', '/admin/users']),
    )
  })

  it('每项均有图标/标签/描述（卡片渲染字段齐全）', () => {
    for (const a of QUICK_ACTIONS) {
      expect(a.label.length).toBeGreaterThan(0)
      expect(a.desc.length).toBeGreaterThan(0)
      expect(a.icon).toBeTruthy() // lucide 图标为 forwardRef 组件
    }
  })
})
