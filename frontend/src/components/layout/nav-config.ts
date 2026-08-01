import {
  LayoutDashboard,
  Network,
  FileText,
  TrendingUp,
  User,
  Shield,
  Users,
  Bot,
  CheckSquare,
  Cpu,
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

/**
 * 主导航 — 与设计文档 §10.2 路由表对齐
 * 分两组：主导航（业务功能）+ 管理后台
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
    label: '个人中心',
    to: '/profile',
    icon: User,
    requireRole: ['user', 'admin'],
    },
]

export const adminNav: NavItem[] = [
  {
    label: '管理后台',
    to: '/admin',
    icon: Shield,
    requireRole: ['admin'],
  },
  {
    label: '账户管理',
    to: '/admin/users',
    icon: Users,
    requireRole: ['admin'],
  },
  {
    label: '爬取管理',
    to: '/admin/crawl',
    icon: Bot,
    requireRole: ['admin'],
  },
  {
    label: '岗位审核',
    to: '/admin/review',
    icon: CheckSquare,
    requireRole: ['admin'],
  },
  {
    label: 'LLM 配置',
    to: '/admin/llm',
    icon: Cpu,
    requireRole: ['admin'],
  },
]
