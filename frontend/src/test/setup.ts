import '@testing-library/jest-dom/vitest'

// jsdom 缺 matchMedia（主题/响应式/暗色订阅逻辑依赖）；默认浅色偏好，测试可覆写
if (!window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string): MediaQueryList => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList,
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
