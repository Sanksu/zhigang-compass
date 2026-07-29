import { FileText, Upload } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'

/**
 * 简历匹配页 — 设计文档 §10.4
 *
 * 待实现功能（M4 阶段）：
 * - 左侧：简历上传区（拖拽/点击，PDF/Word/图片 ≤ 10MB）
 * - 右侧：Top-N 推荐卡片列表
 * - 点击展开：人岗比对报告（环形图/雷达图/热力图/甘特图/诊断报告）
 */
export function ResumeMatchPage() {
  return (
    <>
      <PageHeader
        title="简历匹配"
        description="上传简历 → 自动推荐岗位 → 人岗比对分析"
      />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* 简历上传区 */}
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-center justify-center py-16 text-center">
            <div className="rounded-full bg-subtle p-4 mb-4">
              <Upload className="size-6 text-ink-muted" />
            </div>
            <p className="text-sm font-medium text-ink mb-1">上传简历</p>
            <p className="text-xs text-ink-muted mb-4">
              支持 PDF / Word / 图片，最大 10MB
            </p>
            <Button variant="outline" size="sm" disabled>
              <FileText className="size-4" />
              选择文件
            </Button>
            <p className="text-xs text-ink-faint mt-4 max-w-xs">
              简历文本先经 PII 脱敏处理后再送入 LLM，符合 PIPL/GDPR 合规要求
            </p>
          </CardContent>
        </Card>

        {/* 推荐结果区 */}
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-center justify-center py-16 text-center">
            <p className="text-sm text-ink-muted">推荐结果待简历上传后显示</p>
            <p className="text-xs text-ink-faint font-mono mt-2">
              POST /api/v1/match/recommend
            </p>
          </CardContent>
        </Card>
      </div>
    </>
  )
}
