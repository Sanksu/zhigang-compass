import { create } from 'zustand'

type Theme = 'light' | 'dark'

interface UIState {
  sidebarOpen: boolean
  theme: Theme
  toggleSidebar: () => void
  closeSidebar: () => void
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
}

function getInitialTheme(): Theme {
  if (typeof window === 'undefined') return 'light'
  const stored = localStorage.getItem('zhigang-theme')
  if (stored === 'light' || stored === 'dark') return stored
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function applyTheme(theme: Theme) {
  if (typeof window === 'undefined') return
  document.documentElement.classList.toggle('dark', theme === 'dark')
  localStorage.setItem('zhigang-theme', theme)
}

export const useUIStore = create<UIState>((set) => {
  const initial = getInitialTheme()
  applyTheme(initial)

  return {
    sidebarOpen: false,
    theme: initial,
    toggleSidebar: () => set((s) => {
      const next = !s.sidebarOpen
      // 打开时锁 body 滚动
      if (next) document.body.style.overflow = 'hidden'
      else document.body.style.overflow = ''
      return { sidebarOpen: next }
    }),
    closeSidebar: () => set(() => {
      document.body.style.overflow = ''
      return { sidebarOpen: false }
    }),
    setTheme: (theme) => {
      applyTheme(theme)
      set({ theme })
    },
    toggleTheme: () => set((s) => {
      const next = s.theme === 'light' ? 'dark' : 'light'
      applyTheme(next)
      return { theme: next }
    }),
  }
})