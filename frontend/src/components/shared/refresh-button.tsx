/** 统一刷新按钮 — loading 态 + 图标唯一源。
 * 收敛 refresh / 重载 / 差距刷新 / 路径刷新 / 诊断刷新 等重复实现。 */
import { RefreshCw } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button, type ButtonProps } from '@/components/ui/button'

interface RefreshButtonProps extends Omit<ButtonProps, 'children'> {
  loading?: boolean
  children?: React.ReactNode
}

export function RefreshButton({ loading, children, className, disabled, ...rest }: RefreshButtonProps) {
  return (
    <Button
      variant="outline"
      size="sm"
      className={className}
      disabled={disabled || loading}
      {...rest}
    >
      <RefreshCw className={cn('mr-1', loading && 'animate-spin')} />
      {children ?? (loading ? '刷新中…' : '刷新')}
    </Button>
  )
}