import { useCallback, useRef, useState, type DragEvent } from 'react'
import { FileText, Upload, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'

/**
 * 简历上传组件 — 设计文档 §10.4
 *
 * 支持 PDF/Word/图片，≤ 10MB；拖拽或点击触发。
 * 简历文本先经 PII 脱敏处理后再送入 LLM（PIPL/GDPR 合规）。
 */
const ACCEPTED_TYPES = ['.pdf', '.doc', '.docx', '.png', '.jpg', '.jpeg']
const ACCEPTED_MIME = ['application/pdf', 'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'image/png', 'image/jpeg']
const MAX_SIZE = 10 * 1024 * 1024 // 10MB

interface ResumeUploaderProps {
  onFileSelected?: (file: File) => void
  loading?: boolean
  className?: string
}

function validate(file: File): string | null {
  const ext = '.' + (file.name.split('.').pop()?.toLowerCase() ?? '')
  if (!ACCEPTED_TYPES.includes(ext) && !ACCEPTED_MIME.includes(file.type)) {
    return '不支持的文件类型，仅支持 PDF / Word / 图片'
  }
  if (file.size > MAX_SIZE) {
    return '文件超过 10MB 限制'
  }
  return null
}

export function ResumeUploader({ onFileSelected, loading, className }: ResumeUploaderProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver] = useState(false)
  const [selected, setSelected] = useState<File | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleFile = useCallback(
    (file: File) => {
      const err = validate(file)
      if (err) {
        setError(err)
        setSelected(null)
        return
      }
      setError(null)
      setSelected(file)
      onFileSelected?.(file)
    },
    [onFileSelected],
  )

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files?.[0]
    if (file) handleFile(file)
  }

  function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (file) handleFile(file)
    e.target.value = ''
  }

  function clear() {
    setSelected(null)
    setError(null)
  }

  return (
    <div className={cn('space-y-3', className)}>
      <div
        role="button"
        tabIndex={0}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click()
        }}
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        className={cn(
          'flex flex-col items-center justify-center rounded-lg border border-dashed border-border-strong py-12 px-6 text-center transition-colors cursor-pointer',
          'hover:bg-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink focus-visible:ring-offset-1',
          dragOver && 'border-ink bg-subtle',
        )}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED_TYPES.join(',')}
          onChange={onPick}
          className="hidden"
          disabled={loading}
        />
        <div className="rounded-full bg-subtle p-4 mb-3">
          <Upload className="size-6 text-ink-muted" />
        </div>
        <p className="text-sm font-medium text-ink mb-1">
          {dragOver ? '释放以上传' : '拖拽简历到此处，或点击选择'}
        </p>
        <p className="text-xs text-ink-muted">支持 PDF / Word / 图片，最大 10MB</p>
      </div>

      {error && (
        <p className="text-sm text-state-archived" role="alert">
          {error}
        </p>
      )}

      {selected && (
        <div className="flex items-center justify-between rounded-md border border-border bg-subtle px-3 py-2">
          <div className="flex items-center gap-2 min-w-0">
            <FileText className="size-4 text-ink-muted shrink-0" />
            <span className="text-sm text-ink truncate">{selected.name}</span>
            <span className="text-xs text-ink-faint shrink-0">
              {(selected.size / 1024).toFixed(1)} KB
            </span>
          </div>
          {!loading && (
            <Button variant="ghost" size="icon" className="size-7" onClick={clear} aria-label="移除">
              <X className="size-3.5" />
            </Button>
          )}
        </div>
      )}

      <p className="text-xs text-ink-faint">
        简历文本先经 PII 脱敏处理后再送入 LLM，符合 PIPL/GDPR 合规要求
      </p>
    </div>
  )
}
