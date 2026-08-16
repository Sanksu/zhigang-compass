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
import { apiGet, apiPut, ApiError } from '@/lib/api'
import type { components } from '@/types/api'

/** 运行时配置（契约 RuntimeConfig，/admin/runtime-config，重启后生效） */
type RuntimeConfig = components['schemas']['RuntimeConfig']

type RateLimitEntry = NonNullable<RuntimeConfig['rate_limit']>[string]

type Feedback = { type: 'ok' | 'err'; text: string } | null

/** 配置分区（08-16：不同配置项放不同页面） */
export type SettingsSection = 'tasks' | 'crawl' | 'evolution'

const SECTION_META: Record<SettingsSection, { title: string; desc: string }> = {
  tasks: { title: '任务与告警', desc: 'ARQ 任务并发/超时与告警 Webhook · 重启后生效' },
  crawl: { title: '采集与限频', desc: '单次采集上限与各源请求限频 · 重启后生效' },
  evolution: { title: '演化与缓存', desc: '演化看板缓存 TTL · 重启后生效' },
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

export function AdminSettingsPage({ section }: { section: SettingsSection }) {
  const [scalars, setScalars] = useState<Record<string, string>>({})
  const [rateLimit, setRateLimit] = useState<Record<string, RateLimitEntry>>({})
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
        } else {
          next.evolution_cache_ttl = String(cfg.evolution_cache_ttl ?? 60)
        }
        setScalars(next)
      })
      .catch((e) => setFeedback({ type: 'err', text: e instanceof ApiError ? e.message : '配置加载失败' }))
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
    } else {
      payload.evolution_cache_ttl = Number(scalars.evolution_cache_ttl) || 60
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
      } else {
        setScalars({ evolution_cache_ttl: String(saved.evolution_cache_ttl ?? 60) })
      }
      setFeedback({ type: 'ok', text: '已保存，重启 api/worker 容器后生效' })
    } catch (e) {
      setFeedback({ type: 'err', text: e instanceof ApiError ? e.message : '保存失败' })
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
    } else {
      setScalars({ evolution_cache_ttl: '60' })
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
