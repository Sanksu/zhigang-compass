import { useState } from 'react'
import { PageHeader } from '@/components/layout/page-header'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { CandidateReviewTab } from '@/components/admin/review/candidate-review-tab'
import { EvolutionReviewTab } from '@/components/admin/review/evolution-review-tab'
import { PositionEditorTab } from '@/components/admin/review/position-editor-tab'
import { TechnologyWatchTab } from '@/components/admin/review/technology-watch-tab'

/**
 * 岗位审核页 — 设计文档 §7.2.2 + AL-M4-01
 *
 * 四类审核 Tab（候选晋升 / 演化 / 人工编辑 / 观察池）已拆分至
 * components/admin/review/ 各自组件，本文件仅保留 PageHeader + 受控 Tabs 壳：
 * Tab 自持数据与请求（state 不提升），仅生效 Tab 挂载（保持原条件挂载语义）。
 */
export function AdminReviewPage() {
  const [tab, setTab] = useState<'candidate' | 'evolution' | 'edit' | 'watch'>('candidate')

  return (
    <>
      <PageHeader
        title="岗位审核"
        description="六状态机全链路人工审核：候选晋升（candidate → emerging / rejected）· 演化晋级（emerging → stable / declining）· 衰退归档（declining → archived）"
      />
      <Tabs value={tab} onValueChange={(v) => setTab(v as 'candidate' | 'evolution' | 'edit' | 'watch')}>
        <TabsList>
          <TabsTrigger value="candidate" className="text-xs">候选晋升审核</TabsTrigger>
          <TabsTrigger value="evolution" className="text-xs">演化审核（emerging）</TabsTrigger>
          <TabsTrigger value="edit" className="text-xs">岗位人工编辑</TabsTrigger>
          <TabsTrigger value="watch" className="text-xs">发现观察池</TabsTrigger>
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
      </Tabs>
    </>
  )
}
