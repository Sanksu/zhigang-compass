import { useSearchParams } from 'react-router'
import { PageHeader } from '@/components/layout/page-header'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { CandidateReviewTab } from '@/components/admin/review/candidate-review-tab'
import { DictGuardTab } from '@/components/admin/review/dict-guard-tab'
import { EvolutionReviewTab } from '@/components/admin/review/evolution-review-tab'
import { PositionEditorTab } from '@/components/admin/review/position-editor-tab'
import { TechnologyWatchTab } from '@/components/admin/review/technology-watch-tab'

const VALID_TABS = ['candidate', 'evolution', 'edit', 'watch', 'dict'] as const
type ReviewTab = (typeof VALID_TABS)[number]

/** 解析 URL 查询参数 ?tab=，非法值回退 candidate（快捷操作可直达 /admin/review?tab=dict） */
function tabFromQuery(raw: string | null): ReviewTab {
  return (VALID_TABS as readonly string[]).includes(raw ?? '') ? (raw as ReviewTab) : 'candidate'
}

/**
 * 岗位审核页 — 设计文档 §7.2.2 + AL-M4-01 + 技能字典自治守卫方案 §7
 *
 * 五类审核 Tab（候选晋升 / 演化 / 人工编辑 / 观察池 / 字典守卫）已拆分至
 * components/admin/review/ 各自组件，本文件仅保留 PageHeader + 受控 Tabs 壳：
 * Tab 自持数据与请求（state 不提升），仅生效 Tab 挂载（保持原条件挂载语义）。
 */
export function AdminReviewPage() {
  // Tab 值以 URL ?tab= 为单一来源：URL 直达（快捷操作/收藏）与手动切换都写回 query，
  // 不设独立 state，避免 effect 同步 setState（react-hooks/set-state-in-effect）
  const [searchParams, setSearchParams] = useSearchParams()
  const tab: ReviewTab = tabFromQuery(searchParams.get('tab'))
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
        description="六状态机全链路人工审核：候选晋升（candidate → emerging / rejected）· 演化晋级（emerging → stable / declining）· 衰退归档（declining → archived）"
      />
      <Tabs value={tab} onValueChange={onTabChange}>
        <TabsList>
          <TabsTrigger value="candidate" className="text-xs">候选晋升审核</TabsTrigger>
          <TabsTrigger value="evolution" className="text-xs">演化审核（emerging）</TabsTrigger>
          <TabsTrigger value="edit" className="text-xs">岗位人工编辑</TabsTrigger>
          <TabsTrigger value="watch" className="text-xs">发现观察池</TabsTrigger>
          <TabsTrigger value="dict" className="text-xs">字典守卫</TabsTrigger>
        </TabsList>
        <TabsContent value="candidate">
          <CandidateReviewTab />
        </TabsContent>
        <TabsContent value="evolution">
          <EvolutionReviewTab />
        </TabsContent>
        <TabsContent value="edit">
          <PositionEditorTab />
        </TabsContent>
        <TabsContent value="watch">
          <TechnologyWatchTab />
        </TabsContent>
        <TabsContent value="dict">
          <DictGuardTab />
        </TabsContent>
      </Tabs>
    </>
  )
}
