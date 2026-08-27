/** 演化视图组件（从 evolution-page.tsx 抽出，第六轮审查拆分：页面 ≤800 行惯例）。 */
import { useEffect, useState } from 'react'
import { Eye, Boxes, TrendingUp } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { PaginationBar } from '@/components/ui/pagination'
import { apiGet, errMsg } from '@/lib/api'
import type { components } from '@/types/api'
import type { PositionEvolutionData, PositionEvolutionListData, SkillEvolutionData, SkillEvolutionListData } from './types'
import { SnapshotTimelineView } from './timeline'

// ===== TechnologyWatchView =====

/** 技术热点观察池（真实 GET /evolution/watch，MLI 产业化拐点排名） */
type WatchItem = components['schemas']['WatchOverviewItem']

const SOURCE_LABEL: Record<string, string> = {
  jd: 'JD',
  arxiv: '论文',
  course: '课程',
  github: 'GitHub',
  community: '社区',
  stackoverflow: 'SO',
}

/** 可搜索下拉（08-16：岗位/技能/版本全量可搜索选择）
 *
 * options 为当前可选项；输入时先本地过滤，若提供 onSearch 则由父组件
 * 防抖拉取后端匹配（positions/skills 走 q 参数；versions 仅本地过滤）。
 */
export function TechnologyWatchView() {
  const [data, setData] = useState<WatchItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  // 08-16 翻页：观察池 10 项一页（GET /evolution/watch page/size）
  const PAGE_SIZE = 10
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [pageLoading, setPageLoading] = useState(false)

  // 请求共享：loadPage 与初始 effect 复用同一 fetch（08-17 收敛重复请求）
  function fetchWatchPage(p: number) {
    return apiGet<components['schemas']['WatchOverviewData']>(`/evolution/watch?page=${p}&size=${PAGE_SIZE}`)
  }

  function loadPage(p: number) {
    setPageLoading(true)
    fetchWatchPage(p)
      .then((r) => {
        setData(r.items)
        setTotal(r.total)
      })
      .catch((e) => setError(errMsg(e, '技术热点加载失败')))
      .finally(() => setPageLoading(false))
  }

  // 初始加载：setState 均在请求回调（异步）中，规避 effect 同步 setState 规则
  useEffect(() => {
    let cancelled = false
    fetchWatchPage(1)
      .then((r) => {
        if (cancelled) return
        setData(r.items)
        setTotal(r.total)
      })
      .catch((e) => {
        if (!cancelled) setError(errMsg(e, '技术热点加载失败'))
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (error) {
    return (
      <Card className="mb-4">
        <CardContent className="py-8 text-center text-xs text-state-archived">{error}</CardContent>
      </Card>
    )
  }

  return (
    <Card className="mb-4">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Eye className="size-4 text-ink" />
          <span>技术热点观察池</span>
          <span className="text-[10px] font-normal text-ink-faint">MLI 产业化拐点 · 设计文档 §7.2.5</span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {data === null ? (
          <p className="py-6 text-center text-xs text-ink-faint">加载观察池…</p>
        ) : data.length === 0 ? (
          <p className="py-6 text-center text-xs text-ink-faint">暂无技术热点信号（依赖每日观察池任务）</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>技能</TableHead>
                <TableHead>MLI 指数</TableHead>
                <TableHead>信号来源</TableHead>
                <TableHead>产业化</TableHead>
                <TableHead>状态</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((w) => (
                <TableRow key={w.skill_name}>
                  <TableCell className="font-medium text-ink">{w.skill_name}</TableCell>
                  <TableCell className="font-mono tabular-nums text-ink-muted">
                    {w.mli.toFixed(2)}
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {w.sources.map((s) => (
                        <Badge key={s} variant="outline" className="text-[10px] font-mono">
                          {SOURCE_LABEL[s] ?? s}
                        </Badge>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell>
                    {w.ready_to_industrialize ? (
                      <Badge className="text-[10px] bg-state-emerging">可产业化</Badge>
                    ) : (
                      <Badge variant="outline" className="text-[10px]">观察中</Badge>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant={w.status === 'candidate_promoted' ? 'emerging' : 'outline'} className="text-[10px]">
                      {w.status === 'candidate_promoted' ? '候选提升' : w.status === 'archived' ? '归档' : '观察'}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {/* 观察池翻页（10 项一页，08-16） */}
        {data && data.length > 0 && (
          <PaginationBar
            page={page}
            total={total}
            pageSize={PAGE_SIZE}
            loading={pageLoading}
            onPageChange={(p) => {
              setPage(p)
              loadPage(p)
            }}
          />
        )}
      </CardContent>
    </Card>
  )
}

// ===== 技能频次趋势 / 岗位演化历史（SnapshotTimelineView 配置） =====

export function SkillTrendView() {
  return (
    <SnapshotTimelineView<SkillEvolutionData>
      icon={TrendingUp}
      title="技能频次趋势 · 最近 90 天"
      selectPlaceholder="选择技能"
      idPlaceholder="技能节点 ID（sk_xxxx）"
      idErrorMsg="请输入技能节点 ID（如 sk_xxxx）"
      defaultErrorMsg="默认技能加载失败"
      loadErrorMsg="趋势查询失败"
      loadingMsg="加载默认技能趋势…"
      noDataMsg="暂无技能快照数据（版本数据不足），可输入技能节点 ID 查询"
      emptyMsg="该技能在各版本快照中无关联边"
      listUrl="/evolution/skills?page=1&size=50"
      searchUrl={(q) => `/evolution/skills?page=1&size=50&q=${encodeURIComponent(q)}`}
      detailUrl={(id) => `/evolution/trends?skill=${encodeURIComponent(id)}&window=90`}
      idOf={(d) => d.skill_id}
      nameOf={(d) => (d as { skill_name?: string; skill?: string }).skill_name ?? (d as { skill?: string }).skill ?? d.skill_id}
      extractList={(r) => (r as SkillEvolutionListData).skills}
      extractPoints={(d) => d.points ?? []}
      freqLabel="关联岗位数"
    />
  )
}

export function PositionEvolutionView() {
  return (
    <SnapshotTimelineView<PositionEvolutionData>
      icon={Boxes}
      title="岗位演化历史"
      subtitle="各版本快照中的存在性与关联技能边数"
      selectPlaceholder="选择岗位"
      idPlaceholder="岗位节点 ID（pos_xxxx）"
      idErrorMsg="请输入岗位节点 ID（如 pos_xxxx）"
      defaultErrorMsg="默认岗位加载失败"
      loadErrorMsg="演化历史查询失败"
      loadingMsg="加载默认岗位演化…"
      noDataMsg="暂无岗位快照数据（版本数据不足），可输入岗位节点 ID 查询"
      emptyMsg="该岗位在各版本快照中均未出现"
      listUrl="/evolution/positions?page=1&size=50"
      searchUrl={(q) => `/evolution/positions?page=1&size=50&q=${encodeURIComponent(q)}`}
      detailUrl={(id) => `/evolution/position/${encodeURIComponent(id)}/evolution`}
      idOf={(d) => d.position_id}
      nameOf={(d) => d.position_name}
      extractList={(r) => (r as PositionEvolutionListData).positions}
      extractPoints={(d) => d.points ?? []}
      freqLabel="关联技能边数"
      extraColumns={(_d, p) => (
        <TableCell className="text-right">
          {p.present ? (
            <Badge variant="outline" className="text-xs text-state-stable">存在</Badge>
          ) : (
            <Badge variant="outline" className="text-xs text-ink-faint">未收录</Badge>
          )}
        </TableCell>
      )}
    />
  )
}

