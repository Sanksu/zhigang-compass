/**
 * 路由级错误兜底（第八轮 P1-12）— createBrowserRouter 顶层 errorElement
 *
 * 任何渲染期/loder 异常不再打穿成 React Router 默认英文错误页：
 * 展示中文文案 + 错误摘要，「重试」恢复当前路由，「返回首页」navigate('/')。
 * 样式与 router.tsx NotFound 对齐（居中列 + CompassMark + ink 灰阶）。
 */
import { useNavigate, useRouteError } from 'react-router'
import { Button } from '@/components/ui/button'
import { CompassMark } from '@/components/layout/compass-mark'

/** 错误摘要提取：Error 取 message，路由 ErrorResponse 取 status+statusText，其余 String 化 */
function errorSummary(error: unknown): string {
  if (error instanceof Error) return error.message || error.name
  if (typeof error === 'object' && error !== null && 'status' in error && 'statusText' in error) {
    const { status, statusText } = error as { status: number; statusText: string }
    return `${status} ${statusText}`.trim()
  }
  return String(error)
}

/** 兜底 UI（受控入参，便于单测；错误摘要非空时展示） */
export function ErrorFallback({ error }: { error: unknown }) {
  const navigate = useNavigate()
  const summary = errorSummary(error)
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-3 px-6">
      <CompassMark size="lg" spinning />
      <p className="text-sm font-medium text-ink">页面出现了一点问题</p>
      <p className="text-xs text-ink-muted">请重试或返回首页；若持续出现，请联系管理员。</p>
      {summary && (
        <p className="max-w-xl break-all rounded-md border border-border bg-subtle px-3 py-2 font-mono text-xs text-ink-faint">
          {summary}
        </p>
      )}
      <div className="mt-2 flex items-center gap-2">
        <Button size="sm" onClick={() => navigate(0)}>重试</Button>
        <Button size="sm" variant="outline" onClick={() => navigate('/')}>返回首页</Button>
      </div>
    </div>
  )
}

/** 路由 errorElement 入口：从 router 上下文取错误后渲染兜底 UI */
export function RouteErrorFallback() {
  const error = useRouteError()
  return <ErrorFallback error={error} />
}
