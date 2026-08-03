import { configDefaults, defineConfig } from 'vitest/config'
import { loadEnv } from 'vite'
import react from '@vitejs/plugin-react-swc'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')

  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      port: 5173,
      // 同时监听 IPv4(127.0.0.1) 与 IPv6(::1)：默认仅绑 localhost 时 127.0.0.1 会被拒连
      host: true,
      proxy: {
        '/api': {
          // api 监听 127.0.0.1:8000；target 用 127.0.0.1 避免 localhost 解析到 IPv6 后代理失败
          target: env.VITE_API_TARGET || 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: './src/test/setup.ts',
      css: true,
      // e2e/ 下的 Playwright spec 由 `pnpm e2e` 单独运行，vitest 不得收集
      exclude: [...configDefaults.exclude, 'e2e/**'],
    },
  }
})
