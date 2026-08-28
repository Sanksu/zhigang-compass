/**
 * 演化时间轴（视觉评审 P0-2，2026-08-22）——图谱页底部的版本快照滑轨。
 *
 * 数据面：GET /evolution/versions（快照版本列表）+ GET /evolution/diff?from&to
 * （相邻版本节点增删集），全部为既有后端契约，无后端改动。
 *
 * 交互模型（打标不剔除，与图谱筛选同一范式）：滑到某版本 → 该版新增节点
 * 绿环高亮、消亡节点橙虚线标记（仅画布上存在的节点可标记），滑轨下方
 * 常显增删摘要条（新增/消亡名称 chips）——节点不在画布上时故事仍完整。
 * 播放键自动步进（1.5s/版）复刻演化历程；数据缺失/接口失败时整条静默隐藏。
 *
 * 状态范式：marks 为渲染期派生值（index + 已拉取 diff 缓存），effect 体内
 * 仅发起请求、setState 一律走异步回调（react-hooks/set-state-in-effect 严规）。
 */
/* eslint-disable react-refresh/only-export-components -- 导出播放速率常量供测试断言，HMR 粒度降级可接受 */
import { useEffect, useMemo, useState } from 'react'
import { Pause, Play } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { apiGet } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { components } from '@/types/api'

type EvolutionVersion = components['schemas']['EvolutionVersion']
type EvolutionDiff = components['schemas']['EvolutionDiff']

/** 画布打标与摘要所需的演化标记（graph-2d 消费 addedIds/removedIds） */
export interface EvolutionMarks {
  versionId: string
  dateLabel: string
  summary: string
  addedIds: Set<string>
  removedIds: Set<string>
  addedNames: string[]
  removedNames: string[]
}

/** 版本列表按创建时间升序（滑轨从旧到新），缺时间的异常版本排尾 */
export function sortVersionsAsc(items: EvolutionVersion[]): EvolutionVersion[] {
  return [...items].sort((a, b) => {
    const ta = a.created_at ?? ''
    const tb = b.created_at ?? ''
    if (!ta && tb) return 1
    if (ta && !tb) return -1
    return ta.localeCompare(tb)
  })
}

/** 相邻版本 diff → 演化标记（纯函数，单测覆盖） */
export function toEvolutionMarks(
  version: EvolutionVersion,
  diff: EvolutionDiff,
): EvolutionMarks {
  const date = version.created_at ? new Date(version.created_at) : null
  return {
    versionId: version.version_id,
    dateLabel: date
      ? `${date.getMonth() + 1}/${date.getDate()}`
      : version.version_id.slice(0, 8),
    summary: version.change_summary,
    addedIds: new Set(diff.nodes_added.map((n) => n.id)),
    removedIds: new Set(diff.nodes_removed.map((n) => n.id)),
    addedNames: diff.nodes_added.map((n) => n.name),
    removedNames: diff.nodes_removed.map((n) => n.name),
  }
}

/** diff 拉取失败的兜底（空集 = 该版无标记，摘要条仍显示版本说明） */
const EMPTY_DIFF: EvolutionDiff = {
  nodes_added: [],
  nodes_removed: [],
  nodes_changed: [],
  edges_added: [],
  edges_removed: [],
}

/** 摘要 chips 上限（超出折叠为 +N） */
const CHIP_LIMIT = 6
/** 播放步进间隔（ms）——比常规交互慢，便于观看（演示视频口径） */
const PLAY_STEP_MS = 1500

export function EvolutionTimeline({
  onMarksChange,
  className,
}: {
  onMarksChange: (marks: EvolutionMarks | null) => void
  className?: string
}) {
  const [versions, setVersions] = useState<EvolutionVersion[] | null>(null)
  /** 版本对 diff 缓存（仅经异步回调写入） */
  const [diffs, setDiffs] = useState<Record<string, EvolutionDiff>>({})
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(false)

  // 版本列表（失败静默隐藏整条时间轴；访客可浏览图谱页——401 静默降级，
  // 不触发全局登出跳转，与 dashboard 同端点 skipAuthRedirect 模式一致）
  useEffect(() => {
    let cancelled = false
    apiGet<components['schemas']['EvolutionVersionListData']>(
      '/evolution/versions?page=1&size=30',
      { skipAuthRedirect: true },
    )
      .then((r) => {
        if (!cancelled) setVersions(sortVersionsAsc(r.items))
      })
      .catch(() => {
        if (!cancelled) setVersions([])
      })
    return () => {
      cancelled = true
    }
  }, [])

  // 当前停靠的相邻版本对（index=0 为起点，无 diff）
  const from = versions && index > 0 ? versions[index - 1] : null
  const to = versions && index > 0 ? versions[index] : null
  const pairKey = from && to ? `${from.version_id}|${to.version_id}` : null

  // 缓存的 diff 才驱动打标；缺缓存时拉取（结果仅异步写入）
  useEffect(() => {
    if (!pairKey || !from || !to || diffs[pairKey]) return
    let cancelled = false
    apiGet<EvolutionDiff>(
      `/evolution/diff?from=${encodeURIComponent(from.version_id)}&to=${encodeURIComponent(to.version_id)}`,
      { skipAuthRedirect: true },
    )
      .then((d) => {
        if (!cancelled) setDiffs((prev) => ({ ...prev, [pairKey]: d }))
      })
      .catch(() => {
        if (!cancelled) setDiffs((prev) => ({ ...prev, [pairKey]: EMPTY_DIFF }))
      })
    return () => {
      cancelled = true
    }
  }, [pairKey, from, to, diffs])

  // 演化标记为派生值：起点/未拉到 diff → null（无标记）
  const marks = useMemo(
    () =>
      pairKey && from && to && diffs[pairKey]
        ? toEvolutionMarks(to, diffs[pairKey])
        : null,
    [pairKey, from, to, diffs],
  )

  // 同步给父级（graph-2d 画布打标）——外部系统同步
  useEffect(() => {
    onMarksChange(marks)
  }, [marks, onMarksChange])

  // 播放：有效播放态派生（到末版自动视为停），步进仅在定时回调里 setState
  const effectivePlaying = playing && versions != null && index < versions.length - 1
  useEffect(() => {
    if (!effectivePlaying) return
    const timer = window.setTimeout(() => setIndex((i) => i + 1), PLAY_STEP_MS)
    return () => window.clearTimeout(timer)
  }, [effectivePlaying, index])

  if (!versions || versions.length < 2) return null

  const current = versions[index]
  const chips = (names: string[]) => {
    const shown = names.slice(0, CHIP_LIMIT)
    return { shown, rest: names.length - shown.length }
  }
  const added = marks ? chips(marks.addedNames) : null
  const removed = marks ? chips(marks.removedNames) : null

  return (
    <div
      className={cn(
        'rounded-lg border border-border bg-canvas px-3 py-2.5 shadow-sm',
        className,
      )}
      data-testid="evolution-timeline"
    >
      <div className="flex items-center gap-3">
        <Button
          size="sm"
          variant="outline"
          onClick={() => setPlaying((p) => !p)}
          disabled={index >= versions.length - 1}
          className="h-7 shrink-0 px-2 text-xs"
          title={playing ? '暂停自动演示' : '自动播放演化历程（1.5s/版）'}
        >
          {effectivePlaying ? <Pause className="size-3" /> : <Play className="size-3" />}
          {effectivePlaying ? '暂停' : '播放'}
        </Button>
        <input
          type="range"
          min={0}
          max={versions.length - 1}
          step={1}
          value={index}
          aria-label="演化版本时间轴"
          onChange={(e) => {
            setPlaying(false)
            setIndex(Number(e.target.value))
          }}
          className="h-1.5 flex-1 accent-primary"
        />
        <span className="shrink-0 font-mono text-[12px] tabular-nums text-ink-muted">
          {index}/{versions.length - 1}
        </span>
        <span className="hidden shrink-0 text-[12px] text-ink-secondary sm:inline">
          {marks ? `版本 ${marks.dateLabel}` : '起点（无变更标记）'}
        </span>
      </div>
      {/* 增删摘要：版本变更一句话 + 新增/消亡 chips（画布外节点也能讲完故事） */}
      {marks && (
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px]">
          <span className="text-ink-muted" title={current?.change_summary}>
            {marks.summary || current?.change_summary || '版本快照'}
          </span>
          {added && added.shown.length > 0 && (
            <span className="flex flex-wrap items-center gap-1">
              <span className="shrink-0 font-medium text-state-emerging">
                新增 {marks.addedNames.length}
              </span>
              {added.shown.map((n) => (
                <span
                  key={`a-${n}`}
                  className="rounded-full border border-state-emerging/40 bg-state-emerging/10 px-1.5 py-0 text-[11px] text-ink-secondary"
                >
                  {n}
                </span>
              ))}
              {added.rest > 0 && (
                <span className="text-[11px] text-ink-faint">+{added.rest}</span>
              )}
            </span>
          )}
          {removed && removed.shown.length > 0 && (
            <span className="flex flex-wrap items-center gap-1">
              <span className="shrink-0 font-medium text-state-declining">
                消亡 {marks.removedNames.length}
              </span>
              {removed.shown.map((n) => (
                <span
                  key={`r-${n}`}
                  className="rounded-full border border-state-declining/40 bg-state-declining/10 px-1.5 py-0 text-[11px] text-ink-secondary"
                >
                  {n}
                </span>
              ))}
              {removed.rest > 0 && (
                <span className="text-[11px] text-ink-faint">+{removed.rest}</span>
              )}
            </span>
          )}
        </div>
      )}
    </div>
  )
}
