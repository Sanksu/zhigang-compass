import { useEffect, useState } from 'react'
import { ArrowRight, CheckCircle2, AlertCircle, XCircle, ExternalLink, RotateCcw, FileText } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ResumeUploader } from '@/components/resume/resume-uploader'
import { ScoreRing, RadarChart, SkillHeatmap, GanttChart } from '@/components/match/charts'
import {
  type BackendMatchResult,
  type CandidateProfile,
  type GapItem,
  type MatchResult,
  type RecommendItem,
  type ResumeSummary,
  type SkillMatrixItem,
} from '@/components/match/types'
import type { PositionStatus } from '@/components/graph/types'
import { apiGet, apiPost, ApiError } from '@/lib/api'

const STATUS_LABEL: Record<PositionStatus | 'low', string> = {
  candidate: '候选',
  emerging: '新兴',
  stable: '稳定',
  declining: '衰退',
  archived: '归档',
  low: '待提升',
}

const STATUS_CLASS: Record<PositionStatus | 'low', string> = {
  candidate: 'border-state-candidate/30 text-state-candidate bg-state-candidate/10',
  emerging: 'border-state-emerging/30 text-state-emerging bg-state-emerging/10',
  stable: 'border-state-stable/30 text-state-stable bg-state-stable/10',
  declining: 'border-state-declining/30 text-state-declining bg-state-declining/10',
  archived: 'border-state-archived/30 text-state-archived bg-state-archived/10',
  low: 'border-ink-faint/30 text-ink-muted bg-subtle',
}

const PRIORITY_LABEL = { high: '高', medium: '中', low: '低' } as const
const PRIORITY_CLASS = {
  high: 'border-state-archived/30 text-state-archived bg-state-archived/10',
  medium: 'border-state-declining/30 text-state-declining bg-state-declining/10',
  low: 'border-ink-faint/30 text-ink-muted bg-subtle',
} as const

const GAP_TYPE_LABEL = {
  missing_must: '必备缺失',
  level_gap: '熟练度差距',
  missing_nice: '加分项缺失',
} as const

// ============================================================
// 真实后端数据 → 前端展示结构
// ============================================================

function toRecommendItem(r: BackendMatchResult): RecommendItem {
  const status: RecommendItem['status'] =
    r.total_score >= 0.6 ? 'stable' : r.total_score >= 0.4 ? 'declining' : 'low'
  return {
    position_id: r.position_id,
    position_name: r.position_name,
    total_score: r.total_score,
    must_score: r.must_score,
    nice_score: r.nice_score,
    exp_score: r.exp_score,
    summary: r.summary,
    status,
    key_gaps: r.missing_must.slice(0, 3),
  }
}

function toMatchResult(r: BackendMatchResult): MatchResult {
  const skill_matrix: SkillMatrixItem[] = [
    ...r.matched_must.map((s) => ({
      skill: s, candidate_level: 3, required_level: 3, necessity: 'must' as const, match: 'full' as const,
    })),
    ...r.missing_must.map((s) => ({
      skill: s, candidate_level: 0, required_level: 3, necessity: 'must' as const, match: 'missing' as const,
    })),
  ]
  const gaps: GapItem[] = r.missing_must.map((s) => ({
    skill: s, gap_type: 'missing_must' as const, priority: 'high' as const, current_level: '未掌握', required_level: '熟练',
  }))
  return {
    position_id: r.position_id,
    position_name: r.position_name,
    total_score: r.total_score,
    must_score: r.must_score,
    nice_score: r.nice_score,
    exp_score: r.exp_score,
    summary: r.summary,
    radar: [
      { name: '必备技能', candidate: Math.round(r.must_score * 100), required: 100 },
      { name: '加分技能', candidate: Math.round(r.nice_score * 100), required: 80 },
      { name: '工作经验', candidate: Math.round(r.exp_score * 100), required: 85 },
      { name: '学历背景', candidate: 70, required: 75 },
      { name: '项目经验', candidate: 70, required: 70 },
    ],
    skill_matrix: skill_matrix,
    gaps,
    // 学习路径 / 证据引用依赖课程图谱与证据追溯（M4 交付），后端暂未产出 → 空态
    learning_path: [],
    evidence_refs: [],
  }
}

function toCandidate(s: ResumeSummary): CandidateProfile {
  return {
    name: s.file_name.replace(/\.(pdf|docx?|png|jpe?g)$/i, ''),
    total_years: s.total_years,
    education: s.education_level ?? '未知',
    skills: s.skills.map((n) => ({ name: n, level: '—' })),
  }
}

/**
 * 简历匹配页 — 设计文档 §10.4
 *
 * 数据来源：真实后端 API
 * 上传 → POST /resume/parse；载入已有简历 → GET /resume/list；
 * 推荐 → POST /match/recommend；比对 → POST /match/compare。
 * 学习路径 / 证据引用后端未产出（M4），显示空态。
 */
export function ResumeMatchPage() {
  const [stage, setStage] = useState<'upload' | 'parsing' | 'matched'>('upload')
  const [candidate, setCandidate] = useState<CandidateProfile | null>(null)
  const [recommendations, setRecommendations] = useState<RecommendItem[]>([])
  const [selectedPosition, setSelectedPosition] = useState<RecommendItem | null>(null)
  const [matchResult, setMatchResult] = useState<MatchResult | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [resumeList, setResumeList] = useState<ResumeSummary[]>([])
  const [activeResumeId, setActiveResumeId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  // 载入已解析简历列表（后端 /resume/list）
  useEffect(() => {
    apiGet<{ items: ResumeSummary[]; total: number }>('/resume/list')
      .then((res) => setResumeList(res.items))
      .catch(() => {
        /* 列表加载失败不阻塞页面 */
      })
  }, [])

  // 上传简历 → 真实异步解析
  async function handleFileSelected(file: File) {
    setNotice(null)
    setStage('parsing')
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await apiPost<{ task_id: string; resume_id: string; cached: boolean }>(
        '/resume/parse',
        form,
        { headers: { 'Content-Type': 'multipart/form-data' } },
      )
      if (res.cached) {
        await loadRecommend(res.resume_id)
        return
      }
      // 新简历解析任务已入队（后台 worker 处理完成后写入 resume_cache 即可匹配）
      setStage('upload')
      setNotice(`解析任务已提交（${file.name}），等待后台处理。可直接从下方"已有简历"载入示例候选人发起匹配。`)
    } catch (e) {
      setStage('upload')
      setNotice(e instanceof ApiError ? e.message : '上传失败，请检查后端服务')
    }
  }

  // 载入已有简历 → 真实推荐
  async function loadRecommend(resumeId: string) {
    const summary = resumeList.find((r) => r.id === resumeId)
    if (summary) setCandidate(toCandidate(summary))
    setActiveResumeId(resumeId)
    setStage('parsing')
    try {
      const res = await apiPost<{ items: BackendMatchResult[] }>('/match/recommend', {
        resume_id: resumeId,
        top_n: 10,
      })
      const items = res.items.map(toRecommendItem)
      setRecommendations(items)
      setStage('matched')
      setSelectedPosition(null)
      setMatchResult(null)
    } catch (e) {
      setStage('upload')
      setNotice(e instanceof ApiError ? e.message : '推荐失败，请检查后端服务')
    }
  }

  // 选中推荐岗位 → 真实人岗比对
  async function handleSelectPosition(rec: RecommendItem) {
    if (!activeResumeId) return
    setSelectedPosition(rec)
    setLoadingDetail(true)
    setMatchResult(null)
    try {
      const res = await apiPost<BackendMatchResult>('/match/compare', {
        resume_id: activeResumeId,
        position_id: rec.position_id,
      })
      setMatchResult(toMatchResult(res))
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : '比对失败')
    } finally {
      setLoadingDetail(false)
    }
  }

  function handleReset() {
    setStage('upload')
    setCandidate(null)
    setRecommendations([])
    setSelectedPosition(null)
    setMatchResult(null)
    setActiveResumeId(null)
    setNotice(null)
  }

  // ===== 上传阶段 =====
  if (stage === 'upload' || stage === 'parsing') {
    return (
      <>
        <PageHeader
          title="简历匹配"
          description="上传简历 → 自动推荐岗位 → 人岗比对分析"
        />
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">简历上传</CardTitle>
              <CardDescription>支持 PDF / Word / 图片，≤ 10MB · PII 脱敏后送入 LLM</CardDescription>
            </CardHeader>
            <CardContent>
              <ResumeUploader
                loading={stage === 'parsing'}
                onFileSelected={handleFileSelected}
              />
              {notice && (
                <p className="text-xs text-ink-muted mt-3 border border-border rounded-md p-2 bg-subtle">
                  {notice}
                </p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">已有简历</CardTitle>
              <CardDescription>选择已解析候选人发起真实匹配（推荐 / 比对）</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {resumeList.length === 0 && (
                <p className="text-xs text-ink-faint py-6 text-center">
                  暂无已解析简历，请先上传简历触发解析，或等待解析任务完成
                </p>
              )}
              {resumeList.map((r) => (
                <div
                  key={r.id}
                  className="rounded-md border border-border p-3 hover:bg-subtle transition-colors"
                >
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <span className="flex items-center gap-2 text-sm font-medium text-ink truncate">
                      <FileText className="size-4 text-ink-muted shrink-0" />
                      {r.file_name}
                    </span>
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 text-xs shrink-0"
                      disabled={stage === 'parsing'}
                      onClick={() => loadRecommend(r.id)}
                    >
                      发起匹配
                    </Button>
                  </div>
                  <div className="flex items-center gap-1.5 flex-wrap">
                    {r.skills.slice(0, 8).map((s) => (
                      <Badge key={s} variant="outline" className="text-[10px]">
                        {s}
                      </Badge>
                    ))}
                    {r.skills.length > 8 && (
                      <span className="text-[10px] text-ink-faint">+{r.skills.length - 8}</span>
                    )}
                    <span className="text-[10px] text-ink-faint ml-auto">
                      {r.total_years} 年 · {r.education_level ?? '未知学历'}
                    </span>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </>
    )
  }

  // ===== 匹配结果阶段 =====
  return (
    <>
      <PageHeader
        title="简历匹配"
        description="上传简历 → 自动推荐岗位 → 人岗比对分析"
        actions={
          <Button variant="outline" size="sm" onClick={handleReset}>
            <RotateCcw className="size-3.5" />
            重新选择
          </Button>
        }
      />

      {/* 候选人画像摘要 */}
      {candidate && (
        <Card className="mb-4">
          <CardContent className="py-4">
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
              <span className="font-medium text-ink">{candidate.name}</span>
              <span className="text-ink-muted">
                {candidate.total_years} 年经验 · {candidate.education}
              </span>
              <div className="flex items-center gap-1.5 flex-wrap">
                {candidate.skills.slice(0, 10).map((s) => (
                  <Badge key={s.name} variant="outline" className="text-[10px]">
                    {s.name}
                  </Badge>
                ))}
                {candidate.skills.length > 10 && (
                  <span className="text-xs text-ink-faint">+{candidate.skills.length - 10} 项</span>
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[360px_1fr] gap-4">
        {/* 左栏：Top-N 推荐列表 */}
        <Card className="h-fit">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center justify-between">
              <span>Top-N 推荐</span>
              <span className="text-xs font-normal text-ink-faint">{recommendations.length} 个岗位</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {recommendations.map((rec) => {
              const isSelected = selectedPosition?.position_id === rec.position_id
              const scoreColor =
                rec.total_score >= 0.8
                  ? 'text-state-emerging'
                  : rec.total_score >= 0.6
                    ? 'text-state-stable'
                    : rec.total_score >= 0.4
                      ? 'text-state-declining'
                      : 'text-state-archived'
              return (
                <button
                  key={rec.position_id}
                  onClick={() => handleSelectPosition(rec)}
                  className={`w-full text-left rounded-md border p-3 transition-colors ${
                    isSelected
                      ? 'border-ink bg-subtle'
                      : 'border-border hover:bg-subtle hover:border-border-strong'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <span className="text-sm font-medium text-ink truncate">{rec.position_name}</span>
                    <span className={`text-sm font-mono font-semibold tabular-nums ${scoreColor}`}>
                      {(rec.total_score * 100).toFixed(0)}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 mb-1.5">
                    <Badge variant="outline" className={`text-[10px] ${STATUS_CLASS[rec.status]}`}>
                      {STATUS_LABEL[rec.status]}
                    </Badge>
                    <span className="text-[10px] text-ink-faint font-mono">{rec.position_id}</span>
                  </div>
                  <p className="text-xs text-ink-muted line-clamp-2">{rec.summary}</p>
                  {/* 三维分数 mini bar */}
                  <div className="flex items-center gap-1.5 mt-2">
                    {[
                      { label: '必', val: rec.must_score, color: 'bg-ink' },
                      { label: '加', val: rec.nice_score, color: 'bg-ink-secondary' },
                      { label: '经', val: rec.exp_score, color: 'bg-ink-faint' },
                    ].map((d) => (
                      <div key={d.label} className="flex items-center gap-1 flex-1">
                        <span className="text-[9px] text-ink-faint">{d.label}</span>
                        <div className="flex-1 h-1 rounded-full bg-subtle overflow-hidden">
                          <div
                            className={`h-full rounded-full ${d.color}`}
                            style={{ width: `${d.val * 100}%` }}
                          />
                        </div>
                      </div>
                    ))}
                  </div>
                </button>
              )
            })}
          </CardContent>
        </Card>

        {/* 右栏：人岗比对详情 */}
        <div className="space-y-4">
          {!selectedPosition && (
            <Card className="border-dashed">
              <CardContent className="flex flex-col items-center justify-center py-20 text-center">
                <ArrowRight className="size-6 text-ink-faint mb-2 rotate-180" />
                <p className="text-sm text-ink-muted">从左侧推荐列表选择岗位查看详细比对</p>
                <p className="text-xs text-ink-faint mt-1">含环形图 / 雷达图 / 热力图 / 差距分析</p>
              </CardContent>
            </Card>
          )}

          {selectedPosition && loadingDetail && (
            <Card className="border-dashed">
              <CardContent className="flex flex-col items-center justify-center py-20 text-center">
                <div className="size-8 rounded-full border-2 border-ink border-t-transparent animate-spin mb-3" />
                <p className="text-sm text-ink-muted">加载比对详情…</p>
              </CardContent>
            </Card>
          )}

          {selectedPosition && matchResult && (
            <>
              {/* 总分 + 三维 + 摘要 */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base flex items-center gap-2">
                    <span>{matchResult.position_name}</span>
                    <Badge variant="outline" className={`text-[10px] ${STATUS_CLASS[selectedPosition.status]}`}>
                      {STATUS_LABEL[selectedPosition.status]}
                    </Badge>
                    <span className="text-xs font-mono text-ink-faint ml-auto">{matchResult.position_id}</span>
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="grid grid-cols-1 md:grid-cols-[200px_1fr] gap-4 items-center">
                    <ScoreRing score={matchResult.total_score} />
                    <div className="space-y-2">
                      {[
                        { label: '必备技能', score: matchResult.must_score, weight: '0.6' },
                        { label: '加分技能', score: matchResult.nice_score, weight: '0.2' },
                        { label: '工作经验', score: matchResult.exp_score, weight: '0.2' },
                      ].map((d) => (
                        <div key={d.label} className="flex items-center gap-2">
                          <span className="text-xs text-ink-muted w-20">{d.label}</span>
                          <div className="flex-1 h-2 rounded-full bg-subtle overflow-hidden">
                            <div
                              className="h-full rounded-full bg-ink"
                              style={{ width: `${d.score * 100}%` }}
                            />
                          </div>
                          <span className="text-xs font-mono text-ink tabular-nums w-12 text-right">
                            {(d.score * 100).toFixed(0)}
                          </span>
                          <span className="text-[10px] text-ink-faint w-8">w={d.weight}</span>
                        </div>
                      ))}
                      <p className="text-xs text-ink-muted pt-2 border-t border-border">
                        {matchResult.summary}
                      </p>
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* 雷达图 + 热力图 */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">五维能力对比</CardTitle>
                    <CardDescription>候选人 vs 岗位要求（三维真实 + 学历/项目占位）</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <RadarChart data={matchResult.radar} />
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">技能矩阵</CardTitle>
                    <CardDescription>必备技能命中情况（真实 matched/missing_must）</CardDescription>
                  </CardHeader>
                  <CardContent>
                    {matchResult.skill_matrix.length > 0 ? (
                      <SkillHeatmap data={matchResult.skill_matrix} />
                    ) : (
                      <p className="text-xs text-ink-faint py-10 text-center">该岗位无必备技能数据</p>
                    )}
                  </CardContent>
                </Card>
              </div>

              {/* 差距分析 */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-sm flex items-center gap-2">
                    <AlertCircle className="size-4 text-state-declining" />
                    差距分析
                    <Badge variant="outline" className="text-[10px] ml-auto">
                      {matchResult.gaps.length} 项
                    </Badge>
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {matchResult.gaps.length === 0 ? (
                    <p className="text-xs text-ink-faint py-6 text-center">
                      无必备技能缺口，岗位要求全部满足
                    </p>
                  ) : (
                    <div className="space-y-2">
                      {matchResult.gaps.map((gap, i) => {
                        const Icon =
                          gap.gap_type === 'missing_must'
                            ? XCircle
                            : gap.gap_type === 'level_gap'
                              ? AlertCircle
                              : CheckCircle2
                        const iconColor =
                          gap.gap_type === 'missing_must'
                            ? 'text-state-archived'
                            : gap.gap_type === 'level_gap'
                              ? 'text-state-declining'
                              : 'text-ink-faint'
                        return (
                          <div
                            key={i}
                            className="flex items-center gap-3 rounded-md border border-border p-2.5"
                          >
                            <Icon className={`size-4 shrink-0 ${iconColor}`} />
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-medium text-ink">{gap.skill}</span>
                                <Badge variant="outline" className="text-[10px]">
                                  {GAP_TYPE_LABEL[gap.gap_type]}
                                </Badge>
                              </div>
                              <p className="text-xs text-ink-muted mt-0.5">
                                当前: {gap.current_level} → 要求: {gap.required_level}
                              </p>
                            </div>
                            <Badge
                              variant="outline"
                              className={`text-[10px] ${PRIORITY_CLASS[gap.priority]}`}
                            >
                              {PRIORITY_LABEL[gap.priority]}优
                            </Badge>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* 学习路径甘特图（后端 M4 交付 → 空态） */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">学习路径规划</CardTitle>
                  <CardDescription>基于课程图谱的补足路径 · 后端待交付（M4）</CardDescription>
                </CardHeader>
                <CardContent>
                  {matchResult.learning_path.length > 0 ? (
                    <GanttChart data={matchResult.learning_path} />
                  ) : (
                    <p className="text-xs text-ink-faint py-10 text-center">
                      学习路径由课程图谱生成，等待后端交付（M4）
                    </p>
                  )}
                </CardContent>
              </Card>

              {/* 证据引用（后端 M4 交付 → 空态） */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-sm flex items-center gap-2">
                    <ExternalLink className="size-4" />
                    证据引用
                  </CardTitle>
                  <CardDescription>技能断言可追溯至原始 JD / 论文 / 社区信号</CardDescription>
                </CardHeader>
                <CardContent>
                  {matchResult.evidence_refs.length > 0 ? (
                    <div className="space-y-1.5">
                      {matchResult.evidence_refs.map((ev, i) => (
                        <div
                          key={i}
                          className="flex items-center gap-3 text-xs rounded-md border border-border p-2"
                        >
                          <span className="font-medium text-ink w-24 truncate">{ev.skill}</span>
                          <Badge variant="outline" className="text-[10px]">{ev.source}</Badge>
                          <span className="text-ink-muted font-mono">
                            置信度 {(ev.confidence * 100).toFixed(0)}%
                          </span>
                          <a
                            href={ev.url}
                            target="_blank"
                            rel="noreferrer"
                            className="ml-auto text-ink-muted hover:text-ink underline"
                          >
                            查看原文
                          </a>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-ink-faint py-10 text-center">
                      证据追溯依赖 Evidence 链路，等待后端交付（M4）
                    </p>
                  )}
                </CardContent>
              </Card>
            </>
          )}
        </div>
      </div>
    </>
  )
}
