import { PagePlaceholder } from '@/components/layout/page-placeholder'

export function AdminReviewPage() {
  return (
    <PagePlaceholder
      title="岗位审核"
      description="新兴岗位审批队列 · candidate → emerging / rejected"
      specRef="§7.2.1 岗位状态机 · GET /api/v1/admin/positions/pending"
    />
  )
}
