/** 统一刷新按钮 — loading 态 + 图标唯一源。
 * 收敛 refresh / 重载 / 差距刷新 / 路径刷新 / 诊断刷新 等重复实现。
 * variant/size 可透传（下沉 >> 默认 outline+sm），文案默认「刷新」/「刷新中…」。 */
import { RefreshCw } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button, type ButtonProps } from '@/components/ui/button'

interface RefreshButtonProps extends Omit<ButtonProps, 'children'> {
  loading?: boolean
  children?: React.ReactNode
}

export function RefreshButton({
  loading,
  children,
  className,
  disabled,
  variant = 'outline',
  size = 'sm',
  ...rest
}: RefreshButtonProps) {
  return (
    <Button
      variant={variant}
      size={size}
      className={className}
      disabled={disabled || loading}
      {...rest}
    >
      <RefreshCw className={cn('mr-1', loading && 'animate-spin')} />
      {children ?? (loading ? '刷新中…' : '刷新')}
    </Button>
  )
}