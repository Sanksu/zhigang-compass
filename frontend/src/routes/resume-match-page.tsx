import { useState } from 'react'
import { ArrowRight, CheckCircle2, AlertCircle, XCircle, ExternalLink, RotateCcw } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ResumeUploader } from '@/components/resume/resume-uploader'
import { ScoreRing, RadarChart, SkillHeatmap, GanttChart } from '@/components/match/charts'
import {
  MOCK_CANDIDATE,
  MOCK_RECOMMENDATIONS,
  getMockMatchResult,
  type MatchResult,
  type RecommendItem,
} from '@/components/match/types'
import type { PositionStatus } from '@/components/graph/types'

const STATUS_LABEL: Record<PositionStatus, string> = {
  candidate: '候选',
  emerging: '新兴',
  stable: '稳定',
  declining: '衰退',
  archived: '归档',
}

const STATUS_CLASS: Record<PositionStatus, string> = {
  candidate: 'border-state-candidate/30 text-state-candidate bg-state-candidate/10',
  emerging: 'border-state-emerging/30 text-state-emerging bg-state-emerging/10',
  stable: 'border-state-stable/30 text-state-stable bg-state-stable/10',
  declining: 'border-state-declining/30 text-state-declining bg-state-declining/10',
  archived: 'border-state-archived/30 text-state-archived bg-state-archived/10',
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

/**
 * 简历匹配页 — 设计文档 §10.4
 *
 * 流程：上传简历 → LLM 解析 → 三维加权匹配 → Top-N 推荐 → 人岗比对报告
 * 后端 API（M4 交付）：POST /api/v1/resume/parse + POST /api/v1/match/recommend
 *
 * 当前阶段（M4 前端提前启动）：
 * - 上传后 mock 2s 解析延迟，展示候选人画像 + Top-N 推荐
 * - 点击推荐卡片 → 展开人岗比对报告（4 种可视化 + 差距 + 学习路径）
 */
export function ResumeMatchPage() {
  const [stage, setStage] = useState<'upload' | 'parsing' | 'matched'>('upload')
  const [selectedPosition, setSelectedPosition] = useState<RecommendItem | null>(null)
  const [matchResult, setMatchResult] = useState<MatchResult | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)

  // mock 上传 + 解析 + 推荐
  function handleUpload() {
    setStage('parsing')
    setTimeout(() => {
      setStage('matched')
    }, 2000)
  }

  // 选中推荐岗位 → 加载比对详情
  function handleSelectPosition(rec: RecommendItem) {
    setSelectedPosition(rec)
    setLoadingDetail(true)
    setMatchResult(null)
    // mock 1.5s 加载
    setTimeout(() => {
      setMatchResult(getMockMatchResult(rec.position_id))
      setLoadingDetail(false)
    }, 1500)
  }

  function handleReset() {
    setStage('upload')
    setSelectedPosition(null)
    setMatchResult(null)
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
                onFileSelected={handleUpload}
              />
            </CardContent>
          </Card>

          <Card className="border-dashed">
            <CardContent className="flex flex-col items-center justify-center py-16 text-center">
              {stage === 'parsing' ? (
                <>
                  <div className="size-10 rounded-full border-2 border-ink border-t-transparent animate-spin mb-4" />
                  <p className="text-sm text-ink-muted">LLM 解析中…</p>
                  <p className="text-xs text-ink-faint font-mono mt-2">POST /api/v1/resume/parse</p>
                </>
              ) : (
                <>
                  <p className="text-sm text-ink-muted">推荐结果待简历上传后显示</p>
                  <p className="text-xs text-ink-faint font-mono mt-2">
                    POST /api/v1/match/recommend
                  </p>
                </>
              )}
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
            重新上传
          </Button>
        }
      />

      {/* 候选人画像摘要 */}
      <Card className="mb-4">
        <CardContent className="py-4">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
            <span className="font-medium text-ink">{MOCK_CANDIDATE.name}</span>
            <span className="text-ink-muted">
              {MOCK_CANDIDATE.total_years} 年经验 · {MOCK_CANDIDATE.education} · {MOCK_CANDIDATE.school_tier}
            </span>
            <div className="flex items-center gap-1.5 flex-wrap">
              {MOCK_CANDIDATE.skills.slice(0, 6).map((s) => (
                <Badge key={s.name} variant="outline" className="text-[10px]">
                  {s.name} · {s.level}
                </Badge>
              ))}
              <span className="text-xs text-ink-faint">+{MOCK_CANDIDATE.skills.length - 6} 项</span>
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-[360px_1fr] gap-4">
        {/* 左栏：Top-N 推荐列表 */}
        <Card className="h-fit">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center justify-between">
              <span>Top-N 推荐</span>
              <span className="text-xs font-normal text-ink-faint">5 个岗位</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {MOCK_RECOMMENDATIONS.map((rec) => {
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
                <p className="text-xs text-ink-faint mt-1">含环形图 / 雷达图 / 热力图 / 甘特图 4 种可视化</p>
              </CardContent>
            </Card>
          )}

          {selectedPosition && loadingDetail && (
            <Card className="border-dashed">
              <CardContent className="flex flex-col items-center justify-center py-20 text-center">
                <div className="size-8 rounded-full border-2 border-ink border-t-transparent animate-spin mb-3" />
                <p className="text-sm text-ink-muted">加载比对详情…</p>
                <p className="text-xs text-ink-faint font-mono mt-1">POST /api/v1/match/compare</p>
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
                        {selectedPosition.summary}
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
                    <CardDescription>候选人 vs 岗位要求</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <RadarChart data={matchResult.radar} />
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">技能矩阵</CardTitle>
                    <CardDescription>熟练度热力图（未掌握 → 精通）</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <SkillHeatmap data={matchResult.skill_matrix} />
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
                </CardContent>
              </Card>

              {/* 学习路径甘特图 */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">学习路径规划</CardTitle>
                  <CardDescription>
                    甘特图 · 总计 {matchResult.learning_path.reduce((s, p) => s + p.duration_days, 0)} 天 · 按优先级着色
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <GanttChart data={matchResult.learning_path} />
                  {/* 优先级图例 */}
                  <div className="flex items-center gap-4 mt-2 text-xs text-ink-muted">
                    <span className="flex items-center gap-1">
                      <span className="size-2 rounded-sm bg-state-archived" /> 高优先级
                    </span>
                    <span className="flex items-center gap-1">
                      <span className="size-2 rounded-sm bg-state-declining" /> 中优先级
                    </span>
                    <span className="flex items-center gap-1">
                      <span className="size-2 rounded-sm bg-ink-faint" /> 低优先级
                    </span>
                  </div>
                </CardContent>
              </Card>

              {/* 证据引用 */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-sm flex items-center gap-2">
                    <ExternalLink className="size-4" />
                    证据引用
                    <Badge variant="outline" className="text-[10px] ml-auto text-state-emerging border-state-emerging/30">
                      覆盖率 100%
                    </Badge>
                  </CardTitle>
                  <CardDescription>每条技能断言可追溯至原始 JD / 论文 / 社区信号</CardDescription>
                </CardHeader>
                <CardContent>
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
                </CardContent>
              </Card>
            </>
          )}
        </div>
      </div>
    </>
  )
}
