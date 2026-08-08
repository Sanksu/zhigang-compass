/**
 * 节点详情面板 — 设计文档 §10.3
 *
 * 图谱画布右侧 25-30% 宽度展示选中节点的详细信息：
 * - position：状态机、关联技能数、description
 * - skill：级别、被多少岗位要求、关联证据、反向岗位列表、先修链、学习课程（真实 API）
 * - evidence：来源、描述
 */
import { X, Network, Cpu, FileText, BookOpen, GitBranch, Briefcase, ExternalLink, UnfoldVertical } from 'lucide-react'
import type { NodeDetail, PositionStatus } from './types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

/** 技能反向查询岗位项（GET /graph/skill/{id}/positions） */
export interface SkillPositionItem {
  position_id: string
  position_name: string
  necessity: 'must' | 'nice'
  weight: number
  level: string
}

/** 岗位详情必备/加分技能项（GET /graph/position/{id}） */
export interface PositionSkillItem {
  skill_id: string
  skill_name: string
  necessity: 'must' | 'nice'
  weight: number
  level: string
  source_count: number
}

/** 岗位节点详情（GET /graph/position/{id}） */
export interface PositionDetail {
  id: string
  name: string
  required_years?: number | null
  required_education?: string | null
  status?: PositionStatus
  must_skills: PositionSkillItem[]
  nice_skills: PositionSkillItem[]
}

/** 技能证据项（GET /graph/skill/{id}/evidence） */
export interface SkillEvidenceItem {
  id: string
  source: string
  source_url: string
  crawled_at?: string | null
}

/** 相似技能项（GET /graph/skill/similar） */
export interface SimilarSkillItem {
  skill_id: string
  skill_name: string
  similarity: number
}

/** 先修技能链项（GET /graph/skill/{id}/prerequisites） */
export interface PrerequisiteItem {
  skill_id: string | null
  name: string
  depth: number
}

/** 学习课程项（GET /graph/skill/{id}/courses） */
export interface SkillCourseItem {
  course_id: string
  title: string
  platform: string
  quality_score?: number | null
  recommended: boolean
  source_url: string
  hours?: number | null
}

/** 技能节点详情（skill 专属真实 API 数据） */
export interface SkillDetail {
  /** 对应技能节点 ID（用于与当前选中节点比对，切换节点时防旧数据闪显） */
  skill_id: string
  positions: SkillPositionItem[]
  prerequisites: PrerequisiteItem[]
  courses: SkillCourseItem[]
  loading: boolean
}

interface NodeDetailPanelProps {
  node: NodeDetail | null
  /** 关联边统计（按类型计数）+ 全图最大关联度（权重条归一化基准） */
  stats?: {
    positionCount?: number
    skillCount?: number
    evidenceCount?: number
    maxValue?: number
  }
  /** 岗位节点是否已在画布展开技能 */
  positionExpanded?: boolean
  /** 岗位节点展开/收起技能（画布） */
  onTogglePosition?: (id: string) => void
  /** skill 节点专属：反向岗位/先修链/课程 */
  skillDetail?: SkillDetail | null
  /** 岗位节点专属：详情（GET /graph/position/{id}） */
  positionDetail?: PositionDetail | null
  /** 技能节点专属：证据来源（GET /graph/skill/{id}/evidence） */
  skillEvidence?: SkillEvidenceItem[]
  /** 技能节点专属：相似技能（GET /graph/skill/similar） */
  similarSkills?: SimilarSkillItem[]
  /** 技能节点专属：相似技能点击 → 定位图谱节点 */
  onSelectSkill?: (id: string, name: string) => void
  onClose?: () => void
}

const STATUS_LABEL: Record<PositionStatus, string> = {
  candidate: '候选',
  emerging: '新兴',
  stable: '稳定',
  declining: '衰退',
  archived: '归档',
}

const STATUS_CLASS: Record<PositionStatus, string> = {
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
  if (!node) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-6 py-12 text-center">
        <Network className="size-8 text-ink-faint" />
        <p className="text-sm text-ink-muted">点击图谱节点查看详情</p>
        <p className="text-xs text-ink-faint max-w-[220px]">
          节点详情面板将展示岗位状态、技能级别与证据来源
        </p>
      </div>
    )
  }

  const Icon = TYPE_ICON[node.type]

  return (
    <div className="flex h-full flex-col">
      {/* 头部 */}
      <div className="flex items-start justify-between gap-3 border-b border-border p-4">
        <div className="flex items-start gap-3 min-w-0">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-subtle">
            <Icon className="size-4 text-ink-secondary" />
          </div>
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-ink truncate">{node.name}</h3>
            <p className="text-xs text-ink-muted mt-0.5">{TYPE_LABEL[node.type]}</p>
          </div>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="rounded-sm p-1 text-ink-faint transition-colors hover:bg-subtle hover:text-ink"
            aria-label="关闭详情"
          >
            <X className="size-4" />
          </button>
        )}
      </div>

      {/* 内容 */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* 类型与状态 */}
        <section className="space-y-2">
          <h4 className="text-xs font-medium text-ink-muted uppercase tracking-wide">属性</h4>
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline" className="text-xs">
              {TYPE_LABEL[node.type]}
            </Badge>
            {node.type === 'position' && node.status && (
              <Badge variant="outline" className={`text-xs border ${STATUS_CLASS[node.status]}`}>
                {STATUS_LABEL[node.status]}
              </Badge>
            )}
            {node.type === 'skill' && node.level && (
              <Badge variant="outline" className="text-xs">{node.level}</Badge>
            )}
          </div>
        </section>

        {/* 岗位专属：展开/收起画布技能（与双击岗位交互等价，双入口便于发现） */}
        {node.type === 'position' && onTogglePosition && (
          <Button
            variant="outline"
            className="w-full text-xs"
            onClick={() => onTogglePosition(node.id)}
          >
            <UnfoldVertical className="size-3 mr-1.5" />
            {positionExpanded ? '收起画布中的技能' : '在画布中展开技能'}
          </Button>
        )}

        {/* 关联度：条宽按全图最大关联度归一化，避免高频节点全部满条失真 */}
        {typeof node.value === 'number' && (
          <section className="space-y-1.5">
            <h4 className="text-xs font-medium text-ink-muted uppercase tracking-wide">关联度</h4>
            <div className="flex items-center gap-2">
              <div className="flex-1 h-1.5 rounded-full bg-subtle overflow-hidden">
                <div
                  className="h-full rounded-full bg-ink"
                  style={{
                    width: `${
                      stats?.maxValue
                        ? Math.max(4, Math.min(100, (node.value / stats.maxValue) * 100))
                        : Math.min(100, node.value)
                    }%`,
                  }}
                />
              </div>
              <span className="text-xs font-mono text-ink-secondary tabular-nums">{node.value}</span>
            </div>
            {stats?.maxValue ? (
              <p className="text-[10px] text-ink-faint">按全图最大关联度 {stats.maxValue} 归一化</p>
            ) : null}
          </section>
        )}

        {/* 来源（仅证据） */}
        {node.type === 'evidence' && node.source && (
          <section className="space-y-1.5">
            <h4 className="text-xs font-medium text-ink-muted uppercase tracking-wide">来源</h4>
            <p className="text-sm text-ink">{node.source}</p>
          </section>
        )}

        {/* 关联统计 */}
        {stats && (stats.positionCount || stats.skillCount || stats.evidenceCount) && (
          <section className="space-y-2">
            <h4 className="text-xs font-medium text-ink-muted uppercase tracking-wide">关联</h4>
            <dl className="grid grid-cols-3 gap-2 text-center">
              {stats.positionCount !== undefined && (
                <div className="rounded-md bg-subtle p-2">
                  <dt className="text-[10px] text-ink-muted">关联岗位</dt>
                  <dd className="text-sm font-mono text-ink tabular-nums">{stats.positionCount}</dd>
                </div>
              )}
              {stats.skillCount !== undefined && (
                <div className="rounded-md bg-subtle p-2">
                  <dt className="text-[10px] text-ink-muted">关联技能</dt>
                  <dd className="text-sm font-mono text-ink tabular-nums">{stats.skillCount}</dd>
                </div>
              )}
              {stats.evidenceCount !== undefined && (
                <div className="rounded-md bg-subtle p-2">
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
            <h4 className="text-xs font-medium text-ink-muted uppercase tracking-wide">描述</h4>
            <p className="text-sm text-ink-secondary leading-relaxed">{node.description}</p>
          </section>
        )}

        {/* 岗位专属：任职要求 + 必备/加分技能（真实 GET /graph/position/{id}） */}
        {node.type === 'position' && positionDetail && (
          <>
            {(positionDetail.required_years != null || positionDetail.required_education) && (
              <section className="space-y-1.5">
                <h4 className="text-xs font-medium text-ink-muted uppercase tracking-wide">任职要求</h4>
                <div className="flex flex-wrap gap-1.5">
                  {positionDetail.required_years != null && (
                    <Badge variant="outline" className="text-xs">{positionDetail.required_years} 年经验</Badge>
                  )}
                  {positionDetail.required_education && (
                    <Badge variant="outline" className="text-xs">{positionDetail.required_education}</Badge>
                  )}
                </div>
              </section>
            )}
            <section className="space-y-2">
              <h4 className="flex items-center gap-1.5 text-xs font-medium text-ink-muted uppercase tracking-wide">
                <Briefcase className="size-3" />
                必备技能
                <span className="ml-auto font-mono text-[10px]">{positionDetail.must_skills.length}</span>
              </h4>
              {positionDetail.must_skills.length === 0 ? (
                <p className="text-xs text-ink-faint py-1">图谱中暂无必备技能数据</p>
              ) : (
                <ul className="space-y-1">
                  {positionDetail.must_skills.map((s) => (
                    <li key={s.skill_id}>
                      <button
                        onClick={() => onSelectSkill?.(s.skill_id, s.skill_name)}
                        className="flex w-full items-center justify-between gap-2 rounded-md border border-border px-2 py-1.5 text-left transition-colors hover:border-border-strong hover:bg-subtle"
                      >
                        <span className="text-xs font-medium text-ink truncate">{s.skill_name}</span>
                        <span className="flex items-center gap-1.5 shrink-0">
                          {s.level && <span className="text-[10px] text-ink-faint">{s.level}</span>}
                          <span className="text-[10px] font-mono text-ink-faint">{(s.weight * 100).toFixed(0)}%</span>
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            {positionDetail.nice_skills.length > 0 && (
              <section className="space-y-2">
                <h4 className="flex items-center gap-1.5 text-xs font-medium text-ink-muted uppercase tracking-wide">
                  <Briefcase className="size-3" />
                  加分技能
                  <span className="ml-auto font-mono text-[10px]">{positionDetail.nice_skills.length}</span>
                </h4>
                <div className="flex flex-wrap gap-1.5">
                  {positionDetail.nice_skills.map((s) => (
                    <button
                      key={s.skill_id}
                      onClick={() => onSelectSkill?.(s.skill_id, s.skill_name)}
                      className="rounded-full border border-border px-2 py-0.5 text-[10px] text-ink-secondary transition-colors hover:border-border-strong hover:bg-subtle"
                    >
                      {s.skill_name}
                    </button>
                  ))}
                </div>
              </section>
            )}
          </>
        )}

        {/* 技能专属：反向岗位 / 先修链 / 学习课程（真实 API） */}
        {node.type === 'skill' && skillDetail && (
          <>
            <section className="space-y-2">
              <h4 className="flex items-center gap-1.5 text-xs font-medium text-ink-muted uppercase tracking-wide">
                <Briefcase className="size-3" />
                要求该技能的岗位
              </h4>
              {skillDetail.loading ? (
                <p className="text-xs text-ink-faint py-1">加载岗位列表…</p>
              ) : skillDetail.positions.length === 0 ? (
                <p className="text-xs text-ink-faint py-1">暂无岗位要求该技能</p>
              ) : (
                <ul className="space-y-1.5">
                  {skillDetail.positions.map((p) => (
                    <li key={p.position_id} className="flex items-center justify-between gap-2 rounded-md border border-border px-2 py-1.5">
                      <span className="text-xs font-medium text-ink truncate">{p.position_name}</span>
                      <span className="flex items-center gap-1 shrink-0">
                        <Badge variant="outline" className="text-[10px] font-mono">
                          {p.necessity === 'must' ? '必备' : '加分'}
                        </Badge>
                        <span className="text-[10px] font-mono text-ink-faint">{(p.weight * 100).toFixed(0)}%</span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="space-y-2">
              <h4 className="flex items-center gap-1.5 text-xs font-medium text-ink-muted uppercase tracking-wide">
                <GitBranch className="size-3" />
                先修技能链
              </h4>
              {skillDetail.loading ? (
                <p className="text-xs text-ink-faint py-1">加载先修链…</p>
              ) : skillDetail.prerequisites.length === 0 ? (
                <p className="text-xs text-ink-faint py-1">无先修技能（基础技能）</p>
              ) : (
                <ol className="space-y-1">
                  {skillDetail.prerequisites.map((p) => (
                    <li key={`${p.name}-${p.depth}`} className="flex items-center gap-2 text-xs">
                      <span className="flex size-4 shrink-0 items-center justify-center rounded-full bg-subtle font-mono text-[10px] text-ink-muted">
                        {p.depth}
                      </span>
                      <span className="text-ink">{p.name}</span>
                    </li>
                  ))}
                </ol>
              )}
            </section>

            <section className="space-y-2">
              <h4 className="flex items-center gap-1.5 text-xs font-medium text-ink-muted uppercase tracking-wide">
                <BookOpen className="size-3" />
                推荐课程
              </h4>
              {skillDetail.loading ? (
                <p className="text-xs text-ink-faint py-1">加载课程…</p>
              ) : skillDetail.courses.length === 0 ? (
                <p className="text-xs text-ink-faint py-1">暂无推荐课程</p>
              ) : (
                <ul className="space-y-1.5">
                  {skillDetail.courses.map((c) => (
                    <li key={c.course_id}>
                      <a
                        href={c.source_url || undefined}
                        target="_blank"
                        rel="noreferrer"
                        className="block rounded-md border border-border px-2 py-1.5 transition-colors hover:border-border-strong"
                      >
                        <span className="block text-xs font-medium text-ink leading-snug">{c.title}</span>
                        <span className="mt-0.5 flex items-center gap-2 text-[10px] text-ink-faint">
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

            {/* 相似技能（真实 GET /graph/skill/similar，语义相似度 ≥0.5） */}
            {similarSkills && similarSkills.length > 0 && (
              <section className="space-y-2">
                <h4 className="flex items-center gap-1.5 text-xs font-medium text-ink-muted uppercase tracking-wide">
                  <Network className="size-3" />
                  相似技能
                </h4>
                <div className="flex flex-wrap gap-1.5">
                  {similarSkills.map((s) => (
                    <button
                      key={s.skill_id}
                      onClick={() => onSelectSkill?.(s.skill_id, s.skill_name)}
                      title={`相似度 ${(s.similarity * 100).toFixed(0)}%（点击定位图谱节点）`}
                      className="flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-[10px] text-ink-secondary transition-colors hover:border-border-strong hover:bg-subtle"
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

            {/* 证据来源（真实 GET /graph/skill/{id}/evidence，Skill-EVIDENCED_BY->Evidence） */}
            {skillEvidence && skillEvidence.length > 0 && (
              <section className="space-y-2">
                <h4 className="flex items-center gap-1.5 text-xs font-medium text-ink-muted uppercase tracking-wide">
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
                        className="flex items-center justify-between gap-2 rounded-md border border-border px-2 py-1.5 transition-colors hover:border-border-strong"
                      >
                        <span className="flex items-center gap-1.5 text-xs text-ink truncate">
                          <ExternalLink className="size-3 shrink-0 text-ink-faint" />
                          {ev.source || '原始 JD'}
                        </span>
                        {ev.crawled_at && (
                          <span className="text-[10px] font-mono text-ink-faint shrink-0">
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
    </div>
  )
}
