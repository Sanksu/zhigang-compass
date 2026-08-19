/**
 * 节点详情面板 — 设计文档 §10.3
 *
 * 模块四重构：
 * - 右侧固定侧边栏（占满高度），保留展开/收起占位
 * - 头部 Badge 元信息
 * - 微型进度条展示匹配度/熟练度
 * - 先修链改为伪时间轴（lucide 图标）
 *
 * 类型来源：backend/openapi/openapi.yaml components.schemas（契约优先），
 * 经 openapi-typescript 生成至 src/types/api.d.ts。
 */
import {
  X,
  Network,
  Cpu,
  FileText,
  BookOpen,
  GitBranch,
  Briefcase,
  ExternalLink,
  UnfoldVertical,
  Target,
  ChevronRight,
  MapPin,
} from 'lucide-react'
import type { components } from '@/types/api'
import type { NodeDetail, PositionStatus } from './types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

/** B3: 骨架屏行组件 — 纯 CSS pulse 动画，无额外依赖 */
function SkeletonLine({ className }: { className?: string }) {
  return <div className={`h-7 rounded-lg bg-subtle animate-pulse ${className ?? ''}`} />
}

/** B3: 骨架屏列表（3行） */
function SkeletonList() {
  return (
    <div className="space-y-2">
      <SkeletonLine className="w-full" />
      <SkeletonLine className="w-4/5" />
      <SkeletonLine className="w-3/5" />
    </div>
  )
}

type Schema = components['schemas']

export type SkillPositionItem = Schema['SkillPositionItem']
export type PositionSkillItem = Schema['PositionSkillItem']
export type PositionDetail = Schema['PositionDetail']
export type SkillEvidenceItem = Schema['SkillEvidenceItem']
export type SimilarSkillItem = Schema['SimilarSkillItem']
export type PrerequisiteItem = Schema['PrerequisiteItem']
export type SkillCourseItem = Schema['CourseRecommendation']

export interface SkillDetail {
  skill_id: string
  positions: SkillPositionItem[]
  prerequisites: PrerequisiteItem[]
  courses: SkillCourseItem[]
  loading: boolean
}

interface NodeDetailPanelProps {
  node: NodeDetail | null
  stats?: {
    positionCount?: number
    skillCount?: number
    evidenceCount?: number
    maxValue?: number
  }
  positionExpanded?: boolean
  onTogglePosition?: (id: string) => void
  skillDetail?: SkillDetail | null
  positionDetail?: PositionDetail | null
  skillEvidence?: SkillEvidenceItem[]
  similarSkills?: SimilarSkillItem[]
  onSelectSkill?: (id: string, name: string) => void
  onClose?: () => void
}

const STATUS_LABEL: Record<PositionStatus, string> = {
  active: '活跃',
  candidate: '候选',
  emerging: '新兴',
  stable: '稳定',
  declining: '衰退',
  archived: '归档',
}

const STATUS_CLASS: Record<PositionStatus, string> = {
  active: 'bg-state-active/15 text-state-active border-state-active/30',
  candidate: 'bg-state-candidate/15 text-state-candidate border-state-candidate/30',
  emerging: 'bg-state-emerging/15 text-state-emerging border-state-emerging/30',
  stable: 'bg-state-stable/15 text-state-stable border-state-stable/30',
  declining: 'bg-state-declining/15 text-state-declining border-state-declining/30',
  archived: 'bg-state-archived/15 text-state-archived border-state-archived/30',
}

const TYPE_LABEL: Record<NodeDetail['type'], string> = {
  position: '岗位',
  skill: '技能',
  evidence: '证据',
}

const TYPE_ICON: Record<NodeDetail['type'], typeof Network> = {
  position: Network,
  skill: Cpu,
  evidence: FileText,
}

export function NodeDetailPanel({
  node,
  stats,
  skillDetail,
  positionDetail,
  skillEvidence,
  similarSkills,
  positionExpanded,
  onTogglePosition,
  onSelectSkill,
  onClose,
}: NodeDetailPanelProps) {
  const Icon = node ? TYPE_ICON[node.type] : Network

  return (
    <div className="flex h-full w-full flex-col bg-background">
      {!node ? (
        <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center text-ink-faint">
          <Network className="size-10 opacity-40" />
          <div className="text-sm">选择图谱中的节点</div>
          <div className="text-xs">单击节点查看岗位/技能详情与推荐路径</div>
        </div>
      ) : (
        <>
          {/* 头部 */}
          <div className="flex items-start justify-between gap-3 border-b border-border/60 p-4">
            <div className="flex items-start gap-3 min-w-0">
              <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                <Icon className="size-4 text-primary" />
              </div>
              <div className="min-w-0">
                <h3 className="text-sm font-semibold text-ink truncate">{node.name}</h3>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  <Badge variant="outline" className="px-1.5 py-0 text-[10px]">
                    {TYPE_LABEL[node.type]}
                  </Badge>
                  {node.type === 'position' && node.status && (
                    <Badge variant="outline" className={`px-1.5 py-0 text-[10px] ${STATUS_CLASS[node.status]}`}>
                      {STATUS_LABEL[node.status]}
                    </Badge>
                  )}
                  {node.type === 'skill' && node.level && (
                    <Badge variant="outline" className="px-1.5 py-0 text-[10px]">
                      {node.level}
                    </Badge>
                  )}
                </div>
              </div>
            </div>
            {onClose && (
              <button
                onClick={onClose}
                className="rounded-md p-1.5 text-ink-faint transition-colors hover:bg-subtle hover:text-ink"
                aria-label="关闭详情"
              >
                <X className="size-4" />
              </button>
            )}
          </div>

          {/* 内容 */}
          <div className="flex-1 space-y-4 overflow-y-auto p-4">
            {/* 匹配度 / 熟练度 微型进度条 */}
            {typeof node.value === 'number' && (
              <section className="space-y-2 rounded-xl bg-subtle/50 p-3">
                <div className="flex items-center gap-2 text-xs font-medium text-ink-muted">
                  <Target className="size-3.5" />
                  {node.type === 'skill' ? '熟练度' : '关联度'}
                </div>
                <div className="flex items-center gap-3">
                  <div className="flex-1 h-2 overflow-hidden rounded-full bg-border/80">
                    <div
                      className="h-full rounded-full bg-primary transition-all duration-500"
                      style={{
                        width: `${Math.max(
                          4,
                          Math.min(100, (node.value / (stats?.maxValue ?? Math.max(100, node.value))) * 100),
                        )}%`,
                      }}
                    />
                  </div>
                  <span className="text-xs font-mono text-ink-secondary tabular-nums">{node.value}</span>
                </div>
              </section>
            )}

            {/* 岗位：展开/收起技能 */}
            {node.type === 'position' && onTogglePosition && (
              <Button variant="outline" className="w-full text-xs" onClick={() => onTogglePosition(node.id)}>
                <UnfoldVertical className="mr-1.5 size-3" />
                {positionExpanded ? '收起画布中的技能' : '在画布中展开技能'}
              </Button>
            )}

            {/* 来源 */}
            {node.type === 'evidence' && node.source && (
              <section className="space-y-1.5">
                <h4 className="text-xs font-medium uppercase tracking-wide text-ink-muted">来源</h4>
                <p className="text-sm text-ink">{node.source}</p>
              </section>
            )}

            {/* 关联统计 */}
            {stats && (stats.positionCount || stats.skillCount || stats.evidenceCount) && (
              <section className="space-y-2">
                <h4 className="text-xs font-medium uppercase tracking-wide text-ink-muted">关联统计</h4>
                <dl className="grid grid-cols-3 gap-2 text-center">
                  {stats.positionCount !== undefined && (
                    <div className="rounded-lg bg-subtle/60 p-2">
                      <dt className="text-[10px] text-ink-muted">关联岗位</dt>
                      <dd className="text-sm font-mono text-ink tabular-nums">{stats.positionCount}</dd>
                    </div>
                  )}
                  {stats.skillCount !== undefined && (
                    <div className="rounded-lg bg-subtle/60 p-2">
                      <dt className="text-[10px] text-ink-muted">关联技能</dt>
                      <dd className="text-sm font-mono text-ink tabular-nums">{stats.skillCount}</dd>
                    </div>
                  )}
                  {stats.evidenceCount !== undefined && (
                    <div className="rounded-lg bg-subtle/60 p-2">
                      <dt className="text-[10px] text-ink-muted">关联证据</dt>
                      <dd className="text-sm font-mono text-ink tabular-nums">{stats.evidenceCount}</dd>
                    </div>
                  )}
                </dl>
              </section>
            )}

            {/* 描述 */}
            {node.description && (
              <section className="space-y-1.5">
                <h4 className="text-xs font-medium uppercase tracking-wide text-ink-muted">描述</h4>
                <p className="text-sm leading-relaxed text-ink-secondary">{node.description}</p>
              </section>
            )}

            {/* 岗位详情 */}
            {node.type === 'position' && positionDetail && (
              <>
                {(positionDetail.required_years != null || positionDetail.required_education) && (
                  <section className="space-y-1.5">
                    <h4 className="text-xs font-medium uppercase tracking-wide text-ink-muted">任职要求</h4>
                    <div className="flex flex-wrap gap-1.5">
                      {positionDetail.required_years != null && (
                        <Badge variant="outline" className="text-xs">
                          {positionDetail.required_years} 年经验
                        </Badge>
                      )}
                      {positionDetail.required_education && (
                        <Badge variant="outline" className="text-xs">
                          {positionDetail.required_education}
                        </Badge>
                      )}
                    </div>
                  </section>
                )}

                <section className="space-y-2">
                  <h4 className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-ink-muted">
                    <Briefcase className="size-3" />
                    必备技能
                    <span className="ml-auto font-mono text-[10px]">{positionDetail.must_skills.length}</span>
                  </h4>
                  {positionDetail.must_skills.length === 0 ? (
                    <p className="py-1 text-xs text-ink-faint">图谱中暂无必备技能数据</p>
                  ) : (
                    <ul className="space-y-1">
                      {positionDetail.must_skills.map((s) => (
                        <li key={s.skill_id}>
                          <button
                            onClick={() => onSelectSkill?.(s.skill_id, s.skill_name)}
                            className="flex w-full items-center justify-between gap-2 rounded-lg border border-border px-2.5 py-2 text-left transition-colors hover:border-border-strong hover:bg-subtle/60"
                          >
                            <span className="truncate text-xs font-medium text-ink">{s.skill_name}</span>
                            <span className="flex shrink-0 items-center gap-1.5">
                              {s.level && <span className="text-[10px] text-ink-faint">{s.level}</span>}
                              <span className="text-[10px] font-mono text-ink-faint">
                                {(s.weight * 100).toFixed(0)}%
                              </span>
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                {positionDetail.nice_skills.length > 0 && (
                  <section className="space-y-2">
                    <h4 className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-ink-muted">
                      <Briefcase className="size-3" />
                      加分技能
                      <span className="ml-auto font-mono text-[10px]">{positionDetail.nice_skills.length}</span>
                    </h4>
                    <div className="flex flex-wrap gap-1.5">
                      {positionDetail.nice_skills.map((s) => (
                        <button
                          key={s.skill_id}
                          onClick={() => onSelectSkill?.(s.skill_id, s.skill_name)}
                          className="rounded-full border border-border px-2.5 py-1 text-[10px] text-ink-secondary transition-colors hover:border-border-strong hover:bg-subtle/60"
                        >
                          {s.skill_name}
                        </button>
                      ))}
                    </div>
                  </section>
                )}
              </>
            )}

            {/* 技能详情 */}
            {node.type === 'skill' && skillDetail && (
              <>
                <section className="space-y-2">
                  <h4 className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-ink-muted">
                    <Briefcase className="size-3" />
                    要求该技能的岗位
                  </h4>
                  {skillDetail.loading ? (
                    <SkeletonList />
                  ) : skillDetail.positions.length === 0 ? (
                    <p className="py-1 text-xs text-ink-faint">暂无岗位要求该技能</p>
                  ) : (
                    <ul className="space-y-1.5">
                      {skillDetail.positions.map((p) => (
                        <li
                          key={p.position_id}
                          className="flex items-center justify-between gap-2 rounded-lg border border-border px-2.5 py-2"
                        >
                          <span className="truncate text-xs font-medium text-ink">{p.position_name}</span>
                          <span className="flex shrink-0 items-center gap-1">
                            <Badge variant="outline" className="font-mono text-[10px]">
                              {p.necessity === 'must' ? '必备' : '加分'}
                            </Badge>
                            <span className="text-[10px] font-mono text-ink-faint">
                              {(p.weight * 100).toFixed(0)}%
                            </span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                {/* 先修链伪时间轴 */}
                <section className="space-y-2">
                  <h4 className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-ink-muted">
                    <GitBranch className="size-3" />
                    前置推荐路径
                  </h4>
                  {skillDetail.loading ? (
                    <SkeletonList />
                  ) : skillDetail.prerequisites.length === 0 ? (
                    <p className="py-1 text-xs text-ink-faint">无先修技能（基础技能）</p>
                  ) : (
                    <div className="relative pl-2">
                      <div className="absolute bottom-2 left-[11px] top-2 w-px bg-border" />
                      <ol className="space-y-3">
                        {skillDetail.prerequisites.map((p, i) => (
                          <li key={`${p.name}-${p.depth}`} className="relative flex items-start gap-3">
                            <div className="relative z-10 flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/10 ring-2 ring-white dark:ring-zinc-900">
                              <MapPin className="size-2.5 text-primary" />
                            </div>
                            <div className="flex-1">
                              <div className="flex items-center gap-2">
                                <span className="text-xs font-medium text-ink">{p.name}</span>
                                <span className="rounded bg-subtle px-1 py-0 text-[9px] font-mono text-ink-muted">
                                  深度 {p.depth}
                                </span>
                              </div>
                              {i < skillDetail.prerequisites.length - 1 && (
                                <div className="mt-1 flex items-center gap-1 text-[10px] text-ink-faint">
                                  <ChevronRight className="size-3" />
                                  建议掌握后继续
                                </div>
                              )}
                            </div>
                          </li>
                        ))}
                      </ol>
                    </div>
                  )}
                </section>

                <section className="space-y-2">
                  <h4 className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-ink-muted">
                    <BookOpen className="size-3" />
                    推荐课程
                  </h4>
                  {skillDetail.loading ? (
                    <SkeletonList />
                  ) : skillDetail.courses.length === 0 ? (
                    <p className="py-1 text-xs text-ink-faint">暂无推荐课程</p>
                  ) : (
                    <ul className="space-y-1.5">
                      {skillDetail.courses.map((c) => (
                        <li key={c.course_id}>
                          <a
                            href={c.source_url || undefined}
                            target="_blank"
                            rel="noreferrer"
                            className="block rounded-lg border border-border px-2.5 py-2 transition-colors hover:border-border-strong"
                          >
                            <span className="block text-xs font-medium leading-snug text-ink">{c.title}</span>
                            <span className="mt-1 flex items-center gap-2 text-[10px] text-ink-faint">
                              <span>{c.platform}</span>
                              {c.hours != null && <span>· {c.hours}h</span>}
                              {c.quality_score != null && (
                                <span>· 质量 {(c.quality_score * 100).toFixed(0)}</span>
                              )}
                            </span>
                          </a>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                {similarSkills && similarSkills.length > 0 && (
                  <section className="space-y-2">
                    <h4 className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-ink-muted">
                      <Network className="size-3" />
                      相似技能
                    </h4>
                    <div className="flex flex-wrap gap-1.5">
                      {similarSkills.map((s) => (
                        <button
                          key={s.skill_id}
                          onClick={() => onSelectSkill?.(s.skill_id, s.skill_name)}
                          title={`相似度 ${(s.similarity * 100).toFixed(0)}%`}
                          className="flex items-center gap-1 rounded-full border border-border px-2 py-1 text-[10px] text-ink-secondary transition-colors hover:border-border-strong hover:bg-subtle/60"
                        >
                          {s.skill_name}
                          <span className="font-mono text-[9px] text-ink-faint">
                            {(s.similarity * 100).toFixed(0)}
                          </span>
                        </button>
                      ))}
                    </div>
                  </section>
                )}

                {skillEvidence && skillEvidence.length > 0 && (
                  <section className="space-y-2">
                    <h4 className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-ink-muted">
                      <ExternalLink className="size-3" />
                      证据来源
                      <span className="ml-auto font-mono text-[10px]">{skillEvidence.length}</span>
                    </h4>
                    <ul className="space-y-1">
                      {skillEvidence.slice(0, 8).map((ev) => (
                        <li key={ev.id}>
                          <a
                            href={ev.source_url || undefined}
                            target="_blank"
                            rel="noreferrer"
                            className="flex items-center justify-between gap-2 rounded-lg border border-border px-2.5 py-2 transition-colors hover:border-border-strong"
                          >
                            <span className="flex items-center gap-1.5 truncate text-xs text-ink">
                              <ExternalLink className="size-3 shrink-0 text-ink-faint" />
                              {ev.source || '原始 JD'}
                            </span>
                            {ev.crawled_at && (
                              <span className="shrink-0 text-[10px] font-mono text-ink-faint">
                                {ev.crawled_at.slice(0, 10)}
                              </span>
                            )}
                          </a>
                        </li>
                      ))}
                    </ul>
                    {skillEvidence.length > 8 && (
                      <p className="text-[10px] text-ink-faint">仅显示前 8 条，共 {skillEvidence.length} 条</p>
                    )}
                  </section>
                )}
              </>
            )}
          </div>
        </>
      )}
    </div>
  )
}
