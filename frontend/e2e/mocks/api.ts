import { expect, type Page } from '@playwright/test'

/**
 * CI 可跑 E2E 的 API mock（浏览器层 page.route 拦截 /api/v1，无需 docker/后端）。
 *
 * 响应外壳与后端 ApiResponse 同构（lib/api.ts：code===0 才算业务成功）；
 * 有状态：POST /auth/login 后才放行鉴权端点（admin/简历/版本/审计），否则 401，
 * 使前端 skipAuthRedirect 的降级路径与真实后端一致。未匹配端点给 404 业务码，
 * 页面既有 Promise.allSettled 降级不阻塞。
 */
let authed = false

function ok<T>(data: T, code = 0) {
  return { code, msg: 'ok', data, trace_id: `mock-${Math.random().toString(16).slice(2, 10)}` }
}

/** 会话用户（admin，与 bootstrap 账号一致） */
const USER = {
  id: 'u-admin',
  username: 'admin',
  role: 'admin',
  email: 'admin@example.com',
  phone: null,
  bio: null,
}

/** 图谱 panorama fixture（契约 GraphViewData：2 岗位 + 3 技能 + 4 边） */
const GRAPH_VIEW = {
  view_type: 'panorama',
  stats: { nodes: 5, edges: 4, total_nodes: 5, total_edges: 4 },
  nodes: [
    // domain_id/domain_name：域聚合下钻契约字段（两岗位分属不同域）
    { id: 'pos-1', name: '前端开发工程师', type: 'position', status: 'stable', domain_id: 'dom_fe', domain_name: '前端开发工程师' },
    { id: 'pos-2', name: '算法工程师', type: 'position', status: 'stable', domain_id: 'dom_general', domain_name: '通用与其他岗位' },
    { id: 'sk-1', name: 'React', type: 'skill' },
    { id: 'sk-2', name: 'TypeScript', type: 'skill' },
    { id: 'sk-3', name: 'Python', type: 'skill' },
  ],
  edges: [
    { source: 'pos-1', target: 'sk-1', weight: 0.8, necessity: 'must' },
    { source: 'pos-1', target: 'sk-2', weight: 0.8, necessity: 'must' },
    { source: 'pos-2', target: 'sk-3', weight: 0.8, necessity: 'must' },
    { source: 'pos-2', target: 'sk-1', weight: 0.4, necessity: 'nice' },
  ],
}

const CRAWL_STATUS = {
  platforms: [{ id: 'zhilian', name: '智联招聘', level: 'A', total_count: 6267 }],
}

// M7 修复:fixture 对齐契约 required 字段(ResumeSummaryItem 需 file_name/skills/
// total_years;EvolutionVersion 需 node_added/node_removed/node_changed)。原"最小
// 渲染集"落后于契约,后端收紧校验时 mock 不报警。补全 required 避免双轨漂移。
const RESUME_LIST = {
  items: [
    {
      id: 'r-1',
      file_name: 'test-resume.pdf',
      skills: ['Python', 'TypeScript'],
      total_years: 5,
    },
  ],
  total: 1,
}

const EVOLUTION_VERSIONS = {
  items: [
    {
      version_id: 'v-001',
      created_at: '2026-08-19T08:00:00Z',
      change_summary: '测试版本快照',
      node_added: 3,
      node_removed: 1,
      node_changed: 5,
    },
  ],
  total: 1,
}

const AUDIT_LOGS = {
  items: [{ id: 'a-1', action: 'login', created_at: '2026-08-19T08:00:00Z', detail: { username: 'admin' } }],
  total: 1,
}

/** 安装拦截：后续所有测试请求先过 fixture 分发（须在 goto 前调用）；每次安装复位登录态。 */
export function installApiMock(page: Page): void {
  authed = false
  page.route('**/api/v1/**', async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const method = req.method()
    const p = url.pathname.replace(/^\/api\/v1/, '')

    const fulfill = (data: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify(ok(data)),
      })
    const unauthorized = () =>
      route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ code: 401, msg: '需要登录', data: null, trace_id: 'mock-none' }),
      })

    if (method === 'POST' && p === '/auth/login') {
      authed = true
      return fulfill({ access_token: 'mock-access', refresh_token: 'mock-refresh' })
    }
    if (method === 'POST' && p === '/auth/logout') {
      authed = false
      return fulfill({})
    }
    // 会话恢复：auth/refresh 模拟 httpOnly cookie——已登录（authed=true）则签发新
    // access_token（跨整页重载保持登录态），未登录返回空 → restoreSession 返回 null。
    // 真实后端靠 Set-Cookie，mock 用 authed 状态等价模拟（route 处理器存活于整页重载）。
    if (method === 'POST' && p === '/auth/refresh') {
      return authed ? fulfill({ access_token: 'mock-access' }) : fulfill({})
    }
    if (method === 'GET' && p === '/auth/me') return authed ? fulfill(USER) : unauthorized()
    // 四种视图同构，panorama 默认；图谱为公开路由
    // 注意仪表盘走 /graph/panorama，图谱页走 /graph/view/{type}——同一份 fixture
    if (method === 'GET' && (p.startsWith('/graph/view/') || p === '/graph/panorama')) {
      return fulfill(GRAPH_VIEW)
    }

    // 以下为鉴权端点（dashboard 均以 skipAuthRedirect 调用：未登录 401 静默降级）
    if (method === 'GET' && p === '/admin/crawl/status') return authed ? fulfill(CRAWL_STATUS) : unauthorized()
    if (method === 'GET' && p === '/resume/list') return authed ? fulfill(RESUME_LIST) : unauthorized()
    if (method === 'GET' && p === '/evolution/versions') return authed ? fulfill(EVOLUTION_VERSIONS) : unauthorized()
    if (method === 'GET' && p === '/admin/audit/logs') return authed ? fulfill(AUDIT_LOGS) : unauthorized()

    // 未匹配端点：404 业务码（页面既有降级路径处理，不阻塞）
    return route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ code: 404, msg: 'mock 未覆盖端点', data: null, trace_id: 'mock-none' }),
    })
  })
}

/** 在 mock 环境下完成 admin 登录（installApiMock 之后调用；登录前须先在登录页）。
 *  M4：mock 模式下 /api/v1 全部被拦截、凭据不参与校验——占位口令不得复用任何真实口令。 */
export async function mockLogin(page: Page): Promise<void> {
  await expect(page.getByRole('heading', { name: '登录' })).toBeVisible()
  await page.getByLabel('用户名').fill('admin')
  await page.getByLabel('密码').fill(process.env.E2E_ADMIN_PASSWORD ?? 'mock-password')
  await page.getByRole('button', { name: '登录' }).click()
  // 登录成功 → 跳转首页仪表盘（"真实 API 已接入"徽章，mock 数据已注入）
  await expect(page.getByText('真实 API 已接入')).toBeVisible({ timeout: 20_000 })
}
