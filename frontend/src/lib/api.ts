/**
 * HTTP 客户端 — 设计文档 §12.3 双 Token + §2.4.7 ApiResponse
 *
 * Token 策略（与后端 HTTPBearer 一致）：
 * - access_token：内存变量，请求拦截器附加 Authorization: Bearer
 * - refresh_token：内存变量，通过请求体发送（/auth/refresh）
 *
 * 401 自动续期：响应 401 时用 refresh_token 调 /auth/refresh，成功后重试原请求；
 * 并发 401 共享同一个刷新 Promise，避免多次刷新；刷新失败则登出并跳 /login。
 */
import axios, {
  AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from 'axios'
import type { components } from '@/types/api'

const BASE_URL = '/api/v1'

/** 双 Token 仅内存存留，不写入 localStorage / sessionStorage */
let _accessToken: string | null = null
let _refreshToken: string | null = null

export function setAccessToken(token: string | null) {
  _accessToken = token
}

export function getAccessToken(): string | null {
  return _accessToken
}

export function setRefreshToken(token: string | null) {
  _refreshToken = token
}

export function getRefreshToken(): string | null {
  return _refreshToken
}

/** 会话恢复后的用户信息（来自 /auth/me，契约 User） */
export type SessionUser = components['schemas']['User']

/**
 * 恢复会话（应用启动/刷新页面时调用）。
 *
 * 内存 token 刷新后已清空，此时利用后端写入的 httpOnly Cookie 中的
 * refresh_token 换新 access_token，再拉取 /auth/me 恢复用户态。
 * 未登录时静默返回 null（skipAuthRedirect 避免触发全局登出跳转）。
 */
export async function restoreSession(): Promise<SessionUser | null> {
  try {
    const res = await http.post<ApiResponse<{ access_token?: string }>>(
      '/auth/refresh',
      {},
      { skipAuthRedirect: true },
    )
    if (res.data.code !== 0 || !res.data.data?.access_token) return null
    _accessToken = res.data.data.access_token
    const me = await http.get<ApiResponse<SessionUser>>('/auth/me', { skipAuthRedirect: true })
    if (me.data.code !== 0 || !me.data.data) return null
    return me.data.data
  } catch {
    return null
  }
}

/** 业务错误（ApiResponse.code !== 0） */
export class ApiError extends Error {
  constructor(
    public code: number,
    message: string,
    public traceId?: string,
    public status?: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}



/** 错误消息提取：ApiError 取后端 msg，其余回退文案（08-17 收敛 36 处重复三元）。 */
export function errMsg(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.message : fallback
}
/** ApiResponse 外壳（契约 §2.4.7 单一事实源派生，第八轮 P2-28）：
 *  字段集跟随 openapi 生成类型，仅 data 泛型化（契约 data 为 unknown） */
export type ApiResponse<T = unknown> = Omit<components['schemas']['ApiResponse'], 'data'> & {
  data?: T
}

export const http: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  withCredentials: true,
  timeout: 30_000,
})

// 请求拦截器：附加 access_token（后端 deps.py 走 HTTPBearer）
http.interceptors.request.use((config) => {
  if (_accessToken) {
    config.headers.set('Authorization', `Bearer ${_accessToken}`)
  }
  return config
})

let _refreshing: Promise<string | null> | null = null
let _onAuthFailed: (() => void) | null = null

/** 注册鉴权失败回调（由 store/auth 注入，避免循环依赖） */
export function registerAuthFailedHandler(fn: () => void) {
  _onAuthFailed = fn
}

async function refreshAccessToken(): Promise<string | null> {
  if (_refreshing) return _refreshing
  _refreshing = (async () => {
    try {
      // 内存有 refresh_token 走请求体；页面刷新后内存清空，退化为依赖
      // httpOnly Cookie（withCredentials 自动携带）完成无感续期
      const body = _refreshToken ? { refresh_token: _refreshToken } : {}
      const res = await axios.post<ApiResponse<{ access_token?: string }>>(
        `${BASE_URL}/auth/refresh`,
        body,
        { withCredentials: true },
      )
      if (res.data.code === 0 && res.data.data?.access_token) {
        _accessToken = res.data.data.access_token
        return _accessToken
      }
      return null
    } catch {
      return null
    } finally {
      _refreshing = null
    }
  })()
  return _refreshing
}

// 可选请求标记：401 时静默降级，不触发全局登出（用于游客可访问页面的增强性数据）
// ttl：apiGet 缓存秒数（仅对系统级概览等低实时性接口显式启用；缺省的 GET 仅做并发单飞去重）
declare module 'axios' {
  interface AxiosRequestConfig {
    skipAuthRedirect?: boolean
    ttl?: number
  }
}

http.interceptors.response.use(
  (res) => {
    const body = res.data as Partial<ApiResponse>
    if (body && typeof body.code === 'number' && body.code !== 0) {
      throw new ApiError(body.code, body.msg ?? '请求失败', body.trace_id, res.status)
    }
    return res
  },
  async (error: AxiosError<ApiResponse>) => {
    const status = error.response?.status
    const orig = error.config as InternalAxiosRequestConfig & { _retried?: boolean; skipAuthRedirect?: boolean } | undefined

    if (status === 401 && orig) {
      // 可选请求：游客未登录时直接降级，不触发续期/登出
      if (orig.skipAuthRedirect) {
        return Promise.reject(new ApiError(401, '需要登录', undefined, 401))
      }
      if (!orig._retried && !orig.url?.includes('/auth/')) {
        orig._retried = true
        const ok = await refreshAccessToken()
        if (ok) return http.request(orig)
      }
      _accessToken = null
      _refreshToken = null
      _onAuthFailed?.()
    }

    const body = error.response?.data
    if (body && typeof body.code === 'number') {
      return Promise.reject(new ApiError(body.code, body.msg ?? error.message, body.trace_id, status))
    }
    return Promise.reject(
      new ApiError(status ?? -1, error.message || '网络异常', undefined, status),
    )
  },
)

/** 便捷方法 — 返回 data 字段（已剔除 ApiResponse 外壳） */
export async function apiGet<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const ttl = config?.ttl
  return (ttl !== undefined ? cachedGet(url, config, ttl) : dedupedGet(url, config)) as Promise<T>
}

/* ── GET 加载提速：并发单飞去重 + 可选短 TTL 缓存 ──────────────────────
 * 目标：减少重复网络往返，加速「切页返回」与「多组件同屏并发拉取」。
 * - 单飞去重（所有 GET 默认）：同一 url+params 的并发请求合并为一次网络请求；
 *   最终各调用方拿到同一结果，数据一致，仅省重复请求，不引入陈旧数据风险。
 * - TTL 缓存（显式 config.ttl>0 启用）：仅用于系统级低实时性概览接口（如首页
 *   指标卡）；实时页面（审核/爬取分页等）不传 ttl 则永不缓存。
 * 缓存以内存 Map 存储、未区分用户——前端为单用户会话，短 TTL 下安全。 */

const _inFlight = new Map<string, Promise<unknown>>()
const _getCache = new Map<string, { expires: number; data: unknown }>()

/** GET 缓存/去重 key：url + 序列化 params（query string 形式的 url 本身已含参数） */
function getKey(url: string, config?: AxiosRequestConfig): string {
  const params = config?.params
  if (!params) return url
  try {
    return `${url}\u0000${JSON.stringify(params)}`
  } catch {
    return url
  }
}

function dedupedGet<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const key = getKey(url, config)
  const prior = _inFlight.get(key)
  if (prior) return prior as Promise<T>
  const p = http
    .get<ApiResponse<T>>(url, config)
    .then((res) => res.data.data as T)
    .finally(() => _inFlight.delete(key))
  _inFlight.set(key, p)
  return p
}

function cachedGet<T>(url: string, config: AxiosRequestConfig | undefined, ttl: number): Promise<T> {
  const key = getKey(url, config)
  const hit = _getCache.get(key)
  if (hit && hit.expires > Date.now()) return Promise.resolve(hit.data as T)
  const prior = _inFlight.get(key)
  if (prior) return prior as Promise<T>
  const p = http
    .get<ApiResponse<T>>(url, config)
    .then((res) => res.data.data as T)
    .then((data) => {
      if (ttl > 0) _getCache.set(key, { expires: Date.now() + ttl * 1000, data })
      return data
    })
    .finally(() => _inFlight.delete(key))
  _inFlight.set(key, p)
  return p
}

export async function apiPost<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const res = await http.post<ApiResponse<T>>(url, body, config)
  return res.data.data as T
}

export async function apiPut<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const res = await http.put<ApiResponse<T>>(url, body, config)
  return res.data.data as T
}

export async function apiDelete<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const res = await http.delete<ApiResponse<T>>(url, config)
  return res.data.data as T
}
