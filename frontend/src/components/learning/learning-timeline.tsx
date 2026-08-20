/**
 * 学习路径「微观点状时间轴」— 设计文档双轨制重构 Task 1.2
 *
 * 目标：把力导向"毛球"结构性平铺为可行动的纵向 Stepper：
 *  - 通过先修关系做拓扑分层 → 拆成若干「阶段（Milestone）」；
 *  - 阶段下渲染「任务卡（Task Card）」：预计学时 + 课程数 + 推荐课程 + 前往学习。
 *
 * 数据源：后端 /match/.../path 的 LearningPathItem 数组（status/estimatedHours/roi
 * 后端未直接返回 → 类型已按可选扩展，此处由 `buildTimelineMilestones` 兜底推导）。
 *
 * 纯函数 buildTimelineMilestones 独立导出（供宏观 DAG 视图复用同一套分层逻辑，
 * 保证两视图口径一致）。
 */
import { useMemo } from 'react'
import { ArrowRight, BookOpen, Clock } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { LearningPathItem } from '@/components/match/types'

/** 学习状态：已掌握 / 下一步 / 未解锁 */
export type LearningStatus = 'done' | 'doing' | 'locked'

/** 学习路径中的一项（阶段时间轴任务） */
export interface TimelineTask {
  /** 技能名（作 key） */
  skill: string
  status: LearningStatus
  /** 预计学时（小时） */
  estimatedHours: number
  /** 关联课程数 */
  coursesCount: number
  prerequisites: string[]
  priority: LearningPathItem['priority']
  /** 可选：ROI 指标（供高杠杆缺口打标复用） */
  roi?: number
  /** 推荐课程（含跳转链接，接入课程） */
  courses?: { title: string; platform: string; hours: number; url?: string }[]
}

/** 时间轴中的一个阶段（拓扑层） */
export interface TimelineMilestone {
  /** 层号（1 起，越长先修越深） */
  depth: number
  /** 阶段显示名 */
  label: string
  tasks: TimelineTask[]
}

interface LearningTimelineProps {
  items: LearningPathItem[]
  /** 已掌握技能集（可空；空则把首个可解锁任务判为 doing） */
  completedSkills?: string[]
  onGoToLearn?: (task: TimelineTask) => void
  className?: string
}

/* ── 展示口径常量 ─────────────────────────────── */
const PRIORITY_RANK: Record<LearningPathItem['priority'], number> = { high: 0, medium: 1, low: 2 }

const PRIORITY_LABEL: Record<LearningPathItem['priority'], string> = { high: '高优', medium: '中优', low: '低优' }

/** 缺省学时：duration_days×8 小时（与后端甘特折算一致） */
function estimateHours(item: LearningPathItem): number {
  return item.estimatedHours ?? Math.round((item.duration_days ?? 1) * 8 * 10) / 10
}

/**
 * 将学习路径按先修关系拓扑分层，并为每项推导学习状态。
 *
 * 分层（Milestone）：depth(skill) = 1 + max(depth(prereq))（未知先修视为已满足，不阻断）。
 * 状态推导：
 *  - done   = skill ∈ completedSkills（已掌握）
 *  - doing  = 拓扑顺序中第一个「先修已全部满足且自身未掌握」的任务 → 下一步行动
 *  - locked = 其余尚不可学
 */
export function buildTimelineMilestones(
  items: LearningPathItem[],
  completedSkills: string[] = [],
): TimelineMilestone[] {
  if (items.length === 0) return []

  const byName = new Map<string, LearningPathItem>()
  for (const it of items) byName.set(it.skill, it)

  // 1. 记忆化求每项深度
  const memo = new Map<string, number>()
  const depthOf = (skill: string): number => {
    if (memo.has(skill)) return memo.get(skill)!
    const item = byName.get(skill)
    if (!item) return 0 // 未知技能不计深
    const knownPrereqs = item.prerequisites.filter((p) => byName.has(p))
    const d = knownPrereqs.length ? 1 + Math.max(...knownPrereqs.map(depthOf)) : 1
    memo.set(skill, d)
    return d
  }
  // 先算全部（避免递归中 memo 未命中时的重复计算）
  for (const it of items) depthOf(it.skill)

  // 2. 分组成 [[depth, item]]，大体按深度升序
  const layers = new Map<number, LearningPathItem[]>()
  for (const it of items) {
    const d = depthOf(it.skill)
    if (!layers.has(d)) layers.set(d, [])
    layers.get(d)!.push(it)
  }

  // 3. 全局拓扑顺序（depth 升序，层内按先修满足度稳定的索引序）→ 用作「下一步」判定
  const ordered: LearningPathItem[] = []
  for (let d = 1; d <= layers.size; d++) {
    for (const it of layers.get(d) ?? []) ordered.push(it)
  }
  const doneSet = new Set(completedSkills)
  // 「下一步」= 就绪（先修全满足）且未掌握的任务中，优先级最高的那个（并列取拓扑先序）。
  // 优先高优任务而非严格顺序先者，符合 ROI 导向（阶段内先啃高杠杆缺口）。
  const doingSkill =
    ordered
      .filter(
        (it) => !doneSet.has(it.skill) && it.prerequisites.every((p) => doneSet.has(p) || !byName.has(p)),
      )
      .reduce<LearningPathItem | null>(
        (best, it) => (best === null || PRIORITY_RANK[it.priority] < PRIORITY_RANK[best.priority] ? it : best),
        null,
      )?.skill ?? null
  const statusBySkill = new Map<string, LearningStatus>()
  for (const it of ordered) {
    statusBySkill.set(it.skill, doneSet.has(it.skill) ? 'done' : it.skill === doingSkill ? 'doing' : 'locked')
  }

  // 4. 组装 Milestone（层内排序：优先级、名称）
  const milestones: TimelineMilestone[] = []
  for (let d = 1; d <= layers.size; d++) {
    const tasks = (layers.get(d) ?? [])
      .map<TimelineTask>((it) => ({
        skill: it.skill,
        status: statusBySkill.get(it.skill) ?? 'locked',
        estimatedHours: estimateHours(it),
        coursesCount: it.courses?.length ?? 0,
        prerequisites: it.prerequisites,
        priority: it.priority,
        roi: it.roi,
        courses: it.courses?.map((c) => ({
          title: c.title,
          platform: c.platform,
          hours: c.hours ?? 0,
          url: c.url,
        })),
      }))
      .sort(
        (a, b) =>
          PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority] ||
          a.skill.localeCompare(b.skill, 'zh'),
      )
    milestones.push({ depth: d, label: `阶段 ${d}`, tasks })
  }
  return milestones
}

/* ── 宏观 DAG 视图数据（task T2，与时间轴共用分层/状态口径） ── */

/** DAG 节点（学习路径技能） */
export interface DagSkillNode {
  id: string
  name: string
  /** 拓扑层号（1 起，越大越靠后/越深） */
  layer: number
  status: LearningStatus
}
/** DAG 先修边：source → target（source 是 target 的前置） */
export interface DagSkillLink {
  source: string
  target: string
}

/** 由学习路径构建 DAG 数据：先修边 + 分层 + 状态（供宏观拓扑树渲染） */
export function buildDagGraph(
  items: LearningPathItem[],
  completedSkills: string[] = [],
): { nodes: DagSkillNode[]; links: DagSkillLink[] } {
  const milestones = buildTimelineMilestones(items, completedSkills)
  const known = new Set(items.map((it) => it.skill))

  const nodes: DagSkillNode[] = []
  for (const m of milestones) {
    for (const t of m.tasks) nodes.push({ id: t.skill, name: t.skill, layer: m.depth, status: t.status })
  }

  const links: DagSkillLink[] = []
  for (const it of items) {
    for (const p of it.prerequisites) {
      // 仅连两端都在路径内、且在组织点位的边（先修 → 当前技能）
      if (known.has(p) && known.has(it.skill)) links.push({ source: p, target: it.skill })
    }
  }
  return { nodes, links }
}

export function LearningTimeline({ items, completedSkills, onGoToLearn, className }: LearningTimelineProps) {
  // 重拓扑分层 + 状态推导：数据量小时仍是纯计算，量级上来后用 useMemo 缓存避免每次重渲染重排
  const milestones = useMemo(() => buildTimelineMilestones(items, completedSkills), [items, completedSkills])

  // 汇总：总任务 / 总学时（用于表头，轻量常驻）
  const summary = useMemo(() => {
    const totalTasks = items.length
    const hours = milestones.reduce((n, m) => n + m.tasks.reduce((s, t) => s + t.estimatedHours, 0), 0)
    return { totalTasks, hours }
  }, [items, milestones])

  if (milestones.length === 0) {
    return (
      <div className={cn('py-10 text-center text-xs text-ink-faint', className)}>
        无需要补足的技能差距，学习路径为空
      </div>
    )
  }

  return (
    <div className={cn('space-y-2', className)}>
      {/* 路径概览 */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg bg-subtle/50 px-3 py-2 text-xs text-ink-muted">
        <span className="font-medium text-ink">{summary.totalTasks} 项技能</span>
        <span className="text-ink-faint">·</span>
        <span className="flex items-center gap-1">
          <Clock className="size-3.5" />约 {summary.hours} 学时
        </span>
      </div>

      {/* 纵向时间轴 */}
      <div className="relative">
        {/* 主连接线 */}
        <div className="absolute bottom-4 left-[9px] top-4 w-px bg-border" />

        <ol className="space-y-6">
          {milestones.map((ms, i) => {
            const isLast = i === milestones.length - 1
            return (
              <li key={ms.depth} className="relative pl-8">
                {/* 阶段节点 */}
                <div className="absolute left-0 top-0.5 flex size-5 items-center justify-center rounded-full bg-primary text-canvas ring-4 ring-canvas">
                  <span className="text-[10px] font-semibold leading-none">{ms.depth}</span>
                </div>
                {/* 阶段标题 */}
                <div className="mb-2 flex items-baseline gap-2">
                  <span className="text-sm font-semibold text-ink">{ms.label}</span>
                  <span className="text-[10px] text-ink-faint">{ms.tasks.length} 项</span>
                </div>

                {/* 任务卡列表 */}
                <div className="space-y-2">
                  {ms.tasks.map((task) => {
                    return (
                      <div
                        key={task.skill}
                        className="flex items-start gap-3 rounded-lg border border-border bg-canvas p-2.5 transition-colors"
                      >
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span className="truncate text-sm font-medium text-ink">{task.skill}</span>
                            {task.priority === 'high' && (
                              <span className="rounded-full border border-state-archived/30 bg-state-archived/10 px-1.5 py-0 text-[10px] text-state-archived">
                                {PRIORITY_LABEL.high}
                              </span>
                            )}
                          </div>

                          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-ink-faint">
                            <span className="flex items-center gap-1">
                              <Clock className="size-3" />约 {task.estimatedHours} 学时
                            </span>
                            <span className="flex items-center gap-1">
                              <BookOpen className="size-3" />{task.coursesCount} 门课程
                            </span>
                            {task.prerequisites.length > 0 && (
                              <span className="truncate">先修：{task.prerequisites.join('、')}</span>
                            )}
                          </div>

                          {/* 推荐课程（可点击跳转） */}
                          {task.courses && task.courses.length > 0 && (
                            <div className="mt-1.5 space-y-1">
                              {task.courses.slice(0, 2).map((c, ci) =>
                                c.url ? (
                                  <a
                                    key={ci}
                                    href={c.url}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="flex items-center gap-1.5 rounded border border-border px-2 py-1 text-[10px] text-ink-secondary transition-colors hover:border-border-strong hover:bg-subtle/60"
                                  >
                                    <BookOpen className="size-3 shrink-0 text-primary" />
                                    <span className="truncate">{c.title}</span>
                                    <span className="ml-auto shrink-0 text-ink-faint">{c.platform}</span>
                                  </a>
                                ) : (
                                  <span
                                    key={ci}
                                    className="flex items-center gap-1.5 rounded border border-border px-2 py-1 text-[10px] text-ink-secondary"
                                  >
                                    <BookOpen className="size-3 shrink-0 text-primary" />
                                    <span className="truncate">{c.title}</span>
                                    <span className="ml-auto shrink-0 text-ink-faint">{c.platform}</span>
                                  </span>
                                ),
                              )}
                            </div>
                          )}
                        </div>

                        {/* CTA */}
                        <div className="shrink-0">
                          <button
                            type="button"
                            onClick={() => {
                              // 优先回调（页面可自定义跳转/标记）；缺省跳首门课程链接
                              if (onGoToLearn) onGoToLearn(task)
                              else {
                                const url = task.courses?.find((c) => c.url)?.url
                                if (url) window.open(url, '_blank', 'noreferrer')
                              }
                            }}
                            className="inline-flex items-center gap-1 rounded-md bg-primary px-2.5 py-1 text-[10px] font-medium text-canvas transition-opacity hover:opacity-90"
                          >
                            前往学习 <ArrowRight className="size-3" />
                          </button>
                        </div>
                      </div>
                    )
                  })}
                </div>

                {/* 层间连接（最后一层省略） */}
                {!isLast && <div className="absolute bottom-[-18px] left-[9px] h-[18px] w-px bg-border" />}
              </li>
            )
          })}
        </ol>
      </div>
    </div>
  )
}