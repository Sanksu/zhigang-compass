import { useEffect, useMemo, useRef, useState, type ReactElement } from 'react'
import { ArrowRight, CheckCircle2, AlertCircle, XCircle, ExternalLink, RotateCcw, FileText, ThumbsUp, ThumbsDown, RefreshCw, Sparkles, ChevronDown } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ResumeUploader } from '@/components/resume/resume-uploader'
import { ScoreRing, RadarChart, SkillHeatmap } from '@/components/match/charts'
import { AiThinkingCard } from '@/components/match/ai-thinking-card'
import { CitationBadge } from '@/components/ui/citation-badge'
import { LearningTimeline } from '@/components/learning/learning-timeline'
import { useTypewriter } from '@/hooks/use-typewriter'
import {
  type BackendDiagnosisReport,
  type BackendGapItem,
  type BackendLearningPathItem,
  type BackendMatchResult,
  type CandidateProfile,
  type GapItem,
  type MatchResult,
  type RadarDimension,
  type RecommendItem,
  type ResumeSummary,
  type SkillMatrixItem,
} from '@/components/match/types'
import type { PositionStatus } from '@/components/graph/types'
import {apiGet, apiPost, getAccessToken, errMsg} from '@/lib/api'
import { prefersReducedMotion } from '@/lib/utils'
import type { components } from '@/types/api'

const STATUS_LABEL: Record<PositionStatus | 'low', string> = {
  active: '活跃',
  candidate: '候选',
  emerging: '新兴',
  stable: '稳定',
  declining: '衰退',
  archived: '归档',
  low: '待提升',
}

const STATUS_CLASS: Record<PositionStatus | 'low', string> = {
  active: 'border-state-active/30 text-state-active bg-state-active/10',
  candidate: 'border-state-candidate/30 text-state-candidate bg-state-candidate/10',
  emerging: 'border-state-emerging/30 text-state-emerging bg-state-emerging/10',
  stable: 'border-state-stable/30 text-state-stable bg-state-stable/10',
  declining: 'border-state-declining/30 text-state-declining bg-state-declining/10',
  archived: 'border-state-archived/30 text-state-archived bg-state-archived/10',
  low: 'border-ink-faint/30 text-ink-muted bg-subtle',
}

const REL_LABEL = { gap: '缺口', fit: '达标', surplus: '超出' } as const
function relLabel(rel: keyof typeof REL_LABEL): string {
  return REL_LABEL[rel]
}

const GAP_TYPE_LABEL = {
  missing_must: '必备缺失',
  level_gap: '熟练度差距',
  missing_nice: '加分项缺失',
  matched: '已具备',
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

function toGapItem(g: BackendGapItem): GapItem {
  // 后端三态（missing/weak/matched）→ 前端四态；matched 是已匹配而非缺失
  const gap_type =
    g.gap_type === 'matched'
      ? 'matched'
      : g.gap_type === 'weak'
        ? 'level_gap'
        : g.necessity === 'must'
          ? 'missing_must'
          : 'missing_nice'
  return {
    skill: g.skill,
    gap_type,
    priority: g.priority,
    current_level: g.current_proficiency ?? '未掌握',
    required_level: g.required_proficiency ?? '不限',
    is_soft: g.is_soft,
    // 契约 #341 后装字段透传（后端回填后自动覆盖 mock 兜底）
    demand: g.demand,
    trend: g.trend,
    roi: g.roi,
    high_roi: g.high_roi,
    evidence: g.evidence?.map((e) => ({ role: e.role, text: e.text })),
  }
}

/** 后端 estimated_hours（学时）→ 甘特图天数：每天 8 学时折算，起点按前序累计 */
function toLearningPath(items: BackendLearningPathItem[]): MatchResult['learning_path'] {
  let offset = 0
  return items.map((item) => {
    const duration_days = Math.max(1, Math.ceil(item.estimated_hours / 8))
    const start_offset = offset
    offset += duration_days
    return {
      skill: item.skill,
      duration_days,
      start_offset,
      prerequisites: item.prerequisites,
      courses: item.courses.map((c) => ({
        title: c.title,
        platform: c.platform,
        hours: c.hours ?? 0,
        url: c.source_url ?? undefined,
      })),
      priority: item.priority,
      // 契约 #341 后装字段透传（后端回填后覆盖前端 mock 兜底）
      status: item.status,
      demand: item.demand,
      trend: item.trend,
      roi: item.roi,
    }
  })
}

function toMatchResult(r: BackendMatchResult): MatchResult {
  const skill_matrix: SkillMatrixItem[] = [
    ...r.matched_must.map((s) => ({
      skill: s, candidate_level: 1, required_level: 2, necessity: 'must' as const, match: 'full' as const,
    })),
    ...r.missing_must.map((s) => ({
      skill: s, candidate_level: 0, required_level: 2, necessity: 'must' as const, match: 'missing' as const,
    })),
  ]
  const gaps: GapItem[] = (r.gaps ?? []).map(toGapItem)
  // 五维雷达全量消费后端 radar（08-14 审查：此前学历/项目硬编码占位；education/projects
  // 为保守近似分，无数据维度剔除不占位；must 无必备门槛时同为 null 剔除，A1 口径）
  const radar = r.radar
  const radarDims: RadarDimension[] = []
  const mustVal = radar?.must ?? r.must_score
  if (mustVal != null) {
    radarDims.push({ name: '必备技能', candidate: Math.round(mustVal * 100), required: 100 })
  }
  radarDims.push(
    { name: '加分技能', candidate: Math.round((radar?.nice ?? r.nice_score) * 100), required: 80 },
    { name: '工作经验', candidate: Math.round((radar?.experience ?? r.exp_score) * 100), required: 85 },
  )
  if (radar?.education != null) {
    radarDims.push({ name: '学历背景', candidate: Math.round(radar.education * 100), required: 100 })
  }
  if (radar?.projects != null) {
    radarDims.push({ name: '项目经验', candidate: Math.round(radar.projects * 100), required: 100 })
  }
  return {
    position_id: r.position_id,
    position_name: r.position_name,
    total_score: r.total_score,
    must_score: r.must_score,
    nice_score: r.nice_score,
    exp_score: r.exp_score,
    summary: r.summary,
    radar: radarDims,
    skill_matrix: skill_matrix,
    gaps,
    learning_path: toLearningPath(r.learning_path ?? []),
    // P1 领域跨簇黑名单拦截状态透传（compare / 结果快照共用）
    learning_path_blocked: r.learning_path_blocked ?? false,
    learning_path_block_reason: r.learning_path_block_reason ?? null,
    // 证据引用：技能 → 原始 JD（图谱 MENTIONED_IN 链路，后端 compare 返回）
    evidence_refs: r.evidence_refs ?? [],
  }
}

// ── 差距分析数据升级（task T3）：双轨对齐用熟练度数值 ──

const PROF_NUM: Record<string, number> = {
  专家: 4,
  高级: 3,
  熟练: 3,
  掌握: 2,
  中级: 2,
  初级: 1,
  了解: 1,
  未掌握: 0,
}
/** 熟练度文本 → 0-4 数值（双轨对齐用；无要求/无法识别回退 fallback） */
function profNum(text: string | undefined, fallback: number): number {
  const s = (text ?? '').trim()
  if (!s || s === '不限') return fallback
  return PROF_NUM[s] ?? 2
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
 * 推荐 → POST /match/recommend；比对 → POST /match/compare
 * （含差距三态 + 学习路径 + 证据引用）。
 */
export function ResumeMatchPage() {
  const [stage, setStage] = useState<'upload' | 'parsing' | 'matched'>('upload')
  const [candidate, setCandidate] = useState<CandidateProfile | null>(null)
  const [recommendations, setRecommendations] = useState<RecommendItem[]>([])
  const [selectedPosition, setSelectedPosition] = useState<RecommendItem | null>(null)
  const [matchResult, setMatchResult] = useState<MatchResult | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  // 结果快照 ID（compare 返回，供 /match/result|gap|path|diagnosis|feedback 查询）
  const [matchId, setMatchId] = useState<string | null>(null)
  // AI 诊断报告（GET /match/result/{id}/diagnosis，LLM 生成 + Redis 缓存 24h）
  const [diagnosis, setDiagnosis] = useState<BackendDiagnosisReport | null>(null)
  const [diagnosisLoading, setDiagnosisLoading] = useState(false)
  // 总体匹配度解读打字机呈现（reduced-motion 直接整段渲染；新报告到达自动重播）
  const typedSummary = useTypewriter(diagnosis?.overall_summary ?? '', 60)
  // 动效偏好（挂载时判定一次，驱动打字机光标显隐）
  const reducedMotion = useMemo(() => prefersReducedMotion(), [])
  // 用户反馈（1=有用 / -1=没用，POST /match/feedback）
  const [feedback, setFeedback] = useState<number | null>(null)
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false)
  const [resumeList, setResumeList] = useState<ResumeSummary[]>([])
  const [activeResumeId, setActiveResumeId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // 差距展开溯源（task T3）：被展开的技能集
  const [expandedGaps, setExpandedGaps] = useState<Set<string>>(() => new Set())
  // 差距数据升级派生（task T3）. H4 修复:直接消费后端 gaps(删除 decorateGaps 前端
  // ROI 覆盖与 evidence 重建)。toGapItem 已透传契约 #341 后装字段 demand/trend/
  // roi/high_roi/evidence——前端 GAP_COST 常量公式与后端成本口径(base_hours×熟练度
  // 缺口)不一致,曾推翻后端 high_roi 标记。后端字段缺失时已由后端回填,前端不再 mock 兜底。
  const gapRows = useMemo(() => (matchResult ? matchResult.gaps : []), [matchResult])

  // 载入已解析简历列表（后端 /resume/list）
  function loadResumeList() {
    return apiGet<components['schemas']['ResumeListData']>('/resume/list')
      .then((res) => setResumeList(res.items))
      .catch(() => {
        /* 列表加载失败不阻塞页面 */
      })
  }

  useEffect(() => {
    loadResumeList()
  }, [])

  // 上传简历 → 真实异步解析 + SSE 进度推送（GET /resume/task/{id}/stream）
  async function handleFileSelected(file: File) {
    setNotice(null)
    setStage('parsing')
    try {
      const form = new FormData()
      form.append('file', file)
      // 不手动设 Content-Type：axios 对 FormData 自动生成含 boundary 的 multipart 头，
      // 手动覆盖会丢失 boundary 导致后端解析失败
      const res = await apiPost<components['schemas']['ResumeParseTaskData']>(
        '/resume/parse',
        form,
      )
      if (res.cached) {
        await loadRecommend(res.resume_id)
        return
      }
      // 新简历解析任务已入队 → 订阅 SSE 进度流直至 done/error
      await streamParseProgress(res.task_id, file.name)
    } catch (e) {
      setStage('upload')
      setNotice(errMsg(e, '上传失败，请检查后端服务'))
    }
  }

  // SSE 订阅解析任务进度（event: progress/done/error）
  async function streamParseProgress(taskId: string, fileName: string) {
    const ctrl = new AbortController()
    // 30s 无终态事件则中止（与后端 300s 兜底相比更保守，避免上传卡死）
    const timer = setTimeout(() => ctrl.abort(), 30_000)
    try {
      // 端点要求 user+ 认证，fetch 不走 axios 拦截器，需手动携带 Bearer
      const token = getAccessToken()
      const resp = await fetch(`/api/v1/resume/task/${taskId}/stream`, {
        signal: ctrl.signal,
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (!resp.ok || !resp.body) throw new Error('SSE 连接失败')
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        // 按 SSE 帧分隔解析 event/data
        const frames = buffer.split('\n\n')
        buffer = frames.pop() ?? ''
        for (const frame of frames) {
          const event = frame.match(/^event:\s*(.+)$/m)?.[1]
          const data = frame.match(/^data:\s*(.+)$/m)?.[1]
          if (event === 'done' && data) {
            // done 事件 data 为完整任务载荷，result.resume_id 为新解析简历画像 ID；
            // 刷新已有简历列表并直接触发推荐，避免用户整页刷新后才可发起匹配
            let resumeId: string | null = null
            try {
              resumeId = JSON.parse(data)?.result?.resume_id ?? null
            } catch {
              /* SSE data 非 JSON，退化为仅提示 */
            }
            if (resumeId) {
              // 先等简历列表刷新完成，再触发推荐——否则 loadRecommend 里
              // resumeList.find 取不到刚解析的简历，候选摘要缺失
              await loadResumeList()
              await loadRecommend(resumeId)
              return
            }
            setNotice(`解析完成（${fileName}），已写入简历库，可直接发起匹配。`)
            setStage('upload')
            return
          }
          if (event === 'error' && data) {
            const msg = (() => {
              try {
                return JSON.parse(data).message ?? '解析失败'
              } catch {
                return '解析失败'
              }
            })()
            setNotice(`解析失败：${msg}`)
            setStage('upload')
            return
          }
        }
      }
    } catch {
      // 超时/中断：不阻塞页面，提示可稍后从"已有简历"载入
      setNotice(`解析任务已提交（${fileName}），进度推送中断。稍后可从下方"已有简历"载入。`)
    } finally {
      clearTimeout(timer)
    }
    setStage('upload')
  }

  // 载入已有简历 → 真实推荐（§2.4.4 契约：202 + task_id 异步，轮询后再取结果）
  async function loadRecommend(resumeId: string) {
    const summary = resumeList.find((r) => r.id === resumeId)
    if (summary) setCandidate(toCandidate(summary))
    setActiveResumeId(resumeId)
    setStage('parsing')
    setNotice('推荐计算中，请稍候…')
    try {
      const submitted = await apiPost<components['schemas']['RecommendTaskResult']>('/match/recommend', {
        resume_id: resumeId,
        top_n: 10,
      })
      const task = await pollMatchTask(submitted.task_id)
      if (task.status !== 'success' || !task.match_id) {
        throw new Error(task.error || '推荐失败，请稍后重试')
      }
      const result = await apiGet<components['schemas']['MatchResultList']>(`/match/result/${task.match_id}`)
      const items = result.items.map(toRecommendItem)
      setRecommendations(items)
      setStage('matched')
      setSelectedPosition(null)
      setMatchResult(null)
      setNotice(null)
    } catch (e) {
      setStage('upload')
      setNotice(errMsg(e, '推荐失败，请检查后端服务'))
    }
  }

  // 轮询中止标志（08-14 审查：路由切走后停止轮询，避免卸载后 setState/请求浪费）
  const pollCancelledRef = useRef(false)
  useEffect(() => () => { pollCancelledRef.current = true }, [])

  // 轮询推荐任务状态：pending/running 等待，success/failed 结束，超时抛错
  async function pollMatchTask(
    taskId: string,
    maxWaitMs = 90_000,
  ): Promise<{ status: string; match_id?: string; error?: string }> {
    const deadline = Date.now() + maxWaitMs
    for (;;) {
      if (pollCancelledRef.current) throw new Error('已离开页面，推荐轮询中止')
      const task = await apiGet<components['schemas']['MatchTaskStatus']>(
        `/match/task/${taskId}`,
      )
      if (task.status === 'success' || task.status === 'failed') return task
      if (Date.now() > deadline) throw new Error('推荐计算超时，请稍后从"已有简历"重试')
      await new Promise((r) => setTimeout(r, 1500))
    }
  }

  // 选中推荐岗位 → 真实人岗比对
  async function handleSelectPosition(rec: RecommendItem) {
    if (!activeResumeId) return
    setSelectedPosition(rec)
    setLoadingDetail(true)
    setMatchResult(null)
    setMatchId(null)
    setDiagnosis(null)
    setFeedback(null)
    try {
      const res = await apiPost<BackendMatchResult>('/match/compare', {
        resume_id: activeResumeId,
        position_id: rec.position_id,
      })
      setMatchId(res.match_id ?? null)
      setMatchResult(toMatchResult(res))
    } catch (e) {
      setNotice(errMsg(e, '比对失败'))
    } finally {
      setLoadingDetail(false)
    }
  }

  // 从结果快照重新加载：先校验任务状态（GET /match/task/{id}），再拉取快照（GET /match/result/{id}）。
  // 相比重新跑 compare，快照在 Redis（TTL 24h）中读取，秒级返回且不重复计算。
  async function reloadFromSnapshot() {
    if (!matchId) return
    setNotice(null)
    try {
      const task = await apiGet<components['schemas']['MatchTaskStatus']>(`/match/task/${matchId}`)
      if (task.status !== 'success') {
        setNotice('匹配任务尚未完成，请稍后重试')
        return
      }
      const res = await apiGet<BackendMatchResult>(`/match/result/${matchId}`)
      if (res.position_id) {
        setMatchResult(toMatchResult(res))
        setFeedback(null)
        setNotice('已从结果快照刷新（无需重新计算）')
      }
    } catch (e) {
      setNotice(errMsg(e, '快照刷新失败（结果可能已过期，请重新比对）'))
    }
  }

  // 差距分析独立刷新（GET /match/result/{id}/gap）
  async function refreshGaps() {
    if (!matchId) return
    try {
      const res = await apiGet<components['schemas']['MatchGapData']>(`/match/result/${matchId}/gap`)
      setMatchResult((prev) => (prev ? { ...prev, gaps: res.gaps.map(toGapItem) } : prev))
      setNotice('差距分析已从快照刷新')
    } catch (e) {
      setNotice(errMsg(e, '差距刷新失败'))
    }
  }

  // 学习路径独立刷新（GET /match/result/{id}/path）
  async function refreshPath() {
    if (!matchId) return
    try {
      const res = await apiGet<components['schemas']['MatchPathData']>(`/match/result/${matchId}/path`)
      setMatchResult((prev) =>
        prev
          ? {
              ...prev,
              learning_path: toLearningPath(res.learning_path),
              learning_path_blocked: res.learning_path_blocked ?? false,
              learning_path_block_reason: res.learning_path_block_reason ?? null,
            }
          : prev,
      )
      setNotice('学习路径已从快照刷新')
    } catch (e) {
      setNotice(errMsg(e, '学习路径刷新失败'))
    }
  }

  // 创建异步诊断任务，完成后从兼容 GET 端点读取报告。
  async function loadDiagnosis() {
    if (!matchId || diagnosisLoading) return
    setDiagnosisLoading(true)
    setNotice(null)
    try {
      const created = await apiPost<components['schemas']['DiagnosisTaskResponse']>(
        `/match/result/${matchId}/diagnosis`,
      )
      if (created.report) {
        setDiagnosis(created.report)
        return
      }
      if (!created.task_id) throw new Error('诊断任务创建失败')
      const task = await pollMatchTask(created.task_id, 120_000)
      if (task.status === 'failed') throw new Error(task.error || '诊断任务执行失败')
      const report = await apiGet<BackendDiagnosisReport>(
        `/match/result/${matchId}/diagnosis`,
      )
      setDiagnosis(report)
    } catch (e) {
      setDiagnosis(null)
      setNotice(errMsg(e, '诊断报告生成失败，请稍后重试'))
    } finally {
      setDiagnosisLoading(false)
    }
  }

  // 提交匹配反馈（POST /match/feedback，match_id 校验 + Redis 记录 90 天）
  async function submitFeedback(score: 1 | -1) {
    if (!matchId || feedbackSubmitting) return
    setFeedbackSubmitting(true)
    setNotice(null)
    try {
      await apiPost('/match/feedback', { match_id: matchId, score })
      setFeedback(score)
      setNotice(`已记录反馈（${score === 1 ? '👍 匹配结果有用' : '👎 匹配结果不准确'}）`)
    } catch (e) {
      setNotice(errMsg(e, '反馈提交失败'))
    } finally {
      setFeedbackSubmitting(false)
    }
  }

  function handleReset() {
    setStage('upload')
    setCandidate(null)
    setRecommendations([])
    setSelectedPosition(null)
    setMatchResult(null)
    setMatchId(null)
    setDiagnosis(null)
    setFeedback(null)
    setActiveResumeId(null)
    setNotice(null)
    setExpandedGaps(new Set())
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
                  {/* 三维分数 mini bar（无必备门槛岗位 must=null 显示空条） */}
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
                            style={{ width: `${(d.val ?? 0) * 100}%` }}
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
              <CardContent>
                <AiThinkingCard
                  stages={['正在运行人岗匹配引擎…', '正在计算三维得分与技能矩阵…', '正在生成比对详情…']}
                  rows={4}
                />
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
                  {/* 结果快照 ID + 反馈 + 快照重载（POST /match/feedback / GET /match/task|result） */}
                  <div className="flex flex-wrap items-center gap-2 pt-1">
                    {matchId && (
                      <span className="text-[10px] font-mono text-ink-faint" title="结果快照 ID（Redis 24h）">
                        #{matchId.slice(0, 8)}
                      </span>
                    )}
                    <div className="flex items-center gap-1 ml-auto">
                      <Button
                        size="sm"
                        variant="outline"
                        className={`h-7 px-2 text-xs ${feedback === 1 ? 'border-state-stable text-state-stable' : ''}`}
                        disabled={feedbackSubmitting || feedback !== null}
                        title="匹配结果有用"
                        onClick={() => submitFeedback(1)}
                      >
                        <ThumbsUp className="size-3.5 mr-1" />有用
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className={`h-7 px-2 text-xs ${feedback === -1 ? 'border-state-archived text-state-archived' : ''}`}
                        disabled={feedbackSubmitting || feedback !== null}
                        title="匹配结果不准确"
                        onClick={() => submitFeedback(-1)}
                      >
                        <ThumbsDown className="size-3.5 mr-1" />没用
                      </Button>
                      <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={reloadFromSnapshot}>
                        <RefreshCw className="size-3.5 mr-1" />重载
                      </Button>
                    </div>
                  </div>
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
                              style={{ width: `${(d.score ?? 0) * 100}%` }}
                            />
                          </div>
                          <span className="text-xs font-mono text-ink tabular-nums w-12 text-right">
                            {d.score == null ? '—' : (d.score * 100).toFixed(0)}
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
                    <CardDescription>候选人 vs 岗位要求（后端雷达评分，无数据维度不展示：education/projects 缺数据、must 无必备门槛）</CardDescription>
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
                    <Badge variant="outline" className="text-[10px]">
                      {matchResult.gaps.length} 项
                    </Badge>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-6 px-1.5 text-[10px] ml-auto"
                      onClick={refreshGaps}
                      title="从结果快照刷新差距分析"
                    >
                      <RefreshCw className="size-3 mr-1" />刷新
                    </Button>
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {gapRows.length === 0 ? (
                    <p className="text-xs text-ink-faint py-6 text-center">
                      无必备技能缺口，岗位要求全部满足
                    </p>
                  ) : (
                    <div className="space-y-2">
                      {gapRows.map((gap) => {
                        const expanded = expandedGaps.has(gap.skill)
                        // 双轨对齐：目标(岗位要求) vs 现状(候选人) 的 0-4 熟练度
                        const target = Math.max(0, Math.min(4, profNum(gap.required_level, 3)))
                        const actual = Math.max(0, Math.min(4, profNum(gap.current_level, 0)))
                        const rel = actual < target ? 'gap' : actual === target ? 'fit' : 'surplus'
                        const GAP_ICON =
                          gap.gap_type === 'missing_must' ? XCircle : gap.gap_type === 'level_gap' ? AlertCircle : CheckCircle2
                        const relClass =
                          rel === 'gap' ? 'text-state-archived' : rel === 'surplus' ? 'text-state-emerging' : 'text-state-stable'
                        const relBar =
                          rel === 'gap' ? 'bg-state-archived' : rel === 'surplus' ? 'bg-state-emerging' : 'bg-state-stable'
                        const iconColor =
                          gap.gap_type === 'missing_must'
                            ? 'text-state-archived'
                            : gap.gap_type === 'level_gap'
                              ? 'text-state-declining'
                              : 'text-state-stable'
                        return (
                          <div
                            key={gap.skill}
                            className={`rounded-md border p-2.5 ${expanded ? 'border-border-strong' : 'border-border'}`}
                          >
                            {/* 头部：技能 + 类型/ROI 徽标 + 对齐状态（点击展开溯源） */}
                            <button
                              type="button"
                              onClick={() =>
                                setExpandedGaps((prev) => {
                                  const n = new Set(prev)
                                  if (n.has(gap.skill)) n.delete(gap.skill)
                                  else n.add(gap.skill)
                                  return n
                                })
                              }
                              className="flex w-full items-center gap-3 text-left"
                            >
                              <GAP_ICON className={`size-4 shrink-0 ${iconColor}`} />
                              <div className="min-w-0 flex-1">
                                <div className="flex flex-wrap items-center gap-1.5">
                                  <span className="text-sm font-medium text-ink">{gap.skill}</span>
                                  <Badge variant="outline" className="text-[10px]">
                                    {GAP_TYPE_LABEL[gap.gap_type]}
                                  </Badge>
                                  {gap.is_soft && (
                                    <span className="inline-flex items-center rounded-full border border-[#ec4899]/40 bg-[#ec4899]/5 px-1.5 py-0 text-[10px] font-medium text-[#ec4899] dark:text-[#f472b6]">
                                      软技能
                                    </span>
                                  )}
                                  {gap.high_roi && (
                                    <span className="inline-flex items-center gap-1 rounded-full border border-state-emerging/40 bg-state-emerging/10 px-1.5 py-0 text-[10px] font-medium text-state-emerging">
                                      <Sparkles className="size-3" />核心突破点 · 高 ROI
                                    </span>
                                  )}
                                  <span className={`ml-auto text-[9px] font-mono ${relClass}`}>{relLabel(rel)}</span>
                                </div>
                                {/* 双轨对比基线（task T3）：上轨=目标期望基线，下轨=实际掌握度 */}
                                {/* 上轨：期望达到段（0→target 淡色承托）+ 目标刻度；下轨：现状填充，溢出段高亮 */}
                                <div className="mt-2 space-y-1.5">
                                  <div className="flex items-center gap-2">
                                    <span className="w-8 shrink-0 text-right text-[9px] text-ink-faint">要求</span>
                                    <div className="relative h-2 flex-1 rounded-full bg-border/40">
                                      <div
                                        className={`absolute inset-y-0 left-0 rounded-l-full ${rel === 'gap' ? 'bg-state-archived/20' : 'bg-state-stable/20'}`}
                                        style={{ width: `${(target / 4) * 100}%` }}
                                      />
                                      <div
                                        className="absolute inset-y-[-3px] w-[3px] rounded bg-ink/70"
                                        style={{ left: `${(target / 4) * 100}%` }}
                                        title={`岗位要求 ${target}/4`}
                                      />
                                    </div>
                                    <span className="w-6 shrink-0 text-[9px] font-mono text-ink-muted tabular-nums">
                                      {target}/4
                                    </span>
                                  </div>
                                  <div className="flex items-center gap-2">
                                    <span className="w-8 shrink-0 text-right text-[9px] text-ink-faint">现状</span>
                                    <div className="relative h-2 flex-1 overflow-hidden rounded-full bg-border/40">
                                      <div
                                        className={`absolute inset-y-0 left-0 rounded-full ${relBar}`}
                                        style={{ width: `${(actual / 4) * 100}%` }}
                                      />
                                      {/* 能力溢出段（surplus）：目标刻度后绿色延伸 */}
                                      {rel === 'surplus' && (
                                        <div
                                          className="absolute inset-y-0 rounded-r-full bg-state-emerging"
                                          style={{
                                            left: `${(target / 4) * 100}%`,
                                            width: `${((actual - target) / 4) * 100}%`,
                                          }}
                                        />
                                      )}
                                    </div>
                                    <span className="w-6 shrink-0 text-[9px] font-mono text-ink-muted tabular-nums">
                                      {actual}/4
                                    </span>
                                  </div>
                                  <span className="flex items-center gap-1 text-[9px] text-ink-faint">
                                    <span className={`font-mono ${relClass}`}>{relLabel(rel)}</span>
                                    <ChevronDown
                                      className={`size-3.5 transition-transform ${expanded ? 'rotate-180' : ''}`}
                                    />
                                  </span>
                                </div>
                              </div>
                            </button>
                            {/* 展开：ROI 明细 + 证据溯源（task T3） */}
                            {expanded && (
                              <div className="mt-2 space-y-2 border-t border-border pt-2">
                                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-muted">
                                  <span>
                                    需求 <b className="text-ink">{Math.round((gap.demand ?? 0) * 100)}</b>
                                  </span>
                                  <span>
                                    趋势{' '}
                                    <b className={((gap.trend ?? 0) >= 0 ? 'text-state-emerging' : 'text-state-archived')}>
                                      {(gap.trend ?? 0) >= 0 ? '↑' : '↓'} {Math.round(Math.abs(gap.trend ?? 0) * 100)}
                                    </b>
                                  </span>
                                  <span>
                                    ROI <b className="text-ink">{((gap.roi ?? 0)).toFixed(2)}</b>
                                  </span>
                                </div>
                                {/* 数据溯源（task T3）：一条 JD 要求 ↔ 对应简历特征，逐条成对展示，打破算法黑盒 */}
                                {(() => {
                                  // 按 role 交替配对：每条 JD 要求对应同位置的简历特征
                                  const jd = (gap.evidence ?? []).filter((e) => e.role === 'jd')
                                  const resume = (gap.evidence ?? []).filter((e) => e.role === 'resume')
                                  const rows = Math.max(1, Math.max(jd.length, resume.length))
                                  const pairs: ReactElement[] = []
                                  for (let k = 0; k < rows; k++) {
                                    const j = jd[k]?.text ?? '—'
                                    const r = resume[k]?.text ?? '未标注/缺失'
                                    pairs.push(
                                      <div
                                        key={k}
                                        className="grid grid-cols-2 gap-2 rounded-md border border-border/60 p-2"
                                      >
                                        <div>
                                          <div className="mb-0.5 flex items-center gap-1 text-[9px] font-medium text-state-archived">
                                            <FileText className="size-3" />JD 要求原文
                                          </div>
                                          <p className="text-[10px] leading-relaxed text-ink-secondary">{j}</p>
                                        </div>
                                        <div>
                                          <div className="mb-0.5 flex items-center gap-1 text-[9px] font-medium text-state-stable">
                                            <CheckCircle2 className="size-3" />简历提取特征
                                          </div>
                                          <p className="text-[10px] leading-relaxed text-ink-secondary">{r}</p>
                                        </div>
                                      </div>,
                                    )
                                  }
                                  return <div className="space-y-1.5">{pairs}</div>
                                })()}
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* 学习路径规划（双轨制：先修拓扑分层 → 阶段时间轴；已匹配技能标记已掌握） */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm flex items-center gap-2">
                    <span>学习路径规划</span>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-6 px-1.5 text-[10px] ml-auto"
                      onClick={refreshPath}
                      title="从结果快照刷新学习路径"
                    >
                      <RefreshCw className="size-3 mr-1" />刷新
                    </Button>
                  </CardTitle>
                  <CardDescription>按先修关系分阶段的学习时间轴（学时 / 推荐课程 / 前往学习）</CardDescription>
                </CardHeader>
                <CardContent>
                  {matchResult.learning_path_blocked ? (
                    // P1 演示：领域跨簇语义黑名单拦截（跨域诱导组合拒绝生成）
                    <div className="flex items-start gap-3 rounded-md border border-state-archived/40 bg-state-archived/5 px-4 py-3">
                      <AlertCircle className="size-4 mt-0.5 shrink-0 text-state-archived" />
                      <div className="text-xs text-ink-strong leading-relaxed">
                        <p className="font-medium mb-1">学习路径已拒绝生成（领域语义黑名单拦截）</p>
                        <p className="text-ink-faint">{matchResult.learning_path_block_reason ?? '检测到跨领域诱导组合，已停止规划。'}</p>
                      </div>
                    </div>
                  ) : matchResult.learning_path.length > 0 ? (
                    <LearningTimeline
                      items={matchResult.learning_path}
                      completedSkills={matchResult.skill_matrix
                        .filter((s) => s.match === 'full')
                        .map((s) => s.skill)}
                      onGoToLearn={(task) => {
                        // 接入课程：跳转该技能首门推荐课程（新标签）
                        const url = task.courses?.find((c) => c.url)?.url
                        if (url) window.open(url, '_blank', 'noreferrer')
                        else setNotice(`「${task.skill}」暂无推荐课程，可先补充相关课程后再学习`)
                      }}
                    />
                  ) : (
                    <p className="text-xs text-ink-faint py-10 text-center">
                      无需要补足的技能差距，岗位要求已全部满足
                    </p>
                  )}
                </CardContent>
              </Card>

              {/* AI 诊断报告（GET /match/result/{id}/diagnosis，LLM 生成 + Redis 缓存 24h） */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-sm flex items-center gap-2">
                    <Sparkles className="size-4 text-state-emerging" />
                    AI 诊断报告
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-6 px-1.5 text-[10px] ml-auto"
                      onClick={loadDiagnosis}
                      disabled={diagnosisLoading}
                      title="生成/刷新诊断报告（LLM 生成，结果缓存 24h）"
                    >
                      <RefreshCw className="size-3 mr-1" />
                      {diagnosis ? '刷新' : '生成'}
                    </Button>
                  </CardTitle>
                  <CardDescription>基于匹配结果 + 差距 + 学习路径的 LLM 结构化诊断（§9.5）</CardDescription>
                </CardHeader>
                <CardContent>
                  {diagnosisLoading ? (
                    <AiThinkingCard
                      stages={[
                        'AI 正在汇总匹配结果与关键差距…',
                        'AI 正在生成岗位能力诊断报告…',
                        'AI 正在撰写改进建议与路径解读…',
                      ]}
                      rows={4}
                      hint="LLM 推理约需 1 分钟，多通道自动切换，结果缓存 24h"
                    />
                  ) : diagnosis ? (
                    <div className="space-y-4">
                      {/* 总体匹配度解读（打字机呈现；reduced-motion 直接整段） */}
                      <div>
                        <h4 className="text-xs font-medium text-ink mb-1">总体匹配度解读</h4>
                        <p className="text-sm text-ink-muted leading-relaxed">
                          {typedSummary}
                          {!reducedMotion && typedSummary.length < (diagnosis.overall_summary?.length ?? 0) && (
                            <span className="ml-0.5 inline-block h-3.5 w-[2px] animate-pulse bg-ink/70 align-text-bottom" />
                          )}
                        </p>
                      </div>
                      {/* 三维雷达图解读 */}
                      <div>
                        <h4 className="text-xs font-medium text-ink mb-1">三维雷达图解读</h4>
                        <p className="text-sm text-ink-muted leading-relaxed">{diagnosis.radar_analysis}</p>
                      </div>
                      {/* 关键差距 Top-5（含证据追溯） */}
                      {diagnosis.top_gaps.length > 0 && (
                        <div>
                          <h4 className="text-xs font-medium text-ink mb-2">关键差距与改进建议</h4>
                          <div className="space-y-2">
                            {diagnosis.top_gaps.map((g, i) => (
                              <div key={i} className="rounded-md border border-border p-2.5">
                                <div className="flex items-center gap-2 mb-1">
                                  <span className="text-sm font-medium text-ink">{g.skill}</span>
                                  {g.evidence_id ? (
                                    g.evidence_id.startsWith('http') ? (
                                      <a
                                        href={g.evidence_id}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="ml-auto flex items-center gap-0.5 text-[10px] text-ink-muted hover:text-ink underline"
                                      >
                                        <ExternalLink className="size-3" />证据追溯
                                      </a>
                                    ) : (
                                      <Badge variant="outline" className="ml-auto text-[10px] font-mono" title="证据引用">
                                        {g.evidence_id}
                                      </Badge>
                                    )
                                  ) : (
                                    <Badge variant="outline" className="ml-auto text-[10px] text-ink-faint" title="无对应证据">
                                      无证据
                                    </Badge>
                                  )}
                                </div>
                                <p className="text-xs text-ink-muted leading-relaxed">{g.advice}</p>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                      {/* 学习路径解读 */}
                      {diagnosis.path_analysis && (
                        <div>
                          <h4 className="text-xs font-medium text-ink mb-1">学习路径解读</h4>
                          <p className="text-sm text-ink-muted leading-relaxed">{diagnosis.path_analysis}</p>
                        </div>
                      )}
                      {/* 整体改进建议 */}
                      {diagnosis.recommendations.length > 0 && (
                        <div>
                          <h4 className="text-xs font-medium text-ink mb-2">整体改进建议</h4>
                          <ul className="space-y-1.5">
                            {diagnosis.recommendations.map((rec, i) => (
                              <li key={i} className="flex items-start gap-2 text-sm text-ink-muted leading-relaxed">
                                <CheckCircle2 className="size-3.5 mt-0.5 shrink-0 text-state-stable" />
                                {rec}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="flex flex-col items-center justify-center py-8 text-center">
                      <Sparkles className="size-5 text-ink-faint mb-2" />
                      <p className="text-xs text-ink-muted">
                        生成一份由 LLM 撰写的结构化诊断报告，覆盖总体匹配度、差距建议与学习路径解读
                      </p>
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* 证据引用（技能 → 原始 JD，图谱 EVIDENCED_BY 链路） */}
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
                          className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs rounded-md border border-border p-2"
                        >
                          <span className="font-medium text-ink w-24 truncate">{ev.skill}</span>
                          <CitationBadge
                            source={ev.source}
                            confidence={ev.confidence}
                            url={ev.url}
                            title={`技能「${ev.skill}」溯源至 ${ev.source}`}
                          />
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-ink-faint py-10 text-center">
                      该岗位技能未关联可追溯的原始 JD 证据
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
