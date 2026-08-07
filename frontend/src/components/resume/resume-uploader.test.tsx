import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ResumeUploader } from './resume-uploader'

afterEach(cleanup)

function makeFile(name: string, type: string, size = 1024): File {
  return new File(['x'.repeat(size)], name, { type })
}

/** 获取隐藏的 file input 并注入文件（jsdom 的 files 属性只读，需 defineProperty） */
function pickFile(container: HTMLElement, file: File) {
  const input = container.querySelector('input[type="file"]')!
  Object.defineProperty(input, 'files', { value: [file], configurable: true })
  fireEvent.change(input)
  return input
}

describe('ResumeUploader 文件校验', () => {
  it('选择合法 PDF 触发 onFileSelected 并展示文件名与大小', () => {
    const onFileSelected = vi.fn()
    const { container } = render(<ResumeUploader onFileSelected={onFileSelected} />)
    pickFile(container, makeFile('resume.pdf', 'application/pdf'))
    expect(onFileSelected).toHaveBeenCalledTimes(1)
    expect(screen.getByText('resume.pdf')).toBeInTheDocument()
    expect(screen.getByText('1.0 KB')).toBeInTheDocument()
  })

  it('Word 文档（.docx）也接受', () => {
    const onFileSelected = vi.fn()
    const { container } = render(<ResumeUploader onFileSelected={onFileSelected} />)
    pickFile(container, makeFile('resume.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'))
    expect(onFileSelected).toHaveBeenCalledTimes(1)
  })

  it('非法扩展名拒绝并提示', () => {
    const onFileSelected = vi.fn()
    const { container } = render(<ResumeUploader onFileSelected={onFileSelected} />)
    pickFile(container, makeFile('resume.txt', 'text/plain'))
    expect(onFileSelected).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('不支持的文件类型')
  })

  // 10MB 大文件构造在 vitest 并行 worker 负载下耗时波动，放宽超时避免偶发失败
  it('超过 10MB 拒绝并提示', () => {
    const onFileSelected = vi.fn()
    const { container } = render(<ResumeUploader onFileSelected={onFileSelected} />)
    pickFile(container, makeFile('big.pdf', 'application/pdf', 10 * 1024 * 1024 + 1))
    expect(onFileSelected).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('文件超过 10MB 限制')
  }, 15000)

  it('拖拽投放合法文件同样生效', () => {
    const onFileSelected = vi.fn()
    render(<ResumeUploader onFileSelected={onFileSelected} />)
    const zone = screen.getByRole('button', { name: /拖拽简历到此处/ })
    fireEvent.drop(zone, { dataTransfer: { files: [makeFile('drag.png', 'image/png')] } })
    expect(onFileSelected).toHaveBeenCalledTimes(1)
  })

  it('移除按钮清空所选文件', () => {
    const { container } = render(<ResumeUploader />)
    pickFile(container, makeFile('resume.pdf', 'application/pdf'))
    fireEvent.click(screen.getByRole('button', { name: '移除' }))
    expect(screen.queryByText('resume.pdf')).not.toBeInTheDocument()
  })

  it('loading 时不显示移除按钮', () => {
    const { container } = render(<ResumeUploader loading />)
    pickFile(container, makeFile('resume.pdf', 'application/pdf'))
    expect(screen.queryByRole('button', { name: '移除' })).not.toBeInTheDocument()
  })

  it('键盘 Enter 触发文件选择（点击隐藏 input 不报错）', () => {
    render(<ResumeUploader />)
    const zone = screen.getByRole('button', { name: /拖拽简历到此处/ })
    fireEvent.keyDown(zone, { key: 'Enter' })
    fireEvent.keyDown(zone, { key: ' ' })
  })
})
