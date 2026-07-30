/**
 * 图谱 mock 数据 — 用于 M3 前端提前启动阶段
 *
 * 后端 /api/v1/graph/panorama 端点未就绪前，使用本地 mock 数据驱动 UI 开发。
 * 后端就绪后，删除此文件，改用 lib/api.ts 中的 apiGet<GraphData>('/graph/panorama')。
 */
import type { GraphData, GraphViewType } from './types'

/** 全景视图 mock：12 岗位 + 25 技能 + 4 证据 = 41 节点，远小于 600 上限 */
const panoramaMock: GraphData = {
  nodes: [
    // 岗位节点 — 涵盖五状态机
    { id: 'pos_0001', name: '前端开发工程师', type: 'position', status: 'stable', value: 95, description: '使用 React/Vue 构建用户界面的 Web 前端岗位' },
    { id: 'pos_0002', name: '全栈工程师', type: 'position', status: 'stable', value: 88, description: '前后端全链路开发，覆盖 Web 应用各层' },
    { id: 'pos_0003', name: '后端开发工程师', type: 'position', status: 'stable', value: 92, description: '服务端 API 与业务逻辑开发' },
    { id: 'pos_0004', name: 'AI 应用工程师', type: 'position', status: 'emerging', value: 76, description: '集成大模型能力到产品，构建 AI Agent' },
    { id: 'pos_0005', name: '算法工程师', type: 'position', status: 'stable', value: 84, description: '机器学习模型训练与部署' },
    { id: 'pos_0006', name: '数据工程师', type: 'position', status: 'stable', value: 72, description: '数据管道与数仓建设' },
    { id: 'pos_0007', name: 'DevOps 工程师', type: 'position', status: 'stable', value: 68, description: 'CI/CD 与基础设施自动化' },
    { id: 'pos_0008', name: 'Prompt 工程师', type: 'position', status: 'candidate', value: 45, description: '设计、优化 LLM 提示词以驱动模型输出' },
    { id: 'pos_0009', name: '小程序开发工程师', type: 'position', status: 'declining', value: 38, description: '微信/支付宝小程序开发' },
    { id: 'pos_0010', name: 'Flash 开发工程师', type: 'position', status: 'archived', value: 12, description: '已基本退出市场的 Flash 平台开发' },
    { id: 'pos_0011', name: '测试工程师', type: 'position', status: 'stable', value: 65, description: '自动化测试与质量保障' },
    { id: 'pos_0012', name: '技术经理', type: 'position', status: 'stable', value: 58, description: '技术团队管理与架构决策' },

    // 技能节点 — 不同级别
    { id: 'sk_0001', name: 'JavaScript', type: 'skill', level: '中级', value: 90 },
    { id: 'sk_0002', name: 'TypeScript', type: 'skill', level: '中级', value: 85 },
    { id: 'sk_0003', name: 'React', type: 'skill', level: '中级', value: 88 },
    { id: 'sk_0004', name: 'Vue', type: 'skill', level: '中级', value: 70 },
    { id: 'sk_0005', name: 'Node.js', type: 'skill', level: '中级', value: 80 },
    { id: 'sk_0006', name: 'Python', type: 'skill', level: '高级', value: 92 },
    { id: 'sk_0007', name: 'Java', type: 'skill', level: '高级', value: 78 },
    { id: 'sk_0008', name: 'Go', type: 'skill', level: '中级', value: 60 },
    { id: 'sk_0009', name: 'SQL', type: 'skill', level: '中级', value: 82 },
    { id: 'sk_0010', name: 'PostgreSQL', type: 'skill', level: '中级', value: 55 },
    { id: 'sk_0011', name: 'Redis', type: 'skill', level: '中级', value: 50 },
    { id: 'sk_0012', name: 'Docker', type: 'skill', level: '中级', value: 65 },
    { id: 'sk_0013', name: 'Kubernetes', type: 'skill', level: '高级', value: 48 },
    { id: 'sk_0014', name: 'PyTorch', type: 'skill', level: '高级', value: 62 },
    { id: 'sk_0015', name: 'LangChain', type: 'skill', level: '中级', value: 35 },
    { id: 'sk_0016', name: 'Prompt 设计', type: 'skill', level: '中级', value: 40 },
    { id: 'sk_0017', name: 'CSS', type: 'skill', level: '中级', value: 78 },
    { id: 'sk_0018', name: 'HTML', type: 'skill', level: '初级', value: 85 },
    { id: 'sk_0019', name: 'Git', type: 'skill', level: '中级', value: 90 },
    { id: 'sk_0020', name: 'FastAPI', type: 'skill', level: '中级', value: 52 },
    { id: 'sk_0021', name: '机器学习', type: 'skill', level: '高级', value: 70 },
    { id: 'sk_0022', name: '数据分析', type: 'skill', level: '中级', value: 60 },
    { id: 'sk_0023', name: 'CI/CD', type: 'skill', level: '中级', value: 55 },
    { id: 'sk_0024', name: '自动化测试', type: 'skill', level: '中级', value: 48 },
    { id: 'sk_0025', name: '系统设计', type: 'skill', level: '专家', value: 65 },

    // 证据节点 — 4 个示例（关联到高频技能）
    { id: 'ev_0001', name: 'BOSS直聘 JD 抽取', type: 'evidence', source: 'BOSS直聘', value: 30, description: '2026Q2 共 1247 条 JD 提及 React' },
    { id: 'ev_0002', name: 'GitHub 趋势', type: 'evidence', source: 'GitHub', value: 25, description: 'LangChain star 数 90 天增长 38%' },
    { id: 'ev_0003', name: 'arXiv 论文', type: 'evidence', source: 'arXiv', value: 20, description: '近 30 天 Prompt 相关论文 142 篇' },
    { id: 'ev_0004', name: 'Stack Overflow 问答', type: 'evidence', source: 'StackOverflow', value: 28, description: 'TypeScript 标签月均提问 2.3k' },
  ],
  edges: [
    // 前端开发 → 技能
    { source: 'pos_0001', target: 'sk_0001', relation: 'requires', necessity: 'must', weight: 1.0 },
    { source: 'pos_0001', target: 'sk_0002', relation: 'requires', necessity: 'must', weight: 0.9 },
    { source: 'pos_0001', target: 'sk_0003', relation: 'requires', necessity: 'must', weight: 0.95 },
    { source: 'pos_0001', target: 'sk_0004', relation: 'requires', necessity: 'nice', weight: 0.5 },
    { source: 'pos_0001', target: 'sk_0017', relation: 'requires', necessity: 'must', weight: 0.9 },
    { source: 'pos_0001', target: 'sk_0018', relation: 'requires', necessity: 'must', weight: 0.9 },

    // 全栈 → 技能
    { source: 'pos_0002', target: 'sk_0001', relation: 'requires', necessity: 'must', weight: 0.95 },
    { source: 'pos_0002', target: 'sk_0002', relation: 'requires', necessity: 'must', weight: 0.9 },
    { source: 'pos_0002', target: 'sk_0003', relation: 'requires', necessity: 'nice', weight: 0.7 },
    { source: 'pos_0002', target: 'sk_0005', relation: 'requires', necessity: 'must', weight: 0.9 },
    { source: 'pos_0002', target: 'sk_0006', relation: 'requires', necessity: 'nice', weight: 0.6 },

    // 后端 → 技能
    { source: 'pos_0003', target: 'sk_0006', relation: 'requires', necessity: 'must', weight: 0.95 },
    { source: 'pos_0003', target: 'sk_0007', relation: 'requires', necessity: 'nice', weight: 0.7 },
    { source: 'pos_0003', target: 'sk_0009', relation: 'requires', necessity: 'must', weight: 0.9 },
    { source: 'pos_0003', target: 'sk_0020', relation: 'requires', necessity: 'nice', weight: 0.6 },

    // AI 应用工程师 → 技能
    { source: 'pos_0004', target: 'sk_0006', relation: 'requires', necessity: 'must', weight: 0.9 },
    { source: 'pos_0004', target: 'sk_0015', relation: 'requires', necessity: 'must', weight: 0.85 },
    { source: 'pos_0004', target: 'sk_0016', relation: 'requires', necessity: 'must', weight: 0.9 },
    { source: 'pos_0004', target: 'sk_0002', relation: 'requires', necessity: 'nice', weight: 0.5 },

    // 算法工程师 → 技能
    { source: 'pos_0005', target: 'sk_0006', relation: 'requires', necessity: 'must', weight: 0.95 },
    { source: 'pos_0005', target: 'sk_0014', relation: 'requires', necessity: 'must', weight: 0.9 },
    { source: 'pos_0005', target: 'sk_0021', relation: 'requires', necessity: 'must', weight: 0.9 },

    // 数据工程师 → 技能
    { source: 'pos_0006', target: 'sk_0006', relation: 'requires', necessity: 'must', weight: 0.85 },
    { source: 'pos_0006', target: 'sk_0009', relation: 'requires', necessity: 'must', weight: 0.9 },
    { source: 'pos_0006', target: 'sk_0022', relation: 'requires', necessity: 'must', weight: 0.85 },

    // DevOps → 技能
    { source: 'pos_0007', target: 'sk_0012', relation: 'requires', necessity: 'must', weight: 0.9 },
    { source: 'pos_0007', target: 'sk_0013', relation: 'requires', necessity: 'must', weight: 0.85 },
    { source: 'pos_0007', target: 'sk_0023', relation: 'requires', necessity: 'must', weight: 0.9 },

    // Prompt 工程师 → 技能
    { source: 'pos_0008', target: 'sk_0016', relation: 'requires', necessity: 'must', weight: 1.0 },
    { source: 'pos_0008', target: 'sk_0015', relation: 'requires', necessity: 'nice', weight: 0.6 },

    // 小程序开发 → 技能
    { source: 'pos_0009', target: 'sk_0001', relation: 'requires', necessity: 'must', weight: 0.8 },
    { source: 'pos_0009', target: 'sk_0004', relation: 'requires', necessity: 'nice', weight: 0.5 },

    // Flash 开发（archived，几乎无关联）
    { source: 'pos_0010', target: 'sk_0001', relation: 'requires', necessity: 'nice', weight: 0.2 },

    // 测试工程师 → 技能
    { source: 'pos_0011', target: 'sk_0024', relation: 'requires', necessity: 'must', weight: 0.95 },
    { source: 'pos_0011', target: 'sk_0006', relation: 'requires', necessity: 'nice', weight: 0.5 },

    // 技术经理 → 技能
    { source: 'pos_0012', target: 'sk_0025', relation: 'requires', necessity: 'must', weight: 0.9 },
    { source: 'pos_0012', target: 'sk_0019', relation: 'requires', necessity: 'must', weight: 0.85 },

    // 通用技能连接（多岗位共享）
    { source: 'pos_0001', target: 'sk_0019', relation: 'requires', necessity: 'must', weight: 0.85 },
    { source: 'pos_0002', target: 'sk_0019', relation: 'requires', necessity: 'must', weight: 0.8 },
    { source: 'pos_0003', target: 'sk_0019', relation: 'requires', necessity: 'must', weight: 0.8 },
    { source: 'pos_0005', target: 'sk_0019', relation: 'requires', necessity: 'nice', weight: 0.5 },

    // 证据 → 技能（proves 关系）
    { source: 'ev_0001', target: 'sk_0003', relation: 'proves', weight: 0.95 },
    { source: 'ev_0002', target: 'sk_0015', relation: 'proves', weight: 0.85 },
    { source: 'ev_0003', target: 'sk_0016', relation: 'proves', weight: 0.9 },
    { source: 'ev_0004', target: 'sk_0002', relation: 'proves', weight: 0.88 },
  ],
  stats: {
    totalPositions: 12,
    totalSkills: 25,
    totalEdges: 41,
    returnedNodes: 41,
    totalNodesInGraph: 1248,
  },
}

/**
 * 按视图类型返回不同的 mock 子图。
 * 真实后端实现见 §10.3：techStack 由 Louvain 聚类过滤，level 按 level 分组，positionCenter 做 k-hop 展开。
 */
export function getMockGraphData(view: GraphViewType): GraphData {
  switch (view) {
    case 'panorama':
      return panoramaMock

    case 'techStack': {
      // 模拟后端 Louvain 聚类：仅返回前端技术簇
      const frontendSkillIds = new Set(['sk_0001', 'sk_0002', 'sk_0003', 'sk_0004', 'sk_0017', 'sk_0018'])
      const nodes = panoramaMock.nodes.filter(
        (n) =>
          n.type === 'position' ||
          (n.type === 'skill' && frontendSkillIds.has(n.id)) ||
          n.id === 'ev_0001' ||
          n.id === 'ev_0004',
      )
      const nodeIds = new Set(nodes.map((n) => n.id))
      const edges = panoramaMock.edges.filter(
        (e) => nodeIds.has(e.source) && nodeIds.has(e.target),
      )
      return {
        nodes,
        edges,
        stats: {
          totalPositions: nodes.filter((n) => n.type === 'position').length,
          totalSkills: nodes.filter((n) => n.type === 'skill').length,
          totalEdges: edges.length,
          returnedNodes: nodes.length,
          totalNodesInGraph: panoramaMock.stats.totalNodesInGraph,
        },
      }
    }

    case 'level': {
      // 模拟按 level 过滤：仅返回中级技能及关联岗位
      const nodes = panoramaMock.nodes.filter(
        (n) =>
          n.type !== 'skill' ||
          n.level === '中级',
      )
      const nodeIds = new Set(nodes.map((n) => n.id))
      const edges = panoramaMock.edges.filter(
        (e) => nodeIds.has(e.source) && nodeIds.has(e.target),
      )
      return {
        nodes,
        edges,
        stats: {
          totalPositions: nodes.filter((n) => n.type === 'position').length,
          totalSkills: nodes.filter((n) => n.type === 'skill').length,
          totalEdges: edges.length,
          returnedNodes: nodes.length,
          totalNodesInGraph: panoramaMock.stats.totalNodesInGraph,
        },
      }
    }

    case 'positionCenter': {
      // 模拟以 pos_0001（前端开发）为中心 2-hop 展开
      const centerId = 'pos_0001'
      const hop1 = new Set<string>([centerId])
      panoramaMock.edges.forEach((e) => {
        if (e.source === centerId) hop1.add(e.target)
        if (e.target === centerId) hop1.add(e.source)
      })
      const hop2 = new Set(hop1)
      panoramaMock.edges.forEach((e) => {
        if (hop1.has(e.source)) hop2.add(e.target)
        if (hop1.has(e.target)) hop2.add(e.source)
      })
      const nodes = panoramaMock.nodes.filter((n) => hop2.has(n.id))
      const edges = panoramaMock.edges.filter(
        (e) => hop2.has(e.source) && hop2.has(e.target),
      )
      return {
        nodes,
        edges,
        stats: {
          totalPositions: nodes.filter((n) => n.type === 'position').length,
          totalSkills: nodes.filter((n) => n.type === 'skill').length,
          totalEdges: edges.length,
          returnedNodes: nodes.length,
          totalNodesInGraph: panoramaMock.stats.totalNodesInGraph,
        },
      }
    }
  }
}
