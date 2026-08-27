import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { Network } from 'lucide-react'
import { MetricCard } from './metric-card'

afterEach(cleanup)

describe('MetricCard', () => {
  it('渲染 label 与 value（字符串走 font-mono）', () => {
    render(<MetricCard data={{ label: '图谱版本', value: 'v17' }} />)
    expect(screen.getByText('图谱版本')).toBeInTheDocument()
    expect(screen.getByText('v17')).toBeInTheDocument()
  })

  it('数字 value 千分位；数字 delta 加 +/- 前缀', () => {
    render(<MetricCard data={{ label: '节点', value: 1200, delta: 5 }} />)
    expect(screen.getByText('1,200')).toBeInTheDocument()
    expect(screen.getByText('+5')).toBeInTheDocument()
  })

  it('字符串 delta 原样展示（如「5 边」）', () => {
    render(<MetricCard data={{ label: '图谱节点', value: '7', delta: '5 边' }} />)
    expect(screen.getByText('5 边')).toBeInTheDocument()
  })

  it('bar=true 渲染底部色条', () => {
    const { container } = render(<MetricCard data={{ label: '信号', value: 3, delta: 0, bar: true }} />)
    expect(container.querySelector('.h-0\\.5')).not.toBeNull()
  })

  it('icon 渲染在 label 旁', () => {
    render(<MetricCard data={{ label: '采集', value: 1, icon: Network }} />)
    expect(screen.getByText('采集')).toBeInTheDocument()
  })
})