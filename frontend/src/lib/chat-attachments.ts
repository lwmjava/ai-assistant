import type { ChatAttachmentInput, ChatAttachmentSummary } from '@/types/api'

export const MAX_ATTACHMENT_BYTES = 1048576
export const ATTACHMENT_EXTENSIONS = [
  'png', 'jpg', 'jpeg', 'md', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'pdf',
  'go', 'py', 'css', 'html', 'htm', 'java', 'js', 'json', 'xml', 'yaml', 'yml',
  'c', 'h', 'cpp', 'cc', 'cxx', 'hpp', 'vue', 'ts',
] as const
export const ATTACHMENT_ACCEPT = ATTACHMENT_EXTENSIONS.map((extension) => `.${extension}`).join(',')

export interface AttachmentDraft extends ChatAttachmentSummary {
  id: string
  status: 'reading' | 'ready' | 'error'
  content_base64?: string
  error?: string
}

export function attachmentError(file: Pick<File, 'name' | 'size'>): string | null {
  const dot = file.name.lastIndexOf('.')
  const extension = dot > 0 ? file.name.slice(dot + 1).toLowerCase() : ''
  if (!(ATTACHMENT_EXTENSIONS as readonly string[]).includes(extension)) return '不支持此附件类型'
  if (file.size > MAX_ATTACHMENT_BYTES) return '附件超过 1 MB（1048576 字节）'
  return null
}

export function attachmentSize(size: number): string {
  return size < 1024 ? `${size} B` : `${(size / 1024).toFixed(1)} KB`
}

export function readAttachment(file: File): Promise<ChatAttachmentInput> {
  const invalid = attachmentError(file)
  if (invalid) return Promise.reject(new Error(invalid))
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('读取附件失败，请移除后重新选择'))
    reader.onabort = () => reject(new Error('附件读取已取消，请重新选择'))
    reader.onload = () => {
      const result = reader.result
      if (typeof result !== 'string' || !result.includes(',')) {
        reject(new Error('读取附件失败，请移除后重新选择'))
        return
      }
      resolve({ name: file.name, content_base64: result.slice(result.indexOf(',') + 1) })
    }
    reader.readAsDataURL(file)
  })
}
