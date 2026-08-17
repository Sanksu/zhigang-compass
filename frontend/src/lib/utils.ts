import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * 合并 className — shadcn/ui 标准做法
 * clsx 处理条件类名，tailwind-merge 解决 Tailwind 类名冲突
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * HTML 转义 — ECharts tooltip formatter 返回的字符串经 innerHTML 渲染，
 * 节点名/技能名/课程标题等外部或用户可控数据必须先转义（M1 存储型 XSS 修复）。
 */
export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/**
 * 时间戳 → 中文本地化显示（无效/缺省值显示占位符）。
 */
export function formatDateTime(value: string | number | Date | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('zh-CN')
}


/** 暗色模式判定 — 跟随 documentElement 上的 .dark 类（08-17 收敛 4 处重复）。 */
export function isDark(): boolean {
  return document.documentElement.classList.contains('dark')
}
