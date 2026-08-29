import { Navigate, useSearchParams } from 'react-router'
import { PageHeader } from '@/components/layout/page-header'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ApprovalOverviewTab } from '@/components/admin/review/approval-overview-tab'
import { CandidateReviewTab } from '@/components/admin/review/candidate-review-tab'
import { EvolutionReviewTab } from '@/components/admin/review/evolution-review-tab'
import { PositionEditorTab } from '@/components/admin/review/position-editor-tab'

const VALID_TABS = ['overview', 'candidate', 'evolution', 'edit'] as const
type ReviewTab = (typeof VALID_TABS)[number]

/** 观察池 / 字典守卫已迁出独立路由 /admin/review/watch、/admin/review/dict（数据语义与审批流不同） */
const LEGACY_TAB_HREF: Record<string, string> = {
  watch: '/admin/review/watch',
  dict: '/admin/review/dict',
}

/** 解析 URL 查询参数 ?tab=，非法值回退 overview（总览为默认首 Tab；快捷操作可直达） */
function tabFromQuery(raw: string | null): ReviewTab {
  return (VALID_TABS as readonly string[]).includes(raw ?? '') ? (raw as ReviewTab) : 'overview'
}

/**
 * 岗位审核页 — 设计文档 §7.2.2 + AL-M4-01 + 技能字典自治守卫方案 §7
 *
 * 三类审核 Tab（候选晋升 / 演化 / 人工编辑）+ 观察池/字典守卫两个独立路由。
 * 本文件仅保留 PageHeader + 受控 Tabs 壳：Tab 自持数据与请求，仅生效 Tab 挂载。
 */
export function AdminReviewPage() {
  // Tab 值以 URL ?tab= 为单一来源：URL 直达（快捷操作/收藏）与手动切换都写回 query，
  // 不设独立 state，避免 effect 同步 setState（react-hooks/set-state-in-effect）
  const [searchParams, setSearchParams] = useSearchParams()
  const rawTab = searchParams.get('tab')
  // 旧 ?tab=watch/dict 快捷链接重定向到独立路由，保持收藏/书签可用
  if (rawTab && LEGACY_TAB_HREF[rawTab]) {
    return <Navigate to={LEGACY_TAB_HREF[rawTab]} replace />
  }
  const tab: ReviewTab = tabFromQuery(rawTab)
  const onTabChange = (v: string) => {
    const next = v as ReviewTab
    if (next === tab) return
    setSearchParams((prev) => {
      prev.set('tab', next)
      return prev
    }, { replace: true })
  }

  return (
    <>
      <PageHeader
        title="岗位审核"
        description="全审批池总览 + 六状态机全链路人工审核：候选晋升（candidate → emerging / rejected）· 演化晋级（emerging → stable / declining）· 衰退归档（declining → archived）· 观察池与字典守卫见独立路由"
      />
      <Tabs value={tab} onValueChange={onTabChange}>
        <TabsList>
          <TabsTrigger value="overview" className="text-xs">总览</TabsTrigger>
          <TabsTrigger value="candidate" className="text-xs">候选晋升审核</TabsTrigger>
          <TabsTrigger value="evolution" className="text-xs">演化审核（emerging）</TabsTrigger>
          <TabsTrigger value="edit" className="text-xs">岗位人工编辑</TabsTrigger>
        </TabsList>
        <TabsContent value="overview">
          <ApprovalOverviewTab />
        </TabsContent>
        <TabsContent value="candidate">
          <CandidateReviewTab />
        </TabsContent>
        <TabsContent value="evolution">
          <EvolutionReviewTab />
        </TabsContent>
        <TabsContent value="edit">
          <PositionEditorTab />
        </TabsContent>
      </Tabs>
    </>
  )
}
