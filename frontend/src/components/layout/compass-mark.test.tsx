import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { CompassMark } from './compass-mark'

afterEach(cleanup)

describe('CompassMark', () => {
  it('默认尺寸为 sm（20px）', () => {
    const { container } = render(<CompassMark />)
    const svg = container.querySelector('svg')!
    expect(svg).toHaveAttribute('width', '20')
    expect(svg).toHaveAttribute('height', '20')
  })

  it('size=lg 输出 48px 且 spinning 附加 animate-spin', () => {
    const { container } = render(<CompassMark size="lg" spinning />)
    const svg = container.querySelector('svg')!
    expect(svg).toHaveAttribute('width', '48')
    // jsdom 中 SVGElement.className 是 SVGAnimatedString，需读 class 属性
    expect(svg.getAttribute('class')).toContain('animate-spin')
  })

  it('自定义 className 合并', () => {
    const { container } = render(<CompassMark className="custom-c" />)
    expect(container.querySelector('svg')!.getAttribute('class')).toContain('custom-c')
  })

  it('aria-hidden 隐藏装饰性图标', () => {
    const { container } = render(<CompassMark />)
    expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
  })
})
