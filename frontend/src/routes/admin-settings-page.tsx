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

/** 标量字段定义（名称/标签/占位/说明） */
const SCALAR_FIELDS: {
  key: 'arq_concurrency' | 'arq_job_timeout' | 'alert_webhook_url' | 'evolution_cache_ttl' | 'crawl_items_cap'
  label: string
  placeholder: string
  hint: string
}[] = [
  { key: 'arq_concurrency', label: '任务并发数', placeholder: '10', hint: 'ARQ worker 并发（1-100）' },
  { key: 'arq_job_timeout', label: '任务超时（秒）', placeholder: '1800', hint: 'ARQ 全局任务超时（60-86400）' },
  { key: 'alert_webhook_url', label: '告警 Webhook', placeholder: 'https://…（留空不告警）', hint: '爬虫失败 / 数据过期通知' },
  { key: 'evolution_cache_ttl', label: '演化缓存 TTL（秒）', placeholder: '60', hint: '演化看板列表缓存（5-3600）' },
  { key: 'crawl_items_cap', label: '单次采集上限（条）', placeholder: '100', hint: '爬虫单次采集条数上限（10-1000）' },
]

export function AdminSettingsPage() {
  const [scalars, setScalars] = useState<Record<string, string>>({})
  const [rateLimit, setRateLimit] = useState<Record<string, RateLimitEntry>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [feedback, setFeedback] = useState<Feedback>(null)

  useEffect(() => {
    apiGet<RuntimeConfig>('/admin/runtime-config')
      .then((cfg) => {
        setScalars({
          arq_concurrency: String(cfg.arq_concurrency ?? 10),
          arq_job_timeout: String(cfg.arq_job_timeout ?? 1800),
          alert_webhook_url: cfg.alert_webhook_url ?? '',
          evolution_cache_ttl: String(cfg.evolution_cache_ttl ?? 60),
          crawl_items_cap: String(cfg.crawl_items_cap ?? 100),
        })
        setRateLimit(cfg.rate_limit ?? {})
      })
      .catch((e) => setFeedback({ type: 'err', text: e instanceof ApiError ? e.message : '配置加载失败' }))
      .finally(() => setLoading(false))
  }, [])

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

  async function save() {
    setSaving(true)
    setFeedback(null)
    try {
      const payload: RuntimeConfig = {
        arq_concurrency: Number(scalars.arq_concurrency) || 10,
        arq_job_timeout: Number(scalars.arq_job_timeout) || 1800,
        alert_webhook_url: scalars.alert_webhook_url?.trim() ?? '',
        evolution_cache_ttl: Number(scalars.evolution_cache_ttl) || 60,
        crawl_items_cap: Number(scalars.crawl_items_cap) || 100,
        rate_limit: rateLimit,
      }
      const saved = await apiPut<RuntimeConfig>('/admin/runtime-config', payload)
      setScalars({
        arq_concurrency: String(saved.arq_concurrency ?? 10),
        arq_job_timeout: String(saved.arq_job_timeout ?? 1800),
        alert_webhook_url: saved.alert_webhook_url ?? '',
        evolution_cache_ttl: String(saved.evolution_cache_ttl ?? 60),
        crawl_items_cap: String(saved.crawl_items_cap ?? 100),
      })
      setRateLimit(saved.rate_limit ?? {})
      setFeedback({ type: 'ok', text: '已保存到 runtime_settings.json，重启 api/worker 容器后生效' })
    } catch (e) {
      setFeedback({ type: 'err', text: e instanceof ApiError ? e.message : '保存失败' })
    } finally {
      setSaving(false)
    }
  }

  function resetAll() {
    setScalars({
      arq_concurrency: '10',
      arq_job_timeout: '1800',
      alert_webhook_url: '',
      evolution_cache_ttl: '60',
      crawl_items_cap: '100',
    })
    setRateLimit({})
    setFeedback(null)
  }

  return (
    <>
      <PageHeader title="系统配置" description="后端运行时参数 · 保存后重启 api/worker 容器生效" />
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

      <Card className="mb-4">
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Settings2 className="size-4" />
            运行参数
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {loading ? (
            <p className="py-6 text-center text-xs text-ink-faint">加载配置…</p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {SCALAR_FIELDS.map((f) => (
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
