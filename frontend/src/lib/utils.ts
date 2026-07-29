import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * 合并 className — shadcn/ui 标准做法
 * clsx 处理条件类名，tailwind-merge 解决 Tailwind 类名冲突
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
