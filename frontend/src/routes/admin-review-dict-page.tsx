/** 字典守卫审核独立路由（08-27 自 admin/review 迁出：技能字典自治守卫，语义独立于审批流） */
import { PageHeader } from '@/components/layout/page-header'
import { DictGuardTab } from '@/components/admin/review/dict-guard-tab'

export function AdminReviewDictPage() {
  return (
    <>
      <PageHeader
        title="字典守卫"
        description="技能字典自治守卫 · 非法/多义技能拦截与提案处置"
      />
      <DictGuardTab />
    </>
  )
}