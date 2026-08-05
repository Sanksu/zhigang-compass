import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { PagePlaceholder } from './page-placeholder'

afterEach(cleanup)

describe('PagePlaceholder', () => {
  it('渲染标题、描述与待开发提示', () => {
    render(<PagePlaceholder title="暂未开放" description="该模块开发中" />)
    expect(screen.getByRole('heading', { level: 1, name: '暂未开放' })).toBeInTheDocument()
    expect(screen.getByText('该模块开发中')).toBeInTheDocument()
    expect(screen.getByText('该页面待开发')).toBeInTheDocument()
  })

  it('specRef 存在时展示设计文档引用', () => {
    render(<PagePlaceholder title="X" description="Y" specRef="§7.1" />)
    expect(screen.getByText(/参见设计文档 §7.1/)).toBeInTheDocument()
  })

  it('无 specRef 不渲染引用', () => {
    render(<PagePlaceholder title="X" description="Y" />)
    expect(screen.queryByText(/参见设计文档/)).not.toBeInTheDocument()
  })
})
