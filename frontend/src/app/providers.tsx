import { useEffect } from 'react'
import { useAuthStore } from '@/store/auth'
import { AppRouter } from './router'

/**
 * 应用根 — 组装 Provider 链
 *
 * 08-14 审查：移除 TanStack Query（全项目零 useQuery/useMutation 死依赖，
 * 所有请求走手写 apiGet + cancelled 标志 + loading 状态）。
 * 挂载时触发会话恢复：若后端 httpOnly Cookie 含有效 refresh_token，
 * 则静默续期并恢复用户态，实现刷新页面后登录态不丢失。
 */
export function AppProviders() {
  useEffect(() => {
    void useAuthStore.getState().initialize()
  }, [])

  return <AppRouter />
}
