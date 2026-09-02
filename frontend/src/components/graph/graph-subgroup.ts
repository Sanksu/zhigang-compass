/**
 * 岗位域内二级子分组（技术栈视角，纯函数）——纯前端展示层，不改后端域划分。
 *
 * 背景：算法域/前端域等骨干域成员同质（如算法域含大模型/CV/机器人/芯片），
 * 但岗位节点不带 skill_category（后端仅 skill 节点有），无法直接分组。
 * 本模块从「岗位→关联技能」图谱边（position→skill，weight 约定核心度）出发，
 * 用领域专属关键词表把域内岗位归入子组：大模型/LLM、语音/NLP、推荐/搜索、
 * 计算机视觉、机器人/自动驾驶、芯片/嵌入/验证、Web前端、移动/跨端、通用/其他。
 *
 * 设计原则（可答辩、不硬塞）：
 * - 领域专属词判主组（"视觉/OpenCV"→计算机视觉，而非"PyTorch/深度学习"这类
 *   跨子组基础词）——避免"推荐搜索含 PyTorch 误归视觉"的误报；
 * - 未命中专属词的岗位归「通用/其他」（如通用算法工程师，技能全是跨组基础词）；
 * - 多命中按命中技能数取主组，其余标「兼」（如全栈=Web前端+后端，真实多面性）。
 * 关键词表集中在此，便于人工复核/增删（答辩可解释依据）。
 */
import type { GraphEdge, GraphNode } from './types'

/** 子组展示标签（可解释、对应实际技术栈） */
export interface SubgroupDef {
  label: string
  /** 领域专属词（命中即倾向该组；跨组基础词不放这） */
  keywords: string[]
}

/** 领域子组关键词表（集中维护，供人工复核与答辩引用） */
export const SUBGROUPS: SubgroupDef[] = [
  { label: '大模型/LLM', keywords: ['大语言模型', 'LLM', 'RAG', '检索增强生成', 'Transformer', 'Hugging Face', '微调', '提示词', 'AIGC', '生成式', '大模型'] },
  { label: '语音/自然语言', keywords: ['语音', 'VAD', '音频特征', '语音降噪', '语音合成', '自然语言处理', 'NLP', '语音识别'] },
  { label: '推荐/搜索', keywords: ['推荐算法', '推荐系统', '搜索', 'Pinecone', '向量检索', '召回', '排序', 'Embedding'] },
  { label: '计算机视觉', keywords: ['计算机视觉', '目标检测', '图像处理', '图像分割', 'OpenCV', '图像识别', '人脸识别', '视觉'] },
  { label: '机器人/自动驾驶', keywords: ['机器人', 'ROS', '运动控制', '路径规划', '传感器融合', '自动驾驶', '导航', '运动规划', '无人驾驶', 'SLAM'] },
  { label: '芯片/嵌入/验证', keywords: ['FPGA', 'SystemVerilog', 'Verilog', 'VHDL', 'RTL', 'RTOS', '嵌入式', 'BSP', 'Yocto', 'IC', 'GPU', '仿真', '数字电路', 'UVM', 'ASIC', 'SoC', 'UART', 'IP验证', '硬件'] },
  { label: 'Web前端', keywords: ['Vue', 'React', 'HTML', 'CSS', 'JavaScript', 'TypeScript', 'Angular', '前端', '小程序', 'H5', '响应式布局'] },
  { label: '移动/跨端', keywords: ['iOS', 'Android', 'React Native', 'Flutter', '鸿蒙', '移动端', 'Swift', 'Objective-C'] },
]

/** 未命中专属词的岗位落入的兜底组 */
export const FALLBACK_SUBGROUP = '通用/其他'

export interface SubgroupAssignment {
  /** 主组标签 */
  primary: string
  /** 兼组标签（多面性岗位，如全栈兼后端；无则空数组） */
  secondary: string[]
}

/**
 * 取岗位关联的 Top 技能名（weight 降序，取 k 个；无 weight 视为 0）。
 * 依赖调用方已构造 position→skill 邻接（见 groupSubgroups）。
 */
function topSkillNames(
  pid: string,
  skillByName: Map<string, string>,
  edges: GraphEdge[],
  k = 8,
): string[] {
  const rel: { name: string; w: number }[] = []
  for (const e of edges) {
    const s = e.source
    const t = e.target
    if (s === pid && skillByName.has(t)) rel.push({ name: skillByName.get(t)!, w: e.weight ?? 0 })
    else if (t === pid && skillByName.has(s)) rel.push({ name: skillByName.get(s)!, w: e.weight ?? 0 })
  }
  rel.sort((a, b) => b.w - a.w)
  return rel.slice(0, k).map((r) => r.name)
}

/**
 * 单岗位 → 子组归组（纯函数，供单测）。
 * @returns 主组 + 兼组；无专属命中返回 FALLBACK_SUBGROUP。
 */
export function assignPositionToSubgroup(
  pid: string,
  skillByName: Map<string, string>,
  edges: GraphEdge[],
): SubgroupAssignment {
  const skills = topSkillNames(pid, skillByName, edges)
  const hits: { label: string; n: number }[] = []
  for (const sg of SUBGROUPS) {
    const n = skills.filter((s) => sg.keywords.some((kw) => s.includes(kw))).length
    if (n > 0) hits.push({ label: sg.label, n })
  }
  if (hits.length === 0) return { primary: FALLBACK_SUBGROUP, secondary: [] }
  hits.sort((a, b) => b.n - a.n)
  const primary = hits[0].label
  const secondary = hits.slice(1).map((h) => h.label)
  return { primary, secondary }
}

/**
 * 域内成员岗位 → 子组归组（纯函数）。
 * @param members 域内岗位节点（GraphNode，type=position）
 * @param data 当前视图 GraphData（含 edges 与技能节点）
 * @returns { subgroup → [岗位] }，按子组固定顺序输出（SUBGROUPS 顺序 + FALLBACK 兜底）
 */
export function groupPositionsBySubgroup(
  members: GraphNode[],
  edges: GraphEdge[],
  skills: GraphNode[],
): { label: string; positions: GraphNode[] }[] {
  const skillByName = new Map(skills.map((s) => [s.id, s.name]))
  const bySub = new Map<string, GraphNode[]>()

  const assign = (p: GraphNode) => {
    const a = assignPositionToSubgroup(p.id, skillByName, edges)
    return a
  }

  // 先按固定组序初始化，保证输出顺序稳定
  const order = [...SUBGROUPS.map((s) => s.label), FALLBACK_SUBGROUP]
  for (const label of order) bySub.set(label, [])

  for (const p of members) {
    const a = assign(p)
    bySub.get(a.primary)?.push(p)
  }
  // 去掉空组
  const result: { label: string; positions: GraphNode[] }[] = []
  for (const label of order) {
    const list = bySub.get(label)
    if (list && list.length > 0) result.push({ label, positions: list })
  }
  return result
}
