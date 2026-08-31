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
      proxy: {
        '/api': {
          target: env.VITE_API_TARGET || 'http://localhost:8000',
          changeOrigin: true,
        },
      },
    },
    build: {
      // 手动分包：把稳定的大型第三方库拆成独立 chunk，浏览器长缓存命中 + 
      // echarts/three 仅在实际用到它们的懒加载页进入时才按需下载，缩首屏体积
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (!id.includes('node_modules')) return undefined
            if (/node_modules\/(echarts|zrender|zrender-to-canvas)\//.test(id)) return 'echarts'
            if (/node_modules\/(three|three-stdlib|troika|@react-three|camera-controls|@tweenjs)\//.test(id)) return 'three'
            if (/node_modules\/(axios|follow-redirects)\//.test(id)) return 'axios'
            if (/node_modules\/(react|react-dom|react-router|react-router-dom|scheduler|zustand|@remix-run)\//.test(id)) return 'react'
            return undefined
          },
        },
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: './src/test/setup.ts',
      css: true,
      // e2e/ 下的 Playwright spec 由 `pnpm e2e` 单独运行，vitest 不得收集
      exclude: [...configDefaults.exclude, 'e2e/**'],
      coverage: {
        provider: 'v8',
        reporter: ['text', 'text-summary', 'json-summary'],
        reportsDirectory: './coverage',
        // 核心子集门禁（设计文档 §17.1"核心 ≥ 80%"）：业务逻辑密集的 lib/store/
        // 守卫/UI 基础组件/layout；页面级交互由 E2E（Playwright）覆盖，echarts/
        // force-graph 可视化页不做单元测试覆盖（jsdom 无 canvas，收益低）。
        thresholds: {
          global: { lines: 80, functions: 80, branches: 70, statements: 80 },
        },
        all: true,
        include: [
          'src/lib/**',
          'src/store/**',
          'src/routes/guards.tsx',
          'src/components/ui/**',
          'src/components/resume/**',
          'src/components/layout/**',
        ],
        exclude: ['src/types/**', 'src/styles/**', 'src/test/**', '**/*.d.ts'],
      },
    },
  }
})
