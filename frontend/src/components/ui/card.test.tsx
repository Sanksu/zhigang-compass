import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from './card'

afterEach(cleanup)

describe('Card', () => {
  it('渲染 Card 各子组件', () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle>标题</CardTitle>
          <CardDescription>描述</CardDescription>
        </CardHeader>
        <CardContent>内容</CardContent>
        <CardFooter>页脚</CardFooter>
      </Card>,
    )
    expect(screen.getByText('标题')).toBeInTheDocument()
    expect(screen.getByText('描述')).toBeInTheDocument()
    expect(screen.getByText('内容')).toBeInTheDocument()
    expect(screen.getByText('页脚')).toBeInTheDocument()
  })

  it('Card 自定义 className 合并', () => {
    render(<Card className="shadow-lg">x</Card>)
    expect(screen.getByText('x').className).toContain('shadow-lg')
  })

  it('CardHeader 自定义 className', () => {
    render(<CardHeader className="custom-h">h</CardHeader>)
    expect(screen.getByText('h').className).toContain('custom-h')
  })

  it('CardTitle 渲染为 h3', () => {
    render(<CardTitle>t</CardTitle>)
    expect(screen.getByRole('heading', { level: 3 })).toBeInTheDocument()
  })
})
