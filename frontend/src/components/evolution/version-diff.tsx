/** 演化视图组件（从 evolution-page.tsx 抽出，第六轮审查拆分：页面 ≤800 行惯例）。 */
/* eslint-disable react-refresh/only-export-components -- typeOf/diffToItems 为跨视图纯函数，HMR 粒度降级可接受 */
import { useEffect, useState } from 'react'
import { Eye, GitBranch } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { PaginationBar } from '@/components/ui/pagination'
import { apiGet, errMsg } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { components } from '@/types/api'
import type { EvolutionDiff, EvolutionDiffNode, EvolutionVersion, EvolutionVersionDetail, TrendTone, VersionDiffItem } from './types'
import { SearchableSelect } from './shared'

// ===== VersionDiffView =====

/** 从节点 ID 前缀推断类型（id_generator 约定 pos_/sk_/ev_/co_） */
export function typeOf(id: string): VersionDiffItem['type'] {
  if (id.startsWith('pos_')) return 'position'
  if (id.startsWith('ev_')) return 'evidence'
  if (id.startsWith('co_')) return 'course'
  return 'skill'
}

/** 节点类型：优先用后端快照 type（course/tool 等精确类型），未知时回退 id 前缀推断 */
function nodeTypeOf(n: EvolutionDiffNode): VersionDiffItem['type'] {
  if (n.type === 'position' || n.type === 'skill' || n.type === 'evidence' || n.type === 'course' || n.type === 'tool') {
    return n.type
  }
  return typeOf(n.id)
}

export function diffToItems(d: EvolutionDiff): {
  added: VersionDiffItem[]
  removed: VersionDiffItem[]
  changed: VersionDiffItem[]
} {
  const toItems = (
    list: EvolutionDiffNode[],
    change: VersionDiffItem['change'],
    detail: string,
  ): VersionDiffItem[] =>
    list.map((n) => ({ id: n.id, name: n.name, type: nodeTypeOf(n), change, detail }))
  return {
    added: toItems(d.nodes_added, 'added', '节点新增'),
    removed: toItems(d.nodes_removed, 'removed', '节点删除'),
    changed: toItems(d.nodes_changed, 'changed', '两版本共有（交集节点）'),
  }
}

export function VersionDiffView() {
  const [versions, setVersions] = useState<EvolutionVersion[]>([])
  const [v1, setV1] = useState<string>('')
  const [v2, setV2] = useState<string>('')
  // 08-16 用户决策：版本下拉全量可搜索（保留 90 天 ≈ ≤90 个版本，一次拉全，
  // 移除列表分页——原分页条在巨型 diff 表下方不可见且翻页后下拉选项不全）
  const [diff, setDiff] = useState<{
    v1: string
    v2: string
    data: { added: VersionDiffItem[]; removed: VersionDiffItem[]; changed: VersionDiffItem[] }
  } | null>(null)
  const [error, setError] = useState<string | null>(null)
  // 版本详情弹窗（GET /evolution/versions/{id}）
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailVersion, setDetailVersion] = useState('')
  const [detail, setDetail] = useState<EvolutionVersionDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)

  function loadDetail(id: string) {
    setDetailOpen(true)
    setDetailVersion(id)
    setDetail(null)
    setDetailError(null)
    setDetailLoading(true)
    apiGet<EvolutionVersionDetail>(`/evolution/versions/${encodeURIComponent(id)}`)
      .then(setDetail)
      .catch((e) => setDetailError(errMsg(e, '版本详情加载失败')))
      .finally(() => setDetailLoading(false))
  }

  // 加载全量版本列表（size=100，覆盖 90 天保留期），默认对比最近两个版本。
  // 初始加载不设 loading 态（effect 内同步 setState 违反 react-hooks/set-state-in-effect）
  useEffect(() => {
    let cancelled = false
    apiGet<components['schemas']['EvolutionVersionListData']>(`/evolution/versions?page=1&size=100`)
      .then((res) => {
        if (cancelled) return
        // 快照可能在同一事务写入导致 created_at 相同，按 version_id（graph_vYYYYMMDD）降序保证稳定
        const items = [...res.items].sort((a, b) => b.version_id.localeCompare(a.version_id))
        setVersions(items)
        // 默认对比最近两个版本（函数式 set 尊重已选值：effect 仅首载跑，
        // 但避免读取 v1/v2 闭包触发 exhaustive-deps 缺依赖告警）
        if (items.length >= 2) {
          setV1((prev) => prev || items[1].version_id)
          setV2((prev) => prev || items[0].version_id)
        } else if (items.length === 1) {
          setV1((prev) => prev || items[0].version_id)
        }
      })
      .catch(() => setError('版本列表加载失败'))
    return () => {
      cancelled = true
    }
  }, [])

  // 版本对变化 → 拉取真实 diff（setState 均在异步回调内）
  useEffect(() => {
    if (!v1 || !v2 || v1 === v2) return
    let cancelled = false
    apiGet<EvolutionDiff>(
      `/evolution/diff?from=${encodeURIComponent(v1)}&to=${encodeURIComponent(v2)}`,
    )
      .then((d) => {
        if (!cancelled) setDiff({ v1, v2, data: diffToItems(d) })
      })
      .catch(() => {
        if (!cancelled) setError('版本对比加载失败')
      })
    return () => {
      cancelled = true
    }
  }, [v1, v2])

  // 渲染派生：仅展示与当前版本对匹配的 diff；加载态 = 有版本对但结果未就绪
  const visibleDiff = diff && diff.v1 === v1 && diff.v2 === v2 ? diff.data : null
  const loading = Boolean(v1 && v2 && v1 !== v2 && !visibleDiff && !error)

  return (
    <>
      <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex flex-wrap items-center justify-between gap-3 text-sm">
          <span className="flex items-center gap-2">
            <GitBranch className="size-4" />
            <span>版本快照对比</span>
          </span>
          <div className="flex items-center gap-1.5">
            <SearchableSelect
              value={v1}
              placeholder="选择版本"
              options={versions.map((v) => ({ value: v.version_id, label: v.version_id }))}
              pageSize={10}
              onSelect={setV1}
            />
            <span className="text-xs text-ink-faint">vs</span>
            <SearchableSelect
              value={v2}
              placeholder="选择版本"
              options={versions.map((v) => ({ value: v.version_id, label: v.version_id }))}
              pageSize={10}
              onSelect={setV2}
            />
            <Button
              size="sm"
              variant="ghost"
              className="h-8 px-2 text-xs"
              disabled={versions.length === 0}
              onClick={() => loadDetail(v2 || versions[0]?.version_id)}
            >
              <Eye className="size-3.5 mr-1" />
              版本详情
            </Button>
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        {error && <div className="py-10 text-center text-xs text-state-archived">{error}</div>}
        {!error && versions.length === 0 && (
          <div className="py-10 text-center text-xs text-ink-muted">暂无图谱版本快照</div>
        )}
        {!error && versions.length > 0 && !v1 && !v2 && (
          <div className="py-10 text-center text-xs text-ink-muted">仅存在单个版本，无法对比</div>
        )}
        {loading && (
          <div className="py-10 text-center text-xs text-ink-muted">加载版本差异…</div>
        )}
        {!loading && visibleDiff && (
          <>
            <div className="grid grid-cols-3 gap-3 mb-4">
              <StatTile label="新增节点" count={visibleDiff.added.length} tone="emerging" />
              <StatTile label="删除节点" count={visibleDiff.removed.length} tone="declining" />
              <StatTile label="共有节点" count={visibleDiff.changed.length} tone="stable" />
            </div>
            <Tabs defaultValue="added">
              <TabsList>
                <TabsTrigger value="added" className="text-xs">新增 ({visibleDiff.added.length})</TabsTrigger>
                <TabsTrigger value="removed" className="text-xs">删除 ({visibleDiff.removed.length})</TabsTrigger>
                <TabsTrigger value="changed" className="text-xs">共有 ({visibleDiff.changed.length})</TabsTrigger>
              </TabsList>
              <TabsContent value="added">
                <PaginatedDiffTable key={`${v1}:${v2}`} items={visibleDiff.added} />
              </TabsContent>
              <TabsContent value="removed">
                <PaginatedDiffTable key={`${v1}:${v2}`} items={visibleDiff.removed} />
              </TabsContent>
              <TabsContent value="changed">
                <PaginatedDiffTable key={`${v1}:${v2}`} items={visibleDiff.changed} />
              </TabsContent>
            </Tabs>
          </>
        )}
        {!loading && !error && versions.length > 0 && v1 && v2 && v1 === v2 && (
          <div className="py-10 text-center text-xs text-ink-muted">请选择两个不同版本进行对比</div>
        )}
      </CardContent>
    </Card>

    {/* 版本详情弹窗（真实 GET /evolution/versions/{id}） */}
    <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>版本详情：{detailVersion}</DialogTitle>
          <DialogDescription>
            {detail?.created_at ?? '加载中…'} · 快照节点 {detail?.stats.nodes ?? '—'} · 边 {detail?.stats.edges ?? '—'}
          </DialogDescription>
        </DialogHeader>
        {detailLoading && (
          <div className="py-10 text-center text-xs text-ink-muted">加载版本详情…</div>
        )}
        {detailError && (
          <div className="py-10 text-center text-xs text-state-archived">{detailError}</div>
        )}
        {detail && (
          <div className="space-y-4">
            {/* 节点类型分布 */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {Object.entries(detail.stats.by_type ?? {}).map(([type, count]) => (
                <div key={type} className="rounded-md bg-subtle p-2 text-center">
                  <div className="text-lg font-semibold tabular-nums text-ink">{count}</div>
                  <div className="text-[10px] text-ink-muted">{type}</div>
                </div>
              ))}
            </div>
            {/* 变更摘要 */}
            {detail.change_summary && (
              <p className="rounded-md border border-border bg-subtle/40 p-2.5 text-xs text-ink-secondary leading-relaxed">
                {detail.change_summary}
              </p>
            )}
            {/* 节点列表（前 50 条，避免超载） */}
            <div className="max-h-72 overflow-y-auto rounded-md border border-border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[160px]">节点 ID</TableHead>
                    <TableHead>名称</TableHead>
                    <TableHead className="w-[70px]">类型</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {detail.nodes.slice(0, 50).map((n) => (
                    <TableRow key={n.id}>
                      <TableCell className="font-mono text-[10px] text-ink-muted">{n.id}</TableCell>
                      <TableCell className="text-xs font-medium text-ink">{n.name}</TableCell>
                      <TableCell className="text-[10px] text-ink-faint">{n.type}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {detail.nodes.length > 50 && (
                <p className="border-t border-border p-2 text-center text-[10px] text-ink-faint">
                  仅显示前 50 条，共 {detail.nodes.length} 个节点
                </p>
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
    </>
  )
}

function StatTile({ label, count, tone }: { label: string; count: number; tone: TrendTone }) {
  const color =
    tone === 'emerging'
      ? 'text-state-emerging'
      : tone === 'declining'
        ? 'text-state-declining'
        : 'text-state-stable'
  return (
    <div className="rounded-md border border-border p-3 bg-subtle/40">
      <div className={cn('text-2xl font-semibold tabular-nums', color)}>{count}</div>
      <div className="text-xs text-ink-muted mt-0.5">{label}</div>
    </div>
  )
}

function DiffTable({ items }: { items: VersionDiffItem[] }) {
  if (items.length === 0) {
    return <div className="py-6 text-center text-xs text-ink-faint">无数据</div>
  }
  const typeLabel: Record<VersionDiffItem['type'], string> = {
    position: '岗位',
    skill: '技能',
    evidence: '证据',
    course: '课程',
    tool: '工具',
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-[150px]">节点名</TableHead>
          <TableHead>名称</TableHead>
          <TableHead className="w-[60px]">类型</TableHead>
          <TableHead>变化说明</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((item) => (
          <TableRow key={item.id}>
            <TableCell className="font-mono text-xs text-ink-muted">{item.id}</TableCell>
            <TableCell className="font-medium text-ink">{item.name}</TableCell>
            <TableCell>
              <Badge variant="outline" className="text-[10px] font-mono">
                {typeLabel[item.type]}
              </Badge>
            </TableCell>
            <TableCell className="text-xs text-ink-muted">{item.detail}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/** 版本 diff 表格分页（10 项一页，08-16 用户决策：新增/删除/共有三标签翻页）。
 *
 * 切换版本对时通过 key 重挂载重置页码；切换标签页时 Radix Tabs 卸载
 * 非激活内容，页码同样归位。
 */
const DIFF_PAGE_SIZE = 10

function PaginatedDiffTable({ items }: { items: VersionDiffItem[] }) {
  const [page, setPage] = useState(1)
  const slice = items.slice((page - 1) * DIFF_PAGE_SIZE, page * DIFF_PAGE_SIZE)
  return (
    <>
      <DiffTable items={slice} />
      {items.length > DIFF_PAGE_SIZE && (
        <PaginationBar
          page={page}
          total={items.length}
          pageSize={DIFF_PAGE_SIZE}
          onPageChange={setPage}
        />
      )}
    </>
  )
}

/** 六态元信息（与图谱状态机一致 + rejected 终态） */
