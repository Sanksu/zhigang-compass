/**
 * HTTP 客户端测试（设计 §12.3 双 Token 续期 + §2.4.7 业务错误码）。
 *
 * 策略：替换 http.defaults.adapter 模拟网络层（不加 axios-mock-adapter 依赖），
 * 验证：请求拦截器附加 Bearer、响应拦截器业务错误 ApiError、401 自动续期重试、
 * skipAuthRedirect 降级、刷新失败登出回调、restoreSession 会话恢复。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import axios, { AxiosError, AxiosHeaders } from 'axios'
import {
  apiDelete,
  apiGet,
  apiPost,
  apiPut,
  ApiError,
  getAccessToken,
  http,
  registerAuthFailedHandler,
  restoreSession,
  setAccessToken,
  setRefreshToken,
} from './api'

/** 构造拦截器可识别的 401 AxiosError（config/response 齐全，headers 为 AxiosHeaders 供重试时拦截器 set 调用） */
function http401(url: string, extra: Record<string, unknown> = {}): AxiosError {
  return new AxiosError(
    'Unauthorized',
    undefined,
    { url, headers: new AxiosHeaders(), method: 'get', ...extra } as never,
    {},
    { status: 401, statusText: '', data: undefined, headers: {}, config: {} } as never,
  )
}

function ok(data: unknown) {
  return { data, status: 200, statusText: 'OK', headers: {}, config: {} }
}

function apiOk(data: unknown) {
  return { code: 0, msg: 'ok', data, trace_id: 't1' }
}

let adapter: ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.restoreAllMocks()
  setAccessToken(null)
  setRefreshToken(null)
  adapter = vi.fn()
  http.defaults.adapter = adapter as never
})

describe('api 便捷方法', () => {
  it('apiGet 成功解包 ApiResponse.data', async () => {
    adapter.mockResolvedValue(ok(apiOk({ id: 1 })))
    await expect(apiGet<{ id: number }>('/positions')).resolves.toEqual({ id: 1 })
  })

  it('apiPost/apiPut/apiDelete 透传返回 data', async () => {
    adapter.mockResolvedValue(ok(apiOk({ saved: true })))
    await expect(apiPost('/positions', { name: 'x' })).resolves.toEqual({ saved: true })
    await expect(apiPut('/positions/x', { name: 'y' })).resolves.toEqual({ saved: true })
    await expect(apiDelete('/positions/x')).resolves.toEqual({ saved: true })
    // 三个请求 URL 与方法正确
    const calls = adapter.mock.calls.map(([c]) => `${c.method}:${c.url}`)
    expect(calls).toEqual(['post:/positions', 'put:/positions/x', 'delete:/positions/x'])
  })

  it('请求拦截器附加 Bearer access_token', async () => {
    setAccessToken('abc')
    adapter.mockResolvedValue(ok(apiOk(null)))
    await apiGet('/positions')
    const headers = adapter.mock.calls[0][0].headers as AxiosHeaders
    expect(headers.get('Authorization')).toBe('Bearer abc')
  })

  it('无 token 时不附加 Authorization 头', async () => {
    adapter.mockResolvedValue(ok(apiOk(null)))
    await apiGet('/positions')
    const headers = adapter.mock.calls[0][0].headers as AxiosHeaders
    expect(headers.get('Authorization')).toBeUndefined()
  })
})

describe('GET 并发去重与缓存 key', () => {
  it('同 url+params 的并发请求合并为一次网络请求', async () => {
    adapter.mockResolvedValue(ok(apiOk({ v: 1 })))
    await Promise.all([
      apiGet('/x', { params: { page: 1 } }),
      apiGet('/x', { params: { page: 1 } }),
    ])
    expect(adapter).toHaveBeenCalledTimes(1)
  })

  it('params 序列化失败（toJSON 抛错）时不合并，各自独立发请求', async () => {
    adapter.mockResolvedValue(ok(apiOk({ v: 1 })))
    const unserializable = {
      page: 1,
      toJSON() {
        throw new TypeError('unserializable params')
      },
    }
    await Promise.all([
      apiGet('/x', { params: unserializable }),
      apiGet('/x', { params: unserializable }),
    ])
    expect(adapter).toHaveBeenCalledTimes(2)
  })
})

describe('响应拦截器：业务错误与网络错误', () => {
  it('code !== 0 抛 ApiError（携带 code/msg/traceId/status）', async () => {
    adapter.mockResolvedValue(ok({ code: 4001, msg: '参数错误', trace_id: 't2' }))
    const err = await apiGet('/x').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ code: 4001, message: '参数错误', traceId: 't2', status: 200 })
  })

  it('HTTP 错误且 body 无 code 时兜底为网络异常 ApiError', async () => {
    adapter.mockRejectedValue({ config: {}, response: { status: 500, data: undefined } })
    const err = await apiGet('/x').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ code: 500, status: 500 })
  })

  it('网络中断（无 response）抛 ApiError(code=-1)', async () => {
    adapter.mockRejectedValue(new Error('Network Error'))
    const err = await apiGet('/x').catch((e: unknown) => e)
    expect(err).toMatchObject({ code: -1 })
  })
})

describe('401 自动续期', () => {
  it('skipAuthRedirect 请求 401 时静默降级，不触发刷新', async () => {
    adapter.mockRejectedValue(http401('/x', { skipAuthRedirect: true }))
    const post = vi.spyOn(axios, 'post')
    const err = await apiGet('/x', { skipAuthRedirect: true }).catch((e: unknown) => e)
    expect(err).toMatchObject({ code: 401 })
    expect(post).not.toHaveBeenCalled()
  })

  it('刷新成功（内存 refresh_token）后重试原请求', async () => {
    setRefreshToken('rt-1')
    vi.spyOn(axios, 'post').mockResolvedValue(ok(apiOk({ access_token: 'new-at' })))
    adapter
      .mockRejectedValueOnce(http401('/positions'))
      .mockResolvedValueOnce(ok(apiOk([{ id: 1 }])))
    await expect(apiGet('/positions')).resolves.toEqual([{ id: 1 }])
    expect(getAccessToken()).toBe('new-at')
    // 原请求只重试一次（第二次成功）
    expect(adapter).toHaveBeenCalledTimes(2)
  })

  it('刷新失败时清空 token 并触发登出回调', async () => {
    setAccessToken('old-at')
    const onFail = vi.fn()
    registerAuthFailedHandler(onFail)
    vi.spyOn(axios, 'post').mockRejectedValue(new Error('refresh failed'))
    adapter.mockRejectedValueOnce(http401('/positions'))
    await expect(apiGet('/positions')).rejects.toBeInstanceOf(ApiError)
    expect(getAccessToken()).toBeNull()
    expect(onFail).toHaveBeenCalledTimes(1)
  })

  it('鉴权端点自身 401 不重试（避免循环）', async () => {
    setAccessToken('old-at')
    const onFail = vi.fn()
    registerAuthFailedHandler(onFail)
    const post = vi.spyOn(axios, 'post').mockResolvedValue(ok(apiOk({ access_token: 'x' })))
    adapter.mockRejectedValueOnce(http401('/auth/me'))
    await expect(http.get('/auth/me')).rejects.toBeInstanceOf(ApiError)
    expect(post).not.toHaveBeenCalled()
    expect(onFail).toHaveBeenCalledTimes(1)
  })
})

describe('restoreSession 会话恢复', () => {
  it('Cookie 续期 + /auth/me 成功时返回用户并写内存 token', async () => {
    adapter
      .mockResolvedValueOnce(ok(apiOk({ access_token: 'at-1' })))
      .mockResolvedValueOnce(ok(apiOk({ id: 'u1', username: 'zhang', role: 'admin' })))
    await expect(restoreSession()).resolves.toEqual({ id: 'u1', username: 'zhang', role: 'admin' })
    expect(getAccessToken()).toBe('at-1')
  })

  it('refresh 未返回 token（未登录）时返回 null', async () => {
    adapter.mockResolvedValueOnce(ok({ code: 0, msg: 'ok', data: {}, trace_id: 't' }))
    await expect(restoreSession()).resolves.toBeNull()
  })

  it('refresh 网络异常时静默返回 null', async () => {
    adapter.mockRejectedValue(new Error('Network Error'))
    await expect(restoreSession()).resolves.toBeNull()
  })
})
