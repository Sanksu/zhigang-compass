/**
 * 域聚合下钻纯函数单测：aggregateByDomain（分组/超节点/域间边/未分类兜底）
 * + buildDomainView（三级展开/隶属边/技能上限/域间边过滤）。
 */
import { describe, expect, it } from 'vitest'

import {
  UNCATEGORIZED_DOMAIN_ID,
  aggregateByDomain,
  buildDomainView,
} from './graph-domain'
import type { GraphData, GraphNode } from './types'

function pos(id: string, name: string, domainId?: string, domainName?: string): GraphNode {
  return {
    id,
    name,
    type: 'position',
    status: 'active',
    ...(domainId ? { domain_id: domainId, domain_name: domainName } : {}),
  }
}

function skill(id: string): GraphNode {
  return { id, name: id, type: 'skill' }
}

function baseData(): GraphData {
  return {
    nodes: [
      pos('p1', '前端开发工程师', 'dom_fe', '前端开发工程师'),
      pos('p2', 'Vue前端开发工程师', 'dom_fe', '前端开发工程师'),
      pos('p3', '投资分析师', 'dom_fin', '数据分析师'),
      pos('p4', '信贷分析师', 'dom_fin', '数据分析师'),
      pos('p5', '孤岛岗'),
      skill('React'),
      skill('SQL'),
      skill('Python'),
    ],
    edges: [
      { source: 'p1', target: 'React', weight: 0.8, necessity: 'must' },
      { source: 'p2', target: 'React', weight: 0.4, necessity: 'nice' },
      { source: 'p3', target: 'SQL', weight: 0.4, necessity: 'nice' },
      { source: 'p4', target: 'SQL', weight: 0.4, necessity: 'nice' },
      { source: 'p1', target: 'Python', weight: 0.4, necessity: 'nice' },
      { source: 'p3', target: 'Python', weight: 0.4, necessity: 'nice' },
    ],
    stats: {
      totalPositions: 5,
      totalSkills: 3,
      totalEdges: 6,
      returnedNodes: 8,
      totalNodesInGraph: 8,
    },
  }
}

describe('aggregateByDomain', () => {
  it('岗位按 domain_id 分组，超节点带成员数与代表名', () => {
    const agg = aggregateByDomain(baseData())
    const fe = agg.supernodes.find((n) => n.id === 'dom_fe')
    expect(fe).toMatchObject({ isDomain: true, memberCount: 2, name: '前端开发工程师' })
    expect(agg.positionsByDomain.get('dom_fin')?.map((p) => p.id)).toEqual(['p3', 'p4'])
    // 成员数降序：fin/fe 各 2，未分类 1
    expect(agg.supernodes[0].memberCount).toBe(2)
  })

  it('未回填 domain_id 的岗位落未分类桶', () => {
    const agg = aggregateByDomain(baseData())
    expect(agg.domainOfPosition.get('p5')).toBe(UNCATEGORIZED_DOMAIN_ID)
    expect(agg.positionsByDomain.get(UNCATEGORIZED_DOMAIN_ID)?.[0].name).toBe('孤岛岗')
  })

  it('未分类桶改名「待归类岗位」并带弱化标记（P1-2）', () => {
    const agg = aggregateByDomain(baseData())
    const unc = agg.supernodes.find((n) => n.id === UNCATEGORIZED_DOMAIN_ID)
    expect(unc?.name).toBe('待归类岗位')
    expect(unc?.isUncategorized).toBe(true)
    // 实域超节点不带弱化标记
    expect(agg.supernodes.find((n) => n.id === 'dom_fe')?.isUncategorized).toBe(false)
  })

  it('域成员与域名同名时追加（岗）后缀消歧（P1-2）', () => {
    const agg = aggregateByDomain(baseData())
    const view = buildDomainView(baseData(), agg, {
      expandedDomains: new Set(['dom_fe']),
      expandedPositions: new Set(),
      maxSkillsPerPosition: 12,
    })
    // p1「前端开发工程师」与域名同名 → 加后缀；p2 不同名 → 原名
    expect(view.nodes.find((n) => n.id === 'p1')?.name).toBe('前端开发工程师（岗）')
    expect(view.nodes.find((n) => n.id === 'p2')?.name).toBe('Vue前端开发工程师')
  })

  it('域间边按共享技能计数，低于阈值过滤', () => {
    const agg = aggregateByDomain(baseData())
    // fe×fin 仅共享 Python（1 < 3）→ 无域间边
    expect(agg.domainEdges).toHaveLength(0)
    // 补至 3 个共享技能（Python/React/TS）→ 边出现且 weight=3
    const data = baseData()
    data.nodes.push(skill('TS'))
    data.edges.push({ source: 'p2', target: 'Python', weight: 0.4, necessity: 'nice' })
    data.edges.push({ source: 'p4', target: 'React', weight: 0.4, necessity: 'nice' })
    data.edges.push({ source: 'p1', target: 'TS', weight: 0.4, necessity: 'nice' })
    data.edges.push({ source: 'p3', target: 'TS', weight: 0.4, necessity: 'nice' })
    const agg2 = aggregateByDomain(data)
    expect(agg2.domainEdges).toEqual([
      { source: 'dom_fe', target: 'dom_fin', weight: 3, necessity: 'nice' },
    ])
  })
})

describe('buildDomainView', () => {
  it('默认仅超节点 + 域间边；岗位/技能不可见', () => {
    const data = baseData()
    data.edges.push({ source: 'p2', target: 'Python', weight: 0.4, necessity: 'nice' })
    data.edges.push({ source: 'p4', target: 'React', weight: 0.4, necessity: 'nice' })
    const agg = aggregateByDomain(data)
    const view = buildDomainView(data, agg, {
      expandedDomains: new Set(),
      expandedPositions: new Set(),
      maxSkillsPerPosition: 12,
    })
    expect(view.nodes.every((n) => n.isDomain)).toBe(true)
    expect(view.nodes).toHaveLength(agg.supernodes.length)
  })

  it('展开域：成员岗位上画布 + 超节点隶属边；再展开岗位带出技能', () => {
    const data = baseData()
    const agg = aggregateByDomain(data)
    const view = buildDomainView(data, agg, {
      expandedDomains: new Set(['dom_fe']),
      expandedPositions: new Set(['p1']),
      maxSkillsPerPosition: 12,
    })
    const ids = view.nodes.map((n) => n.id)
    expect(ids).toContain('p1')
    expect(ids).toContain('p2')
    expect(ids).toContain('React') // p1 展开的技能
    expect(ids).toContain('Python')
    expect(ids).not.toContain('p3') // 未展开域的岗位不可见
    expect(ids).not.toContain('SQL')
    // 隶属边：超节点→两个成员
    const memberEdges = view.edges.filter((e) => e.source === 'dom_fe')
    expect(memberEdges.map((e) => e.target).sort()).toEqual(['p1', 'p2'])
  })

  it('技能上限：maxSkillsPerPosition 截断展开岗位的技能数', () => {
    const data = baseData()
    // p1 关联 3 个技能
    data.edges.push({ source: 'p1', target: 'SQL', weight: 0.4, necessity: 'nice' })
    const agg = aggregateByDomain(data)
    const view = buildDomainView(data, agg, {
      expandedDomains: new Set(['dom_fe']),
      expandedPositions: new Set(['p1']),
      maxSkillsPerPosition: 2,
    })
    const skills = view.nodes.filter((n) => n.type === 'skill')
    expect(skills).toHaveLength(2)
  })

  it('未展开域的 expandedPositions 不生效（岗位不在画布）', () => {
    const data = baseData()
    const agg = aggregateByDomain(data)
    const view = buildDomainView(data, agg, {
      expandedDomains: new Set(['dom_fe']),
      expandedPositions: new Set(['p3']), // p3 在 dom_fin，域未展开
      maxSkillsPerPosition: 12,
    })
    expect(view.nodes.some((n) => n.id === 'p3')).toBe(false)
    expect(view.nodes.some((n) => n.type === 'skill')).toBe(false)
  })
})
