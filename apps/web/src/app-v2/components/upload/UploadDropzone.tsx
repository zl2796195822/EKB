import { useEffect, useRef, useState, type DragEvent } from 'react'
import { FolderOpen, UploadSimple } from '@phosphor-icons/react'

interface UploadDropzoneProps {
  readonly onFiles: (files: FileList | File[], mode: 'MULTI_FILE' | 'DIRECTORY') => void
  readonly disabled?: boolean
}

export function UploadDropzone({ onFiles, disabled }: UploadDropzoneProps) {
  const [dragging, setDragging] = useState(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const dirInputRef = useRef<HTMLInputElement | null>(null)

  // 目录选择属性无法靠 JSX 稳定透传（webkitdirectory/directory 是非标准布尔属性，
  // React 不会以预期方式渲染），改为挂载后用 setAttribute 命令式固化，
  // 并同时兼容 Chrome/Safari(webkitdirectory) 与 Firefox(directory)。
  useEffect(() => {
    const el = dirInputRef.current
    if (!el) return
    el.setAttribute('webkitdirectory', '')
    el.setAttribute('directory', '')
    el.setAttribute('mozdirectory', '')
  }, [])

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
    if (disabled) return
    const items = event.dataTransfer?.files
    if (items && items.length > 0) onFiles(items, 'MULTI_FILE')
  }

  return (
    <div
      className={`ut-dropzone${dragging ? ' ut-dropzone--active' : ''}${disabled ? ' ut-dropzone--disabled' : ''}`}
      onDragOver={(event) => {
        event.preventDefault()
        if (!disabled) setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      role="button"
      tabIndex={0}
      onClick={() => fileInputRef.current?.click()}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') fileInputRef.current?.click()
      }}
    >
      <UploadSimple size={28} aria-hidden="true" />
      <p className="ut-dropzone-title">拖拽文件到此处，或点击选择</p>
      <p className="ut-dropzone-hint">支持 PDF / Word / PPT / Excel / TXT / Markdown / CSV，可包含中文、空格与括号的文件名与目录结构</p>
      <div className="ut-dropzone-actions">
        <button
          type="button"
          className="ut-btn ut-btn--primary"
          disabled={disabled}
          onClick={(event) => {
            event.stopPropagation()
            fileInputRef.current?.click()
          }}
        >
          <UploadSimple size={15} aria-hidden="true" />选择文件
        </button>
        <button
          type="button"
          className="ut-btn ut-btn--secondary"
          disabled={disabled}
          onClick={(event) => {
            event.stopPropagation()
            dirInputRef.current?.click()
          }}
        >
          <FolderOpen size={15} aria-hidden="true" />选择目录（保留结构）
        </button>
      </div>
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".txt,.md,.pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.csv"
        className="ut-hidden-input"
        onChange={(event) => {
          const files = event.target.files
          if (files && files.length > 0) onFiles(files, 'MULTI_FILE')
          event.target.value = ''
        }}
      />
      <input
        ref={dirInputRef}
        type="file"
        multiple
        className="ut-hidden-input"
        onChange={(event) => {
          const files = event.target.files
          if (files && files.length > 0) onFiles(files, 'DIRECTORY')
          event.target.value = ''
        }}
      />
    </div>
  )
}
