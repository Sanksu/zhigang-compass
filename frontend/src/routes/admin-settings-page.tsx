import { useEffect, useState } from 'react'
import { RotateCcw, Save, Settings2 } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
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
import {apiGet, apiPut, errMsg} from '@/lib/api'
import type { components } from '@/types/api'

/** 运行时配置（契约 RuntimeConfig，/admin/runtime-config，重启后生效） */
type RuntimeConfig = components['schemas']['RuntimeConfig']

type RateLimitEntry = NonNullable<RuntimeConfig['rate_limit']>[string]

type Feedback = { type: 'ok' | 'err'; text: string } | null

/** 配置分区（08-16：不同配置项放不同页面；08-21：新增 ETL 队列） */
export type SettingsSection = 'tasks' | 'crawl' | 'evolution' | 'etl'

const SECTION_META: Record<SettingsSection, { title: string; desc: string }> = {
  tasks: { title: '任务与告警', desc: 'ARQ 任务并发/超时与告警 Webhook · 重启后生效' },
  crawl: { title: '采集与限频', desc: '单次采集上限与各源请求限频 · 重启后生效' },
  evolution: { title: '演化与缓存', desc: '演化看板缓存 TTL · 重启后生效' },
  etl: { title: 'ETL 队列', desc: 'ETL 批次/调度时间 + 每爬虫开关/采集数量/独立触发时间（容器内 ARQ cron）· 重启 worker 后生效' },
}

/** 任务与告警字段 */
const TASK_FIELDS: {
  key: 'arq_concurrency' | 'arq_job_timeout' | 'alert_webhook_url'
  label: string
  placeholder: string
  hint: string
}[] = [
  { key: 'arq_concurrency', label: '任务并发数', placeholder: '10', hint: 'ARQ worker 并发（1-100）' },
  { key: 'arq_job_timeout', label: '任务超时（秒）', placeholder: '1800', hint: 'ARQ 全局任务超时（60-86400）' },
  { key: 'alert_webhook_url', label: '告警 Webhook', placeholder: 'https://…（留空不告警）', hint: '爬虫失败 / 数据过期通知' },
]

const TASK_DEFAULTS: Record<string, string> = {
  arq_concurrency: '10',
  arq_job_timeout: '1800',
  alert_webhook_url: '',
}

/** ETL 队列字段（08-21：批次 + 容器内 ARQ cron 调度时间） */
const ETL_FIELDS: {
  key: 'etl_batch_cap' | 'etl_structure_load_default' | 'etl_validate_temporal_default' | 'etl_run_hour' | 'etl_run_minute'
  label: string
  placeholder: string
  hint: string
}[] = [
  { key: 'etl_batch_cap', label: 'ETL 批次上限', placeholder: '2000', hint: '积压缩放封顶（100-5000）' },
  { key: 'etl_structure_load_default', label: '结构化加载默认批次', placeholder: '500', hint: 'batch_extract 默认批（100-1000）' },
  { key: 'etl_validate_temporal_default', label: '时滞/通胀检测默认批次', placeholder: '200', hint: 'validate_temporal / detect_inflation（100-500）' },
  { key: 'etl_run_hour', label: '调度小时', placeholder: '5', hint: '容器内 ARQ cron（0-23）' },
  { key: 'etl_run_minute', label: '调度分钟', placeholder: '0', hint: '容器内 ARQ cron（0-59）' },
]

const ETL_DEFAULTS: Record<string, string> = {
  etl_batch_cap: '2000',
  etl_structure_load_default: '500',
  etl_validate_temporal_default: '200',
  etl_run_hour: '5',
  etl_run_minute: '0',
}

/** ETL 主调度爬虫（对齐 backend workers/etl.py crawl_platforms：国内+国际+趋势） */
const CRAWLER_SPIDERS = [
  { name: 'zhilian', label: '智联招聘', note: '国内 · 支持数量上限' },
  { name: 'indeed', label: 'Indeed', note: '国际 · 数量上限未生效' },
  { name: 'glassdoor', label: 'Glassdoor', note: '国际 CDP · 数量上限未生效' },
  { name: 'arxiv', label: 'arXiv', note: '论文 · 支持数量上限' },
  { name: 'github', label: 'GitHub', note: '社区 · 数量上限未生效' },
  { name: 'stackoverflow', label: 'StackOverflow', note: '社区 · 数量上限未生效' },
] as const

type CrawlerConfig = NonNullable<RuntimeConfig['crawlers']>[string]

export function AdminSettingsPage({ section }: { section: SettingsSection }) {
  const [scalars, setScalars] = useState<Record<string, string>>({})
  const [rateLimit, setRateLimit] = useState<Record<string, RateLimitEntry>>({})
  // 每爬虫采集配置：spider -> {enabled, max_results}（空=按源默认）
  const [crawlers, setCrawlers] = useState<Record<string, CrawlerConfig>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [feedback, setFeedback] = useState<Feedback>(null)
  const meta = SECTION_META[section]

  useEffect(() => {
    apiGet<RuntimeConfig>('/admin/runtime-config')
      .then((cfg) => {
        const next: Record<string, string> = {}
        if (section === 'tasks') {
          next.arq_concurrency = String(cfg.arq_concurrency ?? 10)
          next.arq_job_timeout = String(cfg.arq_job_timeout ?? 1800)
          next.alert_webhook_url = cfg.alert_webhook_url ?? ''
        } else if (section === 'crawl') {
          next.crawl_items_cap = String(cfg.crawl_items_cap ?? 100)
          setRateLimit(cfg.rate_limit ?? {})
        } else if (section === 'evolution') {
          next.evolution_cache_ttl = String(cfg.evolution_cache_ttl ?? 60)
        } else {
          next.etl_batch_cap = String(cfg.etl_batch_cap ?? 2000)
          next.etl_structure_load_default = String(cfg.etl_structure_load_default ?? 500)
          next.etl_validate_temporal_default = String(cfg.etl_validate_temporal_default ?? 200)
          next.etl_run_hour = String(cfg.etl_run_hour ?? 5)
          next.etl_run_minute = String(cfg.etl_run_minute ?? 0)
          // 每爬虫配置：缺省全启用 + 按源默认数量 + 无独立触发时间（空=并入主管线）
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
        }
        setScalars(next)
      })
      .catch((e) => setFeedback({ type: 'err', text: errMsg(e, '配置加载失败') }))
      .finally(() => setLoading(false))
  }, [section])

  function setSourceField(source: string, field: 'req_per_min' | 'delay_min' | 'delay_max', value: string) {
    setRateLimit((prev) => {
      const cur = prev[source] ?? { req_per_min: 4, delay_range: [10, 20] }
      const next: RateLimitEntry = { ...cur, delay_range: [...(cur.delay_range ?? [10, 20])] }
      if (field === 'req_per_min') next.req_per_min = Number(value) || 4
      if (field === 'delay_min') next.delay_range![0] = Number(value) || 1
      if (field === 'delay_max') next.delay_range![1] = Number(value) || 1
      return { ...prev, [source]: next }
    })
  }

  function buildPayload(): RuntimeConfig {
    const payload: RuntimeConfig = {}
    if (section === 'tasks') {
      payload.arq_concurrency = Number(scalars.arq_concurrency) || 10
      payload.arq_job_timeout = Number(scalars.arq_job_timeout) || 1800
      payload.alert_webhook_url = scalars.alert_webhook_url?.trim() ?? ''
    } else if (section === 'crawl') {
      payload.crawl_items_cap = Number(scalars.crawl_items_cap) || 100
      payload.rate_limit = rateLimit
    } else if (section === 'evolution') {
      payload.evolution_cache_ttl = Number(scalars.evolution_cache_ttl) || 60
    } else {
      payload.etl_batch_cap = Number(scalars.etl_batch_cap) || 2000
      payload.etl_structure_load_default = Number(scalars.etl_structure_load_default) || 500
      payload.etl_validate_temporal_default = Number(scalars.etl_validate_temporal_default) || 200
      // 0 是合法取值（0 点），不能用 || 兜底
      const hour = Number(scalars.etl_run_hour)
      payload.etl_run_hour = hour >= 0 && hour <= 23 ? hour : 5
      const minute = Number(scalars.etl_run_minute)
      payload.etl_run_minute = minute >= 0 && minute <= 59 ? minute : 0
      // 每爬虫配置：enabled 全量提交；max_results 仅提交非空；hour/minute 成对提交；
      // max_empty_retries 仅 zhilian 消费（其他源后端忽略），提交非空值
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
    }
    return payload
  }

  async function save() {
    setSaving(true)
    setFeedback(null)
    try {
      const saved = await apiPut<RuntimeConfig>('/admin/runtime-config', buildPayload())
      // 回填本页字段（其余页字段后端增量保留）
      if (section === 'tasks') {
        setScalars({
          arq_concurrency: String(saved.arq_concurrency ?? 10),
          arq_job_timeout: String(saved.arq_job_timeout ?? 1800),
          alert_webhook_url: saved.alert_webhook_url ?? '',
        })
      } else if (section === 'crawl') {
        setScalars({ crawl_items_cap: String(saved.crawl_items_cap ?? 100) })
        setRateLimit(saved.rate_limit ?? {})
      } else if (section === 'evolution') {
        setScalars({ evolution_cache_ttl: String(saved.evolution_cache_ttl ?? 60) })
      } else {
        setScalars({
          etl_batch_cap: String(saved.etl_batch_cap ?? 2000),
          etl_structure_load_default: String(saved.etl_structure_load_default ?? 500),
          etl_validate_temporal_default: String(saved.etl_validate_temporal_default ?? 200),
          etl_run_hour: String(saved.etl_run_hour ?? 5),
          etl_run_minute: String(saved.etl_run_minute ?? 0),
        })
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
      }
      setFeedback({ type: 'ok', text: '已保存，重启 api/worker 容器后生效' })
    } catch (e) {
      setFeedback({ type: 'err', text: errMsg(e, '保存失败') })
    } finally {
      setSaving(false)
    }
  }

  function resetAll() {
    if (section === 'tasks') {
      setScalars({ ...TASK_DEFAULTS })
    } else if (section === 'crawl') {
      setScalars({ crawl_items_cap: '100' })
      setRateLimit({})
    } else if (section === 'evolution') {
      setScalars({ evolution_cache_ttl: '60' })
    } else {
      setScalars({ ...ETL_DEFAULTS })
      const reset: Record<string, CrawlerConfig> = {}
      for (const s of CRAWLER_SPIDERS) reset[s.name] = { enabled: true }
      setCrawlers(reset)
    }
    setFeedback(null)
  }

  return (
    <>
      <PageHeader title={meta.title} description={meta.desc} />
      <Card className="mb-4">
        <CardContent className="py-3 flex items-center gap-2 text-xs text-ink-muted">
          <Settings2 className="size-4 shrink-0" />
          <span>
            配置持久化到 <code className="font-mono text-ink">backend/configs/runtime_settings.json</code>
            ，重启后生效；密钥/连接串等敏感项不在此页暴露。
          </span>
        </CardContent>
      </Card>

      {feedback && (
        <div className={`mb-4 rounded-md px-3 py-2 text-xs ${feedback.type === 'ok' ? 'bg-subtle text-state-stable' : 'bg-subtle text-state-archived'}`}>
          {feedback.text}
        </div>
      )}

      {section === 'tasks' && (
        <Card className="mb-4">
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <Settings2 className="size-4" />
              任务与告警
            </CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
              <p className="py-6 text-center text-xs text-ink-faint">加载配置…</p>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {TASK_FIELDS.map((f) => (
                  <div key={f.key} className="space-y-1.5">
                    <Label className="text-xs">{f.label}</Label>
                    <Input
                      value={scalars[f.key] ?? ''}
                      onChange={(e) => setScalars((s) => ({ ...s, [f.key]: e.target.value }))}
                      placeholder={f.placeholder}
                      className="h-8 text-xs font-mono"
                    />
                    <p className="text-[10px] text-ink-faint">{f.hint}</p>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {section === 'crawl' && (
        <>
          <Card className="mb-4">
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
                    value={scalars.crawl_items_cap ?? ''}
                    onChange={(e) => setScalars((s) => ({ ...s, crawl_items_cap: e.target.value }))}
                    placeholder="100"
                    className="h-8 text-xs font-mono"
                  />
                  <p className="text-[10px] text-ink-faint">爬虫单次采集条数上限（10-1000）</p>
                </div>
              )}
            </CardContent>
          </Card>
          <Card className="mb-4">
            <CardHeader>
              <CardTitle className="text-sm flex items-center gap-2">
                <Settings2 className="size-4" />
                爬虫限频
                <Badge variant="outline" className="text-[10px] ml-auto font-mono">
                  每源请求间隔 / 频率
                </Badge>
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
                    {Object.entries(rateLimit).map(([source, cfg]) => {
                      const dr = cfg.delay_range ?? [10, 20]
                      return (
                        <TableRow key={source}>
                          <TableCell className="font-mono text-xs text-ink">{source}</TableCell>
                          <TableCell>
                            <Input
                              type="number"
                              value={cfg.req_per_min ?? 4}
                              onChange={(e) => setSourceField(source, 'req_per_min', e.target.value)}
                              className="h-7 w-20 text-xs font-mono"
                            />
                          </TableCell>
                          <TableCell>
                            <div className="flex items-center gap-1.5">
                              <Input
                                type="number"
                                value={dr[0]}
                                onChange={(e) => setSourceField(source, 'delay_min', e.target.value)}
                                className="h-7 w-20 text-xs font-mono"
                              />
                              <span className="text-xs text-ink-faint">~</span>
                              <Input
                                type="number"
                                value={dr[1]}
                                onChange={(e) => setSourceField(source, 'delay_max', e.target.value)}
                                className="h-7 w-20 text-xs font-mono"
                              />
                              <span className="text-[10px] text-ink-faint">秒</span>
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
        </>
      )}

      {section === 'evolution' && (
        <Card className="mb-4">
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <Settings2 className="size-4" />
              演化与缓存
            </CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
              <p className="py-6 text-center text-xs text-ink-faint">加载配置…</p>
            ) : (
              <div className="max-w-sm space-y-1.5">
                <Label className="text-xs">演化缓存 TTL（秒）</Label>
                <Input
                  value={scalars.evolution_cache_ttl ?? ''}
                  onChange={(e) => setScalars((s) => ({ ...s, evolution_cache_ttl: e.target.value }))}
                  placeholder="60"
                  className="h-8 text-xs font-mono"
                />
                <p className="text-[10px] text-ink-faint">演化看板列表缓存（5-3600）</p>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {section === 'etl' && (
        <Card className="mb-4">
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <Settings2 className="size-4" />
              ETL 批次配置
            </CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
              <p className="py-6 text-center text-xs text-ink-faint">加载配置…</p>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {ETL_FIELDS.slice(0, 3).map((f) => (
                  <div key={f.key} className="space-y-1.5">
                    <Label className="text-xs">{f.label}</Label>
                    <Input
                      value={scalars[f.key] ?? ''}
                      onChange={(e) => setScalars((s) => ({ ...s, [f.key]: e.target.value }))}
                      placeholder={f.placeholder}
                      className="h-8 text-xs font-mono"
                    />
                    <p className="text-[10px] text-ink-faint">{f.hint}</p>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {section === 'etl' && (
        <Card className="mb-4">
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <Settings2 className="size-4" />
              调度时间（容器内 ARQ cron）
              <Badge variant="outline" className="text-[10px] ml-auto font-mono">
                替代外部计划任务 · 重启 worker 后生效
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
              <p className="py-6 text-center text-xs text-ink-faint">加载配置…</p>
            ) : (
              <div className="max-w-sm space-y-1.5">
                <Label className="text-xs">每日执行时间（时:分）</Label>
                <div className="flex items-center gap-1.5">
                  <Input
                    value={scalars.etl_run_hour ?? ''}
                    onChange={(e) => setScalars((s) => ({ ...s, etl_run_hour: e.target.value }))}
                    placeholder="5"
                    className="h-8 w-20 text-xs font-mono"
                  />
                  <span className="text-xs text-ink-faint">:</span>
                  <Input
                    value={scalars.etl_run_minute ?? ''}
                    onChange={(e) => setScalars((s) => ({ ...s, etl_run_minute: e.target.value }))}
                    placeholder="0"
                    className="h-8 w-20 text-xs font-mono"
                  />
                </div>
                <p className="text-[10px] text-ink-faint">ETL 主管线每日在此时入队执行（小时 0-23，分钟 0-59）</p>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {section === 'etl' && (
        <Card className="mb-4">
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <Settings2 className="size-4" />
              每爬虫采集配置
              <Badge variant="outline" className="text-[10px] ml-auto font-mono">
                开关 + 采集数量 + 独立触发时间
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
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
                          <span className="ml-1.5 font-mono text-[10px] text-ink-faint">{s.name}</span>
                        </TableCell>
                        <TableCell>
                          <label className="flex items-center gap-1.5 cursor-pointer">
                            <input
                              type="checkbox"
                              checked={!disabled}
                              onChange={(e) =>
                                setCrawlers((prev) => ({
                                  ...prev,
                                  [s.name]: { ...(prev[s.name] ?? {}), enabled: e.target.checked },
                                }))
                              }
                              className="size-3.5 accent-[var(--color-primary)]"
                            />
                            <span className="text-[10px] text-ink-muted">{disabled ? '停用' : '启用'}</span>
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
                                [s.name]: {
                                  ...(prev[s.name] ?? {}),
                                  max_results: e.target.value ? Number(e.target.value) : undefined,
                                },
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
                                    [s.name]: {
                                      ...(prev[s.name] ?? {}),
                                      max_empty_retries: e.target.value ? Number(e.target.value) : undefined,
                                    },
                                  }))
                                }
                                className="h-7 w-16 text-xs font-mono"
                              />
                              <p className="mt-0.5 text-[9px] text-ink-faint">0=关闭 · 1-10次</p>
                            </>
                          ) : (
                            <span className="text-[10px] text-ink-faint">–</span>
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
                                  [s.name]: {
                                    ...(prev[s.name] ?? {}),
                                    hour: e.target.value ? Number(e.target.value) : undefined,
                                  },
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
                                  [s.name]: {
                                    ...(prev[s.name] ?? {}),
                                    minute: e.target.value ? Number(e.target.value) : undefined,
                                  },
                                }))
                              }
                              className="h-7 w-14 text-xs font-mono"
                            />
                          </div>
                          <p className="mt-0.5 text-[9px] text-ink-faint">留空=并入主管线</p>
                        </TableCell>
                        <TableCell className="text-[10px] text-ink-faint">{s.note}</TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

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
    </>
  )
}
