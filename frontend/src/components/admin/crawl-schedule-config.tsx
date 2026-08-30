/**
 * 爬虫调度与限频配置（自包含组件）— 08-27 从 admin-settings-page 迁出：
 * 爬虫域配置一处管全，收敛到 admin-crawl「调度与限频」Tab。
 *
 * 数据源：GET/PUT /admin/runtime-config（仅提交爬虫相关字段，后端增量保留其他页配置）
 * - 单次采集上限 crawl_items_cap
 * - 各源请求限频 rate_limit
 * - 每爬虫开关/采集数量/空列表重试/独立触发时间 crawlers
 */
import { useEffect, useState } from 'react'
import { RotateCcw, Save, Settings2 } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { apiGet, apiPut, errMsg } from '@/lib/api'
import type { components } from '@/types/api'

type RuntimeConfig = components['schemas']['RuntimeConfig']
type RateLimitEntry = NonNullable<RuntimeConfig['rate_limit']>[string]
type CrawlerConfig = NonNullable<RuntimeConfig['crawlers']>[string]
type Feedback = { type: 'ok' | 'err'; text: string } | null

/** 限频输入草稿（字符串态）：允许清空与输入中间态，保存时按后端契约域清洗——
 * 修复此前 Number(value)||4/||1 兜底令用户无法清空、0 被静默改写的问题。 */
type RateLimitDraft = { req_per_min: string; delay_min: string; delay_max: string }

/** 整数字符串 → 契约域内整数；空/非整数/越界返回 null（调用方省略该字段，
 * 服务端增量合并保留旧值，与后端 _validate_rate_limit 口径一致） */
function toIntInRange(value: string, lo: number, hi: number): number | null {
  const n = Number(value)
  return value.trim() !== '' && Number.isInteger(n) && n >= lo && n <= hi ? n : null
}

/** 全部可配置爬虫（对齐 backend admin_routes/crawl.py PLATFORM_META + etl.py crawl_platforms）。
 *  前 6 源为 ETL 主管线源；CDP/课程源配独立触发时间由 crawl_scheduler 单独触发，否则仅供手动触发。 */
const CRAWLER_SPIDERS = [
  // ── ETL 主管线源 ──
  { name: 'zhilian', label: '智联招聘', note: '国内 · 支持数量上限' },
  { name: 'indeed', label: 'Indeed', note: '国际 · 数量上限未生效' },
  { name: 'glassdoor', label: 'Glassdoor', note: '国际 CDP · 数量上限未生效' },
  { name: 'arxiv', label: 'arXiv', note: '论文 · 支持数量上限' },
  { name: 'github', label: 'GitHub', note: '社区 · 数量上限未生效' },
  { name: 'stackoverflow', label: 'StackOverflow', note: '社区 · 数量上限未生效' },
  // ── CDP 招聘源（需浏览器登录态 9222；配独立时间才自动采集）──
  { name: 'boss', label: 'BOSS直聘', note: 'CDP 登录态 · 独立时间触发' },
  { name: 'monster', label: 'Monster', note: 'CDP 登录态 · 独立时间触发' },
  { name: 'maimai', label: '脉脉', note: 'CDP 登录态 · 独立时间触发' },
  { name: 'linkedin_public', label: 'LinkedIn', note: 'CDP 登录态 · 独立时间触发' },
  // ── 课程源（经 load_courses 消费；配独立时间才自动采集）──
  { name: 'icourse163', label: '中国大学MOOC', note: '课程 · 独立时间触发' },
  { name: 'coursera', label: 'Coursera', note: '课程 · 独立时间触发' },
  { name: 'edx', label: 'edX', note: '课程 · 独立时间触发' },
] as const

/** 已保存配置 → 限频输入草稿（load 与保存成功回填共用） */
function draftsFromConfig(cfg: RuntimeConfig): Record<string, RateLimitDraft> {
  const out: Record<string, RateLimitDraft> = {}
  for (const [source, v] of Object.entries(cfg.rate_limit ?? {})) {
    out[source] = {
      req_per_min: v.req_per_min != null ? String(v.req_per_min) : '',
      delay_min: v.delay_range?.[0] != null ? String(v.delay_range[0]) : '',
      delay_max: v.delay_range?.[1] != null ? String(v.delay_range[1]) : '',
    }
  }
  return out
}

export function CrawlScheduleConfig() {
  const [scalars, setScalars] = useState<{ crawl_items_cap: string }>({ crawl_items_cap: '100' })
  const [rateDraft, setRateDraft] = useState<Record<string, RateLimitDraft>>({})
  const [crawlers, setCrawlers] = useState<Record<string, CrawlerConfig>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [feedback, setFeedback] = useState<Feedback>(null)

  useEffect(() => {
    apiGet<RuntimeConfig>('/admin/runtime-config')
      .then((cfg) => {
        setScalars({ crawl_items_cap: String(cfg.crawl_items_cap ?? 100) })
        setRateDraft(draftsFromConfig(cfg))
        const saved = cfg.crawlers ?? {}
        const init: Record<string, CrawlerConfig> = {}
        for (const s of CRAWLER_SPIDERS) {
          init[s.name] = {
            enabled: saved[s.name]?.enabled ?? true,
            max_results: saved[s.name]?.max_results,
            max_empty_retries: saved[s.name]?.max_empty_retries,
            hour: saved[s.name]?.hour,
            minute: saved[s.name]?.minute,
          }
        }
        setCrawlers(init)
      })
      .catch((e) => setFeedback({ type: 'err', text: errMsg(e, '配置加载失败') }))
      .finally(() => setLoading(false))
  }, [])

  function setSourceField(source: string, field: keyof RateLimitDraft, value: string) {
    setRateDraft((prev) => ({
      ...prev,
      [source]: { ...(prev[source] ?? { req_per_min: '', delay_min: '', delay_max: '' }), [field]: value },
    }))
  }

  function buildPayload(): RuntimeConfig {
    const payload: RuntimeConfig = {}
    // 采集上限：空/越界省略（服务端增量合并保留旧值），不再静默回退 100
    const cap = toIntInRange(scalars.crawl_items_cap, 10, 1000)
    if (cap != null) payload.crawl_items_cap = cap
    // 限频：逐字段清洗到契约域（req_per_min 1-600，delay_range 1-300），
    // 清空/越界的字段省略；三项全空的源整体省略（与后端 _validate_rate_limit 口径一致）
    const rate: NonNullable<RuntimeConfig['rate_limit']> = {}
    for (const [source, d] of Object.entries(rateDraft)) {
      const entry: RateLimitEntry = {}
      const rpm = toIntInRange(d.req_per_min, 1, 600)
      if (rpm != null) entry.req_per_min = rpm
      const dmin = toIntInRange(d.delay_min, 1, 300)
      const dmax = toIntInRange(d.delay_max, 1, 300)
      if (dmin != null && dmax != null) entry.delay_range = [dmin, dmax]
      if (entry.req_per_min != null || entry.delay_range != null) rate[source] = entry
    }
    payload.rate_limit = rate
    // 每爬虫配置：enabled 全量提交；max_results 仅提交非空；hour/minute 成对提交；
    // max_empty_retries 仅 zhilian 消费，提交非空值
    const cleaned: Record<string, CrawlerConfig> = {}
    for (const s of CRAWLER_SPIDERS) {
      const c = crawlers[s.name] ?? {}
      const entry: CrawlerConfig = { enabled: c.enabled ?? true }
      if (c.max_results != null && c.max_results > 0) entry.max_results = c.max_results
      if (s.name === 'zhilian' && c.max_empty_retries != null && c.max_empty_retries >= 0) {
        entry.max_empty_retries = c.max_empty_retries
      }
      if (c.hour != null && c.minute != null && c.hour >= 0 && c.hour <= 23 && c.minute >= 0 && c.minute <= 59) {
        entry.hour = c.hour
        entry.minute = c.minute
      }
      cleaned[s.name] = entry
    }
    payload.crawlers = cleaned
    return payload
  }

  async function save() {
    setSaving(true)
    setFeedback(null)
    try {
      const saved = await apiPut<RuntimeConfig>('/admin/runtime-config', buildPayload())
      setScalars({ crawl_items_cap: String(saved.crawl_items_cap ?? 100) })
      setRateDraft(draftsFromConfig(saved))
      const savedCrawlers = saved.crawlers ?? {}
      const init: Record<string, CrawlerConfig> = {}
      for (const s of CRAWLER_SPIDERS) {
        init[s.name] = {
          enabled: savedCrawlers[s.name]?.enabled ?? true,
          max_results: savedCrawlers[s.name]?.max_results,
          max_empty_retries: savedCrawlers[s.name]?.max_empty_retries,
          hour: savedCrawlers[s.name]?.hour,
          minute: savedCrawlers[s.name]?.minute,
        }
      }
      setCrawlers(init)
      setFeedback({ type: 'ok', text: '已保存，重启 api/worker 容器后生效' })
    } catch (e) {
      setFeedback({ type: 'err', text: errMsg(e, '保存失败') })
    } finally {
      setSaving(false)
    }
  }

  function resetAll() {
    setScalars({ crawl_items_cap: '100' })
    setRateDraft({})
    const reset: Record<string, CrawlerConfig> = {}
    for (const s of CRAWLER_SPIDERS) reset[s.name] = { enabled: true }
    setCrawlers(reset)
    setFeedback(null)
  }

  return (
    <div className="space-y-4">
      {feedback && (
        <div className={`rounded-md px-3 py-2 text-xs ${feedback.type === 'ok' ? 'bg-subtle text-state-stable' : 'bg-subtle text-state-archived'}`}>
          {feedback.text}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Settings2 className="size-4" />
            采集上限
          </CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <p className="py-6 text-center text-xs text-ink-faint">加载配置…</p>
          ) : (
            <div className="max-w-sm space-y-1.5">
              <Label className="text-xs">单次采集上限（条）</Label>
              <Input
                value={scalars.crawl_items_cap}
                onChange={(e) => setScalars({ crawl_items_cap: e.target.value })}
                placeholder="100"
                className="h-8 text-xs font-mono"
              />
              <p className="text-[11px] text-ink-faint">爬虫单次采集条数上限（10-1000）</p>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Settings2 className="size-4" />
            爬虫限频
            <Badge variant="outline" className="text-[11px] ml-auto font-mono">每源请求间隔 / 频率</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <p className="py-6 text-center text-xs text-ink-faint">加载配置…</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[150px]">数据源</TableHead>
                  <TableHead className="w-[120px]">req/min</TableHead>
                  <TableHead>间隔 [min, max] 秒</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {Object.entries(rateDraft).map(([source, d]) => {
                  return (
                    <TableRow key={source}>
                      <TableCell className="font-mono text-xs text-ink">{source}</TableCell>
                      <TableCell>
                        <Input
                          type="number"
                          value={d.req_per_min}
                          placeholder="1-600"
                          onChange={(e) => setSourceField(source, 'req_per_min', e.target.value)}
                          className="h-7 w-20 text-xs font-mono"
                        />
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1.5">
                          <Input
                            type="number"
                            value={d.delay_min}
                            placeholder="1-300"
                            onChange={(e) => setSourceField(source, 'delay_min', e.target.value)}
                            className="h-7 w-20 text-xs font-mono"
                          />
                          <span className="text-xs text-ink-faint">~</span>
                          <Input
                            type="number"
                            value={d.delay_max}
                            placeholder="1-300"
                            onChange={(e) => setSourceField(source, 'delay_max', e.target.value)}
                            className="h-7 w-20 text-xs font-mono"
                          />
                          <span className="text-[11px] text-ink-faint">秒</span>
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Settings2 className="size-4" />
            每爬虫采集配置
            <Badge variant="outline" className="text-[11px] ml-auto font-mono">开关 + 采集数量 + 独立触发时间</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {loading ? (
            <p className="py-6 text-center text-xs text-ink-faint">加载配置…</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[140px]">爬虫</TableHead>
                  <TableHead className="w-[80px]">启用</TableHead>
                  <TableHead className="w-[150px]">单次采集上限</TableHead>
                  <TableHead className="w-[120px]">空列表重试</TableHead>
                  <TableHead className="w-[170px]">独立触发时间</TableHead>
                  <TableHead>说明</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {CRAWLER_SPIDERS.map((s) => {
                  const c = crawlers[s.name] ?? {}
                  const disabled = c.enabled === false
                  return (
                    <TableRow key={s.name}>
                      <TableCell>
                        <span className="text-xs font-medium text-ink">{s.label}</span>
                        <span className="ml-1.5 font-mono text-[11px] text-ink-faint">{s.name}</span>
                      </TableCell>
                      <TableCell>
                        <label className="flex items-center gap-1.5 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={!disabled}
                            onChange={(e) =>
                              setCrawlers((prev) => ({ ...prev, [s.name]: { ...(prev[s.name] ?? {}), enabled: e.target.checked } }))
                            }
                            className="size-3.5 accent-[var(--color-primary)]"
                          />
                          <span className="text-[11px] text-ink-muted">{disabled ? '停用' : '启用'}</span>
                        </label>
                      </TableCell>
                      <TableCell>
                        <Input
                          type="number"
                          disabled={disabled}
                          value={c.max_results ?? ''}
                          placeholder="源默认"
                          min={10}
                          max={1000}
                          onChange={(e) =>
                            setCrawlers((prev) => ({
                              ...prev,
                              [s.name]: { ...(prev[s.name] ?? {}), max_results: e.target.value ? Number(e.target.value) : undefined },
                            }))
                          }
                          className="h-7 w-24 text-xs font-mono"
                        />
                      </TableCell>
                      <TableCell>
                        {s.name === 'zhilian' ? (
                          <>
                            <Input
                              type="number"
                              disabled={disabled}
                              value={c.max_empty_retries ?? ''}
                              placeholder="默认3"
                              min={0}
                              max={10}
                              onChange={(e) =>
                                setCrawlers((prev) => ({
                                  ...prev,
                                  [s.name]: { ...(prev[s.name] ?? {}), max_empty_retries: e.target.value ? Number(e.target.value) : undefined },
                                }))
                              }
                              className="h-7 w-16 text-xs font-mono"
                            />
                            <p className="mt-0.5 text-[10px] text-ink-faint">0=关闭 · 1-10次</p>
                          </>
                        ) : (
                          <span className="text-[11px] text-ink-faint">–</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1">
                          <Input
                            type="number"
                            disabled={disabled}
                            value={c.hour ?? ''}
                            placeholder="–"
                            min={0}
                            max={23}
                            onChange={(e) =>
                              setCrawlers((prev) => ({
                                ...prev,
                                [s.name]: { ...(prev[s.name] ?? {}), hour: e.target.value ? Number(e.target.value) : undefined },
                              }))
                            }
                            className="h-7 w-14 text-xs font-mono"
                          />
                          <span className="text-xs text-ink-faint">:</span>
                          <Input
                            type="number"
                            disabled={disabled}
                            value={c.minute ?? ''}
                            placeholder="–"
                            min={0}
                            max={59}
                            onChange={(e) =>
                              setCrawlers((prev) => ({
                                ...prev,
                                [s.name]: { ...(prev[s.name] ?? {}), minute: e.target.value ? Number(e.target.value) : undefined },
                              }))
                            }
                            className="h-7 w-14 text-xs font-mono"
                          />
                        </div>
                        <p className="mt-0.5 text-[10px] text-ink-faint">留空=并入主管线</p>
                      </TableCell>
                      <TableCell className="text-[11px] text-ink-faint">{s.note}</TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card className="mb-4 border-dashed">
        <CardContent className="py-3 text-[12px] text-ink-muted space-y-1">
          <p className="font-medium text-ink">调度语义</p>
          <ul className="list-disc pl-4 space-y-0.5">
            <li>启用开关：默认全部启用；关闭后该源不参与 ETL 主管线与独立调度。</li>
            <li>单次采集上限：仅 <code className="font-mono">zhilian / arxiv</code> 生效，其余源按源默认数量。</li>
            <li>独立触发时间：留空（–）并入 ETL 主管线（每日 <code className="font-mono">etl_run_hour:etl_run_minute</code>）；配置时:分后该源由 crawl_scheduler 到点单独触发，主管线跳过（防双跑）。</li>
            <li>CDP 源与课程源不在主管线 crawl 阶段：必须配置独立触发时间才会自动采集，否则仅供手动触发（上方实时 Tab）。</li>
          </ul>
        </CardContent>
      </Card>

      <div className="flex items-center gap-2">
        <Button size="sm" className="h-8" onClick={save} disabled={saving || loading}>
          <Save className="size-3.5 mr-1" />
          {saving ? '保存中…' : '保存配置'}
        </Button>
        <Button size="sm" variant="outline" className="h-8" onClick={resetAll} disabled={loading}>
          <RotateCcw className="size-3.5 mr-1" />
          恢复默认
        </Button>
      </div>
    </div>
  )
}