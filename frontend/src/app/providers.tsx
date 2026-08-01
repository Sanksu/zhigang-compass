import { useEffect } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from '@/lib/query-client'
import { useAuthStore } from '@/store/auth'
import { AppRouter } from './router'

/**
 * 应用根 — 组装 Provider 链
 * TanStack Query → Router
 *
 * 挂载时触发会话恢复：若后端 httpOnly Cookie 含有效 refresh_token，
 * 则静默续期并恢复用户态，实现刷新页面后登录态不丢失。
 */
export function AppProviders() {
  useEffect(() => {
    void useAuthStore.getState().initialize()
  }, [])

  return (
    <QueryClientProvider client={queryClient}>
      <AppRouter />
    </QueryClientProvider>
  )
}
