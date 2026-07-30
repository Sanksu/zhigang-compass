/**
 * HTTP 客户端 — 设计文档 §12.3 双 Token + §2.4.7 ApiResponse
 *
 * Token 策略：
 * - access_token：httpOnly Cookie（浏览器自动携带，JS 不可读）
 * - refresh_token：内存变量，通过 Authorization: Bearer 头发送
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

const BASE_URL = '/api/v1'

/** refresh_token 仅内存存留，不写入 localStorage / sessionStorage */
let _refreshToken: string | null = null

export function setRefreshToken(token: string | null) {
  _refreshToken = token
}

export function getRefreshToken(): string | null {
  return _refreshToken
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

export interface ApiResponse<T = unknown> {
  code: number
  msg: string
  data?: T
  trace_id: string
}

export const http: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  withCredentials: true,
  timeout: 30_000,
})

/** 给需鉴权请求注入 refresh_token（仅 /auth/refresh 自身需要） */
http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (_refreshToken && config.headers?.['X-Use-Refresh'] === '1') {
    config.headers.Authorization = `Bearer ${_refreshToken}`
    delete config.headers['X-Use-Refresh']
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
  if (!_refreshToken) return null
  if (_refreshing) return _refreshing
  _refreshing = (async () => {
    try {
      const res = await axios.post<ApiResponse<{ refresh_token?: string }>>(
        `${BASE_URL}/auth/refresh`,
        { refresh_token: _refreshToken },
        { withCredentials: true },
      )
      if (res.data.code === 0 && res.data.data?.refresh_token) {
        _refreshToken = res.data.data.refresh_token
        return _refreshToken
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
    const orig = error.config as InternalAxiosRequestConfig & { _retried?: boolean } | undefined

    if (status === 401 && orig && !orig._retried && !orig.url?.includes('/auth/')) {
      orig._retried = true
      const ok = await refreshAccessToken()
      if (ok) return http.request(orig)
    }

    if (status === 401) {
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
  const res = await http.get<ApiResponse<T>>(url, config)
  return res.data.data as T
}

export async function apiPost<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const res = await http.post<ApiResponse<T>>(url, body, config)
  return res.data.data as T
}

/** 调用 /auth/refresh 时显式标记使用 refresh_token */
export async function apiRefresh<T>(url: string, body?: unknown): Promise<T> {
  return apiPost<T>(url, body, { headers: { 'X-Use-Refresh': '1' } })
}
