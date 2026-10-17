import { useCallback, useEffect, useRef, useState } from 'react'

import { attachmentError, readAttachment } from '@/lib/chat-attachments'
import type { AttachmentDraft } from '@/lib/chat-attachments'

/** 草稿只属于当前输入；失效的读取回调不能恢复移除或另一会话的附件。 */
export function useChatAttachments() {
  const [attachments, setAttachments] = useState<AttachmentDraft[]>([])
  const epochRef = useRef(0)
  useEffect(() => () => { epochRef.current++ }, [])

  const clear = useCallback(() => {
    epochRef.current++
    setAttachments([])
  }, [])
  const cancelReading = useCallback(() => {
    epochRef.current++
    setAttachments((previous) => previous.map((item) => item.status === 'reading'
      ? { ...item, status: 'error', error: '附件读取已取消，请移除后重新选择' } : item))
  }, [])
  const remove = useCallback((id: string) => {
    setAttachments((previous) => previous.filter((item) => item.id !== id))
  }, [])
  const removeSent = useCallback((ids: ReadonlySet<string>) => {
    setAttachments((previous) => previous.filter((item) => !ids.has(item.id)))
  }, [])
  const add = useCallback((files: File[]) => {
    const epoch = epochRef.current
    const drafts: AttachmentDraft[] = files.map((file) => {
      const error = attachmentError(file)
      return { id: crypto.randomUUID(), name: file.name, size: file.size, mime_type: file.type,
        status: error ? 'error' : 'reading', ...(error ? { error } : {}) }
    })
    setAttachments((previous) => [...previous, ...drafts])
    files.forEach((file, index) => {
      const draft = drafts[index]
      if (!draft || draft.status === 'error') return
      void readAttachment(file).then((input) => {
        if (epoch !== epochRef.current) return
        setAttachments((previous) => previous.map((item) => item.id === draft.id
          ? { ...item, status: 'ready', content_base64: input.content_base64 } : item))
      }).catch(() => {
        if (epoch !== epochRef.current) return
        setAttachments((previous) => previous.map((item) => item.id === draft.id
          ? { ...item, status: 'error', error: '读取附件失败，请移除后重新选择' } : item))
      })
    })
  }, [])

  return { attachments, add, remove, removeSent, clear, cancelReading }
}
