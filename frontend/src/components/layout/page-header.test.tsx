import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { PageHeader } from './page-header'

afterEach(cleanup)

describe('PageHeader', () => {
  it('渲染标题与描述', () => {
    render(<PageHeader title="能力图谱" description="技能网络可视化" />)
    expect(screen.getByRole('heading', { level: 1, name: '能力图谱' })).toBeInTheDocument()
    expect(screen.getByText('技能网络可视化')).toBeInTheDocument()
  })

  it('无描述时不渲染描述节点', () => {
    render(<PageHeader title="仪表盘" />)
    expect(screen.queryByText('技能网络可视化')).not.toBeInTheDocument()
  })

  it('actions 渲染在右侧', () => {
    render(<PageHeader title="标题" actions={<button>导出</button>} />)
    expect(screen.getByRole('button', { name: '导出' })).toBeInTheDocument()
  })

  it('无 actions 时不渲染 action 容器', () => {
    const { container } = render(<PageHeader title="标题" />)
    expect(container.querySelector('button')).not.toBeInTheDocument()
  })
})
