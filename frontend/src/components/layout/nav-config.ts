import {
  LayoutDashboard,
  Network,
  FileText,
  TrendingUp,
  Radar,
  User,
  Shield,
  Users,
  Bot,
  CheckSquare,
  Cpu,
  Timer,
  Database,
  Workflow,
  ListChecks,
  type LucideIcon,
} from 'lucide-react'
import type { Role } from '@/lib/constants'

export interface NavItem {
  label: string
  to: string
  icon: LucideIcon
  /** 所需角色，guest 表示无需登录 */
  requireRole?: Role[]
}

/** 管理后台分组（08-16 层级化：管理 + 配置中心两组，可折叠） */
export interface AdminNavGroup {
  label: string
  items: NavItem[]
}

/**
 * 主导航 — 与设计文档 §10.2 路由表对齐
 * 分两组：主导航（业务功能）+ 管理后台分组
 */
export const mainNav: NavItem[] = [
  {
    label: '仪表盘',
    to: '/',
    icon: LayoutDashboard,
    },
  {
    label: '能力图谱',
    to: '/graph',
    icon: Network,
    },
  {
    label: '简历匹配',
    to: '/resume-match',
    icon: FileText,
    requireRole: ['user', 'admin'],
    },
  {
    label: '演化看板',
    to: '/evolution',
    icon: TrendingUp,
  },
  {
    label: '新岗位发现',
    to: '/discovery',
    icon: Radar,
  },
  {
    label: '个人中心',
    to: '/profile',
    icon: User,
    requireRole: ['user', 'admin'],
    },
]

/** 管理后台分组（08-16 层级化：管理 + 配置中心两组，可折叠；09-01 低频治理页收进总览统一入口） */
export const adminNavGroups: AdminNavGroup[] = [
  {
    label: '管理',
    items: [
      { label: '总览', to: '/admin', icon: Shield, requireRole: ['admin'] },
      { label: '账户管理', to: '/admin/users', icon: Users, requireRole: ['admin'] },
      { label: '爬取管理', to: '/admin/crawl', icon: Bot, requireRole: ['admin'] },
      { label: '岗位审核', to: '/admin/review', icon: CheckSquare, requireRole: ['admin'] },
    ],
  },
  {
    label: 'LLM 驱动',
    items: [
      { label: '决策与验收', to: '/admin/llm-decisions', icon: ListChecks, requireRole: ['admin'] },
    ],
  },
  {
    label: '配置中心',
    items: [
      { label: 'LLM 配置', to: '/admin/llm', icon: Cpu, requireRole: ['admin'] },
      { label: '任务与告警', to: '/admin/settings/tasks', icon: Timer, requireRole: ['admin'] },
      { label: '系统节流', to: '/admin/settings/system', icon: Database, requireRole: ['admin'] },
      { label: 'ETL 队列', to: '/admin/settings/etl', icon: Workflow, requireRole: ['admin'] },
    ],
  },
]
