import { QueryClient } from '@tanstack/react-query'

/**
 * TanStack Query 客户端 — 单例
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,        // 30s 内不重新请求
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})
