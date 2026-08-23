# frontend

智岗罗盘 Web 前端，基于 Vite + React 19 + TypeScript，采用现代简约风格（参考 Vercel）。

## 技术栈

- **构建**：Vite 6 + @vitejs/plugin-react-swc
- **框架**：React 19 + React Router 7（懒加载 + 路由守卫）
- **样式**：Tailwind CSS v4（设计令牌见 [src/styles/globals.css](src/styles/globals.css)）
- **状态**：Zustand（auth）+ TanStack Query（服务端数据）
- **HTTP**：Axios（双 Token + 静默续期，见 [src/lib/api.ts](src/lib/api.ts)）
- **可视化**：ECharts 5（2D 力导向图为主）
- **类型生成**：openapi-typescript（契约源 `../contracts/openapi.yaml`）

## 目录结构

```
src/
├── app/              # 应用入口：providers（Provider 链）+ router（路由表）
├── components/
│   ├── layout/       # 应用外壳：AppShell / TopNav / Sidebar / CompassMark
│   └── ui/           # 基础组件：Button / Card / Input / Badge
├── routes/           # 页面级组件 + guards（AuthGuard / GuestGuard）
├── store/            # Zustand store（auth）
├── lib/              # api 客户端、query-client、constants、utils
├── styles/           # globals.css（设计令牌 + 基础样式）
└── types/            # api.d.ts（由 OpenAPI 自动生成，勿手改）
```

## 快速开始

```bash
# 1. 安装依赖（需 pnpm ≥ 9）
pnpm install

# 2. 复制环境变量（可选，默认指向 http://localhost:8000）
cp .env.example .env.local

# 3. 启动开发服务器（http://localhost:5173）
pnpm dev

# 4. 生成 API 类型（后端 OpenAPI 有变更时执行）
pnpm gen:api

# 5. 类型检查 / 构建 / 预览
pnpm typecheck
pnpm build
pnpm preview
```

## 环境变量

见 [.env.example](.env.example)。开发态通过 Vite proxy 转发 `/api` 到后端，前端始终使用相对路径，无需在运行时配置 `baseURL`。

## 设计约束

- **岗位六状态色**仅用于状态指示，不作装饰（active/candidate/emerging/stable/declining/archived，图谱与详情面板统一取 globals.css 设计令牌）
- **图谱视图**后端过滤，2D 力导向图为主，3D 为可选模式
- **匹配结果页**需标注"基于 [日期] 版本快照计算"
- **路由守卫**：`/resume-match` 需 `user`/`admin` 角色，`/admin/*` 需 `admin` 角色

## 代码风格

- 路径别名 `@/` 指向 `src/`
- 页面组件采用懒加载，统一 Suspense fallback 为 `CompassMark spinning`
- Token 策略：access_token 走 httpOnly Cookie，refresh_token 存内存（不持久化）
