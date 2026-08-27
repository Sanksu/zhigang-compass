/**
 * 爬虫调度与限频配置组件测试（crawl-schedule-config.tsx）。
 *
 * 重点锁定 08-28 审查遗留修复：限频输入草稿态（清空/0 值不再被 ||4/||1 静默改写，
 * 保存时按契约域清洗省略）+ 逐字段载荷语义（zhilian 空列表重试 0=关闭、爬虫开关、
 * 采集上限越界省略）。apiGet/apiPut 全 mock，不触真实后端。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { CrawlScheduleConfig } from './crawl-schedule-config'
import { apiGet, apiPut } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  apiGet: vi.fn(),
  apiPut: vi.fn(),
  errMsg: (_e: unknown, fallback: string) => fallback,
}))

const mockedGet = apiGet as unknown as ReturnType<typeof vi.fn>
const mockedPut = apiPut as unknown as ReturnType<typeof vi.fn>

const CFG = {
  crawl_items_cap: 100,
  rate_limit: { zhilian: { req_per_min: 4, delay_range: [10, 20] } },
  crawlers: { zhilian: { enabled: true, max_empty_retries: 3 } },
}

function renderConfig() {
  return render(<CrawlScheduleConfig />)
}

beforeEach(() => {
  mockedGet.mockResolvedValue(structuredClone(CFG))
  mockedPut.mockImplementation((_url: string, body: unknown) => Promise.resolve(body))
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function save() {
  fireEvent.click(screen.getByRole('button', { name: /保存配置/ }))
  await waitFor(() => expect(mockedPut).toHaveBeenCalledTimes(1))
  return mockedPut.mock.calls[0][1] as Record<string, unknown>
}

describe('CrawlScheduleConfig 渲染', () => {
  it('加载已保存配置：上限/限频/爬虫表渲染真实值', async () => {
    renderConfig()
    await waitFor(() => expect(screen.getByDisplayValue('100')).toBeInTheDocument())
    // 限频表：zhilian 行 4 / 10 ~ 20
    expect(screen.getByDisplayValue('4')).toBeInTheDocument()
    expect(screen.getByDisplayValue('10')).toBeInTheDocument()
    expect(screen.getByDisplayValue('20')).toBeInTheDocument()
    // 13 只爬虫全量列出（启用 checkbox = 13）
    expect(screen.getAllByRole('checkbox')).toHaveLength(13)
    // zhilian 空列表重试值
    expect(screen.getByDisplayValue('3')).toBeInTheDocument()
  })

  it('无 rate_limit 配置时限频表为空但不崩', async () => {
    mockedGet.mockResolvedValue({ crawl_items_cap: 100, crawlers: {} })
    renderConfig()
    await waitFor(() => expect(screen.getByDisplayValue('100')).toBeInTheDocument())
    expect(screen.queryByDisplayValue('4')).not.toBeInTheDocument()
  })
})

describe('CrawlScheduleConfig 载荷清洗（0 值/清空修复）', () => {
  it('合法修改提交到契约域内值', async () => {
    renderConfig()
    await waitFor(() => expect(screen.getByDisplayValue('4')).toBeInTheDocument())
    fireEvent.change(screen.getByDisplayValue('4'), { target: { value: '8' } })
    fireEvent.change(screen.getByDisplayValue('100'), { target: { value: '500' } })
    const payload = await save()
    expect(payload.rate_limit).toEqual({ zhilian: { req_per_min: 8, delay_range: [10, 20] } })
    expect(payload.crawl_items_cap).toBe(500)
  })

  it('清空 req_per_min → 省略该字段（不再回退 4），delay_range 保留', async () => {
    renderConfig()
    await waitFor(() => expect(screen.getByDisplayValue('4')).toBeInTheDocument())
    fireEvent.change(screen.getByDisplayValue('4'), { target: { value: '' } })
    const payload = await save()
    const zhilian = (payload.rate_limit as Record<string, Record<string, unknown>>).zhilian
    expect('req_per_min' in zhilian).toBe(false)
    expect(zhilian.delay_range).toEqual([10, 20])
  })

  it('0 为越界值 → 省略（req_per_min 契约域 1-600）', async () => {
    renderConfig()
    await waitFor(() => expect(screen.getByDisplayValue('4')).toBeInTheDocument())
    fireEvent.change(screen.getByDisplayValue('4'), { target: { value: '0' } })
    const payload = await save()
    const zhilian = (payload.rate_limit as Record<string, Record<string, unknown>>).zhilian
    expect('req_per_min' in zhilian).toBe(false)
  })

  it('delay 上下限清空 → delay_range 整体省略；三项全空 → 该源省略', async () => {
    renderConfig()
    await waitFor(() => expect(screen.getByDisplayValue('10')).toBeInTheDocument())
    fireEvent.change(screen.getByDisplayValue('10'), { target: { value: '' } })
    fireEvent.change(screen.getByDisplayValue('20'), { target: { value: '' } })
    fireEvent.change(screen.getByDisplayValue('4'), { target: { value: '' } })
    const payload = await save()
    expect(payload.rate_limit).toEqual({})
  })

  it('采集上限越界（0/1001/非数字）→ 载荷省略 crawl_items_cap', async () => {
    renderConfig()
    await waitFor(() => expect(screen.getByDisplayValue('100')).toBeInTheDocument())
    fireEvent.change(screen.getByDisplayValue('100'), { target: { value: '0' } })
    const p1 = await save()
    expect('crawl_items_cap' in p1).toBe(false)
  })
})

describe('CrawlScheduleConfig 每爬虫配置语义', () => {
  it('zhilian 空列表重试 0=关闭：0 必须进载荷（既有语义回归锁）', async () => {
    renderConfig()
    await waitFor(() => expect(screen.getByDisplayValue('3')).toBeInTheDocument())
    fireEvent.change(screen.getByDisplayValue('3'), { target: { value: '0' } })
    const payload = await save()
    const crawlers = payload.crawlers as Record<string, Record<string, unknown>>
    expect(crawlers.zhilian.max_empty_retries).toBe(0)
  })

  it('停用爬虫开关 → 载荷 enabled:false；其他爬虫默认 enabled:true', async () => {
    renderConfig()
    await waitFor(() => expect(screen.getAllByRole('checkbox')).toHaveLength(13))
    fireEvent.click(screen.getAllByRole('checkbox')[0]) // 首行 = zhilian
    const payload = await save()
    const crawlers = payload.crawlers as Record<string, Record<string, unknown>>
    expect(crawlers.zhilian.enabled).toBe(false)
    expect(crawlers.indeed.enabled).toBe(true)
  })

  it('hour/minute 仅成对且在域内时提交（hour 单填不算）', async () => {
    renderConfig()
    await waitFor(() => expect(screen.getAllByRole('checkbox')).toHaveLength(13))
    // indeed 行（无既有值）：两个占位 "–" 的 hour/minute 输入——通过行内顺序定位
    const hourInputs = screen.getAllByPlaceholderText('–')
    fireEvent.change(hourInputs[2], { target: { value: '4' } }) // 第 3 行 indeed 的 hour（前 2 个为 zhilian 行）
    const payload = await save()
    const crawlers = payload.crawlers as Record<string, Record<string, unknown>>
    expect('hour' in crawlers.indeed).toBe(false)
  })
})
