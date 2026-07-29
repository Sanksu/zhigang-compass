import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from '@/lib/query-client'
import { AppRouter } from './router'

/**
 * 应用根 — 组装 Provider 链
 * TanStack Query → Router
 */
export function AppProviders() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppRouter />
    </QueryClientProvider>
  )
}
