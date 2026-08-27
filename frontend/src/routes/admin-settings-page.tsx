import { useEffect, useState } from 'react'
import { RotateCcw, Save, Settings2 } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { apiGet, apiPut, errMsg } from '@/lib/api'
import type { components } from '@/types/api'

/** 运行时配置（契约 RuntimeConfig，/admin/runtime-config，重启后生效） */
type RuntimeConfig = components['schemas']['RuntimeConfig']

type Feedback = { type: 'ok' | 'err'; text: string } | null

/**
 * 配置分区（08-16；08-27 瘦身：爬虫相关迁至 admin/crawl「调度与限频」Tab，
 * evolution+dictguard 合并为「系统节流」）。
 */
export type SettingsSection = 'tasks' | 'system' | 'etl'

const SECTION_META: Record<SettingsSection, { title: string; desc: string }> = {
  tasks: { title: '任务与告警', desc: 'ARQ 任务并发/超时与告警 Webhook · 重启后生效' },
  system: { title: '系统节流', desc: '演化缓存 TTL + 字典守卫驳回冷却期 · 重启后生效' },
  etl: { title: 'ETL 队列', desc: 'ETL 批次/调度时间（容器内 ARQ cron）· 重启 worker 后生效' },
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

/** 系统节流字段（08-24 驳回冷却期配置化 + 08-27 演化缓存并入） */
const SYSTEM_FIELDS: {
  key: 'evolution_cache_ttl' | 'dict_guard_reproposal_cooldown_days'
  label: string
  placeholder: string
  hint: string
}[] = [
  { key: 'evolution_cache_ttl', label: '演化缓存 TTL（秒）', placeholder: '60', hint: '演化看板列表缓存（5-3600）' },
  { key: 'dict_guard_reproposal_cooldown_days', label: '驳回提案冷却期（天）', placeholder: '7', hint: '已驳回提案在冷却期内不被每日 ETL 重复提议（1-90）' },
]

const SYSTEM_DEFAULTS: Record<string, string> = {
  evolution_cache_ttl: '60',
  dict_guard_reproposal_cooldown_days: '7',
}

/** ETL 批次 + 调度时间字段（每爬虫配置已迁至 admin/crawl「调度与限频」） */
const ETL_FIELDS: {
  key: 'etl_batch_cap' | 'etl_structure_load_default' | 'etl_validate_temporal_default' | 'etl_run_hour' | 'etl_run_minute'
  label: string
  placeholder: string
  hint: string
}[] = [
  { key: 'etl_batch_cap', label: 'ETL 批次上限', placeholder: '2000', hint: '积压缩放封顶（100-5000）' },
  { key: 'etl_structure_load_default', label: '结构化加载默认批次', placeholder: '500', hint: 'batch_extract 默认批（100-1000）' },
  { key: 'etl_validate_temporal_default', label: '时滞/通胀检测默认批次', placeholder: '200', hint: 'validate_temporal / detect_inflation（100-500）' },
  { key: 'etl_run_hour', label: '调度小时', placeholder: '5', hint: '容器内 ARQ cron（0-23）· 留空回落默认' },
  { key: 'etl_run_minute', label: '调度分钟', placeholder: '0', hint: '容器内 ARQ cron（0-59）· 留空回落默认' },
]

const ETL_DEFAULTS: Record<string, string> = {
  etl_batch_cap: '2000',
  etl_structure_load_default: '500',
  etl_validate_temporal_default: '200',
  etl_run_hour: '5',
  etl_run_minute: '0',
}

/** 读取配置时按分节提取字段，返回可编辑标量 */
function scalarsFromCfg(section: SettingsSection, cfg: RuntimeConfig): Record<string, string> {
  if (section === 'tasks') {
    return {
      arq_concurrency: String(cfg.arq_concurrency ?? 10),
      arq_job_timeout: String(cfg.arq_job_timeout ?? 1800),
      alert_webhook_url: cfg.alert_webhook_url ?? '',
    }
  }
  if (section === 'system') {
    return {
      evolution_cache_ttl: String(cfg.evolution_cache_ttl ?? 60),
      dict_guard_reproposal_cooldown_days: String(cfg.dict_guard_reproposal_cooldown_days ?? 7),
    }
  }
  return {
    etl_batch_cap: String(cfg.etl_batch_cap ?? 2000),
    etl_structure_load_default: String(cfg.etl_structure_load_default ?? 500),
    etl_validate_temporal_default: String(cfg.etl_validate_temporal_default ?? 200),
    etl_run_hour: String(cfg.etl_run_hour ?? 5),
    etl_run_minute: String(cfg.etl_run_minute ?? 0),
  }
}

/** 各分节默认值（恢复默认用） */
function defaultsFor(section: SettingsSection): Record<string, string> {
  if (section === 'tasks') return TASK_DEFAULTS
  if (section === 'system') return SYSTEM_DEFAULTS
  return ETL_DEFAULTS
}

/** 构建设置分节的提交载荷（仅含本分节字段，后端增量保留其余页配置） */
function buildPayload(section: SettingsSection, scalars: Record<string, string>): RuntimeConfig {
  const p: RuntimeConfig = {}
  if (section === 'tasks') {
    p.arq_concurrency = Number(scalars.arq_concurrency) || 10
    p.arq_job_timeout = Number(scalars.arq_job_timeout) || 1800
    p.alert_webhook_url = scalars.alert_webhook_url?.trim() ?? ''
  } else if (section === 'system') {
    p.evolution_cache_ttl = Number(scalars.evolution_cache_ttl) || 60
    p.dict_guard_reproposal_cooldown_days = Number(scalars.dict_guard_reproposal_cooldown_days) || 7
  } else {
    p.etl_batch_cap = Number(scalars.etl_batch_cap) || 2000
    p.etl_structure_load_default = Number(scalars.etl_structure_load_default) || 500
    p.etl_validate_temporal_default = Number(scalars.etl_validate_temporal_default) || 200
    // 0 是合法取值（0 点），不能用 || 兜底
    const hour = Number(scalars.etl_run_hour)
    p.etl_run_hour = hour >= 0 && hour <= 23 ? hour : undefined
    const minute = Number(scalars.etl_run_minute)
    p.etl_run_minute = minute >= 0 && minute <= 59 ? minute : undefined
  }
  return p
}

export function AdminSettingsPage({ section }: { section: SettingsSection }) {
  const [scalars, setScalars] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [feedback, setFeedback] = useState<Feedback>(null)
  const meta = SECTION_META[section]
  const fields = section === 'tasks' ? TASK_FIELDS : section === 'system' ? SYSTEM_FIELDS : ETL_FIELDS

  useEffect(() => {
    apiGet<RuntimeConfig>('/admin/runtime-config')
      .then((cfg) => setScalars(scalarsFromCfg(section, cfg)))
      .catch((e) => setFeedback({ type: 'err', text: errMsg(e, '配置加载失败') }))
      .finally(() => setLoading(false))
  }, [section])

  async function save() {
    setSaving(true)
    setFeedback(null)
    try {
      const saved = await apiPut<RuntimeConfig>('/admin/runtime-config', buildPayload(section, scalars))
      setScalars(scalarsFromCfg(section, saved))
      setFeedback({ type: 'ok', text: '已保存，重启 api/worker 容器后生效' })
    } catch (e) {
      setFeedback({ type: 'err', text: errMsg(e, '保存失败') })
    } finally {
      setSaving(false)
    }
  }

  function resetAll() {
    setScalars(defaultsFor(section))
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

      <Card className="mb-4">
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Settings2 className="size-4" />
            {meta.title}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <p className="py-6 text-center text-xs text-ink-faint">加载配置…</p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {fields.map((f) => (
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