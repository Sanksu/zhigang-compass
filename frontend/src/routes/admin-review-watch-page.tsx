/** 技术观察池审核独立路由（08-27 自 admin/review 迁出：数据语义与候选/演化/编辑审批流不同） */
import { PageHeader } from '@/components/layout/page-header'
import { Reveal } from '@/components/ui/reveal'
import { TechnologyWatchTab } from '@/components/admin/review/technology-watch-tab'

export function AdminReviewWatchPage() {
  return (
    <>
      <PageHeader
        title="技术观察池"
        description="技术热点/产业化拐点观察 · 独立于岗位候选审批流"
      />
      <Reveal delay={380}>
        <TechnologyWatchTab />
      </Reveal>
    </>
  )
}