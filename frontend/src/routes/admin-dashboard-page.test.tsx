/**
 * 管理仪表盘快捷操作面板结构测试（08-15 补全后锁定；08-16 6 项；08-22 ETL
 * 触发三连后 9 项）。
 *
 * 背景：快捷操作区曾只有 1 个"触发全量爬取"，管理入口（审核/爬取管理/
 * LLM/用户/系统配置）需跳转侧边栏——补全为触发 + 导航后，本测试防止面板
 * 再次退化或导航目标指向不存在的路由；08-22 新增 ETL 触发型按钮
 * （数据清洗/聚合入图/完整管线），锁定与后端白名单 job 的对应关系。
 */
import { describe, expect, it } from 'vitest'
import { ETL_ACTION_JOBS, QUICK_ACTIONS } from './admin-dashboard-page'

describe('QUICK_ACTIONS 快捷操作面板', () => {
  it('补全为 11 项：4 触发 + 7 导航', () => {
    expect(QUICK_ACTIONS).toHaveLength(11)
    const triggers = QUICK_ACTIONS.filter((a) => !a.to)
    const navs = QUICK_ACTIONS.filter((a) => a.to)
    expect(triggers).toHaveLength(4)
    expect(navs).toHaveLength(7)
    expect(triggers.map((t) => t.id)).toEqual(
      expect.arrayContaining(['crawl', 'etl-clean', 'etl-graph', 'etl-full']),
    )
  })

  it('id 唯一（渲染 key 依赖）', () => {
    const ids = QUICK_ACTIONS.map((a) => a.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('导航目标均为已注册的管理路由', () => {
    const navs = QUICK_ACTIONS.filter((a) => a.to).map((a) => a.to)
    expect(navs).toEqual(
      expect.arrayContaining(['/admin/review', '/admin/review?tab=dict', '/admin/crawl', '/admin/llm', '/admin/users', '/admin/settings/tasks']),
    )
  })

  it('每项均有图标/标签/描述（卡片渲染字段齐全）', () => {
    for (const a of QUICK_ACTIONS) {
      expect(a.label.length).toBeGreaterThan(0)
      expect(a.desc.length).toBeGreaterThan(0)
      expect(a.icon).toBeTruthy() // lucide 图标为 forwardRef 组件
    }
  })

  it('ETL 触发操作与后端白名单 job 一一对应（契约 /admin/etl/trigger）', () => {
    expect(Object.keys(ETL_ACTION_JOBS).sort()).toEqual(['etl-clean', 'etl-full', 'etl-graph'])
    expect(Object.values(ETL_ACTION_JOBS).sort()).toEqual([
      'aggregate_positions',
      'dedup_simhash',
      'run_etl_pipeline',
    ])
  })
})
