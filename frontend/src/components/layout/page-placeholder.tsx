import { Construction } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { PageHeader } from './page-header'

interface PagePlaceholderProps {
  title: string
  description: string
  /** 对应的设计文档章节，便于追溯 */
  specRef?: string
}

/**
 * 占位页 — 脚手架阶段用于占位，标注对应设计文档章节
 * 后续开发时替换为实际页面实现
 */
export function PagePlaceholder({ title, description, specRef }: PagePlaceholderProps) {
  return (
    <>
      <PageHeader title={title} description={description} />
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center justify-center py-16 text-center">
          <Construction className="size-8 text-ink-faint mb-3" />
          <p className="text-sm text-ink-muted">该页面待开发</p>
          {specRef && (
            <p className="mt-2 text-xs text-ink-faint font-mono">
              参见设计文档 {specRef}
            </p>
          )}
        </CardContent>
      </Card>
    </>
  )
}
