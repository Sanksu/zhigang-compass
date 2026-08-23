/**
 * 能力图谱域聚合下钻（panorama 视图，08-22）——纯函数管线。
 *
 * 数据面：Position.domain_id/domain_name（后端岗位投影 Leiden 回填，PR #410）。
 * 展示层三级下钻：域超节点（全部岗位聚合可见）→ 双击展开域内岗位 →
 * 双击岗位展开技能（复用既有 expandedPositions 交互模型）。
 *
 * 全部岗位以超节点形式常驻画布（替代 08-15 的 MAX_POSITIONS=30 度数裁剪），
 * 每层渲染规模：~14 超节点 + 展开域的岗位数 + 展开岗位 × Top-12 技能。
 */
import type { DisplayGraphEdge, GraphData, GraphNode } from './types'

/** 未回填 domain_id 的岗位兜底桶（零边/新增未同步岗位）。
 *  08-22 视觉治理：改名「待归类岗位」弱化"未分类=垃圾簇"观感，画布侧配虚线描边降透明度 */
export const UNCATEGORIZED_DOMAIN_ID = 'dom_uncategorized'
export const UNCATEGORIZED_DOMAIN_NAME = '待归类岗位'

/** 域间边保留阈值：共享技能 < 3 的域对不连线（去毛线球噪声） */
const MIN_DOMAIN_EDGE_WEIGHT = 3
/** 每个域最多保留的最强域间关系，避免 14 域形成中央全连接灰网 */
const MAX_DOMAIN_EDGES_PER_NODE = 3

/** 域超节点 ↔ 成员岗位的隶属边权重（实线粗边，视觉锚定域内聚团） */
const DOMAIN_MEMBER_EDGE_WEIGHT = 3

export interface DomainAggregate {
  /** 域超节点列表（按成员数降序，全部岗位都在某个域里） */
  supernodes: GraphNode[]
  /** 域-域共享技能边（weight=共享技能数，≥MIN_DOMAIN_EDGE_WEIGHT） */
  domainEdges: DisplayGraphEdge[]
  /** domain_id → 成员岗位节点 */
  positionsByDomain: Map<string, GraphNode[]>
  /** position_id → domain_id（书签/定位反查） */
  domainOfPosition: Map<string, string>
}

/** 岗位节点按 domain_id 分组并生成超节点 + 域间边（纯函数）。 */
export function aggregateByDomain(data: GraphData): DomainAggregate {
  const positions = data.nodes.filter((n) => n.type === 'position')
  const byDomain = new Map<string, GraphNode[]>()
  const domainOf = new Map<string, string>()
  for (const p of positions) {
    const dom = p.domain_id || UNCATEGORIZED_DOMAIN_ID
    byDomain.set(dom, [...(byDomain.get(dom) ?? []), p])
    domainOf.set(p.id, dom)
  }

  const supernodes: GraphNode[] = []
  for (const [dom, members] of byDomain) {
    // 代表岗（后端 domain_name）；未分类桶用固定名
    const repName =
      dom === UNCATEGORIZED_DOMAIN_ID ? UNCATEGORIZED_DOMAIN_NAME : members[0]?.domain_name || dom
    supernodes.push({
      id: dom,
      name: repName,
      type: 'position',
      isDomain: true,
      // 待归类桶弱化标记：画布侧虚线描边+降透明度，不与实域抢视觉权重
      isUncategorized: dom === UNCATEGORIZED_DOMAIN_ID,
      memberCount: members.length,
      // value 驱动斥力/尺寸：域规模（成员数），供布局与 label 权重使用
      value: members.length,
    })
  }
  supernodes.sort((a, b) => (b.memberCount ?? 0) - (a.memberCount ?? 0))

  // 域-域边：同一技能被多个域的岗位共同要求 → 域对共享技能计数
  const pairWeight = new Map<string, number>()
  const skillDomains = new Map<string, Set<string>>()
  for (const e of data.edges) {
    const doms = skillDomains.get(e.target) ?? new Set<string>()
    // panorama 边方向：position→skill
    const dom = domainOf.get(e.source)
    if (dom) doms.add(dom)
    skillDomains.set(e.target, doms)
  }
  for (const doms of skillDomains.values()) {
    const arr = [...doms]
    for (let i = 0; i < arr.length; i++) {
      for (let j = i + 1; j < arr.length; j++) {
        const key = arr[i] < arr[j] ? `${arr[i]}|${arr[j]}` : `${arr[j]}|${arr[i]}`
        pairWeight.set(key, (pairWeight.get(key) ?? 0) + 1)
      }
    }
  }
  // 先按共享技能数排序，再以两端域的度数做双向 Top-K 截断。
  // 这样保留最有解释力的跨域关系，同时避免弱关系在中心叠成灰色毛线球。
  const domainDegree = new Map<string, number>()
  const domainEdges: DisplayGraphEdge[] = [...pairWeight.entries()]
    .filter(([, w]) => w >= MIN_DOMAIN_EDGE_WEIGHT)
    .sort(([, wa], [, wb]) => wb - wa)
    .flatMap(([key, w]) => {
      const [a, b] = key.split('|')
      const degreeA = domainDegree.get(a) ?? 0
      const degreeB = domainDegree.get(b) ?? 0
      if (degreeA >= MAX_DOMAIN_EDGES_PER_NODE || degreeB >= MAX_DOMAIN_EDGES_PER_NODE) return []
      domainDegree.set(a, degreeA + 1)
      domainDegree.set(b, degreeB + 1)
      return [{ source: a, target: b, weight: w, necessity: 'nice' as const, isDomainEdge: true }]
    })

  return { supernodes, domainEdges, positionsByDomain: byDomain, domainOfPosition: domainOf }
}

export interface DomainViewOptions {
  expandedDomains: Set<string>
  expandedPositions: Set<string>
  /** 单岗位展开技能上限（沿用 MAX_SKILLS_PER_POSITION） */
  maxSkillsPerPosition: number
}

/** panorama 聚合视图数据（纯函数）：超节点 + 展开域的岗位 + 展开岗位的技能。

 * 层级：域超节点常驻 → 展开域显示成员岗位（超节点↔岗位隶属边锚定聚团）
 * → 展开岗位按边权重 Top-N 技能（与既有展开口径一致）。
 */
export function buildDomainView(
  data: GraphData,
  agg: DomainAggregate,
  opts: DomainViewOptions,
): GraphData {
  const nodes: GraphNode[] = [...agg.supernodes]
  const edges: DisplayGraphEdge[] = agg.domainEdges.filter(
    (e) => agg.positionsByDomain.has(e.source) && agg.positionsByDomain.has(e.target),
  )

  const visiblePositions = new Set<string>()
  for (const dom of opts.expandedDomains) {
    const members = agg.positionsByDomain.get(dom)
    if (!members) continue
    const supernode = agg.supernodes.find((s) => s.id === dom)
    const repName = supernode?.name ?? dom
    const isUncategorized = dom === UNCATEGORIZED_DOMAIN_ID
    for (const p of members) {
      // 域成员与域名同名时（代表岗名 = domain_name）追加后缀，消除同屏歧义
      const displayName =
        !isUncategorized && p.name === repName ? `${p.name}（岗）` : p.name
      nodes.push({ ...p, name: displayName })
      visiblePositions.add(p.id)
      edges.push({
        source: dom,
        target: p.id,
        weight: DOMAIN_MEMBER_EDGE_WEIGHT,
        necessity: 'must',
      })
    }
  }

  // 展开岗位的技能（多岗位共享技能去重，与旧 visibleData 口径一致）
  const skillIds = new Set<string>()
  for (const pid of opts.expandedPositions) {
    if (!visiblePositions.has(pid)) continue
    const ranked = data.edges
      .filter((e) => e.source === pid || e.target === pid)
      .sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0))
    let kept = 0
    for (const e of ranked) {
      if (kept >= opts.maxSkillsPerPosition) break
      const sid = e.source === pid ? e.target : e.source
      if (skillIds.has(sid)) continue
      skillIds.add(sid)
      kept++
    }
  }
  for (const n of data.nodes) {
    if (n.type === 'skill' && skillIds.has(n.id)) nodes.push(n)
  }
  edges.push(
    ...data.edges.filter(
      (e) =>
        (visiblePositions.has(e.source) && skillIds.has(e.target)) ||
        (skillIds.has(e.source) && visiblePositions.has(e.target)),
    ),
  )

  return { ...data, nodes, edges }
}
