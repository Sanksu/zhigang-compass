import { Link } from 'react-router'
import { Activity, Database, GitBranch, Network, TrendingUp, Users, FileText, Clock } from 'lucide-react'
import { CompassMark } from '@/components/layout/compass-mark'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'

/**
 * 仪表盘 mock 数据 — M4 前端提前启动
 *
 * 后端 API 就绪后替换为：
 * - GET /api/v1/dashboard/summary（聚合指标）
 * - GET /api/v1/dashboard/recent-activity（最近活动流）
 */
const MOCK_STATS = [
  { label: '图谱节点', value: '1,248', delta: '+38', icon: Network, hint: '岗位 124 / 技能 1,082 / 证据 42', deltaType: 'up' as const },
  { label: 'JD 采集量', value: '8,642', delta: '+412', icon: Database, hint: '今日新增 · 13 源覆盖', deltaType: 'up' as const },
  { label: '稳定岗位', value: '86', delta: '+5', icon: GitBranch, hint: '五状态机：86 stable / 14 emerging', deltaType: 'up' as const },
  { label: '匹配任务', value: '23', delta: '8 待处理', icon: Activity, hint: '今日累计 · 平均耗时 1.8s', deltaType: 'pending' as const },
]

const MOCK_ACTIVITIES = [
  { id: 'a1', time: '14:32', type: 'graph', icon: Network, title: '图谱版本 v20260729 发布', desc: '新增 38 节点 · 142 边 · 涉及 7 个岗位状态流转', color: 'bg-state-stable' },
  { id: 'a2', time: '13:18', type: 'crawl', icon: Database, title: 'BOSS直聘 爬取完成', desc: '412 条 JD · 含 plaintext salaryDesc · 耗时 22min', color: 'bg-state-emerging' },
  { id: 'a3', time: '12:05', type: 'match', icon: Activity, title: '简历匹配任务完成', desc: 'user_007 · Top-10 推荐 · 综合得分 0.82', color: 'bg-state-stable' },
  { id: 'a4', time: '10:47', type: 'evolution', icon: TrendingUp, title: '检测到新兴技能', desc: 'LangChain z-score=2.34 · 自动转入 emerging 态', color: 'bg-state-emerging' },
  { id: 'a5', time: '09:15', type: 'review', icon: FileText, title: '岗位审核待处理', desc: '3 个 candidate 岗位等待 admin 审核确认', color: 'bg-state-candidate' },
  { id: 'a6', time: '05:00', type: 'graph', icon: Clock, title: '每日 ETL 调度完成', desc: '爬虫→清洗→去重→结构化→图谱增量同步 · 耗时 48min', color: 'bg-ink-faint' },
]

const QUICK_LINKS = [
  { to: '/graph', icon: Network, title: '能力图谱', desc: '2D 力导向图为主，3D 模式可选。四种视图切换：全景 / 技术栈 / 级别 / 岗位中心', badge: '41 节点' },
  { to: '/evolution', icon: TrendingUp, title: '演化看板', desc: '90 天滑动窗口追踪技能频次变化，Z-score 检测新兴/衰退技能', badge: '12 信号' },
  { to: '/resume-match', icon: Users, title: '简历匹配', desc: '上传简历 → LLM 解析 → 三维加权匹配 → 差距分析 → 学习路径推荐', badge: 'M4' },
  { to: '/admin/crawl', icon: Database, title: '爬取管理', desc: '手动触发 13 源采集任务 · 进度监控 · 历史记录回溯', badge: 'admin' },
]

/**
 * 仪表盘 — 系统总览，展示签名罗盘标记与关键指标
 *
 * 签名时刻：罗盘标记配合"系统就绪"状态，
 * 四向指针分别对应系统的四条主线（采集/图谱/匹配/演化）
 *
 * 数据来源：当前为 mock（MOCK_STATS / MOCK_ACTIVITIES），后端就绪后切换至
 * GET /api/v1/dashboard/summary + GET /api/v1/dashboard/recent-activity
 */
export function DashboardPage() {
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
          <div className="space-y-1 flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-semibold">智岗罗盘</h2>
              <Badge variant="outline" className="font-mono">v0.1.0</Badge>
              <Badge variant="outline" className="text-xs font-mono text-state-emerging border-state-emerging/30">
                M2 阶段 · 75%
              </Badge>
            </div>
            <p className="text-sm text-ink-muted">
              证据驱动的人才能力大脑 — 每条技能断言可追溯至原始 JD / 论文 / 社区信号
            </p>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-2 text-xs text-ink-faint">
              <span className="flex items-center gap-1">
                <span className="size-1.5 rounded-full bg-state-emerging" />
                采集管线就绪 · 13/14 源
              </span>
              <span className="flex items-center gap-1">
                <span className="size-1.5 rounded-full bg-state-stable" />
                图谱服务 · 1,248 节点
              </span>
              <span className="flex items-center gap-1">
                <span className="size-1.5 rounded-full bg-state-candidate" />
                匹配引擎 · M3 待接入
              </span>
              <span className="flex items-center gap-1">
                <span className="size-1.5 rounded-full bg-ink-faint" />
                演化算法 · M3 待接入
              </span>
            </div>
          </div>
          <div className="hidden md:flex flex-col items-end gap-1 text-right">
            <p className="text-xs text-ink-muted">最近 ETL</p>
            <p className="text-sm font-mono text-ink">2026-07-29 05:00</p>
            <p className="text-[10px] text-ink-faint">下次 2026-07-30 05:00</p>
          </div>
        </CardContent>
      </Card>

      {/* 关键指标卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {MOCK_STATS.map((stat) => {
          const Icon = stat.icon
          const deltaColor =
            stat.deltaType === 'up'
              ? 'text-state-emerging'
              : stat.deltaType === 'pending'
                ? 'text-state-candidate'
                : 'text-ink-muted'
          return (
            <Card key={stat.label}>
              <CardContent className="py-4">
                <div className="flex items-center justify-between mb-2">
                  <Icon className="size-4 text-ink-faint" />
                  <span className={`text-xs font-mono ${deltaColor}`}>{stat.delta}</span>
                </div>
                <div className="text-2xl font-semibold tracking-tight tabular-nums">{stat.value}</div>
                <div className="text-xs text-ink-muted mt-1">{stat.label}</div>
                <div className="text-[10px] text-ink-faint mt-0.5 truncate">{stat.hint}</div>
              </CardContent>
            </Card>
          )
        })}
      </div>

      {/* 最近活动 + 快捷入口 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* 最近活动流 */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-sm flex items-center justify-between">
              <span>最近活动</span>
              <span className="text-xs font-normal text-ink-faint">最近 24 小时</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {MOCK_ACTIVITIES.map((act) => {
              const Icon = act.icon
              return (
                <div key={act.id} className="flex items-start gap-3">
                  <div className="flex flex-col items-center gap-1 pt-0.5">
                    <span className={`size-2 rounded-full ${act.color}`} />
                    <div className="w-px flex-1 bg-border min-h-[24px]" />
                  </div>
                  <div className="flex-1 min-w-0 pb-3">
                    <div className="flex items-center gap-2">
                      <Icon className="size-3.5 text-ink-muted shrink-0" />
                      <p className="text-sm text-ink truncate">{act.title}</p>
                      <span className="text-[10px] font-mono text-ink-faint ml-auto shrink-0">{act.time}</span>
                    </div>
                    <p className="text-xs text-ink-muted mt-0.5">{act.desc}</p>
                  </div>
                </div>
              )
            })}
          </CardContent>
        </Card>

        {/* 快捷入口 */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">快捷入口</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {QUICK_LINKS.map((link) => {
              const Icon = link.icon
              return (
                <Link
                  key={link.to}
                  to={link.to}
                  className="block rounded-md border border-border p-3 transition-colors hover:bg-subtle hover:border-border-strong"
                >
                  <div className="flex items-center gap-2 mb-1">
                    <Icon className="size-3.5 text-ink-secondary" />
                    <span className="text-sm font-medium text-ink">{link.title}</span>
                    <Badge variant="outline" className="text-[10px] ml-auto font-mono">{link.badge}</Badge>
                  </div>
                  <p className="text-xs text-ink-muted leading-relaxed">{link.desc}</p>
                </Link>
              )
            })}
          </CardContent>
        </Card>
      </div>

      {/* 14 源数据底座 */}
      <Card className="mt-4">
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Database className="size-4" />
            <span>14 源数据底座</span>
            <Badge variant="outline" className="text-xs ml-auto font-mono">A/B/C 分级</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-2 text-xs">
            {[
              { name: 'BOSS直聘', level: 'A', count: 1842 },
              { name: '智联招聘', level: 'A', count: 1456 },
              { name: 'Monster', level: 'A', count: 982 },
              { name: 'Indeed', level: 'A', count: 1124 },
              { name: 'Glassdoor', level: 'B', count: 642 },
              { name: 'LinkedIn', level: 'B', count: 478 },
              { name: '脉脉', level: 'C', count: 312 },
              { name: 'GitHub', level: '信号', count: 248 },
              { name: 'Stack Overflow', level: '信号', count: 186 },
              { name: 'arXiv', level: '论文', count: 94 },
              { name: '中国大学MOOC', level: '课程', count: 168 },
              { name: 'Coursera', level: '课程', count: 142 },
              { name: 'edX', level: '课程', count: 76 },
            ].map((src) => (
              <div
                key={src.name}
                className="rounded-md border p-2"
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[10px] text-ink-faint">{src.level}</span>
                </div>
                <p className="text-ink mt-0.5 truncate">{src.name}</p>
                <p className="text-ink-muted font-mono tabular-nums">{src.count.toLocaleString()}</p>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </>
  )
}
