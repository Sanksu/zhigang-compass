import { useSyncExternalStore } from 'react'

/**
 * 通用媒体查询 Hook（SSR 安全，服务端默认 fallback）
 *
 * 用法：
 *   const isDesktop = useMediaQuery('(min-width: 1024px)')
 *   const isMobile = useMediaQuery('(max-width: 639px)')
 */
export function useMediaQuery(query: string, fallback = false): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const mql = window.matchMedia(query)
      mql.addEventListener('change', onChange)
      return () => mql.removeEventListener('change', onChange)
    },
    () => window.matchMedia(query).matches,
    () => fallback,
  )
}

/** 常用断点快捷 Hook */
export function useIsDesktop(): boolean {
  return useMediaQuery('(min-width: 1024px)', true)
}

export function useIsTablet(): boolean {
  return useMediaQuery('(min-width: 768px) and (max-width: 1023px)', false)
}

export function useIsMobile(): boolean {
  return useMediaQuery('(max-width: 767px)', false)
}
