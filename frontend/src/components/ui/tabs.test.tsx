import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Tabs, TabsContent, TabsList, TabsTrigger } from './tabs'

afterEach(cleanup)

describe('Tabs', () => {
  it('默认 tab 激活并显示对应内容', () => {
    render(
      <Tabs defaultValue="skill">
        <TabsList>
          <TabsTrigger value="skill">技能</TabsTrigger>
          <TabsTrigger value="edu">教育</TabsTrigger>
        </TabsList>
        <TabsContent value="skill">技能内容</TabsContent>
        <TabsContent value="edu">教育内容</TabsContent>
      </Tabs>,
    )
    expect(screen.getByText('技能内容')).toBeInTheDocument()
    expect(screen.queryByText('教育内容')).not.toBeInTheDocument()
  })

  it('点击切换 tab', async () => {
    const onValueChange = vi.fn()
    const user = userEvent.setup()
    render(
      <Tabs defaultValue="skill" onValueChange={onValueChange}>
        <TabsList>
          <TabsTrigger value="skill">技能</TabsTrigger>
          <TabsTrigger value="edu">教育</TabsTrigger>
        </TabsList>
        <TabsContent value="skill">技能内容</TabsContent>
        <TabsContent value="edu">教育内容</TabsContent>
      </Tabs>,
    )
    // Radix Tabs 在 mousedown 时激活，须用 userEvent 触发完整事件序列
    await user.click(screen.getByRole('tab', { name: '教育' }))
    expect(onValueChange).toHaveBeenCalledWith('edu')
    expect(screen.getByText('教育内容')).toBeInTheDocument()
  })
})
