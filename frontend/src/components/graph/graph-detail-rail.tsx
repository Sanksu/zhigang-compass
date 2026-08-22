import { useCallback, useEffect, useId, useRef, useSyncExternalStore, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'

type TabKey = 'detail' | 'analysis'
const DESKTOP_QUERY = '(min-width: 1024px)'

function subscribeDesktop(onChange: () => void) {
  const query = window.matchMedia(DESKTOP_QUERY)
  query.addEventListener('change', onChange)
  return () => query.removeEventListener('change', onChange)
}

function useDesktopLayout() {
  return useSyncExternalStore(subscribeDesktop, () => window.matchMedia(DESKTOP_QUERY).matches, () => false)
}

interface GraphDetailRailProps {
  rightTab: TabKey
  onRightTabChange: (value: TabKey) => void
  ready: boolean
  onClose: () => void
  children: ReactNode
  className?: string
}

export function GraphDetailTabs({
  rightTab,
  onRightTabChange,
  children,
}: Pick<GraphDetailRailProps, 'rightTab' | 'onRightTabChange' | 'children'>) {
  return (
    <Tabs value={rightTab} onValueChange={(value) => onRightTabChange(value as TabKey)} className="flex size-full flex-col">
      <TabsList className="mx-3 mt-3 grid w-auto grid-cols-2">
        <TabsTrigger value="detail" className="text-xs">节点详情</TabsTrigger>
        <TabsTrigger value="analysis" className="text-xs">算法分析</TabsTrigger>
      </TabsList>
      {children}
    </Tabs>
  )
}

export function GraphDetailRail({ rightTab, onRightTabChange, ready, onClose, children, className }: GraphDetailRailProps) {
  const isDesktop = useDesktopLayout()
  const titleId = useId()
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const restoreFocusRef = useRef<HTMLElement | null>(null)

  const handleKey = useCallback((event: KeyboardEvent) => {
    if (event.key !== 'Escape' || !ready) return
    event.preventDefault()
    event.stopPropagation()
    event.stopImmediatePropagation()
    onClose()
  }, [ready, onClose])

  useEffect(() => {
    document.addEventListener('keydown', handleKey, true)
    return () => document.removeEventListener('keydown', handleKey, true)
  }, [handleKey])

  useEffect(() => {
    if (!ready || isDesktop) return
    restoreFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    closeButtonRef.current?.focus()
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previousOverflow
      restoreFocusRef.current?.focus()
      restoreFocusRef.current = null
    }
  }, [isDesktop, ready])

  if (isDesktop) {
    return (
      <Card className={cn('flex h-full flex-col overflow-hidden lg:h-[640px]', className)}>
        <GraphDetailTabs rightTab={rightTab} onRightTabChange={onRightTabChange}>{children}</GraphDetailTabs>
      </Card>
    )
  }

  if (!ready) return null

  return (
    <div className="fixed inset-0 z-50 lg:hidden">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />
      <div role="dialog" aria-modal="true" aria-labelledby={titleId} className="absolute inset-x-0 bottom-0 flex max-h-[82vh] flex-col rounded-t-2xl border-t border-border bg-canvas shadow-2xl">
        <div className="flex justify-center pt-2"><div className="h-1 w-10 rounded-full bg-border" /></div>
        <div className="flex items-center justify-between border-b border-border/60 px-4 py-2">
          <span id={titleId} className="text-xs font-medium text-ink-muted">详情</span>
          <Button ref={closeButtonRef} size="sm" variant="ghost" className="h-7 w-7 p-0" onClick={onClose} aria-label="关闭详情抽屉"><X className="size-4" /></Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <GraphDetailTabs rightTab={rightTab} onRightTabChange={onRightTabChange}>{children}</GraphDetailTabs>
        </div>
      </div>
    </div>
  )
}
