import '@testing-library/jest-dom/vitest'

// jsdom 缺 matchMedia（主题/响应式/暗色订阅逻辑依赖）；默认浅色偏好，测试可覆写
// 模拟桌面视口宽度 1280px，使 md/lg/xl 等 Tailwind 断点在测试中均命中（组件测试默认桌面布局）
if (!window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string): MediaQueryList => {
      // 解析 min-width / max-width，模拟 1280px 视口宽度
      let matches = false
      const minWidthMatch = query.match(/\(min-width:\s*(\d+)px\)/)
      const maxWidthMatch = query.match(/\(max-width:\s*(\d+)px\)/)
      const viewportWidth = 1280

      if (minWidthMatch && maxWidthMatch) {
        const minW = Number(minWidthMatch[1])
        const maxW = Number(maxWidthMatch[1])
        matches = viewportWidth >= minW && viewportWidth <= maxW
      } else if (minWidthMatch) {
        matches = viewportWidth >= Number(minWidthMatch[1])
      } else if (maxWidthMatch) {
        matches = viewportWidth <= Number(maxWidthMatch[1])
      } else if (query.includes('prefers-color-scheme: dark')) {
        matches = false // 默认浅色主题
      } else if (query.includes('prefers-reduced-motion')) {
        matches = false
      }
      return {
        matches,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      } as MediaQueryList
    },
  })
}

// jsdom 缺 ResizeObserver（Radix Select/Dialog 等测量逻辑依赖）；
// 用 typeof 而非 `in` 判断——lib.dom 已声明该类型，`in` 会把 window 收窄为 never
if (typeof ResizeObserver === 'undefined') {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  window.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver
}

// jsdom 缺 PointerEvent（Radix Select 触发器 pointerdown 展开、选项 pointerup
// 选中所依赖）；补 MouseEvent 别名 + pointer capture 桩，使 userEvent 可驱动
// Radix Select 交互（原生 select 迁 ui/select 后组件测试依赖）
if (typeof window.PointerEvent === 'undefined') {
  window.PointerEvent = window.MouseEvent as unknown as typeof PointerEvent
}
window.HTMLElement.prototype.scrollIntoView = () => {}
window.HTMLElement.prototype.hasPointerCapture = () => false
window.HTMLElement.prototype.releasePointerCapture = () => {}

// Node 26 的原生 localStorage（实验性，需 --localstorage-file）与 jsdom 缺省
// url 配置下均不可用（ui.ts 主题持久化与多组件测试依赖）；统一注入内存实现
let hasLocalStorage: boolean
try {
  hasLocalStorage = typeof window.localStorage?.getItem === 'function'
} catch {
  hasLocalStorage = false
}
if (!hasLocalStorage) {
  const store = new Map<string, string>()
  Object.defineProperty(window, 'localStorage', {
    writable: true,
    value: {
      get length() {
        return store.size
      },
      clear: () => store.clear(),
      getItem: (k: string) => store.get(k) ?? null,
      key: (i: number) => [...store.keys()][i] ?? null,
      removeItem: (k: string) => {
        store.delete(k)
      },
      setItem: (k: string, v: string) => {
        store.set(k, String(v))
      },
    } as Storage,
  })
}
