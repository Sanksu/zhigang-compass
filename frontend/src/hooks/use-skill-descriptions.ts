/**
 * 技能解释共享 hook：加载 /admin/skill-descriptions 全部解释到 {skill_name → {override, builtin}}。
 *
 * 技能治理页（admin-skills-page）与原始数据页技能治理（raw-skill-governance）
 * 复用同一加载逻辑，避免跨组件重复实现。
 */
import { useCallback, useEffect, useState } from 'react'
import { apiGet } from '@/lib/api'

export interface SkillDescEntry {
  override?: string
  builtin?: string
}

export function useSkillDescriptions() {
  const [descMap, setDescMap] = useState<Record<string, SkillDescEntry>>({})
  const [loading, setLoading] = useState(true)

  const reloadDescs = useCallback(() => {
    setLoading(true)
    apiGet<{
      items: { skill_name: string; override_desc: string | null; builtin_desc: string | null }[]
    }>('/admin/skill-descriptions?limit=1000')
      .then((r) => {
        const m: Record<string, SkillDescEntry> = {}
        for (const it of r.items)
          m[it.skill_name] = { override: it.override_desc ?? undefined, builtin: it.builtin_desc ?? undefined }
        setDescMap(m)
      })
      .catch(() => setDescMap({}))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    reloadDescs()
  }, [reloadDescs])

  return { descMap, reloadDescs, loading }
}