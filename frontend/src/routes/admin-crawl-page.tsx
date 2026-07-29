import { PagePlaceholder } from '@/components/layout/page-placeholder'

export function AdminCrawlPage() {
  return (
    <PagePlaceholder
      title="爬取管理"
      description="手动触发各平台爬取、进度监控、历史记录"
      specRef="§4 数据采集与预处理 · POST /api/v1/admin/crawl/trigger"
    />
  )
}
