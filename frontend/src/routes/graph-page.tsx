import { Network } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'

/**
 * 能力图谱页 — 设计文档 §10.3
 *
 * 待实现功能（M3 阶段）：
 * - 2D ECharts 力导向图（默认）
 * - 3D react-force-graph-3d（可选，WebGL2 降级至 2D）
 * - 四种视图切换：panorama / techStack / level / positionCenter
 * - 节点详情面板（右侧 25-30% 宽度）
 */
export function GraphPage() {
  return (
    <>
      <PageHeader
        title="能力图谱"
        description="岗位-技能-证据关系可视化 · 2D 力导向图为主，3D 模式可选"
        actions={
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" disabled>2D</Button>
            <Button variant="ghost" size="sm" disabled>3D</Button>
          </div>
        }
      />

      {/* 视图切换 tabs — 待实现 */}
      <div className="flex items-center gap-1 mb-4 border-b border-border">
        {['全景视图', '技术栈视图', '级别视图', '岗位中心视图'].map((tab, i) => (
          <button
            key={tab}
            className={`px-3 py-2 text-sm border-b-2 transition-colors ${
              i === 0
                ? 'border-ink text-ink font-medium'
                : 'border-transparent text-ink-muted hover:text-ink'
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* 图谱画布占位 */}
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center justify-center py-24 text-center">
          <Network className="size-12 text-ink-faint mb-4" />
          <p className="text-sm text-ink-muted mb-1">图谱画布待接入</p>
          <p className="text-xs text-ink-faint font-mono mb-4">
            GET /api/v1/graph/panorama · 数据规模 ≤ 600 节点 / 1500 边
          </p>
          <p className="text-xs text-ink-faint max-w-md">
            M3 阶段接入 ECharts 2D 力导向图，节点 ≥ 100 @ 60fps；
            3D 模式可选启用，WebGL2 不可用时自动降级至 2D
          </p>
        </CardContent>
      </Card>
    </>
  )
}
