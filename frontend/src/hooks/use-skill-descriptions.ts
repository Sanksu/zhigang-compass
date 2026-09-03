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

/** 拉取技能解释并写入 descMap（初始加载与外部刷新复用） */
function loadDescs(
  setDescMap: (m: Record<string, SkillDescEntry>) => void,
) {
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
}

export function useSkillDescriptions() {
  const [descMap, setDescMap] = useState<Record<string, SkillDescEntry>>({})
  const [loading, setLoading] = useState(true)

  // 初始加载：effect 内不直接调用 setState 同步路径（react-hooks lint），
  // 请求回调天然异步 setState；loading 初值已为 true，无需在 effect 内同步置位。
  useEffect(() => {
    let alive = true
    loadDescs((m) => {
      if (alive) {
        setDescMap(m)
        setLoading(false)
      }
    })
    return () => {
      alive = false
    }
  }, [])

  // 外部刷新（保存/LLM 补齐后）：主动置 loading 同步是允许的（非 effect 内调用）
  const reloadDescs = useCallback(() => {
    setLoading(true)
    loadDescs(setDescMap)
  }, [])

  return { descMap, reloadDescs, loading }
}