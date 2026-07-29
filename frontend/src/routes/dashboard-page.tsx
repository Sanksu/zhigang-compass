import { Link } from 'react-router'
import { Activity, Database, GitBranch, Network, TrendingUp, Users } from 'lucide-react'
import { CompassMark } from '@/components/layout/compass-mark'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'

/**
 * 仪表盘 — 系统总览，展示签名罗盘标记与关键指标
 *
 * 签名时刻：罗盘标记配合"系统就绪"状态，
 * 四向指针分别对应系统的四条主线（采集/图谱/匹配/演化）
 */
export function DashboardPage() {
  const stats = [
    { label: '图谱节点', value: '—', icon: Network, hint: '待 M3 上线' },
    { label: 'JD 采集量', value: '—', icon: Database, hint: '待爬虫启动' },
    { label: '岗位状态', value: '—', icon: GitBranch, hint: '五状态机' },
    { label: '匹配任务', value: '—', icon: Activity, hint: '待简历上传' },
  ]

  return (
    <>
      <PageHeader
        title="仪表盘"
        description="多源异构驱动的岗位能力动态演化与人岗匹配系统"
      />

      {/* 签名区域 — 罗盘标记 + 系统状态 */}
      <Card className="mb-6 overflow-hidden">
        <CardContent className="flex items-center gap-6 py-8">
          <CompassMark size="lg" active className="shrink-0" />
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-semibold">智岗罗盘</h2>
              <Badge variant="outline" className="font-mono">v0.1.0</Badge>
            </div>
            <p className="text-sm text-ink-muted">
              证据驱动的人才能力大脑 — 每条技能断言可追溯至原始 JD / 论文 / 社区信号
            </p>
            <div className="flex items-center gap-4 pt-2 text-xs text-ink-faint">
              <span className="flex items-center gap-1">
                <span className="size-1.5 rounded-full bg-state-emerging" />
                采集管线就绪
              </span>
              <span className="flex items-center gap-1">
                <span className="size-1.5 rounded-full bg-state-stable" />
                图谱服务待接入
              </span>
              <span className="flex items-center gap-1">
                <span className="size-1.5 rounded-full bg-state-candidate" />
                匹配引擎待接入
              </span>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* 关键指标卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {stats.map((stat) => {
          const Icon = stat.icon
          return (
            <Card key={stat.label}>
              <CardContent className="py-4">
                <div className="flex items-center justify-between mb-2">
                  <Icon className="size-4 text-ink-faint" />
                </div>
                <div className="text-2xl font-semibold tracking-tight">{stat.value}</div>
                <div className="text-xs text-ink-muted mt-1">{stat.label}</div>
                <div className="text-xs text-ink-faint mt-0.5">{stat.hint}</div>
              </CardContent>
            </Card>
          )
        })}
      </div>

      {/* 快捷入口 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Network className="size-4" />
              <Link to="/graph" className="hover:underline">能力图谱</Link>
            </CardTitle>
            <CardDescription>
              2D 力导向图为主，3D 模式可选。四种视图切换：全景 / 技术栈 / 级别 / 岗位中心
            </CardDescription>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <TrendingUp className="size-4" />
              <Link to="/evolution" className="hover:underline">演化看板</Link>
            </CardTitle>
            <CardDescription>
              90 天滑动窗口追踪技能频次变化，Z-score 检测新兴/衰退技能
            </CardDescription>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Users className="size-4" />
              <Link to="/resume-match" className="hover:underline">简历匹配</Link>
            </CardTitle>
            <CardDescription>
              上传简历 → LLM 解析 → 三维加权匹配 → 差距分析 → 学习路径推荐
            </CardDescription>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Database className="size-4" />
              14 源数据底座
            </CardTitle>
            <CardDescription>
              国内招聘 4 源 + 国际 4 源 + 社区 2 源 + 论文 1 源 + 课程 3 源 + O*NET
            </CardDescription>
          </CardHeader>
        </Card>
      </div>
    </>
  )
}
